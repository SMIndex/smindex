# 15. M5 Session Liquidity Run (Asia Range Raid)

Depends on doc 10. Paper mode, tag `M5`.

## 1. Overview
Asia (00:00 to 07:00 UTC) builds a range. In the first two hours of London (07:00 to 09:00 UTC) or New York (13:00 to 15:00 UTC), price runs one side of the Asia range, takes the stops, and reverses back through the range. Enter on the reclaim toward the opposite side of the Asia range. This is M1 restricted to one level type and one time window, with its own Mind because the context is different. Long version (raid of the Asia low) described.

Type: session liquidity reversal. Day types allowed: range, squeeze; trend only if the raid is against the trend and the daily bias favours the reversal. Expected hold: 90 minutes. Frequency: up to 2 per day per coin (one London, one New York).

## 2. Sequence (long)
1. Asia range: high and low of 00:00 to 07:00 UTC, frozen at 07:00. Range height must be between 0.8 and 4.0 ATR(15m) (too small is noise, too large already trended).
2. Window: 07:00 to 09:00 UTC (London) or 13:00 to 15:00 UTC (New York). Session did not open outside the Asia range (open within the range or within 0.2 ATR of an edge).
3. Raid: a 15m wick below the Asia low by 0.1 to 0.6 ATR.
4. Reclaim: within 3 candles a 15m close back above the Asia low with reclaim_quality >= 0.5. Confirmation (spec v1.3, D-89): either (a) the reclaim close is on a later candle than the raid wick, or (b) when raid and reclaim are the same candle, one additional 15m candle closes above the Asia low with its low not below the raid wick low. Entry is placed only after that confirmation; `reclaim_candles` and `confirmation_used` are recorded on the signal row.
5. Data: liquidation fills on the long side during the raid; OI fell during the raid; taker delta positive on the reclaim candle.
6. Bias: daily_bias up or neutral preferred; a raid of the Asia low with daily_bias up is the ideal case.
7. Mind evaluates.

## 3. Execution
- Entry (spec v1.3, D-89): post-only at min(50 percent of the reclaim candle, reclaim candle close) minus 0.05 ATR(15m) — mirror for shorts — so the order always rests below the market; valid 3 candles from confirmation. Post-only orders that would cross the touch are rejected and re-quoted once one tick inside it (spec v1.3, D-87); the re-quote fills only when a later print goes through it, else it is cancelled at the end of the validity.
- Stop: raid wick low minus 0.15 ATR. Minimum stop distance (spec v1.3, D-88): if the stop is closer than 0.5 ATR(15m) to entry it is moved out to exactly 0.5 ATR(15m) beyond entry; size and R use the final stop, targets stay as computed from the structural stop, `stop_floor_applied` is recorded on the trade row.
- T1: Asia range mid, close 40 percent, stop to breakeven.
- T2: Asia range high. If day_type is trend_up after T1, trail behind 15m higher lows and let it run past the Asia high.
- Time stop: dead-trade at 135 minutes; hard stop at the end of the session window plus 2 hours.
- One attempt per session window per coin.

## 4. Heuristic Mind for M5

### 4.1 Reasoning behind the weights
The Asia range is the only level, so location quality is replaced by range quality (height and cleanness) and by where the session opened relative to it. Raids that happen in the first 45 minutes of the window are the classic pattern and get a timing reason. Daily bias alignment (raid against the bias, reversal with it) is the strongest single filter for this model. Fuel and reclaim quality as in M1. The pre-session drift matters: if price already trended toward the raided side for 3 hours before the window, the raid is more likely a continuation, so that is a negative reason expressed as a veto threshold and a graded reason.

