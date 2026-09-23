"""D-100/D-103 — the risk engine blocks paper entries, and impossible order
geometry is refused. Pure-function tests: no DB, no network.

These are the regression tests for the two defects the 2026-09-10 status report
found: a strategy with a 26-trade losing run kept opening paper positions, and a
long trade was written with its target below its own entry.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.strategy_engine.risk import engine as risk           # noqa: E402
from app.strategy_engine.execution.manager import (            # noqa: E402
    check_order_geometry,
)
from app.strategy_engine import mode                            # noqa: E402

NOW = 1_789_000_000_000


# ---------------------------------------------------------------- part 2 ---
def test_twenty_losses_trips_the_kill_switch_and_blocks_a_paper_entry():
    """The headline regression: 20 losing trades in the rolling window must make
    the next paper entry impossible."""
    g = risk.GlobalRisk(equity_usd=10_000.0)
    s = risk.StratRisk("s05_session_open")

    # feed 20 consecutive losses through the same path a real close takes
    for _ in range(20):
        g, s, _demotions = risk.register_exit(g, s, pnl_net=-10.0, now_ms=NOW)

    pf = risk.rolling_pf(s.recent_pnls)
    assert pf == 0.0, f"20 losses must give PF 0.0, got {pf}"
    assert s.paper_paused is True, "the kill switch must be tripped"

    allowed, why = risk.can_open(g, s, "BTC", "long", NOW)
    assert allowed is False, "a paper entry must be BLOCKED after 20 losses"
    assert "PF" in why or "paused" in why.lower()


def test_a_healthy_strategy_can_still_open():
    g = risk.GlobalRisk(equity_usd=10_000.0)
    s = risk.StratRisk("s01_liq_sweep")
    for _ in range(20):
        g, s, _ = risk.register_exit(g, s, pnl_net=+10.0, now_ms=NOW)
    allowed, why = risk.can_open(g, s, "BTC", "long", NOW)
    assert allowed is True, f"a winning strategy must still open: {why}"


def test_daily_loss_cap_blocks():
    g = risk.GlobalRisk(equity_usd=10_000.0, daily_cap_hit_until=NOW + 3_600_000)
    s = risk.StratRisk("s02_funding_flow")
    allowed, why = risk.can_open(g, s, "ETH", "short", NOW)
    assert allowed is False and "daily loss cap" in why


def test_consecutive_loss_pause_blocks():
    g = risk.GlobalRisk(equity_usd=10_000.0, paused_until=NOW + 60_000)
    s = risk.StratRisk("s02_funding_flow")
    allowed, why = risk.can_open(g, s, "ETH", "short", NOW)
    assert allowed is False and "consecutive losses" in why


def test_max_concurrent_positions_blocks():
    g = risk.GlobalRisk(
        equity_usd=10_000.0,
        open_positions=(risk.OpenPos("a", "BTC", "long"), risk.OpenPos("b", "ETH", "short")),
    )
    s = risk.StratRisk("s03_whale_follow")
    allowed, why = risk.can_open(g, s, "SOL", "long", NOW)
    assert allowed is False and "concurrent" in why


def test_rearm_clears_the_switch():
    s = risk.StratRisk("s05_session_open", paper_paused=True, paused_reason="PF 0.04 < 0.9")
    s2 = risk.rearm(s, "reviewed the losing run, re-enabling")
    assert s2.paper_paused is False and s2.paused_reason is None


# ---------------------------------------------------------------- part 1 ---
def test_s05c_is_disabled_and_never_paper():
    assert mode.is_disabled("s05c_session_open") is True
    eff, reason = mode.compute_effective_mode("paper", strategy_id="s05c_session_open")
    assert eff == "off" and "undocumented" in reason


def test_kill_switch_is_enforced_by_effective_mode():
    eff, reason = mode.compute_effective_mode("paper", kill_switch_tripped=True)
    assert eff == "paper-paused" and "re-arm" in reason
    eff2, _ = mode.compute_effective_mode("paper", daily_cap_hit=True)
    assert eff2 == "paper-paused"
    eff3, _ = mode.compute_effective_mode("paper")
    assert eff3 == "paper"


# ---------------------------------------------------------------- part 5 ---
def test_target_below_a_long_entry_is_refused():
    """Exactly the live defect: trade #10, entry 2541.25, T1 2541.20."""
    d = check_order_geometry("long", 2541.25, 2526.12, 2541.20)
    assert d is not None and "not above the long entry" in d


def test_target_above_a_short_entry_is_refused():
    d = check_order_geometry("short", 2504.25, 2511.75, 2505.00)
    assert d is not None and "not below the short entry" in d


def test_stop_on_the_wrong_side_is_refused():
    assert check_order_geometry("long", 100.0, 101.0, 110.0) is not None
    assert check_order_geometry("short", 100.0, 99.0, 90.0) is not None


def test_good_geometry_passes():
    assert check_order_geometry("long", 100.0, 95.0, 110.0) is None
    assert check_order_geometry("short", 100.0, 105.0, 90.0) is None
    assert check_order_geometry("long", 100.0, None, None) is None


if __name__ == "__main__":
    import traceback
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    bad = 0
    for fn in fns:
        try:
            fn()
            print(f"  PASS {fn.__name__}")
        except Exception:
            bad += 1
            print(f"  FAIL {fn.__name__}")
            traceback.print_exc()
    print(f"\n{len(fns) - bad}/{len(fns)} passed")
    sys.exit(1 if bad else 0)
