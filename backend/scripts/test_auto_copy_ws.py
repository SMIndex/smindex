"""Auto-copy venue-socket behaviour, against a fake Perpl socket.

Covers the three defects found on the owner's live account on 2026-10-09/10:

  1. A confirmation timeout was retried on a NEW socket with a NEW rq, so an
     order that had in fact filled was placed twice (02:05, a reduce).
  2. sr:32 (rq not above the venue's last request) was retried with rq+1 and
     the venue's own number (mt:19 as[].lfr) was never read, so a session could
     not recover and every order failed (22:19 - which paused the sub).
  3. A bare TimeoutError() printed as '' ("order failed: " and nothing else).

No network, no database, no key: the socket is a scripted fake and the frames
have the shapes captured from the venue.

    venv/bin/python scripts/test_auto_copy_ws.py
"""
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services.copy_auto import executor, perpl_ws          # noqa: E402
from app.services.copy_auto.perpl_ws import OrderRejected, PerplTrader, Unconfirmed  # noqa: E402

ACC = 161
results = []


def check(name, cond, detail=""):
    results.append(bool(cond))
    print(f"[{'PASS' if cond else 'FAIL'}] {name} {detail}")


class FakeWS:
    """Scripted venue: `on_order(order) -> list of frames to deliver` (or None
    for silence)."""
    def __init__(self, on_order):
        self.on_order = on_order
        self.sent = []
        self.q = asyncio.Queue()
        self.closed = False

    async def send(self, raw):
        o = json.loads(raw)
        self.sent.append(o)
        for f in (self.on_order(o) or []):
            await self.q.put(json.dumps(f))

    def __aiter__(self):
        return self

    async def __anext__(self):
        raw = await self.q.get()
        if raw is None:
            raise ConnectionError("fake socket dropped")
        return raw

    async def close(self):
        self.closed = True


def trader(on_order, rq=1000) -> tuple[PerplTrader, FakeWS]:
    t = PerplTrader("k", b"\0" * 32, ACC)
    ws = FakeWS(on_order)
    t.ws = ws
    t.rq = rq
    t.block, t.block_at = 5000, perpl_ws.time.monotonic() + 3600   # heartbeat always fresh
    t._reader = asyncio.get_event_loop().create_task(t._read())
    return t, ws


def filled(o, fs=None):
    return [{"mt": 24, "d": [{"rq": o["rq"], "oid": 777, "st": 4, "fs": fs or o["s"], "fp": o["p"]}]}]


def sr32(o):
    return [{"mt": 24, "d": [{"rq": o["rq"], "st": 7, "sr": 32}]}]


KW = dict(market_id=90, side="long", size=4.0, mark=0.0055, size_decimals=0,
          price_decimals=6, slippage_bps=100, ttl_blocks=6)


