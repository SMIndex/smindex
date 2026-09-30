"""Seed strategy state, risk state, feed-meta and parameters (Part 3.7/3.8).

Idempotent: safe to run on every worker/API startup. Every strategy row is
seeded/moved to paper (no gates); `waiting_for_sentence` starts as "Waiting for
first evaluation" and is overwritten on the first evaluation. Parameters are
every tunable named in docs 01–06 (verbatim defaults). The per-strategy counts
are asserted in tests and reported.
"""
from __future__ import annotations

import datetime as _dt
import logging
import time
from pathlib import Path

import yaml
from sqlalchemy import select

from app.db.strategy_models import (
    StratStrategyState, StratRiskState, StratParameter, StratFeedMeta, StratEvent,
)

logger = logging.getLogger("strategy_engine.seed")
_EVENTS_YAML = Path(__file__).resolve().parents[2] / "config" / "events.yaml"

STRATEGIES = [
    ("s01_liq_sweep", "Liquidation sweep reversal"),
    ("s02_funding_flow", "Funding settlement flow"),
    ("s03_whale_follow", "Whale / top-wallet following"),
    ("s04_vol_compression", "Volatility compression breakout"),
    ("s05_session_open", "Session open momentum"),
    ("s05c_session_open", "Session open — chase entry"),
    ("s06_hull_fisher_ema", "Hull + Fisher + 200 EMA trend pullback"),
    ("s06u_hull_fisher_ema", "Hull + Fisher + 200 EMA (ungated comparison)"),
]

WAITING = "Waiting for first evaluation"

# Models M1–M6 (docs 10–16) — registered BESIDE the 01–06 rows (D-01). Same
# strat_strategy_state table, seeded paper; the evaluator for these rows is
# model_runner.ModelEvaluator, not evaluator.REGISTRY.
MODELS = [
    ("m1_sweep_reclaim", "M1 Liquidity Sweep and Reclaim"),
    ("m2_bos_order_block", "M2 Break of Structure into Order Block"),
    ("m3_failed_auction", "M3 Failed Auction at Range Extreme"),
    ("m4_htf_choch", "M4 HTF Change of Character with Crowding"),
    ("m5_session_liquidity_run", "M5 Session Liquidity Run"),
    ("m6_weekly_open_reclaim", "M6 Weekly Open Reclaim"),
]