### 4.2 Reasons
| key | description | detector | weight |
|---|---|---|---|
| bias_alignment | reversal direction agrees with daily bias | 1.0 daily up (for a long after a low raid); 0.6 neutral; 0.2 down | 2.0 |
| range_quality | clean Asia range | 1.0 if 1.2 to 2.5 ATR and no more than 2 wicks outside by > 0.1 ATR during Asia; 0.6 if 0.8 to 1.2 or 2.5 to 4.0 ATR; 0 otherwise | 1.5 |
| reclaim | reclaim candle quality | clip((reclaim_quality - 0.4) / 0.4, 0, 1), x0.7 if 3 candles | 1.8 |
| fuel | forced flow on the raid | clip(long_liq_notional_raid / (0.8 times liq_5m_p90 of the raided side), 0, 1) — calibrated (spec v1.1 Part C; was 0.08 percent of OI) | 1.3 |
| cleared | OI dropped on the raid | clip(-OI_change_raid_pct / 0.8, 0, 1) | 1.0 |
| delta_flip | buyers on reclaim | clip((taker_buy_ratio_reclaim - 0.5) / 0.25, 0, 1) | 1.3 |
| early_in_window | raid in the first 45 minutes of the window | 1.0 if <= 45 min; 0.6 if <= 90; 0.3 otherwise | 1.0 |
| open_location | session opened inside the range | 1.0 inside middle 60 percent; 0.7 inside but near the raided edge; 0.3 within 0.2 ATR outside the raided edge | 1.0 |
| no_pre_drift | no 3h drift toward the raided side before the window | clip(1 - pre_window_3h_move_toward_edge / (1.5 ATR), 0, 1) | 1.2 |
| cohort | cohort adds on the raid | clip(cohort_fresh_adds_same_direction_60m / 2, 0, 1) | 0.8 |

### 4.3 Vetoes
- `opened_outside`: session opened more than 0.2 ATR outside the Asia range.
- `too_deep`: raid depth > 0.6 ATR.
- `range_bad`: Asia range height < 0.8 or > 4.0 ATR.
- `already_taken`: an M5 trade was already taken in this window for this coin.
- `event_30m`.
- `trend_day_with_raid`: day_type is trend_down and the raid is of the Asia low (continuation, not reversal).
- `late_in_window`: raid reclaim completes after the window closes.
- `funding_extreme_same_side`.

### 4.4 Context multipliers
- day_type: range 1.1; squeeze against the raid direction 1.15; trend against reversal 0.5 (usually vetoed); event 0.7.
- window: London 1.0; New York 1.05 (larger participation).
- Monday: 1.05 (weekly open flows); Friday New York: 0.9.
- recent_form: 3 losses 0.8; 5 wins 0.9.
- event_2h: 0.8.

### 4.5 In-trade checks
- `below_asia_low`: 15m close below the Asia low, counts as 2.
- `no_higher_low`: 2 closed candles without a higher low above the raid wick.
- `oi_bleeding`: OI down >= 1 percent since entry.
- `delta_negative`: taker delta negative on the last 2 candles.
exit_threshold = 2.

### 4.6 Thesis template
"M5 long {coin} after {window} raid of Asia low {asia_low}. Asia range {asia_low} to {asia_high} ({range_atr} ATR), opened {open_location}. Raid {depth} ATR, {fuel_notional} liquidated. Daily bias {daily_bias}. Strongest: {top3}. Wrong if 15m closes below {asia_low}. Expect {asia_mid} within 90 min, then {asia_high}."

