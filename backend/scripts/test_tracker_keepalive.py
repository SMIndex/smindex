"""Throwaway (2026-10-22): the HL tracker ws keepalive + gap recovery, run from a
COPY of the tree. Same wallets as the live ws tier (same IP, same users: no
extra unique user). _emit_event is replaced by an in-memory recorder: no DB
writes, no alerts, no auto-copy. Forces one drop at FORCE_DROP_SEC and checks
that fills made while down come back as 'hl_ws_gap'.
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

database.engine = create_async_engine(settings.DATABASE_URL, pool_size=1, max_overflow=0)
database.async_session_factory = async_sessionmaker(database.engine, expire_on_commit=False)
from app.services.hyperliquid import client as hl  # noqa: E402
from app.services.hyperliquid import tracker as tr  # noqa: E402

RUN_SEC = int(os.environ.get("RUN", "900"))
FORCE_DROP_SEC = int(os.environ.get("DROP_AT", "300"))
DOWN_SEC = int(os.environ.get("DOWN", "45"))
events = []


async def fake_emit(wallet, coin, event_type, side, price, size, source, raw, **kw):
    events.append({"w": wallet[:8], "coin": coin, "type": event_type, "src": source,
                   "time": int(((raw or {}).get("fill") or {}).get("time") or 0), "at": int(time.time() * 1000)})


async def main():
    tr._emit_event = fake_emit
    async with database.async_session_factory() as s:
        wallets = [w.lower() for w in (await s.execute(text(
            "SELECT DISTINCT leader_wallet FROM auto_copy_subs WHERE status IN ('active','paused')"))).scalars().all()]
        wl = [w.lower() for w in (await s.execute(text(
            "SELECT trader_wallet FROM watchlists WHERE exchange='hl' GROUP BY trader_wallet ORDER BY COUNT(*) DESC"))).scalars().all()]
    tier = list(dict.fromkeys(wallets + wl))[:10]
    tr._ws_wallets[:] = tier
    task = asyncio.create_task(tr._ws_loop())
    t0 = time.time()
    dropped = False
    while time.time() - t0 < RUN_SEC:
        await asyncio.sleep(5)
        if not dropped and time.time() - t0 >= FORCE_DROP_SEC and tr._ws_conn is not None:
            dropped = True
            # simulate an outage: hold the tier empty so nothing reconnects for DOWN_SEC
            down_from = int(time.time() * 1000)
            tr._ws_wallets[:] = []
            await tr._ws_conn.close()
            tr._drop_ms = down_from
            await asyncio.sleep(DOWN_SEC)
            tr._ws_wallets[:] = tier
            print("forced outage", DOWN_SEC, "s from", down_from, flush=True)
    await asyncio.sleep(20)
    task.cancel()
    # truth: fills during the outage, by REST
    truth = 0
    for w in tier:
        r = await hl.post_info({"type": "userFillsByTime", "user": w, "startTime": down_from - 5000,
                                "endTime": down_from + DOWN_SEC * 1000 + 3000}, priority=hl.BACKGROUND)
        truth += len(await hl.decode_json(r) or [])
        await asyncio.sleep(1)
    gap = [e for e in events if e["src"] == "hl_ws_gap"]
    out = {k: tr.stats[k] for k in ("reconnects", "drops_keepalive", "drops_silent", "drops_other", "gap_recoveries",
                                    "gap_reads", "gap_read_errors", "gap_fills_recovered", "ws_fills_seen")}
    out.update({"tier": len(tier), "events_live": len(events) - len(gap), "events_gap": len(gap),
                "fills_in_outage_by_rest": truth, "run_sec": RUN_SEC})
    print("RESULT " + json.dumps(out), flush=True)


asyncio.run(main())
