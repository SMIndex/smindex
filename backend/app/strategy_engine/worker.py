"""Strategy worker entrypoint:  python -m app.strategy_engine.worker

Runs as the SECOND systemd unit `perpl-strategy-worker`, sharing the MySQL DB
with the API but NOT importing the FastAPI app (prompt Part 1.2). Reasons
(recorded in STRATEGIES_PHASE1_REPORT.md): the API loop already stalls during
the hourly analytics ingest, the docs require 1-second evaluation on the fill
stream, and a strategy crash must never take the terminal down.

Phase 1 responsibilities live in later parts and plug into the hooks below:
  - Part 2: market-data feeds (HL market-wide ws + CEX polling) → strat_* tables
  - Part 3: feature/scheduler loop (regime, gauge, bias, timing) + heartbeat

This module is a runnable skeleton: it loads config, inits the DB, honours the
STRATEGY_ENGINE_ENABLED kill switch every cycle, logs a heartbeat, and shuts
down cleanly. It creates the StrategyEngine (empty in Phase 1) so the wiring
point exists. NOTHING trades.
"""
from __future__ import annotations

import asyncio
import logging
import signal

from app.config import Settings
from app.db import init_db, close_db, get_session_factory

from .engine import StrategyEngine
from .scheduler import Scheduler
from .data.feeds import FeedManager

logger = logging.getLogger("strategy_engine.worker")

HEARTBEAT_SECONDS = 30
BACKTEST_REFRESH_SECONDS = 24 * 3600


class StrategyWorker:
    def __init__(self) -> None:
        self.engine = StrategyEngine()      # Phase 1: no strategies registered
        self._stop = asyncio.Event()
        # Part 2/3 attach their managers here (feeds, scheduler) — kept as
        # explicit attributes so start()/stop() can own their lifecycle.
        self.feeds = None
        self.scheduler = None
        self._backtest_task: asyncio.Task | None = None

    async def _backtests(self, sf) -> None:
        """Background: replay every strategy over the stored candles at startup,
        then refresh daily. Writes backtest/results/{sid}.md|.json which the API
        serves from cache (<24h) so the Backtest button never blocks the loop."""
        from .backtest.runner import run_all
        while not self._stop.is_set():
            try:
                async with sf() as s:
                    await run_all(s)
                logger.info("backtests refreshed")
            except Exception:  # noqa: BLE001 — a replay fault never touches evaluation
                logger.exception("backtest refresh failed")
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=BACKTEST_REFRESH_SECONDS)
            except asyncio.TimeoutError:
                pass

    def request_stop(self, *_: object) -> None:
        logger.info("shutdown signal received")
        self._stop.set()

    async def run(self) -> None:
        settings = Settings()
        logging.basicConfig(
            level=getattr(logging, settings.LOG_LEVEL.upper(), logging.INFO),
            format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
        await init_db(settings.DATABASE_URL)
        logger.info(
            "strategy worker up | assets=%s | engine_enabled=%s | live_allowlist=%d wallets",
            settings.strategy_assets_list, settings.STRATEGY_ENGINE_ENABLED,
            len(settings.STRATEGY_LIVE_ALLOWLIST),
        )

        sf = get_session_factory()
        try:
            from app.db.migrations_models import apply_v15, apply_v16, apply_v17
            async with sf() as session:
                if await apply_v15(session):
                    logger.info("migration v15 applied by worker")
                if await apply_v16(session):
                    logger.info("migration v16 applied by worker")
                if await apply_v17(session):
                    logger.info("migration v17 applied by worker")
        except Exception:  # noqa: BLE001
            logger.exception("migration v15/v16/v17 failed in worker (continuing)")
        self.scheduler = Scheduler(sf, settings.strategy_assets_list)
        seeded = False
        feeds_started = False
        try:
            while not self._stop.is_set():
                # Re-read settings each cycle so the kill switch is honoured
                # without a code change; `systemctl stop perpl-strategy-worker`
                # is the documented hard stop (prompt Part 1.4).
                enabled = Settings().STRATEGY_ENGINE_ENABLED
                if not enabled:
                    logger.info("heartbeat | engine DISABLED (idling) | strategies=%d",
                                len(self.engine.strategy_ids))
                else:
                    try:
                        if not seeded:
                            await self.scheduler.start()
                            seeded = True
                        if not feeds_started:
                            self.feeds = FeedManager(sf, settings.strategy_assets_list)
                            await self.feeds.start(self._stop)
                            feeds_started = True
                            self.scheduler.feeds = self.feeds
                            logger.info("request budget: %s", self.feeds.budget_snapshot())
                        if self._backtest_task is None:
                            self._backtest_task = asyncio.create_task(self._backtests(sf))
                        await self.scheduler.tick()
                    except Exception:  # noqa: BLE001 — a fault must not kill the worker
                        logger.exception("scheduler/feeds cycle failed; continuing")
                try:
                    await asyncio.wait_for(self._stop.wait(), timeout=HEARTBEAT_SECONDS)
                except asyncio.TimeoutError:
                    pass
        finally:
            if self._backtest_task is not None:
                from .backtest import runner as bt_runner
                bt_runner.STOP.set()             # unblocks a replay thread so asyncio.run() can exit
                self._backtest_task.cancel()
            if self.feeds:
                await self.feeds.stop()
            logger.info("strategy worker stopping")
            await close_db()
            logger.info("strategy worker stopped cleanly")


async def _amain() -> None:
    worker = StrategyWorker()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, worker.request_stop)
        except NotImplementedError:
            # Windows dev: add_signal_handler is unsupported; Ctrl+C still raises
            # KeyboardInterrupt which unwinds run(). Production is Linux/systemd.
            pass
    await worker.run()


def main() -> None:
    try:
        asyncio.run(_amain())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
