"""Platform ledger: attribution + stats, on a throwaway schema.

The frames are the real ones the proxy logged for the owner's account on
2026-10-08 (a MON open: order row + fill), plus variations of them. The stats
section is run on real copies of the auto-copy / copy tables.

Run on the server from a throwaway copy of the tree, never the live schema:
  DATABASE_URL=mysql+aiomysql://...@127.0.0.1/<throwaway> \
    venv/bin/python scripts/test_platform_ledger.py
"""
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import text  # noqa: E402

from app.config import settings  # noqa: E402
from app.db import database as dbm  # noqa: E402
import app.db.models, app.db.copy_models, app.db.social_models, app.db.strategy_models, app.db.migrations_models  # noqa: E402,F401
from app.services import platform_ledger as L  # noqa: E402

W = __import__("os").environ["SMINDEX_TEST_WALLET"].lower()
RQ, OID = 1791473023119, 7316789329978
ORDER_ROW = {"oid": OID, "mkt": 10, "acc": 161, "t": 1, "st": 4, "sr": 43, "p": 23728,
             "fs": 12757, "fp": 23522, "lv": 1000, "fl": 4, "rq": RQ, "r": True}
FILL = {"at": {"b": 111645345, "t": 1791473163000, "tx": 57, "txid": "3c8f", "l": 4},
        "mkt": 10, "acc": 161, "oid": OID, "t": 1, "l": 2, "p": 23522, "s": 12757,
        "f": "103525", "bfa": "0"}
results = []


def check(name, cond, detail=""):
    results.append(bool(cond))
    print(f"[{'PASS' if cond else 'FAIL'}] {name} {detail}")


def f24(*rows):
    return json.dumps({"mt": 24, "sid": 40, "d": list(rows)})


def f25(*rows):
    return json.dumps({"mt": 25, "sid": 40, "sn": 1, "at": {"b": 1, "t": 1}, "d": list(rows)})


async def drain(pending):
    sf = dbm.get_session_factory()
    while not L._queue.empty():
        await L.handle(sf, L._queue.get_nowait(), pending)


async def retry(pending):
    sf = dbm.get_session_factory()
    old = list(pending)
    pending.clear()
    for it in old:
        await L.handle(sf, it, pending)


async def count(sql, **kw):
    async with dbm.get_session_factory()() as s:
        return (await s.execute(text(sql), kw)).first()


