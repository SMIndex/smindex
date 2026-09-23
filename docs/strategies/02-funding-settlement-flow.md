# 02. Funding Settlement Flow

## 1. Overview

**What:** Two jobs. (a) A trade: fade the crowded side in the 30 minutes before the 8-hour CEX funding settlements (00:00, 08:00, 16:00 UTC) when CEX funding is extreme, executed on Hyperliquid. (b) A gauge: use Hyperliquid's hourly funding as the sharpest crowding measure available and feed it into the regime and bias layers of every other strategy.

**Why the original idea was downgraded:** Hyperliquid settles funding hourly at 1/8 of the 8h rate, sampled every 5 seconds and averaged over the hour. Each payment is small relative to fees, so nobody repositions around HL's hour marks. Funding sniping is designed out on HL. The crowd repositioning that moves price happens on Binance/Bybit before their 8h settlements, and since BTC/ETH price is global, that move shows up in HL's mark price immediately. So the clock is the CEX clock, the execution is on HL.

**When:** Fires only when CEX funding is extreme. Realistic frequency 3 to 5 times per week, not daily. The gauge runs 24/7.

**Direction:** Against the side paying funding. High positive funding = short. Deeply negative = long.

**Hold time:** Enter 20 to 30 minutes before settlement, exit at settlement or within 30 minutes after. Time stop 60 minutes.

## 2. Market mechanics

- HL funding: `F = average premium index + clamp(interest - premium, -0.0005, 0.0005)`, paid hourly, capped 4% per hour, peer-to-peer, notional at oracle price. Positions opened and closed within the same hour pay nothing.
- HL displays current and predicted next-hour funding. Predicted rate is the useful one.
- Binance/Bybit: 8h settlement at 00:00, 08:00, 16:00 UTC. Holding at the timestamp pays/receives the full amount. Traders close before and reopen after when funding is punitive. Binance switches to hourly settlement once funding hits its cap (±0.3% for BTCUSDT), so during extreme volatility the 8h event disappears there too.
- HL posts the highest mean and standard deviation of funding among major venues because of the 1-hour window (max observed 0.067%/h BTC, 0.075%/h ETH). That makes HL funding the fastest crowding gauge.

## 3. Research data and evidence

- HL docs: hourly settlement, 5-second premium sampling averaged over the hour, 4%/h cap.
- Multiple 2025 to 2026 analyses state that hourly settlement makes funding sniping less effective because each payment is small relative to transaction costs.
- Practitioner consensus (BitMEX, Binance, BingX, ForkLog 2026): extreme funding flags an overheated side and raises reversal odds; Binance notes arbitrageurs quickly push funding back to its mean. No rigorous published study quantifies the pre-settlement price move; treat the trade as a lower-confidence setup.
- Evidence supports funding as a **bias and filter** more strongly than as a **clock-based trigger**.

## 4. Identification rules (exact)

**Gauge (always on, every minute):**
- `hl_funding_pct` = HL predicted hourly funding, expressed as 8h-equivalent (x8).
- `cex_funding_pct` = Binance BTCUSDT (or ETHUSDT) current 8h funding.
- `funding_z` = z-score of `hl_funding_pct` against its trailing 30-day distribution.
- Crowding level: `EXTREME` if `|funding_z|` >= 2 (either side) or `cex_funding_pct` >= 0.05% (or <= -0.03%). `ELEVATED` if `|funding_z|` >= 1. The sign of the funding gives the crowd side (positive = crowd long, negative = crowd short); the trade is taken against it.
  - *Rule change 2026-09-04 (owner-approved):* the z test was previously one-sided (`funding_z >= 2`), so a negative extreme (BTC HL funding z −3.75 on 2026-09-03 21:00 UTC) read as NORMAL. That was a doc oversight, not intent; the CEX rate test was always two-sided.
- Outputs to bias layer: contrarian tilt = -sign(funding) x min(|funding_z| / 3, 1).
- Outputs to regime layer: block same-direction-as-crowd entries for other strategies when `EXTREME`.

