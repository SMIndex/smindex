# SMINDEX: Auto-copy, Telegram alerts, Mobile pass
Design spec v1. Place at docs/design/SMINDEX_FEATURES_8-10_SPEC.md. This is the contract for the build prompts. Where this spec and existing money-path code disagree, the existing safety rules win and the conflict gets reported, not resolved silently.

---

# Part 1: Decisions I need to make before the build

These change the design. My recommendation is marked.

| # | Decision | Options | Recommended |
|---|---|---|---|
| D1 | Where auto-copy runs | (a) in the browser, only while a tab is open. (b) on the server, using the user's Perpl API key stored encrypted on the server | **(b)**. Auto-copy that stops when you close the tab is not auto-copy. Only if step 0 below proves the Perpl API key cannot withdraw funds. If it can withdraw, stop and choose again. |
| D2 | Who can use auto-copy | everyone / allowlist | **Allowlist** (existing LIVE_COPY_ALLOWLIST) until it has run cleanly for a while |
| D3 | Default mode for a new auto-copy | Shadow / Live | **Shadow**. Shows exactly what would have been copied, no orders. User switches to Live any time with one tap; no waiting period. |
| D4 | Which leaders can be auto-copied | any tracked wallet / only real-time wallets | **Only wallets on the real-time websocket tier** (10 slots per server, 9 used today). Polled wallets are 30 s to 20 min late; copying them late is copying a worse price. |

---

# Part 2: Auto-copy

## 2.1 What it is
The user picks a trader. When that trader opens, adds to, reduces or closes a position on Hyperliquid, SMINDEX places the matching order on Perpl for the user, within the user's own limits, without a click. Every decision (copied or skipped) is logged and can be sent to Telegram.

## 2.2 Step 0 (factual check before building anything)
Confirm from Perpl docs and code what a Perpl API key can do. It must be able to trade and cancel, and must NOT be able to withdraw or transfer funds. Write the answer with evidence in the report. If it can withdraw, auto-copy on the server is off the table (D1 falls back to browser mode) and the build stops for my decision.

## 2.3 The flow

```
Leader event on Hyperliquid (ws fill, real-time tier)
  -> Normalize: open / increase / reduce / close / flip, coin, size, price, leader leverage
  -> For each user subscribed to this leader in Shadow or Live:
       -> Map coin to a Perpl market (skip if Perpl does not list it)
       -> Size it (2.5)
       -> Run the copy gates (2.6), existing ladder first, then auto-copy gates
       -> Shadow: log "would have placed X", notify
       -> Live: place order on Perpl with the user's key, attach TP/SL if set
       -> Log result (filled / skipped with reason / failed with error)
       -> Notify (per the user's Telegram settings)
  -> Reconcile: venue-truth sweep compares Perpl position to what we think we hold
```

Idempotency: every leader event gets an id; one leader event can create at most one order per subscription, even after restarts or duplicate ws frames.

## 2.4 Which leader actions are mirrored

| Leader action | What we do | Default |
|---|---|---|
| Opens a position | Open ours, sized per 2.5 | On |
| Adds to it | Add proportionally, capped by the user's max position size | On |
| Reduces it | Reduce ours by the same fraction (leader cut 30%, we cut 30%) | On |
| Closes fully | Close ours fully, auto-cancel our TP/SL (Part C logic) | On, cannot be turned off |
| Flips (long to short) | Close ours. Open the new side only if it passes all gates as a fresh open | Close: on. Re-open: on |
| Leader position opened before the user subscribed | Ignore. Only copy positions opened after subscribing | Fixed rule |

## 2.5 Sizing

| Mode | How it works | Default |
|---|---|---|
| Fixed margin per trade | Every copied open uses $X margin | **Default, $10** |
| Proportional | Our size = leader size × (our allocation / leader account value) | Optional |

Leverage used = the lowest of: leader's leverage, the user's max leverage (default 5x), Perpl's max for that market.

Minimum: if the sized order is below Perpl's minimum order size, skip with reason "below venue minimum".

