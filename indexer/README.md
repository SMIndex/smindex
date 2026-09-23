# Perpl on-chain event indexer (Wallet Explorer Part 4)

Envio **HyperSync** (chain 143, `https://monad.hypersync.xyz`) → Python worker
→ MySQL. Runtime decision, owner-approved 2026-09-08: a systemd worker
consuming HyperSync directly instead of a full HyperIndex Postgres+Node stack —
same Envio data engine, MySQL-native sink, same ops pattern as the other
workers on the box (the 8GB server carries no new runtime). **Built on Envio
HyperSync — implementation notes live in
`WALLET_EXPLORER_REPORT.md` Part 4.**

## What it indexes

Exchange contract `0x34b6552d57a35a1d042ccae1951bd1c370112a6f`, 23 event
types decoded with the REAL ABI (`backend/perpl_abi.json`): AccountCreated,
CollateralDeposit/Withdrawal, Order{Request,Placed,Cancelled,Changed},
TriggerOrderRequest, ImmediateOrCancelExecuted, Maker/TakerOrderFilled,
Position{Opened,Increased,Decreased,Closed,Liquidated,Inverted,Deleveraged,
Unwound,CollateralDecreased}, IncreasePositionCollateral,
AccountLiquidationCredit, FundingEventCompleted.

Tables (guarded DDL here AND migration v20 in `backend/app/main.py`):

* `perpl_events` — at/block/tx/log_index (UNIQUE tx+log_index)/account_id/
  attributed/address/tx_from/market_id/event_name/event_type/amount/
  balance_after/fee/bfa/raw JSON. CNS values are stored as USD (÷1e6);
  PNS/LNS stay raw inside `raw` (per-market decimals belong to the app's
  market registry — the API converts, the worker never guesses).
* `perpl_addr_map` — account_id ↔ address from AccountCreated (+first seen).
* `perpl_indexer_state` — last_block/head_block/first_event_block/note;
  `/health` reads it for the lag metric; the History tab reads it for
  "indexed from X · backfill N%".

Attribution (several events carry NO accountId in the ABI): (1) same-tx
`OrderRequest` context, else (2) tx sender when it maps to a known account in
`perpl_addr_map` — both marked `attributed=1`; otherwise NULL (keeper-sent
txs stay unattributed — never guessed). `tx_from` is always stored as chain
fact.

## Credentials — PENDING (owner)

`ENVIO_API_TOKEN` in `/var/www/terminal/backend/.env` — free:
sign in at https://envio.dev/app/api-tokens, generate, paste. HyperSync
returns 401 without it (mandatory since 2025-11). Until set, the worker
idles and writes `note='awaiting ENVIO_API_TOKEN'` to the state row — every
UI surface reports that truth. After setting:
`systemctl restart perpl-indexer` (this worker hosts no trading bots; the
perpl-terminal MM-slots gate does not apply to it).

## Known gap (PENDING)

The DEPLOYED contract emits event topics absent from our ABI copy (seen in
live receipts: `0xb5858652…`, `0x04cc3d2f…`, `0xa59d6df8…`, `0x9d9bc011…` —
an envelope event precedes each order action). They cannot be decoded
without their definitions — refresh `backend/perpl_abi.json` from Perpl to
extend coverage. Everything decodable today is indexed; nothing is guessed.

## Ops

```bash
# install (server)
cp /var/www/terminal/indexer/perpl-indexer.service /etc/systemd/system/
systemctl daemon-reload && systemctl enable --now perpl-indexer
# watch
tail -f /var/log/perpl-indexer.log
mysql perpl_terminal -e "SELECT * FROM perpl_indexer_state"
# backfill progress = (last_block - first_event_block) / (head_block - first_event_block)
```

Backfill runs from block 0 (HyperSync skips empty ranges server-side);
head-following at ~20s once caught up. Re-runs are idempotent
(INSERT IGNORE on UNIQUE(tx, log_index)).
