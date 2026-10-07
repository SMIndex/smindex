# REPLAY-6M-v1.3 — verification pack

Produced 2026-09-07 14:00 (UTC) with no code change after the v1.3 replay finished. Everything below is read straight out of the two replay databases on the replay server by `docs/models/tools/verify_pack_v13.py`; nothing was recomputed by hand. Same layout as REPLAY-6M-v1.2-VERIFICATION.md, same seed (42), plus §2.1 (the eight v1.2 sampled trades located under the v1.3 rules) and six new CSV columns. Files:

| File | What | sha256 |
|---|---|---|
| `docs/models/replay-v1.3-takes.csv` | one row per **fired model signal**, both fill models (264 rows = 127 adverse + 137 neutral) | `e24a587bd418fe591cb58e189bd4e86beb5b53a6df67e7ec24d4b3ed7dbd67ca` |
| `docs/models/tools/verify_pack_v13.py` | the read-only extraction script that wrote the CSV, the candle samples and the table counts (runs on the replay server as `/root/verify_pack_v13.py`, sha256 `b702f0caaf301b70449a8a56cfe832ae25037ead68b873a7f7afdfd9154a25da`) | — |
| `docs/models/tools/summary_from_csv.py`, `summary.sql` | rebuild the summary table from the CSV (§3) — unchanged from v1.2, the new columns are ignored by both | — |

## 1. The CSV

Columns, in order: `model, coin, fill_model, signal_ts, level_type, level_price, direction, entry_px, entry_fill_ts, stop_px, t1_px, exit_ts, exit_px, exit_reason, r_multiple, conviction, size_tier, stop_floor_applied, stop_structural, entry_requoted, entry_px_orig, reclaim_candles, confirmation_used, reasons_json`. All timestamps UTC `YYYY-MM-DD HH:MM:SS`.

Source of each column (`s` = `strat_signals`, `t` = `strat_trades` joined on `t.signal_id = s.id`, `L` = `t.lifecycle_json`):

| column | source |
|---|---|
| model, coin, signal_ts, level_type, level_price, conviction, size_tier, reasons_json | `s.model, s.asset, s.ts, s.level_type, s.level_price, s.conviction, s.size_tier, s.reasons_json` |
| direction | `t.direction` (falls back to `s.direction` when there is no trade row) |
| entry_px | `t.entry_px` — the price the order finally RESTED at: the original limit when it did not cross, the one-tick-inside re-quote when it did (D-87) |
| entry_fill_ts | `t.fill_ts` — empty when the entry never filled (`cancelled:*`) |
| stop_px | `L.initial_stop` — the FINAL initial stop after the floor (D-88); the stop the trade was sized on and that `r_multiple` divides by. `t.stop_px` is the *last* stop (moved to break-even / trailed), so it is not used |
| t1_px | `L.t1` (= `t.target_px`) |
| exit_ts, exit_reason | `t.exit_ts, t.exit_reason` |
| exit_px | `L.exit.px` — `strat_trades` has no exit-price column; the manager writes it into the lifecycle JSON |
| r_multiple | `t.r_multiple` — includes the 40 % partial at t1 where one happened (`L.partials`) |
| stop_floor_applied (new) | `t.stop_floor_applied` (migration v17) — 1 when the structural stop was nearer than 0.5 ATR and was moved out (D-88), 0 when the structural stop was kept |
| stop_structural (new) | `L.stop_structural` — the stop before the floor; equals `stop_px` when the floor did not apply |
| entry_requoted (new) | `t.entry_requoted` — 1 when the post-only order would have crossed the touch and was re-quoted one tick inside (D-87) |
| entry_px_orig (new) | `t.entry_px_orig` — the original limit when re-quoted, empty otherwise; `entry_px − entry_px_orig` is the price cost of the re-quote |
| reclaim_candles (new) | `s.reclaim_candles` (M1/M5 only) = `Sweep.candles_to_reclaim` from `structure/sweeps.py`: 15-minute candles from the first candle beyond the level to the reclaim close, inclusive. It is NOT the reclaim type — the wick may deepen on a later candle, so rc = 2 occurs for both types; `confirmation_used` alone gives the type (D-89) |
| confirmation_used (new) | `s.confirmation_used` (M1/M5 only) — 1 = case (b), 0 = case (a) |

### 1.1 Fired signals that have no trade row — 2 adverse, 2 neutral

`strat_signals.fired` sums to 127 (adverse) / 137 (neutral) but there are 125 / 135 trade rows. The report's take counts are **trade rows**; the CSV carries the extra fired signals too, with `exit_reason = no_trade (...)` and empty trade columns, so nothing is hidden. Every one of them is the runner's duplicate guard (`model_runner.py` `_open`: `SELECT id FROM strat_trades WHERE model=:m AND asset=:c AND mode='paper' AND exit_ts IS NULL` → "already has trade — TAKE not duplicated", logged at INFO which the `.out` files do not capture, so this was verified from the tables, not from the log):

