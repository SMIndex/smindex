# Setup

## Requirements

- Python 3.12
- Node 22
- MySQL 8

## Install

```
git clone https://github.com/SMIndex/smindex.git
cd smindex

cd backend
python -m venv venv
./venv/bin/pip install -r requirements.txt
cp .env.example .env

cd ../frontend
npm install
cp .env.example .env
```

## Environment keys

Backend (`backend/.env`). Every key is listed in `backend/.env.example`.

| Key | Purpose |
|---|---|
| `DATABASE_URL` | MySQL connection string (async driver) |
| `JWT_SECRET` | Signs the app session tokens |
| `JWT_EXPIRY_HOURS` | Session lifetime |
| `PERPL_REST_URL`, `PERPL_WS_URL` | Perpl API endpoints |
| `CORS_ORIGINS` | Allowed web origins |
| `COPY_LIVE_ENABLED`, `COPY_MODE` | Live copy switch and mode |
| `ADMIN_ADDRESSES` | Wallets with admin access |
| `PERPL_TERMINAL_URL` | Public site URL used in links |
| `TELEGRAM_BOT_TOKEN` | Optional. The bot is off when empty |

Frontend (`frontend/.env`). Every key is listed in `frontend/.env.example`.

| Key | Purpose |
|---|---|
| `VITE_COPY_LIVE_ENABLED`, `VITE_COPY_MODE` | Display flags. The backend decides what is allowed |
| `VITE_API_URL`, `VITE_WS_URL` | Optional API and WebSocket base. Default is same origin |

Never commit a `.env` file.

## Run locally

```
cd backend
./venv/bin/python -m uvicorn app.main:app --host 0.0.0.0 --port 8001

cd frontend
npx vite --port 5174
```

Tables are created on first start. Schema migrations run at startup.

## Tests

```
cd backend && python -m pytest tests/ -q
cd frontend && node tests/order_payload_check.mjs && node tests/one_at_a_time_check.mjs
```

## Deploy

Frontend:

1. Build on your own machine: `cd frontend && npx vite build`.
2. Copy `frontend/dist/assets/` to the server on top of the existing assets. Do not delete old files: browsers that still hold an older page need them.
3. Copy `frontend/dist/index.html` last.

No service restart is needed for the frontend.

Backend:

1. Back up the files you are about to replace.
2. Copy the changed files under `backend/app/` to the server.
3. Import the changed modules once before restarting, to catch a missing import.
4. Restart the API service and confirm `/api/markets` answers.

Server addresses, paths and credentials are kept outside this repository.
