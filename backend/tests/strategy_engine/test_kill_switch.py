"""KILL-SWITCH SAFETY (prompt hard rule): with everything at effective_mode=off,
no code path can produce an OrderIntent that reaches anything, and Phase-1
effective_mode is 'off' under every input."""
import pathlib

from app.strategy_engine import mode
from app.strategy_engine.engine import StrategyEngine
from app.strategy_engine.events import CandleClosed, Trade, AssetCtx, Fill
from app.strategy_engine import seed


def test_engine_has_no_strategies_and_emits_no_intents():
    eng = StrategyEngine()                       # Phase 1: nothing registered
    assert eng.strategy_ids == []
    events = [
        CandleClosed("hl", "BTC", "15m", 1, 1, 2, 0.5, 1.5, 100),
        Trade("BTC", 1, 100.0, 0.1, "buy"),
        AssetCtx("BTC", 1, 1e9, 0.0001, 0.0001, 100.0, 100.0, 0.0),
        Fill("s01_liq_sweep", "BTC", 1, "long", 100.0, 0.1, "oid"),
    ]
    for ev in events:
        assert eng.on_event(ev) == []            # zero intents, always


def test_paper_is_paper_and_live_is_off_under_every_flag():
    # No gates: paper -> paper regardless of kill switch / daily cap / implemented
    # flags (those annotate only); off -> off; live -> off (no adapter).
    for flags in ({}, dict(kill_switch_tripped=True), dict(daily_cap_hit=True),
                  dict(strategy_implemented=False), dict(engine_enabled=False)):
        # D-100: kill switch and daily cap now GATE paper (they used to
        # annotate); every other flag still leaves paper as paper.
        expected = ("paper-paused"
                    if (flags.get("kill_switch_tripped") or flags.get("daily_cap_hit"))
                    else "paper")
        assert mode.compute_effective_mode("paper", **flags)[0] == expected, flags
        assert mode.compute_effective_mode("off", **flags)[0] == "off", flags
        eff, reason = mode.compute_effective_mode("live", live_allowlist_ok=True, **flags)
        assert eff == "off" and reason == mode.LIVE_DISABLED_REASON, flags


def test_live_cannot_engage_without_execution_adapter():
    # Even if a strategy were implemented and everything green, live is impossible
    # because there is no execution adapter in Phase 1.
    assert mode.HAS_EXECUTION_ADAPTER is False
    eff, reason = mode.compute_effective_mode(
        "live", engine_enabled=True, live_allowlist_ok=True,
        kill_switch_tripped=False, daily_cap_hit=False,
        strategy_implemented=True,
    )
    assert eff == "off"
    assert "execution adapter" in reason


def test_paper_build_implemented_but_live_impossible():
    # Phase-2 paper build: all strategies are implemented (paper), s06 has gated +
    # ungated. LIVE is STILL impossible because no execution adapter exists — the
    # real safety floor. STRATEGY_IMPLEMENTED covers exactly the seeded strategies.
    assert mode.HAS_EXECUTION_ADAPTER is False
    # D-99: s05c is the one deliberately-False entry (permanently disabled).
    assert all(v is True for k, v in mode.STRATEGY_IMPLEMENTED.items()
               if k not in mode.DISABLED_STRATEGIES)
    assert mode.STRATEGY_IMPLEMENTED["s05c_session_open"] is False
    assert set(mode.STRATEGY_IMPLEMENTED) == {sid for sid, _ in seed.STRATEGIES}
    # every implemented strategy, requested live, still resolves to off (no adapter)
    for sid in mode.STRATEGY_IMPLEMENTED:
        eff, reason = mode.compute_effective_mode(
            "live", engine_enabled=True, live_allowlist_ok=True, kill_switch_tripped=False,
            daily_cap_hit=False, strategy_implemented=True)
        assert eff == "off" and "execution adapter" in reason


def test_no_order_placement_code_in_strategy_engine_package():
    """Static guard: the strategy_engine package must not import or reference any
    order-placement / signing path (perplTrading, place_order, sign, gate ladder)."""
    pkg = pathlib.Path(__file__).resolve().parents[2] / "app" / "strategy_engine"
    banned = ("perplTrading", "place_order", "place_post_only", "hl_exec",
              "sign_order", "gate_ladder", "copyPosition", "eth_account")
    hits = []
    for py in pkg.rglob("*.py"):
        text = py.read_text(encoding="utf-8")
        for token in banned:
            if token in text:
                hits.append(f"{py.name}:{token}")
    assert hits == [], f"order-placement references found in strategy_engine: {hits}"