| fill | signal | model | coin | signal_ts | trade open at that evaluation | how it was verified |
|---|---|---|---|---|---|---|
| adverse | 758929 | M1 | BTC | 2026-07-17 14:15:05 | #683 fill 2026-07-17 15:05:00 exit 2026-07-17 15:05:00 `stop_be` | trade row with `placed_ts` ≤ signal ts and `exit_ts` ≥ signal ts |
| adverse | 611283 | M3 | BTC | 2026-03-11 10:15:05 | #578 fill — exit 2026-03-11 10:45:05 `cancelled:entry_expired` | trade row with `placed_ts` ≤ signal ts and `exit_ts` ≥ signal ts |
| neutral | 758929 | M1 | BTC | 2026-07-17 14:15:05 | #684 fill 2026-07-17 15:05:00 exit 2026-07-17 15:05:00 `stop_be` | trade row with `placed_ts` ≤ signal ts and `exit_ts` ≥ signal ts |
| neutral | 611283 | M3 | BTC | 2026-03-11 10:15:05 | #577 fill — exit 2026-03-11 10:45:05 `cancelled:entry_expired` | trade row with `placed_ts` ≤ signal ts and `exit_ts` ≥ signal ts |

The 0 'previous trade exited minutes earlier' cases are the evaluate-before-manage ordering already recorded in D-82; the same order runs live. Nothing was changed.

## 2. Eight M1 takes, seed 42, with the 15-minute candles

Selection: adverse-pass M1 trade rows sorted by `strat_trades.id`, `random.Random(42).sample(rows, 8)`, printed in time order — the same procedure and seed as v1.2. **The eight trades are not the same eight trades as v1.2**: under D-89 the M1 population changed (every signal now fires one to two candles later, with a different entry, and many v1.2 setups no longer fire), so the v1.3 trade rows are a different list and the seed picks different members. §2.1 therefore takes the v1.2 sample the other way round: each of the eight v1.2 trades is looked up in the v1.3 signal table to show what happened to that same setup under the new rules.

Candles are `strat_replay_candles` rows with `source='binance'`, `tf='15m'`; the table's `ts` is the kline **close** time (`…:14:59.999`), printed truncated to the second. Twelve candles = 8 before the trigger candle, the trigger candle (tag `SIGNAL` — it closes 5 s before the signal ts), 3 after. Tags: `wick` = `L.setup.wick_ts` (the sweep candle), `reclaim` = `L.setup.reclaim_ts`, `confirm` = `L.setup.confirmation_ts` (the candle whose close confirmed the reclaim, D-89), `FILL` / `EXIT` = the candle containing `fill_ts` / `exit_ts` (fills and exits are evaluated on the 5-minute sub-candles, so both can land in the same 15-minute candle).

### trade #583 — BTC long · signal 2026-03-14 07:00:05 (signal id 614581)

level `pdl` @ 70342.7 · wick 70256.0 @ 2026-03-14 06:44:59 · reclaim candle 2026-03-14 06:44:59 · confirmation candle 2026-03-14 06:59:59 · entry 70410.5 (never filled) · initial stop 70225.6518250773 · t1 70561.09999999999 · exit  @ 2026-03-14 07:45:05 `cancelled:entry_expired_no_fvg` · **R n/a (unfilled)** · conviction 0.7979

reclaim type: same-candle + confirmation (b) · reclaim_candles 1 · structural stop kept (70225.6518250773) · post-only: rested at the original limit (market 70591.9 at placement)

| close ts (UTC) | open | high | low | close | |
|---|---|---|---|---|---|
| 2026-03-14 04:59:59 | 71236.6 | 71269.3 | 70977.0 | 70996.3 |  |
| 2026-03-14 05:14:59 | 70996.3 | 71096.4 | 70935.4 | 71070.1 |  |
| 2026-03-14 05:29:59 | 71070.1 | 71119.0 | 71013.5 | 71076.3 |  |
| 2026-03-14 05:44:59 | 71076.2 | 71082.9 | 70871.1 | 70962.1 |  |
| 2026-03-14 05:59:59 | 70962.1 | 71004.8 | 70935.2 | 70980.7 |  |
| 2026-03-14 06:14:59 | 70980.7 | 70980.7 | 70858.6 | 70883.1 |  |
| 2026-03-14 06:29:59 | 70883.1 | 70883.2 | 70643.1 | 70669.9 |  |
| 2026-03-14 06:44:59 | 70669.8 | 70709.2 | 70256.0 | 70420.6 |  wick reclaim |
| 2026-03-14 06:59:59 | 70420.7 | 70655.0 | 70383.2 | 70591.9 | SIGNAL confirm |
| 2026-03-14 07:14:59 | 70591.9 | 70686.4 | 70556.3 | 70667.7 |  |
| 2026-03-14 07:29:59 | 70667.7 | 70668.2 | 70507.5 | 70649.6 |  |
| 2026-03-14 07:44:59 | 70649.6 | 70706.0 | 70444.0 | 70509.2 |  |

