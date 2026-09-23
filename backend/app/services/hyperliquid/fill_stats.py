"""Tier-2 fills-based quality stats (ANALYTICS_REPORT Part 2).

Hourly sampler over the union of top-50 wallets across the four leaderboard
windows (~<=120 unique). Fetches `userFillsByTime` DELTAS (per-wallet cursor)
into the rolling `hl_fill_events` store, then recomputes 7-day stats from the
true window. Budget discipline:
  * hard cap PAGES_PER_WALLET pages / wallet / run (2000 fills per page)
  * <= 2 concurrent, PAUSE_SEC between wallet fetches
  * total requests per run logged and bounded by REQUEST_BUDGET — when the
    budget is hit the remaining wallets simply wait for the next hour
  * yields to foreground profile fetches exactly like the dating prober
    (profile._fg_active)

Computation rules (each surfaced in the UI with its sample size):
  * a "trade" = a CLOSE fill — dir containing 'Close' or a flip ('>').
    closedPnl is present on every HL fill (0.0 on opens), so "closedPnl
    absent" is not a usable discriminator; dir is. win = closedPnl > 0.
  * win_rate_7d NULL when trades_7d < 10 (a percentage from 3 trades is
    noise, never shown).
  * avg_hold_minutes: close matched to the most recent PRECEDING open fill of
    the same coin+side inside the window; NULL under 5 matched pairs. Trades
    whose open predates the window are NOT approximated.
  * maker_ratio = share of fills with crossed == False; NULL if the flag is
    absent from the payload.
  * realized_pnl_7d = sum of closedPnl over close fills (venue value,
    excludes fees).
  * sample_capped = True when the page cap truncated the fetch — stats are a
    labeled SAMPLE for that wallet (hyperactive MMs), never presented as
    exhaustive.
"""
import asyncio
import time
from datetime import datetime, timedelta

import httpx
from sqlalchemy import text

from app.db.database import get_session_factory
from app.services.hyperliquid import client as hl_client
from app.utils.logger import get_logger

logger = get_logger(__name__)

INTERVAL_SEC = 3600
FIRST_RUN_DELAY_SEC = 420          # after boot ingest+metrics settle
TOP_N_PER_WINDOW = 50
PAGES_PER_WALLET = 3               # hard cap (2000 fills/page)
REQUEST_BUDGET = 150               # per run — hard stop, remainder waits
# Cohort rotation (owner-approved 2026-08-26): after the hot set, spend up to
# ROTATION_BUDGET of the SAME run budget on analytics-cohort wallets whose MM
# flag is unknown (round-robin by cohort rank — the backlog self-advances as
# sampled wallets gain fill stats); once no unsampled unknowns remain, the
# rotation revisits cohort wallets with the OLDEST computed_at first.
ROTATION_BUDGET = 30
CONCURRENCY = 1                    # sequential — 2-concurrent tripped venue 429s in dev
PAUSE_SEC = 1.2                    # between wallet fetches
RATE_LIMIT_BACKOFF_SEC = 20        # one backoff+retry per wallet on 429; second 429 ends the run
WINDOW_DAYS = 7
RETENTION_DAYS = 8
MAX_ROWS_PER_WALLET = 15000
MIN_TRADES_FOR_WINRATE = 10
MIN_PAIRS_FOR_HOLD = 5
PF_NO_LOSS_CAP = 999.99            # PF stored at cap = "no losses in window" (UI: ∞)

_task: asyncio.Task | None = None
_last_run: dict = {}


async def _yield_to_foreground() -> None:
    """Same contract as the dating prober: while a live profile fetch is in
    flight, background sampling pauses (HL throttles the whole IP)."""
    from app.services.hyperliquid import profile as hl_profile
    fg = getattr(hl_profile, "_fg_active", None)
    while fg and fg[0] > 0:
        await asyncio.sleep(0.5)


