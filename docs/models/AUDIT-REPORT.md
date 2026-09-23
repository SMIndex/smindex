# M1–M6 audit against docs 10–16 — AUDIT-REPORT

Audit date: 2026-09-06 (UTC). Code audited: `backend/app/strategy_engine/` (models `strategies/m1…m6`, `strategies/model_base.py`,
`mind/`, `structure/`, `model_runner.py`, `execution/`). Docs: `docs/models/10-…16-…md` (verbatim copies, D-49).
Every change made by the audit is recorded in `docs/models/DECISIONS.md` as D-42 … D-63 (D-55 = examined, kept; D-61 = finding, not changed).
No threshold, weight, multiplier or veto DEFINITION from the docs was changed; strategies 01–06 were not touched.

Conventions: "sequence miss" = `find_setup` returns no setup → the Mind is still evaluated with all reasons 0 and an extra
`no_setup` veto (so every 15m boundary leaves an evaluation row). "graded" = a doc step implemented as a Reason (0..1) rather
than a hard gate — used only where the doc itself lists the step under Reasons or gives no gate tolerance.

Test evidence: `backend/tests/strategy_engine/` — 252 tests pass (`python -m pytest tests/strategy_engine -q`); the
model-specific files are `test_models_m1_m6.py` (fixtures), `test_models_mirror.py` (Part 2), `test_models_audit_wiring.py`
(Parts 6–7 + D-52/D-60), `test_mind.py`, `test_model_runner.py`, `test_paper.py`.

---
## Part 1 — Spec conformance per model

Six tables, one per model (doc item · doc value · implemented value · match · fix). File:line references are to the
audited tree at the time of the review; the "fix applied" column names the DECISIONS entry. Items marked **no** with no fix
are recorded interpretations/gaps (listed again in §1.7 with the reason they were not changed).

