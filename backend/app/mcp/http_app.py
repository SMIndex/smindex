"""HTTP/SSE sub-app for the Perpl MCP server.

Mounted in `app.main` at `/mcp`. Each incoming HTTP request must carry
`Authorization: Bearer <perpl_mcp_xxx>`. The token is stashed into a contextvar
that `app.mcp.auth.current_user()` reads when tools call it.

The actual MCP protocol handling (initialize, tools/list, tools/call, sse stream)
is provided by FastMCP via `server.sse_app()`.
"""
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

from app.config import settings
from app.mcp.auth import set_request_token, set_request_ip
from app.mcp.server import server  # ensures all tools / resources / prompts registered
from app.utils.logger import get_logger

logger = get_logger(__name__)


def _allowed_origins() -> set[str]:
    raw = (settings.MCP_ALLOWED_ORIGINS or "").strip()
    if not raw:
        return set()
    return {o.strip().rstrip("/") for o in raw.split(",") if o.strip()}


class MCPAuthMiddleware(BaseHTTPMiddleware):
    """Pull bearer token off the request and stash it for tool handlers.

    We do NOT reject unauthenticated requests at the middleware layer — the
    MCP `initialize` handshake is allowed without a token. Token enforcement
    happens lazily inside any tool that calls `current_user()`.

    However we DO enforce an Origin allowlist when the Origin header is
    present, to block cross-origin browser-based abuse. Requests with no
    Origin header (Claude Desktop / Code launching the server, curl, etc.)
    are allowed through.
    """

    async def dispatch(self, request: Request, call_next):
        origin = request.headers.get("origin")
        allow = _allowed_origins()
        if origin and allow:
            origin_norm = origin.strip().rstrip("/")
            if origin_norm not in allow:
                logger.warning("MCP origin denied: %s", origin)
                return JSONResponse(
                    {"error": "origin not allowed"}, status_code=403
                )

        auth = request.headers.get("authorization", "")
        token = None
        if auth.lower().startswith("bearer "):
            token = auth[7:].strip()
        set_request_token(token)

        # Stash client IP for auth-failure logging. Honors X-Forwarded-For
        # since the server runs behind nginx.
        xff = request.headers.get("x-forwarded-for", "")
        if xff:
            ip = xff.split(",")[0].strip()
        else:
            ip = request.client.host if request.client else None
        set_request_ip(ip)

        try:
            return await call_next(request)
        finally:
            set_request_token(None)
            set_request_ip(None)


# FastMCP gives us an SSE-transport ASGI app at `/sse` and `/messages/`.
# We wrap it in a Starlette app so we can layer the auth middleware.
_inner = server.sse_app()
http_app = Starlette(
    routes=_inner.routes,
    middleware=[Middleware(MCPAuthMiddleware)],
    lifespan=_inner.lifespan if hasattr(_inner, "lifespan") else None,
)
