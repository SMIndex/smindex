"""Throwaway, read-only (2026-10-21): the tracker's new fill-time hook on a real
ws feed + the hourly pass cost with the idle-account rule. Run from a COPY.

Live: subscribes userFills for 5 wallets the RUNNING tracker already follows
(same IP, same users: no extra unique user against Hyperliquid's 10-user cap),
passes every fill to tracker._note_fill_time only (no events, no DB writes),
then compares with the venue's newest fill read by REST.
Hourly: first pass + hourly pass over 20 listed wallets.
"""
import asyncio
import json
import os
import sys
import time

import websockets

sys.path.insert(0, os.getcwd())
from sqlalchemy import text  # noqa: E402
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine  # noqa: E402

from app.config import settings  # noqa: E402
from app.db import database  # noqa: E402

database.engine = create_async_engine(settings.DATABASE_URL, pool_size=2, max_overflow=0)
database.async_session_factory = async_sessionmaker(database.engine, expire_on_commit=False)

from app.services.hyperliquid import client as hl  # noqa: E402
from app.services.hyperliquid import last_trade as lt  # noqa: E402
from app.services.hyperliquid import tracker  # noqa: E402

LISTEN_SEC = int(os.environ.get("LISTEN", "240"))
fmt = lambda ms: time.strftime("%H:%M:%S", time.gmtime(ms / 1000)) if ms else None  # noqa: E731


def bgw():
    return hl.get_metrics()["per_class"]["background"]["weight"]


async def listen(wallets):
    async with websockets.connect(tracker.WS_URL, ping_interval=20) as ws:
        for w in wallets:
            await ws.send(json.dumps({"method": "subscribe", "subscription": {"type": "userFills", "user": w}}))
        end = time.time() + LISTEN_SEC
        n = 0
        while time.time() < end:
            try:
                raw = await asyncio.wait_for(ws.recv(), max(0.1, end - time.time()))
            except asyncio.TimeoutError:
                break
            msg = json.loads(raw)
            if msg.get("channel") == "userFills":
                d = msg.get("data") or {}
                for f in d.get("fills") or []:
                    tracker._note_fill_time(str(d.get("user", "")).lower(), int(f.get("time") or 0))
                    n += 1
        return n


async def main():
    out = {}
    listed = await lt.listed_wallets()
    async with database.async_session_factory() as s:
        tracked = {w.lower() for w in (await s.execute(text(
            "SELECT DISTINCT trader_wallet FROM watchlists WHERE exchange='hl'"))).scalars().all()}
        tracked |= {w.lower() for w in (await s.execute(text(
            "SELECT DISTINCT leader_wallet FROM auto_copy_subs WHERE status IN ('active','paused')"))).scalars().all()}
    live_w = [w for w in listed if w in tracked][:5]
    out["fills_seen"] = await listen(live_w)
    rows = []
    for w in live_w:
        r = await hl.post_info({"type": "userFills", "user": w, "aggregateByTime": True}, priority=hl.BACKGROUND)
        v = max((int(f["time"]) for f in (await hl.decode_json(r)) or []), default=None)
        ours, src = lt.last_trade(w)
        rows.append([src, w[:6] + ".." + w[-4:], fmt(ours), fmt(v), None if not (ours and v) else round((ours - v) / 1000)])
        await asyncio.sleep(1)
    out["live_compare"] = rows
    out["read_at_utc"] = fmt(int(time.time() * 1000))

    sample = [w for w in listed if w not in tracked][:20]
    w0 = bgw(); t0 = time.time()
    out["first_pass_20"] = {**(await lt.refresh(sample)), "weight": round(bgw() - w0), "sec": round(time.time() - t0)}
    w0 = bgw(); t0 = time.time()
    out["hourly_pass_20"] = {**(await lt.refresh(sample)), "weight": round(bgw() - w0), "sec": round(time.time() - t0)}
    out["no_fill_found"] = [w[:6] for w in sample if w not in lt._hourly]
    print("RESULT " + json.dumps(out, default=str))


asyncio.run(main())
