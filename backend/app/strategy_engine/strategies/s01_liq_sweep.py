"""s01 Liquidation-sweep reversal (doc 01 §5/§6/§13) — verbatim rules, run on
the PARTIAL liquidation coverage that exists (labelled `liq_coverage`), the 1m
taker tape, the 5s book tape and the tracked-cohort liquidation map."""
from __future__ import annotations

import numpy as np

from ..intents import OrderIntent
from . import common as cm
from .base import Cond, EvalResult, PaperStrategy, StrategyContext, UNAVAILABLE, avail, bias_align, ohlc, regime_cond

_5M_MS = 5 * 60_000
_15M_MS = 15 * 60_000


class LiqSweepReversal(PaperStrategy):
    id = "s01_liq_sweep"
    cadence = "minute"

    def evaluate(self, ctx: StrategyContext) -> EvalResult:  # noqa: C901 — one rule per block, verbatim
        r = EvalResult(coin=ctx.coin, fire_threshold=ctx.p("fire_threshold", 0.65))
        cov = ctx.liq_coverage
        r.labels["liq_coverage"] = cov
        c = ctx.candles_15m
        highs, lows, closes = ohlc(c)
        atr, atr_warm = cm.atr_cond_value(ctx, 14)
        oi = ctx.oi_1m[-1]["oi_notional"] if ctx.oi_1m and ctx.oi_1m[-1].get("oi_notional") else None
        conds: list[Cond] = []
        liq_warm = UNAVAILABLE if not ctx.liquidations else None
        cov_txt = f"coverage={cov}"

        # ---- Step 1: cascade over the last 5 minutes (market-method, one side) ----
        window = [lq for lq in ctx.liquidations if int(lq["ts"]) >= ctx.now_ms - _5M_MS and (lq.get("method") == "market")]
        by_side = {"long": [x for x in window if x.get("side") == "long"],
                   "short": [x for x in window if x.get("side") == "short"]}
        best_side = None; notional = 0.0; wallets = 0
        for side, rows in by_side.items():
            n = sum(float(x.get("notional") or 0) for x in rows)
            w = len({x.get("liquidated_user") for x in rows if x.get("liquidated_user")})
            if n > notional:
                best_side, notional, wallets = side, n, w
        min_notional = max(ctx.p("liq_notional_min_btc", 3_000_000) if ctx.coin == "BTC" else ctx.p("liq_notional_min_eth", 1_500_000),
                           (ctx.p("liq_notional_oi_frac", 0.0015) * float(oi)) if oi else 0.0)
        cascade_size_ok = (notional >= min_notional) if ctx.liquidations else None
        conds.append(Cond("Cascade size", f"market-liq notional 5m >= max($3M BTC/$1.5M ETH, 0.15% OI) · {cov_txt}",
                          value=(f"${notional / 1e6:.2f}M {best_side or ''}".strip() if ctx.liquidations else "no liquidation rows (partial feed)"),
                          threshold=f"need ${min_notional / 1e6:.2f}M", met=cascade_size_ok, warming=liq_warm))
        wallets_ok = (wallets >= ctx.p("min_distinct_wallets", 25)) if ctx.liquidations else None
        conds.append(Cond("Distinct wallets", ">= 25 liquidated wallets in the window", value=str(wallets),
                          threshold="need 25", met=wallets_ok, warming=liq_warm))

        # cascade range from the 5s tape over the window (falls back to the last two 15m closes)
        tape = [b for b in ctx.book_5s if int(b["ts"]) >= ctx.now_ms - _5M_MS]
        if tape:
            w_hi = max(float(b["mid"]) for b in tape); w_lo = min(float(b["mid"]) for b in tape)
            move_src = "5s tape"
        elif len(c) >= 2:
            w_hi = max(float(c[-1]["h"]), float(c[-2]["c"])); w_lo = min(float(c[-1]["l"]), float(c[-2]["c"]))
            move_src = "15m candles"
        else:
            w_hi = w_lo = None; move_src = "no tape"
        move_atr = ((w_hi - w_lo) / atr) if (w_hi is not None and np.isfinite(atr) and atr > 0) else None
        move_ok = (move_atr >= ctx.p("cascade_price_move_atr", 1.2)) if (move_atr is not None and ctx.liquidations) else None
        conds.append(Cond("Cascade move", "price move >= 1.2 ATR(14) during the window",
                          value=(f"{move_atr:.2f} ATR ({move_src})" if move_atr is not None else "n/a"),
                          threshold="need 1.2 ATR", met=move_ok, warming=(liq_warm or atr_warm)))
        cascade = bool(cascade_size_ok and wallets_ok and move_ok)
        # reversal direction: longs liquidated => price dumped => buy the reversal
        direction = ("long" if best_side == "long" else "short") if best_side else None
        wick = (w_lo if direction == "long" else w_hi) if (direction and w_lo is not None) else None

        # ---- Step 2: exhaustion ----
        # Quiet period: no new market-liq on that side for 45s
        side_rows = by_side.get(best_side, []) if best_side else []
        last_liq_ts = max((int(x["ts"]) for x in side_rows), default=None)
        quiet_s = ((ctx.now_ms - last_liq_ts) / 1000.0) if last_liq_ts else None
        conds.append(Cond("Quiet period", "no new market-liq on that side for 45s",
                          value=(f"{quiet_s:.0f}s since last" if quiet_s is not None else ("no cascade" if ctx.liquidations else "no liquidation rows")),
                          threshold="need 45s", met=((quiet_s >= ctx.p("quiet_seconds", 45)) if quiet_s is not None else None),
                          warming=liq_warm))

        # Taker exhaustion: taker vol on the liquidated side (longs liquidated -> sells)
        # in the latest minute < 40% of the peak minute inside the window
        tr = [t for t in ctx.trades_1m if int(t["ts"]) >= ctx.now_ms - _5M_MS - 60_000]
        key = "taker_sell_notional" if best_side == "long" else "taker_buy_notional"
        ex_ok = None; ex_val = "no 1m taker tape" if not ctx.trades_1m else ("no cascade" if not best_side else "n/a")
        if tr and best_side:
            vals = [float(t.get(key) or 0) for t in tr]
            peak = max(vals) if vals else 0.0
            latest = vals[-1] if vals else 0.0
            if peak > 0:
                ex_ok = latest < ctx.p("taker_vol_drop_frac", 0.4) * peak
                ex_val = f"{latest / peak:.0%} of peak"
        conds.append(Cond("Taker exhaustion", "taker vol on liquidated side < 40% of cascade peak",
                          value=ex_val, threshold="need < 40%", met=ex_ok,
                          warming=(UNAVAILABLE if not ctx.trades_1m else None)))

        # Book refill: depth within 0.3% on the swept side now >= 60% of the pre-cascade 15-min average
        depth_key = "bid_0_3" if best_side == "long" else "ask_0_3"
        pre = [b for b in ctx.book_5s if ctx.now_ms - _5M_MS - _15M_MS <= int(b["ts"]) < ctx.now_ms - _5M_MS]
        rf_ok = None; rf_val = "no 5s book tape" if not ctx.book_5s else ("no cascade" if not best_side else "n/a")
        rf_warm = UNAVAILABLE if not ctx.book_5s else None
        if ctx.book_5s and best_side:
            now_d = float(ctx.book_5s[-1].get(depth_key) or 0)
            pre_vals = [float(b.get(depth_key) or 0) for b in pre]
            if pre_vals:
                avg = sum(pre_vals) / len(pre_vals)
                if avg > 0:
                    rf_ok = now_d >= ctx.p("bid_depth_refill_frac", 0.6) * avg
                    rf_val = f"{now_d / avg:.0%} of pre-cascade avg"
            rf_warm = avail(len(pre_vals), 180, "book samples") if len(pre_vals) < 180 else None
        conds.append(Cond("Book refill", "bid/ask depth within 0.3% >= 60% of pre-cascade avg",
                          value=rf_val, threshold="need >= 60%", met=rf_ok, warming=rf_warm))

        # No next cluster: tracked-cohort liq map within 1 ATR beyond the wick < 50% of swept notional
        cl_ok = None; cl_val = "no liq map" if not ctx.liq_clusters else ("no cascade" if not wick else "n/a")
        if ctx.liq_clusters and wick is not None and np.isfinite(atr) and atr > 0 and notional > 0:
            lo, hi = ((wick - ctx.p("cluster_check_atr", 1.0) * atr, wick) if direction == "long"
                      else (wick, wick + ctx.p("cluster_check_atr", 1.0) * atr))
            side_needed = "long" if direction == "long" else "short"   # positions that would liquidate next on the same side
            beyond = sum(float(x["notional"] or 0) for x in ctx.liq_clusters
                         if x.get("liq_px") is not None and lo <= float(x["liq_px"]) <= hi and x.get("side") == side_needed)
            cl_ok = beyond < ctx.p("cluster_max_frac", 0.5) * notional
            cl_val = f"${beyond / 1e6:.2f}M beyond wick ({beyond / notional:.0%} of swept) · tracked-cohort map"
        conds.append(Cond("No next cluster", "cluster within 1 ATR beyond wick < 50% of swept size",
                          value=cl_val, threshold="need < 50%", met=cl_ok,
                          warming=(UNAVAILABLE if not ctx.liq_clusters else None)))
        exhaustion = all(x.met for x in conds[3:7]) if cascade else False

        # ---- context ----
        rc = regime_cond(ctx); conds.append(rc); reg_ok = rc.met
        # Funding lean: funding was leaning the liquidated side (positive funding = longs paying = crowd long)
        g = ctx.gauge or {}
        f8 = g.get("hl_funding_8h_equiv")
        lean = None
        if f8 is not None and best_side:
            lean = (float(f8) > 0) if best_side == "long" else (float(f8) < 0)
        conds.append(Cond("Funding lean", "funding was leaning the liquidated side (score only)",
                          value=(f"8h-equiv {float(f8) * 100:+.4f}%" if f8 is not None else "no gauge row") + (f" vs {best_side} liquidated" if best_side else ""),
                          met=lean, warming=(UNAVAILABLE if f8 is None else None)))
        conds.append(Cond("CEX confirm", "CEX aggregate liquidations confirm (score only)",
                          value="no CEX liquidation feed", met=None, warming=UNAVAILABLE))
        # Timing: 1m / 5m Fisher(9) from the 5s mid tape turning toward the reversal
        bars_1m = cm.bars_1m_from_book(ctx.book_5s, 120)
        bars_5m = cm.bars_5m_from_1m(bars_1m)
        ft1, w1 = cm.fisher_turn_warm([b["h"] for b in bars_1m], [b["l"] for b in bars_1m], 9)
        ft5, w5 = cm.fisher_turn_warm([b["h"] for b in bars_5m], [b["l"] for b in bars_5m], 9)
        d = (1 if direction == "long" else -1) if direction else 0
        timing_ok = ((ft1 == d) or (ft5 == d)) if direction else None
        conds.append(Cond("Timing", "1m/5m Fisher(9) turning toward the reversal",
                          value=f"fisher1m={ft1:+d} fisher5m={ft5:+d} (from 5s mid tape)", met=timing_ok,
                          warming=(UNAVAILABLE if not bars_1m else (w5 or w1))))

        r.conditions = conds
        r.direction = direction
        bias_ok = bias_align((ctx.bias or {}).get("bias_score"), direction)
        r.components = {
            "trigger": (ctx.p("score_trigger", 0.5) if (cascade and exhaustion) else (0.0 if ctx.liquidations else None)),
            "regime": (ctx.p("score_regime", 0.2) if reg_ok else (0.0 if ctx.regime else None)),
            "bias": (ctx.p("score_bias", 0.2) if bias_ok else (None if bias_ok is None else 0.0)),
            "timing": (ctx.p("score_timing", 0.1) if timing_ok else (None if timing_ok is None else 0.0)),
        }
        r.score_from_components()
        r.collect_warming()

        # sentence
        if not ctx.liquidations:
            r.waiting_for = f"No liquidation rows in the last 10 min ({cov_txt}) — no cascade to evaluate"
        elif not cascade:
            r.waiting_for = (f"Liq ${notional / 1e6:.2f}M / {wallets} wallets / {move_atr or 0:.2f} ATR in 5m — "
                             f"below cascade thresholds ({cov_txt})")
        elif not exhaustion:
            pend = [x.name for x in conds[3:7] if not x.met]
            r.waiting_for = f"Cascade {best_side} ${notional / 1e6:.1f}M — waiting exhaustion: {', '.join(pend)}"
            r.alerts.append(("pre", f"[S01 LIQ] {ctx.coin} cascade {best_side} ${notional / 1e6:.1f}M {wallets} wallets — watching exhaustion"))
        else:
            r.waiting_for = f"Cascade {best_side} exhausted — reversal {direction}, score {r.total_score:.2f}"

        fired = (cascade and exhaustion and bool(reg_ok) and r.total_score is not None
                 and r.total_score >= r.fire_threshold and all(x.met is not False for x in conds))
        r.fired = bool(fired)
        if fired and direction and wick is not None and np.isfinite(atr) and atr > 0:
            off = ctx.p("entry_offset_atr", 0.15) * atr
            entry_px = wick + off if direction == "long" else wick - off
            stop = wick - ctx.p("stop_atr", 0.35) * atr if direction == "long" else wick + ctx.p("stop_atr", 0.35) * atr
            rng = (w_hi - w_lo)
            t1 = wick + ctx.p("target1_retrace", 0.5) * rng if direction == "long" else wick - ctx.p("target1_retrace", 0.5) * rng
            r.intents = [OrderIntent(self.id, "hl", ctx.coin, direction, "entry", "post_only_limit", 0.0, px=entry_px,
                                     reason="S01 liquidation-sweep reversal",
                                     meta={"stop": stop, "target1": t1, "atr": atr,
                                           "time_stop_h": max(1, int(round(ctx.p("time_stop_min", 90) / 60))),
                                           "entry_valid_min": max(1, int(ctx.p("entry_cancel_seconds", 180) // 60))})]
            r.waiting_for = f"FIRED {direction} — entry {cm.fmt_px(entry_px)} stop {cm.fmt_px(stop)} t1 {cm.fmt_px(t1)}"
            r.alerts.append(("fire", f"[S01 LIQ] {ctx.coin} {direction.upper()} after {best_side} cascade ${notional / 1e6:.1f}M | entry {entry_px:.1f} stop {stop:.1f} t1 {t1:.1f} | score {r.total_score:.2f} (paper)"))
        return r
