"""Audit Part 2 — mirror correctness (docs 11–16 "mirror for shorts").

Every long-version fixture in test_models_m1_m6 is reflected around a price
pivot: prices → 2P − price (high/low swapped), taker buy ratio → 1 − ratio,
long/short liquidations swapped, cohort long/short swapped and net_dir negated,
funding z negated, book bids/asks swapped. OI is unchanged (no doc mirrors an
OI-direction condition: "OI rising" / "OI drop" are the same on both sides).

The mirrored Mind decision must equal the original in conviction, size tier,
reasons hit (strength per key), multipliers and vetoes; intent prices must be
the mirror image. Any asymmetry is a bug."""
from __future__ import annotations

import math

import pytest

from app.strategy_engine.strategies.m1_sweep_reclaim import M1SweepReclaim
from app.strategy_engine.strategies.m2_bos_order_block import M2BreakOfStructure
from app.strategy_engine.strategies.m3_failed_auction import M3FailedAuction
from app.strategy_engine.strategies.m4_htf_choch import M4ChangeOfCharacter
from app.strategy_engine.strategies.m5_session_liquidity_run import M5SessionLiquidityRun
from app.strategy_engine.strategies.m6_weekly_open_reclaim import M6WeeklyOpenReclaim
from app.strategy_engine.structure.candles import Candle

from . import model_fixtures as fx
from . import test_models_m1_m6 as T

PIVOT = 100.0
TOL = 1e-6


# ---------------------------------------------------------------------------
# capture the exact fixture inputs, then mirror them
# ---------------------------------------------------------------------------
class Capture:
    def __init__(self):
        self.calls = []

    def __call__(self, c15, now, **kw):
        self.calls.append((c15, now, kw))
        return fx.snapshot(c15, now, **kw)


def _m(v):
    return None if v is None else 2 * PIVOT - float(v)


def mirror_candles(cs: list[Candle]) -> list[Candle]:
    return [Candle(c.ts, _m(c.o), _m(c.l), _m(c.h), _m(c.c), c.v) for c in cs]


_SIDE = {"long": "short", "short": "long", None: None}
_DT = {"trend_up": "trend_down", "trend_down": "trend_up"}


def mirror_kwargs(kw: dict) -> dict:
    out = dict(kw)
    if kw.get("oi_rows"):
        out["oi_rows"] = [{**r, "mark": _m(r.get("mark")) if r.get("mark") is not None else None,
                           "funding": -float(r["funding"]) if r.get("funding") is not None else None} for r in kw["oi_rows"]]
    if kw.get("trades_rows"):
        out["trades_rows"] = [{**r, "taker_buy_notional": r["taker_sell_notional"],
                               "taker_sell_notional": r["taker_buy_notional"]} for r in kw["trades_rows"]]
    if kw.get("book_rows"):
        rows = []
        for r in kw["book_rows"]:
            n = dict(r)
            n["mid"] = _m(r["mid"])
            for d in ("0_1", "0_3", "0_5"):
                n[f"bid_{d}"], n[f"ask_{d}"] = r[f"ask_{d}"], r[f"bid_{d}"]
            rows.append(n)
        out["book_rows"] = rows
    if kw.get("liq_rows"):
        out["liq_rows"] = [{**r, "side": _SIDE[r["side"]]} for r in kw["liq_rows"]]
    if kw.get("positions_rows"):
        out["positions_rows"] = [{**r, "side": _SIDE[r["side"]], "liq_px": _m(r["liq_px"])} for r in kw["positions_rows"]]
    if kw.get("gauge"):
        g = dict(kw["gauge"])
        if g.get("funding_z") is not None:
            g["funding_z"] = -float(g["funding_z"])
        g["blocked_direction"] = _SIDE[g.get("blocked_direction")]
        out["gauge"] = g
    if kw.get("gauge_24h"):
        out["gauge_24h"] = [{**r, "funding_z": -float(r["funding_z"]) if r.get("funding_z") is not None else None}
                            for r in kw["gauge_24h"]]
    if kw.get("cohort"):
        c = dict(kw["cohort"])
        for k in ("net_dir", "net_dir_24h_ago"):
            if c.get(k) is not None:
                c[k] = -float(c[k])
        for a, b in (("fresh_long", "fresh_short"), ("long_notional", "short_notional")):
            if a in c or b in c:
                c[a], c[b] = c.get(b), c.get(a)
        out["cohort"] = c
    if kw.get("day_type_override") in _DT:
        out["day_type_override"] = _DT[kw["day_type_override"]]
    return out


