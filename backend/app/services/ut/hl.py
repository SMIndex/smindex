"""Unusual Timing: every Hyperliquid read, in the API process only, through
the shared client at BACKGROUND priority (under the existing cap) and paced
at UT_WPM weight per minute; the day stops at UT_DAILY_WEIGHT_CAP. The weight
of every run is written to ut_job_runs (acceptance test 6 reads it).

Reads:
  * universe_snapshot: meta, perpDexs + meta per builder dex, spotMeta ->
    ut_universe (listing and delisting events come from the day-to-day diff)
  * candles_1h / candles_1m: candleSnapshot -> ut_candles
  * oi_snapshot: metaAndAssetCtxs (main hourly, builder dexes every 6 h) ->
    ut_market_drift raw columns
  * ledger: userNonFundingLedgerUpdates -> ut_wallet_meta first deposit
  * pull_fills: userFillsByTime, two pages, for feed wallets over $250K that
    the featured pipeline never read -> hl_fills
"""
from __future__ import annotations

import asyncio
import os
import time
from datetime import datetime, timedelta

import httpx
from sqlalchemy import bindparam, text

from app.db.database import get_session_factory
from app.services.featured import roundtrips as rt
from app.services.hyperliquid import client as hl_client
from app.utils.logger import get_logger

logger = get_logger(__name__)

UT_WPM = float(os.environ.get("UT_WPM", "200"))                      # weight per minute by day (owner, 2026-11-04)
UT_WPM_NIGHT = float(os.environ.get("UT_WPM_NIGHT", "400"))          # 20:00 to 04:00 UTC
DAILY_WEIGHT_CAP = int(os.environ.get("UT_DAILY_WEIGHT_CAP", "18000"))   # brief: under 20,000 a day
HL_RETRIES = 4
CANDLE_MAX = 5000                   # candles per candleSnapshot answer
H1_DAYS = 90
M1_DAYS = 7
FILLS_PAGES = 2                     # the brief's two-page rule for feed wallets
_pause_mult = 1.0


def wpm_now() -> float:
    h = datetime.utcnow().hour
    return UT_WPM_NIGHT if (h >= 20 or h < 4) else UT_WPM


class Budget(Exception):
    """The day's UT weight is spent; the job stops and continues tomorrow."""


class Run:
    def __init__(self, job: str) -> None:
        self.job = job
        self.started = datetime.utcnow()
        self.requests = 0
        self.weight = 0
        self.notes: list[str] = []
        self.id: int | None = None
        self.day_used = 0

    async def open(self) -> None:
        self.day_used = await weight_today()
        sf = get_session_factory()
        async with sf() as s:
            r = await s.execute(text("INSERT INTO ut_job_runs (job, started_at) VALUES (:j, :t)"), {"j": self.job, "t": self.started})
            await s.commit()
            self.id = r.lastrowid

    async def close(self, ok: bool) -> None:
        if self.id is None:
            return
        sf = get_session_factory()
        async with sf() as s:
            await s.execute(text(
                "UPDATE ut_job_runs SET finished_at=:f, ok=:ok, requests=:r, weight=:w, notes=:n WHERE id=:i"),
                {"f": datetime.utcnow(), "ok": 1 if ok else 0, "r": self.requests, "w": self.weight,
                 "n": "; ".join(self.notes)[:255] or None, "i": self.id})
            await s.commit()

    def left(self) -> int:
        return DAILY_WEIGHT_CAP - self.day_used - self.weight


async def weight_today() -> int:
    sf = get_session_factory()
    async with sf() as s:
        v = (await s.execute(text("SELECT COALESCE(SUM(weight),0) FROM ut_job_runs WHERE started_at >= UTC_DATE()"))).scalar()
    return int(v or 0)


