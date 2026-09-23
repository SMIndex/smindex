"""Admin moderation routes (phase 7): hide/unhide trader profiles.

Security model: `require_admin` = valid app JWT AND wallet ∈ ADMIN_ADDRESSES
(config, env-driven). There is deliberately NO client-side-only gating — the
frontend affordance is cosmetic; these routes are the enforcement.
"""
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select

from app.config import settings
from app.db.database import get_session_factory
from app.db.copy_models import TraderProfile
from app.db.models import User
from app.routers.auth import get_authenticated_user
from app.services.copy import audit
from app.utils.logger import get_logger

logger = get_logger(__name__)

router = APIRouter(prefix="/admin", tags=["admin"])

VALID_EXCHANGES = {"perpl", "hl"}


async def require_admin(user: User = Depends(get_authenticated_user)) -> User:
    if user.wallet_address.lower() not in settings.admin_address_set:
        raise HTTPException(status_code=403, detail="Admin access required")
    return user


@router.get("/me")
async def admin_me(user: User = Depends(get_authenticated_user)) -> dict:
    """Cosmetic helper for the frontend: is the connected wallet an admin?
    (Server-side enforcement lives on the mutating routes, not here.)"""
    return {"is_admin": user.wallet_address.lower() in settings.admin_address_set}


async def _set_hidden(exchange: str, wallet: str, hidden: bool, actor: User) -> dict:
    if exchange not in VALID_EXCHANGES:
        raise HTTPException(status_code=400, detail=f"Unknown exchange: {exchange}")
    wallet = wallet.lower()
    sf = get_session_factory()
    async with sf() as session:
        p = (await session.execute(
            select(TraderProfile).where(
                TraderProfile.exchange == exchange,
                TraderProfile.wallet_address == wallet,
            )
        )).scalar_one_or_none()
        if not p:
            raise HTTPException(status_code=404, detail="Trader profile not found")
        p.is_hidden = hidden
        p.hidden_at = datetime.utcnow() if hidden else None
        await session.commit()
    # hide/unhide must take effect immediately despite the read caches
    from app.services.copy.trader_profiles import invalidate_hidden_cache
    from app.routers.leaders import drop_hl_lb_cache
    invalidate_hidden_cache()
    drop_hl_lb_cache()
    await audit.write_audit(
        actor.wallet_address, "trader_hidden" if hidden else "trader_unhidden",
        entity_type="trader_profile",
        detail={"exchange": exchange, "wallet": wallet},
    )
    logger.info("admin %s %s %s/%s", actor.wallet_address[:10],
                "hid" if hidden else "unhid", exchange, wallet[:10])
    return {"exchange": exchange, "wallet": wallet, "is_hidden": hidden}


@router.patch("/traders/{exchange}/{wallet}/hide")
async def hide_trader(exchange: str, wallet: str,
                      user: User = Depends(require_admin)) -> dict:
    return await _set_hidden(exchange, wallet, True, user)


@router.patch("/traders/{exchange}/{wallet}/unhide")
async def unhide_trader(exchange: str, wallet: str,
                        user: User = Depends(require_admin)) -> dict:
    return await _set_hidden(exchange, wallet, False, user)


@router.get("/traders/hidden")
async def list_hidden(user: User = Depends(require_admin)) -> list[dict]:
    sf = get_session_factory()
    async with sf() as session:
        rows = (await session.execute(
            select(TraderProfile).where(TraderProfile.is_hidden == True)  # noqa: E712
            .order_by(TraderProfile.hidden_at.desc())
        )).scalars().all()
    return [{
        "exchange": getattr(p, "exchange", "perpl"),
        "wallet": p.wallet_address,
        "display_name": p.display_name,
        "hidden_at": p.hidden_at.isoformat() if p.hidden_at else None,
    } for p in rows]
