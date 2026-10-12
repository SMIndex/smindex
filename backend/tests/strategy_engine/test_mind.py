"""Mind framework (doc 10 §4): evaluate / veto / multipliers / size tiers, manage
(thesis_failed, dead_trade), snapshot accessors on synthetic feeds, and the
weekly learn/calibration statistics."""
import math

from app.strategy_engine.mind import learn
from app.strategy_engine.mind.base import ContextRule, InTradeCheck, Mind, Position, Reason, Veto
from app.strategy_engine.mind.snapshot import build_snapshot
from app.strategy_engine.structure.candles import Candle, DAY_MS, TF_MS

M15, H1, H4 = TF_MS["15m"], TF_MS["1h"], TF_MS["4h"]
MON = 1_788_739_200_000


def path(prices, tf_ms, start=MON, spread=0.5, v=100.0):
    out = []
    for i, p in enumerate(prices):
        o = prices[i - 1] if i else p
        out.append(Candle(start + (i + 1) * tf_ms, o, max(o, p) + spread, min(o, p) - spread, p, v))
    return out


def zigzag(n, base=100.0, amp=5.0, drift=0.0, period=8):
    return [base + drift * i + amp * math.sin(2 * math.pi * i / period) for i in range(n)]


class Snap:  # minimal stand-in for Mind.evaluate tests
    def __init__(self, **kw):
        self.__dict__.update(kw)
        self.extra_vetoes = kw.get("extra_vetoes", [])


def _mind(exit_threshold=2):
    return Mind("MX",
                reasons=[Reason("a", "A", lambda s: s.a, 2.0), Reason("b", "B", lambda s: s.b, 1.0),
                         Reason("boom", "raises", lambda s: 1 / 0, 1.0)],
                vetoes=[Veto("v", "veto text", lambda s: s.v)],
                rules=[ContextRule("m", lambda s: s.m)],
                checks=[InTradeCheck("c1", "one", lambda s, p: s.c1), InTradeCheck("c2", "two", lambda s, p: s.c2, counts=2)],
                exit_threshold=exit_threshold)


def test_evaluate_weighted_average_and_tiers():
    m = _mind()
    # a=1.0 (w2), b=0.5 (w1), boom → 0 (w1): raw = (2 + 0.5 + 0)/4 = 0.625
    d = m.evaluate(Snap(a=1.0, b=0.5, v=False, m=1.0))
    assert abs(d.raw_conviction - 0.625) < 1e-6 and d.size_tier == "half" and d.take and d.risk_pct == 0.75
    d = m.evaluate(Snap(a=1.0, b=0.5, v=False, m=1.2))
    assert abs(d.conviction - 0.75) < 1e-6 and d.size_tier == "full" and d.risk_pct == 1.5
    d = m.evaluate(Snap(a=0.5, b=0.5, v=False, m=1.0))
    assert d.size_tier == "none" and not d.take
    rj = d.reasons_json()
    assert [r["key"] for r in rj] == ["a", "b", "boom"] and abs(sum(r["contribution"] for r in rj) - d.raw_conviction) < 1e-6


def test_veto_blocks_even_at_full_conviction():
    m = _mind()
    d = m.evaluate(Snap(a=1.0, b=1.0, v=True, m=1.5))
    assert not d.take and d.size_tier == "none" and d.vetoes_hit == ["v"] and d.vetoes_json()[0]["text"] == "veto text"
    d = m.evaluate(Snap(a=1.0, b=1.0, v=False, m=1.5, extra_vetoes=[("no_setup", "no sweep")]))
    assert not d.take and "no_setup" in d.vetoes_hit


def test_strength_clipped_and_bad_multiplier_ignored():
    m = _mind()
    d = m.evaluate(Snap(a=7.0, b=-3.0, v=False, m=float("nan")))
    assert d.reasons[0][1] == 1.0 and d.reasons[1][1] == 0.0 and d.multipliers == [("m", 1.0)]


def test_apply_weights_clipped():
    m = _mind()
    m.apply_weights({"a": 99.0, "b": 0.01})
    assert m.weights()["a"] == 3.0 and m.weights()["b"] == 0.3


def _pos(now, fill, hold=90, entry=100.0, stop=99.0):
    return Position(1, "MX", "BTC", "long", entry, stop, 102.0, 1.0, fill, abs(entry - stop), hold, now_ms=now)


