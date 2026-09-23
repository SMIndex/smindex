"""In-memory per-token rate limiter for MCP tool calls.

A simple fixed-window counter keyed by token hash. Reset every 60s. Cheap and
good enough for the current scale (single backend process). If we ever scale
to multiple workers move this into Redis.
"""
import time
import threading

from app.config import settings

_WINDOW_SECONDS = 60

_lock = threading.Lock()
_buckets: dict[str, tuple[int, float]] = {}  # token_hash -> (count, window_start)


def check_and_increment(token_hash: str) -> None:
    """Raise PermissionError if `token_hash` exceeded the per-minute limit."""
    limit = max(1, settings.MCP_RATE_LIMIT_PER_MIN)
    now = time.time()
    with _lock:
        count, window_start = _buckets.get(token_hash, (0, now))
        if now - window_start >= _WINDOW_SECONDS:
            count = 0
            window_start = now
        count += 1
        _buckets[token_hash] = (count, window_start)
        if count > limit:
            retry_in = int(_WINDOW_SECONDS - (now - window_start))
            raise PermissionError(
                f"MCP rate limit exceeded ({limit}/min). Retry in ~{retry_in}s."
            )
