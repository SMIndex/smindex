import asyncio
import time
from datetime import datetime

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from sqlalchemy import select, desc

from app.db.database import get_session_factory
from app.db.models import Leader, LeaderTrade, User, LeaderboardSnapshot
from app.routers.auth import get_authenticated_user
from app.services.perpl_client import perpl_client
from app.utils import resp_cache
from app.utils.logger import get_logger

logger = get_logger(__name__)

router = APIRouter(prefix="/leaders", tags=["leaders"])

# Beta speed pass (2026-10-08): both of these measured multi-second p95 on this
# box (list 1.77/9.00 s, positions 3.76/12.88 s). 60 s TTL + single flight +
# last-good-on-error.
#
# /positions/{w} is the LIVE VENUE READ (chain_reader.get_trader_detail) - the
# same source the reconcile sweep and my own verification reads use - so it is
# never allowed to answer a money or position view with a cached copy:
#   * fresh=1          -> no cache at all (every call reads the chain). Every
#                         money/position caller sends it: Terminal positions,
#                         the post-order and post-close verification reads, the
#                         copy dashboard, the Dashboard, the live-copy
#                         leader-state check and the account-id lookup.
#   * caller's OWN wallet (JWT) -> 5 s, a second line of defence if some caller
#                         forgets the flag.
#   * anyone else, no flag      -> 60 s. This is the public view only: the
#                         leaderboard positions widget and the leader profile's
#                         resting orders.
# Recording a fill (POST /api/trades, /api/trades/orders) drops the wallet's
# entry as well, so even a 60 s entry cannot survive a trade.
_leaders_cache = resp_cache.RespCache("leaders_list", ttl=60)
_positions_cache = resp_cache.RespCache("leader_positions", ttl=60, stale_max=120)
OWN_WALLET_TTL_S = 5.0
FRESH_TTL_S = 0.0        # 0 = always read the chain (the lock still serializes a burst)

# Cache for live leaderboard per exchange+period+sorting key
_lb_cache: dict[str, dict] = {}
_LB_CACHE_TTL = 30.0  # 30 seconds
# HL rows come from OUR DB (hourly ingest), the ingest re-warms the cache after
# every run, so the TTL only needs to outlive one ingest interval.
_LB_CACHE_TTL_HL = 4000.0

HL_PERIODS = {"all", "day", "week", "month"}

# Perpl combos the background refresher keeps warm. Requests for these serve
# the cached rows even past TTL (stale-while-refreshing) so no visitor ever
# waits on the 1-5s Perpl upstream call.
PERPL_REFRESH_COMBOS: tuple[tuple[str, str], ...] = (
    ("all", "pnl"), ("all", "vol"), ("day", "pnl"), ("day", "vol"))
_PERPL_REFRESH_INTERVAL = 30.0
_refresh_task: asyncio.Task | None = None


async def _load_hl_leaderboard(period: str, sorting: str) -> list[dict]:
    """Hyperliquid leaderboard from OUR DB snapshots (latest ingest batch for
    the window), shaped exactly like the Perpl parsed rows. Hidden profiles
    are excluded here so every consumer inherits the filter."""
    from app.services.copy import trader_profiles

    if period not in HL_PERIODS:
        period = "all"
    sf = get_session_factory()
    async with sf() as session:
        latest_ts = (await session.execute(
            select(LeaderboardSnapshot.timestamp)
            .where(LeaderboardSnapshot.exchange == "hl",
                   LeaderboardSnapshot.period == period)
            .order_by(desc(LeaderboardSnapshot.timestamp)).limit(1)
        )).scalar_one_or_none()
        if latest_ts is None:
            return []
        # Core columns, not ORM objects: a window is ~5,400 rows and full ORM
        # hydration of them was part of the "MySQL row parsing" stall class.
        rows = (await session.execute(
            select(LeaderboardSnapshot.wallet_address, LeaderboardSnapshot.pnl_total,
                   LeaderboardSnapshot.roi, LeaderboardSnapshot.volume)
            .where(LeaderboardSnapshot.exchange == "hl",
                   LeaderboardSnapshot.period == period,
                   LeaderboardSnapshot.timestamp == latest_ts)
            .order_by(LeaderboardSnapshot.rank)
        )).all()

    hidden = await trader_profiles.hidden_wallets("hl")

    def _shape() -> list[dict]:
        out = [
            {"wallet_address": w, "pnl_total": p, "roi": roi, "volume": v, "exchange": "hl"}
            for (w, p, roi, v) in rows if w.lower() not in hidden
        ]
        if sorting == "vol":
            out.sort(key=lambda t: -(t["volume"] or 0))
        return out

    # thousands of rows: shape them in a thread so the loop stays free
    if len(rows) > 1000:
        return await asyncio.to_thread(_shape)
    return _shape()


