# =============================================================================
# DEPRECATED (copy v0). These /copy/* routes (follow, my-follows, history,
# positions, my-copies, stats, followed-leaders/positions, leaders/{w}/followers)
# are superseded by copy v1: traders.py (discovery/profile), watchlist.py (watch),
# and copy.py (subscriptions + paper results). They remain functional for now and
# are UNCHANGED in Phase A. Do not add new routes here — add them under copy v1.
# Note carried over from audit: GET /copy/leaders/{w}/followers is unauthenticated
# (will be auth-gated / count-only in the v1 replacement).
# =============================================================================
import asyncio
import time
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import select, desc

from app.db.database import get_session_factory
from app.db.models import WalletFollow, User, CopyTradeLog, Leader
from app.routers.auth import get_authenticated_user
from app.utils.logger import get_logger

logger = get_logger(__name__)

router = APIRouter(prefix="/copy", tags=["copytrade"])

# NOTE: the old hardcoded VALID_MARKETS / MAX_LEVERAGE_PER_MARKET constants were
# removed (stale SOL=30-era values, and dead — nothing consumed them). Market
# validity comes from app.services.market_registry; per-market leverage caps
# from /api/market-configs (initial_margin/100).

# In-memory cache for followed-leader live positions, keyed by follower wallet.
# 15s TTL matches the frontend refetch interval — same-window polls hit cache.
_followed_positions_cache: dict[str, tuple[float, list[dict]]] = {}
_FOLLOWED_POSITIONS_TTL = 15.0


class FollowRequest(BaseModel):
    leader_wallet: str
    allocation_usd: float = 10.0
    max_leverage: float = 5.0
    auto_copy: bool = False
    sl_pct: Optional[float] = None
    tp_pct: Optional[float] = None


class FollowUpdate(BaseModel):
    allocation_usd: Optional[float] = None
    max_leverage: Optional[float] = None
    auto_copy: Optional[bool] = None
    is_active: Optional[bool] = None
    sl_pct: Optional[float] = None
    tp_pct: Optional[float] = None


@router.post("/follow")
async def follow_trader(
    req: FollowRequest,
    user: User = Depends(get_authenticated_user),
) -> dict:
    leader_wallet = req.leader_wallet.lower()
    follower_wallet = user.wallet_address.lower()

    if leader_wallet == follower_wallet:
        raise HTTPException(status_code=400, detail="Cannot follow yourself")

    # Validate allocation_usd
    if req.allocation_usd < 1 or req.allocation_usd > 100000:
        raise HTTPException(status_code=400, detail="allocation_usd must be between 1 and 100000")

    # Validate max_leverage against global max (highest single-market max = 20x for SOL)
    if req.max_leverage < 1 or req.max_leverage > 20:
        raise HTTPException(status_code=400, detail="max_leverage must be between 1 and 20")

    sf = get_session_factory()
    async with sf() as session:
        # Check existing
        result = await session.execute(
            select(WalletFollow).where(
                WalletFollow.follower_wallet == follower_wallet,
                WalletFollow.leader_wallet == leader_wallet,
            )
        )
        existing = result.scalar_one_or_none()

        if existing:
            if existing.is_active:
                raise HTTPException(status_code=409, detail="Already following this trader")
            existing.is_active = True
            existing.allocation_usd = req.allocation_usd
            existing.max_leverage = req.max_leverage
            existing.auto_copy = req.auto_copy
            existing.sl_pct = req.sl_pct
            existing.tp_pct = req.tp_pct
            await session.commit()
        else:
            follow = WalletFollow(
                follower_wallet=follower_wallet,
                leader_wallet=leader_wallet,
                allocation_usd=req.allocation_usd,
                max_leverage=req.max_leverage,
                auto_copy=req.auto_copy,
                sl_pct=req.sl_pct,
                tp_pct=req.tp_pct,
                is_active=True,
            )
            session.add(follow)
            await session.commit()

    # auto_copy flag is already saved in the WalletFollow record above,
    # so autocopy.get_all_configs() will pick it up from DB automatically.

    logger.info("User %s following %s ($%.0f, %sx, auto=%s)", follower_wallet[:10], leader_wallet[:10], req.allocation_usd, req.max_leverage, req.auto_copy)

    return {
        "success": True,
        "leader_wallet": leader_wallet,
        "allocation_usd": req.allocation_usd,
        "max_leverage": req.max_leverage,
        "auto_copy": req.auto_copy,
    }