## 2.6 Gates (in order, every one logs its result)
1. Global kill switch and allowlist (existing)
2. Subscription is Live (Shadow stops here after logging)
3. Market is enabled for this subscription (default: all markets Perpl lists)
4. Slot rule: Perpl nets positions per market per account. If the user already has a position on this market (manual, or from another copied leader), **skip** with reason "market already in use". One source per market, no mixing.
5. Price drift: if Perpl's price is more than 0.5% worse than the leader's fill, skip ("price moved")
6. Basis guard between Hyperliquid and Perpl (existing, 30 bps)
7. Max open copied positions for this user (default 3)
8. Daily loss limit per subscription (default $30) and total loss limit (default $100): hit it and the subscription pauses itself
9. Available margin check
10. Leverage cap (existing per-market caps)
11. Staleness: if the leader event is older than 10 seconds when we process it, skip ("too late")

## 2.7 Protective TP/SL on copied trades
Leaders on Hyperliquid mostly don't use TP/SL, so there is nothing to copy. Our own protection:
- Stop loss: on by default, at a price move equal to **50% of the margin** (5x leverage means a 10% adverse price move). User adjustable per subscription.
- Take profit: off by default (we exit when the leader exits).
- Part C auto-cancel applies to all of these.

## 2.8 Auto-pause (the subscription stops itself and tells the user)
- Daily or total loss limit hit
- 3 failed orders in a row
- User's Perpl key rejected or expired
- Leader's wallet shows a liquidation
- Global kill switch
Paused subscriptions keep their open positions (they are not force-closed) and still close them when the leader closes. Only new opens stop. Resume is one tap.

## 2.9 Defaults table (new subscription)

| Setting | Default | Range |
|---|---|---|
| Mode | Shadow | Shadow / Live |
| Sizing | Fixed $10 margin | $5 to allocation |
| Allocation (max margin in use) | $50 | user set |
| Max leverage | 5x | 1x to market max |
| Max open copied positions | 3 | 1 to 10 |
| Mirror adds | On | on/off |
| Mirror reduces | On | on/off |
| Re-open on flip | On | on/off |
| Price drift limit | 0.5% | 0.1 to 2% |
| Stop loss | 50% of margin | off / 10 to 90% |
| Take profit | Off | off / % |
| Daily loss limit | $30 | user set |
| Total loss limit | $100 | user set |
| Markets | All listed on Perpl | pick list |

## 2.10 Recommended settings shown to the user (presets)
- **Careful**: $5 per trade, 3x max, max 2 positions, SL 30% of margin, daily limit $15
- **Standard** (default): the table above
- **Active**: $25 per trade, 5x, max 5 positions, SL 50%, daily limit $75
Presets fill the form; every field stays editable.

## 2.11 Views
- **Trader profile / Discover card**: "Auto-copy" button next to "Copy", disabled with a reason if the leader is not on the real-time tier or the user is not allowlisted.
- **Setup sheet**: preset picker, then the settings form with plain-language help under each field, Perpl key status, Shadow/Live toggle, and a summary line: "Copies opens with $10 at up to 5x, max 3 positions, stops for the day after -$30."
- **Copy dashboard, Auto-copy tab**: one card per subscription (leader, mode, status, P/L today and total, open copied positions, last action), plus a live log: every leader event and what we did with it, skips with the gate that stopped it. Shadow results shown the same way with a "shadow" tag, so users can judge a leader before going live.
- **Global panic button**: "Pause all auto-copy" on the dashboard.

## 2.12 Edge cases that must be handled
- Leader closes while our order is still in flight: close as soon as ours fills.
- Our order partially fills: track the real filled size, mirror later reduces against what we actually hold.
- User closes a copied position manually: that position detaches from the subscription; later leader actions on it are ignored.
- Perpl lists the coin under a different name: use the market registry, never guess.
- Server restart mid-flow: idempotency plus reconciliation on boot.
- User's margin runs low: skip with reason, don't partially size down silently.

---

# Part 3: Telegram alert settings

## 3.1 What exists
Linking, unlinking and a message queue with throttle and dedupe. There is no per-user preference store, which is why the Settings toggles were hidden.

## 3.2 Build
A preferences table per user (one row per alert type with on/off and its parameters), a settings screen, and every alert producer checks the user's preferences before queueing.

## 3.3 Alert types, defaults and parameters

