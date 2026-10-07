"""AUDIT (2026-09-06) Parts 6 and 7 — Mind math, wiring and paper-execution rules
checked against docs 10–16 with hand-built inputs. Complements test_mind.py
(which uses interior values) with the exact boundaries and the doc constants."""
from __future__ import annotations

import re

import pytest

from app.strategy_engine.mind.base import (FULL_RISK_PCT, HALF_RISK_PCT, ContextRule, InTradeCheck, Mind,
                                           Position, Reason, Veto)
from app.strategy_engine.mind.config import common
from app.strategy_engine.risk.sizing import size_for
from app.strategy_engine.strategies import model_base as mb
from app.strategy_engine.strategies.m1_sweep_reclaim import M1SweepReclaim
from app.strategy_engine.strategies.m2_bos_order_block import M2BreakOfStructure
from app.strategy_engine.strategies.m3_failed_auction import M3FailedAuction
from app.strategy_engine.strategies.m4_htf_choch import M4ChangeOfCharacter
from app.strategy_engine.strategies.m5_session_liquidity_run import M5SessionLiquidityRun, MIN_MS
from app.strategy_engine.strategies.m5_session_liquidity_run import HARD_STOP_MIN as M5_HARD_STOP_MIN
from app.strategy_engine.strategies.m6_weekly_open_reclaim import M6WeeklyOpenReclaim, friday_20

from . import test_models_m1_m6 as T
from .model_fixtures import with_setup


class Snap:
    def __init__(self, **kw):
        self.__dict__.update(kw)
        self.extra_vetoes = kw.get("extra_vetoes", [])


def mind(reasons, rules=(), vetoes=(), checks=()):
    return Mind("MX", reasons=list(reasons), vetoes=list(vetoes), rules=list(rules), checks=list(checks))


def const_reasons(*pairs):
    return [Reason(f"r{i}", "", (lambda v: (lambda s: v))(s_), w) for i, (s_, w) in enumerate(pairs)]


# ---------------------------------------------------------------- Part 6: Mind math
def test_raw_is_weighted_mean_of_strength_times_weight():
    m = mind(const_reasons((0.9, 2.0), (0.4, 1.0), (1.0, 0.5), (0.0, 1.5)))
    d = m.evaluate(Snap())
    assert d.raw_conviction == pytest.approx((0.9 * 2.0 + 0.4 * 1.0 + 1.0 * 0.5 + 0.0 * 1.5) / 5.0)  # 0.54
    assert d.conviction == d.raw_conviction  # no multipliers → identity
    assert [w for _, _, w in d.reasons] == [2.0, 1.0, 0.5, 1.5]


def test_multipliers_are_multiplied_not_added():
    m = mind(const_reasons((1.0, 1.0)), rules=[ContextRule("a", lambda s: 1.1), ContextRule("b", lambda s: 0.9),
                                                ContextRule("c", lambda s: 0.7)])
    d = m.evaluate(Snap())
    assert d.raw_conviction == 1.0
    assert d.conviction == pytest.approx(1.1 * 0.9 * 0.7, abs=1e-4)          # 0.693
    assert d.conviction != pytest.approx(1.0 + 0.1 - 0.1 - 0.3, abs=1e-4)     # additive would be 0.7
    assert d.multipliers == [("a", 1.1), ("b", 0.9), ("c", 0.7)]


def test_veto_forces_take_false_regardless_of_conviction():
    m = mind(const_reasons((1.0, 1.0)), vetoes=[Veto("v", "text", lambda s: True)])
    d = m.evaluate(Snap())
    assert d.conviction == 1.0 and d.take is False and d.size_tier == "none" and d.vetoes_hit == ["v"]
    assert d.risk_pct == 0.0


@pytest.mark.parametrize("value,tier,take", [
    (0.5499, "none", False), (0.55, "half", True), (0.6999, "half", True), (0.70, "full", True), (1.0, "full", True),
])
def test_size_tiers_switch_at_exactly_055_and_070(value, tier, take):
    d = mind(const_reasons((value, 1.0))).evaluate(Snap())
    assert d.size_tier == tier and d.take is take, d


def test_size_tier_maps_to_075_and_15_percent_risk_in_size_for():
    half = mind(const_reasons((0.60, 1.0))).evaluate(Snap())
    full = mind(const_reasons((0.80, 1.0))).evaluate(Snap())
    assert (HALF_RISK_PCT, FULL_RISK_PCT) == (0.75, 1.5)
    assert half.risk_pct == 0.75 and full.risk_pct == 1.5
    sh = size_for(100.0, 99.0, 10_000.0, risk_pct=half.risk_pct, max_leverage=3.0)
    sf = size_for(100.0, 99.0, 10_000.0, risk_pct=full.risk_pct, max_leverage=3.0)
    assert sh.risk_dollars == 75.0 and sh.size == pytest.approx(75.0)
    assert sf.risk_dollars == 150.0 and sf.size == pytest.approx(150.0)


