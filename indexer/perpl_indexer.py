"""Perpl on-chain event indexer — Envio HyperSync -> MySQL (Wallet Explorer Part 4).

Runtime decision (owner-approved 2026-09-08): a Python systemd worker consuming
Envio HyperSync directly (https://monad.hypersync.xyz, chain 143) instead of a
full HyperIndex Postgres+Node stack — same Envio data engine, MySQL-native
sink, same ops pattern as every other worker on the box. Built on Envio
HyperSync.

What it does:
  * backfills EVERY tracked Exchange-contract event from genesis (from_block 0
    — HyperSync skips empty ranges natively), then follows the head (~20s poll)
  * decodes with the REAL ABI (backend/perpl_abi.json — no signatures guessed;
    every tracked event carries all fields in `data`, none indexed)
  * sinks rows into `perpl_events` (at/block/tx/log_index/account_id/address/
    market_id/event_name/event_type/amount/balance_after/fee/bfa/raw JSON,
    UNIQUE(tx, log_index) => idempotent re-runs) and `perpl_addr_map`
    (AccountCreated: account_id <-> address, first_seen)
  * same-tx attribution: TakerOrderFilled/OrderPlaced/OrderCancelled/
    OrderChanged/ImmediateOrCancelExecuted/TriggerOrderRequest carry NO
    accountId in the ABI — they are attributed to the LAST OrderRequest earlier
    in the same transaction's log stream (attributed=1 marks derived ids;
    no context => account_id NULL, never guessed)
  * `perpl_indexer_state` row: last_block / head_block / first_event_block /
    note / updated_at — the API's /health lag metric and the History tab's
    "indexed from X, backfill N%" read this

Scaling: CNS = collateral nano-scale / 1e6 (USD). amount/fee/bfa/
balance_after columns are USD; PNS/LNS price/lot values stay RAW inside the
JSON (their per-market decimals live in the app's market registry — the API
layer converts, this worker never guesses market config).

Credentials: ENVIO_API_TOKEN (free, https://envio.dev/app/api-tokens) — 401
without it since 2025-11. Without a token the worker idles, logging every
10 min and writing note='awaiting ENVIO_API_TOKEN' to the state row so every
surface reports the truth instead of fabricating an empty history.

Run (server): systemd unit indexer/perpl-indexer.service
  ExecStart=/var/www/terminal/backend/venv/bin/python /var/www/terminal/indexer/perpl_indexer.py
  EnvironmentFile=/var/www/terminal/backend/.env
"""
import json
import logging
import os
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from eth_abi import decode as abi_decode
from sqlalchemy import create_engine, text
from web3 import Web3

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s | %(levelname)-7s | perpl_indexer | %(message)s")
log = logging.getLogger("perpl_indexer")

ROOT = Path(__file__).resolve().parent.parent
CONTRACT = "0x34b6552d57a35a1d042ccae1951bd1c370112a6f"
HYPERSYNC_URL = os.environ.get("HYPERSYNC_URL", "https://monad.hypersync.xyz")
TOKEN = os.environ.get("ENVIO_API_TOKEN", "").strip()
POLL_SEC = 20                       # head-follow cadence once caught up
IDLE_SEC = 600                      # no-token re-check cadence
BATCH_LOGS = 8000                   # rows per insert batch
CNS = 1e6

# DATABASE_URL comes from backend/.env (async url) — the worker runs sync
_db_url = os.environ.get("DATABASE_URL", "mysql+aiomysql://root:@localhost:3306/perpl")
_db_url = _db_url.replace("mysql+aiomysql://", "mysql+pymysql://")
engine = create_engine(_db_url, pool_pre_ping=True, pool_recycle=280)

