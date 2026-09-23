# 10. Structure Engine and Heuristic Mind Framework (shared by M1 to M6)

Read first. Every model doc (11 to 16) assumes the components here exist. This is added to the existing strategy engine where strategies 01 to 06 already run in paper mode; it reuses the existing data layer (Hyperliquid candles, trades, l2Book, OI, funding, liquidations, liquidation map, cohort), the existing RiskEngine, the existing paper executor, the existing signals and trades tables, and the existing Telegram alerts.

## 1. What is new

Two shared modules and six new strategies:

- `structure/` computes market structure on 15m, 1h, 4h and daily: swings, trend, BOS, CHoCH, displacement, order blocks, fair value gaps, liquidity pools, premium and discount, sweeps, session ranges, VWAP and volume profile, plus the higher-timeframe alignment table.
- `mind/` is a deterministic heuristic decision layer (no AI model). Each strategy has a Mind with weighted reasons, vetoes, context multipliers, in-trade checks and a weekly learning rule. The Mind decides take or skip, size tier, early exit, and explains itself in the log.
- `strategies/m1_sweep_reclaim.py` ... `m6_weekly_open_reclaim.py`.

All six start in paper mode with their own trade tags so results are separable from strategies 01 to 06.

## 2. Structure engine (`structure/`)

All functions operate on closed candles only. Thresholds in ATR(14) of the same timeframe unless stated.

### 2.1 Swings
- Swing high on a timeframe: candle high strictly greater than the highs of `k` candles on each side. `k` = 2 on 15m, 1h and 4h, 3 on daily (spec v1.1, D-64; was 3 on 1h and 4h). Mirror for swing low.
- Store swings with ts, price, timeframe, confirmed_at (the close of the k-th following candle).

### 2.2 Trend
- Uptrend if the last 2 confirmed swing highs are ascending and the last 2 swing lows are ascending (spec v1.1, D-64; was 3). Downtrend mirror. Otherwise range.
- `trend(tf)` returns up, down or range and the price of the last swing high and low.

### 2.3 BOS and CHoCH
- BOS up: a candle close above the most recent confirmed swing high while trend is up or range. BOS down mirror.
- CHoCH down: in an uptrend, the first candle close below the most recent higher swing low. CHoCH up mirror. After CHoCH, trend becomes range until a new BOS establishes direction.
- Store events with ts, tf, type, level.

### 2.4 Displacement
- Candle range >= 1.5 ATR and body >= 60 percent of range and the candle closes beyond a swing (it is the BOS candle) or is part of a 2-candle sequence that does.
- `displacement_grade` = clip(0.5 x min(range / ATR - 1.5, 1) + 0.5 x min((body_ratio - 0.6) / 0.3, 1), 0, 1) (spec v1.1, D-64; the range term is the excess over the 1.5 ATR qualifier in ATR — 1.5 ATR with 60 percent body scores 0.0, 2.0 ATR with 75 percent body 0.5, 2.5 ATR with 90 percent body 1.0). The pre-v1.1 formula min(1, (range / 1.5 ATR) x (body_ratio / 0.6)) / 2 was a constant 0.5 (D-61).

### 2.5 Order blocks
- Bullish OB: the last down candle (close < open) before an up displacement. Zone = that candle's open to close (body only). Bearish mirror.
- Status: fresh (never revisited), tested (price entered zone but did not close through), broken (a close through the far side).

### 2.6 Fair value gaps
- Bullish FVG: candle1.high < candle3.low. Zone = candle1.high to candle3.low. Mid = 50 percent. Bearish mirror.
- Status: open, half filled (price reached mid), filled (price reached far edge).

