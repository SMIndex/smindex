"""M3 — Failed Auction at a Range Extreme (doc 13). Short described; longs mirror.

Sequence: range day (or trend_up inside a live 4h supply zone), premium of the
4h range, a level that predates the current session (4h swing high, 4h supply
OB/FVG, 1h/4h equal highs, PDH, PWH, prior-session high); a push above it that
added OI (≥+0.5% over 1h) with funding ticking up; a swing-failure candle (15m
higher high than the prior swing high, close back below the level) with CVD at
the new high below CVD at the prior high; then a retest within 0.2 ATR of the
failed high without a close above.
Entry post-only at failed high −0.1 ATR valid 3 candles; stop SFP high +0.15
ATR; T1 session VWAP or range mid (nearer, 50%, BE); T2 long cluster below,
else range low.
"""
from __future__ import annotations

from typing import Optional

from app.strategy_engine.mind.base import ContextRule, Decision, Veto, clip
from app.strategy_engine.mind.snapshot import Snapshot
from app.strategy_engine.structure.candles import Candle, TF_MS
from app.strategy_engine.structure.sessions import day_start, session_bounds, utc_hour
from . import model_base as mb
from .model_base import H_MS, ModelIntent, ModelStrategy, fmt_pct, fmt_px, fmt_usd, fmt_x

HOLD_MIN = 120
HARD_STOP_MIN = 360
OI_PUSH_MIN = 0.005
RETEST_ATR = 0.2
ENTRY_VALID_CANDLES = 3
LOOKBACK = 100


def _session_start(now_ms: int, session: str) -> int:
    return session_bounds(day_start(now_ms), session)[0]