async def _target_wallets() -> list[str]:
    """Union of top-50 per window from the LATEST ingest batch of each."""
    sf = get_session_factory()
    async with sf() as s:
        rows = (await s.execute(text(
            "SELECT DISTINCT s.wallet_address FROM leaderboard_snapshots s JOIN ("
            "  SELECT period, MAX(timestamp) mt FROM leaderboard_snapshots "
            "  WHERE exchange = 'hl' GROUP BY period"
            ") lb ON lb.period = s.period AND lb.mt = s.timestamp "
            "WHERE s.exchange = 'hl' AND s.`rank` <= :n"
        ), {"n": TOP_N_PER_WINDOW})).scalars().all()
    return [w.lower() for w in rows]


async def _fetch_wallet_fills(wallet: str,
                              cursor_ms: int | None, budget_left: int) -> tuple[list[dict], int, bool]:
    """Delta fetch since cursor (or window start). Returns (fills, requests_used,
    hit_page_cap)."""
    now_ms = int(time.time() * 1000)
    start = max(int((time.time() - WINDOW_DAYS * 86400) * 1000),
                (cursor_ms + 1) if cursor_ms else 0)
    fills: list[dict] = []
    used = 0
    capped = False
    for _page in range(PAGES_PER_WALLET):
        if used >= budget_left:
            capped = True
            break
        await _yield_to_foreground()
        resp = await hl_client.post_info({
            "type": "userFillsByTime", "user": wallet,
            "startTime": start, "endTime": now_ms,
            "aggregateByTime": True,     # merges partial fills of one order
        }, priority=hl_client.BACKGROUND, timeout=30.0)
        used += 1
        resp.raise_for_status()
        batch = resp.json() or []
        fills.extend(batch)
        if len(batch) < 2000:
            break
        start = int(batch[-1].get("time", start)) + 1
    else:
        capped = True
    return fills, used, capped


def _compute_stats(rows: list[dict]) -> dict:
    """Stats over the wallet's true 7d fill window (documented in module doc)."""
    closes = []
    opens_by_key: dict[tuple, list] = {}
    crossed_known = 0
    maker = 0
    for f in sorted(rows, key=lambda r: r["ts_ms"]):
        d = str(f.get("dir") or "")
        if f.get("crossed") is not None:
            crossed_known += 1
            # DB returns 0/1 ints, live payloads booleans — truthiness covers both
            # ('is False' silently missed 0 and zeroed every maker_ratio)
            if not f["crossed"]:
                maker += 1
        is_close = ("Close" in d) or (">" in d)
        side = "long" if "Long" in d.replace(">", " ") else "short"
        key = (f["coin"], side)
        if is_close:
            closes.append(f)
        elif "Open" in d:
            opens_by_key.setdefault(key, []).append(f["ts_ms"])

    trades = len(closes)
    wins = sum(1 for c in closes if (c.get("closed_pnl") or 0) > 0)
    realized = round(sum(float(c.get("closed_pnl") or 0) for c in closes), 2)
    notionals = [abs(float(c.get("px") or 0) * float(c.get("sz") or 0)) for c in closes]
    avg_notional = round(sum(notionals) / len(notionals), 2) if notionals else None

    # ---- Tier-2 Part D: quality metrics from the same closes ---------------
    # profit_factor = gross wins / gross losses; PF_NO_LOSS_CAP (999.99) when
    # the window has wins but no losses ("PF ∞" in the UI); NULL with no
    # closes. max_drawdown_7d = peak-to-trough of the cumulative realized-PnL
    # path over the window's closes in time order (USD; % vs account value is
    # attached at store time when the sweep knows the AV).
    win_pnls = [float(c["closed_pnl"]) for c in closes if (c.get("closed_pnl") or 0) > 0]
    loss_pnls = [-float(c["closed_pnl"]) for c in closes if (c.get("closed_pnl") or 0) < 0]
    gross_win = sum(win_pnls)
    gross_loss = sum(loss_pnls)
    if trades == 0:
        pf = None
    elif gross_loss > 0:
        pf = round(min(gross_win / gross_loss, PF_NO_LOSS_CAP), 4)
    else:
        pf = PF_NO_LOSS_CAP if gross_win > 0 else None
    avg_win = round(gross_win / len(win_pnls), 2) if win_pnls else None
    avg_loss = round(gross_loss / len(loss_pnls), 2) if loss_pnls else None
    wl_ratio = round(avg_win / avg_loss, 4) if (avg_win and avg_loss) else None
    equity = 0.0
    peak = 0.0
    max_dd = 0.0
    for c in sorted(closes, key=lambda r: r["ts_ms"]):
        equity += float(c.get("closed_pnl") or 0)
        peak = max(peak, equity)
        max_dd = max(max_dd, peak - equity)
    max_dd = round(max_dd, 2) if closes else None

    holds = []
    for c in closes:
        d = str(c.get("dir") or "")
        side = "long" if ("Long" in d.split(">")[0]) else "short"
        opens = opens_by_key.get((c["coin"], side)) or []
        prior = [t for t in opens if t < c["ts_ms"]]
        if prior:
            holds.append((c["ts_ms"] - prior[-1]) / 60000.0)
    avg_hold = int(round(sum(holds) / len(holds))) if len(holds) >= MIN_PAIRS_FOR_HOLD else None

    return {
        "trades_7d": trades,
        "win_rate_7d": round(wins / trades, 4) if trades >= MIN_TRADES_FOR_WINRATE else None,
        "avg_hold_minutes": avg_hold,
        "avg_trade_notional": avg_notional,
        "maker_ratio": round(maker / crossed_known, 4) if crossed_known else None,
        "realized_pnl_7d": realized,
        "sample_fills": len(rows),
        "profit_factor": pf,
        "avg_win": avg_win,
        "avg_loss": avg_loss,
        "avg_win_loss_ratio": wl_ratio,
        "max_drawdown_7d": max_dd,
    }


