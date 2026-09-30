# REPLAY-6M-v1.2 — M1…M6 over 180 days on BTC and ETH (spec v1.2 tree, two fill models)

Owner order (2026-09-06, spec v1.2 Parts 3–6): 180 days of Hyperliquid liquidation fills, point-in-time calibration with zero NULL lookups, the adverse-first fill model as the primary result and a neutral path beside it, all six models replayed over 180 days with feed vetoes off and unavailable handling on (cohort only, and taker delta where absent), with the statistics of REPLAY-6M.md plus the v1.2 additions. Every number below is read back from the two replay databases on prod (`perpl_replay` = adverse, `perpl_replay_n` = neutral: `strat_signals`, `strat_trades`, `strat_calibration_hist`) by `/root/replay_stats_v12.py` (outputs `/root/replay_stats_{adverse,neutral}.md/.json`), from the replay driver's own log (`/root/replay_v12_{adverse,neutral}.out`), the feed-presence files (`/root/replay_feeds_{adverse,neutral}.json`), `/root/struct_stats.json` (structure statistics on the v1.2 tree) and `/root/leg_stats_v12.json` (D-75 leg statistics); nothing is estimated. Decisions taken while building and reading it: D-74 … D-81 before the run, D-82 … D-86 from reading it (§11).

## 0. Read this first — what this replay is and is not

**It is NOT the calibrated 180-day replay that was ordered.** Part 3 assumed the 0xArchive key is on the Build plan; probed from prod with the prod key at 2026-09-06 19:25 UTC, `GET /v1/account` returns `tier: "free"` (`subscription_status: active`, `current_period_end: 2026-10-06T11:28:24Z`, `trial_used: false`) and any liquidation request older than 30 days returns `403 history_window_exceeded` ("Your plan includes the most recent 30 days of history … Build and above include the full retained archive, or purchase the range once via the Data Catalog"). The loader was run for the ordered span anyway (`days=215`, D-77) and the API clamped it: **"plan history starts 2026-08-07T19:37:25 — 29 of the requested 215 days loaded"** (`/root/oxarchive_load_v12.log`). Nothing was done to change that — activating the trial or buying a Data Catalog range is the owner's account/money decision (D-81).

Consequences, all measured, none hidden:

- The liquidation tape exists from **2026-08-07 12:08 UTC** only (16.7 % of the window). The 15-minute liquidation-map history covers the same 30 days, not 180.
- Point-in-time calibration (Part 4) is implemented and was built for every day of the window — **182 as-of days per coin, 182 NULL**: at every day boundary the trailing tape is shorter than the 30-day minimum (29.49 d even on the last day, because the as-of rule uses only data before that day), so **every calibration lookup in the replay was NULL** (BTC 17,281 + ETH 17,281 = 34,562 WARNING lines per pass, §1 and §10) and the Snapshot used the fixed fallback normaliser (0.10 % of OI) throughout, exactly as REPLAY-6M did. "Confirm zero NULL calibration lookups" is therefore confirmed FALSE, with the count.
- What the two passes DO measure: the spec v1.2 tree (D-74 M4 extension, D-75 M2 displacement leg) on 180 days of Binance structure with the D-80 unavailable set, under two fill models, side by side. That is a structure + fill-model measurement. Re-running the same prod scripts after a plan upgrade (`oxarchive_load_v12.py` → `v12_setup.py` → `replay_v12.py` × 2 → `replay_stats_v12.py` × 2) produces the ordered result with no code change (D-81).

## 1. Method

| item | value |
|---|---|
| Tree | spec v1.2 (`/root/audit_tree`, same files as the deployed `/var/www/terminal/backend`), 280 unit tests passing |
| Window | 2026-03-10 19:45 → 2026-09-06 19:45 UTC, 180 days, 17,281 15-minute boundaries per coin (34,562 total), BTC and ETH |
| Structure candles | Binance 15m/1h/4h/1d (`strat_replay_candles`, refreshed to the window end); 100 % present |
| Liquidation tape | Hyperliquid liquidation fills via 0xArchive, 2026-08-07 12:08 → 2026-09-06 UTC only (§0, §10); live copy-tracker userFills from 2026-08-11 |
| Liquidation map | 15-minute map rebuilt over the available tape (same 30 days) |
| Feed vetoes | OFF (D-68 carried) |
| Unavailable handling | ON with the D-80 set `{cohort, taker, oi}`: when the feed is absent the reason is EXCLUDED from the conviction denominator; every other absent feed (book, gauge, events, liq, liqmap) evaluates exactly as live (optional reason = 0, vetoes evaluate on whatever is present) |
| Fill model, pass 1 (primary) | adverse-first: stop and target inside one candle → stop (unchanged from REPLAY-6M) — DB `perpl_replay`, log `/root/replay_v12_adverse.out`, 17,666 s wall clock |
| Fill model, pass 2 | neutral: stop and target inside one candle → the one nearer the candle open; equidistant → adverse (D-78) — DB `perpl_replay_n`, log `/root/replay_v12_neutral.out`, 17,372 s wall clock. Never favourable-first |
| Calibration | point-in-time (D-76): `strat_calibration_hist` built at every day boundary from data strictly before that day, trailing 30 d minimum / 180 d maximum; the replay reads the as-of row → 182 as-of days per coin, **182 NULL**, 34,562 NULL lookups per pass (fallback normaliser 0.10 % of OI) |
| Cohort | 30 wallets, loaded once in 47 s (sampled 1.8 % of boundaries — the cohort feed only exists from the Tier-2 sampler start) |
| Tracebacks | 0 in either `.out` |

### 1.1 Feed availability over the window (fraction of boundaries with the feed present)

| feed | BTC | ETH |
|---|---|---|
| candles (Binance) | 1.000 | 1.000 |
| oi | 0.169 | 0.169 |
| liq (tape) | 0.1685 | 0.1685 |
| liqmap | 0.1686 | 0.1684 |
| taker | 0.0179 | 0.0179 |
| cohort | 0.0179 | 0.0179 |
| book | 0.0179 | 0.0179 |
| gauge | 0.018 | 0.018 |
| events | 0.0128 | 0.0128 |

### 1.2 Definitions (unchanged from REPLAY-6M)

- **setup row**: a boundary where the model's precondition completed and a Mind evaluation was produced (`raw_conviction` present); **non-vetoed**: no veto hit; **take**: `fired = 1` (conviction ≥ 0.55, tier full ≥ 0.75 / half otherwise); **filled + exited**: the paper trade filled and closed inside the window.
- **R**: (exit − entry)/(entry − stop) signed by direction; **PF**: gross wins / gross losses; **max DD**: largest peak-to-trough of cumulative R in take order; **hold**: minutes from fill to exit.
- **conviction bucket**: floor to 0.1 of the final conviction (0.5 = [0.5, 0.6)); **day type**: the `day_type` field on the signal row (Mind classifier: trend / range / no_trade).
- **excluded reason**: a reason whose feed was in the D-80 unavailable set at that boundary; it is removed from the denominator (effective maximum weight) and the numerator.

## 2. Funnel (identical in both passes — signals are fill-model independent; the neutral pass differs only downstream, §3.2)

207,372 evaluation rows (17,281 boundaries × 2 coins × 6 models); 189 model trades in the adverse pass, 188 in the neutral pass.

| model | coin | setup rows | vetoed | non-vetoed | takes (filled) | full / half | veto counts | unevaluated vetoes (feed absent; rows) |
|---|---|---|---|---|---|---|---|---|
| M1 | BTC | 776 | 630 | 146 | 70 (66) | 33 / 37 | third_sweep 487, too_deep 392, cluster_below_uncleared 28, event_30m 1 | cluster_below_uncleared 659 (14,360 rows without liqmap) |
| M1 | ETH | 770 | 615 | 155 | 69 (63) | 35 / 34 | third_sweep 450, too_deep 385, cluster_below_uncleared 9, trend_against 1, event_30m 1 | cluster_below_uncleared 623 |
| M2 | BTC | 8 | 8 | 0 | 0 | – | stop_too_wide 8, zone_premium 5 | short_covering 5, oi_exit_retrace 5 (14,360 rows without taker / oi) |
| M2 | ETH | 16 | 13 | 3 | 0 | – | stop_too_wide 13, zone_premium 3 | short_covering 16, oi_exit_retrace 16 |
| M3 | BTC | 57 | 0 | 57 | 9 (6) | 1 / 8 | – | squeeze_risk 57, cohort_adding_longs 57, acceptance 56 (rows without liqmap 16,971 / cohort 16,971 / oi 14,360) |
| M3 | ETH | 39 | 1 | 38 | 5 (3) | 1 / 4 | acceptance 1 | squeeze_risk 39, cohort_adding_longs 39, acceptance 37 |
| M4 | BTC | 0 | 0 | 0 | 0 | – | – | cohort_adding_longs 16,971 rows, oi_rebuilding 14,360 rows |
| M4 | ETH | 0 | 0 | 0 | 0 | – | – | same |
| M5 | BTC | 88 | 78 | 10 | 9 (6) | 5 / 4 | range_bad 62, too_deep 39, opened_outside 18, already_taken 2 | – |
| M5 | ETH | 92 | 80 | 12 | 9 (6) | 2 / 7 | range_bad 52, too_deep 47, opened_outside 21, already_taken 2 | – |
| M6 | BTC | 1,424 | 1,275 | 149 | 9 (7) | 2 / 7 | already_taken 495, too_late 444, daily_strong_down 364, shallow_loss 244, stop_too_wide 204, oi_falling 86 | oi_falling 1,300 (14,360 rows without oi) |
| M6 | ETH | 1,648 | 1,380 | 268 | 9 (8) | 2 / 7 | too_late 524, daily_strong_down 513, already_taken 431, shallow_loss 344, oi_falling 128, stop_too_wide 112 | oi_falling 1,476 |

Conviction histograms of the non-vetoed rows (final conviction after multipliers / raw conviction before them), bucket floor 0.1:

| model | coin | final | raw |
|---|---|---|---|
| M1 | BTC | 0.1:2 0.2:3 0.3:26 0.4:27 0.5:21 0.6:31 0.7:17 0.8:11 0.9:5 1.0:3 | 0.2:1 0.3:2 0.4:17 0.5:26 0.6:47 0.7:32 0.8:20 0.9:1 |
| M1 | ETH | 0.1:1 0.2:8 0.3:19 0.4:42 0.5:28 0.6:20 0.7:17 0.8:11 0.9:5 1.0:4 | 0.2:1 0.3:4 0.4:19 0.5:38 0.6:47 0.7:29 0.8:14 0.9:3 |
| M2 | ETH | 0.1:1 0.2:2 | 0.2:1 0.3:2 |
| M3 | BTC | 0.2:4 0.3:21 0.4:14 0.5:12 0.6:5 0.7:1 | 0.3:7 0.4:25 0.5:23 0.6:1 0.7:1 |
| M3 | ETH | 0.2:2 0.3:11 0.4:19 0.5:3 0.6:2 0.7:1 | 0.3:4 0.4:19 0.5:14 0.6:1 |
| M5 | BTC | 0.5:1 0.6:4 0.7:2 0.8:2 0.9:1 | 0.5:3 0.6:4 0.7:3 |
| M5 | ETH | 0.4:1 0.5:4 0.6:5 0.9:2 | 0.4:2 0.5:4 0.6:3 0.7:1 0.8:2 |
| M6 | BTC | 0.2:56 0.3:24 0.4:52 0.5:13 0.6:2 0.7:2 | 0.2:4 0.3:80 0.4:44 0.5:12 0.6:5 0.7:4 |
| M6 | ETH | 0.2:76 0.3:60 0.4:84 0.5:43 0.6:3 0.7:2 | 0.2:48 0.3:36 0.4:44 0.5:39 0.6:74 0.7:25 0.8:2 |

