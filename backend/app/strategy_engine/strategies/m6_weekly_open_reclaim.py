"""M6 — Weekly Open Reclaim (doc 16). Long described; shorts mirror (WO lost from above).

Weekly open / PWH / PWL frozen Monday 00:00 UTC. Loss: ≥1 4h close below the WO
between Monday 00:00 and Wednesday 23:59 with an excursion ≥0.8 ATR(4h) below.
Reclaim: a 15m close above the WO followed by a 1h close above it, Monday 12:00
to Thursday 23:59, with OI ≥+1% over 4h, buy ratio ≥0.55 over 4h, funding z
≤1.0 and cohort Δ24h ≥0. One attempt per week per coin per direction.
Entry post-only at WO +0.1 ATR(15m) on the first pullback after the 1h close; if
no pullback within 4h the order is re-priced to the 1h close +0.1 ATR; valid 8h.
Stop = lowest low of the 4h before the reclaim −0.2 ATR(1h) (skip >2.5 ATR).
T1 = 50% of the distance to PWH or 1.5R, whichever is nearer (40%, BE); T2 PWH;
trail 4h higher lows after T1.
"""
from __future__ import annotations

from typing import Optional

from app.strategy_engine.mind.base import ContextRule, Decision, Veto, clip
from app.strategy_engine.mind.snapshot import Snapshot
from app.strategy_engine.structure.candles import TF_MS
from app.strategy_engine.structure.sessions import day_start, utc_hour, week_start, weekday
from . import model_base as mb
from .model_base import DAY_MS, H_MS, ModelIntent, ModelStrategy, fmt_atr, fmt_pct, fmt_px, fmt_x

HOLD_MIN = 1440
LOSS_MIN_ATR4H = 0.8
OI_4H_MIN = 0.01
BR_4H_MIN = 0.55
FZ_MAX = 1.0
STOP_MAX_ATR1H = 2.5
ENTRY_VALID_H = 8
PULLBACK_WAIT_H = 4


def friday_20(now_ms: int) -> int:
    return week_start(now_ms) + 4 * DAY_MS + 20 * H_MS