@router.delete("/follow/{leader_wallet}")
async def unfollow_trader(
    leader_wallet: str,
    user: User = Depends(get_authenticated_user),
) -> dict:
    leader_wallet = leader_wallet.lower()
    follower_wallet = user.wallet_address.lower()

    sf = get_session_factory()
    async with sf() as session:
        result = await session.execute(
            select(WalletFollow).where(
                WalletFollow.follower_wallet == follower_wallet,
                WalletFollow.leader_wallet == leader_wallet,
                WalletFollow.is_active == True,
            )
        )
        follow = result.scalar_one_or_none()
        if not follow:
            raise HTTPException(status_code=404, detail="Not following this trader")
        follow.is_active = False
        await session.commit()

    logger.info("User %s unfollowed %s", follower_wallet[:10], leader_wallet[:10])
    return {"success": True}


@router.patch("/follow/{leader_wallet}")
async def update_follow(
    leader_wallet: str,
    body: FollowUpdate,
    user: User = Depends(get_authenticated_user),
) -> dict:
    leader_wallet = leader_wallet.lower()
    follower_wallet = user.wallet_address.lower()

    # Validate fields if provided
    if body.allocation_usd is not None and (body.allocation_usd < 1 or body.allocation_usd > 100000):
        raise HTTPException(status_code=400, detail="allocation_usd must be between 1 and 100000")
    if body.max_leverage is not None and (body.max_leverage < 1 or body.max_leverage > 20):
        raise HTTPException(status_code=400, detail="max_leverage must be between 1 and 20")

    sf = get_session_factory()
    async with sf() as session:
        result = await session.execute(
            select(WalletFollow).where(
                WalletFollow.follower_wallet == follower_wallet,
                WalletFollow.leader_wallet == leader_wallet,
            )
        )
        follow = result.scalar_one_or_none()
        if not follow:
            raise HTTPException(status_code=404, detail="Follow not found")

        if body.allocation_usd is not None:
            follow.allocation_usd = body.allocation_usd
        if body.max_leverage is not None:
            follow.max_leverage = body.max_leverage
        if body.auto_copy is not None:
            follow.auto_copy = body.auto_copy
        if body.is_active is not None:
            follow.is_active = body.is_active
        if "sl_pct" in body.model_fields_set:
            follow.sl_pct = body.sl_pct
        if "tp_pct" in body.model_fields_set:
            follow.tp_pct = body.tp_pct
        await session.commit()

    return {
        "success": True,
        "leader_wallet": leader_wallet,
        "allocation_usd": follow.allocation_usd,
        "max_leverage": follow.max_leverage,
        "auto_copy": follow.auto_copy,
        "is_active": follow.is_active,
    }


@router.get("/my-follows")
async def list_my_follows(
    user: User = Depends(get_authenticated_user),
) -> list[dict]:
    follower_wallet = user.wallet_address.lower()

    sf = get_session_factory()
    async with sf() as session:
        result = await session.execute(
            select(WalletFollow).where(
                WalletFollow.follower_wallet == follower_wallet,
                WalletFollow.is_active == True,
            )
        )
        follows = result.scalars().all()

    return [
        {
            "id": f.id,
            "leader_wallet": f.leader_wallet,
            "allocation_usd": f.allocation_usd,
            "max_leverage": f.max_leverage,
            "auto_copy": f.auto_copy,
            "sl_pct": f.sl_pct,
            "tp_pct": f.tp_pct,
            "is_active": f.is_active,
            "created_at": f.created_at.isoformat() if f.created_at else None,
        }
        for f in follows
    ]


@router.get("/history")
async def copy_trade_history(
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
    user: User = Depends(get_authenticated_user),
) -> list[dict]:
    """Get copy trade execution history for the authenticated user."""
    follower_wallet = user.wallet_address.lower()

    sf = get_session_factory()
    async with sf() as session:
        result = await session.execute(
            select(CopyTradeLog)
            .where(CopyTradeLog.user_wallet == follower_wallet)
            .order_by(desc(CopyTradeLog.timestamp))
            .offset(skip)
            .limit(limit)
        )
        logs = result.scalars().all()

    return [
        {
            "id": log.id,
            "leader_wallet": log.leader_wallet,
            "market_id": log.market_id,
            "symbol": log.symbol,
            "side": log.side,
            "leverage": log.leverage,
            "amount_usd": log.amount_usd,
            "status": log.status,
            "error": log.error,
            "timestamp": log.timestamp.isoformat() if log.timestamp else None,
        }
        for log in logs
    ]


# --- Copy Position Tracking ---

class CreateCopyPositionRequest(BaseModel):
    leader_wallet: str
    market_id: int
    symbol: str
    side: str
    entry_price: float
    size: float
    leverage: float
    allocation_usd: float
    source: str = "manual"


@router.post("/positions")
async def create_copy_position_endpoint(
    req: CreateCopyPositionRequest,
    user: User = Depends(get_authenticated_user),
) -> dict:
    """Create a copy_position record after successful trade execution."""
    from app.services import copy_position_service
    return await copy_position_service.create_copy_position(
        follower_wallet=user.wallet_address,
        leader_wallet=req.leader_wallet,
        market_id=req.market_id,
        symbol=req.symbol,
        side=req.side,
        entry_price=req.entry_price,
        size=req.size,
        leverage=req.leverage,
        allocation_usd=req.allocation_usd,
        source=req.source,
    )


