# =============================================================================
# LEGACY / DEAD (copy v0). Not wired in main.py (periodic_stats_update is never
# started). Computes from the legacy LeaderTrade / FollowerConfig tables.
# Superseded by copy v1 trader stats (trader_profiles + trader_stats_daily).
# DO NOT use for new work. Scheduled for removal in a later phase.
# =============================================================================
import asyncio
import math
from datetime import datetime, timedelta

from sqlalchemy import select, update, func

from app.db.database import get_session_factory
from app.db.models import Leader, LeaderTrade, FollowerConfig
from app.utils.logger import get_logger

logger = get_logger(__name__)


async def calculate_stats(leader_id: int) -> dict:
    async_session = get_session_factory()
    now = datetime.utcnow()
    seven_days_ago = now - timedelta(days=7)
    thirty_days_ago = now - timedelta(days=30)

    async with async_session() as session:
        result = await session.execute(
            select(LeaderTrade)
            .where(LeaderTrade.leader_id == leader_id)
            .order_by(LeaderTrade.timestamp.asc())
        )
        trades = result.scalars().all()

        total_trades = len(trades)

        if total_trades == 0:
            count_result = await session.execute(
                select(func.count())
                .select_from(FollowerConfig)
                .where(
                    FollowerConfig.leader_id == leader_id,
                    FollowerConfig.is_active == True,
                )
            )
            followers_count = count_result.scalar() or 0
            return {
                "total_trades": 0,
                "win_rate": 0.0,
                "pnl_total": 0.0,
                "pnl_7d": 0.0,
                "pnl_30d": 0.0,
                "sharpe_ratio": 0.0,
                "max_drawdown": 0.0,
                "avg_leverage": 0.0,
                "followers_count": followers_count,
            }

    winning_trades = 0
    pnl_total = 0.0
    pnl_7d = 0.0
    pnl_30d = 0.0
    leverages = []
    daily_returns: list[float] = []

    positions: dict[int, dict] = {}

    for trade in trades:
        market_id = trade.market_id
        side = trade.side
        size = trade.size
        price = trade.price
        leverage = trade.leverage
        timestamp = trade.timestamp
        is_close = trade.is_close

        leverages.append(leverage)

        if is_close and market_id in positions:
            pos = positions[market_id]
            if pos["side"] == "buy":
                trade_pnl = (price - pos["avg_price"]) * min(size, pos["size"])
            else:
                trade_pnl = (pos["avg_price"] - price) * min(size, pos["size"])

            trade_pnl *= leverage
            pnl_total += trade_pnl

            if trade_pnl > 0:
                winning_trades += 1

            if timestamp and timestamp >= seven_days_ago:
                pnl_7d += trade_pnl
            if timestamp and timestamp >= thirty_days_ago:
                pnl_30d += trade_pnl

            daily_returns.append(trade_pnl)

            remaining = pos["size"] - size
            if remaining <= 0:
                del positions[market_id]
            else:
                positions[market_id]["size"] = remaining
        else:
            if market_id in positions and positions[market_id]["side"] == side:
                pos = positions[market_id]
                total_size = pos["size"] + size
                pos["avg_price"] = (
                    pos["avg_price"] * pos["size"] + price * size
                ) / total_size
                pos["size"] = total_size
            else:
                positions[market_id] = {
                    "side": side,
                    "avg_price": price,
                    "size": size,
                }

    win_rate = (winning_trades / total_trades * 100) if total_trades > 0 else 0.0
    avg_leverage = sum(leverages) / len(leverages) if leverages else 0.0

    sharpe_ratio = 0.0
    if len(daily_returns) >= 2:
        mean_return = sum(daily_returns) / len(daily_returns)
        variance = sum(
            (r - mean_return) ** 2 for r in daily_returns
        ) / (len(daily_returns) - 1)
        std_dev = math.sqrt(variance) if variance > 0 else 0
        if std_dev > 0:
            sharpe_ratio = (mean_return / std_dev) * math.sqrt(365)

    max_drawdown = 0.0
    cumulative = 0.0
    peak = 0.0
    for ret in daily_returns:
        cumulative += ret
        if cumulative > peak:
            peak = cumulative
        drawdown = peak - cumulative
        if drawdown > max_drawdown:
            max_drawdown = drawdown

    async with async_session() as session:
        count_result = await session.execute(
            select(func.count())
            .select_from(FollowerConfig)
            .where(
                FollowerConfig.leader_id == leader_id,
                FollowerConfig.is_active == True,
            )
        )
        followers_count = count_result.scalar() or 0

    return {
        "total_trades": total_trades,
        "win_rate": round(win_rate, 2),
        "pnl_total": round(pnl_total, 2),
        "pnl_7d": round(pnl_7d, 2),
        "pnl_30d": round(pnl_30d, 2),
        "sharpe_ratio": round(sharpe_ratio, 4),
        "max_drawdown": round(max_drawdown, 2),
        "avg_leverage": round(avg_leverage, 2),
        "followers_count": followers_count,
    }


async def update_leader_stats(leader_id: int) -> None:
    stats = await calculate_stats(leader_id)

    # Don't overwrite manually-seeded stats with zeros
    if stats["total_trades"] == 0:
        async_session = get_session_factory()
        async with async_session() as session:
            result = await session.execute(
                select(Leader.pnl_total).where(Leader.id == leader_id)
            )
            existing_pnl = result.scalar()
            if existing_pnl and existing_pnl != 0:
                # Seeded leader with no real trades — skip entirely
                return

    async_session = get_session_factory()
    async with async_session() as session:
        await session.execute(
            update(Leader).where(Leader.id == leader_id).values(
                total_trades=stats["total_trades"],
                win_rate=stats["win_rate"],
                pnl_total=stats["pnl_total"],
                pnl_7d=stats["pnl_7d"],
                pnl_30d=stats["pnl_30d"],
                sharpe_ratio=stats["sharpe_ratio"],
                max_drawdown=stats["max_drawdown"],
                avg_leverage=stats["avg_leverage"],
                followers_count=stats["followers_count"],
            )
        )
        await session.commit()
    logger.debug("Updated stats for leader %d", leader_id)


async def periodic_stats_update() -> None:
    logger.info("Starting periodic leader stats update (every 5 min)")
    while True:
        try:
            async_session = get_session_factory()
            async with async_session() as session:
                result = await session.execute(
                    select(Leader.id).where(Leader.is_active == True)
                )
                leader_ids = result.scalars().all()

            for leader_id in leader_ids:
                try:
                    await update_leader_stats(leader_id)
                except Exception:
                    logger.exception("Failed to update stats for leader %d", leader_id)

            logger.info("Updated stats for %d leaders", len(leader_ids))

        except asyncio.CancelledError:
            logger.info("Periodic stats update cancelled")
            break
        except Exception:
            logger.exception("Error in periodic stats update")

        await asyncio.sleep(300)
