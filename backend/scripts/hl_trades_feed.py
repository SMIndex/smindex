"""Hyperliquid "last active" from the public trades feed (service smindex-hl-trades).

Own process, one ws connection. Subscribes `trades` for every main perp, HIP-3
perp, then spot pairs and outcome sides with volume, up to MAX_SUBS (the whole
server must stay at or below 900 Hyperliquid subscriptions; the app and the
other services hold about 51). Each trade carries users [buyer, seller]; both
are matched against the listed wallets and their sub-accounts, and the newest
trade per wallet is written to hl_last_active every WRITE_SEC. The app reads
that table (hl/last_trade.py).

Measured 2026-10-22 (scripts/probe_hl_trades_feed.py): 0.35 s median delay,
0 missed of 2,275 checked fills apart from outcome merges, about 35 msg/s.

  * coin list + listed wallets: every COINS_EVERY_SEC (diffed, resubscribed)
  * sub-account map: every SUBS_EVERY_SEC
  * reconnect: resubscribe, then one userFillsByTime read since the drop for
    every account that traded in the last hour
  * wallets the feed has not seen: one newest-fill read (wallet + sub-accounts)
    at start, then once a day until the feed sees them
  * Hyperliquid's ping every 30 s; 90 s of silence reconnects
  * hl_feed_status (one row) is written every WRITE_SEC: the monitor alerts on
    Telegram when it is older than 5 minutes or disconnected
REST reads use their own pacing (REST_WEIGHT_PER_MIN) so the app keeps its budget.

Open positions (2026-10-22, Discover "Open" column and the HL profile page):
  * every master wallet on the list or on screen has its main-perp sizes from
    one clearinghouseState read (2 weight), then follows its trades in this
    feed; a trade that opens, closes or flips a position (the open count can
    change) re-reads it after POS_GATHER_SEC, at most once per POS_MIN_GAP_SEC
  * a wallet with an open profile page is re-read on every trade (sizes)
  * wallets on screen (hl_feed_watch, written by the app) get a count-only
    safety read every POS_SWEEP_SEC; reconnects forget the sizes
  * each read lands in hl_open_positions at once; the app serves it
  * hard cap POS_WPM_MAX weight per minute for these reads

Coins (2026-10-22): a main, HIP-3 or spot coin stays subscribed only while a
listed wallet or sub-account traded it in the last KEEP_DAYS (this feed's own
record in hl_feed_coin_seen, the hourly fill sampler's hl_fill_events, and an
hourly look at the last 30 trades of every dropped coin); the room goes to
outcome sides, newest first. PROBE_SLOTS of MAX_SUBS are kept for that look.
"""
import asyncio
import json
import os
import sys
import time

import httpx
import websockets

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from sqlalchemy import bindparam, text  # noqa: E402
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine  # noqa: E402

from app.config import settings  # noqa: E402
from app.utils.logger import get_logger  # noqa: E402

logger = get_logger("hl_trades_feed")

WS_URL = "wss://api.hyperliquid.xyz/ws"
INFO_URL = "https://api.hyperliquid.xyz/info"
MAX_SUBS = int(os.environ.get("HL_FEED_MAX_SUBS", "840"))
WRITE_SEC = 5.0
COINS_EVERY_SEC = 3600
SUBS_EVERY_SEC = 24 * 3600
UNSEEN_EVERY_SEC = 24 * 3600
RECENT_MS = 3600 * 1000            # reconnect: re-read accounts that traded this recently
PING_SEC = 30.0
SILENT_SEC = 90.0
SUB_SEND_PAUSE = 0.1               # 600 subscribe messages/min, under the 2000 sent/min cap
REST_WEIGHT_PER_MIN = float(os.environ.get("HL_FEED_REST_WPM", "120"))
LISTED_PER_WINDOW = 50
POS_GATHER_SEC = 0.5               # collect the rest of a burst before the read
POS_MIN_GAP_SEC = 5.0              # at most one read per wallet per 5 s
POS_SWEEP_SEC = 300                # count-only safety read for wallets on screen
POS_WPM_MAX = float(os.environ.get("HL_FEED_POS_WPM", "300"))
POS_CONCURRENCY = 4
POS_BUF_MS = 30_000                # trades kept to replay over a read in flight
WATCH_RELOAD_SEC = 5.0
KEEP_DAYS = 7
PROBE_SLOTS = 10                   # subscriptions kept free for the dropped-coin look
PROBE_HOLD_SEC = 3.0

