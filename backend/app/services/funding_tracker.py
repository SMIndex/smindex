"""Tracks funding rate history per market.

Stores 1 sample per funding interval change.
In-memory ring buffer for fast serving + DB persistence for history.
"""
import time
import asyncio
from collections import deque
from datetime import datetime

from app.utils.logger import get_logger

logger = get_logger(__name__)

MAX_BUFFER = 500  # in-memory


class FundingTracker:
    def __init__(self):
        self._buffers: dict[int, deque] = {}
        self._last_rate: dict[int, float] = {}
        self._loaded_from_db: set[int] = set()

    def record(self, market_id: int, funding_rate: float, mark_price: float):
        """Called on every poll. Only stores when rate changes."""
        prev_rate = self._last_rate.get(market_id)
        if prev_rate is not None and abs(funding_rate - prev_rate) < 1e-10:
            return

        self._last_rate[market_id] = funding_rate

        if market_id not in self._buffers:
            self._buffers[market_id] = deque(maxlen=MAX_BUFFER)

        now = time.time()
        entry = {
            "funding_rate": funding_rate,
            "mark_price": mark_price,
            "timestamp": now,
        }
        self._buffers[market_id].append(entry)

        # Persist to DB async
        asyncio.get_event_loop().call_soon(
            lambda: asyncio.ensure_future(self._save_to_db(market_id, funding_rate, mark_price))
        )

    async def _save_to_db(self, market_id: int, funding_rate: float, mark_price: float):
        try:
            from app.db.database import get_session_factory
            from app.db.models import FundingHistory
            sf = get_session_factory()
            async with sf() as session:
                row = FundingHistory(
                    market_id=market_id,
                    funding_rate=funding_rate,
                    mark_price=mark_price,
                    timestamp=datetime.utcnow(),
                )
                session.add(row)
                await session.commit()
        except Exception:
            pass

    async def load_from_db(self, market_id: int):
        """Load historical data from DB on startup."""
        if market_id in self._loaded_from_db:
            return
        try:
            from app.db.database import get_session_factory
            from app.db.models import FundingHistory
            from sqlalchemy import select, desc
            sf = get_session_factory()
            async with sf() as session:
                stmt = (
                    select(FundingHistory)
                    .where(FundingHistory.market_id == market_id)
                    .order_by(desc(FundingHistory.timestamp))
                    .limit(MAX_BUFFER)
                )
                result = await session.execute(stmt)
                rows = result.scalars().all()
                if rows:
                    if market_id not in self._buffers:
                        self._buffers[market_id] = deque(maxlen=MAX_BUFFER)
                    for row in reversed(rows):
                        self._buffers[market_id].append({
                            "funding_rate": row.funding_rate,
                            "mark_price": row.mark_price,
                            "timestamp": row.timestamp.timestamp() if row.timestamp else 0,
                        })
                    self._last_rate[market_id] = rows[0].funding_rate
                    logger.info("Loaded %d funding entries for market %d from DB", len(rows), market_id)
            self._loaded_from_db.add(market_id)
        except Exception:
            logger.exception("Failed to load funding history from DB")

    def get_history(self, market_id: int) -> list[dict]:
        return list(self._buffers.get(market_id, deque()))


funding_tracker = FundingTracker()