M1 level types of the setup rows — BTC (776): asia_high 96, pdh 87, equal_highs_4h 67, pdl 64, pwh 58, equal_lows_1h 54, london_high 54, asia_low 51, 4h_ob_top 42, equal_highs_1h 41, equal_lows_4h 37, london_low 34, 4h_ob_bottom 30, pwl 25, 4h_fvg_top 16, 4h_fvg_bottom 11, liq_cluster_short 7, liq_cluster_long 2. ETH (770): asia_high 100, pdl 94, pdh 67, asia_low 66, london_high 66, equal_highs_1h 50, equal_highs_4h 47, equal_lows_1h 45, london_low 43, 4h_ob_top 43, 4h_ob_bottom 35, pwl 33, equal_lows_4h 27, pwh 17, 4h_fvg_bottom 14, 4h_fvg_top 12, liq_cluster_long 7, liq_cluster_short 4. The liquidation-cluster levels (20 rows in total) can only appear inside the 30 days where the map exists.

### 2.1 M4 under D-74 (extended trend = ≥3 consecutive 4h BOS or ≥5 % in-trend move over 3 days)

Still 0 setup rows on both coins. The v1.2 gate order (`m4_htf_choch.py`): a 4h CHoCH inside 72 h is required first, then the extension test on the trend that the CHoCH broke. Reason-string counts over the 17,281 boundaries per coin (a boundary produces one reason per side):

| coin | side | no 4h CHoCH in 72 h | CHoCH found, trend not extended | extension passed, later stage failed |
|---|---|---|---|---|
| BTC | long (old trend down) | 13,921 | 3,360 | 0 |
| BTC | short (old trend up) | 13,072 | 4,209 | 0 |
| ETH | long | 14,113 | 2,592 | 576 (bounce not reached zone 568, waiting for bounce 8) |
| ETH | short | 13,872 | 2,688 | 721 (no 1h displacement inside the CHoCH move 288, no live 1h zone 267, bounce not reached zone 153, OI 3.05–3.22 % below its 7d high 9, waiting for bounce 4) |

Extension maxima parsed from the not-extended reasons (`BOS n, 3d move ±x %`, direction-signed): BTC — max consecutive BOS 2 on both sides, largest in-trend 3-day move +3.77 % (up) / 4.00 % (down); the 5.9 % figure that appears in the raw absolute maxima was a move AGAINST the trend and does not count. ETH — max BOS 2, largest in-trend move 4.70 % (up) / 3.81 % (down) among the rows that failed. ETH passed the extension in **5 episodes** (2026-03-10→03-12, 03-26→03-29, 05-06→05-09, 06-08→06-10, 08-12→08-15) and every one failed downstream: the 1h bounce never reached the 1h zone left by the CHoCH move in three episodes, the 06-08 episode had no 1h displacement inside the CHoCH move, and the 08-12 episode lost its live zone after 3 h and then failed the OI rule by 0.05–0.22 percentage points on 9 boundaries (OI 3.05–3.22 % below its 7-day high, rule > 3 %). Compared with REPLAY-6M (v1.1: BTC never passed, ETH passed once and failed on "no 1h displacement"), D-74 lets ETH through the extension gate in 5 episodes instead of 1 but the later stages are unchanged and no setup completes. Recorded, not tuned (D-85).

### 2.2 M2 under D-75 (break may be a displacement leg of ≤3 1h candles)

Setup rows 24 (BTC 8, ETH 16) vs 8 in v1.1; non-vetoed 3 (all ETH, conviction 0.18 / 0.25 / 0.25 — the three raw convictions 0.2395 / 0.3477 / 0.3477 before the multipliers, no veto hit), takes 0. First-failing precondition per side over the 17,281 boundaries (rows):

| stage | BTC long | BTC short | ETH long | ETH short |
|---|---|---|---|---|
| 4h trend / fresh 4h CHoCH-confirmed-by-1h-BOS / 1h trend + daily bias not aligned (6 sub-variants) | 10,656 | 12,589 | 10,945 | 12,504 |
| no 1h BOS in the look-back | 1,816 | 956 | 1,424 | 1,304 |
| 1h BOS is not a displacement leg (≤3 candles, ≥1.5 ATR, body ≥60 %) | 1,020 | 1,300 | 1,325 | 688 |
| zone from the break already broken / filled | 1,376 | 760 | 1,252 | 824 |
| range day and the break is not a 4h level | 736 | 545 | 649 | 781 |
| retrace candle range > limit ATR | 647 | 481 | 531 | 475 |
| displacement leg left no 1h FVG inside the leg | 617 | 444 | 676 | 564 |
| retrace < required fraction of the way to the zone | 194 | 182 | 240 | 80 |
| leg has no opposite candle before it (no OB) | 172 | 0 | 180 | 28 |
| break added too little OI | 20 | 0 | 24 | 8 |
| break confirmed — waiting for the retrace | 8 | 12 | 15 | 5 |
| 15m already closed beyond the OB — zone invalid | 7 | 4 | 4 | 4 |
| break buy ratio below threshold | 4 | 0 | 0 | 0 |
| vetoed (zone_premium / stop_too_wide) | 8 | 0 | 13 | 0 |
| setup complete, conviction below 0.55 | 0 | 0 | 3 | 0 |

The D-75 leg detector reaches the FVG stage 2,301 times (617 + 444 + 676 + 564 "no FVG inside the leg") and the OB stage 380 times, i.e. the leg definition itself is now rarely the binding constraint — the FVG-inside-leg requirement and the stale-zone rule are. Leg statistics on the 1h series over the window (§7.8) show that 561 (BTC) / 515 (ETH) legs qualify only as 2- or 3-candle legs, which is the population D-75 adds. Stop_too_wide vetoes all 21 rows that reached a veto (8 BTC, 13 ETH; zone_premium on 8 of them) — the same binding veto as v1.1.

## 3. Performance — adverse (primary) and neutral side by side

### 3.1 Summary (filled + exited takes, both coins)

| model | pass | takes | filled + exited | unfilled | win rate | expectancy R | PF | max DD R | sum R | hold mean / median min |
|---|---|---|---|---|---|---|---|---|---|---|
| M1 | adverse | 139 | 129 | 10 (no_fvg 8, expired 2) | 34 % | **−0.351** | 0.47 | 56.97 | −45.25 | 53 / 20 |
| M1 | neutral | 138 | 129 | 9 (no_fvg 7, expired 2) | 34 % | **−0.376** | 0.43 | 60.21 | −48.50 | 52 / 20 |
| M2 | both | 0 | 0 | 0 | – | – | – | – | – | – |
| M3 | both | 14 | 9 | 5 (expired) | 11 % | **−0.220** | 0.85 | 13.02 | −1.98 | 48 / 15 |
| M4 | both | 0 | 0 | 0 | – | – | – | – | – | – |
| M5 | both | 18 | 12 | 6 (expired) | 33 % | **−0.437** | 0.40 | 6.94 | −5.24 | 52 / 15 |
| M6 | both | 18 | 15 | 3 (expired) | 20 % | **−0.030** | 0.93 | 5.26 | −0.46 | 341 / 190 |

M3, M5 and M6 are trade-for-trade identical in the two passes: the neutral rule only acts when stop and target sit inside one candle, and the neutral pass counted that on **4 trades in total** (`both_inside 4, adverse_first_or_tie 2, favourable_first 2`), all M1. v1.1 reference (REPLAY-6M, adverse): M1 −0.35 R n=129, M3 −0.14 R n=30 (55 takes), M5 −0.44 R n=12, M6 −0.37 R n=18 (21 takes). M1 is bit-identical to v1.1 in the adverse pass (same excluded set, same trades); M3 and M6 differ because of D-80, not D-74/D-75 (§5).

### 3.2 M1 — splits, adverse vs neutral

| split | adverse n | win | exp R | PF | max DD | sum R | neutral n | win | exp R | PF | max DD | sum R |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| all | 129 | 34 % | −0.351 | 0.47 | 56.97 | −45.25 | 129 | 34 % | −0.376 | 0.43 | 60.21 | −48.50 |
| BTC | 66 | 32 % | −0.368 | 0.44 | 30.46 | −24.29 | 66 | 33 % | −0.325 | 0.48 | 27.64 | −21.47 |
| ETH | 63 | 37 % | −0.333 | 0.51 | 27.52 | −20.97 | 63 | 35 % | −0.429 | 0.38 | 33.58 | −27.03 |
| conviction 0.5 | 22 | 41 % | −0.297 | 0.56 | 11.82 | −6.54 | 23 | 39 % | −0.335 | 0.52 | 12.97 | −7.69 |
| conviction 0.6 | 39 | 36 % | −0.130 | 0.79 | 11.47 | −5.08 | 38 | 37 % | −0.218 | 0.63 | 14.66 | −8.28 |
| conviction 0.7 | 31 | 26 % | −0.619 | 0.15 | 19.19 | −19.19 | 31 | 29 % | −0.559 | 0.18 | 17.33 | −17.33 |
| conviction 0.8 | 20 | 30 % | −0.528 | 0.22 | 11.05 | −10.56 | 21 | 29 % | −0.568 | 0.20 | 12.43 | −11.94 |
| conviction 0.9 | 10 | 50 % | −0.219 | – | – | – | 9 | 44 % | −0.298 | 0.52 | 5.21 | −2.68 |
| conviction 1.0 | 7 | 29 % | −0.242 | – | – | – | 7 | 29 % | −0.083 | 0.87 | 3.36 | −0.58 |
| day type range | 129 | 34 % | −0.351 | 0.47 | 56.97 | −45.25 | 129 | 34 % | −0.376 | 0.43 | 60.21 | −48.50 |
| day type trend / no_trade | 0 | – | – | – | – | – | 0 | – | – | – | – | – |
| tier full | 68 | 31 % | −0.495 | – | – | – | 68 | 31 % | −0.478 | 0.29 | 34.09 | −32.53 |
| tier half | 61 | 38 % | −0.190 | – | – | – | 61 | 38 % | −0.262 | 0.58 | 27.36 | −15.97 |

Exit reasons — adverse: stop 68, stop_be 35, dead_trade 8, target 8, time_stop 7, thesis_failed 3. Neutral: stop 67, stop_be 37, dead_trade 8, time_stop 7, target 7, thesis_failed 3. Every M1 take was classified `range` day type — the Mind's day classifier never produced `trend` or `no_trade` at an M1 take in 180 days (M5 has the only `no_trade` take, §3.3).

