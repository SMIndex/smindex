"""Structure engine (doc 10 §2) on synthetic candles: swings, BOS/CHoCH,
displacement, order blocks, FVGs, sweeps, premium/discount, sessions, daily
aggregation, day-type classifier and the alignment table."""
import math

from app.strategy_engine.structure import day_type as dt
from app.strategy_engine.structure import displacement as disp
from app.strategy_engine.structure import sessions as ses
from app.strategy_engine.structure import sweeps as sw_mod
from app.strategy_engine.structure import zones as zn
from app.strategy_engine.structure.alignment import build_structure, format_table
from app.strategy_engine.structure.candles import Candle, DAY_MS, TF_MS, aggregate_daily, closed, to_candles
from app.strategy_engine.structure.ranges import current_range
from app.strategy_engine.structure.swings import Swing, highs, lows, swings
from app.strategy_engine.structure.trend import structure, trend_label

M15 = TF_MS["15m"]
H1 = TF_MS["1h"]
H4 = TF_MS["4h"]
D1 = DAY_MS
# 2026-09-07 (Monday) 00:00 UTC
MON = 1_788_739_200_000
assert ses.weekday(MON) == 0


def path(prices, tf_ms, start=MON, spread=0.5, v=100.0):
    """Candles closing at start + i*tf, close = prices[i], small wick spread."""
    out = []
    for i, p in enumerate(prices):
        o = prices[i - 1] if i else p
        out.append(Candle(start + (i + 1) * tf_ms, o, max(o, p) + spread, min(o, p) - spread, p, v))
    return out


def zigzag(n, base=100.0, amp=5.0, drift=0.0, period=8):
    return [base + drift * i + amp * math.sin(2 * math.pi * i / period) for i in range(n)]


# ---- swings / trend ---------------------------------------------------------
def test_swings_k2_on_15m_and_confirmation():
    c = path(zigzag(40), M15)
    s = swings(c, "15m")
    assert s and all(x.confirmed_at == c[x.index + 2].ts for x in s)
    assert highs(s) and lows(s)
    # each swing high is strictly above its 2 left neighbours
    for h in highs(s):
        assert all(h.price > c[j].h for j in range(h.index - 2, h.index))


def test_swings_k2_on_1h_and_4h_k3_on_daily():
    # spec v1.1 (D-64): k = 2 on 15m, 1h and 4h; 3 on daily
    c = path(zigzag(40), H1)
    s = swings(c, "1h")
    assert s and all(x.confirmed_at == c[x.index + 2].ts for x in s)
    c4 = path(zigzag(40), H4)
    s4 = swings(c4, "4h")
    assert s4 and all(x.confirmed_at == c4[x.index + 2].ts for x in s4)
    cd = path(zigzag(40), D1)
    sd = swings(cd, "1d")
    assert sd and all(x.confirmed_at == cd[x.index + 3].ts for x in sd)


def test_trend_label_needs_two_ascending_highs_and_lows():
    # spec v1.1 (D-64): 2 swing highs + 2 swing lows decide the label (was 3)
    def sw(kind, prices):
        return [Swing(i, p, kind, "1h", i, i) for i, p in enumerate(prices)]
    assert trend_label(sw("high", [10, 11]), sw("low", [8, 9])) == "up"
    assert trend_label(sw("high", [11, 10]), sw("low", [9, 8])) == "down"
    assert trend_label(sw("high", [10, 11]), sw("low", [9, 8])) == "range"     # highs up, lows down
    assert trend_label(sw("high", [10, 11, 12]), sw("low", [9, 8, 7])) == "range"  # highs up, last two lows down
    assert trend_label(sw("high", [10, 11, 12]), sw("low", [9, 7, 8])) == "up"    # only the LAST two count: 7→8, 11→12
    assert trend_label(sw("high", [10]), sw("low", [8, 9])) == "range"           # fewer than 2 highs


def test_trend_label_up_down_range():
    c_up = path(zigzag(64, drift=1.0), M15)
    c_dn = path(zigzag(64, drift=-1.0), M15)
    c_rg = path(zigzag(64), M15)
    for cs, want in ((c_up, "up"), (c_dn, "down"), (c_rg, "range")):
        s = swings(cs, "15m")
        assert trend_label(highs(s), lows(s)) == want


def test_bos_up_in_uptrend_and_choch_on_breakdown():
    up = zigzag(64, drift=1.0)
    c = path(up, M15)
    st = structure(c, swings(c, "15m"), "15m")
    assert st.trend == "up"
    assert any(e.type == "BOS" and e.direction == "up" for e in st.events)
    # now break: fall hard through the last swing low
    crash = up + [up[-1] - 6 * (i + 1) for i in range(6)]
    c2 = path(crash, M15)
    st2 = structure(c2, swings(c2, "15m"), "15m")
    assert any(e.type == "CHoCH" and e.direction == "down" for e in st2.events)
    assert st2.trend in ("range", "down")


