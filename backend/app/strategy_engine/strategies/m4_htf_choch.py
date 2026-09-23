"""M4 — HTF Change of Character with Crowding (doc 14). Short described; longs mirror.

Sequence: extended 4h uptrend (≥3 consecutive 4h BOS up, or ≥+5% over 3 days — spec v1.2, D-74),
OI within 3% of its 7-day high, funding z ≥1.0 at some point in 24h, a 4h close
below the most recent 4h higher low (CHoCH), then a 1h rally into the 1h supply
zone left by the CHoCH move (last up candle before the down displacement, or its
FVG) on falling/flat OI with buy ratio ≤0.55, cohort net-long change 24h ≤0.
Entry post-only at zone mid valid 6h; stop above the most recent 1h swing high
+0.2 ATR(1h) (skip >2.0 ATR); T1 4h range mid (40%, BE); T2 first long cluster
below ≥0.15% OI; T3 4h range low; trail 1h lower highs after T2.
Evaluated on every closed 15m (a superset of "each closed 1h + each 15m while
pending", D-29).
"""
from __future__ import annotations

from typing import Optional

from app.strategy_engine.mind.base import ContextRule, Decision, Veto, clip
from app.strategy_engine.mind.snapshot import Snapshot
from app.strategy_engine.structure.candles import TF_MS
from app.strategy_engine.structure.displacement import detect
from app.strategy_engine.structure.trend import consecutive_bos, last_event_of
from . import model_base as mb
from .model_base import H_MS, DAY_MS, ModelIntent, ModelStrategy, fmt_pct, fmt_px, fmt_x

HOLD_MIN = 480
HARD_STOP_MIN = 2160
BOS_MIN = 3            # spec v1.2 (D-74): was 5
EXT_3D_MIN = 0.05      # spec v1.2 (D-74): was 0.08
OI_NEAR_HIGH = 0.03
FZ_PEAK_MIN = 1.0
CHOCH_MAX_AGE_MS = 72 * H_MS
BOUNCE_BR_MAX = 0.55
STOP_MAX_ATR1H = 2.0
ENTRY_VALID_H = 6


