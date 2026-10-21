"""Throwaway test of hl/last_trade.py on real data (2026-10-21). Read-only: venue
reads + SELECTs; writes nothing to the DB. Run from a COPY of the backend tree:

    PAUSE=6 python scripts/test_hl_last_trade.py > out.json

1. one hourly pass over every listed wallet (+ sub-accounts): requests, weight, time
2. live path: newest fill the running tracker recorded (leader_trade_events raw
   fill time) for listed wallets it follows, vs the venue's newest fill now
3. fallback path: 5 listed wallets the tracker does not follow, hourly value vs
   the venue's newest fill read right after
"""
import asyncio
import json
import os
import sys
import time

sys.path.insert(0, os.getcwd())
from sqlalchemy import text  # noqa: E402
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine  # noqa: E402

from app.config import settings  # noqa: E402
from app.db import database  # noqa: E402

# own small engine, no create_all
database.engine = create_async_engine(settings.DATABASE_URL, pool_size=2, max_overflow=0)
database.async_session_factory = async_sessionmaker(database.engine, expire_on_commit=False)

from app.services.hyperliquid import client as hl  # noqa: E402
from app.services.hyperliquid import last_trade  # noqa: E402

last_trade.PAUSE_SEC = float(os.environ.get("PAUSE", "6"))
fmt = lambda ms: time.strftime("%m-%d %H:%M:%S", time.gmtime(ms / 1000)) if ms else None  # noqa: E731


async def venue_newest(accts: list[str]) -> int | None:
    best = None
    for a in accts:
        r = await hl.post_info({"type": "userFills", "user": a, "aggregateByTime": True}, priority=hl.BACKGROUND)
        body = await hl.decode_json(r)
        n = max((int(f["time"]) for f in body or []), default=None)
        if n and (best is None or n > best):
            best = n
        await asyncio.sleep(last_trade.PAUSE_SEC)
    return best


async def main():
    out = {}
    listed = await last_trade.listed_wallets()
    out["listed"] = len(listed)
    t0 = time.time()
    out["pass"] = await last_trade.refresh(listed)
    out["pass_end_utc"] = fmt(int(time.time() * 1000))
    out["subaccount_wallets"] = sum(1 for w in listed if last_trade._subs.get(w, (0, []))[1])

    sf = database.async_session_factory
    async with sf() as s:
        tracked = {w.lower() for w in (await s.execute(text(
            "SELECT DISTINCT trader_wallet FROM watchlists WHERE exchange='hl' UNION "
            "SELECT DISTINCT leader_wallet FROM auto_copy_subs WHERE status IN ('active','paused')"))).scalars().all()}
        ev = (await s.execute(text(
            "SELECT trader_wallet, raw, detected_at FROM leader_trade_events WHERE exchange='hl' "
            "AND detected_at > NOW() - INTERVAL 1 DAY"))).all()
    live = {}
    for w, raw, det in ev:
        try:
            r = json.loads(raw) if isinstance(raw, str) else raw
            ms = int(r["fill"]["time"]) if r and r.get("fill") else None
        except Exception:
            ms = None
        ms = ms or int(det.timestamp() * 1000)
        w = w.lower()
        live[w] = max(live.get(w, 0), ms)

    rows = []
    for w in [x for x in listed if x in tracked][:5]:
        v = await venue_newest([w])
        ours = live.get(w)
        rows.append({"wallet": w, "path": "live (tracker feed)", "ours": fmt(ours), "venue": fmt(v),
                     "diff_sec": None if not (ours and v) else round((ours - v) / 1000)})
    for w in [x for x in listed if x not in tracked and x in last_trade._hourly][:5]:
        accts = [w] + last_trade._subs.get(w, (0, []))[1]
        v = await venue_newest(accts)
        ours = last_trade._hourly.get(w)
        rows.append({"wallet": w, "path": f"hourly fallback ({len(accts) - 1} sub-accounts)", "ours": fmt(ours),
                     "venue": fmt(v), "diff_sec": None if not (ours and v) else round((ours - v) / 1000)})
    out["compare"] = rows
    out["fallback_wallets"] = [w for w in listed if w not in tracked]
    out["live_wallets"] = [w for w in listed if w in tracked]
    out["no_fill_24h"] = [w for w in listed if w not in last_trade._hourly]
    out["total_sec"] = round(time.time() - t0)
    print(json.dumps(out, indent=1, default=str))


asyncio.run(main())
