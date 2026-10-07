"""s02 Funding settlement flow (doc 02 §4/§6/§13 prompt 2) — verbatim rules over
the available data. Evaluated every minute; the trade window is [T-30, T-10]
before the 00:00/08:00/16:00 UTC settlements, pre-alert at T-45. Direction is
AGAINST the crowd (positive funding => crowd long => short).
"""
from __future__ import annotations

import datetime as _dt

import numpy as np

from ..features import timing as tim
from ..intents import OrderIntent
from . import common as cm
from .base import Cond, EvalResult, PaperStrategy, StrategyContext, UNAVAILABLE, avail, ohlc, regime_cond

_SETTLE_HOURS = (0, 8, 16)


def minutes_to_next_settlement(now_ms: int) -> int:
    u = _dt.datetime.fromtimestamp(now_ms / 1000.0, tz=_dt.timezone.utc)
    best = 24 * 60
    for h in _SETTLE_HOURS + (24,):
        t = u.replace(hour=h % 24, minute=0, second=0, microsecond=0)
        if h >= 24 or t <= u:
            t = t + _dt.timedelta(days=1) if h >= 24 or t <= u else t
        best = min(best, int((t - u).total_seconds() // 60))
    return best


class FundingSettlementFlow(PaperStrategy):
    id = "s02_funding_flow"
    cadence = "minute"

    def evaluate(self, ctx: StrategyContext) -> EvalResult:  # noqa: C901
        r = EvalResult(coin=ctx.coin, fire_threshold=ctx.p("fire_threshold", 0.65))
        mins = minutes_to_next_settlement(ctx.now_ms)
        g = ctx.gauge or {}
        conds: list[Cond] = []
        highs, lows, closes = ohlc(ctx.candles_15m)
        atr, atr_warm = cm.atr_cond_value(ctx, 14)

        # 1) crowding — gauge level (z-score over the available HL funding rows, or Binance rate)
        z = g.get("funding_z")
        level = g.get("crowding_level")
        f8 = g.get("hl_funding_8h_equiv")
        crowd_side = None
        if f8 is not None and float(f8) != 0:
            crowd_side = "long" if float(f8) > 0 else "short"
        direction = None
        if crowd_side:
            direction = "short" if crowd_side == "long" else "long"
        gauge_warm = None
        if not ctx.gauge:
            gauge_warm = UNAVAILABLE
        elif level == "insufficient_data":
            gauge_warm = avail(int(ctx.funding_rows), 720, "funding rows")
        elif ctx.funding_rows < 720:
            gauge_warm = avail(int(ctx.funding_rows), 720, "funding rows")
        crowd_ok = (level == "EXTREME") if level and level != "insufficient_data" else None
        conds.append(Cond("Crowding EXTREME", "CEX funding EXTREME (|z|>=2 either side or Binance >=0.05% / <=-0.03%)",
                          value=(level or "n/a") + (f" z={float(z):+.2f}" if z is not None else "")
                          + (f" · crowd {crowd_side}" if crowd_side else ""),
                          threshold="need EXTREME", met=crowd_ok, warming=gauge_warm))

        # 2) OI +3% over prior 24h (available OI tape, labelled)
        frac, _have, oi_warm = cm.oi_change(ctx.oi_1m, 24 * 3_600_000, ctx.now_ms)
        oi_ok = (frac >= ctx.p("oi_rise_24h", 0.03)) if frac is not None else None
        conds.append(Cond("OI confirmation", "HL OI risen >= 3% over prior 24h",
                          value=(f"{frac:+.2%}" if frac is not None else "no OI tape"), threshold="need +3%",
                          met=oi_ok, warming=oi_warm))

        # 3) no early trim: price has not moved against the crowd by > 1 ATR in the last 2h (8 x 15m)
        trim_ok = None; trim_val = "n/a"
        if crowd_side and len(closes) >= 2 and np.isfinite(atr) and atr > 0:
            base = closes[-9] if len(closes) >= 9 else closes[0]
            move = closes[-1] - base
            adverse = -move if crowd_side == "long" else move
            trim_ok = adverse <= ctx.p("early_trim_atr", 1.0) * atr
            trim_val = f"{adverse / atr:+.2f} ATR against crowd over {min(8, len(closes) - 1)} bars"
        elif not crowd_side:
            trim_val = "no crowd side (funding 0 / no gauge)"
        conds.append(Cond("No early trim", "price not moved against crowd > 1 ATR in last 2h",
                          value=trim_val, threshold="need <= 1 ATR", met=trim_ok,
                          warming=(atr_warm or avail(len(closes), 9, "bars"))))

        # 4) regime + no macro inside the window
        rc = regime_cond(ctx)
        rc.subline = "TRADE_ALLOWED and no macro inside the window"
        conds.append(rc)
        reg_ok = rc.met

        # 5) timing: 15m Fisher turning against the crowd, or 15m Hull(21) flat/against
        ft, w_f = cm.fisher_turn_warm(highs, lows, 9)
        hd, w_h = cm.hull_dir_warm(closes, 21)
        timing_ok = None
        if direction:
            d = 1 if direction == "long" else -1
            timing_ok = (ft == d) or (hd != -d)
        conds.append(Cond("Timing", "15m Fisher turning against crowd or Hull flat/against",
                          value=f"fisher15m={ft:+d} hull15m={hd:+d}", met=timing_ok, warming=(w_f or w_h)))

        r.conditions = conds
        r.direction = direction
        crowd_score = {"EXTREME": ctx.p("score_extreme", 0.4), "ELEVATED": ctx.p("score_elevated", 0.2)}.get(level or "", 0.0)
        r.components = {
            "trigger": (crowd_score if level and level != "insufficient_data" else None),
            "oi": (ctx.p("score_oi", 0.2) if oi_ok else (None if oi_ok is None else 0.0)),
            "regime": (ctx.p("score_regime", 0.2) if reg_ok else (None if reg_ok is None else 0.0)),
            "timing": (ctx.p("score_timing", 0.2) if timing_ok else (None if timing_ok is None else 0.0)),
        }
        r.score_from_components()
        r.collect_warming()

        in_window = ctx.p("window_start_min", 30) >= mins >= ctx.p("window_end_min", 10)
        ztxt = f" (z {float(z):+.2f})" if z is not None else ""
        failing = [x.name for x in conds if x.met is False]
        if mins > 45:
            r.waiting_for = f"Next settlement in {mins} min; funding {level or 'no gauge'}{ztxt}" + (f", crowd {crowd_side}" if crowd_side else "")
        elif mins > 30:
            r.waiting_for = f"Settlement in {mins} min — {level or 'no gauge'}{ztxt}, score {r.total_score if r.total_score is not None else 'n/a'}"
            if r.total_score is not None and r.total_score > 0.5:
                r.alerts.append(("pre", f"[S02 SETTLE] {ctx.coin} setup forming for next settlement, funding {level}{ztxt}, {direction} vs crowd"))
        elif in_window:
            r.waiting_for = (f"Settlement window (T-{mins}) — {level or 'no gauge'}{ztxt}"
                             + (f", failing: {', '.join(failing)}" if failing else
                                (f", score {r.total_score:.2f} < {r.fire_threshold:.2f}" if r.total_score is not None and r.total_score < r.fire_threshold else "")))
        else:
            r.waiting_for = f"Inside T-{mins}: window closed (fires only T-30..T-10)"

        fired = (in_window and direction is not None and bool(reg_ok) and r.total_score is not None
                 and r.total_score >= r.fire_threshold and all(x.met is not False for x in conds))
        r.fired = bool(fired)
        if fired and np.isfinite(atr) and atr > 0 and closes:
            last = closes[-1]
            off = ctx.p("entry_offset_atr", 0.1) * atr
            entry_px = last - off if direction == "long" else last + off
            stop = entry_px - ctx.p("stop_atr", 0.6) * atr if direction == "long" else entry_px + ctx.p("stop_atr", 0.6) * atr
            t1 = entry_px + ctx.p("target_atr", 0.8) * atr if direction == "long" else entry_px - ctx.p("target_atr", 0.8) * atr
            r.intents = [OrderIntent(self.id, "hl", ctx.coin, direction, "entry", "post_only_limit", 0.0, px=entry_px,
                                     reason="S02 settlement flow",
                                     meta={"stop": stop, "target1": t1, "atr": atr,
                                           "time_stop_h": max(1, round((mins + int(ctx.p("time_stop_after_settle_min", 60))) / 60)),
                                           "entry_valid_min": max(1, mins - int(ctx.p("window_end_min", 10))),
                                           "size_mult": ctx.p("size_mult", 0.75)})]
            r.waiting_for = f"Settlement T-{mins} FIRED {direction} vs crowd — entry {cm.fmt_px(entry_px)} stop {cm.fmt_px(stop)} t1 {cm.fmt_px(t1)}"
            r.alerts.append(("fire", f"[S02 SETTLE] {ctx.coin} {direction.upper()} vs crowd T-{mins} | entry {entry_px:.1f} stop {stop:.1f} t1 {t1:.1f} | score {r.total_score:.2f} (paper)"))
        elif in_window and not fired:
            r.alerts.append(("skip", f"[S02 SETTLE] {ctx.coin} T-{mins} skip — " + (", ".join(failing) if failing else "score below threshold")))
        return r
