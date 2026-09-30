# Models M1–M6 + structure engine + heuristic minds — implementation report

Built and deployed 2026-09-05/06 (UTC) from docs 10–17 (`10-structure-engine-and-mind-framework.md`,
`11-M1` … `16-M6`, `17-claude-code-master-prompt.md`). One pass, no phase gates, paper only.
Ambiguities are recorded in `DECISIONS.md` (D-01 … D-41), not resolved by assumption.

## 1. What was built

| Layer | Files | Notes |
|---|---|---|
| Structure engine (doc 10 §2) | `backend/app/strategy_engine/structure/` — `candles, swings, trend, ranges, sessions, pools, sweeps, displacement, zones, vwap, day_type, alignment` (13 modules, ~1.2k lines) | Swings, BOS/CHoCH, trend per tf, ranges + premium/discount, session ranges (Asia frozen 07:00), liquidity pools (pdh/pdl/pwh/pwl/session/equal highs-lows/weekly open), sweeps + reclaim, displacement grade (formula verbatim, D-04), order blocks / FVGs, session + daily VWAP, volume profile (D-19), day-type classifier (D-23/D-24), per-coin alignment table logged every closed 15m. Daily candles aggregated from 4h (D-03). |
| Mind framework (doc 10 §4–7) | `backend/app/strategy_engine/mind/` — `base.py` (Mind: reasons × weights, vetoes, multipliers, conviction, tiers, thesis, `manage()` in-trade checks), `snapshot.py` (MindSnapshot built from structure + feeds), `config.py` (models.yaml loader), `learn.py` (weekly learner: calibration rows, weight proposals, flags; applies only after the 60-day gate) | Deterministic — no AI calls. `size_tier`: skip < 0.55, half 0.55–0.70, full ≥ 0.70 (yaml). Feed-missing gates are recorded vetoes, never assumed passes (D-31). |
| Models | `backend/app/strategy_engine/strategies/model_base.py` + `m1_sweep_reclaim.py`, `m2_bos_order_block.py`, `m3_failed_auction.py`, `m4_htf_choch.py`, `m5_session_liquidity_run.py`, `m6_weekly_open_reclaim.py` | Each: `find_setup` (doc sequence), own Mind (reasons/vetoes/multipliers from its doc), entry/stop/T1/T2/expected hold, in-trade checks, trail rule (D-11), attempt counters in `mind_model_state` (D-07, D-27, D-32, D-35). BTC + ETH. |
| Runner / scheduler | `backend/app/strategy_engine/model_runner.py` (MODEL_REGISTRY, per-15m evaluation, `ModelTradeManager` lifecycle: pending → fill on book-mid tape → partial at T1 (40%) → BE → trail → T2/T3 / thesis exit / hard stop / dead-trade hold; r_multiple D-12), `scheduler.py`, `worker.py` | Pending/open resolution every tick, `Mind.manage` once per closed 15m (D-14). Paper fills use the existing strict `limit_fills_through` (D-05, D-20). Sizing via `GlobalRisk.size_for` at 1.5% / 0.75% risk, max 3x (D-21). 01–06 manager skips model rows (`AND model IS NULL`, D-02). |
| Config | `backend/config/models.yaml` | common: assets BTC/ETH, mode paper (only accepted value, D-22), conviction_skip_below 0.55, full_from 0.70, exit_threshold 2, dead_trade_hold_multiple 1.5, partial_at_t1_pct 40, learning off / 60-day gate. |
| Data | `backend/app/db/strategy_models.py`, `backend/app/db/migrations_models.py` (**migration v15**, guarded, run by API lifespan AND worker — D-06) | New tables `mind_weights`, `mind_calibration`, `mind_model_state`, `strat_alert_prefs`; `strat_signals` += `model, level_type, level_price, day_type, raw_conviction, conviction, size_tier, reasons_json, vetoes_json, multipliers_json, thesis`; `strat_trades` += `model, expected_hold_min, r_multiple, in_trade_checks_json, lifecycle_json`. |
| Seeding | `seed.py` — `MODELS` (6) + `MODEL_PARAMETERS` (6 × 11 = 66), inserted only where `(strategy_id, key)` is missing; 01–06 rows untouched (D-01). `mode.MODEL_IMPLEMENTED`, `conditions.MODEL_CONDITIONS`. | |
| API | `backend/app/routers/strategies.py` — `_NAMES = dict(STRATEGIES + MODELS)`; signals/trades carry the Mind fields; `GET /api/strategies/{sid}/breakdown` (weights, calibration, weight history, R by tier; 404 for non-models); `GET/PUT /api/strategies/alerts/prefs` (per wallet, auth required); `POST /{model}/backtest` → 404 (D-39). `contract.py`: `Signal` Mind fields, `MindBreakdown`, `AlertPref`, `Trade.model/expected_hold_min/r_multiple/lifecycle/in_trade_checks`. | |
| Telegram (step 6) | `backend/app/services/strat_outbox.py` — model kinds `M<n>_<event>` (`_pre` setup, fill, partial, exit), every message prefixed `[Mx]`; drain filters per recipient wallet by `strat_alert_prefs` (absent row = on). Existing `telegram_queue` throttle/dedupe reused; templates for 01–06 untouched. | |
| Frontend (step 5) | `frontend/src/types/strategies.ts`, `frontend/src/lib/strategiesApi.ts` (route ids `M1`..`M6`, `isModelNum`, `getBreakdown`, `get/putAlertPrefs`, `EVIDENCE_ORDER` + models), `design-b/screens/StrategiesOverviewB.tsx` (**Model** badge, dynamic header "8 paper strategies + 6 heuristic-mind models"), `design-b/screens/StrategyDetailB.tsx` (`MindPanel` in place of the condition card, `MindBreakdownTab` in the Breakdown tab, model-aware evaluation-log and trade columns, Backtest hidden), `design-b/screens/SettingsB.tsx` (`ModelAlertsCardB` — six checkboxes). Pages for 01–06 unchanged (their branches untouched, D-41). | |
| Tests | `backend/tests/strategy_engine/test_structure.py`, `test_mind.py`, `test_models_m1_m6.py`, `test_model_runner.py`, `model_fixtures.py` — **67 new tests; suite 187/187 green** (`python -m pytest tests/strategy_engine -q`). Synthetic tapes only where the fixture is the model's own setup dict (D-37); everything else organic. | |

