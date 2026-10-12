from typing import Optional

from fastapi import APIRouter, Query
from sqlalchemy import select, desc

from app.db.database import get_session_factory
from app.db.models import WhaleAlert
from app.utils.logger import get_logger

logger = get_logger(__name__)

router = APIRouter(prefix="/whales", tags=["whales"])


def _alert_to_dict(alert: WhaleAlert) -> dict:
    return {
        "id": alert.id,
        "market_id": alert.market_id,
        "alert_type": alert.alert_type,
        "side": alert.side,
        "size_usd": alert.size_usd,
        "price": alert.price,
        "severity": alert.severity,
        "details": alert.details,
        "timestamp": alert.timestamp.isoformat() if alert.timestamp else None,
    }


@router.get("/alerts")
async def get_whale_alerts(
    market_id: Optional[int] = Query(None),
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
) -> list[dict]:
    async_session = get_session_factory()
    async with async_session() as session:
        stmt = select(WhaleAlert)

        if market_id is not None:
            stmt = stmt.where(WhaleAlert.market_id == market_id)

        stmt = stmt.order_by(desc(WhaleAlert.timestamp)).offset(skip).limit(limit)

        result = await session.execute(stmt)
        alerts = result.scalars().all()

    return [_alert_to_dict(a) for a in alerts]


@router.get("/oi-divergence/{market_id}")
async def get_oi_divergence(
    market_id: int,
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
) -> list[dict]:
    async_session = get_session_factory()
    async with async_session() as session:
        stmt = (
            select(WhaleAlert)
            .where(
                WhaleAlert.market_id == market_id,
                WhaleAlert.alert_type == "oi_divergence",
            )
            .order_by(desc(WhaleAlert.timestamp))
            .offset(skip)
            .limit(limit)
        )
        result = await session.execute(stmt)
        alerts = result.scalars().all()

    return [_alert_to_dict(a) for a in alerts]