def test_bos_down_in_downtrend():
    c = path(zigzag(64, drift=-1.0), M15)
    st = structure(c, swings(c, "15m"), "15m")
    assert st.trend == "down"
    assert any(e.type == "BOS" and e.direction == "down" for e in st.events)


# ---- displacement / zones ---------------------------------------------------
def test_displacement_grade_formula_and_detection():
    atr = 1.0
    # spec v1.1 (D-64): 0.5·min(range/ATR − 1.5, 1) + 0.5·min((body − 0.6)/0.3, 1), clipped 0..1
    assert disp.grade(1.5, 0.6, atr) == 0.0          # exactly at the qualifying thresholds
    assert abs(disp.grade(2.5, 0.9, atr) - 1.0) < 1e-9
    assert abs(disp.grade(2.0, 0.75, atr) - 0.5) < 1e-9
    assert disp.grade(3.5, 1.0, atr) == 1.0          # capped
    assert disp.grade(0.75, 0.6, atr) == 0.0         # below the range threshold clips to 0
    assert disp.grade(1.0, 0.0, 0.0) == 0.0
    big = Candle(MON + M15, 100.0, 102.0, 99.9, 101.9, 1.0)   # range 2.1, body 1.9 → 90%
    small = Candle(MON + M15, 100.0, 100.5, 99.9, 100.2, 1.0)
    assert disp.is_displacement(big, atr) and not disp.is_displacement(small, atr)
    d = disp.detect([small, big], atr, 1)
    assert d and d.direction == "up" and d.start_index == 1


def test_two_candle_displacement():
    a = Candle(MON + M15, 100.0, 100.9, 99.95, 100.8, 1.0)
    b = Candle(MON + 2 * M15, 100.8, 101.8, 100.75, 101.7, 1.0)
    tc = disp.two_candle(a, b, atr=1.0)
    assert tc and tc[0] == "up"


def test_displacement_leg_v12():
    """spec v1.2 (D-75): a leg of up to 3 consecutive same-direction candles qualifies on the
    COMBINED range (>= 1.5 ATR) and NET body (last close - first open >= 60% of the combined range);
    grade on those two numbers; an opposite candle inside the window breaks the leg."""
    atr = 1.0
    cs = [Candle(MON + M15, 100.0, 100.2, 99.4, 99.5, 1.0),           # down candle (the OB)
          Candle(MON + 2 * M15, 99.5, 100.3, 99.45, 100.2, 1.0),      # up, range 0.85
          Candle(MON + 3 * M15, 100.2, 101.0, 100.1, 100.9, 1.0),     # up, range 0.9
          Candle(MON + 4 * M15, 100.9, 101.9, 100.85, 101.8, 1.0)]    # up, range 1.05
    assert disp.leg_at(cs, atr, 3, "up", 1) is None                   # no single candle qualifies
    two = disp.leg_at(cs, atr, 3, "up", 2)                            # 100.1..101.9 = 1.8, body 1.6 -> 89%
    assert two and two.start_index == 2 and abs(two.high - 101.9) < 1e-9 and abs(two.low - 100.1) < 1e-9
    assert abs(two.grade - disp.grade(1.8, 1.6 / 1.8, atr)) < 1e-9
    three = disp.leg_at(cs, atr, 3, "up", 3)                          # 99.45..101.9 = 2.45, body 2.3 -> 94%
    assert three and three.start_index == 1 and abs(three.grade - disp.grade(2.45, 2.3 / 2.45, atr)) < 1e-9
    best = disp.detect_leg(cs, atr, 3, "up")
    assert best == three                                              # highest grade wins
    assert disp.leg_at(cs, atr, 3, "down", 2) is None                 # wrong direction
    ob = zn.order_block(cs, three, "15m")
    assert ob and ob.direction == "bullish" and ob.created_index == 0 # last opposite candle before the leg
    # net body below 60% of the combined range: a leg with a long wick fails
    wick = [Candle(MON + M15, 100.0, 100.5, 99.9, 100.4, 1.0), Candle(MON + 2 * M15, 100.4, 102.5, 100.3, 101.2, 1.0)]
    assert disp.leg_at(wick, atr, 1, "up", 2) is None                 # range 2.6, body 1.2 -> 46%
    # mirror: a down leg
    dn = [Candle(MON + M15, 100.0, 100.6, 99.9, 100.5, 1.0), Candle(MON + 2 * M15, 100.5, 100.55, 99.7, 99.8, 1.0),
          Candle(MON + 3 * M15, 99.8, 99.85, 98.9, 99.0, 1.0)]
    d = disp.detect_leg(dn, atr, 2, "down")
    assert d and d.direction == "down" and d.start_index == 1 and abs(d.high - 100.55) < 1e-9 and abs(d.low - 98.9) < 1e-9
    assert disp.detect_leg(dn, atr, 2, "up") is None


