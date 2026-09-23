"""M1 — Sweep and Reclaim (doc 11). Long described; shorts mirror.

Sequence: an eligible level (PDL, PWL, Asia/London low, 1h/4h equal lows, bottom
edge of a live 4h demand OB/FVG, long liq cluster ≥0.15% OI) is swept by a 15m
wick 0.1–0.5 ATR deep and reclaimed (close back above) within 3 candles with
reclaim quality ≥0.5, in the discount of the 4h range (daily bias is graded by the
htf_bias reason, not gated — D-42).
Spec v1.3 Part 3 (D-89): the reclaim must be CONFIRMED — either the reclaim close is
on a later candle than the wick candle, or (same-candle sweep+reclaim) one more 15m
candle closes above the level with its low not below the wick low. Entry post-only
at min(50% of the reclaim candle, reclaim close) − 0.05 ATR (rests below the
market), valid 3 candles from the confirmation (then the 15m FVG left by the
reclaim, 3 more). Stop wick −0.15 ATR, floored at 0.5 ATR(15m) (D-88). T1 nearest
pool / 1.5R (40%, BE). T2 next 4h supply / range top, trail 15m higher lows once
within 0.5 ATR.
"""
from __future__ import annotations

from typing import Optional

from app.strategy_engine.mind.base import ContextRule, Decision, Veto, clip
from app.strategy_engine.mind.snapshot import Snapshot
from app.strategy_engine.structure.candles import MIN_MS, TF_MS
from app.strategy_engine.structure.sweeps import confirmed_reclaim, detect_sweeps, sweep_count_today
from app.strategy_engine.structure.zones import nearest_zone
from . import model_base as mb
from .model_base import ModelIntent, ModelStrategy, fmt_atr, fmt_pct, fmt_px, fmt_usd, fmt_x

RQ_MIN = 0.5
ENTRY_VALID_CANDLES = 3
HOLD_MIN = 90
HARD_STOP_MIN = 240
LOOKBACK = 100          # 15m candles scanned for sweeps (covers a UTC day for the sweep count)


def _range_for(snap: Snapshot):
    """4h range, or the 1h range when the 4h range is wider than 6 ATR(4h) (D-26)."""
    r4, r1 = snap.s.range_4h, snap.s.range_1h
    a4 = snap.atr("4h")
    if r4 is not None and a4 > 0 and r4.width > 6 * a4 and r1 is not None:
        return r1, "1h"
    return r4, "4h"


def _eligible_levels(snap: Snapshot, direction: str) -> list[tuple[str, float, float, dict]]:
    """Level candidates; a session level cannot be swept inside its own session
    (the wick would be creating it — 'level created by the current candle
    doesn't count')."""
    out = []
    for t, px, st, meta in mb.level_candidates(snap, direction):
        if t.startswith("asia_") and snap.session == "asia":
            continue
        if t.startswith("london_") and snap.session in ("asia", "london"):
            continue
        out.append((t, px, st, meta))
    return out


