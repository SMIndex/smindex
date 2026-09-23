"""Trader profiles + daily stats for copy v1 discovery.

Reads the live Perpl leaderboard (via the existing leaders helper) and upserts
trader_profiles / trader_stats_daily so discovery + profile pages have persistent
identity and a daily stats series. No orders, no chain writes.
"""
from datetime import datetime

from sqlalchemy import select, desc

from app.db.database import get_session_factory
from app.db.copy_models import TraderProfile, TraderStatsDaily
from app.utils.logger import get_logger

logger = get_logger(__name__)


def _profile_dict(p: TraderProfile) -> dict:
    return {
        "wallet_address": p.wallet_address,
        "exchange": getattr(p, "exchange", "perpl"),
        "display_name": p.display_name,
        "is_verified": bool(p.is_verified),
        "source": p.source,
        "bio": p.bio,
        "is_hidden": bool(getattr(p, "is_hidden", False)),
        "first_seen_at": p.first_seen_at.isoformat() if p.first_seen_at else None,
        "last_seen_at": p.last_seen_at.isoformat() if p.last_seen_at else None,
        "last_fill_at": p.last_fill_at.isoformat() if getattr(p, "last_fill_at", None) else None,
    }


def _f(x):
    return float(x) if x is not None else None


def _stat_dict(s: TraderStatsDaily) -> dict:
    return {
        "trader_wallet": s.trader_wallet,
        "stat_date": s.stat_date.isoformat() if s.stat_date else None,
        "pnl": _f(s.pnl),
        "roi": _f(s.roi),
        "volume": _f(s.volume),
        "win_rate": _f(s.win_rate),
        "trades": s.trades,
        "max_drawdown": _f(s.max_drawdown),
        "rank": s.rank,
        "captured_at": s.captured_at.isoformat() if s.captured_at else None,
    }


async def upsert_profile(wallet: str, display_name: str | None = None,
                         source: str = "leaderboard", exchange: str = "perpl") -> dict:
    """Create-or-touch a trader profile for (exchange, wallet).

    NEVER touches is_hidden — the lazy write path in public routes must not
    resurrect a profile an admin hid (it stays hidden; list queries filter it).
    """
    wallet = wallet.lower()
    sf = get_session_factory()
    async with sf() as session:
        p = (await session.execute(
            select(TraderProfile).where(
                TraderProfile.wallet_address == wallet,
                TraderProfile.exchange == exchange,
            )
        )).scalar_one_or_none()
        now = datetime.utcnow()
        if p:
            p.last_seen_at = now
            if display_name and not p.display_name:
                p.display_name = display_name
        else:
            p = TraderProfile(
                exchange=exchange,
                wallet_address=wallet,
                display_name=display_name,
                is_verified=False,
                source=source,
                first_seen_at=now,
                last_seen_at=now,
            )
            session.add(p)
        await session.commit()
        await session.refresh(p)
        return _profile_dict(p)


async def get_profiles_bulk(wallets: list[str], exchange: str = "perpl") -> dict[str, dict]:
    """Read-only bulk profile fetch (ONE SELECT, no writes)."""
    wl = [w.lower() for w in wallets]
    if not wl:
        return {}
    sf = get_session_factory()
    async with sf() as session:
        existing = (await session.execute(
            select(TraderProfile).where(
                TraderProfile.exchange == exchange,
                TraderProfile.wallet_address.in_(wl),
            )
        )).scalars().all()
        return {p.wallet_address: _profile_dict(p) for p in existing}


# Throttle for the list-page write path: the same 50-wallet page re-touched
# every poll was ~1s of UPDATE churn per request (measured limit=1 0.04s vs
# limit=50 1.1s on prod). last_seen_at only needs page-level precision.
_BULK_TOUCH_INTERVAL = 600.0
_bulk_touch_at: dict[str, float] = {}


async def upsert_profiles_bulk(wallets: list[str], exchange: str = "perpl") -> dict[str, dict]:
    """Create-or-touch many profiles in ONE session (3 statements total) —
    replaces the per-row upsert loop that cost ~40s on a 50-row list page
    (HL_QUALITY_REPORT E3). Same semantics as upsert_profile: never touches
    is_hidden, never overwrites an existing display_name.
    The write path runs at most once per page-signature per 10 min; between
    touches the same data comes from a read-only SELECT."""
    import time as _time
    wl = [w.lower() for w in wallets]
    if not wl:
        return {}
    sig = f"{exchange}:{hash(tuple(sorted(set(wl))))}"
    last = _bulk_touch_at.get(sig)
    if last is not None and _time.monotonic() - last < _BULK_TOUCH_INTERVAL:
        profs = await get_profiles_bulk(wl, exchange)
        if len(profs) == len(set(wl)):   # every row exists — reads suffice
            return profs
    now = datetime.utcnow()
    sf = get_session_factory()
    async with sf() as session:
        existing = (await session.execute(
            select(TraderProfile).where(
                TraderProfile.exchange == exchange,
                TraderProfile.wallet_address.in_(wl),
            )
        )).scalars().all()
        by_wallet = {p.wallet_address: p for p in existing}
        for p in existing:
            p.last_seen_at = now
        for w in wl:
            if w not in by_wallet:
                p = TraderProfile(
                    exchange=exchange, wallet_address=w,
                    is_verified=False, source="leaderboard",
                    first_seen_at=now, last_seen_at=now,
                )
                session.add(p)
                by_wallet[w] = p
        await session.commit()
        _bulk_touch_at[sig] = _time.monotonic()
        return {w: _profile_dict(p) for w, p in by_wallet.items()}