engine = create_async_engine(settings.DATABASE_URL, pool_size=2, max_overflow=0, pool_recycle=1800)
Session = async_sessionmaker(engine, expire_on_commit=False)
http = httpx.AsyncClient(timeout=20.0)

acct_of: dict[str, str] = {}       # account (wallet or sub-account) -> listed wallet
accts_of: dict[str, list[str]] = {}
last_ms: dict[str, int] = {}       # wallet -> newest trade ms
src_of: dict[str, str] = {}        # wallet -> 'feed' | 'rest'
acct_last: dict[str, int] = {}     # account -> newest trade ms
seen_by_feed: set[str] = set()
dirty: set[str] = set()
coins: list[str] = []
subscribed: set[str] = set()
state = {"connected": 0, "last_msg": 0.0, "msgs": 0, "trades": 0, "matched": 0, "reconnects": 0,
         "rest_requests": 0, "rest_weight": 0, "rest_429": 0, "coins": 0, "coins_skipped": 0,
         "drop_ms": None, "started": time.time()}
ws_ref = {"ws": None}

main_coins: set[str] = set()
watch: set[str] = set()            # masters on screen (Discover rows, profile pages)
watch_profile: set[str] = set()    # masters with an open profile page
pos_szi: dict[str, dict[str, float]] = {}   # master -> main-perp coin -> size
pos_state_ms: dict[str, int] = {}  # master -> 'time' of the state pos_szi came from
pos_last_read: dict[str, float] = {}
pos_due: dict[str, list] = {}      # master -> [due_mono, trade_ms, recv_ms, reason]
pos_inflight: set[str] = set()
pos_buf: dict[str, list] = {}      # master -> [(trade_ms, coin, delta)]
pos_win: list = []                 # (mono, weight) of reads in the last 60 s
pos_stats = {"reads": 0, "weight": 0, "r429": 0, "errors": 0, "lat": {}, "by": {}}
coin_seen: dict[str, int] = {}     # coin -> newest listed trade ms
coin_dirty: set[str] = set()
probe_q: list[str] = []
probe_subs: set[str] = set()
probe_info = {"total": 0, "passes": 0, "fast": True}
first_probe_done = asyncio.Event()


