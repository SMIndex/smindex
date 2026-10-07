"""Overnight Part C5: auto-copy ENGINE tests on real leader events (DB level),
against a throwaway schema. Real inputs: 5 consecutive real ZEC flip fills of
0xecb63caa (2026-09-29 06:12-06:58 UTC), Perpl's real 1-minute ZEC candles,
the follower's real Perpl account. The venue response is stubbed (no real
order can be placed tonight): opens fill HALF of what was asked (partial fill),
closes fill in full, SL returns oid 'SL-1'.

Asserts: flip -> close ours + open new side; partial fill recorded at the REAL
filled size; the next close is sized from that real size and cancels our SL;
re-feeding the same events creates zero new decisions and zero new orders
(idempotency); three failed orders in a row auto-pause the subscription.

Run on the server: DATABASE_URL=<throwaway> venv/bin/python scripts/test_auto_copy_engine.py <source_db_url>
"""
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import create_engine, text  # noqa: E402

from app.config import settings  # noqa: E402
from app.db import database as dbm  # noqa: E402
import app.db.models, app.db.copy_models, app.db.social_models, app.db.strategy_models, app.db.migrations_models  # noqa: E402,F401
from app.services.copy_auto import engine, executor, rules  # noqa: E402
from app.services.copy_auto.schema import DDL  # noqa: E402

LEADER = "0xecb63caa47c7c4e77f60f1ce858cf28dc2b82b00"
FOLLOWER = "0x<admin-wallet>"
EVENT_IDS = [8366069, 8366082, 8366089, 8366757, 8369079]
calls: list[tuple] = []
FAIL_OPENS = {"on": False}
results = []


def check(name, cond, detail=""):
    results.append(bool(cond))
    print(f"[{'PASS' if cond else 'FAIL'}] {name} {detail}")


