# REPLAY-6M — M1…M6 over 180 days on BTC and ETH (spec v1.1 tree)

Owner request (2026-09-06, Part E): "Run all six models for BTC and ETH over the 180-day window using the corrected tree, calibrated normalisers and reconstructed liquidation map, feed vetoes off, unavailable handling on, the same paper fill model." Every number below is read back from the replay database (`perpl_replay` on prod: `strat_signals`, `strat_trades`, `strat_calibration`) by `/root/replay_stats.py` (output `/root/replay_stats_out.md`) and from `/root/struct_stats.py`; nothing is estimated. Doc numbers (weights, vetoes, thresholds) were not changed. Decisions taken while reading the result: D-71, D-72, D-73.

Read §1 before any number in §2–§6: for 83 % of the window the liquidation, OI, taker, gauge, book and cohort feeds did not exist, so the replay measures the corrected STRUCTURE tree with the fallback normaliser, not the calibrated Mind.

## 1. Method and what was actually replayed

| item | what was used | evidence |
|---|---|---|
| tree | the spec v1.1 tree deployed to prod the same day (A1 displacement formula, A2 swing strength / two-swing trend, M2 alternative alignment branch, calibrated normalisers, replay-only unavailable handling) copied to `/root/audit_tree` | `test_structure.py`, `test_calibration.py`, `test_unavailable.py` — 276 passed |
| window | **2026-03-10 09:30 → 2026-09-06 09:30 UTC, 17,281 boundaries × 2 coins, run time 16,776 s** — end boundary = the last closed Binance 15m candle in the store at start, start = end − 180 d | `replay_6m.log` header and final line |
| code path | `ModelEvaluator.run(now_ms=boundary+5 s)` — the production evaluator, one call per 15m boundary, both coins, all six models; `strat_signals` row per model per coin per boundary (207,372 rows); paper lifecycle through `ModelTradeManager` (post-only entries filled only when the tape trades through, taker fee on stops, 40 % partial at T1, BE, trail, T2/T3, thesis / dead-trade / time exits) | `/root/replay_6m.py`, unchanged `execution/manager.py` fill rules |
| candles | Binance USDT-M perpetual klines BTCUSDT / ETHUSDT (`strat_replay_candles`, source='binance': 15m 24,000 / 1h 5,999 / 4h 1,499 / 1d 249 rows per coin, 250 days). Daily candles are derived from 4h (D-67). The live tables were NOT read for candles | D-67 |
| open interest | Binance `openInterestHist` 5-minute, public limit 30 days → `strat_replay_oi` 8,623 rows per coin 2026-08-07 → 09-06. Before 2026-08-07 the `oi` feed is **unavailable** | D-67 |
| taker delta | the live store (`strat_trades_1m`) where present = since 2026-09-03 14:17 UTC; otherwise **unavailable** | D-67 |
| liquidation tape | 0xArchive Hyperliquid liquidation fills (`strat_liquidations`, source='oxarchive') **from 2026-08-07 12:08 UTC — the key is on the free tier, which serves the most recent 30 days only (403 `history_window_exceeded` for anything older; the loader clamps to the rolling boundary)**, plus the live tracker feed rows from 2026-08-11 (source NULL; an archive fill whose (ts, user, px) matches a live row is left to the live row). 180 days of history need the Build plan or a Data Catalog purchase — owner's decision (D-69). Before 2026-08-07 12:08 the `liq` feed is unavailable | D-69, D-70, `oxarchive_load_replay.log` |
| liquidation map | **0xArchive projected-levels POSITION SNAPSHOTS were available and were used** (`strat_liq_map_hist`, source='levels': the ~5-min snapshot at/before each 15m boundary re-bucketed onto 0.25 % bands — BTC 144,681 bands / 2,879 boundaries, ETH 141,796 / 2,879, 2026-08-07 12:00 → 09-06 11:30 UTC). The fills construction (24 h forward horizon, source='fills': BTC 43,408 / 2,795, ETH 44,612 / 2,675) was also written but is only the last fallback — readers take one source per boundary, levels > live > fills (D-69). Before 2026-08-07 12:00 the `liqmap` feed is unavailable | D-69 |
| gauge / book / events | live store where present (gauge and book since 2026-09-03 14:17 UTC; events calendar as stored); unavailable before | D-68 |
| cohort | unavailable before 2026-09-03 14:17 UTC (owner statement). The cohort was built ONCE on prod at replay start (30 wallets) and the production `cohort_signal` was used from that time on | replay patch 3 |
| calibration | `strat_calibration` recomputed daily inside the replay from the replay tables. The window is measured from the first liquidation row (2026-08-07 12:08 UTC), so every daily recompute inside the replay sees < 30 d and persists **NULL** (end state on `perpl_replay`: window 29.5 d, samples BTC long 1,782 / short 2,530 / band 142,369; ETH 1,039 / 1,542 / 139,501; `live_coverage` BTC 0.0470 / ETH 0.0424 on the DB basis) — the Snapshot falls back to the fixed 0.10 % of OI throughout. The calibrated values first exist from 2026-09-06 12:10 UTC on prod (D-69) — after the replay's last boundary. Before 2026-08-07 OI is unavailable, so the fallback `band_p80` = 0 there and cluster eligibility is gated `threshold > 0` (no clusters) | D-66, D-69, calibration log lines in `replay_6m.log` |
| feed vetoes | OFF (`_feed_vetoes` → `[]`), as ordered | replay patch 2 |
| unavailable handling | ON (`replay_mode=True`): reasons whose feed is unavailable leave both sums of raw conviction (`excluded_weight` in reasons_json); vetoes whose feed is unavailable are skipped and logged `unevaluated`; nothing unavailable is scored 0 | D-68, `strategies/feed_inputs.py` |
| day type | structure + feed inputs (D-62): with OI / gauge unavailable the classifier resolved to `range` on 84 % of M1 rows (BTC 14,494, ETH 14,608) and `no_trade` on 16 % (2,771 / 2,647); `event` (16 + 16 rows) and `trend_up` (ETH, 10 rows) occur only in the feed era from 2026-09-03. The day-type multiplier therefore carried almost no information in this replay | `strat_signals.day_type` |
| paper tape | `strat_book_5s` has no rows before 2026-09-03 14:17 UTC, so the manager's mid tape was synthesized per 15m replay candle as (open, ADVERSE extreme, favourable extreme, close) — adverse first, so a candle that touches both the stop and the target is a stop. This is conservative | replay patch 4 |
| first pass | aborted after 8 days: M1 `location` (weight 2.0) had been feed-gated on `liqmap`, but its input is the swept level's TYPE (a liq cluster is one candidate among PWL/PDL/zones/equal lows/session levels), so the reason is evaluable without the map. `feed_inputs.py` corrected, replay restarted from scratch (`replay_6m_run1_aborted.log`) | D-68 |

Availability by feed inside the window (so a reader can see which reasons were excluded when):

| feed | available from | share of the 180 d |
|---|---|---|
| candles (all tfs) | whole window | 100 % |
| oi | 2026-08-07 | 16.7 % |
| liq, liqmap | 2026-08-07 12:08 UTC (0xArchive plan window) | 16.7 % |
| taker, gauge, book, cohort | 2026-09-03 14:17 UTC | 1.5 % |

Consequences that the numbers must be read with: for ~83 % of the window the fuel / cleared / OI / delta / cohort reasons were excluded and raw conviction was computed over the structural reasons only (§5 lists the effective maximum weight per model); the vetoes that read those feeds were unevaluated (counted in §2). Calibrated normalisers never took a non-NULL value inside the replay (the archive's 30-day plan window is exactly the calibration minimum, and the window only crosses it after the replay's last boundary), so what the replay measures is the corrected structure tree + the fallback normaliser, not the calibrated one — the calibrated values are live on prod from 2026-09-06 12:10 UTC and a re-run after a Build-plan / Data Catalog purchase would measure them over history (D-69, D-73).

Definitions used by `replay_stats.py`: a **take** is a `strat_trades` row (a fired signal that reached the manager); **filled + exited** excludes takes cancelled before a fill (`cancelled:entry_expired`, `cancelled:entry_expired_no_fvg`); **win** = R > 0; **expectancy** = mean R over filled + exited takes; **PF** = gross positive R / gross negative R; **max DD** = largest peak-to-trough drawdown of cumulative R in trade order; **hold** = minutes from fill to exit.

## 2. Evaluation funnel per model and coin

"setup rows" = boundaries where `find_setup` returned a setup; "vetoed" = at least one evaluated veto hit; "takes" = fired. Unevaluated vetoes are those whose feed was unavailable at that boundary (D-68) — they are listed so the reader can see which gates the replay could NOT test.

| model | coin | setup rows | vetoed | non-vetoed | takes (filled) | tiers | vetoes hit (a row may carry several) | unevaluated vetoes on setup rows |
|---|---|---|---|---|---|---|---|---|
| M1 | BTC | 779 | 633 | 146 | 70 (66) | full 33 / half 37 | third_sweep 490, too_deep 393, cluster_below_uncleared 28, event_30m 1 | event_30m 775, funding_extreme_same_side 771, cluster_below_uncleared 663 |
| M1 | ETH | 770 | 615 | 155 | 69 (63) | full 35 / half 34 | third_sweep 450, too_deep 386, cluster_below_uncleared 9, trend_against 1, event_30m 1 | event_30m 764, funding_extreme_same_side 760, cluster_below_uncleared 625 |
| M2 | BTC | 5 | 5 | 0 | 0 | — | zone_premium 5, stop_too_wide 5 | short_covering 5, oi_exit_retrace 5, event_30m 5 |
| M2 | ETH | 3 | 2 | 1 | 0 (conviction 0.18 < 0.55) | — | stop_too_wide 2 | short_covering 3, oi_exit_retrace 3, event_30m 3 |
| M3 | BTC | 57 | 0 | 57 | 32 (17) | full 16 / half 16 | — | squeeze_risk 57, cohort_adding_longs 57, event_30m 57, funding_extreme_short_side 57, acceptance 56 |
| M3 | ETH | 40 | 1 | 39 | 23 (13) | full 10 / half 13 | acceptance 1 | squeeze_risk 40, cohort_adding_longs 40, event_30m 40, funding_extreme_short_side 40, acceptance 38 |
| M4 | BTC | 0 | 0 | 0 | 0 | — | — | — (on all rows: event_30m 17,100, cohort_adding_longs 17,012, oi_rebuilding 14,401) |
| M4 | ETH | 0 | 0 | 0 | 0 | — | — | — |
| M5 | BTC | 89 | 79 | 10 | 9 (6) | full 5 / half 4 | range_bad 63, too_deep 40, opened_outside 18, already_taken 2 | event_30m 88, funding_extreme_same_side 88 |
| M5 | ETH | 93 | 81 | 12 | 9 (6) | full 2 / half 7 | range_bad 53, too_deep 47, opened_outside 21, already_taken 2 | event_30m 93, funding_extreme_same_side 93 |
| M6 | BTC | 1,406 | 1,297 | 109 | 9 (8) | full 3 / half 6 | already_taken 517, too_late 444, daily_strong_down 364, shallow_loss 244, stop_too_wide 204, oi_falling 86 | event_30m 1,406, funding_extreme_long_side 1,405, oi_falling 1,300 |
| M6 | ETH | 1,648 | 1,536 | 112 | 12 (10) | full 5 / half 7 | already_taken 670, too_late 524, daily_strong_down 513, shallow_loss 344, oi_falling 128, stop_too_wide 112 | event_30m 1,648, funding_extreme_long_side 1,648, oi_falling 1,476 |

