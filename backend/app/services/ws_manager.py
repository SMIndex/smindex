import asyncio
from datetime import datetime
from sqlalchemy import select

from app.config import settings
from app.db.database import get_session_factory
from app.db.models import User as UserModel, Leader as LeaderModel, LeaderTrade as LeaderTradeModel
from app.services.notification import event_bus, EVENT_LEADER_TRADE, EVENT_MARKET_STATE_UPDATE
from app.services.whale_detector import whale_detector
from app.services.liquidation_calc import liquidation_calculator
from app.services.copy_engine import copy_engine
from app.services.leader_stats import periodic_stats_update
from app.services.perpl_client import perpl_client
from app.services.funding_tracker import funding_tracker
from app.services.oi_tracker import oi_tracker
from app.services.trader_tracker import trader_tracker
from app.utils.logger import get_logger

logger = get_logger(__name__)

# Module-level reference for other services to access cached market state
ws_manager: "WSManager | None" = None


class WSManager:
    # Mainnet market IDs: BTC=1, MON=10, ETH=20, SOL=30
    # Bootstrap seed only — overwritten from live context every poll cycle.
    MARKET_IDS = [1, 10, 20, 31, 40, 50]
    POLL_INTERVAL = 3  # seconds

    def __init__(self) -> None:
        self.market_ids = list(self.MARKET_IDS)
        self._running: bool = False
        self._background_tasks: list[asyncio.Task] = []
        self.market_state_cache: dict[int, dict] = {}
        self._prev_market_state: dict[int, dict] = {}
        self._market_configs: dict[int, dict] = {}

    async def start(self) -> None:
        global ws_manager
        logger.info("Starting WSManager with markets: %s", self.market_ids)
        self._running = True
        ws_manager = self
        copy_engine.set_ws_manager(self)
        event_bus.subscribe(EVENT_LEADER_TRADE, copy_engine.on_leader_trade)

        # Fetch initial context to populate market configs
        try:
            ctx = await perpl_client.get_context()
            for m in ctx.get("markets", []):
                mid = m.get("id")
                if mid in self.market_ids:
                    self._market_configs[mid] = m.get("config", {})
                    state = m.get("state", {})
                    parsed = self._parse_market_state(mid, m)
                    if parsed:
                        self.market_state_cache[mid] = parsed
            logger.info("Loaded initial context for %d markets", len(self._market_configs))
        except Exception:
            logger.exception("Failed to load initial context (will retry in poll loop)")

        # Start background tasks
        self._background_tasks.append(asyncio.create_task(self._poll_loop()))
        self._background_tasks.append(asyncio.create_task(liquidation_calculator.run_periodic(self)))
        self._background_tasks.append(asyncio.create_task(periodic_stats_update()))
        self._background_tasks.append(asyncio.create_task(trader_tracker.run_periodic()))
        from app.services.position_tracker import position_tracker
        self._background_tasks.append(asyncio.create_task(position_tracker.run_periodic()))
        self._background_tasks.append(asyncio.create_task(self._leaderboard_snapshot_loop()))
        self._background_tasks.append(asyncio.create_task(self._equity_snapshot_loop()))

        logger.info("WSManager started: REST polling every %ds", self.POLL_INTERVAL)

    async def stop(self) -> None:
        logger.info("Stopping WSManager")
        self._running = False
        for task in self._background_tasks:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        self._background_tasks.clear()
        logger.info("WSManager stopped")

    def _parse_market_state(self, market_id: int, market_data: dict) -> dict | None:
        state = market_data.get("state")
        if not state:
            return None

        config = market_data.get("config") or self._market_configs.get(market_id, {})
        price_dec = config.get("price_decimals", 1)
        size_dec = config.get("size_decimals", 5)
        price_divisor = 10 ** price_dec
        size_divisor = 10 ** size_dec

        mark_price = state.get("mrk", 0) / price_divisor if state.get("mrk") else 0
        last_price = state.get("lst", 0) / price_divisor if state.get("lst") else 0
        oracle_price = state.get("orl", 0) / price_divisor if state.get("orl") else 0
        mid_price = state.get("mid", 0) / price_divisor if state.get("mid") else 0
        bid_price = state.get("bid", 0) / price_divisor if state.get("bid") else 0
        ask_price = state.get("ask", 0) / price_divisor if state.get("ask") else 0
        prev_price = state.get("prv", 0) / price_divisor if state.get("prv") else 0

        oi_raw = state.get("oi", 0) / size_divisor if state.get("oi") else 0
        daily_volume = state.get("dv", 0) / size_divisor if state.get("dv") else 0

        # TVL and daily volume amount are in collateral units (AUSD, 6 decimals)
        tvl = int(state.get("tvl", 0)) / 1e6 if state.get("tvl") else 0
        dva = int(state.get("dva", 0)) / 1e6 if state.get("dva") else 0

        # Maintenance margin from config (basis points)
        maintenance_margin = config.get("maintenance_margin", 2000) / 10000

        # Compute OI in USD -- assume 50/50 long/short split
        oi_usd = oi_raw * mark_price if mark_price else 0
        long_oi_usd = oi_usd / 2
        short_oi_usd = oi_usd / 2

        symbol = market_data.get("symbol") or market_data.get("name") or f"MKT-{market_id}"

        # Funding rate from the funding section
        funding = market_data.get("funding", {})
        funding_rate_raw = funding.get("rate", 0)
        # Rate is in basis points (1/10000), so divide by 10000
        funding_rate = funding_rate_raw / 10000 if funding_rate_raw else 0

        return {
            "market_id": market_id,
            "symbol": symbol,
            "mark_price": mark_price,
            "last_price": last_price,
            "oracle_price": oracle_price,
            "mid_price": mid_price,
            "bid_price": bid_price,
            "ask_price": ask_price,
            "prev_price": prev_price,
            "open_interest": oi_raw,
            "open_interest_usd": oi_usd,
            "long_open_interest": long_oi_usd,
            "short_open_interest": short_oi_usd,
            "daily_volume": daily_volume,
            "daily_volume_usd": dva,
            "tvl": tvl,
            "maintenance_margin": maintenance_margin,
            "funding_rate": funding_rate,
            "price_change_24h": round(
                ((mark_price - prev_price) / prev_price * 100) if prev_price else 0, 2
            ),
        }

    async def _poll_loop(self) -> None:
        logger.info("Starting REST poll loop (interval=%ds)", self.POLL_INTERVAL)
        while self._running:
            try:
                ctx = await perpl_client.get_context()
                markets = ctx.get("markets", [])

                # Track the live market set dynamically — no hardcoded {1,10,20,30}.
                live_ids = [m.get("id") for m in markets if m.get("id") is not None]
                if live_ids:
                    self.market_ids = live_ids

                # Keep the on-chain reader's market map fresh from this same fetch
                # (position/orderbook scans), so listings/delistings apply without
                # a code change. Uses config decimals already in the context.
                try:
                    from app.services import chain_reader
                    chain_reader.apply_registry([
                        {
                            "market_id": m.get("id"),
                            "symbol": (m.get("name") or m.get("symbol") or f"MKT-{m.get('id')}").upper(),
                            "price_decimals": (m.get("config", {}) or {}).get("price_decimals"),
                            "size_decimals": (m.get("config", {}) or {}).get("size_decimals"),
                        }
                        for m in markets if m.get("id") is not None
                    ])
                except Exception:
                    pass

                for m in markets:
                    mid = m.get("id")
                    if mid is None:
                        continue

                    self._market_configs[mid] = m.get("config", {})
                    parsed = self._parse_market_state(mid, m)
                    if not parsed:
                        continue

                    await self._on_market_state_update(mid, parsed)

            except asyncio.CancelledError:
                logger.info("Poll loop cancelled")
                break
            except Exception:
                logger.exception("Error in REST poll loop")

            await asyncio.sleep(self.POLL_INTERVAL)

    async def _leaderboard_snapshot_loop(self):
        """Save top 20 leaderboard to DB every 30 minutes."""
        await asyncio.sleep(60)  # wait for startup
        while self._running:
            try:
                from app.db.database import get_session_factory
                from app.db.models import LeaderboardSnapshot
                from datetime import datetime

                traders = await perpl_client.get_leaderboard_parsed("all", "pnl")
                sf = get_session_factory()
                async with sf() as session:
                    now = datetime.utcnow()
                    for t in traders[:20]:
                        session.add(LeaderboardSnapshot(
                            wallet_address=t["wallet_address"],
                            rank=t["rank"],
                            pnl_total=t["pnl_total"],
                            roi=t["roi"],
                            volume=t["volume"],
                            period="all",
                            timestamp=now,
                        ))
                    await session.commit()
                logger.info("Leaderboard snapshot saved: %d traders", min(len(traders), 20))
            except asyncio.CancelledError:
                break
            except Exception:
                logger.exception("Leaderboard snapshot error")
            await asyncio.sleep(1800)  # every 30 minutes

    async def _equity_snapshot_loop(self):
        """Snapshot equity for all users with positions every 4 hours."""
        await asyncio.sleep(120)  # wait for startup + initial data
        INTERVAL = 4 * 3600  # 4 hours
        while self._running:
            try:
                from app.services.chain_reader import get_trader_detail
                from app.db.models import User, EquitySnapshot

                sf = get_session_factory()
                async with sf() as session:
                    result = await session.execute(
                        select(UserModel.wallet_address).where(UserModel.wallet_address.isnot(None))
                    )
                    wallets = [row[0] for row in result.all()]

                saved = 0
                for wallet in wallets:
                    try:
                        detail = await asyncio.get_event_loop().run_in_executor(
                            None, get_trader_detail, wallet
                        )
                        if not detail or not detail.get("positions"):
                            continue

                        balance = detail["balance"]
                        margin_used = detail["margin_used"]
                        positions = detail["positions"]

                        # Calculate unrealized PnL using live mark prices
                        unrealized_pnl = 0
                        for pos in positions:
                            mid = pos["market_id"]
                            mark = pos["mark_price"]
                            if mid in self.market_state_cache:
                                mark = self.market_state_cache[mid].get("mark_price", mark)
                            pnl = (mark - pos["entry_price"]) * pos["size"] if pos["side"] == "long" \
                                else (pos["entry_price"] - mark) * pos["size"]
                            unrealized_pnl += pnl + pos.get("funding_pnl", 0)

                        equity = balance + margin_used + unrealized_pnl

                        async with sf() as session:
                            session.add(EquitySnapshot(
                                wallet_address=wallet.lower(),
                                equity=round(equity, 2),
                                balance=round(balance, 2),
                                unrealized_pnl=round(unrealized_pnl, 2),
                                margin_used=round(margin_used, 2),
                                position_count=len(positions),
                                timestamp=datetime.utcnow(),
                            ))
                            await session.commit()
                        saved += 1
                    except Exception:
                        pass  # skip individual wallet errors

                if saved > 0:
                    logger.info("Equity snapshots saved: %d users", saved)
            except asyncio.CancelledError:
                break
            except Exception:
                logger.exception("Equity snapshot error")
            await asyncio.sleep(INTERVAL)

    async def _on_market_state_update(self, market_id: int, state: dict) -> None:
        from app.ws.client_feed import client_manager

        # Track previous state for OI divergence detection
        if market_id in self.market_state_cache:
            self._prev_market_state[market_id] = self.market_state_cache[market_id].copy()

        self.market_state_cache[market_id] = state

        # Broadcast to frontend clients
        await client_manager.broadcast("market_state", state, market_id=market_id)
        await event_bus.emit(EVENT_MARKET_STATE_UPDATE, state)

        # Track funding rate
        funding_rate = state.get("funding_rate", 0)
        mark_price = state.get("mark_price", 0)
        if funding_rate is not None and mark_price:
            funding_tracker.record(market_id, funding_rate, mark_price)

        # Track OI for spike detection
        oi_usd = state.get("open_interest_usd", 0)
        if oi_usd and oi_usd > 0:
            await oi_tracker.record(market_id, oi_usd)

        # Check OI divergence
        prev = self._prev_market_state.get(market_id)
        if prev:
            await whale_detector.check_oi_divergence(market_id, prev, state)
