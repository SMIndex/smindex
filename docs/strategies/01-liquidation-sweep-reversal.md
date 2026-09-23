# 01. Liquidation Sweep Reversal

## 1. Overview

**What:** Trade the bounce after a burst of forced liquidations pushes price into a sharp wick. Forced sellers (or buyers) are done; price snaps back partway.

**Why it works:** A liquidation is a market order the trader did not choose to send. Cascades overshoot because forced flow ignores price. When the forced flow stops, there is a vacuum and price mean-reverts toward where the cascade started. On Hyperliquid, every liquidation is a visible on-chain fill with an address attached, so we can see the forced flow start and stop rather than guess from the chart.

**When:** Any time of day, but most cascades cluster in the US session and around macro releases. Expect 1 to 2 valid setups per day across BTC and ETH in normal 2026 conditions.

**Direction:** Long after a long-liquidation cascade (price wicked down). Short after a short-liquidation cascade (price wicked up).

**Hold time:** 15 to 90 minutes. Time stop at 90 minutes.

## 2. Market mechanics you must respect (Hyperliquid specific)

- Liquidations trigger on **mark price**, a blend of external CEX prices and HL's own book. Watch the external price approaching a cluster, not HL's last trade.
- Positions over $100K are liquidated in stages: 20% first, then a 30-second cooldown, then the remainder. A big cluster produces a staircase of selling. **Do not enter after the first step. Wait for fills to be quiet for at least 45 seconds.**
- If equity drops below 2/3 of maintenance margin without a clean book close, the position is taken by the HLP backstop vault and never hits the book. Big whale clusters therefore produce less price overshoot than retail clusters of many small positions.
- Liquidations with `method: market` hit the book (tradeable overshoot). `method: backstop` do not.
- Most BTC/ETH cascades start on Binance/Bybit and propagate to HL via the mark price. HL liquidations are the confirmation, CEX aggregate liquidations are the early warning.

## 3. Research data and evidence

- HL docs confirm mark-price liquidation, staged 20%/30s partial liquidation for >$100K positions, and backstop transfer below 2/3 maintenance margin.
- Practitioner cascade anatomy (SmartMoneyAPI strategy notes): bounce begins 30 to 120 seconds after the cascade, price recovers roughly half the lost ground within minutes, then settles 2 to 4% below the pre-cascade level. Their confirmation rule: liquidation velocity > 60 events per 5 minutes AND concentration > 0.5% AND no whale support buying. Their execution rule: limit orders only; market orders during cascades fill 100 bps worse than displayed.
- Market-maker view (XT Exchange, May 2026): the reversal signal is a spike in market selling volume that fails to push price lower, meaning passive bids have absorbed the forced flow.
- 2026 market state: cascades are frequent and two-sided. Feb 1 2026 ("Black Sunday II") $2.2B in 24h; Aug 20 2026 $1.74B short liquidations in 24h, second largest short squeeze on record; leverage rebuilt after every flush. The setup is not arbitraged away.
- Data: public HL websocket exposes liquidations only inside per-wallet `userFills`. Global feed requires GoldRush `liquidationFills` (paid) or a large wallet subscription. Historical liquidation data for backtests: 0xArchive (paid) or reconstruction from wallet fills.

## 4. Identification rules (exact)

All computed on a rolling basis, evaluated every second on the fill stream and every 15m candle close for context.

**Step 1: Cascade detected**
- `liq_notional_5m` = sum of `market`-method liquidation notional on this coin in the last 5 minutes, one side only (longs liquidated = sells).
- Trigger when `liq_notional_5m` >= max($3M for BTC, $1.5M for ETH, 0.15% of coin OI) AND at least 25 distinct liquidated wallets in the window (retail-dominated cluster).
- Price move during window >= 1.2 x ATR(14, 15m).

**Step 2: Exhaustion confirmed**
- No new `market` liquidation fills on that side for 45 seconds (covers the 30s partial-liquidation cooldown).
- Last 60 seconds of trades: taker sell volume (for a long cascade) drops below 40% of its peak 1-minute value during the cascade.
- Order book: bid depth within 0.3% of mid has refilled to at least 60% of its pre-cascade 15-minute average.
- Liquidation map: no further cluster of >= 50% of the swept cluster's size within 1 x ATR beyond the wick low.

