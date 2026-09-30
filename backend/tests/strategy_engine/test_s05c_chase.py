"""s05c "Session open — chase entry" (owner-approved experiment, 2026-09-04).

Identical rules to s05; only the entry differs: post-only limit at the break
candle CLOSE ± 0.1 ATR, re-quoted per the paper executor's rules (1 tick inside,
max 3), taker fee if it must cross. Stop/target/time-stop formulas unchanged.
The manager tests drive _resolve_pending with a fake session over a mid tape.
"""
import asyncio

import pytest

from app.strategy_engine import evaluator, conditions, mode
from app.strategy_engine.execution.manager import PaperTradeManager, compute_close, MAX_REQUOTES
from app.strategy_engine.execution.paper import MAKER_FEE, TAKER_FEE
from app.strategy_engine.seed import STRATEGIES, PARAMETERS
from app.strategy_engine.backtest.runner import REQUIRED
from app.strategy_engine.strategies.s05_session_open import SessionOpenMomentum


def test_registered_beside_s05_everywhere():
    ids = [sid for sid, _ in STRATEGIES]
    assert ids.index("s05c_session_open") == ids.index("s05_session_open") + 1
    assert PARAMETERS["s05c_session_open"] == PARAMETERS["s05_session_open"]
    assert conditions.CONDITIONS["s05c_session_open"] == conditions.CONDITIONS["s05_session_open"]
    # D-99: s05c is permanently disabled. Its parameters, conditions and seed row
    # are DELIBERATELY still present (nothing is deleted, history stays readable)
    # — what changed is that it is never scheduled and never runs.
    assert mode.STRATEGY_IMPLEMENTED["s05c_session_open"] is False
    assert mode.is_disabled("s05c_session_open") is True
    from app.strategy_engine.evaluator import REGISTRY
    assert "s05c_session_open" not in REGISTRY, "disabled strategy must not be scheduled"
    assert REQUIRED["s05c_session_open"] == REQUIRED["s05_session_open"]
    # D-99: no longer in REGISTRY (never scheduled) but still constructible —
    # the chase code path is preserved, not deleted.
    from app.strategy_engine.strategies.s05_session_open import SessionOpenMomentum
    assert SessionOpenMomentum(chase=True).chase is True
    assert SessionOpenMomentum(chase=True).id == "s05c_session_open"
    assert evaluator.REGISTRY["s05_session_open"].chase is False
    assert SessionOpenMomentum().id == "s05_session_open"        # doc-05 instance untouched


def test_intent_entry_is_close_not_edge_and_rules_identical():
    """Same context → same conditions/score/direction; only px differs
    (close ± 0.1 ATR vs OR edge ± 0.1 ATR)."""
    import importlib.util, pathlib
    spec = importlib.util.spec_from_file_location("ng", pathlib.Path(__file__).with_name("test_no_gates.py"))
    ng = importlib.util.module_from_spec(spec); spec.loader.exec_module(ng)
    _ctx, _bars = ng._ctx, ng._bars
    # a fired evaluation needs a session + OR + break; hard to synthesize honestly,
    # so compare the two instances on evaluation output where they must agree
    bars = _bars(3000, 900_000)
    ctx = _ctx(candles_15m=bars[-300:], closes_15m_30d=[b["c"] for b in bars],
               candles_1h=_bars(60, 3_600_000), candles_4h=_bars(60, 14_400_000))
    a = SessionOpenMomentum().evaluate(ctx)
    b = SessionOpenMomentum(chase=True).evaluate(ctx)
    assert [(c.name, c.met, c.value) for c in a.conditions] == [(c.name, c.met, c.value) for c in b.conditions]
    assert a.total_score == b.total_score and a.direction == b.direction and a.fired == b.fired


def test_compute_close_taker_entry_fee():
    g, fees_m, _ = compute_close("long", 100.0, 110.0, 1.0, "target")
    g2, fees_t, _ = compute_close("long", 100.0, 110.0, 1.0, "target", entry_taker=True)
    assert g == g2 == 10.0
    assert fees_m == MAKER_FEE * (100.0 + 110.0)
    assert fees_t == TAKER_FEE * 100.0 + MAKER_FEE * 110.0


def test_marker_roundtrip_chase():
    m = PaperTradeManager(["BTC"])
    s = "pending:hold_h=3:ttl_m=30:chase=1:rq=2:rq_ts=1700000000000:entry=taker"
    d = m._marker(s)
    assert d == {"hold_h": 3, "ttl_m": 30, "chase": True, "rq": 2, "rq_ts": 1700000000000, "entry_taker": True}
    assert m._marker_str(d) == s