async def hl_post(run: Run, payload: dict, *, timeout: float = 30.0):
    """One paced read. Raises Budget when the day's cap would be crossed;
    returns None after HL_RETRIES failed attempts (429, shed, 5xx,
    transport)."""
    global _pause_mult
    info_type = str(payload.get("type") or "")
    if run.left() < hl_client.base_weight(info_type):
        raise Budget(f"{run.job}: day cap {DAILY_WEIGHT_CAP} reached")
    for attempt in range(1, HL_RETRIES + 1):
        try:
            resp = await hl_client.post_info(payload, priority=hl_client.BACKGROUND, timeout=timeout)
        except hl_client.HLWeightShed as exc:
            _pause_mult = min(_pause_mult * 2, 16.0)
            run.notes.append(f"shed x{_pause_mult:.0f}")
            logger.warning("ut %s: shed (%s), pause x%.0f", run.job, exc, _pause_mult)
            await asyncio.sleep(30 * _pause_mult)
            continue
        except httpx.HTTPError as exc:
            _pause_mult = min(_pause_mult * 2, 16.0)
            logger.warning("ut %s: %s on %s (try %d), pause x%.0f", run.job, type(exc).__name__, info_type, attempt, _pause_mult)
            await asyncio.sleep(30 * _pause_mult)
            continue
        run.requests += 1
        if resp.status_code == 429 or resp.status_code >= 500:
            _pause_mult = min(_pause_mult * 2, 16.0)
            run.weight += hl_client.base_weight(info_type)
            run.notes.append(f"{resp.status_code} x{_pause_mult:.0f}")
            logger.warning("ut %s: %d on %s (try %d), pause x%.0f", run.job, resp.status_code, info_type, attempt, _pause_mult)
            await asyncio.sleep(60 * _pause_mult)
            continue
        resp.raise_for_status()
        body = await hl_client.decode_json(resp)
        items = len(body) if isinstance(body, list) else 0
        w = hl_client.base_weight(info_type) + hl_client.extra_weight(info_type, items)
        run.weight += w
        _pause_mult = max(1.0, _pause_mult * 0.9)
        await asyncio.sleep(60.0 * w / wpm_now() * _pause_mult)
        return body
    return None


# ---------------------------------------------------------------------------
# universe
# ---------------------------------------------------------------------------

async def universe_snapshot(run: Run) -> dict:
    """Main perps, builder-dex perps and spot pairs that exist now -> ut_universe."""
    now = datetime.utcnow().replace(second=0, microsecond=0)
    seen: list[tuple[str, str]] = []
    meta = await hl_post(run, {"type": "meta"})
    for a in (meta or {}).get("universe") or []:
        if a.get("name") and not a.get("isDelisted"):
            seen.append((str(a["name"]), "perp"))
    dexes = await hl_post(run, {"type": "perpDexs"}) or []
    names = [str(d["name"]) for d in dexes if isinstance(d, dict) and d.get("name")]
    for dex in names:
        m = await hl_post(run, {"type": "meta", "dex": dex})
        for a in (m or {}).get("universe") or []:
            if a.get("name") and not a.get("isDelisted"):
                nm = str(a["name"])
                seen.append((nm if ":" in nm else f"{dex}:{nm}", "hip3"))
    spot = await hl_post(run, {"type": "spotMeta"})
    for p in (spot or {}).get("universe") or []:
        if p.get("name"):
            seen.append((str(p["name"]), "spot"))
    sf = get_session_factory()
    async with sf() as s:
        for i in range(0, len(seen), 500):
            await s.execute(text(
                "INSERT INTO ut_universe (coin, kind, first_seen, last_seen, first_seen_src) VALUES (:c, :k, :t, :t, 'snapshot') "
                "ON DUPLICATE KEY UPDATE last_seen = VALUES(last_seen), kind = VALUES(kind)"),
                [{"c": c, "k": k, "t": now} for c, k in seen[i:i + 500]])
        await s.commit()
    run.notes.append(f"universe {len(seen)} ({len(names)} dexes)")
    return {"coins": len(seen), "dexes": names}


# ---------------------------------------------------------------------------
# candles
# ---------------------------------------------------------------------------

async def _store_candles(coin: str, tf: str, body: list) -> int:
    rows = []
    for c in body or []:
        try:
            rows.append({"coin": coin, "tf": tf, "t": rt.ms_to_dt(int(c["t"])), "o": float(c["o"]), "h": float(c["h"]),
                         "l": float(c["l"]), "c": float(c["c"]), "v": float(c.get("v") or 0)})
        except (KeyError, TypeError, ValueError):
            continue
    if not rows:
        return 0
    sf = get_session_factory()
    async with sf() as s:
        for i in range(0, len(rows), 1000):
            await s.execute(text(
                "INSERT INTO ut_candles (coin, tf, t, o, h, l, c, v, src) VALUES (:coin, :tf, :t, :o, :h, :l, :c, :v, 'hl') "
                "ON DUPLICATE KEY UPDATE o=VALUES(o), h=VALUES(h), l=VALUES(l), c=VALUES(c), v=VALUES(v), src='hl'"), rows[i:i + 1000])
        await s.commit()
    return len(rows)


