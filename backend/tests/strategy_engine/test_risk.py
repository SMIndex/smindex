"""RiskEngine + sizing rule tests (doc 00 §5)."""
import math

from app.strategy_engine.risk import sizing
from app.strategy_engine.risk import engine as R


def test_size_for_basic():
    s = sizing.size_for(entry=100.0, stop=98.0, equity=1000.0, risk_pct=1.5, max_leverage=3.0)
    # risk $15 / stop distance 2 = 7.5 units; notional 750 <= 3000 cap
    assert math.isclose(s.size, 7.5)
    assert not s.capped
    assert math.isclose(s.notional, 750.0)


def test_size_for_leverage_cap_reduces_size():
    # tight stop would demand huge size; cap at 3x notional
    s = sizing.size_for(entry=100.0, stop=99.9, equity=1000.0, risk_pct=1.5, max_leverage=3.0)
    assert s.capped
    assert math.isclose(s.notional, 3000.0)      # exactly the 3x cap
    assert math.isclose(s.leverage, 3.0)


def test_can_open_max_concurrent():
    g = R.GlobalRisk(equity_usd=1000, open_positions=(R.OpenPos("s1", "BTC", "long"), R.OpenPos("s2", "ETH", "short")))
    ok, reason = R.can_open(g, R.StratRisk("s3"), "SOL", "long", now_ms=0)
    assert not ok and "concurrent" in reason


def test_can_open_no_duplicate_direction():
    g = R.GlobalRisk(equity_usd=1000, open_positions=(R.OpenPos("s1", "BTC", "long"),))
    ok, reason = R.can_open(g, R.StratRisk("s1"), "BTC", "long", now_ms=0)
    assert not ok and "duplicate" in reason
    ok2, _ = R.can_open(g, R.StratRisk("s1"), "BTC", "short", now_ms=0)
    assert ok2                                    # opposite direction allowed


def test_can_open_paper_paused_and_daily_cap():
    g = R.GlobalRisk(equity_usd=1000, daily_cap_hit_until=10_000)
    ok, reason = R.can_open(g, R.StratRisk("s1"), "BTC", "long", now_ms=5_000)
    assert not ok and "daily loss cap" in reason
    ok2, r2 = R.can_open(R.GlobalRisk(equity_usd=1000), R.StratRisk("s1", paper_paused=True, paused_reason="kill"), "BTC", "long", 0)
    assert not ok2 and r2 == "kill"


def test_register_exit_daily_cap_paper_pauses():
    g = R.GlobalRisk(equity_usd=1000.0)
    g2, s2, dem = R.register_exit(g, R.StratRisk("s1"), pnl_net=-45.0, now_ms=1_000_000_000_000)
    assert g2.daily_cap_hit_until is not None      # -45 <= -40 (4% of 1000)
    assert any("daily loss cap" in d for d in dem)


def test_register_exit_two_consecutive_losses_pauses_4h():
    g = R.GlobalRisk(equity_usd=1000.0, consecutive_losses=1)
    g2, _, dem = R.register_exit(g, R.StratRisk("s1"), pnl_net=-5.0, now_ms=1_000)
    assert g2.consecutive_losses == 2
    assert g2.paused_until == 1_000 + 4 * 3_600_000
    assert any("consecutive losses" in d for d in dem)


def test_register_exit_win_resets_consecutive():
    g = R.GlobalRisk(equity_usd=1000.0, consecutive_losses=1)
    g2, _, _ = R.register_exit(g, R.StratRisk("s1"), pnl_net=+3.0, now_ms=1_000)
    assert g2.consecutive_losses == 0


def test_rolling_pf_gate():
    assert R.rolling_pf(tuple([1.0] * 19)) is None            # < 20 trades
    losing = tuple([-1.0] * 15 + [1.0] * 5)                    # PF = 5/15 = 0.33
    pf = R.rolling_pf(losing)
    assert pf is not None and pf < 0.9
    g2, s2, dem = R.register_exit(R.GlobalRisk(equity_usd=1e9), R.StratRisk("s1", recent_pnls=losing[:-1]), pnl_net=1.0, now_ms=0)
    assert s2.paper_paused and any("PF" in d for d in dem)


def test_rearm_clears_pause():
    s = R.StratRisk("s1", paper_paused=True, paused_reason="PF low")
    s2 = R.rearm(s, "reviewed, re-arming")
    assert not s2.paper_paused and s2.paused_reason is None
