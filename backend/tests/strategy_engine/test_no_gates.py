"""NO GATES: every strategy evaluates on whatever data exists (empty → short →
full), never returns not_evaluated, labels short history as warming, keeps the
catalog condition names, and the risk engine annotates instead of blocking."""
import time

import pytest

from app.strategy_engine import evaluator, conditions
from app.strategy_engine.execution.manager import PaperTradeManager
from app.strategy_engine.risk import engine as risk
from app.strategy_engine.strategies.base import StrategyContext, UNAVAILABLE

NOW = int(time.time() * 1000)


def _ctx(**kw):
    base = dict(coin="BTC", now_ms=NOW, params={}, candles_15m=[], candles_1h=[], candles_4h=[],
                oi_1m=[], trades_1m=[], book_5s=[], book_last=None, gauge=None, regime=None, bias=None,
                liquidations=[], liq_coverage="partial", liq_clusters=[], cohort=None, funding_rows=0)
    base.update(kw)
    return StrategyContext(**base)


def _bars(n, step_ms, px=100_000.0):
    # deterministic monotone-ish path — exercises code paths only (not a backtest)
    out = []
    for i in range(n):
        o = px
        px = px * (1 + (0.001 if i % 3 else -0.0015))
        out.append(dict(ts=NOW - (n - i) * step_ms, o=o, h=max(o, px) * 1.001, l=min(o, px) * 0.999, c=px, v=100.0))
    return out


@pytest.mark.parametrize("sid", list(evaluator.REGISTRY))
def test_evaluates_on_empty_history_with_warming_labels(sid):
    r = evaluator.REGISTRY[sid].evaluate(_ctx())
    # not_evaluated = names of conditions whose FEED is absent (labelled 'unavailable
    # (not in score)'); the strategy itself always evaluated — a sentence + score row.
    assert isinstance(r.not_evaluated, list)
    assert r.waiting_for                                   # real sentence, never blank
    assert len(r.conditions) == conditions.condition_counts()[sid]
    assert [c.name for c in r.conditions] == [c["name"] for c in conditions.CONDITIONS[sid]]
    assert any(UNAVAILABLE in w for w in r.warming)        # empty feeds are labelled, not hidden
    assert r.fired is False and r.intents == []


@pytest.mark.parametrize("sid", list(evaluator.REGISTRY))
def test_short_history_is_warming_not_blocked(sid):
    c15, c1, c4 = _bars(40, 900_000), _bars(30, 3_600_000), _bars(30, 14_400_000)
    regime = dict(ts=NOW, coin="BTC", allowed=1, score=0.9, blockers=[])
    bias = dict(ts=NOW, coin="BTC", score=0.2)
    gauge = dict(ts=NOW, coin="BTC", hl_funding_8h_equiv=0.0001, funding_z=0.5, crowding_level="NORMAL",
                 tilt=0.1, blocked_direction=None)
    r = evaluator.REGISTRY[sid].evaluate(_ctx(candles_15m=c15, candles_1h=c1, candles_4h=c4,
                                              regime=regime, bias=bias, gauge=gauge, funding_rows=120))
    # candle-driven conditions are all evaluated; only feed-less ones (OI/book/liq) stay unavailable
    assert "Regime gate" not in r.not_evaluated
    if sid.startswith("s06"):
        assert not any(n in r.not_evaluated for n in ("Direction", "Trend established", "Pullback", "Fisher trigger"))
    assert r.waiting_for
    # 40 bars < EMA200 / percentile-200 windows → at least one "warming: M of N" label
    if sid in ("s04_vol_compression", "s06_hull_fisher_ema", "s06u_hull_fisher_ema"):
        assert any(w.split(": ")[1].startswith("warming") for w in r.warming), r.warming
    for c in r.conditions:
        d = c.as_dict()
        assert set(d) >= {"name", "met", "warming"}


def test_risk_rules_still_computed_but_can_open_result_is_only_an_annotation():
    """D-100: contract INVERTED on 2026-09-11. `can_open` is no longer an
    annotation — `_route` calls it and returns without opening when it refuses.
    This test now asserts the gate exists, which is the opposite of what it
    asserted before and is the entire point of the change."""
    import inspect
    from app.strategy_engine import evaluator as ev
    src = inspect.getsource(ev)
    # the gate is called before any entry is routed
    assert "allowed, why = risk.can_open(" in src, "can_open must gate _route"
    assert "paper entry BLOCKED by risk engine" in src
    # and the state it needs is actually loaded (used to be hardcoded None)
    assert "daily_cap_hit_until=(int(rs[3])" in src
    assert "paused_until=(int(rs[4])" in src

