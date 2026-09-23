# 03. Whale / Top-Wallet Following

## 1. Overview

**What:** Track a curated basket of the most consistently profitable Hyperliquid wallets. When several of them open the same direction on the same coin within a short window, tilt the system's bias that way and, on a pullback, join with a post-only entry.

**Why it works:** Hyperliquid is fully transparent. Every wallet's positions, entries, leverage and history are public. Skilled traders exist and their net direction carries information. On a CEX you infer whale flow; here you read it.

**Why it is a bias input first and a trade second:** The transparency cuts both ways. By the time a top wallet's position shows in a polling loop, other copy traders have already piled in. On BTC/ETH the slippage from the copy crowd is small, but the entry is still worse than the original. So the cohort direction is used as a bias (it changes the score of every other strategy) and only becomes a standalone trade when a pullback offers a clean maker entry.

**When:** 24/7 tracking. Standalone trades: opportunistic, 0 to 1 per day.

**Direction:** Same as the cohort.

**Hold time:** Up to 6 hours, or until the cohort net direction flips. Time stop 6 hours.

## 2. Market mechanics

- Positions and fills are queryable per wallet via `clearinghouseState` and `userFills`. The official leaderboard ranks by PnL/ROI over 1d, 7d, 30d, all-time and is an open JSON endpoint.
- Whales know they are watched. Some split across wallets, some post decoys, some are hedging a book you cannot see. Cohort agreement across many independent wallets is far more reliable than one wallet.
- Copy flow into the same trade can itself move price on thin coins by 0.5 to 1%. On BTC/ETH this is minimal.

## 3. Research data and evidence

- Documented herd example (CoinMarketMan, 2026): one $4.2M-profit wallet opened an ETH short; within 6 hours 14 other wallets opened the same trade.
- Selection criteria from practitioner guides (HyperliquidGuide, Dexly, Dropstab 2026): rank by all-time PnL not account size; require top ranking on both 7-day and all-time boards; PnL/volume above 1% is excellent, below 0.1% means fees exceed edge; look for smooth equity curves; consistent top-100 wallets rarely exceed 10x effective leverage; use a rotating basket, never a single wallet.
- Documented failure modes: signal decays before you act; position sizing mismatch (lead wallet at 2x on $3M is risking 0.5%, you at 2x on $10K are risking everything); hidden hedges.
- No academic study on HL copy-trading returns yet. Treat the edge as real but decaying and requiring strict wallet hygiene.

## 4. Wallet selection (the actual edge)

Rebuilt weekly, `data/wallets.py::build_cohort()`:
1. Pull top 500 by all-time PnL and top 500 by 30-day PnL from the leaderboard.
2. Keep wallets in both lists.
3. For each, compute from `userFills` history: 90-day PnL, 90-day volume, PnL/volume ratio, max drawdown of equity curve, average effective leverage, number of closed trades, median hold time.
4. Filter: PnL/volume >= 0.5%, closed trades >= 40 in 90 days, max drawdown <= 35%, avg leverage <= 10x, median hold >= 30 minutes (excludes HFT/MM wallets whose direction is meaningless).
5. Score = 0.4 x rank(PnL/volume) + 0.3 x rank(90d PnL) + 0.3 x rank(1 / drawdown).
6. Cohort = top 30. Store with a `since` date. A wallet that drops out stays out for 30 days.

## 5. Identification rules (exact)

**Cohort signal (evaluated every 60 seconds):**
- `net_dir` per coin = (sum of cohort long notional - short notional) / (sum of absolute cohort notional). Range -1 to +1.
- `fresh_agree` = number of cohort wallets that opened or added >= 25% to a position in the same direction in the last 60 minutes.
- Bias output to `features/bias.py`: `net_dir` x min(fresh_agree / 3, 1), weight 0.35.

**Standalone trade trigger:**
1. `fresh_agree` >= 3 in the same direction, within 60 minutes.
2. `net_dir` >= +0.3 (for longs) or <= -0.3 (for shorts).
3. Price has pulled back toward the cohort's average fresh entry: current price within 0.5 x ATR(14, 15m) of the volume-weighted average entry of those fresh positions, and not more than 1 ATR beyond it in the adverse direction (they are not yet underwater badly).
4. Regime gate = TRADE_ALLOWED.
5. Timing: 15m Fisher(9) turning in the cohort direction, or 15m Hull(21) slope in the cohort direction.

