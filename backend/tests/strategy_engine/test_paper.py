"""Paper executor tests (doc 00 §6, §7)."""
import math

from app.strategy_engine.execution import paper as P


def test_limit_fills_through_not_on_touch():
    # buy limit at 100: fills only when a trade prints BELOW 100, never at 100
    assert P.limit_fills_through("buy", 100.0, 99.99) is True
    assert P.limit_fills_through("buy", 100.0, 100.0) is False      # touch != fill
    assert P.limit_fills_through("buy", 100.0, 100.01) is False
    # sell limit at 100: fills only above 100
    assert P.limit_fills_through("sell", 100.0, 100.01) is True
    assert P.limit_fills_through("sell", 100.0, 100.0) is False


def test_stop_triggered():
    assert P.stop_triggered("long", 95.0, 95.0) is True            # at/below
    assert P.stop_triggered("long", 95.0, 94.0) is True
    assert P.stop_triggered("long", 95.0, 96.0) is False
    assert P.stop_triggered("short", 105.0, 105.0) is True         # at/above
    assert P.stop_triggered("short", 105.0, 104.0) is False


def test_fee_math_maker_vs_taker():
    assert math.isclose(P.fee_for("entry", 1000.0), 1000 * 0.00015)
    assert math.isclose(P.fee_for("target", 1000.0), 1000 * 0.00015)
    assert math.isclose(P.fee_for("stop", 1000.0), 1000 * 0.00045)


def test_would_cross_and_requote():
    assert P.would_cross("buy", 101.0, 100.0) is True             # buy above market crosses
    assert P.would_cross("buy", 99.0, 100.0) is False
    assert P.would_cross("sell", 99.0, 100.0) is True             # sell below market crosses
    assert math.isclose(P.requote_inside("buy", 100.0, 0.5), 99.5)
    assert math.isclose(P.requote_inside("sell", 100.0, 0.5), 100.5)


def test_place_requotes_one_tick_inside():
    ex = P.PaperExecutor(max_requotes=3)
    # a buy limit above market would cross → re-quote 1 tick inside and rest
    o = P.RestingOrder(id="o1", strategy_id="s05", asset="BTC", side="buy", kind="entry",
                       order_type="post_only_limit", px=200.0, sz=0.1, position_side="long")
    placed = ex.place(o, market_px=100.0, tick=0.5)
    assert placed.status == "resting"
    assert placed.requotes == 1
    assert math.isclose(placed.px, 99.5)          # 1 tick inside the 100 market
    assert placed in ex.resting


def test_place_skips_when_requote_budget_zero():
    # max_requotes=0 → a would-cross order is skipped immediately (the skip guard)
    ex = P.PaperExecutor(max_requotes=0)
    o = P.RestingOrder(id="o1b", strategy_id="s05", asset="BTC", side="buy", kind="entry",
                       order_type="post_only_limit", px=200.0, sz=0.1, position_side="long")
    placed = ex.place(o, market_px=100.0, tick=0.5)
    assert placed.status == "skipped"
    assert placed not in ex.resting


def test_place_and_fill_through():
    ex = P.PaperExecutor()
    o = P.RestingOrder(id="o2", strategy_id="s05", asset="BTC", side="buy", kind="entry",
                       order_type="post_only_limit", px=99.0, sz=0.1, position_side="long")
    ex.place(o, market_px=100.0, tick=0.5)        # 99 does not cross a 100 market
    assert o.status == "resting"
    none_fills = ex.on_trade("BTC", 99.0, ts=1)   # touch, no fill
    assert none_fills == []
    fills = ex.on_trade("BTC", 98.5, ts=2)        # trades through
    assert len(fills) == 1
    f = fills[0]
    assert f.kind == "entry" and f.px == 99.0
    assert math.isclose(f.fee, 99.0 * 0.1 * 0.00015)


def test_stop_order_fires_taker_fee():
    ex = P.PaperExecutor()
    stop = P.RestingOrder(id="s", strategy_id="s05", asset="BTC", side="sell", kind="stop",
                          order_type="trigger_market", px=95.0, sz=0.1, position_side="long")
    ex.resting.append(stop)
    fills = ex.on_trade("BTC", 95.0, ts=3)        # at trigger fires
    assert len(fills) == 1 and fills[0].kind == "stop"
    assert math.isclose(fills[0].fee, 95.0 * 0.1 * 0.00045)   # taker


def test_time_stop_expiry():
    assert P.PaperExecutor.is_expired(placed_ms=0, now_ms=90 * 60_000, max_hold_ms=90 * 60_000) is True
    assert P.PaperExecutor.is_expired(placed_ms=0, now_ms=89 * 60_000, max_hold_ms=90 * 60_000) is False
