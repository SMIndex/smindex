"""Watchlist routes (copy v1). WATCH is separate from COPY.

All routes are auth-gated and scoped to the authenticated user's wallet.
No money, no orders.
"""
from fastapi import APIRouter, Depends, HTTPException, Query

from app.db.models import User
from app.routers.auth import get_authenticated_user
from app.services.copy import watchlist as watchlist_service
from app.services.copy import audit
from app.utils.logger import get_logger

logger = get_logger(__name__)

router = APIRouter(prefix="/watchlist", tags=["watchlist-v1"])


@router.get("")
async def get_watchlist(user: User = Depends(get_authenticated_user)) -> list[dict]:
    return await watchlist_service.list_watchlist(user.wallet_address)


def _exch(exchange: str) -> str:
    e = (exchange or "perpl").lower()
    if e not in ("perpl", "hl"):
        raise HTTPException(status_code=400, detail=f"Unknown exchange: {exchange}")
    return e


@router.post("/{trader_wallet}")
async def add_to_watchlist(
    trader_wallet: str,
    exchange: str = Query("perpl", description="perpl | hl"),
    user: User = Depends(get_authenticated_user),
) -> dict:
    exch = _exch(exchange)
    try:
        row = await watchlist_service.add_watch(user.wallet_address, trader_wallet, exchange=exch)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    await audit.write_audit(
        user.wallet_address, "watch", entity_type="watchlist",
        entity_id=row.get("id"), detail={"trader_wallet": trader_wallet.lower(), "exchange": exch},
    )
    return row


@router.delete("/{trader_wallet}")
async def remove_from_watchlist(
    trader_wallet: str,
    exchange: str = Query("perpl", description="perpl | hl"),
    user: User = Depends(get_authenticated_user),
) -> dict:
    exch = _exch(exchange)
    removed = await watchlist_service.remove_watch(user.wallet_address, trader_wallet, exchange=exch)
    if not removed:
        raise HTTPException(status_code=404, detail="Not on watchlist")
    await audit.write_audit(
        user.wallet_address, "unwatch", entity_type="watchlist",
        detail={"trader_wallet": trader_wallet.lower(), "exchange": exch},
    )
    return {"success": True}
