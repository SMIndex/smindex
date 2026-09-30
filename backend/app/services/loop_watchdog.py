"""Event-loop stall watchdog (optimization pass 2026-09-24).

The API runs ONE uvicorn worker, so any synchronous work on the event loop
stalls every request behind it (the benchmark showed /api/admin/me — a trivial
handler — at p50 0.3 s but p95 6.9 s). An asyncio task stamps a heartbeat every
100 ms; a daemon thread checks it, and when the loop has been silent > STALL_S
it logs the loop thread's current stack once per stall, naming the blocking
code. Read-only diagnostics; stall stats are exposed on GET /health.
"""
from __future__ import annotations

import asyncio
import sys
import threading
import time
import traceback
from collections import deque

from app.utils.logger import get_logger

logger = get_logger("loop_watchdog")

STALL_S = 1.0
_beat = time.monotonic()
_loop_thread_id: int | None = None
_stalls: deque = deque(maxlen=200)          # (ended_at_wall, duration_s, top_frame)


async def _heartbeat() -> None:
    global _beat
    while True:
        _beat = time.monotonic()
        await asyncio.sleep(0.1)


def _watch() -> None:
    in_stall, started, frame_txt = False, 0.0, ""
    while True:
        time.sleep(0.2)
        lag = time.monotonic() - _beat
        if lag > STALL_S and not in_stall:
            in_stall, started = True, _beat
            fr = sys._current_frames().get(_loop_thread_id)
            stack = traceback.format_stack(fr)[-8:] if fr else []
            frame_txt = stack[-1].strip().splitlines()[0] if stack else "?"
            logger.warning("event loop stalled > %.1fs; loop thread stack:\n%s", STALL_S, "".join(stack))
        elif lag <= STALL_S and in_stall:
            dur = time.monotonic() - started
            _stalls.append((time.time(), round(dur, 2), frame_txt))
            logger.warning("event loop stall ended after %.2fs (%s)", dur, frame_txt)
            in_stall = False


def start() -> None:
    global _loop_thread_id
    _loop_thread_id = threading.get_ident()
    asyncio.get_running_loop().create_task(_heartbeat())
    threading.Thread(target=_watch, name="loop-watchdog", daemon=True).start()


def stats() -> dict:
    d = sorted(s[1] for s in _stalls)
    return {"stalls": len(d), "max_s": d[-1] if d else None,
            "p50_s": d[len(d) // 2] if d else None,
            "recent": [{"at": int(t), "s": s, "where": w} for t, s, w in list(_stalls)[-5:]]}