# ---- tracked events + semantic mapping (exact ABI names, read not guessed) --
EVENT_TYPES = {
    "AccountCreated": "account_created",
    "CollateralDeposit": "deposit",
    "CollateralWithdrawal": "withdraw",
    "OrderRequest": "order_request",
    "OrderPlaced": "order_placed",
    "OrderCancelled": "order_cancelled",
    "OrderChanged": "order_changed",
    "TriggerOrderRequest": "trigger_request",
    "ImmediateOrCancelExecuted": "ioc_executed",
    "MakerOrderFilled": "fill_maker",
    "TakerOrderFilled": "fill_taker",
    "PositionOpened": "position_opened",
    "PositionIncreased": "position_increased",
    "PositionDecreased": "position_decreased",
    "PositionClosed": "position_closed",
    "PositionLiquidated": "position_liquidated",
    "PositionInverted": "position_inverted",
    "PositionDeleveraged": "position_deleveraged",
    "PositionUnwound": "position_unwound",
    "PositionCollateralDecreased": "collateral_decreased",
    "IncreasePositionCollateral": "collateral_increased",
    "AccountLiquidationCredit": "liq_credit",
    "FundingEventCompleted": "funding_event",
}
# events whose accountId must come from the same tx's last OrderRequest
NEEDS_TX_CONTEXT = {"TakerOrderFilled", "OrderPlaced", "OrderCancelled",
                    "OrderChanged", "ImmediateOrCancelExecuted",
                    "TriggerOrderRequest"}


def _load_events() -> dict:
    """topic0 -> {name, types, fields} from the real ABI."""
    abi = json.load(open(ROOT / "backend" / "perpl_abi.json"))
    out = {}
    for e in abi:
        if e.get("type") != "event" or e["name"] not in EVENT_TYPES:
            continue
        types = [i["type"] for i in e["inputs"]]
        sig = f"{e['name']}({','.join(types)})"
        # normalized WITHOUT 0x prefix (web3 .hex() prefixing differs by
        # version; the query builder re-adds the prefix)
        topic0 = Web3.keccak(text=sig).hex().lower().removeprefix("0x")
        out[topic0] = {"name": e["name"], "types": types,
                       "fields": [i["name"] for i in e["inputs"]]}
    assert len(out) == len(EVENT_TYPES), \
        f"ABI event lookup mismatch: {len(out)} of {len(EVENT_TYPES)}"
    return out


EVENTS = _load_events()


def ensure_tables() -> None:
    """Guarded DDL (mirrors migration v20 in backend/app/main.py — either
    process brings the schema forward)."""
    with engine.begin() as c:
        c.execute(text(
            "CREATE TABLE IF NOT EXISTS perpl_events ("
            " id BIGINT AUTO_INCREMENT PRIMARY KEY,"
            " at DATETIME NOT NULL,"
            " block BIGINT NOT NULL,"
            " tx VARCHAR(66) NOT NULL,"
            " log_index INT NOT NULL,"
            " account_id BIGINT NULL,"
            " attributed TINYINT(1) NOT NULL DEFAULT 0,"
            " address VARCHAR(42) NULL,"
            " tx_from VARCHAR(42) NULL,"
            " market_id INT NULL,"
            " event_name VARCHAR(48) NOT NULL,"
            " event_type VARCHAR(32) NOT NULL,"
            " amount DECIMAL(30,8) NULL,"
            " balance_after DECIMAL(30,8) NULL,"
            " fee DECIMAL(30,8) NULL,"
            " bfa DECIMAL(30,8) NULL,"
            " raw JSON NULL,"
            " UNIQUE KEY uq_pe_txlog (tx, log_index),"
            " INDEX ix_pe_addr_at (address, at),"
            " INDEX ix_pe_acct_at (account_id, at),"
            " INDEX ix_pe_block (block),"
            " INDEX ix_pe_txfrom (tx_from)"
            ") ENGINE=InnoDB DEFAULT CHARSET=utf8mb4"))
        c.execute(text(
            "CREATE TABLE IF NOT EXISTS perpl_addr_map ("
            " account_id BIGINT NOT NULL,"
            " address VARCHAR(42) NOT NULL,"
            " first_seen_block BIGINT NOT NULL,"
            " first_seen_at DATETIME NOT NULL,"
            " PRIMARY KEY (account_id),"
            " INDEX ix_pam_addr (address)"
            ") ENGINE=InnoDB DEFAULT CHARSET=utf8mb4"))
        c.execute(text(
            "CREATE TABLE IF NOT EXISTS perpl_indexer_state ("
            " id TINYINT NOT NULL,"
            " last_block BIGINT NOT NULL DEFAULT 0,"
            " head_block BIGINT NULL,"
            " first_event_block BIGINT NULL,"
            " note VARCHAR(128) NULL,"
            " updated_at DATETIME NOT NULL,"
            " PRIMARY KEY (id)"
            ") ENGINE=InnoDB DEFAULT CHARSET=utf8mb4"))
        c.execute(text(
            "INSERT IGNORE INTO perpl_indexer_state (id, last_block, updated_at) "
            "VALUES (1, 0, UTC_TIMESTAMP())"))


