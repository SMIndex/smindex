import asyncio
import math
from datetime import datetime

from sqlalchemy import select, delete, desc

from app.config import settings
from app.db.database import get_session_factory
from app.db.models import LiquidationSnapshot as LiquidationSnapshotModel
from app.models.heatmap import LiquidationBin, HeatmapSnapshot
from app.services.notification import event_bus, EVENT_HEATMAP_UPDATE
from app.utils.logger import get_logger

logger = get_logger(__name__)


class LiquidationCalculator:
    LEVERAGE_TIERS: dict[int, float] = {
        2: 0.05,
        3: 0.08,
        5: 0.20,
        10: 0.30,
        20: 0.20,
        50: 0.12,
        100: 0.05,
    }
    MAINTENANCE_MARGIN: float = 0.005
    NUM_BINS: int = 100
    PRICE_RANGE_PCT: float = 0.20

    def calculate_heatmap(
        self,
        market_id: int,
        current_price: float,
        long_oi: float,
        short_oi: float,
    ) -> HeatmapSnapshot:
        price_low = current_price * (1 - self.PRICE_RANGE_PCT)
        price_high = current_price * (1 + self.PRICE_RANGE_PCT)
        bin_width = (price_high - price_low) / self.NUM_BINS

        bin_prices = [
            price_low + (i + 0.5) * bin_width for i in range(self.NUM_BINS)
        ]
        long_liq_usd = [0.0] * self.NUM_BINS
        short_liq_usd = [0.0] * self.NUM_BINS

        for leverage, weight in self.LEVERAGE_TIERS.items():
            tier_long_oi = long_oi * weight
            tier_short_oi = short_oi * weight

            long_liq_price = current_price * (
                1 - 1.0 / leverage + self.MAINTENANCE_MARGIN
            )
            short_liq_price = current_price * (
                1 + 1.0 / leverage - self.MAINTENANCE_MARGIN
            )

            bandwidth = current_price * (0.02 + 0.05 / leverage)

            for i in range(self.NUM_BINS):
                bp = bin_prices[i]

                dist_long = abs(bp - long_liq_price)
                if dist_long < bandwidth:
                    kernel_val = 1.0 - (dist_long / bandwidth)
                    long_liq_usd[i] += tier_long_oi * kernel_val

                dist_short = abs(bp - short_liq_price)
                if dist_short < bandwidth:
                    kernel_val = 1.0 - (dist_short / bandwidth)
                    short_liq_usd[i] += tier_short_oi * kernel_val

        max_val = max(
            max(long_liq_usd, default=0),
            max(short_liq_usd, default=0),
            1e-10,
        )

        bins: list[LiquidationBin] = []
        for i in range(self.NUM_BINS):
            total = long_liq_usd[i] + short_liq_usd[i]
            intensity = min(total / max_val, 1.0)
            bins.append(
                LiquidationBin(
                    price=round(bin_prices[i], 2),
                    intensity=round(intensity, 4),
                    long_liq_usd=round(long_liq_usd[i], 2),
                    short_liq_usd=round(short_liq_usd[i], 2),
                )
            )

        return HeatmapSnapshot(
            market_id=market_id,
            current_price=current_price,
            bins=bins,
            timestamp=datetime.utcnow(),
        )

    async def run_periodic(self, ws_manager) -> None:
        logger.info(
            "Starting periodic heatmap calculation every %ds",
            settings.HEATMAP_INTERVAL_SECONDS,
        )
        while True:
            try:
                await self._tick(ws_manager)
            except asyncio.CancelledError:
                logger.info("Heatmap periodic task cancelled")
                break
            except Exception:
                logger.exception("Error in heatmap periodic tick")

            await asyncio.sleep(settings.HEATMAP_INTERVAL_SECONDS)

    async def _tick(self, ws_manager) -> None:
        from app.ws.client_feed import client_manager

        market_ids = ws_manager.market_ids

        for mid in market_ids:
            state = ws_manager.market_state_cache.get(mid)
            if not state:
                continue

            current_price = state.get("mark_price") or state.get("last_price")
            if not current_price or current_price <= 0:
                continue

            current_price = float(current_price)
            long_oi = float(state.get("long_open_interest", 0))
            short_oi = float(state.get("short_open_interest", 0))

            if long_oi <= 0 and short_oi <= 0:
                oi_usd = float(state.get("open_interest_usd", 0))
                if oi_usd <= 0:
                    continue
                long_oi = oi_usd / 2
                short_oi = oi_usd / 2

            snapshot = self.calculate_heatmap(
                mid, current_price, long_oi, short_oi
            )

            async_session = get_session_factory()
            async with async_session() as session:
                snapshot_row = LiquidationSnapshotModel(
                    market_id=mid,
                    current_price=current_price,
                    bins=[b.model_dump() for b in snapshot.bins],
                    timestamp=datetime.utcnow(),
                )
                session.add(snapshot_row)
                await session.flush()  # assigns snapshot_row.id

                # Keep only the 5 most recent rows per market — the REST
                # endpoint only ever reads the latest, but we keep a small
                # buffer so a slow /api/heatmap read never hits an empty table.
                keep_result = await session.execute(
                    select(LiquidationSnapshotModel.id)
                    .where(LiquidationSnapshotModel.market_id == mid)
                    .order_by(desc(LiquidationSnapshotModel.id))
                    .limit(5)
                )
                keep_ids = [row[0] for row in keep_result.fetchall()]
                if keep_ids:
                    await session.execute(
                        delete(LiquidationSnapshotModel).where(
                            LiquidationSnapshotModel.market_id == mid,
                            LiquidationSnapshotModel.id.notin_(keep_ids),
                        )
                    )

                await session.commit()

            await client_manager.broadcast(
                "heatmap",
                snapshot.model_dump(mode="json"),
                market_id=mid,
            )

            await event_bus.emit(EVENT_HEATMAP_UPDATE, snapshot)

            logger.debug(
                "Heatmap updated for market %d, price=%.2f",
                mid,
                current_price,
            )


liquidation_calculator = LiquidationCalculator()