### 2.7 Liquidity pools
- Equal highs: two or more swing highs within 0.1 ATR of each other on 1h or 4h. Equal lows mirror.
- Reference levels: prior day high and low, prior week high and low, daily open (00:00 UTC), weekly open (Monday 00:00 UTC), Asia session high and low (00:00 to 07:00 UTC), London high and low (07:00 to 13:00 UTC), New York high and low (13:00 to 21:00 UTC).
- Liquidation clusters from the existing liquidation map: any 0.25 percent band with notional >= band_p80 (the calibrated 80th percentile of non-zero map-band notional over the trailing 180 days, spec v1.1 Part C; fallback 0.1 percent of coin OI while the calibration row is NULL), tagged with side, notional, wallet count.
- `pools_above(price)` and `pools_below(price)` return sorted lists with type, level, strength (equal highs count, or cluster notional rank).

### 2.8 Premium and discount
- Current 4h range = last confirmed 4h swing high to last confirmed 4h swing low that contain price. Mid = 50 percent. Price above mid = premium, below = discount. Also compute the same for the 1h range.
- Longs are allowed only in discount of the 4h range, shorts only in premium. Models may relax to the 1h range where stated.

### 2.9 Sweeps
- A sweep of a level (pool or zone edge) is: a candle wick beyond the level by >= 0.1 ATR and <= 0.5 ATR, followed within 3 candles by a close back on the original side. Reclaim candle = the first such close. `reclaim_quality` = body / range of the reclaim candle.
- Confirmed reclaim (spec v1.3, D-89; used by M1 and M5 entries): the reclaim is confirmed either (a) when the reclaim close is on a later candle than the sweep wick — confirmed on that close — or (b) when the sweep wick and the reclaim close are the same candle, by one additional 15m candle that closes on the original side of the level and whose extreme does not exceed the sweep wick. `reclaim_candles` (wick candle to reclaim close, inclusive) and `confirmation_used` (True for case b) are logged on the signal row. `structure/sweeps.py::confirmed_reclaim`.
- Count sweeps per level per UTC day.

### 2.10 VWAP and volume profile
- Session VWAP from 00:00 UTC and from each session open, with 1 and 2 standard deviation bands.
- Volume profile over the current day and prior day using 0.05 percent price bins: point of control, value area (70 percent of volume) high and low.

### 2.11 Higher-timeframe alignment table
Recomputed on every closed 15m candle per coin:

```
daily_bias: up | down | neutral
trend_4h, trend_1h: up | down | range
range_4h: {high, low, mid, position: premium | discount, pct}
range_1h: same
nearest_zone_above, nearest_zone_below: {type: OB | FVG, tf, top, bottom, status}
nearest_pool_above, nearest_pool_below: {type, level, strength}
last_event_4h, last_event_1h: {type: BOS | CHoCH, direction, ts, level}
session: asia | london | newyork | dead, minutes_into_session
day_type: trend_up | trend_down | range | event | squeeze | no_trade (from section 3)
event_within_2h: bool, event_within_30m: bool
asia_range: {high, low}, weekly_open, daily_open, pdh, pdl, pwh, pwl
```

## 3. Day type classifier (`structure/day_type.py`)

Evaluated at 07:00 UTC (provisional), 14:00 UTC (final for the US session), and on every 15m close as a running label. Inputs: open location versus prior day value area, OI 4h change, taker delta skew over last 2h, realized vol percentile, funding z-score, events calendar.

- `event`: macro event within 2h or last 30m.
- `squeeze`: funding z >= 2 on one side AND price moving against that side over the last 2h by >= 1.5 ATR(1h) AND OI falling >= 2 percent over 2h.
- `trend_up`: open above prior day value area high OR two 1h BOS up today, AND OI 4h change >= +1 percent, AND delta skew >= 60 percent buy, AND trend_1h up. `trend_down` mirror.
- `no_trade`: realized vol percentile < 25 AND volume last 2h < 50 percent of 20-day average for that time of day.
- `range`: otherwise.

## 4. Heuristic Mind framework (`mind/`)