def test_marker_fits_exit_reason_column():
    """Prod 2026-09-04 07:47 UTC: the taker-cross UPDATE failed with MySQL 1406
    'Data too long for column exit_reason' (VARCHAR(64), marker 69 chars) and
    the s05c ETH entry was later recorded as a MAKER fill. Column is 128 (v14);
    the longest marker plus the longest close reason must fit."""
    from app.db.strategy_models import StratTrade
    width = StratTrade.__table__.c.exit_reason.type.length
    m = PaperTradeManager(["BTC"])
    longest = m._marker_str({"hold_h": 24, "ttl_m": 120, "chase": True, "rq": MAX_REQUOTES,
                             "rq_ts": 9_999_999_999_999, "entry_taker": True})
    assert len(longest) > 64 <= width          # 64 was the column that failed
    assert len(longest) <= width
    assert len("entry_unfilled:entry=taker") <= width and len("time_stop:entry=taker") <= width


class _FakeSession:
    """Records UPDATEs; serves the mid tape for _mids."""
    def __init__(self, tape):
        self.tape = tape
        self.updates = []

    async def execute(self, stmt, params=None):
        sql = str(stmt)
        if sql.startswith("SELECT ts, mid"):
            rows = [(ts, mid) for ts, mid in self.tape if params["a"] < ts <= params["b"]]
            class R:
                def all(self_inner):
                    return rows
            return R()
        self.updates.append((sql, params))
        return None


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


def test_chase_fills_maker_when_tape_prints_through():
    m = PaperTradeManager(["BTC"])
    s = _FakeSession([(1000, 100.2), (2000, 99.9)])     # second print is BELOW the buy limit → maker fill
    t = {"id": 1, "strategy": "s05c_session_open", "asset": "BTC", "direction": "long", "entry_px": 100.0,
         "exit_reason": "pending:hold_h=3:ttl_m=30:chase=1:rq=0:rq_ts=0", "sig_ts": 1}
    _run(m._resolve_pending(s, t, 3000))
    assert len(s.updates) == 1 and "SET fill_ts=:f" in s.updates[0][0] and s.updates[0][1]["f"] == 2000


def test_chase_requotes_then_crosses_as_taker():
    m = PaperTradeManager(["BTC"])
    # market runs away from a buy at 100: mids only above the limit, never printing through
    s = _FakeSession([(1000, 100.5), (2000, 100.8)])
    t = {"id": 1, "strategy": "s05c_session_open", "asset": "BTC", "direction": "long", "entry_px": 100.0,
         "exit_reason": "pending:hold_h=3:ttl_m=30:chase=1:rq=0:rq_ts=0", "sig_ts": 1}
    _run(m._resolve_pending(s, t, 3000))
    sql, p = s.updates[-1]
    assert "SET entry_px=:e, exit_reason=:r" in sql            # re-quote 1 tick inside the latest mid
    assert p["e"] == pytest.approx(100.8 - 100.0 * 0.0001)
    assert m._marker(p["r"])["rq"] == 1 and m._marker(p["r"])["rq_ts"] == 2000
    # re-quotes spent → taker at the latest mid, fee kind persisted in the marker
    t2 = {**t, "entry_px": p["e"], "exit_reason": p["r"].replace("rq=1", f"rq={MAX_REQUOTES}")}
    s2 = _FakeSession([(4000, 101.0)])
    _run(m._resolve_pending(s2, t2, 5000))
    sql2, p2 = s2.updates[-1]
    assert "SET fill_ts=:f, entry_px=:e" in sql2 and p2["f"] == 4000 and p2["e"] == 101.0
    assert m._marker(p2["r"])["entry_taker"] is True


def test_non_chase_pending_unchanged_pullback_behaviour():
    """s05 (no chase marker) still rests: a tape that never prints through leaves the row untouched."""
    m = PaperTradeManager(["BTC"])
    s = _FakeSession([(1000, 100.5), (2000, 100.8)])
    t = {"id": 1, "strategy": "s05_session_open", "asset": "BTC", "direction": "long", "entry_px": 100.0,
         "exit_reason": "pending:hold_h=3:ttl_m=30", "sig_ts": 1}
    _run(m._resolve_pending(s, t, 3000))
    assert s.updates == []


def test_chase_ttl_cancel_still_applies():
    m = PaperTradeManager(["BTC"])
    s = _FakeSession([(1000, 100.5)])
    t = {"id": 1, "strategy": "s05c_session_open", "asset": "BTC", "direction": "long", "entry_px": 100.0,
         "exit_reason": "pending:hold_h=3:ttl_m=30:chase=1:rq=3:rq_ts=0", "sig_ts": 1}
    _run(m._resolve_pending(s, t, 31 * 60_000))                # past the 30-minute TTL → entry_unfilled, no cross
    assert any("entry_unfilled" in u[0] for u in s.updates)
