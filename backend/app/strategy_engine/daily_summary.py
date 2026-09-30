"""Daily strategy summary — Telegram at 00:05 UTC (D-106, 2026-09-11).

Writes ONE strat_telegram_outbox row per UTC day covering the day that just
ended: per strategy and model, trades / sum R / fees / kill-switch state, plus
any feed gap longer than 30 minutes in the last 24 hours.

Read-only over the trade and feed tables; the only write is the outbox row,
which the existing API-side drain delivers through the existing bot. Nothing
here places or blocks an order.
"""
from __future__ import annotations

import datetime as _dt
import logging
from typing import Optional

from sqlalchemy import text

logger = logging.getLogger("strategy_engine.daily_summary")

SEND_MINUTE = 5                    # 00:05 UTC
DAY_MS = 86_400_000
GAP_MS = 30 * 60 * 1000            # a "feed gap" worth reporting

WATCHED_FEEDS = (
    ("strat_candles", "candles", "tf='15m'"),
    ("strat_oi_1m", "OI", "1=1"),
    ("strat_trades_1m", "taker", "1=1"),
    ("strat_book_5s", "book", "1=1"),
    ("strat_liquidations", "liq live", "source IS NULL"),
    ("strat_liquidations", "liq archive", "source='oxarchive'"),
    ("strat_liq_map_hist", "liq map", "1=1"),
    ("strat_gauge", "gauge", "1=1"),
)


def _day_bounds(now_ms: int) -> tuple[int, int, str]:
    """The UTC day that just ended."""
    now = _dt.datetime.fromtimestamp(now_ms / 1000, _dt.timezone.utc)
    end = now.replace(hour=0, minute=0, second=0, microsecond=0)
    start = end - _dt.timedelta(days=1)
    return int(start.timestamp() * 1000), int(end.timestamp() * 1000), start.strftime("%Y-%m-%d")


def is_due(now_ms: int, last_sent_day: Optional[str]) -> tuple[bool, str]:
    """True on the first boundary at/after 00:05 UTC for a day not yet sent."""
    now = _dt.datetime.fromtimestamp(now_ms / 1000, _dt.timezone.utc)
    _s, _e, label = _day_bounds(now_ms)
    if now.hour == 0 and now.minute < SEND_MINUTE:
        return False, label
    return (last_sent_day != label), label


async def longest_gaps(s, start_ms: int, end_ms: int) -> list[str]:
    """Feeds whose largest inter-row gap in the window exceeds 30 minutes."""
    out: list[str] = []
    for table, label, where in WATCHED_FEEDS:
        rows = (await s.execute(text(
            f"SELECT ts FROM {table} WHERE {where} AND ts >= :a AND ts < :b ORDER BY ts"),
            {"a": start_ms, "b": end_ms})).all()
        if not rows:
            out.append(f"{label}: NO ROWS in 24h")
            continue
        longest, prev, at = 0, None, None
        for (t,) in rows:
            t = int(t)
            if prev is not None and t - prev > longest:
                longest, at = t - prev, prev
            prev = t
        # a feed that stopped before the window ended is also a gap
        tail = end_ms - int(rows[-1][0])
        if tail > longest:
            longest, at = tail, int(rows[-1][0])
        if longest > GAP_MS:
            when = _dt.datetime.fromtimestamp((at or 0) / 1000, _dt.timezone.utc).strftime("%H:%M")
            out.append(f"{label}: {longest / 60000:.0f} min from {when} UTC")
    return out


async def build(s, now_ms: int) -> tuple[str, str]:
    """Returns (message, day_label). Pure read over the DB."""
    start_ms, end_ms, label = _day_bounds(now_ms)
    rows = (await s.execute(text(
        "SELECT COALESCE(model, strategy) AS who, COUNT(*) n, "
        "SUM(CASE WHEN r_multiple IS NOT NULL THEN r_multiple "
        "         WHEN stop_px IS NOT NULL AND size > 0 AND entry_px <> stop_px "
        "         THEN pnl_gross / (ABS(entry_px - stop_px) * size) END) sum_r, "
        "SUM(fees) fees, SUM(pnl_net) pnl "
        "FROM strat_trades WHERE fill_ts >= :a AND fill_ts < :b GROUP BY 1 ORDER BY 5"),
        {"a": start_ms, "b": end_ms})).all()
    state = {r[0]: (r[1], r[2], r[3]) for r in (await s.execute(text(
        "SELECT strategy_id, effective_mode, kill_switch_tripped, rolling20_pf "
        "FROM strat_strategy_state"))).all()}

    lines = [f"Strategy daily summary — {label} UTC", ""]
    if rows:
        lines.append("Trades:")
        for who, n, sum_r, fees, pnl in rows:
            st = state.get(who) or state.get(f"{who}_", None)
            mode = st[0] if st else "?"
            ks = " KILL-SWITCH" if (st and st[1]) else ""
            r_txt = f"{float(sum_r):+.2f}R" if sum_r is not None else "R n/a"
            lines.append(f"  {who}: {n} trades, {r_txt}, fees ${float(fees or 0):.2f}, "
                         f"net ${float(pnl or 0):.2f} [{mode}{ks}]")
    else:
        lines.append("Trades: none in the last 24h.")
    lines.append("")

    paused = [(k, v) for k, v in sorted(state.items()) if v[1] or v[0] not in ("paper",)]
    if paused:
        lines.append("Not trading:")
        for k, v in paused:
            pf = f", rolling20 PF {float(v[2]):.2f}" if v[2] is not None else ""
            lines.append(f"  {k}: {v[0]}{' (kill switch)' if v[1] else ''}{pf}")
        lines.append("")

    gaps = await longest_gaps(s, start_ms, end_ms)
    lines.append("Feed gaps > 30 min:" if gaps else "Feed gaps > 30 min: none.")
    for g in gaps:
        lines.append(f"  {g}")
    return "\n".join(lines), label


async def maybe_send(s, now_ms: int, last_sent_day: Optional[str]) -> Optional[str]:
    """Queue the summary if it is due. Returns the day label when it queued."""
    due, label = is_due(now_ms, last_sent_day)
    if not due:
        return None
    msg, label = await build(s, now_ms)
    await s.execute(text(
        "INSERT INTO strat_telegram_outbox (ts, kind, strategy_id, coin, message, dedupe_key, sent) "
        "VALUES (:ts,'daily_summary','-','-',:m,:k,0) "
        "ON DUPLICATE KEY UPDATE ts=VALUES(ts)"),
        {"ts": now_ms, "m": msg, "k": f"daily_summary:{label}"})
    logger.info("daily summary queued for %s (%d chars)", label, len(msg))
    return label
