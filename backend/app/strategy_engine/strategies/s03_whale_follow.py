"""s03 Whale / top-wallet following (doc 03 §5/§7/§14 prompt 3) — verbatim rules
over the cohort that exists TODAY (strategies/cohort.py: min(90d, available)
fill history, labelled on every signal). Evaluated every minute.
"""
from __future__ import annotations

import numpy as np

from ..intents import OrderIntent
from . import common as cm
from .base import Cond, EvalResult, PaperStrategy, StrategyContext, UNAVAILABLE, ohlc, regime_cond


class WhaleFollow(PaperStrategy):
    id = "s03_whale_follow"
    cadence = "minute"

    def evaluate(self, ctx: StrategyContext) -> EvalResult:  # noqa: C901
        r = EvalResult(coin=ctx.coin, fire_threshold=ctx.p("fire_threshold", 0.65))
        cs = ctx.cohort or {}
        label = cs.get("label") or "cohort: not built"
        r.labels["cohort"] = label
        n_wal = int(cs.get("n_wallets") or 0)
        highs, lows, closes = ohlc(ctx.candles_15m)
        atr, atr_warm = cm.atr_cond_value(ctx, 14)
        last = closes[-1] if closes else None
        conds: list[Cond] = []
        coh_warm = None if (ctx.cohort and n_wal > 0) else UNAVAILABLE

        fa = int(cs.get("fresh_agree") or 0)
        fdir = cs.get("fresh_dir")
        nd = cs.get("net_dir")
        direction = fdir if fa >= int(ctx.p("fresh_agree_min", 3)) else None
        conds.append(Cond("Fresh agreement", "fresh_agree >= 3 cohort wallets same dir within 60m",
                          value=(f"{fa} {fdir or ''} (L{cs.get('fresh_long', 0)}/S{cs.get('fresh_short', 0)}) of {n_wal} wallets" if ctx.cohort else "no cohort"),
                          threshold="need 3", met=((fa >= ctx.p("fresh_agree_min", 3)) if ctx.cohort and n_wal else None), warming=coh_warm))
        nd_ok = None
        if nd is not None and fdir:
            nd_ok = (nd >= ctx.p("net_dir_min", 0.3)) if fdir == "long" else (nd <= -ctx.p("net_dir_min", 0.3))
        conds.append(Cond("Net direction", "net_dir >= +0.3 (long) or <= -0.3 (short)",
                          value=(f"{nd:+.2f} (L{cs.get('n_long', 0)}/S{cs.get('n_short', 0)} positions)" if nd is not None else
                                 ("no cohort positions on this coin" if ctx.cohort else "no cohort")),
                          threshold="need ±0.3", met=nd_ok, warming=coh_warm))

        vwap = cs.get("cohort_vwap")
        pb_ok = None; pb_val = "no fresh entries"; pb_q = None
        if vwap and last is not None and np.isfinite(atr) and atr > 0 and fdir:
            d = 1 if fdir == "long" else -1
            dist = (last - vwap) / atr          # + = above VWAP
            adverse = -dist * d                  # + = price beyond VWAP against the cohort
            near = abs(dist) <= ctx.p("pullback_atr", 0.5)
            pb_ok = near and adverse <= ctx.p("adverse_atr", 1.0)
            pb_q = max(0.0, min(1.0, (0.5 - abs(dist)) / 0.3)) if abs(dist) >= 0.2 else 1.0
            pb_val = f"{dist:+.2f} ATR from VWAP {cm.fmt_px(vwap)}"
        conds.append(Cond("Pullback to VWAP", "within 0.5 ATR of cohort VWAP, not >1 ATR adverse",
                          value=pb_val, threshold="need <= 0.5 ATR", met=pb_ok, warming=(atr_warm if vwap else coh_warm)))
        rc = regime_cond(ctx)
        conds.append(rc)
        reg_ok = rc.met
        ft, w_f = cm.fisher_turn_warm(highs, lows, 9)
        hd, w_h = cm.hull_dir_warm(closes, 21)
        t_ok = None
        if fdir:
            dd = 1 if fdir == "long" else -1
            t_ok = (ft == dd) or (hd == dd)
        conds.append(Cond("Timing", "15m Fisher turning / Hull slope in cohort direction",
                          value=f"fisher15m={ft:+d} hull15m={hd:+d}", met=t_ok, warming=(w_f or w_h)))

        r.conditions = conds
        r.direction = direction
        coh_score = None
        if ctx.cohort and n_wal:
            coh_score = ctx.p("score_cohort", 0.4) if (direction and nd_ok) else (ctx.p("score_cohort", 0.4) / 2 if (direction or nd_ok) else 0.0)
        r.components = {
            "cohort": coh_score,
            "pullback": (ctx.p("score_pullback", 0.2) * pb_q if (pb_ok and pb_q is not None) else (None if pb_ok is None else 0.0)),
            "regime": (ctx.p("score_regime", 0.2) if reg_ok else (None if reg_ok is None else 0.0)),
            "timing": (ctx.p("score_timing", 0.2) if t_ok else (None if t_ok is None else 0.0)),
        }
        r.score_from_components()
        r.collect_warming()

        failing = [x.name for x in conds if x.met is False]
        if not ctx.cohort:
            r.waiting_for = "Cohort not built yet"
        elif n_wal == 0:
            r.waiting_for = f"Cohort empty — {label}"
        elif not fdir:
            r.waiting_for = (f"No fresh cohort entries on {ctx.coin} in 60m (net_dir {nd:+.2f}, {n_wal} wallets) — {label.split(' · ')[0]}"
                             if nd is not None else f"No cohort activity on {ctx.coin} ({n_wal} wallets) — {label.split(' · ')[0]}")
        elif fa < ctx.p("fresh_agree_min", 3):
            r.waiting_for = f"{fa} cohort wallet(s) {fdir} in 60m (need 3), net_dir {nd:+.2f}" if nd is not None else f"{fa} cohort wallet(s) {fdir} in 60m (need 3)"
        else:
            r.waiting_for = (f"{fa} wallets {fdir} in 60m, net_dir {nd:+.2f}" if nd is not None else f"{fa} wallets {fdir} in 60m") + \
                            (f" — failing: {', '.join(failing)}" if failing else
                             (f" — score {r.total_score:.2f} < {r.fire_threshold:.2f}" if r.total_score is not None and r.total_score < r.fire_threshold else ""))
            r.alerts.append(("pre", f"[S03 WHALE] {fa} cohort wallets opened {ctx.coin} {fdir.upper()} in 60m, net_dir {nd:+.2f}, VWAP entry {cm.fmt_px(vwap) if vwap else 'n/a'}" if nd is not None else
                             f"[S03 WHALE] {fa} cohort wallets opened {ctx.coin} {fdir.upper()} in 60m"))

        fired = (direction is not None and bool(nd_ok) and bool(pb_ok) and bool(reg_ok) and r.total_score is not None
                 and r.total_score >= r.fire_threshold and all(x.met is not False for x in conds))
        r.fired = bool(fired)
        if fired and vwap and np.isfinite(atr) and atr > 0:
            off = ctx.p("entry_offset_atr", 0.2) * atr
            entry_px = vwap - off if direction == "long" else vwap + off
            stop = vwap - ctx.p("stop_atr", 1.0) * atr if direction == "long" else vwap + ctx.p("stop_atr", 1.0) * atr
            t1 = entry_px + ctx.p("target_atr", 1.5) * atr if direction == "long" else entry_px - ctx.p("target_atr", 1.5) * atr
            r.intents = [OrderIntent(self.id, "hl", ctx.coin, direction, "entry", "post_only_limit", 0.0, px=entry_px,
                                     reason="S03 cohort pullback",
                                     meta={"stop": stop, "target1": t1, "atr": atr, "time_stop_h": int(ctx.p("time_stop_hours", 6)),
                                           "entry_valid_min": int(ctx.p("entry_valid_min", 30))})]
            r.waiting_for = f"Cohort {direction} FIRED ({fa} wallets) — entry {cm.fmt_px(entry_px)} stop {cm.fmt_px(stop)} t1 {cm.fmt_px(t1)}"
            r.alerts.append(("fire", f"[S03 WHALE] {ctx.coin} {direction.upper()} pullback | entry {entry_px:.1f} stop {stop:.1f} t1 {t1:.1f} | cohort {fa} wallets | score {r.total_score:.2f} (paper)"))
        return r