# (key, value, unit) — verbatim from docs 11–16 §Execution / doc 10 §7.
MODEL_PARAMETERS: dict[str, list[tuple[str, str, str]]] = {
    "m1_sweep_reclaim": [
        ("sweep_min_atr", "0.1", "atr"), ("sweep_max_atr", "0.5", "atr"), ("reclaim_candles", "3", "candles"),
        ("reclaim_quality_min", "0.5", "ratio"), ("entry_valid_candles", "3", "candles"), ("stop_beyond_wick_atr", "0.15", "atr"),
        ("t1_partial_pct", "40", "pct"), ("expected_hold_min", "90", "min"), ("hard_stop_min", "240", "min"),
        ("max_attempts_per_day", "3", "count"),
    ],
    "m2_bos_order_block": [
        ("break_lookback_h", "24", "h"), ("oi_break_min", "0.01", "fraction"), ("br_break_min", "0.65", "ratio"),
        ("funding_z_max", "1.5", "z"), ("stop_below_ob_atr", "0.15", "atr"), ("stop_max_atr", "1.2", "atr"),
        ("retrace_max_range_atr", "1.0", "atr"), ("entry_valid_h", "8", "h"), ("t1_partial_pct", "40", "pct"),
        ("expected_hold_min", "180", "min"), ("hard_stop_min", "480", "min"),
    ],
    "m3_failed_auction": [
        ("oi_push_min_1h", "0.005", "fraction"), ("retest_atr", "0.2", "atr"), ("entry_below_high_atr", "0.1", "atr"),
        ("stop_above_high_atr", "0.15", "atr"), ("entry_valid_candles", "3", "candles"), ("t1_partial_pct", "50", "pct"),
        ("expected_hold_min", "120", "min"), ("hard_stop_min", "360", "min"),
    ],
    "m4_htf_choch": [
        ("bos_min", "5", "count"), ("extension_3d_min", "0.08", "fraction"), ("oi_near_7d_high", "0.03", "fraction"),
        ("funding_z_peak_min", "1.0", "z"), ("bounce_br_max", "0.55", "ratio"), ("stop_above_swing_atr1h", "0.2", "atr"),
        ("stop_max_atr1h", "2.0", "atr"), ("entry_valid_h", "6", "h"), ("t1_partial_pct", "40", "pct"),
        ("t2_cluster_min_oi", "0.0015", "fraction"), ("expected_hold_min", "480", "min"), ("hard_stop_min", "2160", "min"),
    ],
    "m5_session_liquidity_run": [
        ("asia_range_min_atr", "0.8", "atr"), ("asia_range_max_atr", "4.0", "atr"), ("raid_min_atr", "0.1", "atr"),
        ("raid_max_atr", "0.6", "atr"), ("reclaim_candles", "3", "candles"), ("reclaim_quality_min", "0.5", "ratio"),
        ("open_tolerance_atr", "0.2", "atr"), ("entry_valid_candles", "3", "candles"), ("stop_beyond_wick_atr", "0.15", "atr"),
        ("t1_partial_pct", "40", "pct"), ("expected_hold_min", "90", "min"), ("hard_stop_after_window_min", "120", "min"),
        ("windows_utc", "07-09,13-15", "hours"),
    ],
    "m6_weekly_open_reclaim": [
        ("loss_min_atr4h", "0.8", "atr"), ("oi_4h_min", "0.01", "fraction"), ("br_4h_min", "0.55", "ratio"),
        ("funding_z_max", "1.0", "z"), ("entry_above_wo_atr15", "0.1", "atr"), ("pullback_wait_h", "4", "h"),
        ("entry_valid_h", "8", "h"), ("stop_below_low_atr1h", "0.2", "atr"), ("stop_max_atr1h", "2.5", "atr"),
        ("t1_partial_pct", "40", "pct"), ("expected_hold_min", "1440", "min"), ("hard_stop", "Fri 20:00 UTC", "time"),
    ],
}

