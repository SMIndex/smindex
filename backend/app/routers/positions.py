from fastapi import APIRouter, Depends, HTTPException

from app.db.models import User
from app.routers.auth import get_authenticated_user
from app.services import active_positions
from app.utils.logger import get_logger

logger = get_logger(__name__)

router = APIRouter(prefix="/positions", tags=["positions"])


@router.get("/my")
async def get_my_positions(
    user: User = Depends(get_authenticated_user),
) -> list[dict]:
    """Authenticated user's open Perpl positions, read on-chain.

    Historical note: this used to call `perpl_client.get_positions()`, a method
    that never existed — the AttributeError was swallowed and every call
    returned 502. It now uses the same on-chain path the trader-profile modal
    uses (active_positions: registry markets + getAccountByAddr/getPosition,
    45s cache).
    """
    summary = await active_positions.get_summary(user.wallet_address)
    if summary.get("active_positions_error"):
        raise HTTPException(
            status_code=502,
            detail="On-chain position read failed. Try again shortly.",
        )
    return summary.get("active_markets", [])
