"""M2 — Break of Structure + Order Block (doc 12). Long described; shorts mirror.

Sequence: 4h trend up (or a fresh 4h CHoCH up confirmed by a 1h BOS up within
8h); a 1h displacement leg (≤3 consecutive 1h candles, combined range ≥1.5 ATR,
net body ≥60% — spec v1.2, D-75) closes above the most recent 1h swing high
leaving a bullish FVG inside the leg (OB = last opposite candle before the leg);
the break added OI (≥+1%), buy ratio ≥0.65, funding z <1.5; the OB+FVG zone lies in the discount of the new range (displacement high → last 1h
swing low); price retraces calmly (≤1.0 ATR candles, no displacement against)
toward the zone with OI holding and a balanced tape (br 0.35–0.55).
Entry post-only at the FVG mid (OB top if no FVG), valid until fill or a 15m
close below the OB. Stop OB bottom −0.15 ATR (skip >1.2 ATR). T1 displacement
high (40%, BE); T2 next 1h pool; on trend_up trail 15m higher lows instead.
"""
from __future__ import annotations

from typing import Optional

from app.strategy_engine.mind.base import ContextRule, Decision, Veto, clip
from app.strategy_engine.mind.snapshot import Snapshot
from app.strategy_engine.structure.candles import Candle, TF_MS
from app.strategy_engine.structure.displacement import LEG_MAX, any_against, leg_at
from app.strategy_engine.structure.sessions import utc_hour, weekday
from app.strategy_engine.structure.sweeps import detect_sweeps
from app.strategy_engine.structure.trend import last_event_of
from app.strategy_engine.structure.zones import fvg_at, order_block, update_status
from . import model_base as mb
from .model_base import H_MS, ModelIntent, ModelStrategy, fmt_pct, fmt_px, fmt_x

HOLD_MIN = 180
HARD_STOP_MIN = 480
BREAK_LOOKBACK_H = 24          # a break older than this is stale
OI_BREAK_MIN = 0.01
BR_BREAK_MIN = 0.65
FZ_MAX = 1.5
STOP_MAX_ATR = 1.2
RETRACE_MAX_ATR = 1.0


def _bull(direction: str) -> str:
    return "bullish" if direction == "long" else "bearish"