### trade #597 — ETH short · signal 2026-03-30 08:15:05 (signal id 633079)

level `4h_ob_top` @ 2064.95 · wick 2067.78 @ 2026-03-30 07:59:59 · reclaim candle 2026-03-30 07:59:59 · confirmation candle 2026-03-30 08:14:59 · entry 2062.37 (filled 2026-03-30 08:20:00) · initial stop 2069.2337452505612 · t1 2044.1108364996248 · exit 2069.23 @ 2026-03-30 08:35:00 `stop` · **R -1.179** · conviction 0.7711

reclaim type: same-candle + confirmation (b) · reclaim_candles 1 · structural stop kept (2069.2337452505612) · post-only: original limit 2059.1845817501867 rejected (would cross), re-quoted at 2062.37

| close ts (UTC) | open | high | low | close | |
|---|---|---|---|---|---|
| 2026-03-30 06:14:59 | 2046.83 | 2051.17 | 2044.04 | 2044.44 |  |
| 2026-03-30 06:29:59 | 2044.44 | 2045.89 | 2042.13 | 2044.45 |  |
| 2026-03-30 06:44:59 | 2044.44 | 2047.21 | 2043.19 | 2046.33 |  |
| 2026-03-30 06:59:59 | 2046.32 | 2048.59 | 2040.08 | 2041.5 |  |
| 2026-03-30 07:14:59 | 2041.5 | 2048.49 | 2040.87 | 2046.23 |  |
| 2026-03-30 07:29:59 | 2046.24 | 2047.3 | 2042.79 | 2043.7 |  |
| 2026-03-30 07:44:59 | 2043.71 | 2045.7 | 2040.5 | 2042.84 |  |
| 2026-03-30 07:59:59 | 2042.85 | 2067.78 | 2042.57 | 2058.7 |  wick reclaim |
| 2026-03-30 08:14:59 | 2058.69 | 2066.37 | 2057.29 | 2062.16 | SIGNAL confirm |
| 2026-03-30 08:29:59 | 2062.17 | 2064.9 | 2058.33 | 2059.14 | FILL |
| 2026-03-30 08:44:59 | 2059.14 | 2075.0 | 2059.0 | 2065.56 | EXIT |
| 2026-03-30 08:59:59 | 2065.56 | 2067.5 | 2061.37 | 2061.41 |  |

### trade #598 — BTC short · signal 2026-03-30 08:30:05 (signal id 633085)

level `asia_high` @ 67777.0 · wick 67888.5 @ 2026-03-30 08:14:59 · reclaim candle 2026-03-30 08:29:59 · confirmation candle 2026-03-30 08:29:59 · entry 67796.4 (filled 2026-03-30 08:35:00) · initial stop 67924.32723839514 · t1 67604.49017440324 · exit 67796.4 @ 2026-03-30 11:20:00 `stop_be` · **R +0.346** · conviction 0.928

reclaim type: later-candle (a) · reclaim_candles 2 · structural stop kept (67924.32723839514) · post-only: rested at the original limit (market 67727.0 at placement)

| close ts (UTC) | open | high | low | close | |
|---|---|---|---|---|---|
| 2026-03-30 06:29:59 | 67409.9 | 67430.0 | 67238.6 | 67288.5 |  |
| 2026-03-30 06:44:59 | 67288.5 | 67369.3 | 67250.0 | 67333.7 |  |
| 2026-03-30 06:59:59 | 67333.7 | 67394.7 | 67264.6 | 67273.4 |  |
| 2026-03-30 07:14:59 | 67273.5 | 67430.0 | 67180.5 | 67349.8 |  |
| 2026-03-30 07:29:59 | 67349.9 | 67402.8 | 67262.8 | 67301.2 |  |
| 2026-03-30 07:44:59 | 67301.3 | 67399.9 | 67255.0 | 67283.7 |  |
| 2026-03-30 07:59:59 | 67283.7 | 67612.6 | 67267.2 | 67595.9 |  |
| 2026-03-30 08:14:59 | 67595.9 | 67888.5 | 67574.6 | 67865.1 |  wick |
| 2026-03-30 08:29:59 | 67865.0 | 67880.0 | 67688.9 | 67727.0 | SIGNAL reclaim |
| 2026-03-30 08:44:59 | 67727.1 | 67920.0 | 67690.1 | 67725.4 | FILL |
| 2026-03-30 08:59:59 | 67725.1 | 67784.7 | 67623.1 | 67634.0 |  |
| 2026-03-30 09:14:59 | 67633.9 | 67667.9 | 67538.5 | 67550.0 |  |

