# 13. M3 Failed Auction at a Range Extreme

Depends on doc 10. Paper mode, tag `M3`.

## 1. Overview
Price probes above a range high, a 4h supply zone or equal highs, prints a swing failure (higher high that closes back below the prior high) while late longs pile in, and rolls over. Short the retest of the failed high, stop above it, target VWAP or range mid, then the fresh long liquidation cluster below. Short version described; long is the mirror at range lows.

Type: range reversal. Day types allowed: range; trend only when price has reached a 4h supply zone and participation is fading (second failure). Expected hold: 120 minutes. Frequency: 0 to 2 per day.

## 2. Sequence (short)
1. Alignment: day_type range, or trend_up with price inside a 4h supply zone. Price in premium of the 4h range.
2. Level: range high (4h swing high), 4h supply OB or FVG, equal highs on 1h or 4h, prior day high, or session high. Level must predate the current session.
3. Push: price trades above the level. OI rising over the push (>= 0.5 percent over 1h). Funding ticking up.
4. Swing failure: a 15m candle makes a higher high than the prior swing high and closes below that prior high. CVD at the new high is lower than CVD at the prior high (divergence).
5. Data on the failure candle: a fresh long liquidation cluster formed within 1.5 ATR below price; bid depth within 0.3 percent below 70 percent of its 1h average.
6. Retest: price returns to within 0.2 ATR of the failed high without closing above it.
7. Mind evaluates.

## 3. Execution
- Entry: post-only at failed high minus 0.1 ATR, valid 3 candles. Post-only orders that would cross the touch are rejected and re-quoted once one tick inside it (spec v1.3, D-87); the re-quote fills only when a later print goes through it, else it is cancelled at the end of the validity.
- Stop: swing failure high plus 0.15 ATR. Minimum 0.5 ATR(15m) stop distance (spec v1.3, D-88).
- T1: session VWAP or range mid, whichever is nearer, close 50 percent, stop to breakeven.
- T2: the long liquidation cluster below; if none, the range low.
- Time stop: dead-trade at 180 minutes; hard stop 6 hours.

## 4. Heuristic Mind for M3

### 4.1 Reasoning behind the weights
Fading is where retail loses most, so this Mind is the strictest. The trap itself (OI rising into the high, then failure) is the core evidence and gets top weight. CVD divergence is the best exhaustion measure available, top weight. Level quality matters as much as in M1. Thin bids and a nearby long cluster describe the fuel for the drop, second tier. Funding and cohort are environmental but the cohort adding longs is a veto rather than a reason, because the smart wallets are usually right on the higher timeframe. A second failure at the same level is strong and gets its own reason. Squeeze conditions are a veto: never short into crowded shorts.

### 4.2 Reasons
| key | description | detector | weight |
|---|---|---|---|
| trap | late longs positioned | clip(OI_change_1h_into_high_pct / 1.5, 0, 1) | 2.0 |
| cvd_divergence | weaker buying on the new high | clip((CVD_prior_high - CVD_new_high) / (0.5 x CVD_1h_range), 0, 1) | 2.0 |
| location | level quality | 1.0 4h supply zone or 4h equal highs or prior week high; 0.8 prior day high or 1h equal highs; 0.6 session high; +0.2 for coincident level types, cap 1 | 1.8 |
| failure_quality | close well below prior high | clip((prior_high - close_failure) / (0.5 ATR), 0, 1) | 1.2 |
| thin_bids | little passive support | clip((1 - bid_depth_ratio_vs_1h_avg) / 0.5, 0, 1) | 1.0 |
| cluster_fuel | long liquidation cluster below | clip(long_cluster_notional_within_1.5ATR / band_p80, 0, 1) — calibrated (spec v1.1 Part C; was 0.10 percent of OI) | 1.2 |
| funding_up | crowd paying to be long | clip(funding_z / 2.0, 0, 1) | 0.8 |
| premium | position in 4h range | clip((range_4h.pct - 0.5) / 0.5, 0, 1) | 1.0 |
| second_failure | repeat failure at the same level today | 1.0 if this is the second swing failure at this level today; 0 otherwise | 1.5 |
| day_type_fit | range day | 1.0 range; 0.5 trend_up inside a 4h supply zone; 0 otherwise | 1.0 |

### 4.3 Vetoes
- `acceptance`: two consecutive 15m closes above the level with OI rising (it is a breakout, hand to M2).
- `squeeze_risk`: funding z <= -1.5 or short OI share >= 60 percent (crowded shorts).
- `cohort_adding_longs`: cohort fresh long adds >= 2 in the last 60 minutes.
- `trend_day_early`: day_type trend_up and current time before 15:00 UTC (the auction has not finished).
- `event_30m`.
- `first_failure_random_level`: the level is an intraday swing that did not exist before this session.
- `funding_extreme_short_side`: crowding EXTREME on the short side.

### 4.4 Context multipliers
- day_type: range 1.15; trend_up in supply zone 0.85; squeeze 0.5; event 0.7; no_trade 0.7.
- session: New York 1.1; London 1.0; Asia 0.8; dead 0.7.
- level failure count today: 1 → 1.0; 2 → 1.1; 3+ → 0.8.
- recent_form: 3 losses 0.8; 5 wins 0.9.
- event_2h: 0.8.