async def main():
    # --- 1. the venue's lfr is read from where the venue actually sends it ---
    t, ws = trader(lambda o: filled(o))
    t._bump({"mt": 19, "as": [{"id": ACC, "b": 58830000, "lfr": 9_000_000}], "sts": []})
    check("mt:19 as[].lfr is picked up", t.lfr == 9_000_000 and t.rq == 9_000_000, f"lfr={t.lfr} rq={t.rq}")
    t._bump({"mt": 19, "as": [{"id": 999, "lfr": 99_000_000}]})
    check("another account's lfr is ignored", t.lfr == 9_000_000)
    t._bump({"mt": 19, "as": [{"id": ACC, "lfr": 5}]})
    check("a lower lfr never moves rq backwards", t.rq == 9_000_000)
    r = await t.market_close(**KW)
    check("first order after the snapshot goes out above the venue's lfr",
          ws.sent[0]["rq"] == 9_000_001 and r.get("filled"), f"rq={ws.sent[0]['rq']}")

    # --- 2. sr:32 recovery clears the venue's number, not just ours by one ---
    venue = {"lfr": 50_000}

    def raced(o):
        if o["rq"] <= venue["lfr"]:
            # the venue tells us where it is, then rejects
            return [{"mt": 19, "as": [{"id": ACC, "lfr": venue["lfr"]}]}] + sr32(o)
        return filled(o)
    t, ws = trader(raced, rq=1000)
    real_ns = perpl_ws.time.time_ns
    perpl_ws.time.time_ns = lambda: 2000 * 1000          # clock-seeded value is ALSO below the venue
    try:
        r = await t.market_close(**KW)
    finally:
        perpl_ws.time.time_ns = real_ns
    check("sr:32 with the venue far ahead: the retry fills",
          r.get("filled") and len(ws.sent) == 2, f"rqs={[o['rq'] for o in ws.sent]}")
    check("the retry rq is above the venue's lfr (old code sent rq+1 = 1002)",
          ws.sent[1]["rq"] > venue["lfr"], f"{ws.sent[1]['rq']} > {venue['lfr']}")

    t, ws = trader(lambda o: sr32(o), rq=1000)
    try:
        await t.market_close(**KW)
        check("sr:32 twice is still a clean rejection", False)
    except OrderRejected as exc:
        check("sr:32 twice is still a clean rejection (nothing can have filled)",
              "sr:32" in str(exc) and len(ws.sent) == 2, str(exc))

    # --- 3. no confirmation: same rq once, then Unconfirmed - never a new rq ---
    t, ws = trader(lambda o: None, rq=1000)
    orig = t._send
    t._send = lambda order, match, tag, timeout=15.0: orig(order, match, tag, timeout=0.05)
    try:
        await t.market_close(**KW)
        check("silent venue raises Unconfirmed", False)
    except Unconfirmed as exc:
        check("silent venue raises Unconfirmed", True, str(exc))
    except Exception as exc:
        check("silent venue raises Unconfirmed", False, f"got {type(exc).__name__}: {exc}")
    check("the re-send used the SAME rq (at most once on the venue)",
          [o["rq"] for o in ws.sent] == [1001, 1001], f"{[o['rq'] for o in ws.sent]}")
    check("Unconfirmed is not an OSError/ConnectionError (those get retried on a new socket)",
          not issubclass(Unconfirmed, (OSError, ConnectionError)))

    # socket dies after the send
    def drop(o):
        return [None]
    t, ws = trader(drop, rq=1000)
    try:
        await t.market_close(**KW)
        check("socket dropping after the send raises Unconfirmed", False)
    except Unconfirmed as exc:
        check("socket dropping after the send raises Unconfirmed", True, str(exc)[:80])
    except Exception as exc:
        check("socket dropping after the send raises Unconfirmed", False, f"{type(exc).__name__}: {exc}")

    # --- 4. the executor never re-sends an order that may have filled ---
    made = []

    class StubTrader:
        ws = object()

        def __init__(self, behaviour):
            self.behaviour = behaviour
            self._reader = asyncio.get_event_loop().create_future()   # "not done" = alive
            self.calls = 0
            self.closed = False

        async def market_close(self, **kw):
            self.calls += 1
            return await self.behaviour(self)

        async def close(self):
            self.closed = True

    async def no_mark(*a, **k):
        return None
    executor.keys.mark = no_mark

    def install(behaviours):
        made.clear()
        executor._traders.clear()
        it = iter(behaviours)

        async def fake_trader(user_id, wallet):
            t = executor._traders.get(user_id)
            if t is not None:
                return t
            t = StubTrader(next(it))
            made.append(t)
            executor._traders[user_id] = t
            return t
        executor._trader = fake_trader

    async def unconfirmed(t):
        raise Unconfirmed("[auto-close]: sent, no confirmation from Perpl (rq 1)")

    async def ok(t):
        return {"filled": True, "fs": 4, "fp": 5500}

    async def not_sent(t):
        raise TimeoutError("no Perpl heartbeat (block) within 5s")

    install([unconfirmed, ok])
    try:
        await executor._call(1, "0xw", "market_close")
        check("executor does not swallow Unconfirmed", False)
    except Unconfirmed:
        check("executor raises Unconfirmed to the engine", True)
    check("...and did NOT place the order again on a new socket (this was the double fill)",
          len(made) == 1 and made[0].calls == 1, f"sockets={len(made)} sends={sum(t.calls for t in made)}")
    check("...and dropped the socket so the next order starts clean",
          made[0].closed and 1 not in executor._traders)

    install([not_sent, ok])
    r = await executor._call(1, "0xw", "market_close")
    check("a failure BEFORE anything was sent is still retried once (safe)",
          r.get("filled") and len(made) == 2, f"sockets={len(made)}")

    # --- 5. a failure reason is never blank ---
    check("bare TimeoutError() has a reason", executor.why(TimeoutError()) == "TimeoutError", repr(executor.why(TimeoutError())))
    check("a real message is kept", executor.why(OrderRejected("rq rejected twice (sr:32)")) == "rq rejected twice (sr:32)")

    print(f"\n{len(results)} checks, {sum(results)} passed, {len(results) - sum(results)} FAILED")
    return 0 if all(results) else 1


sys.exit(asyncio.run(main()))
