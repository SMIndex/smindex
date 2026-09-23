import secrets
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select

from app.config import settings
from app.db.database import get_session_factory
from app.db.models import User, TelegramLink
from app.routers.auth import get_authenticated_user
from app.utils.logger import get_logger

logger = get_logger(__name__)

router = APIRouter(prefix="/telegram", tags=["telegram"])

BOT_USERNAME = ""  # Will be set on first call if bot is running


@router.post("/generate-link-code")
async def generate_link_code(
    user: User = Depends(get_authenticated_user),
) -> dict:
    """Generate a unique code for linking Telegram account."""
    code = secrets.token_urlsafe(16)
    expires = datetime.utcnow() + timedelta(minutes=10)

    sf = get_session_factory()
    async with sf() as session:
        result = await session.execute(
            select(TelegramLink).where(TelegramLink.user_id == user.id)
        )
        link = result.scalar_one_or_none()

        if link and link.is_active and link.chat_id:
            raise HTTPException(status_code=409, detail="Telegram already linked. Unlink first.")

        if link:
            link.link_code = code
            link.link_code_expires = expires
            link.is_active = False
            link.chat_id = None
        else:
            link = TelegramLink(
                user_id=user.id,
                link_code=code,
                link_code_expires=expires,
                is_active=False,
            )
            session.add(link)
        await session.commit()

    # Build bot URL
    bot_url = f"https://t.me/PerplCopyBot?start={code}"
    if settings.TELEGRAM_BOT_TOKEN:
        # Try to get actual bot username
        try:
            from app.services.telegram_bot import _app
            if _app:
                bot_info = await _app.bot.get_me()
                bot_url = f"https://t.me/{bot_info.username}?start={code}"
        except Exception:
            pass

    return {
        "code": code,
        "bot_url": bot_url,
        "expires_in_seconds": 600,
    }


@router.get("/status")
async def get_telegram_status(
    user: User = Depends(get_authenticated_user),
) -> dict:
    """Check if Telegram is linked for this user."""
    sf = get_session_factory()
    async with sf() as session:
        result = await session.execute(
            select(TelegramLink).where(TelegramLink.user_id == user.id)
        )
        link = result.scalar_one_or_none()

    if not link or not link.is_active or not link.chat_id:
        return {"linked": False}

    # Mask chat_id for privacy
    chat_id = link.chat_id
    masked = "***" + chat_id[-3:] if len(chat_id) > 3 else "***"

    return {
        "linked": True,
        "chat_id_masked": masked,
        "linked_at": link.linked_at.isoformat() if link.linked_at else None,
    }


@router.delete("/unlink")
async def unlink_telegram(
    user: User = Depends(get_authenticated_user),
) -> dict:
    """Unlink Telegram from account."""
    sf = get_session_factory()
    async with sf() as session:
        result = await session.execute(
            select(TelegramLink).where(TelegramLink.user_id == user.id)
        )
        link = result.scalar_one_or_none()
        if link:
            link.is_active = False
            link.chat_id = None
            await session.commit()

    return {"success": True}


@router.get("/queue-stats")
async def telegram_queue_stats(
    user: User = Depends(get_authenticated_user),
) -> dict:
    """Observability for the alert send queue: depth + sent/failed counters.
    Auth-gated (any logged-in user; there is no role system in this app)."""
    from app.services import telegram_queue
    return telegram_queue.get_stats()
