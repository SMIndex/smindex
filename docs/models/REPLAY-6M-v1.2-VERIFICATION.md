# REPLAY-6M-v1.2 — verification pack

Produced 2026-09-07 (UTC) with no code change. Everything below is read straight out of the replay databases on the replay server; nothing was recomputed by hand. Files:

| File | What | sha256 |
|---|---|---|
| `docs/models/replay-v1.2-takes.csv` | one row per **fired model signal**, both fill models (400 rows = 200 adverse + 200 neutral) | `65ab20099920c227fb233a1b893d721039d1e165c9fa7357e0771f1082a37863` |
| `docs/models/tools/verify_pack_v12.py` | the read-only extraction script that wrote the CSV, the candle sample and the table counts (runs on the replay server as `/root/verify_pack_v12.py`, sha256 `7950b9a494f79ec45280922ec045e3c89ed602434628a435a27cadbe3a44fbba`) | — |
| `docs/models/tools/summary_from_csv.py`, `summary.sql` | rebuild the summary table from the CSV (§3) | — |

## 1. The CSV

Columns, in order: `model, coin, fill_model, signal_ts, level_type, level_price, direction, entry_px, entry_fill_ts, stop_px, t1_px, exit_ts, exit_px, exit_reason, r_multiple, conviction, size_tier, reasons_json`. All timestamps UTC `YYYY-MM-DD HH:MM:SS`.

Source of each column (`s` = `strat_signals`, `t` = `strat_trades` joined on `t.signal_id = s.id`, `L` = `t.lifecycle_json`):

| column | source |
|---|---|
| model, coin, signal_ts, level_type, level_price, conviction, size_tier, reasons_json | `s.model, s.asset, s.ts, s.level_type, s.level_price, s.conviction, s.size_tier, s.reasons_json` |
| direction | `t.direction` (falls back to `s.direction` when there is no trade row) |
| entry_px | `t.entry_px` (the resting limit price) |
| entry_fill_ts | `t.fill_ts` — empty when the entry never filled (`cancelled:*`) |
| stop_px | `L.initial_stop` — the stop the trade was sized on and that `r_multiple` divides by. `t.stop_px` itself is the *last* stop (moved to break-even / trailed), so it is not used |
| t1_px | `L.t1` (= `t.target_px`) |
| exit_ts, exit_reason | `t.exit_ts, t.exit_reason` |
| exit_px | `L.exit.px` — `strat_trades` has no exit-price column; the manager writes it into the lifecycle JSON |
| r_multiple | `t.r_multiple` — includes the 40 % partial at t1 where one happened (`L.partials`) |

### 1.1 Fired signals that have no trade row — 11 adverse, 12 neutral

`strat_signals.fired` sums to 200 per pass but there are 189 (adverse) / 188 (neutral) trade rows. The report's take counts (M1 139 / M3 14 / M5 18 / M6 18) are **trade rows**; the CSV carries the extra fired signals too, with `exit_reason = no_trade (...)` and empty trade columns, so nothing is hidden. Every one of the 23 is the runner's duplicate guard (`model_runner.py` `_open`: `SELECT id FROM strat_trades WHERE model=:m AND asset=:c AND mode='paper' AND exit_ts IS NULL` → "already has trade — TAKE not duplicated", logged at INFO which the `.out` files do not capture, so this was verified from the tables, not from the log):

