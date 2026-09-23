"""In-process Telegram send queue.

Detection paths (trader_tracker, hyperliquid tracker) must never await network
sends inline — they ENQUEUE and move on. A single worker drains the queue with:
  - global throttle <= 20 msg/s
  - per-chat spacing >= 1s
  - retry x3 with exponential backoff (1s/2s/4s), honoring Telegram's
    `retry_after` when the exception carries one (flood control / 429)
  - dedupe by caller-supplied key (10-minute window)
  - counters + queue depth for observability (logged every 60s while active)

Single-worker-process constraint by design (no Redis): the queue lives in this
process and is lost on restart — acceptable for ephemeral trade alerts.

The actual sender is injected via start(send_fn) so unit tests can drive the
worker with a fake sender; production injects telegram_bot._send_raw.
"""
import asyncio
import time
from typing import Awaitable, Callable

from app.utils.logger import get_logger

logger = get_logger(__name__)

SendFn = Callable[[str, str, list | None], Awaitable[None]]

_queue: asyncio.Queue | None = None
_worker_task: asyncio.Task | None = None
_send_fn: SendFn | None = None

_DEDUPE_WINDOW = 600.0        # seconds
_GLOBAL_MIN_INTERVAL = 0.05   # 20 msg/s ceiling
_PER_CHAT_MIN_INTERVAL = 1.0
_MAX_RETRIES = 3
_MAX_QUEUE = 5000             # hard cap: drop-oldest beyond this, never OOM

_recent_keys: dict[str, float] = {}
_last_global_send = 0.0
_last_chat_send: dict[str, float] = {}

stats = {
    "enqueued": 0,
    "sent": 0,
    "retries": 0,
    "dropped_duplicate": 0,
    "dropped_after_retries": 0,
    "dropped_queue_full": 0,
}


def get_stats() -> dict:
    return {**stats, "queue_depth": _queue.qsize() if _queue else 0,
            "worker_running": bool(_worker_task and not _worker_task.done())}


def enqueue(chat_id: str, message: str, buttons: list | None = None,
            dedupe_key: str | None = None) -> bool:
    """Queue a message. Returns False if dropped (duplicate/full/not started)."""
    if _queue is None:
        logger.warning("telegram_queue.enqueue before start(); message dropped")
        return False
    now = time.monotonic()
    if dedupe_key:
        # prune expired keys opportunistically (small dict, cheap)
        for k in [k for k, ts in _recent_keys.items() if now - ts > _DEDUPE_WINDOW]:
            _recent_keys.pop(k, None)
        if dedupe_key in _recent_keys:
            stats["dropped_duplicate"] += 1
            return False
        _recent_keys[dedupe_key] = now
    if _queue.qsize() >= _MAX_QUEUE:
        stats["dropped_queue_full"] += 1
        logger.error("telegram_queue full (%d) — dropping message for chat %s", _MAX_QUEUE, chat_id)
        return False
    _queue.put_nowait((str(chat_id), message, buttons))
    stats["enqueued"] += 1
    return True


async def _throttle(chat_id: str) -> None:
    global _last_global_send
    now = time.monotonic()
    wait_g = _GLOBAL_MIN_INTERVAL - (now - _last_global_send)
    wait_c = _PER_CHAT_MIN_INTERVAL - (now - _last_chat_send.get(chat_id, 0.0))
    wait = max(wait_g, wait_c, 0.0)
    if wait > 0:
        await asyncio.sleep(wait)
    now = time.monotonic()
    _last_global_send = now
    _last_chat_send[chat_id] = now
    # keep the per-chat map bounded
    if len(_last_chat_send) > 2000:
        cutoff = time.monotonic() - 60
        for k in [k for k, ts in _last_chat_send.items() if ts < cutoff]:
            _last_chat_send.pop(k, None)


async def _deliver(chat_id: str, message: str, buttons: list | None) -> None:
    assert _send_fn is not None
    delay = 1.0
    for attempt in range(1, _MAX_RETRIES + 2):  # initial try + _MAX_RETRIES retries
        try:
            await _send_fn(chat_id, message, buttons)
            stats["sent"] += 1
            return
        except Exception as exc:  # noqa: BLE001 — worker must survive anything
            if attempt > _MAX_RETRIES:
                stats["dropped_after_retries"] += 1
                logger.error(
                    "telegram send FAILED after %d attempts to chat %s: %s",
                    attempt, chat_id, exc,
                )
                return
            stats["retries"] += 1
            # Honor Telegram flood-control if the exception exposes retry_after
            retry_after = getattr(exc, "retry_after", None)
            sleep_for = float(retry_after) + 0.5 if retry_after else delay
            logger.warning(
                "telegram send attempt %d to chat %s failed (%s) — retrying in %.1fs",
                attempt, chat_id, exc, sleep_for,
            )
            await asyncio.sleep(sleep_for)
            delay *= 2


async def _worker() -> None:
    assert _queue is not None
    last_stats_log = time.monotonic()
    while True:
        chat_id, message, buttons = await _queue.get()
        try:
            await _throttle(chat_id)
            await _deliver(chat_id, message, buttons)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            logger.exception("telegram_queue worker error (message dropped)")
        finally:
            _queue.task_done()
        if time.monotonic() - last_stats_log > 60:
            last_stats_log = time.monotonic()
            logger.info("telegram_queue stats: %s", get_stats())


def start(send_fn: SendFn) -> None:
    """Start (or rebind) the queue worker. Idempotent."""
    global _queue, _worker_task, _send_fn
    _send_fn = send_fn
    if _queue is None:
        _queue = asyncio.Queue()
    if _worker_task is None or _worker_task.done():
        _worker_task = asyncio.get_event_loop().create_task(_worker())
        logger.info("telegram_queue worker started")


async def stop() -> None:
    global _worker_task
    if _worker_task and not _worker_task.done():
        _worker_task.cancel()
        try:
            await _worker_task
        except asyncio.CancelledError:
            pass
    _worker_task = None
    logger.info("telegram_queue worker stopped (final stats: %s)", get_stats())
