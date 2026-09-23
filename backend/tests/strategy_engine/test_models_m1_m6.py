"""Docs 11–16 §Tests — one scenario family per model, run through the REAL
find_setup + Mind (no mocked decisions). Valid setups must TAKE; the two
named vetoes must be the recorded reason; the in-trade exit check must fire
after two consecutive closes beyond its level."""
from __future__ import annotations

import math

import pytest

from app.strategy_engine.mind.base import Position
from app.strategy_engine.strategies.m1_sweep_reclaim import M1SweepReclaim
from app.strategy_engine.strategies.m2_bos_order_block import M2BreakOfStructure
from app.strategy_engine.strategies.m3_failed_auction import M3FailedAuction
from app.strategy_engine.strategies.m4_htf_choch import M4ChangeOfCharacter
from app.strategy_engine.strategies.m5_session_liquidity_run import M5SessionLiquidityRun
from app.strategy_engine.strategies.m6_weekly_open_reclaim import M6WeeklyOpenReclaim
from app.strategy_engine.structure.candles import Candle, DAY_MS

from .model_fixtures import (H1, M15, MON, TUE, atr15, candle, feeds, path15, snapshot, with_setup, zigzag)

GAUGE = {"funding_z": 0.2, "crowding_level": "NORMAL", "blocked_direction": None}


def steps(start_px: float, deltas: list[float]) -> list[float]:
    out, p = [], start_px
    for d in deltas:
        p += d
        out.append(p)
    return out


def flat(n: int, px: float, wob: float = 0.15) -> list[float]:
    return [px + (wob if i % 2 else -wob) for i in range(n)]


def run(model, snap):
    """find_setup + Mind exactly as the runner does (fresh setup / extra_vetoes)."""
    return model.evaluate(with_setup(snap, {}, extra_vetoes=[]))


def assert_take(ev, direction: str):
    assert not ev.decision.vetoes_hit, ev.decision.veto_texts
    assert ev.decision.take is True, (ev.decision.conviction, ev.waiting_for)
    assert ev.intent is not None and ev.intent.direction == direction
    assert ev.decision.conviction >= 0.55
    assert ev.decision.size_tier in ("half", "full")


def assert_veto(ev, key: str):
    assert ev.decision.take is False
    assert key in ev.decision.vetoes_hit, (ev.decision.vetoes_hit, ev.waiting_for)


def position(model: str, direction: str, entry: float, stop: float, fill_ts: int, now_ms: int, setup: dict,
             hold_min: int = 600) -> Position:
    return Position(1, model, "BTC", direction, entry, stop, None, 1.0, fill_ts, abs(entry - stop), hold_min,
                    setup=dict(setup), now_ms=now_ms)


def exit_after_two(model, c15: list[Candle], pos: Position, price: float, split: int = -1):
    """manage() on two consecutive closed candles (c15[:split], then c15)."""
    a = snapshot(c15[:split], c15[split].ts - M15 + 30_000)
    b = snapshot(c15, c15[-1].ts + 30_000)
    r1 = model.mind.manage(a, pos, price)
    assert r1.action == "hold" and r1.streak == 1, r1
    r2 = model.mind.manage(b, pos, price)
    assert r2.action == "exit" and r2.reason == "thesis_failed", r2
    return r2


