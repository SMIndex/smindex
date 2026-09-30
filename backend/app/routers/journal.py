from typing import Optional, List

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import select

from app.db.database import get_session_factory
from app.db.models import User, TradeJournal
from app.routers.auth import get_authenticated_user
from app.utils.logger import get_logger

logger = get_logger(__name__)

router = APIRouter(prefix="/journal", tags=["journal"])


class CreateJournalEntry(BaseModel):
    market_id: int
    symbol: str
    side: str
    entry_price: float
    exit_price: Optional[float] = None
    pnl: Optional[float] = None
    notes: Optional[str] = None
    tags: Optional[List[str]] = None
    rating: Optional[int] = None


class UpdateJournalEntry(BaseModel):
    exit_price: Optional[float] = None
    pnl: Optional[float] = None
    notes: Optional[str] = None
    tags: Optional[List[str]] = None
    rating: Optional[int] = None


def _serialize(entry: TradeJournal) -> dict:
    return {
        "id": entry.id,
        "market_id": entry.market_id,
        "symbol": entry.symbol,
        "side": entry.side,
        "entry_price": entry.entry_price,
        "exit_price": entry.exit_price,
        "pnl": entry.pnl,
        "notes": entry.notes,
        "tags": entry.tags or [],
        "rating": entry.rating,
        "created_at": entry.created_at.isoformat() if entry.created_at else None,
    }


@router.post("")
async def create_entry(
    req: CreateJournalEntry,
    user: User = Depends(get_authenticated_user),
) -> dict:
    if req.rating is not None and (req.rating < 1 or req.rating > 5):
        raise HTTPException(status_code=400, detail="rating must be 1-5")

    sf = get_session_factory()
    async with sf() as session:
        entry = TradeJournal(
            user_id=user.id,
            market_id=req.market_id,
            symbol=req.symbol,
            side=req.side,
            entry_price=req.entry_price,
            exit_price=req.exit_price,
            pnl=req.pnl,
            notes=req.notes,
            tags=req.tags,
            rating=req.rating,
        )
        session.add(entry)
        await session.commit()
        await session.refresh(entry)
        return _serialize(entry)


@router.get("")
async def list_entries(
    market_id: Optional[int] = Query(None),
    tag: Optional[str] = Query(None),
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    user: User = Depends(get_authenticated_user),
) -> list[dict]:
    sf = get_session_factory()
    async with sf() as session:
        q = select(TradeJournal).where(TradeJournal.user_id == user.id)
        if market_id is not None:
            q = q.where(TradeJournal.market_id == market_id)
        if tag:
            q = q.where(TradeJournal.tags.like(f'%"{tag}"%'))
        q = q.order_by(TradeJournal.created_at.desc()).offset(skip).limit(limit)
        result = await session.execute(q)
        entries = result.scalars().all()
        return [_serialize(e) for e in entries]


@router.put("/{entry_id}")
async def update_entry(
    entry_id: int,
    req: UpdateJournalEntry,
    user: User = Depends(get_authenticated_user),
) -> dict:
    sf = get_session_factory()
    async with sf() as session:
        result = await session.execute(
            select(TradeJournal).where(
                TradeJournal.id == entry_id,
                TradeJournal.user_id == user.id,
            )
        )
        entry = result.scalar_one_or_none()
        if not entry:
            raise HTTPException(status_code=404, detail="Entry not found")

        if req.exit_price is not None:
            entry.exit_price = req.exit_price
        if req.pnl is not None:
            entry.pnl = req.pnl
        if req.notes is not None:
            entry.notes = req.notes
        if req.tags is not None:
            entry.tags = req.tags
        if req.rating is not None:
            if req.rating < 1 or req.rating > 5:
                raise HTTPException(status_code=400, detail="rating must be 1-5")
            entry.rating = req.rating

        await session.commit()
        await session.refresh(entry)
        return _serialize(entry)


@router.delete("/{entry_id}")
async def delete_entry(
    entry_id: int,
    user: User = Depends(get_authenticated_user),
) -> dict:
    sf = get_session_factory()
    async with sf() as session:
        result = await session.execute(
            select(TradeJournal).where(
                TradeJournal.id == entry_id,
                TradeJournal.user_id == user.id,
            )
        )
        entry = result.scalar_one_or_none()
        if not entry:
            raise HTTPException(status_code=404, detail="Entry not found")
        await session.delete(entry)
        await session.commit()
        return {"ok": True}
