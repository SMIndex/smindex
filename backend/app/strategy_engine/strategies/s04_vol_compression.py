"""s04 Volatility compression breakout (doc 04 §4/§6/§13 prompts 1-2) — verbatim
rules over the available window. State machine IDLE→COMPRESSING→BROKEN→ARMED is
reconstructed statelessly each candle: a box that was in place k candles ago
(k=1..3) and whose edge the k-th-last candle closed beyond by >= 0.15 ATR is a
break; the entry is valid for `entry_valid_candles` (3) after the break."""
from __future__ import annotations

import numpy as np

from ..features import indicators as ind
from ..intents import OrderIntent
from . import common as cm
from .base import Cond, EvalResult, PaperStrategy, StrategyContext, UNAVAILABLE, avail, bias_align, ohlc, regime_cond

_LOOK = 200


class VolCompressionBreakout(PaperStrategy):
    id = "s04_vol_compression"
    cadence = "candle_15m"

    def evaluate(self, ctx: StrategyContext) -> EvalResult:  # noqa: C901
        r = EvalResult(coin=ctx.coin, fire_threshold=ctx.p("fire_threshold", 0.7))
        c = ctx.candles_15m
        conds: list[Cond] = []
        have = len(c)
        highs, lows, closes = ohlc(c)
        ts = [int(x["ts"]) for x in c]
        rank_warm = avail(have, _LOOK, "bars")
        n_bb = int(ctx.p("bbw_n", 20)); k_bb = ctx.p("bbw_k", 2.0)

        # latest ranks (available window)
        bbw = ind.bbw_warm(closes, n_bb, k_bb) if have >= 2 else np.array([])
        atr_s = ind.atr_warm(highs, lows, closes, 14) if have else np.array([])
        atr = float(atr_s[-1]) if atr_s.size and np.isfinite(atr_s[-1]) else float("nan")
        bbw_rank = ind.percentile_rank(bbw, _LOOK) if bbw.size else float("nan")
        atr_rank = ind.percentile_rank(atr_s, _LOOK) if atr_s.size else float("nan")

        # box now (COMPRESSING) or box k candles ago that broke (BROKEN/ARMED)
        box_now = ind.detect_compression(highs, lows, closes, ts, n_bb, k_bb, 14, _LOOK, warm=True) if have >= 3 else None
        brk = None
        max_valid = int(ctx.p("entry_valid_candles", 3))
        for k in range(1, max_valid + 1):
            if have - k < 3:
                break
            box_k = ind.detect_compression(highs[:-k], lows[:-k], closes[:-k], ts[:-k], n_bb, k_bb, 14, _LOOK, warm=True)
            if not box_k:
                continue
            bc = c[-k]
            a_k = float(atr_s[-k - 1]) if atr_s.size > k and np.isfinite(atr_s[-k - 1]) else atr
            if not (np.isfinite(a_k) and a_k > 0):
                continue
            d = None
            if float(bc["c"]) >= box_k["box_high"] + ctx.p("break_atr", 0.15) * a_k:
                d = "long"
            elif float(bc["c"]) <= box_k["box_low"] - ctx.p("break_atr", 0.15) * a_k:
                d = "short"
            if not d:
                continue
            # subsequent candles must not have closed back inside the box
            back_inside = any(box_k["box_low"] <= float(x["c"]) <= box_k["box_high"] for x in c[-k + 1:]) if k > 1 else False
            if back_inside:
                continue
            brk = {"box": box_k, "candle": bc, "dir": d, "k": k, "atr": a_k}
            break
        box = brk["box"] if brk else box_now
        direction = brk["dir"] if brk else None
        state = "ARMED" if brk else ("COMPRESSING" if box_now else "IDLE")

        conds.append(Cond("BBW rank", "Bollinger band-width percentile(200) <= 20%",
                          value=(f"{bbw_rank:.0f}th pct" if np.isfinite(bbw_rank) else "n/a"), threshold="need <= 20",
                          met=((bbw_rank <= ctx.p("bbw_rank_max", 20)) if np.isfinite(bbw_rank) else None), warming=rank_warm))
        conds.append(Cond("ATR rank", "ATR(14) percentile(200) <= 25%",
                          value=(f"{atr_rank:.0f}th pct" if np.isfinite(atr_rank) else "n/a"), threshold="need <= 25",
                          met=((atr_rank <= ctx.p("atr_rank_max", 25)) if np.isfinite(atr_rank) else None), warming=rank_warm))
        conds.append(Cond("Compression duration", ">= 8 consecutive candles ATR rank <= 35%",
                          value=(f"{box['n_candles']} candles (box {cm.fmt_px(box['box_low'])}–{cm.fmt_px(box['box_high'])})" if box else "no box"),
                          threshold="need 8", met=(bool(box) if have >= 3 else None), warming=rank_warm))

        # break conditions
        if brk:
            bc = brk["candle"]; bx = brk["box"]; a_k = brk["atr"]
            edge = bx["box_high"] if direction == "long" else bx["box_low"]
            dist = abs(float(bc["c"]) - edge) / a_k
            rng = (float(bc["h"]) - float(bc["l"]))
            rng_ok = rng >= ctx.p("break_range_atr", 1.5) * bx["mean_atr"] if bx["mean_atr"] > 0 else None
            conds.append(Cond("Break distance", "close beyond box by >= 0.15 ATR", value=f"{dist:.2f} ATR {direction} ({brk['k']} candle(s) ago)",
                              threshold="need 0.15 ATR", met=True))
            conds.append(Cond("Break range", "break candle range >= 1.5x box mean ATR",
                              value=(f"{rng / bx['mean_atr']:.2f}x mean ATR" if bx["mean_atr"] > 0 else "n/a"),
                              threshold="need 1.5x", met=rng_ok))
        else:
            conds.append(Cond("Break distance", "close beyond box by >= 0.15 ATR",
                              value=("inside box" if box_now else "no box"), met=(False if box_now else None), warming=rank_warm))
            conds.append(Cond("Break range", "break candle range >= 1.5x box mean ATR", value="no break", met=None, warming=rank_warm))

        # OI rotation: OI >= +1.5% since box start; not fall > 0.5% on the break
        oi_ok = None; oi_val = "no OI tape"; oi_warm = UNAVAILABLE if not ctx.oi_1m else None
        if ctx.oi_1m and box:
            rows = [x for x in ctx.oi_1m if x.get("oi_notional")]
            if rows:
                start_ts = int(box["start_ts"]) - 900_000
                base = next((x for x in rows if int(x["ts"]) >= start_ts), rows[0])
                b = float(base["oi_notional"]); now_oi = float(rows[-1]["oi_notional"])
                rise = now_oi / b - 1.0 if b > 0 else None
                if int(rows[0]["ts"]) > start_ts:
                    oi_warm = f"warming: OI tape starts after the box ({cm.ago(int(rows[0]['ts']) - start_ts)} late)"
                fall_ok = True
                if brk:
                    bts = int(brk["candle"]["ts"])
                    pre = [x for x in rows if int(x["ts"]) <= bts - 900_000]
                    post = [x for x in rows if int(x["ts"]) <= bts]
                    if pre and post:
                        pb, pa = float(pre[-1]["oi_notional"]), float(post[-1]["oi_notional"])
                        fall_ok = (pa / pb - 1.0) >= -ctx.p("oi_break_fall_max", 0.005) if pb > 0 else True
                if rise is not None:
                    oi_ok = rise >= ctx.p("oi_rise_frac", 0.015) and fall_ok
                    oi_val = f"{rise:+.2%} since box start" + ("" if fall_ok else " · fell on break")
        elif ctx.oi_1m:
            oi_val = "no box"
        conds.append(Cond("OI rotation", "OI >= +1.5% since box start; not fall >0.5% on break",
                          value=oi_val, threshold="need +1.5%", met=oi_ok, warming=oi_warm))

        # HTF alignment
        hd1, w1 = cm.hull_dir_warm([float(x["c"]) for x in ctx.candles_1h], 21)
        d = (1 if direction == "long" else -1) if direction else 0
        conds.append(Cond("1h alignment", "1h Hull(21) slope agrees with the break",
                          value=f"hull1h={hd1:+d}", met=((hd1 == d) if direction else None), warming=w1))
        h4, l4, c4 = ohlc(ctx.candles_4h)
        hma4 = ind.hull_ma(c4, 21) if c4 else np.array([])
        atr4 = ind.atr_last(h4, l4, c4, 14)[0] if c4 else float("nan")
        against_ok = None; v4 = "n/a"
        w4 = avail(len(c4), cm.hull_need(21), "4h bars")
        if hma4.size >= 2 and np.isfinite(hma4[-1]) and np.isfinite(hma4[-2]) and np.isfinite(atr4) and atr4 > 0:
            sl = float(hma4[-1] - hma4[-2])
            v4 = f"4h Hull slope {sl / atr4:+.2f} ATR/bar"
            if direction:
                against = -sl if direction == "long" else sl
                against_ok = against < ctx.p("htf_against_atr", 0.5) * atr4
        conds.append(Cond("4h not against", "4h Hull(21) against < 0.5x 4h ATR/bar", value=v4, met=against_ok, warming=w4))

        # Depth beyond box: 0.5% band on the break side at the break vs the in-box average of that band
        dp_ok = None; dp_val = "no 5s book tape"; dp_warm = UNAVAILABLE if not ctx.book_5s else None
        if ctx.book_5s and brk:
            key = "ask_0_5" if direction == "long" else "bid_0_5"
            bts = int(brk["candle"]["ts"]); bstart = int(brk["box"]["start_ts"]) - 900_000
            inbox = [float(x.get(key) or 0) for x in ctx.book_5s if bstart <= int(x["ts"]) <= bts - 900_000]
            atbrk = [float(x.get(key) or 0) for x in ctx.book_5s if bts - 900_000 < int(x["ts"]) <= bts]
            if inbox and atbrk:
                ib = sum(inbox) / len(inbox); ab = sum(atbrk) / len(atbrk)
                if ib > 0:
                    dp_ok = ab >= ctx.p("depth_frac", 0.8) * ib
                    dp_val = f"{ab / ib:.0%} of in-box depth (0.5% band, approx)"
            else:
                dp_val = "book tape does not cover the box"
                dp_warm = "warming: book tape shorter than the box"
        elif ctx.book_5s:
            dp_val = "no break"
        conds.append(Cond("Depth beyond box", "depth 0.5% beyond edge >= 80% of in-box depth",
                          value=dp_val, threshold="need >= 80%", met=dp_ok, warming=dp_warm))

        # Regime + rising vol
        rc = regime_cond(ctx)
        ratio, wv = cm.rv_1h_vs_24h(closes)
        rising = (ratio > 1.0) if ratio is not None else None
        reg_ok = rc.met
        both = (bool(reg_ok) and bool(rising)) if (reg_ok is not None and rising is not None) else None
        conds.append(Cond("Regime + rising vol", "TRADE_ALLOWED and realized vol rising",
                          value=f"{rc.value} · RV 1h/24h {ratio:.2f}x" if ratio is not None else f"{rc.value} · RV n/a",
                          met=both, warming=(rc.warming or wv)))

        r.conditions = conds
        r.direction = direction
        bias_ok = bias_align((ctx.bias or {}).get("bias_score"), direction)
        comp_score = (ctx.p("score_compression", 0.2) * max(0.0, 1.0 - bbw_rank / ctx.p("bbw_rank_max", 20))) if (box and np.isfinite(bbw_rank)) else (0.0 if have >= 3 else None)
        if brk:
            bx = brk["box"]; rng = float(brk["candle"]["h"]) - float(brk["candle"]["l"])
            brk_score = ctx.p("score_break", 0.2) * min(1.0, (rng / bx["mean_atr"]) / ctx.p("break_range_atr", 1.5)) if bx["mean_atr"] > 0 else 0.0
        else:
            brk_score = 0.0
        htf = ((0.5 if hd1 == d else 0.0) + (0.5 if against_ok else 0.0)) if direction else 0.0
        r.components = {
            "compression": comp_score,
            "break": brk_score,
            "oi": (ctx.p("score_oi", 0.3) if oi_ok else (None if oi_ok is None else 0.0)),
            "htf": ctx.p("score_htf", 0.2) * htf if direction else 0.0,
            "bias": (ctx.p("score_bias", 0.1) if bias_ok else (None if bias_ok is None else 0.0)),
        }
        r.score_from_components()
        r.collect_warming()

        if state == "IDLE":
            r.waiting_for = (f"No compression — BBW {bbw_rank:.0f}th / ATR {atr_rank:.0f}th pct (need <=20 / <=25)"
                             if np.isfinite(bbw_rank) else "No compression — computing ranks")
        elif state == "COMPRESSING":
            r.waiting_for = f"Compressing {box_now['n_candles']} candles, box {cm.fmt_px(box_now['box_low'])}–{cm.fmt_px(box_now['box_high'])} — waiting for break"
            r.alerts.append(("pre", f"[S04 VOLC] {ctx.coin} compression {box_now['n_candles']} candles, box {box_now['box_low']:.1f}–{box_now['box_high']:.1f}"))
        else:
            failing = [x.name for x in conds if x.met is False]
            r.waiting_for = (f"Broke {direction} {brk['k']} candle(s) ago — score {r.total_score:.2f}/{r.fire_threshold:.2f}"
                             + (f", failing: {', '.join(failing)}" if failing else ""))

        fired = (brk is not None and bool(reg_ok) and r.total_score is not None and r.total_score >= r.fire_threshold
                 and all(x.met is not False for x in conds))
        r.fired = bool(fired)
        if fired:
            bx = brk["box"]; a_k = brk["atr"]
            edge = bx["box_high"] if direction == "long" else bx["box_low"]
            off = ctx.p("entry_offset_atr", 0.1) * a_k
            entry_px = edge + off if direction == "long" else edge - off
            box_mid = (bx["box_high"] + bx["box_low"]) / 2
            far = entry_px - ctx.p("stop_atr", 1.0) * a_k if direction == "long" else entry_px + ctx.p("stop_atr", 1.0) * a_k
            stop = box_mid if abs(entry_px - box_mid) <= abs(entry_px - far) else far
            height = bx["box_high"] - bx["box_low"]
            t1 = (bx["box_high"] + ctx.p("target1_box_mult", 1.5) * height) if direction == "long" else (bx["box_low"] - ctx.p("target1_box_mult", 1.5) * height)
            r.intents = [OrderIntent(self.id, "hl", ctx.coin, direction, "entry", "post_only_limit", 0.0, px=entry_px,
                                     reason="S04 compression break",
                                     meta={"stop": stop, "target1": t1, "atr": a_k,
                                           "time_stop_h": int(ctx.p("time_stop_hours", 4)),
                                           "entry_valid_min": int((max_valid - brk["k"] + 1) * 15)})]
            r.waiting_for = f"Break {direction} FIRED — entry {cm.fmt_px(entry_px)} stop {cm.fmt_px(stop)} t1 {cm.fmt_px(t1)}"
            r.alerts.append(("fire", f"[S04 VOLC] {ctx.coin} {direction.upper()} box break | entry {entry_px:.1f} stop {stop:.1f} t1 {t1:.1f} | score {r.total_score:.2f} (paper)"))
        return r
