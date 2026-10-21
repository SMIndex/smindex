"""Read-only: which Hyperliquid Discover wallets the real-time tracker follows (2026-10-21).

    cd backend && python scripts/probe_hl_coverage.py
"""
import asyncio
import os
import sys

sys.path.insert(0, os.getcwd())
from sqlalchemy import text  # noqa: E402

from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker  # noqa: E402

from app.config import settings  # noqa: E402


async def main():
    # own small engine, no create_all: read-only against the live schema
    sf = async_sessionmaker(create_async_engine(settings.DATABASE_URL, pool_size=1, max_overflow=0))
    async with sf() as s:
        listed = {}
        for period in ("all", "day", "week", "month"):
            ts = (await s.execute(text(
                "SELECT MAX(timestamp) FROM leaderboard_snapshots WHERE exchange='hl' AND period=:p"),
                {"p": period})).scalar()
            rows = (await s.execute(text(
                "SELECT wallet_address FROM leaderboard_snapshots WHERE exchange='hl' AND period=:p "
                "AND timestamp=:t ORDER BY `rank` LIMIT 50"), {"p": period, "t": ts})).scalars().all()
            listed[period] = [w.lower() for w in rows]
        union = set().union(*listed.values())
        wl = {w.lower() for w in (await s.execute(text(
            "SELECT DISTINCT trader_wallet FROM watchlists WHERE exchange='hl'"))).scalars().all()}
        subs = {w.lower() for w in (await s.execute(text(
            "SELECT DISTINCT trader_wallet FROM copy_subscriptions WHERE exchange='hl' AND status='active'"))).scalars().all()}
        ac = {w.lower() for w in (await s.execute(text(
            "SELECT DISTINCT leader_wallet FROM auto_copy_subs WHERE status IN ('active','paused')"))).scalars().all()}
        tracked = wl | subs | ac
        ev = (await s.execute(text(
            "SELECT trader_wallet, MAX(detected_at) FROM leader_trade_events WHERE exchange='hl' "
            "AND detected_at > NOW() - INTERVAL 2 DAY GROUP BY trader_wallet"))).all()
        recent = {w.lower(): t for w, t in ev}
    print({p: len(v) for p, v in listed.items()}, "union", len(union))
    print("tracked total", len(tracked), "(watchlist", len(wl), "subs", len(subs), "auto-copy", len(ac), ")")
    print("listed AND tracked", len(union & tracked), "| 'all' top 50 tracked", len(set(listed["all"]) & tracked))
    print("listed with a trade event in 2 days", len(union & set(recent)))
    for w in sorted(union & tracked):
        print("  tracked:", w[:10], recent.get(w))


asyncio.run(main())