| fill | signal | model | coin | signal_ts | trade open at that evaluation | how it was verified |
|---|---|---|---|---|---|---|
| adverse | 413281 | M1 | BTC | 2026-03-19 13:30:05 | #396 fill 2026-03-19 13:00:00 exit 2026-03-19 14:20:00 `stop_be` | trade row with `placed_ts` ≤ signal ts and `exit_ts` ≥ signal ts |
| adverse | 426937 | M1 | BTC | 2026-03-31 10:00:05 | #418 exit 2026-03-31 09:50:00 `stop` | previous same-model trade exited **inside the 15 min before the signal**; `run()` evaluates/opens BEFORE `_mgr.manage` writes that exit in the same boundary (D-82), so at evaluation time its `exit_ts` was still NULL |
| adverse | 475525 | M1 | BTC | 2026-05-12 14:15:05 | #467 fill 2026-05-12 14:00:00 exit 2026-05-12 14:20:00 `stop_be` | trade row with `placed_ts` ≤ signal ts and `exit_ts` ≥ signal ts |
| adverse | 523753 | M1 | BTC | 2026-06-23 11:00:05 | #510 fill 2026-06-23 10:45:00 exit 2026-06-23 12:35:00 `stop_be` | trade row with `placed_ts` ≤ signal ts and `exit_ts` ≥ signal ts |
| adverse | 551545 | M1 | BTC | 2026-07-17 14:00:05 | #540 fill 2026-07-17 13:45:00 exit 2026-07-17 14:10:00 `target` | trade row with `placed_ts` ≤ signal ts and `exit_ts` ≥ signal ts |
| adverse | 574537 | M1 | BTC | 2026-08-06 13:00:05 | #562 fill 2026-08-06 09:00:00 exit 2026-08-06 13:00:00 `time_stop` | trade row with `placed_ts` ≤ signal ts and `exit_ts` ≥ signal ts |
| adverse | 453811 | M1 | ETH | 2026-04-23 17:45:05 | #450 exit 2026-04-23 17:35:00 `stop` | previous same-model trade exited **inside the 15 min before the signal**; `run()` evaluates/opens BEFORE `_mgr.manage` writes that exit in the same boundary (D-82), so at evaluation time its `exit_ts` was still NULL |
| adverse | 462823 | M1 | ETH | 2026-05-01 13:30:05 | #458 fill 2026-05-01 13:15:00 exit 2026-05-01 13:35:00 `stop` | trade row with `placed_ts` ≤ signal ts and `exit_ts` ≥ signal ts |
| adverse | 546451 | M1 | ETH | 2026-07-13 03:45:05 | #532 exit 2026-07-13 03:40:00 `target` | previous same-model trade exited **inside the 15 min before the signal**; `run()` evaluates/opens BEFORE `_mgr.manage` writes that exit in the same boundary (D-82), so at evaluation time its `exit_ts` was still NULL |
| adverse | 558175 | M1 | ETH | 2026-07-23 08:00:05 | #546 exit 2026-07-23 07:50:00 `stop` | previous same-model trade exited **inside the 15 min before the signal**; `run()` evaluates/opens BEFORE `_mgr.manage` writes that exit in the same boundary (D-82), so at evaluation time its `exit_ts` was still NULL |
| adverse | 403911 | M3 | BTC | 2026-03-11 10:15:05 | #389 fill — exit 2026-03-11 10:45:05 `cancelled:entry_expired` | trade row with `placed_ts` ≤ signal ts and `exit_ts` ≥ signal ts |
| neutral | 413281 | M1 | BTC | 2026-03-19 13:30:05 | #396 fill 2026-03-19 13:00:00 exit 2026-03-19 14:20:00 `stop_be` | trade row with `placed_ts` ≤ signal ts and `exit_ts` ≥ signal ts |
| neutral | 426937 | M1 | BTC | 2026-03-31 10:00:05 | #418 exit 2026-03-31 09:50:00 `stop` | previous same-model trade exited **inside the 15 min before the signal**; `run()` evaluates/opens BEFORE `_mgr.manage` writes that exit in the same boundary (D-82), so at evaluation time its `exit_ts` was still NULL |
| neutral | 475525 | M1 | BTC | 2026-05-12 14:15:05 | #467 fill 2026-05-12 14:00:00 exit 2026-05-12 14:20:00 `stop_be` | trade row with `placed_ts` ≤ signal ts and `exit_ts` ≥ signal ts |
| neutral | 523753 | M1 | BTC | 2026-06-23 11:00:05 | #509 fill 2026-06-23 10:45:00 exit 2026-06-23 12:35:00 `stop_be` | trade row with `placed_ts` ≤ signal ts and `exit_ts` ≥ signal ts |
| neutral | 551545 | M1 | BTC | 2026-07-17 14:00:05 | #539 fill 2026-07-17 13:45:00 exit 2026-07-17 14:10:00 `target` | trade row with `placed_ts` ≤ signal ts and `exit_ts` ≥ signal ts |
| neutral | 574537 | M1 | BTC | 2026-08-06 13:00:05 | #561 fill 2026-08-06 09:00:00 exit 2026-08-06 13:00:00 `time_stop` | trade row with `placed_ts` ≤ signal ts and `exit_ts` ≥ signal ts |
| neutral | 453811 | M1 | ETH | 2026-04-23 17:45:05 | #450 exit 2026-04-23 17:35:00 `stop` | previous same-model trade exited **inside the 15 min before the signal**; `run()` evaluates/opens BEFORE `_mgr.manage` writes that exit in the same boundary (D-82), so at evaluation time its `exit_ts` was still NULL |
| neutral | 462823 | M1 | ETH | 2026-05-01 13:30:05 | #458 fill 2026-05-01 13:15:00 exit 2026-05-01 13:35:00 `stop` | trade row with `placed_ts` ≤ signal ts and `exit_ts` ≥ signal ts |
| neutral | 491671 | M1 | ETH | 2026-05-26 14:30:05 | #487 exit 2026-05-26 14:20:00 `stop` | previous same-model trade exited **inside the 15 min before the signal**; `run()` evaluates/opens BEFORE `_mgr.manage` writes that exit in the same boundary (D-82), so at evaluation time its `exit_ts` was still NULL |
| neutral | 546451 | M1 | ETH | 2026-07-13 03:45:05 | #531 exit 2026-07-13 03:40:00 `target` | previous same-model trade exited **inside the 15 min before the signal**; `run()` evaluates/opens BEFORE `_mgr.manage` writes that exit in the same boundary (D-82), so at evaluation time its `exit_ts` was still NULL |
| neutral | 558175 | M1 | ETH | 2026-07-23 08:00:05 | #545 exit 2026-07-23 07:50:00 `stop` | previous same-model trade exited **inside the 15 min before the signal**; `run()` evaluates/opens BEFORE `_mgr.manage` writes that exit in the same boundary (D-82), so at evaluation time its `exit_ts` was still NULL |
| neutral | 403911 | M3 | BTC | 2026-03-11 10:15:05 | #389 fill — exit 2026-03-11 10:45:05 `cancelled:entry_expired` | trade row with `placed_ts` ≤ signal ts and `exit_ts` ≥ signal ts |

The five 'previous trade exited minutes earlier' cases are the evaluate-before-manage ordering already recorded in D-82; the same order runs live. Nothing was changed.

## 2. Eight M1 takes, seed 42, with the 15-minute candles

Selection: adverse-pass M1 trade rows sorted by `strat_trades.id`, `random.Random(42).sample(rows, 8)`, printed in time order. Candles are `strat_replay_candles` rows with `source='binance'`, `tf='15m'` (Binance USDT-perp klines loaded by `v12_setup.py`); the table's `ts` is the kline **close** time (`…:14:59.999`), printed here truncated to the second. Twelve candles = 8 before the trigger candle, the trigger candle (tag `SIGNAL` — it closes 5 s before the signal ts), 3 after. Tags: `wick` = `L.setup.wick_ts` (the sweep candle), `reclaim` = `L.setup.reclaim_ts`, `FILL` / `EXIT` = the candle containing `fill_ts` / `exit_ts` (fills and exits are evaluated on the 5-minute sub-candles, so both can land in the same 15-minute candle).

