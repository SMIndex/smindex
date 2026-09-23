"""FastMCP server singleton for SMINDEX.

Importing this module is enough to register every tool, resource, and prompt
because the submodules use `@server.tool()` / `@server.resource()` decorators
that mutate this singleton at import time.
"""
from mcp.server.fastmcp import FastMCP

server = FastMCP(
    name="perpl-terminal",
    instructions=(
        "SMINDEX MCP server. Read live perpetual futures market data "
        "(BTC, ETH, SOL, MON on Monad Mainnet), the user's positions and "
        "account health, the leaderboard, funding rates, and whale activity. "
        "Use stage_order to propose a trade — it returns a deep link the user "
        "clicks to review and place the order in the terminal UI. The server "
        "never places orders directly."
    ),
)

# Importing the submodules registers everything via decorators.
# Imports are at the bottom to avoid circular imports.
from app.mcp.tools import markets, account, leaders, stage  # noqa: E402,F401
from app.mcp import resources, prompts  # noqa: E402,F401
