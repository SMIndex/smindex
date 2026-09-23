#!/usr/bin/env python3
"""
Perpl volume bot - opens BTC long at market, immediately closes.

WARNING: market orders both ways = 10 bps per RT. With $100 capital at BTC 10x,
each RT burns ~$1.00 in fees. $500k volume costs ~$250 in fees alone.
This bot will drain your account if left running. Use small MAX_ROUND_TRIPS.

USAGE
    pip install httpx websockets eth-account python-dotenv
    cp scripts/perpl_volume_bot.env.example scripts/.env   # then edit
    python scripts/perpl_volume_bot.py            # dry-run by default
    python scripts/perpl_volume_bot.py --live     # real money

ENV FILE (scripts/.env)
    PRIVATE_KEY=0x...           # required, the wallet trading
    MARGIN_USD=10               # $ per trade (NOT total bankroll)
    LEVERAGE=10                 # 1-10 for BTC
    MARKET_ID=1                 # 1=BTC, 10=MON, 20=ETH, 30=SOL
    MAX_ROUND_TRIPS=5           # bot stops after this many opens+closes
    MAX_DAILY_LOSS_USD=5        # bot stops if PnL+fees go this far negative
    DELAY_BETWEEN_RT_SEC=5      # pause between cycles
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import signal
import sys
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx
import websockets
from eth_account import Account
from eth_account.messages import encode_defunct
from web3 import Web3

try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).parent / ".env")
except ImportError:
    pass


# ---------- Constants ----------
PERPL_REST = "https://app.perpl.xyz"
PERPL_WS = "wss://app.perpl.xyz/ws/v1/trading"
CHAIN_ID = 143  # Monad mainnet
MONAD_RPC = "https://rpc.monad.xyz"
PERPL_CONTRACT = "0x34b6552d57a35a1d042ccae1951bd1c370112a6f"

# Minimal ABI: just getAccountByAddr (struct AccountInfo)
PERPL_ABI = [{
    "type": "function",
    "name": "getAccountByAddr",
    "stateMutability": "view",
    "inputs": [{"name": "accountAddress", "type": "address"}],
    "outputs": [{
        "name": "accountInfo",
        "type": "tuple",
        "components": [
            {"name": "accountId", "type": "uint256"},
            {"name": "balanceCNS", "type": "uint256"},
            {"name": "lockedBalanceCNS", "type": "uint256"},
            {"name": "frozen", "type": "uint8"},
            {"name": "accountAddr", "type": "address"},
            {"name": "positions", "type": "tuple", "components": [
                {"name": "bank1", "type": "uint256"},
                {"name": "bank2", "type": "uint256"},
                {"name": "bank3", "type": "uint256"},
                {"name": "bank4", "type": "uint256"},
            ]},
        ],
    }],
}]

# Order types
T_OPEN_LONG = 1
T_OPEN_SHORT = 2
T_CLOSE_LONG = 3
T_CLOSE_SHORT = 4

# Order flags
FL_GTC = 0
FL_POST_ONLY = 1
FL_IOC = 4

# Per-market config (must match what Perpl returns on /api/v1/pub/context)
# We hardcode these to avoid an extra REST call on every order; verified
# against the live API response at the time of writing.
# Fees: contract events expose them as fee_per_100K, so raw value 500 = 50 bps.
# Confirmed from on-chain TakerOrderFilled event: feeCNS / amountCNS ~ 49.44 bps.
MARKETS = {
    1:  {"symbol": "BTC", "price_decimals": 1, "size_decimals": 5, "max_leverage": 10, "max_price_impact_pct": 5,  "order_ttl_blocks": 6, "taker_fee_bps": 50, "maker_fee_bps": 25},
    10: {"symbol": "MON", "price_decimals": 6, "size_decimals": 0, "max_leverage": 5,  "max_price_impact_pct": 10, "order_ttl_blocks": 6, "taker_fee_bps": 50, "maker_fee_bps": 25},
    20: {"symbol": "ETH", "price_decimals": 2, "size_decimals": 3, "max_leverage": 10, "max_price_impact_pct": 5,  "order_ttl_blocks": 6, "taker_fee_bps": 50, "maker_fee_bps": 25},
    30: {"symbol": "SOL", "price_decimals": 2, "size_decimals": 3, "max_leverage": 5,  "max_price_impact_pct": 5,  "order_ttl_blocks": 6, "taker_fee_bps": 50, "maker_fee_bps": 25},
}


# ---------- Config ----------
@dataclass
class Config:
    private_key: str
    # margin_usd can be a number OR the string "ALL" to use full Perpl balance
    margin_usd: float | str = 10.0
    leverage: int = 10
    market_id: int = 1
    side: str = "long"   # "long" or "short"
    max_round_trips: int = 1
    max_daily_loss_usd: float = 5.0
    delay_between_rt_sec: float = 5.0
    delay_between_legs_sec: float = 0.0  # pause between open fill and close
    live: bool = False

    @property
    def market(self) -> dict:
        return MARKETS[self.market_id]

    @property
    def address(self) -> str:
        return Account.from_key(self.private_key).address


def _normalize_private_key(raw: str) -> str:
    """Accept private key with or without 0x prefix, with leading/trailing whitespace."""
    s = raw.strip()
    if s.lower().startswith("0x"):
        s = s[2:]
    # Must be exactly 64 hex chars
    if len(s) != 64:
        sys.exit(f"ERROR: PRIVATE_KEY must be 64 hex chars (32 bytes). Got {len(s)} chars.")
    try:
        int(s, 16)
    except ValueError:
        sys.exit("ERROR: PRIVATE_KEY contains non-hex characters.")
    return "0x" + s.lower()


def load_config(live: bool) -> Config:
    pk_raw = os.environ.get("PRIVATE_KEY", "")
    if not pk_raw.strip():
        sys.exit("ERROR: PRIVATE_KEY not set. Edit scripts/.env.")
    pk = _normalize_private_key(pk_raw)

    margin_raw = os.environ.get("MARGIN_USD", "10").strip()
    if margin_raw.lower() in ("all", "max", "full"):
        margin_val: float | str = "ALL"
    else:
        try:
            margin_val = float(margin_raw)
        except ValueError:
            sys.exit(f"ERROR: MARGIN_USD must be a number or 'ALL'. Got: {margin_raw}")

    side_raw = os.environ.get("SIDE", "long").strip().lower()
    if side_raw not in ("long", "short"):
        sys.exit(f"ERROR: SIDE must be 'long' or 'short'. Got: {side_raw}")

    cfg = Config(
        private_key=pk,
        margin_usd=margin_val,
        leverage=int(os.environ.get("LEVERAGE", 10)),
        market_id=int(os.environ.get("MARKET_ID", 1)),
        side=side_raw,
        max_round_trips=int(os.environ.get("MAX_ROUND_TRIPS", 1)),
        max_daily_loss_usd=float(os.environ.get("MAX_DAILY_LOSS_USD", 5)),
        delay_between_rt_sec=float(os.environ.get("DELAY_BETWEEN_RT_SEC", 5)),
        delay_between_legs_sec=float(os.environ.get("DELAY_BETWEEN_LEGS_SEC", 0)),
        live=live,
    )
    if cfg.market_id not in MARKETS:
        sys.exit(f"ERROR: MARKET_ID={cfg.market_id} not in {list(MARKETS)}")
    if cfg.leverage < 1 or cfg.leverage > cfg.market["max_leverage"]:
        sys.exit(f"ERROR: LEVERAGE must be 1..{cfg.market['max_leverage']} for {cfg.market['symbol']}")
    return cfg


# ---------- On-chain balance ----------
def get_perpl_balance(address: str) -> dict:
    """Read on-chain Perpl account balance.
    Returns {account_id, balance_usdc, locked_usdc, available_usdc}.
    balance is the free (unlocked) USDC; locked is what's in open positions.
    Returns account_id=0 if no Perpl account exists for this wallet (the
    contract reverts in that case, which we treat as 'not registered')."""
    w3 = Web3(Web3.HTTPProvider(MONAD_RPC))
    contract = w3.eth.contract(address=Web3.to_checksum_address(PERPL_CONTRACT), abi=PERPL_ABI)
    try:
        info = contract.functions.getAccountByAddr(Web3.to_checksum_address(address)).call()
    except Exception as e:
        # Common case: wallet never registered on Perpl, contract reverts.
        # Selector 0x03a0e277 is the "account not found" revert.
        msg = str(e).lower()
        if "0x03a0e277" in msg or "revert" in msg or "execution reverted" in msg:
            return {"account_id": 0, "balance_usdc": 0.0, "locked_usdc": 0.0, "available_usdc": 0.0}
        raise
    account_id = int(info[0])
    balance_cns = int(info[1])
    locked_cns = int(info[2])
    return {
        "account_id": account_id,
        "balance_usdc": balance_cns / 1e6,
        "locked_usdc": locked_cns / 1e6,
        # "available" = balance - locked is what's free to use as new margin
        "available_usdc": max(0.0, (balance_cns - locked_cns) / 1e6),
    }


def resolve_margin(cfg: Config, available_usdc: float) -> float:
    """Convert MARGIN_USD config (number or 'ALL') into a concrete margin amount.
    When 'ALL': leave ~2% buffer for entry fee, close fee, and rounding."""
    if isinstance(cfg.margin_usd, str) and cfg.margin_usd == "ALL":
        # Reserve buffer for fees: at 50 bp taker × leverage, open fee is
        # 0.5% × leverage of margin. At 10x that's 5% of margin per leg.
        # Use 90% of available (10% buffer) to cover entry fee + slippage with room.
        m = available_usdc * 0.90
        return max(0.0, m)
    m = float(cfg.margin_usd)
    if m > available_usdc:
        sys.exit(f"ERROR: MARGIN_USD={m} exceeds available balance ${available_usdc:.2f}")
    return m


# ---------- Logging helpers ----------
def log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


# ---------- Step 1: SIWE auth ----------
async def siwe_authenticate(cfg: Config) -> tuple[str, dict[str, str]]:
    """Sign in with Ethereum to get a Perpl session nonce and cookies.
    Returns (nonce, cookies). Cookies must be passed when opening the WS."""
    async with httpx.AsyncClient(timeout=15.0) as client:
        # Step A: request payload
        log(f"Requesting SIWE payload for {cfg.address}")
        r = await client.post(
            f"{PERPL_REST}/api/v1/auth/payload",
            json={"chain_id": CHAIN_ID, "address": cfg.address},
        )
        if r.status_code == 418:
            sys.exit("ERROR: Wallet not whitelisted on Perpl. Visit perpl.xyz to get access.")
        r.raise_for_status()
        payload = r.json()

        # Step B: sign the message
        message = payload["message"]
        encoded = encode_defunct(text=message)
        signed = Account.sign_message(encoded, private_key=cfg.private_key)
        signature = signed.signature.hex()
        if not signature.startswith("0x"):
            signature = "0x" + signature

        # Step C: submit signature
        log("Submitting signature to Perpl...")
        r = await client.post(
            f"{PERPL_REST}/api/v1/auth/connect",
            json={
                "chain_id": CHAIN_ID,
                "address": cfg.address,
                "message": message,
                "nonce": payload["nonce"],
                "issued_at": payload["issued_at"],
                "mac": payload["mac"],
                "signature": signature,
            },
        )
        if r.status_code != 200:
            sys.exit(f"ERROR: /auth/connect failed: {r.status_code} {r.text[:300]}")
        data = r.json()
        cookies = {k: v for k, v in r.cookies.items()}
        log(f"Connected. session_nonce={data.get('nonce','')[:16]}... cookies={list(cookies)}")
        return data["nonce"], cookies


# ---------- Step 2: get mark price (REST snapshot) ----------
async def get_mark_price(market_id: int) -> float:
    async with httpx.AsyncClient(timeout=10.0) as client:
        r = await client.get(f"{PERPL_REST}/api/v1/pub/context")
        r.raise_for_status()
        ctx = r.json()
        for m in ctx.get("markets", []):
            if m["id"] == market_id:
                pd = 10 ** m["config"]["price_decimals"]
                return m["state"]["mrk"] / pd
    raise RuntimeError(f"Market {market_id} not found in context")


# ---------- Step 3: trading WS session ----------
class TradingSession:
    def __init__(self, cfg: Config, nonce: str, cookies: dict[str, str], account_id: int = 0):
        self.cfg = cfg
        self.nonce = nonce
        self.cookies = cookies
        self.ws: Any = None
        # Pre-set from the on-chain read so we don't have to fish it out of the WS auth ack.
        self.account_id: int = int(account_id)
        self.last_block: int = 0
        self.auth_complete: bool = False
        self._request_id: int = int(time.time() * 1000)
        self._pending: dict[int, asyncio.Future] = {}
        self._block_waiters: list[asyncio.Future] = []
        self._reader_task: asyncio.Task | None = None
        self._closing = False

    async def connect(self) -> None:
        cookie_header = "; ".join(f"{k}={v}" for k, v in self.cookies.items())
        session_id = str(uuid.uuid4())
        headers = {
            "Cookie": cookie_header,
            "x-browser-session-id": session_id,
        }
        log(f"Connecting trading WS...")
        # websockets >= 13 uses additional_headers, <13 (legacy) uses extra_headers.
        # Try the newer keyword first, fall back to the legacy one.
        common_kw = {"ping_interval": 20, "ping_timeout": 10}
        try:
            self.ws = await websockets.connect(
                PERPL_WS, additional_headers=headers, **common_kw,
            )
        except TypeError:
            self.ws = await websockets.connect(
                PERPL_WS, extra_headers=headers, **common_kw,
            )
        # Send auth
        await self.ws.send(json.dumps({
            "mt": 4,
            "nonce": self.nonce,
            "chain_id": CHAIN_ID,
            "ses": session_id,
        }))
        # Start the message reader
        self._reader_task = asyncio.create_task(self._read_loop())
        # Wait for auth ack (any non-heartbeat from Perpl after the auth message)
        # OR for the first block heartbeat (means the WS is talking to us — auth implicitly OK).
        deadline = time.time() + 10
        while time.time() < deadline:
            if self.auth_complete or self.last_block > 0:
                break
            await asyncio.sleep(0.1)
        if not (self.auth_complete or self.last_block > 0):
            raise RuntimeError("No response from Perpl after auth. Check network / cookies.")
        if not self.account_id:
            raise RuntimeError("No account_id available. Wallet is not registered on Perpl.")
        log(f"Authenticated. account_id={self.account_id}  last_block={self.last_block}")

    async def _read_loop(self) -> None:
        try:
            async for raw in self.ws:
                if self._closing:
                    return
                try:
                    msg = json.loads(raw)
                except Exception:
                    continue
                # Heartbeat - track block number
                if msg.get("mt") == 100 and msg.get("sn"):
                    self.last_block = msg["sn"]
                    # Resolve any pending block waiters
                    while self._block_waiters:
                        fut = self._block_waiters.pop()
                        if not fut.done():
                            fut.set_result(msg["sn"])
                    continue
                # Any non-heartbeat message means the WS is responsive — auth went through.
                self.auth_complete = True
                # If Perpl also volunteers account info, keep it (but we already have it from chain).
                if not self.account_id:
                    d = msg.get("d")
                    if isinstance(d, dict):
                        acc = d.get("acc") or d.get("account_id") or d.get("id")
                        if acc:
                            self.account_id = int(acc)
                # Order-related responses: route to pending futures by rq
                rq = msg.get("rq")
                if rq is None and msg.get("mt") == 24 and isinstance(msg.get("d"), list):
                    for od in msg["d"]:
                        rqi = od.get("rq")
                        if rqi and rqi in self._pending:
                            fut = self._pending.pop(rqi)
                            if not fut.done():
                                fut.set_result(("order_update", od))
                    continue
                if rq and rq in self._pending:
                    fut = self._pending.pop(rq)
                    if not fut.done():
                        fut.set_result((msg.get("mt"), msg))
        except websockets.exceptions.ConnectionClosed:
            log("Trading WS closed.")
        except Exception as e:
            log(f"Reader loop error: {e}")

    async def wait_for_fresh_block(self, timeout: float = 5.0) -> int:
        loop = asyncio.get_event_loop()
        fut: asyncio.Future = loop.create_future()
        self._block_waiters.append(fut)
        try:
            return await asyncio.wait_for(fut, timeout=timeout)
        finally:
            if fut in self._block_waiters:
                self._block_waiters.remove(fut)

    async def send_order(
        self,
        order_type: int,
        size: float,
        price: float,
        leverage_x: float,
        flags: int = FL_IOC,
    ) -> dict:
        m = self.cfg.market
        scaled_size = round(size * (10 ** m["size_decimals"]))
        scaled_price = round(price * (10 ** m["price_decimals"]))
        block = await self.wait_for_fresh_block()
        self._request_id += 1
        rq = self._request_id
        order = {
            "mt": 22,
            "rq": rq,
            "mkt": self.cfg.market_id,
            "acc": self.account_id,
            "t": order_type,
            "p": scaled_price,
            "s": scaled_size,
            "fl": flags,
            "lv": round(leverage_x * 100) if order_type in (T_OPEN_LONG, T_OPEN_SHORT) else 0,
            "lb": block + m["order_ttl_blocks"],
        }
        log(f"-> ORDER rq={rq} t={order_type} size={scaled_size} px={scaled_price} fl={flags} lv={order['lv']} lb={order['lb']}")
        loop = asyncio.get_event_loop()
        fut: asyncio.Future = loop.create_future()
        self._pending[rq] = fut
        await self.ws.send(json.dumps(order))
        try:
            kind, payload = await asyncio.wait_for(fut, timeout=15)
        except asyncio.TimeoutError:
            self._pending.pop(rq, None)
            raise RuntimeError("Timeout waiting for order response")
        return {"kind": kind, "payload": payload, "rq": rq}

    async def close(self) -> None:
        self._closing = True
        if self.ws:
            await self.ws.close()
        if self._reader_task:
            try:
                await asyncio.wait_for(self._reader_task, timeout=2)
            except asyncio.TimeoutError:
                pass


# ---------- Main loop ----------
async def run_round_trips(cfg: Config) -> None:
    log("=" * 64)
    log(f"Perpl volume bot — {cfg.market['symbol']} {cfg.leverage}x")
    log(f"Configured margin: {cfg.margin_usd}  RTs: {cfg.max_round_trips}  Mode: {'LIVE' if cfg.live else 'DRY-RUN'}")
    log(f"Wallet: {cfg.address}")
    log("=" * 64)

    # Query on-chain Perpl account state
    log("Reading on-chain Perpl account balance...")
    try:
        acct = get_perpl_balance(cfg.address)
    except Exception as e:
        sys.exit(f"ERROR: Could not read account from chain: {e}")
    if acct["account_id"] == 0:
        sys.exit(
            "ERROR: No Perpl exchange account exists for this wallet.\n"
            "       Visit https://app.perpl.xyz to create one and deposit USDC first."
        )
    log(f"account_id={acct['account_id']}")
    log(f"  balance:   ${acct['balance_usdc']:.4f} USDC")
    log(f"  locked:    ${acct['locked_usdc']:.4f} USDC (in open positions)")
    log(f"  available: ${acct['available_usdc']:.4f} USDC")

    if acct["available_usdc"] < 1:
        sys.exit("ERROR: Available balance below $1. Deposit more USDC into your Perpl account.")

    margin = resolve_margin(cfg, acct["available_usdc"])

    mark = await get_mark_price(cfg.market_id)
    log(f"\n{cfg.market['symbol']} mark: ${mark}")

    notional = margin * cfg.leverage
    size = notional / mark
    est_fee_per_leg = notional * (cfg.market["taker_fee_bps"] / 10000)
    est_fee_per_rt = est_fee_per_leg * 2
    est_total_fee = est_fee_per_rt * cfg.max_round_trips
    est_total_volume = notional * 2 * cfg.max_round_trips

    log(f"Margin per trade: ${margin:.4f}" + (" (full balance × 0.98 buffer)" if cfg.margin_usd == "ALL" else ""))
    log(f"Notional per trade: ${notional:.2f}")
    log(f"Size per trade: {size:.6f} {cfg.market['symbol']}")
    log(f"Est. fee per RT: ${est_fee_per_rt:.4f} (taker {cfg.market['taker_fee_bps']} bps × 2 legs)")
    log(f"Est. total fees over {cfg.max_round_trips} RTs: ${est_total_fee:.2f}")
    log(f"Est. total volume generated: ${est_total_volume:.2f}")
    log("")

    if not cfg.live:
        log("DRY-RUN: not connecting to Perpl. Pass --live to actually trade.")
        return

    nonce, cookies = await siwe_authenticate(cfg)
    sess = TradingSession(cfg, nonce, cookies, account_id=acct["account_id"])
    await sess.connect()

    cumulative_fees = 0.0
    cumulative_pnl = 0.0
    completed = 0
    stop = False

    def handle_signal(signum, frame):
        nonlocal stop
        log("Stop signal received. Will exit after current cycle.")
        stop = True
    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)

    try:
        for rt in range(1, cfg.max_round_trips + 1):
            if stop:
                break
            net = cumulative_pnl - cumulative_fees
            if net <= -cfg.max_daily_loss_usd:
                log(f"DAILY LOSS LIMIT HIT (net ${net:.2f} <= -${cfg.max_daily_loss_usd}). Stopping.")
                break

            log(f"\n--- RT {rt}/{cfg.max_round_trips} (net so far: ${net:.4f}) ---")
            # Refresh mark for sizing this RT
            try:
                mark = await get_mark_price(cfg.market_id)
            except Exception as e:
                log(f"Mark fetch failed: {e}; using previous {mark}")
            # If MARGIN_USD=ALL, re-read balance so each RT scales to whatever's left
            # after the previous RT's fees. Fixed margin uses the value resolved at startup.
            if isinstance(cfg.margin_usd, str) and cfg.margin_usd == "ALL":
                try:
                    fresh = get_perpl_balance(cfg.address)
                    margin = resolve_margin(cfg, fresh["available_usdc"])
                    log(f"  Re-sized: available=${fresh['available_usdc']:.4f}  margin=${margin:.4f}")
                except Exception as e:
                    log(f"  Balance re-read failed: {e}; using previous margin=${margin:.4f}")
                if margin < 1:
                    log(f"Available margin below $1 — stopping.")
                    break
            size = (margin * cfg.leverage) / mark

            # Direction-aware order types and slippage:
            # LONG  open  -> buy,  needs price >= mark; allow up to mark*(1+impact)
            # LONG  close -> sell, needs price <= mark; allow down to mark*(1-impact)
            # SHORT open  -> sell, needs price <= mark; allow down to mark*(1-impact)
            # SHORT close -> buy,  needs price >= mark; allow up to mark*(1+impact)
            impact = cfg.market["max_price_impact_pct"] / 100
            if cfg.side == "long":
                open_type, close_type = T_OPEN_LONG, T_CLOSE_LONG
                open_px = mark * (1 + impact)
            else:
                open_type, close_type = T_OPEN_SHORT, T_CLOSE_SHORT
                open_px = mark * (1 - impact)

            log(f"Opening {cfg.side.upper()} {size:.6f} at IOC bound ${open_px:.4f}")
            try:
                res = await sess.send_order(
                    order_type=open_type, size=size, price=open_px,
                    leverage_x=cfg.leverage, flags=FL_IOC,
                )
            except Exception as e:
                log(f"OPEN failed: {e}")
                break
            log(f"<- {res['kind']}: {json.dumps(res['payload'])[:300]}")
            od = res["payload"] if res["kind"] == "order_update" else None
            if not od or not od.get("fs"):
                log("Open did not fill. Skipping close.")
                continue
            pd_div = 10 ** cfg.market["price_decimals"]
            sd_div = 10 ** cfg.market["size_decimals"]
            filled_size = od["fs"] / sd_div
            filled_price = od.get("fp", 0) / pd_div
            open_notional = filled_size * filled_price
            open_fee = open_notional * (cfg.market["taker_fee_bps"] / 10000)
            log(f"Filled: {filled_size:.6f} @ ${filled_price:.4f}  notional ${open_notional:.2f}  fee ${open_fee:.4f}")

            if cfg.delay_between_legs_sec > 0:
                log(f"Waiting {cfg.delay_between_legs_sec}s before close...")
                await asyncio.sleep(cfg.delay_between_legs_sec)

            # CLOSE leg: opposite slippage direction
            close_px = filled_price * (1 - impact) if cfg.side == "long" else filled_price * (1 + impact)
            log(f"Closing {cfg.side.upper()} {filled_size:.6f} at IOC bound ${close_px:.4f}")
            try:
                res2 = await sess.send_order(
                    order_type=close_type, size=filled_size, price=close_px,
                    leverage_x=0, flags=FL_IOC,
                )
            except Exception as e:
                log(f"CLOSE failed: {e}. Position is OPEN. STOP THE BOT and close manually!")
                break
            log(f"<- {res2['kind']}: {json.dumps(res2['payload'])[:300]}")
            od2 = res2["payload"] if res2["kind"] == "order_update" else None
            close_size = (od2["fs"] / sd_div) if od2 and od2.get("fs") else 0
            close_price = (od2.get("fp", 0) / pd_div) if od2 and od2.get("fp") else 0
            close_notional = close_size * close_price
            close_fee = close_notional * (cfg.market["taker_fee_bps"] / 10000)
            if close_size < filled_size:
                log(f"WARNING: only closed {close_size}/{filled_size}. Manual cleanup needed.")
            # PnL: long = (close - entry) × size, short = (entry - close) × size
            if not close_size:
                rt_pnl = 0
            elif cfg.side == "long":
                rt_pnl = (close_price - filled_price) * close_size
            else:
                rt_pnl = (filled_price - close_price) * close_size
            rt_fees = open_fee + close_fee
            cumulative_pnl += rt_pnl
            cumulative_fees += rt_fees
            completed += 1
            log(f"RT {rt} done. pnl=${rt_pnl:+.4f}  fees=${rt_fees:.4f}  net=${rt_pnl - rt_fees:+.4f}")
            log(f"Cumulative: pnl=${cumulative_pnl:+.4f}  fees=${cumulative_fees:.4f}  net=${cumulative_pnl - cumulative_fees:+.4f}")

            if rt < cfg.max_round_trips and not stop:
                await asyncio.sleep(cfg.delay_between_rt_sec)
    finally:
        await sess.close()

    log("\n" + "=" * 64)
    log(f"FINAL: {completed} RTs completed")
    log(f"  Volume generated: ~${notional * 2 * completed:.2f}")
    log(f"  Fees paid:        ${cumulative_fees:.4f}")
    log(f"  Raw PnL:          ${cumulative_pnl:+.4f}")
    log(f"  Net:              ${cumulative_pnl - cumulative_fees:+.4f}")
    log("=" * 64)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--live", action="store_true", help="Actually trade. Without this flag, dry-run.")
    args = ap.parse_args()

    cfg = load_config(live=args.live)
    try:
        asyncio.run(run_round_trips(cfg))
    except KeyboardInterrupt:
        log("Interrupted.")


if __name__ == "__main__":
    main()
