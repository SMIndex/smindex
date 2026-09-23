# Design B rollout — safety wrapper (paste THIS FIRST, then the design spec below it as one message)

You are applying a complete visual redesign ("Design B") to a live trading terminal with real users and a real-money order path. The design spec follows this wrapper. **This wrapper overrides the spec's process section (§5) and adds hard rules the spec does not have.** Where they conflict, this wrapper wins.

## Rule 0 — Nothing functional changes. Prove it, don't claim it.
Presentation layer only. Every hook, store, API call, WebSocket subscription, handler, gate, signing path and data transformation stays byte-identical in behavior. Before writing any UI: produce `DESIGN_B_FUNCTIONAL_INVENTORY.md` — for each screen, every interactive element and what it does (handler → store/API). This inventory becomes the regression checklist (Rule 3). If a behavior is not in the inventory, it cannot be verified, so find it first.

## Rule 1 — Parallel shell behind a flag, never a big bang
- Implement Design B as a **second shell** selected by a flag: `?design=b` query param (persisted to localStorage) OR env default `UI_DESIGN=a|b`. Default stays `a` (current UI) until the owner flips it. Users see nothing until then.
- Screens migrate ONE AT A TIME. A screen not yet migrated renders the CURRENT UI even inside shell B (the B sidebar links to the old screen). No half-migrated screen is ever reachable.
- Do not delete, rename, or hide any existing component, route, or handler. "Hidden" nav entries in the spec = not rendered in shell B's sidebar; routes stay alive. List everything hidden at the end (spec §5 already asks for this).

## Rule 2 — Migration order is by RISK, not the spec's order
1. Tokens + shell (sidebar, theme toggle, cross-fade) — no screen content yet
2. Settings (lowest risk)
3. Analytics (read-only data, biggest visual change, no money)
4. Copy trade Discover + Trader profile (filters/watch/profile: no money until Copy button, which opens the EXISTING modal untouched)
5. Portfolio
6. **Terminal (Trade page) LAST** — this is the real-money surface. Chart, order book, order panel, calculator. Migrate it only after 1–5 are verified and approved by the owner.

## Rule 3 — Per-screen regression contract (must pass before the next screen starts)
For each migrated screen, run and paste evidence for every item in its inventory. Minimum required checks:
- **Settings**: each Telegram switch reads/writes the same preference key as before (show the request payload before/after); Link/Test buttons hit the same endpoints.
- **Analytics**: every Design B filter control maps 1:1 to the existing filter state (paste the mapping table: B label → existing param/value); Pulse row click and asset strip navigate to the same routes; every number on the B screen equals the A screen for the same asset+filters (side-by-side capture); honesty labels (freshness, low-sample, calibrating, track-record line, ≈ markers, resolution tags, mismatch notes) all present — the spec's "no coloured chips" rule does NOT license removing an honesty state; restyle it, never drop it.
- **Copy trade**: all sort/window/filter controls map 1:1 (table again); search, source switch, view switch preserve state; deep links `?exchange=`, `?wallets=`, `?asset=` still work; Watch toggles the same API; Profile opens the same data; **Copy opens the existing LiveCopyModal component unchanged** — the modal itself is NOT restyled in this pass (it is a real-money surface; restyle it in a separate later pass with the harness re-run).
- **Portfolio**: all figures identical to shell A for the same wallet; positions/orders/history tabs load the same data; Export PnL unchanged.
- **Terminal**: chart data pipeline, timeframes, indicators, signal events all from the existing sources; order book ws unchanged; order panel: Market/Limit, Long/Short, margin, leverage, SL/TP, review math (fill, size, value, fee, liq) recomputed and compared against shell A for 3 cases; calculator math identical; **the submit path calls the exact same function with the exact same payload** (log the payload in dev for both shells and diff); positions/orders/history tabs identical. Then the gate-ladder harness suite must pass unchanged, and the owner performs ONE ~$5 market order round-trip through shell B before B can become default.

## Rule 4 — Empty states honesty
The spec's "never show a dash" rule must not create false statements: "Not enough data" only when the data layer says so; "Loads from Hyperliquid" only for values that genuinely load lazily; a failed fetch renders "Not available right now", never a stale value styled as fresh.

