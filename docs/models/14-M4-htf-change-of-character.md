# 14. M4 Higher-Timeframe Change of Character

Depends on doc 10. Paper mode, tag `M4`.

## 1. Overview
An extended 4h uptrend prints its first close below the last higher low (CHoCH) while OI sits at a multi-day high and funding is elevated. The crowd is long and the structure just cracked. Short the first 1h pullback into the newly formed 1h supply zone. Stop above the recent high, target the 4h range mid then the liquidation clusters below. Short version described; long is the mirror after a downtrend.

Type: swing reversal. Day types allowed: any except event inside 30 minutes; strongest on squeeze and range. Expected hold: 8 hours. Frequency: 2 to 4 per month per coin.

## 2. Sequence (short)
1. Extended trend: trend_4h up for at least 3 consecutive 4h BOS events, or price up >= 5 percent over the last 3 days (spec v1.2, D-74; was 5 BOS / 8 percent).
2. Crowding: OI at or within 3 percent of its 7-day high; funding z >= 1.0 at some point in the last 24h.
3. CHoCH: a 4h close below the most recent 4h higher low.
4. First pullback: after the CHoCH, price rallies on 1h into the 1h supply zone formed by the CHoCH move (the last up candle before the down displacement, or the FVG left by it). The rally shows OI falling or flat and taker buy ratio <= 0.55 (weak bounce).
5. Cohort: cohort net long change over 24h <= 0 (not adding longs).
6. Mind evaluates.

## 3. Execution
- Entry: post-only at the 1h supply zone mid (FVG mid or OB body mid), valid 6 hours. Post-only orders that would cross the touch are rejected and re-quoted once one tick inside it (spec v1.3, D-87); the re-quote fills only when a later print goes through it, else it is cancelled at the end of the validity.
- Stop: above the most recent 1h swing high plus 0.2 ATR(1h). Skip if stop distance > 2.0 ATR(1h). Minimum 0.5 ATR(1h) stop distance (spec v1.3, D-88).
- T1: 4h range mid, close 40 percent, stop to breakeven.
- T2: first long liquidation cluster below with notional >= band_p80 (calibrated, spec v1.1 Part C; was 0.15 percent of OI); T3: 4h range low. Trail behind each new 1h lower high after T2.
- Time stop: dead-trade at 12 hours; hard stop 36 hours.

## 4. Heuristic Mind for M4

### 4.1 Reasoning behind the weights
This is a positioning trade: the more crowded and extended the trend, the more fuel for the reversal, so OI extremity and funding elevation are top tier with the CHoCH itself. The quality of the pullback (weak bounce, OI not rebuilding) tells you whether sellers are in control now. Cohort behaviour is the best higher-timeframe truth-check available and carries the single highest weight; cohort adding longs is also a veto. Daily bias against the trade (daily still strongly up with the CHoCH being a shallow dip) is a veto because deep pullbacks in strong daily trends look identical to reversals on 4h. Clusters below define the reward.

### 4.2 Reasons
| key | description | detector | weight |
|---|---|---|---|
| cohort_reducing | smart wallets exiting longs | clip(-cohort_net_long_change_24h / 0.3, 0, 1) | 2.5 |
| oi_extreme | crowd positioned | clip(1 - (OI_7d_high - OI_now) / (0.05 x OI_7d_high), 0, 1) | 2.0 |
| funding_elevated | crowd paying | clip(max_funding_z_24h / 2.5, 0, 1) | 1.5 |
| choch_quality | decisive break of the higher low | clip((higher_low - choch_close) / (0.5 ATR_4h), 0, 1) | 1.5 |
| weak_bounce | pullback lacks buyers | clip((0.55 - taker_buy_ratio_bounce) / 0.15, 0, 1) x (1.0 if OI_change_bounce <= 0 else 0.5) | 1.8 |
| extension | how stretched the trend is | clip((pct_move_3d - 3) / 6, 0, 1) (spec v1.2, D-74; was (pct_move_3d - 6) / 8) | 1.0 |
| zone_quality | 1h supply from displacement | displacement_grade of the CHoCH move | 1.2 |
| cluster_reward | long clusters below | clip(sum_long_cluster_notional_below_within_range / (5 times band_p80), 0, 1) — calibrated (spec v1.1 Part C; was 0.5 percent of OI) | 1.0 |
| delta_flip | sellers taking control | clip((0.5 - taker_buy_ratio_4h_since_choch) / 0.15, 0, 1) | 1.0 |
| divergence | CVD did not confirm the last high | 1.0 if CVD at last 4h high < CVD at the prior 4h high; 0 otherwise | 1.2 |

### 4.3 Vetoes
- `cohort_adding_longs`: cohort net long change over 24h >= +0.15.
- `daily_strong_against`: daily_bias up and the CHoCH low is above the daily 20-candle midpoint (shallow dip in a strong daily trend).
- `oi_rebuilding`: OI up >= 2 percent during the bounce.
- `event_30m`.
- `stop_too_wide`: > 2.0 ATR(1h).
- `bounce_displacement`: the bounce contains a 1h displacement candle up (buyers are aggressive, not weak).
- `already_reversed`: price has already fallen more than 50 percent of the 4h range from the high before the pullback (late).

### 4.4 Context multipliers
- day_type: squeeze against the old trend 1.2; range 1.05; trend_down 1.0; trend_up 0.7; event 0.7; no_trade 0.8.
- session: entry during London or New York 1.05; Asia 0.9.
- recent_form: 2 consecutive M4 losses 0.85.
- funding trend: funding z falling from its 24h max 1.05 (the crowd is starting to leave).

