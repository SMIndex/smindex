"""Copy subscription service for copy v1.

A subscription is the configured COPY relationship + risk settings. In v1 every
subscription is forced to mode='paper' and live_enabled=False — NO live orders.
Uses the copy_subscriptions table only (never wallet_follows / pending_copies).
"""
from datetime import datetime

from sqlalchemy import select, desc

from app.db.database import get_session_factory
from app.db.copy_models import CopySubscription
from app.utils.logger import get_logger

logger = get_logger(__name__)


class SubscriptionExists(Exception):
    """Raised when a subscription already exists for (follower, trader)."""


def _f(x):
    return float(x) if x is not None else None


def _dict(s: CopySubscription) -> dict:
    return {
        "id": s.id,
        "follower_wallet": s.follower_wallet,
        "trader_wallet": s.trader_wallet,
        "exchange": getattr(s, "exchange", "perpl"),
        "copy_type": s.mode,          # API contract uses copy_type; stored as `mode`
        "mode": s.mode,
        "status": s.status,
        "sizing_mode": s.sizing_mode,
        "allocation_usd": _f(s.allocation_usd),
        "max_leverage": _f(s.max_leverage),
        "max_margin_per_trade": _f(s.max_margin_per_trade),
        "max_daily_loss": _f(s.max_daily_loss),
        "max_total_loss": _f(s.max_total_loss),
        "slippage_bps": s.slippage_bps,
        "allowed_markets": s.allowed_markets,
        "copy_new_only": bool(s.copy_new_only),
        "sl_pct": _f(s.sl_pct),
        "max_basis_bps": s.max_basis_bps,
        "tp_pct": _f(s.tp_pct),
        "live_enabled": bool(s.live_enabled),
        "created_at": s.created_at.isoformat() if s.created_at else None,
        "updated_at": s.updated_at.isoformat() if s.updated_at else None,
    }


async def create_subscription(
    follower_wallet: str,
    trader_wallet: str,
    *,
    exchange: str = "perpl",
    mode: str = "live_manual",
    sizing_mode: str = "fixed",
    allocation_usd: float = 10.0,
    max_leverage: float = 5.0,
    max_margin_per_trade: float | None = None,
    max_daily_loss: float | None = None,
    max_total_loss: float | None = None,
    slippage_bps: int | None = None,
    allowed_markets: list[int] | None = None,
    copy_new_only: bool = True,
    sl_pct: float | None = None,
    tp_pct: float | None = None,
    max_basis_bps: int | None = None,
) -> dict:
    follower = follower_wallet.lower()
    trader = trader_wallet.lower()
    sf = get_session_factory()
    async with sf() as session:
        existing = (await session.execute(
            select(CopySubscription).where(
                CopySubscription.follower_wallet == follower,
                CopySubscription.trader_wallet == trader,
            )
        )).scalar_one_or_none()
        if existing:
            raise SubscriptionExists()

        sub = CopySubscription(
            follower_wallet=follower,
            trader_wallet=trader,
            exchange=exchange,
            mode=mode,             # paper | live_manual (live_auto refused upstream)
            status="active",
            sizing_mode=sizing_mode,
            allocation_usd=allocation_usd,
            max_leverage=max_leverage,
            max_margin_per_trade=max_margin_per_trade,
            max_daily_loss=max_daily_loss,
            max_total_loss=max_total_loss,
            slippage_bps=slippage_bps,
            allowed_markets=allowed_markets,
            copy_new_only=copy_new_only,
            sl_pct=sl_pct,
            tp_pct=tp_pct,
            max_basis_bps=max_basis_bps,
            live_enabled=False,    # FORCED off in v1
            created_at=datetime.utcnow(),
            updated_at=datetime.utcnow(),
        )
        session.add(sub)
        await session.commit()
        await session.refresh(sub)
        return _dict(sub)


async def list_subscriptions(follower_wallet: str) -> list[dict]:
    follower = follower_wallet.lower()
    sf = get_session_factory()
    async with sf() as session:
        rows = (await session.execute(
            select(CopySubscription)
            .where(CopySubscription.follower_wallet == follower)
            .order_by(desc(CopySubscription.created_at))
        )).scalars().all()
    return [_dict(s) for s in rows]


async def get_subscription(sub_id: int, follower_wallet: str) -> dict | None:
    sf = get_session_factory()
    async with sf() as session:
        s = await _load(session, sub_id, follower_wallet)
        return _dict(s) if s else None


async def update_subscription(sub_id: int, follower_wallet: str, fields: dict) -> dict | None:
    """Update mutable fields. `mode`, `live_enabled`, wallets are NOT mutable here."""
    allowed = {
        "sizing_mode", "allocation_usd", "max_leverage", "max_margin_per_trade",
        "max_daily_loss", "max_total_loss", "slippage_bps", "allowed_markets",
        "copy_new_only", "sl_pct", "tp_pct", "max_basis_bps",
    }
    sf = get_session_factory()
    async with sf() as session:
        s = await _load(session, sub_id, follower_wallet)
        if not s:
            return None
        for k, v in fields.items():
            if k in allowed:
                setattr(s, k, v)
        s.updated_at = datetime.utcnow()
        await session.commit()
        await session.refresh(s)
        return _dict(s)


async def set_status(sub_id: int, follower_wallet: str, status: str) -> dict | None:
    """status in {'active','paused','stopped'} (resume->active, pause->paused, stop->stopped)."""
    sf = get_session_factory()
    async with sf() as session:
        s = await _load(session, sub_id, follower_wallet)
        if not s:
            return None
        s.status = status
        s.updated_at = datetime.utcnow()
        await session.commit()
        await session.refresh(s)
        return _dict(s)


async def _load(session, sub_id: int, follower_wallet: str) -> CopySubscription | None:
    return (await session.execute(
        select(CopySubscription).where(
            CopySubscription.id == sub_id,
            CopySubscription.follower_wallet == follower_wallet.lower(),
        )
    )).scalar_one_or_none()
