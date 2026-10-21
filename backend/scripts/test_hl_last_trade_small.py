"""Throwaway, read-only, small: hl/last_trade.py on 5 tracked + 5 untracked listed
wallets (2026-10-21). Measures a first pass (24 h lookback) and an hourly-style
second pass, compares with the venue's newest fill, then counts sub-accounts
of all listed wallets to size the hourly cost. Run from a COPY of the tree.
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

database.engine = create_async_engine(settings.DATABASE_URL, pool_size=2, max_overflow=0)
database.async_session_factory = async_sessionmaker(database.engine, expire_on_commit=False)

from app.services.hyperliquid import client as hl  # noqa: E402
from app.services.hyperliquid import last_trade as lt  # noqa: E402

lt.PAUSE_SEC = float(os.environ.get("PAUSE", "3"))
fmt = lambda ms: time.strftime("%H:%M:%S", time.gmtime(ms / 1000)) if ms else None  # noqa: E731


def bgw():
    return hl.get_metrics()["per_class"]["background"]["weight"]


async def venue_newest(accts):
    best = None
    for a in accts:
        r = await hl.post_info({"type": "userFills", "user": a, "aggregateByTime": True}, priority=hl.BACKGROUND)
        n = max((int(f["time"]) for f in (await hl.decode_json(r)) or []), default=None)
        best = max(best or 0, n or 0) or None
        await asyncio.sleep(lt.PAUSE_SEC)
    return best


async def main():
    out = {}
    listed = await lt.listed_wallets()
    async with database.async_session_factory() as s:
        tracked = {w.lower() for w in (await s.execute(text(
            "SELECT DISTINCT trader_wallet FROM watchlists WHERE exchange='hl'"))).scalars().all()}
        tracked |= {w.lower() for w in (await s.execute(text(
            "SELECT DISTINCT leader_wallet FROM auto_copy_subs WHERE status IN ('active','paused')"))).scalars().all()}
        ev = (await s.execute(text(
            "SELECT trader_wallet, raw, detected_at FROM leader_trade_events WHERE exchange='hl' "
            "AND detected_at > NOW() - INTERVAL 1 DAY"))).all()
    live = {}
    for w, raw, det in ev:
        r = json.loads(raw) if isinstance(raw, str) else raw
        ms = int(r["fill"]["time"]) if r and r.get("fill") else int(det.timestamp() * 1000)
        live[w.lower()] = max(live.get(w.lower(), 0), ms)

    fb = [w for w in listed if w not in tracked][:5]
    w0 = bgw(); t0 = time.time()
    p1 = await lt.refresh(fb)
    p1["weight"] = round(bgw() - w0); p1["sec"] = round(time.time() - t0)
    w0 = bgw(); t0 = time.time()
    p2 = await lt.refresh(fb)        # hourly-style: reads only since the last read
    p2["weight"] = round(bgw() - w0); p2["sec"] = round(time.time() - t0)
    out["first_pass_5"] = p1
    out["hourly_pass_5"] = p2

    rows = []
    for w in [x for x in listed if x in tracked][:5]:
        v = await venue_newest([w]); ours = live.get(w)
        rows.append(["live", w[:6] + ".." + w[-4:], 0, fmt(ours), fmt(v), None if not (ours and v) else round((ours - v) / 1000)])
    for w in fb:
        accts = [w] + lt._subs.get(w, (0, []))[1]
        v = await venue_newest(accts); ours = lt._hourly.get(w)
        rows.append(["hourly", w[:6] + ".." + w[-4:], len(accts) - 1, fmt(ours), fmt(v), None if not (ours and v) else round((ours - v) / 1000)])
    out["compare"] = rows

    # size the hourly pass: sub-accounts of every listed wallet
    stats = {"requests": 0, "errors": 0}
    w0 = bgw()
    n_accts = 0
    for w in listed:
        n_accts += len(await lt._accounts(w, stats))
    out["listed"] = len(listed)
    out["tracked_listed"] = len([w for w in listed if w in tracked])
    out["accounts_total"] = n_accts
    out["subaccounts_weight"] = round(bgw() - w0)
    out["subaccount_counts_top"] = sorted(((len(v[1]), k[:6]) for k, v in lt._subs.items()), reverse=True)[:8]
    print("RESULT " + json.dumps(out, default=str))


asyncio.run(main())
