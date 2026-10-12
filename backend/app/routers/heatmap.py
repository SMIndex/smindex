from fastapi import APIRouter, HTTPException
from sqlalchemy import select, desc

from app.db.database import get_session_factory
from app.db.models import LiquidationSnapshot
from app.utils.logger import get_logger

logger = get_logger(__name__)

router = APIRouter(prefix="/heatmap", tags=["heatmap"])


@router.get("/{market_id}")
async def get_heatmap(market_id: int) -> dict:
    async_session = get_session_factory()
    async with async_session() as session:
        stmt = (
            select(LiquidationSnapshot)
            .where(LiquidationSnapshot.market_id == market_id)
            .order_by(desc(LiquidationSnapshot.timestamp))
            .limit(1)
        )
        result = await session.execute(stmt)
        snapshot = result.scalar_one_or_none()

    if not snapshot:
        raise HTTPException(
            status_code=404,
            detail=f"No heatmap data available for market {market_id}",
        )

    return {
        "id": snapshot.id,
        "market_id": snapshot.market_id,
        "current_price": snapshot.current_price,
        "bins": snapshot.bins,
        "timestamp": snapshot.timestamp.isoformat() if snapshot.timestamp else None,
    }