## 5. Claude Code prompt (paste verbatim)
```
Implement strategies/m5_session_liquidity_run.py as class M5SessionLiquidityRun in the existing strategy engine using structure/ and mind/. Tag model M5. Paper mode, BTC and ETH.

At 07:00 UTC freeze the Asia range (high and low from 00:00 to 07:00 UTC) per coin. Windows are 07:00 to 09:00 UTC (London) and 13:00 to 15:00 UTC (New York). Sequence for longs: Asia range height between 0.8 and 4.0 ATR(15m); the session opened within the Asia range or within 0.2 ATR of an edge; a 15m wick below the Asia low by 0.1 to 0.6 ATR during the window; within 3 candles a 15m close back above the Asia low with reclaim_quality at least 0.5, confirmed either by falling on a later candle than the raid wick or, when raid and reclaim share a candle, by one additional 15m candle closing above the Asia low with its low not below the raid wick low (spec v1.3, D-89). Record long liquidation notional during the raid, OI change percent during the raid, taker buy ratio on the reclaim candle, minutes into the window at the raid, price move over the 3 hours before the window toward the raided edge, and cohort fresh adds in the trade direction in the last 60 minutes. Build the Snapshot and call the M5 Mind. Mirror for shorts after a raid of the Asia high. One attempt per window per coin.

M5 Mind reasons and weights: bias_alignment 1.0 when daily_bias agrees with the reversal direction, 0.6 neutral, 0.2 against, weight 2.0; range_quality 1.0 when the Asia range is 1.2 to 2.5 ATR with at most 2 wicks outside the range by more than 0.1 ATR during Asia, 0.6 when 0.8 to 1.2 or 2.5 to 4.0 ATR, else 0, weight 1.5; reclaim clip((reclaim_quality minus 0.4) over 0.4, 0, 1) times 0.7 when the reclaim took 3 candles, weight 1.8; fuel clip(liquidation notional on the raided side during the raid over 0.8 times liq_5m_p90 of the raided side (calibrated, spec v1.1 Part C; was 0.08 percent of OI), 0, 1) weight 1.3; cleared clip(negative OI change percent during the raid over 0.8, 0, 1) weight 1.0; delta_flip clip((taker buy ratio on the reclaim minus 0.5) over 0.25, 0, 1) weight 1.3; early_in_window 1.0 when the raid occurs within 45 minutes of the window open, 0.6 within 90, else 0.3, weight 1.0; open_location 1.0 when the session opened in the middle 60 percent of the range, 0.7 inside near the raided edge, 0.3 within 0.2 ATR outside the raided edge, weight 1.0; no_pre_drift clip(1 minus the 3-hour pre-window move toward the raided edge over 1.5 ATR, 0, 1) weight 1.2; cohort clip(cohort fresh adds in the trade direction in 60 minutes over 2, 0, 1) weight 0.8.

Vetoes: opened_outside (session opened more than 0.2 ATR outside the Asia range), too_deep (raid depth above 0.6 ATR), range_bad (Asia range below 0.8 or above 4.0 ATR), already_taken (an M5 trade already taken this window for this coin), event_30m, trend_day_with_raid (day_type trend_down and the raid is of the Asia low, mirror for highs), late_in_window (reclaim completes after the window closes), funding_extreme_same_side.

Context multipliers: day_type range 1.1, squeeze against the raid direction 1.15, trend against the reversal 0.5, event 0.7; window London 1.0, New York 1.05; Monday 1.05, Friday New York 0.9; three consecutive M5 losses 0.8, five wins 0.9; event within 2 hours 0.8.

Execution: post-only at min(50 percent of the reclaim candle, reclaim close) minus 0.05 ATR(15m) valid 3 candles from confirmation (spec v1.3, D-89). Post-only orders that would cross the touch are rejected and re-quoted once one tick inside it (spec v1.3, D-87); the re-quote fills only when a later print goes through it, else it is cancelled at the end of the validity. Stop at raid wick low minus 0.15 ATR. Minimum stop distance (spec v1.3, D-88): if the stop is closer than 0.5 ATR(15m) to entry it is moved out to exactly 0.5 ATR(15m) beyond entry; size and R use the final stop, targets stay as computed from the structural stop, `stop_floor_applied` is recorded on the trade row. T1 at the Asia range mid, close 40 percent, stop to breakeven. T2 at the Asia range high; when day_type is trend_up after T1 trail behind 15m higher lows instead of closing at T2. expected_hold_min 90; hard time stop at window end plus 120 minutes. Size via RiskEngine using size_tier.

In-trade checks: below_asia_low (15m close below the Asia low, counts as 2), no_higher_low (2 closed candles without a higher low above the raid wick), oi_bleeding (OI down at least 1 percent since entry), delta_negative (taker delta negative on the last 2 candles). exit_threshold 2.

Thesis template: "M5 long {coin} after {window} raid of Asia low {asia_low}. Asia range {asia_low} to {asia_high} ({range_atr} ATR), opened {open_location}. Raid {depth} ATR, {fuel_notional} liquidated. Daily bias {daily_bias}. Strongest: {top3}. Wrong if 15m closes below {asia_low}. Expect {asia_mid} within 90 min, then {asia_high}."

Log every evaluation with all Mind fields, alert at 07:00 UTC with the frozen Asia range per coin, alert on raid detected, on take with thesis, on skip with vetoes, on exit with exit_reason and R. Unit tests: a valid London raid and reclaim, an opened_outside veto, a range_bad veto, an in-trade below_asia_low exit.
```
