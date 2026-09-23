# 06. Hull Suite + Fisher Transform + 200 EMA Trend Pullback (On-Chain Gated)

## 1. Overview

**What:** A trend-pullback strategy. 200 EMA sets direction, Hull slope confirms the trend is currently alive, Fisher Transform times the entry at the end of a pullback. The twist that makes it usable: it is only allowed to trade when an on-chain regime gate says the market is actually trending with participation. Without the gate this is a well-known retail strategy that bleeds in chop.

**Two roles:**
1. **Timing layer for strategies 01 to 05** (from day one): Hull slope on 1h as a direction filter, Fisher on 15m as the entry timer. This is where most of its value is.
2. **Standalone trend engine** (only after proving itself in paper): captures the middle of trending days when nothing dramatic is happening but price steadily moves.

**When:** Trending days with rising OI, non-extreme funding, elevated volatility, no macro inside 2 hours. On qualifying days, 1 to 3 pullback entries per coin.

**Direction:** With the 200 EMA and Hull slope.

**Hold time:** 1 to 6 hours. Time stop 6 hours.

## 2. Indicator definitions (exact)

- **200 EMA** on 15m: standard EMA, period 200 (50 hours of data).
- **Hull MA(n):** `HMA = WMA(2 x WMA(price, n/2) - WMA(price, n), sqrt(n))`. Use n = 55 on 15m for the standalone strategy, n = 21 on 1h for the timing layer. Slope = HMA[t] - HMA[t-1]. "Up" when slope > 0 for the last 2 bars. Hull Suite variants (EHMA, THMA) are optional; plain HMA is fine.
- **Fisher Transform(n=9)** on 15m, price = (high + low) / 2:
  - `x = 2 x ((price - min_n) / (max_n - min_n) - 0.5)`, clamped to [-0.999, 0.999]
  - `x = 0.33 x x + 0.67 x x_prev`
  - `fisher = 0.5 x ln((1 + x) / (1 - x))`, then `fisher = 0.5 x fisher + 0.5 x fisher_prev`
  - `signal = fisher[t-1]`
  - Extreme = |fisher| >= 1.5. Turn = fisher crosses signal after being at an extreme.

## 3. Research data and evidence

- Long backtests of Hull MA crossovers on equities (LiberatedStockTrader): HMA causes many small losses in consolidation, and a 200-period HMA had only a 7% chance of beating buy-and-hold. QuantifiedStrategies: HMA is fast and smooth but still lagging and best used with confirmation.
- Crypto tests (Opportrade, ETH/EUR 1h, 2025): performs well in decisive uptrends, loses effectiveness in sideways and bearish phases.
- HMA practitioner sites concede it performs best combined with other confirmations; commonly used lengths 21 and 55; works on 5 to 15 minute charts on fast assets.
- No published backtest exists for Hull + Fisher + 200 EMA with OI/funding/volatility gating. The gate's benefit is a hypothesis grounded in: (a) every source locating the losses in chop; (b) the OI-rotation literature (arXiv 2601.06084) showing participation-driven moves have better follow-through; (c) the Reading paper showing momentum only exists on high-vol days.
- Conclusion: weakest standalone evidence of the six. Timing-layer use is low risk; standalone use must earn its place with 60 paper trades.

## 4. Regime gate for this strategy (stricter than the shared gate)

All must hold at entry:
1. Shared regime gate = TRADE_ALLOWED.
2. OI rising with price: OI change over last 4h has the same sign as the price change over last 4h, and |OI change| >= 1%.
3. Funding gauge crowding_level != `EXTREME` on the trade side.
4. Realized vol (last 1h) > its 24h average.
5. No macro event within the next 2 hours or the last 30 minutes.
6. Trend strength: price has been on one side of the 200 EMA for at least 12 consecutive 15m candles (3 hours), and distance from the 200 EMA is between 0.5 and 3.0 x ATR (not overextended, not flat).

## 5. Identification rules (exact, 15m)

**Direction:** long if close > 200 EMA AND Hull(55) slope up; short if close < 200 EMA AND Hull(55) slope down.

**Pullback:** price retraces toward Hull(55) or the 200 EMA: the low (for longs) of the pullback comes within 0.5 x ATR of Hull(55), OR touches the zone between Hull(55) and the 200 EMA, without a 15m close below the 200 EMA. Pullback must be at least 2 candles and at most 8 candles.

**Trigger:** Fisher(9) reached <= -1.5 during the pullback (for longs) and now crosses above its signal line on a closed candle.

