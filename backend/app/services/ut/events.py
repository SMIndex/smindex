"""ut_events: listings and delistings (ut_universe diff, first candle for the
backfill), funding flips (hl_funding rate sign changes), scheduled macro
releases (events_macro.csv), manual events (events.csv) and big moves (1 h
candles against the per-coin threshold).

Direction: a big move carries its own; a listing or delisting is 'unknown'
(no entry on Hyperliquid can precede the coin's own listing here); a macro
release or funding flip takes the direction of the 2 h close-to-close move
after it (BTC for a macro release) when that move is at least half the
coin's threshold, else 'unknown'. Unknown never matches an entry (fewer
flags)."""
from __future__ import annotations

import csv
import os
from datetime import datetime, timedelta

from sqlalchemy import text

from app.db.database import get_session_factory
from app.services.ut import common, rules
from app.utils.logger import get_logger

logger = get_logger(__name__)

FUNDING_MIN_ABS = 1e-5          # a flip needs a real rate on both sides (0.001 %/h)
BIG_MOVE_MERGE_H = 3            # consecutive big-move hours of one coin and direction are one event


async def _upsert(rows: list[dict]) -> int:
    if not rows:
        return 0
    sf = get_session_factory()
    async with sf() as s:
        for i in range(0, len(rows), 500):
            await s.execute(text(
                "INSERT INTO ut_events (time, coin, kind, direction, magnitude, source, note) "
                "VALUES (:t, :c, :k, :d, :m, :s, :n) ON DUPLICATE KEY UPDATE direction=VALUES(direction), "
                "magnitude=VALUES(magnitude), note=VALUES(note)"), rows[i:i + 500])
        await s.commit()
    return len(rows)


async def _direction_after(coin: str, t: datetime, thr: float) -> tuple[str, float | None]:
    """Sign of the 2 h close-to-close move after t from 1 h candles; 'unknown'
    under half the threshold or without bars."""
    bars = await common.load_candles(coin, "1h", t - timedelta(hours=1), t + timedelta(hours=3))
    before = [b for b in bars if b["t"] <= common.ms(t)]
    after = [b for b in bars if b["T"] <= common.ms(t) + 2 * 3_600_000 and b["t"] >= common.ms(t)]
    if not before or not after:
        return "unknown", None
    p0, p1 = before[-1]["c"], after[-1]["c"]
    if p0 <= 0:
        return "unknown", None
    mv = (p1 / p0 - 1.0) * 100.0
    if abs(mv) >= thr / 2.0:
        return ("up" if mv > 0 else "down"), round(mv, 4)
    return "unknown", round(mv, 4)


def _read_csv(name: str) -> list[dict]:
    path = os.path.join(common.DATA_DIR, name)
    if not os.path.exists(path):
        logger.warning("ut events: %s missing", path)
        return []
    with open(path, newline="", encoding="utf-8") as fh:
        return [r for r in csv.DictReader(fh) if any((v or "").strip() for v in r.values())]


def _parse_time(s: str) -> datetime | None:
    s = (s or "").strip().replace("T", " ").replace("Z", "")
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    return None


async def macro(thr_btc: float) -> int:
    rows = []
    for r in _read_csv("events_macro.csv"):
        t = _parse_time(r.get("time_utc", ""))
        if not t:
            continue
        d, mv = await _direction_after("BTC", t, thr_btc) if t < common.now_utc() else ("unknown", None)
        rows.append({"t": t, "c": "ALL", "k": "macro", "d": d, "m": mv, "s": "events_macro.csv", "n": (r.get("note") or "")[:240]})
    return await _upsert(rows)


async def manual(thresholds: dict[str, dict]) -> int:
    rows = []
    for r in _read_csv("events.csv"):
        t = _parse_time(r.get("time_utc", ""))
        if not t:
            continue
        coin = (r.get("coin") or "ALL").strip() or "ALL"
        d = (r.get("direction") or "unknown").strip().lower()
        mv = None
        if d not in ("up", "down"):
            ref = "BTC" if coin == "ALL" else coin
            thr = thresholds.get(ref, {}).get("thr", 5.0)
            d, mv = await _direction_after(ref, t, thr) if t < common.now_utc() else ("unknown", None)
        rows.append({"t": t, "c": coin, "k": "manual", "d": d, "m": mv, "s": "events.csv", "n": (r.get("note") or "")[:240]})
    return await _upsert(rows)


async def funding_flips(thresholds: dict[str, dict], since: datetime) -> int:
    """Per coin hourly rate from hl_funding (every wallet's row of one hour
    carries the same rate), sign changes -> funding_flip events."""
    sf = get_session_factory()
    async with sf() as s:
        rows = (await s.execute(text(
            "SELECT coin, time, MAX(rate) FROM hl_funding WHERE time >= :a AND rate IS NOT NULL GROUP BY coin, time ORDER BY coin, time"),
            {"a": since})).all()
    out = []
    prev: dict[str, tuple[datetime, float]] = {}
    for coin, t, rate in rows:
        r = float(rate)
        p = prev.get(coin)
        prev[coin] = (t, r)
        if p is None or abs(r) < FUNDING_MIN_ABS or abs(p[1]) < FUNDING_MIN_ABS:
            continue
        if (r > 0) != (p[1] > 0) and (t - p[0]) <= timedelta(hours=2):
            thr = thresholds.get(coin, {}).get("thr", 5.0)
            d, mv = await _direction_after(coin, t, thr)
            out.append({"t": t, "c": coin, "k": "funding_flip", "d": d, "m": mv, "s": "hl_funding",
                        "n": f"rate {p[1]:+.6f} to {r:+.6f} per hour"})
    return await _upsert(out)


