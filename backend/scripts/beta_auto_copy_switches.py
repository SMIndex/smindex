"""Beta readiness 4.6: auto-copy user toggle, platform kill switch display, and the
one-source-per-market rule, through the real engine and the real settings API.
Throwaway schema only; real leader events read (SELECT only) from the source DB.
Run: DATABASE_URL=<throwaway> venv/bin/python scripts/beta_auto_copy_switches.py <source_db_url>
"""
import asyncio
import json
import os
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
assert "perpl_terminal" not in os.environ["DATABASE_URL"].split("/")[-1], "refusing to run against prod"

import httpx  # noqa: E402
from jose import jwt as jose_jwt  # noqa: E402
from sqlalchemy import create_engine, text  # noqa: E402

from app.config import settings  # noqa: E402
from app.db import database as dbm  # noqa: E402
import app.db.models, app.db.copy_models, app.db.social_models, app.db.strategy_models, app.db.migrations_models  # noqa: E402,F401
from app.services.copy_auto import engine, executor, user_settings  # noqa: E402
from app.services.copy_auto.schema import DDL  # noqa: E402

LEADER = "0xecb63caa47c7c4e77f60f1ce858cf28dc2b82b00"
FOLLOWER = "0x7e57c0de00000000000000000000000000005a1e"
EVENT_IDS = [8366069, 8366082, 8366089, 8366757, 8369079]
res = []


def check(name, ok, detail=""):
    res.append(ok)
    print(("PASS " if ok else "FAIL ") + name + (f"  [{detail}]" if detail else ""))


async def main(src_url):
    await dbm.init_db(os.environ["DATABASE_URL"])
    async with dbm.engine.begin() as c:
        for stmt in DDL:
            await c.execute(text(stmt))
        for t in ("auto_copy_log", "auto_copy_positions", "auto_copy_subs", "auto_copy_user_settings"):
            await c.execute(text(f"DELETE FROM {t}"))
        r = await c.execute(text("INSERT INTO users (wallet_address, created_at, updated_at) VALUES (:w, UTC_TIMESTAMP(), UTC_TIMESTAMP())"), {"w": FOLLOWER})
        uid = r.lastrowid
        await c.execute(text("INSERT INTO auto_copy_subs (user_id, follower_wallet, leader_wallet, mode, status, created_at, updated_at) "
                             "VALUES (:u, :f, :l, 'shadow', 'active', UTC_TIMESTAMP(), UTC_TIMESTAMP())"), {"u": uid, "f": FOLLOWER, "l": LEADER})
    src = create_engine(src_url.replace("mysql+aiomysql://", "mysql+pymysql://"))
    with src.connect() as c:
        rows = c.execute(text("SELECT id, trader_wallet, market_id, symbol, side, event_type, size, price, source, raw "
                              "FROM leader_trade_events WHERE id IN :ids ORDER BY id").bindparams(
            __import__("sqlalchemy").bindparam("ids", expanding=True)), {"ids": EVENT_IDS}).mappings().all()
    events = [{**dict(r), "raw": json.loads(r["raw"]) if isinstance(r["raw"], str) else r["raw"]} for r in rows]
    # fixed inputs: clock at the event, mark = leader px, zero basis, $200 free margin, no venue
    cur = {"t": 0.0, "px": 0.0, "positions": []}
    engine._mark = lambda mid: cur["px"]
    import app.services.hyperliquid.prices as hp

    async def zero(mid): return 0.0
    hp.basis_bps_for_market = zero

    class _Clock:
        @staticmethod
        def time(): return cur["t"] + 0.5
    engine.time = _Clock
    from app.services import chain_reader
    chain_reader.get_trader_positions_only = lambda w: {"balance": 200.0, "positions": cur["positions"]}

    async def ls(leader, coin): return 5.0, 50000.0
    engine._leader_state = ls

    async def q(sql, **kw):
        async with dbm.engine.begin() as c:
            r = await c.execute(text(sql), kw)
            return r.all() if r.returns_rows else []

    async def decide(evt):
        cur["t"] = float(evt["raw"]["fill"]["time"]) / 1000.0
        cur["px"] = float(evt["raw"]["fill"]["px"])
        await engine.on_leader_event(evt)
        return (await q("SELECT action_key, decision, gate FROM auto_copy_log WHERE leader_event_id=:e ORDER BY id", e=evt["id"]))

    settings.LIVE_COPY_ALLOWLIST = [FOLLOWER]   # Shadow honours the allowlist (by design)
    # 1. user toggle OFF -> opens skipped with user_auto_copy_off
    await user_settings.save(uid, enabled=False)
    d = await decide(events[0])
    opens = [x for x in d if x[0].startswith("open")]
    check("toggle OFF: open skipped as user_auto_copy_off", opens and opens[0][2] == "user_auto_copy_off", str(d))
    # 2. toggle ON -> the next open reaches the gates and is mirrored (shadow)
    await user_settings.save(uid, enabled=True)
    d = await decide(events[2])
    opens = [x for x in d if x[0].startswith("open")]
    check("toggle ON: next open is mirrored again (shadow)", opens and opens[0][1] == "shadow", str(d))
    # 3. one source per market: our account already holds ZEC on-chain -> open refused
    await q("UPDATE auto_copy_positions SET status='detached' WHERE status='open'")
    cur["positions"] = [{"market_id": 50, "side": "long", "size": 1.0}]
    d = await decide(events[4])
    opens = [x for x in d if x[0].startswith("open")]
    check("one source per market: ZEC already held on-chain -> open refused", opens and opens[0][1] == "skipped" and "g4" in (opens[0][2] or ""), str(d))
    # 4. settings API: user ON + AUTO_COPY_ENABLED false -> platform_paused true
    settings.AUTO_COPY_ENABLED = False
    tok = jose_jwt.encode({"sub": str(uid), "wallet": FOLLOWER, "exp": datetime.now(timezone.utc) + timedelta(hours=1)}, settings.JWT_SECRET, algorithm="HS256")
    from app.main import app
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
        r = await c.get("/api/copy-auto/settings", headers={"Authorization": f"Bearer {tok}"})
    j = r.json()
    check("platform switch off + user on -> platform_paused true (UI shows 'paused by the platform')",
          j.get("enabled") is True and j.get("platform_live_on") is False and j.get("platform_paused") is True, json.dumps({k: j.get(k) for k in ("enabled", "platform_live_on", "platform_paused")}))
    print(f"\nTOTAL {len(res)} PASS {sum(res)} FAIL {len(res) - sum(res)}")


asyncio.run(main(sys.argv[1]))
