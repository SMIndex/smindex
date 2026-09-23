"""Hyperliquid leaderboard ingest.

Hourly lifespan task: streams the ~34MB Mainnet leaderboard file (41k+ rows),
ranks each window (day/week/month/allTime) by PnL over eligible accounts
(equity + window-volume floors + artifact sanity guard — see constants), and
persists the top 100 per window into `leaderboard_snapshots(exchange='hl')`
+ upserts `trader_profiles(exchange='hl')`.

Rules (implementation prompt, phase 3):
  * NEVER json.loads the whole body — ijson streaming over a spooled temp file.
  * On failure: keep previous data, one retry after 60s, then wait next cycle.
  * `is_hidden` on trader_profiles is preserved — ingest never resurrects a
    profile an admin hid.
  * Retention: hl snapshots older than 180 days deleted at end of each run.
  * Units match the Perpl rows: pnl/volume in USD, roi in PERCENT.
"""
import asyncio
import os
import tempfile
from datetime import datetime, timedelta

import httpx
from sqlalchemy import delete, select

from app.db.database import get_session_factory
from app.db.copy_models import TraderProfile
from app.db.models import LeaderboardSnapshot
from app.utils.logger import get_logger

logger = get_logger(__name__)

LEADERBOARD_URL = "https://stats-data.hyperliquid.xyz/Mainnet/leaderboard"
USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")
INTERVAL_SEC = 3600
FIRST_RUN_DELAY_SEC = 60
RETRY_DELAY_SEC = 60
# Eligibility (matches Hyperliquid's own leaderboard rules): only accounts with
# >= $100k equity rank, AND the window's traded volume must clear a floor —
# $10M for long windows, $1M for short ones (day/week would go near-empty at
# $10M). A final sanity guard drops vault/deposit artifacts where |pnl| is
# wildly out of proportion to volume (e.g. $445M pnl on $16k volume).
MIN_ACCOUNT_VALUE_USD = 100_000
MIN_WINDOW_VOLUME_USD = {"all": 10_000_000, "month": 10_000_000,
                         "week": 1_000_000, "day": 1_000_000}
PNL_TO_VOLUME_SANITY_MAX = 50  # exclude when abs(pnl) > 50 * max(volume, 1)
# Ratio guard (HL_QUALITY_REPORT A2): positive pnl above 0.25x window volume is
# a detached ~1% tail of non-trading accounts (measured p95=0.06, p99=0.27 over
# 4306 eligible rows) — e.g. $91.8M pnl on $105.6M lifetime volume. Losers are
# not capped: a big negative pnl is not a ranking artifact.
PNL_TO_VOLUME_RATIO_MAX = 0.25
TOP_N = 100
RETENTION_DAYS = 180
WINDOW_MAP = {"day": "day", "week": "week", "month": "month", "allTime": "all"}

_task: asyncio.Task | None = None
_last_run: dict = {}   # observability: ts, rows_scanned, inserted, duration


def _rss_mb() -> float | None:
    try:
        import psutil
        return psutil.Process(os.getpid()).memory_info().rss / (1024 * 1024)
    except Exception:
        return None