class M6WeeklyOpenReclaim(ModelStrategy):
    id = "m6_weekly_open_reclaim"
    model = "M6"
    name = "M6 Weekly Open Reclaim"
    expected_hold_min = HOLD_MIN
    hard_stop_min = 0            # hard stop = Friday 20:00 UTC (set per intent)
    stop_floor_tf = "1h"         # spec v1.3 D-88
    detect_alert_kind = "weekly open reclaimed"

    def __init__(self) -> None:
        S = mb.setup_val
        reasons = [
            ("loss_depth", "excursion beyond the weekly open earlier in the week (0.8–2.0 ATR4h)", 1.8, S("loss_depth_strength")),
            ("oi_commitment", "OI added over the 4h into the reclaim vs 2%", 2.0, S("oi_commitment_strength")),
            ("delta_reclaim", "4h buy ratio above 0.5 into the reclaim", 1.5, S("delta_reclaim_strength")),
            ("funding_room", "funding z below 1.0 leaves room", 1.2, S("funding_room_strength")),
            ("cohort", "cohort net change 24h with the reclaim", 1.8, S("cohort_strength")),
            ("reward", "distance to PWH vs stop distance", 1.2, S("reward_strength")),
            ("timing", "Tue/Wed best, Mon after 12:00 0.7, Thu 0.5", 1.0, S("timing_strength")),
            ("htf_bias", "daily trend with the reclaim", 1.5, S("htf_bias_strength")),
            ("reclaim_quality", "how far the 1h closed beyond the weekly open", 1.0, S("reclaim_quality_strength")),
            ("pwh_untested", "PWH/PWL untouched this week", 0.8, S("pwh_untested_strength")),
        ]
        vetoes = [
            Veto("daily_strong_down", "daily trend against the reclaim and price beyond the daily 20-candle midpoint",
                 lambda s: bool(s.setup.get("daily_strong_against"))),
            Veto("shallow_loss", "excursion beyond the weekly open < 0.8 ATR(4h)", lambda s: bool(s.setup.get("shallow_loss"))),
            Veto("already_taken", "an M6 attempt already made this week in this direction", lambda s: bool(s.setup.get("already_taken"))),
            Veto("too_late", "reclaim after Thursday 23:59 UTC", lambda s: bool(s.setup.get("too_late"))),
            Veto("oi_falling", "OI over the 4h into the reclaim ≤ 0",
                 lambda s: s.setup.get("oi_4h") is not None and s.setup["oi_4h"] <= 0),
            mb.event_30m_veto(),
            Veto("stop_too_wide", "stop further than 2.5 ATR(1h) from entry", lambda s: bool(s.setup.get("stop_too_wide"))),
            mb.funding_extreme_veto("funding_extreme_long_side", lambda s: s.setup.get("direction")),
        ]
        rules = [
            mb.day_type_rule({"range": 1.0, "event": 0.7}, fn=self._day_type_mult),
            ContextRule("prior_week", self._prior_week_mult),
            mb.form_rule(2, 10 ** 6, 0.85, 1.0),
            mb.event_2h_rule(0.85),
        ]
        checks = [
            ("lost_weekly_open", "1h close back beyond the weekly open", mb.check_close_beyond_level("level", "1h"), 2),
            ("oi_dropping", "OI −2% since entry", mb.check_oi_bleeding(0.02), 1),
            ("cohort_flip", "cohort net_dir moved ≥0.15 against the trade since entry", mb.check_cohort_flip(0.15), 1),
            ("no_progress", "12h without a new extreme beyond the reclaim candle", mb.check_no_progress(48, "entry_candle_high"), 1),
        ]
        self.mind = mb.build_mind(self.model, reasons, vetoes, rules, checks)

    @staticmethod
    def _day_type_mult(snap: Snapshot) -> Optional[float]:
        d = snap.setup.get("direction", "long")
        if snap.day_type == mb.day_trend_for(d):
            return 1.1
        if snap.day_type == mb.day_trend_for(mb.opposite(d)):
            return 0.7
        if snap.day_type == "squeeze":
            fz = snap.funding_z
            return 1.05 if fz is not None and ((fz < 0) if d == "long" else (fz > 0)) else 1.0
        return None

    @staticmethod
    def _prior_week_mult(snap: Snapshot) -> float:
        # D-58: read the Monday-00:00 frozen prior-week open/close (runner `weekly_levels:{wk}`),
        # live refs only as the fallback before the first freeze
        frozen = snap.model_state.get(f"weekly_levels:{mb.week_key(snap.now_ms)}") or {}
        refs = snap.s.refs or {}
        o = frozen.get("pw_open", refs.get("pw_open"))
        c = frozen.get("pw_close", refs.get("pw_close"))
        if not o or c is None:
            return 1.0
        d = snap.setup.get("direction", "long")
        chg = (c / o - 1.0) * (1 if d == "long" else -1)
        if chg < -0.06:
            return 0.9
        if chg > 0:
            return 1.05
        return 1.0

    # ---- setup -------------------------------------------------------------------
    def find_setup(self, snap: Snapshot) -> tuple[Optional[dict], str]:
        s = snap.s
        if len(s.c1h) < 48 or len(s.c4h) < 30 or snap.atr("4h") <= 0 or snap.atr("1h") <= 0 or snap.atr("15m") <= 0:
            return None, "warming: need 48 closed 1h / 30 closed 4h candles and ATRs"
        wk = mb.week_key(snap.now_ms)
        frozen = snap.model_state.get(f"weekly_levels:{wk}") or {}
        refs = s.refs or {}
        wo = frozen.get("weekly_open", refs.get("weekly_open"))
        pwh = frozen.get("pwh", refs.get("pwh"))
        pwl = frozen.get("pwl", refs.get("pwl"))
        if wo is None:
            return None, "no weekly open yet (first 1h candle of the week not closed)"
        ws = week_start(snap.now_ms)
        if snap.now_ms < ws + 12 * H_MS:
            return None, "before Monday 12:00 UTC — reclaim window not open"
        parts: list[str] = []
        for direction in ("long", "short"):
            r = self._direction(snap, direction, float(wo), pwh, pwl, ws, wk)
            if isinstance(r, dict):
                return r, ""
            parts.append(r)
        return None, "; ".join(dict.fromkeys(parts))

    def _direction(self, snap: Snapshot, direction: str, wo: float, pwh, pwl, ws: int, wk: str):
        s = snap.s
        lo = direction == "long"
        a4, a1, a15 = snap.atr("4h"), snap.atr("1h"), snap.atr("15m")
        c4 = [c for c in s.c4h if c.ts > ws]
        c1 = [c for c in s.c1h if c.ts > ws]
        c15 = [c for c in s.c15 if c.ts > ws]
        if not c1 or not c15:
            return "no closed candles this week"
        # the reclaim: last 1h closed beyond WO on the reclaim side, previous 1h close on the loss side
        last1 = c1[-1]
        if (lo and last1.c <= wo) or ((not lo) and last1.c >= wo):
            return f"1h not closed {'above' if lo else 'below'} weekly open {fmt_px(wo)} for a {direction}"
        # first 1h close on the reclaim side after the most recent loss
        i = len(c1) - 1
        while i > 0 and ((c1[i - 1].c > wo) if lo else (c1[i - 1].c < wo)):
            i -= 1
        if i == 0:
            return f"price never lost the weekly open this week on 1h closes ({direction})"
        rc1 = c1[i]
        # doc 16 §2 step 3: the reclaim 1h close lies between Monday 12:00 and Thursday 23:59 UTC (D-48)
        if rc1.ts < ws + 12 * H_MS:
            return f"1h reclaim at {mb.utc_str(rc1.ts)} is before Monday 12:00 UTC — window not open"
        # the entry window ("valid 8 hours") is anchored to the 1h reclaim close, not to the evaluation (D-57)
        if rc1.ts != last1.ts and rc1.ts < snap.now_ms - ENTRY_VALID_H * H_MS:
            return f"1h reclaim at {mb.utc_str(rc1.ts)} is older than the {ENTRY_VALID_H}h entry window"
        # loss: ≥1 4h close beyond WO between Mon 00:00 and Wed 23:59, before the reclaim
        loss4 = [c for c in c4 if c.ts <= rc1.ts and c.ts <= ws + 3 * DAY_MS and ((c.c < wo) if lo else (c.c > wo))]
        if not loss4:
            return f"no 4h close {'below' if lo else 'above'} the weekly open Mon–Wed before the reclaim"
        # doc 16 §2 step 2: "the low of THAT excursion" — the contiguous run of 4h closes on the loss
        # side that ends with the last loss close before the reclaim, not the whole week to date (D-56)
        j = c4.index(loss4[-1])
        while j > 0 and ((c4[j - 1].c < wo) if lo else (c4[j - 1].c > wo)):
            j -= 1
        exc_start = c4[j].ts - TF_MS["4h"]
        before = [c for c in c15 if exc_start < c.ts <= rc1.ts]
        if not before:
            return "no 15m candles inside the loss excursion"
        ext = min(c.l for c in before) if lo else max(c.h for c in before)
        depth = ((wo - ext) if lo else (ext - wo)) / a4
        # a 15m close beyond WO must precede the 1h close (doc: 15m close, then 1h close)
        # (the 1h candle's final 15m close IS the 1h close, so look at 15m closes strictly before it)
        if not any(((c.c > wo) if lo else (c.c < wo)) for c in c15 if loss4[-1].ts < c.ts < rc1.ts):
            return "no 15m close beyond the weekly open before the 1h close"
        too_late = rc1.ts > ws + 4 * DAY_MS          # after Thu 23:59
        oi_4h = snap.oi_change(rc1.ts - 4 * H_MS, rc1.ts)
        if oi_4h is not None and 0 < oi_4h < OI_4H_MIN:
            return f"OI into the reclaim {fmt_pct(oi_4h)} < +1%/4h"
        br = snap.buy_ratio(rc1.ts - 4 * H_MS, rc1.ts)
        if br is not None and ((lo and br < BR_4H_MIN) or ((not lo) and br > 1 - BR_4H_MIN)):
            return f"4h buy ratio {br:.2f} does not confirm the {direction} reclaim"
        fz = snap.funding_z
        if fz is not None and ((lo and fz > FZ_MAX) or ((not lo) and fz < -FZ_MAX)):
            return f"funding z {fz:+.2f} already crowded on the {direction} side"
        coh = snap.cohort_net_long_change_24h
        if coh is not None and ((lo and coh < 0) or ((not lo) and coh > 0)):
            return f"cohort net-long change 24h {coh:+.2f} against the {direction}"
        # stop: extreme of the 4h before the reclaim close ∓ 0.2 ATR(1h)
        win = [c for c in c15 if rc1.ts - 4 * H_MS < c.ts <= rc1.ts]
        st_ext = min(c.l for c in win) if lo else max(c.h for c in win)
        stop = st_ext - 0.2 * a1 if lo else st_ext + 0.2 * a1
        entry = wo + 0.1 * a15 if lo else wo - 0.1 * a15
        stop_dist = abs(entry - stop)
        target_ref = pwh if lo else pwl
        if target_ref is None:
            return "no prior-week extreme for the target"
        dist = (target_ref - entry) if lo else (entry - target_ref)
        if dist <= 0:
            return f"price already beyond {'PWH' if lo else 'PWL'} {fmt_px(target_ref)}"
        half = entry + mb.sgn(direction) * 0.5 * dist
        r15 = entry + mb.sgn(direction) * 1.5 * stop_dist
        t1 = half if abs(half - entry) <= abs(r15 - entry) else r15
        touches = sum(1 for c in c1 if ((c.h >= target_ref) if lo else (c.l <= target_ref)))
        wd = weekday(rc1.ts - 1)      # close ts sits on the boundary: the 23:00–00:00 candle belongs to the day it opened (D-48)
        timing = 1.0 if wd in (1, 2) else (0.7 if wd == 0 else (0.5 if wd == 3 else 0.3))
        daily_tr = s.trend("1d")
        mid20 = s.daily_midpoint(20)
        daily_strong = (daily_tr == ("down" if lo else "up") and mid20 is not None and
                        ((snap.price < mid20) if lo else (snap.price > mid20)))
        htf = 1.0 if daily_tr == ("up" if lo else "down") else (0.2 if daily_tr == ("down" if lo else "up") else 0.6)
        key = f"m6:{wk}:{direction}"
        return {
            "direction": direction, "level_type": "weekly_open", "level": wo, "weekly_open": wo, "pwh": pwh, "pwl": pwl,
            "week": wk, "week_key": key, "already_taken": bool(snap.model_state.get(key)),
            "reclaim_ts": rc1.ts, "trigger_ts": rc1.ts, "reclaim_close_1h": rc1.c, "entry_candle_high": rc1.h if lo else rc1.l,
            "loss_ts": loss4[-1].ts, "loss_extreme": ext, "loss_depth_atr": depth, "shallow_loss": depth < LOSS_MIN_ATR4H,
            "too_late": too_late, "oi_4h": oi_4h, "br_4h": br, "funding_z": fz, "cohort_change_24h": coh,
            "entry": entry, "stop": stop, "stop_too_wide": stop_dist > STOP_MAX_ATR1H * a1, "stop_extreme": st_ext,
            "t1": t1, "t2": target_ref, "target_dist": dist, "touches": touches,
            "reprice_at": rc1.ts + PULLBACK_WAIT_H * H_MS, "reprice_px": rc1.c + mb.sgn(direction) * 0.1 * a15,
            "loss_depth_strength": clip((depth - 0.8) / 1.2),
            "oi_commitment_strength": clip((oi_4h * 100.0) / 2.0) if oi_4h is not None else 0.0,
            "delta_reclaim_strength": clip(((br - 0.5) if lo else (0.5 - br)) / 0.15) if br is not None else 0.0,
            "funding_room_strength": clip((1.0 - (fz if lo else -fz)) / 1.5) if fz is not None else 0.0,
            "cohort_strength": ((0.5 + 0.5 * clip(((coh if lo else -coh)) / 0.2)) if (coh if lo else -coh) >= 0 else 0.0) if coh is not None else 0.0,
            "reward_strength": clip((dist / stop_dist - 1.5) / 2.5) if stop_dist > 0 else 0.0,
            "timing_strength": timing, "htf_bias_strength": htf, "daily_strong_against": daily_strong,
            "reclaim_quality_strength": clip(((rc1.c - wo) if lo else (wo - rc1.c)) / (0.5 * a1)),
            "pwh_untested_strength": 1.0 if touches == 0 else (0.5 if touches == 1 else 0.0),
            "cohort_net_dir_entry": snap.cohort_net_dir,
        }

    # ---- pre-alert: weekly open lost ------------------------------------------
    def pre_alerts(self, snap: Snapshot):
        refs = snap.s.refs or {}
        wo = (snap.model_state.get(f"weekly_levels:{mb.week_key(snap.now_ms)}") or {}).get("weekly_open", refs.get("weekly_open"))
        c4 = snap.s.c4h
        if wo is None or not c4 or c4[-1].ts <= week_start(snap.now_ms) or snap.now_ms - c4[-1].ts > TF_MS["15m"]:
            return []
        last, prev = c4[-1], (c4[-2] if len(c4) > 1 else None)
        if prev is None:
            return []
        if last.c < wo <= prev.c:
            return [("M6_pre", f"M6:{snap.coin}:lost:{last.ts}", f"[M6] {snap.coin} 4h closed below the weekly open {fmt_px(wo)} ({fmt_px(last.c)}) — watching for a reclaim")]
        if last.c > wo >= prev.c:
            return [("M6_pre", f"M6:{snap.coin}:lost_above:{last.ts}", f"[M6] {snap.coin} 4h closed above the weekly open {fmt_px(wo)} ({fmt_px(last.c)}) — watching for a loss from above (mirror short)")]
        return []

    # ---- execution -------------------------------------------------------------
    def build_intent(self, snap: Snapshot, setup: dict, d: Decision) -> Optional[ModelIntent]:
        if abs(setup["entry"] - setup["stop"]) <= 0:
            return None
        return ModelIntent(setup["direction"], setup["entry"], setup["stop"], setup["t1"], setup["t2"], None,
                           int(setup["reclaim_ts"]) + ENTRY_VALID_H * H_MS, friday_20(snap.now_ms), "4h", "t1", 40.0,   # D-57
                           fallback_entry={"type": "reprice_at", "at_ms": setup["reprice_at"], "price": setup["reprice_px"]},
                           entry_offset_note="weekly open +0.1 ATR(15m); re-priced to 1h close +0.1 ATR if no pullback in 4h")

    def thesis(self, snap: Snapshot, setup: dict, d: Decision) -> str:
        lo = setup["direction"] == "long"
        return (f"M6 {setup['direction']} {snap.coin} on weekly open {'reclaim' if lo else 'loss'} at {fmt_px(setup['weekly_open'])} "
                f"({mb.weekday_name(setup['reclaim_ts'])}). {'Lost' if lo else 'Held above'} by {fmt_atr(setup['loss_depth_atr'])} ATR earlier this week. "
                f"Reclaim with OI {fmt_pct(setup['oi_4h'])} and buy ratio {fmt_x(setup['br_4h'])}, funding z {fmt_x(setup['funding_z'])}, "
                f"cohort {fmt_x(setup['cohort_change_24h'])}. Strongest: {d.top3()}. Wrong if 1h closes {'below' if lo else 'above'} "
                f"{fmt_px(setup['weekly_open'])}. Expect {fmt_px(setup['t1'])} within 24h, then {'PWH' if lo else 'PWL'} {fmt_px(setup['t2'])}.")

    def alert_detected(self, snap: Snapshot, setup: dict) -> str:
        return (f"[M6] {snap.coin} weekly open {fmt_px(setup['weekly_open'])} {'reclaimed' if setup['direction'] == 'long' else 'lost from above'} "
                f"on 1h close {fmt_px(setup['reclaim_close_1h'])}: OI 4h {fmt_pct(setup['oi_4h'])}, br {fmt_x(setup['br_4h'])}, "
                f"excursion {fmt_atr(setup['loss_depth_atr'])}")

    def detected_key(self, snap: Snapshot, setup: dict) -> str:
        return f"{setup['week_key']}:{setup['reclaim_ts']}"

    def state_updates(self, snap: Snapshot, setup, d, took: bool) -> dict:
        if setup and took:
            return {setup["week_key"]: {"ts": snap.now_ms, "reclaim_ts": setup["reclaim_ts"]}}
        return {}
