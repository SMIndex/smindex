# 00. System Overview: Multi-Strategy Perp Bot on Hyperliquid

Read this first. Every strategy doc (01 to 06) assumes the shared components described here.

## 1. What we are building

A multi-strategy, on-chain perpetuals trading system for a small account ($500 to $2,000), targeting 2 to 4 setups per day on 15-minute charts, BTC and ETH only at launch, executing on Hyperliquid with post-only (maker) orders.

Six strategies run in parallel, each hunting a different kind of move:

| # | Strategy | Type | Evidence rank |
|---|----------|------|---------------|
| 01 | Liquidation sweep reversal | Forced-flow mean reversion | 2 |
| 02 | Funding settlement flow | CEX 8h settlement traded on HL, plus funding gauge | 5 |
| 03 | Whale / top-wallet following | Information bias | 4 |
| 04 | Volatility compression breakout | Expansion trend | 3 |
| 05 | Session open momentum | Time-of-day momentum | 1 |
| 06 | Hull + Fisher + 200 EMA trend pullback | Chart timing, gated | 6 |

Build order: 05 and 01 first (best evidence, daily frequency), then 04, then 03 and 02 as bias/regime inputs, then 06 as the shared timing layer.

## 2. Architecture (one repo, shared services)

```
perpbot/
  config/
    settings.yaml          # account size, risk %, leverage cap, assets
    strategies.yaml        # per-strategy params, enabled flags, paper/live
  data/
    hl_ws.py               # Hyperliquid websocket client (trades, l2Book, candles, userFills)
    hl_info.py             # REST info API (funding, OI, positions, leaderboard, candles)
    cex_funding.py         # Binance/Bybit funding + settlement clock (Coinglass or direct)
    store.py               # Parquet/SQLite writer for candles, fills, funding, OI, positions
    wallets.py             # wallet universe builder + position poller
    liqmap.py              # liquidation map from live positions
  features/
    indicators.py          # Hull MA, Fisher Transform, EMA, ATR, realized vol
    regime.py              # regime gate (vol, funding, OI, session, events)
    bias.py                # bias score (whale cohort, funding, basis, crowd)
  strategies/
    base.py                # Strategy interface: on_candle(), on_fill(), on_tick()
    s01_liq_sweep.py
    s02_funding_flow.py
    s03_whale_follow.py
    s04_vol_compression.py
    s05_session_open.py
    s06_hull_fisher_ema.py
  risk/
    engine.py              # per-trade risk, daily cap, concurrency, kill switches
    sizing.py              # position size from stop distance and volatility
  exec/
    hl_exec.py             # post-only order placement, cancel/replace, fills tracking
    paper.py               # paper execution with realistic maker-fill model
  backtest/
    runner.py              # event-driven backtest over stored data
    metrics.py             # win rate, expectancy, PF, drawdown, fee drag
  alerts/
    notify.py              # Telegram/Discord alerts with reason string
  logs/
    trades.sqlite          # every signal, score, order, fill, outcome
  main.py                  # scheduler: runs data feeds, strategies, risk, exec
```

Language: Python 3.11+. Libraries: `hyperliquid-python-sdk`, `websockets`, `pandas`, `numpy`, `pyarrow`, `pyyaml`, `apscheduler`, `python-telegram-bot` (or a Discord webhook).

## 3. Shared data layer

### Hyperliquid (free, no key needed for reads)
- REST info endpoint: `POST https://api.hyperliquid.xyz/info`
  - `candleSnapshot` (15m, 1h, 4h candles)
  - `metaAndAssetCtxs` (funding, predicted funding, OI, mark, oracle, premium, impact prices)
  - `fundingHistory`
  - `clearinghouseState` for a wallet (positions, entry, leverage, liquidation price, margin)
  - `userFills` for a wallet
  - `leaderboard` (official leaderboard, via app endpoint; third-party mirrors exist)
- WebSocket: `wss://api.hyperliquid.xyz/ws`
  - `trades` (all trades per coin)
  - `l2Book` (order book)
  - `candle`
  - `userFills` (per wallet; each fill carries a `liquidation` object when it was a liquidation, with `liquidatedUser`, `markPx`, `method` = market or backstop)
  - `activeAssetCtx` (live funding/OI/mark)

Important: the public HL websocket has no global liquidation feed. Options: (a) subscribe `userFills` for the top few thousand wallets and filter for the `liquidation` field, or (b) pay for a global `liquidationFills` stream (GoldRush). Historical liquidations for backtesting: 0xArchive (paid) or reconstruct from wallet fills.

