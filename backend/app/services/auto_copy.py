# =============================================================================
# LEGACY / DEAD-END (copy v0). The pending_copies queue is written but NEVER
# executed (the only consumer is GET /autocopy/pending, which no client calls).
# v1 has NO live auto-copy. Superseded by the copy v1 paper engine.
# DO NOT use for new work. Scheduled for removal in a later phase.
# =============================================================================
"""Auto copy-trade service.

When trader_tracker detects a new position from a tracked leader,
queues a copy intent to DB. Executes when the follower has an active trading WS.

Queued approach — the follower's trade executes at CURRENT market price
when they connect, not at the leader's entry price.
"""
import asyncio
from datetime import datetime

from sqlalchemy import select, delete
from app.db.database import get_session_factory
from app.db.models import PendingCopy
from app.utils.logger import get_logger

logger = get_logger(__name__)


async def queue_copy_async(follower_wallet: str, leader_wallet: str, market_id: int, symbol: str, side: str, allocation_usd: float, max_leverage: float):
    """Queue a copy intent for a follower (persisted to DB)."""
    sf = get_session_factory()
    async with sf() as session:
        # Check for existing pending copy (same follower + leader + market)
        result = await session.execute(
            select(PendingCopy).where(
                PendingCopy.follower_wallet == follower_wallet.lower(),
                PendingCopy.leader_wallet == leader_wallet.lower(),
                PendingCopy.market_id == market_id,
            )
        )
        if result.scalar_one_or_none():
            return  # Already queued

        pending = PendingCopy(
            follower_wallet=follower_wallet.lower(),
            leader_wallet=leader_wallet.lower(),
            market_id=market_id,
            symbol=symbol,
            side=side,
            allocation_usd=allocation_usd,
            max_leverage=max_leverage,
            queued_at=datetime.utcnow(),
        )
        session.add(pending)
        await session.commit()
    logger.info("Auto-copy queued: follower=%s leader=%s %s %s", follower_wallet[:10], leader_wallet[:10], side, symbol)


def queue_copy(follower_id: int, leader_wallet: str, market_id: int, symbol: str, side: str, allocation_usd: float, max_leverage: float):
    """Sync wrapper — schedules the async DB write on the running event loop."""
    # follower_id=0 means we use wallet from the config, not user ID
    # This is called from _trigger_auto_copies which has follower_wallet in the config
    pass  # Kept for backward compat; use queue_copy_async instead


async def get_pending_async(follower_wallet: str) -> list[dict]:
    """Get and clear pending copies for a follower from DB."""
    sf = get_session_factory()
    async with sf() as session:
        result = await session.execute(
            select(PendingCopy).where(PendingCopy.follower_wallet == follower_wallet.lower())
        )
        rows = result.scalars().all()

        copies = [
            {
                "leader_wallet": r.leader_wallet,
                "market_id": r.market_id,
                "symbol": r.symbol,
                "side": r.side,
                "allocation_usd": r.allocation_usd,
                "max_leverage": r.max_leverage,
                "queued_at": r.queued_at.isoformat() if r.queued_at else None,
            }
            for r in rows
        ]

        # Clear after reading
        if rows:
            await session.execute(
                delete(PendingCopy).where(PendingCopy.follower_wallet == follower_wallet.lower())
            )
            await session.commit()

    return copies


def get_pending(follower_id: int) -> list[dict]:
    """Legacy sync wrapper — returns empty. Use get_pending_async instead."""
    return []


def get_all_pending() -> dict[int, list[dict]]:
    """Legacy sync wrapper — returns empty. Use DB queries instead."""
    return {}