def _parse_file(path: str) -> tuple[dict[str, list[dict]], int, dict[str, str], dict]:
    """Stream-parse the leaderboard file. Returns (top_by_window, rows_scanned,
    display_names, filter_stats). Runs in a thread executor (sync ijson)."""
    import heapq
    import ijson

    heaps: dict[str, list] = {w: [] for w in WINDOW_MAP}
    display_names: dict[str, str] = {}
    scanned = 0
    seq = 0  # heap tiebreaker — dict entries are not orderable
    eligible_per_window = {WINDOW_MAP[w]: 0 for w in WINDOW_MAP}
    excluded_by_floor = 0    # account-value or window-volume floor
    excluded_by_sanity = 0   # pnl/volume artifact guard
    excluded_by_ratio = 0    # pnl > 0.25x volume tail (report A2)

    with open(path, "rb") as f:
        for row in ijson.items(f, "leaderboardRows.item"):
            scanned += 1
            try:
                acct_val = float(row.get("accountValue") or 0)
            except (TypeError, ValueError):
                continue
            wallet = (row.get("ethAddress") or "").lower()
            if not wallet:
                continue
            if acct_val < MIN_ACCOUNT_VALUE_USD:
                excluded_by_floor += 1
                continue
            name = row.get("displayName")
            if name and str(name) not in ("None", ""):
                display_names[wallet] = str(name)[:100]
            for window, perf in (row.get("windowPerformances") or []):
                if window not in WINDOW_MAP or not isinstance(perf, dict):
                    continue
                period = WINDOW_MAP[window]
                try:
                    pnl = float(perf.get("pnl") or 0)
                    roi = float(perf.get("roi") or 0)
                    vlm = float(perf.get("vlm") or 0)
                except (TypeError, ValueError):
                    continue
                if vlm < MIN_WINDOW_VOLUME_USD[period]:
                    excluded_by_floor += 1
                    continue
                if abs(pnl) > PNL_TO_VOLUME_SANITY_MAX * max(vlm, 1):
                    excluded_by_sanity += 1
                    continue
                if pnl > PNL_TO_VOLUME_RATIO_MAX * vlm:
                    excluded_by_ratio += 1
                    continue
                eligible_per_window[period] += 1
                entry = (pnl, seq, {"wallet": wallet, "pnl": pnl,
                                    "roi_pct": roi * 100, "volume": vlm,
                                    "account_value": acct_val})
                seq += 1
                h = heaps[window]
                if len(h) < TOP_N:
                    heapq.heappush(h, entry)
                elif pnl > h[0][0]:
                    heapq.heapreplace(h, entry)

    top = {
        WINDOW_MAP[w]: [e[2] for e in sorted(h, key=lambda x: -x[0])]
        for w, h in heaps.items()
    }
    filter_stats = {
        "eligible_per_window": eligible_per_window,
        "excluded_by_floor": excluded_by_floor,
        "excluded_by_sanity": excluded_by_sanity,
        "excluded_by_ratio": excluded_by_ratio,
    }
    return top, scanned, display_names, filter_stats


async def run_once() -> dict:
    """One full ingest cycle. Raises on fetch/parse failure (caller retries)."""
    started = datetime.utcnow()
    rss_before = _rss_mb()

    # Stream the body to a temp file — memory never holds the whole payload.
    tmp = tempfile.NamedTemporaryFile(suffix=".json", delete=False)
    tmp_path = tmp.name
    try:
        bytes_dl = 0
        # stats-data host — no info-API weight; shares the pooled connection
        from app.services.hyperliquid import client as hl_client
        async with hl_client.stream("GET", LEADERBOARD_URL,
                                    headers={"User-Agent": USER_AGENT,
                                             "Accept": "application/json"},
                                    timeout=httpx.Timeout(120.0, connect=15.0)) as resp:
            if resp.status_code != 200:
                raise RuntimeError(f"HL leaderboard HTTP {resp.status_code}")
            async for chunk in resp.aiter_bytes():
                tmp.write(chunk)
                bytes_dl += len(chunk)
        tmp.close()

        loop = asyncio.get_event_loop()
        top, scanned, display_names, filter_stats = await loop.run_in_executor(
            None, _parse_file, tmp_path)
        rss_after = _rss_mb()

        now = datetime.utcnow()
        inserted = 0
        sf = get_session_factory()
        async with sf() as session:
            # snapshots: top-N per window, rank 1..N by window PnL
            for period, rows in top.items():
                for rank, r in enumerate(rows, start=1):
                    session.add(LeaderboardSnapshot(
                        exchange="hl",
                        wallet_address=r["wallet"],
                        rank=rank,
                        pnl_total=r["pnl"],
                        roi=r["roi_pct"],
                        volume=r["volume"],
                        period=period,
                        timestamp=now,
                    ))
                    inserted += 1

            # trader_profiles upsert (exchange='hl'): display_name/last_seen only.
            # NEVER touches is_hidden — a hidden profile stays hidden.
            wallets = {r["wallet"] for rows in top.values() for r in rows}
            if wallets:
                existing = (await session.execute(
                    select(TraderProfile).where(
                        TraderProfile.exchange == "hl",
                        TraderProfile.wallet_address.in_(list(wallets)),
                    )
                )).scalars().all()
                by_wallet = {p.wallet_address: p for p in existing}
                for w in wallets:
                    p = by_wallet.get(w)
                    if p is None:
                        session.add(TraderProfile(
                            exchange="hl", wallet_address=w,
                            display_name=display_names.get(w),
                            source="hl_leaderboard",
                        ))
                    else:
                        if display_names.get(w):
                            p.display_name = display_names[w]
                        p.last_seen_at = now

            # retention: drop hl snapshots older than 180 days
            cutoff = now - timedelta(days=RETENTION_DAYS)
            await session.execute(delete(LeaderboardSnapshot).where(
                LeaderboardSnapshot.exchange == "hl",
                LeaderboardSnapshot.timestamp < cutoff,
            ))
            await session.commit()

        dur = (datetime.utcnow() - started).total_seconds()
        stats = {
            "ts": now.isoformat(), "bytes": bytes_dl, "rows_scanned": scanned,
            "eligible_wallets": len(wallets), "snapshot_rows": inserted,
            **filter_stats,
            "duration_sec": round(dur, 1),
            "rss_before_mb": round(rss_before, 1) if rss_before else None,
            "rss_after_mb": round(rss_after, 1) if rss_after else None,
        }
        _last_run.update(stats)
        logger.info("HL leaderboard ingest: %s", stats)
        return stats
    finally:
        try:
            tmp.close()
            os.unlink(tmp_path)
        except Exception:
            pass


