# 05. Session Open Momentum

## 1. Overview

**What:** Trade the first directional push after the US open, the European open, and the Monday Asia open, with direction from the opening move itself plus the bias layer, and only on high-volatility days.

**Why it works:** Bitcoin has documented intraday and intraweek seasonality tied to traditional market hours. Volume and volatility peak during European and US equity hours. A 2025 Swiss Finance Institute paper documents a "Monday Asia Open Effect" in high-frequency trend-following that grew stronger after institutions entered in 2020. A University of Reading paper finds the first half hour of the session predicts the close on high-volatility days and has no predictive power on medium or low volatility days.

**When:** Three windows per day (two on weekdays, plus Monday Asia). Fires only on high-vol days. Expect 1 to 2 trades per day on qualifying days, zero on quiet days.

**Direction:** Direction of the opening-range break, filtered by bias and Hull.

**Hold time:** 1 to 3 hours. Time stop at 3 hours or session midpoint.

## 2. Market mechanics

- Sessions (UTC, adjust for DST): European open 07:00 to 08:00 (Frankfurt/London), US equity open 13:30 (14:30 in winter), CME open 13:30 as well, Tokyo open 00:00 (Monday only for this strategy).
- Volatility is highest in the EU/US overlap. Asia open moves volatility only marginally in general, but Monday's Asia open has a specific trend effect.
- Macro releases (CPI, NFP, FOMC) land 08:30 ET, inside or just before the US window. They dominate the open when present; the regime gate handles them.

## 3. Research data and evidence

- Zarattini, Pagani, Barbon (Swiss Finance Institute, 2025, revised Oct 2025): high-frequency trend-following ensemble on BTC 2018 to 2025, volatility-scaled to 20%, gross Sharpe about 1.6; clear Monday Asia Open Effect that became substantially more pronounced after mid-2020.
- University of Reading, Bitcoin Intraday Time-Series Momentum: first-half-hour return predicts last-half-hour return with strong significance on high-volatility days (R2 2.83%, 1% level) and no evidence on medium or low volatility days.
- Time-of-day periodicity study (Bitstamp data): reverse V-shaped intraday volume/volatility, peaks at European open and around US open, weekday volatility substantially higher than weekend.
- ScienceDirect 2025: 5-minute BTC/ETH data shows significant reactions to US, German and Japanese macro releases.
- QuantPedia: since 2021 most BTC returns realised in the overnight (US-closed) sessions, mirroring equities. Relevant for direction bias, not for this intraday trade.

## 4. Identification rules (exact)

**Session windows (UTC):**
- EU: opening range 07:00 to 07:30, trade window 07:30 to 10:00.
- US: opening range 13:30 to 14:00 (14:30 to 15:00 in US winter), trade window until 17:00.
- Monday Asia: opening range 00:00 to 00:30 Monday, trade window until 03:00.

**High-volatility day filter (evaluated at window start):**
- Realized vol over the prior 24h >= 60th percentile of the trailing 30-day distribution, OR the opening-range height >= 1.2 x ATR(14, 15m).
- If neither holds, the session is skipped entirely.

**Opening range (OR):** high and low of the first 30 minutes (two 15m candles).

**Trigger:** a 15m candle closes beyond the OR by >= 0.1 x ATR within the trade window.

**Confirmation:**
- Bias layer agrees or is neutral (bias x direction >= -0.2).
- 1h Hull(21) slope agrees, or is flat and 15m Fisher(9) is turning in the break direction.
- Funding gauge is not `EXTREME` on the same side as the trade (do not join a crowd that is about to be squeezed).
- Regime gate = TRADE_ALLOWED (blocks macro windows).
- Taker volume in the break candle >= 1.3 x the 20-candle average.

Score: vol-day quality 0.25, OR break quality 0.2, bias alignment 0.2, timing 0.2, volume 0.15. Fire at >= 0.65.

## 5. Candle-level definition (15m)

- OR candles: the first two 15m candles of the session.
- Trigger candle: first 15m close outside the OR by 0.1 ATR.
- Entry candle: (a) close of the trigger candle, or (b) first pullback candle touching OR edge + 0.1 ATR within the next 2 candles. Default (b), fall back to (a) on the next candle if the break candle range was less than 1 ATR (still close).
- Invalidation: 15m close back inside the OR.

## 6. Execution

- Entry: post-only limit at OR edge + 0.1 x ATR (long). Valid for 2 candles.
- Stop: OR midpoint, or 0.8 x ATR below entry, whichever is closer.
- Target 1: 1.0 x OR height beyond the OR edge (take 50%).
- Target 2: 2.0 x OR height, or trail with 15m Hull(21) slope flip.
- Time stop: 3 hours from fill or end of trade window, whichever first.
- One trade per session per coin. If stopped out, no re-entry that session.
- Size: 1.5% risk / stop distance, capped 3x.

## 7. Data collection

- HL 15m and 1h candles, 1m closes for realized vol.
- `trades` websocket for taker volume per 15m candle.
- Economic calendar (CPI, NFP, FOMC, PCE, ISM) loaded weekly into `config/events.yaml` for the regime gate.
- Bias and funding gauge from 02 and 03.
- DST calendar for US/EU offsets.

## 8. Signals and notifications

