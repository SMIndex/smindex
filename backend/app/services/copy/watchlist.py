"""Watchlist service for copy v1.

WATCH is read-only interest in a trader — completely separate from copy
subscriptions. No money, no orders. Uses the watchlists table only.
"""
from datetime import datetime

from sqlalchemy import select, delete

from app.db.database import get_session_factory
from app.db.copy_models import Watchlist, TraderProfile
from app.services.copy import trader_profiles
from app.utils.logger import get_logger

logger = get_logger(__name__)


def _dict(w: Watchlist, display_name: str | None = None) -> dict:
    return {
        "id": w.id,
        "trader_wallet": w.trader_wallet,
        "exchange": getattr(w, "exchange", "perpl"),
        "display_name": display_name,
        "created_at": w.created_at.isoformat() if w.created_at else None,
    }


async def list_watchlist(follower_wallet: str) -> list[dict]:
    follower = follower_wallet.lower()
    sf = get_session_factory()
    async with sf() as session:
        rows = (await session.execute(
            select(Watchlist).where(Watchlist.follower_wallet == follower)
            .order_by(Watchlist.created_at.desc())
        )).scalars().all()
        # enrich with display names — keyed (exchange, wallet): the same wallet
        # can exist on both exchanges with different display names.
        names: dict[tuple[str, str], str] = {}
        wallets = [w.trader_wallet for w in rows]
        if wallets:
            profs = (await session.execute(
                select(TraderProfile).where(TraderProfile.wallet_address.in_(wallets))
            )).scalars().all()
            names = {(getattr(p, "exchange", "perpl"), p.wallet_address): p.display_name
                     for p in profs if p.display_name}
    return [_dict(w, names.get((getattr(w, "exchange", "perpl") or "perpl", w.trader_wallet)))
            for w in rows]


async def add_watch(follower_wallet: str, trader_wallet: str, exchange: str = "perpl") -> dict:
    follower = follower_wallet.lower()
    trader = trader_wallet.lower()
    if follower == trader:
        raise ValueError("Cannot watch yourself")

    sf = get_session_factory()
    async with sf() as session:
        existing = (await session.execute(
            select(Watchlist).where(
                Watchlist.follower_wallet == follower,
                Watchlist.trader_wallet == trader,
                Watchlist.exchange == exchange,
            )
        )).scalar_one_or_none()
        if existing:
            return _dict(existing)
        w = Watchlist(follower_wallet=follower, trader_wallet=trader, exchange=exchange,
                      created_at=datetime.utcnow())
        session.add(w)
        await session.commit()
        await session.refresh(w)
    # ensure a profile row exists for the watched trader (best-effort)
    try:
        await trader_profiles.upsert_profile(trader, exchange=exchange)
    except Exception:
        pass
    return _dict(w)


async def remove_watch(follower_wallet: str, trader_wallet: str, exchange: str = "perpl") -> bool:
    follower = follower_wallet.lower()
    trader = trader_wallet.lower()
    sf = get_session_factory()
    async with sf() as session:
        result = await session.execute(
            delete(Watchlist).where(
                Watchlist.follower_wallet == follower,
                Watchlist.trader_wallet == trader,
                Watchlist.exchange == exchange,
            )
        )
        await session.commit()
        return (result.rowcount or 0) > 0