async def _loop() -> None:
    await asyncio.sleep(FIRST_RUN_DELAY_SEC)
    while True:
        try:
            await run_once()
            await _warm_lb_cache()
            try:
                await _backfill_last_fill()
            except Exception:
                logger.exception("last_fill_at backfill failed (non-fatal)")
            try:
                # Tier-1 activity metrics (both exchanges) — snapshot-derived,
                # zero venue load; overhead logged per run (<5s target).
                from app.services import activity_metrics
                await activity_metrics.run()
            except Exception:
                logger.exception("activity metrics failed (non-fatal)")
        except Exception as exc:
            logger.warning("HL leaderboard ingest failed (%s) — retrying in %ss",
                           exc, RETRY_DELAY_SEC)
            await asyncio.sleep(RETRY_DELAY_SEC)
            try:
                await run_once()
                await _warm_lb_cache()
            except Exception as exc2:
                logger.error("HL leaderboard ingest retry failed (%s) — keeping "
                             "previous data until next cycle", exc2)
        await asyncio.sleep(INTERVAL_SEC)


async def _backfill_last_fill() -> None:
    """Recent-activity backfill (migration v5) — TWO set-based UPDATEs, no
    per-wallet loops. Runs once per hourly ingest cycle:
      1. from detected trade events (both venues): MAX(detected_at) per trader
      2. from the HL position-dating cache: MAX(opened_at) per wallet
    Forward-only: only fills NULL or moves the stamp forward."""
    from sqlalchemy import text
    sf = get_session_factory()
    async with sf() as session:
        await session.execute(text(
            "UPDATE trader_profiles tp "
            "JOIN (SELECT exchange, trader_wallet, MAX(detected_at) m "
            "      FROM leader_trade_events GROUP BY exchange, trader_wallet) ev "
            "  ON ev.exchange = tp.exchange AND ev.trader_wallet = tp.wallet_address "
            "SET tp.last_fill_at = ev.m "
            "WHERE tp.last_fill_at IS NULL OR tp.last_fill_at < ev.m"))
        await session.execute(text(
            "UPDATE trader_profiles tp "
            "JOIN (SELECT wallet, MAX(opened_at) m FROM hl_position_open_cache "
            "      WHERE opened_at IS NOT NULL GROUP BY wallet) oc "
            "  ON tp.exchange = 'hl' AND oc.wallet = tp.wallet_address "
            "SET tp.last_fill_at = oc.m "
            "WHERE tp.last_fill_at IS NULL OR tp.last_fill_at < oc.m"))
        await session.commit()


async def _warm_lb_cache() -> None:
    """Push the fresh ingest batch into the API-side leaderboard cache so no
    request ever pays the cold DB-load cost (late import — router layer)."""
    try:
        from app.routers.leaders import warm_hl_lb_cache
        await warm_hl_lb_cache()
    except Exception:
        logger.exception("HL lb cache warm after ingest failed")


def start() -> None:
    global _task
    if _task is None or _task.done():
        _task = asyncio.get_event_loop().create_task(_loop())
        logger.info("HL leaderboard ingest task started (interval %ss)", INTERVAL_SEC)


async def stop() -> None:
    global _task
    if _task and not _task.done():
        _task.cancel()
        try:
            await _task
        except asyncio.CancelledError:
            pass
    _task = None


def get_last_run() -> dict:
    return dict(_last_run)