async def run_once() -> dict:
    t0 = time.monotonic()
    wallets = await _target_wallets()
    now = datetime.utcnow()
    total_requests = 0
    fetched_wallets = 0
    skipped_budget = 0

    sf = get_session_factory()
    async with sf() as s:
        cur_rows = (await s.execute(text(
            "SELECT wallet_address, cursor_ts FROM trader_fill_stats"))).all()
    cursors = {w.lower(): c for w, c in cur_rows}

    # Sequential + paced (CONCURRENCY=1): dev evidence showed 2-concurrent
    # trips the venue's per-IP rate limit. Budget is reserved BEFORE each
    # attempt so a failed request can never push the run over REQUEST_BUDGET.
    rate_limited_once = False
    if True:   # (kept indentation — was the per-run AsyncClient block)
        for w in wallets:
            left = REQUEST_BUDGET - total_requests
            if left <= 0:
                skipped_budget += 1
                continue
            try:
                fills, used, capped = await _fetch_wallet_fills(w, cursors.get(w), left)
                total_requests += max(used, 1)
            except httpx.HTTPStatusError as exc:
                total_requests += 1
                if exc.response.status_code == 429:
                    if rate_limited_once:
                        logger.warning("fill_stats: second 429 — ending run early "
                                       "(%d wallets left for next hour)",
                                       len(wallets) - fetched_wallets - skipped_budget)
                        skipped_budget += 1
                        break
                    rate_limited_once = True
                    logger.warning("fill_stats: 429 — backing off %ss", RATE_LIMIT_BACKOFF_SEC)
                    await asyncio.sleep(RATE_LIMIT_BACKOFF_SEC)
                    continue   # this wallet waits for next hour; keep going gently
                logger.warning("fill fetch failed for %s: %s", w[:10], exc)
                continue
            except Exception as exc:
                total_requests += 1
                logger.warning("fill fetch failed for %s: %s", w[:10], exc)
                continue
            fetched_wallets += 1
            await _store_and_compute(w, fills, capped, now)
            await asyncio.sleep(PAUSE_SEC)

        # ---- cohort rotation (unknown-flag backlog, then stale oldest-first) --
        rotated = 0
        rotation_mode = None
        if not rate_limited_once and total_requests < REQUEST_BUDGET:
            try:
                from app.services.analytics import cohort as an_cohort
                await an_cohort.ensure()
                hot = {w.lower() for w in wallets}
                sampled = {w for w in cursors}
                backlog = [w for w in an_cohort.wallets()
                           if an_cohort.mm_flag(w) is None
                           and w.lower() not in hot and w.lower() not in sampled]
                if backlog:
                    rotation_mode = "unknown_backlog"
                    targets = backlog          # rank-ordered; shrinks as sampled
                else:
                    rotation_mode = "stale_revisit"
                    ck = [w.lower() for w in an_cohort.wallets() if w.lower() not in hot]
                    targets = []
                    if ck:
                        placeholders = ",".join(f":w{i}" for i in range(len(ck)))
                        params = {f"w{i}": w for i, w in enumerate(ck)}
                        async with sf() as s:
                            targets = [r for (r,) in (await s.execute(text(
                                "SELECT wallet_address FROM trader_fill_stats "
                                f"WHERE wallet_address IN ({placeholders}) "
                                "ORDER BY computed_at ASC LIMIT 40"), params)).all()]
                for w in targets:
                    rot_left = min(ROTATION_BUDGET - rotated,
                                   REQUEST_BUDGET - total_requests)
                    if rot_left <= 0 or rate_limited_once:
                        break
                    try:
                        fills, used, capped = await _fetch_wallet_fills(
                            w, cursors.get(w.lower()), rot_left)
                        total_requests += max(used, 1)
                        rotated += max(used, 1)
                    except httpx.HTTPStatusError as exc:
                        total_requests += 1
                        rotated += 1
                        if exc.response.status_code == 429:
                            logger.warning("fill_stats rotation: 429 — ending rotation")
                            rate_limited_once = True
                            break
                        continue
                    except Exception as exc:
                        total_requests += 1
                        rotated += 1
                        logger.debug("rotation fetch failed for %s: %s", w[:10], exc)
                        continue
                    fetched_wallets += 1
                    await _store_and_compute(w, fills, capped, now)
                    await asyncio.sleep(PAUSE_SEC)
            except Exception:
                logger.exception("fill_stats rotation failed (non-fatal)")

        # ---- hedger spot pass (Tier-2 C1): SEPARATE hard budget (60/hr),
        # folded into this scheduler per spec — no new task. Skipped when this
        # run already tripped the venue rate limit (be gentle).
        spot_stats: dict = {}
        if not rate_limited_once:
            try:
                from app.services.analytics import hedger as an_hedger
                spot_stats = await an_hedger.run_pass()
            except Exception:
                logger.exception("hedger spot pass failed (non-fatal)")

    # retention: 8 days + newest N per wallet (hyperactive MM row cap)
    sf = get_session_factory()
    async with sf() as s:
        await s.execute(text(
            "DELETE FROM hl_fill_events WHERE ts_ms < :cut"),
            {"cut": int((time.time() - RETENTION_DAYS * 86400) * 1000)})
        await s.commit()

    stats = {
        "ts": now.isoformat(), "wallets_targeted": len(wallets),
        "wallets_fetched": fetched_wallets, "skipped_by_budget": skipped_budget,
        "requests": total_requests, "budget": REQUEST_BUDGET,
        "rotation_mode": rotation_mode, "rotation_requests": rotated,
        **spot_stats,
        "duration_sec": round(time.monotonic() - t0, 1),
    }
    _last_run.update(stats)
    logger.info("fill_stats sampler: %s", stats)
    return stats