@router.get("/my-copies")
async def get_my_copies(
    status: str = Query("all"),
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
    user: User = Depends(get_authenticated_user),
) -> list[dict]:
    """Get all copy positions for the authenticated user."""
    from app.services import copy_position_service
    return await copy_position_service.get_all_copies(user.wallet_address, status, skip, limit)


@router.get("/stats")
async def get_copy_stats(
    user: User = Depends(get_authenticated_user),
) -> dict:
    """Aggregated copy trading stats: PnL per leader, win rate, totals."""
    from app.services import copy_position_service
    return await copy_position_service.get_copy_stats(user.wallet_address)


@router.get("/leaders/{leader_wallet}/followers")
async def get_leader_followers(
    leader_wallet: str,
    user: User = Depends(get_authenticated_user),
) -> dict:
    """Get follower count and list for a leader. Auth-gated — the follower list
    (wallets + allocations + leverage) must not be public."""
    from app.services import copy_position_service
    count = await copy_position_service.get_follower_count(leader_wallet)
    followers = await copy_position_service.get_followers_list(leader_wallet)
    return {"count": count, "followers": followers}


class CloseCopyPositionRequest(BaseModel):
    close_price: float


@router.patch("/positions/{position_id}/close")
async def close_copy_position_endpoint(
    position_id: int,
    req: CloseCopyPositionRequest,
    user: User = Depends(get_authenticated_user),
) -> dict:
    """Close a copy position and record realized PnL."""
    from app.services import copy_position_service
    result = await copy_position_service.close_copy_position(position_id, user.wallet_address, req.close_price)
    if not result:
        raise HTTPException(status_code=404, detail="Position not found or already closed")
    return result


@router.get("/followed-leaders/positions")
async def followed_leaders_positions(
    include_paused: bool = Query(False),
    user: User = Depends(get_authenticated_user),
) -> list[dict]:
    """Live on-chain positions of every leader the user follows.
    Powers the Following tab and Dashboard widget."""
    from app.services.chain_reader import get_trader_positions_only

    follower_wallet = user.wallet_address.lower()
    cache_key = f"{follower_wallet}:{int(include_paused)}"
    now = time.time()
    cached = _followed_positions_cache.get(cache_key)
    if cached and now - cached[0] < _FOLLOWED_POSITIONS_TTL:
        return cached[1]

    sf = get_session_factory()
    async with sf() as session:
        stmt = select(WalletFollow).where(WalletFollow.follower_wallet == follower_wallet)
        if not include_paused:
            stmt = stmt.where(WalletFollow.is_active == True)
        follows = (await session.execute(stmt)).scalars().all()

        # Look up display names from Leader table
        leader_wallets = [f.leader_wallet for f in follows]
        display_names: dict[str, str] = {}
        if leader_wallets:
            ld_stmt = select(Leader).where(Leader.wallet_address.in_(leader_wallets))
            for ld in (await session.execute(ld_stmt)).scalars().all():
                if ld.display_name:
                    display_names[ld.wallet_address.lower()] = ld.display_name

    if not follows:
        _followed_positions_cache[cache_key] = (now, [])
        return []

    loop = asyncio.get_event_loop()

    async def _read(leader_wallet: str) -> tuple[str, list[dict] | None, str | None]:
        try:
            detail = await loop.run_in_executor(None, get_trader_positions_only, leader_wallet)
            return leader_wallet, (detail or {}).get("positions", []), None
        except Exception as e:
            logger.warning("followed-leaders: chain read failed for %s: %s", leader_wallet[:10], e)
            return leader_wallet, None, str(e)

    results = await asyncio.gather(*[_read(f.leader_wallet) for f in follows])
    positions_by_wallet = {w: (positions, err) for w, positions, err in results}

    out: list[dict] = []
    for f in follows:
        positions, err = positions_by_wallet.get(f.leader_wallet, (None, "no data"))
        out.append({
            "leader_wallet": f.leader_wallet,
            "leader_display_name": display_names.get(f.leader_wallet.lower()),
            "allocation_usd": f.allocation_usd,
            "max_leverage": f.max_leverage,
            "auto_copy": f.auto_copy,
            "is_active": f.is_active,
            "positions": positions or [],
            "error": err,
        })

    # Sort: active first, then by leader having positions, then by leader name
    out.sort(key=lambda r: (not r["is_active"], not r["positions"], (r["leader_display_name"] or r["leader_wallet"]).lower()))

    _followed_positions_cache[cache_key] = (now, out)
    return out
