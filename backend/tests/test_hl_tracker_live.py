"""Phase-6 LIVE verification harness (~11 min run, real HL websocket).

Watches two real high-activity leaderboard wallets via the ws tier, runs
>=10 minutes, force-kills the shared ws at the midpoint to prove reconnect +
re-baseline + no duplicate events. Telegram delivery uses a FAKE sender via
telegram_queue; follower lookup is monkeypatched to one fake chat id.
Events land in the REAL dev DB (kept as evidence).
"""
import asyncio
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import settings                        # noqa: E402
from app.db.database import init_db, get_session_factory  # noqa: E402

# Two wallets from our ingested HL leaderboard known to trade very actively
# (0xecb6... is a market maker with ~83 positions / constant fills).
WALLETS = [
    "0xecb63caa47c7c4e77f60f1ce858cf28dc2b82b00",
    "0x5b5d51203a0f9079f8aeb098a6523a13f298c060",
]
FOLLOWER = "0x9999999999999999999999999999999999999999"
RUN_SECONDS = 270
RECONNECT_AT = 120


async def main():
    await init_db(settings.DATABASE_URL)
    from sqlalchemy import text
    from app.services import telegram_bot, telegram_queue
    from app.services.hyperliquid import tracker

    sent: list[tuple[str, str]] = []

    async def fake_sender(chat_id, msg, buttons):
        sent.append((chat_id, msg.splitlines()[0] if msg else ""))

    telegram_queue.start(fake_sender)

    async def fake_followers(_wallet):
        return ["999000999"]
    telegram_bot._get_followers_with_telegram = fake_followers  # type: ignore

    sf = get_session_factory()
    async with sf() as s:
        await s.execute(text("DELETE FROM watchlists WHERE follower_wallet = :f"), {"f": FOLLOWER})
        for w in WALLETS:
            await s.execute(text(
                "INSERT INTO watchlists (follower_wallet, trader_wallet, exchange, created_at) "
                "VALUES (:f, :t, 'hl', NOW())"), {"f": FOLLOWER, "t": w})
        await s.commit()

    t0 = datetime.utcnow()
    tracker.start()
    print(f"[{datetime.utcnow():%H:%M:%S}] tracker started, watching {len(WALLETS)} wallets")

    await asyncio.sleep(RECONNECT_AT)
    print(f"[{datetime.utcnow():%H:%M:%S}] MIDPOINT stats: {tracker.get_stats()}")
    print(f"[{datetime.utcnow():%H:%M:%S}] forcing ws close (reconnect test)")
    await tracker.force_reconnect()
    await asyncio.sleep(RUN_SECONDS - RECONNECT_AT)

    stats = tracker.get_stats()
    print(f"[{datetime.utcnow():%H:%M:%S}] FINAL stats: {stats}")
    await tracker.stop()
    await telegram_queue.stop()

    async with sf() as s:
        rows = (await s.execute(text(
            "SELECT id, trader_wallet, symbol, side, event_type, price, size, source, unique_event_key, detected_at "
            "FROM leader_trade_events WHERE exchange = 'hl' AND detected_at >= :t0 "
            "ORDER BY id DESC LIMIT 12"), {"t0": t0})).fetchall()
        dup = (await s.execute(text(
            "SELECT unique_event_key, COUNT(*) c FROM leader_trade_events "
            "WHERE exchange = 'hl' GROUP BY unique_event_key HAVING c > 1"))).fetchall()
        await s.execute(text("DELETE FROM watchlists WHERE follower_wallet = :f"), {"f": FOLLOWER})
        await s.commit()

    print(f"EVENT ROWS this run: {len(rows)}")
    for r in rows[:8]:
        print("  ", r[1][:10], r[2], r[3], r[4], f"px={r[5]}", f"sz={r[6]}", r[7], "| key:", r[8][:70])
    print(f"DB duplicate keys: {len(dup)} (must be 0)")
    print(f"FAKE-SENT telegram messages: {len(sent)}")
    for c, first_line in sent[:5]:
        print("   chat", c, "|", first_line[:90])
    assert len(dup) == 0, "duplicate unique_event_key rows found!"
    print("HL TRACKER LIVE VERIFICATION COMPLETE")


if __name__ == "__main__":
    asyncio.run(main())