# =====================================================================================
# M1 — sweep and reclaim of yesterday's low (doc 11)
# =====================================================================================
def m1_scenario(raid_atr: float = 0.3, earlier_sweeps: int = 0, confirm: str = "same"):
    """confirm (spec v1.3 D-89): 'same' = sweep+reclaim on one candle followed by the confirming
    close (case b); 'later' = sweep candle closes below, the next candle reclaims (case a);
    'none' = same-candle sweep+reclaim with NO confirming candle (pre-v1.3 trigger → no setup)."""
    start = TUE - 12 * DAY_MS
    n = (12 * DAY_MS + 14 * H1 + M15) // M15
    c15 = path15(zigzag(n, base=100.0, amp=1.2, period=20), start)
    mon_i = [i for i, c in enumerate(c15) if MON < c.ts <= TUE]
    j = mon_i[20]
    c15[j] = candle(c15[j].ts, c15[j].o, c15[j].h, 97.0, c15[j].c)          # pdl = 97.0
    a = atr15(c15)
    tue_i = [i for i, c in enumerate(c15) if c.ts > TUE]
    for k in range(earlier_sweeps):                                          # earlier one-candle sweeps of 97.0 today
        i = tue_i[10 + 20 * k]
        c15[i] = candle(c15[i].ts, c15[i].o, c15[i].h, 97.0 - 0.3 * a, c15[i].c)
    last = c15[-1]
    wick = 97.0 - raid_atr * a
    o = 97.3 if raid_atr <= 0.5 else 97.15                                  # keep body/range ≥ 0.5 on the deeper raid
    if confirm == "later":
        c15[-1] = candle(last.ts, o, 97.4, wick, 96.9)                       # sweep candle closes BELOW the level
        c15.append(candle(last.ts + M15, 96.9, 98.4, wick + 0.01, 98.3))    # reclaim on the next candle (case a)
    else:
        c15[-1] = candle(last.ts, o, 98.4, wick, 98.3)                       # sweep + reclaim on one candle
        if confirm == "same":
            c15.append(candle(last.ts + M15, 98.3, 98.5, 97.6, 98.2))        # confirming close > 97.0, low ≥ wick (case b)
    rec = c15[-1] if confirm != "same" else c15[-2]                          # the reclaim candle
    now = c15[-1].ts + 30_000
    oi, tr, book = feeds(now, mid=c15[-1].c, br_steps={rec.ts - M15: 0.7})
    liq = [{"ts": rec.ts - 5 * 60_000, "side": "long", "notional": 4_000.0, "liquidated_user": "0xa"}]
    snap = snapshot(c15, now, oi_rows=oi, trades_rows=tr, book_rows=book, liq_rows=liq, gauge=GAUGE,
                    cohort={"net_dir": 0.2, "fresh_long": 2, "fresh_short": 0})
    return c15, snap


def test_m1_valid_sweep_and_reclaim():
    _, snap = m1_scenario()
    m = M1SweepReclaim()
    setup, missing = m.find_setup(snap)
    assert setup is not None, missing
    assert setup["direction"] == "long" and setup["level"] == 97.0 and setup["level_type"] == "pdl"
    assert 0.1 <= setup["depth_atr"] <= 0.5 and setup["reclaim_quality"] >= 0.5 and setup["in_discount"]
    assert setup["reclaim_candles"] == 1 and setup["confirmation_used"] is True
    assert setup["confirmation_ts"] == snap.s.c15[-1].ts and setup["reclaim_ts"] == snap.s.c15[-2].ts
    ev = run(m, snap)
    assert_take(ev, "long")
    assert ev.intent.stop_px < 97.0 < ev.intent.entry_px
    # D-89 entry: min(50% of the reclaim candle, reclaim close) − 0.05 ATR — always below the market
    rc = snap.s.c15[-2]
    a = snap.atr("15m")
    assert ev.intent.entry_px == pytest.approx(min((rc.h + rc.l) / 2, rc.c) - 0.05 * a)
    assert ev.intent.entry_px < snap.price
    assert ev.intent.entry_valid_until_ms == snap.s.c15[-1].ts + 3 * M15


def test_m1_same_candle_reclaim_without_confirmation_is_not_a_setup():
    """Pre-v1.3 this candle triggered immediately; D-89 waits for the confirming close."""
    _, snap = m1_scenario(confirm="none")
    setup, missing = M1SweepReclaim().find_setup(snap)
    assert setup is None and "no confirmed reclaim" in missing


def test_m1_later_candle_reclaim_triggers_on_reclaim_close():
    _, snap = m1_scenario(confirm="later")
    m = M1SweepReclaim()
    setup, missing = m.find_setup(snap)
    assert setup is not None, missing
    assert setup["reclaim_candles"] == 2 and setup["confirmation_used"] is False
    assert setup["confirmation_ts"] == setup["reclaim_ts"] == snap.s.c15[-1].ts
    ev = run(m, snap)
    assert_take(ev, "long")
    assert ev.intent.entry_px < snap.price


