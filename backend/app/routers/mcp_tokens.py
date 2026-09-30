"""MCP API token management — issue, list, revoke tokens for MCP clients.

Tokens are used by Claude Desktop / Code / Web to authenticate when calling
MCP tools that need user context. The plaintext token is shown ONCE on creation;
only its SHA-256 hash is stored. Each token has an expiry, scopes, and is
counted against a per-user cap to prevent unbounded sprawl.
"""
import datetime
import hashlib
import secrets
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select, func

from app.config import settings
from app.db.database import get_session_factory
from app.db.models import MCPToken, User
from app.routers.auth import get_authenticated_user
from app.utils.logger import get_logger

logger = get_logger(__name__)

router = APIRouter(prefix="/mcp-tokens", tags=["mcp"])

VALID_SCOPES = {"read", "journal", "stage"}


class CreateTokenRequest(BaseModel):
    label: str
    ttl_days: Optional[int] = None  # default = settings.MCP_TOKEN_TTL_DAYS
    scopes: Optional[List[str]] = None  # default = all valid scopes


class TokenInfo(BaseModel):
    id: int
    label: str
    created_at: datetime.datetime
    last_used_at: Optional[datetime.datetime] = None
    revoked_at: Optional[datetime.datetime] = None
    expires_at: Optional[datetime.datetime] = None
    scopes: Optional[str] = None


class CreateTokenResponse(BaseModel):
    token: str  # plaintext — shown ONCE
    info: TokenInfo


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _serialize(t: MCPToken) -> TokenInfo:
    return TokenInfo(
        id=t.id,
        label=t.label,
        created_at=t.created_at,
        last_used_at=t.last_used_at,
        revoked_at=t.revoked_at,
        expires_at=t.expires_at,
        scopes=t.scopes,
    )


def _normalize_scopes(scopes: Optional[List[str]]) -> str:
    if not scopes:
        return ",".join(sorted(VALID_SCOPES))
    cleaned = []
    for s in scopes:
        s = str(s).strip().lower()
        if s not in VALID_SCOPES:
            raise HTTPException(
                status_code=400,
                detail=f"Unknown scope '{s}'. Valid: {sorted(VALID_SCOPES)}",
            )
        if s not in cleaned:
            cleaned.append(s)
    return ",".join(sorted(cleaned))


@router.post("", response_model=CreateTokenResponse)
async def create_token(
    req: CreateTokenRequest,
    user: User = Depends(get_authenticated_user),
) -> CreateTokenResponse:
    label = (req.label or "").strip()[:64]
    if not label:
        raise HTTPException(status_code=400, detail="Label is required")

    scopes_csv = _normalize_scopes(req.scopes)

    ttl_days = req.ttl_days if req.ttl_days is not None else settings.MCP_TOKEN_TTL_DAYS
    ttl_days = max(1, min(int(ttl_days), 365))
    now = datetime.datetime.utcnow()
    expires_at = now + datetime.timedelta(days=ttl_days)

    sf = get_session_factory()
    async with sf() as session:
        # Enforce per-user active-token cap.
        active_count = await session.execute(
            select(func.count(MCPToken.id)).where(
                MCPToken.user_id == user.id,
                MCPToken.revoked_at.is_(None),
            )
        )
        n = active_count.scalar() or 0
        if n >= settings.MCP_MAX_TOKENS_PER_USER:
            raise HTTPException(
                status_code=429,
                detail=(
                    f"Max active MCP tokens reached ({settings.MCP_MAX_TOKENS_PER_USER}). "
                    "Revoke an unused one first."
                ),
            )

        # 32 bytes → ~43 char URL-safe string. Prefix for visual identification.
        plaintext = "perpl_mcp_" + secrets.token_urlsafe(32)
        token_hash = _hash_token(plaintext)

        token = MCPToken(
            user_id=user.id,
            label=label,
            token_hash=token_hash,
            created_at=now,
            expires_at=expires_at,
            scopes=scopes_csv,
        )
        session.add(token)
        await session.commit()
        await session.refresh(token)

    logger.info(
        "MCP token issued user_id=%s label=%s scopes=%s ttl_days=%s",
        user.id, label, scopes_csv, ttl_days,
    )
    return CreateTokenResponse(token=plaintext, info=_serialize(token))


@router.get("", response_model=List[TokenInfo])
async def list_tokens(user: User = Depends(get_authenticated_user)) -> List[TokenInfo]:
    sf = get_session_factory()
    async with sf() as session:
        result = await session.execute(
            select(MCPToken)
            .where(MCPToken.user_id == user.id)
            .order_by(MCPToken.created_at.desc())
        )
        tokens = result.scalars().all()
        return [_serialize(t) for t in tokens]


@router.delete("/{token_id}")
async def revoke_token(
    token_id: int,
    user: User = Depends(get_authenticated_user),
) -> dict:
    sf = get_session_factory()
    async with sf() as session:
        result = await session.execute(
            select(MCPToken).where(
                MCPToken.id == token_id,
                MCPToken.user_id == user.id,
            )
        )
        token = result.scalar_one_or_none()
        if not token:
            raise HTTPException(status_code=404, detail="Token not found")
        if token.revoked_at is not None:
            return {"success": True, "message": "Already revoked"}
        token.revoked_at = datetime.datetime.utcnow()
        await session.commit()

    logger.info("MCP token revoked id=%s user_id=%s", token_id, user.id)
    return {"success": True}
