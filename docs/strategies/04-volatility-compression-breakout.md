# 04. Volatility Compression Breakout

## 1. Overview

**What:** After several hours of shrinking 15-minute ranges, price expands. Trade the expansion in the direction confirmed by rising open interest and higher-timeframe slope.

**Why it works:** Volatility clusters. Quiet periods are followed by loud ones. That part is a structural property of markets. What separates a real expansion from a fake-out is participation: rising OI into and through the breakout means new money is committing (voluntary repositioning), whereas a breakout on falling OI is a liquidity grab that mean-reverts.

**What this is not:** It is not Strategy 01. If the expansion is caused by liquidations (OI collapsing), it belongs to 01. This strategy only trades expansions where OI is rising.

**When:** Fills the gaps on days with no cascades and no strong session move. Typical frequency 0 to 2 per day.

**Direction:** Direction of the break, only if 1h Hull slope agrees.

**Hold time:** 1 to 4 hours. Time stop 4 hours.

## 2. Market mechanics

- Breakouts have a low raw win rate. Practitioner range 30 to 45%. Profit comes from the minority of breaks that run. The entire edge is in the filters.
- OI rotation vs OI collapse (arXiv 2601.06084, 2026): rotation-driven moves show far greater follow-through than collapse-initiated volatility.
- Volume/OI-confirmed breaks after compression outperform raw level breaks by a wide margin (multiple 2025 to 2026 practitioner tests). Simple opening-range breakouts without extra filters have lost their edge on equities, a warning that unfiltered breakouts decay.

## 3. Research data and evidence

- FMZQuant TTM Squeeze implementation (2025): primary risks are false breakouts and consecutive small losses in ranging markets; recommended fixes are volume confirmation and trend filters.
- DataDrivenInvestor quant playbook (July 2026): screen for Bollinger Band Width rank under 20% and ATR rank under 25%, require rising OI in the final stage of consolidation, require 15m/1h/4h alignment, and check funding extremes (deeply negative funding in a bullish coil favours a short squeeze).
- Strefa Tradingu (July 2026): breakout win rates frequently 30 to 45%; filters (volume, compression, candle close, retest) are the difference between a strategy and a coin flip with costs.
- arXiv 2601.06084: distinguishing OI rotation from OI collapse is decisive for breakout sustainability; migration of order-book depth beyond the prior range extremes signals market makers relocating inventory in anticipation of expansion.

## 4. Identification rules (exact)

All on 15m candles for BTC and ETH.

**Compression (must hold at the candle before the break):**
- `bbw_rank` = percentile rank of Bollinger Band Width(20, 2) over the trailing 200 candles. Require <= 20%.
- `atr_rank` = percentile rank of ATR(14) over trailing 200 candles. Require <= 25%.
- Compression duration: at least 8 consecutive candles (2 hours) with `atr_rank` <= 35%.
- Range box: high and low of the compression period.

**Break:**
- A 15m candle closes above box high (long) or below box low (short) by at least 0.15 x ATR(14).
- Candle range >= 1.5 x mean ATR of the compression period.

**Confirmation (all required):**
- OI on HL at candle close >= OI at compression start + 1.5%, AND OI did not fall more than 0.5% during the break candle (rotation, not collapse).
- 1h Hull(21) slope agrees with break direction.
- 4h Hull(21) slope not strongly against (slope magnitude against direction less than 0.5 x 4h ATR per bar).
- Book depth check: depth within 0.5% beyond the box edge in the break direction is at least 80% of the depth that was inside the box (MMs are not walling the break).
- Regime gate = TRADE_ALLOWED. Realized vol must be rising (last 15m realized vol > 30m ago).

**Bonus (score only):** funding on the crowded side against the break (short squeeze potential on a long break).

Score: compression quality 0.2, break quality 0.2, OI confirmation 0.3, higher-timeframe alignment 0.2, bias alignment 0.1. Fire at >= 0.7 (higher bar than other strategies because of the low base win rate).

## 5. Candle-level definition (15m)

- Compression candles: the box.
- Break candle: first 15m close outside the box meeting the rules.
- Entry candle: either (a) the break candle close (aggressive), or (b) the first retest candle whose low (for longs) comes back to within 0.2 ATR of the box edge without closing back inside (conservative). Default is (b) with a 3-candle wait; if no retest within 3 candles and price is already 1 ATR away, skip.
- Invalidation: a 15m close back inside the box.

## 6. Execution

- Entry: post-only limit at box edge + 0.1 x ATR (long), placed after the break candle closes, valid for 3 candles (45 minutes).
- Stop: box midpoint, or 1.0 x ATR below entry, whichever is closer.
- Target 1: 1.5 x box height above box high (take 50%).
- Target 2: trail remainder with 15m Hull(21) slope flip, or exit if OI falls 2% from its post-break peak (participation leaving).
- Time stop: 4 hours.
- Size: 1.5% risk / stop distance, capped 3x.

## 7. Data collection

- HL 15m, 1h, 4h candles.
- OI: `activeAssetCtx` websocket or `metaAndAssetCtxs` polling every 30 seconds, stored as a 1-minute series.
- `l2Book` snapshots every 5 seconds, aggregate depth within 0.5% bands.
- Realized vol from 1m closes.
- Funding from Strategy 02's gauge.

## 8. Signals and notifications

- Compression alert: `[S04 COIL] ETH compressing 11 candles, bbw_rank 12%, atr_rank 18%, box 2,214 to 2,231, OI +0.9%`
- Break alert: `[S04 BREAK] ETH long break 2,236 close, OI +2.1% since coil, 1h Hull up, score 0.76, waiting retest`
- Fire alert with entry/stop/targets/size, and a skip alert naming the failed confirmation.