def test_m1_too_deep_veto():
    _, snap = m1_scenario(raid_atr=0.7)
    m = M1SweepReclaim()
    setup, missing = m.find_setup(snap)
    assert setup is not None and setup["too_deep"] is True, missing
    assert_veto(run(m, snap), "too_deep")


def test_m1_third_sweep_veto():
    _, snap = m1_scenario(earlier_sweeps=2)
    m = M1SweepReclaim()
    setup, missing = m.find_setup(snap)
    assert setup is not None and setup["sweeps_before_today"] >= 2, missing
    assert_veto(run(m, snap), "third_sweep")


def test_m1_in_trade_level_lost_exit():
    c15, _ = m1_scenario()
    fill_ts = c15[-1].ts
    # two more closed candles both closing back below the swept level
    c15 = c15 + path15([96.8, 96.6], fill_ts, spread=0.2)
    pos = position("M1", "long", 97.6, 96.5, fill_ts, c15[-1].ts + 30_000, {"level": 97.0})
    r = exit_after_two(M1SweepReclaim(), c15, pos, 96.6)
    assert "level_lost" in r.failing and r.fail_count >= 2


# =====================================================================================
# M2 — 1h break of structure with displacement, retrace into OB/FVG (doc 12)
# =====================================================================================
def m2_scenario(disp_step: float = 0.625, spread: float = 0.5, ob_depth: float = 0.1, oi_end: float = 1_020_000.0,
                day_type: str = "squeeze"):
    # day_type: the synthetic tape labels itself "range", which doc 12 only allows for a 4h-level
    # break (D-44); "squeeze" is allowed with a neutral 1.0 multiplier in both directions
    end = TUE + 10 * H1
    n = (14 * DAY_MS) // M15
    start = end - n * M15
    shift = (n - 13) - 24                      # peak 3h before `end`, confirmed (k=3) by the OB hour
    prices = [100.0 + 0.004 * i + 0.8 * math.sin(2 * math.pi * (i - shift) / 96) for i in range(n)]
    c15 = path15(prices, start, spread=spread)
    p0 = c15[-1].c
    c15 += path15(steps(p0, [-ob_depth / 4] * 4), end, spread=spread)                               # OB hour 10–11
    c15 += path15(steps(p0 - ob_depth, [disp_step] * 4), end + H1, spread=spread, v=400.0)           # displacement 11–12
    d_hi = c15[-1].c
    c15 += path15(steps(d_hi, [-0.4] * 4), end + 2 * H1, spread=0.05, v=60.0)                        # calm pullback → FVG kept
    c15 += path15(steps(c15[-1].c, [-0.05, -0.05]), end + 3 * H1, spread=0.05, v=60.0)               # resting in the zone
    now = c15[-1].ts + 30_000
    disp_t0, disp_t1 = end + H1, end + 2 * H1
    oi, tr, book = feeds(now, mid=c15[-1].c,
                         oi_steps={disp_t0: 1_000_000.0, disp_t0 + 30 * 60_000: (1_000_000.0 + oi_end) / 2, disp_t1: oi_end},
                         br_steps={disp_t0: 0.75, disp_t1: 0.45})
    liq = [{"ts": disp_t0 + 20 * 60_000, "side": "short", "notional": 700.0, "liquidated_user": "0xb"}]
    snap = snapshot(c15, now, oi_rows=oi, trades_rows=tr, book_rows=book, liq_rows=liq, gauge=GAUGE,
                    cohort={"net_dir": 0.2, "fresh_long": 2, "fresh_short": 0}, day_type_override=day_type)
    return c15, snap


def test_m2_valid_break_and_retrace():
    _, snap = m2_scenario()
    m = M2BreakOfStructure()
    setup, missing = m.find_setup(snap)
    assert setup is not None, missing
    assert setup["direction"] == "long" and setup["zone_premium"] is False
    assert setup["oi_break"] >= 0.01 and setup["br_break"] >= 0.65 and setup["stop_atr"] <= 1.2
    assert setup["fvg_bottom"] <= setup["entry"] <= setup["fvg_top"]
    ev = run(m, snap)
    assert_take(ev, "long")
    assert ev.intent.stop_px < setup["ob_bottom"]