# (key, value, unit) — verbatim defaults from docs 01–06.
PARAMETERS: dict[str, list[tuple[str, str, str]]] = {
    "s01_liq_sweep": [
        ("liq_notional_min_btc", "3000000", "usd"), ("liq_notional_min_eth", "1500000", "usd"),
        ("liq_notional_oi_frac", "0.0015", "fraction"), ("min_distinct_wallets", "25", "count"),
        ("cascade_price_move_atr", "1.2", "atr"), ("quiet_seconds", "45", "s"),
        ("taker_vol_drop_frac", "0.40", "fraction"), ("bid_depth_refill_frac", "0.60", "fraction"),
        ("cluster_check_atr", "1.0", "atr"), ("cluster_max_frac", "0.50", "fraction"),
        ("entry_offset_atr", "0.15", "atr"), ("entry_cancel_seconds", "180", "s"),
        ("requote_offset_atr", "0.25", "atr"), ("stop_atr", "0.35", "atr"),
        ("target1_retrace", "0.50", "fraction"), ("target1_take_frac", "0.60", "fraction"),
        ("target2_retrace", "0.70", "fraction"), ("time_stop_min", "90", "min"),
        ("fire_threshold", "0.65", "score"), ("score_trigger", "0.5", "weight"),
        ("score_regime", "0.2", "weight"), ("score_bias", "0.2", "weight"),
        ("score_timing", "0.1", "weight"),
    ],
    "s02_funding_flow": [
        ("z_extreme", "2.0", "zscore"), ("z_elevated", "1.0", "zscore"),
        ("binance_extreme_pos", "0.0005", "rate"), ("binance_extreme_neg", "-0.0003", "rate"),
        ("oi_rise_24h", "0.03", "fraction"), ("adverse_move_atr", "1.0", "atr"),
        ("entry_offset_atr", "0.1", "atr"), ("cancel_before_settle_min", "10", "min"),
        ("stop_atr", "0.6", "atr"), ("target_atr", "0.8", "atr"),
        ("time_stop_min", "60", "min"), ("size_mult", "0.75", "mult"),
        ("eval_before_settle_min", "30", "min"), ("settlement_hours_utc", "0,8,16", "hours"),
        ("fire_threshold", "0.65", "score"), ("score_crowding", "0.4", "weight"),
        ("score_oi", "0.2", "weight"), ("score_regime", "0.2", "weight"),
        ("score_timing", "0.2", "weight"), ("bias_weight", "0.25", "weight"),
    ],
    "s03_whale_follow": [
        ("cohort_size", "30", "count"), ("top_n_alltime", "500", "count"),
        ("top_n_30d", "500", "count"), ("pnl_to_vol_min", "0.005", "fraction"),
        ("closed_trades_min", "40", "count"), ("max_drawdown_max", "0.35", "fraction"),
        ("avg_leverage_max", "10", "x"), ("median_hold_min", "30", "min"),
        ("score_pnlvol_w", "0.4", "weight"), ("score_pnl90_w", "0.3", "weight"),
        ("score_dd_w", "0.3", "weight"), ("dropout_cooldown_days", "30", "days"),
        ("fresh_agree_min", "3", "count"), ("fresh_window_min", "60", "min"),
        ("fresh_add_frac", "0.25", "fraction"), ("net_dir_min", "0.3", "ratio"),
        ("pullback_atr", "0.5", "atr"), ("adverse_atr", "1.0", "atr"),
        ("entry_offset_atr", "0.2", "atr"), ("cancel_min", "30", "min"),
        ("stop_atr", "1.0", "atr"), ("target1_atr", "1.5", "atr"),
        ("target1_take_frac", "0.50", "fraction"), ("time_stop_hours", "6", "h"),
        ("fire_threshold", "0.65", "score"), ("score_cohort", "0.4", "weight"),
        ("score_pullback", "0.2", "weight"), ("score_regime", "0.2", "weight"),
        ("score_timing", "0.2", "weight"), ("bias_weight", "0.35", "weight"),
    ],
    "s04_vol_compression": [
        ("bbw_n", "20", "candles"), ("bbw_k", "2", "stddev"),
        ("bbw_rank_max", "20", "percentile"), ("atr_rank_max", "25", "percentile"),
        ("compression_atr_rank", "35", "percentile"), ("compression_min_candles", "8", "candles"),
        ("break_atr", "0.15", "atr"), ("break_range_atr", "1.5", "atr"),
        ("oi_rise_frac", "0.015", "fraction"), ("oi_break_fall_max", "0.005", "fraction"),
        ("depth_frac", "0.80", "fraction"), ("retest_atr", "0.2", "atr"),
        ("retest_wait_candles", "3", "candles"), ("entry_offset_atr", "0.1", "atr"),
        ("entry_valid_candles", "3", "candles"), ("stop_atr", "1.0", "atr"),
        ("target1_box_mult", "1.5", "mult"), ("target1_take_frac", "0.5", "fraction"),
        ("oi_exit_fall", "0.02", "fraction"), ("time_stop_hours", "4", "h"),
        ("fire_threshold", "0.7", "score"), ("score_compression", "0.2", "weight"),
        ("score_break", "0.2", "weight"), ("score_oi", "0.3", "weight"),
        ("score_htf", "0.2", "weight"), ("score_bias", "0.1", "weight"),
    ],
    "s05_session_open": [
        ("rv_percentile_min", "60", "percentile"), ("or_height_atr", "1.2", "atr"),
        ("break_atr", "0.1", "atr"), ("taker_vol_mult", "1.3", "mult"),
        ("taker_vol_lookback", "20", "candles"), ("entry_offset_atr", "0.1", "atr"),
        ("entry_valid_candles", "2", "candles"), ("stop_atr", "0.8", "atr"),
        ("target1_or_mult", "1.0", "mult"), ("target1_take_frac", "0.5", "fraction"),
        ("target2_or_mult", "2.0", "mult"), ("time_stop_hours", "3", "h"),
        ("fire_threshold", "0.65", "score"), ("score_volday", "0.25", "weight"),
        ("score_break", "0.2", "weight"), ("score_bias", "0.2", "weight"),
        ("score_timing", "0.2", "weight"), ("score_volume", "0.15", "weight"),
        ("eu_or_start_utc", "07:00", "time"), ("eu_window_end_utc", "10:00", "time"),
        ("us_or_daylight_utc", "13:30", "time"), ("us_or_standard_utc", "14:30", "time"),
        ("us_window_hours", "3.5", "h"), ("asia_or_start_utc", "00:00", "time"),
        ("asia_window_end_utc", "03:00", "time"),
    ],
    "s06_hull_fisher_ema": [
        ("ema_n", "200", "candles"), ("hull_15m_n", "55", "candles"),
        ("hull_1h_n", "21", "candles"), ("fisher_n", "9", "candles"),
        ("atr_n", "14", "candles"), ("fisher_extreme", "1.5", "value"),
        ("trend_consec_candles", "12", "candles"), ("ema_dist_atr_min", "0.5", "atr"),
        ("ema_dist_atr_max", "3.0", "atr"), ("pullback_min_candles", "2", "candles"),
        ("pullback_max_candles", "8", "candles"), ("pullback_hull_atr", "0.5", "atr"),
        ("oi_change_4h", "0.01", "fraction"), ("entry_offset_atr", "0.1", "atr"),
        ("entry_valid_candles", "2", "candles"), ("stop_below_pullback_atr", "0.2", "atr"),
        ("stop_cap_atr", "1.2", "atr"), ("target1_r_mult", "1.5", "mult"),
        ("target1_take_frac", "0.5", "fraction"), ("time_stop_hours", "6", "h"),
        ("fire_threshold_gated", "0.7", "score"), ("fire_threshold_ungated", "0.5", "score"),
        ("score_trend", "0.25", "weight"), ("score_pullback", "0.2", "weight"),
        ("score_fisher", "0.2", "weight"), ("score_gate", "0.25", "weight"),
        ("score_bias", "0.1", "weight"), ("size_mult_first30", "0.5", "mult"),
        ("bias_gate", "-0.2", "value"),
    ],
}
# ungated 06 shares the same parameters (comparison instance, doc 06 §15 prompt 2)
PARAMETERS["s06u_hull_fisher_ema"] = list(PARAMETERS["s06_hull_fisher_ema"])
# s05c chase-entry comparison shares s05's parameters (owner-approved experiment, 2026-09-04)
PARAMETERS["s05c_session_open"] = list(PARAMETERS["s05_session_open"])