### trade #601 — ETH long · signal 2026-03-31 10:15:05 (signal id 634327)

level `equal_lows_1h` @ 2013.58 · wick 2011.25 @ 2026-03-31 09:59:59 · reclaim candle 2026-03-31 09:59:59 · confirmation candle 2026-03-31 10:14:59 · entry 2016.78 (never filled) · initial stop 2009.748450174529 · t1 2025.69 · exit  @ 2026-03-31 11:00:05 `cancelled:entry_expired_no_fvg` · **R n/a (unfilled)** · conviction 0.8252

reclaim type: same-candle + confirmation (b) · reclaim_candles 1 · structural stop kept (2009.748450174529) · post-only: rested at the original limit (market 2023.83 at placement)

| close ts (UTC) | open | high | low | close | |
|---|---|---|---|---|---|
| 2026-03-31 08:14:59 | 2054.99 | 2058.0 | 2050.52 | 2052.97 |  |
| 2026-03-31 08:29:59 | 2052.97 | 2058.66 | 2051.25 | 2053.35 |  |
| 2026-03-31 08:44:59 | 2053.34 | 2053.49 | 2037.59 | 2040.08 |  |
| 2026-03-31 08:59:59 | 2040.08 | 2043.48 | 2034.42 | 2038.89 |  |
| 2026-03-31 09:14:59 | 2038.88 | 2043.62 | 2031.0 | 2036.63 |  |
| 2026-03-31 09:29:59 | 2036.63 | 2037.81 | 2021.71 | 2025.32 |  |
| 2026-03-31 09:44:59 | 2025.31 | 2031.71 | 2023.3 | 2029.64 |  |
| 2026-03-31 09:59:59 | 2029.64 | 2029.72 | 2011.25 | 2017.28 |  wick reclaim |
| 2026-03-31 10:14:59 | 2017.27 | 2024.42 | 2015.85 | 2023.83 | SIGNAL confirm |
| 2026-03-31 10:29:59 | 2023.83 | 2027.73 | 2022.57 | 2024.89 |  |
| 2026-03-31 10:44:59 | 2024.88 | 2026.43 | 2019.85 | 2022.68 |  |
| 2026-03-31 10:59:59 | 2022.68 | 2029.3 | 2022.22 | 2025.26 |  |

### trade #619 — ETH long · signal 2026-04-23 09:30:05 (signal id 660787)

level `pdl` @ 2311.41 · wick 2310.0 @ 2026-04-23 09:14:59 · reclaim candle 2026-04-23 09:14:59 · confirmation candle 2026-04-23 09:29:59 · entry 2319.96 (filled 2026-04-23 09:35:00) · initial stop 2308.6713425737407 · t1 2330.0 · exit 2308.67 @ 2026-04-23 10:05:00 `stop` · **R -1.123** · conviction 0.7432

reclaim type: same-candle + confirmation (b) · reclaim_candles 1 · structural stop kept (2308.6713425737407) · post-only: original limit 2320.327114191247 rejected (would cross), re-quoted at 2319.96

| close ts (UTC) | open | high | low | close | |
|---|---|---|---|---|---|
| 2026-04-23 07:29:59 | 2352.3 | 2352.69 | 2347.39 | 2349.43 |  |
| 2026-04-23 07:44:59 | 2349.44 | 2351.0 | 2346.0 | 2350.71 |  |
| 2026-04-23 07:59:59 | 2350.71 | 2352.18 | 2338.91 | 2342.81 |  |
| 2026-04-23 08:14:59 | 2342.81 | 2343.2 | 2336.82 | 2341.81 |  |
| 2026-04-23 08:29:59 | 2341.8 | 2343.72 | 2336.0 | 2337.76 |  |
| 2026-04-23 08:44:59 | 2337.76 | 2337.93 | 2333.33 | 2334.53 |  |
| 2026-04-23 08:59:59 | 2334.53 | 2339.5 | 2334.35 | 2338.55 |  |
| 2026-04-23 09:14:59 | 2338.55 | 2338.55 | 2310.0 | 2320.77 |  wick reclaim |
| 2026-04-23 09:29:59 | 2320.76 | 2326.66 | 2318.0 | 2320.19 | SIGNAL confirm |
| 2026-04-23 09:44:59 | 2320.2 | 2325.41 | 2319.33 | 2323.81 | FILL |
| 2026-04-23 09:59:59 | 2323.8 | 2324.28 | 2312.75 | 2314.77 |  |
| 2026-04-23 10:14:59 | 2314.77 | 2317.91 | 2303.9 | 2306.65 | EXIT |

