"""Spec v1.1 Part D.6 (D-68) — replay-only unavailable handling: a reason whose
feed is missing leaves BOTH sums of raw conviction, a veto whose feed is missing
is skipped and logged as unevaluated, and the live path (no `unavailable`) is
byte-for-byte the same decision."""
from __future__ import annotations

import pytest

from app.strategy_engine.mind.base import Mind, Reason, Veto, Decision
from app.strategy_engine.strategies import feed_inputs as fi
from app.strategy_engine.strategies.m1_sweep_reclaim import M1SweepReclaim
from app.strategy_engine.strategies.m2_bos_order_block import M2BreakOfStructure
from app.strategy_engine.strategies.m3_failed_auction import M3FailedAuction
from app.strategy_engine.strategies.m4_htf_choch import M4ChangeOfCharacter
from app.strategy_engine.strategies.m5_session_liquidity_run import M5SessionLiquidityRun
from app.strategy_engine.strategies.m6_weekly_open_reclaim import M6WeeklyOpenReclaim

from . import test_models_m1_m6 as T
from .model_fixtures import with_setup

MODELS = {"M1": M1SweepReclaim, "M2": M2BreakOfStructure, "M3": M3FailedAuction,
          "M4": M4ChangeOfCharacter, "M5": M5SessionLiquidityRun, "M6": M6WeeklyOpenReclaim}


class _Snap:
    def __init__(self, unavailable=None):
        self.unavailable = set(unavailable or ())
        self.setup = {}
        self.direction = "long"
        self.day_type = "range"
        self.model_state = {}


def _mind():
    return Mind("MX",
                [Reason("a", "", lambda s: 1.0, 2.0, inputs=("oi",)),
                 Reason("b", "", lambda s: 0.5, 1.0),
                 Reason("c", "", lambda s: 0.0, 1.0, inputs=("taker",))],
                [Veto("v_oi", "needs oi", lambda s: True, inputs=("oi",)),
                 Veto("v_plain", "plain", lambda s: False)],
                [], [])


def test_excluded_reason_leaves_both_sums():
    m = _mind()
    live = m.evaluate(_Snap())
    # live: (1*2 + .5*1 + 0*1) / 4 = 0.625, vetoed by v_oi
    assert live.raw_conviction == pytest.approx(0.625) and live.vetoes_hit == ["v_oi"]
    assert live.excluded == [] and live.unevaluated == []
    rep = m.evaluate(_Snap({"oi"}))
    # oi gone: reason a leaves numerator AND denominator → (.5*1 + 0) / 2 = 0.25 ; v_oi not evaluated
    assert rep.raw_conviction == pytest.approx(0.25)
    assert rep.excluded == [("a", 2.0, ("oi",))]
    assert rep.unevaluated == [("v_oi", ("oi",))] and rep.vetoes_hit == []
    assert rep.effective_max_weight() == pytest.approx(2.0)
    assert live.effective_max_weight() == pytest.approx(4.0)


def test_unavailable_is_never_scored_as_zero():
    m = _mind()
    rep = m.evaluate(_Snap({"oi", "taker"}))
    assert [k for k, _, _ in rep.reasons] == ["b"]           # a and c both gone, not present at 0
    assert rep.raw_conviction == pytest.approx(0.5)


def test_json_carries_exclusions_and_unevaluated_vetoes():
    rep = _mind().evaluate(_Snap({"oi"}))
    rj = {r["key"]: r for r in rep.reasons_json()}
    assert rj["a"]["weight"] == 0.0 and rj["a"]["excluded_weight"] == 2.0 and "unavailable" in rj["a"]["note"]
    assert rj["b"]["contribution"] == pytest.approx(0.25)     # contribution uses the reduced denominator
    vj = {v["key"]: v for v in rep.vetoes_json()}
    assert vj["v_oi"]["unevaluated"] is True


def test_feed_map_keys_exist_on_each_model():
    for code, cls in MODELS.items():
        mind = cls().mind
        rk = {r.key for r in mind.reasons}
        vk = {v.key for v in mind.vetoes}
        assert set(fi.REASON_INPUTS[code]) <= rk, (code, set(fi.REASON_INPUTS[code]) - rk)
        assert set(fi.VETO_INPUTS[code]) <= vk, (code, set(fi.VETO_INPUTS[code]) - vk)
        for r in mind.reasons:
            assert r.inputs == tuple(fi.REASON_INPUTS[code].get(r.key, ()))
        for v in mind.vetoes:
            assert v.inputs == tuple(fi.VETO_INPUTS[code].get(v.key, ()))


def test_m1_live_decision_unchanged_and_replay_excludes_cohort():
    m = M1SweepReclaim()
    _, snap = T.m1_scenario()
    setup, _ = m.find_setup(with_setup(snap, {}))
    live = m.mind.evaluate(with_setup(snap, setup))
    assert live.excluded == [] and live.unevaluated == []
    rep = m.mind.evaluate(with_setup(snap, setup, unavailable={"cohort", "events"}))
    assert [k for k, _, _ in rep.excluded] == ["cohort"]
    assert [k for k, _ in rep.unevaluated] == ["event_30m"]
    scored = {k: (s, w) for k, s, w in live.reasons}
    for k, s, w in rep.reasons:                              # every remaining reason scores identically
        assert scored[k] == (s, w)
    assert rep.effective_max_weight() == pytest.approx(live.effective_max_weight() - scored["cohort"][1])