def test_m2_short_covering_veto():
    _, snap = m2_scenario(oi_end=995_000.0)          # OI fell across the break → covering, not positioning
    m = M2BreakOfStructure()
    setup, missing = m.find_setup(snap)
    assert setup is not None and setup["oi_break"] <= 0, missing
    assert_veto(run(m, snap), "short_covering")


def test_m2_zone_premium_veto():
    _, snap = m2_scenario()
    m = M2BreakOfStructure()
    setup, _ = m.find_setup(snap)
    d = m.mind.evaluate(with_setup(snap, {**setup, "zone_premium": True}))
    assert d.take is False and "zone_premium" in d.vetoes_hit


def test_m2_in_trade_below_ob_exit():
    c15, snap = m2_scenario()
    setup, _ = M2BreakOfStructure().find_setup(snap)
    fill_ts = c15[-1].ts
    ob_edge = setup["ob_edge"]
    c15 = c15 + path15([ob_edge - 0.2, ob_edge - 0.3], fill_ts, spread=0.05)
    pos = position("M2", "long", setup["entry"], setup["stop"], fill_ts, c15[-1].ts + 30_000, {"ob_edge": ob_edge})
    r = exit_after_two(M2BreakOfStructure(), c15, pos, ob_edge - 0.3)
    assert "below_ob" in r.failing


# =====================================================================================
# M3 — failed auction at yesterday's high inside a range day, retest (doc 13)
# =====================================================================================
def m3_scenario(acceptance: bool = False, funding_z: float = 1.1, spread: float = 0.12):
    start = TUE - 12 * DAY_MS
    n = (12 * DAY_MS) // M15
    c15 = path15(zigzag(n, base=100.0, amp=0.9, period=32), start, spread=spread)
    mon_i = [i for i, c in enumerate(c15) if MON < c.ts <= TUE]
    j = mon_i[40]
    c15[j] = candle(c15[j].ts, c15[j].o, 101.5, c15[j].l, c15[j].c)                          # pdh = 101.5
    for i in mon_i:
        if i != j and c15[i].h > 101.3:
            c15[i] = Candle(c15[i].ts, min(c15[i].o, 101.2), 101.3, c15[i].l, min(c15[i].c, 101.2), c15[i].v)
    c15 += path15(zigzag(48, base=100.6, amp=0.35, period=16), TUE, spread=spread)             # Tue 00–12 range
    c15 += [candle(TUE + 12 * H1 + M15, c15[-1].c, 101.2, c15[-1].c - 0.05, 101.05)]           # prior swing high
    c15 += path15([100.9, 100.85, 100.95], TUE + 12 * H1 + M15, spread=0.08)
    fail_ts = TUE + 13 * H1 + M15
    c15 += [candle(fail_ts, 100.95, 101.7, 100.9, 101.1)]                                      # failure: h > pdh, c < prior swing high 101.2 (doc 13 step 4)
    if acceptance:                                                                             # two closes ABOVE the level, OI rising
        c15 += [candle(fail_ts + M15, 101.1, 101.6, 101.05, 101.55)]
        c15 += [candle(fail_ts + 2 * M15, 101.55, 101.65, 101.5, 101.6)]                       # still a retest of 101.7
    else:
        c15 += [candle(fail_ts + M15, 101.1, 101.4, 101.05, 101.2)]
        c15 += [candle(fail_ts + 2 * M15, 101.2, 101.65, 101.15, 101.35)]                      # retest within 0.2 ATR
    now = c15[-1].ts + 30_000
    oi_steps = {fail_ts - H1: 1_000_000.0, fail_ts - 30 * 60_000: 1_004_000.0, fail_ts - M15: 1_008_000.0}
    if acceptance:
        oi_steps.update({fail_ts + 5 * 60_000: 1_012_000.0, fail_ts + M15 + 5 * 60_000: 1_016_000.0})
    oi, tr, book = feeds(now, mid=c15[-1].c, oi_steps=oi_steps, buy_ratio=0.55, br_steps={TUE + 12 * H1 + M15: 0.42})
    g24 = [{"ts": now - 3 * H1, "funding_z": funding_z - 0.5}, {"ts": now - H1, "funding_z": funding_z - 0.2},
           {"ts": now - 10 * 60_000, "funding_z": funding_z}]
    snap = snapshot(c15, now, oi_rows=oi, trades_rows=tr, book_rows=book,
                    gauge={"funding_z": funding_z, "crowding_level": "NORMAL", "blocked_direction": None}, gauge_24h=g24,
                    cohort={"net_dir": 0.3, "fresh_long": 0, "fresh_short": 1, "long_notional": 55.0, "short_notional": 45.0},
                    day_type_override="range")
    return c15, snap