FEED_META = [
    ("hl_candles", "full", "HL candle ws (15m/1h/4h) + candleSnapshot backfill."),
    ("hl_trades_1m", "full", "HL trades ws aggregated to 1-minute taker flow."),
    ("hl_book_5s", "full", "HL l2Book depth bands every 5s."),
    ("hl_oi_1m", "full", "HL activeAssetCtx OI/funding per minute."),
    ("funding_hl", "full", "HL predicted funding + fundingHistory."),
    ("funding_cex", "full", "Binance premiumIndex + Bybit tickers (per Decisions §d)."),
    ("liquidations", "partial", "Only wallets on the existing <=10-slot ws tier; NOT the docs' 2,000-wallet userFills (venue 10-users/IP limit). Every consumer honours coverage=partial."),
]


def parameter_counts() -> dict[str, int]:
    return {sid: len(rows) for sid, rows in PARAMETERS.items()}


async def load_events(session) -> int:
    """Load config/events.yaml into strat_events (idempotent by name+utc_ts).
    Only dates present in the file are loaded — nothing is fabricated."""
    if not _EVENTS_YAML.exists():
        logger.warning("events.yaml not found at %s — no macro events loaded", _EVENTS_YAML)
        return 0
    try:
        doc = yaml.safe_load(_EVENTS_YAML.read_text(encoding="utf-8")) or {}
    except Exception as exc:  # noqa: BLE001
        logger.warning("events.yaml parse failed: %s", exc)
        return 0
    existing = {(e.name, e.utc_ts) for e in (await session.execute(select(StratEvent))).scalars().all()}
    added = 0
    for ev in doc.get("events", []) or []:
        iso = ev.get("utc")
        name = ev.get("name")
        if not iso or not name:
            continue
        try:
            when = _dt.datetime.fromisoformat(iso.replace("Z", "+00:00"))
            utc_ms = int(when.timestamp() * 1000)
        except Exception:  # noqa: BLE001
            continue
        if (name, utc_ms) not in existing:
            session.add(StratEvent(name=name, utc_ts=utc_ms, kind=ev.get("kind")))
            added += 1
    await session.commit()
    return added