def _pos(hold=90, now_min=0.0, entry=100.0, stop=99.0):
    return Position(1, "MX", "BTC", "long", entry, stop, None, 1.0, 0, abs(entry - stop), hold,
                    now_ms=int(now_min * 60_000))


def test_counts2_check_alone_exits_on_two_consecutive_candles():
    m = mind(const_reasons((1.0, 1.0)), checks=[InTradeCheck("c1", "", lambda s, p: s.c1),
                                                 InTradeCheck("c2", "", lambda s, p: s.c2, counts=2)])
    assert m.exit_threshold == 2
    p = _pos()
    r1 = m.manage(Snap(c1=False, c2=True), p, 100.0)
    assert (r1.action, r1.fail_count, r1.streak) == ("hold", 2, 1)
    r2 = m.manage(Snap(c1=False, c2=True), p, 100.0)
    assert (r2.action, r2.reason, r2.streak) == ("exit", "thesis_failed", 2)
    # a counts=1 check alone never reaches the threshold
    q = _pos()
    for _ in range(5):
        r = m.manage(Snap(c1=True, c2=False), q, 100.0)
        assert r.action == "hold" and r.streak == 0
    # a good candle between two bad ones resets the streak
    z = _pos()
    m.manage(Snap(c1=False, c2=True), z, 100.0)
    m.manage(Snap(c1=False, c2=False), z, 100.0)
    assert m.manage(Snap(c1=False, c2=True), z, 100.0).action == "hold"


def test_dead_trade_fires_at_15x_hold_with_progress_below_half_r():
    m = mind(const_reasons((1.0, 1.0)))
    assert m.dead_mult == 1.5
    assert m.manage(Snap(), _pos(90, now_min=134.9), 100.4).action == "hold"
    r = m.manage(Snap(), _pos(90, now_min=135.0), 100.49)
    assert (r.action, r.reason) == ("exit", "dead_trade") and r.progress_r == pytest.approx(0.49)
    assert m.manage(Snap(), _pos(90, now_min=135.0), 100.5).action == "hold"      # 0.5R is not < 0.5R
    assert m.manage(Snap(), _pos(90, now_min=1000.0), 101.0).action == "hold"


# ---------------------------------------------------------------- Part 6: doc constants per model
MODELS = {"M1": M1SweepReclaim, "M2": M2BreakOfStructure, "M3": M3FailedAuction,
          "M4": M4ChangeOfCharacter, "M5": M5SessionLiquidityRun, "M6": M6WeeklyOpenReclaim}

DOC_WEIGHTS = {  # starting weights, docs 11–16 §Reasons tables
    "M1": {"location": 2.0, "reclaim": 2.0, "fuel": 1.5, "cleared": 1.2, "delta_flip": 1.5, "absorption": 1.0,
           "discount": 1.2, "session": 0.8, "cohort": 1.0, "htf_bias": 1.0},
    "M2": {"oi_new_positioning": 2.0, "displacement": 1.8, "zone_discount": 1.5, "retrace_calm": 1.2,
           "oi_holding": 1.5, "delta_break": 1.2, "funding_young": 0.8, "htf_agree": 1.2, "nested_sweep": 1.5,
           "cluster_cleared": 0.8},
    "M3": {"trap": 2.0, "cvd_divergence": 2.0, "location": 1.8, "failure_quality": 1.2, "thin_bids": 1.0,
           "cluster_fuel": 1.2, "funding_up": 0.8, "premium": 1.0, "second_failure": 1.5, "day_type_fit": 1.0},
    "M4": {"cohort_reducing": 2.5, "oi_extreme": 2.0, "funding_elevated": 1.5, "choch_quality": 1.5,
           "weak_bounce": 1.8, "extension": 1.0, "zone_quality": 1.2, "cluster_reward": 1.0, "delta_flip": 1.0,
           "divergence": 1.2},
    "M5": {"bias_alignment": 2.0, "range_quality": 1.5, "reclaim": 1.8, "fuel": 1.3, "cleared": 1.0,
           "delta_flip": 1.3, "early_in_window": 1.0, "open_location": 1.0, "no_pre_drift": 1.2, "cohort": 0.8},
    "M6": {"loss_depth": 1.8, "oi_commitment": 2.0, "delta_reclaim": 1.5, "funding_room": 1.2, "cohort": 1.8,
           "reward": 1.2, "timing": 1.0, "htf_bias": 1.5, "reclaim_quality": 1.0, "pwh_untested": 0.8},
}
DOC_CHECKS = {  # in-trade checks and which count as 2 (docs 11–16 §In-trade checks)
    "M1": {"no_higher_low": 1, "oi_bleeding": 1, "delta_negative": 1, "level_lost": 2},
    "M2": {"below_ob": 2, "oi_dropping": 1, "no_progress": 1, "delta_selling": 1},
    "M3": {"re_approach_with_oi": 2, "cvd_recovering": 1, "no_progress": 1, "close_above_level": 2},
    "M4": {"higher_high_1h": 2, "oi_rebuild": 1, "cohort_flip": 1, "no_progress": 1},
    "M5": {"below_asia_low": 2, "no_higher_low": 1, "oi_bleeding": 1, "delta_negative": 1},
    "M6": {"lost_weekly_open": 2, "oi_dropping": 1, "cohort_flip": 1, "no_progress": 1},
}
DOC_HOLD = {"M1": (90, 240), "M2": (180, 480), "M3": (120, 360), "M4": (480, 2160), "M5": (90, None), "M6": (1440, None)}