**Confirmation:**
- Hull(55) slope still up on the trigger candle (the pullback did not kill the trend).
- 1h Hull(21) slope up.
- Bias layer not against (bias x direction >= -0.2).
- Regime gate for this strategy (Section 4) passes.

Score: trend quality 0.25 (consecutive candles beyond EMA, Hull slope magnitude), pullback quality 0.2 (depth relative to Hull, duration), Fisher trigger 0.2, on-chain gate 0.25, bias 0.1. Fire at >= 0.7.

## 6. Candle-level definition (15m)

- Trend candles: 12+ consecutive closes above 200 EMA with Hull(55) up.
- Pullback candles: 2 to 8 candles moving toward Hull(55).
- Trigger candle: Fisher crosses above signal after an extreme.
- Entry candle: the trigger candle close (post-only just below close) or the next candle.
- Invalidation: 15m close below the 200 EMA, or Hull(55) slope turns down for 2 bars.

## 7. Execution

- Entry: post-only limit at trigger candle close - 0.1 x ATR (long). Valid for 2 candles.
- Stop: below the pullback low by 0.2 x ATR, capped at 1.2 x ATR from entry (skip if the stop would need to be wider).
- Target 1: 1.5 x stop distance (take 50%).
- Target 2: trail on Hull(55) slope flip or 15m close below Hull(55).
- Time stop: 6 hours.
- Size: 1.5% risk / stop distance, capped 3x. Standalone mode starts at 0.5 x size for the first 30 live trades.

## 8. Timing-layer contract (used by strategies 01 to 05)

`features/timing.py` exposes:
- `hull_dir(coin, tf='1h', n=21)` returns +1, -1, 0.
- `fisher_turn(coin, tf='15m', n=9)` returns +1 (turned up from extreme), -1 (turned down from extreme), 0.
- `timing_score(coin, direction)`: 1.0 if hull_dir agrees and fisher_turn agrees; 0.6 if one agrees and the other is 0; 0.2 if one disagrees; 0 if both disagree.

Strategies 01 to 05 use `timing_score` as their timing component. Nothing else from this strategy is required for them.

## 9. Data collection