### trade #622 — BTC long · signal 2026-04-29 18:30:05 (signal id 668125)

level `4h_ob_bottom` @ 74988.6 · wick 74868.0 @ 2026-04-29 18:14:59 · reclaim candle 2026-04-29 18:14:59 · confirmation candle 2026-04-29 18:29:59 · entry 75100.1 (filled 2026-04-29 18:35:00) · initial stop 74817.6921016008 · t1 75598.78859893279 · exit 75804.6 @ 2026-04-29 22:35:00 `time_stop` · **R +2.075** · conviction 0.9513

reclaim type: same-candle + confirmation (b) · reclaim_candles 1 · structural stop kept (74817.6921016008) · post-only: original limit 75130.1307005336 rejected (would cross), re-quoted at 75100.1

| close ts (UTC) | open | high | low | close | |
|---|---|---|---|---|---|
| 2026-04-29 16:29:59 | 76000.0 | 76087.9 | 75653.0 | 75810.0 |  |
| 2026-04-29 16:44:59 | 75810.0 | 75887.1 | 75682.6 | 75859.1 |  |
| 2026-04-29 16:59:59 | 75859.1 | 75880.1 | 75670.0 | 75774.7 |  |
| 2026-04-29 17:14:59 | 75774.7 | 75988.4 | 75774.7 | 75896.8 |  |
| 2026-04-29 17:29:59 | 75896.7 | 76076.0 | 75752.5 | 75855.1 |  |
| 2026-04-29 17:44:59 | 75855.3 | 75985.4 | 75840.4 | 75979.8 |  |
| 2026-04-29 17:59:59 | 75979.8 | 76145.8 | 75893.3 | 76139.6 |  |
| 2026-04-29 18:14:59 | 76139.3 | 76220.0 | 74868.0 | 75146.9 |  wick reclaim |
| 2026-04-29 18:29:59 | 75146.9 | 75337.8 | 75033.2 | 75107.6 | SIGNAL confirm |
| 2026-04-29 18:44:59 | 75107.6 | 75322.0 | 74906.5 | 75196.0 | FILL |
| 2026-04-29 18:59:59 | 75196.0 | 75476.1 | 75177.4 | 75470.1 |  |
| 2026-04-29 19:14:59 | 75470.1 | 75533.1 | 75331.1 | 75529.0 |  |

### trade #626 — ETH short · signal 2026-05-03 12:30:05 (signal id 672451)

level `4h_ob_top` @ 2330.57 · wick 2332.17 @ 2026-05-03 12:14:59 · reclaim candle 2026-05-03 12:14:59 · confirmation candle 2026-05-03 12:29:59 · entry 2328.84 (never filled) · initial stop 2332.996671917578 · t1 2322.593885388281 · exit  @ 2026-05-03 13:15:05 `cancelled:entry_expired_no_fvg` · **R n/a (unfilled)** · conviction 0.7412

reclaim type: same-candle + confirmation (b) · reclaim_candles 1 · structural stop kept (2332.996671917578) · post-only: rested at the original limit (market 2325.87 at placement)

| close ts (UTC) | open | high | low | close | |
|---|---|---|---|---|---|
| 2026-05-03 10:29:59 | 2312.8 | 2312.87 | 2310.19 | 2310.46 |  |
| 2026-05-03 10:44:59 | 2310.47 | 2314.45 | 2310.18 | 2313.61 |  |
| 2026-05-03 10:59:59 | 2313.6 | 2313.61 | 2310.41 | 2310.53 |  |
| 2026-05-03 11:14:59 | 2310.53 | 2315.45 | 2309.25 | 2313.65 |  |
| 2026-05-03 11:29:59 | 2313.64 | 2314.3 | 2312.33 | 2312.54 |  |
| 2026-05-03 11:44:59 | 2312.53 | 2324.04 | 2312.53 | 2320.75 |  |
| 2026-05-03 11:59:59 | 2320.76 | 2327.29 | 2318.52 | 2318.87 |  |
| 2026-05-03 12:14:59 | 2318.88 | 2332.17 | 2318.88 | 2328.56 |  wick reclaim |
| 2026-05-03 12:29:59 | 2328.56 | 2329.67 | 2323.89 | 2325.87 | SIGNAL confirm |
| 2026-05-03 12:44:59 | 2325.87 | 2326.41 | 2323.0 | 2324.53 |  |
| 2026-05-03 12:59:59 | 2324.52 | 2326.0 | 2322.25 | 2324.58 |  |
| 2026-05-03 13:14:59 | 2324.57 | 2327.93 | 2323.38 | 2326.3 |  |

### trade #677 — ETH short · signal 2026-07-06 16:45:05 (signal id 746383)

