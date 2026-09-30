"""Leader trade event ingestion for copy v1 (idempotent).

A LeaderTradeEvent is the normalized record of a detected leader action. The
`unique_event_key` makes ingestion idempotent: re-detecting the same state (e.g.
after a restart or a duplicate poll) returns the existing row and does NOT create
a second one — so the paper engine never produces duplicate copy orders.

Supported event types: opened | increased | reduced | closed | liquidated
"""
from datetime import datetime

from sqlalchemy import select

from app.db.database import get_session_factory
from app.db.copy_models import LeaderTradeEvent
from app.utils.logger import get_logger

logger = get_logger(__name__)

EVENT_TYPES = {"opened", "increased", "reduced", "closed", "liquidated"}


def make_event_key(trader_wallet: str, market_id: int, event_type: str,
                   side: str, price=None, size=None, exchange: str = "perpl") -> str:
    """Deterministic idempotency key for a detected event instance.

    Includes price+size so distinct events on the same market get distinct keys,
    while a re-detection of the same state collides (and is deduped).
    migration v2: NEW keys are exchange-prefixed ("perpl:..." / "hl:...") so the
    two venues can never dedupe against each other. Historical keys are
    unprefixed and are NOT rewritten — the tracker's baseline seeding already
    prevents replays against history.
    TODO(v1): a re-open at the exact same price+size after a close would collide;
    acceptable for v1. A real fill/block id would make this exact.
    """
    p = "" if price is None else f"{float(price):.10g}"
    s = "" if size is None else f"{float(size):.10g}"
    return f"{exchange}:{trader_wallet.lower()}:{market_id}:{event_type}:{side}:{p}:{s}"


def _to_dict(e: LeaderTradeEvent) -> dict:
    return {
        "id": e.id,
        "exchange": getattr(e, "exchange", "perpl"),
        "trader_wallet": e.trader_wallet,
        "market_id": e.market_id,
        "symbol": e.symbol,
        "side": e.side,
        "event_type": e.event_type,
        "size": float(e.size) if e.size is not None else None,
        "price": float(e.price) if e.price is not None else None,
        "detected_at": e.detected_at.isoformat() if e.detected_at else None,
        "source": e.source,
        "raw": e.raw,
        "unique_event_key": e.unique_event_key,
    }


async def record_event(
    trader_wallet: str,
    market_id: int,
    symbol: str,
    side: str,
    event_type: str,
    *,
    size=None,
    price=None,
    source: str = "onchain_poll",
    raw: dict | None = None,
    unique_key: str | None = None,
    exchange: str = "perpl",
) -> tuple[dict, bool]:
    """Create-or-get a LeaderTradeEvent. Returns (event_dict, created).

    Idempotent on unique_event_key. If a row with the key exists, returns it with
    created=False (caller must NOT re-run copy orders for it)."""
    if event_type not in EVENT_TYPES:
        raise ValueError(f"Unsupported event_type: {event_type}")

    trader = trader_wallet.lower()
    key = unique_key or make_event_key(trader, market_id, event_type, side, price, size, exchange=exchange)

    sf = get_session_factory()
    async with sf() as session:
        existing = (await session.execute(
            select(LeaderTradeEvent).where(LeaderTradeEvent.unique_event_key == key)
        )).scalar_one_or_none()
        if existing:
            return _to_dict(existing), False

        evt = LeaderTradeEvent(
            exchange=exchange,
            trader_wallet=trader,
            market_id=market_id,
            symbol=symbol,
            side=side,
            event_type=event_type,
            size=size,
            price=price,
            detected_at=datetime.utcnow(),
            source=source,
            raw=raw,
            unique_event_key=key,
        )
        session.add(evt)
        try:
            await session.commit()
            await session.refresh(evt)
            # Recent-activity stamp (migration v5): this is THE fill-detection
            # choke point for both venues (hl tracker + perpl trader_tracker).
            # One indexed forward-only UPDATE per NEW event; never per fill seen.
            try:
                from app.services.copy.trader_profiles import touch_last_fill
                await touch_last_fill(trader, exchange, evt.detected_at)
            except Exception:
                pass  # activity stamp must never break event recording
            return _to_dict(evt), True
        except Exception:
            # Lost a race on the unique key — fetch the winner.
            await session.rollback()
            existing = (await session.execute(
                select(LeaderTradeEvent).where(LeaderTradeEvent.unique_event_key == key)
            )).scalar_one_or_none()
            if existing:
                return _to_dict(existing), False
            raise


async def get_event(event_id: int) -> dict | None:
    sf = get_session_factory()
    async with sf() as session:
        e = await session.get(LeaderTradeEvent, event_id)
        return _to_dict(e) if e else None
