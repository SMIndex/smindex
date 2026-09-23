from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select

from app.db.database import get_session_factory
from app.db.models import User, PriceAlertDB
from app.routers.auth import get_authenticated_user
from app.utils.logger import get_logger

logger = get_logger(__name__)

router = APIRouter(prefix="/price-alerts", tags=["price_alerts"])


class CreateAlertRequest(BaseModel):
    market_id: int
    symbol: str
    condition: str  # above / below
    target_price: float


@router.post("")
async def create_alert(
    req: CreateAlertRequest,
    user: User = Depends(get_authenticated_user),
) -> dict:
    if req.condition not in ("above", "below"):
        raise HTTPException(status_code=400, detail="condition must be 'above' or 'below'")
    if req.target_price <= 0:
        raise HTTPException(status_code=400, detail="target_price must be > 0")

    sf = get_session_factory()
    async with sf() as session:
        alert = PriceAlertDB(
            user_id=user.id,
            wallet_address=user.wallet_address,
            market_id=req.market_id,
            symbol=req.symbol,
            condition=req.condition,
            target_price=req.target_price,
            status="active",
        )
        session.add(alert)
        await session.commit()
        await session.refresh(alert)
        return {
            "id": alert.id,
            "market_id": alert.market_id,
            "symbol": alert.symbol,
            "condition": alert.condition,
            "target_price": alert.target_price,
            "status": alert.status,
            "created_at": alert.created_at.isoformat() if alert.created_at else None,
        }


@router.get("")
async def list_alerts(
    user: User = Depends(get_authenticated_user),
) -> list[dict]:
    sf = get_session_factory()
    async with sf() as session:
        result = await session.execute(
            select(PriceAlertDB)
            .where(PriceAlertDB.user_id == user.id)
            .order_by(PriceAlertDB.created_at.desc())
        )
        alerts = result.scalars().all()
        return [
            {
                "id": a.id,
                "market_id": a.market_id,
                "symbol": a.symbol,
                "condition": a.condition,
                "target_price": a.target_price,
                "status": a.status,
                "created_at": a.created_at.isoformat() if a.created_at else None,
                "triggered_at": a.triggered_at.isoformat() if a.triggered_at else None,
            }
            for a in alerts
        ]


@router.delete("/{alert_id}")
async def delete_alert(
    alert_id: int,
    user: User = Depends(get_authenticated_user),
) -> dict:
    sf = get_session_factory()
    async with sf() as session:
        result = await session.execute(
            select(PriceAlertDB).where(
                PriceAlertDB.id == alert_id,
                PriceAlertDB.user_id == user.id,
            )
        )
        alert = result.scalar_one_or_none()
        if not alert:
            raise HTTPException(status_code=404, detail="Alert not found")
        await session.delete(alert)
        await session.commit()
        return {"ok": True}