### trade #397 — ETH long · signal 2026-03-19 13:15:05 (signal id 413275)

level `4h_fvg_bottom` @ 2117.62 · wick 2116.03 @ 2026-03-19 13:14:59 · reclaim candle 2026-03-19 13:14:59 · entry 2125.7 (filled 2026-03-19 13:15:00) · initial stop 2114.300938873422 · t1 2128.45 · exit 2114.3 @ 2026-03-19 13:20:00 `stop` · **R -1.112** · conviction 0.8044

| close ts (UTC) | open | high | low | close | |
|---|---|---|---|---|---|
| 2026-03-19 11:14:59 | 2171.73 | 2175.62 | 2166.19 | 2169.48 |  |
| 2026-03-19 11:29:59 | 2169.49 | 2174.82 | 2167.85 | 2173.76 |  |
| 2026-03-19 11:44:59 | 2173.77 | 2176.67 | 2164.55 | 2166.53 |  |
| 2026-03-19 11:59:59 | 2166.53 | 2169.14 | 2164.7 | 2166.0 |  |
| 2026-03-19 12:14:59 | 2165.99 | 2166.58 | 2154.82 | 2161.11 |  |
| 2026-03-19 12:29:59 | 2161.12 | 2161.7 | 2151.9 | 2154.69 |  |
| 2026-03-19 12:44:59 | 2154.69 | 2157.44 | 2152.0 | 2153.03 |  |
| 2026-03-19 12:59:59 | 2153.04 | 2153.31 | 2128.45 | 2131.34 |  |
| 2026-03-19 13:14:59 | 2131.35 | 2135.37 | 2116.03 | 2118.36 | SIGNAL wick reclaim |
| 2026-03-19 13:29:59 | 2118.36 | 2136.34 | 2106.9 | 2128.79 | FILL EXIT |
| 2026-03-19 13:44:59 | 2128.79 | 2141.67 | 2110.0 | 2136.93 |  |
| 2026-03-19 13:59:59 | 2136.92 | 2142.47 | 2126.88 | 2136.67 |  |

### trade #418 — BTC long · signal 2026-03-31 09:30:05 (signal id 426913)

level `equal_lows_1h` @ 66376.3 · wick 66321.0 @ 2026-03-31 09:29:59 · reclaim candle 2026-03-31 09:29:59 · entry 66503.6 (filled 2026-03-31 09:30:00) · initial stop 66283.23041671868 · t1 66764.4 · exit 66283.2 @ 2026-03-31 09:50:00 `stop` · **R -1.180** · conviction 0.8275

| close ts (UTC) | open | high | low | close | |
|---|---|---|---|---|---|
| 2026-03-31 07:29:59 | 67267.2 | 67296.4 | 67071.9 | 67260.9 |  |
| 2026-03-31 07:44:59 | 67260.8 | 67350.0 | 67179.7 | 67299.9 |  |
| 2026-03-31 07:59:59 | 67299.9 | 67410.0 | 67281.2 | 67343.8 |  |
| 2026-03-31 08:14:59 | 67343.9 | 67476.9 | 67208.4 | 67324.0 |  |
| 2026-03-31 08:29:59 | 67323.9 | 67420.0 | 67250.6 | 67280.4 |  |
| 2026-03-31 08:44:59 | 67280.3 | 67281.9 | 66777.0 | 66818.9 |  |
| 2026-03-31 08:59:59 | 66818.9 | 66911.9 | 66655.0 | 66745.1 |  |
| 2026-03-31 09:14:59 | 66745.1 | 66891.8 | 66550.0 | 66669.5 |  |
| 2026-03-31 09:29:59 | 66669.6 | 66686.3 | 66321.0 | 66403.9 | SIGNAL wick reclaim |
| 2026-03-31 09:44:59 | 66403.9 | 66499.7 | 66368.0 | 66477.4 | FILL |
| 2026-03-31 09:59:59 | 66477.4 | 66487.0 | 65938.0 | 66099.3 | EXIT |
| 2026-03-31 10:14:59 | 66099.3 | 66371.9 | 66067.0 | 66342.5 |  |

### trade #424 — BTC short · signal 2026-04-04 16:15:05 (signal id 431845)

level `pdh` @ 67350.0 · wick 67369.0 @ 2026-04-04 16:14:59 · reclaim candle 2026-04-04 16:14:59 · entry 67330.6 (filled 2026-04-04 16:20:00) · initial stop 67384.2615163007 · t1 67250.10772554897 · exit 67384.3 @ 2026-04-04 16:20:00 `stop` · **R -1.754** · conviction 0.6477

| close ts (UTC) | open | high | low | close | |
|---|---|---|---|---|---|
| 2026-04-04 14:14:59 | 67170.0 | 67193.3 | 67160.0 | 67164.9 |  |
| 2026-04-04 14:29:59 | 67165.0 | 67178.5 | 67160.0 | 67160.0 |  |
| 2026-04-04 14:44:59 | 67160.0 | 67160.1 | 67003.7 | 67068.7 |  |
| 2026-04-04 14:59:59 | 67068.6 | 67180.0 | 67068.6 | 67177.6 |  |
| 2026-04-04 15:14:59 | 67177.5 | 67554.5 | 67145.6 | 67351.2 |  |
| 2026-04-04 15:29:59 | 67351.3 | 67445.5 | 67263.6 | 67297.2 |  |
| 2026-04-04 15:44:59 | 67297.2 | 67323.2 | 67242.6 | 67302.5 |  |
| 2026-04-04 15:59:59 | 67302.5 | 67359.0 | 67267.5 | 67357.3 |  |
| 2026-04-04 16:14:59 | 67357.4 | 67369.0 | 67292.2 | 67310.9 | SIGNAL wick reclaim |
| 2026-04-04 16:29:59 | 67310.8 | 67459.1 | 67292.0 | 67410.7 | FILL EXIT |
| 2026-04-04 16:44:59 | 67410.6 | 67487.2 | 67350.0 | 67487.1 |  |
| 2026-04-04 16:59:59 | 67487.2 | 67500.0 | 67319.3 | 67335.2 |  |