class M1SweepReclaim(ModelStrategy):
    id = "m1_sweep_reclaim"
    model = "M1"
    name = "M1 Sweep and Reclaim"
    expected_hold_min = HOLD_MIN
    hard_stop_min = HARD_STOP_MIN
    detect_alert_kind = "sweep detected"

    def __init__(self) -> None:
        S = mb.setup_val
        reasons = [
            ("location", "which level was swept (PWL / 4h zone edge / 4h equal lows strongest)", 2.0, S("location_strength")),
            ("reclaim", "reclaim candle quality, discounted when it took all 3 candles", 2.0,
             lambda s: mb.reclaim_strength(s.setup)),
            ("fuel", "same-side liquidations during the sweep vs liq_5m_p90 (swept side)", 1.5, S("fuel_strength")),
            ("cleared", "OI fell during the sweep (positions cleared)", 1.2, S("cleared_strength")),
            ("delta_flip", "reclaim candle taker buy ratio above 0.5", 1.5, S("delta_flip_strength")),
            ("absorption", "CVD made a lower low while price barely extended (absorption)", 1.0, S("absorption_strength")),
            ("discount", "position inside the 4h range (deeper discount = stronger)", 1.2, S("discount_strength")),
            ("session", "first 120 min of London/NY best, dead hours worst", 0.8, lambda s: s.session_score()),
            ("cohort", "fresh cohort adds in the trade direction in the last 60 min", 1.0,
             lambda s: clip(s.cohort_fresh_adds(s.setup.get("direction", "long")) / 3.0)),
            ("htf_bias", "daily bias and 4h trend agreement", 1.0, S("htf_bias_strength")),
        ]
        vetoes = [
            Veto("too_deep", "wick deeper than 0.5 ATR — a break, not a sweep", lambda s: bool(s.setup.get("too_deep"))),
            Veto("third_sweep", "level already swept twice today", lambda s: int(s.setup.get("sweeps_before_today", 0)) >= 2),
            Veto("trend_against", "trend day against the trade direction",
                 lambda s: s.day_type == ("trend_down" if s.setup.get("direction") == "long" else "trend_up")),
            mb.event_30m_veto(),
            Veto("cluster_below_uncleared", "a ≥50% liquidation cluster sits within 1 ATR beyond the wick, not reduced",
                 lambda s: bool(s.setup.get("cluster_uncleared"))),
            mb.funding_extreme_veto("funding_extreme_same_side", lambda s: s.setup.get("direction")),
        ]
        rules = [
            mb.day_type_rule({"range": 1.1, "event": 0.7, "no_trade": 0.6}, fn=self._day_type_mult),
            ContextRule("session", lambda s: s.session_mult()),
            ContextRule("sweep_count_today", lambda s: 0.8 if int(s.setup.get("sweeps_before_today", 0)) >= 1 else 1.0),
            mb.form_rule(3, 5, 0.8, 0.9),
            mb.event_2h_rule(0.8),
        ]
        checks = [
            ("no_higher_low", "2 closed candles since entry without a higher low above the wick", mb.check_no_higher_low, 1),
            ("oi_bleeding", "OI down 1% since entry", mb.check_oi_bleeding(0.01), 1),
            ("delta_negative", "taker delta against the position for 2 candles", mb.check_delta_negative, 1),
            ("level_lost", "15m close back beyond the swept level", mb.check_close_beyond_level("level"), 2),
        ]
        self.mind = mb.build_mind(self.model, reasons, vetoes, rules, checks)

    # ---- multipliers ------------------------------------------------------
    @staticmethod
    def _day_type_mult(snap: Snapshot) -> Optional[float]:
        d = snap.setup.get("direction", "long")
        dt = snap.day_type
        if dt == ("trend_up" if d == "long" else "trend_down"):
            return 1.0
        if dt == "squeeze":
            z = snap.funding_z
            # squeeze against the sweep direction: crowd short (z<0) squeezes UP against a down-sweep
            if z is not None and ((d == "long" and z < 0) or (d == "short" and z > 0)):
                return 1.1
            return 1.0
        return None

    # ---- setup ------------------------------------------------------------
    def find_setup(self, snap: Snapshot) -> tuple[Optional[dict], str]:
        c15 = snap.s.c15
        atr = snap.atr("15m")
        if len(c15) < 30 or atr <= 0:
            return None, "warming: need 30 closed 15m candles and ATR"
        window = c15[-LOOKBACK:]
        last = len(window) - 1
        best: Optional[dict] = None
        missing_parts: list[str] = []
        for direction in ("long", "short"):
            side = mb.side_word(direction)
            levels = _eligible_levels(snap, direction)
            if not levels:
                missing_parts.append(f"no eligible level to sweep for {direction}")
                continue
            swept_here: list[dict] = []
            for t, px, st, meta in levels:
                sws = detect_sweeps(window, px, atr, side)
                conf = confirmed_reclaim(sws, window, px, side, last)      # D-89
                if conf is None:
                    continue
                sw = conf.sweep
                if sw.reclaim_quality < RQ_MIN:
                    missing_parts.append(f"{t} reclaim quality {sw.reclaim_quality:.2f} < {RQ_MIN}")
                    continue
                n_today = sweep_count_today(sws, snap.now_ms)
                swept_here.append({"level_type": t, "level": px, "location_base": st, "meta": meta, "sweep": sw,
                                   "confirmation": conf, "sweeps_before_today": max(0, n_today - 1)})
            if not swept_here:
                missing_parts.append(f"no confirmed reclaim of a swept eligible {side} on this candle")
                continue
            swept_here.sort(key=lambda x: -x["location_base"])
            top = swept_here[0]
            others = [(x["level_type"], x["level"]) for x in swept_here[1:]]
            others += [(t, px) for t, px, _, _ in levels if t != top["level_type"]]
            setup = self._setup_from(snap, direction, top, others, window)
            if setup is None:
                missing_parts.append(f"{direction} sweep of {top['level_type']} outside sequence")
                continue
            if best is None or setup["location_strength"] > best["location_strength"]:
                best = setup
        if best is None:
            return None, "; ".join(dict.fromkeys(missing_parts)) or "no sweep"
        # sequence gate that is not a veto (doc 11 §3 step 1): discount of the range.
        # Daily bias is NOT a gate (D-42): htf_bias is the graded reason (its 0.3 tier
        # is "daily down with trend_4h range") and trend_against (day_type) is the
        # only bias veto — a bias gate here made that tier unreachable.
        if not best["in_discount"]:
            return None, (f"sweep of {best['level_type']} reclaimed but price is in the "
                          f"{'premium' if best['direction'] == 'long' else 'discount'} of the {best['range_used']} range "
                          f"(pct {best['range_pct']:.2f})")
        return best, ""

    def _setup_from(self, snap: Snapshot, direction: str, top: dict, others, window) -> Optional[dict]:
        sw = top["sweep"]
        confm = top["confirmation"]
        atr = snap.atr("15m")
        c15 = snap.s.c15
        wick_c = window[sw.wick_index]
        rec_c = window[sw.reclaim_index]
        trig_c = window[confm.trigger_index]
        t_wick0 = wick_c.ts - TF_MS["15m"]
        # sweep-window data: liquidations of the trapped side, OI change wick-open → reclaim close
        liq_side = direction            # longs are liquidated on a down-sweep
        fuel_notional, fuel_wallets = snap.liq(liq_side, t_wick0, rec_c.ts)
        oi_sweep = snap.oi_change(t_wick0, rec_c.ts)
        oi_reclaim = snap.oi_change(rec_c.ts - TF_MS["15m"], rec_c.ts)
        tk = snap.taker_candle(rec_c)
        br = tk["ratio"] if tk else None
        # absorption: CVD at the sweep low vs the prior swing low of the same side
        cvd = snap.cvd(window)
        prior = None
        for s in reversed(snap.s.sw["15m"]):
            if s.kind == mb.side_word(direction) and s.ts < wick_c.ts and s.confirmed_at <= snap.now_ms:
                prior = s
                break
        absorption = 0.0
        if prior is not None:
            i_prior = mb.index_of_ts(window, prior.ts)
            conf = mb.cvd_lower_extreme(cvd, i_prior, sw.wick_index, direction)
            beyond = (prior.price - sw.wick_price) if direction == "long" else (sw.wick_price - prior.price)
            if conf is True and beyond <= 0.2 * atr:
                absorption = 1.0
            elif conf is not None:
                a, b = cvd[i_prior], cvd[sw.wick_index]
                rng = max((abs(x) for x in cvd if x is not None), default=0.0)
                if rng > 0 and abs((b or 0) - (a or 0)) <= 0.1 * rng:
                    absorption = 0.5
        rng, used = _range_for(snap)
        pct = rng.pct(snap.price) if rng else 0.5
        in_discount = (pct < 0.5) if direction == "long" else (pct > 0.5)
        discount_strength = clip((0.5 - pct) / 0.5) if direction == "long" else clip((pct - 0.5) / 0.5)
        # uncleared cluster beyond the wick
        cluster_uncleared = False
        swept_notional = float(top["meta"].get("notional") or 0.0)
        band_ref = snap.band_p80()          # spec v1.1 Part C: the 50% reference is band_p80 (was the swept cluster)
        for c in snap.s.clusters:
            if c.side != liq_side:
                continue
            beyond = (sw.wick_price - c.level) if direction == "long" else (c.level - sw.wick_price)
            if 0 < beyond <= 1.0 * atr and band_ref > 0 and c.notional >= 0.5 * band_ref:
                cluster_uncleared = True
        daily, t4 = snap.daily_bias, snap.s.trend("4h")
        want = mb.trend_for(direction)
        if daily == want and t4 in (want, "range"):
            htf = 1.0
        elif daily == "neutral":
            htf = 0.6
        elif daily == mb.trend_for(mb.opposite(direction)) and t4 == "range":
            htf = 0.3
        else:
            htf = 0.0
        loc = min(1.0, top["location_base"] + mb.coincident_bonus(top["level"], others, atr, top["level_type"]))
        return {
            "direction": direction, "level_type": top["level_type"], "level": float(top["level"]),
            "location_strength": loc, "wick_price": sw.wick_price, "wick_ts": sw.wick_ts, "depth_atr": sw.depth_atr,
            "too_deep": sw.too_deep, "reclaim_quality": sw.reclaim_quality, "candles_to_reclaim": sw.candles_to_reclaim,
            "reclaim_ts": rec_c.ts, "trigger_ts": trig_c.ts, "reclaim_high": rec_c.h, "reclaim_low": rec_c.l,
            "reclaim_close": rec_c.c, "confirmation_ts": trig_c.ts,
            # D-89: reclaim type on the signal row — candles from wick to reclaim close, and whether
            # the same-candle case needed the extra confirming close
            "reclaim_candles": int(sw.candles_to_reclaim), "confirmation_used": bool(confm.confirmation_used),
            "entry_candle_high": rec_c.h if direction == "long" else rec_c.l,
            "sweeps_before_today": top["sweeps_before_today"],
            "fuel_notional": fuel_notional, "fuel_wallets": fuel_wallets,
            # spec v1.1 Part C: denominator = liq_5m_p90 of the swept side (0.10% OI only when uncalibrated)
            "fuel_strength": clip(fuel_notional / snap.liq_p90(liq_side)) if snap.liq_p90(liq_side) > 0 else 0.0,
            "fuel_norm": snap.liq_p90(liq_side), "band_p80": band_ref,
            "oi_change_sweep": oi_sweep,
            "cleared_strength": clip(-(oi_sweep or 0.0) * 100.0 / 1.0),
            "br_reclaim": br, "oi_change_reclaim": oi_reclaim,
            # mirrored for shorts (doc 11 "mirror for shorts": sell ratio above 0.5; audit D-50)
            "delta_flip_strength": clip(mb.sgn(direction) * ((br if br is not None else 0.5) - 0.5), 0, 0.25) / 0.25,
            "absorption_strength": absorption,
            "range_used": used, "range_pct": pct, "in_discount": in_discount, "discount_strength": discount_strength,
            "cluster_uncleared": cluster_uncleared, "htf_bias_strength": htf,
            "cohort_net_dir_entry": snap.cohort_net_dir,
        }

    # ---- pre-alert: sweep in progress ------------------------------------------
    def pre_alerts(self, snap: Snapshot):
        c15 = snap.s.c15
        atr = snap.atr("15m")
        if len(c15) < 30 or atr <= 0:
            return []
        out = []
        last = c15[-1]
        for direction in ("long", "short"):
            for t, px, _st, _m in _eligible_levels(snap, direction):
                if direction == "long" and last.l < px - 0.1 * atr and last.c <= px:
                    out.append((f"{self.model}_pre", f"M1:{snap.coin}:pre:{t}:{last.ts}",
                                f"[M1] {snap.coin} sweep in progress: wick {(px - last.l) / atr:.2f} ATR below {t} {fmt_px(px)} — awaiting reclaim (≤3 candles)"))
                if direction == "short" and last.h > px + 0.1 * atr and last.c >= px:
                    out.append((f"{self.model}_pre", f"M1:{snap.coin}:pre:{t}:{last.ts}",
                                f"[M1] {snap.coin} sweep in progress: wick {(last.h - px) / atr:.2f} ATR above {t} {fmt_px(px)} — awaiting reclaim (≤3 candles)"))
        return out[:2]

    # ---- execution ---------------------------------------------------------
    def build_intent(self, snap: Snapshot, setup: dict, d: Decision) -> Optional[ModelIntent]:
        atr = snap.atr("15m")
        direction = setup["direction"]
        entry = mb.reclaim_entry_px(direction, setup["reclaim_high"], setup["reclaim_low"], setup["reclaim_close"], atr)   # D-89
        if direction == "long":
            stop = setup["wick_price"] - 0.15 * atr
        else:
            stop = setup["wick_price"] + 0.15 * atr
        risk = abs(entry - stop)
        if risk <= 0:
            return None
        pool = mb.nearest_pool_beyond(snap, entry, direction, min_dist=0.2 * atr)
        r15 = entry + mb.sgn(direction) * 1.5 * risk
        if pool is not None:
            t1 = min(pool.level, r15) if direction == "long" else max(pool.level, r15)
        else:
            t1 = r15
        # T2: next 4h opposing zone edge, else the 4h range extreme
        zs = [z for z in snap.s.zones.get("4h", []) if z.live]
        z = nearest_zone(zs, entry, above=(direction == "long"))
        r4 = snap.s.range_4h
        if z is not None and z.direction == ("bearish" if direction == "long" else "bullish"):
            t2 = z.bottom if direction == "long" else z.top
        elif r4 is not None:
            t2 = r4.high if direction == "long" else r4.low
        else:
            t2 = entry + mb.sgn(direction) * 3.0 * risk
        if (direction == "long" and t2 <= t1) or (direction == "short" and t2 >= t1):
            t2 = entry + mb.sgn(direction) * max(3.0 * risk, abs(t1 - entry) * 1.5)
        valid_until = setup["confirmation_ts"] + ENTRY_VALID_CANDLES * TF_MS["15m"]     # D-89: from the confirmation
        setup["t1"], setup["t2"], setup["entry"], setup["stop"] = t1, t2, entry, stop
        return ModelIntent(direction, entry, stop, t1, t2, None, valid_until, 0, "15m", "near_t2", 40.0,
                           fallback_entry={"type": "fvg_reclaim", "reclaim_ts": setup["reclaim_ts"], "candles": ENTRY_VALID_CANDLES,
                                           "cancel_level": setup["level"]},      # second_close_below veto while pending (D-43)
                           entry_offset_note=mb.RECLAIM_ENTRY_NOTE)

    def thesis(self, snap: Snapshot, setup: dict, d: Decision) -> str:
        t1 = setup.get("t1")
        return (f"M1 {setup['direction']} {snap.coin} at {setup['level_type']} {fmt_px(setup['level'])}. "
                f"Sweep {fmt_atr(setup['depth_atr'])} ATR with {fmt_usd(setup['fuel_notional'])} liquidated and OI "
                f"{fmt_pct(setup['oi_change_sweep'])}. Reclaim quality {fmt_x(setup['reclaim_quality'])}. "
                f"Strongest: {d.top3()}. Wrong if 15m closes {'below' if setup['direction'] == 'long' else 'above'} "
                f"{fmt_px(setup['level'])} again or price breaks {fmt_px(setup.get('stop'))}. "
                f"Expect {fmt_px(t1) if t1 else 'T1'} within {HOLD_MIN} min.")

    def alert_detected(self, snap: Snapshot, setup: dict) -> str:
        return (f"[M1] {snap.coin} sweep+reclaim of {setup['level_type']} {fmt_px(setup['level'])} "
                f"({setup['direction']}): depth {fmt_atr(setup['depth_atr'])} ATR, rq {fmt_x(setup['reclaim_quality'])}, "
                f"{fmt_usd(setup['fuel_notional'])} liquidated, OI {fmt_pct(setup['oi_change_sweep'])}")

    def state_updates(self, snap: Snapshot, setup, d, took: bool) -> dict:
        return mb.bump_attempts(snap.model_state, "attempts", snap.now_ms) if took else {}