def test_order_block_and_fvg_from_displacement():
    # down candle, then a strong up displacement; the OB is the down candle's body
    cs = [Candle(MON + M15, 100.0, 100.3, 99.5, 99.6, 1.0),
          Candle(MON + 2 * M15, 99.6, 99.9, 99.4, 99.5, 1.0),
          Candle(MON + 3 * M15, 99.5, 102.0, 99.45, 101.9, 1.0),
          Candle(MON + 4 * M15, 101.9, 103.0, 101.8, 102.8, 1.0)]
    d = disp.detect(cs, 1.0, 2)
    assert d and d.direction == "up"
    ob = zn.order_block(cs, d, "15m")
    assert ob and ob.direction == "bullish" and ob.top == 99.6 and ob.bottom == 99.5 and ob.status == "fresh"
    f = zn.fvg_at(cs, 2, "15m")
    assert f and f.direction == "bullish" and f.bottom == 99.9 and f.top == 101.8
    # a later wick into the OB → tested; a close below → broken
    zn.update_status(ob, [Candle(MON + 5 * M15, 102.8, 102.9, 99.55, 102.0, 1.0)])
    assert ob.status == "tested"
    zn.update_status(ob, [Candle(MON + 6 * M15, 102.0, 102.1, 99.0, 99.2, 1.0)])
    assert ob.status == "broken" and not ob.live


def test_fvg_fill_states():
    cs = [Candle(MON + M15, 100.0, 100.3, 99.5, 99.6, 1.0),
          Candle(MON + 2 * M15, 99.6, 99.9, 99.4, 99.5, 1.0),
          Candle(MON + 3 * M15, 99.5, 102.0, 99.45, 101.9, 1.0),
          Candle(MON + 4 * M15, 101.9, 103.0, 101.8, 102.8, 1.0)]
    f = zn.fvg_at(cs, 2, "15m")
    zn.update_status(f, [Candle(MON + 5 * M15, 102.8, 102.9, 100.8, 102.0, 1.0)])
    assert f.status == "half_filled"
    zn.update_status(f, [Candle(MON + 6 * M15, 102.0, 102.1, 99.8, 101.0, 1.0)])
    assert f.status == "filled" and not f.live


# ---- sweeps -----------------------------------------------------------------
def test_sweep_detection_reclaim_and_quality():
    level, atr = 100.0, 1.0
    cs = [Candle(MON + M15, 101.0, 101.2, 100.5, 100.8, 1.0),
          Candle(MON + 2 * M15, 100.8, 100.9, 99.7, 99.9, 1.0),     # wick 0.3 ATR below, closes below
          Candle(MON + 3 * M15, 99.9, 100.9, 99.8, 100.8, 1.0),     # reclaim, body 1.0 / range 1.1
          Candle(MON + 4 * M15, 100.8, 101.0, 100.6, 100.9, 1.0)]
    s = sw_mod.detect_sweeps(cs, level, atr, "low")
    assert len(s) == 1 and s[0].reclaimed and s[0].reclaim_index == 2 and s[0].candles_to_reclaim == 2
    assert abs(s[0].depth_atr - 0.3) < 1e-9 and not s[0].too_deep
    assert abs(s[0].reclaim_quality - 0.9 / 1.1) < 1e-6      # body 0.9 / range 1.1


def test_sweep_too_deep_and_no_reclaim():
    level, atr = 100.0, 1.0
    cs = [Candle(MON + M15, 100.5, 100.6, 99.3, 99.4, 1.0),      # 0.7 ATR → too deep
          Candle(MON + 2 * M15, 99.4, 99.6, 99.2, 99.3, 1.0),
          Candle(MON + 3 * M15, 99.3, 99.5, 99.1, 99.2, 1.0),
          Candle(MON + 4 * M15, 99.2, 99.4, 99.0, 99.1, 1.0)]
    s = sw_mod.detect_sweeps(cs, level, atr, "low")
    assert s and s[0].too_deep and not s[0].reclaimed


def test_sweep_high_side():
    level, atr = 100.0, 1.0
    cs = [Candle(MON + M15, 99.5, 100.25, 99.4, 99.9, 1.0),      # wick 0.25 above, closes back inside → 1 candle
          Candle(MON + 2 * M15, 99.9, 99.95, 99.5, 99.6, 1.0)]
    s = sw_mod.detect_sweeps(cs, level, atr, "high")
    assert s and s[0].reclaimed and s[0].candles_to_reclaim == 1


