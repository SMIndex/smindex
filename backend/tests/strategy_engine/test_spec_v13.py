"""Spec v1.3 (D-87…D-90): realistic post-only fills, minimum stop distance, sweep
confirmation, conviction cap. Pure-function tests plus the ModelTradeManager pending
path with a scripted mid tape (no DB)."""
from __future__ import annotations

import inspect
import types

import pytest

from app.strategy_engine import model_runner as mr
from app.strategy_engine.execution.manager import _tick
from app.strategy_engine.execution.paper import post_only_place, limit_fills_through
from app.strategy_engine.mind.base import ContextRule, Mind, Reason
from app.strategy_engine.strategies import model_base as mb
from app.strategy_engine.strategies.m1_sweep_reclaim import M1SweepReclaim
from app.strategy_engine.strategies.m2_bos_order_block import M2BreakOfStructure
from app.strategy_engine.strategies.m3_failed_auction import M3FailedAuction
from app.strategy_engine.strategies.m4_htf_choch import M4ChangeOfCharacter
from app.strategy_engine.strategies.m5_session_liquidity_run import M5SessionLiquidityRun
from app.strategy_engine.strategies.m6_weekly_open_reclaim import M6WeeklyOpenReclaim
from app.strategy_engine.strategies.model_base import ModelIntent, STOP_FLOOR_ATR
from app.strategy_engine.structure.candles import Candle
from app.strategy_engine.structure.sweeps import confirmed_reclaim, detect_sweeps

from .model_fixtures import M15, TUE, candle, with_setup
from .test_model_runner import FakeSession, _manager, _run
from .test_models_m1_m6 import m1_scenario, m5_scenario, run


# ---------------------------------------------------------------------------
# Part 1 — post-only placement (D-87)
# ---------------------------------------------------------------------------
def test_buy_limit_above_market_is_rejected_and_requoted_one_tick_inside():
    tick = _tick(101.0)
    px, rejected = post_only_place("buy", 101.0, 100.0, tick)
    assert rejected is True
    assert px == pytest.approx(100.0 - tick) and px < 101.0
    # at the touch counts as crossing too (Alo rejects a buy AT the best ask)
    px, rejected = post_only_place("buy", 100.0, 100.0, tick)
    assert rejected is True and px == pytest.approx(100.0 - tick)
    # resting below the market is accepted as quoted
    assert post_only_place("buy", 99.5, 100.0, tick) == (99.5, False)


def test_sell_limit_below_market_mirrors():
    tick = _tick(99.0)
    px, rejected = post_only_place("sell", 99.0, 100.0, tick)
    assert rejected is True and px == pytest.approx(100.0 + tick) and px > 99.0
    assert post_only_place("sell", 100.5, 100.0, tick) == (100.5, False)


def test_no_market_price_rests_as_quoted():
    assert post_only_place("buy", 101.0, None, 0.01) == (101.0, False)


def _tape_manager(monkeypatch, tapes: list[list[tuple[int, float]]]):
    """ModelTradeManager whose mid tape is scripted: each _mids call pops the next tape."""
    m, saved, ob = _manager(monkeypatch)
    calls = []

    async def _mids(s, coin, start, end):
        calls.append((coin, start, end))
        return tapes.pop(0) if tapes else []
    m._risk._mids = _mids
    m.mids_calls = calls
    return m, saved, ob


