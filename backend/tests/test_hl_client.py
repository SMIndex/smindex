"""Shared HL client unit tests (Wallet Explorer Part 1). Standalone:
`python tests/test_hl_client.py`.

Covers, with a mocked transport (no venue traffic):
  1. weight table — base weights 2/20/60 per the research-doc contract
  2. per-item extras — 1 per 20 items (userFillsByTime et al.), 1 per 60
     candles (candleSnapshot), measured from the actual response and added
     to the rolling window
  3. rolling-window accounting + priority shedding — explorer sheds first
     (HLWeightShed) while background waits and critical is never shed
  4. 429 handling — Retry-After honored via the per-class circuit breaker
     (opened for the observed class and every lower class), exponential
     backoff when the header is absent, reset on success; the 429 response
     itself is returned to the caller (module discipline preserved)
"""
import asyncio
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import httpx  # noqa: E402

from app.services.hyperliquid import client as hl  # noqa: E402


def _fresh_state():
    hl._window = hl._Window()
    hl._breaker = {c: {"until": 0.0, "n": 0} for c in hl._CLASS_ORDER}
    for c in hl._CLASS_ORDER:
        hl._stats[c] = {"requests": 0, "weight": 0.0, "shed": 0,
                        "http_429": 0, "wait_timeouts": 0}


def _mock(handler):
    hl._client = httpx.AsyncClient(transport=httpx.MockTransport(handler),
                                   timeout=5.0)


async def test_weight_table():
    _fresh_state()
    assert hl.base_weight("clearinghouseState") == 2
    assert hl.base_weight("spotClearinghouseState") == 2
    assert hl.base_weight("l2Book") == 2
    assert hl.base_weight("allMids") == 2
    assert hl.base_weight("orderStatus") == 2
    assert hl.base_weight("exchangeStatus") == 2
    assert hl.base_weight("userRole") == 60
    assert hl.base_weight("portfolio") == 20
    assert hl.base_weight("frontendOpenOrders") == 20
    assert hl.base_weight("metaAndAssetCtxs") == 20
    assert hl.base_weight("userFillsByTime") == 20
    # per-item extras: 1 per 20 items (ceil, conservative)
    assert hl.extra_weight("userFillsByTime", 2000) == 100
    assert hl.extra_weight("userFills", 19) == 1
    assert hl.extra_weight("userFills", 20) == 1
    assert hl.extra_weight("userFills", 21) == 2
    assert hl.extra_weight("userFunding", 500) == 25
    assert hl.extra_weight("historicalOrders", 0) == 0
    assert hl.extra_weight("userNonFundingLedgerUpdates", 40) == 2
    # candleSnapshot: 1 per 60 candles
    assert hl.extra_weight("candleSnapshot", 60) == 1
    assert hl.extra_weight("candleSnapshot", 61) == 2
    assert hl.extra_weight("candleSnapshot", 5000) == 84
    # fixed-weight types never get extras
    assert hl.extra_weight("clearinghouseState", 500) == 0
    print("PASS weight table (base 2/20/60, per-20 extras, candles per-60)")


async def test_extra_weight_from_response():
    _fresh_state()

    def handler(request):
        payload = json.loads(request.content)
        if payload["type"] == "userFillsByTime":
            return httpx.Response(200, json=[{"px": "1"}] * 100)
        return httpx.Response(200, json={})

    _mock(handler)
    r = await hl.post_info({"type": "userFillsByTime", "user": "0xabc"},
                           priority=hl.BACKGROUND)
    assert r.status_code == 200
    # 20 base + ceil(100/20)=5 extra in the window and the class counter
    used = hl._window.used()
    assert abs(used - 25) < 0.01, used
    assert abs(hl._stats[hl.BACKGROUND]["weight"] - 25) < 0.01
    print("PASS response-measured extra weight (100 fills -> 20+5)")


async def test_priority_shedding():
    _fresh_state()
    _mock(lambda req: httpx.Response(200, json={}))
    old_wait = dict(hl.MAX_WAIT_SEC)
    hl.MAX_WAIT_SEC.update({hl.CRITICAL: 0.3, hl.BACKGROUND: 0.3, hl.EXPLORER: 0.2})
    try:
        # fill the window right up to the explorer ceiling (soft 1000 - 150)
        hl._window.add(hl._class_ceiling(hl.EXPLORER) - 1)
        # explorer: 2-weight request no longer fits -> shed
        try:
            await hl.post_info({"type": "clearinghouseState", "user": "0x1"},
                               priority=hl.EXPLORER)
            raise AssertionError("explorer request was not shed")
        except hl.HLWeightShed:
            pass
        assert hl._stats[hl.EXPLORER]["shed"] == 1
        # background: same window state still fits under the soft ceiling
        r = await hl.post_info({"type": "clearinghouseState", "user": "0x2"},
                               priority=hl.BACKGROUND)
        assert r.status_code == 200
        # fill to the soft ceiling: background now times out (shed for the
        # caller to treat as a failed fetch)...
        hl._window.add(hl._soft_ceiling() - hl._window.used())
        try:
            await hl.post_info({"type": "clearinghouseState", "user": "0x3"},
                               priority=hl.BACKGROUND)
            raise AssertionError("background request admitted over soft ceiling")
        except hl.HLWeightShed:
            pass
        # ...but critical still goes through (borrows the 1000..1200 headroom)
        r = await hl.post_info({"type": "clearinghouseState", "user": "0x4"},
                               priority=hl.CRITICAL)
        assert r.status_code == 200
        assert hl._stats[hl.CRITICAL]["requests"] == 1
    finally:
        hl.MAX_WAIT_SEC.update(old_wait)
    print("PASS priority shedding (explorer first, background at soft ceiling, "
          "critical never)")