## Rule 5 — Verification tooling
Playwright is available in the repo (used in earlier analytics passes): for every migrated screen capture A and B at 1440px and 390px, and run a scripted smoke (click every control in the inventory, assert the same network calls/store transitions). Paste results. No screen is "done" on a visual match alone.

## Rule 6 — Deploy and rollout
- Each migrated screen ships to prod behind the flag (dist overlay, no restart unless backend touched — it should not be). Owner reviews at `?design=b`.
- After all six screens pass Rule 3 and the owner's Terminal test order, flip the default to B in a single, separately revertable commit. Shell A stays in the codebase for one release as the instant rollback (`?design=a`).
- Report `DESIGN_B_REPORT.md`: inventory, per-screen mapping tables, side-by-side captures, smoke results, hidden-routes list, changelog, PENDING (owner's terminal test order), git log — one commit per screen.

## Rule 7 — What NOT to touch in this pass
LiveCopyModal / LiveCloseModal internals, gate ladder, signing, ws proxies, any backend file, the mm bots, the Telegram templates. If the design spec requires changing any of these to achieve a visual, STOP and report the conflict instead of changing it.

--- DESIGN SPEC FOLLOWS (spec §5 process is superseded by Rules 1–6 above) ---


# Perpl Terminal, apply the new design

The reference prototype is at `docs/design/perpl-terminal-design-b.html` (desktop AND mobile layouts are in this one file, resize the browser to see both) with PWA assets at `docs/design/pwa/`. Open it in a browser and read its CSS and markup before writing any code. Match it for layout, spacing, colour, type and component shape. Where this spec and the prototype differ, the prototype wins.

Apply a new visual design to Perpl Terminal. This is a presentation-layer change only. Every hook, API call, store, WebSocket subscription, route handler, business rule and data transformation stays exactly as it is. Do not rename, remove or reorder any data fields. Do not invent placeholder values. If a value is not available from the existing data layer, render the empty state described below.

## 1. Design tokens

Create one tokens file (CSS variables, or extend the Tailwind theme if the project uses Tailwind) and make every component use tokens. No hardcoded colours, sizes or radii anywhere in components.

Light theme (default)
- --bg #F4F5F7, --s1 #FFFFFF, --s2 #EDEFF3, --s3 #E2E5EB, --line #D8DCE4
- --text #121722, --muted #5B6473, --dim #8A93A3
- --accent #2447E6, --accent-soft #E4E9FC, --accent-text #1B38C4
- --long #0B8F5F, --long-soft #DDF3E9, --short #CF3548, --short-soft #FBE3E6
- --amber #B7791F, --amber-soft #FBEFD7

Dark theme (toggle in sidebar footer, persisted per user)
- --bg #0C1418, --s1 #12202A, --s2 #182A35, --s3 #1F3641, --line #274552
- --text #EAF2F5, --muted #9DB4BF, --dim #6E8791
- --accent #4F8CFF, --accent-soft #16304A, --accent-text #7FB0FF
- --long #2DD4A0, --long-soft #123D34, --short #FF6B7A, --short-soft #47202A
- --amber #F2B84B, --amber-soft #3C2E14

Type: IBM Plex Sans for everything, IBM Plex Mono only for wallet addresses and hashes. Enable tabular figures on all numbers (font-feature-settings "tnum"). Sizes used: 11.5, 12, 12.5, 13, 13.5, 14, 15, 17, 18, 20, 22, 24, 26, 30, 34, 40, 44px. Body is 14px.

Spacing: 4, 6, 8, 10, 12, 14, 16, 18, 20, 24, 28, 36px. Radius: 6px controls and tags, 8px buttons and inputs, 10px small cards and rows, 12px cards, 14px primary cards.

Colour rules: green means long or gain, red means short or loss, blue accent means primary action and selection, amber means warning. Nothing else gets colour. Never colour-wash a whole card.

Copy rules: sentence case everywhere, no all-caps labels, no monospace labels, no middle dots, no em-dashes in any UI text. Labels are plain language ("Net position" not "NOTIONAL", "Last active" not "LAST_ACTIVE").

Motion: one staggered fade-in per screen load (each block 50ms after the previous, 300ms each). Screen switches cross-fade in 200ms. No hover lift on cards. Respect prefers-reduced-motion by disabling all animation.

Accessibility: visible focus ring (2px accent, 2px offset) on every interactive element. WCAG AA contrast on all text. Responsive down to 390px.

## 2. App shell and navigation

Replace the current top bar and left markets sidebar with a 232px left sidebar on all screens (inside shell B only, per the wrapper's Rule 1):

- Top: product mark and "Perpl Terminal".
- Section "Trading": Copy trade, Terminal.
- Section "Markets": Analytics.
- Section "User": Portfolio, Settings.
- Footer: theme toggle button ("Switch to dark" / "Switch to light"), Connect wallet button.

Section labels are 12px, --dim, 600 weight. Nav items are 14px, 500 weight, 9px 10px padding, 8px radius. Active item uses --accent-soft background and --accent-text at 600 weight.

Hide every other existing navigation entry (Dashboard, Trade dropdown, More, Pulse, Who moved, the markets sidebar list, top-bar icons) from shell B's sidebar. Keep their routes alive, do not delete code.

Default landing screen after load (in shell B) is Copy trade.

Content area: 28px 36px padding, no max width, min-width 0.

## 3. Screens

### 3.1 Copy trade (route: existing copy trade route)

Header: "Copy trade", subtitle "Pick a trader, mirror their orders on Perpl, confirm each one yourself". Right side: amber tag showing live copy state ("Live copy off. No real orders placed." or "Live copy on").

Tabs (existing): Discover, Watchlist, Dashboard, Portfolio, History. Underline style, accent colour on the active tab.

Filter card (one card, two rows):
- Row 1: source segmented control (Perpl, Hyperliquid), search input, view segmented control (List, Cards).
- Row 2: "Sort by" segmented control (Top profit, Volume, Most active, Consistent, Win rate, Recently active), "Window" segmented control (All, 24h, 1W, 1M), "Only show" toggle chips (Active and profitable, Has open trades).
All of these are the existing filters with new labels. Keep the existing filter state and handlers.

Count line under the filters: "{total} traders on {source}, showing {n}".

List view is a table in a card: #, Trader (name, badges inline: HL tag for Hyperliquid, "7 of 7 days" accent tag, "~967 trades/day" flat tag; second line address and "active {relative time}"), Profit (green), Return (green, 12.5px), Volume, Open (count), Equity trend (110x26 sparkline, green if up, red if down), actions (Profile, Watch, Copy). Copy is the only primary button.

Card view is a grid: repeat(auto-fill, minmax(230px, 1fr)) with 10px gap, so four cards fit at 1440px, three on a laptop, two on a tablet, one on a phone. Card contents in order: rank medal (gold for 1, silver for 2, bronze for 3) with name and meta line "Active {time}, about {n} trades a day"; badge row (HL tag, days tag); profit at 22px with "Total profit" caption and return on the right; two facts (Volume, Open); either the open position chips ("BTC long 7x", green or red) or the full-width sparkline; three buttons in one row.

Profile button opens the trader profile screen (section 3.2). There is no other way to reach a profile.

### 3.2 Trader profile (route: existing profile route, opened from Profile buttons only)

Remove any nav entry for this screen. Sidebar keeps Copy trade highlighted while it is open. Add a ghost button "Back to copy trade" above the title.

Header: "Trader profile", subtitle "Rank {n} on {venue}" (for Hyperliquid add "Signals come from Hyperliquid, orders are placed on Perpl"). Right: for Hyperliquid a window segmented control (All, 24h, 1W, 1M), then Watch and Set up copy (primary).

Two-column layout, 320px left column:
- Left: identity card (medal, short address, full address in mono, tags: On leaderboard, Active 7 of 7 days, About 967 trades a day); Track record card as label and value rows (Total profit, Return, Volume traded, Volume last 24h or Open positions, Active this week, Trades per day, Last active, Consistency, 7 day profit trend, Win rate). Values that are not available show "Not enough data" or "Loads from Hyperliquid" in --dim. Hyperliquid profiles add a "Copy settings preview" card (Executes on Perpl, Markets mirrored: only those listed on Perpl, Confirmation: every order).
- Right: equity chart card (gridlines, dollar axis on the left, area fill in the line colour at 18% opacity, hover crosshair and a small tooltip with the cumulative value), then the open positions table (Market, Side chip, Size, Entry, Mark, Leverage, Opened, Profit, Copy). If positions are not loaded show the dashed empty block with the text from the prototype.

### 3.3 Terminal (the existing Trade page)

Market strip card: coin icon and "BTC / USD Perpetual", price at 22px, then 24h change (green or red), Mark, Oracle, 24h volume, Open interest, Funding (red if negative), each with a 12px caption underneath.

Three-column grid: chart column (1fr), order book (250px), order panel (300px). Below 1250px hide the order book column; below 900px stack everything.

Chart card:
- Tool row: timeframe segmented control (1m, 5m, 15m, 1h, 4h, 1d) and indicator toggles as small bordered chips with a colour swatch each: EMA 9 (blue #2447E6), EMA 21 (amber #B7791F), SMA 50 (grey, off by default), Trade signals, Volume, RSI 14 (violet #7C5CFF). Right side: one plain sentence summary from existing signal state, for example "Trend up, momentum flat, 16 signals today".
- OHLC row above the chart: Open, High, Low, Close, Change, EMA 9, EMA 21, updating on hover.
- Candles: green up, red down, 62% of slot width, price axis on the right with 11px labels, horizontal gridlines, time labels along the bottom, volume bars at 25% opacity in the bottom 52px when enabled, dashed accent line at the last price with a filled accent price tag on the right axis.
- Signals: replace the existing arrow and "XQ" markers with labelled pills ("Open long", "Open short", "Close long", "Close short") in green, red or --dim, connected to the candle with a 1px line. Only the existing signal events are drawn.
- RSI panel under the candles, 110px, with the 30 to 70 band shaded and a one-line caption "RSI 14, momentum. Above 70 overbought, below 30 oversold. Now {value}".
- Legend row under the chart in plain words: what green and red candles mean, what each average is, what each signal colour means.
- The chart is drawn at the element's real pixel width. Redraw on resize.

Order book card: tabs (Order book, Trades, Funding), header row (Price, Size, Total), asks in red with a depth bar at 14% opacity growing from the right, a mid row with the last price and "Spread {x} ({y}%)", bids in green.

Order panel card: tabs (Place order, Calculator). Segmented Market / Limit. Segmented Long / Short where the active side fills green or red. Margin input with USD suffix and quick buttons ($5, $10, $25, $50, $100). Leverage slider with 1x, 5x, 10x, 15x ticks and the current value shown in accent. Stop loss and take profit inputs side by side. Review block (Fill price, Size, Position value, Fee, Liquidation price with "{x}% away" in red). One full-width button that states the action: "Open long, $50 at 5x".

Bottom row: Positions card with tabs (Positions, Orders, Trade history, Order history) and the wallet empty state; Funding rates card as a small table (asset, bar, Perpl rate, Hyperliquid rate) with the note "Negative means shorts pay longs".

### 3.4 Analytics (route: existing pulse route as landing, existing analytics route per asset)

Clicking Analytics in the sidebar opens the pulse table. Clicking any row opens the analytics page for that asset. The asset strip at the top of the analytics page switches assets too. A "All assets" ghost link above the title returns to the pulse table.

Pulse table screen:
- Header "Analytics", subtitle "Every asset the cohort holds, ranked by Smart Money Index. Click an asset for the full breakdown." Right: segmented filter (All, Net long, Net short) and the "as of" stamp with a green dot.
- Summary line: "{n} assets shown of {total}", "{n} net long", "{n} net short", "{n} balanced", "Highest score {asset} {score}".
- One table in a card, sorted by score descending: #, Asset, Smart Money Index (number plus a 110x5px bar, accent when 65 or above, --muted otherwise), Lean (8px dot plus "Net long", "Net short" or "Balanced"), Net position, Open interest, Flow 24h ("Inflow" green or "Outflow" red). Rows are clickable, hover uses --s2. No coloured chips, no coloured tiles (honesty states such as calibrating and low sample are still shown, restyled per the wrapper's Rule 3).
- Footer line "Showing {n} of {total} assets" with a Show all button.

Per-asset analytics screen, in this order:
1. Title "Smart money on {asset}", subtitle with cohort size, badges: "{n} tracked traders" accent, "{n} market makers flagged", "{n} hedgers", "{n} unknown".
2. Asset strip: cards with the symbol and "{OI} open", selected card has accent border.
3. If the selected asset has no full sweep yet, an amber tag: "{asset} is net {side} {net} of {OI} open. The detail panels below refresh from the {asset} cohort sweep."
4. Filter card with a summary line ("Showing Top 400 wallets, dollar weighted, market makers and hedgers excluded, all sizes, win rates, leverage and ages") and a Hide filters / Show filters button. Panel is a 3-column grid of labelled groups, each a segmented control: Cohort size (Top 10, 50, 100, 400), Include (checkbox chips: Market makers with excluded count, Hedgers with excluded count), Weighting (By dollars, By wallet count, By quality), Minimum position size (Any, $10K+, $100K+, $1M+), Account size (Any, Under $10K, $10K to $100K, $100K to $1M, $1M to $10M, $10M+), Win rate (Any, 50%+, 60%+, 70%+, Consistent), Active in (Any, Last 7 days, Last 30 days), Leverage (Any, 0 to 2x, 2 to 5x, 5 to 10x, 10x+), Position age (Any, Under 24h, 1 to 7 days, Over 7 days, Unknown), Position result (Any, In profit, Underwater). These map one to one onto the existing filter state.
5. Hero row: left card with a "Net long" or "Net short" tag, a long/short ratio bar with a centre marker, the two dollar totals at 44px (green left, red right) with wallet counts under each, and a one-sentence verdict. Right: four KPI tiles (Smart Money Index, Wallets positioned, Unrealized longs, Unrealized shorts).
6. Two cards: "Where positions were opened" (mirrored bar chart, longs up in green, shorts down in red, dashed accent line at the current price with a "now ${price}" label, one sentence under it) and "Positioning over the last 48 hours" (line chart of long share with "{n}% long" gridline labels).
7. Three cards: "Longs against shorts" (mirrored bars per metric: Wallets, Net position, Average leverage, In profit, Unrealized), "Index breakdown" (score at 40px with a lean tag and five accent bars: Positioning skew, Flow direction, Breadth, Leverage appetite, Divergence, plus the observation count line), "How old is the conviction" (stacked bar and legend: Opened today, This week, Older than a week, Unknown age, then the resting TP/SL line).
8. Two cards: "Liquidation ladder" (price bands as rows with bars, shorts above the "now" divider in red, longs below in green, one sentence summary) and "Crowded entries" as a table (Side chip, Wallets, Entry band, Combined size).
9. "Recent {asset} moves" table: Wallet (mono), Action chip ("Added to long", "Trimmed short", "Opened short", "Closed long"), Detail (for example "35% of account as margin"), Size change, When. Show 20 rows and a "Show 20 more" button.

### 3.5 Portfolio (route: existing portfolio route)

Remove Deposit and Withdraw from shell B's view. Header: "Portfolio", subtitle with the short address and venue, right side: Live stamp and Export PnL button.

Row 1, three cards (280px, 1fr, 1.2fr):
- Account ring: 170px donut split into Available (accent), Margin in use (amber), Unrealized (green), value in the centre with "Account value" caption, legend under it, then the all-time change line in red or green.
- Risk right now: half dial from "Safe" to "Liquidation" with the needle at the current risk level and the level word in the centre ("Low", "Medium", "High") with a caption; then rows Leverage, Margin usage, 5% move against you, 10% move against you; then Stop loss cover and Take profit cover as five pips with "{covered} of {open}".
- Trading record: win rate ring plus tiles for Trades, Volume, Average trade (volume divided by trades), Net result, Per trade (net result divided by trades), and one plain sentence.

Row 2, two cards: "Daily result" (bars per day, green profit, red loss, flat grey marker on no-trade days, account value as a dotted accent line with dots, window segmented control 7 days, 14 days, 30 days, All) and "Trading calendar" (six-week grid, Mon to Sun headers, deposit days in accent-soft, losing days in short-soft, winning days in long-soft with darker green for bigger days, future days at 35% opacity).

Row 3: pill switcher (Positions, Open orders, Trade history, Copy history, Copy performance, Funding) and the content below. Positions render as cards, not a table. Each empty state states what will appear there.

### 3.6 Settings (route: existing settings route)

Only the Telegram section remains visible in shell B. Hide Link Perpl Account, One-Click Trading, MCP Access and anything else on the page, keep their code.

One card, max width 720px: Telegram icon tile, title "Telegram alerts", description "Get a message when a trader you follow opens or closes a trade. Reply to the message to place the same order on Perpl, you confirm each one.", Status row with a tag (Not linked or Linked as @handle), four switch rows (Trade opened by a followed trader, Trade closed by a followed trader, Stop loss or take profit hit on your copied trade, Daily summary at 09:00), buttons Link Telegram (primary) and Send test message, footnote "Linking opens Telegram with a one-time code. Alerts stop the moment you unlink." Wire the switches to the existing notification preferences; if a preference does not exist yet, hide that row.

## 4. Empty and loading states

Use the dashed empty block from the prototype (1px dashed --line, 10px radius, 22px padding, centred --muted text) for every empty state. Loading states use the same block with "Loading {thing}". Never show a dash, "N/A" or "NaN"; write "Not available", "Not enough data" or "Loads from {source}".

## 5. Process

Superseded by the wrapper's Rules 1 to 6: flagged parallel shell, risk-ordered migration (tokens and shell, Settings, Analytics, Copy trade and Trader profile, Portfolio, Terminal last), per-screen regression contract, Playwright captures at 1440px and 390px, per-screen deploy behind the flag, owner approval between screens, changelog of every file touched, and the final list of every route or component that is hidden but still in the codebase.

### 3.7 Mobile and PWA (same design, responsive layer)

The prototype (v9) already contains the mobile layer. Implement it exactly as its CSS describes:
- At or below 820px: the 232px sidebar is hidden; a sticky mobile top bar (`.mtop`: brand, right-side actions) replaces it, padded for `env(safe-area-inset-top)`; a fixed 5-tab bottom bar (`.mtab`: Copy trade, Terminal, Analytics, Portfolio, Settings) with `aria-selected` on the active tab, padded for `env(safe-area-inset-bottom)`; content padding leaves room for the bar. Breakpoints in the prototype (1250, 1150, 1100, 1000, 900, 820, 800, 760, 480) are the responsive contract, match them.
- Every screen from 3.1 to 3.6 must work at 390px with the same functionality as desktop, including the copy-trade filter card, the analytics filter panel, the order panel, and all tables (stack or horizontally scroll inside their card, never overflow the page).
- Deep links and the existing bottom-sheet filter pattern keep working on mobile.

PWA install layer (assets provided at `docs/design/pwa/`: manifest.webmanifest, sw.js, icons/):
- FIRST check whether the app already ships a manifest or service worker. If it does, replace or merge into the existing registration, never register a second service worker. Report what existed.
- Manifest: use the provided name, colours and icons, but rewrite `start_url`, `scope` and the three shortcut URLs to the REAL routes (copy trade discover, trade page, portfolio), not `?screen=`.
- Service worker: adapt the provided sw.js with these changes: navigation requests (index.html / routes) are NETWORK-FIRST with cache fallback, so a deploy is visible on the next load; hashed build assets are cache-first; `/api/`, websocket upgrades and third-party hosts are never cached (already in the file, keep it); bump the cache name on every build (inject the build hash) so stale shells are evicted. Do not add a PWA build plugin or any new dependency; hand-written sw.js is fine.
- The service worker must not intercept or delay the trading WebSocket proxies or any POST.
- Verify: Lighthouse PWA installability passes on prod; install on a real phone; confirm the bottom tab bar, safe areas, and that a deploy shows up on the next app open without a manual cache clear. Report the results.