async def info(body: dict):
    """One paced Hyperliquid info read. Sleeps after each call in proportion to its
    weight so this process stays at about REST_WEIGHT_PER_MIN."""
    for _ in range(3):
        r = await http.post(INFO_URL, json=body)
        state["rest_requests"] += 1
        if r.status_code == 429:
            state["rest_429"] += 1
            logger.warning("hl trades feed: 429 on %s, waiting 60 s", body.get("type"))
            await asyncio.sleep(60)
            continue
        r.raise_for_status()
        d = r.json()
        w = 20 + (len(d) // 20 if body["type"] in ("userFills", "userFillsByTime") and isinstance(d, list) else 0)
        state["rest_weight"] += w
        await asyncio.sleep(60.0 * w / REST_WEIGHT_PER_MIN)
        return d
    raise RuntimeError("429 three times")


async def ensure_tables():
    async with engine.begin() as c:
        await c.execute(text(
            "CREATE TABLE IF NOT EXISTS hl_last_active ("
            " wallet VARCHAR(42) NOT NULL PRIMARY KEY,"
            " last_trade_ms BIGINT NOT NULL,"
            " account VARCHAR(42) NULL,"
            " src VARCHAR(8) NOT NULL DEFAULT 'feed',"
            " updated_at DATETIME NOT NULL)"))
        await c.execute(text(
            "CREATE TABLE IF NOT EXISTS hl_feed_status ("
            " id TINYINT NOT NULL PRIMARY KEY,"
            " connected TINYINT NOT NULL, last_msg_at DATETIME NULL,"
            " coins INT NOT NULL, coins_skipped INT NOT NULL, wallets INT NOT NULL, accounts INT NOT NULL,"
            " msgs BIGINT NOT NULL, trades BIGINT NOT NULL, matched BIGINT NOT NULL, reconnects INT NOT NULL,"
            " rest_requests INT NOT NULL, rest_weight BIGINT NOT NULL, rest_429 INT NOT NULL,"
            " updated_at DATETIME NOT NULL)"))
        await c.execute(text(
            "CREATE TABLE IF NOT EXISTS hl_open_positions ("
            " wallet VARCHAR(42) NOT NULL PRIMARY KEY,"
            " n INT NOT NULL, markets JSON NOT NULL, state MEDIUMTEXT NOT NULL,"
            " state_ms BIGINT NOT NULL, read_ms BIGINT NOT NULL,"
            " trade_ms BIGINT NULL, recv_ms BIGINT NULL, reason VARCHAR(8) NOT NULL)"))
        await c.execute(text(
            "CREATE TABLE IF NOT EXISTS hl_feed_watch ("
            " wallet VARCHAR(42) NOT NULL PRIMARY KEY,"
            " seen_until DATETIME NOT NULL, profile_until DATETIME NULL)"))
        await c.execute(text(
            "CREATE TABLE IF NOT EXISTS hl_feed_coin_seen ("
            " coin VARCHAR(40) NOT NULL PRIMARY KEY, last_ms BIGINT NOT NULL)"))
    async with Session() as s:
        for coin, ms in (await s.execute(text("SELECT coin, last_ms FROM hl_feed_coin_seen"))).all():
            coin_seen[coin] = int(ms)
        for w, ms, src in (await s.execute(text("SELECT wallet, last_trade_ms, src FROM hl_last_active"))).all():
            last_ms[w] = int(ms)
            src_of[w] = src


async def listed_wallets() -> list[str]:
    out: list[str] = []
    async with Session() as s:
        for period in ("all", "day", "week", "month"):
            ts = (await s.execute(text(
                "SELECT MAX(timestamp) FROM leaderboard_snapshots WHERE exchange='hl' AND period=:p"),
                {"p": period})).scalar()
            if ts is None:
                continue
            rows = (await s.execute(text(
                "SELECT wallet_address FROM leaderboard_snapshots WHERE exchange='hl' AND period=:p "
                "AND timestamp=:t ORDER BY `rank` LIMIT :n"), {"p": period, "t": ts, "n": LISTED_PER_WINDOW})).scalars().all()
            out.extend(w.lower() for w in rows)
    return list(dict.fromkeys(out))


async def refresh_accounts(wallets: list[str], with_subs: bool) -> None:
    for w in wallets:
        acct_of[w] = w
        accts_of.setdefault(w, [w])
    if not with_subs:
        return
    for w in wallets:
        try:
            body = await info({"type": "subAccounts", "user": w})
            subs = [str(x["subAccountUser"]).lower() for x in (body or []) if x.get("subAccountUser")]
        except Exception as exc:
            logger.warning("hl trades feed: subAccounts failed for %s: %s", w[:10], exc)
            continue
        accts_of[w] = [w] + subs
        for a in subs:
            acct_of[a] = w
    logger.info("hl trades feed: account map %d wallets, %d accounts", len(wallets), len(acct_of))


async def traded_coins() -> set[str]:
    """Coins a listed wallet or sub-account traded in the last KEEP_DAYS: this
    feed's record (snapshots of probed coins included), the hourly fill
    sampler (listed masters), plus every coin a tracked wallet holds now."""
    cut = int(time.time() * 1000) - KEEP_DAYS * 86400 * 1000
    out = {c for c, ms in coin_seen.items() if ms >= cut}
    out |= {c for sz in pos_szi.values() for c in sz}
    masters = list(accts_of)
    if masters:
        async with Session() as s:
            rows = (await s.execute(text(
                "SELECT DISTINCT coin FROM hl_fill_events WHERE wallet_address IN :w AND ts_ms >= :c"
            ).bindparams(bindparam("w", expanding=True)), {"w": masters, "c": cut})).scalars().all()
        out |= set(rows)
    return out


async def coin_list() -> tuple[list[str], int]:
    """Main + HIP-3 perps and spot pairs (volume above 0) that listed wallets
    traded in the last KEEP_DAYS, then outcome sides newest first, cut at
    MAX_SUBS - PROBE_SLOTS. The dropped coins go to the hourly look."""
    main = [u["name"] for u in (await info({"type": "meta"}))["universe"] if not u.get("isDelisted")]
    hip3: list[str] = []
    for d in await info({"type": "perpDexs"}):
        if d:
            hip3 += [u["name"] for u in (await info({"type": "meta", "dex": d["name"]}))["universe"]
                     if not u.get("isDelisted")]
    sm = await info({"type": "spotMetaAndAssetCtxs"})
    spot = [n for v, n in sorted(((float(c.get("dayNtlVlm") or 0), u["name"])
                                  for u, c in zip(sm[0]["universe"], sm[1])), reverse=True) if v > 0]
    om = await info({"type": "outcomeMeta"})
    outcome = []
    for o in sorted(om.get("outcomes") or [], key=lambda x: -int(x["outcome"])):
        outcome += [f"#{int(o['outcome']) * 10 + i}" for i in range(len(o.get("sideSpecs") or [0, 1]))]
    main_coins.clear()
    main_coins.update(main)
    traded = await traded_coins()
    base = main + hip3 + spot
    keep = [c for c in base if c in traded]
    dropped = [c for c in base if c not in traded]
    cap = MAX_SUBS - PROBE_SLOTS
    want = (keep + outcome)[:cap]
    left = (keep + outcome)[cap:]
    probe_q[:] = dropped
    probe_info["total"] = len(dropped)
    kinds = {**{c: "main" for c in main}, **{c: "hip3" for c in hip3}, **{c: "spot" for c in spot}}
    by_kind = {k: sum(1 for c in dropped if kinds[c] == k) for k in ("main", "hip3", "spot")}
    logger.info("hl trades feed: coins kept %d of %d (dropped main %d, hip3 %d, spot %d), outcome sides %d of %d, "
                "total %d of cap %d + %d probe slots",
                len(keep), len(base), by_kind["main"], by_kind["hip3"], by_kind["spot"],
                sum(1 for c in want if c.startswith("#")), len(outcome), len(want), cap, PROBE_SLOTS)
    if left:
        logger.info("hl trades feed: left out %d: %s", len(left), " ".join(left))
    return want, len(left)


async def write_loop():
    while True:
        await asyncio.sleep(WRITE_SEC)
        try:
            batch = [(w, last_ms[w]) for w in list(dirty)]
            dirty.clear()
            cbatch = [(c, coin_seen[c]) for c in list(coin_dirty)]
            coin_dirty.clear()
            async with Session() as s:
                for c, ms in cbatch:
                    await s.execute(text(
                        "INSERT INTO hl_feed_coin_seen (coin, last_ms) VALUES (:c, :ms) "
                        "ON DUPLICATE KEY UPDATE last_ms = GREATEST(last_ms, :ms)"), {"c": c, "ms": ms})
                for w, ms in batch:
                    await s.execute(text(
                        "INSERT INTO hl_last_active (wallet, last_trade_ms, account, src, updated_at) "
                        "VALUES (:w, :ms, NULL, :src, UTC_TIMESTAMP()) "
                        "ON DUPLICATE KEY UPDATE src = IF(:ms > last_trade_ms, VALUES(src), src), "
                        "last_trade_ms = GREATEST(last_trade_ms, :ms), updated_at = UTC_TIMESTAMP()"),
                        {"w": w, "ms": ms, "src": src_of.get(w, "feed")})
                lm = state["last_msg"]
                await s.execute(text(
                    "REPLACE INTO hl_feed_status VALUES (1, :c, :lm, :coins, :skip, :wal, :acc, :msgs, :tr, :ma, :rc,"
                    " :rq, :rw, :r429, UTC_TIMESTAMP())"),
                    {"c": state["connected"], "lm": time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(lm)) if lm else None,
                     "coins": state["coins"], "skip": state["coins_skipped"], "wal": len(accts_of), "acc": len(acct_of),
                     "msgs": state["msgs"], "tr": state["trades"], "ma": state["matched"], "rc": state["reconnects"],
                     "rq": state["rest_requests"], "rw": state["rest_weight"], "r429": state["rest_429"]})
                await s.commit()
        except Exception as exc:
            logger.warning("hl trades feed: write failed: %s", exc)