def test_buy_limit_above_market_does_not_fill_at_the_original_price(monkeypatch):
    """Owner's Part 1 test: a buy limit placed ABOVE the market is placed; it must not
    fill at 101 — it is rejected, re-quoted one tick under the touch, and only fills
    when a later print goes THROUGH the re-quote."""
    placed = TUE
    tick = _tick(101.0)
    # touch = last mid on the tape inside the boundary window = 100.0
    m, saved, ob = _tape_manager(monkeypatch, [[(placed - 60_000, 100.2), (placed - 5_000, 100.0)]])
    snap = types.SimpleNamespace(price=100.0, cohort_net_dir=None)
    new_px, po = _run(m.paper_post_only_quote(FakeSession(), "BTC", "buy", 101.0, placed, snap))
    assert po["rejected"] is True and po["original_px"] == 101.0 and po["market_px"] == 100.0
    assert new_px == pytest.approx(100.0 - tick) and po["requote_px"] == new_px

    t = {"id": 1, "model": "M1", "strategy": "m1_sweep_reclaim", "asset": "BTC", "direction": "long",
         "entry_px": new_px, "stop_px": 98.0, "size": 1.0, "lifecycle_json": None, "signal_id": 1}
    life = {"phase": "pending", "placed_ts": placed, "entry_valid_until": placed + 3 * M15, "post_only": po,
            "size": 1.0, "remaining_size": 1.0, "hard_stop_min": 240}
    t["lifecycle_json"] = mr.jsonable(life)
    # prints between the re-quote and the original limit: the ORIGINAL 101 would have
    # "filled" on all of these — the resting re-quote fills on none of them
    m._risk._mids = None
    m, saved, ob = _tape_manager(monkeypatch, [[(placed + 10_000, 100.5), (placed + 20_000, 100.9), (placed + 30_000, 100.0)]])
    _run(m._pending(FakeSession(), t, placed + 60_000, False, None))
    assert all("fill_ts" not in row for row in saved), saved
    assert not any(r["kind"].endswith("_fill") for r in ob.rows)
    assert saved[-1]["life"]["phase"] == "pending" and saved[-1]["life"]["tape_ts"] == placed + 30_000
    for _, mid in [(0, 100.5), (0, 100.9), (0, 100.0)]:
        assert limit_fills_through("buy", 101.0, mid)          # the old rule WOULD have filled these
        assert not limit_fills_through("buy", new_px, mid)

    # a later print through the re-quote fills it at the re-quote, never at 101
    t["lifecycle_json"] = mr.jsonable(saved[-1]["life"])
    m, saved, ob = _tape_manager(monkeypatch, [[(placed + 90_000, 100.1), (placed + 95_000, new_px - 0.02)]])
    _run(m._pending(FakeSession(), t, placed + 120_000, False, None))
    assert saved[-1]["fill_ts"] == placed + 95_000 and saved[-1]["exit_reason"] == "open:model"
    assert ob.rows[-1]["kind"] == "M1_fill" and f"@ {new_px:.2f}" in ob.rows[-1]["message"]
    assert "101.00" not in ob.rows[-1]["message"]


def test_requoted_order_unfilled_within_validity_is_cancelled(monkeypatch):
    placed = TUE
    tick = _tick(101.0)
    new_px = 100.0 - tick
    t = {"id": 2, "model": "M5", "strategy": "m5_session_liquidity_run", "asset": "BTC", "direction": "long",
         "entry_px": new_px, "stop_px": 98.0, "size": 1.0, "signal_id": 1,
         "lifecycle_json": mr.jsonable({"phase": "pending", "placed_ts": placed, "entry_valid_until": placed + 3 * M15,
                                        "post_only": {"rejected": True, "original_px": 101.0, "requote_px": new_px}})}
    m, saved, ob = _tape_manager(monkeypatch, [[(placed + k * 60_000, 100.3) for k in range(1, 46)]])
    _run(m._pending(FakeSession(), t, placed + 3 * M15, True, None))
    assert saved[-1]["exit_reason"] == "cancelled:entry_expired" and saved[-1]["life"]["phase"] == "cancelled"
    assert ob.rows[-1]["kind"] == "M5_cancel"


def test_live_and_replay_share_the_post_only_code():
    """The live worker and the replay driver both run ModelEvaluator._open → ModelTradeManager
    .paper_post_only_quote → post_only_place, and _pending fills through limit_fills_through.
    There is no second executor."""
    src_open = inspect.getsource(mr.ModelEvaluator._open)
    assert "paper_post_only_quote" in src_open and "post_only" in src_open and "entry_requoted" in src_open
    src_q = inspect.getsource(mr.ModelTradeManager.paper_post_only_quote)
    assert "post_only_place" in src_q and "_mids" in src_q
    src_p = inspect.getsource(mr.ModelTradeManager._pending)
    assert "limit_fills_through" in src_p and "paper_post_only_quote" in src_p      # re-price paths too
    assert "cancelled:entry_expired" in src_p


# ---------------------------------------------------------------------------
# Part 2 — minimum stop distance (D-88)
# ---------------------------------------------------------------------------
class _FloorModel(mb.ModelStrategy):
    id, model, name = "mx", "MX", "x"