async def _last_candle_ms(coin: str, tf: str) -> int | None:
    sf = get_session_factory()
    async with sf() as s:
        v = (await s.execute(text("SELECT MAX(t) FROM ut_candles WHERE coin=:c AND tf=:tf"), {"c": coin, "tf": tf})).scalar()
    return rt.dt_to_ms(v) if v else None


GAP_MERGE = 6          # gaps closer than this many bars are one read (20 weight each)


async def _gaps(coin: str, tf: str, start_ms: int, end_ms: int) -> list[tuple[int, int]]:
    """Stretches of [start, end) with no stored bar of this coin and tf,
    merged when closer than GAP_MERGE bars. Bars built from fills count as
    stored (owner, 2026-11-04: read the venue only for what the fills do not
    cover)."""
    step = 60_000 if tf == "1m" else 3_600_000
    sf = get_session_factory()
    async with sf() as s:
        rows = (await s.execute(text("SELECT t FROM ut_candles WHERE coin=:c AND tf=:tf AND t >= :a AND t < :b ORDER BY t"),
                                {"c": coin, "tf": tf, "a": rt.ms_to_dt(start_ms), "b": rt.ms_to_dt(end_ms)})).scalars().all()
    have = [rt.dt_to_ms(t) for t in rows]
    gaps: list[list[int]] = []
    cur = start_ms
    for t in have + [end_ms]:
        if t - cur >= step:
            gaps.append([cur, t])
        cur = t + step
    merged: list[list[int]] = []
    for g in gaps:
        if merged and g[0] - merged[-1][1] <= GAP_MERGE * step:
            merged[-1][1] = g[1]
        else:
            merged.append(g)
    return [(a, b) for a, b in merged if b - a >= step]


