from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field


class LeaderTrade(BaseModel):
    id: Optional[str] = Field(None, alias="_id")
    leader_id: str
    market_id: int
    side: str  # "buy" or "sell"
    size: float
    price: float
    leverage: float
    is_close: bool
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    raw_fill: dict = Field(default_factory=dict)

    model_config = {"populate_by_name": True}


class CopyTradeExecution(BaseModel):
    id: Optional[str] = Field(None, alias="_id")
    follower_id: str
    leader_trade_id: str
    leader_id: str
    market_id: int
    side: str
    intended_size: float
    actual_size: Optional[float] = None
    intended_price: float
    actual_price: Optional[float] = None
    leverage: float
    status: str = "submitted"  # "submitted" / "filled" / "failed" / "partial"
    error: Optional[str] = None
    submitted_at: datetime = Field(default_factory=datetime.utcnow)
    filled_at: Optional[datetime] = None
    latency_ms: Optional[float] = None

    model_config = {"populate_by_name": True}