Conviction histograms on NON-vetoed setup rows (0.1 buckets; final conviction after multipliers / raw conviction before them):

| model | coin | final conviction | raw conviction |
|---|---|---|---|
| M1 | BTC | 0.1:2 0.2:3 0.3:26 0.4:27 0.5:21 0.6:31 0.7:17 0.8:11 0.9:5 1.0:3 | 0.2:1 0.3:2 0.4:17 0.5:26 0.6:47 0.7:32 0.8:20 0.9:1 |
| M1 | ETH | 0.1:1 0.2:8 0.3:19 0.4:42 0.5:28 0.6:20 0.7:17 0.8:11 0.9:5 1.0:4 | 0.2:1 0.3:4 0.4:19 0.5:38 0.6:47 0.7:29 0.8:14 0.9:3 |
| M2 | ETH | 0.1:1 | 0.2:1 |
| M3 | BTC | 0.3:1 0.4:7 0.5:13 0.6:13 0.7:14 0.8:6 0.9:1 1.0:2 | 0.4:6 0.5:21 0.6:22 0.7:7 0.8:1 |
| M3 | ETH | 0.4:5 0.5:11 0.6:10 0.7:10 0.9:3 | 0.3:2 0.4:2 0.5:15 0.6:13 0.7:6 0.8:1 |
| M5 | BTC | 0.5:1 0.6:4 0.7:2 0.8:2 0.9:1 | 0.5:3 0.6:4 0.7:3 |
| M5 | ETH | 0.4:1 0.5:4 0.6:5 0.9:2 | 0.4:2 0.5:4 0.6:3 0.7:1 0.8:2 |
| M6 | BTC | 0.2:4 0.3:56 0.4:40 0.5:2 0.6:4 0.7:2 0.8:1 | 0.2:4 0.3:56 0.4:28 0.5:12 0.6:3 0.7:3 0.8:3 |
| M6 | ETH | 0.2:36 0.3:48 0.4:16 0.6:7 0.7:3 0.8:2 | 0.2:4 0.3:72 0.4:24 0.6:1 0.7:6 0.8:2 0.9:3 |

M1 setup rows by swept level type (BTC 779 / ETH 770): BTC asia_high 98, pdh 87, equal_highs_4h 67, pdl 64, pwh 58, london_high 55, equal_lows_1h 53, asia_low 51, 4h_ob_top 43, equal_highs_1h 41, equal_lows_4h 37, london_low 34, 4h_ob_bottom 30, pwl 25, 4h_fvg_top 16, 4h_fvg_bottom 11, liq_cluster_short 7, liq_cluster_long 2; ETH asia_high 100, pdl 94, asia_low 66, london_high 66, pdh 65, equal_highs_1h 50, equal_highs_4h 47, equal_lows_1h 45, london_low 44, 4h_ob_top 44, 4h_ob_bottom 35, pwl 33, equal_lows_4h 27, pwh 17, 4h_fvg_bottom 14, 4h_fvg_top 12, liq_cluster_long 7, liq_cluster_short 4. Liquidation-cluster levels appear only in the map era (from 2026-08-07).

### 2.1 Why M4 produced no setup in 180 days (D-71)

`strat_signals.reason` for every M4 row (both directions are reported in one string; numbers masked):

| coin | reason | rows |
|---|---|---|
| BTC | no 4h CHoCH down in the last 72h; no 4h CHoCH up in the last 72h | 10,400 |
| BTC | no 4h CHoCH down …; 4h trend down not extended (BOS n, 3d move x) | 3,521 |
| BTC | 4h trend up not extended (BOS n, 3d move x); no 4h CHoCH up … | 2,656 |
| BTC | 4h trend up not extended; 4h trend down not extended | 704 |
| ETH | no 4h CHoCH down …; no 4h CHoCH up … | 11,335 |
| ETH | 4h trend up not extended; no 4h CHoCH up … | 2,496 |
| ETH | no 4h CHoCH down …; 4h trend down not extended | 2,490 |
| ETH | 4h trend up not extended; 4h trend down not extended | 672 |
| ETH | no 4h CHoCH down …; no 1h displacement up inside the CHoCH move | 288 |

4h CHoCHs did occur (§7.2: BTC 14 up / 12 down, ETH 12 / 11) and were seen by M4 for their 72 h life. Every one of them failed doc 14's extension precondition "≥ 5 consecutive 4h BOS with the old trend, or ≥ 8 % over 3 days": the largest values recorded before any CHoCH were **3 consecutive BOS (ETH) and +5.31 % over 3 days (ETH; BTC +4.00 %)**. The single ETH CHoCH that passed extension (288 rows = one CHoCH × 72 h) then found no 1h displacement inside the CHoCH move. The crowding checks (OI within 3 % of the 7-day high, funding z peak ≥ 1.0) were never reached; they would in any case have been unavailable before 2026-08-07. M4's zero is therefore the documented precondition applied to 180 days of BTC / ETH 4h structure, not a detector fault. Not changed (D-71).

### 2.2 Why M2 produced 8 setup rows and no take (D-72)

M2 first-failing precondition, from `strat_signals.reason` (rows classified by the text they contain):

| coin | neither direction aligned (4h trend / fresh 4h CHoCH confirmed by 1h BOS / alt branch "1h trend and daily bias both with the direction") | aligned, no 1h BOS in the last 24 h | 1h BOS was not a displacement candle | reached zone / retrace stage | setup rows |
|---|---|---|---|---|---|
| BTC | 10,356 | 2,772 | 4,148 | 5 | 5 (all vetoed: zone_premium 5, stop_too_wide 5) |
| ETH | 11,100 | 2,728 | 3,450 | 3 | 3 (2 vetoed stop_too_wide; 1 non-vetoed at conviction 0.18 < 0.55) |

The Part A M2 alternative alignment branch did open the door on 36–40 % of boundaries (BTC 6,925 / ETH 6,181 of 17,281) (the "aligned, …" and "BOS was not a displacement" rows), but a 1h BOS that is also a displacement candle (doc 10 §2.4: range ≥ 1.5 ATR and body ≥ 60 %) with a live zone at a discount was found on 8 boundaries in 180 days. Not changed (D-72).

## 3. Per-model performance (filled + exited takes, both coins)

### 3.1 Summary — one line per model (the Part F line)

| model | takes / 180 d | per 30 d (both coins) | per 30 d BTC / ETH | filled + exited | win rate | expectancy R | PF | sum R | max DD R | top exit_reason |
|---|---|---|---|---|---|---|---|---|---|---|
| M1 | 139 | 23.2 | 11.7 / 11.5 | 129 | 34 % | −0.351 | 0.47 | −45.25 | 56.97 | stop 68 of 129 |
| M2 | 0 | 0.0 | 0 / 0 | 0 | — | — | — | — | — | — |
| M3 | 55 | 9.2 | 5.3 / 3.8 | 30 (25 cancelled unfilled) | 37 % | −0.139 | 0.87 | −4.17 | 15.20 | stop 18 of 30 |
| M4 | 0 | 0.0 | 0 / 0 | 0 | — | — | — | — | — | — |
| M5 | 18 | 3.0 | 1.5 / 1.5 | 12 (6 cancelled unfilled) | 33 % | −0.437 | 0.40 | −5.24 | 6.94 | stop 7 of 12 |
| M6 | 21 | 3.5 | 1.5 / 2.0 | 18 (3 cancelled unfilled) | 11 % | −0.369 | 0.18 | −6.63 | 7.02 | thesis_failed 11 of 18 |

All 233 takes: 189 filled + exited, 44 cancelled before a fill. Every exited take happened on a `range` day except one M5 take on `no_trade` (§1, day type).

### 3.2 M1 — 139 takes, 129 filled + exited, 10 unfilled (cancelled:entry_expired_no_fvg 8, cancelled:entry_expired 2)

| split | n | win rate | expectancy R | PF | max DD R | sum R | hold mean min | hold median min |
|---|---|---|---|---|---|---|---|---|
| all | 129 | 34% | -0.351 | 0.47 | 56.97 | -45.25 | 53 | 20 |
| BTC | 66 | 32% | -0.368 | 0.44 | 30.46 | -24.29 | 57 | 20 |
| ETH | 63 | 37% | -0.333 | 0.51 | 27.52 | -20.97 | 48 | 20 |
| conviction 0.5 | 22 | 41% | -0.297 | 0.56 | 11.82 | -6.54 | 58 | 30 |
| conviction 0.6 | 39 | 36% | -0.130 | 0.79 | 11.47 | -5.08 | 54 | 20 |
| conviction 0.7 | 31 | 26% | -0.619 | 0.15 | 19.19 | -19.19 | 44 | 20 |
| conviction 0.8 | 20 | 30% | -0.528 | 0.22 | 11.05 | -10.56 | 47 | 20 |
| conviction 0.9 | 10 | 50% | -0.219 | 0.61 | 4.73 | -2.19 | 52 | 22 |
| conviction 1.0 | 7 | 29% | -0.242 | 0.70 | 4.47 | -1.69 | 81 | 35 |
| tier full | 68 | 31% | -0.495 | 0.29 | 35.20 | -33.63 | 50 | 20 |
| tier half | 61 | 38% | -0.190 | 0.70 | 23.01 | -11.62 | 56 | 20 |

Exit reasons: stop 68, stop_be 35, dead_trade 8, target 8, time_stop 7, thesis_failed 3. 53 % of exits are the initial stop, median hold 20 min: the reclaim entry is being stopped inside the first two 15m candles far more often than it reaches T1. Higher conviction did NOT help (0.7–0.8 buckets are the worst; full tier −0.495 R vs half −0.190 R) — with fuel / cleared / delta / cohort excluded, conviction here is structure quality + multipliers only. The 8 targets and 7 time-stops carry the positive R (largest +4.90 R ETH 05-26, +4.42 R BTC 08-06).

