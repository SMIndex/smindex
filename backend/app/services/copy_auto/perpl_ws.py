"""Minimal Perpl trading-WebSocket client for auto-copy (server side).

Mirrors the browser's proven protocol in frontend/src/lib/perplTrading.ts and
perplApiKey.ts, field for field:
  * sign-in  mt:29 {chain_id, api_key, timestamp(ms str), nonce(16B b64url),
             signature = b64url(ed25519(seed, "143\\ntrading-ws-signin\\n<ts>\\n<nonce>"))}
             — authenticated on the first non-heartbeat frame; 3401/error = rejected.
  * blocks   mt:100 heartbeats carry the head block in `sn`; orders use
             lb = fresh block + orderTtlBlocks (market config, default 6).
  * rq       strictly increasing, seeded from time.time_ns() and bumped from the
             server's `lfr`; a timeout re-sends the SAME rq once (Perpl executes
             at most once per rq); sr:32 (rq too low) retries once with a new rq.
  * orders   mt:22 open  {t 1|2, p = mark*(1±ms/1e4), s = floor(size*10^sd), fl 4 IOC, lv, lb, ms}
             close {t 3|4, fl 4, lv 0, ms}; trigger SL {t 3|4, fl 0, lb 0, tp, tpc 3|4 (mark),
             p = trigger*(1∓5%)}; cancel {t 5, oid}.
The API key can only trade/cancel — withdrawals need a wallet signature on the
Exchange contract (docs: "never permitted via an API key, regardless of scope").
"""
import asyncio
import base64
import json
import os
import time

import websockets
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from app.config import settings
from app.utils.logger import get_logger

logger = get_logger(__name__)

CHAIN_ID = 143
ST_TERMINAL_REJECT = {5, 6, 7}
SLTP_EXEC_BOUND_PCT = 5.0       # same as the browser's placeSlTpOrder


def _b64url(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).decode().rstrip("=")


def signin_frame(api_key: str, seed: bytes, now_ms: int | None = None, nonce: str | None = None) -> dict:
    ts = str(now_ms if now_ms is not None else int(time.time() * 1000))
    nonce = nonce or _b64url(os.urandom(16))
    canonical = "\n".join([str(CHAIN_ID), "trading-ws-signin", ts, nonce]).encode()
    sig = Ed25519PrivateKey.from_private_bytes(seed).sign(canonical)
    return {"mt": 29, "chain_id": CHAIN_ID, "api_key": api_key, "timestamp": ts,
            "nonce": nonce, "signature": _b64url(sig)}


class OrderRejected(Exception):
    pass


