# 12. M2 Break of Structure, Return to Order Block

Depends on doc 10. Paper mode from day one, tag `M2`.

## 1. Overview
A displacement candle breaks structure with real participation (OI rising, delta skewed), leaves an order block and FVG behind, and price returns to fill them. Enter at the zone in the direction of the break, stop below the block, target the displacement high then the next pool. Long version described.

Type: continuation. Day types allowed: trend in the trade direction, range only if the break is on 4h. Expected hold: 180 minutes. Frequency: 0 to 2 per day.

## 2. Sequence (long)
1. Alignment: trend_4h up, or a fresh CHoCH to up on 4h confirmed by a 1h BOS up within the last 8 hours, or (spec v1.1, D-64) trend_4h range with trend_1h up and daily bias up — the branch used is logged as `alignment_branch` (weight 0) in reasons_json.
2. Break: a displacement leg of up to 3 consecutive 1h candles in the break direction whose combined range is at least 1.5 ATR(1h) and whose net body (last close minus first open) is at least 60 percent of the combined range, closing above the most recent 1h swing high and leaving at least one bullish FVG inside the leg (spec v1.2, D-75; was a single 1h displacement candle or a 15m 2-candle sequence). The order block is the last opposite candle before the leg. displacement_grade is computed on the leg's combined range and net body.
3. Data on the break: OI up >= 1.0 percent across the displacement window; taker buy ratio >= 0.65; funding z < 1.5.
4. Zone: the bullish OB (last opposite candle before the leg) and FVG created by the displacement leg. Zone must sit in discount of the new range (displacement high to the last 1h swing low).
5. Retrace: price returns to the FVG mid or OB top. Retrace candles have range <= 1.0 ATR each and no displacement against.
6. Data on the retrace: OI change since break >= -0.5 percent (longs holding); taker buy ratio between 0.35 and 0.55 (passive pullback); the new long liquidation cluster formed by the break remains above the OB bottom.
7. Mind evaluates.

## 3. Execution
- Entry: post-only at the FVG mid, or OB top if there is no FVG. Valid until price fills or a 15m close below the OB. Post-only orders that would cross the touch are rejected and re-quoted once one tick inside it (spec v1.3, D-87); the re-quote fills only when a later print goes through it, else it is cancelled at the end of the validity.
- Stop: OB bottom minus 0.15 ATR. Skip if stop distance > 1.2 ATR from entry. Minimum 0.5 ATR(1h) stop distance (spec v1.3, D-88).
- T1: displacement high, close 40 percent, stop to breakeven.
- T2: next 1h pool above (equal highs, prior day high). On day_type trend_up, trail behind each new 15m higher low instead of fixed T2.
- Time stop: dead-trade check at 270 minutes; hard stop 8 hours.

## 4. Heuristic Mind for M2

### 4.1 Reasoning behind the weights
The single most important question is whether the break was new positioning or short covering, so OI behaviour on the break has the top weight and OI falling is also a veto. Displacement grade proves participation, top tier. Zone location in discount is a strong filter because pullbacks into premium usually go deeper. Retrace calmness and OI holding during the retrace are second tier. Funding young and htf agreement are environmental. A nested 15m sweep into the OB is the highest-quality version so it earns its own reason.

### 4.2 Reasons
| key | description | detector | weight |
|---|---|---|---|
| oi_new_positioning | OI rose on the break | clip(OI_change_break_pct / 2.0, 0, 1) | 2.0 |
| displacement | participation | displacement_grade | 1.8 |
| zone_discount | zone in lower half of new range | clip((0.5 - zone_pct_of_new_range) / 0.5, 0, 1) | 1.5 |
| retrace_calm | orderly pullback | 1.0 if max retrace candle range <= 0.7 ATR and volume falling; 0.6 if <= 1.0 ATR; 0 otherwise | 1.2 |
| oi_holding | longs not exiting | clip(1 + OI_change_since_break_pct / 1.0, 0, 1) | 1.5 |
| delta_break | aggressor skew on break | clip((taker_buy_ratio_break - 0.5) / 0.25, 0, 1) | 1.2 |
| funding_young | move not crowded | clip((1.5 - funding_z) / 1.5, 0, 1) | 0.8 |
| htf_agree | daily and 4h aligned | 1.0 daily up and trend_4h up; 0.7 daily neutral and trend_4h up; 0.4 fresh 4h CHoCH only or the v1.1 1h-trend-with-daily-bias branch | 1.2 |
| nested_sweep | 15m minor low swept into the OB | 1.0 if a 15m sweep of a minor low occurred inside the zone with reclaim_quality >= 0.5; 0 otherwise | 1.5 |
| cluster_cleared | short liquidation cluster cleared on the break | clip(short_liq_notional_break / liq_5m_p90_short, 0, 1) — calibrated (spec v1.1 Part C; was 0.10 percent of OI) | 0.8 |

### 4.3 Vetoes
- `short_covering`: OI change across the break <= 0. Hand the setup to M3 logic (log only).
- `oi_exit_retrace`: OI down >= 2.0 percent since the break.
- `displacement_against`: a candle with range >= 1.5 ATR against the trade during the retrace.
- `zone_premium`: zone entirely above the 50 percent of the new range.
- `late_day`: current time within 60 minutes of 00:00 UTC, or Friday after 20:00 UTC.
- `event_30m`.
- `day_type_against`: trend_down for longs.
- `stop_too_wide`: stop distance > 1.2 ATR.

### 4.4 Context multipliers
- day_type: trend same direction 1.15; range 0.9 (only allowed if the break is 4h); squeeze same direction 1.0; event 0.7; no_trade 0.6.
- session: London or New York 1.0; Asia 0.8; dead 0.7.
- zone status: fresh 1.0; tested once 0.85.
- recent_form: 3 losses 0.8; 5 wins 0.9.
- event_2h: 0.8.