def test_m3_valid_failure_and_retest():
    _, snap = m3_scenario()
    m = M3FailedAuction()
    setup, missing = m.find_setup(snap)
    assert setup is not None, missing
    assert setup["direction"] == "short" and setup["level"] == 101.5 and setup["failed_extreme"] == 101.7
    assert setup["acceptance"] is False and setup["squeeze_risk"] is False
    ev = run(m, snap)
    assert_take(ev, "short")
    assert ev.intent.stop_px > 101.7 > ev.intent.entry_px


def test_m3_acceptance_veto():
    _, snap = m3_scenario(acceptance=True)
    m = M3FailedAuction()
    setup, missing = m.find_setup(snap)
    assert setup is not None and setup["acceptance"] is True, missing
    assert_veto(run(m, snap), "acceptance")


def test_m3_squeeze_risk_veto():
    _, snap = m3_scenario(funding_z=-1.5)            # shorts crowded → squeeze fuel against the short
    m = M3FailedAuction()
    setup, missing = m.find_setup(snap)
    assert setup is not None and setup["squeeze_risk"] is True, missing
    assert_veto(run(m, snap), "squeeze_risk")


def test_m3_in_trade_close_above_level_exit():
    c15, snap = m3_scenario()
    setup, _ = M3FailedAuction().find_setup(snap)
    fill_ts = c15[-1].ts
    c15 = c15 + path15([101.65, 101.8], fill_ts, spread=0.05)
    pos = position("M3", "short", setup["entry"], setup["stop"], fill_ts, c15[-1].ts + 30_000, {"level": 101.5})
    r = exit_after_two(M3FailedAuction(), c15, pos, 101.8)
    assert "close_above_level" in r.failing


# =====================================================================================
# M4 — extended 4h uptrend, 4h CHoCH down, weak bounce into the 1h supply zone (doc 14)
# =====================================================================================
def m4_scenario(cohort_change: float = -0.3):
    peak_end = MON + 20 * H1
    n = (12 * DAY_MS) // M15
    start = peak_end - n * M15
    shift = (n - 1) - 24
    prices = [80.0 + (40.0 / n) * i + 1.5 * math.sin(2 * math.pi * (i - shift) / 96) for i in range(n)]
    c15 = path15(prices, start, spread=0.15)
    P = c15[-1].c
    c15 += path15(steps(P, [-0.8] * 4), peak_end, spread=0.15, v=400.0)                       # 1h displacement down
    c15 += path15(steps(P - 3.2, [-0.25] * 12), peak_end + H1, spread=0.15)                   # grind → 4h CHoCH close
    up = steps(P - 6.2, [0.3] * 20) + [P - 0.3, P - 0.35, P - 0.3, P - 0.4]                    # weak bounce into the OB
    c15 += path15(up, TUE, spread=0.15)
    now = c15[-1].ts + 30_000
    oi, tr, book = feeds(now, mid=c15[-1].c, oi_start=998_000.0, br_steps={TUE: 0.40}, span_h=40)
    g24 = [{"ts": now - 20 * H1, "funding_z": 2.0}, {"ts": now - 10 * H1, "funding_z": 0.9}, {"ts": now - H1, "funding_z": 0.5}]
    snap = snapshot(c15, now, oi_rows=oi, trades_rows=tr, book_rows=book, oi_7d_high=1_000_000.0,
                    gauge={"funding_z": 0.5, "crowding_level": "NORMAL", "blocked_direction": None}, gauge_24h=g24,
                    cohort={"net_dir": 0.2 + cohort_change, "net_dir_24h_ago": 0.2, "fresh_long": 0, "fresh_short": 1},
                    positions_rows=[{"liq_px": P - 4.0, "notional": 2500.0, "side": "long", "wallet": "0xa"},
                                    {"liq_px": P - 4.05, "notional": 2500.0, "side": "long", "wallet": "0xb"}])
    return c15, snap, P