### trade #431 — ETH long · signal 2026-04-07 11:00:05 (signal id 435055)

level `pdl` @ 2086.29 · wick 2085.1 @ 2026-04-07 10:59:59 · reclaim candle 2026-04-07 10:59:59 · entry 2092.0 (filled 2026-04-07 11:00:00) · initial stop 2083.7331168176775 · t1 2104.4128247734843 · exit 2083.73 @ 2026-04-07 11:05:00 `stop` · **R -1.151** · conviction 0.6463

| close ts (UTC) | open | high | low | close | |
|---|---|---|---|---|---|
| 2026-04-07 08:59:59 | 2114.16 | 2116.43 | 2111.9 | 2113.99 |  |
| 2026-04-07 09:14:59 | 2113.99 | 2132.96 | 2113.32 | 2125.65 |  |
| 2026-04-07 09:29:59 | 2125.65 | 2127.68 | 2122.0 | 2124.57 |  |
| 2026-04-07 09:44:59 | 2124.58 | 2130.84 | 2123.82 | 2127.44 |  |
| 2026-04-07 09:59:59 | 2127.44 | 2128.31 | 2125.25 | 2127.26 |  |
| 2026-04-07 10:14:59 | 2127.26 | 2130.78 | 2114.57 | 2116.27 |  |
| 2026-04-07 10:29:59 | 2116.27 | 2123.26 | 2103.3 | 2105.64 |  |
| 2026-04-07 10:44:59 | 2105.64 | 2107.69 | 2095.22 | 2098.0 |  |
| 2026-04-07 10:59:59 | 2098.0 | 2098.91 | 2085.1 | 2088.29 | SIGNAL wick reclaim |
| 2026-04-07 11:14:59 | 2088.29 | 2091.21 | 2080.57 | 2087.84 | FILL EXIT |
| 2026-04-07 11:29:59 | 2087.83 | 2093.23 | 2085.01 | 2090.91 |  |
| 2026-04-07 11:44:59 | 2090.92 | 2091.55 | 2085.79 | 2088.99 |  |

### trade #438 — BTC short · signal 2026-04-14 07:15:05 (signal id 442933)

level `pdh` @ 74870.0 · wick 74900.0 @ 2026-04-14 07:14:59 · reclaim candle 2026-04-14 07:14:59 · entry 74647.8 (filled 2026-04-14 07:15:00) · initial stop 74928.90575776882 · t1 74583.1 · exit 74647.8 @ 2026-04-14 07:35:00 `stop_be` · **R -0.035** · conviction 0.6436

| close ts (UTC) | open | high | low | close | |
|---|---|---|---|---|---|
| 2026-04-14 05:14:59 | 74355.5 | 74356.1 | 74260.2 | 74305.8 |  |
| 2026-04-14 05:29:59 | 74305.7 | 74401.8 | 74275.3 | 74309.0 |  |
| 2026-04-14 05:44:59 | 74308.9 | 74330.0 | 74243.7 | 74267.0 |  |
| 2026-04-14 05:59:59 | 74267.1 | 74267.1 | 74112.2 | 74184.5 |  |
| 2026-04-14 06:14:59 | 74184.5 | 74320.0 | 74184.5 | 74299.0 |  |
| 2026-04-14 06:29:59 | 74299.0 | 74420.0 | 74274.9 | 74349.0 |  |
| 2026-04-14 06:44:59 | 74348.9 | 74419.0 | 74321.8 | 74349.1 |  |
| 2026-04-14 06:59:59 | 74349.1 | 74519.9 | 74335.4 | 74485.0 |  |
| 2026-04-14 07:14:59 | 74485.1 | 74900.0 | 74395.6 | 74739.2 | SIGNAL wick reclaim |
| 2026-04-14 07:29:59 | 74739.2 | 74773.7 | 74568.5 | 74636.8 | FILL |
| 2026-04-14 07:44:59 | 74636.8 | 74648.4 | 74459.6 | 74463.9 | EXIT |
| 2026-04-14 07:59:59 | 74463.9 | 74523.9 | 74430.2 | 74513.4 |  |

### trade #466 — BTC long · signal 2026-05-12 11:15:05 (signal id 475381)

level `equal_lows_1h` @ 80527.75 · wick 80484.0 @ 2026-05-12 11:14:59 · reclaim candle 2026-05-12 11:14:59 · entry 80573.0 (filled 2026-05-12 11:15:00) · initial stop 80461.83222899832 · t1 80649.86 · exit 80461.8 @ 2026-05-12 11:20:00 `stop` · **R -1.435** · conviction 0.5842