async def _store_and_compute(wallet: str, new_fills: list[dict], capped: bool,
                             now: datetime) -> None:
    sf = get_session_factory()
    win_start_ms = int((time.time() - WINDOW_DAYS * 86400) * 1000)
    async with sf() as s:
        if new_fills:
            rows = []
            for f in new_fills:
                try:
                    rows.append({
                        "w": wallet, "ts": int(f.get("time") or 0),
                        "c": str(f.get("coin") or "")[:20],
                        "d": str(f.get("dir") or "")[:20],
                        "px": float(f.get("px") or 0), "sz": float(f.get("sz") or 0),
                        "cp": float(f.get("closedPnl")) if f.get("closedPnl") is not None else None,
                        "x": bool(f.get("crossed")) if f.get("crossed") is not None else None,
                        "tid": str(f.get("tid") or f.get("hash") or "")[:40] or None,
                    })
                except (TypeError, ValueError):
                    continue
            if rows:
                await s.execute(text(
                    "INSERT IGNORE INTO hl_fill_events "
                    "(wallet_address, ts_ms, coin, dir, px, sz, closed_pnl, crossed, tid) "
                    "VALUES (:w, :ts, :c, :d, :px, :sz, :cp, :x, :tid)"), rows)
                # per-wallet row cap: keep the newest MAX_ROWS_PER_WALLET
                n = (await s.execute(text(
                    "SELECT COUNT(*) FROM hl_fill_events WHERE wallet_address = :w"),
                    {"w": wallet})).scalar() or 0
                if n > MAX_ROWS_PER_WALLET:
                    cut_ts = (await s.execute(text(
                        "SELECT ts_ms FROM hl_fill_events WHERE wallet_address = :w "
                        "ORDER BY ts_ms DESC LIMIT 1 OFFSET :o"),
                        {"w": wallet, "o": MAX_ROWS_PER_WALLET})).scalar()
                    if cut_ts:
                        await s.execute(text(
                            "DELETE FROM hl_fill_events WHERE wallet_address = :w AND ts_ms <= :t"),
                            {"w": wallet, "t": cut_ts})

        window = (await s.execute(text(
            "SELECT ts_ms, coin, dir, px, sz, closed_pnl, crossed "
            "FROM hl_fill_events WHERE wallet_address = :w AND ts_ms >= :s"),
            {"w": wallet, "s": win_start_ms})).mappings().all()
        stats = _compute_stats([dict(r) for r in window])
        # drawdown % of account value — only when the sweep knows the AV
        dd_pct = None
        if stats["max_drawdown_7d"]:
            av_row = (await s.execute(text(
                "SELECT account_value FROM analytics_wallet_state "
                "WHERE wallet = :w ORDER BY cycle_ts DESC LIMIT 1"),
                {"w": wallet})).scalar()
            if av_row and float(av_row) > 0:
                dd_pct = round(stats["max_drawdown_7d"] / float(av_row), 4)
        max_ts = max((int(f.get("time") or 0) for f in new_fills), default=None)
        await s.execute(text(
            "INSERT INTO trader_fill_stats "
            "(wallet_address, trades_7d, win_rate_7d, avg_hold_minutes, "
            " avg_trade_notional, maker_ratio, realized_pnl_7d, sample_fills, "
            " sample_capped, cursor_ts, computed_at, "
            " profit_factor, avg_win, avg_loss, avg_win_loss_ratio, "
            " max_drawdown_7d, max_drawdown_pct_7d) "
            "VALUES (:w, :t, :wr, :h, :n, :m, :r, :sf, :cap, :cur, :ca, "
            " :pf, :aw, :al, :wl, :dd, :ddp) "
            "ON DUPLICATE KEY UPDATE trades_7d=VALUES(trades_7d), "
            " win_rate_7d=VALUES(win_rate_7d), avg_hold_minutes=VALUES(avg_hold_minutes), "
            " avg_trade_notional=VALUES(avg_trade_notional), maker_ratio=VALUES(maker_ratio), "
            " realized_pnl_7d=VALUES(realized_pnl_7d), sample_fills=VALUES(sample_fills), "
            " sample_capped=VALUES(sample_capped), "
            " cursor_ts=GREATEST(COALESCE(cursor_ts, 0), COALESCE(VALUES(cursor_ts), 0)), "
            " computed_at=VALUES(computed_at), "
            " profit_factor=VALUES(profit_factor), avg_win=VALUES(avg_win), "
            " avg_loss=VALUES(avg_loss), avg_win_loss_ratio=VALUES(avg_win_loss_ratio), "
            " max_drawdown_7d=VALUES(max_drawdown_7d), "
            " max_drawdown_pct_7d=VALUES(max_drawdown_pct_7d)"
        ), {"w": wallet, "t": stats["trades_7d"], "wr": stats["win_rate_7d"],
            "h": stats["avg_hold_minutes"], "n": stats["avg_trade_notional"],
            "m": stats["maker_ratio"], "r": stats["realized_pnl_7d"],
            "sf": stats["sample_fills"], "cap": capped, "cur": max_ts, "ca": now,
            "pf": stats["profit_factor"], "aw": stats["avg_win"],
            "al": stats["avg_loss"], "wl": stats["avg_win_loss_ratio"],
            "dd": stats["max_drawdown_7d"], "ddp": dd_pct})
        await s.commit()