async def listings(first_snapshot: datetime | None) -> int:
    """A coin first seen after the first universe snapshot is a listing at
    first_seen; for the backfill a coin whose first stored 1 h candle starts
    more than a day after the candle window began is a listing at that bar
    (ut_universe.first_seen is moved there, source 'candle'). A coin not seen
    for over a day is a delisting at last_seen."""
    sf = get_session_factory()
    async with sf() as s:
        uni = (await s.execute(text("SELECT coin, kind, first_seen, last_seen, first_seen_src FROM ut_universe"))).all()
        firsts = dict((await s.execute(text("SELECT coin, MIN(t) FROM ut_candles WHERE tf='1h' GROUP BY coin"))).all())
        latest = (await s.execute(text("SELECT MAX(last_seen) FROM ut_universe"))).scalar()
        cmin = (await s.execute(text("SELECT MIN(t) FROM ut_candles WHERE tf='1h'"))).scalar()
    rows = []
    moves = []
    for coin, kind, fs, ls, src in uni:
        if kind == "spot":
            continue
        fc = firsts.get(coin)
        if fc is not None and cmin is not None and fc > cmin + timedelta(days=1) and (fs is None or fc < fs):
            rows.append({"t": fc, "c": coin, "k": "listing", "d": "unknown", "m": None, "s": "candle", "n": f"first 1h bar ({kind})"})
            moves.append({"c": coin, "t": fc})
        elif first_snapshot is not None and fs is not None and fs > first_snapshot + timedelta(hours=1) and src == "snapshot":
            rows.append({"t": fs, "c": coin, "k": "listing", "d": "unknown", "m": None, "s": "snapshot", "n": f"appeared in the daily universe ({kind})"})
        if latest is not None and ls is not None and ls < latest - timedelta(days=1):
            rows.append({"t": ls, "c": coin, "k": "delisting", "d": "unknown", "m": None, "s": "snapshot", "n": f"gone from the daily universe ({kind})"})
    if moves:
        async with sf() as s:
            for m in moves:
                await s.execute(text("UPDATE ut_universe SET first_seen=:t, first_seen_src='candle' WHERE coin=:c"), m)
            await s.commit()
    return await _upsert(rows)


async def big_moves(thresholds: dict[str, dict], since: datetime) -> int:
    """Hourly big moves per coin from 1 h candles, consecutive hours of one
    direction merged into one event (time = first hour, magnitude = max)."""
    out = []
    t1 = common.now_utc()
    for coin, st in thresholds.items():
        c1h = await common.load_candles(coin, "1h", since - timedelta(hours=3), t1)
        if len(c1h) < 3:
            continue
        mv, _n = rules.big_moves(c1h, st["thr"])
        mv = [m for m in mv if m["t"] >= common.ms(since)]
        mv.sort(key=lambda m: (m["direction"], m["t"]))
        cur = None
        for m in mv:
            if cur and cur["direction"] == m["direction"] and m["t"] - cur["last"] <= BIG_MOVE_MERGE_H * 3_600_000:
                cur["last"] = m["t"]
                cur["magnitude"] = max(cur["magnitude"], m["magnitude"])
                continue
            if cur:
                out.append(cur)
            cur = {"coin": coin, "direction": m["direction"], "t": m["t"], "last": m["t"], "magnitude": m["magnitude"]}
        if cur:
            out.append(cur)
    rows = [{"t": common.dt(m["t"]), "c": m["coin"], "k": "big_move", "d": m["direction"], "m": m["magnitude"], "s": "candles",
             "n": f"{m['magnitude']:.2f}% within 2h ({(m['last'] - m['t']) // 3_600_000 + 1} h)"} for m in out]
    return await _upsert(rows)


async def build(*, full: bool) -> dict:
    """Daily (full=True, 90 days) or hourly (last 48 h of big moves and
    funding flips only)."""
    thresholds = await common.latest_thresholds()
    now = common.now_utc()
    since = now - timedelta(days=common.WINDOW_DAYS) if full else now - timedelta(hours=48)
    out = {}
    if full:
        sf = get_session_factory()
        async with sf() as s:
            first_snapshot = (await s.execute(text("SELECT MIN(first_seen) FROM ut_universe WHERE first_seen_src='snapshot'"))).scalar()
        out["listings"] = await listings(first_snapshot)
        out["macro"] = await macro(thresholds.get("BTC", {}).get("thr", 3.0))
        out["manual"] = await manual(thresholds)
    out["funding_flips"] = await funding_flips(thresholds, since)
    out["big_moves"] = await big_moves(thresholds, since)
    logger.info("ut events: %s", out)
    return out