def test_marker_ttl_and_hold():
    m = PaperTradeManager(["BTC"])
    base = {"chase": False, "rq": 0, "rq_ts": None, "entry_taker": False}
    assert m._marker("pending:hold_h=4") == {"hold_h": 4, "ttl_m": None, **base}
    assert m._marker("pending:hold_h=4:ttl_m=30") == {"hold_h": 4, "ttl_m": 30, **base}
    assert m._ttl_ms("pending:hold_h=4:ttl_m=30") == 30 * 60_000
    assert m._ttl_ms("pending:hold_h=4") == 4 * 3_600_000
    assert m._hold_ms("pending:hold_h=6:ttl_m=30") == 6 * 3_600_000


@pytest.mark.parametrize("sid", list(evaluator.REGISTRY))
def test_signal_payload_is_plain_python_json(sid):
    """Prod 2026-09-03 21:18:59 UTC: 15m strategies raised
    'Object of type bool_ is not JSON serializable' on the strat_signals INSERT
    (numpy scalars leaking out of the indicator code) and the whole tick's commit
    was lost. Every value logged to strat_signals must be plain Python."""
    import json
    strat = evaluator.REGISTRY[sid]
    ctx = _ctx(candles_15m=_bars(300, 900_000), candles_1h=_bars(60, 3_600_000), candles_4h=_bars(60, 14_400_000))
    res = strat.evaluate(ctx)
    payload = evaluator._py({"conditions": [c.as_dict() for c in res.conditions], "components": res.components,
                             "warming": res.warming, "labels": res.labels, "fired": res.fired,
                             "direction": res.direction, "total_score": res.total_score})
    json.dumps(payload)                                    # must not raise
    assert type(evaluator._py(res.fired)) is bool
    for c in payload["conditions"]:
        assert type(c["met"]) in (bool, type(None)), c


def test_py_coerces_numpy_scalars():
    np = pytest.importorskip("numpy")
    out = evaluator._py({"a": np.bool_(True), "b": [np.float64(1.5), np.int64(3)], "c": (np.bool_(False),)})
    assert out == {"a": True, "b": [1.5, 3], "c": [False]}
    assert all(type(x) in (bool, float, int) for x in [out["a"], *out["b"], *out["c"]])


def test_cond_as_dict_coerces_numpy_met():
    np = pytest.importorskip("numpy")
    from app.strategy_engine.strategies.base import Cond
    c = Cond("Pullback", "x", value="v", met=np.bool_(True))   # s06 pb_ok = (...) and touch and not beyond
    assert type(c.as_dict()["met"]) is bool


def test_regime_blockers_json_string_is_decoded_not_iterated():
    """Prod 2026-09-03 19:35 UTC: Regime gate rendered 'blocked: [, ", o, r, a, c, l, e ...' —
    strat_regime.blockers (JSON column) arrives as a str through text('SELECT *')."""
    from app.strategy_engine.strategies.base import regime_blockers, regime_cond
    assert regime_blockers({"blockers": '["oracle_stale", "spread_unavailable"]'}) == ["oracle_stale", "spread_unavailable"]
    assert regime_blockers({"blockers": ["weekend"]}) == ["weekend"]
    assert regime_blockers(None) == []
    c = regime_cond(_ctx(regime={"allowed": False, "score": 0.2, "blockers": '["oracle_stale"]'}))
    assert c.value == "blocked: oracle_stale" and c.met is False


def test_s05_rv_percentile_uses_30d_closes_not_the_300_bar_window():
    """Prod 2026-09-03: 'High-vol day: warming: 2 of 30 days of RV' although 52 days of
    15m candles exist — the 300-bar loader window was capping doc-05's RV24h pct(30d)."""
    strat = evaluator.REGISTRY["s05_session_open"]
    bars = _bars(3000, 900_000)
    ctx = _ctx(candles_15m=bars[-300:], closes_15m_30d=[b["c"] for b in bars])
    res = strat.evaluate(ctx)
    hv = next(c for c in res.conditions if c.name == "High-vol day")
    assert hv.warming is None, hv.warming          # 30 of 30 days available → not warming
    ctx_short = _ctx(candles_15m=bars[-300:])
    hv2 = next(c for c in strat.evaluate(ctx_short).conditions if c.name == "High-vol day")
    assert hv2.warming and "of 30 days of RV" in hv2.warming
