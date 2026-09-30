"""Terminal Statistics — aggregated stats from our own DB."""

from datetime import datetime, timezone, timedelta

from fastapi import APIRouter
from sqlalchemy import select, func, case, distinct, desc, and_

from app.db.database import get_session_factory
from app.db.models import (
    User, TradeHistory, OrderHistory, CopyPosition,
    WalletFollow, CopyTradeLog, EquitySnapshot,
)
from app.utils.logger import get_logger

logger = get_logger(__name__)

router = APIRouter(prefix="/terminal-stats", tags=["terminal-stats"])

# (stale hardcoded _SYMBOLS map removed — it was dead code; symbols come from
# the TradeHistory/OrderHistory rows themselves)


@router.get("")
async def get_terminal_stats() -> dict:
    """Aggregate terminal stats from our DB. No auth required."""
    sf = get_session_factory()
    now = datetime.now(timezone.utc)
    week_ago = now - timedelta(days=7)
    month_ago = now - timedelta(days=30)

    async with sf() as session:
        # === USERS ===
        total_users = (await session.execute(
            select(func.count(User.id))
        )).scalar() or 0

        linked_users = (await session.execute(
            select(func.count(User.id)).where(User.perpl_account_id.isnot(None))
        )).scalar() or 0

        username_users = (await session.execute(
            select(func.count(User.id)).where(User.username.isnot(None))
        )).scalar() or 0

        active_users_7d = (await session.execute(
            select(func.count(distinct(TradeHistory.user_id))).where(
                TradeHistory.created_at >= week_ago
            )
        )).scalar() or 0

        # === TRADES ===
        total_trades = (await session.execute(
            select(func.count(TradeHistory.id))
        )).scalar() or 0

        total_volume = (await session.execute(
            select(func.coalesce(func.sum(TradeHistory.notional), 0))
        )).scalar() or 0

        total_fees = (await session.execute(
            select(func.coalesce(func.sum(TradeHistory.fee), 0))
        )).scalar() or 0

        avg_trade_size = (await session.execute(
            select(func.coalesce(func.avg(TradeHistory.notional), 0))
        )).scalar() or 0

        # Volume by market
        vol_by_market_rows = (await session.execute(
            select(
                TradeHistory.market_id,
                TradeHistory.symbol,
                func.count(TradeHistory.id).label("trades"),
                func.coalesce(func.sum(TradeHistory.notional), 0).label("volume"),
            ).group_by(TradeHistory.market_id, TradeHistory.symbol)
        )).all()

        volume_by_market = [
            {"market_id": r.market_id, "symbol": r.symbol, "trades": r.trades, "volume": round(r.volume, 2)}
            for r in vol_by_market_rows
        ]

        # Trades by source
        source_rows = (await session.execute(
            select(
                TradeHistory.source,
                func.count(TradeHistory.id).label("count"),
                func.coalesce(func.sum(TradeHistory.notional), 0).label("volume"),
            ).group_by(TradeHistory.source)
        )).all()

        by_source = {
            r.source: {"count": r.count, "volume": round(r.volume, 2)}
            for r in source_rows
        }

        # Total realized PnL (close trades only)
        total_pnl = (await session.execute(
            select(func.coalesce(func.sum(TradeHistory.pnl), 0)).where(
                TradeHistory.action == "close"
            )
        )).scalar() or 0

        # === ORDERS ===
        total_orders = (await session.execute(
            select(func.count(OrderHistory.id))
        )).scalar() or 0

        order_status_rows = (await session.execute(
            select(
                OrderHistory.status,
                func.count(OrderHistory.id).label("count"),
            ).group_by(OrderHistory.status)
        )).all()

        order_by_status = {r.status: r.count for r in order_status_rows}

        order_type_rows = (await session.execute(
            select(
                OrderHistory.order_type,
                func.count(OrderHistory.id).label("count"),
            ).group_by(OrderHistory.order_type)
        )).all()

        order_by_type = {r.order_type: r.count for r in order_type_rows}

        fill_rate = round(
            order_by_status.get("filled", 0) / total_orders * 100 if total_orders > 0 else 0, 1
        )

        # === COPY TRADES ===
        total_copies = (await session.execute(
            select(func.count(CopyPosition.id))
        )).scalar() or 0

        copy_volume = (await session.execute(
            select(func.coalesce(func.sum(CopyPosition.allocation_usd), 0))
        )).scalar() or 0

        active_followers = (await session.execute(
            select(func.count(distinct(WalletFollow.follower_wallet))).where(
                WalletFollow.is_active == True
            )
        )).scalar() or 0

        # Most followed leaders
        top_leaders_rows = (await session.execute(
            select(
                WalletFollow.leader_wallet,
                func.count(WalletFollow.id).label("followers"),
            ).where(WalletFollow.is_active == True)
            .group_by(WalletFollow.leader_wallet)
            .order_by(desc("followers"))
            .limit(5)
        )).all()

        top_leaders = [
            {"wallet": r.leader_wallet, "followers": r.followers}
            for r in top_leaders_rows
        ]

        # === DAILY ACTIVITY (last 30 days) ===
        daily_rows = (await session.execute(
            select(
                func.date(TradeHistory.created_at).label("day"),
                func.count(TradeHistory.id).label("trades"),
                func.coalesce(func.sum(TradeHistory.notional), 0).label("volume"),
                func.count(distinct(TradeHistory.user_id)).label("users"),
            ).where(TradeHistory.created_at >= month_ago)
            .group_by(func.date(TradeHistory.created_at))
            .order_by(func.date(TradeHistory.created_at))
        )).all()

        daily_activity = [
            {
                "date": str(r.day),
                "trades": r.trades,
                "volume": round(r.volume, 2),
                "active_users": r.users,
            }
            for r in daily_rows
        ]

    return {
        "users": {
            "total": total_users,
            "perpl_linked": linked_users,
            "with_username": username_users,
            "active_7d": active_users_7d,
        },
        "trading": {
            "total_trades": total_trades,
            "total_volume": round(total_volume, 2),
            "total_fees": round(total_fees, 2),
            "total_pnl": round(total_pnl, 2),
            "avg_trade_size": round(avg_trade_size, 2),
            "by_market": volume_by_market,
            "by_source": by_source,
        },
        "orders": {
            "total": total_orders,
            "by_status": order_by_status,
            "by_type": order_by_type,
            "fill_rate": fill_rate,
        },
        "copy_trading": {
            "total_copies": total_copies,
            "copy_volume": round(copy_volume, 2),
            "active_followers": active_followers,
            "top_leaders": top_leaders,
        },
        "daily_activity": daily_activity,
    }
