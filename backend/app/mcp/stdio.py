"""stdio entry point for the Perpl MCP server.

Run as: `python -m app.mcp.stdio`

Used by Claude Desktop and Claude Code, which launch the MCP server as a
subprocess and communicate over stdin/stdout. Auth comes from the
`PERPL_MCP_TOKEN` environment variable (set in claude_desktop_config.json).

The DB connection is initialized at startup so tools that need user data
(get_my_positions, get_account_health, etc.) can resolve the token.
"""
import asyncio

from app.config import settings
from app.db.database import close_db, init_db
from app.mcp.server import server


async def _main() -> None:
    # Init the SQLAlchemy engine — required for token verification + user lookups.
    await init_db(settings.DATABASE_URL)
    try:
        await server.run_stdio_async()
    finally:
        await close_db()


def main() -> None:
    asyncio.run(_main())


if __name__ == "__main__":
    main()
