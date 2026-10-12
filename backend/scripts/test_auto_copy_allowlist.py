"""OBSOLETE since 2026-10-12: the owner removed the allowlist (live_allowlist_ok is
now True for every wallet), so the "non-allowlisted wallet is blocked" checks
below are expected to fail. Kept as the record of the 2026-10-08 proof.

Live auto-copy is allowlist-only: proof on the real engine (2026-10-08).

Owner question: does LIVE_COPY_ALLOWLIST: the list that guards live MANUAL
copy: also block LIVE AUTO-copy for every other wallet?

Two subscriptions on the SAME real leader event, both mode='live':
  sub 1 follower = the allowlisted wallet
  sub 2 follower = a real wallet that is NOT in the allowlist
Then the same event with the kill switch (AUTO_COPY_ENABLED) forced off.
The venue is stubbed and every call records the wallet it was sent for, so a
single order for the non-allowlisted wallet shows up as a FAIL.

Run on the server (throwaway schema, never the live one):
  DATABASE_URL=mysql+aiomysql://...@127.0.0.1/<throwaway> \
    venv/bin/python scripts/test_auto_copy_allowlist.py <source_db_url>
"""
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import bindparam, create_engine, text  # noqa: E402

from app.config import settings  # noqa: E402
from app.db import database as dbm  # noqa: E402
import app.db.models, app.db.copy_models, app.db.social_models, app.db.strategy_models, app.db.migrations_models  # noqa: E402,F401
from app.services.copy_auto import engine, executor, rules  # noqa: E402
from app.services.copy_auto.schema import DDL  # noqa: E402

LEADER = "0xecb63caa47c7c4e77f60f1ce858cf28dc2b82b00"
ALLOWED = __import__("os").environ["SMINDEX_TEST_WALLET"].lower()      # the wallet under test
OTHER = "0xc8d79f44912a9f55c6faf819283efcea9661d1dc"        # a real leaderboard wallet, NOT allowlisted
EVENT_IDS = [8366069, 8366082]
calls: list[tuple] = []
results = []


def check(name, cond, detail=""):
    results.append(bool(cond))
    print(f"[{'PASS' if cond else 'FAIL'}] {name} {detail}")