Not built (by rule): live mode / venue adapter; any change to 01–06 behaviour, RiskEngine limits, the live path, Telegram templates for 01–06, shell A, the Design B default.

## 2. Deploy log (prod <server>, overlay — server has no git)

| UTC | Step | Result |
|---|---|---|
| ~22:35 | Backend tar (app files above + `config/models.yaml`, no `__pycache__`) → `/var/www/terminal/`; frontend `dist` tar → `/var/www/terminal/frontend/` (build `VITE_COPY_LIVE_ENABLED=true VITE_COPY_MODE=live_manual npx vite build`; `tsc -p tsconfig.app.json` = the 29 pre-existing errors, none in strategies files) | ok |
| 22:42:19 | `systemctl restart perpl-strategy-worker` (hosts no bots) | worker applied **migration v15**, seeded 8 strategies + 6 models (66 new model parameters) |
| 22:42 | MM slots gate `GET /mm/api/admin/slots` → `safeToRestart: true, running: 0` | gate passed |
| 22:42:45 | `systemctl restart perpl-terminal` (API) | up ~22:51 — startup waited ~8 min on a **pre-existing** unguarded lifespan `ALTER TABLE leader_trade_events MODIFY COLUMN symbol …` blocked by a metadata lock from a long `SELECT … FROM leader_trade_events ORDER BY trader_wallet` (219 s). Not v15; not changed (out of scope). |
| 22:47 | First model pass after restart — alignment tables BTC/ETH @ 22:45 candle, M1–M6 × {BTC, ETH} evaluated | 12 `strat_signals` rows with Mind fields |
| 23:00 | Second boundary — alignment tables @ 23:00, all 12 evaluations again | ok, no tracebacks |

## 3. Step-7 verification