**Step 3: Context**
- Regime gate = TRADE_ALLOWED.
- HL hourly funding before the cascade was leaning the liquidated side (positive for a long cascade). Not mandatory, adds to score.
- CEX aggregate liquidations (Coinglass) confirm a broad cascade, not an HL-only event. Adds to score.

**Score:** trigger (Step 1 and 2) 0.5 weight, regime 0.2, bias alignment 0.2, timing (Fisher on 1m/5m turning back toward reversal direction) 0.1. Fire at total >= 0.65.

## 5. Candle-level definition (15m)

The setup rarely aligns with a 15m close, so the strategy runs on ticks, but for review and backtesting on candles:
- The cascade candle: 15m candle whose low (or high) extends >= 1.2 x ATR below the previous close and whose lower wick is >= 50% of total range.
- Entry candle: the next 1m to 5m bar where exhaustion conditions hold.
- Invalidation: a 1m close beyond the wick extreme.

## 6. Execution

- Entry: post-only limit at wick extreme + 0.15 x ATR (for longs), placed as soon as Step 2 confirms. If not filled in 3 minutes, cancel. Re-quote once at +0.25 x ATR if price is still within 0.5 x ATR of the wick. Then skip.
- Stop: 0.35 x ATR beyond the wick extreme. Stop is a trigger market order.
- Target 1: 50% of the cascade range retraced (measured from cascade start price to wick extreme). Take 60% off.
- Target 2: 70% retraced, or trail remainder with a 1m Hull(21) slope flip.
- Time stop: 90 minutes from fill, exit at market if still open.
- Size: risk 1.5% of equity / stop distance. Leverage cap 3x.

## 7. Data collection

- `userFills` websocket for the wallet universe (start with the top 2,000 wallets by account value and 30d volume, refresh weekly). Filter fills with non-null `liquidation`. Store to `liquidations` table.
- `trades` websocket for BTC and ETH: 1-minute taker buy/sell volume.
- `l2Book`: depth within 0.3% of mid, snapshot every 5 seconds.
- `clearinghouseState` polling: top 2,000 wallets every 60 seconds (larger wallets every 15 seconds) to build the liquidation map (`data/liqmap.py`): bucket liquidation prices in 0.25% bands, store notional and wallet count per band per side.
- Coinglass aggregate liquidation feed (or free 1-minute polling) for CEX confirmation.
- Optional upgrade: GoldRush `liquidationFills` global stream replaces the wallet-universe approach.

## 8. Signals and notifications

Telegram message on fire:
`[S01 LIQ] BTC LONG | cascade $4.2M / 61 wallets / -1.6 ATR | quiet 52s | depth 71% | score 0.74 | entry 61,180 stop 60,940 t1 61,690 | size 0.049 BTC 3.0x`

Also send a pre-alert when Step 1 fires (cascade detected) so you can watch it, and a "skipped" message with the failing condition when Step 2 never confirms.

## 9. Performance expectations and KPIs

- Frequency: 20 to 40 setups/month across BTC+ETH.
- Target win rate 55 to 62%, avg win 1.4 x avg loss, profit factor 1.5+, net of fees.
- Fee budget: maker entry + maker target, taker stop. Expect fees to be about 8% of gross profit.
- Kill switch: 20-trade rolling PF < 0.9.

Track: MAE distribution (how far against before it works), time-to-target, fraction of trades that hit the 90-minute time stop.

## 10. Failure modes and how the rules address them

| Failure | Rule |
|---|---|
| Entering after the first 20% chunk of a big liquidation | 45-second quiet rule |
| Market-order slippage in the wick | Post-only only, skip if not filled |
| Whale cluster absorbed by HLP backstop, no overshoot | Wallet-count >= 25 filter favours retail clusters |
| Second cluster just below the wick | Liquidation-map check within 1 ATR |
| Trading an HL-only glitch | CEX cascade confirmation in score |
| Cascade continues (trend day) | Stop at 0.35 ATR, time stop, regime gate blocks around macro events |

## 11. Backtest plan

1. Reconstruct 6 months of HL liquidations from stored wallet fills (or purchase 0xArchive data).
2. For every cascade meeting Step 1, record: max further move after quiet, retracement at 15/30/60/90 minutes, whether a 0.35 ATR stop was hit first.
3. Report hit rate of 50% retracement, expectancy with our entry/stop/target, and sensitivity to the quiet-time threshold (30s, 45s, 60s, 90s).
4. Go/no-go: net expectancy positive and PF > 1.3 with post-only entry modelled as filled only when traded through.