- HL 15m and 1h candles (need at least 300 x 15m candles of history for the 200 EMA and percentile ranks).
- OI 1-minute series (from 04's collector).
- Funding gauge (02), bias (03), realized vol, events calendar (05).

## 10. Signals and notifications

- Trend state change: `[S06 TREND] BTC uptrend established: 14 closes above EMA200, Hull55 up, OI +1.8%/4h, gate OPEN`
- Gate closed: `[S06 TREND] BTC gate CLOSED: funding EXTREME long side`
- Fire: `[S06 PULLBACK] BTC LONG | Fisher turn from -1.9 | entry 61,320 stop 60,980 t1 61,830 | score 0.73 | size 0.13 BTC 3.0x (paper)`

## 11. Performance expectations and KPIs

- Frequency: 10 to 25/month on qualifying days.
- Target win rate 48 to 55%, avg win 1.7 x avg loss, PF 1.3+ (standalone). The ungated version historically runs PF around 1.0 or below on 15m crypto; the KPI is the gap between gated and ungated.
- Run both gated and ungated in paper in parallel from day one. If gated does not beat ungated by at least 0.3 PF over 60 trades, the gate is not doing its job and the standalone strategy is retired.

## 12. Failure modes

| Failure | Rule |
|---|---|
| Chop: Hull flips every hour, Fisher fires both ways | Gate (OI, vol, 12-candle trend), score bar 0.7, invalidation on Hull flip |
| Overextended entry after a long run | Distance from EMA200 capped at 3 ATR |
| Pullback is actually a reversal | Stop below pullback low, close-below-EMA invalidation, Hull must still be up on trigger |
| Fisher repainting concerns | Only closed-candle values used, signal line is previous bar |
| Late entry on wide stop | Skip if stop > 1.2 ATR |
| Overfitting Hull/Fisher lengths | Lengths fixed (55/21/9); changes only via written review with 60+ trades |

## 13. Backtest plan

1. 18 months of 15m and 1h candles, 1m OI, funding, events for BTC/ETH.
2. Run three variants: (a) ungated chart-only, (b) shared regime gate only, (c) full on-chain gate (Section 4).
3. Report trades, win rate, expectancy, PF, max drawdown, longest losing streak per variant. The result that matters is (c) minus (a).
4. Parameter sensitivity: Hull 34/55/89, Fisher 7/9/13, EMA 150/200/250. Only to confirm stability, not to pick the best.
5. Go/no-go for standalone: variant (c) PF > 1.3 net and > variant (a) by 0.3 or more.

## 14. Review checklist

- Gated vs ungated PF gap (weekly).
- Which gate condition blocked the most losing trades vs the most winning trades.
- Timing-layer audit: for strategies 01 to 05, compare expectancy of trades with timing_score >= 0.6 vs < 0.6.

## 15. Claude Code implementation prompts (paste verbatim, in order)

**Prompt 1, timing layer (needed by all strategies):**
```
In the perpbot project, implement features/timing.py using features/indicators.py. Provide hull_dir(coin, tf, n) returning plus 1 when hull_ma(n) slope has been positive for the last 2 closed candles on that timeframe, minus 1 when negative for 2 candles, else 0. Provide fisher_turn(coin, tf, n) returning plus 1 when fisher_transform(n) reached at most negative 1.5 within the last 6 closed candles and on the latest closed candle crosses above its signal line (previous bar value), minus 1 for the mirror case, else 0. Provide timing_score(coin, direction) returning 1.0 when hull_dir(coin, '1h', 21) equals direction and fisher_turn(coin, '15m', 9) equals direction, 0.6 when one equals direction and the other is 0, 0.2 when one equals direction and the other is opposite, and 0 otherwise. Add unit tests with synthetic candle series that produce known Fisher extremes and Hull slopes. Wire timing_score into the timing component of strategies s01 through s05.
```

**Prompt 2, the standalone strategy with gate:**
```
Implement strategies/s06_hull_fisher_ema.py as class HullFisherEmaPullback(Strategy), evaluated on each closed 15m candle for BTC and ETH, with a config flag gated true or false so two instances can run in parallel in paper mode.

Indicators on 15m: ema(close, 200), hull_ma(close, 55) with slope, fisher_transform(high, low, 9) with signal equal to previous bar, ATR(14). On 1h: hull_ma(close, 21) slope.

Direction: long when close is above EMA200 and Hull55 slope is up for 2 bars; short mirror. Trend established when at least 12 consecutive closes are on the trend side of EMA200 and the distance from EMA200 is between 0.5 and 3.0 ATR.

Pullback: 2 to 8 candles moving toward Hull55 where the pullback low for longs comes within 0.5 ATR of Hull55 or enters the zone between Hull55 and EMA200, without a 15m close beyond EMA200. Trigger: Fisher reached at most negative 1.5 during the pullback and crosses above signal on a closed candle, with Hull55 slope still up and 1h Hull21 slope up, and bias score times direction at least negative 0.2.

Gate (only when gated is true): shared regime gate allows trading; OI change over 4 hours has the same sign as price change over 4 hours with absolute OI change at least 1 percent; funding gauge crowding_level is not EXTREME on the trade side; realized vol over the last 1 hour is above its 24 hour average; no macro event within the next 2 hours or the last 30 minutes.

Score: trend quality 0.25, pullback quality 0.2, Fisher trigger 0.2, on-chain gate 0.25 (0 when gated is false so the ungated instance uses a threshold of 0.5 instead of 0.7), bias 0.1. Fire at 0.7 gated, 0.5 ungated.

Execution: post-only limit at trigger close minus 0.1 ATR for longs valid 2 candles. Stop 0.2 ATR below the pullback low as a trigger order; skip the trade if that stop is more than 1.2 ATR from entry. Target 1 at 1.5 times stop distance closing 50 percent, remainder trails on Hull55 slope flip or a 15m close below Hull55. Time stop 6 hours. Invalidate on a 15m close beyond EMA200 or Hull55 slope against for 2 bars. Size via RiskEngine.size_for multiplied by 0.5 for the first 30 live trades.

Tag every signal and trade in the log with variant gated or ungated. Send alerts for trend established, gate closed with the failing condition, and fire.
```

**Prompt 3, backtest:**
```
Implement backtest/s06_backtest.py over 18 months of stored 15m and 1h candles, oi_1m, funding and config/events.yaml for BTC and ETH. Run three variants: ungated chart-only, shared regime gate only, and full on-chain gate. For each report trades, win rate, expectancy, profit factor, max drawdown and longest losing streak, plus the profit factor difference between full gate and ungated. Add a sensitivity table over Hull lengths 34, 55, 89, Fisher lengths 7, 9, 13 and EMA lengths 150, 200, 250 for the full-gate variant only, reporting profit factor for each combination. Model post-only fills only when traded through, maker fee 0.00015 and taker 0.00045 on stops. Write to backtest/results/s06_<daterange>.md.
```