async def candles(run: Run, coin: str, tf: str, days: int) -> int:
    """Fetch only the stretches that have no stored bar (from fills or an
    earlier read) inside the last `days`, in answers of at most CANDLE_MAX
    bars. Returns the number of bars stored. The current open bar is read
    again next time."""
    step = 60_000 if tf == "1m" else 3_600_000
    now_ms = int(time.time() * 1000)
    window_start = now_ms - days * rt.DAY_MS
    stored = 0
    gaps = await _gaps(coin, tf, window_start - (window_start % step), now_ms)
    if not gaps:
        return 0
    # every read costs 20 plus 1 per 60 bars: scattered gaps can cost more than
    # one read of the whole window (12 % of coin-hours are dense in the stored
    # fills, most coins are patchy), so take the cheaper of the two
    per_gap = sum(20 + -(-((g1 - g0) // step) // 60) for g0, g1 in gaps)
    whole = 20 + -(-((now_ms - window_start) // step) // 60)
    if len(gaps) > 1 and per_gap >= whole:
        gaps = [(window_start - (window_start % step), now_ms)]
    for g0, g1 in gaps:
        start = g0
        while start < g1:
            end = min(g1, start + CANDLE_MAX * step)
            body = await hl_post(run, {"type": "candleSnapshot", "req": {"coin": coin, "interval": tf, "startTime": start, "endTime": end}})
            if body is None:
                run.notes.append(f"candles {coin} {tf} failed")
                return stored
            stored += await _store_candles(coin, tf, body)
            if len(body) < CANDLE_MAX:
                break
            start = int(body[-1]["t"]) + step
    return stored


# ---------------------------------------------------------------------------
# open interest and funding snapshot
# ---------------------------------------------------------------------------

async def oi_snapshot(run: Run, with_dexes: bool) -> int:
    """metaAndAssetCtxs for the main perps (and the builder dexes when asked)
    -> ut_market_drift (coin, hour) oi, funding, mark."""
    hour = datetime.utcnow().replace(minute=0, second=0, microsecond=0)
    rows = []

    def take(body, dex: str | None):
        if not isinstance(body, list) or len(body) < 2:
            return
        uni = (body[0] or {}).get("universe") or []
        for i, a in enumerate(uni):
            if i >= len(body[1]) or not a.get("name"):
                continue
            ctx = body[1][i] or {}
            name = str(a["name"])
            if dex and ":" not in name:
                name = f"{dex}:{name}"
            try:
                rows.append({"c": name, "t": hour, "oi": float(ctx.get("openInterest") or 0) * float(ctx.get("markPx") or 0),
                             "f": float(ctx.get("funding") or 0), "m": float(ctx.get("markPx") or 0)})
            except (TypeError, ValueError):
                continue

    take(await hl_post(run, {"type": "metaAndAssetCtxs"}), None)
    if with_dexes:
        sf = get_session_factory()
        async with sf() as s:
            dexes = (await s.execute(text(
                "SELECT DISTINCT SUBSTRING_INDEX(coin, ':', 1) FROM ut_universe WHERE kind='hip3' AND last_seen >= UTC_TIMESTAMP() - INTERVAL 2 DAY"))).scalars().all()
        for dex in dexes:
            take(await hl_post(run, {"type": "metaAndAssetCtxs", "dex": dex}), dex)
    if rows:
        sf = get_session_factory()
        async with sf() as s:
            for i in range(0, len(rows), 500):
                await s.execute(text(
                    "INSERT INTO ut_market_drift (coin, t, oi, funding, mark) VALUES (:c, :t, :oi, :f, :m) "
                    "ON DUPLICATE KEY UPDATE oi=VALUES(oi), funding=VALUES(funding), mark=VALUES(mark)"), rows[i:i + 500])
            await s.commit()
    return len(rows)


# ---------------------------------------------------------------------------
# wallet ledger (first deposit)
# ---------------------------------------------------------------------------

def first_funding(wallet: str, items: list[dict]) -> dict | None:
    """The earliest ledger item that brought money in: an Arbitrum deposit
    (type deposit, hash = the Arbitrum tx) or an internal transfer received
    from another Hyperliquid account (type send with destination = wallet,
    the sender is the funder). Returns {at_ms, usd, tx, kind, funder}."""
    w = wallet.lower()
    best = None
    for it in items or []:
        d = it.get("delta") or {}
        kind = str(d.get("type") or "")
        t = int(it.get("time") or 0)
        row = None
        if kind == "deposit":
            row = {"at_ms": t, "usd": float(d.get("usdc") or 0), "tx": it.get("hash"), "kind": "deposit", "funder": None}
        elif kind in ("send", "internalTransfer") and str(d.get("destination") or "").lower() == w:
            usd = float(d.get("usdcValue") or d.get("usdc") or d.get("amount") or 0)
            row = {"at_ms": t, "usd": usd, "tx": it.get("hash"), "kind": "internal_transfer", "funder": str(d.get("user") or "").lower() or None}
        elif kind == "subAccountTransfer" and str(d.get("user") or "").lower() != w:
            row = {"at_ms": t, "usd": float(d.get("usdc") or 0), "tx": it.get("hash"), "kind": "sub_account", "funder": str(d.get("user") or "").lower() or None}
        if row and row["at_ms"] > 0 and (best is None or row["at_ms"] < best["at_ms"]):
            best = row
    return best


async def ledger(run: Run, wallet: str) -> dict | None:
    # startTime well before the venue existed (the probe with 0 answered an
    # empty list for a funded wallet; a real epoch in ms answers the history)
    body = await hl_post(run, {"type": "userNonFundingLedgerUpdates", "user": wallet, "startTime": 1_600_000_000_000})
    if body is None:
        return None
    ff = first_funding(wallet, body if isinstance(body, list) else [])
    sf = get_session_factory()
    async with sf() as s:
        await s.execute(text(
            "INSERT INTO ut_wallet_meta (wallet, first_deposit_at, first_deposit_usd, first_deposit_tx, first_deposit_kind, funder, "
            "ledger_items, checked_at) VALUES (:w, :at, :usd, :tx, :k, :f, :n, UTC_TIMESTAMP()) ON DUPLICATE KEY UPDATE "
            "first_deposit_at=VALUES(first_deposit_at), first_deposit_usd=VALUES(first_deposit_usd), first_deposit_tx=VALUES(first_deposit_tx), "
            "first_deposit_kind=VALUES(first_deposit_kind), funder=COALESCE(VALUES(funder), funder), ledger_items=VALUES(ledger_items), "
            "checked_at=UTC_TIMESTAMP()"),
            {"w": wallet, "at": rt.ms_to_dt(ff["at_ms"]) if ff else None, "usd": ff["usd"] if ff else None,
             "tx": (ff["tx"] or None) if ff else None, "k": ff["kind"] if ff else None, "f": ff["funder"] if ff else None,
             "n": len(body) if isinstance(body, list) else 0})
        await s.commit()
    return ff


# ---------------------------------------------------------------------------
# fills for feed wallets over $250K (two pages)
# ---------------------------------------------------------------------------

async def pull_fills(run: Run, wallet: str, days: int = 90) -> int:
    now_ms = int(time.time() * 1000)
    start = now_ms - days * rt.DAY_MS
    total = 0
    sf = get_session_factory()
    for _page in range(FILLS_PAGES):
        body = await hl_post(run, {"type": "userFillsByTime", "user": wallet, "startTime": start, "endTime": now_ms, "aggregateByTime": True})
        if body is None:
            break
        batch = [f for f in body if f.get("tid") is not None]
        rows = []
        for f in batch:
            n = rt.norm_fill(f, wallet)
            rows.append({"w": wallet, "tid": n["tid"], "t": rt.ms_to_dt(n["time"]), "coin": n["coin"], "dir": n["dir"][:24],
                         "side": n["side"][:1] or "B", "px": n["px"], "sz": n["sz"], "pnl": n["closed_pnl"], "fee": n["fee"],
                         "ft": (f.get("feeToken") or None), "cr": (None if n["crossed"] is None else int(n["crossed"])),
                         "sp": n["start_position"], "h": n["hash"], "oid": n["oid"], "liq": 1 if n["liquidation"] else 0})
        if rows:
            async with sf() as s:
                for i in range(0, len(rows), 500):
                    await s.execute(text(
                        "INSERT INTO hl_fills (wallet, tid, time, coin, dir, side, px, sz, closed_pnl, fee, fee_token, crossed, "
                        "start_position, hash, oid, liquidation) VALUES (:w, :tid, :t, :coin, :dir, :side, :px, :sz, :pnl, :fee, :ft, :cr, "
                        ":sp, :h, :oid, :liq) ON DUPLICATE KEY UPDATE closed_pnl=VALUES(closed_pnl), fee=VALUES(fee), "
                        "start_position=VALUES(start_position)"), rows[i:i + 500])
                await s.commit()
            total += len(rows)
        if len(body) < 2000:
            break
        start = int(body[-1]["time"]) + 1
    return total


# ---------------------------------------------------------------------------
# the reader passes
# ---------------------------------------------------------------------------

async def coins_for_1h(limit: int | None = None) -> list[str]:
    """Perp coins (main and builder) that a candidate opened a position on in
    the last 90 days, most entries first, minus spot and outcome markets."""
    sf = get_session_factory()
    async with sf() as s:
        rows = (await s.execute(text(
            "SELECT f.coin, COUNT(*) n FROM hl_fills f JOIN ut_universe u ON u.coin = f.coin AND u.kind IN ('perp','hip3') "
            "WHERE f.time >= UTC_TIMESTAMP() - INTERVAL 90 DAY AND f.dir LIKE 'Open%' GROUP BY f.coin ORDER BY n DESC"))).all()
    coins = [c for c, _n in rows]
    return coins[:limit] if limit else coins


async def pass_pending() -> bool:
    """True while a daily pass has started and not finished (the cap stopped
    it, or the API restarted): ut_queue row kind 'pass', item 'daily_1h'."""
    sf = get_session_factory()
    async with sf() as s:
        row = (await s.execute(text("SELECT done_at FROM ut_queue WHERE kind='pass' AND item='daily_1h'"))).first()
    return bool(row) and row[0] is None


async def _pass_mark(done: bool) -> None:
    sf = get_session_factory()
    async with sf() as s:
        if done:
            await s.execute(text("UPDATE ut_queue SET done_at=UTC_TIMESTAMP(), note='complete' WHERE kind='pass' AND item='daily_1h'"))
        else:
            await s.execute(text(
                "INSERT INTO ut_queue (kind, item, queued_at, done_at, note) VALUES ('pass', 'daily_1h', UTC_TIMESTAMP(), NULL, 'started') "
                "ON DUPLICATE KEY UPDATE queued_at=UTC_TIMESTAMP(), done_at=NULL, note='started'"))
        await s.commit()


async def daily(run: Run) -> dict:
    """02:30 UTC (and on a resume): universe snapshot, then 1 h candles for
    every candidate coin, most entries first, reading only the stretches the
    stored bars do not cover; then the queue (ledger, fills, 1 m candles)
    until the day's weight is spent. The pass is marked pending until it
    completes, so a restart or the cap resumes it instead of waiting a day."""
    out = {"universe": 0, "coins_1h": 0, "bars_1h": 0, "reads_1h": 0, "coins_no_read": 0, "queue": 0}
    await _pass_mark(done=False)
    try:
        u = await universe_snapshot(run)
        out["universe"] = u["coins"]
        # wallet age is in every report line and S4 needs it: the ledgers of the
        # flagged wallets go before the remaining candle coins (about 21 weight each)
        out["queue"] = await serve_queue(run, kinds=("ledger",), limit=LEDGER_FIRST)
        coins = await coins_for_1h()
        for coin in coins:
            before = run.requests
            n = await candles(run, coin, "1h", H1_DAYS)
            out["coins_1h"] += 1
            out["bars_1h"] += n
            out["reads_1h"] += run.requests - before
            if run.requests == before:
                out["coins_no_read"] += 1
        out["queue"] += await serve_queue(run)
        await _pass_mark(done=True)
    except Budget as exc:
        run.notes.append(str(exc))
        logger.info("ut daily: %s", exc)
    run.notes.append(f"1h: {out['coins_1h']} coins, {out['reads_1h']} reads, {out['coins_no_read']} covered by stored bars")
    return out


LEDGER_FIRST = 300


async def serve_queue(run: Run, kinds: tuple[str, ...] = ("ledger", "fills", "candles_1m"), limit: int = 400) -> int:
    sf = get_session_factory()
    done = 0
    async with sf() as s:
        items = (await s.execute(text(
            "SELECT kind, item FROM ut_queue WHERE done_at IS NULL AND kind IN :k ORDER BY FIELD(kind, 'ledger', 'candles_1m', 'fills'), queued_at LIMIT :n"
        ).bindparams(bindparam("k", expanding=True)), {"k": list(kinds), "n": limit})).all()
    for kind, item in items:
        if kind not in kinds:
            continue
        note = None
        if kind == "ledger":
            ff = await ledger(run, item)
            note = f"{ff['kind']} {ff['usd']:.0f}" if ff else "no funding item"
        elif kind == "fills":
            note = f"{await pull_fills(run, item)} fills"
        elif kind == "candles_1m":
            note = f"{await candles(run, item, '1m', M1_DAYS)} bars"
        async with sf() as s:
            await s.execute(text("UPDATE ut_queue SET done_at=UTC_TIMESTAMP(), note=:n WHERE kind=:k AND item=:i"),
                            {"n": (note or "")[:120], "k": kind, "i": item})
            await s.commit()
        done += 1
    return done


async def hourly(run: Run) -> dict:
    """Every hour: the open-interest snapshot (builder dexes every 6 h); at
    00, 06, 12 and 18 UTC the 1 m candles of the coins flagged wallets trade
    (ut_queue candles_1m rows are re-queued by the worker)."""
    out = {"oi_rows": 0, "queue": 0}
    hour = datetime.utcnow().hour
    try:
        out["oi_rows"] = await oi_snapshot(run, with_dexes=(hour % 6 == 0))
        if hour % 6 == 0:
            out["queue"] = await serve_queue(run, kinds=("candles_1m", "ledger"))
    except Budget as exc:
        run.notes.append(str(exc))
        logger.info("ut hourly: %s", exc)
    return out