async def main(src_url: str):
    print(f"LIVE_COPY_ALLOWLIST = {settings.LIVE_COPY_ALLOWLIST}")
    print(f"COPY_LIVE_ENABLED = {settings.COPY_LIVE_ENABLED}  AUTO_COPY_ENABLED = {settings.AUTO_COPY_ENABLED}")
    check("the wallet the owner asked for is allowlisted", settings.live_allowlist_ok(ALLOWED))
    check("the other real wallet is NOT allowlisted", not settings.live_allowlist_ok(OTHER))
    check("live switch reads ON from the deployed .env (COPY_LIVE_ENABLED AND AUTO_COPY_ENABLED)",
          engine.live_switch_on())

    await dbm.init_db(settings.DATABASE_URL)
    async with dbm.engine.begin() as c:
        for st in DDL:
            await c.execute(text(st))
        for t in ("auto_copy_log", "auto_copy_positions", "auto_copy_subs"):
            await c.execute(text(f"DELETE FROM {t}"))
        v = {**rules.DEFAULTS, **rules.PRESETS["standard"], "max_leverage": 3.0}
        for sid, uid, who in ((1, 1, ALLOWED), (2, 2, OTHER)):
            await c.execute(text(
                "INSERT INTO auto_copy_subs (id, user_id, follower_wallet, leader_wallet, mode, status, sizing, "
                "margin_usd, allocation_usd, max_leverage, max_positions, mirror_adds, mirror_reduces, "
                "reopen_on_flip, drift_pct, sl_margin_pct, tp_pct, daily_loss_usd, total_loss_usd, created_at, "
                "updated_at) VALUES (:sid, :u, :f, :l, 'live', 'active', 'fixed', 10, 50, :lv, 3, 1, 1, 1, 0.5, "
                "50, 100, 30, 100, UTC_TIMESTAMP(), UTC_TIMESTAMP())"),
                {"sid": sid, "u": uid, "f": who, "l": LEADER, "lv": v["max_leverage"]})

    src = create_engine(src_url.replace("mysql+aiomysql://", "mysql+pymysql://"))
    with src.connect() as c:
        rows = c.execute(text("SELECT id, trader_wallet, market_id, symbol, side, event_type, size, price, source, raw "
                              "FROM leader_trade_events WHERE id IN :ids ORDER BY id").bindparams(
            bindparam("ids", expanding=True)), {"ids": EVENT_IDS}).mappings().all()
    events = [{**dict(r), "raw": json.loads(r["raw"]) if isinstance(r["raw"], str) else r["raw"]} for r in rows]
    print(f"real leader events loaded: {[e['id'] for e in events]}")

    # real Perpl ZEC 1-minute candles around the events (same inputs as the engine test)
    import httpx
    from app.services import market_registry
    await market_registry.refresh_markets(force=True)
    t0 = min(int(e["raw"]["fill"]["time"]) for e in events) - 180_000
    t1 = max(int(e["raw"]["fill"]["time"]) for e in events) + 180_000
    async with httpx.AsyncClient(timeout=20) as hc:
        k = (await hc.get(f"{settings.PERPL_REST_URL}/api/v1/market-data/50/candles/60/{t0}-{t1}")).json()["d"]
    pdp = 10 ** int((await market_registry.get_market_by_id(50))["price_decimals"])
    closes = {int(x["t"]) // 60_000: float(x["c"]) / pdp for x in k}
    cur = {"t": 0.0, "px": 0.0}
    engine._mark = lambda mid: closes.get(int(cur["t"] // 60)) or closes.get(int(cur["t"] // 60) - 1)
    import app.services.hyperliquid.prices as hp

    async def _basis(mid):
        m = engine._mark(mid)
        return (cur["px"] - m) / m * 1e4 if m else None
    hp.basis_bps_for_market = _basis

    class _Clock:
        @staticmethod
        def time():
            return cur["t"] + 0.5
    engine.time = _Clock
    from app.services import chain_reader
    chain_reader.get_trader_positions_only = lambda w: {"balance": 200.0, "positions": []}
    ls = {}
    real_ls = engine._leader_state

    async def _leader_state(leader, coin):
        if coin not in ls:
            ls[coin] = await real_ls(leader, coin)
        return ls[coin]
    engine._leader_state = _leader_state

    async def open_market(uid, w, *, market_id, side, size, leverage, mark, mp):
        calls.append(("open", w, side, size))
        half = int(size * 10 ** mp["size_decimals"] / 2)
        return {"filled": True, "oid": 1000 + len(calls), "fs": half, "fp": round(mark * 10 ** mp["price_decimals"])}

    async def close_market(uid, w, *, market_id, side, size, mark, mp):
        calls.append(("close", w, side, size))
        return {"filled": True, "oid": 2000 + len(calls), "fs": round(size * 10 ** mp["size_decimals"]),
                "fp": round(mark * 10 ** mp["price_decimals"])}

    async def stop_loss(uid, w, *, market_id, side, size, trigger, mp):
        calls.append(("sl", w, size))
        return {"oid": "SL-1"}

    async def take_profit(uid, w, *, market_id, side, size, trigger, mp):
        calls.append(("tp", w, size))
        return {"oid": "TP-1"}

    async def cancel(uid, w, *, market_id, oid):
        calls.append(("cancel", w, oid))
        return {"canceled": True}
    executor.open_market, executor.close_market, executor.stop_loss, executor.cancel = (
        open_market, close_market, stop_loss, cancel)
    executor.take_profit = take_profit

    async def feed(evts):
        for e in evts:
            cur["t"] = float(e["raw"]["fill"]["time"]) / 1000.0
            cur["px"] = float(e["raw"]["fill"]["px"])
            await engine.on_leader_event(e)

    async def q(sql, **kw):
        async with dbm.engine.begin() as c:
            return (await c.execute(text(sql), kw)).all()

    # --- 1) one real leader event, both subscriptions in Live ----------------
    await feed(events[:1])
    log = await q("SELECT sub_id, action_key, decision, gate, reason FROM auto_copy_log ORDER BY id")
    for r in log:
        print("   decision:", r)
    mine = [r for r in log if r[0] == 1 and r[1].startswith("open")]
    theirs = [r for r in log if r[0] == 2 and r[1].startswith("open")]
    check("non-allowlisted wallet: the Live open is SKIPPED at gate 1 (g1_allowlist)",
          theirs and theirs[0][2] == "skipped" and theirs[0][3] == "g1_allowlist", str(theirs))
    check("non-allowlisted wallet: no order of ANY kind reached the venue",
          not [c for c in calls if c[1] == OTHER], str([c for c in calls if c[1] == OTHER]))
    check("non-allowlisted wallet: no live position row was created",
          not await q("SELECT id FROM auto_copy_positions WHERE follower_wallet=:w", w=OTHER))
    check("allowlisted wallet: the same event passed gate 1 and reached the venue",
          mine and mine[0][3] != "g1_allowlist" and [c for c in calls if c[1] == ALLOWED and c[0] == "open"],
          f"{mine} {[c for c in calls if c[1] == ALLOWED]}")

    # --- 2) the non-allowlisted wallet in Shadow is also stopped at gate 1 ---
    async with dbm.engine.begin() as c:
        await c.execute(text("UPDATE auto_copy_subs SET mode='shadow' WHERE id=2"))
        await c.execute(text("UPDATE auto_copy_positions SET status='detached' WHERE status='open'"))
    n_calls = len(calls)
    await feed(events[1:2])
    log2 = await q("SELECT sub_id, action_key, decision, gate FROM auto_copy_log WHERE sub_id=2 ORDER BY id")
    sh = [r for r in log2 if r[1].startswith("open")]
    check("non-allowlisted wallet in Shadow: still stopped at g1_allowlist (not even a shadow position)",
          len(sh) >= 2 and sh[-1][3] == "g1_allowlist"
          and not await q("SELECT id FROM auto_copy_positions WHERE follower_wallet=:w", w=OTHER), str(sh))
    check("non-allowlisted wallet: still zero venue calls after the second event",
          not [c for c in calls[n_calls:] if c[1] == OTHER], str(calls[n_calls:]))

    # --- 3) the off switch: AUTO_COPY_ENABLED=false blocks the allowlisted
    #        wallet's Live open too (this is what removing the .env line does)
    async with dbm.engine.begin() as c:
        await c.execute(text("UPDATE auto_copy_positions SET status='detached' WHERE status='open'"))
        await c.execute(text("DELETE FROM auto_copy_log"))
    settings.AUTO_COPY_ENABLED = False
    check("with AUTO_COPY_ENABLED=false the engine reports the live switch OFF", not engine.live_switch_on())
    n_calls = len(calls)
    await feed(events[1:2])
    log3 = await q("SELECT sub_id, action_key, decision, gate FROM auto_copy_log WHERE sub_id=1 ORDER BY id")
    off = [r for r in log3 if r[1].startswith("open")]
    check("switch off: the allowlisted wallet's Live open is skipped at g1_kill_switch",
          off and off[-1][3] == "g1_kill_switch", str(off))
    check("switch off: no open/sl/tp order reached the venue",
          not [c for c in calls[n_calls:] if c[0] in ("open", "sl", "tp")], str(calls[n_calls:]))
    settings.AUTO_COPY_ENABLED = True

    # --- 4) the API refuses to create a subscription for a non-allowlisted wallet
    from fastapi import HTTPException
    from app.routers import copy_auto as api

    class _U:
        def __init__(self, w):
            self.id, self.wallet_address = 99, w
    try:
        await api.create_sub(api.SubCreate(leader_wallet=LEADER), user=_U(OTHER))
        check("POST /api/copy-auto/subs refuses a non-allowlisted wallet (403)", False, "no exception raised")
    except HTTPException as e:
        check("POST /api/copy-auto/subs refuses a non-allowlisted wallet (403)",
              e.status_code == 403, f"{e.status_code} {e.detail}")
    check("GET /api/copy-auto/settings reports allowlisted=false for the other wallet",
          not api._allowlisted(_U(OTHER)) and api._allowlisted(_U(ALLOWED)))

    print("\nevery call the (stubbed) venue received:")
    for c in calls:
        print("   ", c)
    print(f"\n{sum(results)}/{len(results)} passed")
    await dbm.engine.dispose()


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1]))
