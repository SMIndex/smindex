"""Auto-copy rules (overnight Part C, spec 2.4-2.8). Pure functions, no I/O.
Run: python tests/test_copy_auto_rules.py  (also collected by pytest)."""
import sys
from dataclasses import replace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services.copy_auto import rules as R  # noqa: E402


def _g(**kw):
    base = dict(side="long", kill_switch_on=True, allowlisted=True, live=True, market_id=1,
                markets_allowed=None, market_in_use=False, leader_px=100.0, perpl_px=100.2,
                drift_pct=0.5, basis_bps=5.0, open_positions=0, max_positions=3,
                daily_realized=0.0, total_realized=0.0, daily_loss_usd=30.0, total_loss_usd=100.0,
                free_margin=50.0, margin_needed=10.0, leverage=5.0, market_max_leverage=10.0,
                event_age_sec=1.0)
    base.update(kw)
    return R.GateInput(**base)


def test_all_gates_pass_and_values_recorded():
    gate, v = R.run_gates(_g())
    assert gate is None
    assert list(v) == ["g1_kill_switch_allowlist", "g3_market", "g4_slot", "g5_drift", "g6_basis",
                       "g7_positions", "g8_loss", "g9_margin", "g10_leverage", "g11_staleness"]


def test_each_gate_fails_in_spec_order():
    cases = [
        (dict(allowlisted=False), "g1_allowlist"),
        (dict(kill_switch_on=False), "g1_kill_switch"),
        (dict(market_id=0), "g3_not_listed_on_perpl"),
        (dict(markets_allowed=[20]), "g3_market_not_enabled"),
        (dict(market_in_use=True), "g4_market_already_in_use"),
        (dict(perpl_px=100.6), "g5_price_moved"),
        (dict(perpl_px=None), "g5_price_moved"),
        (dict(basis_bps=31.0), "g6_basis"),
        (dict(basis_bps=None), "g6_basis"),
        (dict(open_positions=3), "g7_max_positions"),
        (dict(daily_realized=-30.0), "g8_daily_loss"),
        (dict(total_realized=-100.0), "g8_total_loss"),
        (dict(free_margin=9.99), "g9_margin"),
        (dict(free_margin=None), "g9_margin"),
        (dict(leverage=11.0), "g10_leverage"),
        (dict(event_age_sec=10.5), "g11_too_late"),
    ]
    for kw, want in cases:
        got, _ = R.run_gates(_g(**kw))
        assert got == want, (kw, got, want)
    # earlier gate wins when two fail
    assert R.run_gates(_g(market_in_use=True, event_age_sec=99))[0] == "g4_market_already_in_use"


def test_shadow_ignores_kill_switch_but_not_allowlist():
    assert R.run_gates(_g(live=False, kill_switch_on=False))[0] is None
    assert R.run_gates(_g(live=False, allowlisted=False))[0] == "g1_allowlist"


def test_drift_direction():
    assert R.drift_ok("long", 100, 100.5, 0.5) and not R.drift_ok("long", 100, 100.51, 0.5)
    assert R.drift_ok("long", 100, 90, 0.5)             # better price is fine
    assert R.drift_ok("short", 100, 99.5, 0.5) and not R.drift_ok("short", 100, 99.49, 0.5)
    assert R.run_gates(_g(side="short", leader_px=100, perpl_px=99.4))[0] == "g5_price_moved"
    assert R.run_gates(_g(side="short", leader_px=100, perpl_px=100.9))[0] is None


def test_sizing_fixed_proportional_and_limits():
    s, m, why = R.size_open(sizing="fixed", margin_usd=10, allocation_usd=50, margin_in_use=0,
                            leverage=5, price=80000, leader_size=1, leader_account_value=None, size_decimals=5)
    assert why is None and s == 0.00062 and abs(m - 9.92) < 1e-6          # floor, never round up
    s, m, why = R.size_open(sizing="proportional", margin_usd=10, allocation_usd=50, margin_in_use=0,
                            leverage=5, price=100, leader_size=10, leader_account_value=1000, size_decimals=3)
    assert why is None and s == 0.5 and m == 10.0                          # 10 x 50/1000
    assert R.size_open(sizing="fixed", margin_usd=10, allocation_usd=50, margin_in_use=45, leverage=5,
                       price=100, leader_size=1, leader_account_value=None, size_decimals=3)[2] == "allocation fully used"
    assert R.size_open(sizing="fixed", margin_usd=5, allocation_usd=50, margin_in_use=0, leverage=1,
                       price=1e9, leader_size=1, leader_account_value=None, size_decimals=5)[2] == "below venue minimum"
    assert R.size_open(sizing="fixed", margin_usd=10, allocation_usd=50, margin_in_use=0, leverage=5,
                       price=100, leader_size=1, leader_account_value=None, size_decimals=3, min_scaled=600)[2] == "below venue minimum"
    assert R.size_open(sizing="proportional", margin_usd=10, allocation_usd=50, margin_in_use=0, leverage=5,
                       price=100, leader_size=1, leader_account_value=None, size_decimals=3)[2] == "leader account value unknown"


