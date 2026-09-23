"""Recent-activity sort (migration v5) — ordering, NULLs-last, forward-only.

Standalone asyncio runner against the dev DB, real code paths. Seeds its own
rows (test-only wallets) and deletes exactly those rows at the end.
"""
import asyncio
import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import settings                            # noqa: E402
from app.db.database import init_db, get_session_factory   # noqa: E402

W_RECENT = "0x7e57ac7100000000000000000000000000000001"   # filled 10 min ago
W_OLD = "0x7e57ac7100000000000000000000000000000002"      # filled 1 h ago
W_NEVER = "0x7e57ac7100000000000000000000000000000003"    # never filled (NULL)
PASS, FAIL = "PASS", "FAIL"
results: list[tuple[str, str]] = []


def check(name: str, ok: bool, detail: str = ""):
    results.append((PASS if ok else FAIL, name))
    print(f"  [{PASS if ok else FAIL}] {name}" + (f" — {detail}" if detail else ""))


async def main():
    await init_db(settings.DATABASE_URL)
    from sqlalchemy import text
    from app.services.copy import trader_profiles

    now = datetime.utcnow()
    sf = get_session_factory()

    # seed
    await trader_profiles.upsert_profiles_bulk([W_RECENT, W_OLD, W_NEVER], exchange="perpl")
    await trader_profiles.touch_last_fill(W_OLD, "perpl", now - timedelta(hours=1))
    await trader_profiles.touch_last_fill(W_RECENT, "perpl", now - timedelta(minutes=10))

    try:
        # 1. ordering: most recent first
        order = await trader_profiles.recent_activity_order(
            [W_NEVER, W_OLD, W_RECENT], exchange="perpl")
        check("recent first, older second", order[:2] == [W_RECENT, W_OLD], str(order))

        # 2. NULLs last
        check("never-filled sorts LAST (NULLs last)", order[-1] == W_NEVER)

        # 3. forward-only: an older timestamp must NOT move the stamp backwards
        await trader_profiles.touch_last_fill(W_RECENT, "perpl", now - timedelta(hours=5))
        order2 = await trader_profiles.recent_activity_order(
            [W_OLD, W_RECENT], exchange="perpl")
        check("backwards touch is a no-op (forward-only)", order2 == [W_RECENT, W_OLD], str(order2))

        # 4. forward touch DOES move it
        await trader_profiles.touch_last_fill(W_OLD, "perpl", now)
        order3 = await trader_profiles.recent_activity_order(
            [W_OLD, W_RECENT], exchange="perpl")
        check("forward touch reorders", order3 == [W_OLD, W_RECENT], str(order3))

        # 5. exchange scoping: same wallet on 'hl' is untouched
        await trader_profiles.upsert_profiles_bulk([W_RECENT], exchange="hl")
        prof_hl = await trader_profiles.get_profiles_bulk([W_RECENT], exchange="hl")
        check("exchange-scoped (hl row untouched)",
              prof_hl[W_RECENT]["last_fill_at"] is None)

        # 6. EXPLAIN the sorted query — index must be used
        async with sf() as s:
            rows = (await s.execute(text(
                "EXPLAIN SELECT wallet_address FROM trader_profiles "
                "WHERE exchange = 'perpl' AND wallet_address IN (:a, :b, :c) "
                "ORDER BY (last_fill_at IS NULL), last_fill_at DESC"
            ), {"a": W_RECENT, "b": W_OLD, "c": W_NEVER})).mappings().all()
        plan = [dict(r) for r in rows]
        print("  EXPLAIN:", plan)
        used_index = any(r.get("key") for r in plan)
        check("EXPLAIN shows index usage", used_index,
              f"key={[r.get('key') for r in plan]}")
    finally:
        async with sf() as s:
            await s.execute(text(
                "DELETE FROM trader_profiles WHERE wallet_address IN (:a, :b, :c)"),
                {"a": W_RECENT, "b": W_OLD, "c": W_NEVER})
            await s.commit()

    print()
    fails = [n for r, n in results if r == FAIL]
    print(f"{len(results) - len(fails)}/{len(results)} PASS" + (f" — FAILURES: {fails}" if fails else ""))
    return 0 if not fails else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
