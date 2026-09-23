"""Part-D verification: HL alert formatting + burst merge via fake sender.
No network, no real telegram — validates the exact message strings."""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import settings                            # noqa: E402
from app.db.database import init_db                        # noqa: E402


async def main():
    await init_db(settings.DATABASE_URL)
    from app.services import telegram_bot, telegram_queue
    from app.services.hyperliquid import tracker

    sent: list[str] = []

    async def fake_sender(chat_id, msg, buttons):
        sent.append(msg)

    telegram_queue.start(fake_sender)

    async def fake_followers(_w, exchange="perpl"):
        return ["111"]
    telegram_bot._get_followers_with_telegram = fake_followers  # type: ignore

    async def fake_username(_w):
        return None
    telegram_bot._get_username = fake_username  # type: ignore

    tracker.ALERT_MERGE_WINDOW_SEC = 1.0

    W = "0x45d26f28196d226497130c4bac709d808fed4029"
    # burst: 3 closing fills same wallet+coin+side inside the window,
    # real closedPnl on each, one with a flip note
    tracker._queue_alert(W, 1, "BTC", "long", "closed", 64000.0, 2.0, pnl=-120.5, size_label="Fill size")
    tracker._queue_alert(W, 1, "BTC", "long", "closed", 64100.0, 1.0, pnl=44.25, size_label="Fill size")
    tracker._queue_alert(W, 1, "BTC", "long", "closed", 64200.0, 1.0, pnl=-10.0, size_label="Fill size",
                         note="Position FLIPPED Long → Short: closed 0.5, opened 0.5 opposite")
    # poll-tier close: NO pnl known
    tracker._queue_alert(W, 31, "SOL", "short", "closed", 93.5, 25120.0,
                         size_label="Position size", price_label="Avg entry (exit px unknown)")
    # entry
    tracker._queue_alert(W, 20, "ETH", "short", "opened", 1876.0, 100.0, size_label="Fill size")

    await asyncio.sleep(6.0)
    await telegram_queue.stop()

    print(f"MESSAGES SENT: {len(sent)} (expect 3: merged BTC close, SOL close, ETH open)")
    for i, m in enumerate(sent, 1):
        print(f"--- message {i} ---")
        print(m.replace("<b>", "").replace("</b>", "").replace("<code>", "").replace("</code>", ""))
    joined = "\n".join(sent)
    assert len(sent) == 3, "burst merge failed"
    assert "+$0.00" not in joined, "fabricated zero PnL present!"
    assert "-$86.25" in joined, "summed closedPnl missing"        # -120.5+44.25-10
    assert "3 fills merged" in joined, "merge count missing"
    assert "FLIPPED" in joined, "flip note missing"
    sol = next(m for m in sent if "SOL" in m)
    assert "PnL" not in sol, "poll-tier close must have NO PnL line"
    assert "Avg entry (exit px unknown)" in sol
    assert "UTC" in joined, "UTC timestamp missing"
    assert "Notional" in joined, "notional missing"
    print("ALL FORMAT ASSERTIONS PASS")


if __name__ == "__main__":
    asyncio.run(main())