@pytest.mark.parametrize("mid", sorted(MODELS))
def test_mind_weights_equal_doc_starting_weights(mid):
    m = MODELS[mid]()
    assert m.mind.weights() == DOC_WEIGHTS[mid]
    assert [r.key for r in m.mind.reasons] == list(DOC_WEIGHTS[mid])          # same keys, same order as the doc


@pytest.mark.parametrize("mid", sorted(MODELS))
def test_in_trade_checks_counting_and_exit_threshold(mid):
    m = MODELS[mid]()
    assert {c.key: c.counts for c in m.mind.checks} == DOC_CHECKS[mid]
    assert m.mind.exit_threshold == 2
    hold, hard = DOC_HOLD[mid]
    assert m.expected_hold_min == hold
    if hard is not None:
        assert m.hard_stop_min == hard


def test_learning_is_disabled():
    cfg = common()
    assert cfg["learning_enabled"] is False
    assert cfg["learning_start_after_days"] == 60
    assert (cfg["conviction_skip_below"], cfg["conviction_full_from"], cfg["exit_threshold"],
            cfg["dead_trade_hold_multiple"], cfg["partial_at_t1_pct"]) == (0.55, 0.70, 2, 1.5, 40)


# ---------------------------------------------------------------- valid scenarios (doc-13 M3 = 50% partial)
def _valid():
    _, s1, *_ = T.m1_scenario()
    _, s2, *_ = T.m2_scenario()
    _, s3, *_ = T.m3_scenario()
    _, s4, *_ = T.m4_scenario()
    _, s5, *_ = T.m5_scenario()
    _, s6, *_ = T.m6_scenario()
    return {"M1": s1, "M2": s2, "M3": s3, "M4": s4, "M5": s5, "M6": s6}


@pytest.mark.parametrize("mid", sorted(MODELS))
def test_thesis_is_populated_with_no_unfilled_placeholders(mid):
    snap = _valid()[mid]
    ev = T.run(MODELS[mid](), snap)
    assert ev.decision.take is True, (ev.decision.vetoes_hit, ev.waiting_for)
    th = ev.decision.thesis
    assert th and len(th) > 40
    assert not re.search(r"\{[a-zA-Z_][^}]*\}", th), th                         # no {placeholder} left
    assert "None" not in th, th


# ---------------------------------------------------------------- Part 7: paper execution rules
DOC_PARTIAL = {"M1": 40.0, "M2": 40.0, "M3": 50.0, "M4": 40.0, "M5": 40.0, "M6": 40.0}
# M2/M5: docs 12/15 trail INSTEAD of T2 only on a trend day in the trade direction (fixtures are non-trend days →
# T2 closes the remainder: "t2" / "never"); the trend-day variant is "t1" (TRAIL_REPLACES_T2 in the runner).
DOC_TRAIL = {"M1": ("15m", "near_t2"), "M2": ("15m", "t2"), "M3": ("15m", "never"), "M4": ("1h", "t2"),
             "M5": ("15m", "never"), "M6": ("4h", "t1")}


