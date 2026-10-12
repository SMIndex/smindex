"""Terminal Statistics: aggregated stats from our own DB."""

from datetime import datetime, timezone, timedelta

from fastapi import APIRouter, Depends
from sqlalchemy import select, func, case, distinct, desc, and_, text

from app.db.database import get_session_factory
from app.db.models import (
    User, TradeHistory, OrderHistory, CopyPosition,
    WalletFollow, CopyTradeLog, EquitySnapshot,
)
from app.utils.logger import get_logger
from app.routers.admin import require_admin

logger = get_logger(__name__)

router = APIRouter(prefix="/terminal-stats", tags=["terminal-stats"])

# (stale hardcoded _SYMBOLS map removed, it was dead code; symbols come from
# the TradeHistory/OrderHistory rows themselves)


async def _smindex_section(session) -> dict:
    """What actually went through SMINDEX, from server-side records only:
    manual orders/fills from the trading proxy (platform_orders/_fills, since
    2026-10-12), auto-copy from auto_copy_log (every order the engine placed),
    and copied positions from the live-copy and auto-copy position tables."""
    async def one(sql: str) -> dict:
        row = (await session.execute(text(sql))).mappings().first()
        return {k: (float(v) if v is not None and not isinstance(v, (str, datetime)) else v)
                for k, v in dict(row or {}).items()}

    po = await one(
        "SELECT COUNT(*) orders, COUNT(DISTINCT wallet) traders, MIN(created_at) since "
        "FROM platform_orders WHERE order_type IN (1,2,3,4) AND (status IS NULL OR status <> 7)")
    pf = await one(
        "SELECT COUNT(*) fills, COALESCE(SUM(notional_usd),0) volume, COALESCE(SUM(fee_usd),0) fees, "
        "COUNT(DISTINCT wallet) traders, "
        "COALESCE(SUM(IF(fill_ts >= UTC_TIMESTAMP() - INTERVAL 1 DAY, notional_usd, 0)),0) volume_24h, "
        "COALESCE(SUM(IF(fill_ts >= UTC_TIMESTAMP() - INTERVAL 7 DAY, notional_usd, 0)),0) volume_7d "
        "FROM platform_fills")
    by_market = [dict(r) for r in (await session.execute(text(
        "SELECT COALESCE(symbol, CONCAT('MKT-', market_id)) symbol, COUNT(*) fills, "
        "ROUND(COALESCE(SUM(notional_usd),0),2) volume FROM platform_fills "
        "GROUP BY 1 ORDER BY volume DESC LIMIT 20"))).mappings().all()]
    # reason is written by the engine as "open filled 3720 @ 0.005377",
    # "add filled N @ P", "reduced N @ P", "closed N @ P".
    notional = ("SUBSTRING_INDEX(SUBSTRING_INDEX(reason, ' @ ', 1), ' ', -1) "
                "* SUBSTRING_INDEX(reason, ' @ ', -1)")
    ac = await one(
        f"SELECT COUNT(*) orders, COALESCE(SUM({notional}),0) volume, "
        f"COALESCE(SUM(IF(created_at >= UTC_TIMESTAMP() - INTERVAL 1 DAY, {notional}, 0)),0) volume_24h, "
        f"COALESCE(SUM(IF(created_at >= UTC_TIMESTAMP() - INTERVAL 7 DAY, {notional}, 0)),0) volume_7d, "
        "COUNT(DISTINCT follower_wallet) traders, MIN(created_at) since "
        "FROM auto_copy_log WHERE mode = 'live' AND decision = 'copied' AND reason LIKE '% @ %'")
    acp = await one(
        "SELECT COUNT(*) positions, COALESCE(SUM(status <> 'closed'),0) open_now, "
        "COALESCE(SUM(realized_pnl),0) realized_pnl FROM auto_copy_positions WHERE mode = 'live'")
    acs = await one(
        "SELECT COUNT(*) subs, COALESCE(SUM(mode = 'live' AND status = 'active'),0) live_active, "
        "COUNT(DISTINCT user_id) users FROM auto_copy_subs")
    co = await one(
        "SELECT COUNT(*) orders, COALESCE(SUM(status = 'filled'),0) filled FROM copy_orders "
        "WHERE mode = 'live_manual'")
    clp = await one(
        "SELECT COUNT(*) positions, COALESCE(SUM(status IN ('open','partially_closed')),0) open_now "
        "FROM copy_live_positions WHERE status <> 'failed'")

    def n(d: dict, k: str) -> float:
        return float(d.get(k) or 0)

    def iso(v):
        return v.isoformat() + "Z" if isinstance(v, datetime) else None

    return {
        "manual": {
            "tracking_since": iso(po.get("since")),
            "orders": int(n(po, "orders")), "fills": int(n(pf, "fills")),
            "volume": round(n(pf, "volume"), 2), "fees": round(n(pf, "fees"), 4),
            "volume_24h": round(n(pf, "volume_24h"), 2), "volume_7d": round(n(pf, "volume_7d"), 2),
            "traders": int(max(n(po, "traders"), n(pf, "traders"))), "by_market": by_market,
        },
        "auto_copy": {
            "tracking_since": iso(ac.get("since")),
            "orders": int(n(ac, "orders")), "volume": round(n(ac, "volume"), 2),
            "volume_24h": round(n(ac, "volume_24h"), 2), "volume_7d": round(n(ac, "volume_7d"), 2),
            "traders": int(n(ac, "traders")), "positions": int(n(acp, "positions")),
            "open_now": int(n(acp, "open_now")), "realized_pnl": round(n(acp, "realized_pnl"), 2),
            "subscriptions": int(n(acs, "subs")), "live_active": int(n(acs, "live_active")),
            "users": int(n(acs, "users")),
        },
        # Manual copy orders are signed in the browser and go through the same
        # proxy, so their volume is already inside "manual" - never add it again.
        "copy": {
            "orders": int(n(co, "orders")), "filled": int(n(co, "filled")),
            "positions": int(n(clp, "positions")), "open_now": int(n(clp, "open_now")),
        },
        "totals": {
            "orders": int(n(po, "orders") + n(ac, "orders")),
            "volume": round(n(pf, "volume") + n(ac, "volume"), 2),
            "volume_24h": round(n(pf, "volume_24h") + n(ac, "volume_24h"), 2),
            "volume_7d": round(n(pf, "volume_7d") + n(ac, "volume_7d"), 2),
            "copied_positions": int(n(clp, "positions") + n(acp, "positions")),
        },
    }


@router.get("")
async def get_terminal_stats(_admin=Depends(require_admin)) -> dict:
    """Aggregate terminal stats from our DB. Admin only (user counts, volumes and
    top leaders are business data, not public)."""
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

        try:
            smindex = await _smindex_section(session)
        except Exception as exc:
            logger.warning("terminal-stats smindex section failed: %s", exc)
            smindex = None

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
        "smindex": smindex,
    }
