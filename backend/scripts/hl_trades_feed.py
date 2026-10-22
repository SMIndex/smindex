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
"""
import asyncio
import json
import os
import sys
import time

import httpx
import websockets

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from sqlalchemy import text  # noqa: E402
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
    async with Session() as s:
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


async def coin_list() -> tuple[list[str], int]:
    """Main + HIP-3 perps first (all active), then spot pairs and outcome sides
    by volume / newest, cut at MAX_SUBS."""
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
    want = main + hip3 + spot + outcome
    return want[:MAX_SUBS], max(0, len(want) - MAX_SUBS)


async def write_loop():
    while True:
        await asyncio.sleep(WRITE_SEC)
        try:
            batch = [(w, last_ms[w]) for w in list(dirty)]
            dirty.clear()
            async with Session() as s:
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
    want = set(coins)
    for c in [c for c in coins if c not in subscribed]:
        await ws.send(json.dumps({"method": "subscribe", "subscription": {"type": "trades", "coin": c}}))
        subscribed.add(c)
        await asyncio.sleep(SUB_SEND_PAUSE)
    for c in [c for c in subscribed if c not in want]:
        await ws.send(json.dumps({"method": "unsubscribe", "subscription": {"type": "trades", "coin": c}}))
        subscribed.discard(c)
        await asyncio.sleep(SUB_SEND_PAUSE)


async def refresh_loop():
    """Coin list + listed wallets hourly, sub-accounts daily."""
    last_subs = time.time()
    while True:
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


async def ws_loop():
    backoff = 1.0
    while True:
        live = False
        try:
            async with websockets.connect(WS_URL, ping_interval=None, close_timeout=5, max_size=16_000_000) as ws:
                subscribed.clear()
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
                    for t in msg.get("data") or []:
                        tm = int(t.get("time") or 0)
                        if tm < t0 - 600_000:
                            continue          # old trades in the subscription snapshot
                        state["trades"] += 1
                        for u in t.get("users") or []:
                            u = u.lower()
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
    tasks = [asyncio.create_task(ws_loop()), asyncio.create_task(write_loop()), asyncio.create_task(refresh_loop())]
    await refresh_accounts(wallets, with_subs=True)     # sub-accounts while the feed already runs
    tasks.append(asyncio.create_task(unseen_loop()))
    await asyncio.gather(*tasks)


if __name__ == "__main__":
    asyncio.run(main())