class PerplTrader:
    def __init__(self, api_key: str, seed: bytes, account_id: int):
        self.api_key, self.seed, self.account_id = api_key, seed, int(account_id)
        self.ws = None
        self.block = 0
        self.block_at = 0.0
        self.rq = time.time_ns() // 1000
        self._waiters: list[tuple] = []     # (predicate, future)
        self._reader: asyncio.Task | None = None

    async def connect(self, timeout: float = 10.0) -> None:
        url = f"{settings.PERPL_WS_URL}/trading"
        self.ws = await websockets.connect(url, open_timeout=timeout, ping_interval=25)
        await self.ws.send(json.dumps(signin_frame(self.api_key, self.seed)))
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            raw = await asyncio.wait_for(self.ws.recv(), timeout=max(0.1, deadline - time.monotonic()))
            msg = json.loads(raw)
            if msg.get("error") or msg.get("code") == 3401:
                raise OrderRejected(f"Perpl sign-in rejected: {msg.get('error') or msg.get('code')}")
            self._bump(msg)
            if msg.get("mt") == 100:
                continue
            if msg.get("mt"):
                self._reader = asyncio.get_event_loop().create_task(self._read())
                return
        raise TimeoutError("Perpl sign-in timeout")

    async def close(self) -> None:
        if self._reader:
            self._reader.cancel()
        if self.ws:
            try:
                await self.ws.close()
            except Exception:
                pass

    def _bump(self, msg: dict) -> None:
        if msg.get("mt") == 100 and msg.get("sn"):
            self.block, self.block_at = int(msg["sn"]), time.monotonic()
        d = msg.get("d") if isinstance(msg.get("d"), dict) else {}
        lfr = msg.get("lfr") or d.get("lfr")
        if isinstance(lfr, int) and lfr >= self.rq:
            self.rq = lfr

    async def _read(self) -> None:
        try:
            async for raw in self.ws:
                try:
                    msg = json.loads(raw)
                except ValueError:
                    continue
                self._bump(msg)
                for pred, fut in list(self._waiters):
                    if fut.done():
                        continue
                    try:
                        r = pred(msg)
                    except Exception as exc:
                        fut.set_exception(exc)
                        continue
                    if r is not None:
                        fut.set_result(r)
        except Exception as exc:
            for _, fut in self._waiters:
                if not fut.done():
                    fut.set_exception(ConnectionError(f"Perpl WS closed: {exc}"))

    async def fresh_block(self) -> int:
        for _ in range(50):
            if self.block and time.monotonic() - self.block_at < 1.2:
                return self.block
            await asyncio.sleep(0.1)
        raise TimeoutError("no Perpl heartbeat (block) within 5s")

    async def _send(self, order: dict, match, tag: str, timeout: float = 15.0):
        """At-most-once: same rq re-sent once on timeout; sr:32 retried once with a new rq."""
        loop = asyncio.get_event_loop()
        retried = sr32 = False
        while True:
            fut = loop.create_future()

            def pred(msg, rq=order["rq"]):
                if msg.get("mt") == 3 and (msg.get("status") or {}).get("code", 0) >= 400:
                    raise OrderRejected((msg["status"].get("error") or f"Perpl error {msg['status'].get('code')}"))
                if msg.get("mt") != 24 or not isinstance(msg.get("d"), list):
                    return None
                for o in msg["d"]:
                    if o.get("rq") == rq and o.get("st") == 7 and o.get("sr") == 32:
                        return {"_sr32": True}
                    r = match(o, rq)
                    if r is not None:
                        return r
                return None
            entry = (pred, fut)
            self._waiters.append(entry)
            try:
                logger.info("%s sending %s", tag, {k: v for k, v in order.items() if k != "acc"})
                await self.ws.send(json.dumps(order))
                r = await asyncio.wait_for(asyncio.shield(fut), timeout=timeout)
            except asyncio.TimeoutError:
                if not retried:
                    retried = True
                    continue
                raise TimeoutError(f"{tag}: no confirmation from Perpl (rq {order['rq']})")
            finally:
                self._waiters.remove(entry)
            if r.get("_sr32") and not sr32:
                sr32 = True
                self.rq += 1
                order["rq"] = self.rq
                continue
            if r.get("_sr32"):
                raise OrderRejected("rq rejected twice (sr:32)")
            return r

    async def market_open(self, *, market_id: int, side: str, size: float, leverage: float,
                          mark: float, size_decimals: int, price_decimals: int,
                          slippage_bps: int, ttl_blocks: int) -> dict:
        block = await self.fresh_block()
        self.rq += 1
        mult = 1 + slippage_bps / 1e4 if side == "long" else 1 - slippage_bps / 1e4
        order = {"mt": 22, "rq": self.rq, "mkt": market_id, "acc": self.account_id,
                 "t": 1 if side == "long" else 2,
                 "p": round(mark * mult * 10 ** price_decimals),
                 "s": int(size * 10 ** size_decimals + 1e-9), "fl": 4,
                 "lv": round(leverage * 100), "lb": block + ttl_blocks, "ms": slippage_bps}
        return await self._send(order, _fill_matcher, "[auto-open]")

    async def market_close(self, *, market_id: int, side: str, size: float, mark: float,
                           size_decimals: int, price_decimals: int, slippage_bps: int,
                           ttl_blocks: int) -> dict:
        block = await self.fresh_block()
        self.rq += 1
        mult = 1 - slippage_bps / 1e4 if side == "long" else 1 + slippage_bps / 1e4
        order = {"mt": 22, "rq": self.rq, "mkt": market_id, "acc": self.account_id,
                 "t": 3 if side == "long" else 4,
                 "p": round(mark * mult * 10 ** price_decimals),
                 "s": round(size * 10 ** size_decimals), "fl": 4, "lv": 0,
                 "lb": block + ttl_blocks, "ms": slippage_bps}
        return await self._send(order, _fill_matcher, "[auto-close]")

    async def stop_loss(self, *, market_id: int, side: str, size: float, trigger: float,
                        size_decimals: int, price_decimals: int) -> dict:
        self.rq += 1
        bound = SLTP_EXEC_BOUND_PCT / 100
        order = {"mt": 22, "rq": self.rq, "mkt": market_id, "acc": self.account_id,
                 "t": 3 if side == "long" else 4,
                 "p": round(trigger * ((1 - bound) if side == "long" else (1 + bound)) * 10 ** price_decimals),
                 "s": round(size * 10 ** size_decimals), "fl": 0, "lv": 0, "lb": 0,
                 "tp": round(trigger * 10 ** price_decimals),
                 "tpc": 4 if side == "long" else 3}      # LTEMark : GTEMark

        def match(o, rq):
            if o.get("rq") != rq:
                return None
            if (o.get("st") in ST_TERMINAL_REJECT) or o.get("r"):
                raise OrderRejected(f"SL rejected (sr {o.get('sr')})")
            return {"oid": o.get("oid"), "st": o.get("st")}
        return await self._send(order, match, "[auto-sl]")

    async def cancel(self, *, market_id: int, oid) -> dict:
        block = await self.fresh_block()
        self.rq += 1
        order = {"mt": 22, "rq": self.rq, "mkt": market_id, "acc": self.account_id,
                 "oid": oid, "t": 5, "s": 0, "fl": 0, "lv": 0, "lb": block + 6}

        def match(o, rq):
            if o.get("oid") != oid:
                return None
            if o.get("st") == 5 or o.get("r"):
                return {"canceled": True}
            if o.get("st") in (6, 7):
                raise OrderRejected(f"cancel rejected (sr {o.get('sr')})")
            return None
        return await self._send(order, match, "[auto-cancel]")


def _fill_matcher(o: dict, rq: int):
    """Same classification as the browser: st 4/10 or fs>0 = filled;
    st 5/6/7 = rejected (an IOC that could not fill inside the bound)."""
    if o.get("rq") != rq:
        return None
    if o.get("st") in (4, 10) or (o.get("fs") or 0) > 0:
        return {"filled": True, "oid": o.get("oid"), "fs": o.get("fs") or 0, "fp": o.get("fp") or 0}
    if o.get("st") in ST_TERMINAL_REJECT:
        raise OrderRejected(f"order rejected (st {o.get('st')}, sr {o.get('sr')})")
    return None
