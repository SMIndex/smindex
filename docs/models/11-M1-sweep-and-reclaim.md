# 11. M1 Sweep and Reclaim

Depends on doc 10 (structure engine, Mind framework). Runs in paper mode from day one, tag `M1`.

## 1. Overview
Price runs the stops beyond a pre-marked higher-timeframe level (liquidity pool, 4h zone edge, liquidation cluster), the forced flow exhausts, and price closes back inside. Enter in the direction of the reclaim, stop beyond the sweep wick, target the next liquidity in the other direction. Long version described; short is the mirror.

Type: liquidity reversal. Day types allowed: range, trend in the same direction as the trade, squeeze if the sweep is against the squeeze direction. Expected hold: 90 minutes. Frequency: 1 to 3 per day across BTC and ETH.

## 2. Eligible levels (must exist before price arrives)
- Prior day low, prior week low, Asia low, London low (for longs).
- Equal lows on 1h or 4h.
- Bottom edge of a fresh or tested 4h demand zone (OB or FVG).
- A long liquidation cluster with notional >= band_p80 (calibrated, spec v1.1 Part C; was 0.15 percent of coin OI).
Levels are frozen at the start of each 15m candle; a level created by the current candle does not count.

## 3. Sequence (long)
1. Alignment: daily_bias up or neutral; price in discount of the 4h range (relax to discount of the 1h range if the 4h range is wider than 6 ATR).
2. Sweep: a 15m wick below the level by 0.1 to 0.5 ATR(15m).
3. Reclaim: within 3 candles a 15m close back above the level, reclaim_quality >= 0.5. Confirmation (spec v1.3, D-89): either (a) the reclaim close is on a later candle than the sweep wick, or (b) when sweep and reclaim are the same candle, one additional 15m candle closes above the level with its low not below the sweep wick low. Entry is placed only after that confirmation; `reclaim_candles` and `confirmation_used` are recorded on the signal row.
4. Data on the sweep candle(s): long-side liquidation fills spiked; OI fell; CVD made a low that price did not confirm on the next push.
5. Data on the reclaim candle: taker delta positive; OI flat or rising.
6. Mind evaluates. If take: place entry.

## 4. Execution
- Entry (spec v1.3, D-89): post-only at min(50 percent of the reclaim candle, reclaim candle close) minus 0.05 ATR(15m) — mirror for shorts — so the order always rests below the market; valid 3 candles from confirmation. If missed and a 15m FVG was left by the reclaim, place at the FVG mid for 3 more candles. Then skip. Post-only orders that would cross the touch are rejected and re-quoted once one tick inside it (spec v1.3, D-87); the re-quote fills only when a later print goes through it, else it is cancelled at the end of the validity.
- Stop: sweep wick low minus 0.15 ATR. Minimum stop distance (spec v1.3, D-88): if the stop is closer than 0.5 ATR(15m) to entry it is moved out to exactly 0.5 ATR(15m) beyond entry; size and R use the final stop, targets stay as computed from the structural stop, `stop_floor_applied` is recorded on the trade row.
- T1: nearest pool above or 1.5R, whichever first, close 40 percent, stop to breakeven.
- T2: next 4h supply zone bottom or top of the 4h range. Trail remaining behind each new 15m higher low after T2 is within 0.5 ATR.
- Time stop: 135 minutes (1.5 x expected hold) via the universal dead-trade check; hard stop at 4 hours.

## 5. Heuristic Mind for M1

### 5.1 Reasoning behind the weights
The two things that separate a real sweep from a breakdown are the quality of the location and the quality of the reclaim, so they carry the highest weights. Fuel (liquidations) and clearance (OI drop) prove the move was forced, second tier. Delta flip and absorption show buyers actually appeared, second tier. Discount position and session are environmental, lower weight. Cohort adding is rare but strong when present. A cluster sitting just beyond the level is positive when it was cleared by the sweep and a warning when it was not, so it is split between a reason and a veto.

