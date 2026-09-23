"""M5 — Session Liquidity Run (doc 15). Long described (raid of the Asia low); shorts mirror.

Asia range = 00:00–07:00 UTC, frozen at 07:00, height 0.8–4.0 ATR(15m). Windows:
London 07:00–09:00, New York 13:00–15:00. The session must open inside the range
(or within 0.2 ATR of an edge). Raid = 15m wick 0.1–0.6 ATR below the Asia low,
reclaimed within 3 candles with rq ≥0.5; long liquidations on the raid, OI fell,
taker delta positive on the reclaim. One attempt per window per coin.
Spec v1.3 Part 3 (D-89): the reclaim must be confirmed (later-candle reclaim, or
same-candle reclaim plus one more close above the level with low ≥ wick low).
Entry post-only at min(50% of the reclaim candle, reclaim close) − 0.05 ATR, valid
3 candles from the confirmation; stop wick −0.15 ATR floored at 0.5 ATR(15m)
(D-88); T1 Asia mid (40%, BE); T2 Asia high; on a trend_up day trail 15m higher
lows past the Asia high after T1.
"""
from __future__ import annotations

from typing import Optional

from app.strategy_engine.mind.base import ContextRule, Decision, Veto, clip
from app.strategy_engine.mind.snapshot import Snapshot
from app.strategy_engine.structure.candles import MIN_MS, TF_MS
from app.strategy_engine.structure.sessions import day_start, weekday
from app.strategy_engine.structure.sweeps import confirmed_reclaim, detect_sweeps
from . import model_base as mb
from .model_base import H_MS, ModelIntent, ModelStrategy, fmt_atr, fmt_px, fmt_usd

HOLD_MIN = 90
HARD_STOP_MIN = 120          # added to the window end (doc 15: "hard stop window end + 120 min")
WINDOWS = (("london", 7, 9), ("newyork", 13, 15))
RANGE_MIN_ATR, RANGE_MAX_ATR = 0.8, 4.0
RAID_MIN, RAID_MAX = 0.1, 0.6
RQ_MIN = 0.5
ENTRY_VALID_CANDLES = 3
OPEN_TOL_ATR = 0.2


def window_of(now_ms: int) -> Optional[tuple[str, int, int]]:
    """(name, start_ms, end_ms) of the M5 window containing `now` (grace: one candle after the end)."""
    ds = day_start(now_ms)
    for name, a, b in WINDOWS:
        s, e = ds + a * H_MS, ds + b * H_MS
        if s < now_ms <= e + TF_MS["15m"]:
            return name, s, e
    return None


