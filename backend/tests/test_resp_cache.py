"""Shared response cache (beta speed pass 2026-10-08): TTL, single flight,
stale-on-error, demand-driven warm, and the route_cache key normalization.
Run: python tests/test_resp_cache.py  (also collected by pytest)."""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.utils import resp_cache as R  # noqa: E402


def run(coro):
    return asyncio.run(coro)


def test_ttl_and_hit():
    c = R.RespCache("t1", ttl=60)
    calls = {"n": 0}

    async def build():
        calls["n"] += 1
        return {"v": calls["n"]}

    async def main():
        a = await c.get("k", build)
        b = await c.get("k", build)
        return a, b
    a, b = run(main())
    assert a == b == {"v": 1} and calls["n"] == 1, (a, b, calls)
    assert c.hits == 1 and c.misses == 1, c.stats()


def test_single_flight():
    c = R.RespCache("t2", ttl=60)
    calls = {"n": 0}

    async def build():
        calls["n"] += 1
        await asyncio.sleep(0.05)          # slow build, 20 concurrent callers
        return calls["n"]

    async def main():
        return await asyncio.gather(*[c.get("k", build) for _ in range(20)])
    out = run(main())
    assert calls["n"] == 1, calls          # ONE build for 20 requests
    assert out == [1] * 20, out
    assert c.coalesced == 19, c.stats()


def test_stale_on_error_then_raise_after_stale_max():
    c = R.RespCache("t3", ttl=0.01, stale_max=0.3, fail_backoff=0)
    state = {"ok": True}

    async def build():
        if not state["ok"]:
            raise RuntimeError("upstream down")
        return "good"

    async def main():
        first = await c.get("k", build)
        state["ok"] = False
        await asyncio.sleep(0.02)
        stale = await c.get("k", build)    # inside stale_max -> last good
        await asyncio.sleep(0.35)          # past stale_max -> must raise
        try:
            await c.get("k", build)
            return first, stale, None
        except RuntimeError as exc:
            return first, stale, str(exc)
    first, stale, err = run(main())
    assert first == "good" and stale == "good", (first, stale)
    assert err == "upstream down", err
    assert c.stale_served >= 1, c.stats()


def test_invalidate_forces_rebuild():
    c = R.RespCache("t4", ttl=600)
    n = {"i": 0}

    async def build():
        n["i"] += 1
        return n["i"]

    async def main():
        a = await c.get("k", build)
        c.invalidate("k")
        b = await c.get("k", build)
        return a, b
    a, b = run(main())
    assert (a, b) == (1, 2), (a, b)


def test_warm_refreshes_only_keys_in_active_demand():
    """Three keys: one polled since its build (warm it), one read once and never
    again (leave it), one nobody touched for 10 s (leave it). Without the
    read-since-build condition the warmer rebuilt everything every minute."""
    c = R.RespCache("t5", ttl=0.2, warm_window=5)
    n = {"polled": 0, "once": 0, "stale": 0}

    def mk(name):
        async def build():
            n[name] += 1
            return n[name]
        return build

    async def main():
        for k in ("polled", "once", "stale"):
            await c.get(k, mk(k))
        await asyncio.sleep(0.05)
        await c.get("polled", mk("polled"))   # poll WHILE still fresh -> a cache hit
        c._e["stale"].read -= 60              # nobody looked at it for a minute
        await asyncio.sleep(0.12)             # now past ttl/2 for everyone
        return await c.refresh_hot()
    done = run(main())
    assert done == 1, done
    assert n == {"polled": 2, "once": 1, "stale": 1}, n


def test_route_cache_key_matches_default_resolved_call():
    from fastapi import Query
    c = R.RespCache("t6", ttl=60)
    calls = []

    async def handler(asset: str, top: int = Query(400), flag: bool = Query(False)):
        calls.append((asset, top, flag))
        return {"asset": asset, "top": top}
    wrapped = R.route_cache(c, "detail")(handler)

    async def main():
        a = await wrapped(asset="BTC", top=400, flag=False)   # as FastAPI calls it
        b = await wrapped(asset="BTC")                        # as the warmer calls it
        d = await wrapped(asset="BTC", top=50, flag=False)    # different params
        return a, b, d
    a, b, d = run(main())
    assert a == b == {"asset": "BTC", "top": 400}, (a, b)
    assert len(calls) == 2, calls          # one build for the two equivalent calls
    assert d == {"asset": "BTC", "top": 50}


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("PASS", name)
    print("ALL RESP CACHE TESTS PASS")