class M4ChangeOfCharacter(ModelStrategy):
    id = "m4_htf_choch"
    model = "M4"
    name = "M4 HTF Change of Character"
    expected_hold_min = HOLD_MIN
    hard_stop_min = HARD_STOP_MIN
    stop_floor_tf = "1h"                 # spec v1.3 D-88
    detect_alert_kind = "CHoCH detected"

    def __init__(self) -> None:
        S = mb.setup_val
        reasons = [
            ("cohort_reducing", "cohort net-long change over 24h against the old trend", 2.5, S("cohort_reducing_strength")),
            ("oi_extreme", "OI within 5% of its 7-day high", 2.0, S("oi_extreme_strength")),
            ("funding_elevated", "24h peak funding z vs 2.5", 1.5, S("funding_elevated_strength")),
            ("choch_quality", "how far the 4h close broke the higher low", 1.5, S("choch_quality_strength")),
            ("weak_bounce", "bounce buy ratio ≤0.55, halved if OI rose in the bounce", 1.8, S("weak_bounce_strength")),
            ("extension", "3-day move beyond 3%", 1.0, S("extension_strength")),
            ("zone_quality", "displacement grade of the CHoCH move", 1.2, S("zone_quality_strength")),
            ("cluster_reward", "long clusters below inside the 4h range vs 5 × band_p80", 1.0, S("cluster_reward_strength")),
            ("delta_flip", "4h buy ratio since the CHoCH below 0.5", 1.0, S("delta_flip_strength")),
            ("divergence", "CVD at the last 4h high below CVD at the prior 4h high", 1.2, S("divergence_strength")),
        ]
        vetoes = [
            Veto("cohort_adding_longs", "cohort net-long change 24h ≥ +0.15 with the old trend",
                 lambda s: (s.setup.get("cohort_change_24h") is not None and
                            s.setup["cohort_change_24h"] * (1 if s.setup.get("direction") == "short" else -1) >= 0.15)),
            Veto("daily_strong_against", "daily trend still with the old move and the CHoCH level beyond the daily 20-candle midpoint",
                 lambda s: bool(s.setup.get("daily_strong_against"))),
            Veto("oi_rebuilding", "OI +2% during the bounce",
                 lambda s: s.setup.get("oi_bounce") is not None and s.setup["oi_bounce"] >= 0.02),
            mb.event_30m_veto(),
            Veto("stop_too_wide", "stop further than 2.0 ATR(1h) from entry", lambda s: bool(s.setup.get("stop_too_wide"))),
            Veto("bounce_displacement", "a 1h displacement with the old trend inside the bounce", lambda s: bool(s.setup.get("bounce_displacement"))),
            Veto("already_reversed", "price already past 50% of the 4h range from the pre-CHoCH extreme", lambda s: bool(s.setup.get("already_reversed"))),
        ]
        rules = [
            mb.day_type_rule({"squeeze": 1.2, "range": 1.05, "event": 0.7, "no_trade": 0.8}, fn=self._day_type_mult),
            mb.session_rule({"london": 1.05, "newyork": 1.05, "asia": 0.9, "dead": 1.0}),
            mb.form_rule(2, 10 ** 6, 0.85, 1.0),
            ContextRule("funding_falling", lambda s: 1.05 if s.setup.get("funding_falling") else 1.0),
        ]
        checks = [
            ("higher_high_1h", "1h close beyond the entry swing extreme (mirror for longs)", mb.check_close_beyond_level("entry_swing", "1h"), 2),
            ("oi_rebuild", "OI +2% since entry", self._oi_rebuild, 1),
            ("cohort_flip", "cohort net_dir moved ≥0.15 against the trade since entry", mb.check_cohort_flip(0.15), 1),
            ("no_progress", "8h without trading beyond the CHoCH level", mb.check_no_progress(32, "choch_level"), 1),
        ]
        self.mind = mb.build_mind(self.model, reasons, vetoes, rules, checks)

    @staticmethod
    def _day_type_mult(snap: Snapshot) -> Optional[float]:
        d = snap.setup.get("direction", "short")
        if snap.day_type == mb.day_trend_for(d):
            return 1.0
        if snap.day_type == mb.day_trend_for(mb.opposite(d)):
            return 0.7
        return None

    @staticmethod
    def _oi_rebuild(snap: Snapshot, pos) -> bool:
        ch = snap.oi_change(pos.fill_ts)
        return ch is not None and ch >= 0.02

    # ---- setup -------------------------------------------------------------------
    def find_setup(self, snap: Snapshot) -> tuple[Optional[dict], str]:
        s = snap.s
        if len(s.c4h) < 60 or len(s.c1h) < 60 or snap.atr("4h") <= 0 or snap.atr("1h") <= 0:
            return None, "warming: need 60 closed 4h + 1h candles and ATRs"
        parts: list[str] = []
        for direction in ("short", "long"):
            r = self._direction(snap, direction)
            if isinstance(r, dict):
                return r, ""
            parts.append(r)
        return None, "; ".join(dict.fromkeys(parts))

    def _direction(self, snap: Snapshot, direction: str):
        s = snap.s
        hi = direction == "short"           # old trend up → short the CHoCH
        old, new = ("up", "down") if hi else ("down", "up")
        st4 = s.st["4h"]
        c4, c1 = s.c4h, s.c1h
        a4, a1 = snap.atr("4h"), snap.atr("1h")
        choch = last_event_of(st4.events, "CHoCH", new)
        if choch is None or choch.ts < snap.now_ms - CHOCH_MAX_AGE_MS:
            return f"no 4h CHoCH {new} in the last 72h"
        # extension before the CHoCH: ≥3 consecutive 4h BOS with the old trend, or ≥5% over 3 days (D-74)
        pre = [e for e in st4.events if e.ts < choch.ts]
        n_bos = consecutive_bos(pre, old)
        i_ch = mb.index_of_ts(c4, choch.ts)
        if i_ch < 20:
            return "CHoCH too early in the 4h window"
        c_3d = next((c for c in reversed(c4[:i_ch]) if c.ts <= choch.ts - 3 * DAY_MS), None)
        pct_3d = ((c4[i_ch - 1].c / c_3d.c - 1.0) * (1 if hi else -1)) if c_3d else 0.0
        if n_bos < BOS_MIN and pct_3d < EXT_3D_MIN:
            return f"4h trend {old} not extended (BOS {n_bos}, 3d move {fmt_pct(pct_3d)})"
        # crowding at the top
        oi_now, oi_hi = snap.oi_now, snap.oi_7d_high
        oi_gap = None
        if oi_now is not None and oi_hi:
            oi_gap = (oi_hi - oi_now) / oi_hi
            if oi_gap > OI_NEAR_HIGH:
                return f"OI {fmt_pct(oi_gap)} below its 7d high (> 3%)"
        fz_peak = snap.funding_z_max_24h() if hi else snap.funding_z_min_24h()
        if fz_peak is not None and (fz_peak if hi else -fz_peak) < FZ_PEAK_MIN:
            return f"funding z never reached {'+' if hi else '-'}1.0 in 24h (peak {fmt_x(fz_peak)})"
        # the CHoCH move on 1h: the displacement that produced the 4h close beyond the level
        window_start = choch.ts - 12 * H_MS
        disp = None
        for i in range(len(c1) - 1, 1, -1):
            c = c1[i]
            if c.ts > choch.ts or c.ts < window_start:
                continue
            atr_i = s.atr_series["1h"]
            atr_at = float(atr_i[i]) if i < len(atr_i) and atr_i[i] == atr_i[i] else a1
            d = detect(c1, atr_at, i)
            if d and d.direction == new:
                disp = d
                break
        if disp is None:
            return f"no 1h displacement {new} inside the CHoCH move"
        zones = [z for z in s.zones.get("1h", []) if z.live and z.direction == ("bearish" if hi else "bullish")
                 and disp.start_index - 3 <= z.created_index <= disp.end_index + 1]
        if not zones:
            return "no live 1h zone left by the CHoCH move"
        zone = max(zones, key=lambda z: z.top if hi else -z.bottom)
        # bounce: from the post-CHoCH extreme back into the zone
        after = [c for c in c1 if c.ts > choch.ts]
        if not after:
            return "waiting for the bounce after the CHoCH"
        ext_c = min(after, key=lambda c: c.l) if hi else max(after, key=lambda c: c.h)
        bounce = [c for c in after if c.ts >= ext_c.ts]
        last = c1[-1]
        touched = (last.h >= zone.bottom and last.c <= zone.top) if hi else (last.l <= zone.top and last.c >= zone.bottom)
        if not touched:
            return f"1h bounce has not reached the zone {fmt_px(zone.bottom)}–{fmt_px(zone.top)}"
        if (hi and last.c > zone.top) or ((not hi) and last.c < zone.bottom):
            return "1h closed through the zone"
        oi_bounce = snap.oi_change(ext_c.ts - TF_MS["1h"], last.ts)
        br_bounce = snap.buy_ratio(ext_c.ts - TF_MS["1h"], last.ts)
        if br_bounce is not None and ((hi and br_bounce > BOUNCE_BR_MAX) or ((not hi) and br_bounce < 1 - BOUNCE_BR_MAX)):
            return f"bounce buy ratio {br_bounce:.2f} too strong for a {direction}"
        cohort_ch = snap.cohort_net_long_change_24h
        with_old = (cohort_ch if hi else -cohort_ch) if cohort_ch is not None else None
        if with_old is not None and 0 < with_old < 0.15:
            return f"cohort net-long change 24h {cohort_ch:+.2f} still with the old trend"
        # >= +0.15 with the old trend falls through so the cohort_adding_longs VETO is the recorded reason (D-34)
        # stop / targets
        sw1 = mb.last_confirmed_swing(s.sw["1h"], "high" if hi else "low", snap.now_ms, after_ts=choch.ts - 24 * H_MS)
        if sw1 is None:
            return "no confirmed 1h swing for the stop"
        entry = zone.mid
        stop = sw1.price + 0.2 * a1 if hi else sw1.price - 0.2 * a1
        stop_dist = abs(stop - entry)
        r4 = s.range_4h
        if r4 is None:
            return "no 4h range"
        t1 = r4.mid
        f15 = snap.band_p80()               # spec v1.1 Part C: T2 cluster threshold = band_p80
        cl_side = "long" if hi else "short"
        cls = sorted([c for c in s.clusters if c.side == cl_side and c.notional >= f15 and
                      ((c.level < entry) if hi else (c.level > entry))], key=lambda c: abs(c.level - entry))
        t2 = cls[0].level if cls else None
        t3 = r4.low if hi else r4.high
        in_range = [c for c in s.clusters if c.side == cl_side and (r4.low <= c.level < entry if hi else entry < c.level <= r4.high)]
        f50 = 5.0 * snap.band_p80()         # spec v1.1 Part C: cluster_reward denominator = 5 × band_p80
        # CHoCH quality: distance of the CHoCH close beyond the broken level
        ch_close = c4[i_ch].c
        choch_q = clip(((choch.level - ch_close) if hi else (ch_close - choch.level)) / (0.5 * a4))
        # daily
        mid20 = s.daily_midpoint(20)
        daily_tr = s.trend("1d")
        daily_strong = (daily_tr == old and mid20 is not None and ((choch.level > mid20) if hi else (choch.level < mid20)))
        # already reversed: doc 14 "price has already fallen more than 50% of the 4h range from the
        # high before the pullback" — measured to the CURRENT price (entry location), not to the
        # post-CHoCH low: the 4h range low usually IS that low, so a low-anchored measure would veto
        # every setup (audit D-55, examined and kept)
        pre_c = c4[max(0, i_ch - 30):i_ch]
        pre_ext = max(c.h for c in pre_c) if hi else min(c.l for c in pre_c)
        moved = (pre_ext - snap.price) if hi else (snap.price - pre_ext)
        already = r4.width > 0 and moved > 0.5 * r4.width
        # 4h delta since CHoCH + CVD divergence at the 4h highs
        br_4h = snap.buy_ratio(choch.ts, snap.now_ms)
        cvd4 = snap.cvd(c4[-80:], "4h")
        sws = [x for x in s.sw["4h"] if x.kind == ("high" if hi else "low") and x.ts <= choch.ts]
        div = 0.0
        if len(sws) >= 2:
            w = c4[-80:]
            ia, ib = mb.index_of_ts(w, sws[-2].ts), mb.index_of_ts(w, sws[-1].ts)
            r = mb.cvd_lower_extreme(cvd4, ia, ib, direction)
            # doc 14: divergence = CVD at the last 4h extreme did NOT confirm (lower than at the
            # prior extreme, mirror). cvd_lower_extreme returns True when CVD CONFIRMED (D-46).
            div = 1.0 if r is False else 0.0
        fz_now = snap.funding_z
        return {
            "direction": direction, "level_type": "4h_choch", "level": choch.level, "choch_level": choch.level,
            "choch_ts": choch.ts, "choch_close": ch_close, "trigger_ts": last.ts,
            "n_bos": n_bos, "pct_3d": pct_3d, "oi_gap": oi_gap, "fz_peak": fz_peak,
            "zone": zone.as_dict(), "zone_top": zone.top, "zone_bottom": zone.bottom,
            "zone_quality_strength": float(zone.grade or 0.0),
            "entry": entry, "stop": stop, "stop_too_wide": stop_dist > STOP_MAX_ATR1H * a1, "entry_swing": sw1.price,
            "t1": t1, "t2": t2, "t3": t3, "cluster_level": t2,
            "oi_bounce": oi_bounce, "br_bounce": br_bounce, "cohort_change_24h": cohort_ch,
            "cohort_reducing_strength": clip(((-cohort_ch) if hi else cohort_ch) / 0.3) if cohort_ch is not None else 0.0,
            "oi_extreme_strength": clip(1.0 - oi_gap / 0.05) if oi_gap is not None else 0.0,
            "funding_elevated_strength": clip(((fz_peak if hi else -fz_peak)) / 2.5) if fz_peak is not None else 0.0,
            "choch_quality_strength": choch_q,
            "weak_bounce_strength": (clip(((BOUNCE_BR_MAX - br_bounce) if hi else (br_bounce - (1 - BOUNCE_BR_MAX))) / 0.15)
                                     * (1.0 if (oi_bounce is None or oi_bounce <= 0) else 0.5)) if br_bounce is not None else 0.0,
            "extension_strength": clip((pct_3d * 100.0 - 3.0) / 6.0),   # D-74: clip((pct_3d - 3) / 6)
            "cluster_reward_strength": clip(sum(c.notional for c in in_range) / f50) if f50 > 0 else 0.0,
            "cluster_reward_norm": f50, "band_p80": f15,
            "delta_flip_strength": clip(((0.5 - br_4h) if hi else (br_4h - 0.5)) / 0.15) if br_4h is not None else 0.0,
            "divergence_strength": div,
            "daily_strong_against": daily_strong, "already_reversed": already,
            # doc 14 veto: the bounce contains a 1h DISPLACEMENT candle against (doc 10 §2.4:
            # range >= 1.5 ATR AND body >= 60%), not any wide-range candle (D-46)
            "bounce_displacement": (any(c.range >= 1.5 * a1 and c.body_ratio >= 0.6 and (c.up if hi else c.down) for c in bounce)
                                    if len(bounce) >= 2 else False),
            "funding_falling": (fz_now is not None and fz_peak is not None and
                                ((fz_now < fz_peak - 0.2) if hi else (fz_now > fz_peak + 0.2))),
            "cohort_net_dir_entry": snap.cohort_net_dir,
        }

    # ---- execution -------------------------------------------------------------
    def build_intent(self, snap: Snapshot, setup: dict, d: Decision) -> Optional[ModelIntent]:
        if abs(setup["entry"] - setup["stop"]) <= 0:
            return None
        t2, t3 = setup["t2"], setup["t3"]
        if t2 is None:
            # no long cluster ≥0.15% OI below: the 4h range low is the next target; the runner
            # only reaches T3 after T2, so promote it (D-46)
            t2, t3 = t3, None
        return ModelIntent(setup["direction"], setup["entry"], setup["stop"], setup["t1"], t2, t3,
                           snap.now_ms + ENTRY_VALID_H * H_MS, 0, "1h", "t2", 40.0,
                           entry_offset_note="1h zone mid")

    def thesis(self, snap: Snapshot, setup: dict, d: Decision) -> str:
        hi = setup["direction"] == "short"
        return (f"M4 {setup['direction']} {snap.coin} after 4h CHoCH at {fmt_px(setup['choch_level'])}. Trend {'up' if hi else 'down'} "
                f"{fmt_pct(setup['pct_3d'])} in 3d, OI within {fmt_pct(setup['oi_gap'])} of 7d high, funding z peaked "
                f"{fmt_x(setup['fz_peak'])}. Cohort net long change {fmt_x(setup['cohort_change_24h'])}. Bounce buy ratio "
                f"{fmt_x(setup['br_bounce'])}. Strongest: {d.top3()}. Wrong if 1h closes {'above' if hi else 'below'} "
                f"{fmt_px(setup['entry_swing'])}. Expect {fmt_px(setup['t1'])} within 8h, then {fmt_px(setup['cluster_level'] or setup['t3'])}.")

    def alert_detected(self, snap: Snapshot, setup: dict) -> str:
        return (f"[M4] {snap.coin} 4h CHoCH {setup['direction']} at {fmt_px(setup['choch_level'])} with crowding: "
                f"OI gap to 7d high {fmt_pct(setup['oi_gap'])}, funding z peak {fmt_x(setup['fz_peak'])}, "
                f"bounce into 1h zone {fmt_px(setup['zone_bottom'])}–{fmt_px(setup['zone_top'])}")

    def detected_key(self, snap: Snapshot, setup: dict) -> str:
        return f"4h_choch:{setup['choch_ts']}:{setup['direction']}"
