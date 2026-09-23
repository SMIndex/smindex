# Terminal Module Changelog

## 2026-07-27 — Real exchange-native SL/TP (Perpl trigger orders) — replaces dead inputs

**Files modified:** `frontend/src/lib/perplTrading.ts`, `frontend/src/components/terminal/OrderForm.tsx`, `frontend/src/components/terminal/PositionsPanel.tsx`

**Why:** User confirmed SL/TP inputs in Place Order did nothing (entry placed, no SL/TP). Cause: `placeSlTpOrder` was audit-disabled (old code sent t:5/t:6 = Cancel/IncreaseCollateral — dangerous) and its throw was silently caught; no backend tracking row was created either. Users appeared protected but weren't.

**What changed:**
- `placeSlTpOrder` implemented per spec: close-side order (t:3 long / t:4 short) + `tp` trigger price + `tpc` MARK-price condition (long: SL=4 LTEMark, TP=3 GTEMark; short mirrored) + `lb:0` (server-managed, st:8 Untriggered while armed). Exec price = trigger ± max-impact slippage, GTC after trigger. Lives on Perpl's servers — works with browser closed.
- OrderForm: market entries attach SL/TP right after the IOC fill (uses real filled size); failures shown in the form (never swallowed); limit entries get a clear "set from Positions after fill" message.
- PositionsPanel: per-position **SL/TP button** → inline editor → places triggers on existing positions; **Orders tab shows armed triggers** ("Active SL/TP on Perpl", from the trading-WS mt:23/24 store — server-side triggers are NOT on-chain, REST orders can't see them) with Cancel; 100% close auto-cancels leftover triggers on that market.

**⚠️ Needs first live test (small size, far triggers):** verify armed order appears under Orders → "Active SL/TP on Perpl" (st:8), fires correctly, and OCO behavior (whether the sibling trigger auto-cancels when position closes — `lp` linking not used yet; full-close via our UI cancels leftovers as a safety net). Deployed commit `cc2d764`, chunk `Trade-ifiswdqY.js`.

## 2026-07-27 — Orderbook fixed: live L2 WS stream replaces broken on-chain-walk cache

**Files modified:** `frontend/src/hooks/useMarketDataWs.ts` (rewritten), `frontend/src/components/terminal/OrderBook.tsx`, `frontend/src/pages/Trade.tsx`

**Why:** Orderbook panel showed garbage on all markets (bids ~$3k below mark, zero asks, 0.00% spread). Root cause verified on-chain: backend `/api/orderbook` walks the on-chain price-level list from price 0 with a 40-RPC-call cap; books now carry hundreds of dust levels below the spread, so the walk never reaches the real bid/ask (45 calls from 0 reached $62.6k vs spread $65.27k on BTC). Not stale — structurally the wrong slice. Clicking a book row also prefilled limit prices thousands off.

**What changed:** `useMarketDataWs` (previously dead code that also mis-parsed the stream) rewritten against the REAL Perpl market-data WS format, verified live: subscribe requires `{stream, subscribe:true}`; book messages are `mt:15` snapshot / `mt:16` incremental deltas with `bid`/`ask` arrays of `{p,s,o}` (NOT `b`/`a`); a level with `o:0`/`s:0` is removed; app-level ping `{mt:1,t}` every ~25s required or Perpl closes the connection ("idle timeout" even with protocol pings). Book kept as raw price→size maps (snapshot survives market-configs race), heartbeat sn gap or delta-before-snapshot triggers resubscribe for a fresh snapshot. Hook mounted ONCE in Trade.tsx (OrderBook renders twice — desktop+mobile). Panel now renders only the live store book; the broken REST cache is no longer displayed anywhere (still serves MCP/whales — that backend path remains broken and is a known open item).

**Verified end-to-end through prod proxy:** best bid/ask 65,251.2/65,252.8, 160 bid + 138 ask levels, deltas streaming. Also confirmed during diagnosis: funding 0.2000% everywhere is CORRECT (Perpl context `funding.rate=20` flat on all markets, fresh timestamp); Place Order fill/mark/liq come from `/api/markets` and were always live. Deployed commit `d4f409b`, bundle `index-BrOwjtZx.js`.

## 2026-07-27 (perf) — One-click order latency: pre-connect + parallel fetch + fresh-block fast path

**Files modified:** `frontend/src/lib/perplTrading.ts`, `frontend/src/hooks/useCopyTrade.ts`, `frontend/src/pages/Trade.tsx`

**Why:** One-click worked but the first order was several seconds slow — the whole connection chain (server-time sync → WS handshake → proxy → Perpl mt:29 auth → on-chain account-id fetch) ran sequentially at click time, and every order waited up to a full block for the next mt:100 heartbeat.

**What changed:** `warmupTradingConnection()` pre-connects in the background on Trade page load and trading-hook mount (uses API key or stored nonce only — never pops the wallet); account-id fetch runs concurrently with the WS connect; `getFreshBlock()` reuses a heartbeat block seen <1.2s ago (order `lb` is current+~6 blocks, so a ≤1.2s-old block is safe) with `trackSessionMessage` now keeping `lastBlock` fresh post-auth; a WS drop after successful auth silently re-warms after 1.5s (failed re-auth stops — no loop). Deployed commit `b73b64f`, bundle `index-D4WlmqbT.js`.

## 2026-07-27 (fix 2, ROOT CAUSE) — Stored API-key token was the whole ApiKeyInfo object → 3401 → popup

**Files modified:** `frontend/src/lib/perplApiKey.ts`, `frontend/src/components/settings/OneClickTradingCard.tsx`

**Why:** After fix 1 the key was found but sign-in still fell back to the wallet popup. Verified by direct WS probe: invalid api_key → Perpl closes `3401 unauthorized` → our client rejects → silent SIWE fallback. Root cause per api-docs types.md: `ApiKeyEnrollResponse = { api_key: ApiKeyInfo }` — the token is `ApiKeyInfo.api_key` (nested). We stored `response.api_key` (the whole object).

**What changed:** enrollApiKey unwraps the nested shape and validates the token is a non-empty string (throws loudly otherwise); getStoredApiKey auto-migrates broken stored records in place (token + secret were both present, so users' existing keys are repaired WITHOUT re-enrollment); Settings card gains a "Test connection" button that reports the exact mt:29 sign-in outcome (success + account id, or the precise failure). Deployed commit `0b57493`, bundle `index-S5NzL-88.js`.

## 2026-07-27 (fix) — Enrolled API key not used: wallet popup still appeared on first order

**Files modified:** `frontend/src/lib/perplTrading.ts`, `frontend/src/hooks/useCopyTrade.ts`, `frontend/src/components/terminal/PositionsPanel.tsx`, `frontend/src/components/copytrade/MyPositions.tsx`

**Why:** User enrolled a key (visible on Perpl) but placing an order still asked for a wallet signature. All connect paths called `tryAutoReconnect()` without an address; the API-key lookup keyed off legacy `perpl_address` (set only by the old SIWE flow), found nothing, and fell through to the SIWE popup.

**What changed:** Every caller now passes the connected wallet address; `tryAutoReconnect` additionally scans `perpl_apikey_*` localStorage entries as a last resort. Deployed commit `0ffebae`, bundle `index-C6sScnPT.js`.

## 2026-07-27 — One-click trading via Perpl Ed25519 API keys + order cancel/edit

**Files modified:**
- `frontend/src/lib/perplApiKey.ts` (NEW) — keypair gen (@noble/ed25519 v3), enrollment (EIP-712 wallet sig + Ed25519 proof-of-possession), localStorage per wallet, mt:29 sign-in frame, signed-REST canonical, list/revoke helpers, clock-skew sync
- `frontend/src/lib/perplTrading.ts` — tryAutoReconnect prefers API-key sign-in (mt:29) over stored SIWE nonce; shared openWsWithFirstFrame; rq counter bumped from server lfr (avoids sr:32); NEW cancelOrder (t:5 + oid), modifyOrder (t:7 + oid), open-orders store fed by mt:23/24
- `frontend/src/components/settings/OneClickTradingCard.tsx` (NEW) — Settings card: enroll (1 signature), paste-key import fallback, revoke (deletes key ON PERPL via /v1/api-key/delete, then clears local), remove-local-only
- `frontend/src/components/settings/EnableOneClickModal.tsx` (NEW) — one-time post-connect prompt on Trade page, per-wallet dismissable
- `frontend/src/pages/Settings.tsx`, `frontend/src/pages/Trade.tsx` — mount card/modal
- `frontend/src/components/terminal/PositionsPanel.tsx` — Orders tab rows get Cancel + inline Edit (price/size) buttons
- `backend/app/routers/auth.py` — NEW /auth/perpl-apikey-payload, /auth/perpl-apikey-enroll (JWT-gated, address forced to JWT wallet, proxied WITHOUT Origin header), /auth/perpl-apikey-proxy (relays browser-signed list/delete with cookie-auth fallback, target allowlist), /auth/server-time
- `backend/app/ws/trading_proxy.py` — accepts mt:29 first frame: JWT gate kept, forwards signed frame verbatim (minus token/address), no cookie injection
- `frontend/package.json` — added @noble/ed25519 ^3.1.0

**Why:** Every trading session previously required a SIWE wallet signature whenever the stored Perpl nonce expired, and the terminal had NO way to cancel or edit resting orders. Perpl's official API (github.com/PerplFoundation/api-docs) supports Ed25519 API keys: enroll once with one wallet signature, then all trading (open/close/cancel/modify) is signed locally with the Ed25519 key — zero wallet popups until the key is revoked.

**Key facts verified empirically:**
- Enrollment endpoints reject non-whitelisted browser Origins (our origin → 400) BUT accept origin-less server-to-server calls (→ 200 typed_data) — hence backend proxying, no Perpl whitelisting needed.
- Undocumented key-management endpoints exist: GET /api/v1/api-key/list, POST /api/v1/api-key/delete (both 401 unauth; docs route revocation "through the web UI" — these are what it uses). Auth scheme (signed headers vs session cookie) unverified — proxy tries signed first, falls back to stored Perpl cookies. VERIFY ON FIRST LIVE REVOKE.
- EIP-712 struct: PerplRegisterApiKey{signer,statement,publicKey,scope,label,expiresAt,ipCidrs,origin,time}, domain perpl.xyz v1 chainId 0x8f.

**Still needs live verification with a funded/Perpl-approved wallet:** enroll step (pop_signature format), mt:29 sign-in through proxy, t:5 cancel, t:7 modify, revoke path. Legacy SIWE flow untouched as fallback — nothing changes for users until they enroll.

## 2026-07-01 — Fee calculator: authoritative Perpl fee model + fix PositionCalculator double-charge

**Files modified:**
- `frontend/src/lib/perplFees.ts` (NEW) — single source of truth for trader-facing fees
- `frontend/src/components/terminal/OrderForm.tsx` — use helper (behavior-identical, now auditable)
- `frontend/src/components/terminal/PositionCalculator.tsx` — remove close/exit fee (Perpl close = 0), fix label

**Why:** Fee shown in the terminal calculators needed to be proven against the real Perpl fee model, not guessed from old code/memory. Old memory claimed "50 bps taker / understates 10x" which was never verified and is wrong.

**Authoritative source found:** Perpl official docs `https://docs.perpl.xyz/exchange/fees.md`, Tier 1: Maker Open 5 bps, Taker Open 8.8 bps, Maker/Taker Close 0. Raw context `/api/v1/pub/context` gives `taker_fee=880`, `maker_fee=500` (unit = hundredths of a bps: raw/100 = bps). Backend `/api/market-configs` already applies raw/100, so frontend receives `takerFeeBps=8.8`/`makerFeeBps=5.0`.

**What changed:**
- OrderForm fee math was already numerically correct (8.8 bps taker market / 5 bps maker PostOnly limit); refactored to `getOrderFeeRate` + `calculateEstimatedFee` for clarity. Labels: taker / maker / estimate.
- PositionCalculator was charging an EXIT fee ("×2") — WRONG per Perpl (close = 0). Now entry-only taker fee; break-even corrected. Label shows "taker open, N bps · close 0".
- No backend change, no order execution/signing change. Frontend-only.

## 2026-07-01 (follow-up) — Fix Calculator tab showing ~$8 fee on ~$50 notional

**Root cause:** On the /trade **Calculator tab** (`PositionCalculator`), `totalFees = 2 × notional × bps/10000`. Fee ≈ $8.80 on $50 notional forces `takerFeeBps ≈ 880` — the frontend was receiving the **raw unscaled** context value (880) instead of 8.8 bps. Production `/api/market-configs` serves 8.8 correctly; the local/stale backend served the raw 880 (process predating commit `9c24e0f` which added `/100`, and the backend can't cleanly restart on this env due to a pydantic/MCP mismatch). Combined with the erroneous ×2 close fee, that produced the ~$8.

**Fix (`frontend/src/lib/perplFees.ts`):** added `normalizePerplFeeBps(value)` — anchored to the documented model (real Perpl fees are single-digit bps; on-chain max = 10% = 1000 bps; raw encoding is ×100 so raw values are ≥ 100). Values ≥ 100 are divided by 100 (880→8.8, 500→5.0); values below pass through (8.8→8.8). Wired into `getOrderFeeRate` (OrderForm) and directly in `PositionCalculator`. Now correct whether the backend serves 8.8 or a stale 880. Verified: $50 notional → $0.044 (taker) / $0.025 (maker); $250 → $0.22. Frontend-only; no backend/execution/signing change.

## 2026-07-01 (architecture) — Single dynamic fee source of truth; remove hardcoded close-fee fallback

**Why:** Ensure the fee always comes from the live Perpl config path (`/api/market-configs` ← Perpl `/api/v1/pub/context`), never a hardcoded constant as primary path, and dedupe fee math.

**What changed:**
- `PositionsPanel.tsx:137` recorded a **close** fee for the trade-history save using a hardcoded `|| 500` bps fallback. Per Perpl docs the close fee is **0**. Now `calculateEstimatedFee(notional, PERPL_CLOSE_FEE_BPS)` → records 0. Removed the hardcoded 500 and the now-unused local `mcfg`. Display/record-only — close **execution/signing** (`closePosition`) untouched.
- OrderForm, PositionCalculator, PositionsPanel now all read the **dynamic** `mcfg.takerFeeBps`/`makerFeeBps` (from `/api/market-configs`) and funnel through the single `perplFees` helper (`normalizePerplFeeBps` / `getOrderFeeRate` / `calculateEstimatedFee`). No fee math duplicated.
- The only 8.8/5.0/880/500 literals in `perplFees.ts` are in **doc comments** (allowed). No hardcoded fee value is used as a primary or fallback compute path in the /trade calculators.
- Out of scope (NOT touched): `paperStore.ts` default config `takerFeeBps: 500` + its close-fee `?? 500` (offline paper sandbox, `/paper`), and copy-trading `CopyModal.tsx`/`useCopyTrade.ts` (dynamic `mcfg.takerFeeBps`, user said don't touch copy trading).

---

## 2026-07-13 — Fix: sitewide 429s on watch/follow (rate limiter used shared 127.0.0.1 bucket behind nginx)

**Files modified**
- `backend/app/main.py`

**Why**
Adding traders to the watchlist threw `429 Too Many Requests` after a few clicks.
`RateLimitMiddleware` keyed buckets on `request.client.host`, which is always
`127.0.0.1` behind nginx (uvicorn runs without --proxy-headers). Result: ALL
users of the site shared ONE bucket of 10 writes/min — any user's POSTs
throttled everyone.

**What changed**
- Bucket key now prefers the nginx-set `X-Real-IP` header (overwritten by
  `proxy_set_header`, not client-spoofable), falling back to client.host.
- Limits raised now that they are genuinely per-client: reads 60→120/min,
  writes 10→30/min.

**Deploy**
- scp main.py → prod, restart perpl-terminal. Backup `/root/predeploy_ratelimit_20260713_111144.tar.gz`.
- Verified on prod: 30 rapid POSTs from one simulated IP pass (422 auth), 31st
  → 429; a different IP unaffected; startup clean, /api/markets 200.

## 2026-07-28 — Hull Suite: true filled band (canvas primitive) + VWAP source fix

**Files:** `frontend/src/lib/hullBandPrimitive.ts` (NEW), `frontend/src/lib/hullSuite.ts`, `frontend/src/components/terminal/TradingChart.tsx`

**Why:** Hull ribbon didn't match the TradingView reference. Root cause: lightweight-charts can't fill between two arbitrary line series, so the port drew MHULL as one thick semi-transparent line + a faint grey SHULL with no fill — reading as a single wavy line, not the bold green/red band.

**What changed:**
- `HullBandPrimitive` (lightweight-charts v5 `ISeriesPrimitive`) renders a TRUE filled band between MHULL and SHULL per trend run on canvas (`useBitmapCoordinateSpace`), trend-coloured, zOrder 'bottom' so candles read on top — matching Pine `fill(Fi1,Fi2)` + `plot(MHULL)` + `plot(SHULL)`. Attached to the candle series, re-attached on chart recreation, cleared when Hull is off / "Show as band" collapses top=bottom.
- Removed the old segmented-line band (`hull_mhull_seg_*`) and grey `hull_shull` line.
- VWAP toned to secondary (thinner/softer blue) so the band is the visual lead.
- `hullSuite.ts` vwap(): price source hlc3 → **close** (Pine `vwap(src)` uses the source = close). Affects both the plotted VWAP line and the VWAP entry filter — now matches TradingView.

**Scope:** chart overlay only, no trading/execution change. Deployed commit `5bd994d`, bundle `index-DwnBeXXF.js`.

## 2026-07-28 — Hull Suite: two selectable strategies + trade boxes + v5 marker fix

**Files:** `frontend/src/lib/hullSuite.ts`, `frontend/src/lib/hullBoxPrimitive.ts` (NEW), `frontend/src/components/terminal/TradingChart.tsx`, `frontend/src/components/terminal/HullSuiteSettings.tsx`

**Why:** User wanted the Hull Suite to (a) offer two strategies selectable from the strategy button and (b) draw entry signals + trade boxes on the chart like the LuxAlgo Hull Suite Strategy.

**What changed:**
- **Strategy dropdown** on the Hull Suite button (also in gear panel): `Hull Entry Suite` (existing filtered 7-condition + cooldown) vs `Hull Suite Strategy` (pure MHULL/SHULL crossover, LuxAlgo-style — flips on trend). Selection drives which signals produce arrows + boxes.
- **Trade boxes** via `HullBoxPrimitive` (v5 canvas primitive):
  - `backtest` mode (A): box from entry to the next opposite signal, green/red by outcome, P&L% label.
  - `target` mode (B, default): ATR-derived TP box (green) + SL box (red) with price labels; resolved when price touches target/stop or the opposite signal fires (stop-first if both in one bar). ATR length + target/stop multipliers configurable; capped to last `maxBoxes` (default 8).
  - Note: Hull defines no targets — the target/stop numbers are OUR ATR logic, clearly labelled.
- `hullSuite.ts`: added `crossoverSignals`, `atr()` (Wilder), `buildTradeBoxes()`, and the new settings fields (`strategyVariant`, `showTradeBoxes`, `boxMode`, `atrLen`, `atrTargetMult`, `atrStopMult`, `maxBoxes`).
- **FIX (latent bug):** BUY/SELL arrows never rendered — lightweight-charts v5 removed `series.setMarkers()`, so the old call threw and was swallowed. Hull markers now use the `createSeriesMarkers()` plugin. (Pattern-detection markers left on the old broken call — separate, unrequested feature; noted for follow-up.)

**Scope:** chart overlay only, no trading/execution change. Deployed commit `e6fbefa`, chunk `TradingChart-BdrjAA_r.js`.

## 2026-07-28 — Hull Suite Option A: clear entry/exit markers, subtle trade box

**Files:** `frontend/src/lib/hullSuite.ts`, `frontend/src/lib/hullBoxPrimitive.ts`, `frontend/src/components/terminal/TradingChart.tsx`

**Why:** User feedback — the ATR target/stop boxes were confusing and didn't convey where to enter/exit (they were invented levels, not the strategy's real exit, and big translucent boxes overlapped everything).

**What changed (Option A):** all drawing now derives from actual TRADES (entry signal → exit at the next opposite signal), capped to last `maxBoxes` (default 6):
- Entry arrow on the signal candle, tagged `LONG <price>` / `SHORT <price>`.
- Exit marker on the opposite-signal candle: `EXIT <price> +X.XX%` (green win / red loss).
- Subtle connecting box entry→exit; the latest (active) trade is bolder with an entry-guide.
- Default `boxMode` → `backtest` (this clean view); `maxBoxes` 8→6. ATR target/stop kept as `target` mode for the optional Option B risk overlay.
- Markers now via v5 `createSeriesMarkers` (arrows previously never rendered).

**Scope:** chart overlay only. Deployed commit `f06c0d0`, chunk `TradingChart-yaZxV1J8.js`.

## 2026-07-28 — Trade layout: collapsible panels + drop redundant market sidebar

**Files:** `frontend/src/pages/Trade.tsx`, `frontend/src/components/layout/Layout.tsx`

**Why:** Chart was squeezed between the 220px global market sidebar (left) and the Order Book (248px) + Place Order (336px) panels (right). Markets already exist in the Trade top-bar pills, so the sidebar was duplicated.

**What changed:**
- `Layout.tsx`: global `Sidebar` no longer rendered on `/trade` (kept on other routes; mobile already hid it). Reclaims 220px for the chart.
- `Trade.tsx`: two persisted header toggles (`trade-show-orderbook`, `trade-show-orderpanel`) hide/show the Order Book and Place Order panels; the chart is `flex-1` and expands to fill whatever's hidden. Toggles sit in the market-header right cluster (desktop), next to Price Alerts.

**Scope:** terminal layout only, no trading logic change. Deployed commit `72e16dc`, bundle `index-CYR89X41.js`.

## 2026-07-28 (fix) — Chart didn't grow into hidden-panel space

**File:** `frontend/src/components/terminal/TradingChart.tsx`

**Why:** After adding the order-book / place-order hide toggles, hiding a panel left empty space — the chart canvas didn't widen. The chart's ResizeObserver only tracked container HEIGHT; width was applied only on a window-resize event, so a flex reflow (panel hidden) never resized the canvas.

**What changed:** the ResizeObserver now also applies the observed width to the main + RSI + MACD charts. Deployed commit `3f8cb33`, bundle `index-Btg4CAXc.js`.

## 2026-07-28 — Trade top bar: market pills → searchable dropdown

**Files:** `frontend/src/components/terminal/MarketSelector.tsx` (NEW), `frontend/src/pages/Trade.tsx`

**Why:** The horizontal pill row (BTC/MON/ETH/SOL/HYPE/ZEC) ate header width and didn't scale. User wanted a dropdown.

**What changed:** `MarketSelector` — a compact selector button (dot + symbol + chevron) that opens a dropdown with search, per-market price / 24h% / volume, and star favorites (shares the old sidebar's `perpl-fav-markets` localStorage key, so stars carry over). Closes on outside-click / Esc. Replaces the pill row in the Trade market header. Deployed commit `54f6f0f`, bundle `index-DeVmiTKt.js`.

## 2026-07-28 (fix) — Hull TP/SL clutter: target/stop now only on the active trade

**Files:** `frontend/src/lib/hullSuite.ts`, `frontend/src/lib/hullBoxPrimitive.ts`

**Why:** In target mode every trade drew TP/SL, so on choppy timeframes long and short trades' targets/stops overlapped and it was impossible to tell which TP/SL belonged to which order.

**What changed:** every trade now shows only the subtle entry→exit box (LONG/SHORT + EXIT markers carry the labels); TP/SL levels render ONLY for the latest/active trade, labelled with direction (`TP LONG` / `SL LONG` + dashed `ENTRY` guide). At most one TP/SL pair on screen, unambiguously tied to the newest entry. Target-mode trades now also compute `pnlPct`/`win` so box color + exit-marker P&L match backtest mode. Deployed commit `b4c763e`, bundle `index-DWDYfHJc.js`.

## 2026-07-28 — Limit orders & SL/TP not reaching Perpl (flags + expiry block)

**File:** `frontend/src/lib/perplTrading.ts`

**Why:** User reported limit orders and stop-losses placed from the terminal never appeared on Perpl. Two order-construction bugs vs Perpl's own examples:
- **Limit** orders sent `fl:1` (PostOnly). Perpl's example uses `fl: price ? 0 : 4` → GTC (`fl:0`) for limit. PostOnly rejects any order that touches the book, so limits didn't land. Also `lb` was `current + order_ttl_blocks` (~20 → seconds), too short for a resting order.
- **SL/TP** triggers sent `lb:0`, which reads as already-expired (every accepted order needs `lb > currentBlock`) — triggers never landed.

**What changed:** limit → `fl:0` (GTC) with `lb = currentBlock + max(1200, ttl)`; SL/TP → `lb = currentBlock + 200000` (far-future so the untriggered order persists) via a `getFreshBlock()` before send. Rejections already surface (mt:3 code≥400 / mt:24 sr) and `[perpl-order]`/`[perpl-sl]` console logs show Perpl's exact response. ⚠️ Needs funded-wallet confirmation. Deployed commit `6c69d3f`, bundle `index-D-rUC5QU.js`.

## 2026-07-28 — Hull Suite: usable Entry Suite, crossover cooldown, per-trade TP/SL

**Files:** `frontend/src/lib/hullSuite.ts`, `frontend/src/lib/hullBoxPrimitive.ts`, `frontend/src/components/terminal/TradingChart.tsx`, `frontend/src/components/terminal/HullSuiteSettings.tsx`

**Why (review feedback):** Entry Suite produced ~no signals; crossover whipsawed; box type "did nothing"; user wanted per-trade target/stop for both strategies.

**What changed:**
- **Entry Suite loosened:** required all 7 filters incl. a simultaneous 20-bar AND 50-bar breakout → fired almost never. Now core = Hull + VWAP + ADX; Structure / S-R / Volume / HTF are optional confirmations, OFF by default. One-time `filtersV` migration in the settings loader so existing persisted settings get loosened too.
- **Crossover cooldown:** min bars between flips = `cooldownBars` (default 12) to cut whipsaws.
- **Per-trade TP/SL (both strategies):** `buildTradeBoxes` computes ATR target/stop for EVERY trade; the primitive draws each trade's green target zone + red stop zone with TP/SL lines **bounded to that trade's entry→exit span** (no cross-trade overlap), labelled with price. In target mode the trade exits at first of TP/SL touch or opposite signal. "Trade P&L" mode = solid win/loss box + markers. Boxes made more opaque/solid.
- Box type relabelled: "Target + Stop (per trade)" vs "Trade P&L".

**Scope:** chart overlay only. Deployed commit `066df93`, bundle `index-2AeFuG1j.js`.

## 2026-07-28 (fix) — Trade boxes overlapping the Hull band / grid showing through

**Files:** `frontend/src/lib/hullBoxPrimitive.ts`, `frontend/src/components/terminal/TradingChart.tsx`

**Why:** Trade boxes drew at zOrder 'top' — large translucent fills covered the Hull band + candles and the grid showed through them.

**What changed:** boxes moved to zOrder 'bottom' and attached BEFORE the band, so band + candles render on top and boxes never obscure the diagram. Band opacity bumped to 0.5–0.9 so the background grid doesn't show through the ribbon (candles still on top since band is behind them). Deployed commit `c8a9724`, bundle `index-Dn-yZTIS.js`.

## 2026-07-30

### Fix "Timeout waiting for block" + "last exec block too high" order errors
**Files**: `frontend/src/lib/perplTrading.ts`
**Why**: Orders frequently failed with "Timeout waiting for block"; limit and SL/TP orders rejected with "last exec block too high".
**What changed**:
- Root cause 1: we never sent the app-level Ping (mt:1) Perpl docs require ~every 30s on the trading WS — after idle, Perpl stops sending mt:100 heartbeats while TCP stays open, so getFreshBlock timed out. Added a 25s mt:1 keepalive ping + 30s heartbeat watchdog (closes the half-dead socket so the existing onclose rewarm reconnects).
- getFreshBlock now recovers on timeout: closes the stale socket, re-auths via tryAutoReconnect, waits for the new session's first heartbeat instead of failing the order click.
- Root cause 2: docs cap lb at head_block + order_ttl_blocks. Limit orders sent +1200 and SL/TP triggers +200000 — both rejected. Limit lb now currentBlock + orderTtlBlocks (lb is only the forwarding deadline; resting comes from GTC flag). Trigger orders reverted to lb:0 per docs ("Trigger orders must set lb: 0"; the 6c69d3f far-future change was wrong). SL/TP no longer calls getFreshBlock at all.
- Added sr:20 InvalidExpiryBlock to the reject-reason map; pre-auth heartbeats now stamp lastBlockAt.

## 2026-07-31

### Wallet Insights page — win-rate ranked tracked wallets + per-wallet trade history
**Files**: `backend/app/db/copy_models.py` (WalletTradeInsight model), `backend/app/services/wallet_insights.py` (new), `backend/app/routers/insights.py` (new), `backend/app/main.py` (wiring), `frontend/src/pages/WalletInsights.tsx` (new), `frontend/src/App.tsx` (route /insights), `frontend/src/components/layout/Header.tsx` (More > Wallet Insights)
**Why**: user wants a page ranking tracked wallets by real win rate with per-trade drill-down (market, times, hold, size/amount, est PnL).
**What changed**:
- Service reconstructs every closed round-trip from leader_trade_events (size col = new TOTAL, price = avg-entry snapshot, detected_at = UTC — verified vs UTC_TIMESTAMP), prices exit legs from Perpl 5-min candles, writes wallet_trade_insights. Refresh on start +120s, every 6h, and via POST /api/insights/refresh.
- GET /api/insights/wallets (win-rate ordered, min_trades filter) + /api/insights/wallets/{w}/trades.
- Page: grid ordered by win rate with W/L, est PnL (gross), avg hold, markets, small-sample warnings, methodology disclaimer; View expands full trade table. Leverage shown as "—" (never recorded by tracker — honest).
- IMPORTANT correction: earlier ad-hoc analysis scripts converted naive timestamps via local machine tz — candle lookups were shifted (IST offset). Server implementation pins UTC; spot-checked SOL trade (candle 05:05 UTC close 73.987 vs est_pnl −2.09) — correct.

### Insights grid: green-PnL filter + sortable columns
**Files**: `frontend/src/pages/WalletInsights.tsx`
**Why**: user wants only profitable wallets shown by default, win rate visible on the grid, and per-column sorting.
**What changed**: "Green PnL only" toggle (default ON, filters est_pnl_total > 0); clickable sort headers (Trades, W/L, Win rate, Est PnL, Avg hold, Last trade) with asc/desc arrows; win-rate column labeled "Win rate (W/R)".

### Insights coverage 10x: wide-tier tracking (top 100 green wallets) + real leverage recording
**Files**: `backend/app/services/trader_tracker.py`, `backend/app/services/wallet_insights.py`, `backend/app/routers/insights.py`, `backend/app/db/copy_models.py`, `frontend/src/pages/WalletInsights.tsx`
**Why**: insights grid had only 7 green wallets — tracker only ever watched 41 wallets (top-10 + watched); no historical source exists for the rest (Perpl history API is own-account-only).
**What changed**:
- TraderTracker wide tier: top 100 leaderboard wallets with pnl>0 polled every 4th tick (~2 min); core set stays at 30s. First poll seeds baseline silently (existing guard) — no false opens. DB: ALTER wallet_trade_insights ADD leverage.
- Leverage now RECORDED at detection (notional / on-chain deposit) into event raw ({deposit, leverage}) for opened/increased/reduced/closed — verified live (3.0x/9.99x/5.0x). Insights service extracts it (JSON_EXTRACT) per span; API + page show real values; pre-2026-07-31 trades stay "—".

## 2026-08-31

### Analytics Tier-2: SMI validation + copy-modal context + cohort precision + wallet quality (Parts A–D, all deployed)
**Files**: `backend/app/main.py` (migrations v11/v12/v13, task wiring, registry boot-warm), `backend/app/services/analytics/smi_study.py` (new), `hedger.py` (new), `clustering.py` (new), `position_sweep.py` (price-history writes, hedger CORE exclusion, budget 450→430), `cohort.py` (hedger flags), `backend/app/services/hyperliquid/fill_stats.py` (PF/avg-win-loss/maxDD, quality sort gate, spot pass fold-in), `backend/app/routers/analytics.py` (track_record gate, context endpoint, admin study view, include_hedgers, entity dedup, quality weights, movers flip weighting, cohort.ensure fix), `backend/app/db/copy_models.py`; frontend: `lib/analyticsApi.ts`, `lib/analyticsCopy.ts` (TR0/1, CTX0-3, CP1, WD1, QS1, FA1 — 35/35 tests), `lib/copyApi.ts`, `pages/Analytics.tsx`, `pages/AnalyticsMovers.tsx`, `pages/copy/Discover.tsx`, `components/analytics/SmartMoneyContext.tsx` (new), `components/copy/LiveCopyModal.tsx` (+2 lines display only), `components/copy/TraderProfileModal.tsx`
**Why**: owner's "Analytics Next Tier" prompt — move from descriptive to evaluative/predictive while staying honest.
**What changed**:
- A: per-cycle venue-mark price history (zero new venue calls, backfilled from rollup flags), nightly forward-return study (4h/24h/72h, elapsed-only), SMI track record HARD-gated server-side (n≥30 & span≥21d, no override; prod: collecting 243 obs/4.7d), admin-only raw view.
- B: read-only /api/analytics/context/{asset}; "Smart money context" block in Copy Live Trade modal (never blocks — proven with endpoint aborted); asset page → Discover ?wallets= link.
- C: hedger classification (60/hr spot sampler; prod flagged 3 incl. rank #2 — HYPE shorts $404.7M→$252.2M once excluded), entity clustering (3 real 2-wallet clusters; 24-wallet transitive chain refused), quality weighting.
- D: PF (hand-checked exact)/avg win-loss/maxDD/flip accuracy (77.4% over 890 flips top case); QS1 "wr · PF · maxDD" everywhere win rate shows; Quality sort gate PF≥1 & trades≥20; movers FLIP weighting.
- Fixes: asset_detail missing cohort.ensure() (empty first request after boot), suspicious-empty cache guard, registry boot-warm.
- Budget: analytics total held at established 1,500/hr worst case (sweep cap shaved 450→430 to absorb the 60/hr spot sampler).
**Report**: ANALYTICS_TIER2_REPORT.md (per-part evidence, grep-proofs, budget table).

### B3 deep-link UX fixes: accent banner + exact-address wallet fetch
**Files**: `backend/app/routers/traders.py` (wallets address-list param), `frontend/src/pages/copy/Discover.tsx` (reactive params, banner, exact-address mode), `frontend/src/hooks/useCopyV1.ts`, `frontend/src/lib/copyApi.ts`, `frontend/src/pages/Analytics.tsx` (&asset= on link)
**Why**: owner review — filtered state too subtle (grey chip) and "6 on this board" dropped 24 of the 30 wallets the analytics link promised.
**What changed**: prominent accent banner "Showing N wallets that hold {ASSET} (from Analytics) · Show all traders"; filter reactive on search params so the Copy Trade nav always lands unfiltered; /api/traders?wallets=... returns exactly the requested addresses (cross-window stored-batch lookup, real rank badges — cohort rank for off-board wallets), no pagination/filter shrinkage. Verified dev+prod: 30/30 rows, Show all restores the board.

### Per-position build history on the HL profile modal
**Files**: `backend/app/services/hyperliquid/build_history.py` (new), `profile.py` (worker derives histories from its existing userFills fetch; deep/shallow split; two-way dating corrections), `backend/app/routers/traders.py` (GET /{wallet}/hl-history), `frontend/src/lib/copyApi.ts`, `frontend/src/components/copy/TraderProfileModal.tsx` (Avg entry column, chevron + inline drawer, action pills, built-over summary)
**Why**: owner feature — see how any position was built, fill by fill, with the running avg entry reproducing the venue's number.
**What changed**: streak replay (OPEN/ADD/REDUCE/PARTIAL CLOSE/FLIP, venue startPosition-anchored, entryPx semantics), zero extra venue calls, 1e-3 venue-reproduction check w/ history_mismatch logging (venue authoritative), honest truncation rows + ≈, dating re-dated when fills refute it. Verified on 3 real wallets incl. a 39-add live build and a May-2025 truncated BTC short; flagship 0xd475 BTC short found closed on-venue (reported, substituted).

### Dating defect fix for high-frequency wallets (hour-resolution dating + confidence labels)
**Files**: `backend/app/services/hyperliquid/profile.py` (_hourly_funding_scan, _day_price_range, re-verification of unconfirmable cached dates, confidence sources), `backend/app/routers/traders.py` (worker_inflight), `frontend/src/components/copy/OpenDuration.tsx` (openedDateLabel, ≈ markers, bound rendering), `frontend/src/components/copy/TraderProfileModal.tsx`, `frontend/src/lib/copyApi.ts`
**Why**: HF wallets showed confident stale open dates (symptom: BTC short 78,205.9 "Opened 12 Aug" on 0xecb63caa — true streak began Aug-30 07:00, proven by a funding-ledger sign flip).
**What changed**: hourly userFunding scan (szi sign flip / zero / guarded chain holes) replaces the daily binary search for recent streaks; sources fill/fund_hr/funding/bound rendered with ≈ + tooltips; dating_implausible price-range guard; every unconfirmable cached date re-verified once per process. Prod HF re-run: 62 wallets, **505 dates corrected** (184 were >1yr stale). Deployed.