### 5.2 Reasons
| key | description | detector (0 to 1) | weight |
|---|---|---|---|
| location | level quality | 1.0 prior week low or 4h zone edge or equal lows on 4h; 0.8 prior day low or equal lows 1h; 0.6 Asia or London low; 0.7 liquidation cluster; plus 0.2 if two level types coincide within 0.2 ATR, capped at 1 | 2.0 |
| reclaim | reclaim candle quality | clip((reclaim_quality - 0.4) / 0.4, 0, 1); times 0.7 if reclaim took 3 candles instead of 1 | 2.0 |
| fuel | forced selling on the sweep | clip(long_liq_notional_5m / liq_5m_p90_long, 0, 1) — liq_5m_p90 of the swept side, calibrated (spec v1.1 Part C; was 0.10 percent of coin OI) | 1.5 |
| cleared | positions removed | clip(-OI_change_during_sweep_pct / 1.0, 0, 1) | 1.2 |
| delta_flip | buyers on the reclaim | clip(taker_buy_ratio_reclaim_candle - 0.5, 0, 0.25) / 0.25 | 1.5 |
| absorption | CVD divergence at the low | 1.0 if CVD low was lower than prior CVD low while price low was not lower than 0.2 ATR beyond; 0.5 if CVD flat; 0 otherwise | 1.0 |
| discount | position in 4h range | clip((0.5 - range_4h.pct) / 0.5, 0, 1) | 1.2 |
| session | participants present | 1.0 first 120 minutes of London or New York; 0.6 rest of London or New York; 0.3 Asia; 0.1 dead | 0.8 |
| cohort | smart wallets adding | clip(cohort_fresh_adds_same_direction_60m / 3, 0, 1) | 1.0 |
| htf_bias | daily and 4h agree | 1.0 daily up and trend_4h up or range; 0.6 daily neutral; 0.3 daily down with trend_4h range | 1.0 |

### 5.3 Vetoes
- `too_deep`: sweep depth > 0.5 ATR. It is a breakdown.
- `third_sweep`: this level has been swept 2 or more times today already.
- `second_close_below`: a second 15m close below the level occurred after the reclaim, before entry filled.
- `trend_against`: day_type is trend_down (for longs).
- `event_30m`: macro event within 30 minutes either side.
- `cluster_below_uncleared`: a long liquidation cluster with notional >= 50 percent of band_p80 (calibrated reference, spec v1.1 Part C; was the swept cluster) sits within 1 ATR below the sweep wick and was not reduced by the sweep.
- `funding_extreme_same_side`: crowding EXTREME on the long side (longs are crowded; a bounce will be sold).

### 5.4 Context multipliers
- day_type: range 1.1; trend same direction 1.0; squeeze against sweep direction 1.1; event 0.7; no_trade 0.6.
- session: as the session reason but applied as multiplier 1.1 / 1.0 / 0.8 / 0.7.
- sweep_count_today of this level: 0 → 1.0; 1 → 0.8.
- recent_form: 3 consecutive M1 losses 0.8; 5 consecutive wins 0.9.
- event_2h: 0.8.

### 5.5 In-trade checks (thesis failing when True)
- `no_higher_low`: two closed 15m candles since entry without a higher low above the sweep wick.
- `oi_bleeding`: OI down >= 1.0 percent since entry.
- `delta_negative`: taker delta negative on the last 2 candles.
- `level_lost`: a 15m close back below the level.
exit_threshold = 2 (level_lost alone counts as 2).

### 5.6 Thesis template
"M1 long {coin} at {level_type} {level}. Sweep {depth} ATR with {fuel_notional} liquidated and OI {oi_change}. Reclaim quality {rq}. Strongest: {top3}. Wrong if 15m closes below {level} again or price breaks {stop}. Expect {t1} within 90 min."

## 6. Logging and alerts
Log every evaluation including skipped ones with vetoes and reasons. Alert on: sweep detected (pre-alert), decision take with thesis, decision skip with vetoes, exit with exit_reason and R.