level `asia_high` @ 1798.4 · wick 1804.5 @ 2026-07-06 16:14:59 · reclaim candle 2026-07-06 16:29:59 · confirmation candle 2026-07-06 16:44:59 · entry 1795.26 (filled 2026-07-06 16:50:00) · initial stop 1806.361571058799 · t1 1785.07 · exit 1794.54 @ 2026-07-06 19:15:05 `dead_trade` · **R -0.032** · conviction 0.6455

reclaim type: same-candle + confirmation (b) · reclaim_candles 2 · structural stop kept (1806.361571058799) · post-only: rested at the original limit (market 1794.28 at placement)

| close ts (UTC) | open | high | low | close | |
|---|---|---|---|---|---|
| 2026-07-06 14:44:59 | 1750.92 | 1755.51 | 1748.7 | 1751.52 |  |
| 2026-07-06 14:59:59 | 1751.53 | 1752.56 | 1744.41 | 1751.08 |  |
| 2026-07-06 15:14:59 | 1751.09 | 1766.12 | 1750.77 | 1762.67 |  |
| 2026-07-06 15:29:59 | 1762.67 | 1765.97 | 1757.78 | 1764.69 |  |
| 2026-07-06 15:44:59 | 1764.68 | 1781.54 | 1758.54 | 1776.85 |  |
| 2026-07-06 15:59:59 | 1776.85 | 1788.26 | 1770.53 | 1787.72 |  |
| 2026-07-06 16:14:59 | 1787.72 | 1803.59 | 1781.53 | 1799.74 |  wick |
| 2026-07-06 16:29:59 | 1799.73 | 1804.5 | 1784.77 | 1787.25 |  reclaim |
| 2026-07-06 16:44:59 | 1787.26 | 1796.77 | 1786.77 | 1794.28 | SIGNAL confirm |
| 2026-07-06 16:59:59 | 1794.28 | 1795.97 | 1789.5 | 1791.9 | FILL |
| 2026-07-06 17:14:59 | 1791.9 | 1796.64 | 1788.21 | 1792.68 |  |
| 2026-07-06 17:29:59 | 1792.68 | 1799.86 | 1792.67 | 1795.45 |  |

### 2.1 The eight v1.2 sampled trades under the v1.3 rules

For each v1.2 seed-42 trade (kept in `strat_trades_v12` / `strat_signals_v12`, the backup tables the v1.3 driver made before its DELETEs) the v1.3 adverse `strat_signals` rows for the same model and coin from 15 candles before to 3 candles after the v1.2 signal, with their fire flag, reason and (when fired) the trade row. Read: `fired 1` with a trade = the same sweep was taken under D-89 at the later confirmation; `fired 0` with a reason = why the v1.3 rules did not take it.

#### v1.2 trade #397 — ETH · v1.2 signal 2026-03-19 13:15:05 · v1.2 entry 2125.7 · v1.2 R -1.112 `stop`

v1.3 rows in the window: 0, fired 0 → not taken under v1.3

| v1.3 signal ts | fired | direction | conviction | reclaim_candles | confirmation_used | reason |
|---|---|---|---|---|---|---|

#### v1.2 trade #418 — BTC · v1.2 signal 2026-03-31 09:30:05 · v1.2 entry 66503.6 · v1.2 R -1.180 `stop`

v1.3 rows in the window: 2, fired 2 → trade #599 entry 66391.7, floor 1, reclaim_candles 1 / confirmation_used 1, fill 2026-03-31 09:50:00, `stop` R -1.3270377747466129

| v1.3 signal ts | fired | direction | conviction | reclaim_candles | confirmation_used | reason |
|---|---|---|---|---|---|---|
| 2026-03-31 09:45:05 | 1 | long | 0.8132 | 1 | 1 | TAKE long conviction 0.81 (full) |
| 2026-03-31 10:15:05 | 1 | long | 0.8718 | 1 | 1 | TAKE long conviction 0.87 (full) |

#### v1.2 trade #424 — BTC · v1.2 signal 2026-04-04 16:15:05 · v1.2 entry 67330.6 · v1.2 R -1.754 `stop`

v1.3 rows in the window: 0, fired 0 → not taken under v1.3

| v1.3 signal ts | fired | direction | conviction | reclaim_candles | confirmation_used | reason |
|---|---|---|---|---|---|---|

#### v1.2 trade #431 — ETH · v1.2 signal 2026-04-07 11:00:05 · v1.2 entry 2092.0 · v1.2 R -1.151 `stop`

v1.3 rows in the window: 0, fired 0 → not taken under v1.3

| v1.3 signal ts | fired | direction | conviction | reclaim_candles | confirmation_used | reason |
|---|---|---|---|---|---|---|

#### v1.2 trade #438 — BTC · v1.2 signal 2026-04-14 07:15:05 · v1.2 entry 74647.8 · v1.2 R -0.035 `stop_be`