| close ts (UTC) | open | high | low | close | |
|---|---|---|---|---|---|
| 2026-05-12 09:14:59 | 80797.5 | 80853.0 | 80710.8 | 80731.4 |  |
| 2026-05-12 09:29:59 | 80731.3 | 80764.4 | 80670.0 | 80698.9 |  |
| 2026-05-12 09:44:59 | 80698.9 | 80925.0 | 80698.9 | 80884.7 |  |
| 2026-05-12 09:59:59 | 80884.4 | 80915.9 | 80765.9 | 80799.1 |  |
| 2026-05-12 10:14:59 | 80799.1 | 80806.5 | 80733.0 | 80760.1 |  |
| 2026-05-12 10:29:59 | 80760.1 | 80792.4 | 80630.0 | 80685.1 |  |
| 2026-05-12 10:44:59 | 80685.1 | 80708.8 | 80578.2 | 80647.3 |  |
| 2026-05-12 10:59:59 | 80647.3 | 80668.2 | 80560.0 | 80634.0 |  |
| 2026-05-12 11:14:59 | 80634.0 | 80662.0 | 80484.0 | 80542.3 | SIGNAL wick reclaim |
| 2026-05-12 11:29:59 | 80542.3 | 80619.4 | 80444.0 | 80601.6 | FILL EXIT |
| 2026-05-12 11:44:59 | 80601.6 | 80700.9 | 80601.5 | 80697.1 |  |
| 2026-05-12 11:59:59 | 80697.0 | 80754.6 | 80670.0 | 80742.7 |  |

### trade #472 — BTC long · signal 2026-05-16 07:15:05 (signal id 479797)

level `pwl` @ 78128.3 · wick 78063.3 @ 2026-05-16 07:14:59 · reclaim candle 2026-05-16 07:14:59 · entry 78336.8 (filled 2026-05-16 07:15:00) · initial stop 78040.28428453923 · t1 78393.9 · exit 78040.3 @ 2026-05-16 07:20:00 `stop` · **R -1.158** · conviction 1.0565

| close ts (UTC) | open | high | low | close | |
|---|---|---|---|---|---|
| 2026-05-16 05:14:59 | 78999.9 | 79001.0 | 78889.7 | 78999.9 |  |
| 2026-05-16 05:29:59 | 79000.0 | 79000.0 | 78978.8 | 78985.0 |  |
| 2026-05-16 05:44:59 | 78985.1 | 79000.0 | 78900.0 | 78959.4 |  |
| 2026-05-16 05:59:59 | 78959.4 | 78990.0 | 78923.0 | 78989.9 |  |
| 2026-05-16 06:14:59 | 78989.9 | 79012.9 | 78875.6 | 78892.6 |  |
| 2026-05-16 06:29:59 | 78892.5 | 78892.6 | 78751.0 | 78788.0 |  |
| 2026-05-16 06:44:59 | 78787.9 | 78865.7 | 78696.1 | 78705.5 |  |
| 2026-05-16 06:59:59 | 78705.6 | 78746.0 | 78393.9 | 78610.2 |  |
| 2026-05-16 07:14:59 | 78610.2 | 78610.3 | 78063.3 | 78219.8 | SIGNAL wick reclaim |
| 2026-05-16 07:29:59 | 78219.8 | 78482.2 | 78000.4 | 78430.1 | FILL EXIT |
| 2026-05-16 07:44:59 | 78430.0 | 78528.5 | 78315.1 | 78460.1 |  |
| 2026-05-16 07:59:59 | 78460.0 | 78481.6 | 78300.0 | 78334.3 |  |

### trade #483 — BTC short · signal 2026-05-25 15:00:05 (signal id 490537)

level `equal_highs_1h` @ 77688.3 · wick 77743.4 @ 2026-05-25 14:59:59 · reclaim candle 2026-05-25 14:59:59 · entry 77639.1 (filled 2026-05-25 15:00:00) · initial stop 77762.80100268697 · t1 77600.0 · exit 77762.8 @ 2026-05-25 15:05:00 `stop` · **R -1.378** · conviction 0.7113

| close ts (UTC) | open | high | low | close | |
|---|---|---|---|---|---|
| 2026-05-25 12:59:59 | 77322.8 | 77405.8 | 77259.2 | 77325.7 |  |
| 2026-05-25 13:14:59 | 77325.7 | 77419.2 | 77280.0 | 77291.9 |  |
| 2026-05-25 13:29:59 | 77291.9 | 77335.0 | 77244.8 | 77299.2 |  |
| 2026-05-25 13:44:59 | 77299.3 | 77330.0 | 77254.8 | 77319.4 |  |
| 2026-05-25 13:59:59 | 77319.4 | 77327.8 | 77267.8 | 77302.1 |  |
| 2026-05-25 14:14:59 | 77302.0 | 77496.1 | 77273.2 | 77435.5 |  |
| 2026-05-25 14:29:59 | 77435.6 | 77588.0 | 77435.0 | 77580.5 |  |
| 2026-05-25 14:44:59 | 77580.4 | 77580.4 | 77502.5 | 77535.0 |  |
| 2026-05-25 14:59:59 | 77535.0 | 77743.4 | 77534.9 | 77676.4 | SIGNAL wick reclaim |
| 2026-05-25 15:14:59 | 77676.4 | 77887.9 | 77640.9 | 77668.4 | FILL EXIT |
| 2026-05-25 15:29:59 | 77668.5 | 77720.4 | 77556.0 | 77562.7 |  |
| 2026-05-25 15:44:59 | 77562.6 | 77648.6 | 77531.1 | 77624.2 |  |

## 3. Recomputing the summary table from the CSV

`docs/models/tools/summary_from_csv.py` (stdlib only):