def note(acct: str, ms: int, src: str) -> None:
    w = acct_of.get(acct)
    if not w:
        return
    if ms > acct_last.get(acct, 0):
        acct_last[acct] = ms
    if ms > last_ms.get(w, 0):
        last_ms[w] = ms
        src_of[w] = src
        dirty.add(w)


async def newest_fill(acct: str, since_ms: int | None = None) -> int | None:
    body = ({"type": "userFillsByTime", "user": acct, "startTime": since_ms} if since_ms
            else {"type": "userFills", "user": acct})
    fills = await info(body)
    return max((int(f["time"]) for f in fills or [] if f.get("time")), default=None)


async def gap_reads(drop_ms: int) -> None:
    now = int(time.time() * 1000)
    accts = [a for a, ms in acct_last.items() if now - ms <= RECENT_MS]
    found = 0
    for a in accts:
        try:
            ms = await newest_fill(a, drop_ms - 5000)
            if ms:
                note(a, ms, "rest")
                found += 1
        except Exception as exc:
            logger.warning("hl trades feed: gap read failed for %s: %s", a[:10], exc)
    logger.info("hl trades feed: reconnect gap reads %d accounts, %d with fills", len(accts), found)


async def unseen_loop():
    """Wallets the feed has not seen: one newest-fill read now, then daily."""
    await asyncio.sleep(30)
    while True:
        todo = [w for w in list(accts_of) if w not in seen_by_feed]
        n = 0
        for w in todo:
            if w in seen_by_feed:
                continue
            best = None
            for a in list(accts_of.get(w, [w])):
                try:
                    ms = await newest_fill(a)
                except Exception as exc:
                    logger.warning("hl trades feed: newest fill read failed for %s: %s", a[:10], exc)
                    continue
                if ms:
                    if ms > acct_last.get(a, 0):
                        acct_last[a] = ms
                    best = max(best or 0, ms)
            if best and best > last_ms.get(w, 0):
                last_ms[w] = best
                src_of[w] = "rest"
                dirty.add(w)
            n += 1
        logger.info("hl trades feed: unseen pass read %d wallets (%d seen by the feed)", n, len(seen_by_feed))
        await asyncio.sleep(UNSEEN_EVERY_SEC)