async def _get_live_leaderboard(period: str = "all", sorting: str = "pnl",
                                exchange: str = "perpl") -> list[dict]:
    """Leaderboard rows with caching. perpl = live Perpl API passthrough;
    hl = latest DB ingest batch (hourly)."""
    cache_key = f"{exchange}:{period}:{sorting}"
    now = time.monotonic()
    entry = _lb_cache.get(cache_key)
    ttl = _LB_CACHE_TTL_HL if exchange == "hl" else _LB_CACHE_TTL
    if entry and now - entry["timestamp"] < ttl:
        return entry["data"]
    # Stale-while-refreshing: combos the background refresher owns never block
    # a request on the Perpl upstream, serve what we have, the loop updates it.
    if (entry and exchange == "perpl" and (period, sorting) in PERPL_REFRESH_COMBOS
            and _refresh_task and not _refresh_task.done()):
        return entry["data"]

    if exchange == "hl":
        traders = await _load_hl_leaderboard(period, sorting)
    else:
        traders = await perpl_client.get_leaderboard_parsed(period, sorting)
    _lb_cache[cache_key] = {"data": traders, "timestamp": now}
    return traders


async def warm_hl_lb_cache() -> None:
    """Re-warm every HL leaderboard cache combo from the DB. Called by the
    hourly ingest after each run (and at refresher start) so no visitor ever
    pays the cold DB-load cost."""
    for period in HL_PERIODS:
        for sorting in ("pnl", "vol"):
            try:
                traders = await _load_hl_leaderboard(period, sorting)
                _lb_cache[f"hl:{period}:{sorting}"] = {
                    "data": traders, "timestamp": time.monotonic()}
            except Exception as exc:
                logger.warning("HL lb cache warm failed for %s/%s: %s",
                               period, sorting, exc)


def drop_hl_lb_cache() -> None:
    """Invalidate the (long-TTL) HL leaderboard cache, used by the admin
    hide/unhide flow so it takes effect on the next request."""
    for k in [k for k in _lb_cache if k.startswith("hl:")]:
        _lb_cache.pop(k, None)


async def _perpl_refresh_loop() -> None:
    # Warm HL from DB once at boot so the first visitor is fast even before
    # the first hourly ingest completes.
    try:
        await warm_hl_lb_cache()
    except Exception:
        logger.exception("initial HL lb warm failed")
    while True:
        for period, sorting in PERPL_REFRESH_COMBOS:
            try:
                traders = await perpl_client.get_leaderboard_parsed(period, sorting)
                _lb_cache[f"perpl:{period}:{sorting}"] = {
                    "data": traders, "timestamp": time.monotonic()}
            except Exception as exc:
                # keep serving the previous rows, never poison the cache
                logger.warning("perpl lb refresh failed for %s/%s: %s",
                               period, sorting, exc)
        await asyncio.sleep(_PERPL_REFRESH_INTERVAL)


def start_lb_refresher() -> None:
    global _refresh_task
    if _refresh_task is None or _refresh_task.done():
        _refresh_task = asyncio.get_event_loop().create_task(_perpl_refresh_loop())
        logger.info("Perpl leaderboard refresher started (interval %ss)",
                    _PERPL_REFRESH_INTERVAL)


async def stop_lb_refresher() -> None:
    global _refresh_task
    if _refresh_task and not _refresh_task.done():
        _refresh_task.cancel()
        try:
            await _refresh_task
        except asyncio.CancelledError:
            pass
    _refresh_task = None


@router.get("/")
async def list_leaders(
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
    sort: str = Query("pnl"),
    period: str = Query("all"),
    active_only: bool = Query(False),
) -> list[dict]:
    """List leaders from live Perpl leaderboard API (60 s cache, single flight).

    sort: "pnl" or "vol"
    period: "all", "day"
    active_only: if true, only return traders with 24h volume > 0
    """
    return await _leaders_cache.get(
        (skip, limit, sort, period, active_only),
        lambda: _build_leaders(skip, limit, sort, period, active_only))


