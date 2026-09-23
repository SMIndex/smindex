"""Tier-1 trader activity metrics (ANALYTICS_REPORT Part 1).

Derived ENTIRELY from the leaderboard_snapshots history already in the DB —
zero additional venue load. Runs at the end of every hourly HL ingest for BOTH
exchanges. All computations are documented inline; underivable fields stay
NULL (the UI renders em-dash, never fake parity).

Derivations (HL: hourly top-100 per window; Perpl: 30-min top-20, 'all' only):
  * Volume in the 'all' window is CUMULATIVE lifetime volume -> any increase
    between snapshots is real trading. Wallets without 'all' rows fall back to
    the 'day' window (positive deltas only, so HL's day-boundary volume reset
    — a negative delta — is never counted as activity).
  * active_days_7d/30d: distinct UTC days whose cumulative volume grew vs the
    previous day's last value (fallback: days with a day-window row, volume>0).
  * last_active_at: newest snapshot timestamp where volume grew vs the
    previous snapshot (48h lookback at hourly resolution; older activity gets
    day precision from the daily aggregates).
  * vol_velocity_24h: cumulative-volume delta over the last ~24h (== sum of
    positive hour-to-hour deltas for a monotonic series); day-window fallback:
    sum of positive deltas across the last 25h of rows.
  * consistency_flags: from the LATEST batch per window — bit set when the
    wallet HAS a row there AND pnl > 0 (bit0 day, bit1 week, bit2 month,
    bit3 all). HL only. Absence != losing; the tooltip says so.
  * pnl_trend_7d: least-squares slope (USD/day) of the week-window PnL taken
    at the last snapshot of each of the last 7 days. HL only; NULL under
    3 days of data.
  * history_days: distinct snapshot days available in the 30d window — the
    honesty denominator ("4/4d (new)" when < 7).
"""
import time
from datetime import datetime, timedelta

from sqlalchemy import text

from app.db.database import get_session_factory
from app.utils.logger import get_logger

logger = get_logger(__name__)

_last_run: dict = {}