async def get_stats_bulk(wallets: list[str]) -> dict[str, dict]:
    wl = [w.lower() for w in wallets]
    if not wl:
        return {}
    placeholders = ",".join(f":w{i}" for i in range(len(wl)))
    params = {f"w{i}": w for i, w in enumerate(wl)}
    sf = get_session_factory()
    async with sf() as s:
        rows = (await s.execute(text(
            "SELECT wallet_address, trades_7d, win_rate_7d, avg_hold_minutes, "
            " avg_trade_notional, maker_ratio, realized_pnl_7d, sample_fills, "
            " sample_capped, computed_at, profit_factor, avg_win, avg_loss, "
            " avg_win_loss_ratio, max_drawdown_7d, max_drawdown_pct_7d, "
            " flip_accuracy, flip_n "
            f"FROM trader_fill_stats WHERE wallet_address IN ({placeholders})"
        ), params)).mappings().all()
    out = {}
    def _f(v):
        return float(v) if v is not None else None
    for r in rows:
        out[r["wallet_address"].lower()] = {
            "trades_7d": r["trades_7d"],
            "win_rate_7d": _f(r["win_rate_7d"]),
            "avg_hold_minutes": r["avg_hold_minutes"],
            "avg_trade_notional": _f(r["avg_trade_notional"]),
            "maker_ratio": _f(r["maker_ratio"]),
            "realized_pnl_7d": _f(r["realized_pnl_7d"]),
            "sample_fills": r["sample_fills"],
            "sample_capped": bool(r["sample_capped"]),
            "computed_at": r["computed_at"].isoformat() if r["computed_at"] else None,
            "profit_factor": _f(r["profit_factor"]),
            "avg_win": _f(r["avg_win"]),
            "avg_loss": _f(r["avg_loss"]),
            "avg_win_loss_ratio": _f(r["avg_win_loss_ratio"]),
            "max_drawdown_7d": _f(r["max_drawdown_7d"]),
            "max_drawdown_pct_7d": _f(r["max_drawdown_pct_7d"]),
            "flip_accuracy": _f(r["flip_accuracy"]),
            "flip_n": r["flip_n"],
        }
    return out