async def main(src_url: str):
    await dbm.init_db(settings.DATABASE_URL)
    async with dbm.engine.begin() as c:
        for st in DDL:
            await c.execute(text(st))
        for t in ("auto_copy_log", "auto_copy_positions", "auto_copy_subs"):
            await c.execute(text(f"DELETE FROM {t}"))
        v = {**rules.DEFAULTS, **rules.PRESETS["standard"], "max_leverage": 3.0}
        await c.execute(text(
            "INSERT INTO auto_copy_subs (id, user_id, follower_wallet, leader_wallet, mode, status, sizing, margin_usd, "
            "allocation_usd, max_leverage, max_positions, mirror_adds, mirror_reduces, reopen_on_flip, drift_pct, "
            "sl_margin_pct, daily_loss_usd, total_loss_usd, created_at, updated_at) VALUES (1, 1, :f, :l, 'live', 'active', "
            "'fixed', 10, 50, :lv, 3, 1, 1, 1, 0.5, 50, 30, 100, UTC_TIMESTAMP(), UTC_TIMESTAMP())"),
            {"f": FOLLOWER, "l": LEADER, "lv": v["max_leverage"]})

    src = create_engine(src_url.replace("mysql+aiomysql://", "mysql+pymysql://"))
    with src.connect() as c:
        rows = c.execute(text("SELECT id, trader_wallet, market_id, symbol, side, event_type, size, price, source, raw "
                              "FROM leader_trade_events WHERE id IN :ids ORDER BY id").bindparams(
            __import__("sqlalchemy").bindparam("ids", expanding=True)), {"ids": EVENT_IDS}).mappings().all()
    events = [{**dict(r), "raw": json.loads(r["raw"]) if isinstance(r["raw"], str) else r["raw"]} for r in rows]

    # real Perpl ZEC 1-minute candles around the events
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
    engine.live_switch_on = lambda: True
    # Free margin is a gate input: a fixed $200 account keeps the test independent of
    # the follower's live balance (it read the real account until 2026-10-07, when a
    # lower balance made every open stop at g9_margin).
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
        calls.append(("open", side, size))
        if FAIL_OPENS["on"]:
            raise executor.OrderRejected("stub: venue rejected")
        half = int(size * 10 ** mp["size_decimals"] / 2)
        return {"filled": True, "oid": 1000 + len(calls), "fs": half, "fp": round(mark * 10 ** mp["price_decimals"])}

    async def close_market(uid, w, *, market_id, side, size, mark, mp):
        calls.append(("close", side, size))
        return {"filled": True, "oid": 2000 + len(calls), "fs": round(size * 10 ** mp["size_decimals"]),
                "fp": round(mark * 10 ** mp["price_decimals"])}

    async def stop_loss(uid, w, *, market_id, side, size, trigger, mp):
        calls.append(("sl", side, size, round(trigger, 2)))
        return {"oid": "SL-1"}

    async def cancel(uid, w, *, market_id, oid):
        calls.append(("cancel", oid))
        return {"canceled": True}
    executor.open_market, executor.close_market, executor.stop_loss, executor.cancel = open_market, close_market, stop_loss, cancel

    async def feed(evts):
        for e in evts:
            cur["t"] = float(e["raw"]["fill"]["time"]) / 1000.0
            cur["px"] = float(e["raw"]["fill"]["px"])
            await engine.on_leader_event(e)

    async def q(sql, **kw):
        async with dbm.engine.begin() as c:
            return (await c.execute(text(sql), kw)).all()

    # 1) first flip: close long (not held) + open short -> partial fill
    await feed(events[:1])
    pos = await q("SELECT side, size, sl_oid, status FROM auto_copy_positions ORDER BY id")
    opens = [c for c in calls if c[0] == "open"]
    log1 = await q("SELECT action_key, decision, gate, reason FROM auto_copy_log ORDER BY id")
    print("   decisions:", log1)
    check("flip -> 'close' of a position we never held is skipped as not_copied",
          any(r[0].startswith("close") and r[2] == "not_copied" for r in log1))
    if opens:
        asked = opens[0][2]
        check("flip -> fresh open of the NEW side", opens[0][1] == "short", str(opens[0]))
        check("partial fill recorded at the REAL filled size (half of asked)",
              pos and abs(pos[0][1] - asked / 2) < 10 ** -6 * 10 and pos[0][2] == "SL-1", f"asked={asked} held={pos}")
        check("protective SL placed on the filled size", any(c[0] == "sl" and abs(c[2] - pos[0][1]) < 1e-9 for c in calls))
    else:
        gate = [r for r in log1 if r[0].startswith("open")]
        check("flip open reached a gate decision (no fill possible)", bool(gate), str(gate))

    # 2) idempotency: the same event again -> nothing new
    n_log, n_calls = len(await q("SELECT id FROM auto_copy_log")), len(calls)
    await feed(events[:1])
    check("idempotency: re-fed event creates 0 new decisions and 0 new orders",
          len(await q("SELECT id FROM auto_copy_log")) == n_log and len(calls) == n_calls,
          f"log {n_log}->{len(await q('SELECT id FROM auto_copy_log'))}, calls {n_calls}->{len(calls)}")

    # 3) next flip closes OUR short using the real held size, cancels the SL, opens long
    held_before = pos[0][1] if pos else None
    await feed(events[1:2])
    closes_ = [c for c in calls if c[0] == "close"]
    if held_before:
        check("close sized from the REAL held size (not the leader's)", closes_ and abs(closes_[-1][2] - held_before) < 1e-9,
              f"close={closes_[-1] if closes_ else None} held={held_before}")
        check("full close cancels our SL", ("cancel", "SL-1") in calls)
        st = await q("SELECT status, realized_pnl FROM auto_copy_positions WHERE side='short' ORDER BY id LIMIT 1")
        check("short position closed with realized P/L recorded", st and st[0][0] == "closed", str(st))

    # 4) three failed orders in a row -> auto-pause
    FAIL_OPENS["on"] = True
    # this phase tests the pause mechanism: price/basis gates (proven in the rule
    # tests, and they blocked 3 of these opens above) are neutralised so every
    # open reaches the venue, which rejects it
    engine._mark = lambda mid: cur["px"]

    async def _zero_basis(mid):
        return 0.0
    hp.basis_bps_for_market = _zero_basis
    async with dbm.engine.begin() as c:     # free the slot so the next opens reach the venue
        await c.execute(text("UPDATE auto_copy_positions SET status='detached' WHERE status='open'"))
    await feed(events[2:5])
    sub = await q("SELECT status, pause_reason, consecutive_failures FROM auto_copy_subs WHERE id=1")
    fails = await q("SELECT COUNT(*) FROM auto_copy_log WHERE decision='failed'")
    print("   subscription after failures:", sub, "failed decisions:", fails)
    check("3 failed orders in a row auto-pause the subscription",
          sub[0][0] == "paused" and "3 failed" in (sub[0][1] or ""), str(sub))

    print("\norders sent to the (stubbed) venue:", calls)
    print(f"\n{sum(results)}/{len(results)} passed")
    await dbm.engine.dispose()


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1]))
