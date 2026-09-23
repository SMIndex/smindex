"""MCP prompts — slash commands users can invoke in Claude clients to
trigger structured analysis. Each prompt returns a string that the client
inserts as the user message; Claude then chooses which tools to call.
"""
from typing import Optional

from app.mcp.server import server


@server.prompt()
def analyze_position(symbol: Optional[str] = None) -> str:
    """Pull the user's positions, account health, and live funding rates,
    then assess the risk and propose specific SL/TP levels.
    """
    target = f"the {symbol.upper()} position" if symbol else "all open positions"
    return (
        f"Analyze {target} on SMINDEX. Steps:\n"
        "1. Call get_account_health() to see equity, margin ratio, and per-position health scores.\n"
        "2. Call get_my_positions() for the on-chain truth (balance, leverage, liquidation distance).\n"
        "3. Call get_funding_compare() to see if funding cost is eroding the position.\n"
        "4. For each position, report:\n"
        "   - Current PnL and ROE %\n"
        "   - Distance to liquidation (warn if < 10%)\n"
        "   - Whether SL/TP is set (call get_my_sl_tp() to check)\n"
        "   - A recommended SL price (1.5x the worst recent swing) and TP price (R:R >= 2)\n"
        "5. End with a clear bullet list of concrete actions the user should take."
    )


@server.prompt()
def suggest_entry(symbol: str) -> str:
    """Pull market data, orderbook, candles, funding, and leaderboard
    exposure for a symbol, then propose an entry with stage_order().
    """
    sym = symbol.upper()
    return (
        f"I want to enter a position on {sym}. Build a trade proposal:\n"
        f"1. Call get_market('{sym}') for current price, bid/ask, OI, 24h change.\n"
        f"2. Call get_candles('{sym}', resolution_minutes=60, hours=72) and analyze the trend, range, "
        "and any key support/resistance levels.\n"
        f"3. Call get_orderbook('{sym}') and check liquidity depth + any whale levels nearby.\n"
        f"4. Call get_funding_compare('{sym}') — if funding is heavily one-sided, that's a contrarian signal.\n"
        f"5. Call get_leaderboard() and check whether top traders are positioned in this market "
        f"(call get_leader_positions(wallet) on the top 3 wallets).\n"
        "6. Call get_account_health() to see the user's available balance and current risk.\n"
        "7. Decide: long or short? entry price (market or limit)? leverage? amount in USD?\n"
        "   Always set BOTH a stop-loss and take-profit. Risk no more than 2% of equity.\n"
        f"8. Call stage_order(symbol='{sym}', side=..., order_type=..., amount_usd=..., "
        "leverage=..., price=..., sl_price=..., tp_price=..., rationale='one-line summary').\n"
        "9. Show the user the deep link, the summary, the estimated liquidation price, and the warnings. "
        "Tell them clearly that clicking the link opens the terminal pre-filled but they still have to "
        "click Place themselves."
    )


@server.prompt()
def review_portfolio() -> str:
    """Comprehensive portfolio review: equity trajectory, copy stats, journal, risk."""
    return (
        "Run a portfolio review for the authenticated user:\n"
        "1. Call get_account_health() — equity, margin used, position count, risk level.\n"
        "2. Call get_equity_curve(days=30) — describe the trajectory (up/down/sideways), "
        "biggest drawdown, biggest gain.\n"
        "3. Call get_copy_stats() — total copy PnL, win rate, ROI, top + worst leaders.\n"
        "4. Call get_my_orders(limit=20) — recent activity.\n"
        "5. Call get_journal(limit=10) — what is the user's recent trading thesis?\n"
        "6. Summarize:\n"
        "   - One-line headline (e.g. 'Up 12% over 30 days, mostly from BTC longs')\n"
        "   - Three things working\n"
        "   - Three risks or things to fix\n"
        "   - One concrete suggestion for the next trading session"
    )


@server.prompt()
def risk_check() -> str:
    """Fast risk audit — flag unprotected positions and near-liquidation exposures."""
    return (
        "Run a fast risk check on the user's account:\n"
        "1. Call get_account_health().\n"
        "2. Flag every position whose liq_distance_pct < 15.\n"
        "3. Flag every position with has_sl == false (unprotected downside).\n"
        "4. If account margin_ratio_pct > 60, warn about over-leverage.\n"
        "5. Output ONE line per problem with the action to take. If everything is fine, "
        "say so in one sentence."
    )


@server.prompt()
def funding_arb() -> str:
    """Cross-venue funding-arbitrage scan."""
    return (
        "Scan for funding-rate arbitrage between Perpl and Hyperliquid:\n"
        "1. Call get_funding_compare() (no arg, returns all 4 markets).\n"
        "2. For each market where the absolute funding rate diff > 5 bps "
        "(signal != 'neutral'), explain the trade:\n"
        "   - Which side to long, which side to short\n"
        "   - The annualized rate differential (rate * 24 * 365 if hourly funding)\n"
        "   - The notional size needed for $100/day target\n"
        "3. If no opportunities, say so. Don't force a trade."
    )
