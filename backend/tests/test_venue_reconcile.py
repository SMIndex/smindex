"""Venue-truth reconciliation sweep harness (audit A3/A4, remediation Part D).

Standalone asyncio runner, dev DB, real sweep code path with the CHAIN READ
mocked (the venue-truth source — mocking it lets us fabricate drift):
  1. fabricated open copy position the venue does NOT hold -> HARD mismatch
  2. venue holds it exactly -> clean run, no mismatch
  3. expired-as-never-placed attempt while the venue holds an unaccounted
     position -> review flag
Seeds its own rows and deletes exactly those rows at the end.
"""
import asyncio
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import settings                            # noqa: E402
from app.db.database import init_db, get_session_factory   # noqa: E402

WALLET = "0x7e57feca0000000000000000000000000000fec4"
PASS, FAIL = "PASS", "FAIL"
results: list[tuple[str, str]] = []


def check(name, ok, detail=""):
    results.append((PASS if ok else FAIL, name))
    print(f"  [{PASS if ok else FAIL}] {name}" + (f" — {detail}" if detail else ""))


async def main():
    await init_db(settings.DATABASE_URL)
    from sqlalchemy import text
    from app.services.copy import venue_reconcile
    from app.db.copy_models import CopyLivePosition, CopyOrder

    settings.LIVE_COPY_ALLOWLIST = [WALLET]

    flagged: list[str] = []
    async def fake_flag(wallet, message, detail):
        flagged.append(message)
        print("  FLAG:", message[:110])
    real_flag = venue_reconcile._flag
    venue_reconcile._flag = fake_flag

    venue_state = {"positions": []}   # what the mocked chain read returns
    async def fake_venue(wallet):
        return venue_state
    real_venue = venue_reconcile._venue_positions
    venue_reconcile._venue_positions = fake_venue

    sf = get_session_factory()
    pos_id = ord_id = None
    try:
        async with sf() as s:
            p = CopyLivePosition(
                copy_order_id=None, follower_wallet=WALLET, trader_wallet=WALLET,
                follower_market_id=1, symbol="BTC", side="long", status="open",
                entry_price=100000, entry_size=0.005, entry_margin=50,
                entry_leverage=10, current_size=0.005,
                opened_at=datetime.utcnow(), updated_at=datetime.utcnow())
            s.add(p)
            o = CopyOrder(
                follower_wallet=WALLET, trader_wallet=WALLET, mode="live_manual",
                market_id=20, symbol="ETH", side="long", status="failed",
                action="open", error_message="expired: no result reported within 15 min",
                idempotency_key="harness:reconcile:expired",
                created_at=datetime.utcnow())
            s.add(o)
            await s.commit()
            await s.refresh(p); await s.refresh(o)
            pos_id, ord_id = p.id, o.id

        # 1. venue holds NOTHING -> fabricated position must be flagged
        flagged.clear()
        r1 = await venue_reconcile.run_once()
        check("fabricated position flagged (venue holds nothing)",
              any("claim long 0.005" in m and "no position" in m for m in flagged),
              f"mismatches={r1['mismatches']}")

        # 2. venue holds it exactly -> position clean; only the expired-ETH
        #    review flag may remain if venue also held ETH (it doesn't here)
        venue_state["positions"] = [{"market_id": 1, "side": "long", "size": 0.005}]
        flagged.clear()
        await venue_reconcile.run_once()
        check("clean when venue matches", not any("market 1" in m for m in flagged))

        # 3. expired attempt + unaccounted venue position on that market
        venue_state["positions"] = [
            {"market_id": 1, "side": "long", "size": 0.005},
            {"market_id": 20, "side": "long", "size": 0.1},
        ]
        flagged.clear()
        await venue_reconcile.run_once()
        check("expired-yet-venue-holds review flag",
              any("expired as never-placed" in m for m in flagged))

        # 4. run stats include chain-read count
        stats = venue_reconcile.get_last_run()
        check("per-run chain reads logged", stats.get("chain_reads") == 1, str(stats))
    finally:
        venue_reconcile._flag = real_flag
        venue_reconcile._venue_positions = real_venue
        async with sf() as s:
            if pos_id: await s.execute(text("DELETE FROM copy_live_positions WHERE id=:i"), {"i": pos_id})
            if ord_id: await s.execute(text("DELETE FROM copy_orders WHERE id=:i"), {"i": ord_id})
            await s.commit()

    fails = [n for r, n in results if r == FAIL]
    print(f"\n{len(results)-len(fails)}/{len(results)} PASS" + (f" — FAILURES: {fails}" if fails else ""))
    return 0 if not fails else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
