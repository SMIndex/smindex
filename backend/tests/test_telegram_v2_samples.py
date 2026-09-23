"""Template-v2 samples (PROFILE_QUALITY_REPORT D3): 5 rendered messages via
the fake sender — open, close+profit, close+loss, flip, merged burst."""
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
        sent.append(msg + "\n[buttons: " + " | ".join(f"{t} -> {u}" for row in (buttons or []) for t, u in row) + "]")

    telegram_queue.start(fake_sender)

    async def fake_followers(_w, exchange="perpl"):
        return ["111"]
    telegram_bot._get_followers_with_telegram = fake_followers  # type: ignore

    async def fake_name(_w, exchange="hl"):
        return "BobbyBigSize" if _w.startswith("0x7fda") else None
    telegram_bot._get_leader_name = fake_name  # type: ignore

    tracker.ALERT_MERGE_WINDOW_SEC = 1.0
    BOBBY = "0x7fdafde5cfb5465924316eced2d3715494c517d1"
    ANON = "0x45d26f28196d226497130c4bac709d808fed4029"

    # 1. open
    tracker._queue_alert(BOBBY, 20, "ETH", "short", "opened", 1877.0, 23130.9,
                         size_label="Fill size", post_pos=-23130.9)
    # 2. close with profit
    tracker._queue_alert(BOBBY, 1, "BTC", "short", "closed", 64100.0, 50.0,
                         pnl=684210.55, size_label="Fill size", post_pos=-219.4)
    # 3. close with loss
    tracker._queue_alert(ANON, 40, "HYPE", "short", "closed", 55.2, 10000.0,
                         pnl=-751700.0, size_label="Fill size", post_pos=-423080.0)
    # 4. flip
    tracker._queue_alert(ANON, 31, "SOL", "long", "closed", 76.5, 86.19,
                         pnl=-0.65, size_label="Fill size", post_pos=-68.05,
                         action="Flipped Long→Short (closed 18.14, opened 68.05)")
    # 5. merged burst (3 fills)
    for px, sz, pnl in ((2301.0, 400.0, 1200.0), (2302.5, 350.0, 1010.0), (2304.0, 250.0, 690.0)):
        tracker._queue_alert(BOBBY, 20, "ETH", "long", "closed", px, sz,
                             pnl=pnl, size_label="Fill size", post_pos=0.0)

    await asyncio.sleep(8.0)
    await telegram_queue.stop()

    print(f"SAMPLES: {len(sent)} (expect 5)")
    for i, m in enumerate(sent, 1):
        print(f"\n===== SAMPLE {i} =====")
        print(m.replace("<b>", "").replace("</b>", "").replace("<code>", "").replace("</code>", ""))
    assert len(sent) == 5
    joined = "\n".join(sent)
    assert "+$0.00" not in joined
    assert "?exchange=hl" in joined, "deep link must carry exchange"
    assert "Flipped Long→Short" in joined
    assert "3 fills merged" in joined
    assert "Position now: FLAT" in joined
    print("\nALL V2 ASSERTIONS PASS")


if __name__ == "__main__":
    asyncio.run(main())