Score: cohort agreement 0.4, pullback quality 0.2, regime 0.2, timing 0.2. Fire at >= 0.65.

## 6. Candle-level definition (15m)

- Trigger candle: the 15m candle during which `fresh_agree` reaches 3.
- Entry candle: a subsequent 15m candle whose low (for longs) touches the zone [cohort VWAP entry - 0.5 ATR, cohort VWAP entry + 0.2 ATR], with Fisher/Hull timing satisfied on close.
- Invalidation: a 15m close beyond cohort VWAP entry - 1 ATR (for longs).

## 7. Execution

- Entry: post-only limit at cohort VWAP entry - 0.2 x ATR (for longs). Cancel if unfilled within 2 candles (30 minutes).
- Stop: 1.0 x ATR below cohort VWAP entry. Rationale: the cohort's own stop is effectively your protection; if price goes 1 ATR against skilled entries, the read was wrong.
- Target: 1.5 x ATR from entry (take 50%), then trail with 1h Hull(21) slope flip or exit when `net_dir` crosses zero.
- Time stop: 6 hours.
- Size: 1.5% risk / stop distance, capped 3x.

## 8. Data collection

- Leaderboard endpoint weekly for cohort rebuild.
- `userFills` history per candidate wallet (paginated) for the 90-day metrics.
- `userFills` websocket for the 30-wallet cohort (real-time opens/adds).
- `clearinghouseState` for the cohort every 30 seconds (positions, entries, leverage).
- Store: `cohort` table (address, score, since, metrics JSON), `cohort_positions` snapshots, `cohort_events` (ts, address, coin, dir, notional_change).

## 9. Signals and notifications

- Cohort event: `[S03 WHALE] 3 cohort wallets opened ETH LONG in 41m, net_dir +0.44, VWAP entry 2,238 -> bias tilt +0.44`
- Trade fire: `[S03 WHALE] ETH LONG pullback | entry 2,229 stop 2,201 t1 2,268 | cohort 4 wallets | score 0.71 | size 0.67 ETH 3.0x`
- Cohort rebuild summary weekly: added/dropped wallets with reasons.

## 10. Performance expectations and KPIs

- Standalone trades: 8 to 20/month. Target win rate 50 to 55%, avg win 1.6 x avg loss, PF 1.3+.
- Bias value: measure other strategies' expectancy when aligned with vs against cohort tilt. This is the main KPI.
- Track cohort quality: 30-day forward PnL of the cohort as a group after each rebuild. If the cohort's own forward PnL goes negative for two consecutive rebuilds, the selection rules are wrong.

## 11. Failure modes

| Failure | Rule |
|---|---|
| Copying one wallet that is hedging or decoying | Minimum 3 independent wallets, net_dir threshold |
| Chasing the copy crowd's entry | Pullback-only entry, post-only |
| Mirroring whale leverage | Size from our own risk engine only |
| Following MM/HFT wallets with no directional view | Median hold >= 30 min, PnL/volume filter |
| Survivorship: hot-streak wallets | Both 7d/30d and all-time ranking, 30-day exclusion after dropout |
| Cohort was right, trade fired late | Time stop 6h, exit on net_dir flip |

## 12. Backtest plan

1. Reconstruct cohort membership monthly for the past 6 months using only data available at that time (no lookahead).
2. Replay cohort events and measure forward price move at 1h, 4h, 12h after `fresh_agree` >= 3, split by net_dir strength.
3. Simulate pullback entries with our rules. Report PF, and separately the forward-return of the bias signal alone.
4. Go/no-go for standalone: PF > 1.3 net. For bias: forward 4h return conditional on tilt sign must be positive with t-stat > 2.

## 13. Review checklist

- Which wallets generated the winning events? Concentration in 1 to 2 wallets means the cohort is too narrow.
- Average lag between first cohort open and our fill.
- Any wallet showing sudden leverage jump (behaviour change) gets flagged for removal.