**Bias veto check (owner's specific question).** M1 DID contain an undocumented `bias_gate` veto: it blocked shorts when the
daily bias was up and longs when it was down (`_eligible_levels` returned "daily bias up against a short (sweep of X seen)").
Doc 11 defines `htf_bias` as a GRADED reason (1.0 / 0.5 / 0.2) and `trend_against` (day_type trend day against the trade) as
the only bias-related veto. The gate had been added during the build as a "safety" reading of doc 11's `htf_bias` reason (D-42); it double-counted
bias and killed every counter-daily short.
**Removed (D-42).** On prod (old code) it blocked 4 of 62 M1 evaluations in the first 7.5 h (three `4h_fvg_top`/`4h_ob_top`
sweeps and one `london_high` sweep, all shorts against an up daily bias) — see Part 3. No other model carried a bias veto;
M2/M3/M4/M5/M6 bias handling is graded (`htf_agree`, `day_type_fit`, `bias_alignment`, `htf_bias`) plus the documented
day_type vetoes (`day_type_against`, `trend_day_against`, `trend_day_with_raid`, `daily_strong_against/_down`).

### 1.1 M1 Sweep and Reclaim (doc 11)

#### M1 Sweep and Reclaim — spec conformance (doc 11 vs `m1_sweep_reclaim.py`)

File abbreviations: **M1** = `backend/app/strategy_engine/strategies/m1_sweep_reclaim.py`, **mb** = `strategies/model_base.py`, **mind** = `mind/base.py`, **snap** = `mind/snapshot.py`, **run** = `model_runner.py`, **sw** = `structure/sweeps.py`.

| doc item | doc value | implemented value | match | fix applied |
|---|---|---|---|---|
| **Reason `location`** | 1.0 PWL / 4h zone edge / 4h equal lows; 0.8 PDL / 1h equal lows; 0.6 Asia or London low; 0.7 liq cluster; +0.2 if two level types within 0.2 ATR; cap 1; w 2.0 | `mb.level_candidates` mb:228–252 (pwl 1.0, pdl 0.8, asia/london 0.6, equal_lows 4h 1.0 / 1h 0.8, 4h bullish OB/FVG bottom 1.0 with status fresh/tested/open/half_filled, liq cluster ≥0.15% OI 0.7); `+coincident_bonus` (0.2 within 0.2 ATR, different type) mb:209–216; `min(1.0, …)` M1:227; w 2.0 M1:65 | yes | — |
| **Reason `reclaim`** | clip((rq−0.4)/0.4,0,1) ×0.7 if reclaim took 3 candles; w 2.0 | `mb.reclaim_strength` mb:151–157: `clip((rq-0.4)/0.4)`, `*=0.7` when `candles_to_reclaim >= 3`; rq = reclaim candle body/range sw:68; w 2.0 M1:66–67 | yes | — |
| **Reason `fuel`** | clip(long_liq_notional_**5m** / 0.10% of coin OI, 0, 1); w 1.5 (§7 prompt says "in the sweep window") | `snap.liq(direction, wick_open, reclaim_close)` M1:177–180; `clip(fuel_notional / snap.oi_frac(0.10))` M1:236 (0 when OI unknown, snap:156–159); w 1.5 M1:68 | no (window = wick-open→reclaim-close, not 5m; §7 wording matches code) | — |
| **Reason `cleared`** | clip(−OI_change_during_sweep_pct / 1.0, 0, 1); w 1.2 | `oi_sweep = snap.oi_change(wick_open, reclaim_close)` M1:181; `clip(-(oi_sweep)*100/1.0)` M1:238; w 1.2 M1:69 | yes | — |
| **Reason `delta_flip`** | clip(taker_buy_ratio_reclaim − 0.5, 0, 0.25)/0.25; w 1.5; mirror for shorts | `clip(sgn(direction)*((br or 0.5)−0.5), 0, 0.25)/0.25` M1:241, br = `snap.taker_candle(rec_c)["ratio"]` M1:183–184; w 1.5 M1:70 | yes | D-50 |
| **Reason `absorption`** | 1.0 if CVD low < prior CVD low while price low not >0.2 ATR beyond prior low; 0.5 if CVD flat; else 0; w 1.0 | prior = last confirmed 15m swing low before wick M1:188–191; `cvd_lower_extreme` (CVD[wick] < CVD[prior]) mb:314–323 and `beyond <= 0.2*atr` → 1.0 M1:195–198; "flat" = \|ΔCVD\| ≤ 0.1 × max\|CVD\| → 0.5 M1:199–203; w 1.0 M1:71 | yes (flat threshold 0.1×max\|CVD\| is an implementation choice not in doc) | — |
| **Reason `discount`** | clip((0.5 − range_4h.pct)/0.5, 0, 1); w 1.2 | `clip((0.5-pct)/0.5)` long / `clip((pct-0.5)/0.5)` short M1:207; pct from `_range_for` (4h, or 1h when 4h > 6 ATR(4h)) M1:31–37, 204–205; w 1.2 M1:72 | yes | — |
| **Reason `session`** | 1.0 first 120 min London/NY; 0.6 rest; 0.3 Asia; 0.1 dead; w 0.8 | `snap.session_score()` snap:239–243 (identical table); w 0.8 M1:73 | yes | — |
| **Reason `cohort`** | clip(cohort_fresh_adds_same_dir_60m / 3, 0, 1); w 1.0 | `clip(s.cohort_fresh_adds(direction)/3.0)` M1:74–75, snap:209–210 (`fresh_long`/`fresh_short`); w 1.0 | yes (60m window lives in the cohort feed, not verified here) | — |
| **Reason `htf_bias`** | 1.0 daily up & trend_4h up/range; 0.6 daily neutral; 0.3 daily down & trend_4h range; w 1.0 | M1:217–226: `daily==want and t4 in (want,"range")`→1.0; `neutral`→0.6; `daily==opposite and t4=="range"`→0.3; else 0.0; w 1.0 M1:76 | yes | — |
| **Veto `too_deep`** | sweep depth > 0.5 ATR | `Sweep.too_deep = depth_atr > MAX_DEPTH_ATR(0.5)` sw:9,53,63 (extreme tracked across candles before reclaim); veto M1:79 | yes | — |
| **Veto `third_sweep`** | level swept ≥2 times today already | `sweeps_before_today = max(0, sweep_count_today − 1)` M1:143–145 (UTC-day wick count over 100-candle window, sw:77–79); veto `>= 2` M1:80 | yes | — |
| **Veto `second_close_below`** | second 15m close below level after reclaim, before entry filled | Not a Mind veto (needs pending order); runner: `fallback_entry.cancel_level = setup.level` M1:299–300 → `_pending` cancels when a closed 15m after `placed_ts` prints through the level, reason `cancelled:second_close_below/above` run:514–524 | yes | D-43 |
| **Veto `trend_against`** | day_type trend_down (longs) | `day_type == ("trend_down" if long else "trend_up")` M1:81–82 | yes | — |
| **Veto `event_30m`** | macro event within 30 min either side | `mb.event_30m_veto` → `snap.s.event_within_30m` mb:174–175; `abs(t−now) <= 30min` alignment.py:116 | yes | — |
| **Veto `cluster_below_uncleared`** | long liq cluster ≥50% of swept cluster within 1 ATR below wick, **not reduced by the sweep** | M1:209–216: same-side cluster with `0 < (wick − c.level) <= 1.0*atr` and `c.notional >= 0.5*swept_notional`, only when swept level was itself a cluster (`swept_notional > 0`); "not reduced" is not evaluated — presence in current `snap.s.clusters` is the proxy | no (partial) | — |
| **Veto `funding_extreme_same_side`** | crowding EXTREME on the trade's side | `mb.funding_extreme_veto` mb:178–182 → `crowding_level=="EXTREME" and blocked_direction==direction` snap:176–178; M1:86 | yes | — |
| **Multiplier day_type** | range 1.1; trend same dir 1.0; squeeze against sweep dir 1.1; event 0.7; no_trade 0.6 | `day_type_rule({"range":1.1,"event":0.7,"no_trade":0.6}, fn=_day_type_mult)` M1:89; fn: same-dir trend → 1.0, squeeze with funding_z against direction → 1.1, squeeze otherwise 1.0 M1:104–116; other → table default 1.0 mb:185–194 | yes ("against sweep direction" judged by funding_z sign) | — |
| **Multiplier session** | 1.1 / 1.0 / 0.8 / 0.7 | `snap.session_mult()` snap:245–248 (1.1 first 120 min L/NY, 1.0 rest, 0.8 Asia, 0.7 dead); M1:90 | yes | — |
| **Multiplier sweep_count_today** | 0 → 1.0; 1 → 0.8 | `0.8 if sweeps_before_today >= 1 else 1.0` M1:91 | yes | — |
| **Multiplier recent_form** | 3 consecutive M1 losses 0.8; 5 consecutive wins 0.9 | `mb.form_rule(3, 5, 0.8, 0.9)` M1:92, mb:160–167 via `snap.consecutive` | yes | — |
| **Multiplier event_2h** | 0.8 | `mb.event_2h_rule(0.8)` M1:93, mb:170–171 (`event_within_2h`) | yes | — |
| **In-trade `no_higher_low`** (count 1) | two closed 15m since entry without a higher low above the sweep wick | `mb.check_no_higher_low` (ref = `setup.wick_price`, `higher_low_since` needs ≥2 closed candles, True when some candle low > wick AND ≥ previous low) mb:286–300, 449–453; count 1 M1:96 | yes | — (D-25 semantics) |
| **In-trade `oi_bleeding`** (count 1) | OI down ≥1.0% since entry | `check_oi_bleeding(0.01)` → `oi_change(fill_ts) <= -0.01` mb:456–461; M1:97 | yes | — |
| **In-trade `delta_negative`** (count 1) | taker delta negative on last 2 candles | `check_delta_negative`: last 2 closed 15m both `delta < 0` (long) / `> 0` (short) mb:464–474; M1:98 | yes | — |
| **In-trade `level_lost`** (count 2) | 15m close back below the level | `check_close_beyond_level("level")`: last closed 15m (after fill) `c.c < level` long / `> level` short mb:477–488; count 2 M1:99 | yes | — |
| **exit_threshold** | 2 (level_lost alone counts as 2) | `EXIT_THRESHOLD = 2` mind:10, `Mind.exit_threshold` mind:146 (config `exit_threshold` may override); counts summed mind:213–220 | yes | — |
| **Entry rule** | post-only at 50% of reclaim candle, valid 3 candles | `entry = (reclaim_high+reclaim_low)/2` M1:270; `valid_until = reclaim_ts + 3×15m` M1:25,296; paper fill via `limit_fills_through` run:505 | yes | — |
| **Entry second attempt** | if missed and a 15m FVG was left by reclaim, place at FVG mid for **3 more candles**, then skip | run:533–551: on expiry, `_reclaim_fvg` (15m FVG, same direction, live, created reclaim_ts−1 … +3 candles) run:555–562; new order rests `M1_FALLBACK_WAIT_CANDLES = 6` run:62,544; no FVG → `cancelled:entry_expired_no_fvg` | deviation | D-30 |
| **Stop rule** | sweep wick low − 0.15 ATR (mirror short) | `wick_price ∓ 0.15*atr(15m)` M1:271–274 | yes | — |
| **T1 rule** | nearest pool above or 1.5R, whichever first | `nearest_pool_beyond(min_dist=0.2 ATR)` M1:278; `t1 = min(pool, 1.5R)` long / `max` short, else 1.5R M1:279–283 | yes (0.2-ATR min pool distance not in doc) | — |
| **T2 rule** | next 4h supply zone bottom or top of 4h range | nearest live 4h zone above; if bearish → `z.bottom`; else `range_4h.high`; else 3R M1:285–293; if T2 not beyond T1 → `max(3R, 1.5×T1dist)` M1:294–295; T2 fill closes the remainder (`TRAIL_REPLACES_T2 = {M2,M5}` excludes M1) run:59,621–637 | yes (3R fallbacks not in doc) | — |
| **Partial percent** | close 40% at T1 | `ModelIntent(..., partial_pct=40.0)` M1:298; runner `size0 × partial_pct/100` run:607 | yes | — |
| **Breakeven rule** | stop to breakeven at T1 | `stop = entry; be_done=True` run:611–612 | yes | — |
| **Trail rule** | trail remaining behind each new 15m higher low once T2 within 0.5 ATR | `trail_after="near_t2"`, `trail_tf="15m"` M1:298; armed when `abs(t2 − mid) <= 0.5×ATR(15m)` run:654–658; `_trail_stop` = last confirmed 15m swing low after fill if above current stop run:689–701 | yes | — (D-11) |
| **expected_hold_min** | 90 | `HOLD_MIN = 90` M1:26,58 | yes | — |
| **Time stop (dead trade)** | 135 min = 1.5 × expected hold via universal dead-trade check | `Mind.manage`: `minutes_in_trade >= 1.5 × expected_hold_min and progress < 0.5R` mind:11,227–228 | yes | — |
| **Hard time stop** | 4 hours / 240 min | `HARD_STOP_MIN = 240` M1:27,59; `hard_stop_ts = fill + hard_stop_min` run:567–568; closes `time_stop` run:602–603 | yes | — |
| **One-attempt rule** | none in doc | none (no gate); `state_updates` bumps an informational `attempts` counter M1:317–318 | yes | D-42 (MAX_PER_DAY=2 removed) |
| **§2 eligible levels list** | PDL, PWL, Asia low, London low, equal lows 1h/4h, bottom of fresh/tested 4h demand OB/FVG, long liq cluster ≥0.15% OI (mirror shorts) | `mb.level_candidates` mb:219–253 (all 7 types; cluster `>= 0.0015 × OI`, below price for longs) | yes | — |
| **§2 level freeze** | levels frozen at 15m open; a level created by the current candle does not count | `_eligible_levels` drops `asia_*` in Asia and `london_*` in Asia/London sessions M1:40–51; levels come from the closed-candle structure bundle | deviation | D-42 (recorded) |
| **§3 step 1a alignment: daily bias** | daily_bias up or neutral | not gated; graded only via `htf_bias` (0.3 tier reachable) M1:161–164 | deviation | D-42 (bias_gate removed) |
| **§3 step 1b alignment: discount** | price in discount of 4h range (1h range if 4h range > 6 ATR) | hard sequence gate `if not best["in_discount"]: return None` M1:165–168; `pct < 0.5` long / `> 0.5` short M1:206; `_range_for` switches to 1h when `range_4h.width > 6 × ATR(4h)` M1:31–37 | deviation (ATR tf = 4h) | D-26 |
| **§3 step 2 sweep** | 15m wick below level by 0.1–0.5 ATR(15m) | `detect_sweeps(min_depth=0.1)` sw:8,35,50; >0.5 flagged `too_deep` → veto (not filtered out of the sequence) sw:53; ATR = `snap.atr("15m")` M1:121,136 | yes | — |
| **§3 step 3 reclaim** | within 3 candles a 15m close back above level; reclaim_quality ≥ 0.5 | `MAX_WAIT = 3` sw:10,56 (wick candle counts as candle 1); `RQ_MIN = 0.5` M1:24,140; setup fires only when the reclaim candle is the latest closed candle (`latest_reclaimed`) M1:137, sw:82–87 | yes | — |
| **§3 step 4 sweep-candle data** | long liq fills spiked; OI fell; CVD low not confirmed by price | graded reasons `fuel` / `cleared` / `absorption` (M1:236,238,242), no hard gate | deviation | D-42 (D-42 text names `delta_reversal`/`oi_flush`; actual M1 keys are fuel/cleared/absorption/delta_flip) |
| **§3 step 5a reclaim-candle taker delta positive** | taker delta positive | graded reason `delta_flip` M1:241 | deviation | D-42 |
| **§3 step 5b reclaim-candle OI flat or rising** | OI flat or rising | `oi_change_reclaim` computed M1:182 and stored M1:239 but consumed by NO reason, veto or multiplier | MISSING (not graded, not gated) | — |
| **§3 step 6 Mind evaluates, place entry** | Mind evaluate → take → entry | `ModelStrategy.evaluate` mb:398–443 → `mind.evaluate` mind:161–204 → `build_intent` M1:267–301 | yes | — |
| **§5.6 thesis template** | "M1 long {coin} at {level_type} {level}. Sweep {depth} ATR with {fuel_notional} liquidated and OI {oi_change}. Reclaim quality {rq}. Strongest: {top3}. Wrong if … Expect {t1} within 90 min." | M1:303–310, mirror wording below/above | yes | — |
| **§6 alerts** | pre-alert on sweep detected; take w/ thesis; skip w/ vetoes; exit w/ reason + R | `pre_alerts` (`M1_pre`, wick >0.1 ATR beyond level and close still beyond, max 2) M1:249–264; `M1_detected`/`M1_take`/`M1_skip` mb:415–441; exit alerts in runner `_close` | yes | — |
| **Not in doc: `Mind.manage` 2-consecutive-candle rule** | — | exit `thesis_failed` only when `count >= exit_threshold` on 2 consecutive closed 15m (`fail_streak >= 2`) mind:221–226 | not in doc (doc 10 rule) | — |
| **Not in doc: `no_setup` synthetic veto** | — | every closed 15m without a setup still evaluates the Mind with veto `no_setup: <missing>` mb:401–411 | not in doc (D-09) | — |
| **Not in doc: pool min distance 0.2 ATR for T1** | — | `min_dist=0.2*atr` M1:278 | not in doc | — |
| **Not in doc: T2 fallbacks** | — | 3R when no zone/range; `max(3R, 1.5×T1dist)` when T2 not beyond T1 M1:293–295 | not in doc | — |
| **Not in doc: level selection when several levels swept** | — | highest `location_base` per direction M1:149–150; best direction by `location_strength` M1:157–158 | not in doc | — |
| **Not in doc: `attempts` state counter** | — | `bump_attempts(model_state, "attempts")` on take M1:317–318 (informational only) | not in doc | — |
| **Not in doc: `absorption` flat threshold** | — | 0.5 when \|CVD[wick] − CVD[prior]\| ≤ 0.1 × max\|CVD\| M1:200–203 | not in doc | — |
| **Not in doc: warm-up gate** | — | needs ≥30 closed 15m and ATR>0 M1:122–123, 252–253 | not in doc | — |

#### Residual mismatches not covered by D-42 / D-43 / D-50 / D-30 (candidate defects)

- **Step 5b "OI flat or rising" on the reclaim candle has no consumer.** Doc §3.5: "Data on the reclaim candle: taker delta positive; OI flat or rising." Code computes `oi_reclaim = snap.oi_change(rec_c.ts - TF_MS["15m"], rec_c.ts)` (M1:182) and stores it as `oi_change_reclaim` (M1:239), but no reason, veto or multiplier reads it. D-42 covers steps 4–5 as "graded reasons", yet nothing grades reclaim-candle OI.
- **`fuel` window differs from §5.2.** Doc §5.2: `clip(long_liq_notional_5m / (0.10 percent of coin OI), 0, 1)`; code sums liquidations from wick-candle open to reclaim-candle close (M1:177–180), i.e. 15–45 min, not 5 min. The §7 prompt says "in the sweep window", so the doc contradicts itself; the table row is flagged `no` against §5.2 — needs an owner ruling on which text governs.
- **`cluster_below_uncleared` does not test "was not reduced by the sweep".** Doc §5.3: "…sits within 1 ATR below the sweep wick and was not reduced by the sweep." Code (M1:211–216) only checks that a same-side cluster ≥50% of the swept cluster's notional currently exists within 1 ATR beyond the wick; there is no before/after comparison of that cluster's notional. Also the veto is inert unless the swept level was itself a liquidation cluster (`swept_notional > 0`, from `top["meta"]["notional"]` — only set for `liq_cluster_*` levels, mb:250–252), which follows the doc's "of the swept cluster" literally but means a PDL sweep with a big uncleared cluster just below is never vetoed.
- **T2 is a hard exit, doc reads as trail-through.** Doc §4: "T2: next 4h supply zone bottom or top of the 4h range. Trail remaining behind each new 15m higher low after T2 is within 0.5 ATR." Runner: `TRAIL_REPLACES_T2 = {"M2", "M5"}` (run:59) so for M1 `t2_is_exit` is True and a touch of T2 closes the remainder at T2 (run:621–637); the near-T2 trail (run:654–658) only matters if price stalls inside 0.5 ATR without touching T2. Whether the remainder should instead ride the trail past T2 is a doc ambiguity worth a DECISIONS entry.
- **`_range_for` 6-ATR test uses ATR(4h); doc tf unspecified.** Doc §3.1 "if the 4h range is wider than 6 ATR" (every other ATR in doc 11 is ATR(15m)). Code M1:34–35 uses `snap.atr("4h")`. D-26 records the 1h-range fallback but is the only place the 4h ATR unit is stated — flagged as `deviation` above; listing here so the unit choice is explicitly owner-confirmed rather than inferred.
- **Minor: D-42 text names the wrong reason keys.** D-42 says steps 4–5 are graded by `delta_reversal` / `oi_flush`; M1's actual keys are `fuel`, `cleared`, `absorption`, `delta_flip` (M1:68–71). Doc-only inconsistency in DECISIONS.md, no code effect.

### 1.2 M2 Break of Structure + Order Block (doc 12)

#### M2 spec-conformance table

Files: doc `E:\wamp64\www\perpl\docs\models\12-M2-break-of-structure-order-block.md`; code `E:\wamp64\www\perpl\backend\app\strategy_engine\strategies\m2_bos_order_block.py` (line numbers below refer to this file unless prefixed `mb:` = `model_base.py`, `base:` = `mind/base.py`, `runner:` = `model_runner.py`, `cfg:` = `mind/config.py`).

| doc item | doc value | implemented value | match | fix applied |
|---|---|---|---|---|
| **Reason** `oi_new_positioning` | clip(OI_change_break_pct / 2.0, 0, 1) · w 2.0 | `clip((oi_break or 0)*100/2.0)` · w 2.0 (L52-53); `oi_break = snap.oi_change(t0, t1)` over displacement window (L188-190) | yes | — |
| Reason `displacement` | displacement_grade · w 1.8 | `S("displacement_grade")` = `disp.grade` from `detect(c1h, atr_at, i)` (L54, L168, L295) · w 1.8 | yes | — |
| Reason `zone_discount` | clip((0.5 − zone_pct_of_new_range)/0.5, 0, 1) · w 1.5 | `clip((0.5 - zone_pct)/0.5)` (L55-56); zone_pct = zone MID position in new range, `1 - pct` for shorts (L217-220) · w 1.5 | yes | — |
| Reason `retrace_calm` | 1.0 if max retrace range ≤ 0.7 ATR and volume falling; 0.6 if ≤ 1.0 ATR; 0 otherwise · w 1.2 | `rmax <= 0.7*a15 and (vol_disp <= 0 or vol_retr < vol_disp)` → 1.0; `rmax <= 1.0*a15` → 0.6; else 0 (L254-263); ATR = 15m; volume = avg 15m vol in retrace vs avg 15m vol in displacement window · w 1.2 (L57) | yes | — |
| Reason `oi_holding` | clip(1 + OI_change_since_break_pct / 1.0, 0, 1) · w 1.5 | `clip(1.0 + (oi_since_break or 0)*100/1.0)` (L58-59); None → 1.0 · w 1.5 | yes | D-44 (None → 1.0 recorded, not changed) |
| Reason `delta_break` | clip((taker_buy_ratio_break − 0.5)/0.25, 0, 1) · w 1.2 | `clip(sgn(direction) * (br_break or 0.5 − 0.5)/0.25)` (L61-63); br_break = `snap.taker(t0,t1)["ratio"]` (L191-192) · w 1.2 | yes | D-44(a) short mirror |
| Reason `funding_young` | clip((1.5 − funding_z)/1.5, 0, 1) · w 0.8 | `clip((FZ_MAX − sgn(direction)*funding_z)/FZ_MAX)`, FZ_MAX 1.5; None → strength 1.0 (L64-66, L32) · w 0.8 | yes | D-44(a) short mirror |
| Reason `htf_agree` | 1.0 daily up & trend_4h up; 0.7 daily neutral & trend_4h up; 0.4 fresh 4h CHoCH only · w 1.2 | 1.0 / 0.7 / 0.4 (`fresh_choch`) / else 0.0 (L275-283; L67) · w 1.2 | yes | D-44(c) undocumented 0.5 tier removed |
| Reason `nested_sweep` | 1.0 if a 15m sweep of a minor low inside the zone with reclaim_quality ≥ 0.5; else 0 · w 1.5 | 15m swing low with `zone_bottom <= price <= zone_top`, `ts > disp.ts`, `detect_sweeps(retr, …)` any `reclaimed and reclaim_quality >= 0.5` → 1.0 (L264-271; L68) · w 1.5 | yes | — |
| Reason `cluster_cleared` | clip(short_liq_notional_break / (0.10% of OI), 0, 1) · w 0.8 | `snap.liq(opposite(direction), t0, t1)` notional / `snap.oi_frac(0.10)` (=0.10% OI), 0 when OI unknown (L272-274, L300; L69) · w 0.8 | yes | — |
| **Veto** `short_covering` | OI change across the break ≤ 0. Hand to M3 logic (log only) | `oi_break is not None and oi_break <= 0` (L72-73). No M3 handoff; veto only logged in `vetoes_hit` | yes (handoff = log only) | — |
| Veto `oi_exit_retrace` | OI down ≥ 2.0% since the break | `oi_since_break <= -0.02` (L74-75); `oi_since = snap.oi_change(disp.ts)` (L243) | yes | — |
| Veto `displacement_against` | candle range ≥ 1.5 ATR against the trade during retrace | `any_against(retr, a15, direction)` default `min_range_atr=1.5`, closing against (L242, L76-77; displacement.py L65-72) | yes | — |
| Veto `zone_premium` | zone entirely above 50% of new range | long: `(zone_bottom − lo)/width > 0.5`; short: `(hi − zone_top)/width > 0.5` (L221, L78-79) | yes | — |
| Veto `late_day` | within 60 min of 00:00 UTC, or Friday after 20:00 UTC | `minutes_to_utc(now, 0) <= 60` (i.e. 23:00–00:00 only) `or (weekday==4 and utc_hour>=20)` (L80-81; mb:L343-349) | yes (see residual #1) | — |
| Veto `event_30m` | `event_30m` | `mb.event_30m_veto()` → `snap.s.event_within_30m` (L82; mb:L174-175) | yes | — |
| Veto `day_type_against` | trend_down for longs | `day_type == ("trend_down" if long else "trend_up")` (L83-84) | yes | — |
| Veto `stop_too_wide` | stop distance > 1.2 ATR | `stop_atr > STOP_MAX_ATR` (1.2); `stop_atr = |entry − stop| / a15` (L85-86, L33, L285) | yes | — |
| **Multiplier** day_type trend same direction | 1.15 | `_day_type_mult`: 1.15 when `day_type == trend_up (long) / trend_down (short)` (L105-109) | yes | — |
| Multiplier day_type range | 0.9 (only allowed if break is 4h) | 0.9 unconditionally (L110-113); reachability gated by sequence L182-186 | yes | D-44(b),(d) |
| Multiplier day_type squeeze same direction | 1.0 | 1.0 for any squeeze (direction not checked; neutral value) (L114-115) | yes | — |
| Multiplier day_type event | 0.7 | table `{"event": 0.7}` (L89; mb:L185-194) | yes | — |
| Multiplier day_type no_trade | 0.6 | table `{"no_trade": 0.6}` (L89) | yes | — |
| Multiplier session | London/NY 1.0; Asia 0.8; dead 0.7 | `session_rule({"london":1.0,"newyork":1.0,"asia":0.8,"dead":0.7})` (L90; mb:L197-202) | yes | — |
| Multiplier zone status | fresh 1.0; tested once 0.85 | `0.85 if zone_tested else 1.0` (L91); `zone_tested = touches > 1`, touches = 1h candles after OB creation with `l <= ob.top` (L286, L294) | deviation | D-44 (zone_tested = touches>1 recorded interpretation) |
| Multiplier recent_form | 3 losses 0.8; 5 wins 0.9 | `form_rule(3, 5, 0.8, 0.9)` (L92; mb:L160-167) | yes | — |
| Multiplier event_2h | 0.8 | `event_2h_rule(0.8)` (L93; mb:L170-171) | yes | — |
| **In-trade check** `below_ob` (counts 2) | 15m close below OB bottom; counts as 2 | `check_close_beyond_level("ob_edge")`, count 2 (L96; mb:L477-488); `ob_edge = ob.bottom` long / `ob.top` short (L293) | yes | — |
| In-trade check `oi_dropping` (1) | OI down ≥ 1.5% since entry | `check_oi_bleeding(0.015)`: `oi_change(fill_ts) <= -0.015` (L97; mb:L456-461) | yes | — |
| In-trade check `no_progress` (1) | after 4 closed candles no higher high than entry candle | `check_no_progress(4, "entry_candle_high")` (L98-99; mb:L491-505); `entry_candle_high` = fill 15m candle's high/low (runner:L648-652) | yes | D-36 (entry_candle_high convention) |
| In-trade check `delta_selling` (1) | taker buy ratio < 0.4 on last 2 candles | last 2 closed 15m `ratio < 0.4` (long) / `> 0.6` (short) (L100, L119-128) | yes | — |
| exit_threshold | 2 | `cfg exit_threshold: 2` (cfg:L20; base:L146); exit when count ≥ 2 on 2 consecutive 15m closes (base:L221-226) | yes | — |
| **Entry rule** | post-only at FVG mid, or OB top if no FVG; valid until fill or 15m close below the OB | `entry = fvg.mid` (L214); FVG required — no OB-top fallback (L175-176); `entry_valid_until_ms=0` (no expiry) + `fallback_entry={"type":"cancel_beyond","level":ob_bottom/ob_top}` (L313-317); runner cancels on closed 15m beyond level (runner:L514-524), no time expiry when 0 (runner:L533-534) | deviation (OB-top fallback not implemented) | D-44(f), D-36; FVG-required recorded D-44 |
| **Stop rule** | OB bottom − 0.15 ATR; skip if stop distance > 1.2 ATR | `ob.bottom − 0.15*a15` (long) / `ob.top + 0.15*a15` (short) (L284); skip via `stop_too_wide` veto (L85-86) | yes (ATR = 15m, doc TF unspecified) | — |
| **T1 rule** | displacement high; close 40%; stop → BE | `t1 = disp.high` (long) / `disp.low` (short) (L287, L302); partial 40.0 (L315); runner partial `partial_pct` then `stop = entry`, `be_done` (runner:L606-616) | yes | D-36 (BE universal) |
| **T2 rule** | next 1h pool above (equal highs, prior day high); on trend_up trail behind each new 15m higher low instead | `nearest_pool_beyond(snap, t1_px, direction, min_dist=0.2*a15)` over ALL pools (refs/eq any TF/liq clusters), fallback `entry + 2·|t1−entry|` (L288-289); `trail_after="t1"` when day_type is trend in trade direction else `"t2"` (L312); M2 in `TRAIL_REPLACES_T2` so armed trail disables T2 exit (runner:L59, L621-623); trail = last confirmed 15m swing low since fill (runner:L689-699) | yes (pool selection: see residual #3) | D-36, D-11; 2R fallback D-44 |
| **Partial percent** | 40% | `40.0` positional `partial_pct` (L315; mb:L44) | yes | — |
| **Breakeven rule** | stop to breakeven at T1 | runner:L611-612 `stop = entry; be_done` | yes | D-36 |
| **expected_hold_min** | 180 | `HOLD_MIN = 180` (L27, L45) | yes | — |
| **Dead-trade check** | 270 min | `Mind.manage`: `minutes_in_trade >= 1.5 × 180` and progress < 0.5R (base:L227-228; cfg:L21) = 270 | yes | — |
| **Hard time stop** | 8 hours / 480 min | `HARD_STOP_MIN = 480` (L28, L46); `hard_stop_ts = fill_ts + 480 min` (runner:L567-568, L602-603) | yes | — |
| **One-attempt rule** | none in doc | none enforced; `taken_zones` still written to state (L333-338) but never read | yes | D-44(d) gate removed |
| **Seq 1** Alignment | trend_4h up, or fresh CHoCH up on 4h confirmed by 1h BOS up within the last 8 hours | `st4.trend == want`; else last 4h CHoCH(want) with `now − ch.ts <= 48h` AND a 1h BOS(want) with `ch.ts <= e.ts <= ch.ts + 8h` (L147-160) | yes (8h window anchored to CHoCH, see residual #2) | D-44 (48h fresh recorded) |
| **Seq 2** Break | 1h displacement candle (or 15m 2-candle sequence) closes above most recent 1h swing high, leaving bullish FVG | most recent 1h BOS(want) within 24h (L162-164); `detect(c1h, atr_at, i)` must be a displacement in direction (1h single- or 2-candle) (L165-170); FVG and OB from 1h zones with `created_index` in `[disp.start_index−3, disp.end_index+1]`, both `live` (L171-180); 15m 2-candle path absent | deviation | D-44 (15m 2-candle NOT implemented, recorded; BREAK_LOOKBACK_H 24) |
| Seq 2b (from §1) range day only if break on 4h | range only if the break is on 4h | `break_is_4h` = a 4h swing (high for long) within 0.1·ATR(4h) of `bos.level`; range day without it → sequence miss (L182-186) | yes | D-44(d) |
| **Seq 3** Data on break: OI | OI up ≥ 1.0% across displacement window | `0 < oi_break < 0.01` → miss (L194-195); `oi_break <= 0` passes to `short_covering` veto; None passes | yes | — |
| Seq 3: taker ratio | ≥ 0.65 | long `br_break < 0.65` miss; short `br_break > 0.35` miss; None passes (L196-197, L31) | yes | — |
| Seq 3: funding z | < 1.5 | long `fz >= 1.5` miss; short `fz <= -1.5` miss; None passes (L198-199, L32) | yes | — |
| **Seq 4** Zone = OB + FVG, must sit in discount of new range (disp high → last 1h swing low) | zone in discount | new range: `hi = disp.high`, `lo = last 1h swing low with ts < disp start` (L201-212); zone = union OB∪FVG (L215-216); discount enforced ONLY via `zone_premium` veto (entirely above 50%, L221) and `zone_discount` reason; a straddling zone reaches the Mind | yes (see residual #4) | — |
| **Seq 5** Retrace to FVG mid / OB top; retrace candles ≤ 1.0 ATR each; no displacement against | as stated | retr = closed 15m after `disp.ts` (L223); `pulled >= 0.5` of the way from disp extreme to entry (L229-238); `rmax <= 1.0*a15` (L239-241, L34); any 15m close beyond OB → miss (L231-236); displacement-against computed as veto flag only (L242) | deviation | D-44 (`pulled >= 0.5` interpretation recorded) |
| **Seq 6** OI since break ≥ −0.5% | ≥ −0.5% | `-0.02 < oi_since < -0.005` → miss (L243-247); ≤ −2% left to veto | yes | D-44(e) |
| Seq 6: taker ratio 0.35–0.55 | between 0.35 and 0.55 | `snap.taker(disp.ts, now)` ratio in [0.35,0.55] (long) / [0.45,0.65] (short); None passes (L248-253) | yes | — |
| Seq 6: new long liq cluster remains above OB bottom | required | not implemented | MISSING | D-44 recorded feature gap (cluster formation time not stored) |
| **Seq 7** Mind evaluates | — | `ModelStrategy.evaluate` → `mind.evaluate(snap)` (mb:L398-443; base:L161-204) | yes | — |
| Thesis template §4.6 | "M2 long {coin} into {zone_type} … Expect {t1} within 3h." | L319-326, same fields; "below/above" and ob_bottom/ob_top mirrored for shorts | yes | — |
| Alerts | break detected / take / skip / exit | `detect_alert_kind="break detected"` + `alert_detected` (L47, L328-331); take/skip in mb:L415-441; exit in runner | yes | — |
| Day types allowed (§1) | trend in trade direction; range only if break on 4h | trend-against vetoed (L83-84); range gated (L182-186); squeeze/event/no_trade pass with multipliers | yes | — |
| Frequency (§1) | 0 to 2 per day | not enforced (descriptive; no counter) | yes (no rule in doc §2-§4) | — |
| **not in doc** warm-up gate | — | ≥30 closed 1h and 15m candles, ATR(1h/15m) > 0 (L134-135) | not in doc | — |
| not in doc BREAK_LOOKBACK_H | — | break older than 24h stale (L29, L163) | not in doc | D-44 recorded |
| not in doc CHoCH freshness | — | 48h (L154) | not in doc | D-44 recorded |
| not in doc zone `live` requirement | — | OB and FVG must be `live` (L179-180) | not in doc (consistent with §3 invalidation) | — |
| not in doc OB/FVG association window | — | `created_index ∈ [disp.start_index−3, disp.end_index+1]` (L172) | not in doc | — |
| not in doc 4h-level tolerance | — | 0.1·ATR(4h) around `bos.level` (L183-184) | not in doc | D-44(d) |
| not in doc T2 min distance | — | pool must be ≥ 0.2·ATR(15m) beyond T1 (L288) | not in doc | — |
| not in doc T2 fallback | — | 2R from entry (L289) | not in doc | D-44 recorded |
| not in doc `taken_zones` state | — | written on TAKE, unused (L181, L333-338) | not in doc | D-44(d) |
| not in doc `cohort_net_dir_entry` | — | stored in setup (L303), no M2 check reads it | not in doc | — |
| not in doc ATR timeframes | — | displacement detection uses 1h ATR at BOS index (L166-168); stop/retrace/stop-distance/T2-min-dist use 15m ATR (L240, L284-285, L288) | not in doc | — |
| not in doc `no_setup` synthetic veto | — | mb:L401-403 | not in doc | D-09 |
| not in doc missing-tape handling | — | `br_break`/`fz`/`br_r`/`oi_break`/`oi_since` None → sequence gates pass (L194-199, L246, L250); `delta_break` None → 0 (L63); `funding_young` None → 1.0 (L66) | not in doc | — |
| MISSING "OB top if there is no FVG" entry fallback | §3 | FVG required (L175-176) | MISSING | D-44 recorded (kept FVG-required; doc step 2 requires FVG) |
| MISSING 15m 2-candle displacement break | §2 step 2 | only 1h `detect` (L168) | MISSING | D-44 recorded |
| MISSING M3 handoff on `short_covering` | §4.3 "Hand the setup to M3 logic (log only)" | veto logged only, no handoff object/alert to M3 | MISSING (doc says log only — arguably satisfied) | — |

#### Residual candidate defects not covered by D-36 / D-44

1. **`late_day` covers only the pre-midnight side.** Doc §4.3: "current time within 60 minutes of 00:00 UTC". Code L81 `mb.minutes_to_utc(s.now_ms, 0) <= 60` (mb:L343-349 = minutes UNTIL the next 00:00) fires 23:00–00:00 but not 00:00–01:00. If "within 60 minutes of" is meant symmetrically, the post-midnight hour is unvetoed.
2. **Seq 1 "1h BOS up within the last 8 hours" anchored to the CHoCH, not to now.** Code L155 requires `ch.ts <= e.ts <= ch.ts + 8h`; the doc wording "within the last 8 hours" can be read as BOS within 8h of the current time. With CHoCH freshness at 48h (L154), a CHoCH 40h ago confirmed by a BOS 35h ago still qualifies as "fresh"; the doc's reading would not. D-44 records the 48h constant but not this window anchoring.
3. **T2 pool selection is not restricted to "1h pool (equal highs, prior day high)".** Doc §3 T2: "next 1h pool above (equal highs, prior day high)". Code L288 `mb.nearest_pool_beyond(...)` scans `snap.s.pools` (mb:L256-267), which `build_pools` fills with refs (pdh/pwh/daily_open/weekly_open/session highs), equal-highs pools of every TF, and `liq_cluster_*` pools (pools.py L141-150). Nearest of any of these ≥ 0.2 ATR(15m) beyond T1 becomes T2 — e.g. a liq cluster or `daily_open` can be T2.
4. **Seq 4 "Zone must sit in discount" is only enforced as the `zone_premium` veto (entirely above 50%).** Doc §2 step 4 states a sequence condition; code (L218-221) lets a zone whose bottom is below 50% but whose mid is above 50% through to the Mind with `zone_discount` = 0 and no veto. If the doc intends the whole zone (or its mid) to be in discount, a straddling zone is currently tradeable.
5. **`displacement_against` veto is unreachable given the sequence gate.** L239-241 returns a sequence miss when any retrace candle has range > 1.0 ATR(15m); `any_against` (L242) needs range ≥ 1.5 ATR on the same candles, so the veto (L76-77) can never fire on a completed setup. Doc has the same overlap (step 5 vs §4.3), so this is a doc/code consistency note rather than a wrong formula — same shape as the D-44(e) fix that kept `oi_exit_retrace` reachable.
6. **Seq 3 OI gate and `short_covering` veto: `oi_break` None passes silently.** L194 only rejects `0 < oi_break < 0.01`; L73 only vetoes when `oi_break is not None`. With no OI reading in the displacement window, both the ≥1% requirement and the veto are skipped and `oi_new_positioning` reads 0 (L53). D-44 records the analogous `oi_holding` None case but not this one.

### 1.3 M3 Failed Auction at a Range Extreme (doc 13)

**M3 spec-conformance table** — doc `docs/models/13-M3-failed-auction-range-extreme.md` vs `backend/app/strategy_engine/strategies/m3_failed_auction.py` (line refs are to that file unless prefixed `mb:` = model_base.py, `mind:` = mind/base.py, `run:` = model_runner.py, `snap:` = mind/snapshot.py).

| doc item | doc value | implemented value | match | fix applied |
|---|---|---|---|---|
| reason `trap` | clip(OI_change_1h_into_high_pct / 1.5, 0, 1), w 2.0 | `clip(oi_push_1h*100/1.5)` L48-49; `oi_push_1h = snap.oi_change(c.ts−1h, c.ts)` over the hour into the SFP candle close L232; w 2.0 | yes | — |
| reason `cvd_divergence` | clip((CVD_prior_high − CVD_new_high) / (0.5 × CVD_1h_range), 0, 1), w 2.0 | `diff = cvd[i_prior] − cvd[i]` (mirror for longs); `rng` = max−min of CVD at closes i−4..i (= 1h of CVD); `clip(diff/(0.5*rng))`, rng=0 → 1.0 if diff>0 L235-244; setup key `cvd_div_strength` L50; w 2.0 | yes | — |
| reason `location` | 1.0 4h supply / 4h equal highs / PWH; 0.8 PDH or 1h equal highs; 0.6 session high; +0.2 coincident types, cap 1; w 1.8 | `_levels` L132-160: 4h swing high 1.0 L140, 4h bearish zone 1.0 L143, equal_highs 4h 1.0 / 1h 0.8 L146, pwh 1.0 / pdh 0.8 L149-151, prior-session highs 0.6 L159; `+coincident_bonus` (other type within 0.2 ATR, mb:209-216) capped `min(1.0, …)` L290; w 1.8 L51 | deviation | D-27 records 4h swing high = 1.0 (doc table has no strength for the doc §2 "range high (4h swing high)" level); D-45(e) dead-session NY high |
| reason `failure_quality` | clip((prior_high − close_failure) / (0.5 ATR), 0, 1), w 1.2 | `clip((prior.price − c.c)/(0.5*a15))` L298 (prior swing extreme, not level); w 1.2 L52 | yes | D-45(b) |
| reason `thin_bids` | clip((1 − bid_depth_ratio_vs_1h_avg) / 0.5, 0, 1), w 1.0 | `depth_ratio = depth("bid_0_3")/depth_avg("bid_0_3", 1h)` L257-258 (snap:189-195); `clip((1−ratio)/0.5)`, None → 0 L259; w 1.0 L53 | yes | — |
| reason `cluster_fuel` | clip(long_cluster_notional_within_1.5ATR / (0.10% of OI), 0, 1), w 1.2 | largest long cluster with `0 < price − level ≤ 1.5 ATR` L260-265; `clip(cl_notional / oi_frac(0.10))`, OI unknown → 0 L266, L301; w 1.2 L54 | yes | — |
| reason `funding_up` | clip(funding_z / 2.0, 0, 1), w 0.8 | `clip(fz/2.0)` (short; `−fz` for long), None → 0 L252-253; w 0.8 L55 | yes | D-45(c) (removed undocumented ×0.5) |
| reason `premium` | clip((range_4h.pct − 0.5) / 0.5, 0, 1), w 1.0 | `clip((pct−0.5)/0.5)` short / `clip((0.5−pct)/0.5)` long, `pct = r4.pct(price)` L178, L303; w 1.0 L56 | yes | — |
| reason `second_failure` | 1.0 if this is the **second** swing failure at this level today; 0 otherwise; w 1.5 | `1.0 if prev_fail >= 1 else 0.0` L305 → 1.0 for the 2nd **and every later** failure; `prev_fail` from `attempts_today(failures:<lvl>)` minus the current SFP if already counted L272-277; w 1.5 L57 | no (minor: ≥2 vs =2) | D-45(d)/D-27 make the counter reachable |
| reason `day_type_fit` | 1.0 range; 0.5 trend_up inside 4h supply; 0 otherwise; w 1.0 | `1.0 if day_type=="range" else (0.5 if in_zone else 0.0)` L291 (setup gate L182 already restricts non-range to trend_up+in_zone); w 1.0 L58 | yes | — |
| veto `acceptance` | two consecutive 15m closes above the level with OI rising | `setup["acceptance"]` L61 ← `_accepted(window[i+1:], lvl)` L268: any consecutive pair both closing beyond `lvl` with `oi_change(open a → close b) > 0` L163-171 | yes | D-33 (surfaced as veto; lone close beyond = sequence miss L215-217) |
| veto `squeeze_risk` | funding z ≤ −1.5 or **short OI share** ≥ 60% | `fz <= −1.5` (long mirror `>= 1.5`) OR `share_against >= 0.6` where `share_against = cohort short_notional/(long+short)` L269-271 | no (share source = cohort notional, not OI) | — |
| veto `cohort_adding_longs` | cohort fresh long adds ≥ 2 in last 60 min | `cohort_fresh_adds(opposite(direction)) >= 2` L63-64; `fresh_long` = OPEN/INCREASE/FLIP flow events of trailing 60 min (cohort.py:197, 241-249; snap:209-210) | yes | — |
| veto `trend_day_early` | day_type trend_up and time before 15:00 UTC | `day_type == "trend_up"` (short; `trend_down` long) `and utc_hour(now) < 15` L65-66 | yes | — |
| veto `event_30m` | scheduled event within 30 min | `mb.event_30m_veto()` L67 → `snap.s.event_within_30m` mb:174-175 | yes | — |
| veto `first_failure_random_level` | level is an intraday swing that did not exist before this session | `bool(setup["level_this_session"])` L68, but the key is hardcoded `False` L307; `_levels` only returns pre-session levels (`confirmed_at/created_ts <= sess_start` L139, 142, 145, 147; prior sessions only L155-159) so an intra-session level is a **sequence miss** ("no … level predating this session" L188-189) — the veto can never fire | no (dead veto; enforced as sequence miss, not recorded in DECISIONS) | — |
| veto `funding_extreme_short_side` | crowding EXTREME on the short side | `mb.funding_extreme_veto(key, direction)` L69 → `crowding_level=="EXTREME" and blocked_direction==direction` snap:176-178 | yes | — |
| multiplier `day_type` | range 1.15; trend_up in supply 0.85; squeeze 0.5; event 0.7; no_trade 0.7 | `day_type_rule({"range":1.15,"squeeze":0.5,"event":0.7,"no_trade":0.7}, fn=_day_type_mult)` L72; `_day_type_mult` → 0.85 when trend_up(mirror) and `in_htf_zone` L88-92; default 1.0 mb:185-194 | yes | — |
| multiplier `session` | NY 1.1; London 1.0; Asia 0.8; dead 0.7 | `session_rule({"newyork":1.1,"london":1.0,"asia":0.8,"dead":0.7})` L73 | yes | — |
| multiplier level failure count today | 1 → 1.0; 2 → 1.1; 3+ → 0.8 | `ContextRule("failures_today", {1:1.0, 2:1.1}.get(failures_today, 0.8))` L74; `failures_today = prev_fail + 1` L277 | yes | D-45(d), D-27 (per level per UTC day, once per SFP candle; `state_updates` L331-342) |
| multiplier `recent_form` | 3 losses 0.8; 5 wins 0.9 | `mb.form_rule(3, 5, 0.8, 0.9)` L75 (mb:160-167, `snap.consecutive`) | yes | — |
| multiplier `event_2h` | 0.8 | `mb.event_2h_rule(0.8)` L76 → `s.event_within_2h` mb:170-171 | yes | — |
| in-trade `re_approach_with_oi` | price within 0.2 ATR of failed high with OI rising, counts 2 | `_re_approach` L95-101: `lvl−px <= 0.2*atr15` (short) and `oi_change(fill_ts) > 0`; counts 2 L79 | yes | — |
| in-trade `cvd_recovering` | CVD higher than at entry for 2 candles (counts 1, doc silent) | `_cvd_recovering` L104-116: last 2 CVD values both > CVD at fill candle (short); counts 1 L80 | yes | — |
| in-trade `no_progress` | after 4 closed candles price has not traded below the failure candle low (counts 1) | `mb.check_no_progress(4, "failure_candle_extreme")` L81-82; `failure_candle_extreme = c.l` (short) L294; mb:491-505; counts 1 | yes | — |
| in-trade `close_above_level` | a 15m close above the level, counts 2 | `mb.check_close_beyond_level("level")` L83 — last closed 15m `c.c > setup["level"]` mb:477-488; counts 2 | yes | — |
| exit_threshold | 2 | `Mind.exit_threshold = cfg.get("exit_threshold", EXIT_THRESHOLD=2)` mind:10, 146; M3 passes none (mb:520-525) | yes | — |
| entry rule | post-only at failed high − 0.1 ATR, valid 3 candles | `entry = ext − 0.1*a15` (short) L279; `entry_valid_until = now + 3×15m` L316 (`ENTRY_VALID_CANDLES=3` L29); runner cancels `entry_expired` at expiry run:533-550; paper fill when mid trades through run:504-507 | yes | — |
| stop rule | swing-failure high + 0.15 ATR | `stop = ext + 0.15*a15` (short) L280 | yes | — |
| T1 rule | session VWAP or range mid, whichever nearer | `cands = [vwap_session, r4.mid]` filtered to the trade's side of entry; `min(cands, key=|x−entry|)` L281-285 | yes | — |
| T2 rule | long liquidation cluster below; if none, range low | `t2 = cl_level if cl_level beyond t1 else r4.low` L286 (cluster = largest long cluster within 1.5 ATR below, L262-265) | yes (see not-in-doc rows for the extra guards) | — |
| partial percent | close 50% at T1 | `ModelIntent(..., partial_pct=50.0)` L316; runner `qty = size0*partial_pct/100` run:607 | yes | (confirmed by audit: 50%, trail never) |
| breakeven rule | stop to breakeven at T1 | runner `stop = entry; be_done=True` on T1 fill run:611-612 | yes | — |
| trail | none in doc | `trail_after="never"` L316 | yes | — |
| expected_hold_min | 120 | `HOLD_MIN = 120` L25 → `expected_hold_min` L41 | yes | — |
| dead-trade time stop | 180 min | `Mind.manage`: exit `dead_trade` at `1.5 × expected_hold_min` (=180) **and progress < 0.5R** mind:11, 227-228 | yes | D-45 (verified) |
| hard time stop | 6 hours | `HARD_STOP_MIN = 360` L26/L42; runner sets `hard_stop_ts = fill + hard_stop_min` run:567-568, closes `time_stop` run:602-603 | yes | — |
| one-attempt rule | none in doc 13 | none in code (only the failures counter L272-277, which is not a gate) | yes | D-45 (verified) |
| seq 1 alignment | day_type range, or trend_up with price inside a 4h supply zone; price in premium of 4h range | `day_type=="range"` or (`trend_up` mirror and `in_zone` = live bearish 4h zone containing price) L180-183; `pct > 0.5` for short (`< 0.5` long) L184-185 | yes | — |
| seq 2 level | range high (4h swing high), 4h supply OB/FVG, equal highs 1h/4h, PDH, session high; must predate current session | `_levels` L132-160 with `sess_start = session_bounds(day_start(now), session)[0]` L33-34, L186; all candidates filtered `<= sess_start`; prior sessions of today only; no levels → miss L188-189; PWH also included L149 (doc §4.2) | yes | D-45(e) |
| seq 3 push | price trades above the level; OI rising ≥ 0.5% over 1h; funding ticking up | `c.h > lvl` L198; `oi_push = oi_change(c.ts−1h, c.ts)`; miss if `< 0.005` (`OI_PUSH_MIN` L27) — **None passes** L232-234; funding: z samples over 2h into the SFP, miss if last < first (short), <2 samples/None passes L250-255 | yes | D-45(c) funding as sequence condition |
| seq 4 swing failure | 15m candle higher high than prior swing high and closes below that prior high; CVD at new high < CVD at prior high | prior = last confirmed 15m swing high before c L203-206; `c.h > prior.price` L207; `c.c < prior.price` L210; additionally `c.c < lvl` L198; CVD: `diff > 0` required, `cvd_ok is False` → miss, **None (no tape) passes** L237-246 | yes | D-45(a) |
| seq 5 failure-candle data | fresh long liquidation cluster within 1.5 ATR below price; bid depth (0.3%) below 70% of its 1h average | not gated: cluster and depth only feed `cluster_fuel_strength` / `thin_bids_strength` L257-266, L299-301; depth measured at evaluation time (latest book row), not on the failure candle; no freshness test | MISSING (as sequence conditions) | — |
| seq 6 retest | price returns to within 0.2 ATR of the failed high without closing above it | retest candle = last closed 15m L219; `ext − rt.h <= 0.2*a15 and rt.h <= ext` L220 (`RETEST_ATR` L28); no close beyond the **level** between SFP and now unless acceptance L215-217 | deviation | D-33 ("closing above it" checked against the level; lone close = miss, pair+OI = acceptance veto) |
| seq 7 Mind evaluates | — | `self.mind = mb.build_mind(...)` L85; `ModelStrategy.evaluate` mb:398-443 (no-setup rows still evaluated with `no_setup` veto) | yes | — |
| thesis template | "M3 short {coin} at failed high {level} ({level_type}). Push added {oi_change} OI with CVD divergence {div}. Long cluster {cluster_notional} at {cluster_level}. Strongest: {top3}. Wrong if 15m closes above {level}. Expect {t1} within 2h, then {cluster_level}." | L319-325, same fields; `{level}` in "at failed high" = `failed_extreme`, "Wrong if" = `level`; final `{cluster_level}` falls back to `t2` when no cluster | yes | — |
| alerts (doc §5) | SFP detected / take / skip / exit | `alert_detected` L327-329 (`detect_alert_kind="SFP detected"` L43); take/skip in mb:415-441; exit alerts in runner | yes | — |
| unit tests (doc §5) | valid failure+retest, acceptance veto, squeeze_risk veto, in-trade close_above_level exit | `tests/strategy_engine/test_models_m1_m6.py` L245, 257, 265, 273 | yes | — |
| SFP recency window | not in doc | SFP candle must be one of the 3 candles before the retest candle (`range(max(2,last−3), last)` L196) → retest within 3 candles of the SFP | not in doc | — |
| retest wick cap | not in doc | retest candle high must not exceed the failed high (`rt.h <= ext` L220) | not in doc | — |
| SFP close below level | not in doc (doc step 4 only "below that prior high") | `c.h > lvl and c.c < lvl` L198 (mirror L200) required in addition to L210 | not in doc | — |
| best-level choice | not in doc | when several levels qualify the highest base location strength wins L223-225 | not in doc | — |
| T1 fallback | not in doc | if neither VWAP nor range mid is on the trade's side of entry → `entry ± 1.5R` L285 | not in doc | — |
| T2 ordering guard / fallback | not in doc | cluster used only if beyond T1 L286; if T2 not beyond T1 → `entry ± 3R` L287-288 | not in doc | — |
| warm-up gate | not in doc | need ≥40 closed 15m, ATR>0, 4h range L122-123 | not in doc | — |
| both directions | doc: "long is the mirror" | scans short then long, first complete setup wins L125-129 | yes | — |
| thesis_failed needs 2 consecutive candles | not in doc 13 (doc 10) | `streak >= 2` of `count >= exit_threshold` mind:221-226 | not in doc | — |
| feed-missing vetoes | not in doc 13 | `feed_missing_oi` / `feed_missing_taker` (no rows in last hour) added by runner run:150-163, 327 | not in doc | D-31 |
| frequency 0–2 per day | descriptive (§1) | no cap in code | no (descriptive; not enforced) | — |

**Residual mismatches not covered by D-27 / D-33 / D-45 (candidate defects)**

- Doc §2 step 5 ("Data on the failure candle: a fresh long liquidation cluster formed within 1.5 ATR below price; bid depth within 0.3 percent below 70 percent of its 1h average") is not a sequence condition in code — L257-266 only compute `thin_bids_strength` / `cluster_fuel_strength`; a setup with no cluster and full bid depth passes the sequence. Depth is also read from the latest book row at evaluation time (snap:189-195), not on the failure candle, and "fresh" is never tested.
- `first_failure_random_level` veto (doc §4.3) is dead: `"level_this_session": False` is hardcoded at L307; the condition is instead enforced as a sequence miss ("no high level predating this session", L188-189). Same "no trade" outcome as D-33's pattern, but this one is not recorded in DECISIONS and the veto key can never appear in `vetoes_hit`.
- `squeeze_risk` doc "short OI share >= 60 percent" is implemented as the cohort's short notional share (`sn/(ln+sn)` from `snap.cohort`, L269-271), not the market's OI split.
- `second_failure` doc "1.0 if this is the second swing failure at this level today; 0 otherwise" — code L305 returns 1.0 for `prev_fail >= 1`, i.e. also on the 3rd+ failure (which simultaneously gets the 0.8 multiplier).
- Step 4 in code is stricter than the doc: the SFP candle must also close back below the level (`c.c < lvl`, L198/L200), and the retest candle's wick may not exceed the failed high (`rt.h <= ext`, L220); the SFP must be within the 3 candles preceding the retest (L196). None of these windows/conditions are in doc 13.
- Step 3 OI gate: `oi_push is None` passes (L233). `feed_missing_oi` (run:159-160) only covers "no OI rows in the last hour"; if rows exist in the last hour but none at/before `c.ts − 1h`, `oi_at` returns None and the ≥0.5% push requirement is silently skipped. Same shape for CVD divergence when the taker tape is None at either index (L239-245 → `cvd_ok` stays None, passes). Undocumented target fallbacks (T1 → 1.5R L285; T2 → 3R L287-288) change targets in edge cases without doc backing.

### 1.4 M4 HTF Change of Character (doc 14)

| doc item | doc value | implemented value | match | fix applied |
|---|---|---|---|---|
| **Reason** `cohort_reducing` | clip(−cohort_net_long_change_24h / 0.3, 0, 1); w 2.5 | `clip((−cohort_ch if short else cohort_ch)/0.3)`, 0.0 when cohort None (m4 L238); weight 2.5 (L49); cohort Δ24h = `net_dir − net_dir_24h_ago` (snapshot.py L203-207) | yes | — |
| **Reason** `oi_extreme` | clip(1 − (OI_7d_high − OI_now)/(0.05 × OI_7d_high), 0, 1); w 2.0 | `oi_gap=(oi_hi−oi_now)/oi_hi` (L138); `clip(1.0 − oi_gap/0.05)` (L239); w 2.0 (L50); oi_7d_high = MAX(oi_notional) over 7d (model_runner L228-230) | yes | — |
| **Reason** `funding_elevated` | clip(max_funding_z_24h / 2.5, 0, 1); w 1.5 | `fz_peak = funding_z_max_24h()` (short) / `funding_z_min_24h()` (long) (L141); `clip(±fz_peak/2.5)` (L240); w 1.5 (L51) | yes | — |
| **Reason** `choch_quality` | clip((higher_low − choch_close)/(0.5 ATR_4h), 0, 1); w 1.5 | `clip((choch.level − c4[i_ch].c)/(0.5*a4))` mirror for longs (L205-206, L241); `choch.level` = the broken higher-low swing price (trend.py L86); w 1.5 (L52) | yes | — |
| **Reason** `weak_bounce` | clip((0.55 − taker_buy_ratio_bounce)/0.15, 0, 1) × (1.0 if OI_change_bounce ≤ 0 else 0.5); w 1.8 | `clip((0.55 − br_bounce)/0.15) × (1.0 if oi_bounce is None or ≤0 else 0.5)`, 0.0 if br None (L242-243); bounce window = post-CHoCH extreme candle − 1h → last 1h close (L176-177); w 1.8 (L53) | yes | — |
| **Reason** `extension` | clip((pct_move_3d − 6)/8, 0, 1); w 1.0 | `clip((pct_3d*100 − 6)/8)` (L244); pct_3d = close of candle before CHoCH vs last 4h close ≥ 3 days before the CHoCH (L130-131); w 1.0 (L54) | yes | — |
| **Reason** `zone_quality` | displacement_grade of the CHoCH move; w 1.2 | `float(zone.grade or 0.0)` — grade of the 1h zone created by the CHoCH displacement (L234; zones.py L22); w 1.2 (L55) | yes | — |
| **Reason** `cluster_reward` | clip(sum_long_cluster_notional_below_within_range/(0.5% of OI), 0, 1); w 1.0 | `in_range` = same-side clusters with `r4.low ≤ level < entry` (L202); `clip(sum notional / oi_frac(0.5))`, 0 when OI unknown (L203, L245); w 1.0 (L56) | yes | — |
| **Reason** `delta_flip` | clip((0.5 − taker_buy_ratio_4h_since_choch)/0.15, 0, 1); w 1.0 | `br_4h = buy_ratio(choch.ts, now_ms)` (L217); `clip((0.5 − br_4h)/0.15)` mirror (L246); w 1.0 (L57) | deviation | D-46 (interpretation recorded: window = CHoCH → now) |
| **Reason** `divergence` | 1.0 if CVD at last 4h high < CVD at prior 4h high, else 0; w 1.2 | last two 4h swing highs with ts ≤ choch.ts (L219); `cvd_lower_extreme(cvd4, ia, ib, direction)` returns True when CVD CONFIRMED; `div = 1.0 if r is False else 0.0` (L224-227); CVD over last 80 4h candles from a 7-day tape (L218; model_runner L231-235); w 1.2 (L58) | yes | D-46 (a) inversion fixed; (d) tape 24h → 7d |
| **Veto** `cohort_adding_longs` | cohort net long change 24h ≥ +0.15 | `cohort_change_24h × (1 if short else −1) ≥ 0.15` (L61-63) | yes | D-34 (≥0.15 falls through find_setup so this veto records, L184) |
| **Veto** `daily_strong_against` | daily_bias up AND CHoCH low above daily 20-candle midpoint | `s.trend("1d") == old and choch.level > s.daily_midpoint(20)` mirror (L208-210); veto reads setup flag (L64-65); `daily_bias` is the same 1d TrendState label (alignment.py L63-64, L113) | yes | D-37 (veto tested via the setup flag); D-46 ("CHoCH low" = broken HL level) |
| **Veto** `oi_rebuilding` | OI up ≥ 2% during the bounce | `oi_bounce ≥ 0.02` (L66-67); oi_bounce = oi_change(ext_c.ts − 1h, last.ts) (L176) | yes | — |
| **Veto** `event_30m` | scheduled event within 30 min | `mb.event_30m_veto()` → `snap.s.event_within_30m` (L68; model_base L174-175; alignment.py L116 `abs(t−now) ≤ 30m`) | yes | — |
| **Veto** `stop_too_wide` | > 2.0 ATR(1h) | `stop_dist > 2.0 × a1` flag (L34, L191, L235); Veto reads flag (L69) | yes | — |
| **Veto** `bounce_displacement` | bounce contains a 1h displacement candle up | any bounce candle with `range ≥ 1.5·ATR1h AND body_ratio ≥ 0.6 AND up` (mirror), needs ≥2 bounce candles (L251-252); Veto reads flag (L70) | yes | D-46 (b) was range ≥ 1.0 ATR; now doc 10 §2.4 displacement |
| **Veto** `already_reversed` | price already fell > 50% of the 4h range from the high before the pullback | `pre_ext` = max high of the 30 4h candles before the CHoCH; `moved = pre_ext − snap.price`; `moved > 0.5 × r4.width` (L212-215); Veto reads flag (L71) | yes (interpretation, see bullets) | — |
| **Multiplier** day_type squeeze | 1.2 (squeeze against the old trend) | `"squeeze": 1.2` undirected (L74) | deviation | D-46 (squeeze multiplier undirected, recorded) |
| **Multiplier** day_type range | 1.05 | `"range": 1.05` (L74) | yes | — |
| **Multiplier** day_type trend_down (short) | 1.0 | `_day_type_mult`: day_type == day_trend_for(direction) → 1.0 (L88-91; short→"trend_down") | yes | — |
| **Multiplier** day_type trend_up (short) | 0.7 | day_type == day_trend_for(opposite) → 0.7 (L92-93) | yes | — |
| **Multiplier** day_type event | 0.7 | `"event": 0.7` (L74) | yes | — |
| **Multiplier** day_type no_trade | 0.8 | `"no_trade": 0.8` (L74) | yes | — |
| **Multiplier** session London/NY | 1.05 | `"london": 1.05, "newyork": 1.05` (L75), session at evaluation time | yes | — |
| **Multiplier** session Asia | 0.9 | `"asia": 0.9` (L75) | yes | — |
| **Multiplier** recent_form | 2 consecutive M4 losses → 0.85 | `form_rule(2, 10**6, 0.85, 1.0)` (L76; model_base L160-167); form = last 5 M4 paper outcomes across coins (model_runner L265-269) | yes | — |
| **Multiplier** funding trend | funding z falling from its 24h max → 1.05 | `funding_falling = fz_now < fz_peak − 0.2` (mirror) (L253-254); rule 1.05 (L77) | deviation | D-46 (0.2 z buffer recorded) |
| **In-trade** `higher_high_1h` | 1h close above the entry swing high; counts 2 | `check_close_beyond_level("entry_swing","1h")`, counts 2 (L80; model_base L477-488); `entry_swing = sw1.price` (L235) | yes | — |
| **In-trade** `oi_rebuild` | OI up ≥ 2% since entry; counts 1 | `snap.oi_change(pos.fill_ts) ≥ 0.02`, counts 1 (L81, L96-99) | yes | — |
| **In-trade** `cohort_flip` | cohort net long change since entry ≥ +0.15; counts 1 | `check_cohort_flip(0.15)`: `cohort_net_dir − cohort_net_dir_entry ≥ 0.15` for shorts (mirror), counts 1 (L82, L255; model_base L508-517) | yes | — |
| **In-trade** `no_progress` | after 8h price has not traded below the CHoCH low; counts 1 | `check_no_progress(32, "choch_level")` = 32 closed 15m since fill and no low < choch_level (mirror), counts 1 (L83; model_base L491-505) | yes | D-46 ("CHoCH low" = broken HL level, recorded) |
| **exit_threshold** | 2 | `Mind.exit_threshold` from models.yaml `common.exit_threshold: 2` (mind/base.py L146; config/models.yaml L8); exit when fail count ≥ 2 on 2 consecutive closed 15m (base.py L221-226) | yes | — |
| **Entry rule** | post-only at 1h supply zone mid (FVG mid or OB body mid), valid 6h | `entry = zone.mid` of the highest live bearish 1h zone created by the CHoCH displacement (L159-163, L189); `entry_valid_until = now + 6h` (L35, L268); runner cancels `entry_expired` (model_runner L533-550) | yes | — |
| **Stop rule** | above most recent 1h swing high + 0.2 ATR(1h); skip if > 2.0 ATR(1h) | `last_confirmed_swing(sw1h,"high", now, after_ts=choch.ts−24h).price + 0.2·a1` (L186-190); >2.0 ATR → `stop_too_wide` veto (L235, L69) | yes | — |
| **T1 rule** | 4h range mid | `t1 = r4.mid` (L195) | yes | — |
| **T2 rule** | first long liquidation cluster below with notional ≥ 0.15% of OI | nearest same-side cluster below entry with `notional ≥ oi_frac(0.15)` (L196-200); None → T3 promoted to T2 (L262-266) | yes | D-46 (c) T2/T3 shift |
| **T3 rule** | 4h range low; trail behind each new 1h lower high after T2 | `t3 = r4.low` (L201); intent `trail_tf="1h", trail_after="t2"` (L268); runner arms trail after T2 fill and moves stop to last confirmed 1h swing high since fill if it improves (model_runner L629-630, L659-663, L689-699) | yes | D-46 (c) |
| **Partial percent** | close 40% at T1 | `partial_pct=40.0` (L268); runner `size0 × partial_pct/100` at T1 (model_runner L606-609) | yes | — |
| **Breakeven rule** | stop to breakeven at T1 | runner `stop = entry; be_done=True` on T1 fill (model_runner L611-612) | yes | — |
| **expected_hold_min** | 480 (8h) | `HOLD_MIN = 480` (L26, L42) | yes | — |
| **Dead-trade time stop** | 12h | Mind.manage: `minutes ≥ 1.5 × 480 = 720 and progress < 0.5R` → `dead_trade` (mind/base.py L227-228; models.yaml L9) | yes | — |
| **Hard time stop** | 36h / 2160 min | `HARD_STOP_MIN = 2160` (L27, L43); runner `hard_stop_ts = fill_ts + 2160 min` → `time_stop` (model_runner L384, L567-568, L602-603) | yes | — |
| **One-attempt rule** | none in doc 14 | none in code (no attempt state); only the generic one-open-trade-per-model/coin guard (model_runner L367-372) | yes | — |
| **Seq 1** extended trend | ≥5 consecutive 4h BOS up, OR price up ≥ 8% over 3 days | `n_bos = consecutive_bos(events before CHoCH, old)`; `pct_3d` to the candle before CHoCH; miss if `n_bos < 5 and pct_3d < 0.08` (L124-133) | yes | D-29 (OR wording confirmed) |
| **Seq 2** crowding | OI within 3% of 7d high; funding z ≥ 1.0 at some point in last 24h | `oi_gap > 0.03` → miss (L135-140; None passes); `fz_peak < 1.0` → miss (L141-143; None passes) | yes | — |
| **Seq 3** CHoCH | a 4h close below the most recent 4h higher low | `last_event_of(st4.events,"CHoCH", new)` (trend.py L85-86: first close below last HL in an uptrend); must be ≤ 72h old and ≥ 20 candles into the 4h window (L121-129) | deviation | D-29 (72h freshness added) |
| **Seq 4a** 1h supply zone from the CHoCH move | last up candle before the down displacement, or the FVG it left | 1h displacement `new`-direction ending within 12h before the CHoCH close (L145-158); live bearish 1h zone (OB or FVG) created at `disp.start−3 … disp.end+1`, highest chosen (L159-163) | yes | — |
| **Seq 4b** first pullback into the zone | price rallies on 1h into the zone | bounce = candles from the post-CHoCH 1h extreme (L165-169); last closed 1h candle `h ≥ zone.bottom and c ≤ zone.top`, not closed through (L170-175) | yes | — |
| **Seq 4c** bounce taker buy ratio ≤ 0.55 | ≤ 0.55 | `br_bounce > 0.55` → miss (mirror `< 0.45`) (L33, L177-179) | yes | — |
| **Seq 4d** bounce OI falling or flat | OI falling or flat during the rally | not a sequence gate; only `oi_rebuilding` veto at ≥ +2% (L66-67) and weak_bounce ×0.5 when oi_bounce > 0 (L243) | deviation | D-46 (step-4 OI is a soft reading, recorded) |
| **Seq 5** cohort ≤ 0 | cohort net long change 24h ≤ 0 | `0 < Δ (with old trend) < 0.15` → miss (L180-183); `≥ 0.15` falls through to veto (L184); None passes | yes | D-34 |
| **Seq 6** Mind evaluates | Mind evaluates | `ModelStrategy.evaluate` → `mind.evaluate(snap)` (model_base L398-414); Mind semantics doc 10 §4.3 (mind/base.py L161-204) | yes | — |
| **Evaluation cadence** (§5) | each closed 1h + each closed 15m while pending | every closed 15m (docstring L11-12; model_runner L276-300) | deviation | D-29 |
| **Thesis template** | "M4 short {coin} after 4h CHoCH at {choch_level}. Trend up {pct_move} in 3d, OI within {oi_gap}… Wrong if 1h closes above {stop_level}. Expect {t1} within 8h, then {cluster_level}." | L271-277: same fields, `{stop_level}` rendered as `entry_swing` (the swing high, not the +0.2 ATR stop), `{cluster_level}` falls back to t3 when no cluster | yes | — |
| **Alerts** (§5) | CHoCH detected w/ crowding; take w/ thesis; skip w/ vetoes; exit w/ exit_reason and R | `alert_detected` (L279-282, key L284-285); take/skip in model_base L417-441; exit in runner `_close` (D-12) | yes | — |
| **not in doc** T2 partial | — | runner closes another `partial_pct` (40%) at T2 when T3 exists, then trails (model_runner L624-631) | not in doc | D-36 (recorded: "M4 T2 partial then trail to T3") |
| **not in doc** session "dead" multiplier | — | `"dead": 1.0` (L75) — neutral | not in doc | — |
| **not in doc** warming gate | — | needs 60 closed 4h + 60 closed 1h + ATRs (L104-105) | not in doc | — |
| **not in doc** interpretation constants | — | CHOCH_MAX_AGE 72h (L32), 12h displacement window (L145), zone index window −3/+1 (L160), stop swing `after_ts = choch−24h` (L186), `already_reversed` 30-candle lookback (L212), `i_ch ≥ 20` (L128) | not in doc | D-29 (72h only) |
| **not in doc** feed-missing vetoes | — | `feed_missing_oi/taker/book` added as extra vetoes (model_runner L151-163) | not in doc | D-31 |
| **not in doc** no_setup synthetic veto | — | every row evaluates with `no_setup: <missing>` (model_base L401-405) | not in doc | D-09 |

Residual candidates not covered by D-29/34/37/46:

- `already_reversed` measures the fall to the **current** price, not to the low reached before the pullback. Doc L51: "price has already fallen more than 50 percent of the 4h range from the high before the pullback". Code L214 `moved = (pre_ext - snap.price)`; at evaluation price has already bounced back into the zone, so the measured fall is smaller than the real pre-pullback drop (`ext_c.l`, L168) → the veto under-fires. Also the "high" is a fixed 30-candle pre-CHoCH lookback (L212), not in the doc.
- `extension` / Seq 1 `pct_3d` is measured to the 4h close **before the CHoCH** (L130-131 `c4[i_ch-1].c` vs the close ≥3 days before the CHoCH), while doc L11/L38 say "over the last 3 days" (relative to evaluation, up to 72h after the CHoCH per D-29). Defensible (trend extension pre-crack) but not what the text says and not recorded.
- `choch_quality` divides by the **current** ATR(4h) (`a4 = snap.atr("4h")`, L120, L206) rather than ATR at the CHoCH candle (up to 72h earlier). Same for the 1h displacement window, which does use the per-candle ATR series (L151-152) — inconsistent treatment within one file.
- Thesis `{stop_level}` renders `entry_swing` (L276-277), i.e. the 1h swing high, while the actual stop is swing + 0.2 ATR (L190). The sentence "Wrong if 1h closes above {stop_level}" aligns with the `higher_high_1h` check (which uses `entry_swing`), so this is consistent with the in-trade check but not with the doc's word "stop".
- Seq 2 funding gate passes when `funding_z_max_24h()` is None (L142) and `gauge_24h` falls back to the current `funding_z` when the 24h series is empty (snapshot.py L180-182) — a single-point reading can satisfy "at some point in the last 24h". Not covered by `feed_missing_*` (no gauge feed veto exists, model_runner L151-163).
- Runner T2 partial (40% at T2, model_runner L624-631) is absent from doc 14 §3 (which specifies a partial only at T1); it is recorded in D-36, not in the audit D-numbers listed for M4.

### 1.5 M5 Session Liquidity Run (doc 15)

#### M5 spec-conformance table (doc 15 vs `m5_session_liquidity_run.py`, read-only)

Code file = `E:\wamp64\www\perpl\backend\app\strategy_engine\strategies\m5_session_liquidity_run.py` (line refs `L`); `mb` = `strategies/model_base.py`; `mind` = `mind/base.py`; `runner` = `model_runner.py`; `align` = `structure/alignment.py`; `sweeps` = `structure/sweeps.py`; `snap` = `mind/snapshot.py`.

| doc item | doc value | implemented value | match | fix applied |
|---|---|---|---|---|
| reason `bias_alignment` | 1.0 daily up (long after low raid) / 0.6 neutral / 0.2 down; w 2.0 | L200: `1.0 if bias == up(long)/down(short); 0.2 if opposite; else 0.6`; w 2.0 L55 | yes | — |
| reason `range_quality` | 1.0 if 1.2–2.5 ATR and ≤2 wicks outside by >0.1 ATR during Asia; 0.6 if 0.8–1.2 or 2.5–4.0; 0 otherwise; w 1.5 | L184–189 exactly those tiers (`wicks = asia.wicks_outside` L183); wicks counted in align L103–106 as wicks beyond the Asia BODY range (max/min of o,c) by >0.1 ATR(15m); w 1.5 L56 | yes | D-47(b) |
| reason `reclaim` | clip((rq−0.4)/0.4), ×0.7 if 3 candles; w 1.8 | mb L151–157: `clip((rq-0.4)/0.4)`, `*=0.7 if candles_to_reclaim >= 3`; rq = reclaim candle body/range (sweeps L68, candles L34–36); w 1.8 L57 | yes | — |
| reason `fuel` | clip(long_liq_notional_raid / (0.08% OI)); w 1.3 | L164 `snap.liq(side, wick_open, reclaim_close)`, L167 `oi_frac(0.08)`, L215 `clip(fuel/f08)` (0 when OI unknown); w 1.3 L58 | yes | — |
| reason `cleared` | clip(−OI_change_raid_pct / 0.8); w 1.0 | L165 `oi_change(wick_open, wick_close)` (wick candle only), L216 `clip((-oi*100)/0.8)`; w 1.0 L59 | yes | — |
| reason `delta_flip` | clip((taker_buy_ratio_reclaim − 0.5)/0.25); w 1.3 | L166 `buy_ratio(reclaim candle)`, L217 `clip((br-0.5)/0.25)` (mirrored `0.5-br` for shorts); w 1.3 L60 | yes | — |
| reason `early_in_window` | 1.0 if raid ≤45 min into window; 0.6 ≤90; 0.3 otherwise; w 1.0 | L191–192: `mins = (wick.ts − window_start)/min` (raid candle close time), `1.0/0.6/0.3` at 45/90; w 1.0 L61 | yes | D-47(a) |
| reason `open_location` | 1.0 inside middle 60%; 0.7 inside near raided edge; 0.3 within 0.2 ATR outside raided edge; w 1.0 | L169–181: pos 0.2–0.8 → 1.0; inside near raided edge → 0.7; outside → 0.3 (any edge); w 1.0 L62 | yes | — |
| reason `no_pre_drift` | clip(1 − pre_window_3h_move_toward_edge / 1.5 ATR); w 1.2 | L194–198 candles closing in (ws−3h, ws], `mv = last.c − first.o`, signed toward raided edge, floored 0; L219 `clip(1 - drift/(1.5*a15))`; w 1.2 L63 | yes | — |
| reason `cohort` | clip(cohort_fresh_adds_same_direction_60m / 2); w 0.8 | L64 `clip(cohort_fresh_adds(direction)/2)` → snap L209–210 `cohort.fresh_long/short` (60m fresh adds, cohort.py L226); w 0.8 | yes | — |
| veto `opened_outside` | session opened >0.2 ATR outside the Asia range | L67 flag; L179–181 `dist > 0.2*a15` when open outside [lo,hi] | yes | — |
| veto `too_deep` | raid depth > 0.6 ATR | L68 flag; L152 `detect_sweeps(..., 0.1, 0.6, 3)` → sweeps L53/L63 `too_deep = depth > max_depth` | yes | — |
| veto `range_bad` | Asia height <0.8 or >4.0 ATR | L69 flag; L182 `not (0.8 <= h_atr <= 4.0)` (L27) | yes | — |
| veto `already_taken` | an M5 trade already taken in this window for this coin | L70 flag; L201–202 `model_state["m5:{day}:{window}"]`; written only on TAKE L268–271 | yes | D-32 (recorded only when TAKEN) |
| veto `event_30m` | scheduled event within 30 min | L71 → mb L174–175 `snap.s.event_within_30m` | yes | — |
| veto `trend_day_with_raid` | day_type trend_down and raid of Asia low (mirror) | L72–73 `day_type == day_trend_for(opposite(direction))` | yes | — |
| veto `late_in_window` | reclaim completes after the window closes | L74 flag; L212 `rc.ts > window_end` (reachable via 1-candle grace L39) | yes | — |
| veto `funding_extreme_same_side` | (doc 10 shared) | L75 → mb L178–182 → snap L176–178 `crowding_level == EXTREME and blocked_direction == direction` | yes | — |
| multiplier day_type range | 1.1 | L78 table `range: 1.1` | yes | — |
| multiplier day_type squeeze against raid | 1.15 | L95–98: squeeze → 1.15 when `funding_z < 0` (long) / `> 0` (short), else 1.0 (interpretation via funding_z sign) | yes | — |
| multiplier day_type trend against reversal | 0.5 | L99–100 `day_type == day_trend_for(opposite(d))` → 0.5 | yes | — |
| multiplier day_type event | 0.7 | L78 `event: 0.7` | yes | — |
| multiplier window London / New York | 1.0 / 1.05 | L79 `session_rule({"london":1.0,"newyork":1.05})` on `snap.session` (sessions.py L10: london 7–13, newyork 13–21) | yes | — |
| multiplier Monday | 1.05 | L105–107 `weekday == 0 → 1.05` | yes | — |
| multiplier Friday New York | 0.9 | L108–109 `weekday == 4 and session == newyork → 0.9` | yes | — |
| multiplier recent_form | 3 losses 0.8; 5 wins 0.9 | L81 `form_rule(3,5,0.8,0.9)` → mb L160–167 (`consecutive`, per-model form) | yes | — |
| multiplier event_2h | 0.8 | L82 → mb L170–171 `event_within_2h → 0.8` | yes | — |
| check `below_asia_low` | 15m close below Asia low, counts 2 | L85 `check_close_beyond_level("level")`, counts 2 → mb L477–488 last closed 15m `c.c < level` (long) | yes | — |
| check `no_higher_low` | 2 closed candles without a higher low above the raid wick (counts 1) | L86 counts 1 → mb L449–453 / L286–300: ≥2 closed candles since fill and no candle with `l > wick_price and l >= prev.l` | deviation | D-25 |
| check `oi_bleeding` | OI down ≥1% since entry (counts 1) | L87 `check_oi_bleeding(0.01)` → mb L456–461 `oi_change(fill_ts) <= -0.01` | yes | — |
| check `delta_negative` | taker delta negative on last 2 candles (counts 1) | L88 → mb L464–474 both of last 2 closed 15m `delta < 0` (long) | yes | — |
| exit_threshold | 2 | not set per model (L90 `build_mind`); mind L146 = config `exit_threshold` else `EXIT_THRESHOLD = 2` (L10); exit when count ≥2 on 2 consecutive candles (mind L221–226) | yes | — |
| entry rule | post-only at 50% of the reclaim candle, valid 3 candles | L203 `(rc.h + rc.l)/2`; L248 `valid_until = reclaim_ts + 3×15m` (L30); L250 note "50% of the reclaim candle" | yes | — |
| stop rule | raid wick low − 0.15 ATR | L204 `wick.l − 0.15*a15` (short: `wick.h + 0.15*a15`), `wick = window[sw.wick_index]` L162 | yes | — |
| T1 rule | Asia range mid | L221 `t1 = (a_hi + a_lo)/2` | yes | — |
| partial percent | close 40% at T1 | L249 `partial_pct = 40.0`; runner L607 `size0 × partial_pct/100` | yes | — |
| breakeven rule | stop to breakeven at T1 | runner L611–612 `stop = entry; be_done = True` | yes | — |
| T2 rule | Asia range high; if day_type trend_up after T1, trail behind 15m higher lows and run past Asia high | L221 `t2 = a_hi` (short `a_lo`); L246 `trail = "t1" if day_type == day_trend_for(direction) else "never"`; runner L59 `TRAIL_REPLACES_T2 = {"M2","M5"}`, L621 T2 not an exit once trail armed; trail = last confirmed 15m swing low since fill (runner L689–701) | deviation | D-36; D-47 (trail decided at entry, recorded) ; D-11 |
| dead-trade time stop | 135 min | mind L227 `minutes >= 1.5 × expected_hold_min(90)` and `progress < 0.5R` | yes | — |
| hard time stop | window end + 2 h | L25 `HARD_STOP_MIN = 120`, L249 `hard_stop_ts = window_end + 120 min`; runner L602–604 `time_stop` | yes | D-32 |
| expected_hold_min | 90 | L24/L48 `HOLD_MIN = 90` | yes | — |
| one attempt per window per coin | one attempt per session window per coin | key `m5:{day}:{window}` L201; veto L70; state write on TAKE only L268–271 | yes | D-32 |
| frequency | up to 2 per day per coin (London + New York) | two window keys (L26) → max 2 takes/day | yes | — |
| seq 1 Asia range frozen 07:00; height 0.8–4.0 ATR | high/low 00:00–07:00 frozen at 07:00 | runner L422–433 upserts `asia_range:{day}` at the 07:00 boundary (state model `*`); L113–118 `_asia` = frozen copy else `Structure.asia` (align L97, needs 28 candles sessions.py L52–56); height → `range_bad` veto L182 (not a sequence miss) | yes | D-32 |
| seq 2 window 07–09 / 13–15; session did not open >0.2 ATR outside | opened within range or within 0.2 ATR of an edge | L26 windows; L34–41 `window_of` (grace +1 candle); L148 window candles = `c.ts > ws` (closing inside), L151 open = first candle's open; tolerance L31/L181 → `opened_outside` veto | yes | D-32 |
| seq 3 raid | 15m wick below Asia low by 0.1–0.6 ATR | L152 `detect_sweeps(window, level, a15, side, 0.1, 0.6, 3)` (sweeps L48–53) | yes | — |
| seq 4 reclaim | within 3 candles a 15m close back above the low with rq ≥0.5 | max_wait 3 counting the wick candle (sweeps L56–70); L153 reclaim must be the last closed candle; L159–160 `rq < 0.5` → sequence miss | yes | — |
| seq 5 data | long liq fills during raid; OI fell during raid; taker delta positive on reclaim | graded only: `fuel` L164, `cleared` L165, `delta_flip` L166 — no gate; zero liq / OI up / delta negative still yields a setup | no | — (not recorded in DECISIONS for M5) |
| seq 6 bias | daily_bias up or neutral preferred | graded `bias_alignment` L199–200 only | yes | — |
| seq 7 Mind evaluates | — | mb L398–443 `ModelStrategy.evaluate` → `Mind.evaluate` (mind L161–204) | yes | — |
| thesis template | §4.6 string | L252–258 same fields, direction-generalised (below/above, T2) | yes | — |
| alerts | 07:00 frozen range; raid detected; take; skip; exit | runner L428–432 `M5_asia`; L260–263 `M5_detected` on reclaim; take/skip mb L417–441; exit in runner `_close` | yes | — |
| unit tests | valid London raid, opened_outside, range_bad, below_asia_low exit | `tests/strategy_engine/test_models_m1_m6.py` L372/L384/L392/L400 | yes | — |
| §1 "trend only if raid against the trend AND daily bias favours the reversal" | trend day allowed only with bias favouring reversal | trend-with-raid → veto L72–73; trend-against-raid → 1.0 multiplier, bias only graded (L199–200); no bias gate | MISSING | — |
| §4.1 "pre-drift … veto threshold" | negative reason expressed as veto threshold + graded reason | graded reason only (L63); no veto (§4.3 also lists none) | MISSING | — |
| `open_location` inside near far edge → 1.0 | not in doc | L175–176 | not in doc | — |
| `open_location` outside the FAR edge (≤0.2 ATR) → 0.3 | doc gives 0.3 only for outside the raided edge | L178–181 (0.3 for any outside open) | not in doc | — |
| day_type squeeze not-against → 1.0; trend with reversal → 1.0; `no_trade` → 1.0 | not in doc | L78 default 1.0, L95–101 | not in doc | — |
| `window_of` grace: evaluation allowed one candle past the window end | not in doc | L39 `now <= end + 15m` (feeds the `late_in_window` veto) | not in doc | — |
| pre-alert `M5_pre` "raid in progress" (wick >0.1 ATR beyond, close still beyond) | not in doc (doc: "alert on raid detected") | L226–240 | not in doc | — |
| warming gate: 40 closed 15m candles + ATR | not in doc | L123–124 | not in doc | — |
| synthetic `no_setup` veto every 15m | not in doc 15 | mb L401–403 | not in doc | D-09 |
| `feed_missing_oi` / `feed_missing_taker` vetoes | not in doc 15 | runner L151–163 via `extra_vetoes` L327 | not in doc | D-31 |
| exit requires 2 consecutive candles at ≥ threshold; skip <0.55 / half <0.70 | not in doc 15 (doc 10) | mind L221–226; L8–9 | not in doc | — |
| `fuel_wallets`, `cohort_net_dir_entry`, `entry_candle_high` recorded in setup | not in doc | L215, L222, L211 | not in doc | — |

#### Residual mismatches not covered by D-47 / D-32 / D-36 (candidate defects)

- **Stop / wick price uses the FIRST raid candle, not the deepest extreme.** Doc §3: "Stop: raid wick low minus 0.15 ATR." `detect_sweeps` extends `sw.wick_price`/`sw.depth_atr` when a later pre-reclaim candle pokes deeper (sweeps L59–63), but M5 takes `wick = window[sw.wick_index]` (L162) and sets `stop = wick.l − 0.15*a15` (L204) and `wick_price = wick.l` (L209). On a 2–3 candle raid whose 2nd candle is lower, the stop sits above the true raid low (inside the wick) while `depth_atr`/`too_deep`/alerts report the deeper extreme; `no_higher_low` (mb L451) also references the shallower wick. Fix candidate: use `sw.wick_price`.
- **`cleared` window ≠ `fuel` window.** Both are "during the raid" (doc §2 step 5 / §4.2). `fuel` spans raid-candle open → reclaim close (L164); `cleared` spans only the first wick candle open → close (L165). For multi-candle raids the OI drop on candles 2–3 is ignored.
- **Doc §2 step 5 (liq on the raided side, OI fell, positive reclaim delta) is not a sequence gate** — only graded (L164–166). Same shape as the M1 D-42 recorded interpretation, but not recorded for M5 in DECISIONS.
- **Doc §1 trend-day condition not enforced**: "trend only if the raid is against the trend and the daily bias favours the reversal". Code vetoes trend-in-raid-direction (L72–73) but a trend_up day with `daily_bias = down` still produces a long setup at 1.0 multiplier with bias only graded 0.2 (L200).
- **Squeeze "against the raid direction" is an unrecorded interpretation**: implemented as the sign of `funding_z` (L95–98), 1.0 otherwise; the doc gives no formula and no default for squeeze-with-raid. Not in DECISIONS.
- **`open_location` outside the FAR edge**: doc defines 0.3 only for "within 0.2 ATR outside the raided edge"; code gives 0.3 for an open ≤0.2 ATR outside either edge (L178–181) and an undocumented 1.0 for "inside near the far edge" (L175–176). Doc §4.1 also says pre-drift is "expressed as a veto threshold" while §4.3 lists no such veto — doc-internal inconsistency, code follows §4.3.

### 1.6 M6 Weekly Open Reclaim (doc 16)

**M6 spec-conformance table** (doc `E:\wamp64\www\perpl\docs\models\16-M6-weekly-open-reclaim.md` vs code `E:\wamp64\www\perpl\backend\app\strategy_engine\strategies\m6_weekly_open_reclaim.py`; "mb." = `model_base.py`, "runner" = `model_runner.py`, "mind" = `mind/base.py`)

| doc item | doc value | implemented value | match | fix applied |
|---|---|---|---|---|
| reason `loss_depth` | clip((excursion_depth_atr4h − 0.8)/1.2, 0, 1), w 1.8 | `clip((depth - 0.8) / 1.2)` L219, depth = (wo − min 15m low this week up to reclaim close)/ATR4h L166–168; w 1.8 L50 | yes | — |
| reason `oi_commitment` | clip(OI_change_4h_pct / 2.0), w 2.0 | `clip((oi_4h*100)/2.0)` L220, oi_4h = `snap.oi_change(rc1.ts−4h, rc1.ts)` L174; None → 0.0; w 2.0 L51 | yes | — |
| reason `delta_reclaim` | clip((taker_buy_ratio_4h − 0.5)/0.15), w 1.5 | `clip((br-0.5)/0.15)` (mirror `0.5−br`) L221, br = `snap.buy_ratio(rc1.ts−4h, rc1.ts)` L177; w 1.5 L52 | yes | — |
| reason `funding_room` | clip((1.0 − funding_z)/1.5), w 1.2 | `clip((1.0 - fz)/1.5)` (mirror −fz) L222; w 1.2 L53 | yes | — |
| reason `cohort` | 0.5 + 0.5·clip(Δ24h/0.2) if Δ ≥ 0 else 0, w 1.8 | `(0.5 + 0.5*clip(coh/0.2)) if coh >= 0 else 0.0` L223 (mirror −coh); w 1.8 L54 | yes | — |
| reason `reward` | clip((dist_to_PWH/stop_dist − 1.5)/2.5), w 1.2 | `clip((dist/stop_dist - 1.5)/2.5)` L224, dist = PWH − entry L195; w 1.2 L55 | yes | — |
| reason `timing` | 1.0 Tue/Wed; 0.7 Mon after 12:00; 0.5 Thu, w 1.0 | `wd = weekday(rc1.ts - 1)` L202; `1.0 if wd in (1,2) else 0.7 if wd==0 else 0.5 if wd==3 else 0.3` L203; Mon<12:00 excluded by L158; w 1.0 L56 | yes | D-48(b) close-side weekday |
| reason `htf_bias` | 1.0 daily up; 0.6 neutral; 0.2 down, w 1.5 | `1.0 if trend=="up" else 0.2 if "down" else 0.6` L208 via `s.trend("1d")` L204; w 1.5 L57 | yes | — |
| reason `reclaim_quality` | clip((close_1h − weekly_open)/(0.5 ATR_1h)), w 1.0 | `clip((rc1.c - wo)/(0.5*a1))` L226; w 1.0 L58 | yes | — |
| reason `pwh_untested` | 1.0 untested; 0.5 tested once, w 0.8 | `1.0 if touches==0 else 0.5 if touches==1 else 0.0` L227, touches = 1h candles this week with h ≥ PWH L201; w 0.8 L59 | yes | — |
| veto `daily_strong_down` | daily_bias down AND price below daily 20-candle midpoint | setup flag `daily_strong_against` L62–63 = `trend("1d")=="down" and price < s.daily_midpoint(20)` L204–207 (midpoint = (max h + min l)/2 of last 20 dailies, alignment.py L56–60) | yes | D-37 style (setup flag) |
| veto `shallow_loss` | excursion depth < 0.8 ATR(4h) | flag `depth < LOSS_MIN_ATR4H(0.8)` L214, veto L64 | yes | — |
| veto `already_taken` | M6 long already taken this week for this coin | `already_taken = bool(model_state.get("m6:{wk}:{direction}"))` L209/212, veto L65; key written on take L272–275 | yes | D-35 |
| veto `too_late` | reclaim after Thu 23:59 UTC | `too_late = rc1.ts > ws + 4*DAY_MS` L173, veto L66 | yes | — |
| veto `oi_falling` | OI Δ4h into the reclaim ≤ 0 | `oi_4h is not None and oi_4h <= 0` L67–68 | yes | — |
| veto `event_30m` | scheduled event within 30 min | `mb.event_30m_veto()` L69 → `snap.s.event_within_30m` mb L174–175 | yes | — |
| veto `stop_too_wide` | > 2.5 ATR(1h) | flag `stop_dist > 2.5*a1` L216, veto L70 | yes | — |
| veto `funding_extreme_long_side` | (name only) | `mb.funding_extreme_veto(...)` L71 → `crowding_level=="EXTREME" and blocked_direction==direction` (snapshot.py L176–178) | yes | — |
| multiplier day_type trend_up | 1.1 | `_day_type_mult` → 1.1 when day_type == trend_for(direction) L90–91 | yes | — |
| multiplier day_type range | 1.0 | table `{"range": 1.0}` L74 | yes | — |
| multiplier day_type squeeze up | 1.05 | squeeze → 1.05 only if funding_z < 0 (long) / > 0 (short), else 1.0 L94–96 ("up" read as squeeze against the crowd) | yes (interpretation) | — |
| multiplier day_type trend_down | 0.7 | 0.7 when day_type == trend_for(opposite) L92–93 | yes | — |
| multiplier day_type event | 0.7 | table `{"event": 0.7}` L74 | yes | — |
| multiplier prior week closed above open | 1.05 | `_prior_week_mult`: `chg = pw_close/pw_open − 1` (sign-flipped for shorts) `> 0 → 1.05` L100–110, refs from live `snap.s.refs` L101 | yes | — |
| multiplier prior week large down (> 6%) | 0.9 | `chg < -0.06 → 0.9` L107–108 | yes | — |
| multiplier recent_form 2 consecutive losses | 0.85 | `mb.form_rule(2, 10**6, 0.85, 1.0)` L76 (win branch disabled) | yes | — |
| multiplier event_2h | 0.85 | `mb.event_2h_rule(0.85)` L77 | yes | — |
| in-trade `lost_weekly_open` | 1h close back below weekly open, counts 2 | `mb.check_close_beyond_level("level","1h")`, count 2 L80; last closed 1h after fill with c < level (mb L477–488) | yes | — |
| in-trade `oi_dropping` | OI down ≥ 2% since entry, counts 1 | `mb.check_oi_bleeding(0.02)`, 1 L81; `oi_change(fill_ts) <= -0.02` (mb L456–461) | yes | — |
| in-trade `cohort_flip` | cohort net long change since entry ≤ −0.15, counts 1 | `mb.check_cohort_flip(0.15)`, 1 L82; `net_dir_now − cohort_net_dir_entry <= -0.15` (mb L508–517), entry value captured L228 | yes | — |
| in-trade `no_progress` | after 12h no higher high than the reclaim high, counts 1 | `mb.check_no_progress(48, "entry_candle_high")`, 1 L83 = 48 closed 15m since fill with no h > rc1.h (L213; mb L491–505) | yes | — |
| exit_threshold | 2 | Mind default `EXIT_THRESHOLD = 2` (mind L10, L146; config-overridable); exit needs count ≥ 2 on 2 consecutive 15m (mind L222–226, doc-10 semantics) | yes | — |
| entry rule | post-only at WO + 0.1 ATR(15m) on first pullback after 1h close; if no pullback in 4h → 1h close + 0.1 ATR; valid 8h | `entry = wo + 0.1*a15` L190; resting limit valid `now_ms + 8h` L252; `fallback_entry={"type":"reprice_at","at_ms": rc1.ts+4h, "price": rc1.c + 0.1*a15}` L218/L253; runner re-prices once at `at_ms` L526–532 | yes | D-35 `reprice_at` |
| stop rule | lowest low of the 4h before the reclaim − 0.2 ATR(1h); skip if > 2.5 ATR(1h) | `win = 15m candles in (rc1.ts−4h, rc1.ts]`, `stop = min(l) − 0.2*a1` L187–189; skip via `stop_too_wide` veto L216/L70 | yes | — |
| T1 rule | 50% of distance to PWH or 1.5R, whichever first | `half = entry + 0.5*dist`, `r15 = entry + 1.5*stop_dist`, `t1 = nearer` L198–200 | yes | — |
| T2 rule | PWH; trail behind each new 4h higher low after T1 | `t2 = pwh` L192/L251; intent `trail_tf="4h", trail_after="t1"` L252; runner arms at T1 L614–615, `_trail_stop` = last confirmed 4h swing low since fill if it raises the stop L689–701; M6 not in `TRAIL_REPLACES_T2` L59 so PWH still exits | deviation | D-11 (confirmed swing low) |
| partial percent | close 40% at T1 | `partial_pct = 40.0` L252; runner L607 | yes | — |
| breakeven rule | stop to breakeven at T1 | runner `stop = entry; be_done` L611–612 | yes | — |
| expected_hold_min | 1440 | `HOLD_MIN = 1440` L25, `expected_hold_min = HOLD_MIN` L43 | yes | — |
| dead-trade time stop | 36h | Mind universal 1.5 × 1440 min = 36h AND progress < 0.5R (mind L227–228) | yes | — |
| hard time stop | Friday 20:00 UTC | `friday_20()` = week_start + 4d + 20h L35–36, passed as `hard_stop_ts` L252; runner stores L384, force-closes `time_stop` L602–603 | yes | D-35 |
| one attempt per week per coin per direction | at most one | state key `m6:{wk}:{direction}` L209 (state is per model+coin), set on take L272–275 → `already_taken` veto L65 | yes | D-35 |
| seq 1: WO/PWH/PWL frozen Monday 00:00 | frozen at Mon 00:00 | runner `_jobs`: `hour == 0 and weekday(boundary) == 0` → `weekly_levels:{wk}` state once/week L437–449 (+ `M6_levels` alert); model reads frozen, falls back to live refs L118–123 | yes | D-48(c) |
| seq 2: loss = ≥1 4h close below WO Mon 00:00–Wed 23:59, excursion low ≥ 0.8 ATR(4h) | as stated | `loss4 = 4h closes < wo with ts ≤ rc1.ts and ts ≤ ws + 3d` L163–165; depth gate via `shallow_loss` veto (not a sequence miss) L214 | yes | D-35 |
| seq 3: 15m close above WO then 1h close above, Mon 12:00–Thu 23:59 | as stated | rc1 = first 1h close > wo after the last 1h close < wo L147–156; 15m close > wo required strictly between last loss 4h close and rc1.ts L171–172; `rc1.ts < ws+12h` → miss L158–159; > Thu 23:59 → `too_late` veto L173 | yes | D-48(a), D-35 |
| seq 4: OI up ≥ 1.0% over 4h; buy ratio ≥ 0.55; funding z ≤ 1.0 | as stated | `0 < oi_4h < 0.01` → miss L175–176 (≤ 0 → veto); `br < 0.55` → miss L178–179; `fz > 1.0` → miss L181–182; None passes each gate | yes | D-35 |
| seq 5: cohort net long change 24h ≥ 0 | as stated | `coh < 0` → miss L184–185 (None passes) | yes | — |
| seq 6: Mind evaluates | — | `ModelStrategy.evaluate` → `self.mind.evaluate(snap)` mb L398–443 | yes | — |
| alert Monday 00:00 with frozen levels | required (§5) | runner `M6_levels` outbox L445–448 | yes | — |
| alert when weekly open is lost | required (§5) | `pre_alerts` `M6_pre` on 4h close crossing below (and mirror above) L232–245 | yes | — |
| thesis template | "…Lost by {depth} ATR earlier this week…" | L256–262; emits `Lost by 1.23 earlier this week` (literal word "ATR" dropped; mirror wording "Held above") | no (text only) | — |
| warm-up gate | not in doc | `len(c1h) < 48 or len(c4h) < 30 or any ATR ≤ 0` → miss L116–117 | not in doc | — |
| reclaim age cap | not in doc | rc1 older than `now − 12h` (4h wait + 8h validity) → miss L160–161 | not in doc | — |
| timing 0.3 Fri–Sun | not in doc | `else 0.3` L203 (unreachable to a TAKE because of `too_late`) | not in doc | — |
| pwh_untested 0.0 when tested ≥ 2 | not in doc | L227 | not in doc | — |
| "price already beyond PWH" / "no prior-week extreme" misses | not in doc | L193–197 | not in doc | — |
| last 1h close must still be above WO at evaluation | not in doc | L148–149 | not in doc | — |
| mirror shorts pre-alert `lost_above` | not in doc (mirror implied) | L243–244 | not in doc | — |
| missing-data handling | not in doc | reasons read 0.0 when oi/br/fz/coh None L220–223; gates pass on None L175/178/181/184; OI/taker absence in the last hour is a runner veto `feed_missing_*` (runner L151–163) | not in doc | D-31/D-51 |

**Residual candidates not covered by D-35 / D-37 / D-48**

- **Excursion depth is week-to-date, not the loss excursion.** Doc §2 step 2: "the low of *that excursion* at least 0.8 ATR(4h) below the weekly open". Code L166–168 takes `min(c.l)` over *all* 15m candles from Monday 00:00 to the reclaim close, including candles before the qualifying 4h loss close. A deep Monday wick with no 4h close below, followed later by a marginal 4h close below and a reclaim, is graded (and passes `shallow_loss`) on the wick depth rather than the excursion belonging to the 4h loss.
- **Entry validity and reprice anchored to evaluation time, not the 1h close.** Doc §3: "Valid 8 hours" / "if no pullback within 4 hours" are relative to the 1h close. Code L252 sets `entry_valid_until = snap.now_ms + 8h`, while L160 lets the same setup be found up to 12h after `rc1.ts`. If the TAKE happens on a later candle (e.g. an `event_30m` or `funding_extreme` veto clearing), the order rests up to 8h beyond the doc window, and `reprice_at = rc1.ts + 4h` (L218) is already past, so runner L526 re-prices to 1h-close + 0.1 ATR immediately, skipping the pullback wait.
- **`oi_4h` None bypasses the OI gate when the tape exists but is short.** `snap.oi_change(rc1.ts−4h, rc1.ts)` returns None when there is no OI reading at/before `rc1.ts−4h` (snapshot.py L74–80). The runner `feed_missing_oi` veto only checks rows in the last hour (runner L158–160), so a tape shorter than 4h passes both the ≥1% sequence gate (L175) and the `oi_falling` veto (L68) with `oi_commitment = 0`. Doc step 4 makes OI ≥ 1% a hard requirement.
- **Prior-week multiplier reads live refs, not the frozen levels.** `_prior_week_mult` L101 uses `snap.s.refs["pw_open"/"pw_close"]`, recomputed from whatever 1h candles exist in the buffer (pools.py L84–87), while `_jobs` freezes `pw_open`/`pw_close` into `weekly_levels:{wk}` (runner L442–443) and the model never reads them. Doc step 1 says the levels are frozen at Monday 00:00; a short 1h buffer late in the week would yield a partial prior week.
- **`cohort_net_dir_entry` is captured at setup time, not at fill.** L228 stores `snap.cohort_net_dir` when the setup is evaluated; the order can fill up to 8h (or more, see above) later. Doc §4.5 `cohort_flip`: "cohort net long change *since entry*".
- **Thesis text drops the literal "ATR".** Doc §4.6 "Lost by {depth} ATR earlier this week"; code L259 renders `Lost by 1.23 earlier this week`. Cosmetic.

### 1.7 Residual "no" rows — examined, NOT changed (interpretations / feature gaps)

Rule applied: fix only a formula that does not match the doc, a direction error, a constant detector, an undocumented veto or a
missing wiring. Anything that would require inventing a tolerance the doc does not give, or data the feeds do not carry, is
recorded here instead.

| model | item | why it was left |
|---|---|---|
| M1 | step 5b "OI flat or rising on the reclaim candle" | no doc Reason and no tolerance; a strict `< 0` gate on one 15m OI print would be an invented rule. Stored as `oi_change_reclaim` in the setup, not gated. |
| M1 | `fuel` window: §Reasons says "5m", §7 prompt says "in the sweep window" | doc self-contradiction; code uses wick-open → reclaim-close (the §7 wording). |
| M1 | `cluster_below_uncleared` "cluster not reduced" | `strat_liquidations` carries no cluster-formation/reduction history; implemented as "cluster still present below the level". |
| M1 | `_range_for` uses ATR(4h) for the "range > 6 ATR → use 1h" switch | doc 11 says "6 ATR" without a timeframe; 4h range measured in ATR(4h) is the consistent reading. |
| M1 | T2 = hard exit at the second target (D-11) | doc 11 leaves the runner after T2 unspecified; kept. |
| M2 | zone straddling the 50% line | doc's own veto says "entirely in the premium half"; a straddling zone is allowed. |
| M2 | `displacement_against` unreachable once the ≥1.0-ATR retrace gate has passed | same overlap exists in the doc (step 3 vs veto); kept both. |
| M2 | `oi_break` None passes the ≥ −0.5% gate | OI None only when the OI feed is missing → `feed_missing_oi` vetoes first. |
| M2 | T2 pool = nearest pool of ANY type above | doc "1h pool (equal highs, prior day high)" — the nearest pool is normally one of those; not narrowed. |
| M2 | "OB top if no FVG" entry fallback not implemented | conflicts with doc step 2 (an FVG is required for the break to qualify) — D-44. |
| M2 | 15m 2-candle displacement sequence not implemented | feature gap (only 1h displacement qualifies a break), not a wrong formula — D-44. |
| M3 | step 5 (fresh liq cluster above + book depth < 70%) graded, not gated | book is an OPTIONAL feed (owner rule, D-51); gating on it would veto on feed absence. |
| M3 | `first_failure_random_level` veto never fires | the condition is enforced earlier as a sequence miss (the level must be a pre-session high); dead veto kept for doc parity. |
| M3 | `squeeze_risk` short share = cohort notional share | no venue-wide long/short OI split in any feed. |
| M3 | extra strictness: close below level, retest wick cap, 3-candle retest window | tighter than doc, never looser; recorded. |
| M3 | OI None passes the gate | as M2. |
| M4 | `extension` measured to the pre-CHoCH close; `choch_quality` uses current ATR(4h) | doc gives no anchor; documented. |
| M4 | thesis `stop_level` = entry swing | doc's placeholder wording; value is the real stop swing. |
| M4 | funding gate None passes | gauge is optional; reason reads 0. |
| M4 | T2 partial (D-36) | unspecified in doc; kept. |
| M5 | step 5 (delta/OI confirmation) graded | same as D-42 (doc lists them under Reasons). |
| M5 | §1 "bias favours the reversal" not a gate | bias is graded per the owner's rule (no undocumented bias veto). |
| M5 | squeeze multiplier direction from `funding_z` sign | doc says "squeeze potential in the trade direction"; sign of funding is the only directional squeeze input. |
| M5 | `open_location` far-edge tiers 1.0 / 0.3 | doc gives the near-edge formula only; far-edge values are an interpolation, recorded. |
| M6 | `oi_4h` None on a short tape passes | as M2. |

## Part 2 — Mirror correctness

`tests/strategy_engine/test_models_mirror.py::test_short_is_exact_mirror_of_long` captures the exact inputs of each long-side
fixture in `test_models_m1_m6.py` and rebuilds the snapshot on the mirror: prices → `2·pivot − price` (high/low swapped),
taker buy/sell notionals swapped (ratio → 1 − ratio), long/short liquidation rows swapped, cohort `net_dir`/`net_dir_24h_ago`
negated and `fresh_long/short`, `long/short_notional` swapped, positions side swapped with mirrored `liq_px`, book bids/asks
swapped (`bid_0_x ↔ ask_0_x`) with mirrored mid, funding (rows, gauge `funding_z`, `gauge_24h`) negated, gauge
`blocked_direction` swapped, `day_type_override` trend_up ↔ trend_down. OI is unchanged — no doc mirrors an OI-direction
condition ("OI rising on the break" / "OI drop" read the same on both sides). Structure (trends, daily bias, ranges, zones,
pools) is recomputed from the mirrored candles, so `daily_bias`, `trend("1d")` and the 20-day midpoint mirror automatically.

Asserted per case: `take`, `conviction`, `raw_conviction`, `size_tier`, every reason strength (1e-6), the multiplier set, the
veto set, and the intent (direction, entry / stop / T1 / T2 / T3, partial %, trail_after, validity and hard-stop timestamps) as mirror images. `test_mirror_helpers_are_involutions`
checks the transform is its own inverse. 16 cases:

| model | cases | result | asymmetry found | fix |
|---|---|---|---|---|
| M1 | m1_valid (take), m1_too_deep, m1_third_sweep | mirror | `delta_flip` read the BUY ratio for both directions (a short reclaim with 70 % sell ratio scored 0) | D-50: `sgn(direction)·(ratio − 0.5)/0.25` |
| M2 | m2_valid, m2_short_covering | mirror | `delta_break` and `funding_young` read the long-side value for shorts (found in the Part 1 review, confirmed by the mirror run) | D-44 (a) |
| M3 | m3_valid, m3_acceptance, m3_squeeze_risk | mirror | none | — |
| M4 | m4_valid (percent-space note), m4_cohort_adding | mirror | none | — |
| M5 | m5_valid, m5_opened_outside, m5_range_bad | mirror | none | — |
| M6 | m6_valid, m6_shallow_loss, m6_oi_falling | mirror | none | — |

Owner's special-attention items, checked in code (all written with a single `lo`/`hi` branch, so the two sides share one
formula):

| item | where | symmetric because |
|---|---|---|
| M2 `below_ob` (in-trade, counts 2) | `mb.check_close_beyond_level("ob_edge")` | compares the last 15m close against `ob_edge` on the trade's wrong side using the position direction; the mirror pair `m2_valid` sets `ob_edge` as the mirrored value |
| M3 `acceptance` | `m3_acceptance` mirror case | 2 closes beyond the level: `c > level` for the short version, `c < level` for the long — asserted equal veto set |
| M3 `thin_bids` | `m3_failed_auction.py:257` | reads `bid_0_3` for a short (bids under a failed high) and `ask_0_3` for a long; the mirror swaps bid/ask columns — strength equal |
| M4 `daily_strong_against` | `m4_htf_choch.py:210` | `daily_tr == old` and `choch.level > mid20` for a high-CHoCH short / `< mid20` for a low-CHoCH long; `test_m4_daily_strong_against_veto` |
| M5 `trend_day_with_raid` | `m5_session_liquidity_run.py:72` | `day_type == day_trend_for(opposite(direction))` — trend_down + low raid ↔ trend_up + high raid |
| M5 `open_location` | `m5_session_liquidity_run.py:170–183` | position in the Asia range: middle 1.0, near the raided edge 0.7 (`pos < 0.2 and lo` / `pos > 0.8 and not lo`), far edge 1.0, outside 0.3 — the raided edge flips with `lo` |
| M6 `daily_strong_down` | `m6_weekly_open_reclaim.py:219` | `daily_tr == ("down" if lo else "up")` and price beyond the 20-day midpoint on the trade's wrong side |

Notes (D-50): M4 `extension` is a PERCENT move ("8 % in 3 days"); a price-space mirror is not a percent-space mirror
(80→120 = +50 %, 120→80 = −33 %), so the test checks each side against the doc formula on its own move and compares the raw
conviction with that reason excluded. M4 T2 is a liquidation-cluster level whose 0.25 % fixed-grid bands can group two liq
prices differently on the mirror — compared within one band width. Everything else is bit-exact.

## Part 3 — Detector health on real data

Source: `strat_signals` rows for M1–M6 on prod (`perpl_terminal`) since deploy `94ae37e` (2026-09-05 22:45:30 UTC) to
2026-09-06 06:15 UTC = **372 evaluation rows (62 per model, 31 per coin)**, produced by the PRE-audit code. Stats script:
`audit_stats.py` (mean / sd / min / max / frac@0 / frac@1 per reason, flag = sd 0 over > 40 rows or ≥ 95 % at exactly 0 or 1).

### 3.1 Reason statistics — all rows (prod, 7.5 h)

Every setup-derived reason is flagged SUSPECT with sd = 0 and frac@0 = 1.00, and that is **by construction, not a detector
fault**: none of the 372 evaluations reached a setup (all carry the `no_setup` veto), and `model_base.evaluate` runs the Mind on
an EMPTY setup so that a row exists for every boundary — every `S("…_strength")` reason reads `setup.get(key, 0.0)` = 0.
The only reasons that vary on a no-setup row are the ones that read the Snapshot directly:

| model | reason | prod stats | reading |
|---|---|---|---|
| M1 | `session` | mean 0.268, sd 0.074, min 0.10, max 0.30 | correct — the window was Asia (0.3) then dead (0.1); no London/NY boundary in the sample |
| M2 | `funding_young` | mean 0.745, sd 0.158, 0.567–1.0 | reads `gauge.funding_z` as `clip((FZ_MAX − z)/FZ_MAX)` with the default long sign; varies with the live gauge — plausible |
| M2 | `oi_holding` | 1.000 constant | reads `setup.oi_since_break`, which is None without a setup → defaults to 1.0 (D-44: "None → 1.0; unreachable on real rows because a break always carries an OI reading"). Not an input fault; on rows WITH a setup it is graded. |

Trace of the inputs (Snapshot on the same boundaries, Part 4 dump): OI present (`oi_now` 2.80 B on BTC), 59 taker rows in the last hour,
book mid present, gauge `funding_z` present, cohort `net_dir` present, liquidation rows present. **No input is missing,
constant or mis-scaled**; the 0s are the absence of a qualifying setup in 7.5 h on two coins, which is the expected order of
magnitude (Part 8 gives the 30-day picture: setups appear at a rate of a few per model-coin-week).

The full prod table is reproduced below for the record.

### 3.2 Prod table — all rows (pre-audit code, 372 rows)

#### Part 3 reason health — all rows

##### M1 (62 rows)
| reason | n | mean | sd | min | max | frac@0 | frac@1 | flag |
|---|---|---|---|---|---|---|---|---|
| location | 62 | 0.000 | 0.000 | 0.000 | 0.000 | 1.00 | 0.00 | SUSPECT |
| reclaim | 62 | 0.000 | 0.000 | 0.000 | 0.000 | 1.00 | 0.00 | SUSPECT |
| fuel | 62 | 0.000 | 0.000 | 0.000 | 0.000 | 1.00 | 0.00 | SUSPECT |
| cleared | 62 | 0.000 | 0.000 | 0.000 | 0.000 | 1.00 | 0.00 | SUSPECT |
| delta_flip | 62 | 0.000 | 0.000 | 0.000 | 0.000 | 1.00 | 0.00 | SUSPECT |
| absorption | 62 | 0.000 | 0.000 | 0.000 | 0.000 | 1.00 | 0.00 | SUSPECT |
| discount | 62 | 0.000 | 0.000 | 0.000 | 0.000 | 1.00 | 0.00 | SUSPECT |
| session | 62 | 0.268 | 0.074 | 0.100 | 0.300 | 0.00 | 0.00 |  |
| cohort | 62 | 0.000 | 0.000 | 0.000 | 0.000 | 1.00 | 0.00 | SUSPECT |
| htf_bias | 62 | 0.000 | 0.000 | 0.000 | 0.000 | 1.00 | 0.00 | SUSPECT |

##### M2 (62 rows)
| reason | n | mean | sd | min | max | frac@0 | frac@1 | flag |
|---|---|---|---|---|---|---|---|---|
| oi_new_positioning | 62 | 0.000 | 0.000 | 0.000 | 0.000 | 1.00 | 0.00 | SUSPECT |
| displacement | 62 | 0.000 | 0.000 | 0.000 | 0.000 | 1.00 | 0.00 | SUSPECT |
| zone_discount | 62 | 0.000 | 0.000 | 0.000 | 0.000 | 1.00 | 0.00 | SUSPECT |
| retrace_calm | 62 | 0.000 | 0.000 | 0.000 | 0.000 | 1.00 | 0.00 | SUSPECT |
| oi_holding | 62 | 1.000 | 0.000 | 1.000 | 1.000 | 0.00 | 1.00 | SUSPECT |
| delta_break | 62 | 0.000 | 0.000 | 0.000 | 0.000 | 1.00 | 0.00 | SUSPECT |
| funding_young | 62 | 0.745 | 0.158 | 0.567 | 1.000 | 0.00 | 0.02 |  |
| htf_agree | 62 | 0.000 | 0.000 | 0.000 | 0.000 | 1.00 | 0.00 | SUSPECT |
| nested_sweep | 62 | 0.000 | 0.000 | 0.000 | 0.000 | 1.00 | 0.00 | SUSPECT |
| cluster_cleared | 62 | 0.000 | 0.000 | 0.000 | 0.000 | 1.00 | 0.00 | SUSPECT |

##### M3 (62 rows)
| reason | n | mean | sd | min | max | frac@0 | frac@1 | flag |
|---|---|---|---|---|---|---|---|---|
| trap | 62 | 0.000 | 0.000 | 0.000 | 0.000 | 1.00 | 0.00 | SUSPECT |
| cvd_divergence | 62 | 0.000 | 0.000 | 0.000 | 0.000 | 1.00 | 0.00 | SUSPECT |
| location | 62 | 0.000 | 0.000 | 0.000 | 0.000 | 1.00 | 0.00 | SUSPECT |
| failure_quality | 62 | 0.000 | 0.000 | 0.000 | 0.000 | 1.00 | 0.00 | SUSPECT |
| thin_bids | 62 | 0.000 | 0.000 | 0.000 | 0.000 | 1.00 | 0.00 | SUSPECT |
| cluster_fuel | 62 | 0.000 | 0.000 | 0.000 | 0.000 | 1.00 | 0.00 | SUSPECT |
| funding_up | 62 | 0.000 | 0.000 | 0.000 | 0.000 | 1.00 | 0.00 | SUSPECT |
| premium | 62 | 0.000 | 0.000 | 0.000 | 0.000 | 1.00 | 0.00 | SUSPECT |
| second_failure | 62 | 0.000 | 0.000 | 0.000 | 0.000 | 1.00 | 0.00 | SUSPECT |
| day_type_fit | 62 | 0.000 | 0.000 | 0.000 | 0.000 | 1.00 | 0.00 | SUSPECT |

##### M4 (62 rows)
| reason | n | mean | sd | min | max | frac@0 | frac@1 | flag |
|---|---|---|---|---|---|---|---|---|
| cohort_reducing | 62 | 0.000 | 0.000 | 0.000 | 0.000 | 1.00 | 0.00 | SUSPECT |
| oi_extreme | 62 | 0.000 | 0.000 | 0.000 | 0.000 | 1.00 | 0.00 | SUSPECT |
| funding_elevated | 62 | 0.000 | 0.000 | 0.000 | 0.000 | 1.00 | 0.00 | SUSPECT |
| choch_quality | 62 | 0.000 | 0.000 | 0.000 | 0.000 | 1.00 | 0.00 | SUSPECT |
| weak_bounce | 62 | 0.000 | 0.000 | 0.000 | 0.000 | 1.00 | 0.00 | SUSPECT |
| extension | 62 | 0.000 | 0.000 | 0.000 | 0.000 | 1.00 | 0.00 | SUSPECT |
| zone_quality | 62 | 0.000 | 0.000 | 0.000 | 0.000 | 1.00 | 0.00 | SUSPECT |
| cluster_reward | 62 | 0.000 | 0.000 | 0.000 | 0.000 | 1.00 | 0.00 | SUSPECT |
| delta_flip | 62 | 0.000 | 0.000 | 0.000 | 0.000 | 1.00 | 0.00 | SUSPECT |
| divergence | 62 | 0.000 | 0.000 | 0.000 | 0.000 | 1.00 | 0.00 | SUSPECT |

##### M5 (62 rows)
| reason | n | mean | sd | min | max | frac@0 | frac@1 | flag |
|---|---|---|---|---|---|---|---|---|
| bias_alignment | 62 | 0.000 | 0.000 | 0.000 | 0.000 | 1.00 | 0.00 | SUSPECT |
| range_quality | 62 | 0.000 | 0.000 | 0.000 | 0.000 | 1.00 | 0.00 | SUSPECT |
| reclaim | 62 | 0.000 | 0.000 | 0.000 | 0.000 | 1.00 | 0.00 | SUSPECT |
| fuel | 62 | 0.000 | 0.000 | 0.000 | 0.000 | 1.00 | 0.00 | SUSPECT |
| cleared | 62 | 0.000 | 0.000 | 0.000 | 0.000 | 1.00 | 0.00 | SUSPECT |
| delta_flip | 62 | 0.000 | 0.000 | 0.000 | 0.000 | 1.00 | 0.00 | SUSPECT |
| early_in_window | 62 | 0.000 | 0.000 | 0.000 | 0.000 | 1.00 | 0.00 | SUSPECT |
| open_location | 62 | 0.000 | 0.000 | 0.000 | 0.000 | 1.00 | 0.00 | SUSPECT |
| no_pre_drift | 62 | 0.000 | 0.000 | 0.000 | 0.000 | 1.00 | 0.00 | SUSPECT |
| cohort | 62 | 0.000 | 0.000 | 0.000 | 0.000 | 1.00 | 0.00 | SUSPECT |

##### M6 (62 rows)
| reason | n | mean | sd | min | max | frac@0 | frac@1 | flag |
|---|---|---|---|---|---|---|---|---|
| loss_depth | 62 | 0.000 | 0.000 | 0.000 | 0.000 | 1.00 | 0.00 | SUSPECT |
| oi_commitment | 62 | 0.000 | 0.000 | 0.000 | 0.000 | 1.00 | 0.00 | SUSPECT |
| delta_reclaim | 62 | 0.000 | 0.000 | 0.000 | 0.000 | 1.00 | 0.00 | SUSPECT |
| funding_room | 62 | 0.000 | 0.000 | 0.000 | 0.000 | 1.00 | 0.00 | SUSPECT |
| cohort | 62 | 0.000 | 0.000 | 0.000 | 0.000 | 1.00 | 0.00 | SUSPECT |
| reward | 62 | 0.000 | 0.000 | 0.000 | 0.000 | 1.00 | 0.00 | SUSPECT |
| timing | 62 | 0.000 | 0.000 | 0.000 | 0.000 | 1.00 | 0.00 | SUSPECT |
| htf_bias | 62 | 0.000 | 0.000 | 0.000 | 0.000 | 1.00 | 0.00 | SUSPECT |
| reclaim_quality | 62 | 0.000 | 0.000 | 0.000 | 0.000 | 1.00 | 0.00 | SUSPECT |
| pwh_untested | 62 | 0.000 | 0.000 | 0.000 | 0.000 | 1.00 | 0.00 | SUSPECT |

### 3.3 Prod veto hit rates and conviction histograms (pre-audit code)

#### Veto hit rates, conviction histograms (non-vetoed rows)

##### M1: 62 evaluations, 0 non-vetoed
| veto | hits | rate |
|---|---|---|
| no_setup | 62 | 100.0% |

| bucket | raw_conviction | conviction |
|---|---|---|
| 0.0 | 0 | 0 |
| 0.1 | 0 | 0 |
| 0.2 | 0 | 0 |
| 0.3 | 0 | 0 |
| 0.4 | 0 | 0 |
| 0.5 | 0 | 0 |
| 0.6 | 0 | 0 |
| 0.7 | 0 | 0 |
| 0.8 | 0 | 0 |
| 0.9 | 0 | 0 |
| 1.0 | 0 | 0 |

non-vetoed with conviction >= 0.55: 0; fired=0; tiers={}

top waiting_for:
| waiting_for (truncated) | n |
|---|---|
| no reclaimed sweep of an eligible low on this candle; no reclaimed sweep of an eligible hi | 56 |
| daily bias up against a short (sweep of 4h_fvg_top seen) | 2 |
| daily bias up against a short (sweep of london_high seen) | 1 |
| no reclaimed sweep of an eligible low on this candle; 4h_fvg_top reclaim quality 0.13 < 0. | 1 |
| no reclaimed sweep of an eligible low on this candle; 4h_fvg_top reclaim quality 0.48 < 0. | 1 |
| daily bias up against a short (sweep of 4h_ob_top seen) | 1 |

| coin | evaluations | setup reached | fired |
|---|---|---|---|
| BTC | 31 | 0 | 0 |
| ETH | 31 | 0 | 0 |

##### M2: 62 evaluations, 0 non-vetoed
| veto | hits | rate |
|---|---|---|
| no_setup | 62 | 100.0% |
| late_day | 8 | 12.9% |

| bucket | raw_conviction | conviction |
|---|---|---|
| 0.0 | 0 | 0 |
| 0.1 | 0 | 0 |
| 0.2 | 0 | 0 |
| 0.3 | 0 | 0 |
| 0.4 | 0 | 0 |
| 0.5 | 0 | 0 |
| 0.6 | 0 | 0 |
| 0.7 | 0 | 0 |
| 0.8 | 0 | 0 |
| 0.9 | 0 | 0 |
| 1.0 | 0 | 0 |

non-vetoed with conviction >= 0.55: 0; fired=0; tiers={}

top waiting_for:
| waiting_for (truncated) | n |
|---|---|
| 4h trend range, no fresh 4h CHoCH up confirmed by 1h BOS (long); 4h trend range, no fresh  | 31 |
| break buy ratio 0.54 not ≥ 0.65; 4h trend up, no fresh 4h CHoCH down confirmed by 1h BOS ( | 14 |
| zone from the break at 2,484.7 already broken/filled; 4h trend up, no fresh 4h CHoCH down  | 13 |
| displacement at 2,493.0 left no 1h FVG; 4h trend up, no fresh 4h CHoCH down confirmed by 1 | 4 |

| coin | evaluations | setup reached | fired |
|---|---|---|---|
| BTC | 31 | 0 | 0 |
| ETH | 31 | 0 | 0 |

##### M3: 62 evaluations, 0 non-vetoed
| veto | hits | rate |
|---|---|---|
| no_setup | 62 | 100.0% |

| bucket | raw_conviction | conviction |
|---|---|---|
| 0.0 | 0 | 0 |
| 0.1 | 0 | 0 |
| 0.2 | 0 | 0 |
| 0.3 | 0 | 0 |
| 0.4 | 0 | 0 |
| 0.5 | 0 | 0 |
| 0.6 | 0 | 0 |
| 0.7 | 0 | 0 |
| 0.8 | 0 | 0 |
| 0.9 | 0 | 0 |
| 1.0 | 0 | 0 |

non-vetoed with conviction >= 0.55: 0; fired=0; tiers={}

top waiting_for:
| waiting_for (truncated) | n |
|---|---|
| no swing-failure + retest at a pre-session high on this candle; price in the premium of th | 22 |
| price in the discount of the 4h range (pct 0.35) — no failed-high short; no swing-failure  | 10 |
| price in the discount of the 4h range (pct 0.32) — no failed-high short; no swing-failure  | 5 |
| price in the discount of the 4h range (pct 0.44) — no failed-high short; no swing-failure  | 4 |
| price in the discount of the 4h range (pct 0.34) — no failed-high short; no swing-failure  | 4 |
| price in the discount of the 4h range (pct 0.37) — no failed-high short; no swing-failure  | 4 |
| price in the discount of the 4h range (pct 0.43) — no failed-high short; no swing-failure  | 2 |
| price in the discount of the 4h range (pct 0.33) — no failed-high short; no swing-failure  | 2 |

| coin | evaluations | setup reached | fired |
|---|---|---|---|
| BTC | 31 | 0 | 0 |
| ETH | 31 | 0 | 0 |

##### M4: 62 evaluations, 0 non-vetoed
| veto | hits | rate |
|---|---|---|
| no_setup | 62 | 100.0% |

| bucket | raw_conviction | conviction |
|---|---|---|
| 0.0 | 0 | 0 |
| 0.1 | 0 | 0 |
| 0.2 | 0 | 0 |
| 0.3 | 0 | 0 |
| 0.4 | 0 | 0 |
| 0.5 | 0 | 0 |
| 0.6 | 0 | 0 |
| 0.7 | 0 | 0 |
| 0.8 | 0 | 0 |
| 0.9 | 0 | 0 |
| 1.0 | 0 | 0 |

non-vetoed with conviction >= 0.55: 0; fired=0; tiers={}

top waiting_for:
| waiting_for (truncated) | n |
|---|---|
| no 4h CHoCH down in the last 72h; no 4h CHoCH up in the last 72h | 62 |

| coin | evaluations | setup reached | fired |
|---|---|---|---|
| BTC | 31 | 0 | 0 |
| ETH | 31 | 0 | 0 |

##### M5: 62 evaluations, 0 non-vetoed
| veto | hits | rate |
|---|---|---|
| no_setup | 62 | 100.0% |

| bucket | raw_conviction | conviction |
|---|---|---|
| 0.0 | 0 | 0 |
| 0.1 | 0 | 0 |
| 0.2 | 0 | 0 |
| 0.3 | 0 | 0 |
| 0.4 | 0 | 0 |
| 0.5 | 0 | 0 |
| 0.6 | 0 | 0 |
| 0.7 | 0 | 0 |
| 0.8 | 0 | 0 |
| 0.9 | 0 | 0 |
| 1.0 | 0 | 0 |

non-vetoed with conviction >= 0.55: 0; fired=0; tiers={}

top waiting_for:
| waiting_for (truncated) | n |
|---|---|
| outside the London 07–09 / New York 13–15 UTC windows | 62 |

| coin | evaluations | setup reached | fired |
|---|---|---|---|
| BTC | 31 | 0 | 0 |
| ETH | 31 | 0 | 0 |

##### M6: 62 evaluations, 0 non-vetoed
| veto | hits | rate |
|---|---|---|
| no_setup | 62 | 100.0% |

| bucket | raw_conviction | conviction |
|---|---|---|
| 0.0 | 0 | 0 |
| 0.1 | 0 | 0 |
| 0.2 | 0 | 0 |
| 0.3 | 0 | 0 |
| 0.4 | 0 | 0 |
| 0.5 | 0 | 0 |
| 0.6 | 0 | 0 |
| 0.7 | 0 | 0 |
| 0.8 | 0 | 0 |
| 0.9 | 0 | 0 |
| 1.0 | 0 | 0 |

non-vetoed with conviction >= 0.55: 0; fired=0; tiers={}

top waiting_for:
| waiting_for (truncated) | n |
|---|---|
| 1h reclaim at 2026-09-03 10:59 is older than the entry window; 1h not closed below weekly  | 31 |
| 1h reclaim at 2026-09-03 12:59 is older than the entry window; 1h not closed below weekly  | 31 |

| coin | evaluations | setup reached | fired |
|---|---|---|---|
| BTC | 31 | 0 | 0 |
| ETH | 31 | 0 | 0 |

### 3.4 Suspect reasons on rows WITH a setup — input trace and verdict

Prod has no setup rows yet (0 of 372), so the "rows WITH a setup" analysis uses the 30-day replay (pass 2, fixed tree,
`--no-feed-veto`, 34 572 rows: M1 35 setup rows on 5 762 evaluations, M3 10, M5 37, M6 450, M2/M4 0) and the feed-era replay
(pass 3 / pass 4, 2026-09-03 13:30 → 2026-09-06 06:15, 520 evaluations per model: M1 10 setup rows, M5 2). Rule: a reason
is suspect when sd = 0 over > 40 rows or ≥ 95 % of rows sit at exactly 0 or 1. Each suspect reason was traced to its
Snapshot input.

| model | reason (weight) | stats on setup rows | input traced | verdict | action |
|---|---|---|---|---|---|
| M1 | `fuel` (1.5) | pass 2: 35 rows, 0.000 everywhere; feed era: 10 rows, mean 0.008, max 0.043 | `snap.liq(side, wick_open, reclaim_close)` / `oi_frac(0.10)`. `strat_liquidations` has rows for the whole 30 days (BTC 5 202, ETH 2 170, `coverage=partial`); the normaliser 0.10 % of OI = **$2.8 M (BTC) / $2.2 M (ETH)** per sweep window. Observed same-side liquidation notional per 5-minute window on prod: BTC long max $1.75 M, mean $139 k; ETH long max $948 k, mean $85 k; BTC short max $26.9 M, mean $443 k; ETH short max $7.0 M, mean $287 k. | genuinely near 0 in this market with the doc's normaliser — the input is present and correctly scaled (notional USD / notional USD); the doc's denominator is 10–20× the typical burst. Not a code defect. | none (doc number). Reported as Part 8 finding F-5. |
| M1 | `cleared` (1.2), `delta_flip` (1.5), `absorption` (1.0) | pass 2: 0.011 / 0.020 / 0.019 (≥ 95 % at 0); feed era: 0.233 / 0.414 / 0.400, 20–30 % at 1 | `cleared` = `clip(−OI_change_during_sweep / 1 %)` (`strat_oi_1m`, **absent before 2026-09-03 14:17** → `oi_change` None → 0); `delta_flip` = taker buy/sell ratio on the reclaim candle (`strat_trades_1m`, absent before 09-03); `absorption` = CVD at the sweep low vs the prior swing low (taker feed, absent before 09-03). | 0 by feed absence for 27 of 30 replay days; on the 3 feed-era days all three vary 0 → 1 (`cleared` 1.0 on 2 of 10 rows = an OI drop ≥ 1 % over the sweep, `delta_flip` 0.28–1.0, `absorption` 0.5 / 1.0 on half the rows). | none. Healthy where the input exists. |
| M1 | `cohort` (1.0) | feed era: 10 rows, 0.067 mean, 9 of 10 at 0 | `cohort.net_dir` sign vs direction; the replay uses ONE cohort (built at replay start on 2026-09-06, net_dir +0.55 BTC / +1.00 ETH) for every boundary — short setups read 0 unless the 24h cohort change happens to favour them (9 of the 10 feed-era M1 setups were shorts; 1 read 0.667). | replay artefact (harness patch 1), not a detector fault; on prod the cohort is rebuilt each cycle. | none. |
| M3 | `trap`, `cvd_divergence`, `thin_bids`, `cluster_fuel` | pass 2: 10 rows, all 0.000 | `trap` = OI change from the failed high (OI feed), `cvd_divergence` = taker CVD slope (taker feed), `thin_bids` = book depth (book feed), `cluster_fuel` = largest same-side liquidation cluster within 1.5 ATR beyond the level vs 0.10 % of OI (liq feed present; same $2.2–2.8 M normaliser as M1 `fuel`, so a cluster reads ≥ 0.1 only when it exceeds ~$250 k). All 10 setup rows are pre-feed (August). | 0 by feed absence (3 of 4) / doc normaliser vs cluster size (1 of 4). No M3 setup exists in the feed era to confirm the graded path on real rows; `test_models_m1_m6.py::m3_valid` covers it synthetically (trap 1.0, cvd 0.67, thin_bids 0.5, cluster_fuel 1.0). | none. |
| M3 | `day_type_fit` | pass 2: 10 rows, constant 1.0 | reads `day_type == "range"`; all 10 rows fall on pre-feed days, which are "range" by construction (D-62: no OI/taker → no trend/squeeze/event label). | constant by construction of the pre-feed window; on the feed era M3 reaches no setup at all (§3.6: "day type no_trade / event is not range" 137 rows, "price in the discount" 267 rows). | none. |
| M5 | `reclaim` (1.8) | pass 2: 37 rows, 0.000; feed era: 2 rows, 0.000 | detector was the bare `mb.reclaim_strength` (takes a setup dict) receiving the Snapshot → raised → `Mind.evaluate` swallowed it as strength 0. Mirror test could not see it (0 == 0). | **code defect** | **fixed, D-63** (`lambda s: mb.reclaim_strength(s.setup)`, same as M1); new test `test_every_reason_detector_runs_without_raising_on_a_valid_setup` runs every reason and veto detector of every model on a valid setup and asserts M5 `reclaim` > 0. Pass 4 (feed era, D-63 tree) re-run below. |
| M5 | `fuel` (1.3), `cleared` (1.0), `delta_flip` (1.3) | pass 2: 0.000 / 0.001 / 0.027 | `fuel` = same-side liq over the raid vs 0.08 % of OI (**$2.2 M BTC / $1.8 M ETH**) — same picture as M1 `fuel`; `cleared` = OI fell during the raid (OI feed); `delta_flip` = taker ratio on the reclaim (taker feed) — both pre-feed for 35 of 37 rows. | `fuel` genuinely near 0 with the doc's denominator; `delta_flip` / `cleared` 0 by feed absence. | none (doc numbers). |
| M5 | `cohort` (0.8) | feed era: 2 rows at 0 | same single-cohort artefact as M1 (both feed-era M5 raids were on the side the current cohort opposes). | replay artefact | none. |
| M6 | `oi_commitment` (2.0), `delta_reclaim` (1.5) | pass 2: 450 rows, 0.000 | `oi_commitment` = OI change over the reclaim (OI feed), `delta_reclaim` = taker ratio on the reclaim (taker feed). Every M6 setup row in the window belongs to a weekly-open reclaim of the four August weeks, all before the feeds started (09-03 14:17); the 08-31…09-06 week produced no reclaim inside the 8 h window (§3.6: "1h reclaim at 2026-09-03 10:59 is older than the 8h entry window"). | 0 by feed absence. The other 8 M6 reasons vary (loss_depth 0.48, funding_room 0.74, cohort 0.39, reward 0.70, timing 0.80, reclaim_quality 0.67, pwh_untested 0.73). | none. |
| M2 | all 10 reasons | prod + replay: never a setup | not a detector question — the sequence never completes (see §3.6 waiting_for and Part 8 F-6). | — | none. |
| M4 | all 10 reasons | prod + replay: never a setup | "no 4h CHoCH down/up in the last 72h" on every boundary of the 30 days — the 4h structure had no CHoCH in the window (Part 4 §4.2 lists the 1h events; the 4h series is trend-continuation throughout). | genuinely absent in this market window | none. |
| M1 | `session` (0.8) | prod: sd 0.074, 0.1–0.3 | correct (Asia / dead windows only in the 7.5 h prod sample); on the feed-era replay it spans 0.1–1.0 (mean 0.50, 17 % at 1). | healthy | none. |
| M2 | `funding_young` | prod 0.567–1.0; feed era 0.653–0.897 | reads the live `funding_z`; varies. | healthy | none. |
| M2 | `oi_holding` | 1.000 on every no-setup row | `setup.oi_since_break` None → 1.0 (D-44); graded only on setup rows, of which there are none. | unreachable path, not an input fault | none. |

Summary: one detector fault found by this part (M5 `reclaim`, D-63, fixed). Every other suspect reason traces to (a) the
OI/taker feeds not existing before 2026-09-03 14:17 UTC, (b) the doc's liquidation normaliser (0.10 % / 0.08 % of OI) being an
order of magnitude above the same-side liquidation bursts this feed records, (c) a replay-harness artefact (single cohort), or
(d) a genuinely absent structure event (no 4h CHoCH, no completed M2 chain). Nothing is mis-scaled: every ratio compares like
units (USD/USD, price/ATR, count/count).

### 3.4b Feed-era replay (pass 3, vetoes ON, D-62 tree) — the 2 non-vetoed M1 rows explained

The feed-era stats (§3.5b below) show M1 reaching 10 setups in 2.7 days, of which 2 are non-vetoed and both sit at
conviction 0.1–0.2. Row-level trace (`reasons_json` / `multipliers_json`):

| ts (UTC) | coin | dir | raw | multipliers | conviction | why |
|---|---|---|---|---|---|---|
| 2026-09-04 13:30 | BTC | short | 0.351 | day_type 0.7 (event day) × sweep_count_today 0.8 × event_2h 0.8 | 0.157 | location 0.8, reclaim 0.32, discount 0.93, session 0.6, htf_bias 0.3; fuel / cleared / delta_flip / cohort 0, absorption 0.5 |
| 2026-09-04 12:00 (vetoed `third_sweep`) | ETH | short | 0.574 | 1.1 × 1.0 × 0.8 | 0.505 | the best feed-era row: reclaim 1.0, absorption 1.0, discount 0.90, cleared 0.33, delta_flip 0.28 — blocked by the doc's third-sweep veto (level already swept twice that day) |
| 2026-09-04 14:45 (vetoed `third_sweep`, `event_30m`) | ETH | short | 0.557 | 0.7 × 0.8 × 0.8 | 0.249 | cleared 1.0, reclaim 1.0, absorption 1.0 — the 09-04 14:30 scheduled event applies the 0.7 day_type and 0.8 event_2h multipliers |

The multipliers are applied exactly as doc 11 specifies (event day 0.7, 2nd sweep 0.8, event within 2 h 0.8); the low
convictions are the doc's arithmetic on a scheduled-event day, not a wiring fault. With `fuel` structurally ~0 (F-5) and
`cohort` 0 for shorts under the current cohort, the reachable raw on the best feed-era row is 0.574 — enough for `half` only
when no multiplier is below 1.0.

### 3.5 Replay (30 days, fixed tree, pass 2) — reason statistics on rows WITH a setup

#### Part 3 reason health — rows WITH a setup

##### M1 (211 rows)
| reason | n | mean | sd | min | max | frac@0 | frac@1 | flag |
|---|---|---|---|---|---|---|---|---|
| location | 211 | 0.824 | 0.170 | 0.600 | 1.000 | 0.00 | 0.43 |  |
| reclaim | 211 | 0.651 | 0.251 | 0.219 | 1.000 | 0.00 | 0.20 |  |
| fuel | 211 | 0.000 | 0.004 | 0.000 | 0.043 | 0.99 | 0.00 | SUSPECT |
| cleared | 211 | 0.011 | 0.099 | 0.000 | 1.000 | 0.99 | 0.01 | SUSPECT |
| delta_flip | 211 | 0.020 | 0.123 | 0.000 | 1.000 | 0.97 | 0.01 | SUSPECT |
| absorption | 211 | 0.019 | 0.127 | 0.000 | 1.000 | 0.98 | 0.01 | SUSPECT |
| discount | 211 | 0.551 | 0.308 | 0.004 | 0.999 | 0.00 | 0.00 |  |
| session | 211 | 0.593 | 0.280 | 0.100 | 1.000 | 0.00 | 0.23 |  |
| cohort | 211 | 0.573 | 0.482 | 0.000 | 1.000 | 0.41 | 0.54 |  |
| htf_bias | 211 | 0.601 | 0.119 | 0.000 | 1.000 | 0.01 | 0.04 |  |

##### M2 (0 rows)
no rows

##### M3 (10 rows)
| reason | n | mean | sd | min | max | frac@0 | frac@1 | flag |
|---|---|---|---|---|---|---|---|---|
| trap | 10 | 0.000 | 0.000 | 0.000 | 0.000 | 1.00 | 0.00 | SUSPECT |
| cvd_divergence | 10 | 0.000 | 0.000 | 0.000 | 0.000 | 1.00 | 0.00 | SUSPECT |
| location | 10 | 0.940 | 0.092 | 0.800 | 1.000 | 0.00 | 0.70 |  |
| failure_quality | 10 | 0.743 | 0.298 | 0.145 | 1.000 | 0.00 | 0.40 |  |
| thin_bids | 10 | 0.000 | 0.000 | 0.000 | 0.000 | 1.00 | 0.00 | SUSPECT |
| cluster_fuel | 10 | 0.000 | 0.000 | 0.000 | 0.000 | 1.00 | 0.00 | SUSPECT |
| funding_up | 10 | 0.049 | 0.078 | 0.000 | 0.260 | 0.60 | 0.00 |  |
| premium | 10 | 0.675 | 0.252 | 0.127 | 0.999 | 0.00 | 0.00 |  |
| second_failure | 10 | 0.100 | 0.300 | 0.000 | 1.000 | 0.90 | 0.10 |  |
| day_type_fit | 10 | 1.000 | 0.000 | 1.000 | 1.000 | 0.00 | 1.00 | SUSPECT |

##### M4 (0 rows)
no rows

##### M5 (37 rows)
| reason | n | mean | sd | min | max | frac@0 | frac@1 | flag |
|---|---|---|---|---|---|---|---|---|
| bias_alignment | 37 | 0.589 | 0.174 | 0.200 | 1.000 | 0.00 | 0.08 |  |
| range_quality | 37 | 0.227 | 0.291 | 0.000 | 0.600 | 0.62 | 0.00 |  |
| reclaim | 37 | 0.000 | 0.000 | 0.000 | 0.000 | 1.00 | 0.00 | SUSPECT |
| fuel | 37 | 0.000 | 0.000 | 0.000 | 0.000 | 1.00 | 0.00 | SUSPECT |
| cleared | 37 | 0.001 | 0.004 | 0.000 | 0.026 | 0.97 | 0.00 | SUSPECT |
| delta_flip | 37 | 0.027 | 0.162 | 0.000 | 1.000 | 0.97 | 0.03 | SUSPECT |
| early_in_window | 37 | 0.730 | 0.240 | 0.300 | 1.000 | 0.00 | 0.41 |  |
| open_location | 37 | 0.635 | 0.259 | 0.300 | 1.000 | 0.00 | 0.22 |  |
| no_pre_drift | 37 | 0.520 | 0.400 | 0.000 | 1.000 | 0.27 | 0.27 |  |
| cohort | 37 | 0.568 | 0.495 | 0.000 | 1.000 | 0.43 | 0.57 |  |

##### M6 (450 rows)
| reason | n | mean | sd | min | max | frac@0 | frac@1 | flag |
|---|---|---|---|---|---|---|---|---|
| loss_depth | 450 | 0.482 | 0.455 | 0.000 | 1.000 | 0.35 | 0.35 |  |
| oi_commitment | 450 | 0.000 | 0.000 | 0.000 | 0.000 | 1.00 | 0.00 | SUSPECT |
| delta_reclaim | 450 | 0.000 | 0.000 | 0.000 | 0.000 | 1.00 | 0.00 | SUSPECT |
| funding_room | 450 | 0.736 | 0.188 | 0.320 | 1.000 | 0.00 | 0.21 |  |
| cohort | 450 | 0.385 | 0.410 | 0.000 | 1.000 | 0.48 | 0.22 |  |
| reward | 450 | 0.697 | 0.409 | 0.000 | 1.000 | 0.10 | 0.63 |  |
| timing | 450 | 0.803 | 0.312 | 0.300 | 1.000 | 0.00 | 0.71 |  |
| htf_bias | 450 | 0.564 | 0.114 | 0.200 | 0.600 | 0.00 | 0.00 |  |
| reclaim_quality | 450 | 0.672 | 0.310 | 0.029 | 1.000 | 0.00 | 0.24 |  |
| pwh_untested | 450 | 0.729 | 0.397 | 0.000 | 1.000 | 0.19 | 0.65 |  |

### 3.6 Replay (pass 2) — veto hit rates, conviction histograms, waiting_for, per coin

#### Veto hit rates, conviction histograms (non-vetoed rows)

##### M1: 5762 evaluations, 35 non-vetoed
| veto | hits | rate |
|---|---|---|
| no_setup | 5551 | 96.3% |
| third_sweep | 129 | 2.2% |
| too_deep | 111 | 1.9% |
| event_30m | 8 | 0.1% |

| bucket | raw_conviction | conviction |
|---|---|---|
| 0.0 | 0 | 0 |
| 0.1 | 0 | 2 |
| 0.2 | 1 | 5 |
| 0.3 | 13 | 18 |
| 0.4 | 18 | 7 |
| 0.5 | 3 | 1 |
| 0.6 | 0 | 2 |
| 0.7 | 0 | 0 |
| 0.8 | 0 | 0 |
| 0.9 | 0 | 0 |
| 1.0 | 0 | 0 |

non-vetoed with conviction >= 0.55: 3; fired=3; tiers={'half': 3}

top waiting_for:
| waiting_for (truncated) | n |
|---|---|
| no reclaimed sweep of an eligible low on this candle; no reclaimed sweep of an eligible hi | 5142 |
| vetoed: too_deep, third_sweep | 64 |
| vetoed: third_sweep | 64 |
| vetoed: too_deep | 47 |
| london_low reclaim quality 0.47 < 0.5; no reclaimed sweep of an eligible low on this candl | 4 |
| no reclaimed sweep of an eligible low on this candle; london_high reclaim quality 0.21 < 0 | 4 |
| asia_low reclaim quality 0.48 < 0.5; no reclaimed sweep of an eligible low on this candle; | 4 |
| no reclaimed sweep of an eligible low on this candle; asia_high reclaim quality 0.37 < 0.5 | 4 |

| coin | evaluations | setup reached | fired |
|---|---|---|---|
| BTC | 2881 | 97 | 0 |
| ETH | 2881 | 114 | 3 |

BTC: 2881 evaluations, 12 non-vetoed; vetoes: no_setup=2784, third_sweep=65, too_deep=53, event_30m=4; conviction hist (non-vetoed): 0.1:1, 0.2:1, 0.3:7, 0.4:3

ETH: 2881 evaluations, 23 non-vetoed; vetoes: no_setup=2767, third_sweep=64, too_deep=58, event_30m=4; conviction hist (non-vetoed): 0.1:1, 0.2:4, 0.3:11, 0.4:4, 0.5:1, 0.6:2

##### M2: 5762 evaluations, 0 non-vetoed
| veto | hits | rate |
|---|---|---|
| no_setup | 5762 | 100.0% |
| late_day | 600 | 10.4% |
| event_30m | 8 | 0.1% |

| bucket | raw_conviction | conviction |
|---|---|---|
| 0.0 | 0 | 0 |
| 0.1 | 0 | 0 |
| 0.2 | 0 | 0 |
| 0.3 | 0 | 0 |
| 0.4 | 0 | 0 |
| 0.5 | 0 | 0 |
| 0.6 | 0 | 0 |
| 0.7 | 0 | 0 |
| 0.8 | 0 | 0 |
| 0.9 | 0 | 0 |
| 1.0 | 0 | 0 |

non-vetoed with conviction >= 0.55: 0; fired=0; tiers={}

top waiting_for:
| waiting_for (truncated) | n |
|---|---|
| 4h trend range, no fresh 4h CHoCH up confirmed by 1h BOS (long); 4h trend range, no fresh  | 4817 |
| displacement at 2,120.1 left no 1h OB; 4h trend up, no fresh 4h CHoCH down confirmed by 1h | 72 |
| 1h BOS up at 2,439.0 was not a displacement candle; 4h trend up, no fresh 4h CHoCH down co | 72 |
| range day and the 1h break at 2,319.9 is not a 4h level; 4h trend up, no fresh 4h CHoCH do | 64 |
| range day and the 1h break at 72,501.0 is not a 4h level; 4h trend up, no fresh 4h CHoCH d | 60 |
| 1h BOS up at 75,816.0 was not a displacement candle; 4h trend up, no fresh 4h CHoCH down c | 56 |
| zone from the break at 2,450.0 already broken/filled; 4h trend up, no fresh 4h CHoCH down  | 56 |
| displacement at 64,760.0 left no 1h OB; 4h trend up, no fresh 4h CHoCH down confirmed by 1 | 52 |

| coin | evaluations | setup reached | fired |
|---|---|---|---|
| BTC | 2881 | 0 | 0 |
| ETH | 2881 | 0 | 0 |

BTC: 2881 evaluations, 0 non-vetoed; vetoes: no_setup=2881, late_day=300, event_30m=4; conviction hist (non-vetoed): -

ETH: 2881 evaluations, 0 non-vetoed; vetoes: no_setup=2881, late_day=300, event_30m=4; conviction hist (non-vetoed): -

##### M3: 5762 evaluations, 0 non-vetoed
| veto | hits | rate |
|---|---|---|
| no_setup | 5752 | 99.8% |
| cohort_adding_longs | 5257 | 91.2% |
| event_30m | 8 | 0.1% |
| squeeze_risk | 6 | 0.1% |

| bucket | raw_conviction | conviction |
|---|---|---|
| 0.0 | 0 | 0 |
| 0.1 | 0 | 0 |
| 0.2 | 0 | 0 |
| 0.3 | 0 | 0 |
| 0.4 | 0 | 0 |
| 0.5 | 0 | 0 |
| 0.6 | 0 | 0 |
| 0.7 | 0 | 0 |
| 0.8 | 0 | 0 |
| 0.9 | 0 | 0 |
| 1.0 | 0 | 0 |

non-vetoed with conviction >= 0.55: 0; fired=0; tiers={}

top waiting_for:
| waiting_for (truncated) | n |
|---|---|
| no swing-failure + retest at a pre-session high on this candle; price in the premium of th | 3299 |
| price in the discount of the 4h range (pct 0.39) — no failed-high short; no swing-failure  | 85 |
| price in the discount of the 4h range (pct 0.28) — no failed-high short; no swing-failure  | 77 |
| price in the discount of the 4h range (pct 0.30) — no failed-high short; no swing-failure  | 76 |
| price in the discount of the 4h range (pct 0.35) — no failed-high short; no swing-failure  | 73 |
| price in the discount of the 4h range (pct 0.26) — no failed-high short; no swing-failure  | 68 |
| price in the discount of the 4h range (pct 0.40) — no failed-high short; no swing-failure  | 68 |
| price in the discount of the 4h range (pct 0.27) — no failed-high short; no swing-failure  | 66 |

| coin | evaluations | setup reached | fired |
|---|---|---|---|
| BTC | 2881 | 3 | 0 |
| ETH | 2881 | 7 | 0 |

BTC: 2881 evaluations, 0 non-vetoed; vetoes: no_setup=2878, cohort_adding_longs=2615, event_30m=4, squeeze_risk=2; conviction hist (non-vetoed): -

ETH: 2881 evaluations, 0 non-vetoed; vetoes: no_setup=2874, cohort_adding_longs=2642, squeeze_risk=4, event_30m=4; conviction hist (non-vetoed): -

##### M4: 5762 evaluations, 0 non-vetoed
| veto | hits | rate |
|---|---|---|
| no_setup | 5762 | 100.0% |
| event_30m | 8 | 0.1% |

| bucket | raw_conviction | conviction |
|---|---|---|
| 0.0 | 0 | 0 |
| 0.1 | 0 | 0 |
| 0.2 | 0 | 0 |
| 0.3 | 0 | 0 |
| 0.4 | 0 | 0 |
| 0.5 | 0 | 0 |
| 0.6 | 0 | 0 |
| 0.7 | 0 | 0 |
| 0.8 | 0 | 0 |
| 0.9 | 0 | 0 |
| 1.0 | 0 | 0 |

non-vetoed with conviction >= 0.55: 0; fired=0; tiers={}

top waiting_for:
| waiting_for (truncated) | n |
|---|---|
| no 4h CHoCH down in the last 72h; no 4h CHoCH up in the last 72h | 5474 |
| no 4h CHoCH down in the last 72h; 4h trend down not extended (BOS 2, 3d move +0.70%) | 288 |

| coin | evaluations | setup reached | fired |
|---|---|---|---|
| BTC | 2881 | 0 | 0 |
| ETH | 2881 | 0 | 0 |

BTC: 2881 evaluations, 0 non-vetoed; vetoes: no_setup=2881, event_30m=4; conviction hist (non-vetoed): -

ETH: 2881 evaluations, 0 non-vetoed; vetoes: no_setup=2881, event_30m=4; conviction hist (non-vetoed): -

##### M5: 5762 evaluations, 5 non-vetoed
| veto | hits | rate |
|---|---|---|
| no_setup | 5725 | 99.4% |
| range_bad | 21 | 0.4% |
| too_deep | 20 | 0.3% |
| opened_outside | 12 | 0.2% |
| event_30m | 8 | 0.1% |

| bucket | raw_conviction | conviction |
|---|---|---|
| 0.0 | 0 | 0 |
| 0.1 | 0 | 0 |
| 0.2 | 3 | 1 |
| 0.3 | 1 | 3 |
| 0.4 | 1 | 0 |
| 0.5 | 0 | 1 |
| 0.6 | 0 | 0 |
| 0.7 | 0 | 0 |
| 0.8 | 0 | 0 |
| 0.9 | 0 | 0 |
| 1.0 | 0 | 0 |

non-vetoed with conviction >= 0.55: 1; fired=1; tiers={'half': 1}

top waiting_for:
| waiting_for (truncated) | n |
|---|---|
| outside the London 07–09 / New York 13–15 UTC windows | 4682 |
| waiting for the first closed candle of the window | 120 |
| vetoed: too_deep, range_bad | 11 |
| no reclaimed newyork raid of the Asia low 64,137.0 on this candle; newyork raid of Asia hi | 8 |
| no reclaimed newyork raid of the Asia low 1,893.5 on this candle; newyork raid of Asia hig | 8 |
| no reclaimed london raid of the Asia low 64,778.0 on this candle; no reclaimed london raid | 8 |
| no reclaimed london raid of the Asia low 1,911.3 on this candle; no reclaimed london raid  | 8 |
| no reclaimed newyork raid of the Asia low 1,911.3 on this candle; newyork raid of Asia hig | 8 |

| coin | evaluations | setup reached | fired |
|---|---|---|---|
| BTC | 2881 | 15 | 1 |
| ETH | 2881 | 22 | 0 |

BTC: 2881 evaluations, 2 non-vetoed; vetoes: no_setup=2866, range_bad=10, too_deep=7, opened_outside=4, event_30m=4; conviction hist (non-vetoed): 0.2:1, 0.5:1

ETH: 2881 evaluations, 3 non-vetoed; vetoes: no_setup=2859, too_deep=13, range_bad=11, opened_outside=8, event_30m=4; conviction hist (non-vetoed): 0.3:3

##### M6: 5762 evaluations, 124 non-vetoed
| veto | hits | rate |
|---|---|---|
| no_setup | 5312 | 92.2% |
| shallow_loss | 157 | 2.7% |
| too_late | 121 | 2.1% |
| stop_too_wide | 64 | 1.1% |
| daily_strong_down | 40 | 0.7% |
| event_30m | 8 | 0.1% |

| bucket | raw_conviction | conviction |
|---|---|---|
| 0.0 | 0 | 0 |
| 0.1 | 0 | 0 |
| 0.2 | 0 | 0 |
| 0.3 | 36 | 72 |
| 0.4 | 84 | 32 |
| 0.5 | 4 | 20 |
| 0.6 | 0 | 0 |
| 0.7 | 0 | 0 |
| 0.8 | 0 | 0 |
| 0.9 | 0 | 0 |
| 1.0 | 0 | 0 |

non-vetoed with conviction >= 0.55: 0; fired=0; tiers={}

top waiting_for:
| waiting_for (truncated) | n |
|---|---|
| price never lost the weekly open this week on 1h closes (long); 1h not closed below weekly | 1256 |
| 1h not closed above weekly open 64,868.0 for a long; 1h reclaim at 2026-08-10 12:59 is old | 588 |
| 1h not closed above weekly open 1,910.0 for a long; 1h reclaim at 2026-08-12 13:59 is olde | 392 |
| before Monday 12:00 UTC — reclaim window not open | 376 |
| 1h reclaim at 2026-08-04 09:59 is older than the 8h entry window; 1h not closed below week | 263 |
| 1h reclaim at 2026-08-05 16:59 is older than the 8h entry window; 1h not closed below week | 263 |
| 1h reclaim at 2026-09-03 10:59 is older than the 8h entry window; 1h not closed below week | 238 |
| 1h reclaim at 2026-09-03 12:59 is older than the 8h entry window; 1h not closed below week | 230 |

| coin | evaluations | setup reached | fired |
|---|---|---|---|
| BTC | 2881 | 129 | 0 |
| ETH | 2881 | 321 | 0 |

BTC: 2881 evaluations, 32 non-vetoed; vetoes: no_setup=2752, too_late=57, daily_strong_down=40, stop_too_wide=24, event_30m=4; conviction hist (non-vetoed): 0.3:32

ETH: 2881 evaluations, 92 non-vetoed; vetoes: no_setup=2560, shallow_loss=157, too_late=64, stop_too_wide=40, event_30m=4; conviction hist (non-vetoed): 0.3:40, 0.4:32, 0.5:20

### 3.5b Feed-era replay (2026-09-03 13:30 → 09-06 06:15, vetoes ON) — reason statistics on rows WITH a setup

Pass 3 = D-62 tree (M5 `reclaim` still constant 0); pass 4 = D-62 + D-63 tree. M1 rows are identical between the passes (M1 code unchanged).

#### pass 4 (final tree)

##### Part 3 reason health — rows WITH a setup

###### M1 (10 rows)
| reason | n | mean | sd | min | max | frac@0 | frac@1 | flag |
|---|---|---|---|---|---|---|---|---|
| location | 10 | 0.820 | 0.166 | 0.600 | 1.000 | 0.00 | 0.40 |  |
| reclaim | 10 | 0.562 | 0.239 | 0.316 | 1.000 | 0.00 | 0.20 |  |
| fuel | 10 | 0.008 | 0.016 | 0.000 | 0.043 | 0.80 | 0.00 |  |
| cleared | 10 | 0.233 | 0.396 | 0.000 | 1.000 | 0.70 | 0.20 |  |
| delta_flip | 10 | 0.414 | 0.392 | 0.000 | 1.000 | 0.40 | 0.20 |  |
| absorption | 10 | 0.400 | 0.436 | 0.000 | 1.000 | 0.50 | 0.30 |  |
| discount | 10 | 0.567 | 0.390 | 0.014 | 0.999 | 0.00 | 0.00 |  |
| session | 10 | 0.460 | 0.180 | 0.100 | 0.600 | 0.00 | 0.00 |  |
| cohort | 10 | 0.067 | 0.200 | 0.000 | 0.667 | 0.90 | 0.00 |  |
| htf_bias | 10 | 0.400 | 0.319 | 0.000 | 1.000 | 0.30 | 0.10 |  |

###### M2 (0 rows)
no rows

###### M3 (0 rows)
no rows

###### M4 (0 rows)
no rows

###### M5 (2 rows)
| reason | n | mean | sd | min | max | frac@0 | frac@1 | flag |
|---|---|---|---|---|---|---|---|---|
| bias_alignment | 2 | 0.200 | 0.000 | 0.200 | 0.200 | 0.00 | 0.00 |  |
| range_quality | 2 | 0.300 | 0.300 | 0.000 | 0.600 | 0.50 | 0.00 |  |
| reclaim | 2 | 0.635 | 0.298 | 0.337 | 0.934 | 0.00 | 0.00 |  |
| fuel | 2 | 0.000 | 0.000 | 0.000 | 0.000 | 1.00 | 0.00 | SUSPECT |
| cleared | 2 | 0.013 | 0.013 | 0.000 | 0.026 | 0.50 | 0.00 |  |
| delta_flip | 2 | 0.500 | 0.500 | 0.000 | 1.000 | 0.50 | 0.50 |  |
| early_in_window | 2 | 0.650 | 0.350 | 0.300 | 1.000 | 0.00 | 0.50 |  |
| open_location | 2 | 0.700 | 0.000 | 0.700 | 0.700 | 0.00 | 0.00 |  |
| no_pre_drift | 2 | 0.253 | 0.016 | 0.237 | 0.269 | 0.00 | 0.00 |  |
| cohort | 2 | 0.000 | 0.000 | 0.000 | 0.000 | 1.00 | 0.00 | SUSPECT |

###### M6 (0 rows)
no rows

#### pass 3 (before D-63, for the M5 `reclaim` comparison)

###### M5 (2 rows)
| reason | n | mean | sd | min | max | frac@0 | frac@1 | flag |
|---|---|---|---|---|---|---|---|---|
| bias_alignment | 2 | 0.200 | 0.000 | 0.200 | 0.200 | 0.00 | 0.00 |  |
| range_quality | 2 | 0.300 | 0.300 | 0.000 | 0.600 | 0.50 | 0.00 |  |
| reclaim | 2 | 0.000 | 0.000 | 0.000 | 0.000 | 1.00 | 0.00 | SUSPECT |
| fuel | 2 | 0.000 | 0.000 | 0.000 | 0.000 | 1.00 | 0.00 | SUSPECT |
| cleared | 2 | 0.013 | 0.013 | 0.000 | 0.026 | 0.50 | 0.00 |  |
| delta_flip | 2 | 0.500 | 0.500 | 0.000 | 1.000 | 0.50 | 0.50 |  |
| early_in_window | 2 | 0.650 | 0.350 | 0.300 | 1.000 | 0.00 | 0.50 |  |
| open_location | 2 | 0.700 | 0.000 | 0.700 | 0.700 | 0.00 | 0.00 |  |
| no_pre_drift | 2 | 0.253 | 0.016 | 0.237 | 0.269 | 0.00 | 0.00 |  |
| cohort | 2 | 0.000 | 0.000 | 0.000 | 0.000 | 1.00 | 0.00 | SUSPECT |

### 3.6b Feed-era replay (pass 4) — veto hit rates, conviction histograms, waiting_for, per coin

#### Veto hit rates, conviction histograms (non-vetoed rows)

##### M1: 520 evaluations, 2 non-vetoed
| veto | hits | rate |
|---|---|---|
| no_setup | 510 | 98.1% |
| feed_missing_oi | 8 | 1.5% |
| feed_missing_taker | 8 | 1.5% |
| event_30m | 8 | 1.5% |
| third_sweep | 6 | 1.2% |
| too_deep | 3 | 0.6% |

| bucket | raw_conviction | conviction |
|---|---|---|
| 0.0 | 0 | 0 |
| 0.1 | 0 | 1 |
| 0.2 | 1 | 1 |
| 0.3 | 1 | 0 |
| 0.4 | 0 | 0 |
| 0.5 | 0 | 0 |
| 0.6 | 0 | 0 |
| 0.7 | 0 | 0 |
| 0.8 | 0 | 0 |
| 0.9 | 0 | 0 |
| 1.0 | 0 | 0 |

non-vetoed with conviction >= 0.55: 0; fired=0; tiers={}

top waiting_for:
| waiting_for (truncated) | n |
|---|---|
| no reclaimed sweep of an eligible low on this candle; no reclaimed sweep of an eligible hi | 477 |
| vetoed: third_sweep | 4 |
| sweep of london_high reclaimed but price is in the discount of the 4h range (pct 0.30) | 3 |
| no reclaimed sweep of an eligible low on this candle; asia_high reclaim quality 0.15 < 0.5 | 2 |
| sweep of asia_high reclaimed but price is in the discount of the 4h range (pct 0.29) | 2 |
| no reclaimed sweep of an eligible low on this candle; asia_high reclaim quality 0.39 < 0.5 | 2 |
| vetoed: too_deep | 2 |
| no reclaimed sweep of an eligible low on this candle; pwh reclaim quality 0.14 < 0.5; equa | 1 |

| coin | evaluations | setup reached | fired |
|---|---|---|---|
| BTC | 260 | 3 | 0 |
| ETH | 260 | 7 | 0 |

BTC: 260 evaluations, 1 non-vetoed; vetoes: no_setup=257, feed_missing_oi=4, feed_missing_taker=4, event_30m=4, third_sweep=2, too_deep=1; conviction hist (non-vetoed): 0.1:1

ETH: 260 evaluations, 1 non-vetoed; vetoes: no_setup=253, feed_missing_oi=4, feed_missing_taker=4, third_sweep=4, event_30m=4, too_deep=2; conviction hist (non-vetoed): 0.2:1

##### M2: 520 evaluations, 0 non-vetoed
| veto | hits | rate |
|---|---|---|
| no_setup | 520 | 100.0% |
| late_day | 72 | 13.8% |
| feed_missing_oi | 8 | 1.5% |
| feed_missing_taker | 8 | 1.5% |
| event_30m | 8 | 1.5% |

| bucket | raw_conviction | conviction |
|---|---|---|
| 0.0 | 0 | 0 |
| 0.1 | 0 | 0 |
| 0.2 | 0 | 0 |
| 0.3 | 0 | 0 |
| 0.4 | 0 | 0 |
| 0.5 | 0 | 0 |
| 0.6 | 0 | 0 |
| 0.7 | 0 | 0 |
| 0.8 | 0 | 0 |
| 0.9 | 0 | 0 |
| 1.0 | 0 | 0 |

non-vetoed with conviction >= 0.55: 0; fired=0; tiers={}

top waiting_for:
| waiting_for (truncated) | n |
|---|---|
| 4h trend range, no fresh 4h CHoCH up confirmed by 1h BOS (long); 4h trend range, no fresh  | 414 |
| no 1h BOS up in the last 24h; 4h trend up, no fresh 4h CHoCH down confirmed by 1h BOS (sho | 32 |
| zone from the break at 2,524.7 already broken/filled; 4h trend up, no fresh 4h CHoCH down  | 20 |
| zone from the break at 2,484.7 already broken/filled; 4h trend up, no fresh 4h CHoCH down  | 16 |
| break buy ratio 0.54 not ≥ 0.65; 4h trend up, no fresh 4h CHoCH down confirmed by 1h BOS ( | 14 |
| retrace 38% of the way to the zone — waiting (needs ≥50%); 4h trend up, no fresh 4h CHoCH  | 12 |
| displacement at 2,461.3 left no 1h FVG; 4h trend up, no fresh 4h CHoCH down confirmed by 1 | 4 |
| range day and the 1h break at 2,461.3 is not a 4h level; 4h trend up, no fresh 4h CHoCH do | 4 |

| coin | evaluations | setup reached | fired |
|---|---|---|---|
| BTC | 260 | 0 | 0 |
| ETH | 260 | 0 | 0 |

BTC: 260 evaluations, 0 non-vetoed; vetoes: no_setup=260, late_day=36, feed_missing_oi=4, feed_missing_taker=4, event_30m=4; conviction hist (non-vetoed): -

ETH: 260 evaluations, 0 non-vetoed; vetoes: no_setup=260, late_day=36, feed_missing_oi=4, feed_missing_taker=4, event_30m=4; conviction hist (non-vetoed): -

##### M3: 520 evaluations, 0 non-vetoed
| veto | hits | rate |
|---|---|---|
| no_setup | 520 | 100.0% |
| cohort_adding_longs | 25 | 4.8% |
| feed_missing_oi | 8 | 1.5% |
| feed_missing_taker | 8 | 1.5% |
| event_30m | 8 | 1.5% |

| bucket | raw_conviction | conviction |
|---|---|---|
| 0.0 | 0 | 0 |
| 0.1 | 0 | 0 |
| 0.2 | 0 | 0 |
| 0.3 | 0 | 0 |
| 0.4 | 0 | 0 |
| 0.5 | 0 | 0 |
| 0.6 | 0 | 0 |
| 0.7 | 0 | 0 |
| 0.8 | 0 | 0 |
| 0.9 | 0 | 0 |
| 1.0 | 0 | 0 |

non-vetoed with conviction >= 0.55: 0; fired=0; tiers={}

top waiting_for:
| waiting_for (truncated) | n |
|---|---|
| no swing-failure + retest at a pre-session high on this candle; price in the premium of th | 267 |
| day type no_trade is not range (and price not inside a 4h supply zone) for a short; day ty | 105 |
| day type event is not range (and price not inside a 4h supply zone) for a short; day type  | 32 |
| price in the discount of the 4h range (pct 0.47) — no failed-high short; no swing-failure  | 9 |
| price in the discount of the 4h range (pct 0.28) — no failed-high short; no swing-failure  | 8 |
| price in the discount of the 4h range (pct 0.26) — no failed-high short; no swing-failure  | 7 |
| price in the discount of the 4h range (pct 0.27) — no failed-high short; no swing-failure  | 7 |
| price in the discount of the 4h range (pct 0.20) — no failed-high short; no swing-failure  | 7 |

| coin | evaluations | setup reached | fired |
|---|---|---|---|
| BTC | 260 | 0 | 0 |
| ETH | 260 | 0 | 0 |

BTC: 260 evaluations, 0 non-vetoed; vetoes: no_setup=260, feed_missing_oi=4, feed_missing_taker=4, event_30m=4; conviction hist (non-vetoed): -

ETH: 260 evaluations, 0 non-vetoed; vetoes: no_setup=260, cohort_adding_longs=25, feed_missing_oi=4, feed_missing_taker=4, event_30m=4; conviction hist (non-vetoed): -

##### M4: 520 evaluations, 0 non-vetoed
| veto | hits | rate |
|---|---|---|
| no_setup | 520 | 100.0% |
| feed_missing_oi | 8 | 1.5% |
| feed_missing_taker | 8 | 1.5% |
| event_30m | 8 | 1.5% |

| bucket | raw_conviction | conviction |
|---|---|---|
| 0.0 | 0 | 0 |
| 0.1 | 0 | 0 |
| 0.2 | 0 | 0 |
| 0.3 | 0 | 0 |
| 0.4 | 0 | 0 |
| 0.5 | 0 | 0 |
| 0.6 | 0 | 0 |
| 0.7 | 0 | 0 |
| 0.8 | 0 | 0 |
| 0.9 | 0 | 0 |
| 1.0 | 0 | 0 |

non-vetoed with conviction >= 0.55: 0; fired=0; tiers={}

top waiting_for:
| waiting_for (truncated) | n |
|---|---|
| no 4h CHoCH down in the last 72h; no 4h CHoCH up in the last 72h | 520 |

| coin | evaluations | setup reached | fired |
|---|---|---|---|
| BTC | 260 | 0 | 0 |
| ETH | 260 | 0 | 0 |

BTC: 260 evaluations, 0 non-vetoed; vetoes: no_setup=260, feed_missing_oi=4, feed_missing_taker=4, event_30m=4; conviction hist (non-vetoed): -

ETH: 260 evaluations, 0 non-vetoed; vetoes: no_setup=260, feed_missing_oi=4, feed_missing_taker=4, event_30m=4; conviction hist (non-vetoed): -

##### M5: 520 evaluations, 1 non-vetoed
| veto | hits | rate |
|---|---|---|
| no_setup | 518 | 99.6% |
| feed_missing_oi | 8 | 1.5% |
| feed_missing_taker | 8 | 1.5% |
| event_30m | 8 | 1.5% |
| too_deep | 1 | 0.2% |
| range_bad | 1 | 0.2% |

| bucket | raw_conviction | conviction |
|---|---|---|
| 0.0 | 0 | 0 |
| 0.1 | 0 | 0 |
| 0.2 | 1 | 1 |
| 0.3 | 0 | 0 |
| 0.4 | 0 | 0 |
| 0.5 | 0 | 0 |
| 0.6 | 0 | 0 |
| 0.7 | 0 | 0 |
| 0.8 | 0 | 0 |
| 0.9 | 0 | 0 |
| 1.0 | 0 | 0 |

non-vetoed with conviction >= 0.55: 0; fired=0; tiers={}

top waiting_for:
| waiting_for (truncated) | n |
|---|---|
| outside the London 07–09 / New York 13–15 UTC windows | 434 |
| waiting for the first closed candle of the window | 8 |
| no reclaimed newyork raid of the Asia low 76,951.0 on this candle; newyork raid of Asia hi | 7 |
| no reclaimed newyork raid of the Asia low 2,369.2 on this candle; newyork raid of Asia hig | 7 |
| no reclaimed london raid of the Asia low 80,528.0 on this candle; no reclaimed london raid | 7 |
| no reclaimed london raid of the Asia low 2,497.4 on this candle; no reclaimed london raid  | 7 |
| no reclaimed newyork raid of the Asia low 2,444.1 on this candle; newyork raid of Asia hig | 7 |
| no reclaimed london raid of the Asia low 79,413.0 on this candle; no reclaimed london raid | 6 |

| coin | evaluations | setup reached | fired |
|---|---|---|---|
| BTC | 260 | 2 | 0 |
| ETH | 260 | 0 | 0 |

BTC: 260 evaluations, 1 non-vetoed; vetoes: no_setup=258, feed_missing_oi=4, feed_missing_taker=4, event_30m=4, too_deep=1, range_bad=1; conviction hist (non-vetoed): 0.2:1

ETH: 260 evaluations, 0 non-vetoed; vetoes: no_setup=260, feed_missing_oi=4, feed_missing_taker=4, event_30m=4; conviction hist (non-vetoed): -

##### M6: 520 evaluations, 0 non-vetoed
| veto | hits | rate |
|---|---|---|
| no_setup | 520 | 100.0% |
| feed_missing_oi | 8 | 1.5% |
| feed_missing_taker | 8 | 1.5% |
| event_30m | 8 | 1.5% |

| bucket | raw_conviction | conviction |
|---|---|---|
| 0.0 | 0 | 0 |
| 0.1 | 0 | 0 |
| 0.2 | 0 | 0 |
| 0.3 | 0 | 0 |
| 0.4 | 0 | 0 |
| 0.5 | 0 | 0 |
| 0.6 | 0 | 0 |
| 0.7 | 0 | 0 |
| 0.8 | 0 | 0 |
| 0.9 | 0 | 0 |
| 1.0 | 0 | 0 |

non-vetoed with conviction >= 0.55: 0; fired=0; tiers={}

top waiting_for:
| waiting_for (truncated) | n |
|---|---|
| 1h reclaim at 2026-09-03 10:59 is older than the 8h entry window; 1h not closed below week | 238 |
| 1h reclaim at 2026-09-03 12:59 is older than the 8h entry window; 1h not closed below week | 230 |
| no 15m close beyond the weekly open before the 1h close; 1h not closed below weekly open 2 | 30 |
| cohort net-long change 24h -0.22 against the long; 1h not closed below weekly open 77,660. | 22 |

| coin | evaluations | setup reached | fired |
|---|---|---|---|
| BTC | 260 | 0 | 0 |
| ETH | 260 | 0 | 0 |

BTC: 260 evaluations, 0 non-vetoed; vetoes: no_setup=260, feed_missing_oi=4, feed_missing_taker=4, event_30m=4; conviction hist (non-vetoed): -

ETH: 260 evaluations, 0 non-vetoed; vetoes: no_setup=260, feed_missing_oi=4, feed_missing_taker=4, event_30m=4; conviction hist (non-vetoed): -

### 3.7 Replay pass 1 (pre-fix tree, feed vetoes ON) — veto hit rates for comparison

#### Veto hit rates, conviction histograms (non-vetoed rows)

##### M1: 5762 evaluations, 2 non-vetoed
| veto | hits | rate |
|---|---|---|
| no_setup | 5551 | 96.3% |
| feed_missing_book | 5252 | 91.1% |
| feed_missing_oi | 5250 | 91.1% |
| feed_missing_taker | 5250 | 91.1% |
| third_sweep | 129 | 2.2% |
| too_deep | 111 | 1.9% |
| event_30m | 8 | 0.1% |

| bucket | raw_conviction | conviction |
|---|---|---|
| 0.0 | 0 | 0 |
| 0.1 | 0 | 1 |
| 0.2 | 1 | 1 |
| 0.3 | 1 | 0 |
| 0.4 | 0 | 0 |
| 0.5 | 0 | 0 |
| 0.6 | 0 | 0 |
| 0.7 | 0 | 0 |
| 0.8 | 0 | 0 |
| 0.9 | 0 | 0 |
| 1.0 | 0 | 0 |

non-vetoed with conviction >= 0.55: 0; fired=0; tiers={}

top waiting_for:
| waiting_for (truncated) | n |
|---|---|
| no reclaimed sweep of an eligible low on this candle; no reclaimed sweep of an eligible hi | 5142 |
| vetoed: too_deep, third_sweep, feed_missing_oi, feed_missing_taker, feed_missing_book | 63 |
| vetoed: third_sweep, feed_missing_oi, feed_missing_taker, feed_missing_book | 60 |
| vetoed: too_deep, feed_missing_oi, feed_missing_taker, feed_missing_book | 45 |
| vetoed: feed_missing_oi, feed_missing_taker, feed_missing_book | 33 |
| london_low reclaim quality 0.47 < 0.5; no reclaimed sweep of an eligible low on this candl | 4 |
| no reclaimed sweep of an eligible low on this candle; london_high reclaim quality 0.21 < 0 | 4 |
| asia_low reclaim quality 0.48 < 0.5; no reclaimed sweep of an eligible low on this candle; | 4 |

| coin | evaluations | setup reached | fired |
|---|---|---|---|
| BTC | 2881 | 97 | 0 |
| ETH | 2881 | 114 | 0 |

BTC: 2881 evaluations, 1 non-vetoed; vetoes: no_setup=2784, feed_missing_book=2626, feed_missing_oi=2625, feed_missing_taker=2625, third_sweep=65, too_deep=53, event_30m=4; conviction hist (non-vetoed): 0.1:1

ETH: 2881 evaluations, 1 non-vetoed; vetoes: no_setup=2767, feed_missing_book=2626, feed_missing_oi=2625, feed_missing_taker=2625, third_sweep=64, too_deep=58, event_30m=4; conviction hist (non-vetoed): 0.2:1

##### M2: 5762 evaluations, 0 non-vetoed
| veto | hits | rate |
|---|---|---|
| no_setup | 5762 | 100.0% |
| feed_missing_book | 5252 | 91.1% |
| feed_missing_oi | 5250 | 91.1% |
| feed_missing_taker | 5250 | 91.1% |
| late_day | 360 | 6.2% |
| event_30m | 8 | 0.1% |

| bucket | raw_conviction | conviction |
|---|---|---|
| 0.0 | 0 | 0 |
| 0.1 | 0 | 0 |
| 0.2 | 0 | 0 |
| 0.3 | 0 | 0 |
| 0.4 | 0 | 0 |
| 0.5 | 0 | 0 |
| 0.6 | 0 | 0 |
| 0.7 | 0 | 0 |
| 0.8 | 0 | 0 |
| 0.9 | 0 | 0 |
| 1.0 | 0 | 0 |

non-vetoed with conviction >= 0.55: 0; fired=0; tiers={}

top waiting_for:
| waiting_for (truncated) | n |
|---|---|
| 4h trend range, no fresh 4h CHoCH up confirmed by 1h BOS (long); 4h trend range, no fresh  | 4881 |
| displacement at 2,120.1 left no 1h OB; 4h trend up, no fresh 4h CHoCH down confirmed by 1h | 72 |
| 1h BOS up at 2,439.0 was not a displacement candle; 4h trend up, no fresh 4h CHoCH down co | 72 |
| range day and the 1h break at 2,319.9 is not a 4h level; 4h trend up, no fresh 4h CHoCH do | 64 |
| range day and the 1h break at 72,501.0 is not a 4h level; 4h trend up, no fresh 4h CHoCH d | 60 |
| 1h BOS up at 75,816.0 was not a displacement candle; 4h trend up, no fresh 4h CHoCH down c | 56 |
| zone from the break at 2,450.0 already broken/filled; 4h trend up, no fresh 4h CHoCH down  | 56 |
| displacement at 64,760.0 left no 1h OB; 4h trend up, no fresh 4h CHoCH down confirmed by 1 | 52 |

| coin | evaluations | setup reached | fired |
|---|---|---|---|
| BTC | 2881 | 0 | 0 |
| ETH | 2881 | 0 | 0 |

BTC: 2881 evaluations, 0 non-vetoed; vetoes: no_setup=2881, feed_missing_book=2626, feed_missing_oi=2625, feed_missing_taker=2625, late_day=180, event_30m=4; conviction hist (non-vetoed): -

ETH: 2881 evaluations, 0 non-vetoed; vetoes: no_setup=2881, feed_missing_book=2626, feed_missing_oi=2625, feed_missing_taker=2625, late_day=180, event_30m=4; conviction hist (non-vetoed): -

##### M3: 5762 evaluations, 0 non-vetoed
| veto | hits | rate |
|---|---|---|
| no_setup | 5752 | 99.8% |
| cohort_adding_longs | 5301 | 92.0% |
| feed_missing_book | 5252 | 91.1% |
| feed_missing_oi | 5250 | 91.1% |
| feed_missing_taker | 5250 | 91.1% |
| event_30m | 8 | 0.1% |
| squeeze_risk | 6 | 0.1% |

| bucket | raw_conviction | conviction |
|---|---|---|
| 0.0 | 0 | 0 |
| 0.1 | 0 | 0 |
| 0.2 | 0 | 0 |
| 0.3 | 0 | 0 |
| 0.4 | 0 | 0 |
| 0.5 | 0 | 0 |
| 0.6 | 0 | 0 |
| 0.7 | 0 | 0 |
| 0.8 | 0 | 0 |
| 0.9 | 0 | 0 |
| 1.0 | 0 | 0 |

non-vetoed with conviction >= 0.55: 0; fired=0; tiers={}

top waiting_for:
| waiting_for (truncated) | n |
|---|---|
| no swing-failure + retest at a pre-session high on this candle; price in the premium of th | 3299 |
| price in the discount of the 4h range (pct 0.39) — no failed-high short; no swing-failure  | 85 |
| price in the discount of the 4h range (pct 0.28) — no failed-high short; no swing-failure  | 77 |
| price in the discount of the 4h range (pct 0.30) — no failed-high short; no swing-failure  | 76 |
| price in the discount of the 4h range (pct 0.35) — no failed-high short; no swing-failure  | 73 |
| price in the discount of the 4h range (pct 0.26) — no failed-high short; no swing-failure  | 68 |
| price in the discount of the 4h range (pct 0.40) — no failed-high short; no swing-failure  | 68 |
| price in the discount of the 4h range (pct 0.27) — no failed-high short; no swing-failure  | 66 |

| coin | evaluations | setup reached | fired |
|---|---|---|---|
| BTC | 2881 | 3 | 0 |
| ETH | 2881 | 7 | 0 |

BTC: 2881 evaluations, 0 non-vetoed; vetoes: no_setup=2878, cohort_adding_longs=2659, feed_missing_book=2626, feed_missing_oi=2625, feed_missing_taker=2625, event_30m=4, squeeze_risk=2; conviction hist (non-vetoed): -

ETH: 2881 evaluations, 0 non-vetoed; vetoes: no_setup=2874, cohort_adding_longs=2642, feed_missing_book=2626, feed_missing_oi=2625, feed_missing_taker=2625, squeeze_risk=4, event_30m=4; conviction hist (non-vetoed): -

##### M4: 5762 evaluations, 0 non-vetoed
| veto | hits | rate |
|---|---|---|
| no_setup | 5762 | 100.0% |
| feed_missing_book | 5252 | 91.1% |
| feed_missing_oi | 5250 | 91.1% |
| feed_missing_taker | 5250 | 91.1% |
| event_30m | 8 | 0.1% |

| bucket | raw_conviction | conviction |
|---|---|---|
| 0.0 | 0 | 0 |
| 0.1 | 0 | 0 |
| 0.2 | 0 | 0 |
| 0.3 | 0 | 0 |
| 0.4 | 0 | 0 |
| 0.5 | 0 | 0 |
| 0.6 | 0 | 0 |
| 0.7 | 0 | 0 |
| 0.8 | 0 | 0 |
| 0.9 | 0 | 0 |
| 1.0 | 0 | 0 |

non-vetoed with conviction >= 0.55: 0; fired=0; tiers={}

top waiting_for:
| waiting_for (truncated) | n |
|---|---|
| no 4h CHoCH down in the last 72h; no 4h CHoCH up in the last 72h | 5474 |
| no 4h CHoCH down in the last 72h; 4h trend down not extended (BOS 2, 3d move +0.70%) | 288 |

| coin | evaluations | setup reached | fired |
|---|---|---|---|
| BTC | 2881 | 0 | 0 |
| ETH | 2881 | 0 | 0 |

BTC: 2881 evaluations, 0 non-vetoed; vetoes: no_setup=2881, feed_missing_book=2626, feed_missing_oi=2625, feed_missing_taker=2625, event_30m=4; conviction hist (non-vetoed): -

ETH: 2881 evaluations, 0 non-vetoed; vetoes: no_setup=2881, feed_missing_book=2626, feed_missing_oi=2625, feed_missing_taker=2625, event_30m=4; conviction hist (non-vetoed): -

##### M5: 5762 evaluations, 1 non-vetoed
| veto | hits | rate |
|---|---|---|
| no_setup | 5725 | 99.4% |
| feed_missing_book | 5252 | 91.1% |
| feed_missing_oi | 5250 | 91.1% |
| feed_missing_taker | 5250 | 91.1% |
| range_bad | 21 | 0.4% |
| too_deep | 20 | 0.3% |
| opened_outside | 12 | 0.2% |
| event_30m | 8 | 0.1% |

| bucket | raw_conviction | conviction |
|---|---|---|
| 0.0 | 0 | 0 |
| 0.1 | 0 | 0 |
| 0.2 | 1 | 1 |
| 0.3 | 0 | 0 |
| 0.4 | 0 | 0 |
| 0.5 | 0 | 0 |
| 0.6 | 0 | 0 |
| 0.7 | 0 | 0 |
| 0.8 | 0 | 0 |
| 0.9 | 0 | 0 |
| 1.0 | 0 | 0 |

non-vetoed with conviction >= 0.55: 0; fired=0; tiers={}

top waiting_for:
| waiting_for (truncated) | n |
|---|---|
| outside the London 07–09 / New York 13–15 UTC windows | 4682 |
| waiting for the first closed candle of the window | 120 |
| vetoed: too_deep, range_bad, feed_missing_oi, feed_missing_taker, feed_missing_book | 10 |
| no reclaimed newyork raid of the Asia low 64,137.0 on this candle; newyork raid of Asia hi | 8 |
| no reclaimed newyork raid of the Asia low 1,893.5 on this candle; newyork raid of Asia hig | 8 |
| no reclaimed london raid of the Asia low 64,778.0 on this candle; no reclaimed london raid | 8 |
| no reclaimed london raid of the Asia low 1,911.3 on this candle; no reclaimed london raid  | 8 |
| no reclaimed newyork raid of the Asia low 1,911.3 on this candle; newyork raid of Asia hig | 8 |

| coin | evaluations | setup reached | fired |
|---|---|---|---|
| BTC | 2881 | 15 | 0 |
| ETH | 2881 | 22 | 0 |

BTC: 2881 evaluations, 1 non-vetoed; vetoes: no_setup=2866, feed_missing_book=2626, feed_missing_oi=2625, feed_missing_taker=2625, range_bad=10, too_deep=7, opened_outside=4, event_30m=4; conviction hist (non-vetoed): 0.2:1

ETH: 2881 evaluations, 0 non-vetoed; vetoes: no_setup=2859, feed_missing_book=2626, feed_missing_oi=2625, feed_missing_taker=2625, too_deep=13, range_bad=11, opened_outside=8, event_30m=4; conviction hist (non-vetoed): -

##### M6: 5762 evaluations, 0 non-vetoed
| veto | hits | rate |
|---|---|---|
| feed_missing_book | 5252 | 91.1% |
| feed_missing_oi | 5250 | 91.1% |
| feed_missing_taker | 5250 | 91.1% |
| no_setup | 5204 | 90.3% |
| too_late | 152 | 2.6% |
| stop_too_wide | 80 | 1.4% |
| daily_strong_down | 56 | 1.0% |
| shallow_loss | 20 | 0.3% |
| event_30m | 8 | 0.1% |

| bucket | raw_conviction | conviction |
|---|---|---|
| 0.0 | 0 | 0 |
| 0.1 | 0 | 0 |
| 0.2 | 0 | 0 |
| 0.3 | 0 | 0 |
| 0.4 | 0 | 0 |
| 0.5 | 0 | 0 |
| 0.6 | 0 | 0 |
| 0.7 | 0 | 0 |
| 0.8 | 0 | 0 |
| 0.9 | 0 | 0 |
| 1.0 | 0 | 0 |

non-vetoed with conviction >= 0.55: 0; fired=0; tiers={}

top waiting_for:
| waiting_for (truncated) | n |
|---|---|
| price never lost the weekly open this week on 1h closes (long); 1h not closed below weekly | 1256 |
| 1h not closed above weekly open 64,868.0 for a long; 1h reclaim at 2026-08-10 12:59 is old | 572 |
| before Monday 12:00 UTC — reclaim window not open | 376 |
| 1h not closed above weekly open 1,910.0 for a long; 1h reclaim at 2026-08-12 13:59 is olde | 376 |
| vetoed: feed_missing_oi, feed_missing_taker, feed_missing_book | 322 |
| 1h reclaim at 2026-08-04 09:59 is older than the entry window; 1h not closed below weekly  | 263 |
| 1h reclaim at 2026-08-05 16:59 is older than the entry window; 1h not closed below weekly  | 263 |
| 1h reclaim at 2026-09-03 10:59 is older than the entry window; 1h not closed below weekly  | 222 |

| coin | evaluations | setup reached | fired |
|---|---|---|---|
| BTC | 2881 | 164 | 0 |
| ETH | 2881 | 394 | 0 |

BTC: 2881 evaluations, 0 non-vetoed; vetoes: no_setup=2717, feed_missing_book=2626, feed_missing_oi=2625, feed_missing_taker=2625, too_late=60, daily_strong_down=56, stop_too_wide=24, event_30m=4; conviction hist (non-vetoed): -

ETH: 2881 evaluations, 0 non-vetoed; vetoes: feed_missing_book=2626, feed_missing_oi=2625, feed_missing_taker=2625, no_setup=2487, too_late=92, stop_too_wide=56, shallow_loss=20, event_30m=4; conviction hist (non-vetoed): -

## Part 4 — Snapshot and structure sanity

### 4.0 Method and verdict

* Script `audit_part4.py` (server `/root/audit_part4.py`) builds the Snapshot for BTC and ETH through `ModelEvaluator.build`
  (the worker's own loader + `build_snapshot` + `finalize_day_type`, real cohort of 30 wallets) at **2026-09-06 07:15 UTC**
  and prints the alignment table, a 30-row plausibility check per coin, zones / pools / liquidation clusters, the last
  20 RECLAIMED 15m sweeps (with the number of unreclaimed candidates), the last 10 1h BOS/CHoCH, the last 6 4h events,
  the last 10 1h displacements, every fresh OB / open FVG on 1h and 4h, the Asia freeze vs the 00:00–07:00 candles, the
  weekly levels vs the Monday 00:00 candle and last week's 1h candles, and the `day_type:*` freeze history.
  The full output is reproduced in §4.9 so every number can be spot-checked against the chart.
* **Result: 30/30 plausibility checks OK for BTC and 30/30 for ETH** (price inside the 4h range, discount/premium
  consistent with range position — BTC 0.29 → discount, ETH 0.51 → premium (the table calls > 0.5 premium), nearest
  zones/pools within 0.02–2.5 %, session `london +15m` matching the UTC clock, day type present, all reference levels
  populated, ATRs, OI, book mid, taker rows, funding z and cohort net_dir all present).
* Three script corrections were needed to get an honest listing (script bugs, not engine bugs): (1) reference levels
  were read for "today" with `now_ms` past midnight so pdh/pdl/asia of the NEXT day leaked in; (2) the sweep listing
  showed every candidate incl. never-reclaimed ones, flooding the table — it now lists reclaimed sweeps and reports the
  candidate / unreclaimed counts; (3) session levels were scanned before the session had closed (look-ahead) — now
  scanned only after 07:00 (Asia) / 13:00 (London).

### 4.1 Alignment tables (07:15 UTC)

| field | BTC | ETH |
|---|---|---|
| price | 79,656.00 | 2,489.50 |
| daily_bias | neutral | up |
| trend 4h / 1h | range / range | up / up |
| 4h range | 78,600 .. 82,268 (04 15:59 / 03 23:59), pos 0.29 → discount | 2,430.6 .. 2,545.9, pos 0.51 → premium |
| 1h range | 79,497 .. 80,192 (0.23) | 2,449.3 .. 2,493.0 (0.92) |
| nearest zone above / below | FVG 1h 79,690–81,056 open (0.04 %) / OB 1h 78,881–79,366 tested (0.36 %) | FVG 1h 2,491.5–2,500.4 open (0.08 %) / FVG 4h 2,412.7–2,488.9 half_filled (0.02 %) |
| nearest pool above / below | asia_low 79,669 (0.02 %) / london_low 79,568 (0.11 %) | pdh 2,493.0 (0.14 %) / london_low 2,483.0 (0.26 %) |
| last event 4h / 1h | BOS up 79,250 (09-03 15:59) / BOS up 79,731 (09-05 15:59) | BOS up 2,490.0 (09-03 15:59) / BOS up 2,493.0 (09-06 01:59) |
| session | london +15m (clock: london +15m) | london +15m |
| day_type | range | range |
| asia range (frozen) | 79,669 .. 80,100 | 2,478.4 .. 2,524.2 |
| weekly_open / daily_open | 77,660 / 79,804 | 2,417.5 / 2,479.8 |
| pdh / pdl | 80,192 / 79,413 | 2,493.0 / 2,444.1 |
| pwh / pwl | 81,483 / 76,693 | 2,566.4 / 2,388.0 |
| ATR 15m / 1h / 4h | 107.88 / 225.22 / 747.21 | 7.34 / 11.61 / 30.59 |
| oi_now / book mid / taker rows 1h | 2.787 B / 79,672.5 / 59 | 2.242 B / 2,492.05 / 59 |
| funding_z / cohort net_dir | 0.60 / +0.55 | 0.15 / +1.00 |

Observations (not defects): the BTC `nearest_pool_above` is `asia_low 79,669` because price (79,656) is 13 $ below
today's Asia low — correct, the level is above price. Old 4h bullish OBs/FVGs from 16–20 Aug (−13 % … −21 %) are still
listed as fresh/open: nothing has traded into them, and doc 10 defines no age expiry for zones.

### 4.2 Sweeps, events, displacements, zones

Listed in full in §4.9 (BTC: 81 reclaimed of 227 candidates in 8 days, 146 never reclaimed within 3 candles; ETH: 73
of 244, 171 unreclaimed). Spot checks against the candles:

* BTC 2026-09-04 13:44–14:44 `london_low 79,156` series: wicks 78,953 / 79,068 / 78,600 → depth 1.88 / 0.82 / 5.15 ATR,
  all flagged TOO DEEP (M1 `too_deep` veto > 0.6 ATR) — this is the 09-04 12:59 down displacement (3.62 ATR1h) and the
  4h range low 78,600, correctly not tradeable as a sweep.
* BTC 2026-09-05 `asia_high 79,691` swept 9 times between 07:44 and 15:29 (depth 0.16–0.45 ATR, reclaim_q 0.14–1.00):
  the third and later sweeps are what M1's `third_sweep` veto (65 hits in the replay) is for.
* ETH 2026-09-06 07:14 `pdh 2,493.0` wick 2,507.7 = 2.00 ATR, reclaimed in 1 candle, q 0.66 — TOO DEEP for M1.
* 1h BOS/CHoCH: BTC 09-03 12:59 BOS up 78,177 → 19:59 BOS up 81,346 (the rally), 09-04 12:59 BOS down 80,497 (sell-off),
  09-05 15:59 BOS up 79,731 — all coincide with the displacement list (09-03 12:59/13:59/14:59/15:59/19:59 up,
  09-04 12:59/13:59 down). No CHoCH in the last 10 1h events for either coin: every event is a BOS, i.e. the 1h swing
  logic never flagged a trend change in the window even though BTC trend_1h alternated (range → up → down). That is
  consistent with `trend_1h == "range"` most of the time (a CHoCH needs an established trend to break).
* Displacements: **20/20 grade 0.50** — see D-61 / F-4: doc 10 §2.3's grade formula is saturated by its own
  qualification thresholds.
* Zones: BTC 1h bearish FVG 79,690–81,056 (09-04 13:59) is `open` although 09-05 16:59 traded to 80,192 = 37 % into it
  (half_filled needs ≥ 50 %); the 4h bearish FVG 79,862–80,497 is `half_filled` (80,192 = 52 %). Consistent.

### 4.3 Asia range freeze (07:00 UTC)

| coin | day | frozen (mind_model_state `asia_range:{day}`) | calc from 00:00–07:00 15m candles (28) | match |
|---|---|---|---|---|
| BTC | 2026-09-06 | 79,669.00 .. 80,100.00 | 79,669.00 .. 80,100.00 | OK |
| ETH | 2026-09-06 | 2,478.40 .. 2,524.20 | 2,478.40 .. 2,524.20 | OK |
| both | 2026-09-03/04/05 | — (no key) | see §4.9 | not applicable — deploy was 2026-09-05 22:45 UTC, the first 07:00 after deploy is 09-06 |

Worker journal (CEST = UTC+2): `Asia range frozen BTC 2026-09-06: 79669.0..80100.0` at `Sep 06 09:00:19` (= 07:00:19 UTC)
and the same line for ETH — frozen on the first evaluation after 07:00 UTC. The "frozen at" column in §4.9 shows `-`
because the stored asia dict carries no `ts` key (the row's `updated_ts` has it; cosmetic, not changed).

### 4.4 Weekly open / prior week high-low

Not frozen yet: the `weekly_levels:{wk}` key is written on the first 15m boundary of Monday 00:00 UTC and the first
Monday after deploy is 2026-09-07. The running reference levels are correct now: `weekly_open` 77,660.00 (BTC) /
2,417.50 (ETH) = the Monday 2026-08-31 00:00 UTC 1h AND 15m candle open; `pwh/pwl` 81,483 / 76,693 (BTC) and
2,566.4 / 2,388.0 (ETH) = max(h) / min(l) of the 168 1h candles of 2026-08-24 00:00 → 08-31 00:00. The freeze on
09-07 00:00 must reproduce the same numbers for W37 (to be checked on Monday; not part of this run).

### 4.5 Day type history — stuck on "range": input defect found and fixed (D-62)

* Prod has ONE freeze since deploy, `day_type:2026-09-06:07` = `range` for both coins, and both freezes carry
  `"vol_2h_ratio": null`. The 30-day replay froze **55/55 "range" for BTC and 55/55 for ETH** (07:00 + 14:00 keys,
  2026-08-07 → 09-06).
* Cause 1 (by construction, not a defect): doc 10 §3 `trend_up`/`trend_down` require OI 4h change and taker skew,
  `squeeze` requires funding z and OI 2h change — all None before 2026-09-03 14:17 UTC (feeds absent) — so only
  `event`, `no_trade` or `range` were reachable for 27 of the 30 days.
* Cause 2 (**code defect, fixed**): `volume_2h_ratio` took the last 8 closed 15m candles from `[now−120 min, now]`;
  the worker and the replay evaluate 5–20 s AFTER the boundary, so the window start cut off the 8th candle and the ratio
  was `None` on every evaluation → `no_trade` could never fire. The window is now anchored on the last closed 15m
  boundary (`end_ms = floor(now, 15 min)`); regression test
  `test_volume_2h_ratio_is_defined_a_few_seconds_after_the_boundary` (fails on the old code, passes on the fix).
  No threshold changed.
* Re-classification on prod data at every 07:00 / 14:00 of the last 3 days with the fixed input (`audit_daytype.py`,
  same loader, `now = boundary + 5 s`):

| coin | boundary UTC | vol_2h old → new | rv_pct | trend_1h | bos up/down | oi_4h | skew_2h | funding_z | label |
|---|---|---|---|---|---|---|---|---|---|
| BTC | 09-03 07:00 | None → 1.12 | 57.9 | range | 1/0 | – | – | – | range |
| BTC | 09-03 14:00 | None → 1.50 | 47.4 | up | 2/0 | – (feed not yet started) | – | – | range |
| BTC | 09-04 07:00 | None → 0.58 | 78.9 | up | 0/0 | −0.018 | 0.53 | 0.64 | range |
| BTC | 09-04 14:00 | None → 2.86 | 89.5 | range | 0/1 | +0.047 | 0.51 | 0.57 | **event** |
| BTC | 09-05 07:00 | None → 0.14 | 78.9 | down | 0/0 | −0.007 | 0.61 | 0.61 | range |
| BTC | 09-05 14:00 | None → 0.11 | 10.5 | range | 0/0 | −0.004 | 0.44 | −0.91 | **no_trade** |
| BTC | 09-06 07:00 | None → 0.51 | 0.0 | range | 0/0 | −0.011 | 0.39 | 0.60 | range |
| ETH | 09-03 07:00 | None → 0.54 | 68.4 | range | 0/0 | – | – | – | range |
| ETH | 09-03 14:00 | None → 0.85 | 47.4 | range | 1/0 | – | – | – | range |
| ETH | 09-04 07:00 | None → 0.68 | 68.4 | up | 0/0 | +0.008 | 0.58 | 0.22 | range |
| ETH | 09-04 14:00 | None → 3.32 | 89.5 | range | 1/1 | −0.023 | 0.48 | 0.20 | **event** |
| ETH | 09-05 07:00 | None → 0.10 | 78.9 | range | 0/0 | +0.001 | 0.25 | 0.18 | range |
| ETH | 09-05 14:00 | None → 0.12 | 5.3 | range | 0/0 | +0.002 | 0.49 | 0.17 | **no_trade** |
| ETH | 09-06 07:00 | None → 0.91 | 5.3 | up | 1/0 (open > VAH) | +0.006 | 0.50 | 0.15 | range |

  With the input present the label is **not stuck**: range / event / no_trade all occur in the last 3 days. The
  2026-09-03 rally (BTC 09-03 14:00: trend_1h up, 2 BOS up) would still be "range" because the OI/taker feeds did not
  exist yet at that boundary; 09-04 07:00 (BTC trend_1h up) fails doc 10's `oi_change_4h ≥ +1 %` (−1.8 %) and
  `delta_skew_2h ≥ 0.60` (0.53) — a threshold outcome, not a defect. `rv_pct = 0.0` for BTC on Sunday 09-06 07:00 is
  genuine: RV24h 0.136 (annualised) is the lowest of the 19 prior full days that the 2 000-bar 15m window holds
  (ETH 5.3 = 1/19). Doc 10 does not fix the percentile lookback; 19–20 days is what the loader's 2 000 15m candles give.

### 4.9 Full script output (2026-09-06 07:15 UTC) — for chart spot checks

### Part 4 run at 2026-09-06 07:15 UTC (now_ms=1788678930746)

cohort wallets: 30

#### BTC

##### 4.1 alignment table
```
  coin / price             | BTC 79,656.00
  swings 15m/1h/4h/1d      | 559/101/78/12
  daily_bias               | neutral
  trend_4h / trend_1h      | range / range
  range_4h                 | 78,600.00 .. 82,268.00 mid 80,434.00 discount pct 0.29
  range_1h                 | 79,497.00 .. 80,192.00 mid 79,844.50 discount pct 0.23
  nearest_zone_above       | FVG 1h 79,690.00..81,056.00 open
  nearest_zone_below       | OB 1h 78,881.00..79,366.00 tested
  nearest_pool_above       | asia_low 79,669.00 s=1.00 (of 22)
  nearest_pool_below       | london_low 79,568.00 s=1.00 (of 36)
  last_event_4h            | BOS up @ 79,250.00
  last_event_1h            | BOS up @ 79,731.00
  session                  | london +15m
  day_type                 | range
  event_within_2h / 30m    | False / False
  asia_range               | 79,669.00 .. 80,100.00
  weekly_open / daily_open | 77,660.00 / 79,804.00
  pdh / pdl                | 80,192.00 / 79,413.00
  pwh / pwl                | 81,483.00 / 76,693.00
```

| field | value | plausible |
|---|---|---|
| price | 79,656.00 | OK |
| trend 15m | range | OK |
| trend 1h | range | OK |
| trend 4h | range | OK |
| trend 1d | range | OK |
| 4h range | 78,600.00 .. 82,268.00 (2026-09-04 15:59 / 2026-09-03 23:59) | OK |
| price inside 4h range | True | OK |
| premium/discount consistent | discount pct=0.288 | OK |
| 1h range | 79,497.00 .. 80,192.00 | OK |
| session label vs UTC clock | london (15m) vs london (15m) | OK |
| day_type | range | OK |
| daily_bias | neutral | OK |
| ref weekly_open | 77,660.00 | OK |
| ref daily_open | 79,804.00 | OK |
| ref pdh | 80,192.00 | OK |
| ref pdl | 79,413.00 | OK |
| ref pwh | 81,483.00 | OK |
| ref pwl | 76,693.00 | OK |
| ref asia_high | 80,100.00 | OK |
| ref asia_low | 79,669.00 | OK |
| atr 15m | 107.88 | OK |
| atr 1h | 225.22 | OK |
| atr 4h | 747.21 | OK |
| asia (frozen struct) | 79,669.00..80,100.00 | OK |
| event_within_2h/30m | False/False | OK |
| oi_now | 2,786,630,000 | OK |
| book mid | 79,672.50 | OK |
| taker 1h rows | 59 | OK |
| funding_z (gauge) | 0.600649 | OK |
| cohort net_dir | 0.5452303478440553 | OK |

nearest live zones:
| tf | side | type | dir | bottom | top | status | dist % | dist ATR15 | created |
|---|---|---|---|---|---|---|---|---|---|
| 1h | above | FVG | bearish | 79,690.00 | 81,056.00 | open | 0.04% | 0.3 | 2026-09-04 13:59 |
| 1h | below | OB | bullish | 78,881.00 | 79,366.00 | tested | 0.36% | 2.7 | 2026-09-04 14:59 |
| 4h | above | FVG | bearish | 79,862.00 | 80,497.00 | half_filled | 0.26% | 1.9 | 2026-09-04 19:59 |
| 4h | below | OB | bullish | 77,635.00 | 77,685.00 | fresh | 2.47% | 18.3 | 2026-09-03 07:59 |

nearest pools:
| side | type | level | dist | tf | strength |
|---|---|---|---|---|---|
| above | asia_low | 79,669.00 | 0.02% | - | 1.0 |
| above | london_high | 79,774.00 | 0.15% | - | 1.0 |
| above | daily_open | 79,804.00 | 0.19% | - | 1.0 |
| above | asia_high | 80,100.00 | 0.56% | - | 1.0 |
| above | pdh | 80,192.00 | 0.67% | - | 1.0 |
| above | pwh | 81,483.00 | 2.29% | - | 1.0 |
| below | london_low | 79,568.00 | 0.11% | - | 1.0 |
| below | pdl | 79,413.00 | 0.31% | - | 1.0 |
| below | equal_lows | 78,592.50 | 1.34% | 1h | 2.0 |
| below | weekly_open | 77,660.00 | 2.51% | - | 1.0 |
| below | equal_lows | 77,461.50 | 2.75% | 1h | 2.0 |
| below | equal_lows | 76,941.50 | 3.41% | 1h | 2.0 |

liquidation clusters (top by notional):
| side | level | dist | notional | wallets |
|---|---|---|---|---|
| short | 120,653.33 | +51.47% | 171,730,464 | 1 |
| short | 126,354.68 | +58.63% | 99,182,735 | 1 |
| long | 53,540.84 | -32.78% | 79,736,000 | 1 |
| long | 68,383.56 | -14.15% | 47,044,240 | 1 |
| short | 150,464.98 | +88.89% | 34,087,140 | 1 |
| long | 65,236.50 | -18.10% | 33,223,911 | 1 |
| long | 46,128.16 | -42.09% | 23,925,600 | 1 |
| long | 46,494.83 | -41.63% | 23,925,600 | 1 |

##### 4.2 last 20 RECLAIMED 15m sweeps (of 81 reclaimed; 227 candidates in 8 days, 146 never reclaimed within 3 candles; ATR15 107.88)
| wick ts | level type | level | wick | depth ATR | reclaimed | reclaim_q | candles | flag |
|---|---|---|---|---|---|---|---|---|
| 2026-09-04 08:14 | asia_low | 80,528.00 | 80,497.00 | 0.29 | yes | 0.48 | 1 |  |
| 2026-09-04 13:44 | london_low | 79,156.00 | 78,953.00 | 1.88 | yes | 0.16 | 1 | TOO DEEP |
| 2026-09-04 14:29 | london_low | 79,156.00 | 79,068.00 | 0.82 | yes | 0.11 | 1 | TOO DEEP |
| 2026-09-04 14:44 | london_low | 79,156.00 | 78,600.00 | 5.15 | yes | 0.71 | 3 | TOO DEEP |
| 2026-09-04 15:29 | london_low | 79,156.00 | 79,135.00 | 0.19 | yes | 0.81 | 1 |  |
| 2026-09-05 07:44 | asia_high | 79,691.00 | 79,726.00 | 0.32 | yes | 0.15 | 1 |  |
| 2026-09-05 07:59 | asia_high | 79,691.00 | 79,731.00 | 0.37 | yes | 0.15 | 1 |  |
| 2026-09-05 09:14 | asia_high | 79,691.00 | 79,723.00 | 0.30 | yes | 1.00 | 2 |  |
| 2026-09-05 12:44 | asia_high | 79,691.00 | 79,729.00 | 0.35 | yes | 0.39 | 2 |  |
| 2026-09-05 13:29 | asia_high | 79,691.00 | 79,738.00 | 0.44 | yes | 0.77 | 2 |  |
| 2026-09-05 13:59 | london_low | 79,536.00 | 79,497.00 | 0.36 | yes | 0.24 | 1 |  |
| 2026-09-05 14:44 | asia_high | 79,691.00 | 79,708.00 | 0.16 | yes | 0.53 | 1 |  |
| 2026-09-05 14:59 | asia_high | 79,691.00 | 79,719.00 | 0.26 | yes | 0.14 | 1 |  |
| 2026-09-05 15:14 | asia_high | 79,691.00 | 79,719.00 | 0.26 | yes | 0.19 | 1 |  |
| 2026-09-05 15:29 | asia_high | 79,691.00 | 79,740.00 | 0.45 | yes | 0.39 | 1 |  |
| 2026-09-05 19:59 | london_high | 79,731.00 | 79,817.00 | 0.80 | yes | 0.67 | 1 | TOO DEEP |
| 2026-09-05 20:14 | london_high | 79,731.00 | 79,800.00 | 0.64 | yes | 0.21 | 1 | TOO DEEP |
| 2026-09-05 20:29 | london_high | 79,731.00 | 79,797.00 | 0.61 | yes | 0.08 | 1 | TOO DEEP |
| 2026-09-05 20:44 | london_high | 79,731.00 | 79,765.00 | 0.32 | yes | 0.73 | 2 |  |
| 2026-09-05 23:14 | london_high | 79,731.00 | 79,759.00 | 0.26 | yes | 0.57 | 1 |  |

##### 4.3 last 10 1h BOS/CHoCH
| ts | type | dir | level |
|---|---|---|---|
| 2026-08-30 23:59 | BOS | down | 77,936.00 |
| 2026-08-31 17:59 | BOS | up | 78,790.00 |
| 2026-09-01 08:59 | BOS | down | 78,153.00 |
| 2026-09-02 05:59 | BOS | up | 77,611.00 |
| 2026-09-02 10:59 | BOS | down | 76,687.00 |
| 2026-09-03 02:59 | BOS | up | 77,581.00 |
| 2026-09-03 12:59 | BOS | up | 78,177.00 |
| 2026-09-03 19:59 | BOS | up | 81,346.00 |
| 2026-09-04 12:59 | BOS | down | 80,497.00 |
| 2026-09-05 15:59 | BOS | up | 79,731.00 |

last 6 4h events:
| ts | type | dir | level |
|---|---|---|---|
| 2026-08-20 11:59 | BOS | up | 70,224.00 |
| 2026-08-23 07:59 | BOS | down | 76,558.00 |
| 2026-08-24 15:59 | BOS | up | 78,885.00 |
| 2026-08-28 19:59 | BOS | down | 77,618.00 |
| 2026-08-30 15:59 | BOS | up | 78,331.00 |
| 2026-09-03 15:59 | BOS | up | 79,250.00 |

##### 4.4 last 10 1h displacement candles
| close ts | dir | low | high | range ATR1h | body | grade | kind |
|---|---|---|---|---|---|---|---|
| 2026-09-03 12:59 | up | 77,859.00 | 78,748.00 | 1.97 | 0.79 | 0.50 | single |
| 2026-09-03 13:59 | up | 77,859.00 | 78,973.00 | 2.42 | 0.33 | 0.50 | 2-candle |
| 2026-09-03 14:59 | up | 78,667.00 | 80,546.00 | 3.35 | 0.92 | 0.50 | single |
| 2026-09-03 15:59 | up | 80,305.00 | 81,346.00 | 1.75 | 0.76 | 0.50 | single |
| 2026-09-03 19:59 | up | 80,849.00 | 81,755.00 | 1.57 | 0.75 | 0.50 | 2-candle |
| 2026-09-04 12:59 | down | 79,156.00 | 81,318.00 | 3.62 | 0.81 | 0.50 | single |
| 2026-09-04 13:59 | down | 78,953.00 | 81,318.00 | 3.90 | 0.09 | 0.50 | 2-candle |
| 2026-09-04 16:59 | up | 78,763.00 | 79,862.00 | 1.74 | 0.66 | 0.50 | 2-candle |
| 2026-09-05 16:59 | up | 79,648.00 | 80,192.00 | 2.14 | 0.53 | 0.50 | 2-candle |
| 2026-09-05 19:59 | down | 79,670.00 | 80,056.00 | 1.57 | 0.84 | 0.50 | 2-candle |

##### 4.5 fresh OBs and open FVGs (1h, 4h)
| tf | type | dir | bottom | top | status | grade | created | mid vs px |
|---|---|---|---|---|---|---|---|---|
| 1h | OB | bullish | 77,593.00 | 77,814.00 | fresh | 0.50 | 2026-09-03 09:59 | -2.45% |
| 1h | FVG | bullish | 77,972.00 | 78,420.00 | open | 0.50 | 2026-09-03 13:59 | -1.83% |
| 1h | OB | bearish | 81,161.00 | 81,181.00 | fresh | 0.50 | 2026-09-04 11:59 | +1.90% |
| 1h | FVG | bearish | 79,690.00 | 81,056.00 | open | 0.50 | 2026-09-04 13:59 | +0.90% |
| 4h | OB | bullish | 62,873.00 | 63,082.00 | fresh | 0.50 | 2026-08-16 23:59 | -20.94% |
| 4h | FVG | bullish | 63,110.00 | 63,402.00 | half_filled | 0.50 | 2026-08-17 07:59 | -20.59% |
| 4h | FVG | bullish | 63,750.00 | 63,938.00 | open | 0.50 | 2026-08-17 19:59 | -19.85% |
| 4h | OB | bullish | 64,266.00 | 64,300.00 | fresh | 0.50 | 2026-08-19 07:59 | -19.30% |
| 4h | FVG | bullish | 64,500.00 | 67,792.00 | open | 0.50 | 2026-08-19 19:59 | -16.96% |
| 4h | OB | bullish | 69,188.00 | 69,323.00 | fresh | 0.50 | 2026-08-20 03:59 | -13.06% |
| 4h | FVG | bullish | 69,999.00 | 71,134.00 | open | 0.50 | 2026-08-20 15:59 | -11.41% |
| 4h | OB | bullish | 77,635.00 | 77,685.00 | fresh | 0.50 | 2026-09-03 07:59 | -2.51% |
| 4h | FVG | bullish | 78,056.00 | 80,600.00 | half_filled | 0.50 | 2026-09-03 19:59 | -0.41% |
| 4h | OB | bearish | 80,630.00 | 81,181.00 | fresh | 0.50 | 2026-09-04 11:59 | +1.57% |
| 4h | FVG | bearish | 79,862.00 | 80,497.00 | half_filled | 0.50 | 2026-09-04 19:59 | +0.66% |

##### 4.6 Asia range freeze vs candles

| day | frozen low | frozen high | frozen at | calc low (00-07) | calc high | candles | match |
|---|---|---|---|---|---|---|---|
| 2026-09-03 | - | - | - | 76,951.00 | 77,911.00 | 28 | **CHECK** |
| 2026-09-04 | - | - | - | 80,528.00 | 81,400.00 | 28 | **CHECK** |
| 2026-09-05 | - | - | - | 79,413.00 | 79,691.00 | 28 | **CHECK** |
| 2026-09-06 | 79,669.00 | 80,100.00 | - | 79,669.00 | 80,100.00 | 28 | OK |

##### 4.7 weekly levels (2026-W36, week start 2026-08-31 00:00)
| item | value |
|---|---|
| weekly_open (frozen) | - |
| weekly_open (refs now) | 77,660.00 |
| Monday 00:00 1h candle open | 77,660.00 |
| Monday 00:00 15m candle open | 77,660.00 |
| pwh frozen / refs / last-week max(h) | - / 81,483.00 / 81,483.00 |
| pwl frozen / refs / last-week min(l) | - / 76,693.00 / 76,693.00 |
| last-week 1h candles | 168 |
| frozen at | - |

##### 4.8 day type freeze history
| key | label | frozen at | inputs |
|---|---|---|---|
| day_type:2026-09-06:07 | range | 2026-09-06 07:00 | {"atr_1h": 225.2173, "rv_pct": 0.0, "trend_1h": "range", "funding_z": 0.602, "prior_vah": 79957.556, "prior_val": 79559.361, "daily_open": 79804.0, "oi_change_2h": -0.0076, "oi_change_4h": -0.0111, "v |

current live day_type=range bias=neutral inputs={"event_within_2h": false, "event_within_30m": false, "funding_z": 0.600649, "price_move_2h": -126.0, "atr_1h": 225.21731328241063, "oi_change_2h": -0.006354187095508945, "oi_change_4h": -0.011233762316865903, "daily_open": 79804.0, "prior_vah": 79957.556, "prior_val": 79559.36099999999, "bos_1h_up_

#### ETH

##### 4.1 alignment table
```
  coin / price             | ETH 2,489.50
  swings 15m/1h/4h/1d      | 576/99/87/12
  daily_bias               | up
  trend_4h / trend_1h      | up / up
  range_4h                 | 2,430.60 .. 2,545.90 mid 2,488.25 premium pct 0.51
  range_1h                 | 2,449.30 .. 2,493.00 mid 2,471.15 premium pct 0.92
  nearest_zone_above       | FVG 1h 2,491.50..2,500.40 open
  nearest_zone_below       | FVG 4h 2,412.70..2,488.90 half_filled
  nearest_pool_above       | pdh 2,493.00 s=1.00 (of 23)
  nearest_pool_below       | london_low 2,483.00 s=1.00 (of 42)
  last_event_4h            | BOS up @ 2,490.00
  last_event_1h            | BOS up @ 2,493.00
  session                  | london +15m
  day_type                 | range
  event_within_2h / 30m    | False / False
  asia_range               | 2,478.40 .. 2,524.20
  weekly_open / daily_open | 2,417.50 / 2,479.80
  pdh / pdl                | 2,493.00 / 2,444.10
  pwh / pwl                | 2,566.40 / 2,388.00
```

| field | value | plausible |
|---|---|---|
| price | 2,489.50 | OK |
| trend 15m | range | OK |
| trend 1h | up | OK |
| trend 4h | up | OK |
| trend 1d | up | OK |
| 4h range | 2,430.60 .. 2,545.90 (2026-09-04 15:59 / 2026-09-04 11:59) | OK |
| price inside 4h range | True | OK |
| premium/discount consistent | premium pct=0.511 | OK |
| 1h range | 2,449.30 .. 2,493.00 | OK |
| session label vs UTC clock | london (15m) vs london (15m) | OK |
| day_type | range | OK |
| daily_bias | up | OK |
| ref weekly_open | 2,417.50 | OK |
| ref daily_open | 2,479.80 | OK |
| ref pdh | 2,493.00 | OK |
| ref pdl | 2,444.10 | OK |
| ref pwh | 2,566.40 | OK |
| ref pwl | 2,388.00 | OK |
| ref asia_high | 2,524.20 | OK |
| ref asia_low | 2,478.40 | OK |
| atr 15m | 7.34 | OK |
| atr 1h | 11.61 | OK |
| atr 4h | 30.59 | OK |
| asia (frozen struct) | 2,478.40..2,524.20 | OK |
| event_within_2h/30m | False/False | OK |
| oi_now | 2,241,640,000 | OK |
| book mid | 2,492.05 | OK |
| taker 1h rows | 59 | OK |
| funding_z (gauge) | 0.153345 | OK |
| cohort net_dir | 1.0 | OK |

nearest live zones:
| tf | side | type | dir | bottom | top | status | dist % | dist ATR15 | created |
|---|---|---|---|---|---|---|---|---|---|
| 1h | above | FVG | bullish | 2,491.50 | 2,500.40 | open | 0.08% | 0.3 | 2026-09-06 02:59 |
| 1h | below | OB | bullish | 2,479.90 | 2,481.20 | fresh | 0.33% | 1.1 | 2026-09-05 23:59 |
| 4h | above | OB | bearish | 2,503.70 | 2,522.90 | tested | 0.57% | 1.9 | 2026-09-04 11:59 |
| 4h | below | FVG | bullish | 2,412.70 | 2,488.90 | half_filled | 0.02% | 0.1 | 2026-09-03 19:59 |

nearest pools:
| side | type | level | dist | tf | strength |
|---|---|---|---|---|---|
| above | pdh | 2,493.00 | 0.14% | - | 1.0 |
| above | london_high | 2,507.70 | 0.73% | - | 1.0 |
| above | asia_high | 2,524.20 | 1.39% | - | 1.0 |
| above | equal_highs | 2,532.50 | 1.73% | 1h | 2.0 |
| above | equal_highs | 2,533.90 | 1.78% | 1h | 2.0 |
| above | equal_highs | 2,534.83 | 1.82% | 4h | 3.0 |
| below | london_low | 2,483.00 | 0.26% | - | 1.0 |
| below | daily_open | 2,479.80 | 0.39% | - | 1.0 |
| below | asia_low | 2,478.40 | 0.45% | - | 1.0 |
| below | equal_lows | 2,450.30 | 1.57% | 1h | 3.0 |
| below | pdl | 2,444.10 | 1.82% | - | 1.0 |
| below | equal_lows | 2,431.10 | 2.35% | 1h | 2.0 |

liquidation clusters (top by notional):
| side | level | dist | notional | wallets |
|---|---|---|---|---|
| short | 3,587.60 | +44.11% | 202,381,513 | 1 |
| short | 3,754.25 | +50.80% | 114,208,357 | 1 |
| long | 2,176.21 | -12.58% | 112,961,841 | 1 |
| long | 2,333.29 | -6.27% | 97,980,690 | 1 |
| long | 1,753.82 | -29.55% | 77,654,364 | 2 |
| short | 3,752.22 | +50.72% | 72,402,056 | 1 |
| long | 1,520.46 | -38.93% | 50,124,000 | 2 |
| long | 1,019.58 | -59.04% | 50,118,000 | 1 |

##### 4.2 last 20 RECLAIMED 15m sweeps (of 73 reclaimed; 244 candidates in 8 days, 171 never reclaimed within 3 candles; ATR15 7.34)
| wick ts | level type | level | wick | depth ATR | reclaimed | reclaim_q | candles | flag |
|---|---|---|---|---|---|---|---|---|
| 2026-09-03 07:14 | asia_high | 2,411.00 | 2,419.10 | 1.10 | yes | 0.64 | 2 | TOO DEEP |
| 2026-09-03 07:44 | asia_high | 2,411.00 | 2,412.70 | 0.23 | yes | 0.01 | 1 |  |
| 2026-09-03 08:14 | asia_high | 2,411.00 | 2,412.70 | 0.23 | yes | 0.47 | 1 |  |
| 2026-09-03 12:29 | asia_high | 2,411.00 | 2,412.00 | 0.14 | yes | 0.37 | 1 |  |
| 2026-09-04 08:59 | pdh | 2,529.40 | 2,534.00 | 0.63 | yes | 0.45 | 1 | TOO DEEP |
| 2026-09-04 08:59 | asia_high | 2,524.70 | 2,545.90 | 2.89 | yes | 0.30 | 3 | TOO DEEP |
| 2026-09-04 09:14 | pdh | 2,529.40 | 2,545.90 | 2.25 | yes | 0.30 | 2 | TOO DEEP |
| 2026-09-04 09:44 | pdh | 2,529.40 | 2,533.00 | 0.49 | yes | 0.56 | 1 |  |
| 2026-09-04 09:44 | asia_high | 2,524.70 | 2,533.00 | 1.13 | yes | 0.83 | 2 | TOO DEEP |
| 2026-09-04 09:59 | pdh | 2,529.40 | 2,531.10 | 0.23 | yes | 0.83 | 1 |  |
| 2026-09-04 10:44 | asia_high | 2,524.70 | 2,528.70 | 0.54 | yes | 0.59 | 3 | TOO DEEP |
| 2026-09-04 11:29 | asia_high | 2,524.70 | 2,527.60 | 0.39 | yes | 0.40 | 3 |  |
| 2026-09-04 12:29 | pdh | 2,529.40 | 2,530.80 | 0.19 | yes | 0.83 | 2 |  |
| 2026-09-04 12:29 | asia_high | 2,524.70 | 2,530.80 | 0.83 | yes | 0.83 | 2 | TOO DEEP |
| 2026-09-04 14:59 | london_low | 2,433.40 | 2,430.60 | 0.38 | yes | 0.25 | 1 |  |
| 2026-09-05 10:14 | asia_high | 2,456.10 | 2,458.10 | 0.27 | yes | 0.92 | 2 |  |
| 2026-09-05 13:44 | asia_high | 2,456.10 | 2,459.40 | 0.45 | yes | 0.36 | 2 |  |
| 2026-09-05 13:59 | london_low | 2,452.60 | 2,449.30 | 0.45 | yes | 0.36 | 1 |  |
| 2026-09-05 14:44 | asia_high | 2,456.10 | 2,457.50 | 0.19 | yes | 0.37 | 1 |  |
| 2026-09-06 07:14 | pdh | 2,493.00 | 2,507.70 | 2.00 | yes | 0.66 | 1 | TOO DEEP |

##### 4.3 last 10 1h BOS/CHoCH
| ts | type | dir | level |
|---|---|---|---|
| 2026-08-30 12:59 | BOS | up | 2,461.10 |
| 2026-08-30 23:59 | BOS | down | 2,451.70 |
| 2026-09-01 08:59 | BOS | down | 2,454.50 |
| 2026-09-02 09:59 | BOS | down | 2,387.00 |
| 2026-09-03 12:59 | BOS | up | 2,419.10 |
| 2026-09-04 08:59 | BOS | up | 2,524.70 |
| 2026-09-04 12:59 | BOS | down | 2,500.00 |
| 2026-09-05 16:59 | BOS | up | 2,461.30 |
| 2026-09-05 21:59 | BOS | up | 2,484.70 |
| 2026-09-06 01:59 | BOS | up | 2,493.00 |

last 6 4h events:
| ts | type | dir | level |
|---|---|---|---|
| 2026-08-16 23:59 | BOS | down | 1,876.40 |
| 2026-08-17 03:59 | BOS | up | 1,885.60 |
| 2026-08-19 15:59 | BOS | up | 1,922.00 |
| 2026-08-21 07:59 | BOS | up | 2,346.80 |
| 2026-09-02 11:59 | BOS | down | 2,383.20 |
| 2026-09-03 15:59 | BOS | up | 2,490.00 |

##### 4.4 last 10 1h displacement candles
| close ts | dir | low | high | range ATR1h | body | grade | kind |
|---|---|---|---|---|---|---|---|
| 2026-09-03 12:59 | up | 2,395.30 | 2,426.80 | 1.82 | 0.77 | 0.50 | 2-candle |
| 2026-09-03 13:59 | up | 2,406.00 | 2,435.20 | 1.68 | 0.34 | 0.50 | 2-candle |
| 2026-09-03 14:59 | up | 2,425.00 | 2,489.80 | 3.12 | 0.93 | 0.50 | single |
| 2026-09-03 15:59 | up | 2,479.80 | 2,517.60 | 1.72 | 0.74 | 0.50 | single |
| 2026-09-04 08:59 | up | 2,503.00 | 2,534.00 | 1.63 | 0.76 | 0.50 | single |
| 2026-09-04 12:59 | down | 2,433.40 | 2,530.80 | 4.06 | 0.74 | 0.50 | single |
| 2026-09-05 16:59 | up | 2,458.60 | 2,474.00 | 1.62 | 0.75 | 0.50 | single |
| 2026-09-05 17:59 | up | 2,458.60 | 2,484.70 | 2.65 | 0.72 | 0.50 | 2-candle |
| 2026-09-05 21:59 | up | 2,476.80 | 2,493.00 | 1.63 | 0.73 | 0.50 | single |
| 2026-09-06 01:59 | up | 2,488.50 | 2,507.40 | 1.78 | 0.86 | 0.50 | single |

##### 4.5 fresh OBs and open FVGs (1h, 4h)
| tf | type | dir | bottom | top | status | grade | created | mid vs px |
|---|---|---|---|---|---|---|---|---|
| 1h | OB | bullish | 2,393.30 | 2,403.30 | fresh | 0.50 | 2026-09-03 09:59 | -3.66% |
| 1h | FVG | bullish | 2,399.00 | 2,406.00 | open | 0.50 | 2026-09-03 12:59 | -3.49% |
| 1h | FVG | bullish | 2,411.20 | 2,415.90 | open | 0.50 | 2026-09-03 13:59 | -3.05% |
| 1h | OB | bullish | 2,453.50 | 2,458.10 | fresh | 0.50 | 2026-09-05 13:59 | -1.35% |
| 1h | FVG | bullish | 2,459.60 | 2,470.60 | open | 0.50 | 2026-09-05 17:59 | -0.98% |
| 1h | OB | bullish | 2,476.90 | 2,477.20 | fresh | 0.50 | 2026-09-05 20:59 | -0.50% |
| 1h | OB | bullish | 2,479.90 | 2,481.20 | fresh | 0.50 | 2026-09-05 23:59 | -0.36% |
| 1h | FVG | bullish | 2,491.50 | 2,500.40 | open | 0.50 | 2026-09-06 02:59 | +0.26% |
| 4h | FVG | bullish | 1,905.00 | 1,910.00 | half_filled | 0.50 | 2026-08-18 19:59 | -23.38% |
| 4h | OB | bullish | 1,911.30 | 1,916.90 | fresh | 0.50 | 2026-08-19 03:59 | -23.11% |
| 4h | FVG | bullish | 1,929.00 | 2,067.20 | open | 0.50 | 2026-08-19 19:59 | -19.74% |
| 4h | FVG | bullish | 2,107.00 | 2,224.00 | open | 0.50 | 2026-08-20 03:59 | -13.01% |
| 4h | OB | bullish | 2,251.60 | 2,252.90 | fresh | 0.50 | 2026-08-20 03:59 | -9.53% |
| 4h | OB | bullish | 2,322.30 | 2,326.90 | fresh | 0.50 | 2026-08-20 19:59 | -6.62% |
| 4h | FVG | bullish | 2,340.00 | 2,341.70 | open | 0.50 | 2026-08-21 07:59 | -5.97% |
| 4h | OB | bullish | 2,398.30 | 2,403.60 | fresh | 0.50 | 2026-09-03 07:59 | -3.56% |
| 4h | FVG | bullish | 2,412.70 | 2,488.90 | half_filled | 0.50 | 2026-09-03 19:59 | -1.55% |

##### 4.6 Asia range freeze vs candles

| day | frozen low | frozen high | frozen at | calc low (00-07) | calc high | candles | match |
|---|---|---|---|---|---|---|---|
| 2026-09-03 | - | - | - | 2,369.20 | 2,411.00 | 28 | **CHECK** |
| 2026-09-04 | - | - | - | 2,497.40 | 2,524.70 | 28 | **CHECK** |
| 2026-09-05 | - | - | - | 2,444.10 | 2,456.10 | 28 | **CHECK** |
| 2026-09-06 | 2,478.40 | 2,524.20 | - | 2,478.40 | 2,524.20 | 28 | OK |

##### 4.7 weekly levels (2026-W36, week start 2026-08-31 00:00)
| item | value |
|---|---|
| weekly_open (frozen) | - |
| weekly_open (refs now) | 2,417.50 |
| Monday 00:00 1h candle open | 2,417.50 |
| Monday 00:00 15m candle open | 2,417.50 |
| pwh frozen / refs / last-week max(h) | - / 2,566.40 / 2,566.40 |
| pwl frozen / refs / last-week min(l) | - / 2,388.00 / 2,388.00 |
| last-week 1h candles | 168 |
| frozen at | - |

##### 4.8 day type freeze history
| key | label | frozen at | inputs |
|---|---|---|---|
| day_type:2026-09-06:07 | range | 2026-09-06 07:00 | {"atr_1h": 11.6147, "rv_pct": 5.2632, "trend_1h": "up", "funding_z": 0.1536, "prior_vah": 2478.742, "prior_val": 2448.0645, "daily_open": 2479.8, "oi_change_2h": 0.0113, "oi_change_4h": 0.0062, "vol_2 |

current live day_type=range bias=up inputs={"event_within_2h": false, "event_within_30m": false, "funding_z": 0.153345, "price_move_2h": -13.0, "atr_1h": 11.614724611937833, "oi_change_2h": -0.000249754705200278, "oi_change_4h": -0.006378461279326952, "daily_open": 2479.8, "prior_vah": 2478.7419999999997, "prior_val": 2448.0644999999995, "bo

## Part 5 — The feed-missing veto

DECISIONS D-31 added three vetoes to every model: `feed_missing_oi` (no OI row in the last hour), `feed_missing_taker` (no
taker-flow row in the last hour), `feed_missing_book` (no book mid in the last 5 minutes).

**Fire count since deploy (prod, 372 evaluations 2026-09-05 22:45 → 2026-09-06 06:15): 0 for all three, every model.**
The OI/taker/book feeds have been continuous since 2026-09-03 14:17 UTC (feed start).

**Change (D-51): `feed_missing_book` removed.** Owner rule: only OI, taker delta and candles are required; every other feed is
optional and its absence must set the affected reason to 0, never veto. Only M3 `thin_bids` reads the book (already 0 when the
book is None) and a resting paper order simply waits for the mid tape. `feed_missing_oi` / `feed_missing_taker` stay with the
1-hour lookback — the OI and taker samplers write every minute, so a 60-minute gap is a genuine outage, not strictness.
`_feed_vetoes` now returns at most those two. Tests: `test_feed_vetoes_all_missing`, `test_feed_vetoes_none_when_fresh`,
`test_feed_vetoes_stale_book_is_not_a_veto`.

Optional-feed behaviour verified in `Snapshot` (`test_snapshot_empty_feeds_read_none_or_zero`): `book_depth()` → None,
`liq()` → 0, `gauge` fields → None/0, cohort → {} — the reasons that consume them clip to 0.

In the 30-day replay (Part 8) the two remaining vetoes fired on every evaluation before 2026-09-03 14:17 (no feed rows exist
before that), which is the correct behaviour for absent REQUIRED feeds; the second replay pass suppresses them to exercise the
structure stage over the whole window.

**Feed-era replay (pass 3 / pass 4, 2026-09-03 13:30 → 09-06 06:15, vetoes ON):** `feed_missing_oi` and `feed_missing_taker`
fired together on **8 of 520 evaluations per model = 1.5 %** (every model identical), all on the four boundaries 13:30, 13:45,
14:00 and 14:15 on 2026-09-03 — the boundaries before the first OI / taker row at 14:17. Missing feed each time: both OI and
taker (the two samplers started in the same deploy). No firing after 14:17. Well under the 10 % threshold; the feed and the
1-hour lookback are left as they are. Since the audit deploy (2026-09-06 07:50 UTC) the veto count on prod is re-checked in
§9.4.

## Part 6 — Mind math and wiring

| check | test | result |
|---|---|---|
| raw = Σ strength·weight / Σ weight (hand-built 4-reason list) | `test_raw_is_weighted_mean_of_strength_times_weight`, `test_evaluate_weighted_average_and_tiers` | pass |
| multipliers multiplied, not added (0.8 × 1.25 = 1.0, 0.7 × 0.7 = 0.49) | `test_multipliers_are_multiplied_not_added` | pass |
| a veto forces `take=False` at conviction 1.0 | `test_veto_forces_take_false_regardless_of_conviction`, `test_veto_blocks_even_at_full_conviction` | pass |
| tiers switch at exactly 0.55 / 0.70 (0.5499 skip, 0.55 half, 0.6999 half, 0.70 full) | `test_size_tiers_switch_at_exactly_055_and_070` | pass |
| `size_for` half → 0.75 % risk, full → 1.5 % (model trades) | `test_size_tier_maps_to_075_and_15_percent_risk_in_size_for` | pass |
| `Mind.manage` runs on every closed 15m candle for open model positions | runner `_open`: `on_15m and c15[-1].ts > fill_ts` → `mind.manage(snap, …)`; `test_counts2_check_alone_exits_on_two_consecutive_candles` | pass |
| a count-2 check alone exits after 2 consecutive failing candles (streak ≥ 2 with count ≥ exit_threshold) | same test + `test_manage_thesis_failed_needs_two_consecutive_candles` | pass |
| dead-trade exit at 1.5 × expected_hold with progress < 0.5 R (and NOT at ≥ 0.5 R) | `test_dead_trade_fires_at_15x_hold_with_progress_below_half_r`, `test_manage_dead_trade_at_150pct_hold_without_progress` | pass |
| T1 moves the stop to breakeven | runner `_open` T1 branch (`be_px = entry`, stored `stop_px`), `test_partial_then_close_r_multiple` | pass |
| `mind_weights` = doc starting weights, unchanged | `test_mind_weights_equal_doc_starting_weights` (all six models, every key/weight vs docs 11–16) | pass |
| in-trade checks: which count 2, exit_threshold, expected_hold / hard stop | `test_in_trade_checks_counting_and_exit_threshold` — count-2 checks: M1 `level_lost`, M2 `below_ob`, M3 `re_approach_with_oi` + `close_above_level`, M4 `higher_high_1h`, M5 `below_asia_low`, M6 `lost_weekly_open`; all others 1; `exit_threshold` 2 for every model; hold/hard 90/240, 180/480, 120/360, 480/2160, 90/(window end + 120), 1440/(Fri 20:00) | pass |
| learning disabled: `learning_enabled=False`, `learning_start_after_days=60`; common config 0.55 / 0.70 / exit 2 / dead 1.5× / partial 40 | `test_learning_is_disabled` | pass |
| thesis populated, no `{…}` placeholders | `test_thesis_is_populated_with_no_unfilled_placeholders` (regex `\{[a-z_]+\}` on every model's fixture thesis) | pass |

## Part 7 — Paper execution realism

| rule | where | evidence |
|---|---|---|
| post-only limit fills only when a trade prints THROUGH the limit (strict `<` buy / `>` sell), never on touch | `execution/paper.py::limit_fills_through`; runner `_pending` walks the 5-second mid tape (`_mids`) and fills at the limit price | `test_limit_fills_through_not_on_touch`, `test_fill_rule_is_strict_through`, `test_place_and_fill_through` |
| an order that would cross at placement is re-quoted one tick inside (post-only), never filled as taker | `paper.py::would_cross / requote_inside` | `test_would_cross_and_requote`, `test_place_requotes_one_tick_inside` |
| stops are taker fills with the taker fee (0.045 %) | `manager.py` stop/hard-stop close → `fee_for("stop", …)` = TAKER_FEE | `test_stop_exit_is_taker_and_negative_r`, `test_stop_order_fires_taker_fee`, `test_fee_math_maker_vs_taker` |
| entry maker fee 0.015 % | `fee_for("entry")` = MAKER_FEE | same |
| partial at T1 = 40 % (M3 = 50 %) | `Intent.partial_pct` per model; runner `_open` closes `size × partial_pct` | `test_intent_partial_pct_trail_and_hard_stop`, `test_partial_then_close_r_multiple` |
| M5 one attempt per window per coin | `model_state["m5:{day}:{window}"]` → `already_taken` veto | `test_m5_one_attempt_per_window_per_coin` |
| M6 one attempt per week per coin per direction | `model_state["m6:{week}:{dir}"]` | `test_m6_one_attempt_per_week_per_coin_per_direction` |
| M3 one attempt per level | doc 13 has NO one-attempt rule; repeats are handled by the failures-at-level multiplier (1.0/1.1/0.8) and `second_failure` (D-54) — none enforced, by the doc | `test_m1_m2_m3_m4_have_no_one_attempt_veto` |
| unfilled orders expire (`entry_valid_until`) / cancel on a 15m close through the level (`cancel_level`, D-43) | runner `_pending` | `test_time_stop_expiry`; M1 fallback wiring in `test_reclaim_fvg_finds_bullish_gap_after_reclaim` |
| hard time stops per model (M1 240 m, M2 480 m, M3 360 m, M4 2160 m, M5 window end + 120 m, M6 Friday 20:00 UTC) | `Intent.hard_stop_ts` | `test_intent_partial_pct_trail_and_hard_stop` |

Replay caveat (Part 8): where `strat_book_5s` has no rows (before 2026-09-03 14:17) the replay harness synthesises the mid
tape from the closed 15m candle (open → adverse extreme → favourable extreme → close), so a stop inside a candle wins over a
target. The production path is unchanged.

## Part 8 — Thirty-day replay

### 8.1 Method

* Data: a copy of prod (`perpl_replay` — `strat_candles` 15m/1h/4h/1d, `strat_oi_1m`, `strat_trades_1m`, `strat_book_5s`,
  `strat_gauge`, `strat_liquidations`, `mind_calibration`) for BTC and ETH. The candle history covers the full 30 days;
  the OI / taker / book / gauge feeds exist only since **2026-09-03 14:17 UTC** (the feed collectors were started with the
  no-gates deploy), so the first ~27 days of the window have candles + liquidations + cohort only.
* Code path: `ModelEvaluator.run(now_ms=boundary+5s)` on every closed 15m boundary — the SAME evaluator, Minds,
  `ModelTradeManager`, `PaperTradeManager` and RiskEngine the worker runs; nothing bypassed. Harness `audit_part8.py`
  (kept in the report appendix path `/root/audit_part8.py` on the server; also in the session scratchpad).
* Three harness patches, none of which change model logic:
  1. `build_cohort` computed ONCE against prod (read-only) and reused for every boundary — the real call costs 37–112 s
     per cycle (it scans `hl_fill_events`), which makes a 2 881-boundary replay impossible otherwise. Consequence: cohort
     `net_dir` / crowd figures are the CURRENT cohort, not the historical one, for all 30 days.
  2. `PaperTradeManager._mids`: when `strat_book_5s` has no rows for a fill/stop window (before 2026-09-03) a synthetic
     tape is built from the closed 15m candles — open, ADVERSE extreme, favourable extreme, close — so a stop inside a candle
     always wins over a target inside the same candle (conservative).
  3. `--no-feed-veto` (pass 2 only): `_feed_vetoes` returns `[]` so the structure/sequence stage is exercised for the full
     30 days; the OI / taker-based reasons read 0 / None where the feed is absent, exactly as the doc says an optional
     missing input must behave.
* Four passes:
  * **pass 1** — the PRE-audit tree (the code that was deployed as `94ae37e`, copied to `/root/audit_tree` before any
    fix), feed vetoes ON. Shows what the shipped code would have done.
  * **pass 2** — the FIXED tree (all D-42…D-61 applied), `--no-feed-veto`. This is the pass the frequency comparison uses.
  * **pass 3 / pass 4** — feed era only (2026-09-03 13:30 → 09-06 06:15, `REPLAY_DAYS=2.7`), feed vetoes ON; pass 3 with
    D-62 applied, pass 4 with D-62 + D-63 (the final tree). §8.5.
* Not modelled: Telegram outbox (rows written, not sent — table cleared at start), UI. `strat_signals` rows are written
  exactly as the worker writes them, so `audit_stats.py` produces the same tables for the replay as for prod.
* Displacement grade: the 1h displacement detector grades on body/range only — on this data every graded candle scores
  0.5 (see Part 4 §4.4), so M2 `displacement` and M4 `displacement` sit at 0.5 whenever a displacement exists. Recorded as
  finding F-4 below (§8.6) (it is the doc's formula; not changed).

### 8.2 Pass 1 — pre-audit tree, feed vetoes ON (what the deployed code would have done)

# stats for perpl_replay: 34572 model evaluation rows since start, 2026-08-07 06:15 -> 2026-09-06 06:15; 0 model trades

#### Veto hit rates, conviction histograms (non-vetoed rows)

##### M1: 5762 evaluations, 2 non-vetoed
| veto | hits | rate |
|---|---|---|
| no_setup | 5551 | 96.3% |
| feed_missing_book | 5252 | 91.1% |
| feed_missing_oi | 5250 | 91.1% |
| feed_missing_taker | 5250 | 91.1% |
| third_sweep | 129 | 2.2% |
| too_deep | 111 | 1.9% |
| event_30m | 8 | 0.1% |

| bucket | raw_conviction | conviction |
|---|---|---|
| 0.0 | 0 | 0 |
| 0.1 | 0 | 1 |
| 0.2 | 1 | 1 |
| 0.3 | 1 | 0 |
| 0.4 | 0 | 0 |
| 0.5 | 0 | 0 |
| 0.6 | 0 | 0 |
| 0.7 | 0 | 0 |
| 0.8 | 0 | 0 |
| 0.9 | 0 | 0 |
| 1.0 | 0 | 0 |

non-vetoed with conviction >= 0.55: 0; fired=0; tiers={}

top waiting_for:
| waiting_for (truncated) | n |
|---|---|
| no reclaimed sweep of an eligible low on this candle; no reclaimed sweep of an eligible hi | 5142 |
| vetoed: too_deep, third_sweep, feed_missing_oi, feed_missing_taker, feed_missing_book | 63 |
| vetoed: third_sweep, feed_missing_oi, feed_missing_taker, feed_missing_book | 60 |
| vetoed: too_deep, feed_missing_oi, feed_missing_taker, feed_missing_book | 45 |
| vetoed: feed_missing_oi, feed_missing_taker, feed_missing_book | 33 |
| london_low reclaim quality 0.47 < 0.5; no reclaimed sweep of an eligible low on this candl | 4 |
| no reclaimed sweep of an eligible low on this candle; london_high reclaim quality 0.21 < 0 | 4 |
| asia_low reclaim quality 0.48 < 0.5; no reclaimed sweep of an eligible low on this candle; | 4 |

| coin | evaluations | setup reached | fired |
|---|---|---|---|
| BTC | 2881 | 97 | 0 |
| ETH | 2881 | 114 | 0 |

BTC: 2881 evaluations, 1 non-vetoed; vetoes: no_setup=2784, feed_missing_book=2626, feed_missing_oi=2625, feed_missing_taker=2625, third_sweep=65, too_deep=53, event_30m=4; conviction hist (non-vetoed): 0.1:1

ETH: 2881 evaluations, 1 non-vetoed; vetoes: no_setup=2767, feed_missing_book=2626, feed_missing_oi=2625, feed_missing_taker=2625, third_sweep=64, too_deep=58, event_30m=4; conviction hist (non-vetoed): 0.2:1

##### M2: 5762 evaluations, 0 non-vetoed
| veto | hits | rate |
|---|---|---|
| no_setup | 5762 | 100.0% |
| feed_missing_book | 5252 | 91.1% |
| feed_missing_oi | 5250 | 91.1% |
| feed_missing_taker | 5250 | 91.1% |
| late_day | 360 | 6.2% |
| event_30m | 8 | 0.1% |

| bucket | raw_conviction | conviction |
|---|---|---|
| 0.0 | 0 | 0 |
| 0.1 | 0 | 0 |
| 0.2 | 0 | 0 |
| 0.3 | 0 | 0 |
| 0.4 | 0 | 0 |
| 0.5 | 0 | 0 |
| 0.6 | 0 | 0 |
| 0.7 | 0 | 0 |
| 0.8 | 0 | 0 |
| 0.9 | 0 | 0 |
| 1.0 | 0 | 0 |

non-vetoed with conviction >= 0.55: 0; fired=0; tiers={}

top waiting_for:
| waiting_for (truncated) | n |
|---|---|
| 4h trend range, no fresh 4h CHoCH up confirmed by 1h BOS (long); 4h trend range, no fresh  | 4881 |
| displacement at 2,120.1 left no 1h OB; 4h trend up, no fresh 4h CHoCH down confirmed by 1h | 72 |
| 1h BOS up at 2,439.0 was not a displacement candle; 4h trend up, no fresh 4h CHoCH down co | 72 |
| range day and the 1h break at 2,319.9 is not a 4h level; 4h trend up, no fresh 4h CHoCH do | 64 |
| range day and the 1h break at 72,501.0 is not a 4h level; 4h trend up, no fresh 4h CHoCH d | 60 |
| 1h BOS up at 75,816.0 was not a displacement candle; 4h trend up, no fresh 4h CHoCH down c | 56 |
| zone from the break at 2,450.0 already broken/filled; 4h trend up, no fresh 4h CHoCH down  | 56 |
| displacement at 64,760.0 left no 1h OB; 4h trend up, no fresh 4h CHoCH down confirmed by 1 | 52 |

| coin | evaluations | setup reached | fired |
|---|---|---|---|
| BTC | 2881 | 0 | 0 |
| ETH | 2881 | 0 | 0 |

BTC: 2881 evaluations, 0 non-vetoed; vetoes: no_setup=2881, feed_missing_book=2626, feed_missing_oi=2625, feed_missing_taker=2625, late_day=180, event_30m=4; conviction hist (non-vetoed): -

ETH: 2881 evaluations, 0 non-vetoed; vetoes: no_setup=2881, feed_missing_book=2626, feed_missing_oi=2625, feed_missing_taker=2625, late_day=180, event_30m=4; conviction hist (non-vetoed): -

##### M3: 5762 evaluations, 0 non-vetoed
| veto | hits | rate |
|---|---|---|
| no_setup | 5752 | 99.8% |
| cohort_adding_longs | 5301 | 92.0% |
| feed_missing_book | 5252 | 91.1% |
| feed_missing_oi | 5250 | 91.1% |
| feed_missing_taker | 5250 | 91.1% |
| event_30m | 8 | 0.1% |
| squeeze_risk | 6 | 0.1% |

| bucket | raw_conviction | conviction |
|---|---|---|
| 0.0 | 0 | 0 |
| 0.1 | 0 | 0 |
| 0.2 | 0 | 0 |
| 0.3 | 0 | 0 |
| 0.4 | 0 | 0 |
| 0.5 | 0 | 0 |
| 0.6 | 0 | 0 |
| 0.7 | 0 | 0 |
| 0.8 | 0 | 0 |
| 0.9 | 0 | 0 |
| 1.0 | 0 | 0 |

non-vetoed with conviction >= 0.55: 0; fired=0; tiers={}

top waiting_for:
| waiting_for (truncated) | n |
|---|---|
| no swing-failure + retest at a pre-session high on this candle; price in the premium of th | 3299 |
| price in the discount of the 4h range (pct 0.39) — no failed-high short; no swing-failure  | 85 |
| price in the discount of the 4h range (pct 0.28) — no failed-high short; no swing-failure  | 77 |
| price in the discount of the 4h range (pct 0.30) — no failed-high short; no swing-failure  | 76 |
| price in the discount of the 4h range (pct 0.35) — no failed-high short; no swing-failure  | 73 |
| price in the discount of the 4h range (pct 0.26) — no failed-high short; no swing-failure  | 68 |
| price in the discount of the 4h range (pct 0.40) — no failed-high short; no swing-failure  | 68 |
| price in the discount of the 4h range (pct 0.27) — no failed-high short; no swing-failure  | 66 |

| coin | evaluations | setup reached | fired |
|---|---|---|---|
| BTC | 2881 | 3 | 0 |
| ETH | 2881 | 7 | 0 |

BTC: 2881 evaluations, 0 non-vetoed; vetoes: no_setup=2878, cohort_adding_longs=2659, feed_missing_book=2626, feed_missing_oi=2625, feed_missing_taker=2625, event_30m=4, squeeze_risk=2; conviction hist (non-vetoed): -

ETH: 2881 evaluations, 0 non-vetoed; vetoes: no_setup=2874, cohort_adding_longs=2642, feed_missing_book=2626, feed_missing_oi=2625, feed_missing_taker=2625, squeeze_risk=4, event_30m=4; conviction hist (non-vetoed): -

##### M4: 5762 evaluations, 0 non-vetoed
| veto | hits | rate |
|---|---|---|
| no_setup | 5762 | 100.0% |
| feed_missing_book | 5252 | 91.1% |
| feed_missing_oi | 5250 | 91.1% |
| feed_missing_taker | 5250 | 91.1% |
| event_30m | 8 | 0.1% |

| bucket | raw_conviction | conviction |
|---|---|---|
| 0.0 | 0 | 0 |
| 0.1 | 0 | 0 |
| 0.2 | 0 | 0 |
| 0.3 | 0 | 0 |
| 0.4 | 0 | 0 |
| 0.5 | 0 | 0 |
| 0.6 | 0 | 0 |
| 0.7 | 0 | 0 |
| 0.8 | 0 | 0 |
| 0.9 | 0 | 0 |
| 1.0 | 0 | 0 |

non-vetoed with conviction >= 0.55: 0; fired=0; tiers={}

top waiting_for:
| waiting_for (truncated) | n |
|---|---|
| no 4h CHoCH down in the last 72h; no 4h CHoCH up in the last 72h | 5474 |
| no 4h CHoCH down in the last 72h; 4h trend down not extended (BOS 2, 3d move +0.70%) | 288 |

| coin | evaluations | setup reached | fired |
|---|---|---|---|
| BTC | 2881 | 0 | 0 |
| ETH | 2881 | 0 | 0 |

BTC: 2881 evaluations, 0 non-vetoed; vetoes: no_setup=2881, feed_missing_book=2626, feed_missing_oi=2625, feed_missing_taker=2625, event_30m=4; conviction hist (non-vetoed): -

ETH: 2881 evaluations, 0 non-vetoed; vetoes: no_setup=2881, feed_missing_book=2626, feed_missing_oi=2625, feed_missing_taker=2625, event_30m=4; conviction hist (non-vetoed): -

##### M5: 5762 evaluations, 1 non-vetoed
| veto | hits | rate |
|---|---|---|
| no_setup | 5725 | 99.4% |
| feed_missing_book | 5252 | 91.1% |
| feed_missing_oi | 5250 | 91.1% |
| feed_missing_taker | 5250 | 91.1% |
| range_bad | 21 | 0.4% |
| too_deep | 20 | 0.3% |
| opened_outside | 12 | 0.2% |
| event_30m | 8 | 0.1% |

| bucket | raw_conviction | conviction |
|---|---|---|
| 0.0 | 0 | 0 |
| 0.1 | 0 | 0 |
| 0.2 | 1 | 1 |
| 0.3 | 0 | 0 |
| 0.4 | 0 | 0 |
| 0.5 | 0 | 0 |
| 0.6 | 0 | 0 |
| 0.7 | 0 | 0 |
| 0.8 | 0 | 0 |
| 0.9 | 0 | 0 |
| 1.0 | 0 | 0 |

non-vetoed with conviction >= 0.55: 0; fired=0; tiers={}

top waiting_for:
| waiting_for (truncated) | n |
|---|---|
| outside the London 07–09 / New York 13–15 UTC windows | 4682 |
| waiting for the first closed candle of the window | 120 |
| vetoed: too_deep, range_bad, feed_missing_oi, feed_missing_taker, feed_missing_book | 10 |
| no reclaimed newyork raid of the Asia low 64,137.0 on this candle; newyork raid of Asia hi | 8 |
| no reclaimed newyork raid of the Asia low 1,893.5 on this candle; newyork raid of Asia hig | 8 |
| no reclaimed london raid of the Asia low 64,778.0 on this candle; no reclaimed london raid | 8 |
| no reclaimed london raid of the Asia low 1,911.3 on this candle; no reclaimed london raid  | 8 |
| no reclaimed newyork raid of the Asia low 1,911.3 on this candle; newyork raid of Asia hig | 8 |

| coin | evaluations | setup reached | fired |
|---|---|---|---|
| BTC | 2881 | 15 | 0 |
| ETH | 2881 | 22 | 0 |

BTC: 2881 evaluations, 1 non-vetoed; vetoes: no_setup=2866, feed_missing_book=2626, feed_missing_oi=2625, feed_missing_taker=2625, range_bad=10, too_deep=7, opened_outside=4, event_30m=4; conviction hist (non-vetoed): 0.2:1

ETH: 2881 evaluations, 0 non-vetoed; vetoes: no_setup=2859, feed_missing_book=2626, feed_missing_oi=2625, feed_missing_taker=2625, too_deep=13, range_bad=11, opened_outside=8, event_30m=4; conviction hist (non-vetoed): -

##### M6: 5762 evaluations, 0 non-vetoed
| veto | hits | rate |
|---|---|---|
| feed_missing_book | 5252 | 91.1% |
| feed_missing_oi | 5250 | 91.1% |
| feed_missing_taker | 5250 | 91.1% |
| no_setup | 5204 | 90.3% |
| too_late | 152 | 2.6% |
| stop_too_wide | 80 | 1.4% |
| daily_strong_down | 56 | 1.0% |
| shallow_loss | 20 | 0.3% |
| event_30m | 8 | 0.1% |

| bucket | raw_conviction | conviction |
|---|---|---|
| 0.0 | 0 | 0 |
| 0.1 | 0 | 0 |
| 0.2 | 0 | 0 |
| 0.3 | 0 | 0 |
| 0.4 | 0 | 0 |
| 0.5 | 0 | 0 |
| 0.6 | 0 | 0 |
| 0.7 | 0 | 0 |
| 0.8 | 0 | 0 |
| 0.9 | 0 | 0 |
| 1.0 | 0 | 0 |

non-vetoed with conviction >= 0.55: 0; fired=0; tiers={}

top waiting_for:
| waiting_for (truncated) | n |
|---|---|
| price never lost the weekly open this week on 1h closes (long); 1h not closed below weekly | 1256 |
| 1h not closed above weekly open 64,868.0 for a long; 1h reclaim at 2026-08-10 12:59 is old | 572 |
| before Monday 12:00 UTC — reclaim window not open | 376 |
| 1h not closed above weekly open 1,910.0 for a long; 1h reclaim at 2026-08-12 13:59 is olde | 376 |
| vetoed: feed_missing_oi, feed_missing_taker, feed_missing_book | 322 |
| 1h reclaim at 2026-08-04 09:59 is older than the entry window; 1h not closed below weekly  | 263 |
| 1h reclaim at 2026-08-05 16:59 is older than the entry window; 1h not closed below weekly  | 263 |
| 1h reclaim at 2026-09-03 10:59 is older than the entry window; 1h not closed below weekly  | 222 |

| coin | evaluations | setup reached | fired |
|---|---|---|---|
| BTC | 2881 | 164 | 0 |
| ETH | 2881 | 394 | 0 |

BTC: 2881 evaluations, 0 non-vetoed; vetoes: no_setup=2717, feed_missing_book=2626, feed_missing_oi=2625, feed_missing_taker=2625, too_late=60, daily_strong_down=56, stop_too_wide=24, event_30m=4; conviction hist (non-vetoed): -

ETH: 2881 evaluations, 0 non-vetoed; vetoes: feed_missing_book=2626, feed_missing_oi=2625, feed_missing_taker=2625, no_setup=2487, too_late=92, stop_too_wide=56, shallow_loss=20, event_30m=4; conviction hist (non-vetoed): -

#### Part 8 takes

no trades

### 8.3 Pass 2 — fixed tree, `--no-feed-veto` (per model and per coin: evaluations, vetoes, histograms, takes)

# stats for perpl_replay: 34572 model evaluation rows since start, 2026-08-07 06:15 -> 2026-09-06 06:15; 4 model trades

#### Veto hit rates, conviction histograms (non-vetoed rows)

##### M1: 5762 evaluations, 35 non-vetoed
| veto | hits | rate |
|---|---|---|
| no_setup | 5551 | 96.3% |
| third_sweep | 129 | 2.2% |
| too_deep | 111 | 1.9% |
| event_30m | 8 | 0.1% |

| bucket | raw_conviction | conviction |
|---|---|---|
| 0.0 | 0 | 0 |
| 0.1 | 0 | 2 |
| 0.2 | 1 | 5 |
| 0.3 | 13 | 18 |
| 0.4 | 18 | 7 |
| 0.5 | 3 | 1 |
| 0.6 | 0 | 2 |
| 0.7 | 0 | 0 |
| 0.8 | 0 | 0 |
| 0.9 | 0 | 0 |
| 1.0 | 0 | 0 |

non-vetoed with conviction >= 0.55: 3; fired=3; tiers={'half': 3}

top waiting_for:
| waiting_for (truncated) | n |
|---|---|
| no reclaimed sweep of an eligible low on this candle; no reclaimed sweep of an eligible hi | 5142 |
| vetoed: too_deep, third_sweep | 64 |
| vetoed: third_sweep | 64 |
| vetoed: too_deep | 47 |
| london_low reclaim quality 0.47 < 0.5; no reclaimed sweep of an eligible low on this candl | 4 |
| no reclaimed sweep of an eligible low on this candle; london_high reclaim quality 0.21 < 0 | 4 |
| asia_low reclaim quality 0.48 < 0.5; no reclaimed sweep of an eligible low on this candle; | 4 |
| no reclaimed sweep of an eligible low on this candle; asia_high reclaim quality 0.37 < 0.5 | 4 |

| coin | evaluations | setup reached | fired |
|---|---|---|---|
| BTC | 2881 | 97 | 0 |
| ETH | 2881 | 114 | 3 |

BTC: 2881 evaluations, 12 non-vetoed; vetoes: no_setup=2784, third_sweep=65, too_deep=53, event_30m=4; conviction hist (non-vetoed): 0.1:1, 0.2:1, 0.3:7, 0.4:3

ETH: 2881 evaluations, 23 non-vetoed; vetoes: no_setup=2767, third_sweep=64, too_deep=58, event_30m=4; conviction hist (non-vetoed): 0.1:1, 0.2:4, 0.3:11, 0.4:4, 0.5:1, 0.6:2

##### M2: 5762 evaluations, 0 non-vetoed
| veto | hits | rate |
|---|---|---|
| no_setup | 5762 | 100.0% |
| late_day | 600 | 10.4% |
| event_30m | 8 | 0.1% |

| bucket | raw_conviction | conviction |
|---|---|---|
| 0.0 | 0 | 0 |
| 0.1 | 0 | 0 |
| 0.2 | 0 | 0 |
| 0.3 | 0 | 0 |
| 0.4 | 0 | 0 |
| 0.5 | 0 | 0 |
| 0.6 | 0 | 0 |
| 0.7 | 0 | 0 |
| 0.8 | 0 | 0 |
| 0.9 | 0 | 0 |
| 1.0 | 0 | 0 |

non-vetoed with conviction >= 0.55: 0; fired=0; tiers={}

top waiting_for:
| waiting_for (truncated) | n |
|---|---|
| 4h trend range, no fresh 4h CHoCH up confirmed by 1h BOS (long); 4h trend range, no fresh  | 4817 |
| displacement at 2,120.1 left no 1h OB; 4h trend up, no fresh 4h CHoCH down confirmed by 1h | 72 |
| 1h BOS up at 2,439.0 was not a displacement candle; 4h trend up, no fresh 4h CHoCH down co | 72 |
| range day and the 1h break at 2,319.9 is not a 4h level; 4h trend up, no fresh 4h CHoCH do | 64 |
| range day and the 1h break at 72,501.0 is not a 4h level; 4h trend up, no fresh 4h CHoCH d | 60 |
| 1h BOS up at 75,816.0 was not a displacement candle; 4h trend up, no fresh 4h CHoCH down c | 56 |
| zone from the break at 2,450.0 already broken/filled; 4h trend up, no fresh 4h CHoCH down  | 56 |
| displacement at 64,760.0 left no 1h OB; 4h trend up, no fresh 4h CHoCH down confirmed by 1 | 52 |

| coin | evaluations | setup reached | fired |
|---|---|---|---|
| BTC | 2881 | 0 | 0 |
| ETH | 2881 | 0 | 0 |

BTC: 2881 evaluations, 0 non-vetoed; vetoes: no_setup=2881, late_day=300, event_30m=4; conviction hist (non-vetoed): -

ETH: 2881 evaluations, 0 non-vetoed; vetoes: no_setup=2881, late_day=300, event_30m=4; conviction hist (non-vetoed): -

##### M3: 5762 evaluations, 0 non-vetoed
| veto | hits | rate |
|---|---|---|
| no_setup | 5752 | 99.8% |
| cohort_adding_longs | 5257 | 91.2% |
| event_30m | 8 | 0.1% |
| squeeze_risk | 6 | 0.1% |

| bucket | raw_conviction | conviction |
|---|---|---|
| 0.0 | 0 | 0 |
| 0.1 | 0 | 0 |
| 0.2 | 0 | 0 |
| 0.3 | 0 | 0 |
| 0.4 | 0 | 0 |
| 0.5 | 0 | 0 |
| 0.6 | 0 | 0 |
| 0.7 | 0 | 0 |
| 0.8 | 0 | 0 |
| 0.9 | 0 | 0 |
| 1.0 | 0 | 0 |

non-vetoed with conviction >= 0.55: 0; fired=0; tiers={}

top waiting_for:
| waiting_for (truncated) | n |
|---|---|
| no swing-failure + retest at a pre-session high on this candle; price in the premium of th | 3299 |
| price in the discount of the 4h range (pct 0.39) — no failed-high short; no swing-failure  | 85 |
| price in the discount of the 4h range (pct 0.28) — no failed-high short; no swing-failure  | 77 |
| price in the discount of the 4h range (pct 0.30) — no failed-high short; no swing-failure  | 76 |
| price in the discount of the 4h range (pct 0.35) — no failed-high short; no swing-failure  | 73 |
| price in the discount of the 4h range (pct 0.26) — no failed-high short; no swing-failure  | 68 |
| price in the discount of the 4h range (pct 0.40) — no failed-high short; no swing-failure  | 68 |
| price in the discount of the 4h range (pct 0.27) — no failed-high short; no swing-failure  | 66 |

| coin | evaluations | setup reached | fired |
|---|---|---|---|
| BTC | 2881 | 3 | 0 |
| ETH | 2881 | 7 | 0 |

BTC: 2881 evaluations, 0 non-vetoed; vetoes: no_setup=2878, cohort_adding_longs=2615, event_30m=4, squeeze_risk=2; conviction hist (non-vetoed): -

ETH: 2881 evaluations, 0 non-vetoed; vetoes: no_setup=2874, cohort_adding_longs=2642, squeeze_risk=4, event_30m=4; conviction hist (non-vetoed): -

##### M4: 5762 evaluations, 0 non-vetoed
| veto | hits | rate |
|---|---|---|
| no_setup | 5762 | 100.0% |
| event_30m | 8 | 0.1% |

| bucket | raw_conviction | conviction |
|---|---|---|
| 0.0 | 0 | 0 |
| 0.1 | 0 | 0 |
| 0.2 | 0 | 0 |
| 0.3 | 0 | 0 |
| 0.4 | 0 | 0 |
| 0.5 | 0 | 0 |
| 0.6 | 0 | 0 |
| 0.7 | 0 | 0 |
| 0.8 | 0 | 0 |
| 0.9 | 0 | 0 |
| 1.0 | 0 | 0 |

non-vetoed with conviction >= 0.55: 0; fired=0; tiers={}

top waiting_for:
| waiting_for (truncated) | n |
|---|---|
| no 4h CHoCH down in the last 72h; no 4h CHoCH up in the last 72h | 5474 |
| no 4h CHoCH down in the last 72h; 4h trend down not extended (BOS 2, 3d move +0.70%) | 288 |

| coin | evaluations | setup reached | fired |
|---|---|---|---|
| BTC | 2881 | 0 | 0 |
| ETH | 2881 | 0 | 0 |

BTC: 2881 evaluations, 0 non-vetoed; vetoes: no_setup=2881, event_30m=4; conviction hist (non-vetoed): -

ETH: 2881 evaluations, 0 non-vetoed; vetoes: no_setup=2881, event_30m=4; conviction hist (non-vetoed): -

##### M5: 5762 evaluations, 5 non-vetoed
| veto | hits | rate |
|---|---|---|
| no_setup | 5725 | 99.4% |
| range_bad | 21 | 0.4% |
| too_deep | 20 | 0.3% |
| opened_outside | 12 | 0.2% |
| event_30m | 8 | 0.1% |

| bucket | raw_conviction | conviction |
|---|---|---|
| 0.0 | 0 | 0 |
| 0.1 | 0 | 0 |
| 0.2 | 3 | 1 |
| 0.3 | 1 | 3 |
| 0.4 | 1 | 0 |
| 0.5 | 0 | 1 |
| 0.6 | 0 | 0 |
| 0.7 | 0 | 0 |
| 0.8 | 0 | 0 |
| 0.9 | 0 | 0 |
| 1.0 | 0 | 0 |

non-vetoed with conviction >= 0.55: 1; fired=1; tiers={'half': 1}

top waiting_for:
| waiting_for (truncated) | n |
|---|---|
| outside the London 07–09 / New York 13–15 UTC windows | 4682 |
| waiting for the first closed candle of the window | 120 |
| vetoed: too_deep, range_bad | 11 |
| no reclaimed newyork raid of the Asia low 64,137.0 on this candle; newyork raid of Asia hi | 8 |
| no reclaimed newyork raid of the Asia low 1,893.5 on this candle; newyork raid of Asia hig | 8 |
| no reclaimed london raid of the Asia low 64,778.0 on this candle; no reclaimed london raid | 8 |
| no reclaimed london raid of the Asia low 1,911.3 on this candle; no reclaimed london raid  | 8 |
| no reclaimed newyork raid of the Asia low 1,911.3 on this candle; newyork raid of Asia hig | 8 |

| coin | evaluations | setup reached | fired |
|---|---|---|---|
| BTC | 2881 | 15 | 1 |
| ETH | 2881 | 22 | 0 |

BTC: 2881 evaluations, 2 non-vetoed; vetoes: no_setup=2866, range_bad=10, too_deep=7, opened_outside=4, event_30m=4; conviction hist (non-vetoed): 0.2:1, 0.5:1

ETH: 2881 evaluations, 3 non-vetoed; vetoes: no_setup=2859, too_deep=13, range_bad=11, opened_outside=8, event_30m=4; conviction hist (non-vetoed): 0.3:3

##### M6: 5762 evaluations, 124 non-vetoed
| veto | hits | rate |
|---|---|---|
| no_setup | 5312 | 92.2% |
| shallow_loss | 157 | 2.7% |
| too_late | 121 | 2.1% |
| stop_too_wide | 64 | 1.1% |
| daily_strong_down | 40 | 0.7% |
| event_30m | 8 | 0.1% |

| bucket | raw_conviction | conviction |
|---|---|---|
| 0.0 | 0 | 0 |
| 0.1 | 0 | 0 |
| 0.2 | 0 | 0 |
| 0.3 | 36 | 72 |
| 0.4 | 84 | 32 |
| 0.5 | 4 | 20 |
| 0.6 | 0 | 0 |
| 0.7 | 0 | 0 |
| 0.8 | 0 | 0 |
| 0.9 | 0 | 0 |
| 1.0 | 0 | 0 |

non-vetoed with conviction >= 0.55: 0; fired=0; tiers={}

top waiting_for:
| waiting_for (truncated) | n |
|---|---|
| price never lost the weekly open this week on 1h closes (long); 1h not closed below weekly | 1256 |
| 1h not closed above weekly open 64,868.0 for a long; 1h reclaim at 2026-08-10 12:59 is old | 588 |
| 1h not closed above weekly open 1,910.0 for a long; 1h reclaim at 2026-08-12 13:59 is olde | 392 |
| before Monday 12:00 UTC — reclaim window not open | 376 |
| 1h reclaim at 2026-08-04 09:59 is older than the 8h entry window; 1h not closed below week | 263 |
| 1h reclaim at 2026-08-05 16:59 is older than the 8h entry window; 1h not closed below week | 263 |
| 1h reclaim at 2026-09-03 10:59 is older than the 8h entry window; 1h not closed below week | 238 |
| 1h reclaim at 2026-09-03 12:59 is older than the 8h entry window; 1h not closed below week | 230 |

| coin | evaluations | setup reached | fired |
|---|---|---|---|
| BTC | 2881 | 129 | 0 |
| ETH | 2881 | 321 | 0 |

BTC: 2881 evaluations, 32 non-vetoed; vetoes: no_setup=2752, too_late=57, daily_strong_down=40, stop_too_wide=24, event_30m=4; conviction hist (non-vetoed): 0.3:32

ETH: 2881 evaluations, 92 non-vetoed; vetoes: no_setup=2560, shallow_loss=157, too_late=64, stop_too_wide=40, event_30m=4; conviction hist (non-vetoed): 0.3:40, 0.4:32, 0.5:20

### 8.4 Pass 2 takes — tiers and per-take outcomes

#### Part 8 takes

| model | coin | tier | takes |
|---|---|---|---|
| M1 | ETH | half | 3 |
| M5 | BTC | half | 1 |

| id | model | coin | dir | signal ts | entry | stop | tier | conv | fill | exit | exit_reason | R | pnl_net |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | M1 | ETH | long | 2026-08-10 13:00 | 1,909.60 | 1,903.68 | half | 0.64 | 2026-08-10 13:00 | 2026-08-10 13:35 | stop | -1.19 | -8.95 |
| 2 | M1 | ETH | long | 2026-08-11 15:45 | 1,864.70 | 1,851.68 | half | 0.57 | 2026-08-11 15:45 | 2026-08-11 18:00 | dead_trade | -0.12 | -0.87 |
| 3 | M1 | ETH | short | 2026-08-18 14:30 | 1,907.55 | 1,919.29 | half | 0.60 | 2026-08-18 14:30 | 2026-08-18 14:35 | stop | -1.10 | -8.24 |
| 4 | M5 | BTC | short | 2026-08-31 13:15 | 78,050.50 | 78,231.40 | half | 0.57 | 2026-08-31 13:15 | 2026-08-31 13:35 | stop | -1.26 | -8.76 |

| model | exit_reason | n |
|---|---|---|
| M1 | dead_trade | 1 |
| M1 | stop | 2 |
| M5 | stop | 1 |

### 8.4b Pass 4 — feed era, final tree, vetoes ON (per model and per coin)

/root/audit_stats.py:17: DeprecationWarning: datetime.datetime.utcfromtimestamp() is deprecated and scheduled for removal in a future version. Use timezone-aware objects to represent datetimes in UTC: datetime.datetime.fromtimestamp(timestamp, datetime.UTC).

#### Veto hit rates, conviction histograms (non-vetoed rows)

##### M1: 520 evaluations, 2 non-vetoed
| veto | hits | rate |
|---|---|---|
| no_setup | 510 | 98.1% |
| feed_missing_oi | 8 | 1.5% |
| feed_missing_taker | 8 | 1.5% |
| event_30m | 8 | 1.5% |
| third_sweep | 6 | 1.2% |
| too_deep | 3 | 0.6% |

| bucket | raw_conviction | conviction |
|---|---|---|
| 0.0 | 0 | 0 |
| 0.1 | 0 | 1 |
| 0.2 | 1 | 1 |
| 0.3 | 1 | 0 |
| 0.4 | 0 | 0 |
| 0.5 | 0 | 0 |
| 0.6 | 0 | 0 |
| 0.7 | 0 | 0 |
| 0.8 | 0 | 0 |
| 0.9 | 0 | 0 |
| 1.0 | 0 | 0 |

non-vetoed with conviction >= 0.55: 0; fired=0; tiers={}

top waiting_for:
| waiting_for (truncated) | n |
|---|---|
| no reclaimed sweep of an eligible low on this candle; no reclaimed sweep of an eligible hi | 477 |
| vetoed: third_sweep | 4 |
| sweep of london_high reclaimed but price is in the discount of the 4h range (pct 0.30) | 3 |
| no reclaimed sweep of an eligible low on this candle; asia_high reclaim quality 0.15 < 0.5 | 2 |
| sweep of asia_high reclaimed but price is in the discount of the 4h range (pct 0.29) | 2 |
| no reclaimed sweep of an eligible low on this candle; asia_high reclaim quality 0.39 < 0.5 | 2 |
| vetoed: too_deep | 2 |
| no reclaimed sweep of an eligible low on this candle; pwh reclaim quality 0.14 < 0.5; equa | 1 |

| coin | evaluations | setup reached | fired |
|---|---|---|---|
| BTC | 260 | 3 | 0 |
| ETH | 260 | 7 | 0 |

BTC: 260 evaluations, 1 non-vetoed; vetoes: no_setup=257, feed_missing_oi=4, feed_missing_taker=4, event_30m=4, third_sweep=2, too_deep=1; conviction hist (non-vetoed): 0.1:1

ETH: 260 evaluations, 1 non-vetoed; vetoes: no_setup=253, feed_missing_oi=4, feed_missing_taker=4, third_sweep=4, event_30m=4, too_deep=2; conviction hist (non-vetoed): 0.2:1

##### M2: 520 evaluations, 0 non-vetoed
| veto | hits | rate |
|---|---|---|
| no_setup | 520 | 100.0% |
| late_day | 72 | 13.8% |
| feed_missing_oi | 8 | 1.5% |
| feed_missing_taker | 8 | 1.5% |
| event_30m | 8 | 1.5% |

| bucket | raw_conviction | conviction |
|---|---|---|
| 0.0 | 0 | 0 |
| 0.1 | 0 | 0 |
| 0.2 | 0 | 0 |
| 0.3 | 0 | 0 |
| 0.4 | 0 | 0 |
| 0.5 | 0 | 0 |
| 0.6 | 0 | 0 |
| 0.7 | 0 | 0 |
| 0.8 | 0 | 0 |
| 0.9 | 0 | 0 |
| 1.0 | 0 | 0 |

non-vetoed with conviction >= 0.55: 0; fired=0; tiers={}

top waiting_for:
| waiting_for (truncated) | n |
|---|---|
| 4h trend range, no fresh 4h CHoCH up confirmed by 1h BOS (long); 4h trend range, no fresh  | 414 |
| no 1h BOS up in the last 24h; 4h trend up, no fresh 4h CHoCH down confirmed by 1h BOS (sho | 32 |
| zone from the break at 2,524.7 already broken/filled; 4h trend up, no fresh 4h CHoCH down  | 20 |
| zone from the break at 2,484.7 already broken/filled; 4h trend up, no fresh 4h CHoCH down  | 16 |
| break buy ratio 0.54 not ≥ 0.65; 4h trend up, no fresh 4h CHoCH down confirmed by 1h BOS ( | 14 |
| retrace 38% of the way to the zone — waiting (needs ≥50%); 4h trend up, no fresh 4h CHoCH  | 12 |
| displacement at 2,461.3 left no 1h FVG; 4h trend up, no fresh 4h CHoCH down confirmed by 1 | 4 |
| range day and the 1h break at 2,461.3 is not a 4h level; 4h trend up, no fresh 4h CHoCH do | 4 |

| coin | evaluations | setup reached | fired |
|---|---|---|---|
| BTC | 260 | 0 | 0 |
| ETH | 260 | 0 | 0 |

BTC: 260 evaluations, 0 non-vetoed; vetoes: no_setup=260, late_day=36, feed_missing_oi=4, feed_missing_taker=4, event_30m=4; conviction hist (non-vetoed): -

ETH: 260 evaluations, 0 non-vetoed; vetoes: no_setup=260, late_day=36, feed_missing_oi=4, feed_missing_taker=4, event_30m=4; conviction hist (non-vetoed): -

##### M3: 520 evaluations, 0 non-vetoed
| veto | hits | rate |
|---|---|---|
| no_setup | 520 | 100.0% |
| cohort_adding_longs | 25 | 4.8% |
| feed_missing_oi | 8 | 1.5% |
| feed_missing_taker | 8 | 1.5% |
| event_30m | 8 | 1.5% |

| bucket | raw_conviction | conviction |
|---|---|---|
| 0.0 | 0 | 0 |
| 0.1 | 0 | 0 |
| 0.2 | 0 | 0 |
| 0.3 | 0 | 0 |
| 0.4 | 0 | 0 |
| 0.5 | 0 | 0 |
| 0.6 | 0 | 0 |
| 0.7 | 0 | 0 |
| 0.8 | 0 | 0 |
| 0.9 | 0 | 0 |
| 1.0 | 0 | 0 |

non-vetoed with conviction >= 0.55: 0; fired=0; tiers={}

top waiting_for:
| waiting_for (truncated) | n |
|---|---|
| no swing-failure + retest at a pre-session high on this candle; price in the premium of th | 267 |
| day type no_trade is not range (and price not inside a 4h supply zone) for a short; day ty | 105 |
| day type event is not range (and price not inside a 4h supply zone) for a short; day type  | 32 |
| price in the discount of the 4h range (pct 0.47) — no failed-high short; no swing-failure  | 9 |
| price in the discount of the 4h range (pct 0.28) — no failed-high short; no swing-failure  | 8 |
| price in the discount of the 4h range (pct 0.26) — no failed-high short; no swing-failure  | 7 |
| price in the discount of the 4h range (pct 0.27) — no failed-high short; no swing-failure  | 7 |
| price in the discount of the 4h range (pct 0.20) — no failed-high short; no swing-failure  | 7 |

| coin | evaluations | setup reached | fired |
|---|---|---|---|
| BTC | 260 | 0 | 0 |
| ETH | 260 | 0 | 0 |

BTC: 260 evaluations, 0 non-vetoed; vetoes: no_setup=260, feed_missing_oi=4, feed_missing_taker=4, event_30m=4; conviction hist (non-vetoed): -

ETH: 260 evaluations, 0 non-vetoed; vetoes: no_setup=260, cohort_adding_longs=25, feed_missing_oi=4, feed_missing_taker=4, event_30m=4; conviction hist (non-vetoed): -

##### M4: 520 evaluations, 0 non-vetoed
| veto | hits | rate |
|---|---|---|
| no_setup | 520 | 100.0% |
| feed_missing_oi | 8 | 1.5% |
| feed_missing_taker | 8 | 1.5% |
| event_30m | 8 | 1.5% |

| bucket | raw_conviction | conviction |
|---|---|---|
| 0.0 | 0 | 0 |
| 0.1 | 0 | 0 |
| 0.2 | 0 | 0 |
| 0.3 | 0 | 0 |
| 0.4 | 0 | 0 |
| 0.5 | 0 | 0 |
| 0.6 | 0 | 0 |
| 0.7 | 0 | 0 |
| 0.8 | 0 | 0 |
| 0.9 | 0 | 0 |
| 1.0 | 0 | 0 |

non-vetoed with conviction >= 0.55: 0; fired=0; tiers={}

top waiting_for:
| waiting_for (truncated) | n |
|---|---|
| no 4h CHoCH down in the last 72h; no 4h CHoCH up in the last 72h | 520 |

| coin | evaluations | setup reached | fired |
|---|---|---|---|
| BTC | 260 | 0 | 0 |
| ETH | 260 | 0 | 0 |

BTC: 260 evaluations, 0 non-vetoed; vetoes: no_setup=260, feed_missing_oi=4, feed_missing_taker=4, event_30m=4; conviction hist (non-vetoed): -

ETH: 260 evaluations, 0 non-vetoed; vetoes: no_setup=260, feed_missing_oi=4, feed_missing_taker=4, event_30m=4; conviction hist (non-vetoed): -

##### M5: 520 evaluations, 1 non-vetoed
| veto | hits | rate |
|---|---|---|
| no_setup | 518 | 99.6% |
| feed_missing_oi | 8 | 1.5% |
| feed_missing_taker | 8 | 1.5% |
| event_30m | 8 | 1.5% |
| too_deep | 1 | 0.2% |
| range_bad | 1 | 0.2% |

| bucket | raw_conviction | conviction |
|---|---|---|
| 0.0 | 0 | 0 |
| 0.1 | 0 | 0 |
| 0.2 | 1 | 1 |
| 0.3 | 0 | 0 |
| 0.4 | 0 | 0 |
| 0.5 | 0 | 0 |
| 0.6 | 0 | 0 |
| 0.7 | 0 | 0 |
| 0.8 | 0 | 0 |
| 0.9 | 0 | 0 |
| 1.0 | 0 | 0 |

non-vetoed with conviction >= 0.55: 0; fired=0; tiers={}

top waiting_for:
| waiting_for (truncated) | n |
|---|---|
| outside the London 07–09 / New York 13–15 UTC windows | 434 |
| waiting for the first closed candle of the window | 8 |
| no reclaimed newyork raid of the Asia low 76,951.0 on this candle; newyork raid of Asia hi | 7 |
| no reclaimed newyork raid of the Asia low 2,369.2 on this candle; newyork raid of Asia hig | 7 |
| no reclaimed london raid of the Asia low 80,528.0 on this candle; no reclaimed london raid | 7 |
| no reclaimed london raid of the Asia low 2,497.4 on this candle; no reclaimed london raid  | 7 |
| no reclaimed newyork raid of the Asia low 2,444.1 on this candle; newyork raid of Asia hig | 7 |
| no reclaimed london raid of the Asia low 79,413.0 on this candle; no reclaimed london raid | 6 |

| coin | evaluations | setup reached | fired |
|---|---|---|---|
| BTC | 260 | 2 | 0 |
| ETH | 260 | 0 | 0 |

BTC: 260 evaluations, 1 non-vetoed; vetoes: no_setup=258, feed_missing_oi=4, feed_missing_taker=4, event_30m=4, too_deep=1, range_bad=1; conviction hist (non-vetoed): 0.2:1

ETH: 260 evaluations, 0 non-vetoed; vetoes: no_setup=260, feed_missing_oi=4, feed_missing_taker=4, event_30m=4; conviction hist (non-vetoed): -

##### M6: 520 evaluations, 0 non-vetoed
| veto | hits | rate |
|---|---|---|
| no_setup | 520 | 100.0% |
| feed_missing_oi | 8 | 1.5% |
| feed_missing_taker | 8 | 1.5% |
| event_30m | 8 | 1.5% |

| bucket | raw_conviction | conviction |
|---|---|---|
| 0.0 | 0 | 0 |
| 0.1 | 0 | 0 |
| 0.2 | 0 | 0 |
| 0.3 | 0 | 0 |
| 0.4 | 0 | 0 |
| 0.5 | 0 | 0 |
| 0.6 | 0 | 0 |
| 0.7 | 0 | 0 |
| 0.8 | 0 | 0 |
| 0.9 | 0 | 0 |
| 1.0 | 0 | 0 |

non-vetoed with conviction >= 0.55: 0; fired=0; tiers={}

top waiting_for:
| waiting_for (truncated) | n |
|---|---|
| 1h reclaim at 2026-09-03 10:59 is older than the 8h entry window; 1h not closed below week | 238 |
| 1h reclaim at 2026-09-03 12:59 is older than the 8h entry window; 1h not closed below week | 230 |
| no 15m close beyond the weekly open before the 1h close; 1h not closed below weekly open 2 | 30 |
| cohort net-long change 24h -0.22 against the long; 1h not closed below weekly open 77,660. | 22 |

| coin | evaluations | setup reached | fired |
|---|---|---|---|
| BTC | 260 | 0 | 0 |
| ETH | 260 | 0 | 0 |

BTC: 260 evaluations, 0 non-vetoed; vetoes: no_setup=260, feed_missing_oi=4, feed_missing_taker=4, event_30m=4; conviction hist (non-vetoed): -

ETH: 260 evaluations, 0 non-vetoed; vetoes: no_setup=260, feed_missing_oi=4, feed_missing_taker=4, event_30m=4; conviction hist (non-vetoed): -

### 8.5 Feed-era replay — pass 3 (D-62 tree) and pass 4 (D-62 + D-63 tree), vetoes ON

Window 2026-09-03 13:30 → 2026-09-06 06:15 UTC (260 boundaries × 2 coins = 520 evaluations per model, 3 120 rows), the
only stretch where every required feed exists. Same harness, `REPLAY_DAYS=2.7`, feed vetoes ON (they fire on the 4
boundaries before 14:17 on 09-03 — 8 rows per model, 1.5 %, see Part 5). The tables below are pass 4; pass 3 differs only
in M5 `reclaim` (0.000 on both setup rows in pass 3 → 0.934 / 0.337 in pass 4, D-63) and the resulting M5 raw
(0.2 bucket → 0.252 / 0.416).

| model | evaluations | setups | non-vetoed | vetoes (besides no_setup) | conviction (non-vetoed) | ≥ 0.55 | takes |
|---|---|---|---|---|---|---|---|
| M1 | 520 | 10 (BTC 3, ETH 7) | 2 | third_sweep 6, too_deep 3, event_30m 8 (incl. no-setup rows), feed_missing 8 | 0.157, 0.2 | 0 | 0 |
| M2 | 520 | 0 | 0 | late_day 72, event_30m 8, feed_missing 8 | — | 0 | 0 |
| M3 | 520 | 0 | 0 | cohort_adding_longs 25 (ETH), event_30m 8, feed_missing 8 | — | 0 | 0 |
| M4 | 520 | 0 | 0 | event_30m 8, feed_missing 8 | — | 0 | 0 |
| M5 | 520 | 2 (BTC) | 1 | too_deep 1, range_bad 1, event_30m 8, feed_missing 8 | 0.265 | 0 | 0 |
| M6 | 520 | 0 | 0 | event_30m 8, feed_missing 8 | — | 0 | 0 |

Per-model tables (reason stats, veto rates, buckets, waiting_for, per coin) are in Part 3 §3.5b / §3.6b.

### 8.6 Frequency vs the doc's expected order of magnitude (30 days, BTC + ETH together)

| model | expected takes / 30 d (doc) | pass 2 takes (fixed tree, no feed veto) | pass 1 (shipped tree, feed vetoes) | ratio to range | > 5× outside? | responsible precondition / detector |
|---|---|---|---|---|---|---|
| M1 | 20 – 60 | 3 (ETH 3, BTC 0) | 0 | 6.7× below | **yes** | F-1 (feed coverage) + F-5 (fuel normaliser); 97 BTC + 114 ETH setups reached, 35 non-vetoed, 3 ≥ 0.55 |
| M5 | 15 – 40 | 1 (BTC) | 0 | 15× below | **yes** | F-3 (`reclaim` constant 0 — code defect, fixed D-63) + F-1 + F-5; 37 setups, 5 non-vetoed, 1 ≥ 0.55 |
| M2 | 10 – 30 | 0 | 0 | ∞ | **yes** | F-6 (4h alignment: trend_4h "range" on 84 % of boundaries and no 4h CHoCH in 30 days — doc 10 §2.2/2.3 definitions, not a code defect) |
| M3 | 10 – 30 | 0 | 0 | ∞ | **yes** | F-7 (`cohort_adding_longs` on 91 % of evaluations = the single replay cohort net-long at +0.55 / +1.00; 10 setups reached, all vetoed) |
| M6 | 0 – 8 | 0 | 0 | in range | no | 450 setup rows over 4 Mondays, 124 non-vetoed, best conviction 0.5x; feed-based reasons 0 on every one (F-1) |
| M4 | 0 – 6 | 0 | 0 | in range | no | no 4h CHoCH in 30 days (F-6) |

Four models are more than 5× below their range. The causes, in order of weight:

**F-1 — feed coverage, not a code defect.** `strat_oi_1m` / `strat_trades_1m` / `strat_book_5s` / `strat_gauge` start on
2026-09-03 14:17 UTC (the feed collectors were introduced with the no-gates deploy). 27 of the 30 replay days therefore run
with candles + liquidations + cohort only. For M1 that zeroes `cleared` (1.2), `delta_flip` (1.5) and `absorption` (1.0) —
3.7 of 13.2 weight — and with `fuel` (1.5) structurally ~0 (F-5) the reachable raw is 8.0 / 13.2 = **0.61**, or 7.0 / 13.2 =
**0.53** when `cohort` also reads 0 (9 of the 10 feed-era M1 setups, all shorts under the current net-long cohort). The `half` tier needs 0.55 after
multipliers, so pre-feed M1 can only fire on a row where location, reclaim, discount, session, htf_bias and cohort are all
near 1 and no multiplier is below 1.0 — which is exactly what the 3 pass-2 takes are (conviction 0.57–0.64, all ETH).
M5 loses `fuel` (1.3), `cleared` (1.0), `delta_flip` (1.3) = 3.6 of 12.9 the same way; M6 loses `oi_commitment` (2.0) and
`delta_reclaim` (1.5) = 3.5 of 13.8 on all four August Mondays. Consequence: the 30-day replay cannot show the doc's
frequency; only the feed era can, and it is 2.7 days long. Feed-era rate: M1 10 setups / 2.7 d (≈ 110 per 30 d), of which
80 % were removed by the doc's `third_sweep` / `too_deep` / `event_30m` vetoes and the rest sat below 0.55 on a scheduled-event
day (0.7 × 0.8 × 0.8 multipliers, §3.4b). No fix; the feeds must accumulate. Re-check after 30 feed days (~2026-10-03).

**F-2 — `vol_2h_ratio` was always None (code defect, fixed D-62).** `volume_2h_ratio` took the 8 closed 15m candles from
`[now − 120 min, now]`; the worker runs 5–19 s after the boundary, so the window contained 7 candles and the ratio was
None on every evaluation → `no_trade` unreachable, and the volume input to `event` / `squeeze` missing. Anchored on the last
closed boundary; day-type history now reads range / range / event / no_trade / range across the last 3 days (Part 4 §4.5).
Test `test_volume_2h_ratio_is_defined_a_few_seconds_after_the_boundary`.

**F-3 — M5 `reclaim` was a constant 0 (code defect, fixed D-63).** The reason row passed the bare `mb.reclaim_strength`
(signature `(setup)`) to the Mind, which calls detectors with the Snapshot; the call raised and `Mind.evaluate` read it as
strength 0 on every row (37 pass-2 rows, 2 feed-era rows). Weight 1.8 of 12.9 = 14 % of M5's conviction. Fixed to
`lambda s: mb.reclaim_strength(s.setup)` (as M1); pass 4 shows 0.934 / 0.337 on the two feed-era rows. The mirror test could
not catch it (0 == 0 on both sides), hence the new detector-fault test that runs every reason and veto detector of every
model on a valid setup.

**F-4 — `displacement_grade` is a constant 0.5 (doc formula, NOT changed, D-61).** Doc 10 §2.4 grades
`min(1, (range / 1.5 ATR) × (body / 0.6)) / 2` with the same thresholds that qualify the candle, so every qualifying
displacement scores ≥ 1 before the halving → 0.5. Part 4: 20 of 20 graded displacements = 0.50. M2 `displacement` (1.8) and
M4 `choch_quality` therefore cap at 0.5. Doc amendment left to the owner (a normaliser above the qualifying threshold, e.g.
3 ATR / 0.9 body, would grade; not the audit's call).

**F-5 — liquidation `fuel` normalisers are unreachable on this feed (doc numbers, NOT changed).** M1 `fuel` = same-side
liquidation notional over the sweep / 0.10 % of OI = $2.8 M (BTC) / $2.2 M (ETH); M5 uses 0.08 % ($2.2 M / $1.8 M); M3
`cluster_fuel` uses 0.10 %. `strat_liquidations` (`coverage = partial`) records per 5-minute window: BTC long max $1.75 M,
mean $139 k; ETH long max $948 k, mean $85 k; shorts larger (BTC max $26.9 M, mean $443 k). Feed-era M1 `fuel` max 0.043.
The reason is correctly scaled (USD / USD); the denominator is 10–20× the bursts this feed sees. Combined with F-1 it is the
reason M1 and M5 convictions cluster at 0.3–0.5.

**F-6 — M2 and M4 depend on 4h structure events that did not occur (doc definitions, NOT changed).** Doc 10 §2.2 labels a
trend only when the last 3 confirmed swing highs AND lows are monotonic; §2.3 emits a CHoCH only from a labelled trend
(in a range every break is a BOS). On the replay window the 4h trend read "range" on 4 817 of 5 762 M2 boundaries (84 %);
the 4h event list for both coins (Part 4 §4.2) is BOS up / BOS down alternating with **no CHoCH in 30 days**. Doc 12's
alignment is "trend_4h up, or a fresh 4h CHoCH confirmed by a 1h BOS within 8 h" → M2 waits on 84 % of boundaries; doc 14's
M4 starts from a 4h CHoCH → 0 setups. In the feed era M2 got further (1h BOS found; candidates then failed on documented
steps: "zone already broken/filled" 36, "break buy ratio 0.54 not ≥ 0.65" 14, "retrace 38 % (needs ≥ 50 %)" 12, "left no 1h
FVG" 4, "range day and the break is not a 4h level" 4). `trend.py` implements §2.2/2.3 verbatim (tests
`test_structure.py`); the question whether "3 ascending highs and lows" is too strict for 4h is a doc question.

**F-7 — M3 is blocked by `cohort_adding_longs` on 91 % of evaluations (replay artefact + market state, NOT a defect).** The
harness builds the cohort once (patch 1) and the current cohort is net-long (+0.55 BTC / +1.00 ETH, adding); doc 13's veto
fires whenever the cohort added longs over the prior 24 h, so every M3 short is vetoed for all 30 days. On prod the cohort is
rebuilt each cycle (feed era: 25 hits, ETH only). All 10 pass-2 M3 setups were vetoed by it. Not changed.

**F-8 — 3 of 4 pass-2 takes stopped out within 5–35 minutes (performance note, not a judgement).** Pre-feed takes fill on
the synthetic candle tape (harness patch 2: adverse extreme first), so a stop inside the fill candle always wins — the
−1.1 to −1.3 R outcomes are the conservative tape, and the sample is 4. No conclusion on edge is drawn from this replay.

### 8.7 Fixes applied by Part 8

| id | model | change | evidence |
|---|---|---|---|
| D-62 | structure (day type) | `volume_2h_ratio` window anchored on the last closed 15m boundary | day-type labels vary; `test_volume_2h_ratio_is_defined_a_few_seconds_after_the_boundary` |
| D-63 | M5 | `reclaim` detector wired through the setup | pass 4 reclaim 0.934 / 0.337; `test_every_reason_detector_runs_without_raising_on_a_valid_setup[M1…M6]` |

No threshold, weight, multiplier or veto definition was changed to move any model toward its expected range.

## Part 9 — Fixes applied, suite, deploy

### Fixes (all in DECISIONS.md)

| id | model | defect class | change |
|---|---|---|---|
| D-42 | M1 | undocumented veto | removed `bias_gate` (daily-bias veto) and `MAX_PER_DAY` cap |
| D-43 | M1 | missing wiring | `second_close_below` cancel wired (`cancel_level`) |
| D-44 | M2 | direction error / formula / undocumented gate | `delta_break`, `funding_young` mirrored; range multiplier 0.9; `htf_agree` 0.5 tier removed; range-day 4h-break precondition; `taken_zones` gate removed; OI ≥ −0.5 % step; entry validity = 15m close below OB |
| D-45 | M3 | formula / missing step | close-below-prior-high step; `failure_quality` from the prior high; funding-up sequence step + `*0.5` removed; failures counter per SFP; dead-session levels |
| D-46 | M4 | direction error / formula / wiring | `divergence` inverted → fixed; `bounce_displacement` = 1.5 ATR & 60 % body; T3 reachable; taker tape 24 h → 7 d |
| D-47 | M5 | formula / constant detector | `early_in_window` from the raid candle; `range_quality` wicks vs BODY range (was constant 0) |
| D-48 | M6 | formula / dead condition | reclaim window from Mon 12:00; `timing` on close weekday; weekly freeze condition simplified |
| D-50 | M1 | direction error (mirror test) | `delta_flip` mirrored |
| D-51 | all | veto not in doc | `feed_missing_book` removed |
| D-52 | M2 | formula | `late_day` symmetric around 00:00 UTC |
| D-53 | M2 | formula | alt-alignment BOS "within the last 8 h" now-relative |
| D-54 | M3 | formula | `second_failure` = exactly the second failure |
| D-55 | M4 | examined, kept | `already_reversed` current-price reading |
| D-56 | M6 | formula | excursion depth = the loss excursion, not week-to-date low |
| D-57 | M6 | formula | entry window anchored to the reclaim close |
| D-58 | M6 | missing wiring | prior-week multiplier reads the frozen weekly levels |
| D-59 | M4/M6 | missing wiring | `cohort_net_dir_entry` re-anchored at the paper fill |
| D-60 | M5 | formula | stop from the deepest raid wick; `cleared` over the whole raid |
| D-61 | structure (M2/M4 readers) | constant detector — NOT changed | `displacement_grade` = 0.5 on every displacement by the doc's own formula (Part 8 F-4); doc amendment left to the owner |
| D-62 | structure (day type) | missing input (`volume_2h_ratio` always None) | window anchored on the last closed 15m boundary; `no_trade` reachable, `event` / `squeeze` get their volume input |
| D-63 | M5 | constant detector (missing wiring) | `reclaim` detector called through the setup (`lambda s: mb.reclaim_strength(s.setup)`); was raising → read as 0 on every row |

### 9.2 Test suite

| suite | command | result |
|---|---|---|
| strategy engine (models, minds, structure, paper, runner, mirror, wiring) | `cd backend && python -m pytest tests/strategy_engine -q` | **252 passed** |
| full backend suite | `cd backend && python -m pytest tests -q` | 252 passed, **5 failed — all pre-existing** in `tests/test_telegram_queue.py` (async fixture setup, unrelated to this work; same 5 fail on `94ae37e` before the audit). Not touched. |

New test files: `tests/strategy_engine/test_models_mirror.py` (Part 2, 16 mirror cases + involution check) and
`tests/strategy_engine/test_models_audit_wiring.py` (Parts 6–7, D-52 / D-60 / D-62 regressions, detector-fault test for
D-63). The server venv carries no `pytest`; the local run above is the evidence.

### 9.3 Deploy log (UTC, 2026-09-06)

| step | detail |
|---|---|
| overlay | `tar` of the changed backend files (`strategy_engine/strategies/m1…m6`, `model_base.py` untouched, `mind/snapshot.py`, `model_runner.py`, `structure/alignment.py`, `structure/day_type.py`, tests) + `docs/models/` extracted over `/var/www/terminal` — no `rm`, no directory replaced |
| worker | `systemctl restart perpl-strategy-worker` at **07:50:45** — journal: `strategy worker up | assets=['BTC','ETH'] | engine_enabled=True`, `seeded: 8 strategies + 6 models, 0 new parameters`; no traceback since |
| API | **not restarted** — `perpl-terminal` does not import the model / structure / mind modules (it reads `strat_signals` / `strat_trades` rows written by the worker); nothing to reload, so the MM-slots gate was not needed and not exercised |
| deployed-code check | `grep -c "mb.reclaim_strength(s.setup)" …/m5_session_liquidity_run.py` = 1; `grep -c "end_ms = (now_ms // (15 \* MIN_MS))" …/structure/day_type.py` = 1 |

### 9.4 Post-deploy verification (prod)

**Fourteen rows, all paper** — `strat_strategy_state` (`requested_mode` / `effective_mode` / `effective_reason`), read at 07:53 UTC:

| strategy_id | requested | effective |
|---|---|---|
| s01_liq_sweep, s02_funding_flow, s03_whale_follow, s04_vol_compression, s05_session_open, s05c_session_open, s06_hull_fisher_ema, s06u_hull_fisher_ema | paper | paper |
| m1_sweep_reclaim, m2_bos_order_block, m3_failed_auction, m4_htf_choch, m5_session_liquidity_run, m6_weekly_open_reclaim | paper | paper |

`/api/strategies` returns the same 14 ids. `mode.py` has no live path for models (model `/backtest` = 404, doc 17 rule).

**M1–M6 logging evaluations after the restart** — `strat_signals` rows with `ts ≥ 07:50:45 UTC`, read at 08:01:47 UTC:

| model | rows | boundaries covered | feed_missing vetoes | setups | max conviction | day_type labels seen |
|---|---|---|---|---|---|---|
| M1 | 4 | 07:45 (catch-up on start, written 07:54:27) and 08:00 (written 08:00:16) × BTC, ETH | 0 | 0 | 0.073 | no_trade, range |
| M2 | 4 | same | 0 | 0 | 0.148 | no_trade, range |
| M3 | 4 | same | 0 | 0 | 0 | no_trade, range |
| M4 | 4 | same | 0 | 0 | 0 | no_trade, range |
| M5 | 4 | same | 0 | 0 | 0 | no_trade, range |
| M6 | 4 | same | 0 | 0 | 0 | no_trade, range |

The `no_trade` label appearing on prod is D-62 live (Sunday 07:00 freeze, RV percentile 0 / volume ratio now defined) —
before the fix the label could never be `no_trade`. `last_eval_ts` for all six model rows = 08:00:16 UTC. Since deploy
(22:45 UTC 09-05) the feed-missing vetoes have fired 0 times on prod (74 + 4 rows per model). Heartbeat after the 08:00
boundary: `hl_trades_1m 88 s`, `hl_oi_1m 28 s`, `hl_book_5s 6 s`, `funding 28 s` — all feeds fresh.

### 9.5 Scratch artefacts left on the server (not part of the app)

| artefact | what | keep / drop |
|---|---|---|
| database `perpl_replay` | copy of the prod strategy tables used by the 30-day replay (pass 4 rows are what it holds now) | left in place so the owner can re-query pass 4; drop with `DROP DATABASE perpl_replay` when done |
| `/root/audit_tree/backend` | the audited tree the harness imports (identical to the deployed code after the D-62 / D-63 upload) | drop when done |
| `/root/audit_part8.py`, `/root/audit_stats.py`, `/root/audit_part4.py`, `/root/audit_*.out` | replay harness (`REPLAY_DAYS`, `--no-feed-veto`), stats printer, structure dump, their outputs | keep for the ~2026-10-03 re-run (30 feed days) |

Nothing in `/var/www/terminal` depends on them.

### 9.6 Commit

`Audit M1-M6 against spec, fix conformance defects` on `master` — model files, `snapshot.py`, `model_runner.py`,
`alignment.py`, `day_type.py`, the four test files, `docs/models/10…17`, `DECISIONS.md`, this report,
`changelogs/strategies_changelog.md`. Strategies 01–06 have no diff.

### 9.7 Top 5 findings

1. **M1 carried an undocumented daily-bias veto** (`bias_gate`: shorts blocked when the daily bias was up, longs when down) on top of the doc's graded `htf_bias`; it removed 4 of the first 62 prod M1 evaluations and every counter-daily short — removed (D-42), `trend_against` is now the only bias-related veto as doc 11 defines.
2. **Three detectors were constants by wiring, not by market**: M4 `divergence` was inverted (D-46), M5 `range_quality` compared wicks against the wrong range and always read 0 (D-47), and M5 `reclaim` (weight 1.8) raised inside `Mind.evaluate` and was silently read as 0 on every row (D-63) — the Mind's swallow-and-zero design hid it from the mirror tests, so a detector-fault test now calls every detector directly.
3. **`vol_2h_ratio` was None on every evaluation** because the 2-hour window was anchored on wall-clock time 5–20 s after the boundary and lost its 8th candle (D-62); `no_trade` was unreachable and the volume input to `event`/`squeeze` missing — anchored on the last closed boundary, and `no_trade` appeared on prod at the first post-deploy freeze.
4. **Short-side asymmetries** existed in M1 `delta_flip` and M2 `delta_break` / `funding_young` (all read the long-side value for shorts) — found by the exact-mirror test and fixed (D-50, D-44); every other special-attention item (M2 `below_ob`, M3 `acceptance` / `thin_bids`, M4 `daily_strong_against`, M5 `trend_day_with_raid` / `open_location`, M6 `daily_strong_down`) is bit-exact on the mirror.
5. **The models fire far below the doc's expected frequency for reasons that are not code defects**: the OI / taker / book feeds exist only since 2026-09-03 14:17 UTC (27 of 30 replay days lose 3.5–3.7 of each Mind's weight), the liquidation `fuel` normaliser (0.10 % / 0.08 % of OI ≈ $2.2–2.8 M) is 10–20× the same-side bursts this partial-coverage feed records, doc 10's 4h trend / CHoCH definitions produced "range" on 84 % of boundaries and no 4h CHoCH in 30 days (M2 / M4 never align), and doc 10 §2.4's `displacement_grade` formula is a constant 0.5 (D-61) — all reported for the owner's decision, none changed; re-run the replay after ~30 feed days (~2026-10-03).
