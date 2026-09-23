from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field


class FollowerConfigCreate(BaseModel):
    leader_id: str
    allocation_usd: float
    max_leverage: float = 50.0


class FollowerConfigUpdate(BaseModel):
    allocation_usd: Optional[float] = None
    max_leverage: Optional[float] = None
    is_active: Optional[bool] = None


class FollowerConfigInDB(BaseModel):
    id: Optional[str] = Field(None, alias="_id")
    follower_id: str
    leader_id: str
    allocation_usd: float
    max_leverage: float
    is_active: bool = True
    created_at: datetime = Field(default_factory=datetime.utcnow)

    model_config = {"populate_by_name": True}


class FollowerConfigResponse(BaseModel):
    id: str
    leader_id: str
    allocation_usd: float
    max_leverage: float
    is_active: bool
    leader_wallet: Optional[str] = None
    leader_display_name: Optional[str] = None
