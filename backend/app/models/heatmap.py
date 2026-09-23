from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field


class LiquidationBin(BaseModel):
    price: float
    intensity: float  # 0-1
    long_liq_usd: float
    short_liq_usd: float


class HeatmapSnapshot(BaseModel):
    market_id: int
    current_price: float
    bins: list[LiquidationBin]
    timestamp: datetime = Field(default_factory=datetime.utcnow)