async def sync_subscriptions(ws) -> None:
    # unsubscribe first: the server-wide total must never pass the cap, not even briefly
    want = set(coins)
    for c in [c for c in subscribed if c not in want]:
        await ws.send(json.dumps({"method": "unsubscribe", "subscription": {"type": "trades", "coin": c}}))
        subscribed.discard(c)
        await asyncio.sleep(SUB_SEND_PAUSE)
    for c in [c for c in coins if c not in subscribed]:
        await ws.send(json.dumps({"method": "subscribe", "subscription": {"type": "trades", "coin": c}}))
        subscribed.add(c)
        await asyncio.sleep(SUB_SEND_PAUSE)


async def probe_loop():
    """The hourly look at dropped coins: subscribe PROBE_SLOTS at a time for
    PROBE_HOLD_SEC; the subscription's first message carries the coin's last
    30 trades with users, and any listed account in them (within KEEP_DAYS)
    lands in coin_seen, so the next refresh subscribes the coin again. The
    first pass after start runs at once; later passes spread over the hour."""
    while True:
        ws = ws_ref["ws"]
        if ws is None or not probe_q:
            if ws is not None and not probe_q and probe_info["fast"]:
                probe_info["fast"] = False
                first_probe_done.set()
            await asyncio.sleep(5)
            continue
        batch = probe_q[:PROBE_SLOTS]
        del probe_q[:PROBE_SLOTS]
        want = set(coins)
        batch = [c for c in batch if c not in want and c not in subscribed]
        try:
            for c in batch:
                await ws.send(json.dumps({"method": "subscribe", "subscription": {"type": "trades", "coin": c}}))
                probe_subs.add(c)
                await asyncio.sleep(SUB_SEND_PAUSE)
            await asyncio.sleep(PROBE_HOLD_SEC)
            for c in batch:
                if c not in set(coins):
                    await ws.send(json.dumps({"method": "unsubscribe", "subscription": {"type": "trades", "coin": c}}))
                probe_subs.discard(c)
                await asyncio.sleep(SUB_SEND_PAUSE)
        except Exception as exc:
            probe_subs.clear()
            logger.warning("hl trades feed: probe failed: %s", exc)
            continue
        if not probe_q:
            probe_info["passes"] += 1
            logger.info("hl trades feed: dropped-coin look done (%d coins, pass %d)",
                        probe_info["total"], probe_info["passes"])
            if probe_info["fast"]:
                probe_info["fast"] = False
                first_probe_done.set()
        if not probe_info["fast"]:
            rounds = max(1.0, probe_info["total"] / PROBE_SLOTS)
            await asyncio.sleep(max(1.0, COINS_EVERY_SEC * 0.9 / rounds - PROBE_HOLD_SEC))


