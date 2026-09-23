"""Basis-gate unit test (phase 5). Standalone: `python tests/test_basis_gate.py`.

Uses the REAL dev DB (creates + cleans an hl-signal subscription) and MOCKED
price caches (hl mid + perpl mark injected) so both the block and the pass
paths of live_orders._eval_risk are exercised deterministically.
"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import settings                       # noqa: E402
from app.db.database import init_db, get_session_factory  # noqa: E402


async def main():
    await init_db(settings.DATABASE_URL)
    from sqlalchemy import text
    from app.services.copy import live_orders, subscriptions as sub_service
    from app.services.hyperliquid import prices as hl_prices
    import app.services.ws_manager as wsm

    follower = "0x3333333333333333333333333333333333333333"
    trader = "0x4444444444444444444444444444444444444444"

    # clean any prior test rows
    sf = get_session_factory()
    async with sf() as s:
        await s.execute(text("DELETE FROM copy_subscriptions WHERE follower_wallet = :f"), {"f": follower})
        await s.commit()

    sub = await sub_service.create_subscription(
        follower_wallet=follower, trader_wallet=trader,
        exchange="hl", mode="live_manual", allocation_usd=50, max_leverage=5,
        max_basis_bps=20,
    )
    assert sub["exchange"] == "hl" and sub["max_basis_bps"] == 20, sub

    # Mock the two price sides for BTC (perpl market 1 <-> HL BTC via market_map)
    if wsm.ws_manager is None:
        wsm.ws_manager = wsm.WSManager()
    hl_prices._map_cache = (10**9, {1: "BTC"})   # skip DB lookup, far-future TTL

    async def eval_for(hl_mid, perpl_mark):
        hl_prices.hl_price_cache["BTC"] = hl_mid
        wsm.ws_manager.market_state_cache[1] = {"mark_price": perpl_mark}
        return await live_orders._eval_risk(follower, sub["id"], 1, 3.0, 50.0)

    # PASS path: basis = (64810-64800)/64800*1e4 = 1.54 bps <= 20
    ok, reason, snap = await eval_for(64810.0, 64800.0)
    print(f"pass-path: allowed={ok} reason={reason} basis={snap.get('basis_bps')}")
    assert ok and reason is None and abs(snap["basis_bps"] - 1.54) < 0.1

    # BLOCK path: basis = (65200-64800)/64800*1e4 = 61.7 bps > 20
    ok, reason, snap = await eval_for(65200.0, 64800.0)
    print(f"block-path: allowed={ok} reason={reason} basis={snap.get('basis_bps')}")
    assert not ok and reason == "basis_exceeded" and snap["basis_bps"] > 20

    # UNAVAILABLE path: no HL mid -> fail-closed
    hl_prices.hl_price_cache.pop("BTC", None)
    wsm.ws_manager.market_state_cache[1] = {"mark_price": 64800.0}
    ok, reason, snap = await live_orders._eval_risk(follower, sub["id"], 1, 3.0, 50.0)
    print(f"unavailable-path: allowed={ok} reason={reason}")
    assert not ok and reason == "basis_unavailable"

    # negative basis magnitude also gated: -61.7 bps
    ok, reason, snap = await eval_for(64400.0, 64800.0)
    print(f"neg-block-path: allowed={ok} reason={reason} basis={snap.get('basis_bps')}")
    assert not ok and reason == "basis_exceeded" and snap["basis_bps"] < -20

    # cleanup
    async with sf() as s:
        await s.execute(text("DELETE FROM copy_subscriptions WHERE follower_wallet = :f"), {"f": follower})
        await s.commit()
    print("ALL BASIS GATE TESTS PASS")


if __name__ == "__main__":
    asyncio.run(main())