**Why the neutral pass differs by more than the two candles it re-ordered (D-82).** The neutral rule changed the direct outcome of exactly two M1 trades: signal 479797 (BTC long 2026-05-16 07:15) adverse `stop −1.158` → neutral `stop_be −0.050`, and signal 490093 (BTC short 2026-05-25 05:45) adverse `stop −1.546` → neutral `stop_be +0.164` (+1.108 and +1.710 R in the neutral pass's favour). The other two both-inside candles resolved adverse-first-or-tie, i.e. identical to pass 1. Everything else is a cascade:

1. `ModelRunner._recent_form` (last 5 outcomes of the model, 0.8 multiplier on the losing pattern) saw a different history from 2026-05-25 onward, so **438 M1 signal rows carry a different final conviction** between the passes (e.g. signal 490537: adverse 0.7113 with recent_form 0.8, neutral 0.8891 with 1.0) while raw conviction is identical everywhere.
2. That moved three signals across the 0.55 take line: neutral took 491647 (ETH short 2026-05-26 14:00, conviction 0.56 vs adverse 0.45) → `stop −1.157` at 14:20; adverse took 492829 (BTC long 2026-05-27 15:30, 0.61 vs neutral 0.49) → cancelled unfilled; and 491671 (ETH short 2026-05-26 14:30) fired in BOTH passes (neutral 0.80, adverse 0.64) but produced a trade only in adverse, where it ran to **target +4.903 R**. In the neutral pass the 14:00 trade was still open at the 14:30 evaluation: `run()` evaluates and opens before `_mgr.manage` closes the previous trade inside the same boundary (`model_runner.py`: evaluate → `_open` duplicate check on `exit_ts IS NULL` → manage), so the 14:30 fire was recorded as a take with no trade ("already has trade" is logged at INFO, which the `.out` does not capture — the mechanism is read from the code, not from the log).
3. Net: neutral gains +2.82 R on the two re-ordered candles and loses the +4.90 R target plus the −1.16 R extra stop → sum −48.50 vs −45.25.

This is a **property of the replay's within-boundary ordering and of recent_form, not of the fill model** — the identical sequence would happen live whenever a stop and a new fire land in the same 15-minute boundary. Recorded, not changed (D-82): the owner froze weights, vetoes and thresholds, and the ordering is the live path's ordering.

### 3.3 M3, M5, M6 — splits (identical in both passes)

| model | split | n | win rate | expectancy R | PF | max DD R | sum R | hold mean / median min |
|---|---|---|---|---|---|---|---|---|
| M3 | all | 9 | 11 % | −0.220 | 0.85 | 13.02 | −1.98 | 48 / 15 |
| M3 | BTC | 6 | 17 % | +0.450 | 1.32 | 8.34 | +2.70 | 68 / 8 |
| M3 | ETH | 3 | 0 % | −1.560 | 0.00 | 4.68 | −4.68 | 10 / 15 |
| M3 | conviction 0.5 | 3 | 0 % | −1.609 | 0.00 | 4.83 | −4.83 | 15 / 15 |
| M3 | conviction 0.6 | 6 | 17 % | +0.474 | 1.35 | 8.19 | +2.84 | 65 / 8 |
| M3 | day type range | 9 | 11 % | −0.220 | 0.85 | 13.02 | −1.98 | 48 / 15 |
| M3 | tier half (all 9) | 9 | 11 % | −0.220 | 0.85 | 13.02 | −1.98 | 48 / 15 |
| M5 | all | 12 | 33 % | −0.437 | 0.40 | 6.94 | −5.24 | 52 / 15 |
| M5 | BTC | 6 | 33 % | −0.467 | 0.20 | 2.96 | −2.80 | 31 / 10 |
| M5 | ETH | 6 | 33 % | −0.407 | 0.54 | 4.14 | −2.44 | 72 / 38 |
| M5 | conviction 0.5 | 2 | 0 % | −1.300 | 0.00 | 2.60 | −2.60 | 32 / 32 |
| M5 | conviction 0.6 | 4 | 25 % | −0.352 | 0.44 | 2.53 | −1.41 | 76 / 70 |
| M5 | conviction 0.7 | 2 | 100 % | +0.348 | inf | 0.00 | +0.70 | 22 / 22 |
| M5 | conviction 0.8 | 2 | 0 % | −1.170 | 0.00 | 2.34 | −2.34 | 5 / 5 |
| M5 | conviction 0.9 | 2 | 50 % | +0.204 | 1.32 | 1.28 | +0.41 | 98 / 98 |
| M5 | day type no_trade | 1 | 0 % | −0.005 | 0.00 | 0.00 | −0.00 | 125 / 125 |
| M5 | day type range | 11 | 36 % | −0.476 | 0.40 | 6.93 | −5.24 | 45 / 15 |
| M5 | tier full | 6 | 50 % | −0.206 | 0.66 | 2.93 | −1.24 | 42 / 10 |
| M5 | tier half | 6 | 17 % | −0.668 | 0.22 | 4.01 | −4.01 | 62 / 38 |
| M6 | all | 15 | 20 % | −0.030 | 0.93 | 5.26 | −0.46 | 341 / 190 |
| M6 | BTC | 7 | 29 % | +0.170 | 1.34 | 3.48 | +1.19 | 284 / 280 |
| M6 | ETH | 8 | 12 % | −0.206 | 0.42 | 1.79 | −1.64 | 391 / 175 |
| M6 | conviction 0.5 | 6 | 17 % | −0.178 | 0.53 | 2.26 | −1.07 | 262 / 100 |
| M6 | conviction 0.6 | 5 | 20 % | +0.186 | 1.27 | 2.44 | +0.93 | 582 / 330 |
| M6 | conviction 0.7 | 4 | 25 % | −0.080 | 0.43 | 0.56 | −0.32 | 158 / 175 |
| M6 | day type range | 15 | 20 % | −0.030 | 0.93 | 5.26 | −0.46 | 341 / 190 |
| M6 | tier full | 4 | 25 % | −0.080 | 0.43 | 0.56 | −0.32 | 158 / 175 |
| M6 | tier half | 11 | 18 % | −0.012 | 0.98 | 4.71 | −0.14 | 407 / 310 |

Exit reasons: M3 stop 8, time_stop 1; M5 stop 7, stop_be 3, time_stop 2; M6 thesis_failed 8, stop_be 2, stop 2, target 2, time_stop 1 (M6 detail in §8). Conviction-bucket reading with these sample sizes (n ≤ 6 per bucket for M3/M5/M6, n ≤ 39 for M1): no bucket with n ≥ 7 is positive — M1's best bucket (0.6, n 39) is −0.130 adverse / −0.218 neutral, and the higher buckets 0.7–0.8 (n 51) are the worst at −0.56 to −0.62. Higher conviction did not mean better outcomes in this window under the fallback normaliser; with 121 of 129 M1 takes carrying excluded fuel/cleared/delta/absorption/cohort reasons (§6), the conviction here is mostly structure + level quality + multipliers, not the full Mind.

## 4. Frequency vs the docs (BTC + ETH together, 180 d = 6 × 30 d)

| model | doc frequency | per 30 d (doc) | AUDIT §8.6 range | takes / 180 d (adverse) | per 30 d | ratio | > 5× outside? | neutral |
|---|---|---|---|---|---|---|---|---|
| M1 | 1 to 3 per day across BTC and ETH | 30–90 | 20–60 | 139 | 23.2 | 1.3× below | no | 138 → 23.0 |
| M2 | 0 to 2 per day | 0–60 | 10–30 | 0 | 0.0 | in range (lower bound 0) | no | 0 |
| M3 | 0 to 2 per day | 0–60 | 10–30 | 14 | 2.3 | in range | no | 14 |
| M4 | 2 to 4 per month per coin | 4–8 | 0–6 | 0 | 0.0 | ∞ below | **yes** | 0 |
| M5 | up to 2 per day per coin | 0–120 | 15–40 | 18 | 3.0 | in range (below §8.6) | no | 18 |
| M6 | at most one per week per coin per direction | 0–17 | 0–8 | 18 | 3.0 | in range | no | 18 |

Per coin per 30 d (adverse): M1 BTC 11.7 / ETH 11.5 (neutral 69 / 69 takes → 11.5 / 11.5; tiers BTC 33 full / 36 half, ETH 36 / 33 — the recent_form cascade of §3.2 also moved tiers); M3 1.5 / 0.8; M5 1.5 / 1.5; M6 1.5 / 1.5.

## 5. Unavailable handling (D-80) — excluded reasons and effective maximum weight

| model | total weight | setup rows | excluded reason counts | effective max weight distribution |
|---|---|---|---|---|
| M1 | 13.2 | 1,546 | delta_flip 1,525, absorption 1,525, cohort 1,525, fuel 1,282, cleared 1,282 | 7.0: 1,282 · 9.7: 243 · 13.2: 21 |
| M2 | 13.5 | 24 | delta_break 24, oi_new_positioning 21, oi_holding 21, cluster_cleared 21 | 8.0: 21 · 12.3: 3 |
| M3 | 13.5 | 96 | cvd_divergence 96, trap 93, cluster_fuel 93 | 8.3: 93 · 11.5: 3 |
| M4 | – | 0 | – | – |
| M5 | 12.9 | 180 | delta_flip 179, cohort 179, fuel 146, cleared 146 | 8.5: 146 · 10.8: 33 · 12.9: 1 |
| M6 | 13.8 | 3,072 | delta_reclaim 3,054, cohort 3,054, oi_commitment 2,776 | 8.5: 2,776 · 10.5: 278 · 13.8: 18 |

D-80 vs REPLAY-6M's D-68 set: D-68 excluded every absent feed's reasons; D-80 excludes only `{cohort, taker, oi}` and lets book / gauge / events / liq / liqmap reasons evaluate as live (an absent optional feed scores its reason 0 but keeps it in the denominator). Effect, read from the funnel:

- **M1 unchanged** (139 takes, identical trades): its reasons on absent feeds are exactly fuel/cleared (oi, liqmap→oi-normalised), delta_flip/absorption (taker) and cohort — all inside the D-80 set — so the denominator is the same as in v1.1.
- **M3 55 → 14 takes** with the same 96 setup rows: `thin_bids` (book) and `funding_up` (gauge) now stay in the denominator at 0 for 98.2 % of boundaries, raising the effective maximum from 6.5 (v1.1: 94 of 97 rows) to 8.3 (93 of 96 rows) and pulling the raw conviction peak from 0.5–0.6 down to 0.4–0.5 (histograms in §2) — 41 of the v1.1 takes fall below 0.55. Not a model change; a stricter reading of "unavailable" (D-83).
- **M6 21 → 18 takes**: `funding_room` (gauge) now evaluates at 0, effective maximum 7.3 (v1.1, 2,776 rows) → 8.5 (2,776 rows); three v1.1 takes drop below the line. Non-vetoed rows rose (BTC 109 → 149, ETH 112 → 268) because `already_taken` fires less often when fewer takes happen (495 / 431 vs 517 / 670), not because more setups exist.
- M2 (`funding_young`) and M5 (`funding_ok`) likewise keep their gauge reason in the denominator; M5 takes are unchanged at 18 because its non-vetoed rows were already few and well above the line.

## 6. Fuel — M1 `fuel` and M3 `cluster_fuel`, takes above 0.7 vs below 0.3

| model | split | n | win rate | expectancy R | PF | max DD R | sum R | hold mean / median min |
|---|---|---|---|---|---|---|---|---|
| M1 | fuel_strength > 0.7 | 0 | – | – | – | – | – | – |
| M1 | fuel_strength < 0.3 (all) | 128 | 34 % | −0.353 | 0.47 | 50.74 | −45.24 | 53 / 20 |
| M1 | fuel_strength < 0.3 and the reason EVALUATED (liq/liqmap present) | 7 | 43 % | +0.675 | 2.94 | 1.22 | +4.72 | 46 / 20 |
| M1 | reason EXCLUDED (liq/liqmap absent) | 121 | 34 % | −0.413 | 0.40 | 51.98 | −49.96 | 53 / 20 |
| M3 | cluster_fuel_strength > 0.7 | 0 | – | – | – | – | – | – |
| M3 | cluster_fuel_strength < 0.3 (all) | 9 | 11 % | −0.220 | 0.85 | 13.02 | −1.98 | 48 / 15 |
| M3 | cluster_fuel_strength < 0.3 and EVALUATED | 0 | – | – | – | – | – | – |
| M3 | reason EXCLUDED | 9 | 11 % | −0.220 | 0.85 | 13.02 | −1.98 | 48 / 15 |

M1 `fuel_strength` was non-zero on 5 of 129 takes (0.02, 0.03, 0.05, 0.07, 0.59) — all within the 30 days where the tape exists and all under the fallback normaliser (0.10 % of OI), which is why nothing reaches 0.7. M3's `cluster_fuel_strength` is 0.0 on all 9 takes (map absent at every M3 take). The ordered comparison ("above 0.7 vs below 0.3") therefore has an empty upper cell for both models; the only readable contrast is evaluated-vs-excluded for M1 (7 vs 121 trades), and n = 7 is not evidence. Same as v1.1 (§8 finding 6 there) — the fuel dimension cannot be judged until the tape covers the window (D-81).

## 7. Structure statistics over the window (v1.2 tree, 2026-03-10 19:45 → 2026-09-06 19:45; `struct_stats.py`, 66 s BTC / 70 s ETH)

Compared with REPLAY-6M §7 (v1.1 tree, window shifted back by one day) every difference is a window-shift difference: the trend/BOS/CHoCH/displacement/zone/sweep code paths are untouched by D-74/D-75 (D-86). D-75 adds `detect_leg`/`leg_at` beside `detect`, which `struct_stats.py` does not call; its statistics are in §7.8.

### 7.1 4h / 1d trend by day

| coin | 4h up / range / down (days) | 1d up / range / down |
|---|---|---|
| BTC | 58 / 85 / 37 | 80 / 57 / 43 |
| ETH | 54 / 88 / 38 | 56 / 71 / 53 |

### 7.2 Structure events

| coin | 4h BOS up / down | 4h CHoCH up / down | 4h total | 1h BOS up / down | 1h CHoCH up / down |
|---|---|---|---|---|---|
| BTC | 44 / 34 | 14 / 12 | 104 | 136 / 142 | 62 / 53 |
| ETH | 41 / 30 | 12 / 11 | 94 | 143 / 144 | 51 / 55 |

### 7.3 1h displacements (v1.1 `detect`: single or two-candle)

| coin | n | up / down | two-candle | grade histogram 0.0 … 1.0 |
|---|---|---|---|---|
| BTC | 633 | 313 / 320 | 362 | 14 · 67 · 76 · 100 · 70 · 108 · 74 · 46 · 36 · 25 · 17 |
| ETH | 570 | 294 / 276 | 310 | 19 · 59 · 66 · 76 · 76 · 92 · 71 · 39 · 39 · 21 · 12 |

### 7.4 Zones (order blocks / FVGs, state at window end)

| coin | 1h OB (fresh / tested / broken) | 1h FVG (open / half / filled) | 4h OB | 4h FVG |
|---|---|---|---|---|
| BTC | 421 (15 / 11 / 395) | 452 (15 / 3 / 434) | 103 (8 / 7 / 88) | 101 (5 / 3 / 93) |
| ETH | 365 (18 / 13 / 334) | 400 (17 / 4 / 379) | 95 (9 / 5 / 81) | 102 (5 / 2 / 95) |

Zone grade histograms 0.0 … 1.0 — BTC 1h: 20 · 91 · 98 · 140 · 102 · 162 · 100 · 61 · 49 · 27 · 23; BTC 4h: 11 · 12 · 32 · 26 · 41 · 29 · 11 · 15 · 17 · 4 · 6; ETH 1h: 31 · 82 · 92 · 106 · 110 · 135 · 81 · 46 · 37 · 29 · 16; ETH 4h: 7 · 13 · 20 · 29 · 26 · 28 · 36 · 18 · 10 · 5 · 5.

### 7.5 Sessions

| coin | Asia range / ATR median / mean | days in 0.8–4.0 | days ≥ 4.0 | London high raids (raided / held / reclaimed / continued) | London low | NY high | NY low |
|---|---|---|---|---|---|---|---|
| BTC | 5.57 / 5.84 | 12 | 168 | 91 / 58 / 27 / 48 | 76 / 36 / 15 / 51 | 210 / 87 / 37 / 166 | 184 / 57 / 22 / 150 |
| ETH | 5.43 / 5.72 | 25 | 155 | 98 / 65 / 31 / 62 | 70 / 32 / 15 / 45 | 208 / 94 / 34 / 165 | 183 / 66 / 22 / 154 |

The Asia-range gate of M5 (0.8–4.0 × ATR) passes on 12 (BTC) / 25 (ETH) of 180 days — same finding as v1.1: `range_bad` is M5's dominant veto (§2).

### 7.6 Weekly open (M6 population)

| coin | long: losses / reclaimed / Mon 12:00–Thu / depth ≥ 0.8 | short: same |
|---|---|---|
| BTC | 18 / 13 / 6 / 10 | 22 / 18 / 9 / 12 |
| ETH | 19 / 15 / 8 / 8 | 23 / 18 / 14 / 15 |

### 7.7 Sweeps (M1 population)

| coin | long sweeps: total / reclaimed / depth < 0.5 / third+ | short: same | unique levels swept |
|---|---|---|---|
| BTC | 2,810 / 1,050 / 447 / 2,202 | 3,621 / 1,386 / 624 / 2,825 | 6,431 |
| ETH | 2,665 / 1,111 / 411 / 2,019 | 3,257 / 1,273 / 539 / 2,529 | 5,922 |

Level types swept — BTC: pwh 938, asia_high 867, pdh 767, asia_low 708, pdl 617, pwl 615, london_high 525, london_low 478, equal_highs_1h 194, equal_lows_1h 188, equal_highs_4h 176, 4h_ob_top 154, 4h_ob_bottom 117, equal_lows_4h 87; ETH: asia_high 870, pwh 713, asia_low 682, pdh 662, pdl 605, pwl 556, london_high 542, london_low 490, equal_highs_1h 255, equal_lows_1h 202, equal_highs_4h 109, 4h_ob_top 106, 4h_ob_bottom 80, equal_lows_4h 50. Depth histogram (< 0.2 / < 0.3 / < 0.5 / < 1.0 / ≥ 1.0 ATR): BTC 449 / 385 / 570 / 857 / 4,170; ETH 502 / 344 / 528 / 778 / 3,770. Reclaim-quality histogram 0.0 … 1.0: BTC 254 · 255 · 273 · 276 · 307 · 301 · 277 · 251 · 169 · 71 · 2; ETH 239 · 244 · 281 · 332 · 338 · 316 · 268 · 205 · 109 · 51 · 1.

### 7.8 D-75 displacement legs (new; `leg_stats_v12.py`, 1h, 4,320 candles per coin, best leg of length 1–3 per direction at each candle)

| coin | v1.1 `detect` displacements | legs found | by best length 1 / 2 / 3 | up / down | grade histogram 0.0 … 1.0 | qualify ONLY as 2- / 3-candle | mean grade gain when a longer leg beats the single candle (n) |
|---|---|---|---|---|---|---|---|
| BTC | 633 | 832 | 141 / 297 / 394 | 419 / 413 | 13 · 52 · 72 · 103 · 77 · 126 · 102 · 93 · 88 · 62 · 44 | 220 / 341 | +0.30 (130) |
| ETH | 570 | 775 | 133 / 259 / 383 | 407 / 368 | 16 · 53 · 75 · 74 · 90 · 119 · 98 · 94 · 76 · 47 · 33 | 192 / 323 | +0.284 (127) |

D-75 roughly adds 561 (BTC) / 515 (ETH) leg-qualified breaks that no single candle qualified for, and where both a single candle and a longer leg qualify the leg's grade is on average 0.28–0.30 higher (its combined range and net body are larger). That is the whole of D-75's measurable effect: it triples M2's setup rows (8 → 24) but, per §2.2, the binding constraints downstream (FVG inside the leg, stale zone, stop_too_wide) leave M2 at 0 takes.

## 8. M6 — exit reasons and the thesis_failed counterfactual (identical in both passes)

Exit reasons of the 15 filled + exited M6 trades: thesis_failed 8, stop_be 2, stop 2, target 2, time_stop 1.

| trade | signal ts | coin | dir | R at thesis_failed | R if held to stop / target | held-to | held exit ts |
|---|---|---|---|---|---|---|---|
| 475 | 2026-05-20 10:00 | BTC | long | −0.21 | −1.13 | stop | 2026-05-20 13:44 |
| 500 | 2026-06-16 20:00 | BTC | short | −0.38 | +0.55 | stop_be | 2026-06-17 15:59 |
| 528 | 2026-07-07 03:00 | BTC | short | −0.74 | +0.41 | stop_be | 2026-07-09 07:59 |
| 575 | 2026-09-03 03:00 | BTC | long | +0.24 | +3.39 | target | 2026-09-03 19:14 |
| 429 | 2026-04-07 02:00 | ETH | short | −0.08 | −1.04 | stop | 2026-04-07 21:14 |
| 464 | 2026-05-07 03:00 | ETH | short | −0.31 | +0.67 | time_stop | 2026-05-08 20:14 |
| 486 | 2026-05-26 01:00 | ETH | short | −0.11 | −1.07 | stop | 2026-05-26 10:29 |
| 516 | 2026-06-30 13:00 | ETH | short | −0.01 | −1.05 | stop | 2026-07-01 03:14 |

Mean R at the moment thesis_failed fired **−0.200** vs mean R had the trade been held to stop or target **+0.091** (n = 8): exiting was better in 4 of 8 (the four that would have hit the full stop, saving 0.92–1.04 R each) and worse in 4 (three would have reached break-even-stop or the time-stop in profit; one would have reached the +3.39 R target). Net −0.292 R per trade against the in-trade checks on this sample — **hurting**, but n = 8 with one +3.39 R outlier decides the sign (without trade 575 the means are −0.263 vs −0.380: helping). Recorded, no change (D-84); the check stays as documented in doc 16 and is re-read on the calibrated replay.

## 9. Feed presence per model (fraction of replay boundaries where each required feed was present; mean of BTC / ETH)

| model | required feeds |
|---|---|
| M1 | candles 100 %, cohort 1.8 %, events 1.3 %, gauge 1.8 %, liq 16.8 %, liqmap 16.8 %, oi 16.9 %, taker 1.8 % |
| M2 | candles 100 %, events 1.3 %, gauge 1.8 %, liq 16.8 %, oi 16.9 %, taker 1.8 % |
| M3 | candles 100 %, book 1.8 %, cohort 1.8 %, events 1.3 %, gauge 1.8 %, liqmap 16.8 %, oi 16.9 %, taker 1.8 % |
| M4 | candles 100 %, cohort 1.8 %, events 1.3 %, gauge 1.8 %, liqmap 16.8 %, oi 16.9 %, taker 1.8 % |
| M5 | candles 100 %, cohort 1.8 %, events 1.3 %, gauge 1.8 %, liq 16.8 %, oi 16.9 %, taker 1.8 % |
| M6 | candles 100 %, cohort 1.8 %, events 1.3 %, gauge 1.8 %, oi 16.9 %, taker 1.8 % |

Identical in both passes (feed presence is a property of the data, not the fill model). Read together with §5: for 83 % of the window every model except the structure-only parts ran on candles alone; the Mind reasons that make M1/M3/M5/M6 differ from "structure + level" were either excluded (D-80 set) or scored 0.

## 10. Calibration history, NULL count, 0xArchive span and live_coverage

### 10.1 Point-in-time history used by the replay (`strat_calibration_hist` in both replay DBs)

| coin | key | as-of days | NULL days | first as-of | last as-of | first non-NULL | window min / max d |
|---|---|---|---|---|---|---|---|
| BTC | band_p80 | 182 | 182 | 2026-03-09 00:00 | 2026-09-06 00:00 | – | 0.0 / 29.5 |
| BTC | liq_5m_p90_long | 182 | 182 | 2026-03-09 00:00 | 2026-09-06 00:00 | – | 0.0 / 29.5 |
| BTC | liq_5m_p90_short | 182 | 182 | 2026-03-09 00:00 | 2026-09-06 00:00 | – | 0.0 / 29.5 |
| ETH | band_p80 | 182 | 182 | 2026-03-09 00:00 | 2026-09-06 00:00 | – | 0.0 / 29.5 |
| ETH | liq_5m_p90_long | 182 | 182 | 2026-03-09 00:00 | 2026-09-06 00:00 | – | 0.0 / 29.5 |
| ETH | liq_5m_p90_short | 182 | 182 | 2026-03-09 00:00 | 2026-09-06 00:00 | – | 0.0 / 29.5 |

NULL calibration lookups: **34,562 in `/root/replay_v12_adverse.out` and 34,562 in `/root/replay_v12_neutral.out`** (one per boundary per coin). The point-in-time mechanism worked as specified — it refused to compute a percentile from less than 30 days and never used future data — and the data made every day fail the minimum. The first as-of day that can be non-NULL is 2026-09-07 (tape from 2026-08-07 12:08 → 30.5 d before 2026-09-07 00:00); on prod that row now exists: `strat_calibration_hist` holds as-of 2026-09-06 (3 keys per coin, NULL) and as-of 2026-09-07 (3 keys per coin, populated) written by the daily calibration job at 2026-09-07 00:16 UTC.

### 10.2 0xArchive load, rows per coin per month, span (prod `strat_liquidations`, read 2026-09-07 01:17 UTC)

| coin | source | 2026-08 | 2026-09 | total | first | last |
|---|---|---|---|---|---|---|
| BTC | oxarchive | 93,742 | 17,245 | 110,987 | 2026-08-07 12:08:13 | 2026-09-06 23:46:44 |
| ETH | oxarchive | 25,996 | 3,071 | 29,067 | 2026-08-07 12:08:13 | 2026-09-06 23:46:32 |
| BTC | live (copy-tracker userFills) | 4,526 | 677 | 5,203 | 2026-08-11 14:52:07 | 2026-09-06 23:43:16 |
| ETH | live | 1,992 | 184 | 2,176 | 2026-08-11 15:37:09 | 2026-09-06 23:46:32 |

Span confirmed: **30.5 days, not 180** (2026-08-07 12:08 → 2026-09-06 23:46 UTC). The 215-day load added 304 new rows after dedupe (everything older than the plan boundary was refused with 403; §0). Months 2026-03 … 2026-07: **0 rows** — not loaded, not loadable on this plan.

### 10.3 Calibration values on prod (daily job, computed 2026-09-07 00:16 UTC, window 30.51 d — each row carries the note "window 30.5d < 180d (longest available)")

| coin | key | value | sample count |
|---|---|---|---|
| BTC | band_p80 | $21,325,600 | 147,249 |
| BTC | liq_5m_p90_long | $1,944,590 | 1,816 |
| BTC | liq_5m_p90_short | $2,968,530 | 2,598 |
| ETH | band_p80 | $8,127,910 | 144,191 |
| ETH | liq_5m_p90_long | $713,317 | 1,069 |
| ETH | liq_5m_p90_short | $570,390 | 1,640 |

### 10.4 live_coverage (live copy-tracker fills ÷ 0xArchive fills over the overlap)

Recomputed on the full overlap available (3.0 d trailing window as the job defines it, computed 2026-09-07 00:00 UTC): **BTC 0.0787 (n 4,121), ETH 0.1498 (n 962)** — i.e. the live feed sees 7.9 % of BTC and 15.0 % of ETH Hyperliquid liquidation notional. The replay seed at 2026-09-06 19:37 UTC was BTC 0.0780 (n 5,561) / ETH 0.1416 (n 961). The 180-day overlap the order asked for does not exist (the live feed starts 2026-08-11, the archive 2026-08-07).

## 11. Findings and decisions

1. **The ordered result was not produced, and the reason is a plan tier, not code** (§0, §10; D-81). Every v1.2 mechanism — 180-day loader with dedupe, point-in-time calibration with the 30-day minimum, dual fill model, extended statistics — ran end to end; the data stopped at 30.5 days. Zero NULL lookups is confirmed FALSE: 34,562 per pass.
2. **D-74 (M4) and D-75 (M2) changed the funnel, not the outcomes** (§2.1, §2.2, §7.8): M4 still 0 setups (ETH now passes the extension gate in 5 episodes and fails on bounce/zone/displacement/OI; BTC never exceeds 2 consecutive BOS or 4.0 % in-trend); M2 setup rows 8 → 24, 0 takes (stop_too_wide on all 21 vetoed rows, 3 non-vetoed at 0.18–0.25). No tuning (D-85).
3. **The neutral fill model changed two M1 candles and the replay changed 438 convictions** (§3.2; D-82): recent_form and the evaluate-before-manage order inside a boundary turn a 2-trade difference into a −3.25 R net difference dominated by one +4.90 R target present only in the adverse pass. Reported side by side as ordered; the adverse pass remains primary. The 0.55 line and recent_form are frozen by the order.
4. **D-80 made M3 (55 → 14) and M6 (21 → 18) stricter than v1.1 without any weight change** (§5; D-83): keeping book/gauge reasons in the denominator when those feeds are absent is the correct reading of "unavailable handling on (cohort only, and taker delta where absent)" and is what live does. M1 and M5 are identical to v1.1.
5. **No model is positive over the window under the fallback normaliser** (§3): M1 −0.351 / −0.376 R (n 129), M3 −0.220 (n 9), M5 −0.437 (n 12), M6 −0.030 (n 15); higher conviction buckets are not better (M1 0.7–0.8 buckets are the worst at −0.53 to −0.62). This is the same structure-only picture as REPLAY-6M and carries the same caveat: 83 % of the window has no tape, no OI, and 98 % has no taker/cohort/book/gauge; M1 evaluated-fuel takes are 7 of 129.
6. **M6 in-trade checks: −0.292 R per trade against them on n = 8, sign decided by one trade** (§8; D-84). No change.
7. **Structure statistics are unchanged by v1.2** apart from the one-day window shift (§7; D-86).
8. What the next run needs, in order: 0xArchive Build plan or a Data Catalog purchase of 2026-03-09 → 2026-08-07 for BTC and ETH liquidation fills (owner) → `oxarchive_load_v12.py` (dedupe is by fill identity, safe to re-run) → `v12_setup.py` (rebuilds the map and the as-of history) → both `replay_v12.py` passes → `replay_stats_v12.py`. Expected first non-NULL as-of day = load start + 30 d.

Decisions recorded from this report: D-82 (neutral divergence mechanism, unchanged), D-83 (M3/M6 take drop is D-80 semantics, no tuning), D-84 (M6 in-trade checks, no change), D-85 (M4/M2 under D-74/D-75, no tuning), D-86 (structure stats unchanged) — `docs/models/DECISIONS.md`.

## 12. Appendix — every take (adverse pass; neutral differences listed first)

### 12.0 Neutral pass — the five M1 differences

| signal | coin | dir | signal ts | adverse | neutral |
|---|---|---|---|---|---|
| 479797 | BTC | long | 2026-05-16 07:15 | stop −1.158 | stop_be −0.050 (both-inside candle, target nearer the open) |
| 490093 | BTC | short | 2026-05-25 05:45 | stop −1.546 | stop_be +0.164 (both-inside candle) |
| 491647 | ETH | short | 2026-05-26 14:00 | not taken (conviction 0.45) | taken at 0.56 → stop −1.157 at 14:20 |
| 491671 | ETH | short | 2026-05-26 14:30 | taken at 0.64 → **target +4.903** | fired at 0.80, no trade (491647 still open inside the boundary) |
| 492829 | BTC | long | 2026-05-27 15:30 | taken at 0.61 → cancelled unfilled | not taken (conviction 0.49) |

All other 184 trades (M1 remainder, M3, M5, M6) are identical in signal, fill, exit reason and R. The tables below are the adverse pass, verbatim from `/root/replay_stats_adverse.md`.

### 12.1 M1 BTC

| trade | signal ts | dir | tier | conviction | filled | exit_reason | R | hold min | day type | excluded reasons |
|---|---|---|---|---|---|---|---|---|---|---|
| 396 | 2026-03-19 13:00 | long | full | 0.828 | yes | stop_be | 0.50 | 80 | range | fuel,cleared,delta_flip,absorption,cohort |
| 399 | 2026-03-20 14:00 | long | full | 0.799 | yes | stop | -1.12 | 5 | range | fuel,cleared,delta_flip,absorption,cohort |
| 403 | 2026-03-22 20:15 | long | half | 0.699 | yes | stop | -1.24 | 30 | range | fuel,cleared,delta_flip,absorption,cohort |
| 404 | 2026-03-23 13:45 | short | half | 0.667 | yes | stop | -1.09 | 5 | range | fuel,cleared,delta_flip,absorption,cohort |
| 406 | 2026-03-24 08:00 | short | full | 0.733 | yes | stop_be | 0.17 | 95 | range | fuel,cleared,delta_flip,absorption,cohort |
| 408 | 2026-03-25 05:15 | short | half | 0.605 | yes | stop_be | -0.06 | 15 | range | fuel,cleared,delta_flip,absorption,cohort |
| 410 | 2026-03-26 06:00 | long | half | 0.616 | yes | stop | -1.17 | 5 | range | fuel,cleared,delta_flip,absorption,cohort |
| 411 | 2026-03-26 08:45 | long | half | 0.599 | yes | stop_be | 0.28 | 15 | range | fuel,cleared,delta_flip,absorption,cohort |
| 412 | 2026-03-26 15:15 | long | full | 1.040 | yes | stop | -1.11 | 5 | range | fuel,cleared,delta_flip,absorption,cohort |
| 415 | 2026-03-28 08:45 | short | full | 0.750 | yes | stop_be | -0.29 | 0 | range | fuel,cleared,delta_flip,absorption,cohort |
| 417 | 2026-03-30 08:30 | short | full | 0.928 | yes | stop_be | 0.37 | 165 | range | fuel,cleared,delta_flip,absorption,cohort |
| 418 | 2026-03-31 09:30 | long | full | 0.828 | yes | stop | -1.18 | 20 | range | fuel,cleared,delta_flip,absorption,cohort |
| 421 | 2026-03-31 17:30 | short | full | 0.815 | yes | stop_be | -0.38 | 0 | range | fuel,cleared,delta_flip,absorption,cohort |
| 424 | 2026-04-04 16:15 | short | half | 0.648 | yes | stop | -1.75 | 0 | range | fuel,cleared,delta_flip,absorption,cohort |
| 432 | 2026-04-08 13:15 | short | full | 0.799 | yes | stop | -1.10 | 5 | range | fuel,cleared,delta_flip,absorption,cohort |
| 433 | 2026-04-10 19:30 | short | half | 0.567 | yes | thesis_failed | -0.92 | 25 | range | fuel,cleared,delta_flip,absorption,cohort |
| 436 | 2026-04-13 09:00 | long | half | 0.633 | no | cancelled:entry_expired_no_fvg | - | - | range | fuel,cleared,delta_flip,absorption,cohort |
| 438 | 2026-04-14 07:15 | short | half | 0.644 | yes | stop_be | -0.04 | 20 | range | fuel,cleared,delta_flip,absorption,cohort |
| 440 | 2026-04-16 19:45 | short | half | 0.636 | yes | stop | -1.20 | 5 | range | fuel,cleared,delta_flip,absorption,cohort |
| 443 | 2026-04-20 14:30 | short | half | 0.625 | yes | stop_be | 0.40 | 50 | range | fuel,cleared,delta_flip,absorption,cohort |
| 444 | 2026-04-20 18:30 | short | full | 0.835 | yes | stop | -1.27 | 15 | range | fuel,cleared,delta_flip,absorption,cohort |
| 445 | 2026-04-21 07:30 | short | full | 0.894 | yes | stop | -1.21 | 50 | range | fuel,cleared,delta_flip,absorption,cohort |
| 451 | 2026-04-24 09:15 | long | half | 0.601 | yes | time_stop | 2.33 | 240 | range | fuel,cleared,delta_flip,absorption,cohort |
| 452 | 2026-04-27 15:15 | long | full | 0.983 | yes | stop | -1.14 | 5 | range | fuel,cleared,delta_flip,absorption,cohort |
| 455 | 2026-04-29 18:15 | long | full | 0.757 | yes | stop_be | 0.00 | 120 | range | fuel,cleared,delta_flip,absorption,cohort |
| 456 | 2026-05-01 08:30 | short | full | 0.821 | yes | dead_trade | 0.13 | 150 | range | fuel,cleared,delta_flip,absorption,cohort |
| 457 | 2026-05-01 13:15 | short | full | 0.924 | yes | stop | -1.15 | 20 | range | fuel,cleared,delta_flip,absorption,cohort |
| 463 | 2026-05-07 00:00 | long | half | 0.622 | yes | stop | -1.51 | 15 | range | fuel,cleared,delta_flip,absorption,cohort |
| 466 | 2026-05-12 11:15 | long | half | 0.584 | yes | stop | -1.43 | 5 | range | fuel,cleared,delta_flip,absorption,cohort |
| 467 | 2026-05-12 14:00 | long | full | 0.789 | yes | stop_be | -0.06 | 20 | range | fuel,cleared,delta_flip,absorption,cohort |
| 468 | 2026-05-12 15:45 | long | half | 0.620 | yes | stop_be | -0.04 | 10 | range | fuel,cleared,delta_flip,absorption,cohort |
| 470 | 2026-05-13 13:45 | long | full | 0.733 | yes | stop | -1.19 | 5 | range | fuel,cleared,delta_flip,absorption,cohort |
| 471 | 2026-05-14 16:15 | short | full | 0.737 | yes | stop_be | 0.05 | 30 | range | fuel,cleared,delta_flip,absorption,cohort |
| 472 | 2026-05-16 07:15 | long | full | 1.056 | yes | stop | -1.16 | 5 | range | fuel,cleared,delta_flip,absorption,cohort |
| 476 | 2026-05-21 08:15 | short | full | 1.028 | yes | time_stop | 2.88 | 240 | range | fuel,cleared,delta_flip,absorption,cohort |
| 478 | 2026-05-25 02:15 | short | full | 0.789 | yes | dead_trade | 0.14 | 135 | range | fuel,cleared,delta_flip,absorption,cohort |
| 479 | 2026-05-25 05:00 | short | half | 0.556 | yes | stop | -1.34 | 20 | range | fuel,cleared,delta_flip,absorption,cohort |
| 480 | 2026-05-25 05:45 | short | half | 0.631 | yes | stop | -1.55 | 20 | range | fuel,cleared,delta_flip,absorption,cohort |
| 483 | 2026-05-25 15:00 | short | full | 0.711 | yes | stop | -1.38 | 5 | range | fuel,cleared,delta_flip,absorption,cohort |
| 490 | 2026-05-27 14:45 | long | half | 0.613 | no | cancelled:entry_expired_no_fvg | - | - | range | fuel,cleared,delta_flip,absorption,cohort |
| 491 | 2026-05-29 16:30 | short | full | 0.735 | yes | stop | -1.15 | 0 | range | fuel,cleared,delta_flip,absorption,cohort |
| 493 | 2026-06-01 07:30 | long | half | 0.674 | yes | stop | -1.30 | 5 | range | fuel,cleared,delta_flip,absorption,cohort |
| 494 | 2026-06-02 07:45 | long | full | 0.714 | yes | stop | -1.14 | 60 | range | fuel,cleared,delta_flip,absorption,cohort |
| 497 | 2026-06-11 04:00 | short | half | 0.625 | yes | dead_trade | -0.49 | 135 | range | fuel,cleared,delta_flip,absorption,cohort |
| 499 | 2026-06-12 10:00 | short | full | 0.727 | yes | stop_be | 0.38 | 60 | range | fuel,cleared,delta_flip,absorption,cohort |
| 501 | 2026-06-18 04:15 | long | half | 0.633 | yes | thesis_failed | -0.80 | 45 | range | fuel,cleared,delta_flip,absorption,cohort |
| 503 | 2026-06-18 16:15 | long | full | 0.870 | yes | time_stop | 1.57 | 240 | range | fuel,cleared,delta_flip,absorption,cohort |
| 504 | 2026-06-19 09:15 | long | half | 0.616 | yes | dead_trade | 0.19 | 195 | range | fuel,cleared,delta_flip,absorption,cohort |
| 506 | 2026-06-23 00:45 | long | half | 0.600 | yes | stop_be | -0.18 | 0 | range | fuel,cleared,delta_flip,absorption,cohort |
| 510 | 2026-06-23 10:45 | long | full | 0.708 | yes | stop_be | -0.03 | 110 | range | fuel,cleared,delta_flip,absorption,cohort |
| 512 | 2026-06-27 08:15 | short | half | 0.631 | yes | time_stop | 1.18 | 240 | range | fuel,cleared,delta_flip,absorption,cohort |
| 513 | 2026-06-29 10:15 | short | half | 0.596 | yes | stop_be | 0.42 | 80 | range | fuel,cleared,delta_flip,absorption,cohort |
| 522 | 2026-07-04 00:15 | short | half | 0.558 | yes | dead_trade | 0.37 | 210 | range | fuel,cleared,delta_flip,absorption,cohort |
| 529 | 2026-07-10 00:00 | short | half | 0.681 | no | cancelled:entry_expired_no_fvg | - | - | range | fuel,cleared,delta_flip,absorption,cohort |
| 530 | 2026-07-10 02:00 | short | half | 0.640 | yes | stop | -1.12 | 50 | range | fuel,cleared,delta_flip,absorption,cohort |
| 531 | 2026-07-12 06:45 | long | half | 0.616 | no | cancelled:entry_expired_no_fvg | - | - | range | fuel,cleared,delta_flip,absorption,cohort |
| 534 | 2026-07-14 12:45 | short | full | 0.913 | yes | stop | -1.10 | 5 | range | fuel,cleared,delta_flip,absorption,cohort |
| 539 | 2026-07-17 05:45 | long | half | 0.610 | yes | stop | -1.20 | 5 | range | fuel,cleared,delta_flip,absorption,cohort |
| 540 | 2026-07-17 13:45 | long | full | 0.982 | yes | target | 2.16 | 25 | range | fuel,cleared,delta_flip,absorption,cohort |
| 542 | 2026-07-20 16:00 | short | full | 0.899 | yes | stop | -1.14 | 125 | range | fuel,cleared,delta_flip,absorption,cohort |
| 544 | 2026-07-21 13:30 | short | full | 0.885 | yes | stop_be | 0.13 | 15 | range | fuel,cleared,delta_flip,absorption,cohort |
| 548 | 2026-07-24 13:00 | long | full | 0.896 | yes | stop | -1.30 | 5 | range | fuel,cleared,delta_flip,absorption,cohort |
| 551 | 2026-07-28 00:00 | long | half | 0.614 | yes | stop | -1.30 | 0 | range | fuel,cleared,delta_flip,absorption,cohort |
| 552 | 2026-07-29 20:00 | long | half | 0.669 | yes | stop | -1.16 | 5 | range | fuel,cleared,delta_flip,absorption,cohort |
| 555 | 2026-08-01 18:00 | long | full | 0.733 | yes | stop | -1.42 | 5 | range | fuel,cleared,delta_flip,absorption,cohort |
| 558 | 2026-08-03 04:00 | long | half | 0.552 | yes | stop | -1.22 | 65 | range | fuel,cleared,delta_flip,absorption,cohort |
| 561 | 2026-08-06 05:15 | short | half | 0.589 | yes | dead_trade | -0.47 | 135 | range | fuel,cleared,delta_flip,absorption,cohort |
| 562 | 2026-08-06 09:00 | short | half | 0.684 | yes | time_stop | 4.42 | 240 | range | fuel,cleared,delta_flip,absorption,cohort |
| 568 | 2026-08-14 15:00 | long | half | 0.553 | yes | target | 1.37 | 40 | range | delta_flip,absorption,cohort |
| 572 | 2026-08-24 07:30 | short | half | 0.653 | yes | stop_be | -0.12 | 0 | range | delta_flip,absorption,cohort |

### 12.1 M1 ETH

| trade | signal ts | dir | tier | conviction | filled | exit_reason | R | hold min | day type | excluded reasons |
|---|---|---|---|---|---|---|---|---|---|---|
| 387 | 2026-03-11 07:45 | long | full | 1.044 | yes | time_stop | 1.07 | 240 | range | fuel,cleared,delta_flip,absorption,cohort |
| 392 | 2026-03-11 17:30 | short | full | 0.844 | yes | stop_be | 0.50 | 80 | range | fuel,cleared,delta_flip,absorption,cohort |
| 393 | 2026-03-13 13:00 | short | full | 0.888 | yes | stop | -1.09 | 5 | range | fuel,cleared,delta_flip,absorption,cohort |
| 394 | 2026-03-13 13:30 | short | full | 0.766 | yes | stop | -1.04 | 50 | range | fuel,cleared,delta_flip,absorption,cohort |
| 395 | 2026-03-18 11:30 | long | full | 0.972 | yes | stop | -1.10 | 5 | range | fuel,cleared,delta_flip,absorption,cohort |
| 397 | 2026-03-19 13:15 | long | full | 0.804 | yes | stop | -1.11 | 5 | range | fuel,cleared,delta_flip,absorption,cohort |
| 407 | 2026-03-24 08:00 | short | half | 0.616 | yes | stop_be | 0.11 | 35 | range | fuel,cleared,delta_flip,absorption,cohort |
| 409 | 2026-03-25 11:30 | short | full | 0.743 | yes | stop | -1.23 | 5 | range | fuel,cleared,delta_flip,absorption,cohort |
| 413 | 2026-03-27 10:45 | long | full | 1.028 | yes | stop | -1.05 | 5 | range | fuel,cleared,delta_flip,absorption,cohort |
| 414 | 2026-03-28 08:00 | short | full | 0.718 | yes | time_stop | 2.04 | 240 | range | fuel,cleared,delta_flip,absorption,cohort |
| 416 | 2026-03-30 08:00 | short | full | 0.953 | yes | stop | -1.09 | 35 | range | fuel,cleared,delta_flip,absorption,cohort |
| 419 | 2026-03-31 10:00 | long | full | 0.858 | yes | stop_be | 0.10 | 35 | range | fuel,cleared,delta_flip,absorption,cohort |
| 420 | 2026-03-31 14:30 | short | half | 0.698 | yes | stop_be | 0.32 | 125 | range | fuel,cleared,delta_flip,absorption,cohort |
| 425 | 2026-04-05 07:15 | long | full | 0.857 | yes | stop | -1.43 | 15 | range | fuel,cleared,delta_flip,absorption,cohort |
| 431 | 2026-04-07 11:00 | long | half | 0.646 | yes | stop | -1.15 | 5 | range | fuel,cleared,delta_flip,absorption,cohort |
| 434 | 2026-04-11 18:45 | short | half | 0.559 | yes | stop | -1.05 | 20 | range | fuel,cleared,delta_flip,absorption,cohort |
| 435 | 2026-04-12 14:00 | long | half | 0.572 | yes | stop | -1.28 | 35 | range | fuel,cleared,delta_flip,absorption,cohort |
| 437 | 2026-04-13 09:00 | long | half | 0.616 | no | cancelled:entry_expired_no_fvg | - | - | range | fuel,cleared,delta_flip,absorption,cohort |
| 441 | 2026-04-17 13:00 | short | half | 0.657 | yes | stop | -1.09 | 5 | range | fuel,cleared,delta_flip,absorption,cohort |
| 442 | 2026-04-19 07:15 | long | full | 0.734 | yes | stop | -1.25 | 5 | range | fuel,cleared,delta_flip,absorption,cohort |
| 447 | 2026-04-21 15:00 | long | half | 0.639 | yes | stop | -1.09 | 5 | range | fuel,cleared,delta_flip,absorption,cohort |
| 449 | 2026-04-23 09:15 | long | half | 0.593 | yes | stop | -1.09 | 50 | range | fuel,cleared,delta_flip,absorption,cohort |
| 450 | 2026-04-23 17:30 | long | half | 0.560 | yes | stop | -1.15 | 5 | range | fuel,cleared,delta_flip,absorption,cohort |
| 453 | 2026-04-28 13:15 | long | full | 0.861 | yes | stop_be | -0.02 | 20 | range | fuel,cleared,delta_flip,absorption,cohort |
| 454 | 2026-04-28 14:30 | long | half | 0.696 | yes | stop | -1.23 | 5 | range | fuel,cleared,delta_flip,absorption,cohort |
| 458 | 2026-05-01 13:15 | short | full | 0.828 | yes | stop | -1.17 | 20 | range | fuel,cleared,delta_flip,absorption,cohort |
| 459 | 2026-05-03 12:15 | short | full | 0.757 | yes | dead_trade | 0.06 | 135 | range | fuel,cleared,delta_flip,absorption,cohort |
| 460 | 2026-05-06 09:00 | short | full | 1.099 | yes | stop | -1.15 | 35 | range | fuel,cleared,delta_flip,absorption,cohort |
| 461 | 2026-05-06 16:45 | long | full | 0.712 | yes | thesis_failed | -0.54 | 25 | range | fuel,cleared,delta_flip,absorption,cohort |
| 474 | 2026-05-20 05:15 | short | half | 0.567 | yes | stop | -1.16 | 20 | range | fuel,cleared,delta_flip,absorption,cohort |
| 477 | 2026-05-23 21:15 | short | half | 0.642 | no | cancelled:entry_expired | - | - | range | fuel,cleared,delta_flip,absorption,cohort |
| 481 | 2026-05-25 08:15 | short | full | 0.764 | yes | stop | -1.38 | 15 | range | fuel,cleared,delta_flip,absorption,cohort |
| 484 | 2026-05-25 15:30 | short | half | 0.622 | no | cancelled:entry_expired_no_fvg | - | - | range | fuel,cleared,delta_flip,absorption,cohort |
| 487 | 2026-05-26 14:30 | short | half | 0.641 | yes | target | 4.90 | 85 | range | fuel,cleared,delta_flip,absorption,cohort |
| 488 | 2026-05-27 14:00 | long | full | 0.917 | yes | stop_be | 0.49 | 165 | range | fuel,cleared,delta_flip,absorption,cohort |
| 492 | 2026-06-01 02:15 | long | half | 0.565 | no | cancelled:entry_expired_no_fvg | - | - | range | fuel,cleared,delta_flip,absorption,cohort |
| 496 | 2026-06-09 15:00 | long | half | 0.690 | yes | stop_be | 0.14 | 0 | range | fuel,cleared,delta_flip,absorption,cohort |
| 498 | 2026-06-12 09:45 | short | half | 0.552 | yes | stop | -1.37 | 5 | range | fuel,cleared,delta_flip,absorption,cohort |
| 505 | 2026-06-22 14:15 | short | half | 0.698 | no | cancelled:entry_expired | - | - | range | fuel,cleared,delta_flip,absorption,cohort |
| 509 | 2026-06-23 09:30 | long | full | 0.716 | yes | stop | -1.29 | 0 | range | fuel,cleared,delta_flip,absorption,cohort |
| 511 | 2026-06-24 13:15 | long | full | 0.864 | yes | stop | -1.09 | 5 | range | fuel,cleared,delta_flip,absorption,cohort |
| 514 | 2026-06-29 17:30 | short | full | 0.717 | yes | stop | -1.09 | 20 | range | fuel,cleared,delta_flip,absorption,cohort |
| 517 | 2026-07-01 14:30 | short | half | 0.614 | yes | stop_be | 0.20 | 0 | range | fuel,cleared,delta_flip,absorption,cohort |
| 518 | 2026-07-02 13:15 | short | half | 0.553 | yes | stop | -1.14 | 5 | range | fuel,cleared,delta_flip,absorption,cohort |
| 519 | 2026-07-02 14:30 | short | half | 0.559 | no | cancelled:entry_expired_no_fvg | - | - | range | fuel,cleared,delta_flip,absorption,cohort |
| 520 | 2026-07-03 08:45 | short | half | 0.656 | yes | stop_be | 0.01 | 20 | range | fuel,cleared,delta_flip,absorption,cohort |
| 523 | 2026-07-05 02:30 | long | half | 0.563 | yes | stop_be | 0.42 | 180 | range | fuel,cleared,delta_flip,absorption,cohort |
| 524 | 2026-07-06 08:45 | long | full | 0.722 | yes | stop | -1.19 | 35 | range | fuel,cleared,delta_flip,absorption,cohort |
| 525 | 2026-07-06 13:15 | long | full | 0.724 | yes | stop | -1.16 | 20 | range | fuel,cleared,delta_flip,absorption,cohort |
| 526 | 2026-07-06 16:30 | short | half | 0.602 | yes | dead_trade | 0.14 | 145 | range | fuel,cleared,delta_flip,absorption,cohort |
| 532 | 2026-07-13 00:15 | short | half | 0.652 | yes | target | 1.52 | 205 | range | fuel,cleared,delta_flip,absorption,cohort |
| 533 | 2026-07-13 12:45 | long | half | 0.672 | yes | stop | -1.16 | 35 | range | fuel,cleared,delta_flip,absorption,cohort |
| 535 | 2026-07-14 12:45 | short | full | 0.752 | yes | stop | -1.06 | 5 | range | fuel,cleared,delta_flip,absorption,cohort |
| 538 | 2026-07-16 12:45 | long | full | 0.795 | yes | stop_be | 0.50 | 125 | range | fuel,cleared,delta_flip,absorption,cohort |
| 541 | 2026-07-17 13:45 | long | full | 0.902 | yes | stop_be | 0.34 | 80 | range | fuel,cleared,delta_flip,absorption,cohort |
| 545 | 2026-07-22 16:45 | short | half | 0.560 | yes | target | 1.20 | 115 | range | fuel,cleared,delta_flip,absorption,cohort |
| 546 | 2026-07-23 07:15 | long | full | 1.012 | yes | stop | -1.17 | 35 | range | fuel,cleared,delta_flip,absorption,cohort |
| 549 | 2026-07-24 13:15 | long | full | 0.986 | yes | stop_be | 0.02 | 20 | range | fuel,cleared,delta_flip,absorption,cohort |
| 550 | 2026-07-27 22:45 | long | half | 0.551 | yes | stop | -1.11 | 5 | range | fuel,cleared,delta_flip,absorption,cohort |
| 553 | 2026-07-29 20:00 | long | full | 0.719 | yes | stop | -1.10 | 5 | range | fuel,cleared,delta_flip,absorption,cohort |
| 556 | 2026-08-01 18:30 | long | full | 0.771 | yes | stop | -1.20 | 5 | range | fuel,cleared,delta_flip,absorption,cohort |
| 557 | 2026-08-02 18:45 | short | half | 0.577 | yes | stop_be | 0.03 | 20 | range | fuel,cleared,delta_flip,absorption,cohort |
| 559 | 2026-08-05 02:15 | short | half | 0.590 | yes | target | 1.49 | 70 | range | fuel,cleared,delta_flip,absorption,cohort |
| 564 | 2026-08-07 12:15 | short | half | 0.694 | yes | stop | -1.10 | 20 | range | delta_flip,absorption,cohort |
| 565 | 2026-08-11 07:30 | long | half | 0.555 | yes | target | 2.61 | 145 | range | delta_flip,absorption,cohort |
| 566 | 2026-08-11 15:45 | long | full | 0.861 | yes | stop_be | -0.02 | 45 | range | delta_flip,absorption,cohort |
| 567 | 2026-08-12 12:30 | short | half | 0.640 | yes | target | 3.18 | 100 | range | delta_flip,absorption,cohort |
| 569 | 2026-08-18 14:30 | short | full | 0.822 | yes | stop | -1.09 | 5 | range | delta_flip,absorption,cohort |
| 570 | 2026-08-19 07:30 | short | full | 0.730 | yes | stop_be | -0.12 | 10 | range | delta_flip,absorption,cohort |

### 12.2 M3 BTC

| trade | signal ts | dir | tier | conviction | filled | exit_reason | R | hold min | day type | excluded reasons |
|---|---|---|---|---|---|---|---|---|---|---|
| 389 | 2026-03-11 10:00 | long | half | 0.589 | no | cancelled:entry_expired | - | - | range | trap,cvd_divergence,cluster_fuel |
| 390 | 2026-03-11 12:45 | long | half | 0.621 | yes | time_stop | 11.03 | 360 | range | trap,cvd_divergence,cluster_fuel |
| 398 | 2026-03-20 13:00 | long | half | 0.584 | yes | stop | -1.70 | 30 | range | trap,cvd_divergence,cluster_fuel |
| 405 | 2026-03-23 15:15 | short | half | 0.588 | no | cancelled:entry_expired | - | - | range | trap,cvd_divergence,cluster_fuel |
| 422 | 2026-04-01 15:15 | short | half | 0.677 | yes | stop | -1.57 | 0 | range | trap,cvd_divergence,cluster_fuel |
| 423 | 2026-04-02 11:15 | long | half | 0.643 | yes | stop | -1.85 | 0 | range | trap,cvd_divergence,cluster_fuel |
| 469 | 2026-05-12 16:30 | long | half | 0.606 | yes | stop | -1.79 | 0 | range | trap,cvd_divergence,cluster_fuel |
| 495 | 2026-06-08 14:00 | short | half | 0.669 | yes | stop | -1.43 | 15 | range | trap,cvd_divergence,cluster_fuel |
| 527 | 2026-07-06 18:30 | short | full | 0.780 | no | cancelled:entry_expired | - | - | range | trap,cvd_divergence,cluster_fuel |

### 12.2 M3 ETH

| trade | signal ts | dir | tier | conviction | filled | exit_reason | R | hold min | day type | excluded reasons |
|---|---|---|---|---|---|---|---|---|---|---|
| 427 | 2026-04-06 15:00 | short | half | 0.590 | yes | stop | -1.47 | 0 | range | trap,cvd_divergence,cluster_fuel |
| 428 | 2026-04-06 16:15 | short | full | 0.756 | no | cancelled:entry_expired | - | - | range | trap,cvd_divergence,cluster_fuel |
| 462 | 2026-05-06 17:30 | long | half | 0.604 | yes | stop | -1.55 | 15 | range | trap,cvd_divergence,cluster_fuel |
| 473 | 2026-05-16 13:45 | long | half | 0.606 | no | cancelled:entry_expired | - | - | range | trap,cvd_divergence,cluster_fuel |
| 560 | 2026-08-05 15:45 | short | half | 0.556 | yes | stop | -1.65 | 15 | range | trap,cvd_divergence,cluster_fuel |

### 12.3 M5 BTC

| trade | signal ts | dir | tier | conviction | filled | exit_reason | R | hold min | day type | excluded reasons |
|---|---|---|---|---|---|---|---|---|---|---|
| 391 | 2026-03-11 13:15 | long | full | 0.908 | no | cancelled:entry_expired | - | - | range | fuel,cleared,delta_flip,cohort |
| 400 | 2026-03-20 14:00 | long | full | 0.821 | yes | stop | -1.12 | 5 | range | fuel,cleared,delta_flip,cohort |
| 401 | 2026-03-21 07:45 | short | half | 0.625 | no | cancelled:entry_expired | - | - | no_trade | fuel,cleared,delta_flip,cohort |
| 426 | 2026-04-06 14:30 | short | full | 0.848 | yes | stop | -1.22 | 5 | range | fuel,cleared,delta_flip,cohort |
| 439 | 2026-04-15 14:00 | long | full | 0.777 | yes | stop_be | 0.54 | 30 | range | fuel,cleared,delta_flip,cohort |
| 446 | 2026-04-21 14:30 | short | half | 0.648 | yes | stop | -1.15 | 5 | range | fuel,cleared,delta_flip,cohort |
| 465 | 2026-05-10 08:30 | short | half | 0.625 | yes | stop_be | -0.00 | 125 | no_trade | fuel,cleared,delta_flip,cohort |
| 521 | 2026-07-03 09:00 | short | full | 0.776 | yes | stop_be | 0.16 | 15 | range | fuel,cleared,delta_flip,cohort |
| 563 | 2026-08-06 14:45 | long | half | 0.664 | no | cancelled:entry_expired | - | - | range | fuel,cleared,delta_flip,cohort |

### 12.3 M5 ETH

| trade | signal ts | dir | tier | conviction | filled | exit_reason | R | hold min | day type | excluded reasons |
|---|---|---|---|---|---|---|---|---|---|---|
| 388 | 2026-03-11 07:45 | long | full | 0.919 | yes | time_stop | 1.69 | 195 | range | fuel,cleared,delta_flip,cohort |
| 402 | 2026-03-21 07:45 | short | half | 0.666 | no | cancelled:entry_expired | - | - | no_trade | fuel,cleared,delta_flip,cohort |
| 448 | 2026-04-21 15:00 | long | half | 0.589 | yes | stop | -1.09 | 5 | range | fuel,cleared,delta_flip,cohort |
| 482 | 2026-05-25 08:15 | short | half | 0.685 | yes | stop | -1.38 | 15 | range | fuel,cleared,delta_flip,cohort |
| 489 | 2026-05-27 14:00 | long | half | 0.635 | yes | time_stop | 1.12 | 160 | range | fuel,cleared,delta_flip,cohort |
| 537 | 2026-07-16 08:00 | long | full | 0.905 | yes | stop | -1.28 | 0 | range | fuel,cleared,delta_flip,cohort |
| 554 | 2026-08-01 07:30 | short | half | 0.687 | no | cancelled:entry_expired | - | - | no_trade | fuel,cleared,delta_flip,cohort |
| 571 | 2026-08-19 07:30 | short | half | 0.570 | yes | stop | -1.51 | 60 | range | delta_flip,cohort |
| 573 | 2026-08-24 07:30 | short | half | 0.627 | no | cancelled:entry_expired | - | - | range | delta_flip,cohort |

### 12.4 M6 BTC

| trade | signal ts | dir | tier | conviction | filled | exit_reason | R | hold min | day type | excluded reasons |
|---|---|---|---|---|---|---|---|---|---|---|
| 430 | 2026-04-07 11:00 | short | full | 0.718 | yes | stop_be | -0.47 | 0 | range | oi_commitment,delta_reclaim,cohort |
| 475 | 2026-05-20 10:00 | long | half | 0.569 | yes | thesis_failed | -0.21 | 70 | range | oi_commitment,delta_reclaim,cohort |
| 485 | 2026-05-26 01:00 | short | half | 0.641 | yes | stop | -1.68 | 330 | range | oi_commitment,delta_reclaim,cohort |
| 500 | 2026-06-16 20:00 | short | half | 0.581 | yes | thesis_failed | -0.38 | 70 | range | oi_commitment,delta_reclaim,cohort |
| 507 | 2026-06-23 07:00 | short | half | 0.578 | no | cancelled:entry_expired | - | - | range | oi_commitment,delta_reclaim,cohort |
| 515 | 2026-06-30 08:00 | short | half | 0.596 | no | cancelled:entry_expired | - | - | range | oi_commitment,delta_reclaim,cohort |
| 528 | 2026-07-07 03:00 | short | half | 0.565 | yes | thesis_failed | -0.74 | 555 | range | oi_commitment,delta_reclaim,cohort |
| 547 | 2026-07-23 19:00 | long | half | 0.661 | yes | target | 4.43 | 680 | range | oi_commitment,delta_reclaim,cohort |
| 575 | 2026-09-03 03:00 | long | full | 0.702 | yes | thesis_failed | 0.24 | 280 | range | delta_reclaim,cohort |

### 12.4 M6 ETH

| trade | signal ts | dir | tier | conviction | filled | exit_reason | R | hold min | day type | excluded reasons |
|---|---|---|---|---|---|---|---|---|---|---|
| 429 | 2026-04-07 02:00 | short | full | 0.730 | yes | thesis_failed | -0.08 | 160 | range | oi_commitment,delta_reclaim,cohort |
| 464 | 2026-05-07 03:00 | short | half | 0.574 | yes | thesis_failed | -0.31 | 130 | range | oi_commitment,delta_reclaim,cohort |
| 486 | 2026-05-26 01:00 | short | half | 0.623 | yes | thesis_failed | -0.11 | 310 | range | oi_commitment,delta_reclaim,cohort |
| 502 | 2026-06-18 16:00 | short | half | 0.666 | yes | time_stop | -0.65 | 1440 | range | oi_commitment,delta_reclaim,cohort |
| 508 | 2026-06-23 07:00 | short | half | 0.578 | no | cancelled:entry_expired | - | - | range | oi_commitment,delta_reclaim,cohort |
| 516 | 2026-06-30 13:00 | short | full | 0.727 | yes | thesis_failed | -0.01 | 190 | range | oi_commitment,delta_reclaim,cohort |
| 536 | 2026-07-14 13:00 | long | half | 0.556 | yes | stop_be | -0.62 | 0 | range | oi_commitment,delta_reclaim,cohort |
| 543 | 2026-07-20 16:00 | long | half | 0.560 | yes | target | 1.20 | 745 | range | oi_commitment,delta_reclaim,cohort |
| 574 | 2026-09-02 06:00 | long | half | 0.627 | yes | stop | -1.05 | 150 | range | delta_reclaim,cohort |