async def refresh_loop():
    """Coin list + listed wallets hourly, sub-accounts daily."""
    last_subs = time.time()
    first = True
    while True:
        if first:
            await first_probe_done.wait()
            first = False
        else:
            await asyncio.sleep(COINS_EVERY_SEC)
        try:
            ws_ = await listed_wallets()
            daily = time.time() - last_subs >= SUBS_EVERY_SEC
            await refresh_accounts(ws_, with_subs=daily)
            if daily:
                last_subs = time.time()
            new, skipped = await coin_list()
            coins[:] = new
            state["coins"], state["coins_skipped"] = len(new), skipped
            if ws_ref["ws"] is not None:
                await sync_subscriptions(ws_ref["ws"])
            logger.info("hl trades feed: hourly refresh, %d coins (%d over the cap), %d wallets",
                        len(new), skipped, len(accts_of))
        except Exception as exc:
            logger.warning("hl trades feed: hourly refresh failed: %s", exc)


# ---- open positions -------------------------------------------------------

def schedule(w: str, reason: str, trade_ms: int | None, recv_ms: int | None) -> None:
    now = time.monotonic()
    cur = pos_due.get(w)
    if cur is not None and not (cur[3] == "sweep" and reason != "sweep"):
        return
    gather = 0.0 if reason == "sweep" else POS_GATHER_SEC
    due = max(now + gather, pos_last_read.get(w, -1e9) + POS_MIN_GAP_SEC)
    pos_due[w] = [due, trade_ms, recv_ms, reason]


def _apply(w: str, coin: str, d: float) -> bool:
    """Apply one trade to the tracked sizes; True when the open count or a
    side can change (opened, closed or flipped), or when a reducing trade
    leaves less than its own size: measured 10-22, busy market makers' tracked
    sizes can drift a little from Hyperliquid's (6 of 63 wallets, counts all
    right), so a near-close is read instead of trusted."""
    sz = pos_szi[w]
    b = sz.get(coin, 0.0)
    a = round(b + d, 8)
    if abs(a) < 1e-9:
        a = 0.0
    if a:
        sz[coin] = a
    else:
        sz.pop(coin, None)
    return (b == 0.0) != (a == 0.0) or b * a < 0 or (abs(a) < abs(b) and abs(a) <= abs(d))


def pos_trade(w: str, coin: str, d: float, tm: int, recv_ms: int) -> None:
    buf = pos_buf.setdefault(w, [])
    buf.append((tm, coin, d))
    if len(buf) > 400 or buf[0][0] < tm - POS_BUF_MS:
        pos_buf[w] = [x for x in buf if x[0] >= tm - POS_BUF_MS][-400:]
    if w in pos_inflight:
        return                        # the read in flight replays the buffer
    if w not in pos_szi:
        schedule(w, "new", tm, recv_ms)
        return
    if tm <= pos_state_ms.get(w, 0):
        return                        # already inside the state that was read
    if _apply(w, coin, d):
        schedule(w, "count", tm, recv_ms)
    elif w in watch_profile:
        schedule(w, "size", tm, recv_ms)


async def pos_take(weight: int) -> None:
    while True:
        now = time.monotonic()
        while pos_win and pos_win[0][0] < now - 60:
            pos_win.pop(0)
        if sum(x[1] for x in pos_win) + weight <= POS_WPM_MAX:
            pos_win.append((now, weight))
            return
        await asyncio.sleep(0.2)


async def pos_read(w: str, item: list) -> None:
    _, trade_ms, recv_ms, reason = item
    try:
        await pos_take(2)
        r = await http.post(INFO_URL, json={"type": "clearinghouseState", "user": w})
        pos_stats["reads"] += 1
        pos_stats["weight"] += 2
        pos_stats["by"][reason] = pos_stats["by"].get(reason, 0) + 1
        if r.status_code == 429:
            pos_stats["r429"] += 1
            pos_szi.pop(w, None)
            logger.warning("hl trades feed: 429 on clearinghouseState, positions wait 10 s")
            await asyncio.sleep(10)
            return
        r.raise_for_status()
        body = r.json()
    except Exception as exc:
        pos_stats["errors"] += 1
        pos_szi.pop(w, None)
        logger.warning("hl trades feed: positions read failed for %s: %s", w[:10], exc)
        return
    finally:
        pos_last_read[w] = time.monotonic()
    now_ms = int(time.time() * 1000)
    st_ms = int(body.get("time") or now_ms)
    szi: dict[str, float] = {}
    for ap in body.get("assetPositions") or []:
        p = ap.get("position") or {}
        v = float(p.get("szi") or 0)
        if v:
            szi[str(p.get("coin"))] = v
    markets = [{"symbol": c, "side": "long" if v > 0 else "short"} for c, v in szi.items()]
    pos_szi[w] = dict(szi)
    pos_state_ms[w] = st_ms
    changed_at = None
    # trades that arrived while the read was in flight and are newer than the
    # state (several fills of one order share a millisecond: no dedupe by time)
    for tm, coin, d in list(pos_buf.get(w, [])):
        if tm > st_ms and _apply(w, coin, d):
            changed_at = tm
    try:
        async with Session() as s:
            await s.execute(text(
                "INSERT INTO hl_open_positions (wallet, n, markets, state, state_ms, read_ms, trade_ms, recv_ms, reason) "
                "VALUES (:w, :n, :m, :st, :sm, :rm, :tm, :rv, :rs) ON DUPLICATE KEY UPDATE n = VALUES(n), "
                "markets = VALUES(markets), state = VALUES(state), state_ms = VALUES(state_ms), "
                "read_ms = VALUES(read_ms), trade_ms = VALUES(trade_ms), recv_ms = VALUES(recv_ms), reason = VALUES(reason)"),
                {"w": w, "n": len(markets), "m": json.dumps(markets), "st": json.dumps(body), "sm": st_ms,
                 "rm": int(time.time() * 1000), "tm": trade_ms, "rv": recv_ms, "rs": reason})
            await s.commit()
    except Exception as exc:
        logger.warning("hl trades feed: positions write failed for %s: %s", w[:10], exc)
    if trade_ms:
        pos_stats["lat"].setdefault(reason, []).append(int(time.time() * 1000) - trade_ms)
    if changed_at is not None:
        schedule(w, "count", changed_at, None)