### 4.1 Reasoning behind the design
A trader's judgement is not a checklist. It is: several pieces of evidence of different importance, a few things that kill the trade outright, an adjustment for the environment, a willingness to leave a trade that is not behaving, and a slow update of beliefs from results. The framework encodes exactly those five things, deterministically, and logs every number so it can be audited. It does not call any AI model.

Design rules:
- Evidence is graded 0 to 1 by a detector, never boolean, so partial evidence counts partially.
- Weights express importance and differ per model. Starting weights are set from how discriminating each reason is expected to be; the learning rule adjusts them slowly.
- Vetoes are the failure modes. They are boolean and absolute.
- Context multipliers scale conviction for day type, session, level history, recent form and event proximity. Conviction is capped at 1.0 after the multipliers (spec v1.3, D-90).
- In-trade checks re-evaluate whether the thesis is working and can exit before the stop.
- Learning only adjusts weights after enough observations and within tight bounds, so it cannot chase noise.

### 4.2 Data structures

```python
@dataclass
class Reason:
    key: str
    description: str
    detector: Callable[[Snapshot], float]   # returns 0.0 to 1.0
    weight: float                            # starting weight
    min_weight: float = 0.3
    max_weight: float = 3.0

@dataclass
class Veto:
    key: str
    description: str
    detector: Callable[[Snapshot], bool]

@dataclass
class ContextRule:
    key: str
    multiplier: Callable[[Snapshot], float]  # returns e.g. 0.5 to 1.2

@dataclass
class InTradeCheck:
    key: str
    description: str
    detector: Callable[[Snapshot, Position], bool]  # True = thesis failing

@dataclass
class Decision:
    take: bool
    conviction: float           # 0 to 1 after multipliers, capped at 1.0 (spec v1.3, D-90)
    raw_conviction: float       # before multipliers
    size_tier: str              # none | half | full
    reasons: list[tuple[str, float, float]]   # key, strength, weight
    vetoes_hit: list[str]
    multipliers: list[tuple[str, float]]
    thesis: str                 # templated one-paragraph text
```

### 4.3 Evaluation

```
raw = sum(strength_i * weight_i) / sum(weight_i)
conviction = min(1.0, raw * product(multipliers))   # capped at 1.0 (spec v1.3, D-90)
if any veto: take = False, size_tier = none
elif conviction < 0.55: take = False
elif conviction < 0.70: take = True, size_tier = half
else: take = True, size_tier = full
```

Size tier maps to RiskEngine: full = 1.5 percent risk, half = 0.75 percent. Never more.

Replay-only unavailable handling (spec v1.1 Part D.6, D-68): each Reason and Veto carries `inputs` — the data feeds it
reads (`strategies/feed_inputs.py`: oi, taker, liq, liqmap, gauge, book, cohort, events). When the replay marks a feed
unavailable at a boundary (`Snapshot.unavailable`), a reason listing it is excluded from BOTH the numerator and the
denominator of `raw` (logged in `reasons_json` with `excluded_weight`; `Decision.effective_max_weight()` is the weight
that remained), and a veto listing it is skipped and logged `unevaluated: true`. Unavailable is never scored as zero.
Live evaluation never sets `unavailable`, so the worker's arithmetic is exactly the block above.

### 4.4 Thesis text
Templated, not generated: "{model} {direction} {coin} at {level_type} {level}. Strongest: {top 3 reasons with strengths}. Weakest: {bottom reason}. Wrong if {invalidation}. Expect {target} within {expected_hold}." Goes to the Telegram alert and the trade log.

### 4.5 In-trade management
Every closed 15m candle while in position: run the model's InTradeChecks. If the number of failing checks >= the model's `exit_threshold` (default 2) for 2 consecutive candles, exit at market and tag exit_reason = thesis_failed. Universal check for all models: at 150 percent of expected hold with progress < 0.5R, exit, tag = dead_trade. Universal: after T1 is hit move stop to breakeven.