### 4.5 In-trade checks
- `below_ob`: 15m close below the OB bottom (counts as 2).
- `oi_dropping`: OI down >= 1.5 percent since entry.
- `no_progress`: after 4 closed candles price has not made a higher high than the entry candle.
- `delta_selling`: taker buy ratio < 0.4 on the last 2 candles.
exit_threshold = 2.

### 4.6 Thesis template
"M2 long {coin} into {zone_type} {zone_top} to {zone_bottom} after 1h BOS at {bos_level}. Break added {oi_change} OI with {delta} buy ratio, displacement grade {dg}. Strongest: {top3}. Wrong if 15m closes below {ob_bottom}. Expect {t1} within 3h."

## 5. Claude Code prompt (paste verbatim)
```
Implement strategies/m2_bos_order_block.py as class M2BreakOfStructure in the existing strategy engine using structure/ and mind/. Tag model M2. Paper mode, BTC and ETH.

Sequence for longs: trend_4h up, or a 4h CHoCH to up confirmed by a 1h BOS up within the last 8 hours, or trend_4h range with trend_1h up and daily bias up (spec v1.1). A displacement leg of up to 3 consecutive 1h candles in the break direction (combined range at least 1.5 ATR(1h), net body last close minus first open at least 60 percent of the combined range; spec v1.2, D-75) closes above the most recent 1h swing high and leaves at least one bullish FVG inside the leg; the order block is the last opposite candle before the leg and displacement_grade is computed on the leg's combined range and net body. Record the break window and compute OI change percent across it, taker buy ratio across it, and funding z at the break. Define the new range as the displacement high to the last 1h swing low. Zone is the bullish order block and FVG created by the displacement. Wait for price to return to the FVG mid or OB top with retrace candles each having range at most 1.0 ATR and no displacement candle against the direction. Build the Snapshot and call the M2 Mind. Mirror for shorts.

M2 Mind reasons and weights: oi_new_positioning clip(OI change percent across the break over 2.0, 0, 1) weight 2.0; displacement equal to displacement_grade weight 1.8; zone_discount clip((0.5 minus zone position in the new range) over 0.5, 0, 1) weight 1.5; retrace_calm 1.0 when max retrace candle range at most 0.7 ATR with falling volume, 0.6 when at most 1.0 ATR, else 0, weight 1.2; oi_holding clip(1 plus OI change percent since the break over 1.0, 0, 1) weight 1.5; delta_break clip((taker buy ratio on the break minus 0.5) over 0.25, 0, 1) weight 1.2; funding_young clip((1.5 minus funding z) over 1.5, 0, 1) weight 0.8; htf_agree 1.0 when daily up and trend_4h up, 0.7 daily neutral with trend_4h up, 0.4 fresh 4h CHoCH only or the v1.1 alternative branch, weight 1.2; nested_sweep 1.0 when a 15m sweep of a minor low occurred inside the zone with reclaim_quality at least 0.5, else 0, weight 1.5; cluster_cleared clip(short liquidation notional during the break over liq_5m_p90 of the cleared side (calibrated, spec v1.1 Part C; was 0.10 percent of OI), 0, 1) weight 0.8.

Vetoes: short_covering (OI change across the break at most 0), oi_exit_retrace (OI down at least 2.0 percent since the break), displacement_against (a candle with range at least 1.5 ATR against the direction during the retrace), zone_premium (zone entirely above the 50 percent of the new range), late_day (within 60 minutes of 00:00 UTC or Friday after 20:00 UTC), event_30m, day_type_against (trend_down for longs), stop_too_wide (stop distance above 1.2 ATR).

Context multipliers: day_type trend same direction 1.15, range 0.9 and only permitted when the break is on 4h, squeeze same direction 1.0, event 0.7, no_trade 0.6; session London or New York 1.0, Asia 0.8, dead 0.7; zone fresh 1.0, tested once 0.85; three consecutive M2 losses 0.8, five wins 0.9; event within 2 hours 0.8.

Execution: post-only at the FVG mid, or OB top when there is no FVG, valid until filled or a 15m close below the OB. Stop at OB bottom minus 0.15 ATR. Minimum stop distance (spec v1.3, D-88): if the stop is closer than 0.5 ATR(1h) to entry it is moved out to exactly 0.5 ATR(1h) beyond entry; size and R use the final stop, targets stay as computed from the structural stop, `stop_floor_applied` is recorded on the trade row. T1 at the displacement high, close 40 percent, stop to breakeven. T2 at the next 1h pool above; when day_type is trend_up trail behind each new 15m higher low instead. expected_hold_min 180; hard time stop 480 minutes. Size via RiskEngine using size_tier.

In-trade checks: below_ob (15m close below OB bottom, counts as 2), oi_dropping (OI down at least 1.5 percent since entry), no_progress (no higher high than the entry candle after 4 closed candles), delta_selling (taker buy ratio below 0.4 on the last 2 candles). exit_threshold 2.

Thesis template: "M2 long {coin} into {zone_type} {zone_top} to {zone_bottom} after 1h BOS at {bos_level}. Break added {oi_change} OI with {delta} buy ratio, displacement grade {dg}. Strongest: {top3}. Wrong if 15m closes below {ob_bottom}. Expect {t1} within 3h."

Log every evaluation with all Mind fields, alert on break detected, on take with thesis, on skip with vetoes, and on exit with exit_reason and R. Unit tests: a valid break and retrace, a short_covering veto, a zone_premium veto, an in-trade below_ob exit.
```
