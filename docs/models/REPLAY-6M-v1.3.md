# REPLAY-6M-v1.3 — M1…M6 over 180 days on BTC and ETH (spec v1.3 tree, two fill models, same data as v1.2)

Owner order (2026-09-07, spec v1.3 Part 6): run the unit suite, re-run the 180-day replay for all six models on both fill models with the current data (30-day liquidation window, calibration as-of), write this report with the same statistics as REPLAY-6M-v1.2.md plus the new v1.3 splits (Part 5), a direct v1.2-versus-v1.3 comparison per model, and regenerate the verification pack with the same seed. Every number below is read back from the two replay databases on prod (`perpl_replay` = adverse, `perpl_replay_n` = neutral: `strat_signals`, `strat_trades`, `strat_calibration_hist`, plus the v1.2 result tables preserved as `strat_signals_v12` / `strat_trades_v12` / `mind_model_state_v12`) by `/root/replay_stats_v13.py` (outputs `/root/replay_stats_v13_{adverse,neutral}.md/.json`), from the replay driver's own logs (`/root/replay_v13_{adverse,neutral}.out`), the feed-presence files (`/root/replay_feeds_v13_{adverse,neutral}.json`), the verification pack (`/root/replay-v1.3-takes.csv`, `/root/replay-v1.3-pack.json`) and two read-only probe scripts against the same tables; nothing is estimated. Decisions taken while building it: D-87 … D-92 before the run; D-93 … D-98 from reading it (§13).

## 0. Read this first — what changed and what did not

