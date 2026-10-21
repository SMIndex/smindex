"""Hyperliquid last trade time for the Discover list (2026-10-21).

The list used to show when OUR hourly leaderboard snapshot first saw a wallet's
volume grow. Hyperliquid's leaderboard file lags, so that could trail the real
fill by over an hour (0xbf73: shown 04:07, real fill 02:44).

Two sources, the newest wins:
  * live: the real-time tracker (hl/tracker.py) notes every fill time it sees
    for the wallets it follows (ws userFills, including the subscription
    snapshot; poll-tier position changes at detection time). Zero requests.
  * hourly: after each hourly leaderboard ingest, each listed wallet's newest
    fill is read with userFillsByTime from the previous read (first read: 24 h)
    for the wallet AND its sub-accounts (Hyperliquid's leaderboard counts both;
    the per-user endpoints do not). Background priority, paced, once an hour:
    no per-minute requests. Sub-account lists are cached for a day.

A wallet the tracker does not follow shows the hourly value (fallback); the
tracker's 10 real-time slots are Hyperliquid's per-IP cap and adding wallets to
its 15 s poll tier would add per-minute requests, so the tracked set is not
widened here.
"""
import asyncio
import time
from datetime import datetime, timezone

from sqlalchemy import text

from app.db.database import get_session_factory
from app.services.hyperliquid import client as hl_client
from app.utils.logger import get_logger

logger = get_logger(__name__)

LISTED_PER_WINDOW = 50              # Discover asks for 50 per window
FIRST_LOOKBACK_MS = 24 * 3600 * 1000
OVERLAP_MS = 5 * 60 * 1000          # re-read a little before the last read
FULL_PAGE = 2000                    # userFillsByTime page cap (oldest first)
SUBS_TTL_SEC = 24 * 3600
PAUSE_SEC = 0.5

_hourly: dict[str, int] = {}        # wallet -> newest fill ms (wallet + sub-accounts)
_acct_last: dict[str, int] = {}     # account -> newest fill ms seen
_acct_read: dict[str, int] = {}     # account -> ms of its last successful read
_subs: dict[str, tuple[float, list[str]]] = {}
_task: asyncio.Task | None = None
_last_run: dict = {}


def last_trade(wallet: str) -> tuple[int | None, str | None]:
    """(newest fill ms, 'live' | 'hourly') for a wallet, or (None, None)."""
    from app.services.hyperliquid import tracker
    w = wallet.lower()
    live = tracker.last_fill_ms.get(w)
    hourly = _hourly.get(w)
    if live is None and hourly is None:
        return None, None
    if hourly is None or (live is not None and live >= hourly):
        return live, "live"
    return hourly, "hourly"


def iso(ms: int) -> str:
    # naive UTC, the same shape as every other timestamp in the list payload
    return datetime.fromtimestamp(ms / 1000, timezone.utc).replace(tzinfo=None).isoformat(timespec="seconds")


async def listed_wallets() -> list[str]:
    """Top LISTED_PER_WINDOW of every HL window in the latest ingest batch."""
    sf = get_session_factory()
    out: list[str] = []
    async with sf() as s:
        for period in ("all", "day", "week", "month"):
            ts = (await s.execute(text(
                "SELECT MAX(timestamp) FROM leaderboard_snapshots "
                "WHERE exchange='hl' AND period=:p"), {"p": period})).scalar()
            if ts is None:
                continue
            rows = (await s.execute(text(
                "SELECT wallet_address FROM leaderboard_snapshots WHERE exchange='hl' "
                "AND period=:p AND timestamp=:t ORDER BY `rank` LIMIT :n"),
                {"p": period, "t": ts, "n": LISTED_PER_WINDOW})).scalars().all()
            out.extend(w.lower() for w in rows)
    return list(dict.fromkeys(out))


async def _post(payload: dict):
    resp = await hl_client.post_info(payload, priority=hl_client.BACKGROUND, timeout=20.0)
    if resp.status_code != 200:
        raise RuntimeError(f"HTTP {resp.status_code}")
    return await hl_client.decode_json(resp)


async def _accounts(wallet: str, stats: dict) -> list[str]:
    hit = _subs.get(wallet)
    if hit and time.monotonic() - hit[0] < SUBS_TTL_SEC:
        return [wallet] + hit[1]
    try:
        body = await _post({"type": "subAccounts", "user": wallet})
        stats["requests"] += 1
        subs = [str(x.get("subAccountUser", "")).lower() for x in (body or []) if x.get("subAccountUser")]
        _subs[wallet] = (time.monotonic(), subs)
    except Exception as exc:
        stats["errors"] += 1
        logger.debug("hl last trade: subAccounts failed for %s: %s", wallet[:10], exc)
        subs = (hit or (0, []))[1]
    finally:
        await asyncio.sleep(PAUSE_SEC)
    return [wallet] + subs


async def _read_account(acct: str, now_ms: int, stats: dict) -> int | None:
    start = _acct_read.get(acct, now_ms - FIRST_LOOKBACK_MS) - OVERLAP_MS
    try:
        fills = await _post({"type": "userFillsByTime", "user": acct,
                             "startTime": start, "aggregateByTime": True})
        stats["requests"] += 1
        if isinstance(fills, list) and len(fills) >= FULL_PAGE:
            # oldest-first page is full: the newest fills are past it, ask for the newest instead
            await asyncio.sleep(PAUSE_SEC)
            fills = await _post({"type": "userFills", "user": acct, "aggregateByTime": True})
            stats["requests"] += 1
            stats["full_pages"] += 1
        newest = max((int(f["time"]) for f in fills or [] if f.get("time")), default=None)
        _acct_read[acct] = now_ms
        if newest is not None and newest > _acct_last.get(acct, 0):
            _acct_last[acct] = newest
    except Exception as exc:
        stats["errors"] += 1
        logger.debug("hl last trade: fills read failed for %s: %s", acct[:10], exc)
    finally:
        await asyncio.sleep(PAUSE_SEC)
    return _acct_last.get(acct)


async def refresh(wallets: list[str] | None = None) -> dict:
    """One hourly pass over the listed wallets. Returns the stats it logs."""
    t0 = time.monotonic()
    wallets = wallets if wallets is not None else await listed_wallets()
    stats = {"wallets": len(wallets), "accounts": 0, "requests": 0, "full_pages": 0,
             "errors": 0, "with_fill": 0}
    w0 = hl_client.get_metrics()["per_class"]["background"]["weight"]
    for w in wallets:
        accts = await _accounts(w, stats)
        stats["accounts"] += len(accts)
        now_ms = int(time.time() * 1000)
        newest = [n for n in [await _read_account(a, now_ms, stats) for a in accts] if n]
        if newest:
            _hourly[w] = max(newest)
            stats["with_fill"] += 1
    stats["weight"] = round(hl_client.get_metrics()["per_class"]["background"]["weight"] - w0)
    stats["duration_sec"] = round(time.monotonic() - t0, 1)
    _last_run.clear()
    _last_run.update(stats)
    logger.info("hl last trade refresh: %s", stats)
    return stats


def start_refresh() -> None:
    """Called after each hourly ingest; a pass still running is never doubled."""
    global _task
    if _task is not None and not _task.done():
        logger.info("hl last trade refresh still running, this hour skipped")
        return
    _task = asyncio.get_event_loop().create_task(_safe_refresh())


async def _safe_refresh() -> None:
    try:
        await refresh()
    except Exception:
        logger.exception("hl last trade refresh failed (list keeps the previous values)")


def get_last_run() -> dict:
    return dict(_last_run)
