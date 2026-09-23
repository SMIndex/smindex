"""Audit + risk-event writers/readers for copy v1.

Every create/update/pause/resume/stop copy action writes an audit_logs row.
Risk-gate decisions (used by the future paper engine) write risk_events rows.
Append-only; never blocks the main action (best-effort).
"""
from datetime import datetime

from sqlalchemy import select, desc

from app.db.database import get_session_factory
from app.db.copy_models import AuditLog, RiskEvent
from app.utils.logger import get_logger

logger = get_logger(__name__)


async def write_audit(
    actor_wallet: str | None,
    action: str,
    entity_type: str | None = None,
    entity_id: int | None = None,
    detail: dict | None = None,
) -> None:
    """Append an audit_logs row. Best-effort — never raises into the caller."""
    try:
        sf = get_session_factory()
        async with sf() as session:
            session.add(AuditLog(
                actor_wallet=(actor_wallet or "").lower() or None,
                action=action,
                entity_type=entity_type,
                entity_id=entity_id,
                detail=detail,
                created_at=datetime.utcnow(),
            ))
            await session.commit()
    except Exception:
        logger.exception("write_audit failed (action=%s)", action)


async def write_risk_event(
    follower_wallet: str,
    event_type: str,
    subscription_id: int | None = None,
    detail: dict | None = None,
) -> None:
    """Append a risk_events row. Best-effort."""
    try:
        sf = get_session_factory()
        async with sf() as session:
            session.add(RiskEvent(
                subscription_id=subscription_id,
                follower_wallet=follower_wallet.lower(),
                event_type=event_type,
                detail=detail,
                created_at=datetime.utcnow(),
            ))
            await session.commit()
    except Exception:
        logger.exception("write_risk_event failed (type=%s)", event_type)


async def list_audit_logs(actor_wallet: str, skip: int = 0, limit: int = 50) -> list[dict]:
    sf = get_session_factory()
    async with sf() as session:
        rows = (await session.execute(
            select(AuditLog)
            .where(AuditLog.actor_wallet == actor_wallet.lower())
            .order_by(desc(AuditLog.created_at))
            .offset(skip).limit(limit)
        )).scalars().all()
    return [
        {
            "id": r.id,
            "actor_wallet": r.actor_wallet,
            "action": r.action,
            "entity_type": r.entity_type,
            "entity_id": r.entity_id,
            "detail": r.detail,
            "created_at": r.created_at.isoformat() if r.created_at else None,
        }
        for r in rows
    ]


async def list_risk_events(follower_wallet: str, skip: int = 0, limit: int = 50) -> list[dict]:
    sf = get_session_factory()
    async with sf() as session:
        rows = (await session.execute(
            select(RiskEvent)
            .where(RiskEvent.follower_wallet == follower_wallet.lower())
            .order_by(desc(RiskEvent.created_at))
            .offset(skip).limit(limit)
        )).scalars().all()
    return [
        {
            "id": r.id,
            "subscription_id": r.subscription_id,
            "follower_wallet": r.follower_wallet,
            "event_type": r.event_type,
            "detail": r.detail,
            "created_at": r.created_at.isoformat() if r.created_at else None,
        }
        for r in rows
    ]
