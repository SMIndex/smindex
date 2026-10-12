from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field


class LeaderStats(BaseModel):
    total_trades: int = 0
    win_rate: float = 0.0
    pnl_total: float = 0.0
    pnl_7d: float = 0.0
    pnl_30d: float = 0.0
    sharpe_ratio: float = 0.0
    max_drawdown: float = 0.0
    avg_leverage: float = 0.0
    followers_count: int = 0


class LeaderInDB(BaseModel):
    id: Optional[str] = Field(None, alias="_id")
    user_id: str
    wallet_address: str
    display_name: Optional[str] = None
    is_active: bool = True
    stats: LeaderStats = Field(default_factory=LeaderStats)
    registered_at: datetime = Field(default_factory=datetime.utcnow)

    model_config = {"populate_by_name": True}


class LeaderResponse(BaseModel):
    id: str
    wallet_address: str
    display_name: Optional[str] = None
    is_active: bool
    stats: LeaderStats