def long_and_mirror(scenario, monkeypatch, **kw):
    """Run the fixture once (long) and once on its exact mirror (short)."""
    cap = Capture()
    monkeypatch.setattr(T, "snapshot", cap)
    scenario(**kw)
    assert len(cap.calls) == 1
    c15, now, skw = cap.calls[0]
    snap_l = fx.snapshot(c15, now, **skw)
    snap_s = fx.snapshot(mirror_candles(c15), now, **mirror_kwargs(skw))
    return snap_l, snap_s


def assert_close(a, b, what):
    if a is None or b is None:
        assert a is None and b is None, (what, a, b)
        return
    assert math.isclose(float(a), float(b), rel_tol=TOL, abs_tol=TOL), (what, a, b)


def _raw(reasons: dict, skip: set) -> float:
    tw = sum(w for k, (_, w) in reasons.items() if k not in skip)
    return sum(s * w for k, (s, w) in reasons.items() if k not in skip) / tw if tw else 0.0


def assert_mirror(ev_l, ev_s, pct_reasons: set = frozenset(), cluster_targets: set = frozenset()):
    """pct_reasons: reasons defined by the docs as a PERCENT move (M4 `extension` = 3-day % move).
    A price-space mirror is not a percent-space mirror (80→120 is +50%, 120→80 is −33%), so those
    strengths legitimately differ; everything else must match exactly and the raw conviction
    must match once they are excluded.
    cluster_targets: intent prices that are a liquidation-cluster level (M4 T2). Clusters are 0.25%
    fixed-grid bands (`pools.liquidation_clusters`), so two liq prices straddling a band edge on one
    side may share a band on the mirror — the level moves by less than one band width. Grid
    quantisation, not a direction asymmetry; compared within one band width."""
    dl, ds = ev_l.decision, ev_s.decision
    assert (ev_l.setup is None) == (ev_s.setup is None), (ev_l.waiting_for, ev_s.waiting_for)
    if ev_l.setup:
        assert ev_l.setup.get("direction") == _SIDE[ev_s.setup.get("direction")], (ev_l.setup, ev_s.setup)
    assert dl.take == ds.take, (dl, ds)
    assert dl.vetoes_hit == ds.vetoes_hit, (dl.vetoes_hit, ds.vetoes_hit, dl.veto_texts, ds.veto_texts)
    rl = {k: (s, w) for k, s, w in dl.reasons}
    rs = {k: (s, w) for k, s, w in ds.reasons}
    assert rl.keys() == rs.keys(), (rl.keys(), rs.keys())
    for k in rl:
        assert_close(rl[k][1], rs[k][1], f"reason {k} weight")
        if k not in pct_reasons:
            assert_close(rl[k][0], rs[k][0], f"reason {k} strength")
    if pct_reasons:
        assert_close(_raw(rl, pct_reasons), _raw(rs, pct_reasons), "raw excluding percent reasons")
        # the percent reason itself follows the doc formula on each side's own % move
        for k in pct_reasons:
            assert 0.0 <= rs[k][0] <= 1.0 and 0.0 <= rl[k][0] <= 1.0
        # (conviction = raw × Π multipliers on both sides; the multipliers are compared below)
    else:
        assert dl.size_tier == ds.size_tier
        assert_close(dl.raw_conviction, ds.raw_conviction, "raw")
        assert_close(dl.conviction, ds.conviction, "conviction")
    ml, ms = dict(dl.multipliers), dict(ds.multipliers)
    assert ml.keys() == ms.keys(), (ml, ms)
    for k in ml:
        assert_close(ml[k], ms[k], f"multiplier {k}")
    assert (ev_l.intent is None) == (ev_s.intent is None)
    if ev_l.intent:
        il, is_ = ev_l.intent, ev_s.intent
        assert il.direction == _SIDE[is_.direction]
        for f in ("entry_px", "stop_px", "t1", "t2", "t3"):
            a, b = getattr(il, f), (_m(getattr(is_, f)) if getattr(is_, f) is not None else None)
            if f in cluster_targets and a is not None and b is not None:
                assert abs(a - b) <= 0.0025 * a, (f, a, b)
            else:
                assert_close(a, b, f)
        assert il.partial_pct == is_.partial_pct and il.trail_after == is_.trail_after
        assert il.entry_valid_until_ms == is_.entry_valid_until_ms and il.hard_stop_ts == is_.hard_stop_ts