### 4.6 Learning rule (weekly job, `mind/learn.py`)
For each model, for each reason with >= 100 logged trades: compute win rate and mean R when strength >= 0.7 (strong) versus strength <= 0.3 (weak). `discrimination` = mean_R_strong minus mean_R_weak. Update `weight += 0.05 * sign(discrimination)` if |discrimination| > 0.1R, clipped to [min_weight, max_weight], and never more than 25 percent total change per 30 days. Log every change with the numbers. Reasons with |discrimination| < 0.05R after 200 trades are flagged `review` in the UI, not removed. Learning is disabled for the first 60 days after a model starts; weights are fixed until then.

Calibration report (weekly): realised win rate and mean R by conviction bucket (0.55 to 0.65, 0.65 to 0.75, 0.75+). If the top bucket does not beat the bottom bucket by at least 0.2R after 100 trades, flag the model `mind_not_discriminating`.

### 4.7 Snapshot
`Snapshot` is built per coin on every closed 15m candle and on demand: alignment table (section 2.11), last 20 candles on 15m, 1h, 4h, ATR per tf, OI now and changes over 15m, 1h, 4h, 24h, funding z and crowding level, taker delta and CVD over 15m, 1h, 2h, liquidation fills last 5m and 15m by side with wallet counts, liquidation map bands within 3 ATR, cohort net_dir and fresh_agree and last 60m adds and reduces, l2Book depth within 0.3 and 0.5 percent, session VWAP and bands, value area, level sweep counts today, model recent form (last 5 outcomes), events.

