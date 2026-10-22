"""Hyperliquid open positions for the Discover "Open" column and the profile
page (2026-10-22), from the trades feed service (scripts/hl_trades_feed.py).

The feed follows every listed or on-screen wallet's trades and re-reads its
positions (clearinghouseState, 2 weight) within seconds of a trade that can
change them; each read lands in hl_open_positions. This module only reads that
table (no Hyperliquid request) and tells the feed which wallets are on screen
(hl_feed_watch): Discover rows for WATCH_SEC after the page last asked, and
profile pages for PROFILE_SEC (the feed then re-reads on every trade, not only
on a count change). The feed gives on-screen wallets a safety read every 5 min.
"""
import json
import time

from sqlalchemy import bindparam, text

from app.db.database import get_session_factory
from app.utils.logger import get_logger

logger = get_logger(__name__)

CACHE_SEC = 1.0
WATCH_SEC = 600
PROFILE_SEC = 60
WATCH_WRITE_EVERY = 60.0
PROFILE_WRITE_EVERY = 20.0
MMAP_SEC = 60.0

_rows: dict[str, tuple[float, dict | None]] = {}
_watch_written: dict[str, float] = {}
_profile_written: dict[str, float] = {}
_mmap: tuple[float, dict] = (0.0, {})


def _row(r) -> dict:
    return {"n": int(r[1]), "markets": json.loads(r[2]) if isinstance(r[2], str) else r[2],
            "state_ms": int(r[3]), "read_ms": int(r[4]),
            "trade_ms": int(r[5]) if r[5] is not None else None}


async def rows(wallets: list[str]) -> dict[str, dict | None]:
    """Newest feed read per wallet ({n, markets, state_ms, read_ms, trade_ms});
    None = the feed has not read it yet."""
    now = time.monotonic()
    ws = [w.lower() for w in wallets]
    need = [w for w in ws if w not in _rows or now - _rows[w][0] >= CACHE_SEC]
    if need:
        sf = get_session_factory()
        async with sf() as s:
            got = (await s.execute(text(
                "SELECT wallet, n, markets, state_ms, read_ms, trade_ms FROM hl_open_positions WHERE wallet IN :w"
            ).bindparams(bindparam("w", expanding=True)), {"w": need})).all()
        found = {r[0]: _row(r) for r in got}
        for w in need:
            _rows[w] = (now, found.get(w))
    return {w: _rows[w][1] for w in ws}


async def state_row(wallet: str) -> dict | None:
    """The feed's newest read for one wallet with the raw clearinghouseState
    (profile page), or None."""
    sf = get_session_factory()
    async with sf() as s:
        r = (await s.execute(text(
            "SELECT wallet, n, markets, state_ms, read_ms, trade_ms, state FROM hl_open_positions WHERE wallet = :w"),
            {"w": wallet.lower()})).first()
    if r is None:
        return None
    d = _row(r)
    d["state"] = json.loads(r[6])
    return d


async def watch(wallets: list[str], profile: bool = False) -> None:
    """Tell the feed these wallets are on screen (throttled per wallet)."""
    now = time.monotonic()
    every = PROFILE_WRITE_EVERY if profile else WATCH_WRITE_EVERY
    seen = _profile_written if profile else _watch_written
    todo = [w.lower() for w in wallets if now - seen.get(w.lower(), -1e9) >= every]
    if not todo:
        return
    try:
        sf = get_session_factory()
        async with sf() as s:
            for w in todo:
                if profile:
                    await s.execute(text(
                        "INSERT INTO hl_feed_watch (wallet, seen_until, profile_until) VALUES "
                        "(:w, UTC_TIMESTAMP() + INTERVAL :ws SECOND, UTC_TIMESTAMP() + INTERVAL :ps SECOND) "
                        "ON DUPLICATE KEY UPDATE seen_until = VALUES(seen_until), profile_until = VALUES(profile_until)"),
                        {"w": w, "ws": WATCH_SEC, "ps": PROFILE_SEC})
                else:
                    await s.execute(text(
                        "INSERT INTO hl_feed_watch (wallet, seen_until) VALUES "
                        "(:w, UTC_TIMESTAMP() + INTERVAL :ws SECOND) "
                        "ON DUPLICATE KEY UPDATE seen_until = VALUES(seen_until)"),
                        {"w": w, "ws": WATCH_SEC})
            await s.commit()
        for w in todo:
            seen[w] = now
    except Exception as exc:
        # the table is created by the feed service; before its first start it may not exist
        logger.warning("hl open positions: watch write failed: %s", exc)


async def _market_ids() -> dict:
    global _mmap
    if time.monotonic() - _mmap[0] < MMAP_SEC:
        return _mmap[1]
    from app.services.hyperliquid import profile as hl_profile
    _mmap = (time.monotonic(), await hl_profile._market_map())
    return _mmap[1]


async def summaries(wallets: list[str]) -> dict[str, dict]:
    """{wallet: {count, markets[{market_id, symbol, side}], pending, read_at,
    as_of}}, the shape of /api/traders/hl-active plus when the feed read it and
    Hyperliquid's own time of that state."""
    try:
        got = await rows(wallets)
    except Exception as exc:
        logger.warning("hl open positions: read failed: %s", exc)
        return {}
    mmap = await _market_ids()
    out = {}
    for w, r in got.items():
        if r is None:
            out[w] = {"count": None, "markets": [], "pending": True, "read_at": None, "as_of": None}
            continue
        out[w] = {"count": r["n"],
                  "markets": [{"market_id": mmap.get(str(m["symbol"]).upper()), "symbol": m["symbol"],
                               "side": m["side"]} for m in r["markets"]],
                  "pending": False, "read_at": r["read_ms"] / 1000.0, "as_of": r["state_ms"] / 1000.0}
    return out