```python
"""Recompute the REPLAY-6M-v1.2 summary table from docs/models/replay-v1.2-takes.csv (stdlib only).
takes    = rows with a trade (exit_reason not starting with 'no_trade')
filled   = takes with entry_fill_ts set (cancelled:* rows have none)
win rate = filled rows with r_multiple > 0 / filled
exp R    = mean r_multiple over filled rows;  sum R = sum;  PF = sum(R>0) / -sum(R<0)
usage: python summary_from_csv.py docs/models/replay-v1.2-takes.csv"""
import csv, sys
from collections import defaultdict
g = defaultdict(list)
for r in csv.DictReader(open(sys.argv[1], encoding="utf-8")):
    g[(r["fill_model"], r["model"])].append(r)
print(f"{'fill':8}{'model':6}{'fired':>6}{'takes':>6}{'filled':>7}{'wins':>5}{'win%':>7}{'expR':>8}{'sumR':>8}{'PF':>6}  exit reasons")
for (fill, model), rows in sorted(g.items()):
    takes = [r for r in rows if not r["exit_reason"].startswith("no_trade")]
    filled = [r for r in takes if r["entry_fill_ts"]]
    R = [float(r["r_multiple"]) for r in filled if r["r_multiple"] != ""]
    wins = [x for x in R if x > 0]; losses = [x for x in R if x < 0]
    pf = (sum(wins) / -sum(losses)) if losses else float("inf")
    reasons = defaultdict(int)
    for r in takes: reasons[r["exit_reason"]] += 1
    print(f"{fill:8}{model:6}{len(rows):6}{len(takes):6}{len(filled):7}{len(wins):5}"
          f"{(100*len(wins)/len(R) if R else 0):7.1f}{(sum(R)/len(R) if R else 0):8.3f}{sum(R):8.2f}{pf:6.2f}  {dict(reasons)}")
```

Output on the committed CSV — matches REPLAY-6M-v1.2.md §3 (adverse M1 139 takes / 129 filled / −0.351 R; M3 14 / −0.220; M5 18 / −0.437; M6 18 / −0.030; neutral M1 138 / −0.376):

```
fill    model  fired takes filled wins   win%    expR    sumR    PF  exit reasons
adverse M1       149   139    129   44   34.1  -0.351  -45.25  0.47  {'stop_be': 35, 'stop': 68, 'thesis_failed': 3, 'cancelled:entry_expired_no_fvg': 8, 'time_stop': 7, 'dead_trade': 8, 'target': 8, 'cancelled:entry_expired': 2}
adverse M3        15    14      9    1   11.1  -0.220   -1.98  0.85  {'cancelled:entry_expired': 5, 'time_stop': 1, 'stop': 8}
adverse M5        18    18     12    4   33.3  -0.437   -5.24  0.40  {'cancelled:entry_expired': 6, 'stop': 7, 'stop_be': 3, 'time_stop': 2}
adverse M6        18    18     15    3   20.0  -0.030   -0.46  0.93  {'stop_be': 2, 'thesis_failed': 8, 'stop': 2, 'cancelled:entry_expired': 3, 'target': 2, 'time_stop': 1}
neutral M1       149   138    129   44   34.1  -0.376  -48.50  0.43  {'stop_be': 37, 'stop': 67, 'thesis_failed': 3, 'cancelled:entry_expired_no_fvg': 7, 'time_stop': 7, 'dead_trade': 8, 'target': 7, 'cancelled:entry_expired': 2}
neutral M3        15    14      9    1   11.1  -0.220   -1.98  0.85  {'cancelled:entry_expired': 5, 'time_stop': 1, 'stop': 8}
neutral M5        18    18     12    4   33.3  -0.437   -5.24  0.40  {'cancelled:entry_expired': 6, 'stop': 7, 'stop_be': 3, 'time_stop': 2}
neutral M6        18    18     15    3   20.0  -0.030   -0.46  0.93  {'stop_be': 2, 'thesis_failed': 8, 'stop': 2, 'cancelled:entry_expired': 3, 'target': 2, 'time_stop': 1}
```

Same table in SQL (`docs/models/tools/summary.sql`):

```sql
-- Same table in MySQL: LOAD the CSV into a scratch table first, e.g.
-- CREATE TABLE takes (model VARCHAR(4), coin VARCHAR(8), fill_model VARCHAR(8), signal_ts DATETIME, level_type VARCHAR(32),
--   level_price DOUBLE, direction VARCHAR(5), entry_px DOUBLE, entry_fill_ts VARCHAR(19), stop_px DOUBLE, t1_px DOUBLE,
--   exit_ts VARCHAR(19), exit_px DOUBLE, exit_reason VARCHAR(255), r_multiple VARCHAR(32), conviction DOUBLE, size_tier VARCHAR(8), reasons_json TEXT);
-- LOAD DATA LOCAL INFILE 'docs/models/replay-v1.2-takes.csv' INTO TABLE takes FIELDS TERMINATED BY ',' OPTIONALLY ENCLOSED BY '"' LINES TERMINATED BY '\n' IGNORE 1 LINES;
SELECT fill_model, model,
       COUNT(*)                                                     AS fired,
       SUM(exit_reason NOT LIKE 'no_trade%')                        AS takes,
       SUM(entry_fill_ts <> '')                                     AS filled,
       SUM(entry_fill_ts <> '' AND r_multiple + 0 > 0)              AS wins,
       ROUND(100 * SUM(entry_fill_ts <> '' AND r_multiple + 0 > 0) / NULLIF(SUM(entry_fill_ts <> ''), 0), 1) AS win_pct,
       ROUND(AVG(CASE WHEN entry_fill_ts <> '' THEN r_multiple + 0 END), 3)  AS exp_r,
       ROUND(SUM(CASE WHEN entry_fill_ts <> '' THEN r_multiple + 0 END), 2)  AS sum_r,
       ROUND(SUM(CASE WHEN entry_fill_ts <> '' AND r_multiple + 0 > 0 THEN r_multiple + 0 END)
           / -NULLIF(SUM(CASE WHEN entry_fill_ts <> '' AND r_multiple + 0 < 0 THEN r_multiple + 0 END), 0), 2) AS pf
FROM takes GROUP BY fill_model, model ORDER BY fill_model, model;
```