async def _build_leaders(skip: int, limit: int, sort: str, period: str,
                         active_only: bool) -> list[dict]:
    sorting = "pnl" if sort in ("pnl", "pnl_total", "pnl_7d", "pnl_30d") else "vol"

    if active_only:
        # Get 24h data and filter to traders with volume > 0
        traders = await _get_live_leaderboard("day", sorting)
        traders = [t for t in traders if t["volume"] > 0]
    else:
        traders = await _get_live_leaderboard(period, sorting)

    # Apply pagination
    page = traders[skip : skip + limit]

    # Enrich with follower counts and usernames
    if page:
        from app.services.copy_position_service import get_follower_count
        from app.db.database import get_session_factory
        from sqlalchemy import select as sa_select
        from app.db.models import User

        # Batch-fetch usernames
        wallets = [t["wallet_address"].lower() for t in page]
        async_session = get_session_factory()
        async with async_session() as session:
            result = await session.execute(
                sa_select(User.wallet_address, User.username).where(
                    User.wallet_address.in_(wallets)
                )
            )
            username_map = {r.wallet_address: r.username for r in result.all()}

        for trader in page:
            trader["followers_count"] = await get_follower_count(trader["wallet_address"])
            trader["username"] = username_map.get(trader["wallet_address"].lower())

    return page


@router.get("/by-address/{wallet_address}")
async def get_leader_by_address(wallet_address: str) -> dict:
    """Get a specific trader's stats from live leaderboard by wallet address."""
    traders = await _get_live_leaderboard()
    addr_lower = wallet_address.lower()
    for t in traders:
        if t["wallet_address"].lower() == addr_lower:
            return t
    raise HTTPException(status_code=404, detail="Trader not found on leaderboard")


@router.get("/positions/{wallet_address}")
async def get_trader_positions(wallet_address: str,
                               fresh: int = Query(0, ge=0, le=1,
                                                  description="1 = read the chain now, never a cached copy "
                                                              "(money and position views must send this)"),
                               authorization: str | None = Header(default=None)) -> dict:
    """A trader's live on-chain account and open positions.

    fresh=1 -> uncached live chain read. Otherwise: 5 s for the caller's own
    wallet, 60 s for other wallets (public views). Single flight in every case."""
    own = False
    if authorization:
        try:
            from app.routers.auth import get_optional_user
            user = await get_optional_user(authorization)
            own = bool(user) and user.wallet_address.lower() == wallet_address.lower()
        except Exception:
            own = False
    ttl = FRESH_TTL_S if fresh else (OWN_WALLET_TTL_S if own else None)
    detail = await _positions_cache.get(
        wallet_address.lower(), lambda: _read_trader_positions(wallet_address), ttl=ttl)
    if not detail:
        raise HTTPException(status_code=404, detail="Trader account not found on-chain")
    return detail


def drop_positions_cache(wallet_address: str) -> None:
    """Invalidate one wallet's cached positions - called right after that
    wallet records a fill, so a close shows up on the next poll."""
    _positions_cache.invalidate(wallet_address.lower())


async def _read_trader_positions(wallet_address: str) -> dict | None:
    from app.services.chain_reader import get_trader_detail
    return await asyncio.to_thread(get_trader_detail, wallet_address)


@router.get("/{leader_id}")
async def get_leader(leader_id: int) -> dict:
    """Get leader from DB (for copy trade references)."""
    async_session = get_session_factory()
    async with async_session() as session:
        result = await session.execute(
            select(Leader).where(Leader.id == leader_id)
        )
        leader = result.scalar_one_or_none()

    if not leader:
        raise HTTPException(status_code=404, detail="Leader not found")

    # Try to enrich with live data
    traders = await _get_live_leaderboard()
    live_stats = None
    for t in traders:
        if t["wallet_address"].lower() == leader.wallet_address.lower():
            live_stats = t
            break

    return {
        "id": leader.id,
        "wallet_address": leader.wallet_address,
        "display_name": leader.display_name,
        "is_active": leader.is_active,
        "stats": {
            "total_trades": leader.total_trades,
            "win_rate": leader.win_rate,
            "pnl_total": live_stats["pnl_total"] if live_stats else leader.pnl_total,
            "roi": live_stats["roi"] if live_stats else 0.0,
            "volume": live_stats["volume"] if live_stats else 0.0,
            "pnl_7d": leader.pnl_7d,
            "pnl_30d": leader.pnl_30d,
            "sharpe_ratio": leader.sharpe_ratio,
            "max_drawdown": leader.max_drawdown,
            "avg_leverage": leader.avg_leverage,
            "followers_count": leader.followers_count,
        },
    }