### 4.8 Calibrated liquidation normalisers (spec v1.1 Part C, `structure/calibration.py`, D-66)
Per coin, daily at 00:05 UTC and on the worker's first run, persisted to `strat_calibration (coin, key, value,
computed_at, sample_count, window_days, note)` and logged on every recompute:
- `liq_5m_p90_long` / `liq_5m_p90_short`: 90th percentile of same-side liquidation notional over rolling 5-minute
  windows (1-minute steps, non-zero windows only) across the trailing 180 days; falls back to the longest available
  window, minimum 30 days and minimum 20 non-zero windows, else NULL.
- `band_p80`: 80th percentile of non-zero liquidation-map band notional (`strat_liq_map_hist`, 15m, 0.25 percent
  bands) over the same window; same fallback rule. One map source per boundary, by precedence `levels` (0xArchive
  position snapshots) > `live` (our sampler) > `fills` (forward-fills proxy) — the three constructions are never mixed
  at the same boundary (D-69).
- `live_coverage`: live-feed notional / 0xArchive notional over the last 3 days, recomputed weekly (Monday) and on the
  worker's first run; the worker pulls the archive's last 4 days first so the comparison is against fresh archive rows.
  The archive side is the full archive STREAM (every fill the API returns for the window), not the stored archive rows —
  stored rows exclude the fills shadowed by live rows (D-70). Live-feed liquidation rows are scaled by 1 / live_coverage
  ONLY in windows that hold no archive row; where archive rows exist, live + archive already form the full tape and
  nothing is scaled. NULL while no archive rows exist.
- While a value is NULL the pre-v1.1 fixed normaliser (0.10 percent of coin OI) is used and logged.
Sites: M1 fuel / liq_5m_p90 (swept side); M1 eligible cluster threshold and `cluster_below_uncleared` reference =
band_p80; M2 cluster_cleared / liq_5m_p90; M3 cluster_fuel / band_p80; M4 T2 cluster threshold band_p80 and
cluster_reward / (5 x band_p80); M5 fuel / (0.8 x liq_5m_p90); pools.py cluster eligibility band_p80. Each model's
Breakdown tab shows the normaliser table (key, calibrated value or fallback, sample count, computed_at, note).
- Point-in-time history (spec v1.2 Part 4, D-76): `strat_calibration_hist (coin, key, as_of, value, computed_at,
  sample_count, window_days, note)` holds one row per UTC day boundary for `liq_5m_p90_long / liq_5m_p90_short /
  band_p80`, each computed from data strictly BEFORE `as_of` over the trailing window (min 30 d, max 180 d, same
  non-zero-count rule; NULL with a note below 30 d). The daily recompute appends today's row; `build_history` fills a
  span in one pass. In replay the lookup is the latest `as_of <= boundary` (`load_as_of`), never a recompute, never
  future data; every NULL lookup is logged as a WARNING so the replay log proves the count.

### 4.9 Liquidation history and replay data (spec v1.1 Parts B and D, D-65 / D-67 / D-69 / D-70)
- `data/oxarchive.py` loads Hyperliquid liquidation fills from 0xArchive (`OXARCHIVE_API_KEY`) into `strat_liquidations`
  with the live schema (`source='oxarchive'`). Dedup (D-70): an archive fill whose (ts, liquidated_user, coin, px)
  matches a LIVE row is skipped (the live row stands for it — live rows aggregate the maker-split fills); archive
  fills are otherwise kept by multiplicity of (ts, user, px, sz), because the archive holds distinct fills (distinct
  trade_id, same tx_hash) sharing that tuple, so a re-run inserts nothing and no genuine split fill is dropped.
- Plan window (D-69): the owner's key is on the free tier — the API serves the most recent 30 days only and answers
  older requests with 403 `history_window_exceeded` carrying a rolling "earliest allowed timestamp"; the loader clamps
  its start to that boundary (+5 min margin, re-clamped when the boundary moves during the run) and reports
  `status=ok_plan_window` with the effective start. 180 days need the Build plan or a one-off Data Catalog purchase —
  the owner's decision; the code needs no change for it. 2026-09-06 load: 2026-08-07 11:42 UTC → now.
- Liquidation-map history `strat_liq_map_hist` is rebuilt for the loaded window from 0xArchive projected-levels
  POSITION SNAPSHOTS (`source='levels'`, ~5-min cadence, each 15m boundary takes the latest snapshot at/before it; the
  archive's ~0.4 percent price grid is re-bucketed onto our 0.25 percent bands, band width = snapshot mid x 0.25
  percent, notional-weighted band price) — this is the
  map used (record in REPLAY-6M.md §1). The fills construction (`source='fills'`: at each boundary the fills of the
  next 24 h bucketed around that boundary's close) is also written for the same window but only as the last
  fallback; it is forward-looking. The worker persists the live map each 15m (`source='live'`). Readers take one
  source per boundary by precedence levels > live > fills (`map_rows`, calibration `band_p80`).
- The worker re-pulls the archive's last 4 days on first run and weekly before the `live_coverage` recompute
  (`ModelRunner._refresh_archive`), so the coverage ratio and the recent tape stay current without a manual run.
- `data/binance_replay.py` fills `strat_replay_candles` / `strat_replay_oi` (`source='binance'`, USDT-M perpetuals)
  for replay only; the live `strat_*` tables are never written. OI history is limited to Binance's 30-day public window.
- `ModelEvaluator.replay_mode` reads candles / OI / map history from those tables where the live store has no rows and
  computes the per-boundary `unavailable` set (4.3). Daily candles are still derived from 4h (D-03).

### 4.10 Execution realism (spec v1.3 Parts 1 and 2, D-87 / D-88)

- Paper post-only placement, all models (`execution/paper.py::post_only_place`, wired in `ModelTradeManager.paper_post_only_quote` and used by the initial entry and by every re-price path — M6 reprice, M1 FVG mid). The paper tape holds mids only (`strat_book_5s` live, the synthesised candle tape in replay), so "the touch" is the last mid in the boundary window, else the last 15m close. A buy limit at or above the touch, or a sell limit at or below it, is not filled at its price: it is rejected (Hyperliquid Alo behaviour), re-quoted once one tick inside the touch (buy at touch minus tick, sell at touch plus tick; tick = 1 bp of price, 0.1 floor) and rests there. It fills only when a later print goes through that price (`limit_fills_through`, strict) and is cancelled at the strategy's entry validity (`cancelled:entry_expired`). The rejection and re-quote are logged on the trade row (`lifecycle_json.post_only` = rejected / original_px / requote_px / market_px / ts; columns `entry_requoted`, `entry_px_orig`); re-prices land in `lifecycle_json.post_only_reprice`. Sizing uses the re-quoted entry. The live paper worker and the replay driver run the same `ModelEvaluator` / `ModelTradeManager` code — there is no second executor.
- Minimum stop distance, all models (`ModelStrategy.apply_stop_floor`, run centrally in `ModelStrategy.evaluate` after each model's `build_intent`): after the structural stop is computed, if the stop distance is below 0.5 ATR(15m) for M1, M3 and M5, or 0.5 ATR(1h) for M2, M4 and M6, the stop is moved out to exactly that distance beyond entry. Size and R use the final stop; targets stay as the model computed them from the structural stop. No ATR (0 or missing) means no floor. Recorded as `strat_trades.stop_floor_applied` and `lifecycle_json.stop_structural`.

## 5. Logging additions

- `signals` table gets columns: model, level_type, level_price, day_type, raw_conviction, conviction, size_tier, reasons_json, vetoes_json, multipliers_json, thesis.
- `trades` table gets columns: model, expected_hold_min, exit_reason (stop | t1 | t2 | trail | thesis_failed | dead_trade | time_stop | manual), r_multiple, in_trade_checks_json.
- New table `mind_weights` (model, reason_key, weight, updated_at, reason_text) and `mind_calibration` (model, week, bucket, trades, win_rate, mean_r).
- Spec v1.1 (migration v16): `strat_calibration`, `strat_liq_map_hist`, `strat_replay_candles`, `strat_replay_oi`; `strat_liquidations.source` gains the values oxarchive / ws; `reasons_json` entries may carry `excluded_weight` (replay) or `weight: 0` notes (alignment branch); `vetoes_json` entries may carry `unevaluated: true` (replay). Spec v1.2: `strat_calibration_hist` (point-in-time calibration, D-76). Spec v1.3 (migration v17, D-91): `strat_signals.reclaim_candles`, `strat_signals.confirmation_used`; `strat_trades.stop_floor_applied`, `strat_trades.entry_requoted`, `strat_trades.entry_px_orig`; `lifecycle_json.post_only` / `post_only_reprice` / `stop_structural`.

## 6. UI additions (Strategies page)
For M1 to M6 the conditions panel shows one row per reason: name, strength bar, weight, contribution; a vetoes row; a multipliers row; the conviction with tier; and the thesis text. The Breakdown tab shows the calibration table, the weight history, (spec v1.1) the liquidation-normaliser table with the calibrated value or the fallback in force, and (spec v1.3 Part 5) the outcome splits by stop floor applied / structural stop kept (every model) and by reclaim type (M1, M5: same-candle with confirmation vs later-candle), plus the fraction of entries rejected and re-quoted and the mean difference between the original limit and the actual fill.

## 7. Shared parameters (config/models.yaml)

```yaml
models:
  common:
    assets: [BTC, ETH]
    mode: paper
    conviction_skip_below: 0.55
    conviction_full_from: 0.70
    exit_threshold: 2
    dead_trade_hold_multiple: 1.5
    partial_at_t1_pct: 40
    learning_enabled: false
    learning_start_after_days: 60