async def get_profile(wallet: str, exchange: str = "perpl") -> dict | None:
    wallet = wallet.lower()
    sf = get_session_factory()
    async with sf() as session:
        p = (await session.execute(
            select(TraderProfile).where(
                TraderProfile.wallet_address == wallet,
                TraderProfile.exchange == exchange,
            )
        )).scalar_one_or_none()
        return _profile_dict(p) if p else None


async def touch_last_fill(wallet: str, exchange: str, ts: datetime) -> None:
    """Record a detected fill time — ONE indexed UPDATE, forward-only (an
    out-of-order or replayed event can never move the timestamp backwards).
    No-op when the row doesn't exist (detection paths upsert profiles anyway)."""
    from sqlalchemy import text as _text
    sf = get_session_factory()
    async with sf() as session:
        await session.execute(_text(
            "UPDATE trader_profiles SET last_fill_at = :ts "
            "WHERE exchange = :e AND wallet_address = :w "
            "AND (last_fill_at IS NULL OR last_fill_at < :ts)"
        ), {"ts": ts, "e": exchange, "w": wallet.lower()})
        await session.commit()


async def recent_activity_order(wallets: list[str], exchange: str) -> list[str]:
    """The given wallets ordered by recent activity: last_fill_at DESC with
    NULLs LAST (MySQL: ORDER BY (last_fill_at IS NULL), last_fill_at DESC)."""
    from sqlalchemy import text as _text
    wl = [w.lower() for w in wallets]
    if not wl:
        return []
    placeholders = ",".join(f":w{i}" for i in range(len(wl)))
    params = {f"w{i}": w for i, w in enumerate(wl)}
    params["e"] = exchange
    sf = get_session_factory()
    async with sf() as session:
        rows = (await session.execute(_text(
            f"SELECT wallet_address FROM trader_profiles "
            f"WHERE exchange = :e AND wallet_address IN ({placeholders}) "
            f"ORDER BY (last_fill_at IS NULL), last_fill_at DESC"
        ), params)).scalars().all()
    return [w.lower() for w in rows]


# Hidden-set cache: queried on every list request; hides change only via the
# admin route (which calls invalidate_hidden_cache for immediate effect).
_hidden_cache: dict[str, tuple[float, set[str]]] = {}
_HIDDEN_TTL = 60.0


def invalidate_hidden_cache() -> None:
    _hidden_cache.clear()


async def hidden_wallets(exchange: str) -> set[str]:
    """Wallets hidden by an admin on this exchange (excluded from public lists)."""
    import time as _time
    c = _hidden_cache.get(exchange)
    if c and _time.monotonic() - c[0] < _HIDDEN_TTL:
        return c[1]
    sf = get_session_factory()
    async with sf() as session:
        rows = (await session.execute(
            select(TraderProfile.wallet_address).where(
                TraderProfile.exchange == exchange,
                TraderProfile.is_hidden == True,  # noqa: E712
            )
        )).scalars().all()
    hidden = {w.lower() for w in rows}
    _hidden_cache[exchange] = (_time.monotonic(), hidden)
    return hidden


async def upsert_daily_stat(wallet: str, *, pnl=None, roi=None, volume=None,
                            win_rate=None, trades=0, max_drawdown=None, rank=None) -> None:
    """Upsert today's (UTC) stats row for a trader. One row per (wallet, day)."""
    wallet = wallet.lower()
    today = datetime.utcnow().date()
    sf = get_session_factory()
    async with sf() as session:
        s = (await session.execute(
            select(TraderStatsDaily).where(
                TraderStatsDaily.trader_wallet == wallet,
                TraderStatsDaily.stat_date == today,
            )
        )).scalar_one_or_none()
        now = datetime.utcnow()
        if s:
            s.pnl, s.roi, s.volume = pnl, roi, volume
            s.win_rate, s.trades, s.max_drawdown, s.rank = win_rate, trades or 0, max_drawdown, rank
            s.captured_at = now
        else:
            session.add(TraderStatsDaily(
                trader_wallet=wallet, stat_date=today,
                pnl=pnl, roi=roi, volume=volume, win_rate=win_rate,
                trades=trades or 0, max_drawdown=max_drawdown, rank=rank,
                captured_at=now,
            ))
        await session.commit()


async def get_stats_series(wallet: str, days: int = 30) -> list[dict]:
    """Return trader_stats_daily rows for the last `days`. Empty list if none yet."""
    from datetime import timedelta
    wallet = wallet.lower()
    since = (datetime.utcnow() - timedelta(days=days)).date()
    sf = get_session_factory()
    async with sf() as session:
        rows = (await session.execute(
            select(TraderStatsDaily)
            .where(
                TraderStatsDaily.trader_wallet == wallet,
                TraderStatsDaily.stat_date >= since,
            )
            .order_by(TraderStatsDaily.stat_date)
        )).scalars().all()
    return [_stat_dict(s) for s in rows]