def _snap(atr15: float, atr1h: float):
    return types.SimpleNamespace(atr=lambda tf: {"15m": atr15, "1h": atr1h}[tf])


def test_stop_floor_moves_a_tight_stop_to_exactly_half_atr():
    m = _FloorModel.__new__(_FloorModel)
    setup = {}
    it = ModelIntent("long", 100.0, 99.6, 101.0, 102.0)
    m.apply_stop_floor(_snap(2.0, 5.0), setup, it)
    assert it.stop_floor_applied is True and it.stop_structural == 99.6
    assert it.stop_px == pytest.approx(100.0 - STOP_FLOOR_ATR * 2.0) and setup["stop"] == it.stop_px
    assert setup["stop_floor_applied"] is True and setup["stop_floor_tf"] == "15m"
    it = ModelIntent("short", 100.0, 100.4, 99.0, 98.0)
    m.apply_stop_floor(_snap(2.0, 5.0), setup, it)
    assert it.stop_floor_applied is True and it.stop_px == pytest.approx(101.0)


def test_stop_floor_leaves_a_wide_stop_alone_and_needs_atr():
    m = _FloorModel.__new__(_FloorModel)
    it = ModelIntent("long", 100.0, 98.0, 101.0, 102.0)
    setup = {}
    m.apply_stop_floor(_snap(2.0, 5.0), setup, it)
    assert it.stop_floor_applied is False and it.stop_px == 98.0 and it.stop_structural == 98.0
    assert setup["stop_floor_applied"] is False
    it = ModelIntent("long", 100.0, 99.9, 101.0, 102.0)
    m.apply_stop_floor(_snap(0.0, 0.0), setup, it)
    assert it.stop_floor_applied is False and it.stop_px == 99.9


def test_stop_floor_timeframe_per_model():
    assert M1SweepReclaim.stop_floor_tf == "15m" and M3FailedAuction.stop_floor_tf == "15m"
    assert M5SessionLiquidityRun.stop_floor_tf == "15m"
    assert M2BreakOfStructure.stop_floor_tf == "1h" and M4ChangeOfCharacter.stop_floor_tf == "1h"
    assert M6WeeklyOpenReclaim.stop_floor_tf == "1h"
    m = _FloorModel.__new__(_FloorModel)
    m.stop_floor_tf = "1h"
    it = ModelIntent("long", 100.0, 99.0, 101.0, 102.0)
    m.apply_stop_floor(_snap(1.0, 4.0), {}, it)
    assert it.stop_px == pytest.approx(98.0) and it.stop_floor_applied is True


def test_m1_take_applies_the_floor_through_evaluate():
    _, snap = m1_scenario()
    ev = run(M1SweepReclaim(), snap)
    assert ev.intent is not None
    a = snap.atr("15m")
    assert abs(ev.intent.entry_px - ev.intent.stop_px) >= STOP_FLOOR_ATR * a - 1e-9
    assert ev.intent.stop_structural is not None
    assert ev.setup["stop_floor_applied"] is ev.intent.stop_floor_applied
    if ev.intent.stop_floor_applied:
        assert ev.intent.stop_px == pytest.approx(ev.intent.entry_px - STOP_FLOOR_ATR * a)
    else:
        assert ev.intent.stop_px == ev.intent.stop_structural


# ---------------------------------------------------------------------------
# Part 3 — confirmed reclaim (D-89)
# ---------------------------------------------------------------------------
def _c(i: int, o, h, l, c) -> Candle:
    return candle(TUE + (i + 1) * M15, o, h, l, c)


def test_confirmed_reclaim_case_a_later_candle():
    lvl, atr = 100.0, 1.0
    cs = [_c(0, 101, 101.5, 100.5, 101), _c(1, 101, 101.2, 99.7, 99.9), _c(2, 99.9, 100.8, 99.8, 100.6)]
    sws = detect_sweeps(cs, lvl, atr, "low")
    conf = confirmed_reclaim(sws, cs, lvl, "low", 2)
    assert conf is not None and conf.confirmation_used is False
    assert conf.trigger_index == 2 and conf.wick_candle_index == 1 and conf.sweep.candles_to_reclaim == 2