async def pos_loop():
    sem = asyncio.Semaphore(POS_CONCURRENCY)

    async def run(w, item):
        pos_inflight.add(w)
        try:
            await pos_read(w, item)
        finally:
            pos_inflight.discard(w)
            sem.release()

    while True:
        await asyncio.sleep(0.1)
        now = time.monotonic()
        ready = sorted((v[3] == "sweep", v[0], w) for w, v in list(pos_due.items())
                       if v[0] <= now and w not in pos_inflight)
        for _, _, w in ready:
            if sem.locked():
                break
            item = pos_due.pop(w)
            await sem.acquire()
            asyncio.create_task(run(w, item))


async def pos_sweep_loop():
    """Wallets on screen: a first read when unknown, then a count-only safety
    read every POS_SWEEP_SEC (event reads count as fresh). A failed first read
    is retried after 60 s."""
    while True:
        await asyncio.sleep(2)
        now = time.monotonic()
        for w in list(watch):
            if w in pos_due or w in pos_inflight:
                continue
            last = pos_last_read.get(w)
            if w not in pos_szi:
                if last is None or now - last >= 60:
                    schedule(w, "new", None, None)
            elif now - last >= POS_SWEEP_SEC:
                schedule(w, "sweep", None, None)


async def watch_loop():
    """hl_feed_watch (the app writes the wallets on screen) every WATCH_RELOAD_SEC,
    and a positions stats line every minute."""
    last_stats = time.monotonic()
    last_clean = 0.0
    while True:
        await asyncio.sleep(WATCH_RELOAD_SEC)
        try:
            async with Session() as s:
                rows = (await s.execute(text(
                    "SELECT wallet, profile_until IS NOT NULL AND profile_until > UTC_TIMESTAMP() "
                    "FROM hl_feed_watch WHERE seen_until > UTC_TIMESTAMP()"))).all()
                if time.monotonic() - last_clean > 3600:
                    await s.execute(text(
                        "DELETE FROM hl_feed_watch WHERE seen_until < UTC_TIMESTAMP() - INTERVAL 1 DAY"))
                    await s.commit()
                    last_clean = time.monotonic()
            watch.clear()
            watch.update(str(w).lower() for w, _ in rows)
            watch_profile.clear()
            watch_profile.update(str(w).lower() for w, p in rows if p)
        except Exception as exc:
            logger.warning("hl trades feed: watch reload failed: %s", exc)
        if time.monotonic() - last_stats >= 60:
            def q3(v):
                v = sorted(v)
                return [round(v[min(len(v) - 1, int(len(v) * f))] / 1000, 2) for f in (0.5, 0.95, 1.0)]
            lat = {k: q3(v) for k, v in pos_stats["lat"].items()}
            logger.info("hl trades feed: positions minute: reads %d %s, weight/min %.0f, 429 %d, errors %d, "
                        "trade to row s [p50,p95,max] %s, tracked %d, on screen %d, profile %d, subs %d+%d",
                        pos_stats["reads"], json.dumps(pos_stats["by"], sort_keys=True),
                        sum(x[1] for x in pos_win), pos_stats["r429"], pos_stats["errors"],
                        json.dumps(lat, sort_keys=True), len(pos_szi), len(watch), len(watch_profile),
                        len(subscribed), len(probe_subs))
            pos_stats.update({"reads": 0, "weight": 0, "r429": 0, "errors": 0, "lat": {}, "by": {}})
            last_stats = time.monotonic()