Definitions: **takes** = rows with a trade (exit_reason not `no_trade…`); **filled** = takes with `entry_fill_ts`; win rate = filled rows with R > 0 ÷ filled; expectancy = mean R over filled rows; PF = ΣR(wins) ÷ −ΣR(losses). `cancelled:*` rows have no R and are excluded from every ratio.

## 4. Underlying tables in `perpl_replay` over the replay window

Window = 2026-03-10 19:45:00 → 2026-09-06 19:45:00 UTC (17,281 boundaries × BTC, ETH). `perpl_replay_n` is a `mysqldump` clone of the same feed tables (`v12_setup.py`); only `strat_signals` / `strat_trades` differ between the two.

| table | rows | min ts (UTC) | max ts (UTC) |
|---|---|---|---|
| strat_replay_candles binance 15m BTC (window) | 17,280 | 2026-03-10 19:59:59 | 2026-09-06 19:44:59 |
| strat_replay_candles binance 15m ETH (window) | 17,280 | 2026-03-10 19:59:59 | 2026-09-06 19:44:59 |
| strat_replay_candles binance 1h BTC (window) | 4,320 | 2026-03-10 19:59:59 | 2026-09-06 18:59:59 |
| strat_replay_candles binance 1h ETH (window) | 4,320 | 2026-03-10 19:59:59 | 2026-09-06 18:59:59 |
| strat_replay_candles binance 4h BTC (window) | 1,080 | 2026-03-10 19:59:59 | 2026-09-06 15:59:59 |
| strat_replay_candles binance 4h ETH (window) | 1,080 | 2026-03-10 19:59:59 | 2026-09-06 15:59:59 |
| strat_replay_candles binance 1d BTC (window) | 180 | 2026-03-10 23:59:59 | 2026-09-05 23:59:59 |
| strat_replay_candles binance 1d ETH (window) | 180 | 2026-03-10 23:59:59 | 2026-09-05 23:59:59 |
| strat_replay_candles binance 15m BTC (whole table incl. warm-up) | 24,041 | 2025-12-30 09:44:59 | 2026-09-06 19:44:59 |
| strat_replay_candles binance 1d BTC (whole table incl. warm-up) | 249 | 2025-12-31 23:59:59 | 2026-09-05 23:59:59 |
| strat_replay_candles binance 1h BTC (whole table incl. warm-up) | 6,009 | 2025-12-30 10:59:59 | 2026-09-06 18:59:59 |
| strat_replay_candles binance 4h BTC (whole table incl. warm-up) | 1,501 | 2025-12-30 15:59:59 | 2026-09-06 15:59:59 |
| strat_replay_candles binance 15m ETH (whole table incl. warm-up) | 24,041 | 2025-12-30 09:44:59 | 2026-09-06 19:44:59 |
| strat_replay_candles binance 1d ETH (whole table incl. warm-up) | 249 | 2025-12-31 23:59:59 | 2026-09-05 23:59:59 |
| strat_replay_candles binance 1h ETH (whole table incl. warm-up) | 6,009 | 2025-12-30 10:59:59 | 2026-09-06 18:59:59 |
| strat_replay_candles binance 4h ETH (whole table incl. warm-up) | 1,501 | 2025-12-30 15:59:59 | 2026-09-06 15:59:59 |
| strat_replay_oi binance BTC (window) | 8,763 | 2026-08-07 09:35:00 | 2026-09-06 19:45:00 |
| strat_replay_oi binance ETH (window) | 8,763 | 2026-08-07 09:35:00 | 2026-09-06 19:45:00 |
| strat_liquidations live BTC (window) | 5,202 | 2026-08-11 14:52:07 | 2026-09-06 05:34:06 |
| strat_liquidations live ETH (window) | 2,171 | 2026-08-11 15:37:09 | 2026-09-06 17:37:13 |
| strat_liquidations oxarchive BTC (window) | 110,758 | 2026-08-07 12:08:13 | 2026-09-06 18:43:47 |
| strat_liquidations oxarchive ETH (window) | 29,056 | 2026-08-07 12:08:13 | 2026-09-06 17:37:13 |
| strat_liq_map_hist fills BTC (window) | 43,629 | 2026-08-07 11:45:00 | 2026-09-06 18:30:00 |
| strat_liq_map_hist levels BTC (window) | 146,141 | 2026-08-07 12:00:00 | 2026-09-06 19:30:00 |
| strat_liq_map_hist live BTC (window) | 2,635 | 2026-09-06 09:45:00 | 2026-09-06 19:45:00 |
| strat_liq_map_hist fills ETH (window) | 44,861 | 2026-08-07 11:45:00 | 2026-09-06 17:30:00 |
| strat_liq_map_hist levels ETH (window) | 143,214 | 2026-08-07 12:00:00 | 2026-09-06 19:30:00 |
| strat_liq_map_hist live ETH (window) | 2,392 | 2026-09-06 09:45:00 | 2026-09-06 19:45:00 |
| strat_calibration_hist BTC band_p80 | 182 (NULL: 182) | 2026-03-09 00:00:00 | 2026-09-06 00:00:00 |
| strat_calibration_hist BTC liq_5m_p90_long | 182 (NULL: 182) | 2026-03-09 00:00:00 | 2026-09-06 00:00:00 |
| strat_calibration_hist BTC liq_5m_p90_short | 182 (NULL: 182) | 2026-03-09 00:00:00 | 2026-09-06 00:00:00 |
| strat_calibration_hist ETH band_p80 | 182 (NULL: 182) | 2026-03-09 00:00:00 | 2026-09-06 00:00:00 |
| strat_calibration_hist ETH liq_5m_p90_long | 182 (NULL: 182) | 2026-03-09 00:00:00 | 2026-09-06 00:00:00 |
| strat_calibration_hist ETH liq_5m_p90_short | 182 (NULL: 182) | 2026-03-09 00:00:00 | 2026-09-06 00:00:00 |
| strat_signals (model rows) | 207,372 (fired 200) | 2026-03-10 19:45:05 | 2026-09-06 19:45:05 |
| strat_trades (model rows) | 189 |  |  |
| perpl_replay_n strat_signals (model rows) | 207,372 (fired 200) | | |
| perpl_replay_n strat_trades (model rows) | 188 | | |

