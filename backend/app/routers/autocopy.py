# =============================================================================
# DEPRECATED / DEAD-END (copy v0). The /autocopy/* routes configure live
# auto-copy via the pending_copies queue, which is NEVER executed. v1 removes
# live auto-copy entirely (paper-only). UNCHANGED in Phase A; scheduled for
# removal once the v1 copy router replaces it. Do not build new work on this.
# =============================================================================
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.db.database import get_session_factory
from app.db.models import User, WalletFollow
from app.routers.auth import get_authenticated_user
from app.services.auto_copy import get_pending_async
from app.utils.logger import get_logger

logger = get_logger(__name__)

router = APIRouter(prefix="/autocopy", tags=["autocopy"])


class AutoCopyConfigRequest(BaseModel):
    leader_wallet: str
    allocation_usd: float = Field(10.0, ge=1, le=100000)
    max_leverage: float = Field(5.0, ge=1, le=20)


@router.post("/configure")
async def configure_autocopy(
    req: AutoCopyConfigRequest,
    user: User = Depends(get_authenticated_user),
) -> dict:
    """Set up auto-copy for a leader wallet (persisted via wallet_follows)."""
    leader_wallet = req.leader_wallet.lower()
    follower_wallet = user.wallet_address.lower()

    sf = get_session_factory()
    async with sf() as session:
        result = await session.execute(
            select(WalletFollow).where(
                WalletFollow.follower_wallet == follower_wallet,
                WalletFollow.leader_wallet == leader_wallet,
            )
        )
        follow = result.scalar_one_or_none()

        if follow:
            follow.auto_copy = True
            follow.allocation_usd = req.allocation_usd
            follow.max_leverage = req.max_leverage
            follow.is_active = True
        else:
            follow = WalletFollow(
                follower_wallet=follower_wallet,
                leader_wallet=leader_wallet,
                allocation_usd=req.allocation_usd,
                max_leverage=req.max_leverage,
                auto_copy=True,
                is_active=True,
            )
            session.add(follow)
        await session.commit()

    logger.info("Auto-copy configured: user=%d leader=%s alloc=$%.2f", user.id, req.leader_wallet[:10], req.allocation_usd)
    return {"success": True, "leader_wallet": leader_wallet, "allocation_usd": req.allocation_usd, "max_leverage": req.max_leverage}


@router.get("/configs")
async def get_autocopy_configs(
    user: User = Depends(get_authenticated_user),
) -> list[dict]:
    """Get user's auto-copy configurations from DB."""
    follower_wallet = user.wallet_address.lower()

    sf = get_session_factory()
    async with sf() as session:
        result = await session.execute(
            select(WalletFollow).where(
                WalletFollow.follower_wallet == follower_wallet,
                WalletFollow.auto_copy == True,
                WalletFollow.is_active == True,
            )
        )
        follows = result.scalars().all()

    return [
        {
            "leader_wallet": f.leader_wallet,
            "allocation_usd": f.allocation_usd,
            "max_leverage": f.max_leverage,
            "active": f.is_active,
        }
        for f in follows
    ]


@router.delete("/configure/{leader_wallet}")
async def remove_autocopy(
    leader_wallet: str,
    user: User = Depends(get_authenticated_user),
) -> dict:
    """Disable auto-copy for a leader (keeps follow active)."""
    leader_wallet = leader_wallet.lower()
    follower_wallet = user.wallet_address.lower()

    sf = get_session_factory()
    async with sf() as session:
        result = await session.execute(
            select(WalletFollow).where(
                WalletFollow.follower_wallet == follower_wallet,
                WalletFollow.leader_wallet == leader_wallet,
            )
        )
        follow = result.scalar_one_or_none()
        if follow:
            follow.auto_copy = False
            await session.commit()

    return {"success": True}


@router.get("/pending")
async def get_pending_copies(
    user: User = Depends(get_authenticated_user),
) -> list[dict]:
    """Get and clear pending auto-copy trades for the user."""
    return await get_pending_async(user.wallet_address)


async def get_all_configs() -> dict[str, list[dict]]:
    """Get all active auto-copy configs from DB. Used by trader_tracker.
    Returns dict keyed by leader_wallet -> list of follower configs."""
    sf = get_session_factory()
    async with sf() as session:
        result = await session.execute(
            select(WalletFollow).where(
                WalletFollow.auto_copy == True,
                WalletFollow.is_active == True,
            )
        )
        follows = result.scalars().all()

    configs: dict[str, list[dict]] = {}
    for f in follows:
        if f.leader_wallet not in configs:
            configs[f.leader_wallet] = []
        configs[f.leader_wallet].append({
            "follower_wallet": f.follower_wallet,
            "allocation_usd": f.allocation_usd,
            "max_leverage": f.max_leverage,
            "sl_pct": f.sl_pct,
            "tp_pct": f.tp_pct,
        })
    return configs