## 7. Claude Code prompt (paste verbatim)
```
Implement strategies/m1_sweep_reclaim.py as class M1SweepReclaim in the existing strategy engine, using the structure/ and mind/ packages from the shared layer. Tag all signals and trades with model M1. Run in paper mode for BTC and ETH.

Eligible levels, frozen at each 15m candle open: prior day low, prior week low, Asia low, London low, equal lows on 1h or 4h, the bottom edge of a fresh or tested 4h demand order block or FVG, and any long liquidation cluster holding at least band_p80 (calibrated, spec v1.1 Part C; was 0.15 percent of coin OI). Mirror for shorts.

Sequence for longs: daily_bias up or neutral; price in discount of the 4h range, or of the 1h range when the 4h range exceeds 6 ATR; a 15m wick below an eligible level by 0.1 to 0.5 ATR(15m); within 3 candles a 15m close back above the level with reclaim_quality at least 0.5; the reclaim is confirmed either by falling on a later candle than the sweep wick, or, when sweep and reclaim share a candle, by one additional 15m candle closing above the level with its low not below the sweep wick low (spec v1.3, D-89). Build the Snapshot and call the M1 Mind only after the confirmation.

M1 Mind reasons with detectors and weights: location (1.0 for prior week low, 4h zone edge or 4h equal lows; 0.8 prior day low or 1h equal lows; 0.6 Asia or London low; 0.7 liquidation cluster; add 0.2 when two level types coincide within 0.2 ATR; cap 1.0) weight 2.0; reclaim clip((reclaim_quality minus 0.4) over 0.4, 0, 1) times 0.7 if the reclaim took 3 candles, weight 2.0; fuel clip(long liquidation notional in the sweep window over 0.10 percent of coin OI, 0, 1) weight 1.5; cleared clip(negative OI change percent during the sweep over 1.0, 0, 1) weight 1.2; delta_flip clip(taker buy ratio on the reclaim candle minus 0.5, 0, 0.25) over 0.25 weight 1.5; absorption 1.0 when CVD printed a lower low while price did not exceed 0.2 ATR beyond the prior low, 0.5 when CVD flat, else 0, weight 1.0; discount clip((0.5 minus range_4h.pct) over 0.5, 0, 1) weight 1.2; session 1.0 in the first 120 minutes of London or New York, 0.6 rest of those sessions, 0.3 Asia, 0.1 dead, weight 0.8; cohort clip(cohort fresh adds in the trade direction in 60 minutes over 3, 0, 1) weight 1.0; htf_bias 1.0 when daily up and trend_4h up or range, 0.6 daily neutral, 0.3 daily down with trend_4h range, weight 1.0.

Vetoes: too_deep (sweep depth above 0.5 ATR), third_sweep (level swept 2 or more times today), second_close_below (a second 15m close below the level after the reclaim before fill), trend_against (day_type trend_down for longs), event_30m, cluster_below_uncleared (long liquidation cluster at least 50 percent of the swept cluster within 1 ATR below the wick that was not reduced by the sweep), funding_extreme_same_side.

Context multipliers: day_type range 1.1, trend same direction 1.0, squeeze against the sweep direction 1.1, event 0.7, no_trade 0.6; session 1.1, 1.0, 0.8, 0.7 for London or New York first 120 minutes, rest of those sessions, Asia, dead; level sweep count today 0 gives 1.0 and 1 gives 0.8; three consecutive M1 losses 0.8, five consecutive wins 0.9; event within 2 hours 0.8.

Execution: post-only entry at min(50 percent of the reclaim candle, reclaim close) minus 0.05 ATR(15m) valid 3 candles from confirmation (spec v1.3, D-89), then at the mid of any 15m FVG left by the reclaim for 3 more candles, then skip. Post-only orders that would cross the touch are rejected and re-quoted once one tick inside it (spec v1.3, D-87); the re-quote fills only when a later print goes through it, else it is cancelled at the end of the validity. Stop at sweep wick low minus 0.15 ATR. Minimum stop distance (spec v1.3, D-88): if the stop is closer than 0.5 ATR(15m) to entry it is moved out to exactly 0.5 ATR(15m) beyond entry; size and R use the final stop, targets stay as computed from the structural stop, `stop_floor_applied` is recorded on the trade row. T1 at the nearest pool above or 1.5R whichever first, close 40 percent, move stop to breakeven. T2 at the next 4h supply zone bottom or the top of the 4h range; trail the remainder behind each new 15m higher low once within 0.5 ATR of T2. expected_hold_min 90; hard time stop 240 minutes. Size via RiskEngine using the Mind size_tier.

In-trade checks: no_higher_low (2 closed candles since entry without a higher low above the sweep wick), oi_bleeding (OI down at least 1.0 percent since entry), delta_negative (taker delta negative on the last 2 candles), level_lost (a 15m close back below the level, counts as 2 failures). exit_threshold 2.

Thesis template: "M1 long {coin} at {level_type} {level}. Sweep {depth} ATR with {fuel_notional} liquidated and OI {oi_change}. Reclaim quality {rq}. Strongest: {top3}. Wrong if 15m closes below {level} again or price breaks {stop}. Expect {t1} within 90 min." Mirror wording for shorts.

Log every evaluation to the signals table with all Mind fields, send Telegram pre-alert on sweep detection, alert on take with the thesis, alert on skip with vetoes, and alert on exit with exit_reason and R. Add unit tests with synthetic candle sequences covering a valid sweep and reclaim, a too_deep veto, a third_sweep veto, and an in-trade level_lost exit.
```