async def _loop() -> None:
    await asyncio.sleep(FIRST_RUN_DELAY_SEC)
    while True:
        try:
            await run_once()
        except Exception:
            logger.exception("fill_stats run failed — next attempt in an hour")
        await asyncio.sleep(INTERVAL_SEC)


def start() -> None:
    global _task
    if _task is None or _task.done():
        _task = asyncio.get_event_loop().create_task(_loop())
        logger.info("fill_stats sampler started (interval %ss, budget %s req/run)",
                    INTERVAL_SEC, REQUEST_BUDGET)


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

async def win_rate_order(wallets: list[str]) -> list[str]:
    """Tier-2 D2: the "Win rate" sort became "Quality" — ELIGIBLE wallets
    (win_rate non-NULL AND trades_7d >= 20 AND profit_factor >= 1.0; the
    999.99 no-loss cap passes by construction) ordered by win rate DESC.
    A high win rate with PF < 1 is a grinder giving it all back — gated out.
    Ineligible wallets are NOT returned — the router keeps them below in
    their existing order (UI explains why on hover)."""
    wl = [w.lower() for w in wallets]
    if not wl:
        return []
    placeholders = ",".join(f":w{i}" for i in range(len(wl)))
    params = {f"w{i}": w for i, w in enumerate(wl)}
    sf = get_session_factory()
    async with sf() as s:
        rows = (await s.execute(text(
            f"SELECT wallet_address FROM trader_fill_stats "
            f"WHERE wallet_address IN ({placeholders}) "
            f"AND win_rate_7d IS NOT NULL AND trades_7d >= 20 "
            f"AND profit_factor IS NOT NULL AND profit_factor >= 1.0 "
            f"ORDER BY win_rate_7d DESC, trades_7d DESC"
        ), params)).scalars().all()
    return [w.lower() for w in rows]
