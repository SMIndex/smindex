# 16. M6 Weekly Open Reclaim

Depends on doc 10. Paper mode, tag `M6`.

## 1. Overview
Price loses the weekly open (Monday 00:00 UTC) in the first part of the week, then reclaims it with rising OI and a 15m close back above. Long above the weekly open, stop below the reclaim low, target the prior week high. Mirror: price rallies above the weekly open early, then loses it with rising OI, short toward the prior week low. Long version described.

Type: swing level reclaim. Day types allowed: any except event inside 30 minutes. Expected hold: 24 hours. Frequency: at most one per week per coin per direction.

## 2. Sequence (long)
1. Weekly open frozen at Monday 00:00 UTC. Prior week high (PWH) and low (PWL) frozen at the same time.
2. Loss: at least one 4h close below the weekly open between Monday 00:00 and Wednesday 23:59 UTC, with the low of that excursion at least 0.8 ATR(4h) below the weekly open.
3. Reclaim: a 15m close back above the weekly open, followed by a 1h close above it, between Monday 12:00 and Thursday 23:59 UTC.
4. Data on the reclaim: OI up >= 1.0 percent over the 4 hours into the reclaim; taker buy ratio >= 0.55 over the same window; funding z <= 1.0 (crowd not yet long).
5. Cohort: cohort net long change over 24h >= 0.
6. Mind evaluates.

## 3. Execution
- Entry: post-only at the weekly open plus 0.1 ATR(15m) on the first pullback after the 1h close above; if no pullback within 4 hours, enter at the 1h close plus 0.1 ATR. Valid 8 hours. Post-only orders that would cross the touch are rejected and re-quoted once one tick inside it (spec v1.3, D-87); the re-quote fills only when a later print goes through it, else it is cancelled at the end of the validity.
- Stop: reclaim low (lowest low of the 4 hours before the reclaim) minus 0.2 ATR(1h). Skip if stop distance > 2.5 ATR(1h). Minimum 0.5 ATR(1h) stop distance (spec v1.3, D-88).
- T1: 50 percent of the distance to PWH, or 1.5R, whichever first, close 40 percent, stop to breakeven.
- T2: PWH. Trail behind each new 4h higher low after T1.
- Time stop: dead-trade at 36 hours; hard stop Friday 20:00 UTC.
- One attempt per week per coin per direction.

## 4. Heuristic Mind for M6

### 4.1 Reasoning behind the weights
The reclaim is meaningful only if the loss was real (deep enough to trap sellers and late longs from last week) and the reclaim has fresh commitment (OI rising, delta positive). Those three are the top tier. Funding not yet elevated and cohort agreement are the truth-checks that the reclaim is early rather than crowded. Daily bias against is a veto, because a weekly open reclaim inside a strong daily downtrend is a bear-market rally. Distance to PWH defines reward and enters as a reason. Time in week matters: Tuesday and Wednesday reclaims are the classic pattern; Thursday reclaims have less time to reach target.

### 4.2 Reasons
| key | description | detector | weight |
|---|---|---|---|
| loss_depth | trap size below the weekly open | clip((excursion_depth_atr4h - 0.8) / 1.2, 0, 1) | 1.8 |
| oi_commitment | fresh positioning into the reclaim | clip(OI_change_4h_pct / 2.0, 0, 1) | 2.0 |
| delta_reclaim | aggressive buyers | clip((taker_buy_ratio_4h - 0.5) / 0.15, 0, 1) | 1.5 |
| funding_room | crowd not yet long | clip((1.0 - funding_z) / 1.5, 0, 1) | 1.2 |
| cohort | cohort adding or holding longs | clip(cohort_net_long_change_24h / 0.2, 0, 1) x 0.5 + 0.5 if >= 0 else 0 | 1.8 |
| reward | distance to PWH in R | clip((distance_to_PWH_over_stop_distance - 1.5) / 2.5, 0, 1) | 1.2 |
| timing | day of week | 1.0 Tuesday or Wednesday; 0.7 Monday after 12:00; 0.5 Thursday | 1.0 |
| htf_bias | daily agrees | 1.0 daily up; 0.6 neutral; 0.2 down (vetoed if strongly down) | 1.5 |
| reclaim_quality | 15m and 1h closes decisive | clip((close_1h - weekly_open) / (0.5 ATR_1h), 0, 1) | 1.0 |
| pwh_untested | PWH not yet touched this week | 1.0 if untested; 0.5 if tested once | 0.8 |

### 4.3 Vetoes
- `daily_strong_down`: daily_bias down and price below the daily 20-candle midpoint.
- `shallow_loss`: excursion depth < 0.8 ATR(4h) (no trap).
- `already_taken`: an M6 long already taken this week for this coin.
- `too_late`: reclaim after Thursday 23:59 UTC.
- `oi_falling`: OI change over 4h into the reclaim <= 0 (short covering pop).
- `event_30m`.
- `stop_too_wide`: > 2.5 ATR(1h).
- `funding_extreme_long_side`.

### 4.4 Context multipliers
- day_type at entry: trend_up 1.1; range 1.0; squeeze up 1.05; trend_down 0.7; event 0.7.
- week context: prior week closed above its open 1.05 (continuation week); prior week was a large down week (> 6 percent) 0.9.
- recent_form: 2 consecutive M6 losses 0.85.
- event_2h: 0.85.