### CEX context (free)
- Binance/Bybit funding rates and 8h settlement clock (00:00, 08:00, 16:00 UTC). Coinglass free tier or direct exchange public endpoints.
- Coinglass aggregate liquidations as a leading indicator (most BTC cascades start on CEXs).

### Storage
- Parquet per asset per day for candles, trades, OI, funding.
- SQLite for wallet positions snapshots (wallet, coin, size, entry, leverage, liq_px, ts) and for the trade log.
- Keep everything. The review process depends on it.

## 4. Shared feature layer

### Indicators (features/indicators.py)
- EMA(n): standard.
- Hull MA(n): `HMA = WMA(2 * WMA(price, n/2) - WMA(price, n), sqrt(n))`. Slope = HMA[t] - HMA[t-1]. Colour up if slope > 0.
- Fisher Transform(n): `x = 2 * ((price - low_n) / (high_n - low_n) - 0.5)`, clamp to [-0.999, 0.999], smooth `x = 0.33 * x + 0.67 * x_prev`, `fisher = 0.5 * ln((1 + x) / (1 - x))`, smooth `fisher = 0.5 * fisher + 0.5 * fisher_prev`. Signal = fisher[t-1]. Use price = (high + low) / 2, n = 9 on 15m.
- ATR(14) on 15m.
- Realized vol: std of 1-minute log returns over trailing 60 minutes, annualised, compared to its own 24h average.
- Range compression: current 15m ATR / 20-bar mean ATR.

### Regime gate (features/regime.py), evaluated every candle
Returns `TRADE_ALLOWED` true/false plus a regime score 0 to 1. Trading is blocked if any of:
- Scheduled macro event (CPI, FOMC, NFP) within next 2 hours or last 30 minutes.
- Oracle stale: HL oracle price not updated for > 10 seconds.
- Spread on BTC/ETH wider than 2x its 24h median.
- Weekend low-liquidity window (Sat 00:00 to Sun 12:00 UTC) unless strategy explicitly allows.

Regime score inputs (each 0 to 1, averaged): realized vol vs 24h avg, OI 24h change magnitude, funding extremity, session quality (US/EU open = 1, dead hours = 0.3).

### Bias score (features/bias.py), evaluated every 5 minutes
Returns a number from -1 (strong short bias) to +1 (strong long bias):
- Whale cohort net positioning change over 24h (see 03) weight 0.35
- HL hourly funding sign and extremity (contrarian: high positive funding = short bias) weight 0.25
- HL vs Binance basis (HL trading rich = short tilt) weight 0.15
- Crowd long/short ratio (contrarian) weight 0.15
- 1h Hull slope direction weight 0.10

## 5. Shared risk engine (risk/engine.py)

Non-negotiable defaults for a $1,000 account:
- Risk per trade: 1.5% of account equity ($15). Position size = risk_dollars / stop_distance. Leverage is an output, capped at 3x notional/equity. If required leverage > 3x, reduce size, never widen stop.
- Max concurrent positions: 2. Never two positions same direction same asset.
- Daily loss cap: 4% of equity. On hit, bot goes to paper until next UTC day.
- Max consecutive losses: 2 then pause 4 hours.
- Per-strategy kill switch: rolling 20-trade profit factor < 0.9 puts that strategy in paper mode; it re-arms only by manual toggle after review.
- Time stop: every strategy defines a max hold; the engine enforces it.
- All orders post-only (`tif: Alo` on HL). If a post-only order would cross, it is cancelled and re-quoted 1 tick inside. Max 3 re-quotes, then skip the trade.

## 6. Execution (exec/hl_exec.py)

- Entries: post-only limit at the strategy's entry price. Track fill via `userFills`.
- Stops: HL trigger order (stop market) placed immediately after entry fill. Stops are the one allowed taker order.
- Targets: post-only limit at target. Optional partial: 50% at target 1, rest trails.
- Cancel/replace: if unfilled after the strategy's entry timeout, cancel.
- Paper mode: `exec/paper.py` simulates maker fills only when the market trades through the limit price (not touch), to avoid optimistic fills.

## 7. Fees (as of Aug 2026, base tier)
- Maker 0.015%, taker 0.045%. Post-only both sides on a $3,000 position ≈ $0.90 round trip. 60 trades/month ≈ $54 ≈ 5.4% of a $1,000 account. Market orders both sides ≈ 16%. This is why post-only is mandatory.
- Referral discount 4% applies at signup. HYPE staking tiers give further discounts.
- Stops fire as taker; budget for it.