@pytest.mark.parametrize("mid", sorted(MODELS))
def test_intent_partial_pct_trail_and_hard_stop(mid):
    snap = _valid()[mid]
    ev = T.run(MODELS[mid](), snap)
    it = ev.intent
    assert it is not None
    assert it.partial_pct == DOC_PARTIAL[mid]
    assert (it.trail_tf, it.trail_after) == DOC_TRAIL[mid], (it.trail_tf, it.trail_after)
    if mid == "M5":
        assert it.hard_stop_ts == ev.setup["window_end"] + M5_HARD_STOP_MIN * MIN_MS and M5_HARD_STOP_MIN == 120
    elif mid == "M6":
        assert it.hard_stop_ts == friday_20(snap.now_ms)
        assert (it.hard_stop_ts // 3_600_000) % 24 == 20 and ((it.hard_stop_ts // 86_400_000) + 3) % 7 == 4  # Fri 20:00
    else:
        assert it.hard_stop_ts == 0 and MODELS[mid]().hard_stop_min == DOC_HOLD[mid][1]  # runner adds hard_stop_min
    # stop and targets on the right side of the entry
    sgn = 1 if it.direction == "long" else -1
    assert (it.entry_px - it.stop_px) * sgn > 0
    assert it.t1 is not None and (it.t1 - it.entry_px) * sgn > 0


def test_m5_one_attempt_per_window_per_coin():
    _, snap, _ = T.m5_scenario()
    m = M5SessionLiquidityRun()
    setup, _ = m.find_setup(snap)
    key = f"m5:{mb.day_key(snap.now_ms)}:{setup['window']}"
    assert setup["already_taken"] is False
    taken = with_setup(snap, {}, model_state={key: {"ts": snap.now_ms}}, extra_vetoes=[])
    ev = m.evaluate(taken)
    T.assert_veto(ev, "already_taken")
    other = with_setup(snap, {}, model_state={f"m5:{mb.day_key(snap.now_ms)}:newyork": 1}, extra_vetoes=[])
    T.assert_take(m.evaluate(other), "long")                                    # another window does not block


def test_m6_one_attempt_per_week_per_coin_per_direction():
    _, snap = T.m6_scenario()
    m = M6WeeklyOpenReclaim()
    setup, _ = m.find_setup(snap)
    wk = mb.week_key(snap.now_ms)
    assert setup["week_key"] == f"m6:{wk}:long" and setup["already_taken"] is False
    st = dict(snap.model_state)
    st[f"m6:{wk}:long"] = {"ts": snap.now_ms}
    T.assert_veto(m.evaluate(with_setup(snap, {}, model_state=st, extra_vetoes=[])), "already_taken")
    st2 = dict(snap.model_state)
    st2[f"m6:{wk}:short"] = {"ts": snap.now_ms}                                  # opposite direction does not block
    T.assert_take(m.evaluate(with_setup(snap, {}, model_state=st2, extra_vetoes=[])), "long")
    st3 = dict(snap.model_state)
    st3[f"m6:{mb.week_key(snap.now_ms - 7 * 86_400_000)}:long"] = 1              # last week does not block
    T.assert_take(m.evaluate(with_setup(snap, {}, model_state=st3, extra_vetoes=[])), "long")


def test_m1_m2_m3_m4_have_no_one_attempt_veto():
    """Docs 11–14 define no one-attempt rule (M1 repeats are graded by `third_sweep`, M3 by the failures
    multiplier). The audit removed M1's MAX_PER_DAY and M2's taken_zones gate (D-42, D-44)."""
    for mid in ("M1", "M2", "M3", "M4"):
        assert "already_taken" not in [v.key for v in MODELS[mid]().mind.vetoes], mid


def test_m2_late_day_veto_is_symmetric_around_midnight():
    """Doc 12 §6: 'within 60 min of 00:00 UTC' — both sides (D-52)."""
    _, snap = T.m2_scenario()
    m = M2BreakOfStructure()
    setup, _ = m.find_setup(snap)
    late = next(v for v in m.mind.vetoes if v.key == "late_day")
    ds = mb.day_start(snap.now_ms)
    wed = ds + ((2 - mb.weekday(ds)) % 7) * 86_400_000                          # a Wednesday, not Friday
    for off_min, hit in ((30, True), (23 * 60 + 30, True), (90, False), (22 * 60 + 30, False)):
        s = with_setup(snap, setup, now_ms=wed + off_min * 60_000)
        assert bool(late.detector(s)) is hit, (off_min, hit)


def test_m5_stop_sits_beyond_the_sweep_wick_not_the_level():
    """Doc 15 §4 stop: '0.15 ATR beyond the raid wick' (D-60)."""
    _, snap, a_lo = T.m5_scenario()
    m = M5SessionLiquidityRun()
    setup, missing = m.find_setup(snap)
    assert setup is not None, missing
    assert setup["wick_price"] < a_lo
    ev = T.run(m, snap)
    assert ev.intent.stop_px < setup["wick_price"]
    assert abs((setup["wick_price"] - ev.intent.stop_px) - 0.15 * snap.atr("15m")) < 1e-6


def test_volume_2h_ratio_is_defined_a_few_seconds_after_the_boundary():
    """Doc 10 §3 no_trade needs `vol_2h_ratio`; the worker evaluates ~5–20 s after the 15m boundary and the
    unaligned 2h window dropped the 8th candle, so the ratio was always None on prod (D-62)."""
    from app.strategy_engine.structure.candles import Candle
    from app.strategy_engine.structure.day_type import volume_2h_ratio
    step = 15 * MIN_MS
    t0 = 1_788_609_600_000                                                       # 2026-09-05 00:00 UTC
    c15 = [Candle(t0 + (i + 1) * step - 1, 100.0, 101.0, 99.0, 100.0, 10.0) for i in range(96 * 21)]
    boundary = c15[-1].ts + 1
    for late in (0, 5_000, 19_000):
        r = volume_2h_ratio(c15, boundary + late)
        assert r is not None and abs(r - 1.0) < 1e-9, (late, r)


@pytest.mark.parametrize("mid", sorted(MODELS))
def test_every_reason_detector_runs_without_raising_on_a_valid_setup(mid):
    """Mind.evaluate reads a raising detector as strength 0 (never a crash), which silently turned M5
    `reclaim` (weight 1.8) into a constant 0 — the bare `mb.reclaim_strength` was handed the Snapshot
    instead of `setup` (D-63). Call every detector directly so a fault is a test failure, not a 0."""
    snap = _valid()[mid]
    m = MODELS[mid]()
    setup, missing = m.find_setup(snap)
    assert setup is not None, missing
    s = with_setup(snap, setup)
    for r in m.mind.reasons:
        r.detector(s)                                                             # must not raise
    for v in m.mind.vetoes:
        v.detector(s)
    if mid == "M5":
        st = next(r for r in m.mind.reasons if r.key == "reclaim").detector(s)
        assert st == mb.reclaim_strength(setup) and st > 0


# --------------------------------------------------------------------------- spec v1.1 (D-64): M2 alternative alignment
def _m2_with_4h(trend_4h: str, daily: str):
    _, snap = T.m2_scenario()
    snap.s.st["4h"].trend = trend_4h
    snap.s.daily_bias = daily
    return snap


def test_m2_alignment_accepts_1h_trend_with_daily_bias_when_4h_is_range():
    """Spec v1.1: trend_1h up + daily_bias up while trend_4h is range is an aligned long
    (mirror for shorts); the branch is logged in reasons_json as a weight-0 note."""
    snap = _m2_with_4h("range", "up")
    assert snap.s.st["1h"].trend == "up"
    m = M2BreakOfStructure()
    setup, missing = m.find_setup(snap)
    assert setup is not None, missing
    assert setup["direction"] == "long" and setup["alignment_branch"] == "1h_trend_daily_bias"
    assert setup["htf_agree_strength"] == 0.4          # alternative branches share the doc's third tier
    ev = m.evaluate(snap)
    notes = [r for r in ev.decision.reasons_json() if r.get("note")]
    assert notes == [{"key": "alignment_branch", "note": "1h_trend_daily_bias", "weight": 0.0}]
    weighted = [r for r in ev.decision.reasons_json() if "note" not in r]
    assert sum(r["weight"] for r in weighted) == sum(m.mind.weights().values())   # the note carries no weight


def test_m2_alignment_branch_needs_both_1h_trend_and_daily_bias():
    m = M2BreakOfStructure()
    for daily in ("neutral", "down"):
        setup, missing = m.find_setup(_m2_with_4h("range", daily))
        assert setup is None and "daily bias" in missing, (daily, missing)
    snap = _m2_with_4h("range", "up")
    snap.s.st["1h"].trend = "range"
    setup, missing = m.find_setup(snap)
    assert setup is None and "1h trend range" in missing
    # the plain 4h-trend branch is still the primary one and is logged too
    setup, _ = m.find_setup(_m2_with_4h("up", "neutral"))
    assert setup is not None and setup["alignment_branch"] == "4h_trend" and setup["htf_agree_strength"] == 0.7
