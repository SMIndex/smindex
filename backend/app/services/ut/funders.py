"""Funder of a wallet's first Arbitrum deposit (spec section 3: one RPC read
per wallet, no Hyperliquid budget). The deposit's Arbitrum transaction is
sent by the wallet itself in nearly every case, so the funder is whoever
sent USDC to that address before it: the last USDC Transfer into the wallet
in the FUNDER_LOOKBACK blocks before the deposit (about 1.4 hours on
Arbitrum). A transfer from the zero address is a mint (CCTP or canonical
bridge). Nothing found reads 'unknown'; the label comes from funders.csv.

Internal Hyperliquid transfers (type send) already carry their sender from
the ledger read (hl.first_funding); this module only handles kind 'deposit'.
"""
from __future__ import annotations

import asyncio
import os
from datetime import datetime

import httpx
from sqlalchemy import text

from app.db.database import get_session_factory
from app.services.ut import scores
from app.utils.logger import get_logger

logger = get_logger(__name__)

RPCS = [u for u in os.environ.get("UT_ARBITRUM_RPCS", "https://arb1.arbitrum.io/rpc,https://arbitrum-one-rpc.publicnode.com,https://arbitrum.drpc.org").split(",") if u]
USDC = {"0xaf88d065e77c8cc2239327c5edb3a432268e5831", "0xff970a61a04b1ca14834a43f5de4533ebddb5cc8"}   # native USDC, USDC.e
TRANSFER_TOPIC = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"
FUNDER_LOOKBACK = 20_000        # blocks (about 0.25 s each)
PACE_SEC = 0.4
MAX_PER_RUN = 400


async def _rpc(client: httpx.AsyncClient, method: str, params: list):
    last = None
    for url in RPCS:
        try:
            r = await client.post(url, json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params}, timeout=20.0)
            d = r.json()
            if "result" in d:
                return d["result"]
            last = d.get("error")
        except Exception as exc:  # noqa: BLE001
            last = str(exc)
    raise RuntimeError(f"{method}: {last}")


def _topic_addr(topic: str) -> str:
    return "0x" + topic[-40:].lower()


async def trace_one(client: httpx.AsyncClient, wallet: str, tx: str) -> tuple[str | None, str]:
    """(funder address or None, note)."""
    t = await _rpc(client, "eth_getTransactionByHash", [tx])
    if not t:
        return None, "deposit tx not found on Arbitrum"
    sender = str(t.get("from") or "").lower()
    block = int(t.get("blockNumber"), 16)
    if sender and sender != wallet.lower():
        return sender, "deposit sent by another address"
    logs = await _rpc(client, "eth_getLogs", [{
        "fromBlock": hex(max(0, block - FUNDER_LOOKBACK)), "toBlock": hex(block),
        "address": sorted(USDC), "topics": [TRANSFER_TOPIC, None, "0x" + "0" * 24 + wallet.lower()[2:]]}])
    if not logs:
        return None, f"no USDC transfer into the wallet in the {FUNDER_LOOKBACK} blocks before the deposit"
    logs.sort(key=lambda l: (int(l["blockNumber"], 16), int(l.get("logIndex", "0x0"), 16)))
    frm = _topic_addr(logs[-1]["topics"][1])
    return frm, ("mint (bridge or CCTP)" if frm == "0x" + "0" * 40 else f"last USDC transfer in, {len(logs)} in the window")


async def build(limit: int = MAX_PER_RUN) -> dict:
    labels = scores.funder_labels()
    sf = get_session_factory()
    async with sf() as s:
        rows = (await s.execute(text(
            "SELECT wallet, first_deposit_tx FROM ut_wallet_meta WHERE first_deposit_kind='deposit' AND first_deposit_tx IS NOT NULL "
            "AND funder IS NULL AND funder_checked_at IS NULL AND flagged_at IS NOT NULL ORDER BY flagged_at LIMIT :n"), {"n": limit})).all()
    done = found = 0
    async with httpx.AsyncClient() as client:
        for wallet, tx in rows:
            try:
                funder, note = await trace_one(client, wallet, tx)
            except Exception as exc:  # noqa: BLE001
                logger.warning("ut funders: %s: %s", wallet[:10], exc)
                continue
            label = labels.get(funder or "", (None, None))[0] if funder else None
            async with sf() as s:
                await s.execute(text(
                    "UPDATE ut_wallet_meta SET funder=:f, funder_label=COALESCE(:l, funder_label), funder_checked_at=UTC_TIMESTAMP() WHERE wallet=:w"),
                    {"f": funder, "l": label, "w": wallet})
                await s.commit()
            done += 1
            found += 1 if funder else 0
            await asyncio.sleep(PACE_SEC)
    out = {"traced": done, "with_funder": found, "pending": max(0, len(rows) - done)}
    logger.info("ut funders: %s", out)
    return out