### 4.5 In-trade checks
- `lost_weekly_open`: a 1h close back below the weekly open, counts as 2.
- `oi_dropping`: OI down >= 2 percent since entry.
- `cohort_flip`: cohort net long change since entry <= -0.15.
- `no_progress`: after 12 hours price has not made a higher high than the reclaim high.
exit_threshold = 2.

### 4.6 Thesis template
"M6 long {coin} on weekly open reclaim at {weekly_open} ({weekday}). Lost by {depth} ATR earlier this week. Reclaim with OI {oi_change} and buy ratio {br}, funding z {fz}, cohort {cohort_change}. Strongest: {top3}. Wrong if 1h closes below {weekly_open}. Expect {t1} within 24h, then PWH {pwh}."

## 5. Claude Code prompt (paste verbatim)
```
Implement strategies/m6_weekly_open_reclaim.py as class M6WeeklyOpenReclaim in the existing strategy engine using structure/ and mind/. Tag model M6. Paper mode, BTC and ETH. Evaluate on each closed 15m candle.

At Monday 00:00 UTC freeze per coin the weekly open, prior week high and prior week low. Sequence for longs: at least one 4h close below the weekly open between Monday 00:00 and Wednesday 23:59 UTC with the excursion low at least 0.8 ATR(4h) below the weekly open; then a 15m close back above the weekly open followed by a 1h close above it, between Monday 12:00 and Thursday 23:59 UTC; OI up at least 1.0 percent over the 4 hours into the reclaim; taker buy ratio at least 0.55 over that window; funding z at most 1.0; cohort net long change over 24 hours at least 0. Build the Snapshot and call the M6 Mind. Mirror for shorts when price rallies above the weekly open early in the week and then loses it. One attempt per week per coin per direction.

M6 Mind reasons and weights: loss_depth clip((excursion depth in ATR(4h) minus 0.8) over 1.2, 0, 1) weight 1.8; oi_commitment clip(OI change percent over 4h into the reclaim over 2.0, 0, 1) weight 2.0; delta_reclaim clip((taker buy ratio over 4h minus 0.5) over 0.15, 0, 1) weight 1.5; funding_room clip((1.0 minus funding z) over 1.5, 0, 1) weight 1.2; cohort 0.5 plus 0.5 times clip(cohort net long change over 24h over 0.2, 0, 1) when the change is at least 0, else 0, weight 1.8; reward clip((distance to PWH divided by stop distance minus 1.5) over 2.5, 0, 1) weight 1.2; timing 1.0 Tuesday or Wednesday, 0.7 Monday after 12:00 UTC, 0.5 Thursday, weight 1.0; htf_bias 1.0 daily up, 0.6 neutral, 0.2 down, weight 1.5; reclaim_quality clip((1h close minus weekly open) over 0.5 ATR(1h), 0, 1) weight 1.0; pwh_untested 1.0 when PWH untouched this week, 0.5 when touched once, weight 0.8.

Vetoes: daily_strong_down (daily_bias down and price below the midpoint of the last 20 daily candles), shallow_loss (excursion depth below 0.8 ATR(4h)), already_taken, too_late (reclaim after Thursday 23:59 UTC), oi_falling (OI change over 4h into the reclaim at most 0), event_30m, stop_too_wide (above 2.5 ATR(1h)), funding_extreme_long_side.

Context multipliers: day_type at entry trend_up 1.1, range 1.0, squeeze up 1.05, trend_down 0.7, event 0.7; prior week closed above its open 1.05; prior week down more than 6 percent 0.9; two consecutive M6 losses 0.85; event within 2 hours 0.85.

Execution: post-only at weekly open plus 0.1 ATR(15m) on the first pullback after the 1h close above; if no pullback within 4 hours, place at the 1h close plus 0.1 ATR. Valid 8 hours. Stop at the lowest low of the 4 hours before the reclaim minus 0.2 ATR(1h), skip if above 2.5 ATR(1h) from entry. Minimum stop distance (spec v1.3, D-88): if the stop is closer than 0.5 ATR(1h) to entry it is moved out to exactly 0.5 ATR(1h) beyond entry; size and R use the final stop, targets stay as computed from the structural stop, `stop_floor_applied` is recorded on the trade row. T1 at 50 percent of the distance to PWH or 1.5R whichever first, close 40 percent, stop to breakeven. T2 at PWH, trailing behind each new 4h higher low after T1. expected_hold_min 1440; hard time stop Friday 20:00 UTC. Size via RiskEngine using size_tier.

In-trade checks: lost_weekly_open (1h close below the weekly open, counts as 2), oi_dropping (OI down at least 2 percent since entry), cohort_flip (cohort net long change since entry at most negative 0.15), no_progress (no higher high than the reclaim high after 12 hours). exit_threshold 2.

Thesis template: "M6 long {coin} on weekly open reclaim at {weekly_open} ({weekday}). Lost by {depth} ATR earlier this week. Reclaim with OI {oi_change} and buy ratio {br}, funding z {fz}, cohort {cohort_change}. Strongest: {top3}. Wrong if 1h closes below {weekly_open}. Expect {t1} within 24h, then PWH {pwh}."

Log every evaluation with all Mind fields, alert at Monday 00:00 UTC with the frozen weekly levels, alert when the weekly open is lost, on take with thesis, on skip with vetoes, on exit with exit_reason and R. Unit tests: a valid loss and reclaim, a shallow_loss veto, an oi_falling veto, an in-trade lost_weekly_open exit.
```