### 3.3 M3 — 55 takes, 30 filled + exited, 25 unfilled (cancelled:entry_expired 25)

| split | n | win rate | expectancy R | PF | max DD R | sum R | hold mean min | hold median min |
|---|---|---|---|---|---|---|---|---|
| all | 30 | 37% | -0.139 | 0.87 | 15.20 | -4.17 | 78 | 0 |
| BTC | 17 | 18% | -0.594 | 0.60 | 21.13 | -10.10 | 65 | 0 |
| ETH | 13 | 62% | 0.456 | 1.77 | 3.17 | 5.93 | 95 | 15 |
| conviction 0.5 | 5 | 40% | 0.685 | 1.71 | 2.37 | 3.43 | 147 | 15 |
| conviction 0.6 | 9 | 33% | -0.917 | 0.22 | 9.01 | -8.25 | 52 | 0 |
| conviction 0.7 | 11 | 45% | 0.537 | 1.57 | 5.63 | 5.91 | 101 | 30 |
| conviction 0.8 | 3 | 33% | -0.633 | 0.45 | 1.90 | -1.90 | 5 | 0 |
| conviction 0.9 | 2 | 0% | -1.672 | 0.00 | 3.34 | -3.34 | 8 | 8 |
| tier full | 16 | 38% | 0.041 | 1.04 | 10.37 | 0.66 | 71 | 15 |
| tier half | 14 | 36% | -0.345 | 0.69 | 8.97 | -4.83 | 88 | 0 |

Exit reasons: stop 18, stop_be 8, time_stop 4. 45 % of M3 takes never filled (the post-only entry at the range extreme was not traded through within the entry validity) and 16 of the 30 fills exited inside the fill candle (hold 0 min — the synthesized adverse-first tape, §1): 13 initial stops and 3 BE exits after T1. The two large winners were time-stops (+11.03 R BTC 03-11, +7.49 R ETH 04-06), i.e. the runner never reached T2/T3 in the doc's sense. BTC 18 % vs ETH 62 % on 17 / 13 trades is not a stable difference at this sample size.

### 3.4 M5 — 18 takes, 12 filled + exited, 6 unfilled (cancelled:entry_expired 6)

| split | n | win rate | expectancy R | PF | max DD R | sum R | hold mean min | hold median min |
|---|---|---|---|---|---|---|---|---|
| all | 12 | 33% | -0.437 | 0.40 | 6.94 | -5.24 | 52 | 15 |
| BTC | 6 | 33% | -0.467 | 0.20 | 2.96 | -2.80 | 31 | 10 |
| ETH | 6 | 33% | -0.407 | 0.54 | 4.14 | -2.44 | 72 | 38 |
| conviction 0.5 | 2 | 0% | -1.300 | 0.00 | 2.60 | -2.60 | 32 | 32 |
| conviction 0.6 | 4 | 25% | -0.352 | 0.44 | 2.53 | -1.41 | 76 | 70 |
| conviction 0.7 | 2 | 100% | 0.348 | inf | 0.00 | 0.70 | 22 | 22 |
| conviction 0.8 | 2 | 0% | -1.170 | 0.00 | 2.34 | -2.34 | 5 | 5 |
| conviction 0.9 | 2 | 50% | 0.204 | 1.32 | 1.28 | 0.41 | 98 | 98 |
| day type no_trade | 1 | 0% | -0.005 | 0.00 | 0.00 | -0.00 | 125 | 125 |
| day type range | 11 | 36% | -0.476 | 0.40 | 6.93 | -5.24 | 45 | 15 |
| tier full | 6 | 50% | -0.206 | 0.66 | 2.93 | -1.24 | 42 | 10 |
| tier half | 6 | 17% | -0.668 | 0.22 | 4.01 | -4.01 | 62 | 38 |

Exit reasons: stop 7, stop_be 3, time_stop 2. M5's funnel is dominated by `range_bad` (63 / 53 of the setup rows): §7.5 shows the Asia range measured at 07:00 UTC was ≥ 4.0 ATR15 on 168 of 180 BTC days and 155 of 180 ETH days, so doc 15's 0.8–4.0 ATR15 band admitted 12 BTC / 25 ETH days in six months. 12 exits is too few for any per-split reading.

### 3.5 M6 — 21 takes, 18 filled + exited, 3 unfilled (cancelled:entry_expired 3)

| split | n | win rate | expectancy R | PF | max DD R | sum R | hold mean min | hold median min |
|---|---|---|---|---|---|---|---|---|
| all | 18 | 11% | -0.369 | 0.18 | 7.02 | -6.63 | 280 | 155 |
| BTC | 8 | 12% | -0.537 | 0.05 | 4.54 | -4.30 | 170 | 70 |
| ETH | 10 | 10% | -0.233 | 0.34 | 2.48 | -2.33 | 368 | 175 |
| conviction 0.5 | 2 | 0% | -0.595 | 0.00 | 1.19 | -1.19 | 43 | 43 |
| conviction 0.6 | 8 | 12% | -0.204 | 0.42 | 2.83 | -1.63 | 261 | 100 |
| conviction 0.7 | 5 | 20% | -0.652 | 0.07 | 3.50 | -3.26 | 502 | 310 |
| conviction 0.8 | 3 | 0% | -0.186 | 0.00 | 0.56 | -0.56 | 117 | 160 |
| tier full | 8 | 12% | -0.477 | 0.06 | 4.05 | -3.82 | 358 | 235 |
| tier half | 10 | 10% | -0.282 | 0.30 | 4.02 | -2.82 | 218 | 70 |

Exit reasons: thesis_failed 11, stop_be 3, stop 2, time_stop 1, target 1. M6 is the only model whose takes end mostly by the thesis check (11 of 18) rather than the stop — the result per thesis exit is small (−0.92 … +0.24 R), one target (+1.20 R ETH 07-20). 3,054 setup rows produced 21 takes: `already_taken` (517 / 670) and `too_late` (444 / 524) are the doc's own once-per-week and Mon 12:00–Thu window; `daily_strong_down` (364 / 513) is the daily-trend veto.

## 4. Frequency vs doc 17 (takes scaled to 30 days, BTC + ETH together)

Doc 17 frequency wording and the AUDIT-REPORT §8.6 order-of-magnitude ranges, against the 180-day take counts:

| model | doc frequency | per 30 d (doc) | AUDIT §8.6 range | takes / 180 d | per 30 d | ratio | > 5× outside? |
|---|---|---|---|---|---|---|---|
| M1 | 1 to 3 per day across BTC and ETH | 30–90 | 20–60 | 139 | 23.2 | 1.3x below | no |
| M2 | 0 to 2 per day | 0–60 | 10–30 | 0 | 0.0 | in range (lower bound 0) | no |
| M3 | 0 to 2 per day | 0–60 | 10–30 | 55 | 9.2 | in range | no |
| M4 | 2 to 4 per month per coin | 4–8 | 0–6 | 0 | 0.0 | inf below | **yes** |
| M5 | up to 2 per day per coin | 0–120 | 15–40 | 18 | 3.0 | in range (below §8.6) | no |
| M6 | at most one per week per coin per direction | 0–17 | 0–8 | 21 | 3.5 | in range | no |

Per coin per 30 d: M1 BTC 11.7 / ETH 11.5; M3 5.3 / 3.8; M5 1.5 / 1.5; M6 1.5 / 2.0. M1 at 23 per 30 d is one third below the doc's lower bound with `third_sweep` and `too_deep` vetoing 81 % of its setup rows; M4 is the only model more than 5× outside its documented order of magnitude and §2.1 traces that to doc 14's extension precondition; M2 sits on the doc's lower bound of 0 by wording but is far below the §8.6 expectation and §2.2 traces that to "1h BOS that is a displacement candle". The AUDIT's 30-day replay (§8.3, pass 2) found the same shape (M1 3 / M5 1 / others 0 in 30 days on the then-available data); 180 days on Binance candles confirms it is the rule set, not the short window.

## 5. Unavailable handling — what the Mind could and could not score

Effective maximum weight per model over setup rows (sum of reason weights whose feed WAS available; the raw conviction denominator, D-68):

| model | total weight | setup rows | excluded reasons (rows) | effective max weight distribution |
|---|---|---|---|---|
| M1 | 13.2 | 1,549 | delta_flip 1,531, absorption 1,531, cohort 1,531, fuel 1,288, cleared 1,288 | 7.0: 1,288 · 9.7: 243 · 13.2: 18 |
| M2 | 13.5 | 8 | oi_new_positioning 8, oi_holding 8, delta_break 8, funding_young 8, cluster_cleared 8 | 7.2: 8 |
| M3 | 13.5 | 97 | cvd_divergence 97, thin_bids 97, funding_up 97, trap 94, cluster_fuel 94 | 6.5: 94 · 9.7: 3 |
| M4 | — | 0 | — | — |
| M5 | 12.9 | 182 | delta_flip 181, cohort 181, fuel 148, cleared 148 | 8.5: 148 · 10.8: 33 · 12.9: 1 |
| M6 | 13.8 | 3,054 | delta_reclaim 3,054, cohort 3,054, funding_room 3,053, oi_commitment 2,776 | 7.3: 2,776 · 9.3: 277 · 10.5: 1 |

Only 18 M1 setup rows and 1 M5 row in 180 days were scored on the full weight; 83 % of M1 rows (1,288) were scored on 7.0 of 13.2. The conviction numbers in §3 are therefore structure-only convictions for most takes, which is also why the fuel section in §6 has almost no evaluated rows.

## 6. M1 `fuel` and M3 `cluster_fuel` — takes above 0.7 vs below 0.3

Owner question: do takes with strong liquidation fuel (> 0.7) outperform weak ones (< 0.3)? The replay cannot answer it: the fuel reasons were only evaluable from 2026-08-07 12:08 UTC, and inside the map era the fallback normaliser (0.10 % of OI, ≈ $2.8 M at BTC / $2.3 M at ETH, D-69) is 3.5–7.6× BELOW the calibrated `band_p80`, i.e. the fallback should make fuel read HIGH more easily — and the strengths still came out at 0.

M1 — 129 filled + exited takes; `fuel_strength` non-zero on 5 takes only (0.02, 0.03, 0.05, 0.07, 0.59); all others 0.0 (121 excluded — reason not evaluable; 3 evaluated at 0):

| split | n | win rate | expectancy R | PF | max DD R | sum R | hold mean min | hold median min |
|---|---|---|---|---|---|---|---|---|
| fuel_strength > 0.7 | 0 | - | - | - | - | - | - | - |
| fuel_strength < 0.3 (all) | 128 | 34% | -0.353 | 0.47 | 50.74 | -45.24 | 53 | 20 |
| fuel_strength < 0.3 and reason evaluated | 7 | 43% | 0.675 | 2.94 | 1.22 | 4.72 | 46 | 20 |
| reason excluded (liq/liqmap unavailable) | 121 | 34% | -0.413 | 0.40 | 51.98 | -49.96 | 53 | 20 |