- Session start: `[S05 SESSION] US open, vol-day YES (24h RV 82nd pct), OR 61,020 to 61,410, watching`
- Skip: `[S05 SESSION] EU open skipped, low-vol day (RV 31st pct)`
- Fire: `[S05 SESSION] BTC LONG US-open break | entry 61,450 stop 61,215 t1 61,800 t2 62,190 | score 0.72 | size 0.19 BTC 3.0x`

## 9. Performance expectations and KPIs

- Frequency: 20 to 35 trades/month (skipping quiet days).
- Target win rate 50 to 56%, avg win 1.5 x avg loss, PF 1.4+.
- KPI: performance split by session (EU vs US vs Monday Asia) and by vol-day percentile bucket. Expect the strategy to be clearly better in the top vol tercile; if not, the filter thresholds are wrong.

## 10. Failure modes

| Failure | Rule |
|---|---|
| Trading the open on a quiet day | High-vol day filter (research shows no edge otherwise) |
| Macro release whipsaw | Regime gate blocks 30 min before to 30 min after |
| Fake break of OR | Volume confirmation, pullback entry, close-inside invalidation |
| Joining a crowded side into a squeeze | Funding gauge block |
| DST mistakes | Explicit DST-aware session table, unit-tested |
| Over-trading a choppy session | One trade per session per coin |

## 11. Backtest plan

1. 18 months of 15m candles, 1m closes, taker volume for BTC/ETH.
2. For each session, compute OR, vol-day flag, first break, and forward returns at 1h, 2h, 3h and session end. Split by vol-day flag and by session type.
3. Simulate with our entry/stop/targets, post-only fills only when traded through, fees.
4. Go/no-go: PF > 1.3 net on high-vol days; and the vol-day filter must show a clear gap (high-vol expectancy materially above low-vol).

## 12. Review checklist

- Which session is earning? Drop a session if PF < 1.0 over 30 trades.
- Vol-day threshold: check whether the 60th percentile cut is losing good trades (look at expectancy in the 50 to 60 bucket).
- Check every skipped macro day: was skipping right?

## 13. Claude Code implementation prompts (paste verbatim, in order)

**Prompt 1, session infrastructure:**
```
In the perpbot project, implement features/sessions.py with a DST-aware session table: EU opening range 07:00 to 07:30 UTC with trade window ending 10:00 UTC; US opening range starting at 13:30 UTC during US daylight time and 14:30 UTC during US standard time, opening range 30 minutes, trade window ending 3.5 hours after the range start; Monday Asia opening range 00:00 to 00:30 UTC on Mondays with trade window ending 03:00 UTC. Provide current_session(ts) and opening_range(coin, session) computed from stored 15m candles. Add unit tests for DST transitions in March and November. Add config/events.yaml with a list of scheduled macro events (name, utc_ts) and a loader in features/regime.py that blocks trading from 30 minutes before to 30 minutes after each event. Add to features/indicators.py realized_vol_24h_percentile(coin) equal to the percentile rank of the last 24 hours realized vol against the trailing 30 days.
```

**Prompt 2, the strategy:**
```
Implement strategies/s05_session_open.py as class SessionOpenMomentum(Strategy), evaluated on each closed 15m candle for BTC and ETH.

At session start compute vol_day equal to realized_vol_24h_percentile at least 60 OR opening range height at least 1.2 times ATR(14, 15m). If vol_day is false, send a skip alert and do nothing for that session. After the opening range closes, wait for a 15m candle closing beyond the range by at least 0.1 ATR. Confirm when: bias score times direction is at least negative 0.2; 1h hull_ma(21) slope agrees or is flat with 15m fisher_transform(n=9) turning in the break direction; funding gauge crowding_level is not EXTREME on the trade side; regime gate allows trading; taker volume in the break candle is at least 1.3 times the 20-candle average.

Score: vol-day quality 0.25 (percentile scaled), break quality 0.2, bias alignment 0.2, timing 0.2, volume 0.15. Fire at total at least 0.65.

Execution: post-only limit at the opening range edge plus 0.1 ATR for longs (minus for shorts), valid for 2 candles; if the break candle range was under 1 ATR and the pullback did not fill, place at the next candle close instead. Stop at the range midpoint or entry minus 0.8 ATR, whichever is closer, as a trigger order. Target 1 at range edge plus 1.0 times range height closing 50 percent, target 2 at 2.0 times range height or trailing on 15m hull_ma(21) slope flip. Time stop 3 hours after fill or end of the trade window. One trade per session per coin, no re-entry after a stop. Any 15m close back inside the opening range cancels or exits. Size via RiskEngine.size_for.

Send Telegram alerts at session start with vol-day status and the range, on skip, and on fire with entry, stops, targets and size. Log all evaluations to the signals table with the session name.
```

**Prompt 3, backtest:**
```
Implement backtest/s05_backtest.py over 18 months of stored 15m candles, 1m closes and taker volume for BTC and ETH. For every EU, US and Monday Asia session compute the opening range, vol_day flag, first qualifying break and forward returns at 1h, 2h, 3h and window end, reported split by session type and by vol_day flag and by realized vol percentile buckets (0-40, 40-60, 60-80, 80-100). Then simulate SessionOpenMomentum with post-only fills only when traded through, maker fee 0.00015 and taker 0.00045 on stops, reporting trades, win rate, expectancy, profit factor, max drawdown per session type. Write to backtest/results/s05_<daterange>.md.
```