async def test_429_handling():
    _fresh_state()
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(429, headers={"Retry-After": "3"}, text="rate limited")
        return httpx.Response(200, json={})

    _mock(handler)
    old_wait = dict(hl.MAX_WAIT_SEC)
    hl.MAX_WAIT_SEC.update({hl.CRITICAL: 0.3, hl.BACKGROUND: 0.3, hl.EXPLORER: 0.2})
    try:
        # background 429: response returned untouched (module discipline keeps
        # working), breaker opened for background AND explorer with Retry-After
        r = await hl.post_info({"type": "clearinghouseState", "user": "0x1"},
                               priority=hl.BACKGROUND)
        assert r.status_code == 429
        assert hl._stats[hl.BACKGROUND]["http_429"] == 1
        now = time.monotonic()
        assert 2.0 < hl._breaker[hl.BACKGROUND]["until"] - now <= 3.1
        assert 2.0 < hl._breaker[hl.EXPLORER]["until"] - now <= 3.1
        assert hl._breaker[hl.CRITICAL]["until"] == 0.0  # critical unaffected
        # explorer sheds instantly while its breaker is open
        try:
            await hl.post_info({"type": "clearinghouseState", "user": "0x2"},
                               priority=hl.EXPLORER)
            raise AssertionError("explorer not shed during open breaker")
        except hl.HLWeightShed:
            pass
        # critical proceeds immediately (breaker not opened upward)
        r = await hl.post_info({"type": "clearinghouseState", "user": "0x3"},
                               priority=hl.CRITICAL)
        assert r.status_code == 200
        assert hl._breaker[hl.CRITICAL]["n"] == 0  # success resets

        # no Retry-After header: exponential default, growing per consecutive 429
        _fresh_state()
        _mock(lambda req: httpx.Response(429, text="rate limited"))
        r = await hl.post_info({"type": "clearinghouseState", "user": "0x4"},
                               priority=hl.CRITICAL)
        assert r.status_code == 429
        now = time.monotonic()
        # critical breaker capped short (10s base within cap)
        assert 0 < hl._breaker[hl.CRITICAL]["until"] - now <= hl.BREAKER_MAX_SEC[hl.CRITICAL] + 0.1
        # lower classes opened too (per-IP limit affects everyone below)
        assert hl._breaker[hl.BACKGROUND]["until"] > now
        assert hl._breaker[hl.EXPLORER]["until"] > now
        n1 = hl._breaker[hl.EXPLORER]["n"]
        hl._breaker[hl.CRITICAL]["until"] = 0.0   # let the next call through
        r = await hl.post_info({"type": "clearinghouseState", "user": "0x5"},
                               priority=hl.CRITICAL)
        assert hl._breaker[hl.EXPLORER]["n"] == n1 + 1  # backoff grows
    finally:
        hl.MAX_WAIT_SEC.update(old_wait)
    print("PASS 429 handling (Retry-After breaker, class cascade, exponential "
          "default, success reset, response passthrough)")


async def test_metrics_shape():
    _fresh_state()
    _mock(lambda req: httpx.Response(200, json={}))
    await hl.post_info({"type": "userRole", "user": "0x1"}, priority=hl.EXPLORER)
    m = hl.get_metrics()
    assert m["soft_ceiling"] == 1000 and m["hard_ceiling"] == 1200
    assert m["explorer_ceiling"] == 850
    assert abs(m["window_weight_used"] - 60) < 0.01
    assert m["per_class"][hl.EXPLORER]["requests"] == 1
    assert m["per_class"][hl.EXPLORER]["weight"] == 60
    for c in (hl.CRITICAL, hl.BACKGROUND, hl.EXPLORER):
        assert {"requests", "weight", "shed", "http_429", "wait_timeouts",
                "breaker_open_sec", "consecutive_429"} <= set(m["per_class"][c])
    print("PASS metrics shape (/health payload)")


async def main():
    await test_weight_table()
    await test_extra_weight_from_response()
    await test_priority_shedding()
    await test_429_handling()
    await test_metrics_shape()
    await hl.aclose()
    print("ALL PASS (5/5)")


if __name__ == "__main__":
    asyncio.run(main())
