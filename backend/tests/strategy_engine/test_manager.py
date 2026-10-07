"""Phase 2 — paper trade lifecycle arithmetic + risk demotion wiring (pure)."""
from app.strategy_engine.execution.manager import compute_close, _entry_side
from app.strategy_engine.execution.paper import MAKER_FEE, TAKER_FEE
from app.strategy_engine import mode as mode_mod
from app.strategy_engine.risk import engine as risk


def test_entry_side():
    assert _entry_side("long") == "buy"
    assert _entry_side("short") == "sell"


def test_close_long_target_is_maker_and_profitable():
    # long 100 -> 110, size 1: gross +10, both legs maker
    g, fees, net = compute_close("long", 100.0, 110.0, 1.0, "target")
    assert g == 10.0
    assert fees == MAKER_FEE * (100.0 + 110.0)          # entry maker + target maker
    assert net == 10.0 - fees


def test_close_long_stop_is_taker_and_loss():
    # long 100 -> 95, size 2: gross -10, exit is taker (stop)
    g, fees, net = compute_close("long", 100.0, 95.0, 2.0, "stop")
    assert g == -10.0
    assert fees == MAKER_FEE * 100.0 * 2 + TAKER_FEE * 95.0 * 2
    assert net == g - fees


def test_close_short_direction_sign():
    # short 100 -> 90, size 1: price fell -> short profits +10
    g, _f, net = compute_close("short", 100.0, 90.0, 1.0, "target")
    assert g == 10.0
    assert net < g                                       # fees drag


def test_close_time_stop_exit_is_taker():
    _g, fees_ts, _n = compute_close("long", 100.0, 101.0, 1.0, "time_stop")
    _g2, fees_tg, _n2 = compute_close("long", 100.0, 101.0, 1.0, "target")
    assert fees_ts > fees_tg                              # time_stop exit taker > target maker


def test_paper_kill_switch_annotates_only():  # D-100: now ENFORCES, name kept for history
    """A PF<0.9 / kill-switch outcome on a PAPER strategy is an annotation: the
    strategy stays paper and keeps evaluating + filling (no paused state)."""
    eff, reason = mode_mod.compute_effective_mode(
        "paper", engine_enabled=True, live_allowlist_ok=True,
        kill_switch_tripped=True, daily_cap_hit=True, strategy_implemented=True)
    # D-100: a tripped kill switch is ENFORCED in paper now (was: annotate-only)
    assert eff == "paper-paused" and "re-arm" in reason


def test_live_kill_switch_still_off():
    from app.strategy_engine.mode import compute_effective_mode
    """D-100: renamed contract. A tripped kill switch used to be ignored in paper
    (this asserted `off` only because `live` is unreachable). It now produces
    `paper-paused`: evaluation continues, entries are blocked, and only a manual
    re-arm clears it. The old assertion is kept below as the live-adapter check."""
    eff, reason = compute_effective_mode("paper", kill_switch_tripped=True)
    assert eff == "paper-paused", eff
    assert "re-arm" in reason
    # live remains impossible while no execution adapter exists
    eff_live, reason_live = compute_effective_mode("live")
    assert eff_live == "off" and "execution adapter" in reason_live

def test_can_open_blocks_when_paper_paused():
    g = risk.GlobalRisk(equity_usd=1000.0)
    sr = risk.StratRisk("s05_session_open", paper_paused=True, paused_reason="paper-paused")
    ok, reason = risk.can_open(g, sr, "BTC", "long", now_ms=0)
    assert not ok and "paused" in reason.lower()


def test_can_open_blocks_duplicate_direction():
    g = risk.GlobalRisk(equity_usd=1000.0,
                        open_positions=(risk.OpenPos("s05_session_open", "BTC", "long"),))
    sr = risk.StratRisk("s05_session_open")
    ok, reason = risk.can_open(g, sr, "BTC", "long", now_ms=0)
    assert not ok and "duplicate" in reason.lower()
    ok2, _ = risk.can_open(g, sr, "BTC", "short", now_ms=0)   # opposite dir on same asset ok
    assert ok2


def test_register_exit_daily_cap_trips_at_4pct():
    g = risk.GlobalRisk(equity_usd=1000.0)
    sr = risk.StratRisk("s05_session_open")
    g2, _s2, demotions = risk.register_exit(g, sr, pnl_net=-45.0, now_ms=1_000_000)
    assert g2.daily_cap_hit_until is not None
    assert any("daily loss cap" in d for d in demotions)