## 14. Claude Code implementation prompts (paste verbatim, in order)

**Prompt 1, cohort builder:**
```
In the perpbot project, implement data/wallets.py::build_cohort() and a weekly scheduler entry.

Pull the top 500 wallets by all-time PnL and the top 500 by 30-day PnL from the Hyperliquid leaderboard endpoint. Keep addresses present in both lists. For each, fetch 90 days of userFills with pagination and compute: pnl_90d, volume_90d, pnl_to_volume, max_drawdown of the reconstructed equity curve, avg_effective_leverage, closed_trade_count, median_hold_minutes. Filter to pnl_to_volume at least 0.005, closed_trade_count at least 40, max_drawdown at most 0.35, avg_effective_leverage at most 10, median_hold_minutes at least 30. Score equals 0.4 times percentile rank of pnl_to_volume plus 0.3 times percentile rank of pnl_90d plus 0.3 times percentile rank of the inverse of max_drawdown. Keep the top 30 as the cohort. Persist to a cohort table with columns address, score, since_ts, metrics_json, active. A wallet that leaves the cohort is marked inactive with a cooldown_until 30 days ahead and cannot re-enter before then. Send a Telegram summary of additions and removals with the failing metric for each removal.
```

**Prompt 2, cohort signal and bias wiring:**
```
Implement features/cohort_signal.py. Subscribe userFills and poll clearinghouseState every 30 seconds for all active cohort wallets. Maintain per coin: net_dir equal to (sum long notional minus sum short notional) divided by sum of absolute notional across the cohort, and fresh_agree equal to the count of cohort wallets that opened a new position or increased an existing one by at least 25 percent in the same direction within the last 60 minutes, together with the notional-weighted average entry price of those fresh positions as cohort_vwap. Write every open/add/close event to a cohort_events table with ts, address, coin, direction, notional_change, price. Expose tilt(coin) equal to net_dir multiplied by min(fresh_agree divided by 3, 1) and wire it into features/bias.py with weight 0.35. Send a Telegram alert when fresh_agree reaches 3 in one direction.
```

**Prompt 3, the strategy:**
```
Implement strategies/s03_whale_follow.py as class WhaleFollow(Strategy), evaluated on each closed 15m candle and every 60 seconds for the trigger.

Trigger: fresh_agree at least 3 in one direction within 60 minutes AND net_dir at least 0.3 in that direction. Entry condition: current price within 0.5 ATR(14, 15m) of cohort_vwap and not more than 1 ATR beyond it against the cohort direction, regime gate allows trading, and timing satisfied by 15m fisher_transform(n=9) turning in the cohort direction or 15m hull_ma(21) slope in the cohort direction.

Score: cohort agreement 0.4, pullback quality 0.2 (1.0 when price is within 0.2 ATR of cohort_vwap, decaying linearly to 0 at 0.5 ATR), regime 0.2, timing 0.2. Fire at total at least 0.65.

Execution: post-only limit at cohort_vwap minus 0.2 ATR for longs (plus for shorts), cancel if unfilled after 30 minutes. Stop at cohort_vwap minus 1.0 ATR for longs as a trigger order. Target 1 at entry plus 1.5 ATR closing 50 percent, remainder trails on 1h hull_ma(21) slope flip or exits when net_dir crosses zero. Time stop 6 hours. Size via RiskEngine.size_for.

Log evaluations to the signals table and send fire alerts with entry, stop, target, cohort wallet count and size.
```

**Prompt 4, backtest:**
```
Implement backtest/s03_backtest.py. Rebuild cohort membership at the start of each month for the past 6 months using only fills available before that date. Replay cohort_events and for each moment fresh_agree first reaches 3, record forward price change at 1h, 4h and 12h split by net_dir buckets (0.3 to 0.5, 0.5 to 0.7, above 0.7), and compute mean, hit rate and t-statistic. Then simulate WhaleFollow with post-only fills only when traded through, maker fee 0.00015 and taker 0.00045 on stops, reporting trades, win rate, expectancy, profit factor and max drawdown. Write to backtest/results/s03_<daterange>.md.
```
