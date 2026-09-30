"""Backtest metrics tests (Part E)."""
from app.strategy_engine.backtest.metrics import compute_metrics


def test_empty():
    m = compute_metrics([])
    assert m["trades"] == 0 and m["profit_factor"] is None


def test_known_metrics():
    trades = [{"pnl_net": 10, "pnl_gross": 11, "fees": 1},
              {"pnl_net": -5, "pnl_gross": -4, "fees": 1},
              {"pnl_net": -5, "pnl_gross": -4, "fees": 1},
              {"pnl_net": 20, "pnl_gross": 21, "fees": 1}]
    m = compute_metrics(trades)
    assert m["trades"] == 4
    assert m["win_rate"] == 50.0
    assert m["profit_factor"] == 3.0            # (10+20)/(5+5)
    assert m["longest_losing_streak"] == 2
    assert m["expectancy"] == 5.0
