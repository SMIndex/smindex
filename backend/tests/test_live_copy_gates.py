"""LIVE copy execution v2 — gate-ladder harness (C4.1, LIVE_COPY_REPORT).

Standalone asyncio runner against the dev DB, real code paths, mocked signing:
NO real order is ever placed (that is architecturally impossible server-side —
orders are client-wallet-signed). Venue/chain lookups are monkeypatched.

Proves and PRINTS the copy_orders rows for:
  1. market open  -> submitted (order_type=market)
  2. limit open   -> submitted with TP/SL stored (order_type=limit)
  3. blocked-by-basis      (sub max_basis_bps=20, live basis=35)
  4. blocked-by-allowlist  (wallet not in LIVE_COPY_ALLOWLIST)
  5. TP registration       (filled + real trigger order ids recorded)
  6. close linked to open  (live position lifecycle open -> closed)
  7. invalid TP/SL side    -> blocked
  8. leverage above market max -> blocked
  9. resting limit 'placed' status (real order id, NO position row)

Seeds its own rows (idempotency prefix 'harness:') and deletes them at the end.
"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import settings                            # noqa: E402
from app.db.database import init_db, get_session_factory   # noqa: E402

FOLLOWER = "0x7e57c0de0000000000000000000000000000c0de"
OUTSIDER = "0x7e57c0de00000000000000000000000000000bad"
LEADER = "0x7e57c0de000000000000000000000000000f00d5"
PASS, FAIL = "PASS", "FAIL"
results: list[tuple[str, str]] = []


def check(name: str, ok: bool, detail: str = ""):
    results.append((PASS if ok else FAIL, name))
    print(f"  [{PASS if ok else FAIL}] {name}" + (f" — {detail}" if detail else ""))


def show(tag: str, o: dict):
    keys = ["id", "status", "skip_reason", "action", "order_type", "market_id", "side",
            "intended_size", "intended_price", "leverage", "allocation_usd",
            "tp_price", "sl_price", "tp_order_id", "sl_order_id",
            "perpl_order_id", "fill_price", "fill_size", "live_position_id"]
    print(f"  ROW[{tag}]: " + ", ".join(f"{k}={o.get(k)}" for k in keys if o.get(k) is not None))


async def main():
    await init_db(settings.DATABASE_URL)
    from sqlalchemy import text
    from app.services.copy import live_orders, live_positions
    from app.services import market_registry
    from app.services.hyperliquid import prices as hl_prices
    from app.db.copy_models import CopySubscription

    # ---- mocks: gate inputs only; NO signing, NO orders ----
    settings.COPY_LIVE_ENABLED = True
    settings.COPY_MODE = "live_manual"
    settings.LIVE_COPY_ALLOWLIST = [FOLLOWER]
    settings.COPY_REQUIRE_INTERNAL_ALLOWLIST = False

    async def fake_eligible(wallet: str) -> bool:
        return True
    live_orders._perpl_eligible = fake_eligible

    real_is_active = market_registry.is_market_active
    async def fake_active(mid: int) -> bool:
        return True
    market_registry.is_market_active = fake_active

    real_max_lev = live_orders.market_max_leverage
    async def fake_max_lev(mid: int) -> float:
        return 15.0
    live_orders.market_max_leverage = fake_max_lev

    real_basis = hl_prices.basis_bps_for_market
    async def fake_basis(mid: int):
        return 35.0
    hl_prices.basis_bps_for_market = fake_basis

    sf = get_session_factory()
    sub_id = None
    try:
        # seed an HL-signal subscription with a 20bps basis cap
        async with sf() as s:
            sub = CopySubscription(
                follower_wallet=FOLLOWER, trader_wallet=LEADER, exchange="hl",
                mode="live_manual", status="active", allocation_usd=50,
                max_leverage=10, max_basis_bps=20)
            s.add(sub)
            await s.commit()
            await s.refresh(sub)
            sub_id = sub.id

        # basis_cap_bps=0 = user cleared the ad-hoc basis guard (audit A7) —
        # keeps the pre-A7 semantics for the plain gate cases below (mocked
        # basis is 35 bps, which would trip the new 30 bps ad-hoc default).
        base = dict(follower_wallet=FOLLOWER, trader_wallet=LEADER, market_id=1,
                    symbol="BTC", side="long", leverage=2, allocation_usd=10,
                    basis_cap_bps=0)

        # 1. market open
        o1 = await live_orders.create_attempt(
            **base, intended_size=0.0002, intended_price=100000.0,
            idempotency_key="harness:mkt-open", order_type="market")
        show("market-open", o1)
        check("market open authorized (submitted)", o1["status"] == "submitted" and o1["order_type"] == "market")

        # 2. limit open with TP/SL
        o2 = await live_orders.create_attempt(
            **base, intended_size=0.0002, intended_price=99000.0,
            idempotency_key="harness:lim-open", order_type="limit",
            tp_price=110000.0, sl_price=95000.0)
        show("limit-open", o2)
        check("limit open authorized with TP/SL stored",
              o2["status"] == "submitted" and o2["order_type"] == "limit"
              and o2["tp_price"] == 110000.0 and o2["sl_price"] == 95000.0)

        # 3. blocked by basis (sub cap 20, live 35)
        o3 = await live_orders.create_attempt(
            **base, intended_size=0.0002, intended_price=100000.0,
            subscription_id=sub_id, idempotency_key="harness:basis-block")
        show("basis-block", o3)
        check("basis 35bps > cap 20bps blocked",
              o3["status"] == "risk_blocked" and o3["skip_reason"] == "basis_exceeded")

        # 4. blocked by allowlist (outsider wallet)
        o4 = await live_orders.create_attempt(
            **{**base, "follower_wallet": OUTSIDER}, intended_size=0.0002,
            intended_price=100000.0, idempotency_key="harness:allowlist-block")
        show("allowlist-block", o4)
        check("non-allowlisted wallet blocked",
              o4["status"] == "risk_blocked" and o4["skip_reason"] == "not_in_live_allowlist")

        # 5. TP registration: mocked fill result + real-shaped trigger ids
        o5 = await live_orders.update_result(
            o1["id"], FOLLOWER, status="filled",
            perpl_order_id="HARNESS-ORD-1", fill_price=100050.0, fill_size=0.0002,
            tp_order_id="HARNESS-TRG-TP", sl_order_id="HARNESS-TRG-SL")
        show("filled+triggers", o5)
        check("fill + TP/SL trigger ids recorded",
              o5["status"] == "filled" and o5["tp_order_id"] == "HARNESS-TRG-TP"
              and o5["sl_order_id"] == "HARNESS-TRG-SL")

        # 6. live position from the fill, then close linked to it
        pos = await live_positions.open_from_order(o5)
        print(f"  POSITION opened: id={pos['id']} status={pos['status']} entry={pos['entry_price']} size={pos['entry_size']}")
        check("live position created from confirmed fill", pos["status"] == "open")

        o6 = await live_orders.create_attempt(
            **base, intended_size=0.0002, intended_price=100500.0,
            idempotency_key=f"harness:close:pos:{pos['id']}:1",
            action="close", live_position_id=pos["id"])
        show("close-attempt", o6)
        check("close attempt authorized + linked",
              o6["status"] == "submitted" and o6["live_position_id"] == pos["id"])
        o6b = await live_orders.update_result(
            o6["id"], FOLLOWER, status="filled",
            perpl_order_id="HARNESS-ORD-2", fill_price=100500.0, fill_size=0.0002)
        pos2 = await live_positions.apply_close_result(
            pos["id"], FOLLOWER, action="close", close_price=100500.0, reason="manual")
        print(f"  POSITION closed: id={pos2['id']} status={pos2['status']} realized={pos2['realized_pnl']}")
        check("position lifecycle open->closed with realized PnL",
              pos2["status"] == "closed" and abs(pos2["realized_pnl"] - (100500.0 - 100050.0) * 0.0002) < 1e-9)

        # 7. invalid trigger side blocked (long with TP below entry)
        o7 = await live_orders.create_attempt(
            **base, intended_size=0.0002, intended_price=100000.0,
            idempotency_key="harness:bad-triggers", tp_price=90000.0)
        show("bad-triggers", o7)
        check("TP on wrong side blocked",
              o7["status"] == "risk_blocked" and o7["skip_reason"] == "invalid_trigger_prices")

        # 8. leverage above per-market max (mocked 15x) blocked
        o8 = await live_orders.create_attempt(
            **{**base, "leverage": 16}, intended_size=0.0002, intended_price=100000.0,
            idempotency_key="harness:lev-block")
        show("lev-block", o8)
        check("leverage 16x > market max 15x blocked",
              o8["status"] == "risk_blocked" and o8["skip_reason"] == "leverage_out_of_range")

        # 9b. audit A7: ad-hoc DEFAULT basis cap (30bps) blocks at mocked 35bps
        o9b = await live_orders.create_attempt(
            **{**base, "basis_cap_bps": None}, intended_size=0.0002,
            intended_price=100000.0, idempotency_key="harness:adhoc-basis-default")
        show("adhoc-basis-default", o9b)
        check("ad-hoc default 30bps cap blocks 35bps basis",
              o9b["status"] == "risk_blocked" and o9b["skip_reason"] == "basis_exceeded")

        # 9c. audit A7: explicit user cap 50bps passes 35bps basis
        o9c = await live_orders.create_attempt(
            **{**base, "basis_cap_bps": 50}, intended_size=0.0002,
            intended_price=100000.0, idempotency_key="harness:adhoc-basis-user50")
        check("ad-hoc user cap 50bps passes 35bps basis", o9c["status"] == "submitted")

        # 9d. audit A1: triggers_failed marker propagates to the position row
        o9d = await live_orders.create_attempt(
            **base, intended_size=0.0002, intended_price=100000.0,
            idempotency_key="harness:trigfail")
        o9d = await live_orders.update_result(
            o9d["id"], FOLLOWER, status="filled",
            perpl_order_id="HARNESS-ORD-TF", fill_price=100000.0, fill_size=0.0002)
        pos_tf = await live_positions.open_from_order(o9d, triggers_failed=True)
        print(f"  POSITION trigfail: id={pos_tf['id']} triggers_failed={pos_tf['triggers_failed']}")
        check("triggers_failed marker on position row", pos_tf["triggers_failed"] is True)

        # 9. resting limit 'placed' (real order id, no position)
        o9 = await live_orders.update_result(
            o2["id"], FOLLOWER, status="placed", perpl_order_id="HARNESS-ORD-3")
        show("resting-placed", o9)
        created = True
        try:
            await live_positions.open_from_order({**o9, "perpl_order_id": None, "perpl_fill_id": None})
        except ValueError:
            created = False
        check("resting limit recorded 'placed'; unconfirmed position refused",
              o9["status"] == "placed" and not created)

    finally:
        # restore mocks + delete exactly our rows
        market_registry.is_market_active = real_is_active
        live_orders.market_max_leverage = real_max_lev
        hl_prices.basis_bps_for_market = real_basis
        async with sf() as s:
            await s.execute(text("DELETE FROM copy_live_positions WHERE follower_wallet = :w"), {"w": FOLLOWER})
            await s.execute(text("DELETE FROM copy_orders WHERE idempotency_key LIKE 'harness:%'"))
            await s.execute(text("DELETE FROM risk_events WHERE follower_wallet IN (:w1, :w2)"), {"w1": FOLLOWER, "w2": OUTSIDER})
            await s.execute(text("DELETE FROM audit_logs WHERE actor_wallet IN (:w1, :w2)"), {"w1": FOLLOWER, "w2": OUTSIDER})
            if sub_id:
                await s.execute(text("DELETE FROM copy_subscriptions WHERE id = :i"), {"i": sub_id})
            await s.commit()

    print()
    fails = [n for r, n in results if r == FAIL]
    print(f"{len(results) - len(fails)}/{len(results)} PASS" + (f" — FAILURES: {fails}" if fails else ""))
    return 0 if not fails else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
