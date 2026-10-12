# SMINDEX

The smart money index for onchain perps.

Live at [smindex.xyz](https://smindex.xyz)

SMINDEX tracks the top wallets on Hyperliquid in real time, scores each asset with a Smart Money Index, and lets you explore any wallet or copy a trader on Perpl with your own confirmation on every order.

## Features

**Smart Money Analytics**
- Tracks the top 400 Hyperliquid wallets: positions, entries, leverage, liquidation prices and fills
- Smart Money Index (0 to 100) per asset, built from five components: positioning skew, flow direction, breadth, leverage appetite and funding divergence. Every score opens down to its raw inputs
- Market Pulse dashboard with SMI per asset, biggest movers, crowded trades and live flow
- Per asset pages with cohort positioning, entry distribution, liquidation clusters, position age and a flow tape of what the cohort is opening, closing and flipping
- Market makers and hedgers are detected and excluded so the index reads directional traders, not quote noise

**Wallet Explorer**
- Coinglass style page for any address on Hyperliquid and Perpl
- PnL history and equity curve, positions with build history, liquidation price and funding
- Fills, deposits and withdrawals, funding paid, order history, spot holdings
- Perpl data is read from the chain: account state, positions, resting TP/SL orders, and full event history indexed from the Exchange contract on Monad
- Every wallet page is public and shareable

**Copy Trading**
- Discover profitable traders, watch them, open full profiles with position build history
- Paper copy to test, live copy with your confirmation on every order
- Server side risk checks: basis guard between venues, per market leverage caps, loss limits, TP/SL sanity checks

**Terminal**
- Order entry on Perpl: chart with indicators, orderbook, positions, SL/TP
- Funding comparison between Perpl and Hyperliquid

**PWA**
- Installable on mobile, works as an app with the same features

## Architecture

```
frontend/            React 18, Vite, TypeScript, Tailwind
  src/design-b/      UI shell
  src/components/    terminal, copy, analytics, settings
  src/lib/           venue clients, API clients
  public/            PWA manifest and service worker

backend/             FastAPI, async SQLAlchemy, MySQL 8
  app/routers/       analytics, explorer, copy, auth, traders
  app/services/
    analytics/       position sweep, cohort, SMI, clustering
    hyperliquid/     shared HL client with request weight budgeting
  app/mcp/           MCP server for market data and order staging

```

Data sources: Hyperliquid REST and WebSocket for market data and wallet state, Monad mainnet RPC for Perpl onchain positions and order locks, Binance and Bybit for cross venue funding reference.

## Installation

Requirements: Python 3.12, Node 22, MySQL 8.

**Backend**

```
cd backend
python -m venv venv
./venv/bin/pip install -r requirements.txt
cp .env.example .env    # fill in DATABASE_URL, JWT_SECRET, API keys
./venv/bin/python -m uvicorn app.main:app --host 0.0.0.0 --port 8001
```

**Frontend**

```
cd frontend
npm install
cp .env.example .env
npx vite --port 5174
```

Tables are created on first boot. Schema migrations run at startup.


## Tests

```
cd backend && python -m pytest tests/ -q
cd frontend && node tests/order_payload_check.mjs && node tests/one_at_a_time_check.mjs
```

## Setup and deployment

See [docs/SETUP.md](docs/SETUP.md).

## Licence

All rights reserved. See [LICENSE](LICENSE).