async def seed_all(session) -> dict:
    """Idempotent upsert of state/risk/params/feed-meta. Returns a summary dict."""
    now_ms = int(time.time() * 1000)

    # strategy_state: every row paper (no gates). Rows still carrying the old
    # gated states are moved to paper once; kill_switch/pauses are annotate-only.
    from .mode import DISABLED_STRATEGIES
    existing = {r.strategy_id: r for r in (await session.execute(select(StratStrategyState))).scalars().all()}
    for sid, _name in STRATEGIES + MODELS:
        st = existing.get(sid)
        disabled = sid in DISABLED_STRATEGIES
        if st is None:
            session.add(StratStrategyState(
                strategy_id=sid,
                requested_mode="off" if disabled else "paper",
                effective_mode="off" if disabled else "paper",
                effective_reason=(f"disabled: {DISABLED_STRATEGIES[sid]}" if disabled else "paper"),
                waiting_for_sentence=("Permanently disabled" if disabled else WAITING),
                kill_switch_tripped=False,
            ))
        else:
            if disabled:
                # D-99: force and KEEP it off; history rows are untouched.
                st.requested_mode = "off"
                st.effective_mode = "off"
                st.effective_reason = f"disabled: {DISABLED_STRATEGIES[sid]}"
                st.waiting_for_sentence = "Permanently disabled"
                continue
            if st.requested_mode not in ("paper", "off"):
                st.requested_mode = "paper"
            # D-100: the seeder used to clear kill_switch_tripped on EVERY worker
            # start, which silently undid every pause the risk engine asked for.
            # A tripped kill switch now survives restarts and needs a manual
            # re-arm (rearm_strategy() below) with a reason.
            if st.kill_switch_tripped:
                st.effective_mode = "paper-paused"
                st.effective_reason = st.effective_reason or "kill switch tripped — manual re-arm required"
            elif st.effective_mode not in ("paper", "paper-paused"):
                st.effective_mode = "paper"
                st.effective_reason = "paper"

    # risk_state single row — paper equity starts at STRATEGY_PAPER_EQUITY_USD
    from app.config import settings
    rs = (await session.execute(select(StratRiskState))).scalars().first()
    if rs is None:
        session.add(StratRiskState(equity_usd=float(settings.STRATEGY_PAPER_EQUITY_USD),
                                   daily_realized_pnl=0.0, consecutive_losses=0, open_positions_count=0))
    elif rs.equity_usd is None:
        rs.equity_usd = float(settings.STRATEGY_PAPER_EQUITY_USD)

    # parameters
    existing_params = {(p.strategy_id, p.key) for p in (await session.execute(select(StratParameter))).scalars().all()}
    pcount = 0
    for sid, rows in list(PARAMETERS.items()) + list(MODEL_PARAMETERS.items()):
        for key, value, unit in rows:
            if (sid, key) not in existing_params:
                session.add(StratParameter(strategy_id=sid, key=key, value=value, unit=unit, default=value))
                pcount += 1

    # feed meta
    existing_feeds = {f.feed for f in (await session.execute(select(StratFeedMeta))).scalars().all()}
    for feed, coverage, note in FEED_META:
        if feed not in existing_feeds:
            session.add(StratFeedMeta(feed=feed, coverage=coverage, note=note))

    await session.commit()
    events_loaded = await load_events(session)
    return {
        "strategies": len(STRATEGIES),
        "models": len(MODELS),
        "parameters_seeded": pcount,
        "parameter_counts": parameter_counts(),
        "events_loaded": events_loaded,
        "now_ms": now_ms,
    }
