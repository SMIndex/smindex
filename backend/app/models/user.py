from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field


class UserInDB(BaseModel):
    id: Optional[str] = Field(None, alias="_id")
    wallet_address: str
    perpl_account_id: Optional[str] = None
    perpl_auth_token_encrypted: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)

    model_config = {"populate_by_name": True}


class UserResponse(BaseModel):
    wallet_address: str
    perpl_linked: bool