async def ws_loop():
    backoff = 1.0
    while True:
        live = False
        try:
            async with websockets.connect(WS_URL, ping_interval=None, close_timeout=5, max_size=16_000_000) as ws:
                subscribed.clear()
                probe_subs.clear()
                pos_szi.clear()           # trades were missed while down: read again
                pos_state_ms.clear()
                ws_ref["ws"] = ws
                t0 = int(time.time() * 1000)
                sub_task = asyncio.create_task(sync_subscriptions(ws))
                live = True
                state["connected"] = 1
                backoff = 1.0
                last_rx = last_ping = time.monotonic()
                gap_started = False
                logger.info("hl trades feed: connected, subscribing %d coins", len(coins))
                while True:
                    try:
                        raw = await asyncio.wait_for(ws.recv(), timeout=5.0)
                    except asyncio.TimeoutError:
                        raw = None
                    now = time.monotonic()
                    if now - last_ping >= PING_SEC:
                        await ws.send('{"method":"ping"}')
                        last_ping = now
                    if raw is None:
                        if now - last_rx > SILENT_SEC:
                            raise ConnectionError(f"no frame for {SILENT_SEC:.0f}s")
                        continue
                    last_rx = now
                    state["last_msg"] = time.time()
                    state["msgs"] += 1
                    if not gap_started and sub_task.done() and state["drop_ms"]:
                        gap_started = True
                        asyncio.create_task(gap_reads(state["drop_ms"]))
                        state["drop_ms"] = None
                    msg = json.loads(raw)
                    if msg.get("channel") != "trades":
                        continue
                    now_ms = int(time.time() * 1000)
                    keep_cut = now_ms - KEEP_DAYS * 86400 * 1000
                    for t in msg.get("data") or []:
                        tm = int(t.get("time") or 0)
                        coin = t.get("coin")
                        users = [u.lower() for u in t.get("users") or []]
                        for u in users:
                            if u in acct_of and tm >= keep_cut and tm > coin_seen.get(coin, 0):
                                coin_seen[coin] = tm
                                coin_dirty.add(coin)
                        if coin in main_coins and now_ms - tm < 60_000:
                            for i, u in enumerate(users[:2]):
                                if u in accts_of or u in watch:
                                    d = float(t.get("sz") or 0) * (1 if i == 0 else -1)
                                    pos_trade(u, coin, d, tm, now_ms)
                        if tm < t0 - 600_000:
                            continue          # old trades in the subscription snapshot
                        state["trades"] += 1
                        for u in users:
                            if u in acct_of:
                                state["matched"] += 1
                                seen_by_feed.add(acct_of[u])
                                note(u, tm, "feed")
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            state["reconnects"] += 1
            if live:
                state["drop_ms"] = int(time.time() * 1000)
            logger.warning("hl trades feed: ws dropped (%s), reconnect in %.0fs", exc, backoff)
        finally:
            ws_ref["ws"] = None
            state["connected"] = 0
        await asyncio.sleep(backoff)
        backoff = min(backoff * 2, 30.0)


async def main():
    await ensure_tables()
    wallets = await listed_wallets()
    await refresh_accounts(wallets, with_subs=False)
    new, skipped = await coin_list()
    coins[:] = new
    state["coins"], state["coins_skipped"] = len(new), skipped
    logger.info("hl trades feed: %d coins (%d over the cap of %d), %d listed wallets",
                len(new), skipped, MAX_SUBS, len(wallets))
    tasks = [asyncio.create_task(ws_loop()), asyncio.create_task(write_loop()), asyncio.create_task(refresh_loop()),
             asyncio.create_task(probe_loop()), asyncio.create_task(pos_loop()), asyncio.create_task(pos_sweep_loop()),
             asyncio.create_task(watch_loop())]
    await refresh_accounts(wallets, with_subs=True)     # sub-accounts while the feed already runs
    tasks.append(asyncio.create_task(unseen_loop()))
    await asyncio.gather(*tasks)


if __name__ == "__main__":
    asyncio.run(main())