CASES = [
    # (id, model class, scenario, kwargs, expected: "take" | veto key)
    ("m1_valid", M1SweepReclaim, T.m1_scenario, {}, "take"),
    ("m1_too_deep", M1SweepReclaim, T.m1_scenario, {"raid_atr": 0.7}, "too_deep"),
    ("m1_third_sweep", M1SweepReclaim, T.m1_scenario, {"earlier_sweeps": 2}, "third_sweep"),
    ("m2_valid", M2BreakOfStructure, T.m2_scenario, {}, "take"),
    ("m2_short_covering", M2BreakOfStructure, T.m2_scenario, {"oi_end": 995_000.0}, "short_covering"),
    ("m3_valid", M3FailedAuction, T.m3_scenario, {}, "take"),
    ("m3_acceptance", M3FailedAuction, T.m3_scenario, {"acceptance": True}, "acceptance"),
    ("m3_squeeze_risk", M3FailedAuction, T.m3_scenario, {"funding_z": -1.5}, "squeeze_risk"),
    ("m4_valid", M4ChangeOfCharacter, T.m4_scenario, {}, "take"),                       # `extension` is a % move (see assert_mirror)
    ("m4_cohort_adding", M4ChangeOfCharacter, T.m4_scenario, {"cohort_change": +0.2}, "cohort_adding_longs"),
    ("m5_valid", M5SessionLiquidityRun, T.m5_scenario, {}, "take"),
    ("m5_opened_outside", M5SessionLiquidityRun, T.m5_scenario, {"open_off_atr": 1.5}, "opened_outside"),
    ("m5_range_bad", M5SessionLiquidityRun, T.m5_scenario, {"asia_amp_atr": 3.2}, "range_bad"),
    ("m6_valid", M6WeeklyOpenReclaim, T.m6_scenario, {}, "take"),
    ("m6_shallow_loss", M6WeeklyOpenReclaim, T.m6_scenario, {"loss_low": 99.3}, "shallow_loss"),
    ("m6_oi_falling", M6WeeklyOpenReclaim, T.m6_scenario, {"oi_ramp": -0.02}, "oi_falling"),
]


@pytest.mark.parametrize("cid,cls,scenario,kw,expect", CASES, ids=[c[0] for c in CASES])
def test_short_is_exact_mirror_of_long(monkeypatch, cid, cls, scenario, kw, expect):
    snap_l, snap_s = long_and_mirror(scenario, monkeypatch, **kw)
    m = cls()
    ev_l = T.run(m, snap_l)
    ev_s = T.run(cls(), snap_s)
    # the long side behaves as the base test expects
    if expect == "take":
        assert ev_l.decision.take is True, (ev_l.decision, ev_l.waiting_for)
    else:
        assert expect in ev_l.decision.vetoes_hit, (ev_l.decision.vetoes_hit, ev_l.waiting_for)
    # and the short side is its exact image
    is_m4 = cls is M4ChangeOfCharacter
    assert_mirror(ev_l, ev_s, pct_reasons={"extension"} if is_m4 else frozenset(),
                  cluster_targets={"t2"} if is_m4 else frozenset())
    if cls is M4ChangeOfCharacter:
        # extension_strength follows doc 14 on each side's own 3-day % move
        for ev in (ev_l, ev_s):
            assert_close(ev.setup["extension_strength"], min(1.0, max(0.0, (ev.setup["pct_3d"] * 100.0 - 3.0) / 6.0)), "ext")   # spec v1.2 D-74


def test_mirror_helpers_are_involutions():
    cs = fx.path15([99.0, 101.5, 100.2], fx.TUE)
    back = mirror_candles(mirror_candles(cs))
    for a, b in zip(cs, back):
        for f in ("o", "h", "l", "c"):
            assert_close(getattr(a, f), getattr(b, f), f)
    kw = {"trades_rows": [{"ts": 1, "taker_buy_notional": 700.0, "taker_sell_notional": 300.0}],
          "cohort": {"net_dir": 0.3, "fresh_long": 2, "fresh_short": 0},
          "gauge": {"funding_z": 1.1, "blocked_direction": "long"}}
    assert mirror_kwargs(mirror_kwargs(kw)) == kw