def test_m4_valid_choch_and_weak_bounce():
    _, snap, P = m4_scenario()
    m = M4ChangeOfCharacter()
    setup, missing = m.find_setup(snap)
    assert setup is not None, missing
    assert setup["direction"] == "short" and (setup["n_bos"] >= 3 or setup["pct_3d"] >= 0.05)   # spec v1.2 D-74 precondition
    assert setup["br_bounce"] <= 0.55 and setup["level"] < P - 4
    ev = run(m, snap)
    assert_take(ev, "short")
    assert ev.intent.stop_px > ev.intent.entry_px > setup["level"]


def test_m4_cohort_adding_longs_veto():
    _, snap, _ = m4_scenario(cohort_change=+0.2)     # cohort added longs with the OLD trend
    m = M4ChangeOfCharacter()
    setup, missing = m.find_setup(snap)
    assert setup is not None and setup["cohort_change_24h"] >= 0.15, missing
    assert_veto(run(m, snap), "cohort_adding_longs")


def test_m4_daily_strong_against_veto():
    _, snap, _ = m4_scenario()
    m = M4ChangeOfCharacter()
    setup, _ = m.find_setup(snap)
    d = m.mind.evaluate(with_setup(snap, {**setup, "daily_strong_against": True}))
    assert d.take is False and "daily_strong_against" in d.vetoes_hit


def test_m4_in_trade_higher_high_1h_exit():
    c15, snap, _ = m4_scenario()
    setup, _ = M4ChangeOfCharacter().find_setup(snap)
    fill_ts = c15[-1].ts
    sw = setup["entry_swing"]
    c15 = c15 + path15([sw + 0.2] * 8, fill_ts, spread=0.05)        # two full 1h candles closing above the entry swing
    pos = position("M4", "short", setup["entry"], setup["stop"], fill_ts, c15[-1].ts + 30_000, {"entry_swing": sw})
    r = exit_after_two(M4ChangeOfCharacter(), c15, pos, sw + 0.2, split=-4)
    assert "higher_high_1h" in r.failing


# =====================================================================================
# M5 — London raid of the Asia low, reclaimed inside the window (doc 15)
# =====================================================================================
def m5_scenario(raid_atr: float = 0.3, open_off_atr: float = 0.0, asia_amp_atr: float = 0.6, confirm: str = "same"):
    """confirm: as m1_scenario (D-89) — 'same' adds the 07:45 confirming close, 'later' reclaims at 07:45,
    'none' stops at the 07:30 raid+reclaim candle."""
    start = TUE - 12 * DAY_MS
    n_hist = (12 * DAY_MS) // M15
    c15 = path15(zigzag(n_hist, base=100.0, amp=1.2, period=20), start)
    a = atr15(c15)
    c15 += path15(zigzag(28, base=100.0, amp=asia_amp_atr * a, period=14), TUE, spread=0.3)   # Asia 00–07
    a_lo = min(c.l for c in c15[-28:])
    a_hi = max(c.h for c in c15[-28:])
    op = (a_hi + a_lo) / 2 + open_off_atr * a
    c15.append(candle(TUE + 7 * H1 + M15, op, op + 0.2, a_lo + 0.02, a_lo + 0.05))            # 07:15 London open
    open_c = c15[-1]
    rc = a_lo + 0.9 * a
    wick = a_lo - raid_atr * a
    if confirm == "later":
        c15.append(candle(TUE + 7 * H1 + 2 * M15, a_lo + 0.05, a_lo + 0.1, wick, a_lo - 0.02))       # 07:30 raid closes below
        c15.append(candle(TUE + 7 * H1 + 3 * M15, a_lo - 0.02, rc + 0.05, wick + 0.01, rc))          # 07:45 reclaim (case a)
    else:
        c15.append(candle(TUE + 7 * H1 + 2 * M15, a_lo + 0.05, rc + 0.05, wick, rc))                 # 07:30 raid + reclaim
        if confirm == "same":
            c15.append(candle(TUE + 7 * H1 + 3 * M15, rc, rc + 0.1, a_lo + 0.1, rc - 0.02))          # 07:45 confirming close (case b)
    rec_c = c15[-1] if confirm != "same" else c15[-2]
    now = c15[-1].ts + 30_000
    oi, tr, book = feeds(now, mid=c15[-1].c, br_steps={rec_c.ts - M15: 0.7}, oi_steps={open_c.ts - M15: 990_000.0})
    liq = [{"ts": rec_c.ts - 5 * 60_000, "side": "long", "notional": 900.0, "liquidated_user": "0xa"}]
    snap = snapshot(c15, now, oi_rows=oi, trades_rows=tr, book_rows=book, liq_rows=liq,
                    gauge={"funding_z": 0.1, "crowding_level": "NORMAL", "blocked_direction": None},
                    cohort={"net_dir": 0.1, "fresh_long": 1, "fresh_short": 0})
    return c15, snap, a_lo