**Trade (evaluated at T-30 minutes before each of 00:00, 08:00, 16:00 UTC):**
1. Crowding level is `EXTREME` on the CEX rate for this coin.
2. OI on HL has risen at least 3% over the prior 24h (crowd is actually positioned, not just a premium print).
3. Price has not already moved against the crowd by more than 1 x ATR(14, 15m) in the last 2 hours (the trim already happened).
4. Regime gate = TRADE_ALLOWED and no macro release inside the window.
5. Timing: 15m Fisher(9) is turning against the crowd side, or 15m Hull(21) slope is flat-to-against.

Score: crowding 0.4, OI confirmation 0.2, regime 0.2, timing 0.2. Fire at >= 0.65.

## 5. Candle-level definition (15m)

- Setup candles: the two 15m candles closing at T-30 and T-15 before settlement.
- Entry: on the close of the T-30 candle if rules hold, post-only at that close ± 0.1 ATR in your favour.
- Exit: the first 15m candle close after settlement, or target/stop.

## 6. Execution

- Entry: post-only limit at candle close +0.1 ATR (for shorts, above close). Cancel if unfilled by T-10.
- Stop: 0.6 x ATR beyond entry.
- Target: 0.8 x ATR, or exit at first 15m close after settlement, whichever first.
- Time stop: 60 minutes after settlement.
- Size: 1.5% risk / stop distance, capped at 3x. Because this is the lower-confidence strategy, weight at 0.75 of normal size until 30 trades prove it.

## 7. Data collection

- HL: `metaAndAssetCtxs` every 60 seconds for funding, predicted funding, OI, premium. `fundingHistory` for the 30-day distribution.
- CEX: Binance `premiumIndex` and Bybit `tickers` public endpoints, or Coinglass funding table, polled every 60 seconds. Store to `funding` table: ts, venue, coin, rate, predicted, oi.
- Settlement clock: computed, no data needed.

## 8. Signals and notifications

- Gauge alert whenever crowding flips to `EXTREME` or back: `[S02 GAUGE] BTC funding EXTREME +0.061%/8h (z=2.4), OI +5.1%/24h -> short bias, long entries blocked for other strategies`
- Trade pre-alert at T-45: `[S02 SETTLE] BTC short setup forming for 08:00 UTC, score 0.58, waiting for timing`
- Fire alert at T-30 with entry/stop/target/size.

## 9. Performance expectations and KPIs

- Frequency: 12 to 20 trades/month.
- Target win rate 52 to 58%, avg win ≈ avg loss, PF 1.2 to 1.4. This is the modest one.
- The gauge's value is measured differently: track the PnL of other strategies' trades taken with vs against the gauge's tilt. If trades against the tilt lose more, the gauge is earning its place even if the standalone trade is flat.

## 10. Failure modes

| Failure | Rule |
|---|---|
| Trading HL's hour marks expecting a flow that does not exist | Only CEX 8h times are used |
| Binance already switched to hourly (cap hit) | Detect cap regime, skip the trade, keep the gauge |
| Crowd already trimmed early | Rule 3 (no > 1 ATR adverse move in prior 2h) |
| Funding extreme because of a real trend, price keeps going | Stop 0.6 ATR, timing requirement, reduced size |
| Extreme funding on HIP-3 or thin assets with different premium formula | BTC/ETH only |

## 11. Backtest plan

1. Pull 12 months of Binance 8h funding and HL hourly funding, plus HL 15m candles.
2. For each settlement where CEX funding was `EXTREME`, measure price change from T-30 to settlement and to T+30, split by whether OI rose 3%+ in prior 24h.
3. Compute expectancy with our entry/stop/target. Also measure the gauge: for a random set of hypothetical entries, does trading against the tilt underperform?
4. Go/no-go for the trade: PF > 1.2 net of fees. The gauge stays regardless.

## 12. Review checklist