async def main():
    assert "perpl_terminal" != settings.DATABASE_URL.rsplit("/", 1)[-1].split("?")[0], "refusing the live schema"
    await dbm.init_db(settings.DATABASE_URL)
    async with dbm.engine.begin() as conn:
        for stmt in L.DDL:
            await conn.execute(text(stmt))
    L._queue = asyncio.Queue(maxsize=L.QUEUE_MAX)
    pending = []

    # 1. an order sent through us, its row and its fill
    s = L.Session(W)
    s.on_client_frame(json.dumps({"mt": 22, "rq": RQ, "mkt": 10, "acc": 161, "t": 1, "p": 23728, "s": 12757}))
    s.on_venue_frame(f24(ORDER_ROW))
    s.on_venue_frame(f25(FILL))
    await drain(pending)
    r = await count("SELECT COUNT(*), MAX(status) FROM platform_orders WHERE wallet=:w", w=W)
    check("order sent through the proxy is recorded", r[0] == 1 and r[1] == 4, str(tuple(r)))
    r = await count("SELECT COUNT(*), MAX(symbol), MAX(price), MAX(size), MAX(notional_usd), MAX(fee_usd) FROM platform_fills")
    check("its fill is recorded once", r[0] == 1, str(tuple(r)))
    check("real MON fill scales to 12,757 MON @ 0.023522 = $300.07, fee $0.103525",
          r[1] == "MON" and abs(r[4] - 300.07) < 0.01 and abs(r[5] - 0.103525) < 1e-9, str(tuple(r)))

    # 2. the same fill frame again (reconnect / replay) does not double the volume
    s.on_venue_frame(f25(FILL))
    await drain(pending)
    r = await count("SELECT COUNT(*) FROM platform_fills")
    check("a repeated fill frame is not counted twice", r[0] == 1, str(r[0]))

    # 3. a fill for an order that did NOT go through us (auto-copy, Perpl's own site)
    L.PENDING_TTL_SEC = 0.0
    other = dict(FILL, oid=7353626787843, at=dict(FILL["at"], b=222, tx=1))
    s.on_venue_frame(f25(other))
    await drain(pending)
    r = await count("SELECT COUNT(*) FROM platform_fills")
    check("a fill for an order placed elsewhere is NOT counted", r[0] == 1 and L.stats["unmatched"] == 1,
          f"rows={r[0]} unmatched={L.stats['unmatched']}")
    s.on_venue_frame(f24(dict(ORDER_ROW, oid=7353626787843, rq=1791642760804238)))
    await drain(pending)
    r = await count("SELECT COUNT(*) FROM platform_orders")
    check("an order row with someone else's rq is NOT recorded", r[0] == 1, str(r[0]))

    # 4. fill arrives BEFORE its order row
    L.PENDING_TTL_SEC = 30.0
    s2 = L.Session(W)
    s2.on_client_frame(json.dumps({"mt": 22, "rq": 5001, "mkt": 10, "t": 3}))
    early = dict(FILL, oid=9001, t=3, at=dict(FILL["at"], b=333, tx=2))
    s2.on_venue_frame(f25(early))
    await drain(pending)
    r = await count("SELECT COUNT(*) FROM platform_fills")
    check("fill before its order row waits, is not dropped", r[0] == 1 and len(pending) == 1, f"rows={r[0]} pending={len(pending)}")
    s2.on_venue_frame(f24(dict(ORDER_ROW, oid=9001, rq=5001, t=3)))
    await drain(pending)
    await retry(pending)
    r = await count("SELECT COUNT(*) FROM platform_fills")
    check("...and is counted once the order row lands", r[0] == 2 and not pending, f"rows={r[0]} pending={len(pending)}")

    # 5. a stop-loss placed through us fills in a LATER browser session
    s3 = L.Session(W)
    s3.on_client_frame(json.dumps({"mt": 22, "rq": 6001, "mkt": 10, "t": 3}))
    s3.on_venue_frame(f24({"oid": 288791797170211, "mkt": 10, "acc": 161, "t": 3, "st": 8, "sr": 35,
                           "os": 17022, "tp": 22400, "tpc": 4, "rq": 6001}))
    await drain(pending)
    s4 = L.Session(W)                                   # new connection: remembers nothing
    s4.on_venue_frame(f25(dict(FILL, oid=288791797170211, t=3, at=dict(FILL["at"], b=444, tx=3))))
    await drain(pending)
    r = await count("SELECT COUNT(*) FROM platform_fills WHERE oid = 288791797170211")
    check("a trigger order's fill in a later session is still counted", r[0] == 1, str(r[0]))
    r = await count("SELECT is_trigger FROM platform_orders WHERE oid = 288791797170211")
    check("...and the order is marked as a trigger", r and r[0] == 1, str(r))

    # 6. a rejected order and a cancel are stored but not counted as orders
    s5 = L.Session(W)
    s5.on_client_frame(json.dumps({"mt": 22, "rq": 7001}))
    s5.on_client_frame(json.dumps({"mt": 22, "rq": 7002}))
    s5.on_venue_frame(f24({"oid": 9100, "mkt": 10, "t": 1, "st": 7, "sr": 32, "rq": 7001},
                          {"oid": 9101, "mkt": 10, "t": 5, "st": 5, "sr": 28, "rq": 7002}))
    await drain(pending)
    r = await count("SELECT COUNT(*) FROM platform_orders WHERE order_type IN (1,2,3,4) AND (status IS NULL OR status <> 7)")
    check("rejected (st:7) and non-order types are excluded from the order count", r[0] == 3, str(r[0]))

    # 7. another wallet's session cannot be credited with this wallet's oid
    s6 = L.Session("0xc8d79f44912a9f55c6faf819283efcea9661d1dc")
    s6.on_venue_frame(f25(dict(FILL, at=dict(FILL["at"], b=555, tx=9))))
    L.PENDING_TTL_SEC = 0.0
    await drain(pending)
    r = await count("SELECT COUNT(DISTINCT wallet) FROM platform_fills")
    check("oid match is per wallet", r[0] == 1, str(r[0]))

    # 8. junk never raises out of the hooks in a way that reaches traffic
    quiet = True
    for junk in ("", "not json", '{"mt":100}', '{"mt":24,"d":null}', '{"mt":25,"d":[1,null,{}]}',
                 '{"mt":24,"d":[{"oid":"x","rq":null}]}', '{"mt": 22}'):
        try:
            s.on_venue_frame(junk)
            s.on_client_frame(junk)
        except ValueError:
            pass                      # the proxy wraps both calls in try/except
        except Exception as exc:
            quiet = False
            print("   raised", type(exc).__name__, exc, "on", junk)
    await drain(pending)
    check("malformed frames do nothing", quiet and L.stats["errors"] == 0, str(L.stats))

    # 9. the stats section, on real copies of the copy / auto-copy tables
    from app.routers.terminal_stats import _smindex_section
    async with dbm.get_session_factory()() as sess:
        sm = await _smindex_section(sess)
    print(json.dumps(sm, indent=1, default=str))
    check("manual: 3 orders, 3 fills", sm["manual"]["orders"] == 3 and sm["manual"]["fills"] == 3, "")
    r = await count("SELECT COUNT(*) FROM auto_copy_log WHERE mode='live' AND decision='copied'")
    check("auto-copy orders = every live copied log row", sm["auto_copy"]["orders"] == r[0] and r[0] > 0, f"{sm['auto_copy']['orders']} vs {r[0]}")
    check("totals add manual + auto-copy, copy volume not added twice",
          sm["totals"]["orders"] == sm["manual"]["orders"] + sm["auto_copy"]["orders"]
          and abs(sm["totals"]["volume"] - (sm["manual"]["volume"] + sm["auto_copy"]["volume"])) < 0.02, "")

    print(f"\n{len(results)} checks, {sum(results)} passed, {len(results) - sum(results)} FAILED")
    await dbm.engine.dispose()
    return 0 if all(results) else 1


sys.exit(asyncio.run(main()))
