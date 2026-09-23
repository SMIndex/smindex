"""MCP resources — let Claude pull live state into context without an
explicit tool call. Useful when you want the model to "always know" the
current market snapshot when discussing trades.

Resources are read on demand by the client when the user mentions them or
when the prompt template references them. They MUST return strings.
"""
import json

from app.mcp.auth import current_user
from app.mcp.server import server


@server.resource("perpl://markets/all")
async def markets_snapshot() -> str:
    """Live snapshot of every Perpl market: price, OI, funding, 24h change."""
    from app.mcp.tools.markets import list_markets
    data = await list_markets()
    return json.dumps(data, default=str, indent=2)


@server.resource("perpl://leaderboard/top20")
async def leaderboard_snapshot() -> str:
    """Top 20 traders on Perpl by all-time PnL."""
    from app.mcp.tools.leaders import get_leaderboard
    data = await get_leaderboard("all", "pnl", 20)
    return json.dumps(data, default=str, indent=2)


@server.resource("perpl://account/positions")
async def my_positions_snapshot() -> str:
    """The authenticated user's current on-chain positions and balance."""
    from app.mcp.tools.account import get_my_positions
    try:
        await current_user()
    except PermissionError as exc:
        return json.dumps({"error": str(exc)}, indent=2)
    data = await get_my_positions()
    return json.dumps(data, default=str, indent=2)
