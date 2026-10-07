from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field


class WhaleAlert(BaseModel):
    id: Optional[str] = Field(None, alias="_id")
    market_id: int
    alert_type: str  # "large_order" / "large_fill" / "oi_divergence"
    side: Optional[str] = None
    size_usd: float
    price: Optional[float] = None
    severity: str  # "medium" / "high" / "extreme"
    details: dict = Field(default_factory=dict)
    timestamp: datetime = Field(default_factory=datetime.utcnow)

    model_config = {"populate_by_name": True}


class OIDivergence(BaseModel):
    market_id: int
    oi_change_pct: float
    price_change_pct: float
    direction: str  # "bullish_divergence" / "bearish_divergence"
    timestamp: datetime = Field(default_factory=datetime.utcnow)