def test_m5_valid_london_raid_and_reclaim():
    _, snap, a_lo = m5_scenario()
    m = M5SessionLiquidityRun()
    setup, missing = m.find_setup(snap)
    assert setup is not None, missing
    assert setup["direction"] == "long" and setup["level"] == a_lo and setup["window"] == "london"
    assert setup["opened_outside"] is False and setup["range_bad"] is False and setup["reclaim_quality"] >= 0.5
    assert setup["reclaim_candles"] == 1 and setup["confirmation_used"] is True
    ev = run(m, snap)
    assert_take(ev, "long")
    assert ev.intent.stop_px < a_lo
    rc = snap.s.c15[-2]
    assert ev.intent.entry_px == pytest.approx(min((rc.h + rc.l) / 2, rc.c) - 0.05 * snap.atr("15m"))
    assert ev.intent.entry_px < snap.price
    assert ev.intent.entry_valid_until_ms == snap.s.c15[-1].ts + 3 * M15


def test_m5_same_candle_reclaim_without_confirmation_is_not_a_setup():
    _, snap, _ = m5_scenario(confirm="none")
    setup, missing = M5SessionLiquidityRun().find_setup(snap)
    assert setup is None and "no confirmed reclaim" in missing


def test_m5_later_candle_reclaim_triggers_on_reclaim_close():
    _, snap, a_lo = m5_scenario(confirm="later")
    m = M5SessionLiquidityRun()
    setup, missing = m.find_setup(snap)
    assert setup is not None, missing
    assert setup["reclaim_candles"] == 2 and setup["confirmation_used"] is False
    ev = run(m, snap)
    assert_take(ev, "long")


def test_m5_opened_outside_veto():
    _, snap, _ = m5_scenario(open_off_atr=1.5)       # London opened well above the Asia high
    m = M5SessionLiquidityRun()
    setup, missing = m.find_setup(snap)
    assert setup is not None and setup["opened_outside"] is True, missing
    assert_veto(run(m, snap), "opened_outside")


def test_m5_range_bad_veto():
    _, snap, _ = m5_scenario(asia_amp_atr=3.2)       # Asia range far wider than 4 ATR
    m = M5SessionLiquidityRun()
    setup, missing = m.find_setup(snap)
    assert setup is not None and setup["range_bad"] is True, missing
    assert_veto(run(m, snap), "range_bad")


def test_m5_in_trade_below_asia_low_exit():
    c15, snap, a_lo = m5_scenario()
    setup, _ = M5SessionLiquidityRun().find_setup(snap)
    fill_ts = c15[-1].ts
    c15 = c15 + path15([a_lo - 0.2, a_lo - 0.3], fill_ts, spread=0.05)
    pos = position("M5", "long", setup["entry"], setup["stop"], fill_ts, c15[-1].ts + 30_000, {"level": a_lo})
    r = exit_after_two(M5SessionLiquidityRun(), c15, pos, a_lo - 0.3)
    assert "below_asia_low" in r.failing


