"""MCP authentication — verify a bearer token and resolve it to a User.

Two extraction paths:
- HTTP/SSE transport: token comes in `Authorization: Bearer ...` header,
  read from a contextvar set by MCPAuthMiddleware on each request.
- stdio transport: token comes from the `PERPL_MCP_TOKEN` env var,
  set once when Claude Desktop / Code launches the server.

Both paths converge on `current_user()` which:
  - validates the token hash, revocation, and expiry
  - enforces an optional scope (`read` / `journal` / `stage`)
  - applies a per-token rate limit
  - logs failed lookups (without leaking the token)
  - returns a `User` row or raises `PermissionError`
"""
import contextvars
import datetime
import hashlib
import os
from typing import Optional

from sqlalchemy import select

from app.db.database import get_session_factory
from app.db.models import MCPToken, User
from app.mcp.rate_limit import check_and_increment
from app.utils.logger import get_logger

logger = get_logger(__name__)

# Set per-HTTP-request by MCPAuthMiddleware (see http_app.py).
_request_token: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar(
    "perpl_mcp_request_token", default=None
)
_request_ip: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar(
    "perpl_mcp_request_ip", default=None
)


def set_request_token(token: Optional[str]) -> None:
    _request_token.set(token)


def set_request_ip(ip: Optional[str]) -> None:
    _request_ip.set(ip)


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _extract_token() -> Optional[str]:
    tok = _request_token.get()
    if tok:
        return tok
    return os.environ.get("PERPL_MCP_TOKEN")


def _peer() -> str:
    return _request_ip.get() or "stdio"


def _check_scope(stored: Optional[str], required: Optional[str]) -> bool:
    if required is None:
        return True
    # NULL scopes column = legacy token, treat as full access (back-compat).
    if not stored:
        return True
    granted = {s.strip() for s in stored.split(",") if s.strip()}
    return required in granted or "all" in granted


async def current_user(required_scope: Optional[str] = None) -> User:
    """Resolve the bearer token to a User. Raise PermissionError if invalid.

    `required_scope`: if set, the token's `scopes` column must contain it
    (or be NULL for legacy tokens). Use 'read', 'journal', or 'stage'.
    """
    token = _extract_token()
    if not token:
        logger.warning("MCP auth: missing token peer=%s scope=%s", _peer(), required_scope)
        raise PermissionError(
            "Missing MCP token. Set Authorization: Bearer <token> (HTTP) "
            "or PERPL_MCP_TOKEN env var (stdio). Generate a token at "
            "Settings → MCP Access in SMINDEX."
        )

    token_hash = _hash_token(token)

    # Rate-limit BEFORE the DB hit so a flood of bad tokens can't hammer MySQL.
    check_and_increment(token_hash)

    sf = get_session_factory()
    if sf is None:
        raise RuntimeError("Database not initialized")

    async with sf() as session:
        result = await session.execute(
            select(MCPToken).where(MCPToken.token_hash == token_hash)
        )
        row = result.scalar_one_or_none()
        if row is None:
            logger.warning(
                "MCP auth: invalid token peer=%s prefix=%s",
                _peer(), token[:12],
            )
            raise PermissionError("Invalid MCP token")
        if row.revoked_at is not None:
            logger.warning("MCP auth: revoked token id=%s peer=%s", row.id, _peer())
            raise PermissionError("MCP token has been revoked")
        if row.expires_at is not None and row.expires_at < datetime.datetime.utcnow():
            logger.warning("MCP auth: expired token id=%s peer=%s", row.id, _peer())
            raise PermissionError("MCP token has expired")
        if not _check_scope(row.scopes, required_scope):
            logger.warning(
                "MCP auth: scope %s denied for token id=%s scopes=%s",
                required_scope, row.id, row.scopes,
            )
            raise PermissionError(
                f"This MCP token does not have the '{required_scope}' scope. "
                "Issue a new token from Settings → MCP Access."
            )

        # Touch last_used_at (best-effort).
        row.last_used_at = datetime.datetime.utcnow()
        await session.commit()

        result2 = await session.execute(select(User).where(User.id == row.user_id))
        user = result2.scalar_one_or_none()
        if user is None:
            raise PermissionError("Token owner no longer exists")
        return user


async def current_user_optional() -> Optional[User]:
    """Same as current_user but returns None instead of raising."""
    try:
        return await current_user()
    except (PermissionError, RuntimeError):
        return None