def set_state(**kw) -> None:
    sets = ", ".join(f"{k} = :{k}" for k in kw)
    with engine.begin() as c:
        c.execute(text(
            f"UPDATE perpl_indexer_state SET {sets}, updated_at = UTC_TIMESTAMP() "
            "WHERE id = 1"), kw)


def get_state() -> dict:
    with engine.begin() as c:
        row = c.execute(text(
            "SELECT last_block, head_block, first_event_block, note "
            "FROM perpl_indexer_state WHERE id = 1")).first()
    return {"last_block": row[0], "head_block": row[1],
            "first_event_block": row[2], "note": row[3]} if row else {}


def _jsonable(v):
    if isinstance(v, bytes):
        return "0x" + v.hex()
    if isinstance(v, (list, tuple)):
        return [_jsonable(x) for x in v]
    return v


def _usd(v) -> float | None:
    try:
        return round(int(v) / CNS, 6)
    except (TypeError, ValueError):
        return None


def derive_columns(name: str, f: dict) -> dict:
    """amount/balance_after/fee/bfa/market_id/account_id from the DECODED
    fields — only what the event explicitly states, nothing inferred."""
    out = {"account_id": None, "market_id": None, "amount": None,
           "balance_after": None, "fee": None, "bfa": None}
    acct = f.get("accountId", f.get("posAccountId"))
    if acct is not None:
        out["account_id"] = int(acct)
    if f.get("perpId") is not None:
        out["market_id"] = int(f["perpId"])
    if f.get("balanceCNS") is not None:
        out["balance_after"] = _usd(f["balanceCNS"])
    if name == "PositionLiquidated" and f.get("accBalanceCNS") is not None:
        out["balance_after"] = _usd(f["accBalanceCNS"])
    if name == "AccountLiquidationCredit":
        out["balance_after"] = _usd(f.get("endBalanceCNS"))
        if f.get("endBalanceCNS") is not None and f.get("startBalanceCNS") is not None:
            out["amount"] = round((int(f["endBalanceCNS"]) - int(f["startBalanceCNS"])) / CNS, 6)
    if name in ("CollateralDeposit", "CollateralWithdrawal", "IncreasePositionCollateral"):
        out["amount"] = _usd(f.get("amountCNS"))
        if name == "CollateralDeposit":
            out["bfa"] = out["amount"]
        elif name == "CollateralWithdrawal":
            out["bfa"] = -out["amount"] if out["amount"] is not None else None
    elif name in ("MakerOrderFilled", "TakerOrderFilled", "OrderPlaced", "OrderCancelled"):
        # amountCNS here is the SIGNED account-balance effect
        if f.get("amountCNS") is not None:
            out["bfa"] = round(int(f["amountCNS"]) / CNS, 6)
        out["fee"] = _usd(f.get("feeCNS"))
    elif name == "PositionLiquidated":
        if f.get("accAmountCNS") is not None:
            out["bfa"] = round(int(f["accAmountCNS"]) / CNS, 6)
        out["amount"] = _usd(f.get("posDepositCNS"))
    elif name.startswith("Position") or name == "PositionCollateralDecreased":
        if f.get("deltaPnlCNS") is not None:
            out["bfa"] = round(int(f["deltaPnlCNS"]) / CNS, 6)
        dep = f.get("endDepositCNS", f.get("depositCNS"))
        out["amount"] = _usd(dep)
        fees = [f.get("insFeeCNS"), f.get("protFeeCNS")]
        if any(x is not None for x in fees):
            out["fee"] = round(sum(int(x) for x in fees if x is not None) / CNS, 6)
    elif name == "OrderRequest":
        out["amount"] = _usd(f.get("amountCNS"))
    return out


