"""model_runner.py — the pure / arithmetic parts (fill rule, partial + BE,
r_multiple on exit, feed-missing vetoes D-31, FVG fallback, trail stop D-11,
JSON coercion) and the outbox model tagging. DB paths are covered by the
prod verification in docs/models/IMPLEMENTATION-REPORT.md."""
from __future__ import annotations

import asyncio
import dataclasses
import math

import numpy as np
import pytest

from app.strategy_engine import model_runner as mr
from app.strategy_engine.execution.paper import MAKER_FEE, TAKER_FEE, limit_fills_through, stop_triggered
from app.strategy_engine.structure.candles import DAY_MS
from app.services.strat_outbox import _model_of

from .model_fixtures import H1, M15, TUE, candle, feeds, path15, snapshot, zigzag


def _run(coro):
    """Own loop per call; leaves the default loop (used by sibling tests) untouched."""
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


class FakeSession:
    """Collects SQL executes; nothing hits a DB."""
    def __init__(self):
        self.calls = []

    async def execute(self, stmt, params=None):
        self.calls.append((stmt, params))
        return None


class FakeOutbox:
    def __init__(self):
        self.rows = []

    async def __call__(self, s, now_ms, kind, sid, coin, message, dedupe_key):
        self.rows.append({"kind": kind, "sid": sid, "coin": coin, "message": message, "dedupe": dedupe_key})


def _manager(monkeypatch):
    m = mr.ModelTradeManager(["BTC", "ETH"])
    saved = []

    async def _save_life(s, tid, life, **cols):
        saved.append({"id": tid, "life": mr.jsonable(life), **cols})
    m._save_life = _save_life
    risk_calls = []

    async def _apply_risk(s, sid, pnl_net, now_ms):
        risk_calls.append((sid, pnl_net))
    m._risk._apply_risk = _apply_risk
    m.risk_calls = risk_calls
    ob = FakeOutbox()
    monkeypatch.setattr(mr, "outbox", ob)
    return m, saved, ob


# ---------------------------------------------------------------------------
# fills are strict: through, never on touch (D-05 / master prompt "post-only conservatively")
# ---------------------------------------------------------------------------
def test_fill_rule_is_strict_through():
    assert limit_fills_through("buy", 100.0, 99.99)
    assert not limit_fills_through("buy", 100.0, 100.0)
    assert not limit_fills_through("buy", 100.0, 100.01)
    assert limit_fills_through("sell", 100.0, 100.01)
    assert not limit_fills_through("sell", 100.0, 100.0)
    assert stop_triggered("long", 95.0, 95.0) and not stop_triggered("long", 95.0, 95.01)
    assert stop_triggered("short", 105.0, 105.0) and not stop_triggered("short", 105.0, 104.99)


def test_entry_exit_sides():
    assert mr._entry_side("long") == "buy" and mr._exit_side("long") == "sell"
    assert mr._entry_side("short") == "sell" and mr._exit_side("short") == "buy"


