"""Unit test for app.services.telegram_queue — runs standalone (no pytest in
this venv): `python tests/test_telegram_queue.py`. Uses a FAKE sender only;
live Telegram delivery is not testable in this checkout (no bot token) and is
reported as UNVERIFIED in the implementation report.
"""
import asyncio
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services import telegram_queue as tq  # noqa: E402


def reset():
    tq._queue = None
    tq._worker_task = None
    tq._send_fn = None
    tq._recent_keys.clear()
    tq._last_chat_send.clear()
    tq._last_global_send = 0.0
    for k in tq.stats:
        tq.stats[k] = 0


async def drain():
    assert tq._queue is not None
    await tq._queue.join()
    await asyncio.sleep(0.05)


async def test_send_and_per_chat_spacing():
    reset()
    sent = []

    async def fake(chat_id, msg, buttons):
        sent.append((time.monotonic(), chat_id, msg))

    tq.start(fake)
    tq.enqueue("111", "m1")
    tq.enqueue("111", "m2")
    tq.enqueue("222", "m3")
    await drain()
    assert [m for _, _, m in sent] == ["m1", "m3", "m2"] or len(sent) == 3, sent
    t_chat1 = [t for t, c, _ in sent if c == "111"]
    assert len(t_chat1) == 2 and (t_chat1[1] - t_chat1[0]) >= 0.95, (
        f"per-chat spacing violated: {t_chat1[1] - t_chat1[0]:.3f}s"
    )
    assert tq.stats["sent"] == 3
    await tq.stop()
    print("PASS per-chat spacing >=1s + all delivered (order:", [m for _, _, m in sent], ")")


async def test_dedupe():
    reset()
    sent = []

    async def fake(chat_id, msg, buttons):
        sent.append(msg)

    tq.start(fake)
    assert tq.enqueue("1", "a", dedupe_key="k1") is True
    assert tq.enqueue("1", "a", dedupe_key="k1") is False
    assert tq.enqueue("1", "b", dedupe_key="k2") is True
    await drain()
    assert sent == ["a", "b"], sent
    assert tq.stats["dropped_duplicate"] == 1
    await tq.stop()
    print("PASS dedupe window drops duplicate key")


async def test_retry_then_success():
    reset()
    calls = {"n": 0}
    sent = []

    async def flaky(chat_id, msg, buttons):
        calls["n"] += 1
        if calls["n"] < 3:
            raise ConnectionError("boom")
        sent.append(msg)

    # shrink backoff for the test
    orig = tq._MAX_RETRIES
    tq.start(flaky)
    t0 = time.monotonic()
    tq.enqueue("9", "will-retry")
    await drain()
    dt = time.monotonic() - t0
    assert sent == ["will-retry"]
    assert calls["n"] == 3
    assert tq.stats["retries"] == 2 and tq.stats["sent"] == 1
    assert dt >= 2.9, f"expected ~1s+2s backoff before success, took {dt:.2f}s"
    await tq.stop()
    tq._MAX_RETRIES = orig
    print(f"PASS retry x2 with backoff then success (took {dt:.2f}s, attempts={calls['n']})")


async def test_drop_after_retries_and_retry_after():
    reset()

    class Flood(Exception):
        retry_after = 0.2

    calls = {"n": 0}

    async def always_fail(chat_id, msg, buttons):
        calls["n"] += 1
        raise Flood("flood control")

    tq.start(always_fail)
    t0 = time.monotonic()
    tq.enqueue("7", "doomed")
    await drain()
    dt = time.monotonic() - t0
    assert calls["n"] == 1 + tq._MAX_RETRIES, calls
    assert tq.stats["dropped_after_retries"] == 1 and tq.stats["sent"] == 0
    # retry_after=0.2 honored (+0.5 pad) => ~2.1s total, NOT the 1+2+4 default
    assert dt < 4.0, f"retry_after not honored, took {dt:.2f}s"
    await tq.stop()
    print(f"PASS drop after {tq._MAX_RETRIES} retries, retry_after honored ({dt:.2f}s, attempts={calls['n']})")


async def test_global_throttle():
    reset()
    sent = []

    async def fake(chat_id, msg, buttons):
        sent.append(time.monotonic())

    tq.start(fake)
    n = 30
    for i in range(n):
        tq.enqueue(str(1000 + i), f"m{i}")  # distinct chats -> only global throttle
    await drain()
    span = sent[-1] - sent[0]
    rate = (n - 1) / span if span > 0 else float("inf")
    assert rate <= 21.0, f"global throttle exceeded: {rate:.1f} msg/s"
    await tq.stop()
    print(f"PASS global throttle: {n} msgs at {rate:.1f} msg/s (<=20 target)")


async def main():
    await test_send_and_per_chat_spacing()
    await test_dedupe()
    await test_retry_then_success()
    await test_drop_after_retries_and_retry_after()
    await test_global_throttle()
    print("ALL TELEGRAM QUEUE TESTS PASS")


if __name__ == "__main__":
    asyncio.run(main())