```

## 8. Claude Code prompt for the shared layer (paste verbatim)

```
In the existing strategy engine where strategies 01 to 06 run in paper mode, add two shared modules and their tests, without modifying the existing strategies.

structure/ package: swings.py (swing highs and lows with k equal to 2 on 15m, 1h and 4h and 3 on daily, confirmed on the k-th following close — spec v1.1), trend.py (up, down, range from the last 2 swing highs and lows — spec v1.1; BOS as a close beyond the most recent swing in trend direction; CHoCH as the first close beyond the most recent higher low or lower high; events stored with ts, tf, type, level), displacement.py (range at least 1.5 ATR(14) and body at least 60 percent of range on the BOS candle or a 2-candle sequence, with displacement_grade; for M2's break only, a displacement leg of up to 3 consecutive 1h candles in the break direction graded on the leg's combined range and net body — spec v1.2, D-75), zones.py (order blocks as the body of the last opposite candle before a displacement with status fresh, tested, broken; fair value gaps as candle1 high below candle3 low or mirror with status open, half_filled, filled), pools.py (equal highs and lows within 0.1 ATR on 1h and 4h; prior day high and low; prior week high and low; daily open 00:00 UTC; weekly open Monday 00:00 UTC; Asia 00:00 to 07:00, London 07:00 to 13:00, New York 13:00 to 21:00 session highs and lows; liquidation clusters from the existing liquidation map where a 0.25 percent band holds at least band_p80 (calibrated, spec v1.1 Part C; fallback 0.1 percent of coin OI); functions pools_above and pools_below sorted by distance with type, level, strength), ranges.py (current 4h and 1h range from the last confirmed swing high and low containing price, with mid and premium or discount flag), sweeps.py (wick beyond a level by 0.1 to 0.5 ATR followed within 3 candles by a close back inside; reclaim candle and reclaim_quality equal to body over range; sweep count per level per UTC day; confirmed_reclaim per spec v1.3 D-89), vwap.py (session VWAP from 00:00 UTC and from each session open with 1 and 2 standard deviation bands; volume profile with 0.05 percent bins giving point of control and 70 percent value area for today and the prior day), alignment.py (the alignment table exactly as specified in the design doc section 2.11, recomputed per coin on every closed 15m candle), day_type.py (labels event, squeeze, trend_up, trend_down, no_trade, range per the rules in section 3, evaluated at 07:00 and 14:00 UTC and as a running label on each 15m close).