@router.get("/{leader_id}/trades")
async def get_leader_trades(
    leader_id: int,
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
) -> list[dict]:
    async_session = get_session_factory()
    async with async_session() as session:
        result = await session.execute(
            select(Leader).where(Leader.id == leader_id)
        )
        leader = result.scalar_one_or_none()
        if not leader:
            raise HTTPException(status_code=404, detail="Leader not found")

        stmt = (
            select(LeaderTrade)
            .where(LeaderTrade.leader_id == leader_id)
            .order_by(desc(LeaderTrade.timestamp))
            .offset(skip)
            .limit(limit)
        )
        result = await session.execute(stmt)
        trades = result.scalars().all()

    return [
        {
            "id": t.id,
            "leader_id": t.leader_id,
            "market_id": t.market_id,
            "side": t.side,
            "size": t.size,
            "price": t.price,
            "leverage": t.leverage,
            "is_close": t.is_close,
            "timestamp": t.timestamp.isoformat() if t.timestamp else None,
            "raw_fill": t.raw_fill,
        }
        for t in trades
    ]


@router.post("/register")
async def register_leader(
    user: User = Depends(get_authenticated_user),
) -> dict:
    async_session = get_session_factory()
    async with async_session() as session:
        result = await session.execute(
            select(Leader).where(Leader.user_id == user.id)
        )
        existing = result.scalar_one_or_none()
        if existing:
            raise HTTPException(status_code=409, detail="Already registered as leader")

        now = datetime.utcnow()
        leader = Leader(
            user_id=user.id,
            wallet_address=user.wallet_address,
            display_name=None,
            is_active=True,
            total_trades=0,
            win_rate=0.0,
            pnl_total=0.0,
            pnl_7d=0.0,
            pnl_30d=0.0,
            sharpe_ratio=0.0,
            max_drawdown=0.0,
            avg_leverage=0.0,
            followers_count=0,
            registered_at=now,
        )
        session.add(leader)
        await session.commit()
        await session.refresh(leader)

    logger.info("New leader registered: user=%s leader_id=%s", user.id, leader.id)

    return {
        "id": leader.id,
        "wallet_address": leader.wallet_address,
        "display_name": leader.display_name,
        "is_active": leader.is_active,
        "stats": {
            "total_trades": 0,
            "win_rate": 0.0,
            "pnl_total": 0.0,
            "roi": 0.0,
            "volume": 0.0,
            "pnl_7d": 0.0,
            "pnl_30d": 0.0,
            "sharpe_ratio": 0.0,
            "max_drawdown": 0.0,
            "avg_leverage": 0.0,
            "followers_count": 0,
        },
    }


@router.get("/performance/{wallet_address}")
async def get_leader_performance(
    wallet_address: str,
    days: int = Query(30, ge=1, le=365),
) -> list[dict]:
    """Get leader's PnL/ROI time series from leaderboard snapshots."""
    from datetime import timedelta

    since = datetime.utcnow() - timedelta(days=days)
    wallet = wallet_address.lower()

    async_session = get_session_factory()
    async with async_session() as session:
        result = await session.execute(
            select(LeaderboardSnapshot)
            .where(
                LeaderboardSnapshot.wallet_address == wallet,
                LeaderboardSnapshot.timestamp >= since,
            )
            .order_by(LeaderboardSnapshot.timestamp)
        )
        snapshots = result.scalars().all()

    # Downsample if too many points (keep ~200 max)
    data = [
        {
            "timestamp": s.timestamp.isoformat(),
            "pnl_total": s.pnl_total,
            "roi": s.roi,
            "volume": s.volume,
        }
        for s in snapshots
    ]

    if len(data) > 200:
        step = len(data) // 200
        data = data[::step]

    return data