| # | Check | Evidence |
|---|---|---|
| a | Alignment table logged per coin | worker journal: `alignment table BTC @ 2026-09-05 22:45`, `… ETH @ …` and again at 23:00 |
| b | ≥1 signal row per model within 30 min | `strat_signals WHERE model IS NOT NULL`: 12 rows at ts 1788648330800 (22:45 candle), 2 per model (BTC+ETH) — inside 5 min of the restart |
| c | Mind fields populated | `GET /api/strategies/m2_bos_order_block` BTC latest: conviction 0.1013 (raw 0.1447), tier `none`, 10 reasons with strength/weight/contribution, veto `no_setup` hit, 5 multipliers (session ×0.7), thesis text, day_type `range`, session `dead`, `setup {}`; every other model returns the same shape |
| d | Paper orders carry model tag / expected_hold_min / r_multiple | column + contract + UI path in place; **no fire yet** (all 12 evaluations `size_tier none` — see §4), so no `strat_trades` model row exists to show |
| e | UI both themes, no console errors | Playwright (Chromium, 1440×1000) against prod, `perpl-design-theme` light and dark: `/strategies`, `/strategies/M1`…`/M6` (+ Breakdown tab click), `/settings` — **0 console errors, 0 page errors, 0 failed requests** on all 16 loads; Model badge, MindPanel and Breakdown weights render (screenshots in the session scratchpad). The model-alerts card sits inside the Telegram section, which needs a connected wallet (D-40) — not reachable headless; the endpoint is verified (`GET /alerts/prefs` → 422 unauth; PUT/GET shape exercised by unit tests). |
| f | ≥1 Telegram model alert delivered | `strat_telegram_outbox` ids 590–593, kind `M1_pre`, `sent=1`, sent_ts 22:51:31 UTC, delivered to the 3 linked chats via `telegram_queue`: `[M1] BTC sweep in progress: wick 1.29 ATR above asia_high 79,691.0 — awaiting reclaim (≤3 …`, `[M1] BTC … london_high 79,731.0`, `[M1] ETH … asia_high 2,456.1`, `[M1] ETH … london_high 2,461.3` |

Worker journal since the restart: no tracebacks (the only one is the old process's aiomysql `__del__` "Event loop is closed" at shutdown). API log: only the pre-existing patterns (`Error in REST poll loop` on Perpl context timeouts; `_orderbook_refresh_loop` "dictionary changed size during iteration" at `main.py:1196`) — both present before this deploy, neither touched.

## 4. Where the models stand right now (22:45 / 23:00 UTC evaluations)

| Model | BTC | ETH |
|---|---|---|
| M1 | 0.00 none — no reclaimed sweep of an eligible level on this candle (sweeps of asia/london highs in progress, awaiting reclaim → the four `M1_pre` alerts) | same |
| M2 | 0.10 none — 4h trend `range`, no fresh 4h CHoCH confirmed by 1h BOS | 0.11 none — zone from the 2,484.7 break already broken/filled; 4h up, no fresh CHoCH down |
| M3 | 0.00 none — price in the discount of the 4h range (0.32), no failed-high short; no SFP + retest at a pre-session low | same (0.44) |
| M4 | 0.00 none — no 4h CHoCH in the last 72h | same |
| M5 | 0.00 none — outside London 07–09 / NY 13–15 UTC windows (next: 07:15 UTC first window candle) | same |
| M6 | 0.00 none — 1h reclaim 09-03 10:59 older than the entry window; no 1h close below weekly open 77,660 for a short | same (reclaim 09-03 12:59; weekly open 2,417.5) |

Thresholds are the documented ones and were not lowered. If nothing fires by ~04:45 UTC (6 h after
the first evaluation) `FIRST-6H-REPORT.md` is written per doc 17 step 8.

## 5. Known limits / notes for the owner

- `day_type` cannot say `no_trade` until ≥5 days of 15m history exist for the 2h-volume ratio (D-24).
- Liquidation clusters use the analytics cohort's positions (`analytics_positions`) and the partial-coverage `strat_liquidations` feed (D-16/D-17) — the same subset the 01–06 strategies see.
- Weekly learner runs but only proposes (logged as `mind-learn:proposed`) until the 60-day gate; weights stay at the doc defaults.
- `POST /{model}/backtest` is 404 by design (D-39).
- Unrelated pre-existing prod issues seen during the deploy, not changed: unguarded `leader_trade_events` ALTER in the API lifespan (delays restart under load); `_orderbook_refresh_loop` iteration traceback.