M3 — 30 filled + exited takes; `cluster_fuel_strength` = 0.0 on all 30 (all 30 excluded — every M3 fill happened before 2026-08-07; the only map-era M3 take, BTC 2026-08-10, was cancelled unfilled):

| split | n | win rate | expectancy R | PF | max DD R | sum R | hold mean min | hold median min |
|---|---|---|---|---|---|---|---|---|
| cluster_fuel_strength > 0.7 | 0 | - | - | - | - | - | - | - |
| cluster_fuel_strength < 0.3 (all) | 30 | 37% | -0.139 | 0.87 | 22.60 | -4.17 | 78 | 0 |
| cluster_fuel_strength < 0.3 and reason evaluated | 0 | - | - | - | - | - | - | - |
| reason excluded (liq/liqmap unavailable) | 30 | 37% | -0.139 | 0.87 | 22.60 | -4.17 | 78 | 0 |

The 7 evaluated M1 takes (all in the archive era, August 2026; +4.72 R, 3 of 7 winners) are the only takes in the replay scored on the liquidation feeds; 7 is not a sample. The question needs the archive history behind the takes (Build plan / Data Catalog, D-69) or the live paper record accumulating from 2026-09-06 12:10 UTC with the calibrated normaliser.

## 7. Structure statistics over the window (corrected tree)

Window 2026-03-10 09:30 → 2026-09-06 09:30 UTC (180 days), computed from `strat_replay_candles` (Binance) with the corrected tree (`/root/struct_stats.py`, 78 s BTC / 114 s ETH).

### 7.1 4h trend label by day (label at 00:00 UTC)

| coin | up | range | down | 1d up | 1d range | 1d down |
|---|---|---|---|---|---|---|
| BTC | 58 | 85 | 37 | 80 | 57 | 43 |
| ETH | 54 | 88 | 38 | 56 | 71 | 53 |

### 7.2 4h and 1h BOS / CHoCH counts

| coin | 4h BOS up | 4h BOS down | 4h CHoCH up | 4h CHoCH down | 4h total | 1h BOS up | 1h BOS down | 1h CHoCH up | 1h CHoCH down |
|---|---|---|---|---|---|---|---|---|---|
| BTC | 44 | 34 | 14 | 12 | 104 | 137 | 141 | 62 | 53 |
| ETH | 41 | 30 | 12 | 11 | 94 | 144 | 143 | 51 | 55 |

### 7.3 1h displacements and grade distribution (spec v1.1 formula)

| coin | n | up | down | two-candle | 0.0 | 0.1 | 0.2 | 0.3 | 0.4 | 0.5 | 0.6 | 0.7 | 0.8 | 0.9 | 1.0 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| BTC | 634 | 313 | 321 | 362 | 14 | 66 | 76 | 100 | 70 | 109 | 75 | 46 | 36 | 25 | 17 |
| ETH | 572 | 294 | 278 | 312 | 19 | 59 | 66 | 77 | 76 | 93 | 71 | 39 | 39 | 21 | 12 |

### 7.4 Order blocks and FVGs (1h and 4h; status at window end)

| coin | tf | OB | fresh | tested | broken | FVG | open | half | filled |
|---|---|---|---|---|---|---|---|---|---|
| BTC | 1h | 422 | 15 | 11 | 396 | 454 | 15 | 3 | 436 |
| BTC | 4h | 104 | 8 | 7 | 89 | 101 | 5 | 3 | 93 |
| ETH | 1h | 367 | 18 | 14 | 335 | 400 | 18 | 3 | 379 |
| ETH | 4h | 95 | 9 | 5 | 81 | 102 | 5 | 2 | 95 |

Zone grade distribution (OB+FVG):

| coin | tf | 0.0 | 0.1 | 0.2 | 0.3 | 0.4 | 0.5 | 0.6 | 0.7 | 0.8 | 0.9 | 1.0 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| BTC | 1h | 20 | 91 | 98 | 140 | 102 | 164 | 101 | 61 | 49 | 27 | 23 |
| BTC | 4h | 11 | 13 | 32 | 26 | 41 | 29 | 11 | 15 | 17 | 4 | 6 |
| ETH | 1h | 31 | 82 | 92 | 107 | 110 | 136 | 81 | 46 | 37 | 29 | 16 |
| ETH | 4h | 7 | 13 | 20 | 29 | 26 | 28 | 36 | 18 | 10 | 5 | 5 |

### 7.5 Asia range height (ATR15 at 07:00 UTC) and London / New York raids

| coin | days | median | mean | in 0.8–4.0 (M5 `range_bad` passes) | ≥ 4.0 | < 4.0 |
|---|---|---|---|---|---|---|
| BTC | 180 | 5.57 | 5.84 | 12 | 168 | 12 |
| ETH | 180 | 5.43 | 5.72 | 25 | 155 | 25 |

| coin | session | Asia level | raids | reclaimed | reclaimed rq ≥ 0.5 | too deep |
|---|---|---|---|---|---|---|
| BTC | london | high | 91 | 58 | 27 | 48 |
| BTC | london | low | 76 | 36 | 15 | 51 |
| BTC | newyork | high | 210 | 87 | 37 | 166 |
| BTC | newyork | low | 184 | 57 | 22 | 150 |
| ETH | london | high | 98 | 65 | 31 | 62 |
| ETH | london | low | 70 | 32 | 15 | 45 |
| ETH | newyork | high | 208 | 94 | 34 | 165 |
| ETH | newyork | low | 183 | 66 | 22 | 154 |

### 7.6 Weekly-open loss then reclaim

| coin | side (reclaim direction) | weeks with a loss (4h close beyond WO) | reclaimed (1h close back) | reclaimed inside Mon 12:00–Thu | excursion depth ≥ 0.8 ATR4h |
|---|---|---|---|---|---|
| BTC | long | 18 | 13 | 6 | 10 |
| BTC | short | 22 | 18 | 9 | 12 |
| ETH | long | 19 | 15 | 8 | 8 |
| ETH | short | 23 | 18 | 14 | 15 |

### 7.7 15m sweeps of eligible levels

| coin | direction | sweeps | reclaimed | reclaimed rq ≥ 0.5 | too deep |
|---|---|---|---|---|---|
| BTC | long | 2801 | 1042 | 447 | 2193 |
| BTC | short | 3640 | 1391 | 627 | 2843 |
| ETH | long | 2660 | 1106 | 412 | 2015 |
| ETH | short | 3272 | 1280 | 541 | 2536 |

Unique sweeps (by level price + wick ts): BTC 6441, ETH 5932. Level types swept:

| coin | 4h_ob_bottom | 4h_ob_top | asia_high | asia_low | equal_highs_1h | equal_highs_4h | equal_lows_1h | equal_lows_4h | london_high | london_low | pdh | pdl | pwh | pwl |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| BTC | 117 | 155 | 875 | 705 | 194 | 176 | 184 | 87 | 527 | 478 | 775 | 615 | 938 | 615 |
| ETH | 80 | 107 | 879 | 679 | 255 | 109 | 202 | 50 | 545 | 488 | 664 | 605 | 713 | 556 |

Sweep depth (ATR15) distribution:

| coin | <0.2 | <0.3 | <0.5 | <1.0 | >=1.0 |
|---|---|---|---|---|---|
| BTC | 449 | 386 | 570 | 859 | 4177 |
| ETH | 505 | 345 | 531 | 777 | 3774 |

Reclaim-quality distribution (reclaimed sweeps):

| coin | 0.0 | 0.1 | 0.2 | 0.3 | 0.4 | 0.5 | 0.6 | 0.7 | 0.8 | 0.9 | 1.0 |
|---|---|---|---|---|---|---|---|---|---|---|---|
| BTC | 247 | 255 | 273 | 278 | 306 | 303 | 277 | 251 | 169 | 72 | 2 |
| ETH | 241 | 242 | 284 | 329 | 337 | 316 | 272 | 205 | 108 | 51 | 1 |

## 8. Findings and what was decided

1. **The replay measured the structure tree, not the calibrated Mind.** Liquidation, OI, taker, gauge, book and cohort were unavailable for 83–98.5 % of the window (D-67/D-68); calibration stayed NULL throughout (D-69). Every §3 number is a structure-only conviction with the fallback normaliser. **Nothing in the weights, vetoes or thresholds is changed on the strength of this replay (D-73).**
2. **M1, M3, M5, M6 all have negative expectancy on this data** (−0.35 / −0.14 / −0.44 / −0.37 R; sum −61.3 R over 189 exits) with the initial stop as the top exit for M1/M3/M5 and the thesis check for M6. The two conditions that flatter the models the least were both ordered by the owner and are recorded: the synthesized adverse-first paper tape (a candle touching stop and target counts as a stop) and post-only fills only when the tape trades through (44 of 233 takes never filled).
3. **M4: zero setups in 180 days traced to doc 14's extension precondition** (≥ 5 consecutive 4h BOS or ≥ 8 % in 3 days; observed maxima 3 BOS / +5.31 %) — §2.1, D-71, not changed.
4. **M2: 8 setup rows in 180 days** — the A3 alignment branch works (it admitted 36–40 % of boundaries) but "1h BOS that is a displacement candle with a live discount zone" is rare — §2.2, D-72, not changed.
5. **M5 `range_bad` excludes ~90 % of days**: Asia range ≥ 4.0 ATR15 on 168 / 155 of 180 days (§7.5). Recorded, not changed.
6. **M1 `third_sweep` + `too_deep` veto 81 % of setup rows** (1,248 of 1,549 setup rows vetoed; third_sweep 940 hits, too_deep 779; §7.7 shows 64 % of eligible-level sweeps were ≥ 1.0 ATR15 deep). Recorded, not changed.
7. **Fuel question unanswerable on this data** (§6): 7 evaluated M1 takes, 0 evaluated M3 takes.
8. **Day type carried no information** (84 % `range`, 16 % `no_trade` outside the feed era) — a consequence of the missing feeds, not of the D-62 fix.

