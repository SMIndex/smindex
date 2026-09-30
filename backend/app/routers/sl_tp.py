from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from app.db.models import User
from app.routers.auth import get_authenticated_user
from app.services import sl_tp_service
from app.utils.logger import get_logger

logger = get_logger(__name__)

router = APIRouter(prefix="/sl-tp", tags=["sl_tp"])

def _is_valid_market(market_id: int) -> bool:
    # Validate against the live chain_reader map (registry-synced) instead of a
    # hardcoded set that rejects HYPE/ZEC/SOL-31.
    from app.services.chain_reader import MARKETS
    return market_id in MARKETS


class CreateStopOrderRequest(BaseModel):
    market_id: int
    side: str  # long or short
    order_type: str  # sl or tp
    trigger_price: float
    size: Optional[float] = None  # null = close full position
    source: str = "manual"


@router.post("/orders")
async def create_stop_order(
    req: CreateStopOrderRequest,
    user: User = Depends(get_authenticated_user),
) -> dict:
    if not _is_valid_market(req.market_id):
        raise HTTPException(status_code=400, detail="Invalid or inactive market_id")
    if req.side not in ("long", "short"):
        raise HTTPException(status_code=400, detail="side must be 'long' or 'short'")
    if req.order_type not in ("sl", "tp"):
        raise HTTPException(status_code=400, detail="order_type must be 'sl' or 'tp'")
    if req.trigger_price <= 0:
        raise HTTPException(status_code=400, detail="trigger_price must be > 0")

    result = await sl_tp_service.add_order(
        user_id=user.id,
        wallet_address=user.wallet_address,
        market_id=req.market_id,
        side=req.side,
        order_type=req.order_type,
        trigger_price=req.trigger_price,
        size=req.size,
        source=req.source,
    )
    return result


@router.get("/orders")
async def list_stop_orders(
    status: str = Query("active"),
    user: User = Depends(get_authenticated_user),
) -> list[dict]:
    return await sl_tp_service.get_user_orders(user.id, status=status)


@router.delete("/orders/{order_id}")
async def cancel_stop_order(
    order_id: int,
    user: User = Depends(get_authenticated_user),
) -> dict:
    success = await sl_tp_service.cancel_order(order_id, user.id)
    if not success:
        raise HTTPException(status_code=404, detail="Order not found or already cancelled")
    return {"success": True}