class M5SessionLiquidityRun(ModelStrategy):
    id = "m5_session_liquidity_run"
    model = "M5"
    name = "M5 Session Liquidity Run"
    expected_hold_min = HOLD_MIN
    hard_stop_min = HARD_STOP_MIN
    detect_alert_kind = "raid detected"

    def __init__(self) -> None:
        S = mb.setup_val
        reasons = [
            ("bias_alignment", "daily bias agrees with the reversal", 2.0, S("bias_strength")),
            ("range_quality", "Asia range 1.2–2.5 ATR with ≤2 wicks outside", 1.5, S("range_quality_strength")),
            # detector gets the Snapshot; the bare `mb.reclaim_strength` raised on it and read as 0 (D-63)
            ("reclaim", "reclaim candle body quality, ×0.7 when it took all 3 candles", 1.8,
             lambda s: mb.reclaim_strength(s.setup)),
            ("fuel", "liquidations on the raid vs 0.8 × liq_5m_p90", 1.3, S("fuel_strength")),
            ("cleared", "OI fell during the raid", 1.0, S("cleared_strength")),
            ("delta_flip", "reclaim buy ratio above 0.5", 1.3, S("delta_flip_strength")),
            ("early_in_window", "raid within 45 / 90 min of the window open", 1.0, S("early_strength")),
            ("open_location", "session opened in the middle 60% of the range", 1.0, S("open_location_strength")),
            ("no_pre_drift", "no drift toward the raided edge in the 3h before the window", 1.2, S("no_pre_drift_strength")),
            ("cohort", "fresh cohort adds on the trade's side (÷2)", 0.8, lambda s: clip(s.cohort_fresh_adds(s.setup.get("direction", "long")) / 2.0)),
        ]
        vetoes = [
            Veto("opened_outside", "session opened >0.2 ATR outside the Asia range", lambda s: bool(s.setup.get("opened_outside"))),
            Veto("too_deep", "raid deeper than 0.6 ATR", lambda s: bool(s.setup.get("too_deep"))),
            Veto("range_bad", "Asia range outside 0.8–4.0 ATR", lambda s: bool(s.setup.get("range_bad"))),
            Veto("already_taken", "an M5 attempt already made in this window", lambda s: bool(s.setup.get("already_taken"))),
            mb.event_30m_veto(),
            Veto("trend_day_with_raid", "trend day in the raid's direction (the raid is continuation)",
                 lambda s: s.day_type == mb.day_trend_for(mb.opposite(s.setup.get("direction", "long")))),
            Veto("late_in_window", "reclaim closed after the window end", lambda s: bool(s.setup.get("late_in_window"))),
            mb.funding_extreme_veto("funding_extreme_same_side", lambda s: s.setup.get("direction")),
        ]
        rules = [
            mb.day_type_rule({"range": 1.1, "event": 0.7}, fn=self._day_type_mult),
            mb.session_rule({"london": 1.0, "newyork": 1.05}),
            ContextRule("weekday", self._weekday_mult),
            mb.form_rule(3, 5, 0.8, 0.9),
            mb.event_2h_rule(0.8),
        ]
        checks = [
            ("below_asia_low", "15m close beyond the raided Asia edge", mb.check_close_beyond_level("level"), 2),
            ("no_higher_low", "no higher low formed since entry (D-25)", mb.check_no_higher_low, 1),
            ("oi_bleeding", "OI −1% since entry", mb.check_oi_bleeding(0.01), 1),
            ("delta_negative", "taker delta against the trade on the last 2 candles", mb.check_delta_negative, 1),
        ]
        self.mind = mb.build_mind(self.model, reasons, vetoes, rules, checks)

    @staticmethod
    def _day_type_mult(snap: Snapshot) -> Optional[float]:
        d = snap.setup.get("direction", "long")
        if snap.day_type == "squeeze":
            fz = snap.funding_z
            # a squeeze against the raid: crowd positioned with the raid (shorts crowded when the low was raided)
            return 1.15 if fz is not None and ((fz < 0) if d == "long" else (fz > 0)) else 1.0
        if snap.day_type == mb.day_trend_for(mb.opposite(d)):
            return 0.5
        return None

    @staticmethod
    def _weekday_mult(snap: Snapshot) -> float:
        wd = weekday(snap.now_ms)
        if wd == 0:
            return 1.05
        if wd == 4 and snap.session == "newyork":
            return 0.9
        return 1.0

    # ---- helpers ----------------------------------------------------------------
    @staticmethod
    def _asia(snap: Snapshot) -> Optional[dict]:
        frozen = snap.model_state.get(f"asia_range:{mb.day_key(snap.now_ms)}")
        if isinstance(frozen, dict) and frozen.get("high") is not None and frozen.get("low") is not None:
            return frozen
        return snap.s.asia

    # ---- setup -------------------------------------------------------------------
    def find_setup(self, snap: Snapshot) -> tuple[Optional[dict], str]:
        a15 = snap.atr("15m")
        if len(snap.s.c15) < 40 or a15 <= 0:
            return None, "warming: need 40 closed 15m candles and ATR"
        win = window_of(snap.now_ms)
        if win is None:
            return None, "outside the London 07–09 / New York 13–15 UTC windows"
        asia = self._asia(snap)
        if asia is None:
            return None, "Asia range not frozen (needs the full 00–07 UTC candle set)"
        name, ws, we = win
        parts: list[str] = []
        for direction in ("long", "short"):
            r = self._direction(snap, direction, asia, name, ws, we)
            if isinstance(r, dict):
                return r, ""
            parts.append(r)
        return None, "; ".join(dict.fromkeys(parts))

    def _direction(self, snap: Snapshot, direction: str, asia: dict, name: str, ws: int, we: int):
        c15 = snap.s.c15
        a15 = snap.atr("15m")
        lo = direction == "long"
        a_hi, a_lo = float(asia["high"]), float(asia["low"])
        height = a_hi - a_lo
        h_atr = height / a15
        level = a_lo if lo else a_hi
        window = [c for c in c15 if c.ts > ws]                    # candles closing inside the window (first closes at 07:15)
        if not window:
            return "waiting for the first closed candle of the window"
        open_px = window[0].o
        sws = detect_sweeps(window, level, a15, "low" if lo else "high", RAID_MIN, RAID_MAX, 3)
        conf = confirmed_reclaim(sws, window, level, "low" if lo else "high", len(window) - 1)   # D-89
        if conf is None:
            deep = [x for x in sws if x.too_deep]
            if deep:
                return f"{name} raid of Asia {'low' if lo else 'high'} too deep ({fmt_atr(deep[-1].depth_atr)})"
            return f"no confirmed reclaim of a {name} raid of the Asia {'low' if lo else 'high'} {fmt_px(level)} on this candle"
        sw = conf.sweep
        if sw.reclaim_quality < RQ_MIN:
            return f"raid reclaimed with rq {sw.reclaim_quality:.2f} < 0.5"
        rc = window[sw.reclaim_index]
        wick = window[sw.wick_index]
        trig = window[conf.trigger_index]
        # data during the raid / reclaim — one raid window (raid-candle open → reclaim close) for
        # BOTH fuel and cleared (D-60; a 2–3 candle raid keeps its OI drop), extreme = sw.wick_price
        fuel, wallets = snap.liq("long" if lo else "short", wick.ts - TF_MS["15m"], rc.ts)
        oi_raid = snap.oi_change(wick.ts - TF_MS["15m"], rc.ts)
        br = snap.buy_ratio(rc.ts - TF_MS["15m"], rc.ts)
        f08 = 0.8 * snap.liq_p90("long" if lo else "short")   # spec v1.1 Part C: 0.8 × liq_5m_p90 of the raided side
        # open location vs the range
        if a_lo <= open_px <= a_hi:
            pos = (open_px - a_lo) / height if height > 0 else 0.5
            if 0.2 <= pos <= 0.8:
                open_loc, open_txt = 1.0, "in the middle of the range"
            elif (pos < 0.2 and lo) or (pos > 0.8 and not lo):
                open_loc, open_txt = 0.7, "inside near the raided edge"
            else:
                open_loc, open_txt = 1.0, "inside near the far edge"
            opened_outside = False
        else:
            dist = (a_lo - open_px) if open_px < a_lo else (open_px - a_hi)
            open_loc, open_txt = 0.3, "just outside the range"
            opened_outside = dist > OPEN_TOL_ATR * a15
        range_bad = not (RANGE_MIN_ATR <= h_atr <= RANGE_MAX_ATR)
        wicks = int(asia.get("wicks_outside") or 0)
        if 1.2 <= h_atr <= 2.5 and wicks <= 2:
            rq_range = 1.0
        elif RANGE_MIN_ATR <= h_atr < 1.2 or 2.5 < h_atr <= RANGE_MAX_ATR:
            rq_range = 0.6
        else:
            rq_range = 0.0
        # doc 15: "raid within 45 minutes of the window open" — measured at the RAID candle, not the reclaim (D-47)
        mins = (wick.ts - ws) / MIN_MS
        early = 1.0 if mins <= 45 else (0.6 if mins <= 90 else 0.3)
        # pre-drift: the 3h before the window, signed toward the raided edge
        before = [c for c in c15 if ws - 3 * H_MS < c.ts <= ws]
        drift = 0.0
        if len(before) >= 2:
            mv = before[-1].c - before[0].o
            drift = max(0.0, (-mv) if lo else mv)
        bias = snap.daily_bias
        bias_s = 1.0 if bias == ("up" if lo else "down") else (0.2 if bias == ("down" if lo else "up") else 0.6)
        key = f"m5:{mb.day_key(snap.now_ms)}:{name}"
        already = bool(snap.model_state.get(key))
        entry = mb.reclaim_entry_px(direction, rc.h, rc.l, rc.c, a15)                 # D-89
        stop = sw.wick_price - 0.15 * a15 if lo else sw.wick_price + 0.15 * a15   # D-60: deepest raid extreme
        return {
            "direction": direction, "level_type": f"asia_{'low' if lo else 'high'}", "level": level,
            "window": name, "window_start": ws, "window_end": we, "window_key": key,
            "asia_high": a_hi, "asia_low": a_lo, "asia_mid": (a_hi + a_lo) / 2.0, "range_atr": h_atr, "wicks_outside": wicks,
            "wick_price": sw.wick_price, "wick_ts": sw.wick_ts, "depth_atr": sw.depth_atr, "too_deep": sw.too_deep,
            "reclaim_quality": sw.reclaim_quality, "candles_to_reclaim": sw.candles_to_reclaim, "reclaim_ts": rc.ts,
            "trigger_ts": trig.ts, "confirmation_ts": trig.ts, "reclaim_high": rc.h, "reclaim_low": rc.l, "reclaim_close": rc.c,
            "reclaim_candles": int(sw.candles_to_reclaim), "confirmation_used": bool(conf.confirmation_used),   # D-89
            "entry_candle_high": rc.h if lo else rc.l,
            "late_in_window": rc.ts > we,
            "open_px": open_px, "open_location": open_txt, "open_location_strength": open_loc, "opened_outside": opened_outside,
            "range_bad": range_bad, "range_quality_strength": rq_range,
            "fuel_notional": fuel, "fuel_wallets": wallets, "fuel_strength": clip(fuel / f08) if f08 > 0 else 0.0, "fuel_norm": f08,
            "oi_change_raid": oi_raid, "cleared_strength": clip((-oi_raid * 100.0) / 0.8) if oi_raid is not None else 0.0,
            "br_reclaim": br, "delta_flip_strength": clip(((br - 0.5) if lo else (0.5 - br)) / 0.25) if br is not None else 0.0,
            "minutes_into_window": mins, "early_strength": early,
            "pre_drift": drift, "no_pre_drift_strength": clip(1.0 - drift / (1.5 * a15)),
            "daily_bias": bias, "bias_strength": bias_s, "already_taken": already,
            "entry": entry, "stop": stop, "t1": (a_hi + a_lo) / 2.0, "t2": a_hi if lo else a_lo,
            "cohort_net_dir_entry": snap.cohort_net_dir,
        }

    # ---- pre-alert: raid in progress -------------------------------------------
    def pre_alerts(self, snap: Snapshot):
        win = window_of(snap.now_ms)
        asia = self._asia(snap)
        a15 = snap.atr("15m")
        if win is None or asia is None or a15 <= 0 or not snap.s.c15:
            return []
        last = snap.s.c15[-1]
        out = []
        if last.l < asia["low"] - RAID_MIN * a15 and last.c <= asia["low"]:
            out.append(("M5_pre", f"M5:{snap.coin}:pre:low:{last.ts}",
                        f"[M5] {snap.coin} {win[0]} raid in progress: wick {fmt_atr((asia['low'] - last.l) / a15)} below Asia low {fmt_px(asia['low'])} — awaiting reclaim"))
        if last.h > asia["high"] + RAID_MIN * a15 and last.c >= asia["high"]:
            out.append(("M5_pre", f"M5:{snap.coin}:pre:high:{last.ts}",
                        f"[M5] {snap.coin} {win[0]} raid in progress: wick {fmt_atr((last.h - asia['high']) / a15)} above Asia high {fmt_px(asia['high'])} — awaiting reclaim"))
        return out

    # ---- execution -------------------------------------------------------------
    def build_intent(self, snap: Snapshot, setup: dict, d: Decision) -> Optional[ModelIntent]:
        if abs(setup["entry"] - setup["stop"]) <= 0:
            return None
        trail = "t1" if snap.day_type == mb.day_trend_for(setup["direction"]) else "never"
        return ModelIntent(setup["direction"], setup["entry"], setup["stop"], setup["t1"], setup["t2"], None,
                           setup["confirmation_ts"] + ENTRY_VALID_CANDLES * TF_MS["15m"],       # D-89: from the confirmation
                           setup["window_end"] + HARD_STOP_MIN * MIN_MS, "15m", trail, 40.0,
                           entry_offset_note=mb.RECLAIM_ENTRY_NOTE)

    def thesis(self, snap: Snapshot, setup: dict, d: Decision) -> str:
        lo = setup["direction"] == "long"
        return (f"M5 {setup['direction']} {snap.coin} after {setup['window']} raid of Asia {'low' if lo else 'high'} {fmt_px(setup['level'])}. "
                f"Asia range {fmt_px(setup['asia_low'])} to {fmt_px(setup['asia_high'])} ({fmt_atr(setup['range_atr'])}), opened "
                f"{setup['open_location']}. Raid {fmt_atr(setup['depth_atr'])}, {fmt_usd(setup['fuel_notional'])} liquidated. Daily bias "
                f"{setup['daily_bias']}. Strongest: {d.top3()}. Wrong if 15m closes {'below' if lo else 'above'} {fmt_px(setup['level'])}. "
                f"Expect {fmt_px(setup['asia_mid'])} within 90 min, then {fmt_px(setup['t2'])}.")

    def alert_detected(self, snap: Snapshot, setup: dict) -> str:
        return (f"[M5] {snap.coin} {setup['window']} raid of Asia {setup['level_type'][5:]} {fmt_px(setup['level'])} reclaimed: "
                f"depth {fmt_atr(setup['depth_atr'])}, rq {setup['reclaim_quality']:.2f}, {fmt_usd(setup['fuel_notional'])} liquidated, "
                f"OI {mb.fmt_pct(setup['oi_change_raid'])}")

    def detected_key(self, snap: Snapshot, setup: dict) -> str:
        return f"{setup['window_key']}:{setup['direction']}:{setup['wick_ts']}"

    def state_updates(self, snap: Snapshot, setup, d, took: bool) -> dict:
        if setup and took:
            return {setup["window_key"]: {"ts": snap.now_ms, "direction": setup["direction"]}}
        return {}
