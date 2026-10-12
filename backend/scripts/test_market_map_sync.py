"""Proof for services/market_map_sync.py: every branch, on real data.

The Perpl side is the live registry (Perpl public context). The Hyperliquid
side is the live allMids universe. Only the DB session is a fake, so the SQL
this would run is recorded instead of executed and nothing is written.

    venv/bin/python scripts/test_market_map_sync.py
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.services import market_map_sync          # noqa: E402
from app.services.hyperliquid import prices        # noqa: E402

checks = []


def check(name, got, want):
    ok = got == want
    checks.append(ok)
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}\n          got  {got}\n          want {want}")


class FakeResult:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows


class FakeSession:
    """Records every statement; answers the one SELECT from `rows`."""
    def __init__(self, rows):
        self.rows = rows
        self.writes = []
        self.commits = 0

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def execute(self, stmt, params=None):
        sql = " ".join(str(stmt).split())
        if sql.startswith("SELECT"):
            return FakeResult(self.rows)
        self.writes.append((sql.split(" SET ")[0].split(" (")[0], params))
        return None

    async def commit(self):
        self.commits += 1


def factory_for(rows):
    """Stands in for get_session_factory: calling it yields a factory, and
    calling THAT yields the session - the same two steps the real code takes."""
    session = FakeSession(rows)

    def get_session_factory():
        return lambda: session
    get_session_factory.session = session
    return get_session_factory


async def live_registry():
    from app.services import market_registry
    # refresh_markets() also calls the sync hook; call the fetch path directly
    # so this test exercises sync() on its own.
    from app.services.perpl_client import perpl_client
    raw = await perpl_client.get_markets()
    return [market_registry._normalize(m) for m in raw if m.get("id") is not None]


async def live_hl_universe():
    from app.services.hyperliquid import client as hl_client
    r = await hl_client.post_info({"type": "allMids"}, priority=hl_client.BACKGROUND, timeout=15.0)
    r.raise_for_status()
    mids = r.json()
    mids = mids.get("mids", mids)
    return {str(k).upper(): float(v) for k, v in mids.items()}


async def main():
    markets = await live_registry()
    active = sorted((m["symbol"], m["market_id"]) for m in markets if m["is_active"])
    print(f"Perpl registry (live): {len(active)} active markets")
    print(f"  {active}")

    hl = await live_hl_universe()
    prices.hl_price_cache.clear()
    prices.hl_price_cache.update(hl)
    print(f"Hyperliquid allMids (live): {len(hl)} symbols\n")

    both = [(s, i) for s, i in active if s in hl]
    print(f"On BOTH venues: {len(both)} -> {both}\n")

    # 1. empty map, HL primed: every shared market gets a row
    print("1. empty map, HL primed")
    f = factory_for([])
    market_map_sync.get_session_factory = f
    out = await market_map_sync.sync(markets)
    check("inserts one row per shared market",
          sorted(out["added"]), sorted(f"{s}:{i}" for s, i in both))
    check("repoints nothing", out["repointed"], [])
    check("unmaps nothing", out["unmapped"], [])
    check("every write is an INSERT",
          sorted({w[0] for w in f.session.writes}),
          ["INSERT INTO market_map"])

    # 2. correct map: no writes at all (safe to run every 60s)
    print("\n2. map already correct (idempotency)")
    f = factory_for([(s, i) for s, i in both])
    market_map_sync.get_session_factory = f
    out = await market_map_sync.sync(markets)
    check("no changes", {k: v for k, v in out.items() if v}, {})
    check("no SQL writes", f.session.writes, [])
    check("no commit", f.session.commits, 0)

    # 3. the exact bug: the August 5-market seed
    print("\n3. the August seed (the state that broke auto-copy)")
    seed = [("BTC", 1), ("ETH", 20), ("SOL", 31), ("HYPE", 40), ("ZEC", 50)]
    f = factory_for(seed)
    market_map_sync.get_session_factory = f
    out = await market_map_sync.sync(markets)
    check("adds exactly the six that were missing",
          sorted(out["added"]),
          sorted(f"{s}:{i}" for s, i in both if s not in dict(seed)))

    # 4. a relisted market under a new id gets repointed, not duplicated
    print("\n4. relist under a new market id")
    f = factory_for([("SOL", 30)] + [(s, i) for s, i in both if s != "SOL"])
    market_map_sync.get_session_factory = f
    out = await market_map_sync.sync(markets)
    check("repoints SOL to the live id", out["repointed"], ["SOL 30->31"])
    check("adds nothing", out["added"], [])
    check("one UPDATE only",
          [w[0] for w in f.session.writes], ["UPDATE market_map"])

    # 5. a delisted market is NULLed, never left pointing at a dead id
    print("\n5. Perpl delists a market")
    f = factory_for([(s, i) for s, i in both] + [("GONECOIN", 777)])
    market_map_sync.get_session_factory = f
    out = await market_map_sync.sync(markets)
    check("NULLs the stale row", out["unmapped"], ["GONECOIN was 777"])
    check("touches nothing else", (out["added"], out["repointed"]), ([], []))

    # 6. HL stream cold: a no-op, NEVER a wipe
    print("\n6. HL allMids cold (process just booted)")
    prices.hl_price_cache.clear()
    f = factory_for(seed)
    market_map_sync.get_session_factory = f
    out = await market_map_sync.sync(markets)
    check("skipped with a reason", out["skipped"], "HL allMids not primed (0 coins)")
    check("did not touch the map", f.session.writes, [])
    prices.hl_price_cache.update(hl)

    # 7. registry fetch failed / empty: also a no-op
    print("\n7. empty registry")
    f = factory_for(seed)
    market_map_sync.get_session_factory = f
    out = await market_map_sync.sync([])
    check("skipped", out["skipped"], "registry empty")
    check("did not touch the map", f.session.writes, [])

    # 8. a Perpl market Hyperliquid does not quote is never invented
    print("\n8. Perpl market with no HL coin")
    fake = markets + [{"symbol": "NOTONHL", "market_id": 9999, "is_active": True}]
    f = factory_for([(s, i) for s, i in both])
    market_map_sync.get_session_factory = f
    out = await market_map_sync.sync(fake)
    check("not mapped", [a for a in out["added"] if "NOTONHL" in a], [])
    check("no writes", f.session.writes, [])

    print(f"\n{len(checks)} checks, {sum(checks)} passed, {len(checks) - sum(checks)} FAILED")
    return 0 if all(checks) else 1


sys.exit(asyncio.run(main()))