async def compute_exchange(exchange: str) -> dict:
    """Compute + upsert metrics for every wallet with snapshots in the last
    30 days on `exchange`. Set-based SQL pulls, python aggregation over a few
    thousand rows, ONE bulk upsert."""
    t0 = time.monotonic()
    now = datetime.utcnow()
    d30 = now - timedelta(days=30)
    d7_days = {(now - timedelta(days=i)).date() for i in range(7)}
    h48 = now - timedelta(hours=48)
    h25 = now - timedelta(hours=25)

    sf = get_session_factory()
    async with sf() as s:
        # daily aggregates, 30d: last cumulative volume per wallet/day ('all')
        daily_all = (await s.execute(text(
            "SELECT wallet_address w, DATE(timestamp) d, MAX(volume) v "
            "FROM leaderboard_snapshots "
            "WHERE exchange = :e AND period = 'all' AND timestamp >= :d30 "
            "GROUP BY wallet_address, DATE(timestamp)"
        ), {"e": exchange, "d30": d30})).all()
        # day-window presence per wallet/day (fallback activity signal)
        daily_day = (await s.execute(text(
            "SELECT wallet_address w, DATE(timestamp) d, MAX(volume) v "
            "FROM leaderboard_snapshots "
            "WHERE exchange = :e AND period = 'day' AND timestamp >= :d30 "
            "GROUP BY wallet_address, DATE(timestamp)"
        ), {"e": exchange, "d30": d30})).all()
        # hourly resolution, last 48h ('all') for last_active/velocity
        hourly_all = (await s.execute(text(
            "SELECT wallet_address w, timestamp ts, volume v "
            "FROM leaderboard_snapshots "
            "WHERE exchange = :e AND period = 'all' AND timestamp >= :h48 "
            "ORDER BY wallet_address, timestamp"
        ), {"e": exchange, "h48": h48})).all()
        hourly_day = (await s.execute(text(
            "SELECT wallet_address w, timestamp ts, volume v "
            "FROM leaderboard_snapshots "
            "WHERE exchange = :e AND period = 'day' AND timestamp >= :h25 "
            "ORDER BY wallet_address, timestamp"
        ), {"e": exchange, "h25": h25})).all()
        # latest batch per window -> consistency flags (pnl sign per window)
        flags_rows = (await s.execute(text(
            "SELECT s.wallet_address w, s.period p, s.pnl_total pnl "
            "FROM leaderboard_snapshots s JOIN ("
            "  SELECT period, MAX(timestamp) mt FROM leaderboard_snapshots "
            "  WHERE exchange = :e GROUP BY period"
            ") lb ON lb.period = s.period AND lb.mt = s.timestamp "
            "WHERE s.exchange = :e"
        ), {"e": exchange})).all()
        # week-window PnL at the last snapshot of each of the last 7 days
        trend_rows = (await s.execute(text(
            "SELECT s.wallet_address w, DATE(s.timestamp) d, s.pnl_total pnl "
            "FROM leaderboard_snapshots s JOIN ("
            "  SELECT wallet_address, DATE(timestamp) dd, MAX(timestamp) mt "
            "  FROM leaderboard_snapshots "
            "  WHERE exchange = :e AND period = 'week' AND timestamp >= :d7 "
            "  GROUP BY wallet_address, DATE(timestamp)"
            ") x ON x.wallet_address = s.wallet_address AND x.mt = s.timestamp "
            "WHERE s.exchange = :e AND s.period = 'week'"
        ), {"e": exchange, "d7": now - timedelta(days=7)})).all() if exchange == "hl" else []

    # ---- python aggregation ----
    # per-wallet daily cumulative-volume series
    day_series: dict[str, dict] = {}
    for w, d, v in daily_all:
        day_series.setdefault(w, {})[d] = float(v or 0)
    day_presence: dict[str, dict] = {}
    for w, d, v in daily_day:
        day_presence.setdefault(w, {})[d] = float(v or 0)

    wallets = set(day_series) | set(day_presence)

    # active days: cumulative growth day-over-day, else day-window presence
    def active_days(w: str, days: int) -> tuple[int, int]:
        cutoff = (now - timedelta(days=days)).date()
        cum = day_series.get(w) or {}
        pres = day_presence.get(w) or {}
        all_days = sorted(set(cum) | set(pres))
        hist = len([d for d in all_days if d >= (now - timedelta(days=30)).date()])
        active = set()
        prev_v = None
        for d in sorted(cum):
            v = cum[d]
            if prev_v is not None and v > prev_v and d >= cutoff:
                active.add(d)
            prev_v = v
        for d, v in pres.items():
            if d >= cutoff and v > 0:
                active.add(d)
        return len(active), hist

    # hourly series for last_active + velocity
    h_all: dict[str, list] = {}
    for w, ts, v in hourly_all:
        h_all.setdefault(w, []).append((ts, float(v or 0)))
    h_day: dict[str, list] = {}
    for w, ts, v in hourly_day:
        h_day.setdefault(w, []).append((ts, float(v or 0)))

    flags: dict[str, int] = {}
    if exchange == "hl":
        BITS = {"day": 1, "week": 2, "month": 4, "all": 8}
        for w, p, pnl in flags_rows:
            if p in BITS and pnl is not None and float(pnl) > 0:
                flags[w] = flags.get(w, 0) | BITS[p]
            else:
                flags.setdefault(w, 0)

    trend_series: dict[str, dict] = {}
    for w, d, pnl in trend_rows:
        trend_series.setdefault(w, {})[d] = float(pnl or 0)

    def lsq_slope(w: str):
        pts = sorted((trend_series.get(w) or {}).items())
        if len(pts) < 3:
            return None
        xs = [(d - pts[0][0]).days for d, _ in pts]
        ys = [p for _, p in pts]
        n = len(xs)
        mx, my = sum(xs) / n, sum(ys) / n
        den = sum((x - mx) ** 2 for x in xs)
        return round(sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / den, 2) if den else None

    rows = []
    for w in wallets:
        a7, hist = active_days(w, 7)
        a30, _ = active_days(w, 30)
        # last_active: newest hourly cumulative increase (48h), else newest
        # active day at day precision (that day's last snapshot ts)
        last_active = None
        ser = h_all.get(w) or []
        for i in range(len(ser) - 1, 0, -1):
            if ser[i][1] > ser[i - 1][1]:
                last_active = ser[i][0]
                break
        if last_active is None:
            cum = day_series.get(w) or {}
            prev_v = None
            best_d = None
            for d in sorted(cum):
                if prev_v is not None and cum[d] > prev_v:
                    best_d = d
                prev_v = cum[d]
            if best_d is not None:
                last_active = datetime.combine(best_d, datetime.max.time().replace(microsecond=0))
        # velocity: cumulative delta over ~24h, else sum of positive day-window deltas
        velocity = None
        if len(ser) >= 2:
            base = [p for p in ser if p[0] >= now - timedelta(hours=24)]
            if len(base) >= 2:
                velocity = round(max(0.0, base[-1][1] - base[0][1]), 2)
        if velocity is None:
            dser = h_day.get(w) or []
            if len(dser) >= 2:
                velocity = round(sum(max(0.0, dser[i][1] - dser[i - 1][1])
                                     for i in range(1, len(dser))), 2)
        rows.append({
            "e": exchange, "w": w,
            "a7": min(a7, 7), "a30": min(a30, 30),
            "la": last_active,
            "vv": velocity,
            "cf": flags.get(w) if exchange == "hl" else None,
            "tr": lsq_slope(w) if exchange == "hl" else None,
            "hd": min(hist, 30),
            "ca": now,
        })

    if rows:
        sf = get_session_factory()
        async with sf() as s:
            await s.execute(text(
                "INSERT INTO trader_activity_metrics "
                "(exchange, wallet_address, active_days_7d, active_days_30d, "
                " last_active_at, vol_velocity_24h, consistency_flags, "
                " pnl_trend_7d, history_days, computed_at) "
                "VALUES (:e, :w, :a7, :a30, :la, :vv, :cf, :tr, :hd, :ca) "
                "ON DUPLICATE KEY UPDATE "
                " active_days_7d=VALUES(active_days_7d), "
                " active_days_30d=VALUES(active_days_30d), "
                " last_active_at=VALUES(last_active_at), "
                " vol_velocity_24h=VALUES(vol_velocity_24h), "
                " consistency_flags=VALUES(consistency_flags), "
                " pnl_trend_7d=VALUES(pnl_trend_7d), "
                " history_days=VALUES(history_days), "
                " computed_at=VALUES(computed_at)"
            ), rows)
            await s.commit()

    dur = round(time.monotonic() - t0, 2)
    stats = {"exchange": exchange, "wallets": len(rows), "duration_sec": dur}
    logger.info("activity metrics: %s", stats)
    return stats