**Same data as v1.2, rules only (D-92).** The feed tables of both replay databases are exactly as `v12_setup.py` left them on 2026-09-06: Binance structure candles for the whole window, OI / liquidation tape / 15-minute liquidation map from **2026-08-07** only (the 0xArchive key is still on the free tier — D-81, unchanged, owner's decision), and the point-in-time calibration history with **182 as-of days per coin, 182 NULL** (34,562 NULL lookups per pass again, §12). So, as in v1.2, this is a structure + fill-model measurement with the fallback normaliser, NOT the calibrated replay; what is new is that every v1.2 → v1.3 difference isolates the four rule changes:

| change | decision | what it does to the replay |
|---|---|---|
| post-only placement (Part 1) | D-87 | a limit that would cross the touch is rejected and re-quoted once one tick inside; it then fills only when a later print goes THROUGH it — 10 M1 entries were re-quoted per pass (§4.2) |
| minimum stop distance (Part 2) | D-88 | stop moved out to 0.5 ATR(15m) for M1/M3/M5, 0.5 ATR(1h) for M2/M4/M6 when the structural stop is nearer; size and R use the final stop, targets are NOT recomputed — 12–14 M1, all 14 M3, 1 M5, 1 M6 takes floored (§4.1) |
| M1/M5 confirmed reclaim + resting entry (Part 3) | D-89 | the entry is placed one to two candles later than in v1.2, at `min(50 % of the reclaim candle, close) − 0.05 ATR` (mirror for shorts), valid 3 candles — the M1/M5 setup population changed (§2), takes M1 139 → 81, M5 18 → 12, and 37 % of M1 takes never fill (§3) |
| conviction cap (Part 4) | D-90 | `min(1.0, raw × multipliers)` — engaged on 9 (adverse) / 10 (neutral) M1 signal rows, changed no take (§2) |

M2, M3, M4 and M6 have **identical setup rows** to v1.2 (§2, setup-row check); M6 differs by 4 non-vetoed BTC rows through the `recent_form` multiplier (a v1.3 outcome feeds a later evaluation, §5.4). The v1.2 verification pack's eight sampled trades cannot be reproduced trade-for-trade because the M1 population changed; the same seed draws eight different v1.3 trades, and REPLAY-6M-v1.3-VERIFICATION.md §2.1 looks each of the eight v1.2 trades up under the v1.3 rules instead (2 of 8 re-fire, 6 have no v1.3 setup within −15…+3 candles).

One pre-existing defect surfaced while reading the fills and is **reported, not fixed** (outside the v1.3 order; D-97): M1's FVG second attempt re-prices the entry to the FVG mid without recomputing stop and T1, so when the mid is beyond T1 the trade fills already past its target and the "partial" executes at a loss — 4 of 8 FVG trades in v1.3 (worst #683, −1.947 R), 2 of 6 in v1.2 (§13).

## 1. Method

| item | value |
|---|---|
| Tree | spec v1.3 (`/root/audit_tree/backend`, overlay of the working tree): all **74** `.py/.yaml` files under `backend/app/strategy_engine` identical md5-for-md5 (after CRLF→LF) between the local tree, `/root/audit_tree` and the deployed `/var/www/terminal/backend` — 0 differences, 0 extra/missing. Unit suite **300 passed** (the 5 `test_telegram_queue` failures are the known local pytest-asyncio gap, unrelated) |
| Window | 2026-03-10 19:45 → 2026-09-06 19:45 UTC, 180 days, 17,281 15-minute boundaries per coin (34,562 total), BTC and ETH — identical to v1.2 |
| Structure candles | Binance 15m/1h/4h/1d (`strat_replay_candles`, as refreshed for v1.2); 100 % present |
| Liquidation tape / map | as v1.2: Hyperliquid liquidation fills via 0xArchive, 2026-08-07 12:08 → 2026-09-06 UTC only (free tier, D-81); 15-minute map over the same 30 days. `v12_setup.py` NOT re-run (D-92) |
| Feed vetoes | OFF (D-68 carried) |
| Unavailable handling | ON with the D-80 set `{cohort, taker, oi}` (unchanged) |
| Fill model, pass 1 (primary) | adverse-first: stop and target inside one candle → stop — DB `perpl_replay`, log `/root/replay_v13_adverse.out` (35,112 lines), **14,426 s** wall clock, 0 tracebacks |
| Fill model, pass 2 | neutral: stop and target inside one candle → the one nearer the candle open; equidistant → adverse (D-78) — DB `perpl_replay_n`, log `/root/replay_v13_neutral.out` (35,112 lines), **14,385 s** wall clock, 0 tracebacks. Fill ordering counted by the pass: `both_inside 7, favourable_first 5, adverse_first_or_tie 2` (v1.2: 4 / 2 / 2) |
| Post-only executor (new) | D-87: touch = last tape mid inside the boundary window, else the snapshot close; buy at/above (sell at/below) the touch → rejected, re-quoted once one tick (1 bp, 0.1 floor) inside, rests, fills only on a print THROUGH it, cancelled at `entry_valid_until`. Same `ModelEvaluator` → `ModelTradeManager.paper_post_only_quote` code in the live worker and in the driver (`test_live_and_replay_share_the_post_only_code`) |
| Stop floor (new) | D-88: `ModelStrategy.apply_stop_floor` after `build_intent`, 0.5 × ATR(15m) M1/M3/M5, 0.5 × ATR(1h) M2/M4/M6; `stop_floor_applied` + `lifecycle_json.stop_structural` recorded |
| M1/M5 entry (new) | D-89: `confirmed_reclaim` (case a later-candle reclaim / case b same-candle + one confirming close), entry `min(mid, close) − 0.05 ATR(15m)` longs (mirror shorts) from the reclaim candle, validity 3 candles from confirmation; `reclaim_candles` + `confirmation_used` on the signal row |
| Conviction cap (new) | D-90: `min(1.0, raw × Π multipliers)` |
| Calibration | point-in-time as-of rows from v1.2 (D-76): 182 as-of days per coin, **182 NULL**, 34,562 NULL lookups per pass → fallback normaliser 0.10 % of OI throughout |
| Cohort | 30 wallets, as v1.2 (cohort feed present on 1.8 % of boundaries) |
| Driver / stats / pack | `/root/replay_v13.py` (= `replay_v12.py` + `apply_v17` on the replay DB before its DELETEs, backs the v1.2 result tables up as `*_v12` first) sha256 `985eb98…088267`; `/root/replay_stats_v13.py` sha256 `5a90c0c…a3716`; `docs/models/tools/verify_pack_v13.py` sha256 `b702f0c…a25da`, seed 42, run twice → identical CSV sha256 `e24a587…67ca` (264 rows) |
| Signal rows | 207,372 per pass (17,281 × 2 coins × 6 models), same count as v1.2 |

### 1.1 Feed availability over the window (fraction of boundaries with the feed present — identical to v1.2, both passes)

| feed | BTC | ETH | neutral BTC | neutral ETH |
|---|---|---|---|---|
| candles (Binance) | 1.000 | 1.000 | 1.000 | 1.000 |
| book | 0.0179 | 0.0179 | 0.0179 | 0.0179 |
| cohort | 0.0179 | 0.0179 | 0.0179 | 0.0179 |
| events | 0.0128 | 0.0128 | 0.0128 | 0.0128 |
| gauge | 0.0180 | 0.0180 | 0.0180 | 0.0180 |
| liq | 0.1685 | 0.1685 | 0.1685 | 0.1685 |
| liqmap | 0.1686 | 0.1684 | 0.1686 | 0.1684 |
| oi | 0.1690 | 0.1690 | 0.1690 | 0.1690 |
| taker | 0.0179 | 0.0179 | 0.0179 | 0.0179 |

boundaries: {"BTC": 17281, "ETH": 17281}; absent: {"BTC": {"taker": 16971, "gauge": 16970, "events": 17059, "cohort": 16971, "liq": 14370, "book": 16971, "oi": 14360, "liqmap": 14368}, "ETH": {"taker": 16971, "gauge": 16970, "events": 17059, "cohort": 16971, "liq": 14370, "book": 16971, "oi": 14360, "liqmap": 14371}}

### 1.2 Definitions (v1.2 terms unchanged; v1.3 terms added)

- **setup row**: a boundary where the model's precondition completed and a Mind evaluation was produced (`raw_conviction` present); **non-vetoed**: no veto hit; **take**: `fired = 1` (conviction ≥ 0.55, tier full ≥ 0.75 / half otherwise) with a trade row; **filled + exited**: the paper trade filled and closed inside the window; **unfilled**: the resting entry was cancelled at `entry_valid_until` (`cancelled:entry_expired`) or, for M1, the FVG second attempt found no gap (`cancelled:entry_expired_no_fvg`).
- **R**: (exit − entry)/(entry − initial stop) signed by direction, where the initial stop is the FINAL stop after the floor (`lifecycle_json.initial_stop`); **PF**: gross wins / gross losses; **max DD**: largest peak-to-trough of cumulative R in take order; **hold**: minutes from fill to exit.
- **conviction bucket**: floor to 0.1 of the final (capped) conviction; **day type**: the `day_type` field on the signal row.
- **excluded reason**: a reason whose feed was in the D-80 unavailable set at that boundary; removed from the denominator and the numerator.
- **stop floor applied** (`strat_trades.stop_floor_applied`, D-88): 1 when `|entry − structural stop| < 0.5 ATR` and the stop was moved out to exactly that distance; `lifecycle_json.stop_structural` keeps the pre-floor stop. Targets were computed by the model from the structural stop and are not moved.
- **reclaim type** (M1/M5, D-89): `confirmation_used = 0` = case (a), the reclaim close is on a later candle than the candle carrying the sweep wick; `confirmation_used = 1` = case (b), sweep wick and reclaim are the same candle and one additional 15m candle closed on the original side of the level with its extreme not beyond the wick. `reclaim_candles` = `Sweep.candles_to_reclaim` = candles from the first candle beyond the level to the reclaim close, inclusive (the wick may deepen on a later candle than the first breach, which is why a `reclaim_candles = 2` row can be either type).
- **rejected + re-quoted** (`strat_trades.entry_requoted`, D-87): 1 when the post-only limit was at/above the touch (buy) or at/below it (sell), was rejected and re-quoted one tick inside; `entry_px_orig` = the original limit, `entry_px` = the resting (re-quoted) price. **fill − original** = mean of `(entry_px − entry_px_orig) / entry_px_orig × 10⁴` over re-quoted, filled trades, raw sign (positive = the fill was higher than the original limit); the favourable-signed version is given beside it in §4.2.
- **re-price path rejection**: the same post-only rule applied when a model re-prices an already-resting order (M6 `reprice_at`, M1 FVG mid); recorded in `lifecycle_json.post_only_reprice`, not in `entry_requoted`.

## 2. Funnel (identical in both passes except where the neutral pass's earlier outcomes feed `recent_form` — §3.4)

207,372 evaluation rows adverse / 207,372 neutral; 125 model trades in the adverse pass, 135 in the neutral pass.

| model | coin | setup rows | vetoed | non-vetoed | takes (filled) adverse | full / half | takes (filled) neutral | v1.2 takes (filled) adverse | veto counts | unevaluated vetoes on setup rows |
|---|---|---|---|---|---|---|---|---|---|---|
| M1 | BTC | 642 | 549 | 93 | 40 (25) | 28 / 12 | 45 (28) | 70 (66) | third_sweep 427, too_deep 368, cluster_below_uncleared 22, event_30m 1 | cluster_below_uncleared 538 |
| M1 | ETH | 602 | 511 | 91 | 41 (26) | 20 / 21 | 46 (27) | 69 (63) | third_sweep 374, too_deep 348, cluster_below_uncleared 9, trend_against 1, event_30m 1 | cluster_below_uncleared 487 |
| M2 | BTC | 8 | 8 | 0 | 0 (0) | 0 / 0 | 0 (0) | 0 (0) | stop_too_wide 8, zone_premium 5 | short_covering 5, oi_exit_retrace 5 |
| M2 | ETH | 16 | 13 | 3 | 0 (0) | 0 / 0 | 0 (0) | 0 (0) | stop_too_wide 13, zone_premium 3 | short_covering 16, oi_exit_retrace 16 |
| M3 | BTC | 57 | 0 | 57 | 9 (6) | 1 / 8 | 9 (6) | 9 (6) | – | squeeze_risk 57, cohort_adding_longs 57, acceptance 56 |
| M3 | ETH | 39 | 1 | 38 | 5 (3) | 1 / 4 | 5 (3) | 5 (3) | acceptance 1 | squeeze_risk 39, cohort_adding_longs 39, acceptance 37 |
| M4 | BTC | 0 | 0 | 0 | 0 (0) | 0 / 0 | 0 (0) | 0 (0) | – | – |
| M4 | ETH | 0 | 0 | 0 | 0 (0) | 0 / 0 | 0 (0) | 0 (0) | – | – |
| M5 | BTC | 64 | 58 | 6 | 5 (2) | 3 / 2 | 5 (2) | 9 (6) | range_bad 47, too_deep 31, opened_outside 13 | – |
| M5 | ETH | 76 | 66 | 10 | 7 (4) | 3 / 4 | 7 (4) | 9 (6) | range_bad 44, too_deep 42, opened_outside 21 | – |
| M6 | BTC | 1,424 | 1,279 | 145 | 9 (8) | 2 / 7 | 9 (8) | 9 (7) | already_taken 499, too_late 444, daily_strong_down 364, shallow_loss 244, stop_too_wide 204, oi_falling 86 | oi_falling 1300 |
| M6 | ETH | 1,648 | 1,380 | 268 | 9 (8) | 2 / 7 | 9 (8) | 9 (8) | too_late 524, daily_strong_down 513, already_taken 431, shallow_loss 344, oi_falling 128, stop_too_wide 112 | oi_falling 1476 |

Setup-row check vs v1.2 (same signals data expected for M2/M3/M4/M6; M1/M5 change under D-89):

| model | coin | setup rows v1.2 | v1.3 | non-vetoed v1.2 | v1.3 | takes v1.2 | v1.3 |
|---|---|---|---|---|---|---|---|
| M1 | BTC | 776 | 642 | 146 | 93 | 70 | 40 |
| M1 | ETH | 770 | 602 | 155 | 91 | 69 | 41 |
| M2 | BTC | 8 | 8 | 0 | 0 | 0 | 0 |
| M2 | ETH | 16 | 16 | 3 | 3 | 0 | 0 |
| M3 | BTC | 57 | 57 | 57 | 57 | 9 | 9 |
| M3 | ETH | 39 | 39 | 38 | 38 | 5 | 5 |
| M4 | BTC | 0 | 0 | 0 | 0 | 0 | 0 |
| M4 | ETH | 0 | 0 | 0 | 0 | 0 | 0 |
| M5 | BTC | 88 | 64 | 10 | 6 | 9 | 5 |
| M5 | ETH | 92 | 76 | 12 | 10 | 9 | 7 |
| M6 | BTC | 1,424 | 1,424 | 149 | 145 | 9 | 9 |
| M6 | ETH | 1,648 | 1,648 | 268 | 268 | 9 | 9 |

Conviction histograms of the non-vetoed rows (final / raw), adverse pass, bucket floor 0.1:

| model | coin | final | raw |
|---|---|---|---|
| M1 | BTC | 0.1:1 0.2:7 0.3:9 0.4:19 0.5:22 0.6:6 0.7:17 0.8:7 0.9:4 1.0:1 | 0.2:1 0.3:2 0.4:6 0.5:20 0.6:28 0.7:26 0.8:10 |
| M1 | ETH | 0.1:2 0.2:4 0.3:17 0.4:23 0.5:14 0.6:11 0.7:13 0.8:2 0.9:2 1.0:3 | 0.2:1 0.3:4 0.4:11 0.5:22 0.6:32 0.7:14 0.8:6 0.9:1 |
| M2 | ETH | 0.1:1 0.2:2 | 0.2:1 0.3:2 |
| M3 | BTC | 0.2:4 0.3:21 0.4:14 0.5:12 0.6:5 0.7:1 | 0.3:7 0.4:25 0.5:23 0.6:1 0.7:1 |
| M3 | ETH | 0.2:2 0.3:11 0.4:19 0.5:3 0.6:2 0.7:1 | 0.3:4 0.4:19 0.5:14 0.6:1 |
| M5 | BTC | 0.5:1 0.6:2 0.7:2 0.9:1 | 0.5:2 0.6:2 0.7:2 |
| M5 | ETH | 0.4:2 0.5:2 0.6:3 0.7:1 0.8:1 0.9:1 | 0.3:1 0.4:2 0.5:2 0.6:3 0.7:1 0.8:1 |
| M6 | BTC | 0.2:56 0.3:24 0.4:52 0.5:7 0.6:4 0.7:2 | 0.2:4 0.3:80 0.4:44 0.5:9 0.6:4 0.7:4 |
| M6 | ETH | 0.2:76 0.3:60 0.4:84 0.5:42 0.6:4 0.7:1 0.8:1 | 0.2:48 0.3:36 0.4:44 0.5:39 0.6:74 0.7:25 0.8:2 |

M1 level types of the setup rows — BTC (642): pdh 83, asia_high 79, equal_highs_4h 59, pdl 52, london_high 51, pwh 50, asia_low 45, equal_lows_1h 38, 4h_ob_top 37, equal_highs_1h 26, equal_lows_4h 26, 4h_ob_bottom 25, london_low 22, pwl 22, 4h_fvg_top 13, liq_cluster_short 9, 4h_fvg_bottom 5. ETH (602): asia_high 83, pdl 74, pdh 53, asia_low 52, london_high 49, equal_highs_4h 39, 4h_ob_top 36, equal_lows_1h 35, equal_highs_1h 34, london_low 33, pwl 30, 4h_ob_bottom 26, equal_lows_4h 21, pwh 14, 4h_fvg_bottom 9, 4h_fvg_top 6, liq_cluster_long 5, liq_cluster_short 3.


### 2.1 What D-89 did to the M1 / M5 population

The setup-row check above is the control: M2, M3, M4 setup rows, veto counts and non-vetoed counts are identical to v1.2 to the row (their detectors, Minds and vetoes were not touched), M6 setup rows are identical and its non-vetoed count differs by 4 BTC rows (§5.4). M1 and M5 changed because `confirmed_reclaim` replaced `latest_reclaimed`:

- **M1 setup rows 1,546 → 1,244** (BTC 776 → 642, ETH 770 → 602), non-vetoed 301 → 184, takes 139 → 81 (adverse). A same-candle sweep + reclaim is no longer a setup on its own candle; it becomes one only if the very next candle confirms it (case b), and a later-candle reclaim is a setup only on the reclaim candle itself (case a). Rows that in v1.2 were setups on the sweep candle and again on the next candle now collapse to one confirmation row or none.
- **M5 setup rows 180 → 140** (BTC 88 → 64, ETH 92 → 76), non-vetoed 22 → 16, takes 18 → 12.
- Reclaim type of the setup rows (`confirmation_used`), both coins: M1 same-candle + confirmation 526, later-candle 718; M5 55 / 85. Of the takes (adverse): M1 64 same-candle-confirmed / 17 later-candle; M5 3 / 9 (§4).
- `reclaim_candles` on the setup rows (adverse, both coins): M1 1 → 422 rows (58 fired), 2 → 526 (24 fired), 3 → 296 (**0 fired**); M5 1 → 48 (3 fired), 2 → 65 (9 fired), 3 → 27 (0 fired). Three-candle reclaims never reach 0.55 on this window — every fired M1/M5 signal reclaimed within two candles of the first breach.
- The vetoes themselves did not change in kind: M1 `third_sweep` 801 and `too_deep` 716 remain the two that remove most setups (v1.2: 937 / 777 on the larger population); M5 `range_bad` 91, `too_deep` 73, `opened_outside` 34 (v1.2: 114 / 86 / 39, plus `already_taken` 4, which does not occur on the v1.3 population).

### 2.2 Conviction cap (D-90)

The cap engaged on **9 M1 signal rows in the adverse pass and 10 in the neutral pass** (`conviction = 1.0`; maximum in every other model: M2 0.4086, M3 0.7802, M5 0.9405, M6 0.855, M4 no rows). All 9 / 10 rows were above the full-tier line before the cap, so the cap changed no take, no tier and no size; the `1.0` bucket in §3.2 is these rows' filled trades (n = 2). Weights, vetoes, `SKIP_BELOW` 0.55 and `FULL_FROM` 0.70 are untouched.

## 3. Performance — adverse (primary) and neutral side by side

### 3.1 Summary (filled + exited takes, both coins)

| model | pass | takes | filled + exited | unfilled | n | win rate | expectancy R | PF | max DD R | sum R | hold mean / median min |
|---|---|---|---|---|---|---|---|---|---|---|---|
| M1 | adverse | 81 | 51 | 30 (cancelled:entry_expired_no_fvg 27, cancelled:entry_expired 3) | 51 | 39 % | −0.310 | 0.49 | 16.41 | −15.80 | 54 / 15 |
| M1 | neutral | 91 | 55 | 36 (cancelled:entry_expired_no_fvg 32, cancelled:entry_expired 4) | 55 | 42 % | −0.260 | 0.53 | 15.13 | −14.31 | 51 / 15 |
| M2 | adverse | 0 | 0 | 0 | 0 | – | – | – | – | – | – |
| M2 | neutral | 0 | 0 | 0 | 0 | – | – | – | – | – | – |
| M3 | adverse | 14 | 9 | 5 (cancelled:entry_expired 5) | 9 | 11 % | −0.485 | 0.56 | 9.88 | −4.37 | 49 / 15 |
| M3 | neutral | 14 | 9 | 5 (cancelled:entry_expired 5) | 9 | 11 % | −0.485 | 0.56 | 9.88 | −4.37 | 49 / 15 |
| M4 | adverse | 0 | 0 | 0 | 0 | – | – | – | – | – | – |
| M4 | neutral | 0 | 0 | 0 | 0 | – | – | – | – | – | – |
| M5 | adverse | 12 | 6 | 6 (cancelled:entry_expired 6) | 6 | 50 % | −0.302 | 0.53 | 2.67 | −1.81 | 54 / 45 |
| M5 | neutral | 12 | 6 | 6 (cancelled:entry_expired 6) | 6 | 50 % | −0.302 | 0.53 | 2.67 | −1.81 | 54 / 45 |
| M6 | adverse | 18 | 16 | 2 (cancelled:entry_expired 2) | 16 | 25 % | +0.001 | 1.00 | 4.54 | +0.02 | 321 / 175 |
| M6 | neutral | 18 | 16 | 2 (cancelled:entry_expired 2) | 16 | 25 % | +0.001 | 1.00 | 4.54 | +0.02 | 321 / 175 |

- M1 exit reasons — adverse: stop 21, stop_be 20, time_stop 7, thesis_failed 2, dead_trade 1. Neutral: stop_be 25, stop 20, time_stop 7, thesis_failed 2, dead_trade 1.
- M2 exit reasons — adverse: –. Neutral: –.
- M3 exit reasons — adverse: stop 7, time_stop 1, thesis_failed 1. Neutral: stop 7, time_stop 1, thesis_failed 1.
- M4 exit reasons — adverse: –. Neutral: –.
- M5 exit reasons — adverse: stop 3, stop_be 2, time_stop 1. Neutral: stop 3, stop_be 2, time_stop 1.
- M6 exit reasons — adverse: thesis_failed 9, stop_be 2, stop 2, target 2, time_stop 1. Neutral: thesis_failed 9, stop_be 2, stop 2, target 2, time_stop 1.


Reading it against v1.2 (full comparison in §5): M1's expectancy improved (−0.351 → −0.310 adverse, −0.376 → −0.260 neutral) and its drawdown fell by two thirds, but on 51 / 55 filled trades instead of 129 — 30 / 36 of the 81 / 91 takes never filled (37 % / 40 %), because the D-89 entry rests below the market and the FVG second attempt found no gap on 27 / 32 of them. M3 is the same 14 takes and 9 fills, worse per trade (−0.220 → −0.485) for one reason: the stop floor doubled every M3 stop distance while the targets stayed where the structural stop had put them (§4.1). M5 improved on fewer fills (12 → 6). M6 is trade-for-trade the same set of 18 takes bar one (§5.4) and ends the window at +0.02 R. M3, M5 and M6 are trade-for-trade identical in the two passes; the neutral rule met 7 both-inside candles (favourable-first on 5), and the 2 that flipped a trade present in both passes are both M1 (§3.4).

No model is positive over the window under the fallback normaliser, as in v1.2; the caveat is unchanged — 83 % of the window has no tape and no OI, 98 % no taker / cohort / book / gauge (§11).

### 3.2 M1 — splits, adverse vs neutral

**adverse**

| split | n | win rate | expectancy R | PF | max DD R | sum R | hold mean min | hold median min |
|---|---|---|---|---|---|---|---|---|
| all | 51 | 39% | -0.310 | 0.49 | 16.41 | -15.80 | 54 | 15 |
| BTC | 25 | 40% | -0.234 | 0.62 | 6.08 | -5.84 | 59 | 15 |
| ETH | 26 | 38% | -0.383 | 0.36 | 10.56 | -9.95 | 50 | 15 |
| conviction 0.5 | 10 | 40% | -0.246 | 0.65 | 4.07 | -2.46 | 52 | 0 |
| conviction 0.6 | 9 | 33% | -0.474 | 0.03 | 4.29 | -4.27 | 37 | 15 |
| conviction 0.7 | 21 | 33% | -0.432 | 0.37 | 9.07 | -9.07 | 39 | 15 |
| conviction 0.8 | 5 | 20% | -0.446 | 0.43 | 2.60 | -2.23 | 51 | 0 |
| conviction 0.9 | 4 | 100% | 0.748 | inf | 0.00 | 2.99 | 146 | 165 |
| conviction 1.0 | 2 | 50% | -0.380 | 0.44 | 1.37 | -0.76 | 128 | 128 |
| day type range | 51 | 39% | -0.310 | 0.49 | 16.41 | -15.80 | 54 | 15 |
| tier full | 32 | 41% | -0.283 | 0.54 | 9.68 | -9.07 | 60 | 15 |
| tier half | 19 | 37% | -0.354 | 0.41 | 7.42 | -6.73 | 45 | 10 |

**neutral**

| split | n | win rate | expectancy R | PF | max DD R | sum R | hold mean min | hold median min |
|---|---|---|---|---|---|---|---|---|
| all | 55 | 42% | -0.260 | 0.53 | 15.13 | -14.31 | 51 | 15 |
| BTC | 28 | 43% | -0.203 | 0.64 | 6.05 | -5.67 | 54 | 15 |
| ETH | 27 | 41% | -0.320 | 0.41 | 9.45 | -8.63 | 49 | 15 |
| conviction 0.5 | 10 | 50% | -0.102 | 0.82 | 4.07 | -1.02 | 53 | 2 |
| conviction 0.6 | 13 | 31% | -0.454 | 0.05 | 6.04 | -5.90 | 28 | 10 |
| conviction 0.7 | 21 | 38% | -0.352 | 0.43 | 7.38 | -7.38 | 40 | 15 |
| conviction 0.8 | 5 | 20% | -0.446 | 0.43 | 2.60 | -2.23 | 51 | 0 |
| conviction 0.9 | 4 | 100% | 0.748 | inf | 0.00 | 2.99 | 146 | 165 |
| conviction 1.0 | 2 | 50% | -0.380 | 0.44 | 1.37 | -0.76 | 128 | 128 |
| day type range | 55 | 42% | -0.260 | 0.53 | 15.13 | -14.31 | 51 | 15 |
| tier full | 32 | 44% | -0.231 | 0.59 | 7.99 | -7.38 | 60 | 15 |
| tier half | 23 | 39% | -0.301 | 0.42 | 9.07 | -6.92 | 38 | 5 |


The four filled trades in the 0.9 bucket are the only positive bucket (+0.748 R, all wins, both passes); the 0.6–0.8 buckets are the worst at −0.43 to −0.47 (adverse), as in v1.2 higher conviction is not better. Tier full 32 trades in both passes (−0.283 / −0.231); the neutral pass adds 4 half-tier trades (§3.4).

### 3.3 M3, M5, M6 — splits (identical in both passes, adverse tables shown)

**M3**

| split | n | win rate | expectancy R | PF | max DD R | sum R | hold mean min | hold median min |
|---|---|---|---|---|---|---|---|---|
| all | 9 | 11% | -0.485 | 0.56 | 9.88 | -4.37 | 49 | 15 |
| BTC | 6 | 17% | -0.192 | 0.83 | 6.67 | -1.15 | 68 | 8 |
| ETH | 3 | 0% | -1.072 | 0.00 | 3.22 | -3.22 | 13 | 15 |
| conviction 0.5 | 3 | 0% | -1.305 | 0.00 | 3.92 | -3.92 | 15 | 15 |
| conviction 0.6 | 6 | 17% | -0.075 | 0.92 | 5.97 | -0.45 | 67 | 8 |
| day type range | 9 | 11% | -0.485 | 0.56 | 9.88 | -4.37 | 49 | 15 |
| tier half | 9 | 11% | -0.485 | 0.56 | 9.88 | -4.37 | 49 | 15 |

**M5**

| split | n | win rate | expectancy R | PF | max DD R | sum R | hold mean min | hold median min |
|---|---|---|---|---|---|---|---|---|
| all | 6 | 50% | -0.302 | 0.53 | 2.67 | -1.81 | 54 | 45 |
| BTC | 2 | 50% | -0.293 | 0.50 | 1.18 | -0.59 | 38 | 38 |
| ETH | 4 | 50% | -0.306 | 0.55 | 1.48 | -1.23 | 62 | 45 |
| conviction 0.5 | 1 | 0% | -1.304 | 0.00 | 1.30 | -1.30 | 0 | 0 |
| conviction 0.7 | 3 | 67% | 0.213 | 1.54 | 1.18 | 0.64 | 78 | 75 |
| conviction 0.8 | 1 | 0% | -1.407 | 0.00 | 1.41 | -1.41 | 15 | 15 |
| conviction 0.9 | 1 | 100% | 0.259 | inf | 0.00 | 0.26 | 75 | 75 |
| day type range | 6 | 50% | -0.302 | 0.53 | 2.67 | -1.81 | 54 | 45 |
| tier full | 5 | 60% | -0.102 | 0.80 | 1.41 | -0.51 | 65 | 75 |
| tier half | 1 | 0% | -1.304 | 0.00 | 1.30 | -1.30 | 0 | 0 |

**M6**

| split | n | win rate | expectancy R | PF | max DD R | sum R | hold mean min | hold median min |
|---|---|---|---|---|---|---|---|---|
| all | 16 | 25% | 0.001 | 1.00 | 4.54 | 0.02 | 321 | 175 |
| BTC | 8 | 25% | 0.086 | 1.20 | 3.51 | 0.69 | 253 | 175 |
| ETH | 8 | 25% | -0.083 | 0.70 | 1.05 | -0.67 | 389 | 175 |
| conviction 0.5 | 6 | 17% | -0.133 | 0.64 | 2.21 | -0.80 | 260 | 100 |
| conviction 0.6 | 6 | 33% | 0.190 | 1.39 | 1.79 | 1.14 | 491 | 320 |
| conviction 0.7 | 3 | 33% | -0.102 | 0.44 | 0.55 | -0.31 | 147 | 160 |
| conviction 0.8 | 1 | 0% | -0.012 | 0.00 | 0.01 | -0.01 | 190 | 190 |
| day type range | 16 | 25% | 0.001 | 1.00 | 4.54 | 0.02 | 321 | 175 |
| tier full | 4 | 25% | -0.080 | 0.43 | 0.56 | -0.32 | 158 | 175 |
| tier half | 12 | 25% | 0.028 | 1.07 | 3.98 | 0.34 | 375 | 230 |


### 3.4 Adverse vs neutral — where the 10-take M1 difference comes from

Raw conviction, multipliers other than `recent_form`, and every veto are identical on all 207,372 rows of the two passes; the signal tables differ only through `recent_form`, which reads the model's own recent outcomes (D-82). The neutral fill rule changed **two** M1 candles:

| signal ts | coin | dir | adverse | neutral |
|---|---|---|---|---|
| 2026-05-01 13:30 | BTC | short | stop −1.439 | stop_be +0.249 (both-inside candle, target nearer the open) |
| 2026-08-07 12:30 | ETH | short | stop −1.230 | stop_be +0.210 (both-inside candle) |

Each flip moves `recent_form` from 0.8 to 1.0 for the evaluations that follow, and that multiplier alone produced **10 neutral-only takes and 0 adverse-only takes** — all M1, all half tier:

| signal ts | coin | dir | neutral conviction | reclaim type | floor | neutral outcome |
|---|---|---|---|---|---|---|
| 2026-05-07 00:00 | BTC | long | 0.6218 | later-candle | 1 | stop −1.525 |
| 2026-05-09 04:30 | ETH | short | 0.6472 | same + confirmation | 0 | stop_be −0.119 |
| 2026-08-12 13:00 | ETH | short | 0.5998 | same + confirmation | 0 | cancelled:entry_expired |
| 2026-08-17 16:15 | BTC | short | 0.5735 | same + confirmation | 1 | cancelled:entry_expired_no_fvg |
| 2026-08-24 07:30 | BTC | short | 0.6531 | later-candle | 0 | stop_be −0.132 |
| 2026-08-24 10:30 | ETH | short | 0.5916 | same + confirmation | 0 | cancelled:entry_expired_no_fvg |
| 2026-08-26 15:45 | BTC | long | 0.5526 | same + confirmation | 0 | cancelled:entry_expired_no_fvg |
| 2026-08-30 17:00 | ETH | short | 0.6104 | same + confirmation | 0 | cancelled:entry_expired_no_fvg |
| 2026-09-01 19:15 | ETH | long | 0.5543 | same + confirmation | 0 | cancelled:entry_expired_no_fvg |
| 2026-09-03 13:15 | BTC | short | 0.6820 | same + confirmation | 0 | stop_be +0.138 |

One further row is taken in both passes at a different tier: 2026-08-11 16:00 ETH long, conviction 0.6795 half (adverse) → 0.8494 full (neutral), unfilled in both. Reconciliation of the sum: adverse −15.80 + (0.249 + 1.439) + (0.210 + 1.230) + (−1.525 − 0.119 − 0.132 + 0.138) = −14.31 = neutral. The mechanism is the one recorded in D-82; nothing was changed (D-96). The adverse pass stays primary.

## 4. v1.3 splits (Part 5) — stop floor, reclaim type, post-only re-quotes

`n / win / exp R / PF / sum R` columns are over filled + exited trades; "(takes)" columns count trade rows including unfilled ones. Mean fill − original is over re-quoted trades that filled (all 10 did), raw sign.

| model | pass | takes | floor applied (takes) | floor applied n / win / exp R / PF / sum R | structural stop kept n / win / exp R / PF / sum R | same-candle + confirmation (takes) n / win / exp R | later-candle reclaim (takes) n / win / exp R | rejected + re-quoted | of which filled | mean fill − original | re-price path rejections |
|---|---|---|---|---|---|---|---|---|---|---|---|
| M1 | adverse | 81 | 12 | 7 / 43 % / −0.787 / 0.08 / −5.51 | 44 / 39 % / −0.234 / 0.59 / −10.29 | (64) 36 / 39 % / −0.330 | (17) 15 / 40 % / −0.260 | 10 / 81 (12.3 %) | 10 | −2.16 (+4.74 bps) | 0 |
| M1 | neutral | 91 | 14 | 8 / 38 % / −0.879 / 0.06 / −7.03 | 47 / 43 % / −0.155 / 0.68 / −7.27 | (72) 38 / 45 % / −0.230 | (19) 17 / 35 % / −0.327 | 10 / 91 (11.0 %) | 10 | −2.16 (+4.74 bps) | 0 |
| M2 | adverse | 0 | 0 | 0 | 0 | n/a | n/a | 0 / 0 | 0 | – | 0 |
| M2 | neutral | 0 | 0 | 0 | 0 | n/a | n/a | 0 / 0 | 0 | – | 0 |
| M3 | adverse | 14 | 14 | 9 / 11 % / −0.485 / 0.56 / −4.37 | 0 | n/a | n/a | 0 / 14 (0.0 %) | 0 | – | 0 |
| M3 | neutral | 14 | 14 | 9 / 11 % / −0.485 / 0.56 / −4.37 | 0 | n/a | n/a | 0 / 14 (0.0 %) | 0 | – | 0 |
| M4 | adverse | 0 | 0 | 0 | 0 | n/a | n/a | 0 / 0 | 0 | – | 0 |
| M4 | neutral | 0 | 0 | 0 | 0 | n/a | n/a | 0 / 0 | 0 | – | 0 |
| M5 | adverse | 12 | 1 | 1 / 0 % / −1.407 / 0.00 / −1.41 | 5 / 60 % / −0.081 / 0.84 / −0.41 | (3) 1 / 100 % / +0.594 | (9) 5 / 40 % / −0.481 | 0 / 12 (0.0 %) | 0 | – | 0 |
| M5 | neutral | 12 | 1 | 1 / 0 % / −1.407 / 0.00 / −1.41 | 5 / 60 % / −0.081 / 0.84 / −0.41 | (3) 1 / 100 % / +0.594 | (9) 5 / 40 % / −0.481 | 0 / 12 (0.0 %) | 0 | – | 0 |
| M6 | adverse | 18 | 1 | 1 / 100 % / +3.960 / inf / +3.96 | 15 / 20 % / −0.263 / 0.31 / −3.94 | n/a | n/a | 0 / 18 (0.0 %) | 0 | – | 3 |
| M6 | neutral | 18 | 1 | 1 / 100 % / +3.960 / inf / +3.96 | 15 / 20 % / −0.263 / 0.31 / −3.94 | n/a | n/a | 0 / 18 (0.0 %) | 0 | – | 3 |


### 4.1 Stop floor (D-88) — how far the stops moved and what it did

Mean stop distance in bps of entry, adverse pass (`|entry − stop| / entry × 10⁴`, structural → final):

| model | floor | takes | structural | final | note |
|---|---|---|---|---|---|
| M1 | 0 | 69 | 34.4 | 34.4 | structural stop kept |
| M1 | 1 | 12 | 24.0 | 27.8 | +16 % on the floored trades |
| M3 | 1 | **14 of 14** | 10.4 | 20.8 | every M3 stop doubled |
| M5 | 0 | 11 | 31.0 | 31.0 | |
| M5 | 1 | 1 | 14.5 | 14.7 | |
| M6 | 0 | 17 | 170.9 | 170.9 | 1h-ATR floor rarely binds on a weekly-open stop |
| M6 | 1 | 1 | 20.5 | 23.0 | |

- **M1**: floored trades are the worst split of the report — 7 filled, 43 % win, **−0.787 R**, PF 0.08 (neutral 8, −0.879) against −0.234 (neutral −0.155) with the structural stop kept. Their T1 is still 1.5 × the structural risk, i.e. less than 1.5 final-R (D-88), so a floored M1 trade risks more to reach the same target; on this sample 4 of the 7 were losses (−1.327, −1.277, −1.407 and #683's −1.947, which is the D-97 FVG defect, not the floor).
- **M3**: the floor applied to every take. M3's structural stop is the raid low/high a few bps away (10.4 bps mean); 0.5 ATR(15m) is about twice that. Because targets are not recomputed, each M3 trade now risks 2× to reach the same price: 8 of the same 9 fills exit at the same price and reason and every R is halved; the ninth (#628) was a stop in v1.2 that the wider stop no longer reached, so it ran on to a thesis_failed exit — e.g. #579 time_stop +11.033 → +5.517, #586 stop −1.701 → −1.350, #628 stop −1.554 → thesis_failed −0.651. Expectancy −0.220 → −0.485 and PF 0.85 → 0.56 are entirely this arithmetic (D-94); win rate is unchanged at 11 %.
- **M5 / M6**: one floored trade each. M5's is a loss (−1.407). M6's is #690 BTC long 2026-07-23, target +4.425 → +3.960 R after the floor (structural 20.5 → 23.0 bps) — the same exit, smaller R.

### 4.2 Post-only rejections and re-quotes (D-87)

**10 of 81 M1 takes (12.3 %) in the adverse pass and 10 of 91 (11.0 %) in the neutral pass were rejected at placement and re-quoted; the same ten trades in both passes; 0 in M3, M5, M6.** All ten re-quotes filled. The ten, with the original limit, the resting price and the outcome:

| signal ts | coin | dir | original limit | touch at placement | re-quote (rested / filled) | fill − original | exit | R |
|---|---|---|---|---|---|---|---|---|
| 2026-03-13 13:45 | ETH | short | 2182.908 | 2187.77 | 2187.99 | +23.28 bps | stop | −1.067 |
| 2026-03-30 08:15 | ETH | short | 2059.185 | 2062.16 | 2062.37 | +15.47 bps | stop | −1.179 |
| 2026-04-11 19:00 | ETH | short | 2295.463 | 2304.38 | 2304.61 | +39.85 bps | stop | −1.141 |
| 2026-04-21 07:45 | BTC | short | 76148.85 | 76154.9 | 76162.5 | +1.79 bps | stop | −1.322 |
| 2026-04-23 09:30 | ETH | long | 2320.327 | 2320.19 | 2319.96 | −1.58 bps | stop | −1.123 |
| 2026-04-24 09:30 | BTC | long | 77467.91 | 77458.0 | 77450.2 | −2.29 bps | time_stop | +3.099 |
| 2026-04-29 18:30 | BTC | long | 75130.13 | 75107.6 | 75100.1 | −4.00 bps | time_stop | +2.075 |
| 2026-07-05 02:45 | ETH | long | 1759.646 | 1757.39 | 1757.21 | −13.84 bps | time_stop | +2.942 |
| 2026-07-06 09:00 | ETH | long | 1765.275 | 1764.97 | 1764.79 | −2.75 bps | stop | −1.297 |
| 2026-07-23 07:30 | ETH | long | 1916.441 | 1914.99 | 1914.80 | −8.56 bps | stop | −1.367 |

- **Mean fill − original, raw sign: +4.74 bps** (−2.16 price units; the sign is dominated by the three ETH shorts whose original limits sat 15–40 bps below the touch). **Favourable-signed (always an improvement for a post-only re-quote inside the touch): mean +11.34 bps, min 1.58, max 39.85.** Every re-quote rests one tick inside the touch, so the fill is by construction at a better price than the original limit would have been; what it costs is the fill itself, which in this sample it never did.
- Why M1: the D-89 entry is `min(mid, close) − 0.05 ATR` of the reclaim candle, placed on the confirmation close; on these ten placements the touch had already moved past that limit (all ten are case (b), where the order is placed one candle after the reclaim candle). M5 uses the same entry rule and had no re-quote on its 12 placements; M3 and M6 place at levels away from the market.
- Outcome of the ten: 7 stops, 3 time_stops, sum −0.380 R, 30 % win — not distinguishable from the rest of M1 on n = 10.

### 4.3 Reclaim type (D-89) — same-candle + confirmation vs later-candle

| model | pass | same-candle + confirmation | later-candle |
|---|---|---|---|
| M1 | adverse | 64 takes, 36 filled, 39 % win, −0.330 R | 17 takes, 15 filled, 40 % win, −0.260 R |
| M1 | neutral | 72 takes, 38 filled, 45 % win, −0.230 R | 19 takes, 17 filled, 35 % win, −0.327 R |
| M5 | both | 3 takes, 1 filled, +0.594 R | 9 takes, 5 filled, 40 % win, −0.481 R |

The same-candle case is the majority of M1 takes (79 %) and fills only 56 % of the time (36 / 64 adverse) against 88 % (15 / 17) for the later-candle case: the case-(b) entry is placed on the confirming candle's close, which is one candle past the reclaim candle whose range sets the limit. The unfilled split by reclaim type (adverse): same-candle 25 `no_fvg` + 3 `expired`, later-candle 2 `no_fvg`. Expectancy differences between the two types (−0.330 vs −0.260 adverse, −0.230 vs −0.327 neutral) change sign between the passes on n = 15–17 — no reading (D-93).

### 4.4 Re-price path rejections

The post-only rule also applies when a model re-prices a resting order. **3 M6 re-prices were rejected and re-quoted** (same in both passes): #660 ETH short 2026-06-18 (original 1680.06 vs touch 1704.79 → rested 1704.96; time_stop, R −0.655 in v1.2 → +0.107), #679 BTC short 2026-07-07 (63275.80 vs 63310.0 → 63316.33; thesis_failed −0.742 → −0.693), #685 ETH long 2026-07-20 (1902.56 vs 1897.69 → 1897.50; target +1.197 → +1.415). 0 M1 FVG re-prices were rejected (8 FVG second attempts, all rested at the FVG mid — see the D-97 defect in §13 for what that means when the mid is beyond T1).

## 5. v1.2 versus v1.3, per model (same data, same window, both passes)

**adverse pass**

| model | takes | filled + exited | unfilled | win rate | expectancy R | PF | max DD R | sum R | exit reasons (v1.2 → v1.3) |
|---|---|---|---|---|---|---|---|---|---|
| M1 | 139 → 81 | 129 → 51 | 10 → 30 | 34 % → 39 % | −0.351 → −0.310 | 0.47 → 0.49 | 56.97 → 16.41 | −45.25 → −15.80 | stop 68, stop_be 35, dead_trade 8, target 8, time_stop 7, thesis_failed 3 → stop 21, stop_be 20, time_stop 7, thesis_failed 2, dead_trade 1 |
| M2 | 0 → 0 | 0 → 0 | 0 → 0 | – → – | – → – | – → – | – → – | – → – | – → – |
| M3 | 14 → 14 | 9 → 9 | 5 → 5 | 11 % → 11 % | −0.220 → −0.485 | 0.85 → 0.56 | 13.02 → 9.88 | −1.98 → −4.37 | stop 8, time_stop 1 → stop 7, time_stop 1, thesis_failed 1 |
| M4 | 0 → 0 | 0 → 0 | 0 → 0 | – → – | – → – | – → – | – → – | – → – | – → – |
| M5 | 18 → 12 | 12 → 6 | 6 → 6 | 33 % → 50 % | −0.437 → −0.302 | 0.40 → 0.53 | 6.94 → 2.67 | −5.24 → −1.81 | stop 7, stop_be 3, time_stop 2 → stop 3, stop_be 2, time_stop 1 |
| M6 | 18 → 18 | 15 → 16 | 3 → 2 | 20 % → 25 % | −0.030 → +0.001 | 0.93 → 1.00 | 5.26 → 4.54 | −0.46 → +0.02 | thesis_failed 8, stop_be 2, stop 2, target 2, time_stop 1 → thesis_failed 9, stop_be 2, stop 2, target 2, time_stop 1 |

**neutral pass**

| model | takes | filled + exited | unfilled | win rate | expectancy R | PF | max DD R | sum R | exit reasons (v1.2 → v1.3) |
|---|---|---|---|---|---|---|---|---|---|
| M1 | 138 → 91 | 129 → 55 | 9 → 36 | 34 % → 42 % | −0.376 → −0.260 | 0.43 → 0.53 | 60.21 → 15.13 | −48.50 → −14.31 | stop 67, stop_be 37, dead_trade 8, time_stop 7, target 7, thesis_failed 3 → stop_be 25, stop 20, time_stop 7, thesis_failed 2, dead_trade 1 |
| M2 | 0 → 0 | 0 → 0 | 0 → 0 | – → – | – → – | – → – | – → – | – → – | – → – |
| M3 | 14 → 14 | 9 → 9 | 5 → 5 | 11 % → 11 % | −0.220 → −0.485 | 0.85 → 0.56 | 13.02 → 9.88 | −1.98 → −4.37 | stop 8, time_stop 1 → stop 7, time_stop 1, thesis_failed 1 |
| M4 | 0 → 0 | 0 → 0 | 0 → 0 | – → – | – → – | – → – | – → – | – → – | – → – |
| M5 | 18 → 12 | 12 → 6 | 6 → 6 | 33 % → 50 % | −0.437 → −0.302 | 0.40 → 0.53 | 6.94 → 2.67 | −5.24 → −1.81 | stop 7, stop_be 3, time_stop 2 → stop 3, stop_be 2, time_stop 1 |
| M6 | 18 → 18 | 15 → 16 | 3 → 2 | 20 % → 25 % | −0.030 → +0.001 | 0.93 → 1.00 | 5.26 → 4.54 | −0.46 → +0.02 | thesis_failed 8, stop_be 2, stop 2, target 2, time_stop 1 → thesis_failed 9, stop_be 2, stop 2, target 2, time_stop 1 |


### 5.1 M1 — fewer, later, resting entries

Takes 139 → 81 (adverse) is D-89 alone (§2.1): the population of confirmable reclaims is smaller and each fires one to two candles later. Of the 81, 30 never fill (v1.2: 10 of 139) — the entry now rests `0.05 ATR` below `min(mid, close)` of the reclaim candle and waits for a print through it, and the FVG second attempt found no gap on 27. Per filled trade the result is better on every metric (win 34 % → 39 %, expectancy −0.351 → −0.310, PF 0.47 → 0.49, max DD 56.97 → 16.41, neutral −0.376 → −0.260) and v1.2's 8 `target` exits and 8 `dead_trade` exits are gone (`target` 0, `dead_trade` 1). The improvement is on n = 51 against n = 129 and the sign is still negative; nothing is tuned (D-93). Twelve of the 81 takes were floored and ten re-quoted (§4).

### 5.2 M3 — same trades, half the R

Identical 14 takes, 9 fills, 11 % win. Expectancy −0.220 → −0.485 and PF 0.85 → 0.56 are the stop floor doubling every M3 stop with targets left in place (§4.1, D-94). Kept as the spec says (size and R use the final stop; targets not in the order); flagged because it is the one place where v1.3 makes a model look worse without any trade changing.

### 5.3 M5 — smaller population, better per fill

Takes 18 → 12, fills 12 → 6 (D-89). Win 33 % → 50 %, expectancy −0.437 → −0.302, PF 0.40 → 0.53 on n = 6 — not a reading. One take floored, none re-quoted.

### 5.4 M6 — one signal moved, three re-prices re-quoted, one floor

M6's detector and Mind are untouched and its setup rows are identical, but the trade set differs by one: 2026-06-30 05:00 BTC fires in v1.3 (conviction 0.6261, `recent_form` 1.0) and not in v1.2 (0.5322, `recent_form` 0.85) — `recent_form` is the model's last five paper exits across both coins (`model_runner._recent_form`), and #660 ETH, whose re-quoted re-price turned −0.655 into +0.107 (§4.4), exited on 2026-06-19 12:00 inside that window; the multiplier read one more win than in v1.2. That signal becomes #669 (entry 59533.4, not rejected, filled 05:35, thesis_failed `lost_weekly_open` −0.085), vetoes 05:15–05:45 and 08:00 as `already_taken` (the 4 non-vetoed BTC rows of §2), and v1.2's 08:00 take (which expired unfilled) is absent. Net: fills 15 → 16, thesis_failed 8 → 9, expectancy −0.030 → +0.001, sum −0.46 → +0.02, of which the three re-quoted re-prices contribute (+0.107 + 0.655) + (−0.693 + 0.742) + (+1.415 − 1.197) = +1.03 R and the floor on #690 −0.465 R.

## 6. Frequency vs the docs (BTC + ETH together, 180 d = 6 × 30 d)

| model | doc frequency | per 30 d (doc) | AUDIT §8.6 range | takes / 180 d (adverse) | per 30 d | ratio | > 5× outside? | neutral | v1.2 adverse |
|---|---|---|---|---|---|---|---|---|---|
| M1 | 1 to 3 per day across BTC and ETH | 30–90 | 20–60 | 81 | 13.5 | 2.2x below | no | 91 → 15.2 | 139 → 23.2 |
| M2 | 0 to 2 per day | 0–60 | 10–30 | 0 | 0.0 | in range | no | 0 → 0.0 | 0 → 0.0 |
| M3 | 0 to 2 per day | 0–60 | 10–30 | 14 | 2.3 | in range | no | 14 → 2.3 | 14 → 2.3 |
| M4 | 2 to 4 per month per coin | 4–8 | 0–6 | 0 | 0.0 | inf below | **yes** | 0 → 0.0 | 0 → 0.0 |
| M5 | up to 2 per day per coin | 0–120 | 15–40 | 12 | 2.0 | in range | no | 12 → 2.0 | 18 → 3.0 |
| M6 | at most one per week per coin per direction | 0–17 | 0–8 | 18 | 3.0 | in range | no | 18 → 3.0 | 18 → 3.0 |

Per coin per 30 d (adverse): M1 BTC 6.7 / ETH 6.8; M3 BTC 1.5 / ETH 0.8; M5 BTC 0.8 / ETH 1.2; M6 BTC 1.5 / ETH 1.5.


M1 moved from 1.3× below the doc's lower bound to 2.2× below (13.5 per 30 d against 30–90); still inside the 5× line. M4 is unchanged at zero (D-85). No threshold is touched.

## 7. Unavailable handling (D-80) — excluded reasons and effective maximum weight

Unchanged mechanism; the counts differ from v1.2 only where the setup population changed (M1 1,546 → 1,244 rows, M5 180 → 140):

| model | total weight | setup rows | excluded reason counts | effective max weight distribution |
|---|---|---|---|---|
| M1 | 13.2 | 1,244 | delta_flip 1228, absorption 1228, cohort 1228, fuel 1025, cleared 1025 | 7.0: 1,025 · 9.7: 203 · 13.2: 16 |
| M2 | 13.5 | 24 | delta_break 24, oi_new_positioning 21, oi_holding 21, cluster_cleared 21 | 8.0: 21 · 12.3: 3 |
| M3 | 13.5 | 96 | cvd_divergence 96, trap 93, cluster_fuel 93 | 8.3: 93 · 11.5: 3 |
| M4 | – | 0 | – |  |
| M5 | 12.9 | 140 | delta_flip 139, cohort 139, fuel 112, cleared 112 | 8.5: 112 · 10.8: 27 · 12.9: 1 |
| M6 | 13.8 | 3,072 | delta_reclaim 3054, cohort 3054, oi_commitment 2776 | 8.5: 2,776 · 10.5: 278 · 13.8: 18 |


## 8. Fuel — M1 `fuel` and M3 `cluster_fuel`, takes above 0.7 vs below 0.3 (adverse pass)

| model | split | n | win rate | expectancy R | PF | max DD R | sum R | hold mean / median min |
|---|---|---|---|---|---|---|---|---|
| M1 | strength > 0.7 | 0 | – | – | – | – | – | – |
| M1 | strength < 0.3 (all) | 51 | 39 % | −0.310 | 0.49 | 16.04 | −15.80 | 54 / 15 |
| M1 | strength < 0.3 and the reason EVALUATED | 1 | 0 % | −1.230 | 0.00 | 1.23 | −1.23 | 0 / 0 |
| M1 | reason EXCLUDED | 50 | 40 % | −0.291 | 0.51 | 14.81 | −14.57 | 56 / 15 |
| M3 | strength > 0.7 | 0 | – | – | – | – | – | – |
| M3 | strength < 0.3 (all) | 9 | 11 % | −0.485 | 0.56 | 9.88 | −4.37 | 49 / 15 |
| M3 | strength < 0.3 and the reason EVALUATED | 0 | – | – | – | – | – | – |
| M3 | reason EXCLUDED | 9 | 11 % | −0.485 | 0.56 | 9.88 | −4.37 | 49 / 15 |

M1 fuel values non-zero: [0.02752036710972394]; M3: []


M1 `fuel_strength` was non-zero on 1 of 51 filled takes (0.028; v1.2: 5 of 129) and that one trade is the only "evaluated" row (−1.230 R, n = 1). M3's `cluster_fuel_strength` is 0.0 on all 9. The upper cell is empty for both models, as in v1.1 and v1.2 — the fuel dimension cannot be judged until the tape covers the window (D-81). The evaluated-fuel count fell from 7 to 1 because the tape-covered month (2026-08-07 → 09-06) holds 2 M1 takes / 1 fill in the v1.3 adverse pass against 8 / 8 in v1.2 (§2.1; eight of the ten neutral-only takes of §3.4 also fall in that month, six of them unfilled).

## 9. Structure statistics over the window

Not re-run for v1.3. D-87 … D-90 change the executor, the stop floor, the M1/M5 setup gate and the conviction cap; the trend / BOS / CHoCH / displacement / zone / session / weekly-open / sweep detectors (`structure/*.py` other than the new `confirmed_reclaim` helper, which reads `detect_sweeps` output and does not alter it) are byte-identical to v1.2, so REPLAY-6M-v1.2.md §7.1–§7.8 (`/root/struct_stats.json`, `/root/leg_stats_v12.json`) stand for this window unchanged. The M1/M5 population change of §2.1 is a change in which sweeps become setups, not in which sweeps are detected — §7.7 there (sweeps per level type, reclaim fraction, depth) is the same population this report's `confirmed_reclaim` filters.

## 10. M6 — exit reasons and the thesis_failed counterfactual (identical in both passes)

Exit reasons of the 16 filled + exited M6 trades: thesis_failed 9, stop_be 2, stop 2, target 2, time_stop 1 (v1.2: 8 / 2 / 2 / 2 / 1 on 15 — the ninth thesis_failed is #669, §5.4).

adverse: exit reasons thesis_failed 9, stop_be 2, stop 2, target 2, time_stop 1; counterfactual n=9 mean R at exit −0.182 vs if held +0.311, exit better 4
| trade | signal ts | coin | dir | R at thesis_failed | R if held | held-to | held exit ts |
|---|---|---|---|---|---|---|---|
| 640 | 2026-05-20 10:00 | BTC | long | -0.21 | -1.13 | stop | 2026-05-20 13:44 |
| 659 | 2026-06-16 20:00 | BTC | short | -0.38 | 0.55 | stop_be | 2026-06-17 15:59 |
| 669 | 2026-06-30 05:00 | BTC | short | -0.09 | 2.05 | target | 2026-07-01 01:14 |
| 679 | 2026-07-07 03:00 | BTC | short | -0.69 | 0.43 | stop_be | 2026-07-09 18:14 |
| 700 | 2026-09-03 03:00 | BTC | long | 0.24 | 3.39 | target | 2026-09-03 19:14 |
| 608 | 2026-04-07 02:00 | ETH | short | -0.08 | -1.04 | stop | 2026-04-07 21:14 |
| 629 | 2026-05-07 03:00 | ETH | short | -0.31 | 0.67 | time_stop | 2026-05-08 20:14 |
| 648 | 2026-05-26 01:00 | ETH | short | -0.11 | -1.07 | stop | 2026-05-26 10:29 |
| 670 | 2026-06-30 13:00 | ETH | short | -0.01 | -1.05 | stop | 2026-07-01 03:14 |

neutral: exit reasons thesis_failed 9, stop_be 2, stop 2, target 2, time_stop 1; counterfactual n=9 mean R at exit −0.182 vs if held +0.311, exit better 4
| trade | signal ts | coin | dir | R at thesis_failed | R if held | held-to | held exit ts |
|---|---|---|---|---|---|---|---|
| 641 | 2026-05-20 10:00 | BTC | long | -0.21 | -1.13 | stop | 2026-05-20 13:44 |
| 660 | 2026-06-16 20:00 | BTC | short | -0.38 | 0.55 | stop_be | 2026-06-17 15:59 |
| 670 | 2026-06-30 05:00 | BTC | short | -0.09 | 2.05 | target | 2026-07-01 01:14 |
| 680 | 2026-07-07 03:00 | BTC | short | -0.69 | 0.43 | stop_be | 2026-07-09 18:14 |
| 708 | 2026-09-03 03:00 | BTC | long | 0.24 | 3.39 | target | 2026-09-03 19:14 |
| 607 | 2026-04-07 02:00 | ETH | short | -0.08 | -1.04 | stop | 2026-04-07 21:14 |
| 629 | 2026-05-07 03:00 | ETH | short | -0.31 | 0.67 | time_stop | 2026-05-08 20:14 |
| 649 | 2026-05-26 01:00 | ETH | short | -0.11 | -1.07 | stop | 2026-05-26 10:29 |
| 671 | 2026-06-30 13:00 | ETH | short | -0.01 | -1.05 | stop | 2026-07-01 03:14 |

failing checks adverse: {"M1": {"no_higher_low": 12, "level_lost": 8}, "M2": {}, "M3": {"close_above_level": 4}, "M4": {}, "M5": {"below_asia_low": 1, "no_higher_low": 1}, "M6": {"no_progress": 49, "lost_weekly_open": 18}}


Mean R at the moment thesis_failed fired **−0.182** vs mean R had the trade been held to stop or target **+0.311** (n = 9; v1.2 −0.200 vs +0.091 on n = 8): exiting was better in 4 of 9 (the four that would have hit the full stop) and worse in 5 — the new ninth, #669, would have reached its +2.05 R target. Net −0.493 R per trade against the in-trade checks on this sample; two trades (#669 +2.05, #700 +3.39) decide the sign. Recorded, no change (D-84 stands); the failing checks over all M6 setup rows are `no_progress` 49 and `lost_weekly_open` 18.

## 11. Feed presence per model (fraction of replay boundaries where each required feed was present; mean of BTC / ETH)

Identical to v1.2 §9 — feed presence is a property of the data, which did not change (D-92):

| model | required feeds |
|---|---|
| M1 | candles 100 %, cohort 1.8 %, events 1.3 %, gauge 1.8 %, liq 16.8 %, liqmap 16.8 %, oi 16.9 %, taker 1.8 % |
| M2 | candles 100 %, events 1.3 %, gauge 1.8 %, liq 16.8 %, oi 16.9 %, taker 1.8 % |
| M3 | candles 100 %, book 1.8 %, cohort 1.8 %, events 1.3 %, gauge 1.8 %, liqmap 16.8 %, oi 16.9 %, taker 1.8 % |
| M4 | candles 100 %, cohort 1.8 %, events 1.3 %, gauge 1.8 %, liqmap 16.8 %, oi 16.9 %, taker 1.8 % |
| M5 | candles 100 %, cohort 1.8 %, events 1.3 %, gauge 1.8 %, liq 16.8 %, oi 16.9 %, taker 1.8 % |
| M6 | candles 100 %, cohort 1.8 %, events 1.3 %, gauge 1.8 %, oi 16.9 %, taker 1.8 % |

For 83 % of the window every model ran on candles alone; the Mind reasons that make M1/M3/M5/M6 differ from "structure + level" were either excluded (D-80 set) or scored 0.

## 12. Calibration history, NULL count, 0xArchive span and live_coverage

Unchanged from v1.2 (no `v12_setup.py` re-run, no new 0xArchive load — the key is still free tier, D-81):

| coin | key | as-of days | NULL days | first as-of | last as-of | first non-NULL | window min / max d |
|---|---|---|---|---|---|---|---|
| BTC | band_p80 | 182 | 182 | 2026-03-09 00:00 | 2026-09-06 00:00 | - | 0.0 / 29.5 |
| BTC | liq_5m_p90_long | 182 | 182 | 2026-03-09 00:00 | 2026-09-06 00:00 | - | 0.0 / 29.5 |
| BTC | liq_5m_p90_short | 182 | 182 | 2026-03-09 00:00 | 2026-09-06 00:00 | - | 0.0 / 29.5 |
| ETH | band_p80 | 182 | 182 | 2026-03-09 00:00 | 2026-09-06 00:00 | - | 0.0 / 29.5 |
| ETH | liq_5m_p90_long | 182 | 182 | 2026-03-09 00:00 | 2026-09-06 00:00 | - | 0.0 / 29.5 |
| ETH | liq_5m_p90_short | 182 | 182 | 2026-03-09 00:00 | 2026-09-06 00:00 | - | 0.0 / 29.5 |

NULL calibration lookups in log: adverse 34,562, neutral 34,562
`strat_calibration` rows at the end of each pass: BTC `live_coverage` 0.0787 (n 4,121, window 3.0 d), ETH `live_coverage` 0.1498 (n 962, window 3.0 d), both stamped 2026-09-07 00:00 UTC — the daily job values of §10.4 in REPLAY-6M-v1.2.md.


The 0xArchive span, the 30.5-day calibration window and the prod values are as in REPLAY-6M-v1.2.md §10.2–§10.3. live_coverage recomputed at the same job: **BTC 0.0787 (n 4,121), ETH 0.1498 (n 962)**.

## 13. Findings and decisions

1. **v1.3 is a rules-only re-measurement on the v1.2 data** (§0, §1; D-92): 0 tracebacks, 207,372 signal rows per pass, M2/M3/M4/M6 setup rows identical to v1.2 to the row, 74/74 engine files identical between the local tree, the replay tree and the deployed worker. The calibration caveat of v1.2 is unchanged — 182/182 NULL, fallback normaliser throughout — and so is the owner-side blocker (free-tier 0xArchive key, D-81).
2. **D-89 shrank and shifted the M1/M5 population; the per-fill result is better, the fill rate is worse** (§2.1, §3, §5.1, §5.3; **D-93**): M1 takes 139 → 81, fills 129 → 51, 37 % unfilled (v1.2 7 %); expectancy −0.351 → −0.310 (neutral −0.376 → −0.260), max DD 56.97 → 16.41, `target` and `dead_trade` exits gone. M5 18 → 12 takes, 12 → 6 fills, −0.437 → −0.302. Same-candle-confirmed reclaims are 79 % of M1 takes and fill 56 % of the time against 88 % for later-candle reclaims; the expectancy difference between the two types changes sign between the passes. No tuning — the 0.55 line, the 3-candle validity and the 0.05 ATR offset are the order's.
3. **The stop floor makes M3 look worse without changing a trade** (§4.1, §5.2; **D-94**): all 14 M3 takes floored, mean stop distance 10.4 → 20.8 bps, targets unchanged (D-88), so the same 9 fills halve their R: −0.220 → −0.485, PF 0.85 → 0.56. Twelve M1 takes floored (worst split, −0.787 R on n = 7, one of them the D-97 trade), one M5, one M6 (#690 +4.425 → +3.960). Kept exactly as the spec words it; the target question (recompute from the final stop, or leave) is the owner's — flagged, not changed.
4. **Post-only rejections happened on M1 only, 10 per pass (12.3 % / 11.0 %), all case (b), all filled at a better price than the original limit** (§4.2, §4.4; **D-95**): mean fill − original +4.74 bps raw / +11.34 bps favourable-signed (min 1.58, max 39.85); outcome of the ten −0.380 R total. Three M6 re-prices were rejected and re-quoted (+1.03 R net against v1.2 on those three). The rejection is recorded on the trade row (`entry_requoted`, `entry_px_orig`, `lifecycle_json.post_only` / `post_only_reprice`) and the live worker runs the same code.
5. **Adverse vs neutral differ by 10 M1 takes through `recent_form`, from two flipped candles** (§3.4; **D-96**): the same D-82 mechanism as v1.2 (there 2 flipped candles → 5 differences). 0 adverse-only takes. Adverse remains primary; nothing changed.
6. **Pre-existing defect, not in the v1.3 order, reported and NOT changed** (§4.4; **D-97**): `ModelTradeManager._pending`'s M1 FVG second attempt (`model_runner.py`, the `fvg_reclaim` branch) re-prices the resting entry to the FVG mid without recomputing the stop or T1. When the mid is beyond T1 the order fills already past its target; the T1 "partial" then executes at a loss and the break-even stop exits at entry. v1.3 adverse: 8 FVG second attempts, **4** filled beyond T1 (#602 −0.288, #603 −0.453, #663 −0.414, **#683 −1.947**: original 62530.14, FVG entry 63015.6, T1 62611, initial stop 62431.5, partial pnl −7.76). v1.2: 6 attempts, 2 such fills (#421 −0.385, #506 −0.183). Proposed fix, awaiting the owner's go: re-derive stop and targets from the re-priced entry (same structural distance), or refuse a re-price whose mid is at or beyond T1. Until then every M1 `fvg` trade whose entry is past `t1` is this defect, and M1's floored split carries one of them.
7. **The conviction cap engaged on 9 / 10 M1 rows and changed no take** (§2.2; **D-98**). No model's maximum other than M1 reaches 1.0.
8. **No model is positive over the window** (§3.1): M1 −0.310 / −0.260 R (n 51 / 55), M3 −0.485 (n 9), M5 −0.302 (n 6), M6 +0.001 (n 16, sum +0.02 R). Same structure-only caveat as v1.1 and v1.2.
9. What the next run needs is unchanged from v1.2 §11 item 8 (0xArchive Build plan or Data Catalog range → `oxarchive_load_v12.py` → `v12_setup.py` → `replay_v13.py` × 2 → `replay_stats_v13.py` × 2 → `verify_pack_v13.py`).

Decisions recorded from this report: D-93 (M1/M5 population under D-89, no tuning), D-94 (M3 all-floored with fixed targets, kept, flagged), D-95 (re-quote / re-price measurements), D-96 (adverse–neutral divergence = D-82, unchanged), D-97 (M1 FVG second-attempt defect — reported, fix proposed, not applied), D-98 (conviction cap engaged, no effect on takes) — `docs/models/DECISIONS.md`. D-92 corrected there: the driver logs are `/root/replay_v13_<fill>.out`, not `.log`.

## 14. Appendix — every take (adverse pass; neutral differences in §3.4)

The tables below are the adverse pass, verbatim from `/root/replay_stats_v13_adverse.md`. The neutral pass differs by the 10 M1 takes and the 2 flipped candles listed in §3.4 and by the one tier change (2026-08-11 16:00 ETH); M3, M5 and M6 are identical.

### 14.1 M1 BTC

| trade | signal ts | dir | tier | conviction | filled | exit_reason | R | hold min | day type | excluded reasons |
|---|---|---|---|---|---|---|---|---|---|---|
| 583 | 2026-03-14 07:00 | long | full | 0.798 | no | cancelled:entry_expired_no_fvg | - | - | range | fuel,cleared,delta_flip,absorption,cohort |
| 585 | 2026-03-19 13:30 | long | full | 0.792 | yes | stop_be | 0.24 | 45 | range | fuel,cleared,delta_flip,absorption,cohort |
| 589 | 2026-03-22 20:15 | long | half | 0.699 | yes | stop | -1.25 | 30 | range | fuel,cleared,delta_flip,absorption,cohort |
| 591 | 2026-03-24 08:15 | short | full | 0.901 | no | cancelled:entry_expired_no_fvg | - | - | range | fuel,cleared,delta_flip,absorption,cohort |
| 593 | 2026-03-25 05:30 | short | half | 0.599 | no | cancelled:entry_expired_no_fvg | - | - | range | fuel,cleared,delta_flip,absorption,cohort |
| 594 | 2026-03-26 09:00 | long | full | 0.740 | yes | stop_be | 0.22 | 0 | range | fuel,cleared,delta_flip,absorption,cohort |
| 596 | 2026-03-28 08:45 | short | full | 0.750 | yes | stop_be | -0.31 | 0 | range | fuel,cleared,delta_flip,absorption,cohort |
| 598 | 2026-03-30 08:30 | short | full | 0.928 | yes | stop_be | 0.35 | 165 | range | fuel,cleared,delta_flip,absorption,cohort |
| 599 | 2026-03-31 09:45 | long | full | 0.813 | yes | stop | -1.33 | 0 | range | fuel,cleared,delta_flip,absorption,cohort |
| 600 | 2026-03-31 10:15 | long | full | 0.872 | no | cancelled:entry_expired_no_fvg | - | - | range | fuel,cleared,delta_flip,absorption,cohort |
| 603 | 2026-03-31 17:45 | short | full | 0.759 | yes | stop_be | -0.45 | 0 | range | fuel,cleared,delta_flip,absorption,cohort |
| 610 | 2026-04-10 19:30 | short | half | 0.567 | yes | thesis_failed | -0.91 | 25 | range | fuel,cleared,delta_flip,absorption,cohort |
| 613 | 2026-04-14 07:30 | short | half | 0.636 | no | cancelled:entry_expired_no_fvg | - | - | range | fuel,cleared,delta_flip,absorption,cohort |
| 615 | 2026-04-20 14:45 | short | half | 0.594 | yes | stop_be | 0.45 | 0 | range | fuel,cleared,delta_flip,absorption,cohort |
| 616 | 2026-04-20 18:30 | short | full | 0.835 | yes | stop | -1.28 | 15 | range | fuel,cleared,delta_flip,absorption,cohort |
| 618 | 2026-04-21 07:45 | short | full | 0.717 | yes | stop | -1.32 | 30 | range | fuel,cleared,delta_flip,absorption,cohort |
| 620 | 2026-04-24 09:30 | long | full | 0.755 | yes | time_stop | 3.10 | 240 | range | fuel,cleared,delta_flip,absorption,cohort |
| 622 | 2026-04-29 18:30 | long | full | 0.951 | yes | time_stop | 2.07 | 240 | range | fuel,cleared,delta_flip,absorption,cohort |
| 623 | 2026-05-01 08:45 | short | full | 0.811 | no | cancelled:entry_expired_no_fvg | - | - | range | fuel,cleared,delta_flip,absorption,cohort |
| 624 | 2026-05-01 13:30 | short | full | 0.733 | yes | stop | -1.44 | 0 | range | fuel,cleared,delta_flip,absorption,cohort |
| 630 | 2026-05-12 14:30 | long | half | 0.660 | yes | stop_be | 0.11 | 15 | range | fuel,cleared,delta_flip,absorption,cohort |
| 631 | 2026-05-12 15:45 | long | full | 0.775 | yes | stop_be | -0.02 | 10 | range | fuel,cleared,delta_flip,absorption,cohort |
| 635 | 2026-05-14 16:30 | short | full | 0.920 | yes | stop_be | 0.09 | 15 | range | fuel,cleared,delta_flip,absorption,cohort |
| 638 | 2026-05-18 07:00 | long | half | 0.569 | no | cancelled:entry_expired_no_fvg | - | - | range | fuel,cleared,delta_flip,absorption,cohort |
| 641 | 2026-05-21 08:30 | short | full | 1.000 | no | cancelled:entry_expired_no_fvg | - | - | range | fuel,cleared,delta_flip,absorption,cohort |
| 643 | 2026-05-25 02:30 | short | full | 0.766 | no | cancelled:entry_expired_no_fvg | - | - | range | fuel,cleared,delta_flip,absorption,cohort |
| 651 | 2026-05-27 15:00 | long | half | 0.617 | no | cancelled:entry_expired_no_fvg | - | - | range | fuel,cleared,delta_flip,absorption,cohort |
| 652 | 2026-05-29 16:30 | short | full | 0.735 | yes | stop | -1.16 | 0 | range | fuel,cleared,delta_flip,absorption,cohort |
| 654 | 2026-06-02 07:45 | long | full | 0.714 | yes | stop | -1.15 | 45 | range | fuel,cleared,delta_flip,absorption,cohort |
| 657 | 2026-06-11 04:15 | short | half | 0.622 | yes | stop | -1.13 | 120 | range | fuel,cleared,delta_flip,absorption,cohort |
| 658 | 2026-06-12 10:00 | short | full | 0.727 | no | cancelled:entry_expired_no_fvg | - | - | range | fuel,cleared,delta_flip,absorption,cohort |
| 661 | 2026-06-18 16:15 | long | full | 0.870 | yes | time_stop | 1.66 | 240 | range | fuel,cleared,delta_flip,absorption,cohort |
| 663 | 2026-06-23 00:45 | long | half | 0.600 | yes | stop_be | -0.41 | 0 | range | fuel,cleared,delta_flip,absorption,cohort |
| 666 | 2026-06-23 11:00 | long | full | 0.704 | no | cancelled:entry_expired_no_fvg | - | - | range | fuel,cleared,delta_flip,absorption,cohort |
| 667 | 2026-06-26 08:30 | short | half | 0.592 | no | cancelled:entry_expired_no_fvg | - | - | range | fuel,cleared,delta_flip,absorption,cohort |
| 668 | 2026-06-27 08:15 | short | full | 0.789 | yes | time_stop | 1.26 | 240 | range | fuel,cleared,delta_flip,absorption,cohort |
| 673 | 2026-07-04 00:30 | short | half | 0.553 | no | cancelled:entry_expired_no_fvg | - | - | range | fuel,cleared,delta_flip,absorption,cohort |
| 683 | 2026-07-17 14:00 | long | full | 0.734 | yes | stop_be | -1.95 | 0 | range | fuel,cleared,delta_flip,absorption,cohort |
| 686 | 2026-07-20 16:15 | short | full | 0.892 | no | cancelled:entry_expired_no_fvg | - | - | range | fuel,cleared,delta_flip,absorption,cohort |
| 687 | 2026-07-21 13:45 | short | full | 0.874 | yes | stop | -1.28 | 0 | range | fuel,cleared,delta_flip,absorption,cohort |

### 14.1 M1 ETH

| trade | signal ts | dir | tier | conviction | filled | exit_reason | R | hold min | day type | excluded reasons |
|---|---|---|---|---|---|---|---|---|---|---|
| 576 | 2026-03-11 08:00 | long | full | 1.000 | yes | time_stop | 0.61 | 240 | range | fuel,cleared,delta_flip,absorption,cohort |
| 581 | 2026-03-11 17:45 | short | full | 0.788 | no | cancelled:entry_expired_no_fvg | - | - | range | fuel,cleared,delta_flip,absorption,cohort |
| 582 | 2026-03-13 13:45 | short | full | 0.779 | yes | stop | -1.07 | 30 | range | fuel,cleared,delta_flip,absorption,cohort |
| 584 | 2026-03-14 07:00 | long | full | 0.798 | no | cancelled:entry_expired_no_fvg | - | - | range | fuel,cleared,delta_flip,absorption,cohort |
| 592 | 2026-03-24 08:15 | short | half | 0.613 | no | cancelled:entry_expired_no_fvg | - | - | range | fuel,cleared,delta_flip,absorption,cohort |
| 595 | 2026-03-28 08:15 | short | half | 0.560 | yes | time_stop | 0.90 | 240 | range | fuel,cleared,delta_flip,absorption,cohort |
| 597 | 2026-03-30 08:15 | short | full | 0.771 | yes | stop | -1.18 | 15 | range | fuel,cleared,delta_flip,absorption,cohort |
| 601 | 2026-03-31 10:15 | long | full | 0.825 | no | cancelled:entry_expired_no_fvg | - | - | range | fuel,cleared,delta_flip,absorption,cohort |
| 602 | 2026-03-31 14:45 | short | half | 0.624 | yes | stop_be | -0.29 | 0 | range | fuel,cleared,delta_flip,absorption,cohort |
| 611 | 2026-04-11 19:00 | short | half | 0.582 | yes | stop | -1.14 | 0 | range | fuel,cleared,delta_flip,absorption,cohort |
| 612 | 2026-04-13 09:00 | long | half | 0.616 | no | cancelled:entry_expired_no_fvg | - | - | range | fuel,cleared,delta_flip,absorption,cohort |
| 617 | 2026-04-21 07:00 | short | full | 0.767 | yes | stop_be | 0.16 | 10 | range | fuel,cleared,delta_flip,absorption,cohort |
| 619 | 2026-04-23 09:30 | long | full | 0.743 | yes | stop | -1.12 | 30 | range | fuel,cleared,delta_flip,absorption,cohort |
| 621 | 2026-04-28 13:30 | long | full | 0.822 | yes | stop_be | -0.00 | 0 | range | fuel,cleared,delta_flip,absorption,cohort |
| 625 | 2026-05-01 13:30 | short | half | 0.651 | yes | stop | -1.30 | 0 | range | fuel,cleared,delta_flip,absorption,cohort |
| 626 | 2026-05-03 12:30 | short | full | 0.741 | no | cancelled:entry_expired_no_fvg | - | - | range | fuel,cleared,delta_flip,absorption,cohort |
| 627 | 2026-05-06 17:00 | long | full | 0.720 | yes | thesis_failed | -0.50 | 25 | range | fuel,cleared,delta_flip,absorption,cohort |
| 633 | 2026-05-13 13:00 | long | half | 0.586 | yes | stop | -1.11 | 15 | range | fuel,cleared,delta_flip,absorption,cohort |
| 634 | 2026-05-14 00:45 | long | half | 0.645 | yes | stop_be | 0.01 | 15 | range | fuel,cleared,delta_flip,absorption,cohort |
| 636 | 2026-05-15 07:00 | long | full | 0.765 | yes | stop_be | 0.14 | 75 | range | fuel,cleared,delta_flip,absorption,cohort |
| 639 | 2026-05-20 05:30 | short | half | 0.566 | yes | stop | -1.35 | 0 | range | fuel,cleared,delta_flip,absorption,cohort |
| 642 | 2026-05-23 21:30 | short | half | 0.622 | no | cancelled:entry_expired | - | - | range | fuel,cleared,delta_flip,absorption,cohort |
| 644 | 2026-05-25 08:15 | short | full | 0.764 | yes | stop | -1.41 | 15 | range | fuel,cleared,delta_flip,absorption,cohort |
| 646 | 2026-05-25 15:45 | short | full | 0.780 | no | cancelled:entry_expired_no_fvg | - | - | range | fuel,cleared,delta_flip,absorption,cohort |
| 649 | 2026-05-27 14:00 | long | full | 0.917 | yes | stop_be | 0.48 | 165 | range | fuel,cleared,delta_flip,absorption,cohort |
| 653 | 2026-06-01 02:30 | long | half | 0.569 | no | cancelled:entry_expired_no_fvg | - | - | range | fuel,cleared,delta_flip,absorption,cohort |
| 656 | 2026-06-09 15:15 | long | full | 0.716 | yes | stop_be | 0.18 | 0 | range | fuel,cleared,delta_flip,absorption,cohort |
| 662 | 2026-06-22 14:30 | short | half | 0.654 | no | cancelled:entry_expired | - | - | range | fuel,cleared,delta_flip,absorption,cohort |
| 671 | 2026-07-01 14:45 | short | half | 0.580 | yes | stop_be | 0.23 | 0 | range | fuel,cleared,delta_flip,absorption,cohort |
| 674 | 2026-07-05 02:45 | long | half | 0.596 | yes | time_stop | 2.94 | 240 | range | fuel,cleared,delta_flip,absorption,cohort |
| 675 | 2026-07-06 09:00 | long | full | 0.726 | yes | stop | -1.30 | 15 | range | fuel,cleared,delta_flip,absorption,cohort |
| 676 | 2026-07-06 13:30 | long | half | 0.579 | yes | stop | -1.25 | 0 | range | fuel,cleared,delta_flip,absorption,cohort |
| 677 | 2026-07-06 16:45 | short | half | 0.645 | yes | dead_trade | -0.03 | 145 | range | fuel,cleared,delta_flip,absorption,cohort |
| 680 | 2026-07-13 13:00 | long | half | 0.631 | yes | stop_be | 0.03 | 10 | range | fuel,cleared,delta_flip,absorption,cohort |
| 682 | 2026-07-16 13:00 | long | full | 1.000 | no | cancelled:entry_expired_no_fvg | - | - | range | fuel,cleared,delta_flip,absorption,cohort |
| 684 | 2026-07-17 14:00 | long | full | 0.942 | no | cancelled:entry_expired_no_fvg | - | - | range | fuel,cleared,delta_flip,absorption,cohort |
| 688 | 2026-07-23 07:30 | long | full | 1.000 | yes | stop | -1.37 | 15 | range | fuel,cleared,delta_flip,absorption,cohort |
| 689 | 2026-07-23 08:15 | long | half | 0.619 | no | cancelled:entry_expired | - | - | range | fuel,cleared,delta_flip,absorption,cohort |
| 692 | 2026-08-02 19:00 | short | half | 0.551 | no | cancelled:entry_expired_no_fvg | - | - | range | fuel,cleared,delta_flip,absorption,cohort |
| 695 | 2026-08-07 12:30 | short | half | 0.550 | yes | stop | -1.23 | 0 | range | delta_flip,absorption,cohort |
| 696 | 2026-08-11 16:00 | long | half | 0.679 | no | cancelled:entry_expired_no_fvg | - | - | range | delta_flip,absorption,cohort |

### 14.2 M3 BTC

| trade | signal ts | dir | tier | conviction | filled | exit_reason | R | hold min | day type | excluded reasons |
|---|---|---|---|---|---|---|---|---|---|---|
| 578 | 2026-03-11 10:00 | long | half | 0.589 | no | cancelled:entry_expired | - | - | range | trap,cvd_divergence,cluster_fuel |
| 579 | 2026-03-11 12:45 | long | half | 0.621 | yes | time_stop | 5.52 | 360 | range | trap,cvd_divergence,cluster_fuel |
| 586 | 2026-03-20 13:00 | long | half | 0.584 | yes | stop | -1.35 | 30 | range | trap,cvd_divergence,cluster_fuel |
| 590 | 2026-03-23 15:15 | short | half | 0.588 | no | cancelled:entry_expired | - | - | range | trap,cvd_divergence,cluster_fuel |
| 604 | 2026-04-01 15:15 | short | half | 0.677 | yes | stop | -1.28 | 0 | range | trap,cvd_divergence,cluster_fuel |
| 605 | 2026-04-02 11:15 | long | half | 0.643 | yes | stop | -1.42 | 0 | range | trap,cvd_divergence,cluster_fuel |
| 632 | 2026-05-12 16:30 | long | half | 0.606 | yes | stop | -1.39 | 0 | range | trap,cvd_divergence,cluster_fuel |
| 655 | 2026-06-08 14:00 | short | half | 0.669 | yes | stop | -1.21 | 15 | range | trap,cvd_divergence,cluster_fuel |
| 678 | 2026-07-06 18:30 | short | full | 0.780 | no | cancelled:entry_expired | - | - | range | trap,cvd_divergence,cluster_fuel |

### 14.2 M3 ETH

| trade | signal ts | dir | tier | conviction | filled | exit_reason | R | hold min | day type | excluded reasons |
|---|---|---|---|---|---|---|---|---|---|---|
| 606 | 2026-04-06 15:00 | short | half | 0.590 | yes | stop | -1.24 | 0 | range | trap,cvd_divergence,cluster_fuel |
| 607 | 2026-04-06 16:15 | short | full | 0.756 | no | cancelled:entry_expired | - | - | range | trap,cvd_divergence,cluster_fuel |
| 628 | 2026-05-06 17:30 | long | half | 0.604 | yes | thesis_failed | -0.65 | 25 | range | trap,cvd_divergence,cluster_fuel |
| 637 | 2026-05-16 13:45 | long | half | 0.606 | no | cancelled:entry_expired | - | - | range | trap,cvd_divergence,cluster_fuel |
| 693 | 2026-08-05 15:45 | short | half | 0.556 | yes | stop | -1.33 | 15 | range | trap,cvd_divergence,cluster_fuel |

### 14.3 M5 BTC

| trade | signal ts | dir | tier | conviction | filled | exit_reason | R | hold min | day type | excluded reasons |
|---|---|---|---|---|---|---|---|---|---|---|
| 580 | 2026-03-11 13:30 | long | full | 0.911 | no | cancelled:entry_expired | - | - | range | fuel,cleared,delta_flip,cohort |
| 587 | 2026-03-21 07:45 | short | half | 0.625 | no | cancelled:entry_expired | - | - | no_trade | fuel,cleared,delta_flip,cohort |
| 614 | 2026-04-15 14:15 | long | full | 0.777 | yes | stop_be | 0.59 | 0 | range | fuel,cleared,delta_flip,cohort |
| 672 | 2026-07-03 09:00 | short | full | 0.776 | yes | stop | -1.18 | 75 | range | fuel,cleared,delta_flip,cohort |
| 694 | 2026-08-06 15:00 | long | half | 0.664 | no | cancelled:entry_expired | - | - | range | fuel,cleared,delta_flip,cohort |

### 14.3 M5 ETH

| trade | signal ts | dir | tier | conviction | filled | exit_reason | R | hold min | day type | excluded reasons |
|---|---|---|---|---|---|---|---|---|---|---|
| 577 | 2026-03-11 08:45 | long | full | 0.941 | yes | stop_be | 0.26 | 75 | range | fuel,cleared,delta_flip,cohort |
| 588 | 2026-03-21 07:45 | short | half | 0.666 | no | cancelled:entry_expired | - | - | no_trade | fuel,cleared,delta_flip,cohort |
| 645 | 2026-05-25 08:15 | short | full | 0.856 | yes | stop | -1.41 | 15 | range | fuel,cleared,delta_flip,cohort |
| 650 | 2026-05-27 14:00 | long | full | 0.794 | yes | time_stop | 1.23 | 160 | range | fuel,cleared,delta_flip,cohort |
| 691 | 2026-08-01 07:30 | short | half | 0.687 | no | cancelled:entry_expired | - | - | no_trade | fuel,cleared,delta_flip,cohort |
| 697 | 2026-08-19 08:00 | short | half | 0.556 | yes | stop | -1.30 | 0 | range | delta_flip,cohort |
| 698 | 2026-08-24 07:30 | short | half | 0.627 | no | cancelled:entry_expired | - | - | range | delta_flip,cohort |

### 14.4 M6 BTC

| trade | signal ts | dir | tier | conviction | filled | exit_reason | R | hold min | day type | excluded reasons |
|---|---|---|---|---|---|---|---|---|---|---|
| 609 | 2026-04-07 11:00 | short | full | 0.718 | yes | stop_be | -0.47 | 0 | range | oi_commitment,delta_reclaim,cohort |
| 640 | 2026-05-20 10:00 | long | half | 0.569 | yes | thesis_failed | -0.21 | 70 | range | oi_commitment,delta_reclaim,cohort |
| 647 | 2026-05-26 01:00 | short | half | 0.641 | yes | stop | -1.68 | 330 | range | oi_commitment,delta_reclaim,cohort |
| 659 | 2026-06-16 20:00 | short | half | 0.581 | yes | thesis_failed | -0.38 | 70 | range | oi_commitment,delta_reclaim,cohort |
| 664 | 2026-06-23 07:00 | short | half | 0.679 | no | cancelled:entry_expired | - | - | range | oi_commitment,delta_reclaim,cohort |
| 669 | 2026-06-30 05:00 | short | half | 0.626 | yes | thesis_failed | -0.09 | 40 | range | oi_commitment,delta_reclaim,cohort |
| 679 | 2026-07-07 03:00 | short | half | 0.565 | yes | thesis_failed | -0.69 | 550 | range | oi_commitment,delta_reclaim,cohort |
| 690 | 2026-07-23 19:00 | long | half | 0.661 | yes | target | 3.96 | 680 | range | oi_commitment,delta_reclaim,cohort |
| 700 | 2026-09-03 03:00 | long | full | 0.702 | yes | thesis_failed | 0.24 | 280 | range | delta_reclaim,cohort |

### 14.4 M6 ETH

| trade | signal ts | dir | tier | conviction | filled | exit_reason | R | hold min | day type | excluded reasons |
|---|---|---|---|---|---|---|---|---|---|---|
| 608 | 2026-04-07 02:00 | short | full | 0.730 | yes | thesis_failed | -0.08 | 160 | range | oi_commitment,delta_reclaim,cohort |
| 629 | 2026-05-07 03:00 | short | half | 0.574 | yes | thesis_failed | -0.31 | 130 | range | oi_commitment,delta_reclaim,cohort |
| 648 | 2026-05-26 01:00 | short | half | 0.623 | yes | thesis_failed | -0.11 | 310 | range | oi_commitment,delta_reclaim,cohort |
| 660 | 2026-06-18 16:00 | short | half | 0.666 | yes | time_stop | 0.11 | 1435 | range | oi_commitment,delta_reclaim,cohort |
| 665 | 2026-06-23 07:00 | short | half | 0.679 | no | cancelled:entry_expired | - | - | range | oi_commitment,delta_reclaim,cohort |
| 670 | 2026-06-30 13:00 | short | full | 0.855 | yes | thesis_failed | -0.01 | 190 | range | oi_commitment,delta_reclaim,cohort |
| 681 | 2026-07-14 13:00 | long | half | 0.556 | yes | stop_be | -0.62 | 0 | range | oi_commitment,delta_reclaim,cohort |
| 685 | 2026-07-20 16:00 | long | half | 0.560 | yes | target | 1.41 | 740 | range | oi_commitment,delta_reclaim,cohort |
| 699 | 2026-09-02 06:00 | long | half | 0.627 | yes | stop | -1.05 | 150 | range | delta_reclaim,cohort |
