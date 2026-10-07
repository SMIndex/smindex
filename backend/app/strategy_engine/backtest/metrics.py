"""Backtest metrics (Part E) — win rate, expectancy, PF, max drawdown, longest
losing streak, fee drag, MAE/MFE. Pure over a list of closed-trade dicts."""
from __future__ import annotations


def compute_metrics(trades: list[dict]) -> dict:
    """trades: [{pnl_net, pnl_gross, fees, mae, mfe}]. Returns the standard block."""
    n = len(trades)
    if n == 0:
        return {"trades": 0, "win_rate": None, "expectancy": None, "profit_factor": None,
                "max_drawdown": None, "longest_losing_streak": 0, "fee_drag_pct": None,
                "avg_mae": None, "avg_mfe": None}
    nets = [t.get("pnl_net", 0.0) for t in trades]
    wins = [p for p in nets if p > 0]
    losses = [p for p in nets if p < 0]
    gross_win = sum(wins)
    gross_loss = -sum(losses)
    pf = (gross_win / gross_loss) if gross_loss > 0 else (float("inf") if gross_win > 0 else None)
    # equity curve max drawdown
    eq = 0.0; peak = 0.0; maxdd = 0.0
    streak = 0; longest = 0
    for p in nets:
        eq += p
        peak = max(peak, eq)
        maxdd = min(maxdd, eq - peak)
        streak = streak + 1 if p < 0 else 0
        longest = max(longest, streak)
    fees = sum(t.get("fees", 0.0) for t in trades)
    gross = sum(t.get("pnl_gross", 0.0) for t in trades)
    fee_drag = (fees / gross * 100.0) if gross > 0 else None
    maes = [t["mae"] for t in trades if t.get("mae") is not None]
    mfes = [t["mfe"] for t in trades if t.get("mfe") is not None]
    return {
        "trades": n,
        "win_rate": round(len(wins) / n * 100.0, 1),
        "expectancy": round(sum(nets) / n, 4),
        "profit_factor": (round(pf, 3) if pf not in (None, float("inf")) else pf),
        "max_drawdown": round(maxdd, 2),
        "longest_losing_streak": longest,
        "fee_drag_pct": (round(fee_drag, 2) if fee_drag is not None else None),
        "avg_mae": (round(sum(maes) / len(maes), 4) if maes else None),
        "avg_mfe": (round(sum(mfes) / len(mfes), 4) if mfes else None),
    }