def test_confirmed_reclaim_case_b_same_candle_needs_a_confirming_close():
    lvl, atr = 100.0, 1.0
    same = _c(1, 101, 101.2, 99.7, 100.4)                       # sweep and reclaim on one candle
    cs = [_c(0, 101, 101.5, 100.5, 101), same]
    sws = detect_sweeps(cs, lvl, atr, "low")
    assert confirmed_reclaim(sws, cs, lvl, "low", 1) is None      # not confirmed on the reclaim candle itself
    ok = cs + [_c(2, 100.4, 100.9, 99.9, 100.5)]                  # close > level, low 99.9 >= wick 99.7
    conf = confirmed_reclaim(detect_sweeps(ok, lvl, atr, "low"), ok, lvl, "low", 2)
    assert conf is not None and conf.confirmation_used is True and conf.trigger_index == 2
    bad_close = cs + [_c(2, 100.4, 100.6, 99.9, 99.95)]           # closes back below the level
    assert confirmed_reclaim(detect_sweeps(bad_close, lvl, atr, "low"), bad_close, lvl, "low", 2) is None
    bad_low = cs + [_c(2, 100.4, 100.9, 99.6, 100.5)]             # low 99.6 below the wick 99.7
    assert confirmed_reclaim(detect_sweeps(bad_low, lvl, atr, "low"), bad_low, lvl, "low", 2) is None
    late = ok + [_c(3, 100.5, 100.9, 100.2, 100.7)]               # confirmation was last candle, not this one
    assert confirmed_reclaim(detect_sweeps(late, lvl, atr, "low"), late, lvl, "low", 3) is None


def test_confirmed_reclaim_short_mirror():
    lvl, atr = 100.0, 1.0
    cs = [_c(0, 99, 99.5, 98.5, 99), _c(1, 99, 100.3, 98.8, 99.6), _c(2, 99.6, 100.1, 99.1, 99.5)]
    conf = confirmed_reclaim(detect_sweeps(cs, lvl, atr, "high"), cs, lvl, "high", 2)
    assert conf is not None and conf.confirmation_used is True
    bad = cs[:2] + [_c(2, 99.6, 100.4, 99.1, 99.5)]               # high 100.4 above the wick 100.3
    assert confirmed_reclaim(detect_sweeps(bad, lvl, atr, "high"), bad, lvl, "high", 2) is None


def test_reclaim_entry_px_rests_away_from_the_market():
    # long: min(50% of the candle, close) − 0.05 ATR
    assert mb.reclaim_entry_px("long", 102.0, 98.0, 101.0, 2.0) == pytest.approx(100.0 - 0.1)
    assert mb.reclaim_entry_px("long", 102.0, 98.0, 99.5, 2.0) == pytest.approx(99.5 - 0.1)
    # short mirror: max(50%, close) + 0.05 ATR
    assert mb.reclaim_entry_px("short", 102.0, 98.0, 99.0, 2.0) == pytest.approx(100.0 + 0.1)
    assert mb.reclaim_entry_px("short", 102.0, 98.0, 100.5, 2.0) == pytest.approx(100.5 + 0.1)


def test_m5_entry_below_market_and_validity_from_confirmation():
    _, snap, _ = m5_scenario()
    ev = run(M5SessionLiquidityRun(), snap)
    assert ev.intent is not None and ev.intent.entry_px < snap.price
    assert ev.intent.entry_valid_until_ms == snap.s.c15[-1].ts + 3 * M15
    assert ev.intent.entry_offset_note == mb.RECLAIM_ENTRY_NOTE


# ---------------------------------------------------------------------------
# Part 4 — conviction cap (D-90)
# ---------------------------------------------------------------------------
def test_conviction_capped_at_one_after_multipliers():
    mind = Mind("MX", [Reason("a", "", lambda s: 1.0, 1.0)], [], [ContextRule("boost", lambda s: 1.3)], [])
    d = mind.evaluate(types.SimpleNamespace(setup={}, extra_vetoes=[]))
    assert d.raw_conviction == pytest.approx(1.0) and d.conviction == 1.0 and d.size_tier == "full"
    assert dict(d.multipliers)["boost"] == 1.3
    mind2 = Mind("MX", [Reason("a", "", lambda s: 0.6, 1.0)], [], [ContextRule("boost", lambda s: 1.3)], [])
    assert mind2.evaluate(types.SimpleNamespace(setup={}, extra_vetoes=[])).conviction == pytest.approx(0.78)