| Group | Alert | Default | Parameter (default) |
|---|---|---|---|
| Copy trading | Copied trade opened / closed | On | |
| | Trade skipped (with reason) | Off | |
| | Order failed | On | |
| | Subscription auto-paused | On | |
| Positions | TP or SL triggered | On | |
| | Leftover TP/SL cancelled | On | |
| | Liquidation warning | On | when margin use passes 80% |
| Smart money | SMI crosses a level on an asset I watch | Off | above 70 / below 30 |
| | A watched wallet opens or closes | On | only positions over $50k |
| | Crowded trade warning on an asset I hold | Off | |
| Summary | Daily summary (P/L, copied trades, SMI moves on my assets) | On | 08:00 in my timezone |
| System | Test message button | | |

Global controls: quiet hours (default off; when on, only failures and liquidation warnings get through), max 20 messages per hour (overflow is batched into one digest message).

## 3.4 Where events come from
Copy and auto-copy events: server side, already known. SMI and watched-wallet alerts: server side from the sweep. Positions, TP/SL and liquidation warnings: server side from the existing chain reads of the user's account (the venue-truth sweep), so they work with no tab open. State in the report which alerts are real-time and which are delayed by the sweep interval, and show that delay in the settings help text.

## 3.5 View
Settings > Telegram: link status card (existing), then one section per group with a toggle per alert and its parameter inline, quiet hours, message limit, "Send test message". Every toggle saves immediately and is read by the producers on the next event (no restart).

---

# Part 4: Mobile pass

## 4.1 Goal
Every screen works on a phone the way it works on desktop: same data, same actions, nothing hidden. Money actions are harder to mis-tap, not easier. The PWA installs and behaves like an app.

## 4.2 Navigation
Bottom tab bar, 5 tabs: **Analytics · Copy · Trade · Explorer · Portfolio**. Settings moves to the top bar (avatar/menu icon), with wallet and theme. Strategies is removed from mobile navigation while the engine is shut down (route still works).

## 4.3 Screen by screen

| Screen | Mobile layout |
|---|---|
| Analytics pulse | Table becomes a list of rows (asset, SMI with bar, lean, net position); filters in a bottom sheet |
| Asset page | Sections stacked in the desktop order; charts full width; filter card collapsed by default |
| Copy discover | Trader cards, one per row; sort and filters in a bottom sheet |
| Trader profile | Full screen page, not a modal; tabs scroll horizontally |
| Copy dashboard / auto-copy | Subscription cards; log as a list; "Pause all" pinned at top |
| Trade | Price strip at top, chart (collapsible, default 45% height), below it tabs: Order / Book / Positions / Orders. Order form opens as a bottom sheet with big Long/Short buttons |
| Explorer | Stat cards 2 per row, chart full width, positions as cards, tabs scroll horizontally |
| Portfolio | Stacked cards |
| Settings | Single column |

## 4.4 Money action rules on mobile
- Every order, close and cancel goes through a confirmation sheet showing side, size, price, leverage and fees, with a clear primary button. No one-tap trading on mobile in v1.
- Buttons at least 44 px tall, Long and Short never adjacent without spacing.
- Order status visible in the sheet through submitted, confirmed, failed (the same lifecycle as desktop).
- Toasts sit above the tab bar, never covering the action button.

## 4.5 Wallets on mobile
Test and support: MetaMask mobile in-app browser, WalletConnect from Safari and Chrome to MetaMask mobile, the installed PWA. SIWE and the Perpl key flow must work in each. Report which combos work.

## 4.6 Technical rules
- Safe areas (notch, home bar) respected everywhere.
- No horizontal page scroll; wide tables scroll inside their card.
- Bundle: mobile first load stays within the current code-split budget; charts load lazily.
- Websockets reconnect cleanly when the phone locks and unlocks, with a "reconnecting" state shown.

## 4.7 Test matrix (evidence in the report)
iPhone Safari, iPhone installed PWA, Android Chrome, Android installed PWA, MetaMask in-app browser. For each: every screen screenshot at 390 px in both themes, one full order flow (open with TP/SL, close) on a small real order, and a lock/unlock reconnect check.

---

# Part 5: Build order
1. Telegram alert settings (smallest, and auto-copy needs its notifications)
2. Auto-copy step 0 check, then auto-copy in Shadow mode end to end
3. Auto-copy Live, tested by me with small real orders
4. Mobile pass (covers the new auto-copy and Telegram screens too)