def test_leverage_lowest_of_three():
    assert R.choose_leverage(20, 5, 10) == (5.0, "")
    assert R.choose_leverage(3, 5, 10) == (3.0, "")
    assert R.choose_leverage(None, 5, 4)[0] == 4.0 and "unknown" in R.choose_leverage(None, 5, 4)[1]


def test_flip_split_and_normalize():
    acts = R.normalize("closed", "long", {"dir": "Long > Short", "sz": "3", "startPosition": "2", "px": "50"})
    assert [(a.kind, a.side, a.leader_size) for a in acts] == [("close", "long", 2.0), ("open", "short", 1.0)]
    acts = R.normalize("closed", "short", {"dir": "Short > Long", "sz": "2", "startPosition": "-2", "px": "50"})
    assert [(a.kind, a.side) for a in acts] == [("close", "short")]         # exact flat: no reopen
    a = R.normalize("increased", "long", {"sz": "1", "startPosition": "4", "px": "10"})[0]
    assert (a.kind, a.leader_size, a.leader_prev) == ("add", 1.0, 4.0)
    assert R.normalize("liquidated", "long", {}) == []


def test_fraction_mirroring_partial_fills():
    assert R.scaled_change(0.5, 3, 10, 3) == 0.15                # leader cut 30% -> we cut 30%
    assert R.scaled_change(0.5, 12, 10, 3) == 0.5                # capped at what we hold
    assert R.scaled_change(0.0, 1, 10, 3) == 0.0                 # nothing held (partial fill = real size)
    assert R.scaled_change(0.333, 1, 2, 3) == 0.166              # floors, never over-reduces


def test_stop_price():
    assert abs(R.stop_price("long", 100, 5, 50) - 90) < 1e-9     # 50% of margin at 5x = 10%
    assert abs(R.stop_price("short", 100, 5, 50) - 110) < 1e-9
    assert R.stop_price("long", 100, 5, None) is None


def test_auto_pause():
    kw = dict(consecutive_failures=0, daily_realized=0, total_realized=0, daily_loss_usd=30, total_loss_usd=100)
    assert R.pause_reason(**kw) is None
    assert "3 failed" in R.pause_reason(**{**kw, "consecutive_failures": 3})
    assert "daily" in R.pause_reason(**{**kw, "daily_realized": -30})
    assert "total" in R.pause_reason(**{**kw, "total_realized": -100})
    assert "key" in R.pause_reason(**kw, key_rejected=True)
    assert "liquidated" in R.pause_reason(**kw, leader_liquidated=True)


def test_liquidation_only_when_leader_is_the_liquidated_user():
    me = "0xabc0000000000000000000000000000000000001"
    other = {"liquidation": {"liquidatedUser": "0x4cf1de13c21f254c5040c86d81e4c7825d1757c3", "method": "market"}}
    mine = {"liquidation": {"liquidatedUser": me.upper(), "method": "market"}}
    assert not R.leader_was_liquidated(other, me)       # leader was the counterparty (real 2026-09-28 fills)
    assert R.leader_was_liquidated(mine, me)
    assert not R.leader_was_liquidated({}, me) and not R.leader_was_liquidated({"liquidation": None}, me)


def test_presets_and_summary():
    assert R.PRESETS["careful"]["margin_usd"] == 5 and R.PRESETS["active"]["max_positions"] == 5
    s = R.summary_sentence({**R.DEFAULTS})
    assert s == ("Copies opens with $10 at up to 5x, max 3 positions, stop at 50% of margin, "
                 "stops for the day after -$30."), s


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("PASS", name)
    print("ALL AUTO-COPY RULE TESTS PASS")