### 4.5 In-trade checks
- `higher_high_1h`: a 1h close above the entry swing high, counts as 2.
- `oi_rebuild`: OI up >= 2 percent since entry.
- `cohort_flip`: cohort net long change since entry >= +0.15.
- `no_progress`: after 8 hours price has not traded below the CHoCH low.
exit_threshold = 2.

### 4.6 Thesis template
"M4 short {coin} after 4h CHoCH at {choch_level}. Trend up {pct_move} in 3d, OI within {oi_gap} of 7d high, funding z peaked {fz}. Cohort net long change {cohort_change}. Bounce buy ratio {br}. Strongest: {top3}. Wrong if 1h closes above {stop_level}. Expect {t1} within 8h, then {cluster_level}."

## 5. Claude Code prompt (paste verbatim)
```
Implement strategies/m4_htf_choch.py as class M4ChangeOfCharacter in the existing strategy engine using structure/ and mind/. Tag model M4. Paper mode, BTC and ETH. Evaluate on each closed 1h candle and on each closed 15m candle while a setup is pending.

Sequence for shorts: trend_4h up with at least 3 consecutive 4h BOS up events or price up at least 5 percent over 3 days (spec v1.2, D-74); OI within 3 percent of its 7-day high; funding z reached at least 1.0 within the last 24 hours; a 4h close below the most recent 4h higher low (CHoCH); then a 1h rally into the 1h supply zone formed by the CHoCH move (the last up candle before the down displacement or the FVG it left) with OI falling or flat during the rally and taker buy ratio at most 0.55; cohort net long change over 24 hours at most 0. Build the Snapshot and call the M4 Mind. Mirror for longs after an extended downtrend.

M4 Mind reasons and weights: cohort_reducing clip(negative cohort net long change over 24h over 0.3, 0, 1) weight 2.5; oi_extreme clip(1 minus (OI 7-day high minus OI now) over 0.05 times the 7-day high, 0, 1) weight 2.0; funding_elevated clip(max funding z in 24h over 2.5, 0, 1) weight 1.5; choch_quality clip((higher low minus CHoCH close) over 0.5 ATR(4h), 0, 1) weight 1.5; weak_bounce clip((0.55 minus taker buy ratio during the bounce) over 0.15, 0, 1) multiplied by 1.0 when OI change during the bounce is at most 0 else 0.5, weight 1.8; extension clip((percent move over 3 days minus 3) over 6, 0, 1) (spec v1.2, D-74) weight 1.0; zone_quality equal to displacement_grade of the CHoCH move weight 1.2; cluster_reward clip(sum of long liquidation cluster notional below price within the 4h range over 5 times band_p80 (calibrated, spec v1.1 Part C; was 0.5 percent of OI), 0, 1) weight 1.0; delta_flip clip((0.5 minus taker buy ratio over 4h since the CHoCH) over 0.15, 0, 1) weight 1.0; divergence 1.0 when CVD at the last 4h high is below CVD at the prior 4h high else 0, weight 1.2.

Vetoes: cohort_adding_longs (cohort net long change over 24h at least 0.15), daily_strong_against (daily_bias up and the CHoCH low above the midpoint of the last 20 daily candles), oi_rebuilding (OI up at least 2 percent during the bounce), event_30m, stop_too_wide (above 2.0 ATR(1h)), bounce_displacement (a 1h displacement candle up inside the bounce), already_reversed (price already below 50 percent of the 4h range before the pullback).

Context multipliers: day_type squeeze against the old trend 1.2, range 1.05, trend_down 1.0, trend_up 0.7, event 0.7, no_trade 0.8; entry during London or New York 1.05, Asia 0.9; two consecutive M4 losses 0.85; funding z falling from its 24h max 1.05.

Execution: post-only at the 1h supply zone mid valid 6 hours. Stop above the most recent 1h swing high plus 0.2 ATR(1h), skip if above 2.0 ATR(1h) from entry. Minimum stop distance (spec v1.3, D-88): if the stop is closer than 0.5 ATR(1h) to entry it is moved out to exactly 0.5 ATR(1h) beyond entry; size and R use the final stop, targets stay as computed from the structural stop, `stop_floor_applied` is recorded on the trade row. T1 at the 4h range mid, close 40 percent, stop to breakeven. T2 at the first long liquidation cluster below holding at least band_p80 (calibrated, spec v1.1 Part C; was 0.15 percent of OI), T3 at the 4h range low, trailing behind each new 1h lower high after T2. expected_hold_min 480; hard time stop 2160 minutes. Size via RiskEngine using size_tier.

In-trade checks: higher_high_1h (1h close above the entry swing high, counts as 2), oi_rebuild (OI up at least 2 percent since entry), cohort_flip (cohort net long change since entry at least 0.15), no_progress (no trade below the CHoCH low after 8 hours). exit_threshold 2.

Thesis template: "M4 short {coin} after 4h CHoCH at {choch_level}. Trend up {pct_move} in 3d, OI within {oi_gap} of 7d high, funding z peaked {fz}. Cohort net long change {cohort_change}. Bounce buy ratio {br}. Strongest: {top3}. Wrong if 1h closes above {stop_level}. Expect {t1} within 8h, then {cluster_level}."

Log every evaluation with all Mind fields, alert on CHoCH detected with crowding readings, on take with thesis, on skip with vetoes, on exit with exit_reason and R. Unit tests: a valid CHoCH and weak bounce, a cohort_adding_longs veto, a daily_strong_against veto, an in-trade higher_high_1h exit.
```