v1.3 rows in the window: 1, fired 1 → trade #613 entry 74748.9, floor 0, reclaim_candles 1 / confirmation_used 1, fill —, `cancelled:entry_expired_no_fvg` R —

| v1.3 signal ts | fired | direction | conviction | reclaim_candles | confirmation_used | reason |
|---|---|---|---|---|---|---|
| 2026-04-14 07:30:05 | 1 | short | 0.6358 | 1 | 1 | TAKE short conviction 0.64 (half) |

#### v1.2 trade #466 — BTC · v1.2 signal 2026-05-12 11:15:05 · v1.2 entry 80573.0 · v1.2 R -1.435 `stop`

v1.3 rows in the window: 0, fired 0 → not taken under v1.3

| v1.3 signal ts | fired | direction | conviction | reclaim_candles | confirmation_used | reason |
|---|---|---|---|---|---|---|

#### v1.2 trade #472 — BTC · v1.2 signal 2026-05-16 07:15:05 · v1.2 entry 78336.8 · v1.2 R -1.158 `stop`

v1.3 rows in the window: 0, fired 0 → not taken under v1.3

| v1.3 signal ts | fired | direction | conviction | reclaim_candles | confirmation_used | reason |
|---|---|---|---|---|---|---|

#### v1.2 trade #483 — BTC · v1.2 signal 2026-05-25 15:00:05 · v1.2 entry 77639.1 · v1.2 R -1.378 `stop`

v1.3 rows in the window: 0, fired 0 → not taken under v1.3

| v1.3 signal ts | fired | direction | conviction | reclaim_candles | confirmation_used | reason |
|---|---|---|---|---|---|---|

## 3. Recomputing the summary table from the CSV

`docs/models/tools/summary_from_csv.py` (stdlib only, unchanged from v1.2):

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

Output on the committed CSV — matches REPLAY-6M-v1.3.md §3:

```
fill    model  fired takes filled wins   win%    expR    sumR    PF  exit reasons
adverse M1        82    81     51   20   39.2  -0.310  -15.80  0.49  {'cancelled:entry_expired_no_fvg': 27, 'stop_be': 20, 'stop': 21, 'thesis_failed': 2, 'time_stop': 7, 'cancelled:entry_expired': 3, 'dead_trade': 1}
adverse M3        15    14      9    1   11.1  -0.485   -4.37  0.56  {'cancelled:entry_expired': 5, 'time_stop': 1, 'stop': 7, 'thesis_failed': 1}
adverse M5        12    12      6    3   50.0  -0.302   -1.81  0.53  {'cancelled:entry_expired': 6, 'stop_be': 2, 'stop': 3, 'time_stop': 1}
adverse M6        18    18     16    4   25.0   0.001    0.02  1.00  {'stop_be': 2, 'thesis_failed': 9, 'stop': 2, 'cancelled:entry_expired': 2, 'target': 2, 'time_stop': 1}
neutral M1        92    91     55   23   41.8  -0.260  -14.31  0.53  {'cancelled:entry_expired_no_fvg': 32, 'stop_be': 25, 'stop': 20, 'thesis_failed': 2, 'time_stop': 7, 'cancelled:entry_expired': 4, 'dead_trade': 1}
neutral M3        15    14      9    1   11.1  -0.485   -4.37  0.56  {'cancelled:entry_expired': 5, 'time_stop': 1, 'stop': 7, 'thesis_failed': 1}
neutral M5        12    12      6    3   50.0  -0.302   -1.81  0.53  {'cancelled:entry_expired': 6, 'stop_be': 2, 'stop': 3, 'time_stop': 1}
neutral M6        18    18     16    4   25.0   0.001    0.02  1.00  {'stop_be': 2, 'thesis_failed': 9, 'stop': 2, 'cancelled:entry_expired': 2, 'target': 2, 'time_stop': 1}
```

Same table in SQL (`docs/models/tools/summary.sql`; add the six new columns to the scratch table DDL in the same order as the CSV header):

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

Window = 2026-03-10 19:45:00 → 2026-09-06 19:45:00 UTC (17,281 boundaries × BTC, ETH) — the feed tables are exactly the v1.2 ones (no `v12_setup.py` re-run, D-92); `perpl_replay_n` is the `mysqldump` clone of the same feed tables; only `strat_signals` / `strat_trades` differ between the two.

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
| strat_signals (model rows) | 207,372 (fired 127) | 2026-03-10 19:45:05 | 2026-09-06 19:45:05 |
| strat_trades (model rows) | 125 |  |  |
| perpl_replay_n strat_signals (model rows) | 207,372 (fired 137) | | |
| perpl_replay_n strat_trades (model rows) | 135 | | |

