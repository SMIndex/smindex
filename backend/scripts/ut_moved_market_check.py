"""Moved-market check (owner, 2026-11-04): what volume does the 20 percent
rule divide by? The stored flag now uses VENUE volume only (bars read from
Hyperliquid, src 'hl'). This script recomputes the flag the old way (any
stored bar, including bars built from our own fills) for every measured
entry and counts how many entries change flag between the two rules.

    python scripts/ut_moved_market_check.py
"""
import asyncio
import os
import sys
from datetime import timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import text  # noqa: E402

from app.config import settings  # noqa: E402
from app.db import database  # noqa: E402
from app.services.ut import common, rules  # noqa: E402


async def main() -> None:
    await database.init_db(settings.DATABASE_URL)
    sf = database.get_session_factory()
    since = common.window_start()
    async with sf() as s:
        coins = (await s.execute(text("SELECT DISTINCT coin FROM ut_entries WHERE computed_at IS NOT NULL AND time >= :a"), {"a": since})).scalars().all()
    tot = old_flag = new_flag = both = only_old = only_new = unknown_venue = 0
    for coin in coins:
        bars, ts = rules.sorted_candles(await common.load_candles(coin, "1h", since - timedelta(days=1), common.now_utc()))
        async with sf() as s:
            ents = (await s.execute(text(
                "SELECT time, notional, moved_market FROM ut_entries WHERE coin=:c AND computed_at IS NOT NULL AND time >= :a"), {"c": coin, "a": since})).all()
        for t, notional, stored in ents:
            tm = common.ms(t)
            v_any = rules.volume_after(bars, tm, ts=ts)
            v_venue = rules.volume_after(bars, tm, ts=ts, venue_only=True)
            o = rules.moved_market(float(notional), v_any)
            n = rules.moved_market(float(notional), v_venue)
            tot += 1
            old_flag += o
            new_flag += n
            both += o and n
            only_old += o and not n
            only_new += n and not o
            unknown_venue += v_venue is None
            if bool(stored) != n:
                pass
    print(f"entries measured: {tot:,}")
    print(f"old rule (any stored bar, our fills' volume included): {old_flag:,} flagged")
    print(f"new rule (venue bars only): {new_flag:,} flagged; windows without venue volume: {unknown_venue:,} (never flagged)")
    print(f"changed flag: {only_old + only_new:,} (flagged only by the old rule {only_old:,}, only by the new rule {only_new:,}, both {both:,})")
    await database.close_db()


asyncio.run(main())