def hypersync_query(from_block: int) -> dict:
    q = {
        "from_block": from_block,
        "logs": [{"address": [CONTRACT],
                  "topics": [["0x" + t for t in EVENTS.keys()]]}],
        "field_selection": {
            "log": ["block_number", "transaction_hash", "log_index", "topic0", "data"],
            "block": ["number", "timestamp"],
            "transaction": ["hash", "from"],
        },
    }
    req = urllib.request.Request(
        f"{HYPERSYNC_URL}/query", data=json.dumps(q).encode(),
        headers={"Content-Type": "application/json",
                 "Authorization": f"Bearer {TOKEN}"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.load(r)


_addr_map: dict[int, str] = {}


def _load_addr_map() -> None:
    with engine.begin() as c:
        for aid, addr in c.execute(text(
                "SELECT account_id, address FROM perpl_addr_map")):
            _addr_map[int(aid)] = addr


def process_batch(body: dict) -> tuple[int, int]:
    """Decode + sink one HyperSync response. Returns (events, addr_rows)."""
    block_ts: dict[int, int] = {}
    tx_from: dict[str, str] = {}
    logs: list[dict] = []
    for d in body.get("data", []):
        for t in d.get("transactions", []):
            h = str(t.get("hash") or "").lower()
            fr = str(t.get("from") or "").lower()
            if h and fr:
                tx_from[h] = fr
        for b in d.get("blocks", []):
            try:
                block_ts[int(b["number"])] = int(b["timestamp"], 16) \
                    if isinstance(b["timestamp"], str) else int(b["timestamp"])
            except (KeyError, TypeError, ValueError):
                continue
        logs.extend(d.get("logs", []))
    logs.sort(key=lambda l: (int(l["block_number"]), int(l["log_index"])))

    rows = []
    addr_rows = []
    addr_to_acct = {a: i for i, a in _addr_map.items()}
    tx_ctx: dict[str, int] = {}   # tx -> last OrderRequest accountId
    for lg in logs:
        topic0 = str(lg.get("topic0", "")).lower().removeprefix("0x")
        ev = EVENTS.get(topic0)
        if ev is None:
            continue
        data_hex = str(lg.get("data") or "").removeprefix("0x")
        try:
            vals = abi_decode(ev["types"], bytes.fromhex(data_hex))
        except Exception as exc:
            log.warning("decode failed %s at %s/%s: %s", ev["name"],
                        lg.get("block_number"), lg.get("log_index"), exc)
            continue
        f = dict(zip(ev["fields"], vals))
        name = ev["name"]
        tx = str(lg["transaction_hash"]).lower()
        cols = derive_columns(name, f)
        attributed = 0
        if name == "OrderRequest" and cols["account_id"] is not None:
            tx_ctx[tx] = cols["account_id"]
        if name in NEEDS_TX_CONTEXT and cols["account_id"] is None:
            ctx = tx_ctx.get(tx)
            if ctx is not None:
                cols["account_id"] = ctx
                attributed = 1
            else:
                # layered fallback: the tx SENDER, when it maps to a known
                # Perpl account, is the acting trader for direct-wallet txs
                # (keeper-sent txs stay unattributed — never guessed)
                sender = tx_from.get(tx)
                if sender and sender in addr_to_acct:
                    cols["account_id"] = addr_to_acct[sender]
                    attributed = 1
        blk = int(lg["block_number"])
        ts = block_ts.get(blk)
        at = datetime.fromtimestamp(ts, tz=timezone.utc).replace(tzinfo=None) \
            if ts else datetime.utcnow()
        if name == "AccountCreated":
            _addr_map[int(f["id"])] = str(f["account"]).lower()
            addr_rows.append({"aid": int(f["id"]),
                              "addr": str(f["account"]).lower(),
                              "blk": blk, "at": at})
        rows.append({
            "at": at, "block": blk, "tx": tx, "li": int(lg["log_index"]),
            "aid": cols["account_id"], "attr": attributed,
            "addr": _addr_map.get(cols["account_id"]) if cols["account_id"] is not None else None,
            "txf": tx_from.get(tx),
            "mid": cols["market_id"], "en": name,
            "et": EVENT_TYPES[name], "am": cols["amount"],
            "ba": cols["balance_after"], "fee": cols["fee"], "bfa": cols["bfa"],
            "raw": json.dumps({k: _jsonable(v) for k, v in f.items()}),
        })

    inserted = 0
    with engine.begin() as c:
        for i in range(0, len(addr_rows), 1000):
            c.execute(text(
                "INSERT IGNORE INTO perpl_addr_map "
                "(account_id, address, first_seen_block, first_seen_at) "
                "VALUES (:aid, :addr, :blk, :at)"), addr_rows[i:i + 1000])
        for i in range(0, len(rows), 1000):
            batch = rows[i:i + 1000]
            c.execute(text(
                "INSERT IGNORE INTO perpl_events "
                "(at, block, tx, log_index, account_id, attributed, address, "
                " tx_from, market_id, event_name, event_type, amount, "
                " balance_after, fee, bfa, raw) "
                "VALUES (:at, :block, :tx, :li, :aid, :attr, :addr, :txf, :mid, "
                " :en, :et, :am, :ba, :fee, :bfa, :raw)"), batch)
            inserted += len(batch)
        # repair pass only when some rows could not resolve inline (an
        # account_id seen before its AccountCreated — should not happen in an
        # in-order backfill; logged if it does)
        if any(r["aid"] is not None and r["addr"] is None for r in rows):
            n = c.execute(text(
                "UPDATE perpl_events e JOIN perpl_addr_map m ON m.account_id = e.account_id "
                "SET e.address = m.address WHERE e.address IS NULL AND e.account_id IS NOT NULL"))
            log.warning("address repair pass updated %s rows", n.rowcount)
    return inserted, len(addr_rows)


def run() -> None:
    ensure_tables()
    if not TOKEN:
        log.error("ENVIO_API_TOKEN missing — indexer idle (token is free: "
                  "https://envio.dev/app/api-tokens). Re-checking env is "
                  "pointless in-process; restart the service after setting it.")
        while True:
            set_state(note="awaiting ENVIO_API_TOKEN")
            time.sleep(IDLE_SEC)

    _load_addr_map()
    st = get_state()
    from_block = int(st.get("last_block") or 0)
    log.info("starting from block %s (HyperSync %s, %d event types)",
             from_block, HYPERSYNC_URL, len(EVENTS))
    backoff = 5.0
    while True:
        try:
            body = hypersync_query(from_block)
        except urllib.error.HTTPError as exc:
            if exc.code == 401:
                log.error("HyperSync 401 — token invalid/expired; idling")
                set_state(note="ENVIO_API_TOKEN rejected (401)")
                time.sleep(IDLE_SEC)
                continue
            if exc.code == 429:
                ra = exc.headers.get("Retry-After")
                wait = float(ra) if ra else backoff
                log.warning("HyperSync 429 — sleeping %.0fs", wait)
                time.sleep(wait)
                backoff = min(backoff * 2, 120)
                continue
            log.warning("HyperSync HTTP %s — retry in %.0fs", exc.code, backoff)
            time.sleep(backoff)
            backoff = min(backoff * 2, 120)
            continue
        except Exception as exc:
            log.warning("HyperSync query failed (%s) — retry in %.0fs", exc, backoff)
            time.sleep(backoff)
            backoff = min(backoff * 2, 120)
            continue
        backoff = 5.0

        inserted, accounts = process_batch(body)
        next_block = int(body.get("next_block") or from_block)
        head = body.get("archive_height")
        if inserted or accounts:
            log.info("blocks %s..%s: %d events, %d accounts (head %s)",
                     from_block, next_block, inserted, accounts, head)
        kw = {"last_block": next_block, "note": "ok"}
        if head is not None:
            kw["head_block"] = int(head)
        st2 = get_state()
        if st2.get("first_event_block") is None and inserted:
            with engine.begin() as c:
                fb = c.execute(text("SELECT MIN(block) FROM perpl_events")).scalar()
            if fb:
                kw["first_event_block"] = int(fb)
        set_state(**kw)
        from_block = next_block
        if head is not None and next_block >= int(head):
            time.sleep(POLL_SEC)


if __name__ == "__main__":
    try:
        run()
    except KeyboardInterrupt:
        sys.exit(0)