# =====================================================================================
# M6 — Monday loss below the weekly open, Tuesday 1h reclaim (doc 16)
# =====================================================================================
def m6_scenario(loss_low: float = 96.0, oi_ramp: float = 0.025):
    start = MON - 21 * DAY_MS
    n_hist = (21 * DAY_MS) // M15
    c15 = path15(zigzag(n_hist, base=100.0, amp=1.2, period=20), start)
    mon = [100.0 - (100.0 - loss_low) * (i + 1) / 48 for i in range(48)] + flat(48, loss_low, 0.2)
    c15 += path15(mon, MON, spread=0.15)                                                   # Monday: slide + hover
    tue = [loss_low + (99.5 - loss_low) * (i + 1) / 40 for i in range(40)] + flat(8, 99.6, 0.15)
    tue += [99.8, 99.9, 100.15, 99.95]                                                     # 12:15–13:00: 1h still below WO
    tue += [100.1, 100.25, 100.35, 100.45]                                                 # 13:15–14:00: 1h reclaim
    c15 += path15(tue, TUE, spread=0.15)
    now = c15[-1].ts + 30_000
    rc_ts = c15[-1].ts
    oi, tr, book = feeds(now, mid=c15[-1].c,
                         oi_steps={rc_ts - 4 * H1 + 60_000 * k: 1_000_000.0 * (1 + oi_ramp * k / 240) for k in range(0, 241, 10)},
                         br_steps={rc_ts - 4 * H1: 0.66})
    snap = snapshot(c15, now, oi_rows=oi, trades_rows=tr, book_rows=book,
                    gauge={"funding_z": -0.2, "crowding_level": "NORMAL", "blocked_direction": None},
                    cohort={"net_dir": 0.3, "net_dir_24h_ago": 0.1, "fresh_long": 2, "fresh_short": 0})
    return c15, snap


def test_m6_valid_loss_and_reclaim():
    _, snap = m6_scenario()
    m = M6WeeklyOpenReclaim()
    setup, missing = m.find_setup(snap)
    assert setup is not None, missing
    wo = snap.s.refs["weekly_open"]
    assert setup["direction"] == "long" and setup["level"] == wo
    assert setup["shallow_loss"] is False and setup["oi_4h"] > 0.01 and setup["too_late"] is False
    ev = run(m, snap)
    assert_take(ev, "long")
    assert ev.intent.stop_px < wo < ev.intent.t1


def test_m6_shallow_loss_veto():
    _, snap = m6_scenario(loss_low=99.3)             # excursion well under 0.8 ATR(4h)
    m = M6WeeklyOpenReclaim()
    setup, missing = m.find_setup(snap)
    assert setup is not None and setup["shallow_loss"] is True, missing
    assert_veto(run(m, snap), "shallow_loss")


def test_m6_oi_falling_veto():
    _, snap = m6_scenario(oi_ramp=-0.02)             # OI bled over the 4h into the reclaim
    m = M6WeeklyOpenReclaim()
    setup, missing = m.find_setup(snap)
    assert setup is not None and setup["oi_4h"] <= 0, missing
    assert_veto(run(m, snap), "oi_falling")


def test_m6_in_trade_lost_weekly_open_exit():
    c15, snap = m6_scenario()
    setup, _ = M6WeeklyOpenReclaim().find_setup(snap)
    wo = setup["level"]
    fill_ts = c15[-1].ts
    c15 = c15 + path15([wo - 0.3] * 8, fill_ts, spread=0.05)        # two 1h closes back below the weekly open
    pos = position("M6", "long", setup["entry"], setup["stop"], fill_ts, c15[-1].ts + 30_000, {"level": wo}, hold_min=1440)
    r = exit_after_two(M6WeeklyOpenReclaim(), c15, pos, wo - 0.3, split=-4)
    assert "lost_weekly_open" in r.failing