def test_manage_thesis_failed_needs_two_consecutive_candles():
    m = _mind()
    p = _pos(MON + 30 * 60_000, MON)
    r = m.manage(Snap(c1=True, c2=False), p, 100.2)       # count 1 < threshold 2
    assert r.action == "hold" and r.streak == 0
    r = m.manage(Snap(c1=False, c2=True), p, 100.2)       # count 2 → streak 1
    assert r.action == "hold" and r.streak == 1
    r = m.manage(Snap(c1=True, c2=False), p, 100.2)       # reset
    assert r.streak == 0
    m.manage(Snap(c1=True, c2=True), p, 100.2)
    r = m.manage(Snap(c1=False, c2=True), p, 100.2)
    assert r.action == "exit" and r.reason == "thesis_failed" and r.streak == 2


def test_manage_dead_trade_at_150pct_hold_without_progress():
    m = _mind()
    p = _pos(MON + 135 * 60_000, MON, hold=90)             # 135 min = 1.5 × 90
    r = m.manage(Snap(c1=False, c2=False), p, 100.3)       # progress 0.3R
    assert r.action == "exit" and r.reason == "dead_trade"
    p2 = _pos(MON + 135 * 60_000, MON, hold=90)
    r = m.manage(Snap(c1=False, c2=False), p2, 100.6)      # 0.6R → survives
    assert r.action == "hold"