## 9. Performance expectations and KPIs

- Frequency: 10 to 30 setups/month.
- Target win rate 40 to 48%, avg win 2.2 x avg loss, PF 1.4+. Accept long losing streaks; the risk engine's 2-loss pause will interact with this strategy often, which is intended.
- KPI: fraction of breaks that reach target 1 vs fraction that close back inside the box within 3 candles. If back-inside rate exceeds 55%, tighten the OI threshold before touching anything else.

## 10. Failure modes

| Failure | Rule |
|---|---|
| Chop: repeated small losses | Compression duration 8+ candles, score bar 0.7, regime gate, 2-loss pause |
| Liquidity-grab fake-out | OI rotation requirement, depth-beyond-box check |
| Expansion driven by liquidations (belongs to 01) | OI must not fall during break candle |
| Breaking against the higher timeframe | 1h Hull agreement, 4h not strongly against |
| Entering late after a 1 ATR run | Retest-only default, skip after 3 candles |
| News-driven break that reverses | Regime gate blocks macro windows |

## 11. Backtest plan

1. 12 months of 15m/1h/4h candles and 1-minute OI for BTC/ETH.
2. Identify every compression per the rules; for each break, record forward move at 1h, 2h, 4h and whether price closed back inside within 3 candles.
3. Split by OI condition (rising >= 1.5% vs not) and by 1h Hull agreement. The OI split is the key result: if rising-OI breaks do not outperform, the strategy is not worth running.
4. Simulate with retest entry, our stop/targets, post-only fills only when traded through, fees. Go/no-go: PF > 1.3 net, and rising-OI subgroup materially better than the rest.

## 12. Review checklist

- Back-inside rate.
- Average OI change during winning vs losing breaks.
- Time of day of winners (expect US/EU sessions; if winners are all in dead hours, something is wrong with the data).

## 13. Claude Code implementation prompts (paste verbatim, in order)

**Prompt 1, features:**
```
In the perpbot project, add to features/indicators.py: bollinger_band_width(close, n=20, k=2) returning (upper minus lower) divided by middle, percentile_rank(series, window=200), and a compression detector detect_compression(df_15m) that returns the current box (start_ts, box_high, box_low, n_candles, mean_atr) when bbw percentile rank over 200 candles is at most 20, ATR(14) percentile rank is at most 25, and at least 8 consecutive candles have ATR rank at most 35, otherwise None. Add to data/hl_info.py a 1-minute OI series stored in the store as oi_1m with columns ts, coin, oi_notional, sourced from activeAssetCtx websocket messages. Add to data/hl_ws.py an l2Book aggregator that stores every 5 seconds the bid and ask notional depth within 0.1, 0.3 and 0.5 percent of mid.
```

**Prompt 2, the strategy:**
```
Implement strategies/s04_vol_compression.py as class VolCompressionBreakout(Strategy), evaluated on each closed 15m candle for BTC and ETH.

State machine with states IDLE, COMPRESSING, BROKEN, ARMED. Enter COMPRESSING when detect_compression returns a box. Enter BROKEN when a 15m candle closes beyond the box by at least 0.15 ATR(14) with candle range at least 1.5 times the box mean ATR. Confirm and enter ARMED only if: OI at break close is at least 1.5 percent above OI at box start AND OI did not fall more than 0.5 percent during the break candle; 1h hull_ma(21) slope agrees with the break direction; 4h hull_ma(21) slope against the direction has magnitude below 0.5 times 4h ATR per bar; l2Book depth within 0.5 percent beyond the box edge in the break direction is at least 80 percent of the average depth that was inside the box during compression; regime gate allows trading; 15m realized vol is higher than 30 minutes earlier.

Score: compression quality 0.2 (lower bbw rank scores higher), break quality 0.2 (range relative to mean ATR), OI confirmation 0.3, higher-timeframe alignment 0.2, bias alignment 0.1. Fire at total at least 0.7.

Execution: place a post-only limit at box edge plus 0.1 ATR for longs (minus for shorts) valid for 3 candles; cancel if unfilled or if price is already more than 1 ATR beyond the box when the third candle closes. Stop at the box midpoint or entry minus 1.0 ATR, whichever is closer, as a trigger order. Target 1 at box high plus 1.5 times box height for longs, close 50 percent. Remainder exits on 15m hull_ma(21) slope flip or when OI falls 2 percent from its post-break peak. Time stop 4 hours. Any 15m close back inside the box while ARMED cancels the entry or exits the position. Size via RiskEngine.size_for.

Send Telegram alerts on COMPRESSING (box details), BROKEN (score and pending confirmations), fire, and skip with the failed condition. Log all evaluations to the signals table.
```

**Prompt 3, backtest:**
```
Implement backtest/s04_backtest.py over 12 months of stored 15m, 1h, 4h candles and oi_1m for BTC and ETH. For every compression and break per the strategy rules, record forward returns at 1h, 2h and 4h in the break direction and whether a 15m close returned inside the box within 3 candles. Report these split by OI condition (rising at least 1.5 percent vs not) and by 1h Hull agreement. Then simulate VolCompressionBreakout with retest entries, post-only fills only when traded through, maker fee 0.00015, taker 0.00045 on stops, and report trades, win rate, expectancy, profit factor, max drawdown, back-inside rate and longest losing streak. Write to backtest/results/s04_<daterange>.md.
```