Reading it: identical feed rows to the v1.2 pack (candles 15m 17,280 per coin over the window + warm-up; OI / liquidations / map from **2026-08-07** only — free-tier 0xArchive key, D-81; `strat_calibration_hist` 182/182 NULL per key per coin, so every model ran with the fallback normaliser). The v1.2 result tables are preserved beside them as `strat_trades_v12`, `strat_signals_v12`, `mind_model_state_v12` in both databases.

## 5. Where it ran and how to run it again

| | |
|---|---|
| Server | `<server>` (hostname `<server>`), the production host; MySQL on the same box |
| Engine tree | `/root/audit_tree/backend` — an overlay copy of the working tree. Verified identical to commit **`db64b81`** (`Spec v1.3: realistic post-only fills, stop floor, sweep confirmation`, origin/master): all 74 `.py/.yaml` files under `backend/app/strategy_engine` match the HEAD blobs md5-for-md5 after CRLF→LF normalisation (0 differences, 0 extra/missing files) |
| Driver | `/root/replay_v13.py` sha256 `985eb9860a8119bfd7d001560e1584518b60053918ef05290f59037e36088267` (replay_v12.py + `apply_v17` before its DELETEs and the v1.3 log/feeds file names; not in the repo, copy kept in the session scratchpad) |
| Stats | `/root/replay_stats_v13.py` sha256 `5a90c0c91a1772dc56275c6176efe5deb3e88a1fd4a4797af3cb39b563ae3716` → `/root/replay_stats_v13_{adverse,neutral}.md/.json` (REPLAY-6M-v1.3.md appendix is the adverse `.md` take tables verbatim) |
| Data setup | not re-run for v1.3 (D-92): `perpl_replay` / `perpl_replay_n` feed tables are as `v12_setup.py` left them on 2026-09-06 |
| Databases | `perpl_replay` = adverse pass, `perpl_replay_n` = neutral pass |
| Logs | `/root/replay_v13_adverse.out` (35,112 lines / 5,995,073 bytes, mtime 2026-09-07T13:38:54Z, `done: 17281 boundaries in 14426s`), `/root/replay_v13_neutral.out` (35,112 lines / 5,995,145 bytes, mtime 2026-09-07T13:38:14Z, `done: 17281 boundaries in 14385s`); WARNING level and above only; 0 tracebacks in either |
| Runtime | adverse 14,426 s, neutral 14,385 s (both started 2026-09-07 ~09:38 UTC, finished 13:38 UTC) |

Exact re-run (as root on the server; `DATABASE_URL` and `OXARCHIVE_API_KEY` come from `/var/www/terminal/backend/.env` and are never printed). The driver empties `strat_trades`, `strat_signals`, `mind_model_state`, `strat_telegram_outbox`, `strat_changelog` and `strat_calibration` in `REPLAY_DB` first (the `*_v12` backup tables are untouched) and never writes to `perpl_terminal`:

```bash
cd /root && set -a && . /var/www/terminal/backend/.env && set +a
PY=/var/www/terminal/backend/venv/bin/python

# 1 the two passes (each ~5 h; run both at once as they were)
FILL_MODEL=adverse REPLAY_DB=perpl_replay   REPLAY_DAYS=180 AUDIT_TREE=/root/audit_tree/backend nohup $PY /root/replay_v13.py > /root/replay_v13_adverse.out 2>&1 &
FILL_MODEL=neutral REPLAY_DB=perpl_replay_n REPLAY_DAYS=180 AUDIT_TREE=/root/audit_tree/backend nohup $PY /root/replay_v13.py > /root/replay_v13_neutral.out 2>&1 &
wait

# 2 stats (writes /root/replay_stats_v13_<fill>.md and .json)
REPLAY_LOG=/root/replay_v13_adverse.out AUDIT_TREE=/root/audit_tree/backend $PY /root/replay_stats_v13.py 180 perpl_replay   adverse > /root/replay_stats_v13_adverse.md
REPLAY_LOG=/root/replay_v13_neutral.out AUDIT_TREE=/root/audit_tree/backend $PY /root/replay_stats_v13.py 180 perpl_replay_n neutral > /root/replay_stats_v13_neutral.md

# 3 this verification pack (CSV + candle samples + v1.2-sample lookup + table counts)
$PY /root/verify_pack_v13.py      # -> /root/replay-v1.3-takes.csv, /root/replay-v1.3-pack.json
```

The two passes are deterministic functions of the replay tables and the tree, so a re-run on unchanged data reproduces the 127 / 137 fired and 125 + 135 trade rows; the pack script was run twice and produced the same CSV sha256 both times. The neutral pass's `fill ordering:` line in its `.out` (`{'both_inside': 7, 'favourable_first': 5, 'adverse_first_or_tie': 2}`) is the check that the D-78 rule engaged.