- Which threshold produced the fires (z-score or absolute CEX rate)? Keep both logged.
- How often did Binance's hourly cap regime block the trade?
- Correlation of gauge tilt with other strategies' outcomes.

## 13. Claude Code implementation prompts (paste verbatim, in order)

**Prompt 1, funding gauge:**
```
In the perpbot project, implement data/cex_funding.py and features/funding_gauge.py.

data/cex_funding.py: poll every 60 seconds the Binance USDT-M futures public premiumIndex endpoint for BTCUSDT and ETHUSDT to get lastFundingRate and nextFundingTime, and the Hyperliquid metaAndAssetCtxs endpoint for BTC and ETH to get funding, predicted funding, openInterest and premium. Store rows in a funding table in logs/data.sqlite with columns ts, venue, coin, rate, predicted_rate, next_settlement_ts, oi_notional. Also backfill 30 days of Hyperliquid fundingHistory on first run.

features/funding_gauge.py: compute for each coin hl_funding_8h_equiv equal to predicted hourly funding times 8, funding_z as the z-score of hl_funding_8h_equiv against the trailing 30-day distribution, and crowding_level which is EXTREME when abs(funding_z) is at least 2 (either side) or the Binance rate is at least 0.0005 or at most -0.0003, ELEVATED when abs(funding_z) is at least 1, else NORMAL. Expose tilt(coin) returning negative sign of funding multiplied by min(abs(funding_z) divided by 3, 1), and blocked_direction(coin) returning the crowd side when crowding_level is EXTREME. Wire tilt into features/bias.py with weight 0.25 and blocked_direction into features/regime.py so other strategies cannot open in the blocked direction. Send a Telegram alert whenever crowding_level changes.
```

**Prompt 2, the settlement trade:**
```
Implement strategies/s02_funding_flow.py as class FundingSettlementFlow(Strategy).

Schedule evaluation at exactly 30 minutes before each of 00:00, 08:00 and 16:00 UTC for BTC and ETH. Skip entirely if Binance nextFundingTime indicates hourly settlement (cap regime).

Rules: crowding_level from features/funding_gauge.py is EXTREME on the Binance rate for this coin; Hyperliquid openInterest is at least 3 percent higher than 24 hours ago; price has not moved against the crowd side by more than 1 ATR(14) on 15m in the last 2 hours; regime gate allows trading and no macro event is inside the window; timing is satisfied when 15m fisher_transform(n=9) is turning against the crowd side or 15m hull_ma(21) slope is zero or against the crowd side.

Score: crowding 0.4, OI confirmation 0.2, regime 0.2, timing 0.2. Fire at total at least 0.65. Direction is against the crowd side.

Execution: post-only limit at the last 15m close plus 0.1 ATR in the trade's favour, cancel if unfilled 10 minutes before settlement. Stop 0.6 ATR beyond entry as a trigger order. Target 0.8 ATR, or exit at the first 15m candle close after settlement, whichever comes first. Time stop 60 minutes after settlement. Size via RiskEngine.size_for multiplied by 0.75.

Log all evaluations to the signals table, send a pre-alert at 45 minutes before settlement when score is above 0.5, and a fire alert with entry, stop, target, size and leverage.
```

**Prompt 3, backtest:**
```
Implement backtest/s02_backtest.py: load 12 months of Binance 8h funding, Hyperliquid hourly funding, Hyperliquid OI history and 15m candles from the store. For every 8h settlement where crowding_level was EXTREME, compute price change from T-30 minutes to settlement and to T+30 minutes, split by whether OI rose at least 3 percent in the prior 24 hours. Then simulate FundingSettlementFlow with post-only fills modelled as filled only when traded through, maker fee 0.00015 and taker fee 0.00045 on stops, and report trades, win rate, expectancy, profit factor and max drawdown per coin. Separately, take every trade from the other strategies' backtests, tag each as with-tilt or against-tilt using the gauge at entry time, and report the expectancy of each group. Write to backtest/results/s02_<daterange>.md.
```