mind/ package: base.py with dataclasses Reason (key, description, detector returning 0 to 1, weight, min_weight 0.3, max_weight 3.0), Veto (key, description, boolean detector), ContextRule (key, multiplier function), InTradeCheck (key, description, detector taking snapshot and position returning True when the thesis is failing), Decision (take, conviction, raw_conviction, size_tier none half or full, reasons list of key strength weight, vetoes_hit, multipliers list, thesis text), and class Mind with evaluate(snapshot) computing raw equal to sum of strength times weight over sum of weights, conviction equal to raw times the product of multipliers, take False if any veto or conviction below 0.55, size_tier half from 0.55 to below 0.70 and full from 0.70; manage(snapshot, position) returning hold, partial or exit where exit fires when failing checks are at least exit_threshold for 2 consecutive closed 15m candles or when time in trade exceeds 1.5 times expected_hold with progress below 0.5R; thesis(snapshot, decision) producing the templated paragraph described in section 4.4. snapshot.py builds the Snapshot object described in section 4.7 from the existing data layer. learn.py implements the weekly learning rule and calibration report from section 4.6 exactly, writing to new tables mind_weights and mind_calibration, disabled until 60 days after each model's first trade.

Extend the signals table with model, level_type, level_price, day_type, raw_conviction, conviction, size_tier, reasons_json, vetoes_json, multipliers_json, thesis, and the trades table with model, expected_hold_min, exit_reason, r_multiple, in_trade_checks_json. Map size_tier full to 1.5 percent risk and half to 0.75 percent in the RiskEngine. Add config/models.yaml with the common block from section 7. Write unit tests for swings, BOS and CHoCH, displacement, order blocks, FVGs, sweeps, premium and discount, and Mind.evaluate using synthetic candles with known answers. Run the tests and fix failures until green.
```