# ---- snapshot on synthetic feeds --------------------------------------------------
def _snapshot(now=None, **kw):
    n15 = 96 * 12
    start = MON - 12 * DAY_MS
    c15 = path(zigzag(n15, amp=2.0, period=16), M15, start=start)
    c1h = path(zigzag(n15 // 4, amp=3.0, period=12), H1, start=start)
    c4h = path(zigzag(n15 // 16, amp=4.0, period=10), H4, start=start)
    now = now or (c15[-1].ts + 60_000)
    oi = [{"ts": now - 25 * H1 + i * 60_000, "oi_notional": 1_000_000 * (1 + 0.0001 * i)} for i in range(25 * 60)]
    tr = [{"ts": now - 3 * H1 + (i + 1) * 60_000, "taker_buy_notional": 600.0, "taker_sell_notional": 400.0} for i in range(180)]
    book = [{"ts": now - H1 + i * 5_000, "mid": 100.0, "bid_0_3": 50_000.0 + i, "ask_0_3": 40_000.0} for i in range(720)]
    liq = [{"ts": now - 4 * 60_000, "side": "short", "notional": 30_000.0, "liquidated_user": "0xa"},
           {"ts": now - 3 * 60_000, "side": "short", "notional": 20_000.0, "liquidated_user": "0xb"},
           {"ts": now - 50 * 60_000, "side": "long", "notional": 5_000.0, "liquidated_user": "0xc"}]
    gauge = {"funding_z": 1.2, "crowding_level": "ELEVATED", "blocked_direction": "long"}
    g24 = [{"funding_z": z} for z in (0.5, 2.1, 1.2)]
    cohort = {"net_dir": 0.4, "net_dir_24h_ago": 0.1, "fresh_long": 3, "fresh_short": 1}
    base = dict(oi_rows=oi, trades_rows=tr, book_rows=book, liq_rows=liq, gauge=gauge, gauge_24h=g24,
                cohort=cohort, recent_form=["win", "loss", "loss"])
    base.update(kw)
    return build_snapshot("BTC", now, c15, c1h, c4h, **base)


def test_snapshot_feed_accessors():
    s = _snapshot()
    ch = s.oi_changes()
    assert ch["1h"] is not None and abs(ch["1h"] - 0.006 / (1 + 0.0001 * (24 * 60))) < 1e-3
    assert ch["24h"] is not None and ch["24h"] > 0.1
    assert s.oi_change_span(30 * DAY_MS) is None                      # before the feed → None, never invented
    t = s.taker_span(H1)
    assert t and abs(t["ratio"] - 0.6) < 1e-9 and t["n"] == 60
    assert s.taker_span(10 * H1)["n"] == 180                          # only 3h of tape exists
    n, w = s.liq_span("short", 5 * 60_000)
    assert n == 50_000.0 and w == 2
    assert s.liq_span("long", 5 * 60_000) == (0.0, 0)
    assert s.funding_z == 1.2 and s.funding_z_max_24h() == 2.1 and s.funding_z_min_24h() == 0.5
    assert s.funding_extreme("long") is False
    assert s.depth("bid_0_3") == 50_719.0 and abs(s.depth_avg("bid_0_3") - (50_000 + 719 / 2)) < 1e-6
    assert abs(s.cohort_net_long_change_24h - 0.3) < 1e-9 and s.cohort_fresh_adds("long") == 3
    assert s.consecutive("loss") == 2 and s.consecutive("win") == 0
    assert s.oi_frac(0.1) > 0
    assert s.day_type in ("range", "trend_up", "trend_down", "no_trade", "squeeze", "event")
    assert s.a["day_type"] == s.day_type
    assert s.session in ("asia", "london", "newyork", "dead")


def test_snapshot_empty_feeds_read_none_or_zero():
    s = _snapshot(oi_rows=[], trades_rows=[], book_rows=[], liq_rows=[], gauge=None, gauge_24h=[], cohort={},
                  recent_form=[])
    assert s.oi_now is None and s.oi_change_span(H1) is None and s.taker_span(H1) is None
    assert s.liq_span("short", H1) == (0.0, 0) and s.funding_z is None and s.depth("bid_0_3") is None
    assert s.cohort_net_long_change_24h is None and s.oi_frac(0.1) == 0.0 and s.consecutive("loss") == 0
    assert s.funding_extreme("long") is False
    assert s.day_type == "range"


def test_snapshot_cvd_and_event_flags():
    s = _snapshot()
    cvd = s.cvd(s.s.c15[-12:])
    assert cvd[-1] is not None and cvd[-1] > 0
    now = s.now_ms
    s2 = _snapshot(events_ts=[now + 20 * 60_000])
    assert s2.s.event_within_30m and s2.s.event_within_2h and s2.day_type == "event"


# ---- learn / calibration statistics --------------------------------------------------
def test_reason_stats_and_weight_proposals():
    trades = []
    for i in range(120):
        strong = i % 2 == 0
        trades.append({"r": (1.0 if strong else 0.0), "conviction": 0.8 if strong else 0.6,
                       "reasons": [{"key": "a", "strength": 0.9 if strong else 0.1},
                                   {"key": "b", "strength": 0.5}]})
    st = learn.reason_stats(trades)
    assert st["a"]["n_strong"] == 60 and st["a"]["n_weak"] == 60 and abs(st["a"]["diff"] - 1.0) < 1e-9
    assert "b" not in st or st["b"]["diff"] is None
    m = _mind()
    new, review, log = learn.propose_weights(st, m, 120, {"a": 2.0})
    assert new == {"a": 2.05} and not review and log[0]["from"] == 2.0
    # 30-day cap: base 1.6 → max 2.0; current 2.0 → no move
    new, _, _ = learn.propose_weights(st, m, 120, {"a": 1.6})
    assert new == {}
    # under 100 trades nothing moves
    assert learn.propose_weights(st, m, 99, {})[0] == {}


def test_review_flag_after_200_flat_trades():
    trades = [{"r": 0.5, "conviction": 0.7, "reasons": [{"key": "a", "strength": 0.9 if i % 2 else 0.1}]} for i in range(200)]
    st = learn.reason_stats(trades)
    m = _mind()
    new, review, _ = learn.propose_weights(st, m, 200, {})
    assert new == {} and review == ["a"]


def test_calibration_buckets_and_discrimination():
    trades = [{"conviction": 0.6, "r": -0.2}] * 60 + [{"conviction": 0.7, "r": 0.3}] * 30 + [{"conviction": 0.9, "r": 0.9}] * 30
    cal = learn.calibration(trades)
    assert cal["0.55-0.65"]["trades"] == 60 and cal["0.55-0.65"]["win_rate"] == 0.0
    assert cal["0.75+"]["trades"] == 30 and abs(cal["0.75+"]["mean_r"] - 0.9) < 1e-9
    assert learn.not_discriminating(cal, 120) is False
    flat = [{"conviction": 0.6, "r": 0.5}] * 60 + [{"conviction": 0.9, "r": 0.6}] * 60
    assert learn.not_discriminating(learn.calibration(flat), 120) is True
    assert learn.not_discriminating(learn.calibration(flat), 50) is False
    assert learn.bucket_of(0.5) is None and learn.bucket_of(0.75) == "0.75+"
    assert learn.iso_week(MON) == "2026-W37"