## 8. Logging and review (logs/trades.sqlite)

Every signal, fired or not, is logged with: ts, strategy, asset, direction, score components (regime, bias, trigger, timing), entry, stop, target, size, leverage, fill ts, exit ts, exit reason, pnl_gross, fees, pnl_net, mae, mfe.

Weekly review (manual, 20 minutes):
1. Per strategy: trades, win rate, expectancy per trade, profit factor, avg MAE/MFE, fee drag.
2. Which score components predicted outcomes (simple correlation of each component with pnl_net).
3. Kill or re-arm decisions.
4. Parameter changes only with a written reason in `CHANGELOG.md`.

## 9. Phases

1. Week 1: data layer running 24/7, storing everything. No strategies.
2. Week 2: strategies 05 and 01 as alerts + paper. Manual review each evening.
3. Week 3 to 4: risk engine + live execution for 05 and 01 at $500. Add 04 in paper.
4. Month 2: promote 04 if paper PF > 1.3 over 30 trades. Add 03 and 02 as bias inputs. 06 stays timing-only.
5. Month 3: scale to full capital only if net-of-fees expectancy positive over 60 live trades.

## 10. Claude Code bootstrap prompt (paste verbatim)

```
Create a Python 3.11 project called perpbot with this exact structure: config/settings.yaml, config/strategies.yaml, data/hl_ws.py, data/hl_info.py, data/cex_funding.py, data/store.py, data/wallets.py, data/liqmap.py, features/indicators.py, features/regime.py, features/bias.py, strategies/base.py, risk/engine.py, risk/sizing.py, exec/hl_exec.py, exec/paper.py, backtest/runner.py, backtest/metrics.py, alerts/notify.py, main.py, requirements.txt, README.md.

Use hyperliquid-python-sdk for REST and websocket access. settings.yaml must contain: account_equity_usd: 1000, risk_per_trade_pct: 1.5, max_leverage: 3, max_concurrent_positions: 2, daily_loss_cap_pct: 4, max_consecutive_losses: 2, assets: [BTC, ETH], mode: paper, telegram_bot_token: "", telegram_chat_id: "".

data/hl_ws.py: subscribe to trades, l2Book, candle (15m, 1h, 4h) and activeAssetCtx for BTC and ETH, plus userFills for a configurable list of wallet addresses loaded from config/wallets.txt. Persist everything through data/store.py as Parquet files partitioned by asset and UTC date, and write liquidation fills (fills where the liquidation field is non-null) to a separate table liquidations in logs/data.sqlite with columns ts, coin, side, px, sz, liquidated_user, mark_px, method.

data/hl_info.py: functions get_candles(coin, interval, start_ms, end_ms), get_asset_ctx(coin) returning funding, predicted_funding, oi, mark, oracle, premium, get_funding_history(coin, start_ms), get_wallet_state(address) returning positions with size, entry, leverage, liquidation_px, margin_used.

features/indicators.py: implement ema(series, n), hull_ma(series, n), fisher_transform(high, low, n=9) returning fisher and signal, atr(high, low, close, n=14), realized_vol(close_1m, window=60), with unit tests in tests/test_indicators.py using known values.

risk/engine.py: implement RiskEngine with methods can_open(strategy, asset, direction), size_for(entry, stop, equity), register_fill, register_exit, and the rules: risk 1.5% per trade, leverage cap 3x, max 2 concurrent positions, no duplicate direction per asset, daily loss cap 4% switches mode to paper until next UTC day, 2 consecutive losses pauses 4 hours, per-strategy rolling 20-trade profit factor below 0.9 sets that strategy to paper.

exec/hl_exec.py: place_post_only(coin, side, px, sz) using tif Alo, cancel(oid), place_stop(coin, side, trigger_px, sz), track fills from the userFills stream. exec/paper.py: same interface, fills a resting order only when a trade prints through the limit price.

logs/trades.sqlite schema: signals(id, ts, strategy, asset, direction, regime_score, bias_score, trigger_score, timing_score, total_score, fired), trades(id, signal_id, entry_px, stop_px, target_px, size, leverage, fill_ts, exit_ts, exit_reason, pnl_gross, fees, pnl_net, mae, mfe).

main.py: start the data feeds, run enabled strategies from strategies.yaml on each closed 15m candle, route signals through RiskEngine, execute via paper or live per settings, send a Telegram alert for every fired signal with a one-line reason.

Do not implement any strategy logic yet. Make everything run with python main.py in paper mode and verify data is being written.
```
