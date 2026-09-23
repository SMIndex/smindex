"""Tracks bid vs ask volume from orderbook. Persists to DB."""
import time
import asyncio
from collections import deque
from datetime import datetime

MAX_SNAPSHOTS = 360  # 1 hour at 10s intervals


class FlowTracker:
    def __init__(self):
        self._buffers: dict[int, deque] = {}

    def record(self, market_id: int, bids: list[dict], asks: list[dict]):
        if market_id not in self._buffers:
            self._buffers[market_id] = deque(maxlen=MAX_SNAPSHOTS)

        bid_vol = sum(b.get("size", 0) for b in bids)
        ask_vol = sum(a.get("size", 0) for a in asks)
        delta = bid_vol - ask_vol
        now = time.time()

        self._buffers[market_id].append({
            "timestamp": now,
            "bid_vol": round(bid_vol, 6),
            "ask_vol": round(ask_vol, 6),
            "delta": round(delta, 6),
        })

        # Persist every 6th snapshot (every ~60s) to avoid DB spam
        if len(self._buffers[market_id]) % 6 == 0:
            try:
                asyncio.get_event_loop().call_soon(
                    lambda mv=market_id, bv=bid_vol, av=ask_vol, d=delta:
                        asyncio.ensure_future(self._save(mv, bv, av, d))
                )
            except Exception:
                pass

    async def _save(self, market_id: int, bid_vol: float, ask_vol: float, delta: float):
        try:
            from app.db.database import get_session_factory
            from app.db.models import OrderFlowSnapshot
            sf = get_session_factory()
            async with sf() as session:
                session.add(OrderFlowSnapshot(
                    market_id=market_id,
                    bid_vol=round(bid_vol, 6),
                    ask_vol=round(ask_vol, 6),
                    delta=round(delta, 6),
                    timestamp=datetime.utcnow(),
                ))
                await session.commit()
        except Exception:
            pass

    def get_flow(self, market_id: int) -> list[dict]:
        return list(self._buffers.get(market_id, deque()))


flow_tracker = FlowTracker()