What would make a re-run informative: (a) 180 days of 0xArchive fills and level snapshots (Build plan or Data Catalog — owner's money decision, D-69) so `liq` / `liqmap` / calibration exist for the whole window; (b) an OI history longer than Binance's public 30 days; (c) the live `strat_trades_1m` / gauge / book / cohort stores simply accumulating (they started 2026-09-03). The replay tooling (`/root/replay_6m.py`, `/root/replay_stats.py`, `/root/struct_stats.py`, `/root/audit_tree`) is reusable as is.

## 9. Appendix — every take (signal ts UTC, R on filled + exited takes)

### 9.1 M1 BTC

| trade | signal ts | dir | tier | conviction | filled | exit_reason | R | hold min | day type | excluded reasons |
|---|---|---|---|---|---|---|---|---|---|---|
| 164 | 2026-03-19 13:00 | long | full | 0.828 | yes | stop_be | 0.50 | 80 | range | fuel,cleared,delta_flip,absorption,cohort |
| 168 | 2026-03-20 14:00 | long | full | 0.799 | yes | stop | -1.12 | 5 | range | fuel,cleared,delta_flip,absorption,cohort |
| 173 | 2026-03-22 20:15 | long | half | 0.699 | yes | stop | -1.24 | 30 | range | fuel,cleared,delta_flip,absorption,cohort |
| 174 | 2026-03-23 13:45 | short | half | 0.667 | yes | stop | -1.09 | 5 | range | fuel,cleared,delta_flip,absorption,cohort |
| 176 | 2026-03-24 08:00 | short | full | 0.733 | yes | stop_be | 0.17 | 95 | range | fuel,cleared,delta_flip,absorption,cohort |
| 179 | 2026-03-25 05:15 | short | half | 0.605 | yes | stop_be | -0.06 | 15 | range | fuel,cleared,delta_flip,absorption,cohort |
| 181 | 2026-03-26 06:00 | long | half | 0.616 | yes | stop | -1.17 | 5 | range | fuel,cleared,delta_flip,absorption,cohort |
| 182 | 2026-03-26 08:45 | long | half | 0.599 | yes | stop_be | 0.28 | 15 | range | fuel,cleared,delta_flip,absorption,cohort |
| 183 | 2026-03-26 15:15 | long | full | 1.040 | yes | stop | -1.11 | 5 | range | fuel,cleared,delta_flip,absorption,cohort |
| 186 | 2026-03-28 08:45 | short | full | 0.750 | yes | stop_be | -0.29 | 0 | range | fuel,cleared,delta_flip,absorption,cohort |
| 188 | 2026-03-30 08:30 | short | full | 0.928 | yes | stop_be | 0.37 | 165 | range | fuel,cleared,delta_flip,absorption,cohort |
| 189 | 2026-03-31 09:30 | long | full | 0.828 | yes | stop | -1.18 | 20 | range | fuel,cleared,delta_flip,absorption,cohort |
| 192 | 2026-03-31 17:30 | short | full | 0.815 | yes | stop_be | -0.38 | 0 | range | fuel,cleared,delta_flip,absorption,cohort |
| 197 | 2026-04-04 16:15 | short | half | 0.648 | yes | stop | -1.75 | 0 | range | fuel,cleared,delta_flip,absorption,cohort |
| 205 | 2026-04-08 13:15 | short | full | 0.799 | yes | stop | -1.10 | 5 | range | fuel,cleared,delta_flip,absorption,cohort |
| 206 | 2026-04-10 19:30 | short | half | 0.567 | yes | thesis_failed | -0.92 | 25 | range | fuel,cleared,delta_flip,absorption,cohort |
| 210 | 2026-04-13 09:00 | long | half | 0.633 | no | cancelled:entry_expired_no_fvg | - | - | range | fuel,cleared,delta_flip,absorption,cohort |
| 212 | 2026-04-14 07:15 | short | half | 0.644 | yes | stop_be | -0.04 | 20 | range | fuel,cleared,delta_flip,absorption,cohort |
| 216 | 2026-04-16 19:45 | short | half | 0.636 | yes | stop | -1.20 | 5 | range | fuel,cleared,delta_flip,absorption,cohort |
| 221 | 2026-04-20 14:30 | short | half | 0.625 | yes | stop_be | 0.40 | 50 | range | fuel,cleared,delta_flip,absorption,cohort |
| 222 | 2026-04-20 18:30 | short | full | 0.835 | yes | stop | -1.27 | 15 | range | fuel,cleared,delta_flip,absorption,cohort |
| 223 | 2026-04-21 07:30 | short | full | 0.894 | yes | stop | -1.21 | 50 | range | fuel,cleared,delta_flip,absorption,cohort |
| 229 | 2026-04-24 09:15 | long | half | 0.601 | yes | time_stop | 2.33 | 240 | range | fuel,cleared,delta_flip,absorption,cohort |
| 231 | 2026-04-27 15:15 | long | full | 0.983 | yes | stop | -1.14 | 5 | range | fuel,cleared,delta_flip,absorption,cohort |
| 235 | 2026-04-29 18:15 | long | full | 0.757 | yes | stop_be | 0.00 | 120 | range | fuel,cleared,delta_flip,absorption,cohort |
| 237 | 2026-05-01 08:30 | short | full | 0.821 | yes | dead_trade | 0.13 | 150 | range | fuel,cleared,delta_flip,absorption,cohort |
| 238 | 2026-05-01 13:15 | short | full | 0.924 | yes | stop | -1.15 | 20 | range | fuel,cleared,delta_flip,absorption,cohort |
| 245 | 2026-05-07 00:00 | long | half | 0.622 | yes | stop | -1.51 | 15 | range | fuel,cleared,delta_flip,absorption,cohort |
| 249 | 2026-05-12 11:15 | long | half | 0.584 | yes | stop | -1.43 | 5 | range | fuel,cleared,delta_flip,absorption,cohort |
| 250 | 2026-05-12 14:00 | long | full | 0.789 | yes | stop_be | -0.06 | 20 | range | fuel,cleared,delta_flip,absorption,cohort |
| 252 | 2026-05-12 15:45 | long | half | 0.620 | yes | stop_be | -0.04 | 10 | range | fuel,cleared,delta_flip,absorption,cohort |
| 254 | 2026-05-13 13:45 | long | full | 0.733 | yes | stop | -1.19 | 5 | range | fuel,cleared,delta_flip,absorption,cohort |
| 255 | 2026-05-14 16:15 | short | full | 0.737 | yes | stop_be | 0.05 | 30 | range | fuel,cleared,delta_flip,absorption,cohort |
| 256 | 2026-05-16 07:15 | long | full | 1.056 | yes | stop | -1.16 | 5 | range | fuel,cleared,delta_flip,absorption,cohort |
| 263 | 2026-05-21 08:15 | short | full | 1.028 | yes | time_stop | 2.88 | 240 | range | fuel,cleared,delta_flip,absorption,cohort |
| 267 | 2026-05-25 02:15 | short | full | 0.789 | yes | dead_trade | 0.14 | 135 | range | fuel,cleared,delta_flip,absorption,cohort |
| 268 | 2026-05-25 05:00 | short | half | 0.556 | yes | stop | -1.34 | 20 | range | fuel,cleared,delta_flip,absorption,cohort |
| 269 | 2026-05-25 05:45 | short | half | 0.631 | yes | stop | -1.55 | 20 | range | fuel,cleared,delta_flip,absorption,cohort |
| 273 | 2026-05-25 15:00 | short | full | 0.711 | yes | stop | -1.38 | 5 | range | fuel,cleared,delta_flip,absorption,cohort |
| 280 | 2026-05-27 14:45 | long | half | 0.613 | no | cancelled:entry_expired_no_fvg | - | - | range | fuel,cleared,delta_flip,absorption,cohort |
| 282 | 2026-05-29 16:30 | short | full | 0.735 | yes | stop | -1.15 | 0 | range | fuel,cleared,delta_flip,absorption,cohort |
| 285 | 2026-06-01 07:30 | long | half | 0.674 | yes | stop | -1.30 | 5 | range | fuel,cleared,delta_flip,absorption,cohort |
| 286 | 2026-06-02 07:45 | long | full | 0.714 | yes | stop | -1.14 | 60 | range | fuel,cleared,delta_flip,absorption,cohort |
| 290 | 2026-06-11 04:00 | short | half | 0.625 | yes | dead_trade | -0.49 | 135 | range | fuel,cleared,delta_flip,absorption,cohort |
| 292 | 2026-06-12 10:00 | short | full | 0.727 | yes | stop_be | 0.38 | 60 | range | fuel,cleared,delta_flip,absorption,cohort |
| 296 | 2026-06-18 04:15 | long | half | 0.633 | yes | thesis_failed | -0.80 | 45 | range | fuel,cleared,delta_flip,absorption,cohort |
| 299 | 2026-06-18 16:15 | long | full | 0.870 | yes | time_stop | 1.57 | 240 | range | fuel,cleared,delta_flip,absorption,cohort |
| 300 | 2026-06-19 09:15 | long | half | 0.616 | yes | dead_trade | 0.19 | 195 | range | fuel,cleared,delta_flip,absorption,cohort |
| 304 | 2026-06-23 00:45 | long | half | 0.600 | yes | stop_be | -0.18 | 0 | range | fuel,cleared,delta_flip,absorption,cohort |
| 308 | 2026-06-23 10:45 | long | full | 0.708 | yes | stop_be | -0.03 | 110 | range | fuel,cleared,delta_flip,absorption,cohort |
| 311 | 2026-06-27 08:15 | short | half | 0.631 | yes | time_stop | 1.18 | 240 | range | fuel,cleared,delta_flip,absorption,cohort |
| 313 | 2026-06-29 10:15 | short | half | 0.596 | yes | stop_be | 0.42 | 80 | range | fuel,cleared,delta_flip,absorption,cohort |
| 324 | 2026-07-04 00:15 | short | half | 0.558 | yes | dead_trade | 0.37 | 210 | range | fuel,cleared,delta_flip,absorption,cohort |
| 334 | 2026-07-10 00:00 | short | half | 0.681 | no | cancelled:entry_expired_no_fvg | - | - | range | fuel,cleared,delta_flip,absorption,cohort |
| 335 | 2026-07-10 02:00 | short | half | 0.640 | yes | stop | -1.12 | 50 | range | fuel,cleared,delta_flip,absorption,cohort |
| 337 | 2026-07-12 06:45 | long | half | 0.616 | no | cancelled:entry_expired_no_fvg | - | - | range | fuel,cleared,delta_flip,absorption,cohort |
| 341 | 2026-07-14 12:45 | short | full | 0.913 | yes | stop | -1.10 | 5 | range | fuel,cleared,delta_flip,absorption,cohort |
| 346 | 2026-07-17 05:45 | long | half | 0.610 | yes | stop | -1.20 | 5 | range | fuel,cleared,delta_flip,absorption,cohort |
| 347 | 2026-07-17 13:45 | long | full | 0.982 | yes | target | 2.16 | 25 | range | fuel,cleared,delta_flip,absorption,cohort |
| 350 | 2026-07-20 16:00 | short | full | 0.899 | yes | stop | -1.14 | 125 | range | fuel,cleared,delta_flip,absorption,cohort |
| 353 | 2026-07-21 13:30 | short | full | 0.885 | yes | stop_be | 0.13 | 15 | range | fuel,cleared,delta_flip,absorption,cohort |
| 357 | 2026-07-24 13:00 | long | full | 0.896 | yes | stop | -1.30 | 5 | range | fuel,cleared,delta_flip,absorption,cohort |
| 360 | 2026-07-28 00:00 | long | half | 0.614 | yes | stop | -1.30 | 0 | range | fuel,cleared,delta_flip,absorption,cohort |
| 361 | 2026-07-29 20:00 | long | half | 0.669 | yes | stop | -1.16 | 5 | range | fuel,cleared,delta_flip,absorption,cohort |
| 364 | 2026-08-01 18:00 | long | full | 0.733 | yes | stop | -1.42 | 5 | range | fuel,cleared,delta_flip,absorption,cohort |
| 367 | 2026-08-03 04:00 | long | half | 0.552 | yes | stop | -1.22 | 65 | range | fuel,cleared,delta_flip,absorption,cohort |
| 371 | 2026-08-06 05:15 | short | half | 0.589 | yes | dead_trade | -0.47 | 135 | range | fuel,cleared,delta_flip,absorption,cohort |
| 372 | 2026-08-06 09:00 | short | half | 0.684 | yes | time_stop | 4.42 | 240 | range | fuel,cleared,delta_flip,absorption,cohort |
| 379 | 2026-08-14 15:00 | long | half | 0.553 | yes | target | 1.37 | 40 | range | delta_flip,absorption,cohort |
| 383 | 2026-08-24 07:30 | short | half | 0.653 | yes | stop_be | -0.12 | 0 | range | delta_flip,absorption,cohort |

### 9.1 M1 ETH

| trade | signal ts | dir | tier | conviction | filled | exit_reason | R | hold min | day type | excluded reasons |
|---|---|---|---|---|---|---|---|---|---|---|
| 155 | 2026-03-11 07:45 | long | full | 1.044 | yes | time_stop | 1.07 | 240 | range | fuel,cleared,delta_flip,absorption,cohort |
| 160 | 2026-03-11 17:30 | short | full | 0.844 | yes | stop_be | 0.50 | 80 | range | fuel,cleared,delta_flip,absorption,cohort |
| 161 | 2026-03-13 13:00 | short | full | 0.888 | yes | stop | -1.09 | 5 | range | fuel,cleared,delta_flip,absorption,cohort |
| 162 | 2026-03-13 13:30 | short | full | 0.766 | yes | stop | -1.04 | 50 | range | fuel,cleared,delta_flip,absorption,cohort |
| 163 | 2026-03-18 11:30 | long | full | 0.972 | yes | stop | -1.10 | 5 | range | fuel,cleared,delta_flip,absorption,cohort |
| 165 | 2026-03-19 13:15 | long | full | 0.804 | yes | stop | -1.11 | 5 | range | fuel,cleared,delta_flip,absorption,cohort |
| 177 | 2026-03-24 08:00 | short | half | 0.616 | yes | stop_be | 0.11 | 35 | range | fuel,cleared,delta_flip,absorption,cohort |
| 180 | 2026-03-25 11:30 | short | full | 0.743 | yes | stop | -1.23 | 5 | range | fuel,cleared,delta_flip,absorption,cohort |
| 184 | 2026-03-27 10:45 | long | full | 1.028 | yes | stop | -1.05 | 5 | range | fuel,cleared,delta_flip,absorption,cohort |
| 185 | 2026-03-28 08:00 | short | full | 0.718 | yes | time_stop | 2.04 | 240 | range | fuel,cleared,delta_flip,absorption,cohort |
| 187 | 2026-03-30 08:00 | short | full | 0.953 | yes | stop | -1.09 | 35 | range | fuel,cleared,delta_flip,absorption,cohort |
| 190 | 2026-03-31 10:00 | long | full | 0.858 | yes | stop_be | 0.10 | 35 | range | fuel,cleared,delta_flip,absorption,cohort |
| 191 | 2026-03-31 14:30 | short | half | 0.698 | yes | stop_be | 0.32 | 125 | range | fuel,cleared,delta_flip,absorption,cohort |
| 198 | 2026-04-05 07:15 | long | full | 0.857 | yes | stop | -1.43 | 15 | range | fuel,cleared,delta_flip,absorption,cohort |
| 204 | 2026-04-07 11:00 | long | half | 0.646 | yes | stop | -1.15 | 5 | range | fuel,cleared,delta_flip,absorption,cohort |
| 207 | 2026-04-11 18:45 | short | half | 0.559 | yes | stop | -1.05 | 20 | range | fuel,cleared,delta_flip,absorption,cohort |
| 208 | 2026-04-12 14:00 | long | half | 0.572 | yes | stop | -1.28 | 35 | range | fuel,cleared,delta_flip,absorption,cohort |
| 211 | 2026-04-13 09:00 | long | half | 0.616 | no | cancelled:entry_expired_no_fvg | - | - | range | fuel,cleared,delta_flip,absorption,cohort |
| 218 | 2026-04-17 13:00 | short | half | 0.657 | yes | stop | -1.09 | 5 | range | fuel,cleared,delta_flip,absorption,cohort |
| 219 | 2026-04-19 07:15 | long | full | 0.734 | yes | stop | -1.25 | 5 | range | fuel,cleared,delta_flip,absorption,cohort |
| 225 | 2026-04-21 15:00 | long | half | 0.639 | yes | stop | -1.09 | 5 | range | fuel,cleared,delta_flip,absorption,cohort |
| 227 | 2026-04-23 09:15 | long | half | 0.593 | yes | stop | -1.09 | 50 | range | fuel,cleared,delta_flip,absorption,cohort |
| 228 | 2026-04-23 17:30 | long | half | 0.560 | yes | stop | -1.15 | 5 | range | fuel,cleared,delta_flip,absorption,cohort |
| 232 | 2026-04-28 13:15 | long | full | 0.861 | yes | stop_be | -0.02 | 20 | range | fuel,cleared,delta_flip,absorption,cohort |
| 234 | 2026-04-28 14:30 | long | half | 0.696 | yes | stop | -1.23 | 5 | range | fuel,cleared,delta_flip,absorption,cohort |
| 239 | 2026-05-01 13:15 | short | full | 0.828 | yes | stop | -1.17 | 20 | range | fuel,cleared,delta_flip,absorption,cohort |
| 240 | 2026-05-03 12:15 | short | full | 0.757 | yes | dead_trade | 0.06 | 135 | range | fuel,cleared,delta_flip,absorption,cohort |
| 242 | 2026-05-06 09:00 | short | full | 1.099 | yes | stop | -1.15 | 35 | range | fuel,cleared,delta_flip,absorption,cohort |
| 243 | 2026-05-06 16:45 | long | full | 0.712 | yes | thesis_failed | -0.54 | 25 | range | fuel,cleared,delta_flip,absorption,cohort |
| 261 | 2026-05-20 05:15 | short | half | 0.567 | yes | stop | -1.16 | 20 | range | fuel,cleared,delta_flip,absorption,cohort |
| 265 | 2026-05-23 21:15 | short | half | 0.642 | no | cancelled:entry_expired | - | - | range | fuel,cleared,delta_flip,absorption,cohort |
| 271 | 2026-05-25 08:15 | short | full | 0.764 | yes | stop | -1.38 | 15 | range | fuel,cleared,delta_flip,absorption,cohort |
| 274 | 2026-05-25 15:30 | short | half | 0.622 | no | cancelled:entry_expired_no_fvg | - | - | range | fuel,cleared,delta_flip,absorption,cohort |
| 277 | 2026-05-26 14:30 | short | half | 0.641 | yes | target | 4.90 | 85 | range | fuel,cleared,delta_flip,absorption,cohort |
| 278 | 2026-05-27 14:00 | long | full | 0.917 | yes | stop_be | 0.49 | 165 | range | fuel,cleared,delta_flip,absorption,cohort |
| 284 | 2026-06-01 02:15 | long | half | 0.565 | no | cancelled:entry_expired_no_fvg | - | - | range | fuel,cleared,delta_flip,absorption,cohort |
| 289 | 2026-06-09 15:00 | long | half | 0.690 | yes | stop_be | 0.14 | 0 | range | fuel,cleared,delta_flip,absorption,cohort |
| 291 | 2026-06-12 09:45 | short | half | 0.552 | yes | stop | -1.37 | 5 | range | fuel,cleared,delta_flip,absorption,cohort |
| 303 | 2026-06-22 14:15 | short | half | 0.698 | no | cancelled:entry_expired | - | - | range | fuel,cleared,delta_flip,absorption,cohort |
| 307 | 2026-06-23 09:30 | long | full | 0.716 | yes | stop | -1.29 | 0 | range | fuel,cleared,delta_flip,absorption,cohort |
| 310 | 2026-06-24 13:15 | long | full | 0.864 | yes | stop | -1.09 | 5 | range | fuel,cleared,delta_flip,absorption,cohort |
| 314 | 2026-06-29 17:30 | short | full | 0.717 | yes | stop | -1.09 | 20 | range | fuel,cleared,delta_flip,absorption,cohort |
| 317 | 2026-07-01 14:30 | short | half | 0.614 | yes | stop_be | 0.20 | 0 | range | fuel,cleared,delta_flip,absorption,cohort |
| 318 | 2026-07-02 13:15 | short | half | 0.553 | yes | stop | -1.14 | 5 | range | fuel,cleared,delta_flip,absorption,cohort |
| 319 | 2026-07-02 14:30 | short | half | 0.559 | no | cancelled:entry_expired_no_fvg | - | - | range | fuel,cleared,delta_flip,absorption,cohort |
| 320 | 2026-07-03 08:45 | short | half | 0.656 | yes | stop_be | 0.01 | 20 | range | fuel,cleared,delta_flip,absorption,cohort |
| 326 | 2026-07-05 02:30 | long | half | 0.563 | yes | stop_be | 0.42 | 180 | range | fuel,cleared,delta_flip,absorption,cohort |
| 327 | 2026-07-06 08:45 | long | full | 0.722 | yes | stop | -1.19 | 35 | range | fuel,cleared,delta_flip,absorption,cohort |
| 328 | 2026-07-06 13:15 | long | full | 0.724 | yes | stop | -1.16 | 20 | range | fuel,cleared,delta_flip,absorption,cohort |
| 329 | 2026-07-06 16:30 | short | half | 0.602 | yes | dead_trade | 0.14 | 145 | range | fuel,cleared,delta_flip,absorption,cohort |
| 338 | 2026-07-13 00:15 | short | half | 0.652 | yes | target | 1.52 | 205 | range | fuel,cleared,delta_flip,absorption,cohort |
| 340 | 2026-07-13 12:45 | long | half | 0.672 | yes | stop | -1.16 | 35 | range | fuel,cleared,delta_flip,absorption,cohort |
| 342 | 2026-07-14 12:45 | short | full | 0.752 | yes | stop | -1.06 | 5 | range | fuel,cleared,delta_flip,absorption,cohort |
| 345 | 2026-07-16 12:45 | long | full | 0.795 | yes | stop_be | 0.50 | 125 | range | fuel,cleared,delta_flip,absorption,cohort |
| 348 | 2026-07-17 13:45 | long | full | 0.902 | yes | stop_be | 0.34 | 80 | range | fuel,cleared,delta_flip,absorption,cohort |
| 354 | 2026-07-22 16:45 | short | half | 0.560 | yes | target | 1.20 | 115 | range | fuel,cleared,delta_flip,absorption,cohort |
| 355 | 2026-07-23 07:15 | long | full | 1.012 | yes | stop | -1.17 | 35 | range | fuel,cleared,delta_flip,absorption,cohort |
| 358 | 2026-07-24 13:15 | long | full | 0.986 | yes | stop_be | 0.02 | 20 | range | fuel,cleared,delta_flip,absorption,cohort |
| 359 | 2026-07-27 22:45 | long | half | 0.551 | yes | stop | -1.11 | 5 | range | fuel,cleared,delta_flip,absorption,cohort |
| 362 | 2026-07-29 20:00 | long | full | 0.719 | yes | stop | -1.10 | 5 | range | fuel,cleared,delta_flip,absorption,cohort |
| 365 | 2026-08-01 18:30 | long | full | 0.771 | yes | stop | -1.20 | 5 | range | fuel,cleared,delta_flip,absorption,cohort |
| 366 | 2026-08-02 18:45 | short | half | 0.577 | yes | stop_be | 0.03 | 20 | range | fuel,cleared,delta_flip,absorption,cohort |
| 369 | 2026-08-05 02:15 | short | half | 0.590 | yes | target | 1.49 | 70 | range | fuel,cleared,delta_flip,absorption,cohort |
| 374 | 2026-08-07 12:15 | short | half | 0.694 | yes | stop | -1.10 | 20 | range | delta_flip,absorption,cohort |
| 376 | 2026-08-11 07:30 | long | half | 0.555 | yes | target | 2.61 | 145 | range | delta_flip,absorption,cohort |
| 377 | 2026-08-11 15:45 | long | full | 0.861 | yes | stop_be | -0.02 | 45 | range | delta_flip,absorption,cohort |
| 378 | 2026-08-12 12:30 | short | half | 0.640 | yes | target | 3.18 | 100 | range | delta_flip,absorption,cohort |
| 380 | 2026-08-18 14:30 | short | full | 0.822 | yes | stop | -1.09 | 5 | range | delta_flip,absorption,cohort |
| 381 | 2026-08-19 07:30 | short | full | 0.730 | yes | stop_be | -0.12 | 10 | range | delta_flip,absorption,cohort |

### 9.3 M3 BTC

| trade | signal ts | dir | tier | conviction | filled | exit_reason | R | hold min | day type | excluded reasons |
|---|---|---|---|---|---|---|---|---|---|---|
| 157 | 2026-03-11 10:00 | long | full | 0.753 | no | cancelled:entry_expired | - | - | range | trap,cvd_divergence,thin_bids,cluster_fuel,funding_up |
| 158 | 2026-03-11 12:45 | long | full | 0.793 | yes | time_stop | 11.03 | 360 | range | trap,cvd_divergence,thin_bids,cluster_fuel,funding_up |
| 167 | 2026-03-20 13:00 | long | full | 0.745 | yes | stop | -1.70 | 30 | range | trap,cvd_divergence,thin_bids,cluster_fuel,funding_up |
| 175 | 2026-03-23 15:15 | short | full | 0.751 | no | cancelled:entry_expired | - | - | range | trap,cvd_divergence,thin_bids,cluster_fuel,funding_up |
| 193 | 2026-04-01 04:30 | short | half | 0.557 | yes | stop | -1.69 | 15 | range | trap,cvd_divergence,thin_bids,cluster_fuel,funding_up |
| 194 | 2026-04-01 15:15 | short | full | 0.865 | yes | stop | -1.57 | 0 | range | trap,cvd_divergence,thin_bids,cluster_fuel,funding_up |
| 195 | 2026-04-02 11:15 | long | half | 0.657 | yes | stop | -1.85 | 0 | range | trap,cvd_divergence,thin_bids,cluster_fuel,funding_up |
| 196 | 2026-04-02 13:15 | long | half | 0.682 | yes | stop | -1.79 | 0 | range | trap,cvd_divergence,thin_bids,cluster_fuel,funding_up |
| 214 | 2026-04-15 16:15 | long | full | 0.706 | no | cancelled:entry_expired | - | - | range | trap,cvd_divergence,thin_bids,cluster_fuel,funding_up |
| 215 | 2026-04-16 19:30 | short | half | 0.664 | yes | stop | -1.54 | 0 | range | trap,cvd_divergence,thin_bids,cluster_fuel,funding_up |
| 217 | 2026-04-17 08:30 | short | half | 0.577 | yes | stop | -2.23 | 0 | range | trap,cvd_divergence,thin_bids,cluster_fuel,funding_up |
| 230 | 2026-04-27 10:45 | short | half | 0.653 | no | cancelled:entry_expired | - | - | range | trap,cvd_divergence,thin_bids,cluster_fuel,funding_up |
| 236 | 2026-04-30 14:45 | short | full | 0.824 | no | cancelled:entry_expired | - | - | range | trap,cvd_divergence,thin_bids,cluster_fuel,funding_up |
| 241 | 2026-05-03 23:15 | short | half | 0.636 | no | cancelled:entry_expired | - | - | range | trap,cvd_divergence,thin_bids,cluster_fuel,funding_up |
| 251 | 2026-05-12 14:45 | long | full | 0.857 | yes | stop_be | 1.56 | 15 | range | trap,cvd_divergence,thin_bids,cluster_fuel,funding_up |
| 253 | 2026-05-12 16:30 | long | full | 0.968 | yes | stop | -1.79 | 0 | range | trap,cvd_divergence,thin_bids,cluster_fuel,funding_up |
| 260 | 2026-05-19 14:15 | long | half | 0.670 | yes | stop | -1.83 | 0 | range | trap,cvd_divergence,thin_bids,cluster_fuel,funding_up |
| 264 | 2026-05-21 21:00 | short | half | 0.558 | yes | time_stop | -0.92 | 360 | range | trap,cvd_divergence,thin_bids,cluster_fuel,funding_up |
| 270 | 2026-05-25 06:00 | short | half | 0.689 | yes | stop | -2.14 | 0 | range | trap,cvd_divergence,thin_bids,cluster_fuel,funding_up |
| 281 | 2026-05-28 01:45 | long | half | 0.606 | no | cancelled:entry_expired | - | - | range | trap,cvd_divergence,thin_bids,cluster_fuel,funding_up |
| 287 | 2026-06-03 14:30 | long | full | 0.739 | yes | stop | -1.55 | 0 | range | trap,cvd_divergence,thin_bids,cluster_fuel,funding_up |
| 288 | 2026-06-08 13:15 | short | half | 0.559 | no | cancelled:entry_expired | - | - | range | trap,cvd_divergence,thin_bids,cluster_fuel,funding_up |
| 293 | 2026-06-16 19:45 | long | full | 0.745 | yes | stop_be | 2.27 | 330 | range | trap,cvd_divergence,thin_bids,cluster_fuel,funding_up |
| 297 | 2026-06-18 12:00 | long | full | 0.797 | no | cancelled:entry_expired | - | - | range | trap,cvd_divergence,thin_bids,cluster_fuel,funding_up |
| 301 | 2026-06-21 10:45 | short | full | 0.746 | yes | stop | -2.46 | 0 | range | trap,cvd_divergence,thin_bids,cluster_fuel,funding_up |
| 302 | 2026-06-22 04:30 | long | half | 0.617 | no | cancelled:entry_expired | - | - | range | trap,cvd_divergence,thin_bids,cluster_fuel,funding_up |
| 309 | 2026-06-23 19:30 | long | full | 0.726 | no | cancelled:entry_expired | - | - | range | trap,cvd_divergence,thin_bids,cluster_fuel,funding_up |
| 312 | 2026-06-27 08:30 | short | half | 0.611 | no | cancelled:entry_expired | - | - | range | trap,cvd_divergence,thin_bids,cluster_fuel,funding_up |
| 330 | 2026-07-06 17:45 | short | half | 0.626 | no | cancelled:entry_expired | - | - | range | trap,cvd_divergence,thin_bids,cluster_fuel,funding_up |
| 336 | 2026-07-10 20:00 | short | full | 0.764 | no | cancelled:entry_expired | - | - | range | trap,cvd_divergence,thin_bids,cluster_fuel,funding_up |
| 356 | 2026-07-23 15:00 | long | full | 0.803 | yes | stop | -1.89 | 0 | range | trap,cvd_divergence,thin_bids,cluster_fuel,funding_up |
| 375 | 2026-08-10 19:00 | long | half | 0.603 | no | cancelled:entry_expired | - | - | range | cvd_divergence,thin_bids,funding_up |

### 9.3 M3 ETH

| trade | signal ts | dir | tier | conviction | filled | exit_reason | R | hold min | day type | excluded reasons |
|---|---|---|---|---|---|---|---|---|---|---|
| 154 | 2026-03-10 16:45 | short | half | 0.663 | no | cancelled:entry_expired | - | - | range | trap,cvd_divergence,thin_bids,cluster_fuel,funding_up |
| 170 | 2026-03-20 15:15 | long | half | 0.668 | no | cancelled:entry_expired | - | - | range | trap,cvd_divergence,thin_bids,cluster_fuel,funding_up |
| 178 | 2026-03-25 01:15 | short | half | 0.590 | no | cancelled:entry_expired | - | - | range | trap,cvd_divergence,thin_bids,cluster_fuel,funding_up |
| 200 | 2026-04-06 15:00 | short | full | 0.753 | yes | stop | -1.47 | 0 | range | trap,cvd_divergence,thin_bids,cluster_fuel,funding_up |
| 201 | 2026-04-06 16:00 | short | half | 0.588 | yes | time_stop | 7.49 | 360 | range | trap,cvd_divergence,thin_bids,cluster_fuel,funding_up |
| 209 | 2026-04-12 14:15 | long | half | 0.622 | yes | stop_be | 1.23 | 0 | range | trap,cvd_divergence,thin_bids,cluster_fuel,funding_up |
| 220 | 2026-04-19 18:30 | long | half | 0.692 | yes | stop | -1.50 | 105 | range | trap,cvd_divergence,thin_bids,cluster_fuel,funding_up |
| 233 | 2026-04-28 13:45 | long | half | 0.585 | yes | stop_be | 0.78 | 0 | range | trap,cvd_divergence,thin_bids,cluster_fuel,funding_up |
| 244 | 2026-05-06 17:30 | long | full | 0.964 | yes | stop | -1.55 | 15 | range | trap,cvd_divergence,thin_bids,cluster_fuel,funding_up |
| 248 | 2026-05-11 08:30 | long | full | 0.736 | yes | stop_be | 0.27 | 285 | range | trap,cvd_divergence,thin_bids,cluster_fuel,funding_up |
| 257 | 2026-05-16 13:45 | long | full | 0.967 | no | cancelled:entry_expired | - | - | range | trap,cvd_divergence,thin_bids,cluster_fuel,funding_up |
| 262 | 2026-05-20 16:00 | short | full | 0.751 | yes | stop_be | 2.03 | 60 | range | trap,cvd_divergence,thin_bids,cluster_fuel,funding_up |
| 266 | 2026-05-25 01:30 | short | half | 0.630 | yes | stop_be | 0.41 | 0 | range | trap,cvd_divergence,thin_bids,cluster_fuel,funding_up |
| 283 | 2026-05-29 20:45 | short | half | 0.656 | yes | time_stop | 0.75 | 360 | range | trap,cvd_divergence,thin_bids,cluster_fuel,funding_up |
| 295 | 2026-06-17 13:30 | long | full | 0.754 | yes | stop_be | 0.67 | 30 | range | trap,cvd_divergence,thin_bids,cluster_fuel,funding_up |
| 322 | 2026-07-03 10:30 | short | full | 0.798 | yes | stop | -1.52 | 0 | range | trap,cvd_divergence,thin_bids,cluster_fuel,funding_up |
| 323 | 2026-07-03 14:00 | short | half | 0.566 | no | cancelled:entry_expired | - | - | range | trap,cvd_divergence,thin_bids,cluster_fuel,funding_up |
| 325 | 2026-07-04 09:00 | short | full | 0.713 | no | cancelled:entry_expired | - | - | range | trap,cvd_divergence,thin_bids,cluster_fuel,funding_up |
| 333 | 2026-07-08 07:30 | long | full | 0.751 | no | cancelled:entry_expired | - | - | range | trap,cvd_divergence,thin_bids,cluster_fuel,funding_up |
| 339 | 2026-07-13 05:45 | long | half | 0.686 | no | cancelled:entry_expired | - | - | range | trap,cvd_divergence,thin_bids,cluster_fuel,funding_up |
| 349 | 2026-07-20 03:30 | short | half | 0.576 | no | cancelled:entry_expired | - | - | range | trap,cvd_divergence,thin_bids,cluster_fuel,funding_up |
| 368 | 2026-08-03 20:30 | short | half | 0.633 | no | cancelled:entry_expired | - | - | range | trap,cvd_divergence,thin_bids,cluster_fuel,funding_up |
| 370 | 2026-08-05 15:45 | short | full | 0.710 | yes | stop | -1.65 | 15 | range | trap,cvd_divergence,thin_bids,cluster_fuel,funding_up |

### 9.5 M5 BTC

| trade | signal ts | dir | tier | conviction | filled | exit_reason | R | hold min | day type | excluded reasons |
|---|---|---|---|---|---|---|---|---|---|---|
| 159 | 2026-03-11 13:15 | long | full | 0.908 | no | cancelled:entry_expired | - | - | range | fuel,cleared,delta_flip,cohort |
| 169 | 2026-03-20 14:00 | long | full | 0.821 | yes | stop | -1.12 | 5 | range | fuel,cleared,delta_flip,cohort |
| 171 | 2026-03-21 07:45 | short | half | 0.625 | no | cancelled:entry_expired | - | - | no_trade | fuel,cleared,delta_flip,cohort |
| 199 | 2026-04-06 14:30 | short | full | 0.848 | yes | stop | -1.22 | 5 | range | fuel,cleared,delta_flip,cohort |
| 213 | 2026-04-15 14:00 | long | full | 0.777 | yes | stop_be | 0.54 | 30 | range | fuel,cleared,delta_flip,cohort |
| 224 | 2026-04-21 14:30 | short | half | 0.648 | yes | stop | -1.15 | 5 | range | fuel,cleared,delta_flip,cohort |
| 247 | 2026-05-10 08:30 | short | half | 0.625 | yes | stop_be | -0.00 | 125 | no_trade | fuel,cleared,delta_flip,cohort |
| 321 | 2026-07-03 09:00 | short | full | 0.776 | yes | stop_be | 0.16 | 15 | range | fuel,cleared,delta_flip,cohort |
| 373 | 2026-08-06 14:45 | long | half | 0.664 | no | cancelled:entry_expired | - | - | range | fuel,cleared,delta_flip,cohort |

### 9.5 M5 ETH

| trade | signal ts | dir | tier | conviction | filled | exit_reason | R | hold min | day type | excluded reasons |
|---|---|---|---|---|---|---|---|---|---|---|
| 156 | 2026-03-11 07:45 | long | full | 0.919 | yes | time_stop | 1.69 | 195 | range | fuel,cleared,delta_flip,cohort |
| 172 | 2026-03-21 07:45 | short | half | 0.666 | no | cancelled:entry_expired | - | - | no_trade | fuel,cleared,delta_flip,cohort |
| 226 | 2026-04-21 15:00 | long | half | 0.589 | yes | stop | -1.09 | 5 | range | fuel,cleared,delta_flip,cohort |
| 272 | 2026-05-25 08:15 | short | half | 0.685 | yes | stop | -1.38 | 15 | range | fuel,cleared,delta_flip,cohort |
| 279 | 2026-05-27 14:00 | long | half | 0.635 | yes | time_stop | 1.12 | 160 | range | fuel,cleared,delta_flip,cohort |
| 344 | 2026-07-16 08:00 | long | full | 0.905 | yes | stop | -1.28 | 0 | range | fuel,cleared,delta_flip,cohort |
| 363 | 2026-08-01 07:30 | short | half | 0.687 | no | cancelled:entry_expired | - | - | no_trade | fuel,cleared,delta_flip,cohort |
| 382 | 2026-08-19 07:30 | short | half | 0.570 | yes | stop | -1.51 | 60 | range | delta_flip,cohort |
| 384 | 2026-08-24 07:30 | short | half | 0.627 | no | cancelled:entry_expired | - | - | range | delta_flip,cohort |

### 9.6 M6 BTC

| trade | signal ts | dir | tier | conviction | filled | exit_reason | R | hold min | day type | excluded reasons |
|---|---|---|---|---|---|---|---|---|---|---|
| 203 | 2026-04-07 11:00 | short | full | 0.836 | yes | stop_be | -0.47 | 0 | range | oi_commitment,delta_reclaim,funding_room,cohort |
| 258 | 2026-05-18 13:00 | long | half | 0.592 | yes | thesis_failed | -0.92 | 70 | range | oi_commitment,delta_reclaim,funding_room,cohort |
| 275 | 2026-05-26 01:00 | short | full | 0.746 | yes | stop | -1.68 | 330 | range | oi_commitment,delta_reclaim,funding_room,cohort |
| 294 | 2026-06-16 20:00 | short | half | 0.676 | yes | thesis_failed | -0.38 | 70 | range | oi_commitment,delta_reclaim,funding_room,cohort |
| 305 | 2026-06-23 07:00 | short | half | 0.672 | no | cancelled:entry_expired | - | - | range | oi_commitment,delta_reclaim,funding_room,cohort |
| 315 | 2026-06-30 05:00 | short | half | 0.620 | yes | thesis_failed | -0.09 | 40 | range | oi_commitment,delta_reclaim,funding_room,cohort |
| 331 | 2026-07-07 03:00 | short | half | 0.658 | yes | thesis_failed | -0.74 | 555 | range | oi_commitment,delta_reclaim,funding_room,cohort |
| 351 | 2026-07-20 16:00 | long | half | 0.550 | yes | stop_be | -0.27 | 15 | range | oi_commitment,delta_reclaim,funding_room,cohort |
| 386 | 2026-09-03 03:00 | long | full | 0.792 | yes | thesis_failed | 0.24 | 280 | range | delta_reclaim,funding_room,cohort |

### 9.6 M6 ETH

| trade | signal ts | dir | tier | conviction | filled | exit_reason | R | hold min | day type | excluded reasons |
|---|---|---|---|---|---|---|---|---|---|---|
| 166 | 2026-03-19 14:30 | short | half | 0.626 | no | cancelled:entry_expired | - | - | range | oi_commitment,delta_reclaim,funding_room,cohort |
| 202 | 2026-04-07 02:00 | short | full | 0.850 | yes | thesis_failed | -0.08 | 160 | range | oi_commitment,delta_reclaim,funding_room,cohort |
| 246 | 2026-05-07 03:00 | short | half | 0.669 | yes | thesis_failed | -0.31 | 130 | range | oi_commitment,delta_reclaim,funding_room,cohort |
| 259 | 2026-05-19 01:00 | long | half | 0.608 | yes | thesis_failed | -0.36 | 55 | range | oi_commitment,delta_reclaim,funding_room,cohort |
| 276 | 2026-05-26 01:00 | short | full | 0.726 | yes | thesis_failed | -0.11 | 310 | range | oi_commitment,delta_reclaim,funding_room,cohort |
| 298 | 2026-06-18 16:00 | short | full | 0.776 | yes | time_stop | -0.65 | 1440 | range | oi_commitment,delta_reclaim,funding_room,cohort |
| 306 | 2026-06-23 07:00 | short | half | 0.672 | no | cancelled:entry_expired | - | - | range | oi_commitment,delta_reclaim,funding_room,cohort |
| 316 | 2026-06-30 13:00 | short | full | 0.846 | yes | thesis_failed | -0.01 | 190 | range | oi_commitment,delta_reclaim,funding_room,cohort |
| 332 | 2026-07-07 03:00 | short | half | 0.629 | yes | thesis_failed | -0.33 | 495 | range | oi_commitment,delta_reclaim,funding_room,cohort |
| 343 | 2026-07-14 13:00 | long | half | 0.648 | yes | stop_be | -0.62 | 0 | range | oi_commitment,delta_reclaim,funding_room,cohort |
| 352 | 2026-07-20 16:00 | long | half | 0.651 | yes | target | 1.20 | 745 | range | oi_commitment,delta_reclaim,funding_room,cohort |
| 385 | 2026-09-02 06:00 | long | full | 0.708 | yes | stop | -1.05 | 150 | range | delta_reclaim,funding_room,cohort |

