import csv
import io
from datetime import datetime

from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse
from sqlalchemy import select, desc

from app.db.database import get_session_factory
from app.db.models import User, CopyTradeLog, CopyTradeExecution, CopyPosition
from app.routers.auth import get_authenticated_user
from app.utils.logger import get_logger

logger = get_logger(__name__)

router = APIRouter(tags=["orders"])


# ---- Feature 1: Order History ----

@router.get("/orders/history")
async def get_order_history(
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    market_id: int | None = None,
    user: User = Depends(get_authenticated_user),
) -> list[dict]:
    """Combined order history from CopyTradeLog and CopyTradeExecution tables."""
    wallet = user.wallet_address.lower()
    sf = get_session_factory()
    async with sf() as session:
        # Query CopyTradeLog
        stmt_logs = select(CopyTradeLog).where(
            CopyTradeLog.user_wallet == wallet
        )
        if market_id is not None:
            stmt_logs = stmt_logs.where(CopyTradeLog.market_id == market_id)
        stmt_logs = stmt_logs.order_by(desc(CopyTradeLog.timestamp)).offset(skip).limit(limit)
        result_logs = await session.execute(stmt_logs)
        logs = result_logs.scalars().all()

        # Query CopyTradeExecution
        stmt_exec = select(CopyTradeExecution).where(
            CopyTradeExecution.follower_id == user.id
        )
        if market_id is not None:
            stmt_exec = stmt_exec.where(CopyTradeExecution.market_id == market_id)
        stmt_exec = stmt_exec.order_by(desc(CopyTradeExecution.submitted_at)).offset(skip).limit(limit)
        result_exec = await session.execute(stmt_exec)
        execs = result_exec.scalars().all()

    # Merge and format
    orders = []
    seen_ids = set()

    for log in logs:
        orders.append({
            "id": f"log-{log.id}",
            "time": log.timestamp.isoformat() if log.timestamp else None,
            "market_id": log.market_id,
            "symbol": log.symbol,
            "side": log.side,
            "type": "market",
            "size": None,
            "price": None,
            "amount_usd": log.amount_usd,
            "leverage": log.leverage,
            "status": log.status,
            "error": log.error,
            "source": "copy_trade",
        })

    for ex in execs:
        orders.append({
            "id": f"exec-{ex.id}",
            "time": ex.submitted_at.isoformat() if ex.submitted_at else None,
            "market_id": ex.market_id,
            "symbol": _market_symbol(ex.market_id),
            "side": ex.side,
            "type": "market",
            "size": ex.actual_size or ex.intended_size,
            "price": ex.actual_price or ex.intended_price,
            "amount_usd": None,
            "leverage": ex.leverage,
            "status": ex.status,
            "error": ex.error,
            "source": "copy_execution",
        })

    # Sort by time descending
    orders.sort(key=lambda x: x["time"] or "", reverse=True)
    return orders[:limit]


# ---- Feature 5: CSV Exports ----

@router.get("/export/trades")
async def export_trades_csv(
    user: User = Depends(get_authenticated_user),
) -> StreamingResponse:
    """Export trade history as CSV."""
    wallet = user.wallet_address.lower()
    sf = get_session_factory()
    async with sf() as session:
        result = await session.execute(
            select(CopyTradeLog)
            .where(CopyTradeLog.user_wallet == wallet)
            .order_by(desc(CopyTradeLog.timestamp))
            .limit(1000)
        )
        logs = result.scalars().all()

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["Time", "Market", "Symbol", "Side", "Leverage", "Amount USD", "Status", "Error", "Leader"])
    for log in logs:
        writer.writerow([
            log.timestamp.isoformat() if log.timestamp else "",
            log.market_id,
            log.symbol,
            log.side,
            log.leverage,
            log.amount_usd,
            log.status,
            log.error or "",
            log.leader_wallet,
        ])

    output.seek(0)
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename=trades_{wallet[:10]}_{datetime.utcnow().strftime('%Y%m%d')}.csv"},
    )


@router.get("/export/copies")
async def export_copies_csv(
    user: User = Depends(get_authenticated_user),
) -> StreamingResponse:
    """Export copy position history as CSV."""
    wallet = user.wallet_address.lower()
    sf = get_session_factory()
    async with sf() as session:
        result = await session.execute(
            select(CopyPosition)
            .where(CopyPosition.follower_wallet == wallet)
            .order_by(desc(CopyPosition.opened_at))
            .limit(1000)
        )
        copies = result.scalars().all()

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["Opened", "Closed", "Market", "Symbol", "Side", "Entry Price", "Close Price", "Size", "Leverage", "Allocation USD", "Realized PnL", "Status", "Leader", "Source"])
    for cp in copies:
        writer.writerow([
            cp.opened_at.isoformat() if cp.opened_at else "",
            cp.closed_at.isoformat() if cp.closed_at else "",
            cp.market_id,
            cp.symbol,
            cp.side,
            cp.entry_price,
            cp.close_price or "",
            cp.size,
            cp.leverage,
            cp.allocation_usd,
            cp.realized_pnl or "",
            cp.status,
            cp.leader_wallet,
            cp.source,
        ])

    output.seek(0)
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename=copies_{wallet[:10]}_{datetime.utcnow().strftime('%Y%m%d')}.csv"},
    )


@router.get("/export/pnl")
async def export_pnl_csv(
    user: User = Depends(get_authenticated_user),
) -> StreamingResponse:
    """Export PnL data as CSV from closed copy positions."""
    wallet = user.wallet_address.lower()
    sf = get_session_factory()
    async with sf() as session:
        result = await session.execute(
            select(CopyPosition)
            .where(CopyPosition.follower_wallet == wallet, CopyPosition.status == "closed")
            .order_by(desc(CopyPosition.closed_at))
            .limit(1000)
        )
        closed = result.scalars().all()

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["Closed At", "Symbol", "Side", "Entry Price", "Close Price", "Size", "Leverage", "Realized PnL", "ROE %", "Leader"])
    for cp in closed:
        roe = 0.0
        if cp.allocation_usd and cp.allocation_usd > 0 and cp.realized_pnl is not None:
            roe = (cp.realized_pnl / cp.allocation_usd) * 100
        writer.writerow([
            cp.closed_at.isoformat() if cp.closed_at else "",
            cp.symbol,
            cp.side,
            cp.entry_price,
            cp.close_price or "",
            cp.size,
            cp.leverage,
            cp.realized_pnl or 0,
            round(roe, 2),
            cp.leader_wallet,
        ])

    output.seek(0)
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename=pnl_{wallet[:10]}_{datetime.utcnow().strftime('%Y%m%d')}.csv"},
    )


# Helper — symbols from the live chain_reader map (registry-synced), not a
# hardcoded {1,10,20,30} that mislabels HYPE/ZEC/SOL.
def _market_symbol(market_id: int) -> str:
    from app.services.chain_reader import market_symbol
    return market_symbol(market_id)