Reading it: candles cover the whole window at every timeframe (15m 17,280 = 180 d × 96 per coin, plus 70 days of warm-up before the window). OI, liquidations and the liquidation map exist only from **2026-08-07** (free-tier 0xArchive key, D-81) — 30.5 of the 180 days; `strat_calibration_hist` is 182/182 NULL per key per coin, which is why every model ran with the fallback normaliser and no calibrated band/liquidation numbers (REPLAY-6M-v1.2.md §0). The `live` liquidation rows are the copy-tracker userFills feed; `strat_liq_map_hist live` only starts 2026-09-06 09:45.

## 5. Where it ran and how to run it again

| | |
|---|---|
| Server | `<server>` (hostname `<server>`), the production host; MySQL on the same box |
| Engine tree | `/root/audit_tree/backend` — an overlay copy of the working tree. Verified identical to commit **`f1dcb75`** (`Spec v1.2, 180-day liquidation history, point-in-time calibration, dual fill model`, origin/master): all 74 `.py/.yaml` files under `backend/app/strategy_engine` match the HEAD blobs md5-for-md5 after CRLF→LF normalisation (0 differences, 0 extra/missing files) |
| Driver | `/root/replay_v12.py` sha256 `b28d6b8b9818e21e8b055e1fe50fee194fe8db43187e9db3cab53cfeb47ae54e` (not in the repo; copy kept in the session scratchpad) |
| Stats | `/root/replay_stats_v12.py` sha256 `841b57a00cbf17c9676477c609b896dd69b9bf0a898e52894f711c1588a273cc` → `/root/replay_stats_{adverse,neutral}.md/.json` (REPLAY-6M-v1.2.md §12 is the adverse `.md` verbatim) |
| Data setup | `/root/v12_setup.py` sha256 `cebc480e4a32eed6055edf679259d5b0a187683fa17134cfaa0a30420381a4fb` (refreshes `perpl_replay` from `perpl_terminal`, clones to `perpl_replay_n`); `/root/oxarchive_load_v12.py` sha256 `b971016176d2a6c3f49dca1d52b75b5a6326fba0be870ff9b75ef2dfd12addc3` (0xArchive liquidation loader) |
| Databases | `perpl_replay` = adverse pass, `perpl_replay_n` = neutral pass |
| Logs | `/root/replay_v12_adverse.out` (started 2026-09-06 22:03 UTC, `done: 17281 boundaries in 17666s`), `/root/replay_v12_neutral.out` (started 22:08 UTC, `done … 17372s`); WARNING level and above only |
| Runtime | ~4.9 h per pass; the two passes ran concurrently |

Exact re-run (as root on the server; `DATABASE_URL` and `OXARCHIVE_API_KEY` come from `/var/www/terminal/backend/.env` and are never printed). Step 1 only if the feed tables should be refreshed from prod first — it **drops and rebuilds** `perpl_replay` / `perpl_replay_n`, so the current results are gone once it runs; steps 2–3 alone re-run on the data exactly as it is now:

```bash
cd /root && set -a && . /var/www/terminal/backend/.env && set +a
PY=/var/www/terminal/backend/venv/bin/python

# 1 (optional) refresh replay data from prod and clone: perpl_terminal -> perpl_replay -> perpl_replay_n
$PY /root/v12_setup.py

# 2 the two passes (each ~4.9 h; run both at once as they were)
FILL_MODEL=adverse REPLAY_DB=perpl_replay   REPLAY_DAYS=180 AUDIT_TREE=/root/audit_tree/backend nohup $PY /root/replay_v12.py > /root/replay_v12_adverse.out 2>&1 &
FILL_MODEL=neutral REPLAY_DB=perpl_replay_n REPLAY_DAYS=180 AUDIT_TREE=/root/audit_tree/backend nohup $PY /root/replay_v12.py > /root/replay_v12_neutral.out 2>&1 &
wait

# 3 stats (writes /root/replay_stats_<fill>.md and .json)
$PY /root/replay_stats_v12.py 180 perpl_replay   adverse
$PY /root/replay_stats_v12.py 180 perpl_replay_n neutral

# 4 this verification pack (CSV + candle sample + table counts)
$PY /root/verify_pack_v12.py      # -> /root/replay-v1.2-takes.csv, /root/replay-v1.2-pack.json
```

`replay_v12.py` starts by emptying `strat_trades`, `strat_signals`, `mind_model_state`, `strat_telegram_outbox`, `strat_changelog` and `strat_calibration` in `REPLAY_DB` (then re-seeds `strat_calibration` with prod's `live_coverage` row) and never writes to `perpl_terminal` (it reads the cohort and that seed from it). The window is derived from the data: `end = last 15m candle + 1`, `start = end − 180 × 96 boundaries`. The two passes are deterministic functions of the replay tables and the tree, so a re-run on unchanged data reproduces the 200 fired / 189 + 188 trade rows; the neutral pass's `fill ordering: {'both_inside': 4, 'adverse_first_or_tie': 2, 'favourable_first': 2}` line in its `.out` is the check that the D-78 rule engaged.
