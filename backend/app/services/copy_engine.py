# =============================================================================
# LEGACY / DEAD (copy v0). This engine is NOT wired anywhere (main.py never
# instantiates or starts it). It targets the legacy FollowerConfig /
# CopyTradeExecution tables. Superseded by copy v1 (paper engine, no live orders).
# DO NOT use for new work. Scheduled for removal in a later phase.
# =============================================================================
import asyncio
import time
from datetime import datetime
from sqlalchemy import select, update

from app.db.database import get_session_factory
from app.db.models import FollowerConfig, CopyTradeExecution, Leader
from app.services.notification import event_bus, EVENT_COPY_EXECUTION
from app.utils.logger import get_logger

logger = get_logger(__name__)


class CopyEngine:
    BATCH_SIZE = 20
    BATCH_DELAY = 0.05

    def __init__(self):
        self._ws_manager = None
        self._semaphore = asyncio.Semaphore(50)

    def set_ws_manager(self, ws_manager):
        self._ws_manager = ws_manager

    async def on_leader_trade(self, trade_data: dict) -> None:
        leader_id = trade_data["leader_id"]

        async_session = get_session_factory()
        async with async_session() as session:
            result = await session.execute(select(Leader).where(Leader.id == leader_id))
            leader = result.scalar_one_or_none()
            if not leader:
                logger.warning("Leader %d not found, skipping copy", leader_id)
                return
            leader_equity = max(abs(leader.pnl_total), 10000)

            result = await session.execute(
                select(FollowerConfig).where(
                    FollowerConfig.leader_id == leader_id,
                    FollowerConfig.is_active == True,
                )
            )
            configs = result.scalars().all()

        if not configs:
            logger.debug("No active followers for leader %d", leader_id)
            return

        logger.info("Processing leader trade for %d, %d followers", leader_id, len(configs))

        for batch_start in range(0, len(configs), self.BATCH_SIZE):
            batch = configs[batch_start:batch_start + self.BATCH_SIZE]
            tasks = [self._execute_copy(cfg, trade_data, leader_equity) for cfg in batch]
            results = await asyncio.gather(*tasks, return_exceptions=True)
            for i, r in enumerate(results):
                if isinstance(r, Exception):
                    logger.error("Copy failed for follower %d: %s", batch[i].follower_id, r)
            if batch_start + self.BATCH_SIZE < len(configs):
                await asyncio.sleep(self.BATCH_DELAY)

    async def _execute_copy(self, config: FollowerConfig, trade_data: dict, leader_equity: float) -> None:
        async with self._semaphore:
            follower_id = config.follower_id
            start_time = time.monotonic()

            proportional_size = trade_data["size"] * (config.allocation_usd / leader_equity)
            actual_leverage = min(trade_data["leverage"], config.max_leverage)
            if actual_leverage < trade_data["leverage"]:
                proportional_size *= actual_leverage / trade_data["leverage"]

            if proportional_size * trade_data["price"] < 1.0:
                logger.debug("Skipping tiny copy trade for follower %d (< $1)", follower_id)
                return

            async_session = get_session_factory()
            async with async_session() as session:
                execution = CopyTradeExecution(
                    follower_id=follower_id,
                    leader_trade_id=trade_data["id"],
                    leader_id=trade_data["leader_id"],
                    market_id=trade_data["market_id"],
                    side=trade_data["side"],
                    intended_size=proportional_size,
                    intended_price=trade_data["price"],
                    leverage=actual_leverage,
                    status="submitted",
                    submitted_at=datetime.utcnow(),
                )
                session.add(execution)
                await session.commit()
                await session.refresh(execution)
                exec_id = execution.id

            try:
                if not self._ws_manager:
                    raise RuntimeError("WS manager not set")
                trading_ws = await self._ws_manager.get_trading_ws(follower_id)
                if not trading_ws:
                    raise RuntimeError(f"No trading WS for follower {follower_id}")

                from app.utils.scaling import scale_price, scale_size

                result = await trading_ws.place_order(
                    market_id=trade_data["market_id"],
                    side=trade_data["side"],
                    size=scale_size(proportional_size, 8),
                    price=scale_price(trade_data["price"], 8),
                    leverage=int(actual_leverage),
                    order_type="market",
                )

                latency_ms = (time.monotonic() - start_time) * 1000

                async with async_session() as session:
                    if result.get("success") or result.get("order_id"):
                        actual_price = result.get("fill_price")
                        actual_size_val = result.get("fill_size")
                        status = "filled"
                        if actual_size_val and float(actual_size_val) < proportional_size * 0.99:
                            status = "partial"
                        await session.execute(
                            update(CopyTradeExecution).where(CopyTradeExecution.id == exec_id).values(
                                status=status,
                                actual_price=float(actual_price) if actual_price else None,
                                actual_size=float(actual_size_val) if actual_size_val else None,
                                filled_at=datetime.utcnow(),
                                latency_ms=latency_ms,
                            )
                        )
                        logger.info(
                            "Copy trade %s for follower %d: exec_id=%d (%.1fms)",
                            status, follower_id, exec_id, latency_ms,
                        )
                    else:
                        error_msg = result.get("error", "Unknown error")
                        await session.execute(
                            update(CopyTradeExecution).where(CopyTradeExecution.id == exec_id).values(
                                status="failed",
                                error=error_msg,
                                latency_ms=latency_ms,
                            )
                        )
                        logger.warning(
                            "Copy trade failed for follower %d: %s", follower_id, error_msg,
                        )
                    await session.commit()

            except Exception as exc:
                latency_ms = (time.monotonic() - start_time) * 1000
                async with async_session() as session:
                    await session.execute(
                        update(CopyTradeExecution).where(CopyTradeExecution.id == exec_id).values(
                            status="failed", error=str(exc), latency_ms=latency_ms,
                        )
                    )
                    await session.commit()
                logger.error("Copy exception for follower %d: %s", follower_id, exc)

            await event_bus.emit(EVENT_COPY_EXECUTION, {
                "execution_id": exec_id,
                "follower_id": follower_id,
                "leader_id": trade_data["leader_id"],
                "market_id": trade_data["market_id"],
                "side": trade_data["side"],
            })


copy_engine = CopyEngine()