async def run() -> dict:
    """Both exchanges; called at the end of every hourly HL ingest."""
    out = {}
    for e in ("hl", "perpl"):
        try:
            out[e] = await compute_exchange(e)
        except Exception:
            logger.exception("activity metrics failed for %s", e)
            out[e] = {"error": True}
    _last_run.update(out)
    return out


async def get_metrics_bulk(wallets: list[str], exchange: str) -> dict[str, dict]:
    """Read-only bulk fetch for list/profile endpoints."""
    wl = [w.lower() for w in wallets]
    if not wl:
        return {}
    placeholders = ",".join(f":w{i}" for i in range(len(wl)))
    params = {f"w{i}": w for i, w in enumerate(wl)}
    params["e"] = exchange
    sf = get_session_factory()
    async with sf() as s:
        rows = (await s.execute(text(
            "SELECT wallet_address, active_days_7d, active_days_30d, "
            " last_active_at, vol_velocity_24h, consistency_flags, "
            " pnl_trend_7d, history_days, computed_at "
            f"FROM trader_activity_metrics WHERE exchange = :e "
            f"AND wallet_address IN ({placeholders})"
        ), params)).mappings().all()
    out = {}
    for r in rows:
        out[r["wallet_address"].lower()] = {
            "active_days_7d": r["active_days_7d"],
            "active_days_30d": r["active_days_30d"],
            "last_active_at": r["last_active_at"].isoformat() if r["last_active_at"] else None,
            "vol_velocity_24h": float(r["vol_velocity_24h"]) if r["vol_velocity_24h"] is not None else None,
            "consistency_flags": r["consistency_flags"],
            "pnl_trend_7d": float(r["pnl_trend_7d"]) if r["pnl_trend_7d"] is not None else None,
            "history_days": r["history_days"],
            "computed_at": r["computed_at"].isoformat() if r["computed_at"] else None,
        }
    return out


def get_last_run() -> dict:
    return dict(_last_run)

async def activity_order(wallets: list[str], exchange: str, mode: str) -> list[str]:
    """Board wallets ordered by activity metrics (one indexed SQL, NULLs last).
    mode 'active':     most active days (7d), then 24h volume velocity
    mode 'consistent': most profitable windows simultaneously (BIT_COUNT of
                       consistency_flags), then week-PnL trend slope"""
    wl = [w.lower() for w in wallets]
    if not wl:
        return []
    placeholders = ",".join(f":w{i}" for i in range(len(wl)))
    params = {f"w{i}": w for i, w in enumerate(wl)}
    params["e"] = exchange
    if mode == "consistent":
        order = ("(consistency_flags IS NULL), BIT_COUNT(consistency_flags) DESC, "
                 "(pnl_trend_7d IS NULL), pnl_trend_7d DESC")
    else:
        order = ("(active_days_7d IS NULL), active_days_7d DESC, "
                 "(vol_velocity_24h IS NULL), vol_velocity_24h DESC")
    sf = get_session_factory()
    async with sf() as s:
        rows = (await s.execute(text(
            f"SELECT wallet_address FROM trader_activity_metrics "
            f"WHERE exchange = :e AND wallet_address IN ({placeholders}) "
            f"ORDER BY {order}"
        ), params)).scalars().all()
    return [w.lower() for w in rows]