class M2BreakOfStructure(ModelStrategy):
    id = "m2_bos_order_block"
    model = "M2"
    name = "M2 Break of Structure + Order Block"
    expected_hold_min = HOLD_MIN
    hard_stop_min = HARD_STOP_MIN
    stop_floor_tf = "1h"                 # spec v1.3 D-88
    detect_alert_kind = "break detected"

    def __init__(self) -> None:
        S = mb.setup_val
        reasons = [
            ("oi_new_positioning", "OI added across the displacement (new positioning, not covering)", 2.0,
             lambda s: clip((s.setup.get("oi_break") or 0.0) * 100.0 / 2.0)),
            ("displacement", "displacement grade of the break", 1.8, S("displacement_grade")),
            ("zone_discount", "zone position inside the new range", 1.5,
             lambda s: clip((0.5 - float(s.setup.get("zone_pct", 0.5))) / 0.5)),
            ("retrace_calm", "retrace candles small with falling volume", 1.2, S("retrace_calm_strength")),
            ("oi_holding", "OI held since the break", 1.5,
             lambda s: clip(1.0 + (s.setup.get("oi_since_break") or 0.0) * 100.0 / 1.0)),
            # mirrored for shorts (doc 12 "mirror for shorts"; audit D-44): ratio → 1-ratio, funding z → -z
            ("delta_break", "taker buy ratio of the break (sell ratio for shorts)", 1.2,
             lambda s: clip(mb.sgn(s.setup.get("direction", "long"))
                            * ((s.setup.get("br_break") if s.setup.get("br_break") is not None else 0.5) - 0.5) / 0.25)),
            ("funding_young", "funding z still low on the trade side (crowd not yet in)", 0.8,
             lambda s: clip((FZ_MAX - (mb.sgn(s.setup.get("direction", "long")) * s.funding_z
                                       if s.funding_z is not None else FZ_MAX)) / FZ_MAX)),
            ("htf_agree", "daily bias and 4h trend agree with the break", 1.2, S("htf_agree_strength")),
            ("nested_sweep", "15m sweep of a minor low inside the zone with reclaim ≥0.5", 1.5, S("nested_sweep_strength")),
            ("cluster_cleared", "opposite-side liquidations during the break vs liq_5m_p90", 0.8, S("cluster_cleared_strength")),
        ]
        vetoes = [
            Veto("short_covering", "break added no OI — covering, not new positioning",
                 lambda s: s.setup.get("oi_break") is not None and s.setup["oi_break"] <= 0),
            Veto("oi_exit_retrace", "OI down ≥2% since the break",
                 lambda s: s.setup.get("oi_since_break") is not None and s.setup["oi_since_break"] <= -0.02),
            Veto("displacement_against", "a ≥1.5 ATR candle against the trade inside the retrace",
                 lambda s: bool(s.setup.get("displacement_against"))),
            Veto("zone_premium", "zone lies entirely in the premium half of the new range",
                 lambda s: bool(s.setup.get("zone_premium"))),
            Veto("late_day", "within 60 min of 00:00 UTC, or Friday after 20:00 UTC",
                 lambda s: min(mb.minutes_to_utc(s.now_ms, 0), 1440.0 - mb.minutes_to_utc(s.now_ms, 0)) <= 60   # D-52: either side of 00:00
                 or (weekday(s.now_ms) == 4 and utc_hour(s.now_ms) >= 20)),
            mb.event_30m_veto(),
            Veto("day_type_against", "trend day against the break",
                 lambda s: s.day_type == ("trend_down" if s.setup.get("direction") == "long" else "trend_up")),
            Veto("stop_too_wide", f"stop beyond {STOP_MAX_ATR} ATR from the entry",
                 lambda s: float(s.setup.get("stop_atr", 0)) > STOP_MAX_ATR),
        ]
        rules = [
            mb.day_type_rule({"event": 0.7, "no_trade": 0.6}, fn=self._day_type_mult),
            mb.session_rule({"london": 1.0, "newyork": 1.0, "asia": 0.8, "dead": 0.7}),
            ContextRule("zone_state", lambda s: 0.85 if s.setup.get("zone_tested") else 1.0),
            mb.form_rule(3, 5, 0.8, 0.9),
            mb.event_2h_rule(0.8),
        ]
        checks = [
            ("below_ob", "15m close beyond the OB", mb.check_close_beyond_level("ob_edge"), 2),
            ("oi_dropping", "OI down 1.5% since entry", mb.check_oi_bleeding(0.015), 1),
            ("no_progress", "4 closed candles without a new high beyond the entry candle",
             mb.check_no_progress(4, "entry_candle_high"), 1),
            ("delta_selling", "buy ratio <0.4 on the last 2 candles (mirror for shorts)", self._delta_selling, 1),
        ]
        self.mind = mb.build_mind(self.model, reasons, vetoes, rules, checks)

    @staticmethod
    def _day_type_mult(snap: Snapshot) -> Optional[float]:
        d = snap.setup.get("direction", "long")
        dt = snap.day_type
        if dt == ("trend_up" if d == "long" else "trend_down"):
            return 1.15
        if dt == "range":
            # doc 12 §4.4: range 0.9 — a range day is only reachable here when the
            # break is on a 4h level (sequence gate in _direction, D-44)
            return 0.9
        if dt == "squeeze":
            return 1.0
        return None

    @staticmethod
    def _delta_selling(snap: Snapshot, pos) -> bool:
        cs = snap.s.c15[-2:]
        if len(cs) < 2:
            return False
        rs = [snap.taker_candle(c) for c in cs]
        if any(r is None for r in rs):
            return False
        if pos.direction == "long":
            return all(r["ratio"] < 0.4 for r in rs)
        return all(r["ratio"] > 0.6 for r in rs)

    # ---- setup ---------------------------------------------------------------
    def find_setup(self, snap: Snapshot) -> tuple[Optional[dict], str]:
        c1h, c15 = snap.s.c1h, snap.s.c15
        a1h, a15 = snap.atr("1h"), snap.atr("15m")
        if len(c1h) < 30 or len(c15) < 30 or a1h <= 0 or a15 <= 0:
            return None, "warming: need 30 closed 1h + 15m candles and ATR"
        parts: list[str] = []
        for direction in ("long", "short"):
            r = self._direction(snap, direction)
            if isinstance(r, dict):
                return r, ""
            parts.append(r)
        return None, "; ".join(dict.fromkeys(parts))

    def _direction(self, snap: Snapshot, direction: str):
        c1h, c15 = snap.s.c1h, snap.s.c15
        a1h, a15 = snap.atr("1h"), snap.atr("15m")
        want = mb.trend_for(direction)
        st4, st1 = snap.s.st["4h"], snap.s.st["1h"]
        # -- HTF condition: 4h trend, or fresh 4h CHoCH confirmed by 1h BOS within 8h
        htf_ok = st4.trend == want
        branch = "4h_trend" if htf_ok else None
        fresh_choch = False
        if not htf_ok:
            ch = last_event_of(st4.events, "CHoCH", want)
            if ch is not None and snap.now_ms - ch.ts <= 48 * H_MS:
                # doc 12 §2.1: "confirmed by a 1h BOS up within the LAST 8 hours" — the window is
                # anchored to now, not to the CHoCH (D-53); the BOS must follow the CHoCH
                bos1 = [e for e in st1.events if e.type == "BOS" and e.direction == want
                        and e.ts >= ch.ts and e.ts >= snap.now_ms - 8 * H_MS]
                if bos1:
                    htf_ok = True
                    fresh_choch = True
                    branch = "fresh_4h_choch_1h_bos"
        if not htf_ok and st4.trend == "range" and st1.trend == want and snap.daily_bias == want:
            # spec v1.1 (D-64): 4h range is also aligned when the 1h trend and the daily bias
            # both point the trade's way — logged as an alternative branch in reasons_json
            htf_ok = True
            branch = "1h_trend_daily_bias"
        if not htf_ok:
            return (f"4h trend {st4.trend}, no fresh 4h CHoCH {want} confirmed by 1h BOS, "
                    f"1h trend {st1.trend} / daily bias {snap.daily_bias} not both {want} ({direction})")
        # -- the break: most recent 1h BOS in direction with a displacement + FVG
        bos = last_event_of(st1.events, "BOS", want)
        if bos is None or snap.now_ms - bos.ts > BREAK_LOOKBACK_H * H_MS:
            return f"no 1h BOS {want} in the last {BREAK_LOOKBACK_H}h"
        i = bos.index
        atr_i = snap.s.atr_series["1h"]
        atr_at = float(atr_i[i]) if i < len(atr_i) and atr_i[i] == atr_i[i] else a1h
        # spec v1.2 (D-75): the break is a displacement LEG of up to LEG_MAX consecutive 1h
        # candles in the break direction that contains the BOS candle and closes beyond the
        # swept swing — combined range ≥1.5 ATR(1h), net body ≥60% of it; grade on the leg
        disp = None
        for end in range(i, min(i + LEG_MAX, len(c1h))):
            if (c1h[end].c <= bos.level) if direction == "long" else (c1h[end].c >= bos.level):
                break
            for length in range(end - i + 1, LEG_MAX + 1):
                d = leg_at(c1h, atr_at, end, want, length)
                if d is not None and (disp is None or d.grade > disp.grade):
                    disp = d
            if disp is not None:
                break
        if disp is None:
            return f"1h BOS {want} at {fmt_px(bos.level)} is not a displacement leg (≤{LEG_MAX} candles, ≥1.5 ATR, body ≥60%)"
        # OB = last opposite candle before the leg; FVGs whose middle candle is inside the leg
        ob = order_block(c1h, disp, "1h")
        if ob is not None:
            update_status(ob, c1h[disp.end_index + 1:])
        fvg = None
        for k in range(disp.start_index, disp.end_index + 1):
            f = fvg_at(c1h, k, "1h", disp.grade)
            if f is not None and f.direction == _bull(direction):
                f.disp_high, f.disp_low = disp.high, disp.low
                update_status(f, c1h[k + 2:])
                fvg = f
                break
        if fvg is None:
            return f"displacement leg at {fmt_px(bos.level)} left no 1h FVG inside the leg"
        if ob is None:
            return f"displacement leg at {fmt_px(bos.level)} has no opposite candle before it (no OB)"
        if not ob.live or not fvg.live:
            return f"zone from the break at {fmt_px(bos.level)} already broken/filled"
        zone_id = f"{direction}:{ob.created_ts}"
        # break level also a 4h swing? doc 12 §1: "range only if the break is on 4h"
        break_is_4h = any(abs(s.price - bos.level) <= 0.1 * snap.atr("4h") for s in snap.s.sw["4h"]
                          if s.kind == ("high" if direction == "long" else "low"))
        if snap.day_type == "range" and not break_is_4h:
            return f"range day and the 1h break at {fmt_px(bos.level)} is not a 4h level"
        # -- break data
        t0 = c1h[disp.start_index].ts - TF_MS["1h"]
        t1 = disp.ts
        oi_break = snap.oi_change(t0, t1)
        tk = snap.taker(t0, t1)
        br_break = tk["ratio"] if tk else None
        fz = snap.funding_z
        if oi_break is not None and oi_break < OI_BREAK_MIN and oi_break > 0:
            return f"break added only {fmt_pct(oi_break)} OI (< +1%)"
        if br_break is not None and ((direction == "long" and br_break < BR_BREAK_MIN) or (direction == "short" and br_break > 1 - BR_BREAK_MIN)):
            return f"break buy ratio {br_break:.2f} not {'≥' if direction == 'long' else '≤'} {BR_BREAK_MIN if direction == 'long' else round(1 - BR_BREAK_MIN, 2)}"
        if fz is not None and ((direction == "long" and fz >= FZ_MAX) or (direction == "short" and fz <= -FZ_MAX)):
            return f"funding z {fz:.2f} already at/above {FZ_MAX} on the trade side"
        # -- new range: displacement extreme → last 1h swing on the other side before the displacement
        origin = None
        for s in reversed(snap.s.sw["1h"]):
            if s.kind == ("low" if direction == "long" else "high") and s.ts < c1h[disp.start_index].ts:
                origin = s
                break
        if origin is None:
            return "no 1h swing origin for the new range"
        hi = disp.high if direction == "long" else origin.price
        lo = origin.price if direction == "long" else disp.low
        width = hi - lo
        if width <= 0:
            return "new range has no width"
        entry_zone = fvg
        entry = fvg.mid
        zone_top = max(ob.top, fvg.top)
        zone_bottom = min(ob.bottom, fvg.bottom)
        zone_mid = (zone_top + zone_bottom) / 2.0
        zone_pct = (zone_mid - lo) / width
        if direction == "short":
            zone_pct = 1.0 - zone_pct
        zone_premium = ((zone_bottom - lo) / width > 0.5) if direction == "long" else ((hi - zone_top) / width > 0.5)
        # -- retrace: closed 15m candles after the break candle
        retr = [c for c in c15 if c.ts > disp.ts]
        if not retr:
            return f"break at {fmt_px(bos.level)} confirmed — waiting for the retrace"
        ext = max(c.h for c in retr) if direction == "long" else min(c.l for c in retr)
        prog_ref = disp.high if direction == "long" else disp.low
        cur = retr[-1].c
        if direction == "long":
            pulled = (prog_ref - min(c.l for c in retr)) / max(prog_ref - entry, 1e-9)
            closed_beyond = any(c.c < ob.bottom for c in retr)
        else:
            pulled = (max(c.h for c in retr) - prog_ref) / max(entry - prog_ref, 1e-9)
            closed_beyond = any(c.c > ob.top for c in retr)
        if closed_beyond:
            return f"15m already closed beyond the OB {fmt_px(ob.bottom)}–{fmt_px(ob.top)} — zone invalid"
        if pulled < 0.5:
            return f"retrace {pulled * 100:.0f}% of the way to the zone — waiting (needs ≥50%)"
        rmax = max(c.range for c in retr)
        if rmax > RETRACE_MAX_ATR * a15:
            return f"retrace candle range {rmax / a15:.2f} ATR > {RETRACE_MAX_ATR}"
        disp_against = any_against(retr, a15, direction)
        oi_since = snap.oi_change(disp.ts)
        # doc 12 §2 step 6: OI change since the break >= -0.5% (holders holding). Below
        # -2% the documented veto oi_exit_retrace takes over so it stays reachable (D-44).
        if oi_since is not None and -0.02 < oi_since < -0.005:
            return f"OI {fmt_pct(oi_since)} since the break (< -0.5%) — holders leaving"
        tk_r = snap.taker(disp.ts, snap.now_ms)
        br_r = tk_r["ratio"] if tk_r else None
        if br_r is not None:
            lo_b, hi_b = (0.35, 0.55) if direction == "long" else (0.45, 0.65)
            if not (lo_b <= br_r <= hi_b):
                return f"retrace buy ratio {br_r:.2f} outside {lo_b}–{hi_b}"
        # retrace calm: max range and volume falling vs displacement candles' 15m volume
        disp_15 = [c for c in c15 if c1h[disp.start_index].ts - TF_MS["1h"] < c.ts <= disp.ts]
        vol_disp = sum(c.v for c in disp_15) / len(disp_15) if disp_15 else 0.0
        vol_retr = sum(c.v for c in retr) / len(retr)
        if rmax <= 0.7 * a15 and (vol_disp <= 0 or vol_retr < vol_disp):
            calm = 1.0
        elif rmax <= 1.0 * a15:
            calm = 0.6
        else:
            calm = 0.0
        # nested sweep: a 15m swing (minor) inside the zone swept & reclaimed rq≥0.5
        nested = 0.0
        for s in snap.s.sw["15m"]:
            if s.kind == ("low" if direction == "long" else "high") and zone_bottom <= s.price <= zone_top and s.ts > disp.ts:
                sws = detect_sweeps(retr, s.price, a15, "low" if direction == "long" else "high")
                if any(x.reclaimed and x.reclaim_quality >= 0.5 for x in sws):
                    nested = 1.0
                    break
        liq_side = mb.opposite(direction)
        cleared_n, _w = snap.liq(liq_side, t0, t1)
        f10 = snap.liq_p90(liq_side)        # spec v1.1 Part C: cluster_cleared denominator = liq_5m_p90
        daily, t4 = snap.daily_bias, snap.s.trend("4h")
        if daily == want and t4 == want:
            htf = 1.0
        elif daily == "neutral" and t4 == want:
            htf = 0.7
        elif fresh_choch or branch == "1h_trend_daily_bias":
            htf = 0.4   # alternative branches share the doc's third tier (D-64)
        else:
            htf = 0.0   # doc 12 lists exactly three tiers (1.0 / 0.7 / 0.4); anything else is 0 (D-44)
        stop = ob.bottom - 0.15 * a15 if direction == "long" else ob.top + 0.15 * a15
        stop_atr = abs(entry - stop) / a15
        touches = sum(1 for c in c1h[ob.created_index + 1:] if (c.l <= ob.top if direction == "long" else c.h >= ob.bottom))
        t1_px = disp.high if direction == "long" else disp.low
        pool = mb.nearest_pool_beyond(snap, t1_px, direction, min_dist=0.2 * a15)
        t2_px = pool.level if pool is not None else entry + mb.sgn(direction) * 2.0 * abs(t1_px - entry)
        return {
            "direction": direction, "level_type": f"1h_{'fvg' if entry_zone.type == 'FVG' else 'ob'}", "level": entry,
            "zone_type": "OB+FVG", "zone_top": zone_top, "zone_bottom": zone_bottom, "ob_top": ob.top, "ob_bottom": ob.bottom,
            "ob_edge": ob.bottom if direction == "long" else ob.top,
            "fvg_top": fvg.top, "fvg_bottom": fvg.bottom, "zone_id": zone_id, "zone_tested": touches > 1,
            "bos_level": bos.level, "bos_ts": bos.ts, "trigger_ts": bos.ts, "displacement_grade": disp.grade,
            "disp_high": disp.high, "disp_low": disp.low, "range_high": hi, "range_low": lo, "zone_pct": zone_pct,
            "zone_premium": zone_premium, "oi_break": oi_break, "br_break": br_break, "funding_z": fz,
            "oi_since_break": oi_since, "br_retrace": br_r, "retrace_calm_strength": calm,
            "displacement_against": disp_against, "nested_sweep_strength": nested,
            "cluster_cleared_strength": clip(cleared_n / f10) if f10 > 0 else 0.0, "cleared_notional": cleared_n, "cleared_norm": f10,
            "htf_agree_strength": htf, "fresh_choch": fresh_choch, "alignment_branch": branch, "break_is_4h": break_is_4h,
            "entry": entry, "stop": stop, "stop_atr": stop_atr, "t1": t1_px, "t2": t2_px,
            "cohort_net_dir_entry": snap.cohort_net_dir,
        }

    # ---- execution -----------------------------------------------------------
    def build_intent(self, snap: Snapshot, setup: dict, d: Decision) -> Optional[ModelIntent]:
        direction = setup["direction"]
        entry, stop = setup["entry"], setup["stop"]
        if abs(entry - stop) <= 0:
            return None
        trail_after = "t1" if snap.day_type == ("trend_up" if direction == "long" else "trend_down") else "t2"
        # doc 12 §3: valid until filled or a 15m close beyond the OB — no time expiry (D-44)
        return ModelIntent(direction, entry, stop, setup["t1"], setup["t2"], None,
                           0, 0, "15m", trail_after, 40.0,
                           fallback_entry={"type": "cancel_beyond", "level": setup["ob_bottom"] if direction == "long" else setup["ob_top"]},
                           entry_offset_note="FVG mid")

    def thesis(self, snap: Snapshot, setup: dict, d: Decision) -> str:
        return (f"M2 {setup['direction']} {snap.coin} into {setup['zone_type']} {fmt_px(setup['zone_top'])} to "
                f"{fmt_px(setup['zone_bottom'])} after 1h BOS at {fmt_px(setup['bos_level'])}. Break added "
                f"{fmt_pct(setup['oi_break'])} OI with {fmt_x(setup['br_break'])} buy ratio, displacement grade "
                f"{fmt_x(setup['displacement_grade'])}. Strongest: {d.top3()}. Wrong if 15m closes "
                f"{'below' if setup['direction'] == 'long' else 'above'} "
                f"{fmt_px(setup['ob_bottom'] if setup['direction'] == 'long' else setup['ob_top'])}. "
                f"Expect {fmt_px(setup['t1'])} within 3h.")

    def alert_detected(self, snap: Snapshot, setup: dict) -> str:
        return (f"[M2] {snap.coin} 1h break {setup['direction']} at {fmt_px(setup['bos_level'])}: OI {fmt_pct(setup['oi_break'])}, "
                f"buy ratio {fmt_x(setup['br_break'])}, grade {fmt_x(setup['displacement_grade'])}; zone "
                f"{fmt_px(setup['zone_bottom'])}–{fmt_px(setup['zone_top'])}, retrace in progress")

    def state_updates(self, snap: Snapshot, setup, d, took: bool) -> dict:
        if not took or not setup:
            return {}
        taken = list(snap.model_state.get("taken_zones") or [])[-20:]
        taken.append(setup["zone_id"])
        return {"taken_zones": taken}