# ---------------------------------------------------------------------------
# T1 partial + breakeven, then exit → r_multiple (D-12)
# ---------------------------------------------------------------------------
def test_partial_then_close_r_multiple(monkeypatch):
    m, saved, ob = _manager(monkeypatch)
    t = {"id": 7, "model": "M1", "strategy": "m1_sweep_reclaim", "asset": "BTC", "direction": "long",
         "entry_px": 100.0, "stop_px": 99.0, "size": 10.0, "fill_ts": TUE}
    life = {"size": 10.0, "remaining_size": 10.0, "partial_pnl": 0.0, "partial_fees": 0.0,
            "initial_risk_dollars": 10.0, "t1": 101.0, "t2": 102.0}          # risk = 1.0 × 10 = $10
    s = FakeSession()
    _run(m._partial(s, t, life, 101.0, 4.0, TUE + H1, TUE + H1, "t1"))
    assert life["remaining_size"] == 6.0 and life["phase"] == "partial"
    assert life["partial_pnl"] == pytest.approx(4.0)
    assert life["partial_fees"] == pytest.approx(101.0 * 4.0 * MAKER_FEE)
    assert life["partials"][0]["which"] == "t1"
    assert ob.rows[-1]["kind"] == "M1_partial" and ob.rows[-1]["message"].startswith("[M1] BTC T1 hit")
    assert "stop to breakeven" in ob.rows[-1]["message"]

    _run(m._close(s, t, life, 102.0, TUE + 2 * H1, "target_t2", -0.2, 2.0, TUE + 2 * H1, taker=False))
    row = saved[-1]
    gross = 4.0 + (102.0 - 100.0) * 6.0
    fees = 100.0 * 10.0 * MAKER_FEE + 101.0 * 4.0 * MAKER_FEE + 102.0 * 6.0 * MAKER_FEE
    assert row["pnl_gross"] == pytest.approx(gross)
    assert row["fees"] == pytest.approx(fees)
    assert row["pnl_net"] == pytest.approx(gross - fees)
    assert row["r_multiple"] == pytest.approx((gross - fees) / 10.0)
    assert row["exit_reason"] == "target_t2" and life["phase"] == "closed" and life["remaining_size"] == 0.0
    assert ob.rows[-1]["kind"] == "M1_exit" and "[M1] BTC LONG closed @" in ob.rows[-1]["message"]
    assert m.risk_calls == [("m1_sweep_reclaim", pytest.approx(gross - fees))]


def test_stop_exit_is_taker_and_negative_r(monkeypatch):
    m, saved, ob = _manager(monkeypatch)
    t = {"id": 8, "model": "M3", "strategy": "m3_failed_auction", "asset": "ETH", "direction": "short",
         "entry_px": 200.0, "stop_px": 202.0, "size": 5.0, "fill_ts": TUE}
    life = {"size": 5.0, "remaining_size": 5.0, "initial_risk_dollars": 10.0}
    _run(m._close(FakeSession(), t, life, 202.0, TUE + M15, "stop", -2.0, 0.1, TUE + M15, taker=True))
    row = saved[-1]
    assert row["pnl_gross"] == pytest.approx(-10.0)
    assert row["fees"] == pytest.approx(200.0 * 5.0 * MAKER_FEE + 202.0 * 5.0 * TAKER_FEE)
    assert row["r_multiple"] < -1.0
    assert ob.rows[-1]["kind"] == "M3_exit" and ob.rows[-1]["message"].startswith("[M3] ETH SHORT closed")


def test_close_without_risk_dollars_has_no_r(monkeypatch):
    m, saved, ob = _manager(monkeypatch)
    t = {"id": 9, "model": "M5", "strategy": "m5", "asset": "BTC", "direction": "long", "entry_px": 100.0,
         "stop_px": 99.0, "size": 1.0, "fill_ts": TUE}
    _run(m._close(FakeSession(), t, {"size": 1.0, "remaining_size": 1.0}, 101.0, TUE + M15, "hard_stop",
                         0.0, 1.0, TUE + M15, taker=True))
    assert saved[-1]["r_multiple"] is None
    assert "n/a R" in ob.rows[-1]["message"]


