"""Unusual Timing acceptance test 5: a replayed entry by a flagged wallet
produces exactly one admin alert row (sent by the API poller within 20 s),
the same trade again inside 10 minutes is debounced, a reducing trade is
not alerted, and an entry seen before the position is known is resolved by
the position read.

Runs the trades feed's own functions in-process (no websocket, no venue
reads): scripts/hl_trades_feed.py is imported as a module.

    python scripts/test_ut_live.py
"""
import asyncio
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from sqlalchemy import text  # noqa: E402

import hl_trades_feed as feed  # noqa: E402

W = "0x00000000000000000000000000000000000000ut"[:42].lower()
checks = []


def check(name, got, want):
    ok = got == want
    checks.append(ok)
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}: got {got!r} want {want!r}")


async def rows_for(w):
    async with feed.Session() as s:
        return (await s.execute(text(
            "SELECT id, coin, side, notional, sent_at, chat_id, error, created_at FROM ut_alerts_sent WHERE wallet=:w ORDER BY id"), {"w": w})).all()


async def main():
    async with feed.Session() as s:
        await s.execute(text("DELETE FROM ut_alerts_sent WHERE wallet=:w"), {"w": W})
        await s.commit()
    feed.ut_flagged[W] = {"level": "strong", "score": 71.4, "hit_rate": 0.5, "hits": 6, "entries": 12, "events_preceded": 4, "misses": 6,
                          "worst_loss_usd": -2500.0, "cluster_id": None, "cluster_size": None, "age_days": 3.0, "test": True}
    tm = int(time.time() * 1000)
    print("1. entry with the position known (flat before): one alert")
    feed.pos_szi[W] = {}
    feed.pos_lev[W] = {"BTC": 20.0}
    feed.ut_on_trade(W, "BTC", -2.0, 78_000.0, tm)        # short 2 BTC = $156K
    await asyncio.sleep(0.5)
    rows = await rows_for(W)
    check("one alert row", len(rows), 1)
    check("row is BTC short $156,000", (rows[0][1], rows[0][2], float(rows[0][3])), ("BTC", "short", 156000.0))
    print("2. same trade again inside 10 minutes: debounced")
    feed.ut_on_trade(W, "BTC", -2.0, 78_000.0, tm + 5000)
    await asyncio.sleep(0.3)
    check("still one row", len(await rows_for(W)), 1)
    check("debounce counted", feed.ut_stats["debounced"] >= 1, True)
    print("3. a reducing trade is not alerted")
    feed.pos_szi[W] = {"ETH": -100.0}
    feed.ut_on_trade(W, "ETH", 60.0, 3_000.0, tm)         # buy 60 ETH ($180K) against a 100 short = reduce
    await asyncio.sleep(0.3)
    check("no ETH row", sum(1 for r in await rows_for(W) if r[1] == "ETH"), 0)
    print("4. a small trade is not alerted")
    feed.ut_on_trade(W, "SOL", 10.0, 150.0, tm)           # $1,500
    await asyncio.sleep(0.3)
    check("no SOL row", sum(1 for r in await rows_for(W) if r[1] == "SOL"), 0)
    print("5. position unknown: the trade waits, the read resolves it")
    feed.pos_szi.pop(W, None)
    feed.ut_on_trade(W, "HYPE", 3000.0, 40.0, tm)         # $120K long, state unknown
    await asyncio.sleep(0.3)
    check("pending, no HYPE row yet", (W in feed.ut_pending, sum(1 for r in await rows_for(W) if r[1] == "HYPE")), (True, 0))
    feed.ut_resolve(W, {"HYPE": 3000.0}, tm + 2000)       # the read shows the position holding it
    await asyncio.sleep(0.5)
    check("HYPE row after the read", sum(1 for r in await rows_for(W) if r[1] == "HYPE"), 1)
    print("6. the API poller sends every row to the admin chat within 20 s")
    t0 = time.time()
    sent = []
    while time.time() - t0 < 25:
        rows = await rows_for(W)
        sent = [r for r in rows if r[4] is not None]
        if len(sent) == len(rows) and rows:
            break
        await asyncio.sleep(1)
    rows = await rows_for(W)
    check("every row sent", (len(sent), len(rows)), (2, 2))
    lat = [(r[4] - r[7]).total_seconds() for r in rows if r[4] is not None]
    check("delivery under 20 s", all(x < 20 for x in lat) and bool(lat), True)
    print(f"      delivery latencies s: {[round(x, 1) for x in lat]}")
    chats = {r[5] for r in rows}
    from app.config import settings
    check("every row stamped with the admin chat only", chats, {(settings.ADMIN_TELEGRAM_CHAT_ID or '').strip()})
    async with feed.Session() as s:
        others = (await s.execute(text(
            "SELECT COUNT(*) FROM ut_alerts_sent WHERE chat_id IS NOT NULL AND chat_id NOT IN ('queued', :a)"), {"a": (settings.ADMIN_TELEGRAM_CHAT_ID or '').strip()})).scalar()
    check("no alert row ever went to another chat", int(others or 0), 0)
    n_ok, n = sum(checks), len(checks)
    print(f"\n{n_ok}/{n} checks passed; alert ids {[r[0] for r in rows]}")
    await feed.engine.dispose()
    sys.exit(0 if n_ok == n else 1)


if __name__ == "__main__":
    asyncio.run(main())