# ---- ranges / premium-discount ------------------------------------------------
def test_range_premium_discount():
    c = path(zigzag(64), H4)
    s = swings(c, "4h")
    now = c[-1].ts
    r_hi = current_range(c, s, "4h", 104.0, now)
    r_lo = current_range(c, s, "4h", 96.0, now)
    assert r_hi and r_hi.position(104.0) == "premium" and r_hi.pct(104.0) > 0.5
    assert r_lo and r_lo.position(96.0) == "discount"
    assert abs(r_hi.mid - (r_hi.high + r_hi.low) / 2) < 1e-9


# ---- sessions / daily aggregation --------------------------------------------
def test_sessions_and_asia_range():
    assert ses.session_of(MON + 30 * 60_000)[0] == "asia"
    assert ses.session_of(MON + 8 * H1) == ("london", 60)
    assert ses.session_of(MON + 14 * H1)[0] == "newyork"
    assert ses.session_of(MON + 22 * H1)[0] == "dead"
    c = path([100 + (i % 5) for i in range(96)], M15, start=MON)
    a = ses.asia_range(c, MON)
    assert a and a["high"] >= a["low"] and a["n"] == 28
    assert ses.week_start(MON + 3 * DAY_MS + 5 * H1) == MON


def test_aggregate_daily_needs_full_days():
    c4 = path(zigzag(6 * 5 + 3), H4)  # 5 full days + 3 candles
    d = aggregate_daily(c4)
    assert len(d) == 5
    first6 = c4[:6]
    assert d[0].o == first6[0].o and d[0].c == first6[-1].c
    assert d[0].h == max(x.h for x in first6) and d[0].l == min(x.l for x in first6)
    assert d[0].ts == first6[-1].ts


def test_closed_excludes_open_candle():
    c = path([1, 2, 3], M15)
    assert len(closed(c, c[1].ts)) == 2
    rows = [{"ts": x.ts, "o": x.o, "h": x.h, "l": x.l, "c": x.c, "v": x.v} for x in reversed(c)]
    assert [x.ts for x in to_candles(rows)] == [x.ts for x in c]


# ---- day type -----------------------------------------------------------------
def test_day_type_classifier_order():
    assert dt.classify(dt.DayInputs(event_within_2h=True)) == "event"
    assert dt.classify(dt.DayInputs(funding_z=2.5, price_move_2h=-3.0, atr_1h=1.0, oi_change_2h=-0.03)) == "squeeze"
    assert dt.classify(dt.DayInputs(daily_open=105.0, prior_vah=100.0, oi_change_4h=0.02, delta_skew_2h=0.65,
                                    trend_1h="up")) == "trend_up"
    assert dt.classify(dt.DayInputs(daily_open=95.0, prior_val=100.0, oi_change_4h=0.02, delta_skew_2h=0.35,
                                    trend_1h="down")) == "trend_down"
    assert dt.classify(dt.DayInputs(rv_pct=10.0, vol_2h_ratio=0.3)) == "no_trade"
    assert dt.classify(dt.DayInputs()) == "range"


# ---- alignment table -----------------------------------------------------------
def test_build_structure_alignment_table_fields():
    days = 40
    n15 = 96 * days
    c15 = path(zigzag(n15, amp=2.0, period=16), M15, start=MON - days * DAY_MS)
    c1h = path(zigzag(n15 // 4, amp=3.0, period=12), H1, start=MON - days * DAY_MS)
    c4h = path(zigzag(n15 // 16, amp=4.0, period=30), H4, start=MON - days * DAY_MS)
    now = c15[-1].ts + 5 * 60_000
    s = build_structure("BTC", now, c15, c1h, c4h)
    a = s.alignment
    for k in ("daily_bias", "trend_4h", "trend_1h", "range_4h", "range_1h", "nearest_pool_above", "nearest_pool_below",
              "session", "day_type", "weekly_open", "daily_open", "pdh", "pdl", "pwh", "pwl"):
        assert k in a
    assert a["swings"]["15m"] > 0 and a["swings"]["1h"] > 0 and a["swings"]["4h"] > 0 and a["swings"]["1d"] > 0
    assert a["trend_4h"] in ("up", "down", "range")
    assert a["range_4h"]["position"] in ("premium", "discount")
    assert a["pools_above"] >= 1 and a["pools_below"] >= 1
    assert a["day_type"] in dt.DAY_TYPES
    assert s.atr["15m"] > 0 and len(s.c1d) >= 10
    txt = format_table("BTC", a)
    assert "trend_4h" in txt and "range_4h" in txt