## 12. Review checklist (weekly)

- Did any cascade fire Step 1 but never confirm? Why (which condition)?
- Are wins coming from BTC or ETH? Drop the weak one if PF diverges by more than 0.5.
- Is the 45s quiet threshold causing missed bounces (MFE before fill)? Adjust only with 30+ trades of evidence.

## 13. Claude Code implementation prompts (paste verbatim, in order)

**Prompt 1, liquidation feed and map:**
```
In the perpbot project, implement data/wallets.py and data/liqmap.py.

data/wallets.py: build config/wallets.txt with the top 2000 Hyperliquid wallet addresses ranked by account value plus 30-day volume using the leaderboard and clearinghouseState endpoints, refresh weekly via a function refresh_wallet_universe(). Subscribe userFills for all wallets in batches of 100 addresses per websocket subscription and write every fill whose liquidation field is non-null into the liquidations table in logs/data.sqlite with columns ts, coin, side, px, sz, notional, liquidated_user, mark_px, method.

data/liqmap.py: every 60 seconds poll clearinghouseState for every wallet (every 15 seconds for the top 200 by position notional), and maintain an in-memory map keyed by coin with 0.25 percent price bands, storing for each band: long_liq_notional, short_liq_notional, long_wallet_count, short_wallet_count. Expose get_cluster(coin, price_from, price_to, side) returning total notional and wallet count. Snapshot the full map to Parquet every 5 minutes.
```

**Prompt 2, the strategy:**
```
Implement strategies/s01_liq_sweep.py as class LiqSweepReversal(Strategy) with these exact rules.

Cascade detection, evaluated every second on the liquidations table: liq_notional_5m is the sum of notional for fills with method equal to market on one side in the last 5 minutes. Trigger when liq_notional_5m is at least the maximum of (3000000 for BTC, 1500000 for ETH) and 0.0015 times current open interest notional, AND distinct liquidated_user count in the window is at least 25, AND the price move during the window is at least 1.2 times ATR(14) on 15m candles.

Exhaustion confirmation: no new market-method liquidation fills on that side for 45 seconds, AND 1-minute taker volume on the liquidated side is below 40 percent of its peak 1-minute value during the cascade, AND bid (for long cascade) or ask (for short cascade) depth within 0.3 percent of mid from l2Book is at least 60 percent of its 15-minute pre-cascade average, AND liqmap.get_cluster for the next 1 ATR beyond the wick extreme on the same side returns notional less than 50 percent of the swept cluster notional.

Scoring: trigger 0.5, regime score from features/regime.py 0.2, bias alignment 0.2 (bias sign matches reversal direction), timing 0.1 (fisher_transform on 1m with n=9 turning toward the reversal direction). Fire when total is at least 0.65 and regime gate allows trading.

Execution: entry post-only limit at wick extreme plus 0.15 ATR for longs (minus for shorts), cancel if unfilled after 180 seconds, re-quote once at 0.25 ATR if price is within 0.5 ATR of the wick, then skip. Stop at 0.35 ATR beyond the wick extreme as a trigger order. Target 1 at 50 percent retracement of the cascade range, close 60 percent of size. Target 2 at 70 percent retracement or trailing exit when 1m Hull MA(21) slope flips against the position. Time stop 90 minutes after fill. Size via RiskEngine.size_for.

Log every evaluation to the signals table including all score components and whether it fired, and send a Telegram pre-alert on cascade detection, a fire alert with entry stop target size leverage, and a skip alert naming the failing condition when exhaustion never confirms within 10 minutes.
```

**Prompt 3, backtest:**
```
Implement backtest/s01_backtest.py that replays the liquidations, trades, l2Book snapshots and 15m candles from the Parquet store for a given date range, runs LiqSweepReversal in replay mode, models post-only fills as filled only when a trade prints through the limit price, applies maker fee 0.00015 on entry and target fills and taker fee 0.00045 on stop fills, and outputs per-asset trades, win rate, expectancy per trade, profit factor, max drawdown, average MAE and MFE, and a sensitivity table for quiet thresholds of 30, 45, 60 and 90 seconds. Write results to backtest/results/s01_<daterange>.md.
```