# ---------------------------------------------------------------------------
# D-31 feed-missing vetoes
# ---------------------------------------------------------------------------
def _c15(n_days=3):
    return path15(zigzag((n_days * DAY_MS) // M15, base=100.0, amp=1.0, period=20), TUE - n_days * DAY_MS)


def test_feed_vetoes_all_missing():
    c15 = _c15()
    snap = snapshot(c15, c15[-1].ts + 30_000)
    keys = [k for k, _ in mr._feed_vetoes(snap)]
    assert keys == ["feed_missing_oi", "feed_missing_taker"]     # D-51: book is optional, never a veto


def test_feed_vetoes_none_when_fresh():
    c15 = _c15()
    now = c15[-1].ts + 30_000
    oi, tr, book = feeds(now, mid=100.0)
    snap = snapshot(c15, now, oi_rows=oi, trades_rows=tr, book_rows=book)
    assert mr._feed_vetoes(snap) == []


def test_feed_vetoes_stale_book_is_not_a_veto():
    c15 = _c15()
    now = c15[-1].ts + 30_000
    oi, tr, book = feeds(now, mid=100.0)
    stale = [dict(r, ts=r["ts"] - 10 * 60_000) for r in book]         # newest mid 10 min old
    snap = snapshot(c15, now, oi_rows=oi, trades_rows=tr, book_rows=stale)
    assert mr._feed_vetoes(snap) == []                                 # D-51: only OI / taker / candles are required
    snap = snapshot(c15, now, oi_rows=oi, trades_rows=tr, book_rows=[])
    assert mr._feed_vetoes(snap) == []


# ---------------------------------------------------------------------------
# FVG fallback (M1 second attempt) and D-11 trail
# ---------------------------------------------------------------------------
def test_reclaim_fvg_finds_bullish_gap_after_reclaim():
    base = _c15()
    t0 = base[-1].ts
    # reclaim candle, then a gap-up leaving a bullish 15m FVG (c[i-1].h < c[i+1].l)
    ext = [candle(t0 + M15, 100.0, 100.4, 99.6, 100.3),
           candle(t0 + 2 * M15, 100.3, 101.6, 100.2, 101.5),
           candle(t0 + 3 * M15, 101.5, 102.0, 101.0, 101.8),
           candle(t0 + 4 * M15, 101.8, 101.9, 101.4, 101.6)]
    c15 = base + ext
    snap = snapshot(c15, c15[-1].ts + 30_000)
    z = mr.ModelTradeManager._reclaim_fvg(snap, "long", t0 + M15, 6)
    assert z is not None and z.type == "FVG" and z.direction == "bullish"
    assert z.bottom == pytest.approx(100.4) and z.top == pytest.approx(101.0)
    assert mr.ModelTradeManager._reclaim_fvg(snap, "short", t0 + M15, 6) is None


def test_trail_stop_uses_confirmed_higher_low_only():
    base = _c15()
    t0 = base[-1].ts
    fill_ts = t0
    # after fill: dip to a low (99.2), rally, a higher low (99.8) confirmed by k=2 candles, price 101
    path = [100.2, 99.6, 99.2, 99.7, 100.3, 100.6, 100.1, 99.8, 100.4, 100.9, 101.0]
    c15 = base + path15(path, t0, spread=0.05)
    snap = snapshot(c15, c15[-1].ts + 30_000)
    st = mr.ModelTradeManager._trail_stop(snap, "15m", "long", fill_ts, cur_stop=98.5)
    assert st is not None and st == pytest.approx(99.75)               # 99.8 close − 0.05 spread = swing low
    # never worsens the stop, never above price
    assert mr.ModelTradeManager._trail_stop(snap, "15m", "long", fill_ts, cur_stop=99.9) is None
    # short mirror: no lower high formed since fill in this rising tape
    assert mr.ModelTradeManager._trail_stop(snap, "15m", "short", fill_ts, cur_stop=102.0) is None


# ---------------------------------------------------------------------------
# JSON coercion + outbox model tagging
# ---------------------------------------------------------------------------
def test_jsonable_coerces_numpy_nan_tuples_dataclasses():
    @dataclasses.dataclass
    class D:
        a: int
        b: float
    out = mr.jsonable({"n": np.float64(1.5), "i": np.int64(3), "nan": float("nan"), "inf": math.inf,
                       "t": (1, 2), "d": D(1, 2.5), "s": {"x"}, "b": True, "none": None})
    assert out == {"n": 1.5, "i": 3, "nan": None, "inf": None, "t": [1, 2], "d": {"a": 1, "b": 2.5}, "s": ["x"],
                   "b": True, "none": None}
    assert isinstance(out["n"], float) and isinstance(out["i"], int)


def test_loads_tolerates_bad_json():
    assert mr._loads(None) is None
    assert mr._loads({"a": 1}) == {"a": 1}
    assert mr._loads('{"a": 1}') == {"a": 1}
    assert mr._loads("not json") is None


def test_outbox_model_of_kind():
    class Row:
        def __init__(self, kind):
            self.kind = kind
    assert _model_of(Row("M1_fill")) == "M1"
    assert _model_of(Row("M6_exit")) == "M6"
    assert _model_of(Row("M4")) == "M4"
    assert _model_of(Row("leader_open")) is None
    assert _model_of(Row("MM_bot")) is None
    assert _model_of(Row("")) is None