class M3FailedAuction(ModelStrategy):
    id = "m3_failed_auction"
    model = "M3"
    name = "M3 Failed Auction"
    expected_hold_min = HOLD_MIN
    hard_stop_min = HARD_STOP_MIN
    detect_alert_kind = "SFP detected"

    def __init__(self) -> None:
        S = mb.setup_val
        reasons = [
            ("trap", "OI added over the hour into the failed high (trapped late entrants)", 2.0,
             lambda s: clip((s.setup.get("oi_push_1h") or 0.0) * 100.0 / 1.5)),
            ("cvd_divergence", "CVD at the new extreme below CVD at the prior extreme", 2.0, S("cvd_div_strength")),
            ("location", "4h supply / 4h equal highs / PWH strongest, session high weakest", 1.8, S("location_strength")),
            ("failure_quality", "how far below the level the failure candle closed", 1.2, S("failure_quality_strength")),
            ("thin_bids", "bid depth 0.3% below its 1h average", 1.0, S("thin_bids_strength")),
            ("cluster_fuel", "long liquidation cluster within 1.5 ATR below vs band_p80", 1.2, S("cluster_fuel_strength")),
            ("funding_up", "funding z rising into the high", 0.8, S("funding_up_strength")),
            ("premium", "position in the premium of the 4h range", 1.0, S("premium_strength")),
            ("second_failure", "second failure of the same level today", 1.5, S("second_failure_strength")),
            ("day_type_fit", "range day best; trend day into supply half", 1.0, S("day_type_fit_strength")),
        ]
        vetoes = [
            Veto("acceptance", "two consecutive 15m closes beyond the level with OI rising", lambda s: bool(s.setup.get("acceptance"))),
            Veto("squeeze_risk", "funding z ≤ −1.5 or short OI share ≥60% (mirror for longs)", lambda s: bool(s.setup.get("squeeze_risk"))),
            Veto("cohort_adding_longs", "≥2 fresh cohort adds against the trade in the last 60 min",
                 lambda s: s.cohort_fresh_adds(mb.opposite(s.setup.get("direction", "short"))) >= 2),
            Veto("trend_day_early", "trend day in the trade's counter-direction before 15:00 UTC",
                 lambda s: s.day_type == ("trend_up" if s.setup.get("direction") == "short" else "trend_down") and utc_hour(s.now_ms) < 15),
            mb.event_30m_veto(),
            Veto("first_failure_random_level", "level was created in the current session", lambda s: bool(s.setup.get("level_this_session"))),
            mb.funding_extreme_veto("funding_extreme_short_side", lambda s: s.setup.get("direction")),
        ]
        rules = [
            mb.day_type_rule({"range": 1.15, "squeeze": 0.5, "event": 0.7, "no_trade": 0.7}, fn=self._day_type_mult),
            mb.session_rule({"newyork": 1.1, "london": 1.0, "asia": 0.8, "dead": 0.7}),
            ContextRule("failures_today", lambda s: {1: 1.0, 2: 1.1}.get(int(s.setup.get("failures_today", 1)), 0.8)),
            mb.form_rule(3, 5, 0.8, 0.9),
            mb.event_2h_rule(0.8),
        ]
        checks = [
            ("re_approach_with_oi", "price within 0.2 ATR of the failed extreme with OI rising since entry", self._re_approach, 2),
            ("cvd_recovering", "CVD above its entry-time value for 2 candles (mirror for longs)", self._cvd_recovering, 1),
            ("no_progress", "4 closed candles without trading beyond the failure candle's extreme",
             mb.check_no_progress(4, "failure_candle_extreme"), 1),
            ("close_above_level", "15m close beyond the failed level", mb.check_close_beyond_level("level"), 2),
        ]
        self.mind = mb.build_mind(self.model, reasons, vetoes, rules, checks)

    @staticmethod
    def _day_type_mult(snap: Snapshot) -> Optional[float]:
        d = snap.setup.get("direction", "short")
        if snap.day_type == ("trend_up" if d == "short" else "trend_down") and snap.setup.get("in_htf_zone"):
            return 0.85
        return None

    @staticmethod
    def _re_approach(snap: Snapshot, pos) -> bool:
        a = snap.atr("15m")
        lvl = float(pos.setup.get("failed_extreme") or pos.stop_px)
        px = snap.price
        near = (lvl - px <= 0.2 * a) if pos.direction == "short" else (px - lvl <= 0.2 * a)
        ch = snap.oi_change(pos.fill_ts)
        return near and ch is not None and ch > 0

    @staticmethod
    def _cvd_recovering(snap: Snapshot, pos) -> bool:
        cs = snap.s.c15[-LOOKBACK:]
        cvd = snap.cvd(cs)
        i_fill = next((i for i, c in enumerate(cs) if c.ts - TF_MS["15m"] < pos.fill_ts <= c.ts), None)
        if i_fill is None or len(cs) - 1 - i_fill < 2:
            return False
        base = cvd[i_fill]
        last2 = cvd[-2:]
        if base is None or any(v is None for v in last2):
            return False
        if pos.direction == "short":
            return all(v > base for v in last2)
        return all(v < base for v in last2)

    # ---- setup -------------------------------------------------------------------
    def find_setup(self, snap: Snapshot) -> tuple[Optional[dict], str]:
        c15 = snap.s.c15
        a15 = snap.atr("15m")
        if len(c15) < 40 or a15 <= 0 or snap.s.range_4h is None:
            return None, "warming: need 40 closed 15m candles, ATR and a 4h range"
        parts: list[str] = []
        for direction in ("short", "long"):
            r = self._direction(snap, direction)
            if isinstance(r, dict):
                return r, ""
            parts.append(r)
        return None, "; ".join(dict.fromkeys(parts))

    def _levels(self, snap: Snapshot, direction: str, sess_start: int) -> list[tuple[str, float, float, dict]]:
        s = snap.s
        out: list[tuple[str, float, float, dict]] = []
        refs = s.refs or {}
        hi = direction == "short"
        # 4h swing extremes confirmed before this session
        for sw in s.sw["4h"][-12:]:
            if sw.kind == ("high" if hi else "low") and sw.confirmed_at <= sess_start and sw.confirmed_at <= snap.now_ms:
                out.append(("4h_swing_high" if hi else "4h_swing_low", sw.price, 1.0, {}))
        for z in s.zones.get("4h", []):
            if z.live and z.created_ts <= sess_start and z.direction == ("bearish" if hi else "bullish"):
                out.append((f"4h_{z.type.lower()}", z.bottom if hi else z.top, 1.0, {"zone": z.as_dict()}))
        for p in s.pools:
            if hi and p.type == "equal_highs" and p.created_ts <= sess_start:
                out.append((f"equal_highs_{p.tf}", p.level, 1.0 if p.tf == "4h" else 0.8, {}))
            if (not hi) and p.type == "equal_lows" and p.created_ts <= sess_start:
                out.append((f"equal_lows_{p.tf}", p.level, 1.0 if p.tf == "4h" else 0.8, {}))
        for key, st in ((("pwh" if hi else "pwl"), 1.0), (("pdh" if hi else "pdl"), 0.8)):
            if refs.get(key) is not None:
                out.append((key, float(refs[key]), st, {}))
        # prior sessions' highs today (not the running one)
        order = ["asia", "london", "newyork"]
        # in the dead session (21:00–00:00) all three sessions of the day are prior sessions (D-45)
        prior_sessions = order[:order.index(snap.session)] if snap.session in order else order
        for name in prior_sessions:
            k = {"asia": "asia", "london": "london", "newyork": "ny"}[name] + ("_high" if hi else "_low")
            if refs.get(k) is not None:
                out.append((k, float(refs[k]), 0.6, {}))
        return out

    @staticmethod
    def _accepted(snap: Snapshot, after: list, lvl: float, hi: bool) -> bool:
        """Two consecutive 15m closes beyond the level with OI rising across them."""
        for k in range(1, len(after)):
            a_, b_ = after[k - 1], after[k]
            if ((a_.c > lvl and b_.c > lvl) if hi else (a_.c < lvl and b_.c < lvl)):
                oi_ab = snap.oi_change(a_.ts - TF_MS["15m"], b_.ts)
                if oi_ab is not None and oi_ab > 0:
                    return True
        return False

    def _direction(self, snap: Snapshot, direction: str):
        c15 = snap.s.c15
        a15 = snap.atr("15m")
        r4 = snap.s.range_4h
        hi = direction == "short"
        pct = r4.pct(snap.price)
        # day-type / location condition
        in_zone = any(z.live and z.bottom <= snap.price <= z.top and z.direction == ("bearish" if hi else "bullish")
                      for z in snap.s.zones.get("4h", []))
        if snap.day_type not in ("range",) and not (snap.day_type == ("trend_up" if hi else "trend_down") and in_zone):
            return f"day type {snap.day_type} is not range (and price not inside a 4h {'supply' if hi else 'demand'} zone) for a {direction}"
        if (hi and pct <= 0.5) or ((not hi) and pct >= 0.5):
            return f"price in the {'discount' if hi else 'premium'} of the 4h range (pct {pct:.2f}) — no failed-{'high' if hi else 'low'} {direction}"
        sess_start = _session_start(snap.now_ms, snap.session)
        levels = self._levels(snap, direction, sess_start)
        if not levels:
            return f"no {'high' if hi else 'low'} level predating this session"
        window = c15[-LOOKBACK:]
        last = len(window) - 1
        # SFP: a candle in the last 4 that took out the prior swing extreme beyond the level and closed back inside,
        # followed by a retest within 0.2 ATR without a close beyond; the retest candle is the last closed candle.
        best = None
        for t, lvl, st, meta in levels:
            for i in range(max(2, last - 3), last):
                c = window[i]
                if hi and not (c.h > lvl and c.c < lvl):
                    continue
                if (not hi) and not (c.l < lvl and c.c > lvl):
                    continue
                prior = None
                for sw in reversed(snap.s.sw["15m"]):
                    if sw.kind == ("high" if hi else "low") and sw.ts < c.ts and sw.confirmed_at <= c.ts:
                        prior = sw
                        break
                if prior is None or (hi and c.h <= prior.price) or ((not hi) and c.l >= prior.price):
                    continue
                # doc 13 §2 step 4: ... "and closes below that prior high" (mirror) — D-45
                if (hi and c.c >= prior.price) or ((not hi) and c.c <= prior.price):
                    continue
                after = window[i + 1:]
                if not after:
                    continue
                beyond = [((x.c > lvl) if hi else (x.c < lvl)) for x in after]
                if any(beyond) and not self._accepted(snap, after, lvl, hi):
                    continue          # a lone close beyond = failure undone; two with OI rising = acceptance (veto row, D-33)
                ext = c.h if hi else c.l
                rt = window[last]
                near = (ext - rt.h <= RETEST_ATR * a15 and rt.h <= ext) if hi else (rt.l - ext <= RETEST_ATR * a15 and rt.l >= ext)
                if not near:
                    continue
                cand = {"level_type": t, "level": lvl, "loc": st, "meta": meta, "sfp_index": i, "prior": prior}
                if best is None or st > best["loc"]:
                    best = cand
        if best is None:
            return f"no swing-failure + retest at a pre-session {'high' if hi else 'low'} on this candle"
        i = best["sfp_index"]
        c = window[i]
        lvl = best["level"]
        # push data: OI over the hour into the failure, funding ticking up
        oi_push = snap.oi_change(c.ts - H_MS, c.ts)
        if oi_push is not None and oi_push < OI_PUSH_MIN:
            return f"push into {best['level_type']} added only {fmt_pct(oi_push)} OI (< +0.5%/1h)"
        cvd = snap.cvd(window)
        i_prior = mb.index_of_ts(window, best["prior"].ts)
        cvd_div = 0.0
        cvd_ok = None
        if i_prior >= 0 and cvd[i_prior] is not None and cvd[i] is not None:
            vals = [v for v in cvd[max(0, i - 4):i + 1] if v is not None]
            rng = (max(vals) - min(vals)) if len(vals) > 1 else 0.0
            diff = (cvd[i_prior] - cvd[i]) if hi else (cvd[i] - cvd[i_prior])
            cvd_ok = diff > 0
            cvd_div = clip(diff / (0.5 * rng)) if rng > 0 else (1.0 if diff > 0 else 0.0)
        if cvd_ok is False:
            return f"CVD confirmed the new {'high' if hi else 'low'} at {best['level_type']} — no divergence"
        # doc 13 §2 step 3 "funding ticking up" (mirror: down) is a sequence condition; the
        # reason funding_up is clip(z/2) exactly (D-45). Funding is optional: no samples or
        # a flat reading over the 2h into the failure passes; only a move against fails.
        zs = [float(r["funding_z"]) for r in snap.gauge_24h if r.get("funding_z") is not None
              and c.ts - 2 * H_MS <= int(r["ts"]) <= c.ts]
        fz = snap.funding_z
        funding_up = clip((fz if hi else -fz) / 2.0) if fz is not None else 0.0
        if len(zs) >= 2 and ((hi and zs[-1] < zs[0]) or ((not hi) and zs[-1] > zs[0])):
            return f"funding z moved {zs[0]:.2f}→{zs[-1]:.2f} into the failed {'high' if hi else 'low'} — not ticking {'up' if hi else 'down'}"
        # failure data
        depth_now, depth_avg = snap.depth("bid_0_3" if hi else "ask_0_3"), snap.depth_avg("bid_0_3" if hi else "ask_0_3")
        depth_ratio = (depth_now / depth_avg) if depth_now is not None and depth_avg else None
        thin = clip((1.0 - depth_ratio) / 0.5) if depth_ratio is not None else 0.0
        cl_side = "long" if hi else "short"
        cl_notional, cl_level = 0.0, None
        for cl in snap.s.clusters:
            d = (snap.price - cl.level) if hi else (cl.level - snap.price)
            if cl.side == cl_side and 0 < d <= 1.5 * a15 and cl.notional > cl_notional:
                cl_notional, cl_level = cl.notional, cl.level
        f10 = snap.band_p80()               # spec v1.1 Part C: cluster_fuel denominator = band_p80
        # acceptance: two consecutive closes beyond the level with OI rising
        acc = self._accepted(snap, window[i + 1:], lvl, hi)
        ln, sn = float(snap.cohort.get("long_notional") or 0), float(snap.cohort.get("short_notional") or 0)
        share_against = (sn / (ln + sn)) if hi and ln + sn > 0 else ((ln / (ln + sn)) if ln + sn > 0 else None)
        squeeze = (fz is not None and ((hi and fz <= -1.5) or ((not hi) and fz >= 1.5))) or (share_against is not None and share_against >= 0.6)
        failures_key = f"failures:{round(lvl, 2)}"
        fstate = snap.model_state.get(failures_key) or {}
        prev_fail = mb.attempts_today(snap.model_state, failures_key, snap.now_ms)
        if prev_fail and fstate.get("last_sfp") == c.ts:
            prev_fail -= 1          # this same failure candle was already counted on an earlier evaluation (D-45)
        failures_today = prev_fail + 1
        ext = c.h if hi else c.l
        entry = ext - 0.1 * a15 if hi else ext + 0.1 * a15
        stop = ext + 0.15 * a15 if hi else ext - 0.15 * a15
        vw = (snap.s.vwap_session or {}).get("vwap") if snap.s.vwap_session else None
        mid = r4.mid
        cands = [x for x in (vw, mid) if x is not None and ((x < entry) if hi else (x > entry))]
        # T1 = nearer of session VWAP / 4h range mid (both must be on the trade's side); else 1.5R
        t1 = min(cands, key=lambda x: abs(x - entry)) if cands else entry + mb.sgn(direction) * 1.5 * abs(entry - stop)
        t2 = cl_level if cl_level is not None and ((cl_level < t1) if hi else (cl_level > t1)) else (r4.low if hi else r4.high)
        if (hi and t2 >= t1) or ((not hi) and t2 <= t1):
            t2 = entry + mb.sgn(direction) * 3.0 * abs(entry - stop)
        others = [(t, px) for t, px, _, _ in levels if t != best["level_type"]]
        loc = min(1.0, best["loc"] + mb.coincident_bonus(lvl, others, a15, best["level_type"]))
        fit = 1.0 if snap.day_type == "range" else (0.5 if in_zone else 0.0)
        return {
            "direction": direction, "level_type": best["level_type"], "level": lvl, "location_strength": loc,
            "failed_extreme": ext, "sfp_ts": c.ts, "trigger_ts": c.ts, "failure_candle_extreme": c.l if hi else c.h,
            "entry_candle_high": c.l if hi else c.h, "prior_extreme": best["prior"].price,
            "oi_push_1h": oi_push, "cvd_div_strength": cvd_div,
            # doc 13: clip((prior_high - close_failure) / 0.5 ATR) — the PRIOR SWING extreme, not the level (D-45)
            "failure_quality_strength": clip(((best["prior"].price - c.c) if hi else (c.c - best["prior"].price)) / (0.5 * a15)),
            "depth_ratio": depth_ratio, "thin_bids_strength": thin,
            "cluster_notional": cl_notional, "cluster_level": cl_level,
            "cluster_fuel_strength": clip(cl_notional / f10) if f10 > 0 else 0.0, "cluster_norm": f10,
            "funding_z": fz, "funding_up_strength": funding_up,
            "range_pct": pct, "premium_strength": clip((pct - 0.5) / 0.5) if hi else clip((0.5 - pct) / 0.5),
            "failures_today": failures_today, "failures_key": failures_key,
            "second_failure_strength": 1.0 if failures_today == 2 else 0.0,   # doc 13: THE second failure, 0 otherwise (D-54)
            "day_type_fit_strength": fit, "in_htf_zone": in_zone, "acceptance": acc, "squeeze_risk": squeeze,
            "share_against": share_against, "level_this_session": False,
            "entry": entry, "stop": stop, "t1": t1, "t2": t2, "cohort_net_dir_entry": snap.cohort_net_dir,
        }

    # ---- execution -------------------------------------------------------------
    def build_intent(self, snap: Snapshot, setup: dict, d: Decision) -> Optional[ModelIntent]:
        if abs(setup["entry"] - setup["stop"]) <= 0:
            return None
        return ModelIntent(setup["direction"], setup["entry"], setup["stop"], setup["t1"], setup["t2"], None,
                           snap.now_ms + ENTRY_VALID_CANDLES * TF_MS["15m"], 0, "15m", "never", 50.0,
                           entry_offset_note="failed extreme −0.1 ATR")

    def thesis(self, snap: Snapshot, setup: dict, d: Decision) -> str:
        hi = setup["direction"] == "short"
        return (f"M3 {setup['direction']} {snap.coin} at failed {'high' if hi else 'low'} {fmt_px(setup['failed_extreme'])} "
                f"({setup['level_type']}). Push added {fmt_pct(setup['oi_push_1h'])} OI with CVD divergence "
                f"{fmt_x(setup['cvd_div_strength'])}. {'Long' if hi else 'Short'} cluster {fmt_usd(setup['cluster_notional'])} at "
                f"{fmt_px(setup['cluster_level'])}. Strongest: {d.top3()}. Wrong if 15m closes {'above' if hi else 'below'} "
                f"{fmt_px(setup['level'])}. Expect {fmt_px(setup['t1'])} within 2h, then {fmt_px(setup['cluster_level'] or setup['t2'])}.")

    def alert_detected(self, snap: Snapshot, setup: dict) -> str:
        return (f"[M3] {snap.coin} swing failure at {setup['level_type']} {fmt_px(setup['level'])} ({setup['direction']}): "
                f"extreme {fmt_px(setup['failed_extreme'])}, OI into it {fmt_pct(setup['oi_push_1h'])}, CVD div {fmt_x(setup['cvd_div_strength'])}, retest seen")

    def state_updates(self, snap: Snapshot, setup, d, took: bool) -> dict:
        if not setup:
            return {}
        # every detected failure of a level counts toward "failures today" (taken or not) —
        # but one failure candle is counted once, however many evaluations re-detect it (D-45)
        key = setup["failures_key"]
        cur = snap.model_state.get(key) or {}
        if cur.get("day") == mb.day_key(snap.now_ms) and cur.get("last_sfp") == setup["sfp_ts"]:
            return {}
        upd = mb.bump_attempts(snap.model_state, key, snap.now_ms)
        upd[key]["last_sfp"] = setup["sfp_ts"]
        return upd