### 4.5 In-trade checks
- `re_approach_with_oi`: price within 0.2 ATR of the failed high with OI rising (acceptance forming), counts as 2.
- `cvd_recovering`: CVD higher than at entry for 2 candles.
- `no_progress`: after 4 closed candles price has not traded below the failure candle low.
- `close_above_level`: a 15m close above the level, counts as 2.
exit_threshold = 2.

### 4.6 Thesis template
"M3 short {coin} at failed high {level} ({level_type}). Push added {oi_change} OI with CVD divergence {div}. Long cluster {cluster_notional} at {cluster_level}. Strongest: {top3}. Wrong if 15m closes above {level}. Expect {t1} within 2h, then {cluster_level}."

## 5. Claude Code prompt (paste verbatim)
```
Implement strategies/m3_failed_auction.py as class M3FailedAuction in the existing strategy engine using structure/ and mind/. Tag model M3. Paper mode, BTC and ETH.

Sequence for shorts: day_type range, or trend_up with price inside a 4h supply zone; price in premium of the 4h range; an eligible level that predates the current session (4h swing high, 4h supply OB or FVG, equal highs on 1h or 4h, prior day high, session high); price trades above the level with OI rising at least 0.5 percent over the last hour; a 15m candle prints a higher high than the prior swing high and closes below that prior high, with CVD at the new high lower than CVD at the prior high; then price returns to within 0.2 ATR of the failed high without closing above it. Build the Snapshot and call the M3 Mind. Mirror for longs at range lows.

M3 Mind reasons and weights: trap clip(OI change percent over the hour into the high over 1.5, 0, 1) weight 2.0; cvd_divergence clip((CVD at prior high minus CVD at new high) over 0.5 times the 1h CVD range, 0, 1) weight 2.0; location 1.0 for 4h supply zone, 4h equal highs or prior week high, 0.8 prior day high or 1h equal highs, 0.6 session high, add 0.2 when level types coincide, cap 1.0, weight 1.8; failure_quality clip((prior high minus failure candle close) over 0.5 ATR, 0, 1) weight 1.2; thin_bids clip((1 minus bid depth within 0.3 percent as a ratio of its 1h average) over 0.5, 0, 1) weight 1.0; cluster_fuel clip(long liquidation cluster notional within 1.5 ATR below over band_p80 (calibrated, spec v1.1 Part C; was 0.10 percent of OI), 0, 1) weight 1.2; funding_up clip(funding z over 2.0, 0, 1) weight 0.8; premium clip((range_4h.pct minus 0.5) over 0.5, 0, 1) weight 1.0; second_failure 1.0 when this is the second swing failure at this level today else 0, weight 1.5; day_type_fit 1.0 range, 0.5 trend_up inside a 4h supply zone, else 0, weight 1.0.

Vetoes: acceptance (two consecutive 15m closes above the level with OI rising), squeeze_risk (funding z at most negative 1.5 or short OI share at least 60 percent), cohort_adding_longs (at least 2 cohort fresh long adds in 60 minutes), trend_day_early (day_type trend_up before 15:00 UTC), event_30m, first_failure_random_level (level created during the current session), funding_extreme_short_side.

Context multipliers: day_type range 1.15, trend_up inside supply 0.85, squeeze 0.5, event 0.7, no_trade 0.7; session New York 1.1, London 1.0, Asia 0.8, dead 0.7; failures at this level today 1 gives 1.0, 2 gives 1.1, 3 or more gives 0.8; three consecutive M3 losses 0.8, five wins 0.9; event within 2 hours 0.8.

Execution: post-only at failed high minus 0.1 ATR valid 3 candles. Stop at swing failure high plus 0.15 ATR. Minimum stop distance (spec v1.3, D-88): if the stop is closer than 0.5 ATR(15m) to entry it is moved out to exactly 0.5 ATR(15m) beyond entry; size and R use the final stop, targets stay as computed from the structural stop, `stop_floor_applied` is recorded on the trade row. T1 at session VWAP or range mid whichever nearer, close 50 percent, stop to breakeven. T2 at the long liquidation cluster below, or the range low if none. expected_hold_min 120; hard time stop 360 minutes. Size via RiskEngine using size_tier.

In-trade checks: re_approach_with_oi (price within 0.2 ATR of the failed high with OI rising, counts as 2), cvd_recovering (CVD above entry level for 2 candles), no_progress (no trade below the failure candle low after 4 closed candles), close_above_level (counts as 2). exit_threshold 2.

Thesis template: "M3 short {coin} at failed high {level} ({level_type}). Push added {oi_change} OI with CVD divergence {div}. Long cluster {cluster_notional} at {cluster_level}. Strongest: {top3}. Wrong if 15m closes above {level}. Expect {t1} within 2h, then {cluster_level}."

Log every evaluation with all Mind fields, alert on swing failure detected, on take with thesis, on skip with vetoes, on exit with exit_reason and R. Unit tests: a valid failure and retest, an acceptance veto, a squeeze_risk veto, an in-trade close_above_level exit.
```
