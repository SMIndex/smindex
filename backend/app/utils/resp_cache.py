"""Shared response cache for read-only API payloads (beta speed pass 2026-10-08).

Three things every heavy public endpoint on this box needs:

  * TTL caching - the box is CPU-starved by other tenants, so an endpoint that
    computes in 6 ms when the loop is free can take seconds under load; the
    cheapest fix is to compute it once per TTL.
  * single flight - concurrent requests for the same key wait on ONE build
    instead of each starting their own (the pattern that made
    /api/leaders/positions/{w} cost one chain read per poll per tab).
  * stale-on-error - when the build raises (venue timeout, DB hiccup) the last
    good payload is served for up to `stale_max`, exactly like
    /api/market-configs; after that the error propagates so the UI can show its
    honest "unavailable - retrying" state instead of silently ancient data.

Entries remember when they were last READ, so the background warmer only
refreshes what someone is actually looking at: no traffic, no background work.
"""
from __future__ import annotations

import asyncio
import functools
import inspect
import time
from typing import Any, Awaitable, Callable

from app.utils.logger import get_logger

logger = get_logger(__name__)

_registry: list["RespCache"] = []


class _Entry:
    __slots__ = ("val", "at", "read", "fail_at", "lock", "builder")

    def __init__(self) -> None:
        self.val: Any = None
        self.at: float = 0.0            # monotonic time the value was built
        self.read: float = 0.0          # monotonic time it was last served
        self.fail_at: float = 0.0       # monotonic time the last build failed
        self.lock = asyncio.Lock()
        self.builder: Callable[[], Awaitable[Any]] | None = None


class RespCache:
    def __init__(self, name: str, ttl: float, *, stale_max: float = 900.0,
                 fail_backoff: float = 5.0, warm_window: float = 600.0,
                 max_entries: int = 512) -> None:
        self.name = name
        self.ttl = ttl
        self.stale_max = stale_max
        self.fail_backoff = fail_backoff
        self.warm_window = warm_window
        self.max_entries = max_entries
        self._e: dict[Any, _Entry] = {}
        self.hits = 0
        self.misses = 0
        self.coalesced = 0
        self.stale_served = 0
        self.warmed = 0
        _registry.append(self)

    # -- internals ---------------------------------------------------------
    def _entry(self, key) -> _Entry:
        e = self._e.get(key)
        if e is None:
            if len(self._e) >= self.max_entries:
                # drop the least recently READ half
                for k, _ in sorted(self._e.items(), key=lambda kv: kv[1].read)[: self.max_entries // 2]:
                    self._e.pop(k, None)
            e = self._e[key] = _Entry()
        return e

    # -- public ------------------------------------------------------------
    async def get(self, key, builder: Callable[[], Awaitable[Any]], *, ttl: float | None = None):
        """Cached value for `key`, building it with `builder` when needed."""
        ttl = self.ttl if ttl is None else ttl
        e = self._entry(key)
        e.builder = builder
        now = time.monotonic()
        e.read = now
        if e.val is not None and now - e.at < ttl:
            self.hits += 1
            return e.val
        waited = e.lock.locked()
        async with e.lock:
            now = time.monotonic()
            if e.val is not None and now - e.at < ttl:
                # another caller just built it (single flight)
                if waited:
                    self.coalesced += 1
                else:
                    self.hits += 1
                return e.val
            if (e.fail_at and now - e.fail_at < self.fail_backoff
                    and e.val is not None and now - e.at < self.stale_max):
                self.stale_served += 1
                return e.val
            try:
                val = await builder()
            except Exception as exc:
                e.fail_at = time.monotonic()
                if e.val is not None and time.monotonic() - e.at < self.stale_max:
                    self.stale_served += 1
                    logger.warning("%s: serving last good %s (%ds old): %s",
                                   self.name, key, int(time.monotonic() - e.at), exc)
                    return e.val
                raise
            e.val, e.at, e.fail_at = val, time.monotonic(), 0.0
            self.misses += 1
            return val

    def peek(self, key):
        e = self._e.get(key)
        return e.val if e else None

    def age(self, key) -> float | None:
        e = self._e.get(key)
        return (time.monotonic() - e.at) if (e and e.val is not None) else None

    def invalidate(self, key=None) -> None:
        if key is None:
            self._e.clear()
        else:
            self._e.pop(key, None)

    async def refresh_hot(self) -> int:
        """Rebuild the entries that are in ACTIVE demand and at least half
        expired, so a visitor never pays for a cold rebuild on a busy endpoint.

        "Active demand" means the entry was read AFTER it was last built: a page
        someone is polling keeps its value warm, while an entry that was read
        once and never again is left to expire. Without that condition the
        warmer rebuilt every entry every minute (measured: 423 analytics
        rebuilds in 30 min), which is pure background load on a box that is
        already CPU-starved - the opposite of the point."""
        now = time.monotonic()
        hot = [(k, e) for k, e in list(self._e.items())
               if e.builder is not None and now - e.read < self.warm_window
               and e.read > e.at                      # read since the last build
               and now - e.at > self.ttl / 2]
        done = 0
        for k, e in hot:
            if e.lock.locked():
                continue
            async with e.lock:
                try:
                    e.val = await e.builder()
                    e.at = time.monotonic()
                    e.fail_at = 0.0
                    done += 1
                    self.warmed += 1
                except Exception as exc:
                    e.fail_at = time.monotonic()
                    short = k[0] if isinstance(k, tuple) else k
                    logger.warning("%s: warm refresh failed for %s: %s", self.name, short, exc)
            await asyncio.sleep(0)       # never hog the loop across many keys
        return done

    def stats(self) -> dict:
        return {"name": self.name, "ttl": self.ttl, "entries": len(self._e), "hits": self.hits,
                "misses": self.misses, "coalesced": self.coalesced,
                "stale_served": self.stale_served, "warmed": self.warmed}


def _resolved_defaults(sig: "inspect.Signature") -> dict:
    """Parameter defaults as the values FastAPI resolves to: a Query()/FieldInfo
    default contributes its own .default, so a direct call from the warmer keys
    the same entry as the HTTP request."""
    out = {}
    for n, p in sig.parameters.items():
        if p.default is inspect.Parameter.empty:
            continue
        out[n] = getattr(p.default, "default", p.default)
    return out


def route_cache(cache: RespCache, name: str, *, ttl: float | None = None):
    """Single flight + TTL + stale-on-error around a read-only route handler
    whose parameters are plain path/query values. The key is the full bound
    argument list, so /x?a=1 and /x?a=2 are separate entries and a direct call
    with defaults hits the same entry as the request that resolved them."""
    def deco(fn):
        sig = inspect.signature(fn)
        defaults = _resolved_defaults(sig)

        @functools.wraps(fn)
        async def inner(*args, **kwargs):
            try:
                bound = sig.bind(*args, **kwargs).arguments
            except TypeError:
                return await fn(*args, **kwargs)
            # Resolved defaults are used BOTH for the key and for the call: a
            # direct caller (the warmer) must not pass a Query()/FieldInfo
            # object into the handler body, which validates plain values.
            vals = {**defaults, **bound}
            key = (name,) + tuple(sorted((k, repr(v)) for k, v in vals.items()))
            return await cache.get(key, lambda: fn(**vals), ttl=ttl)
        return inner
    return deco


def all_stats() -> list[dict]:
    return [c.stats() for c in _registry]


async def refresh_all_hot() -> dict:
    out = {}
    for c in _registry:
        try:
            n = await c.refresh_hot()
        except Exception as exc:         # a warmer must never die
            logger.warning("resp_cache warm pass failed for %s: %s", c.name, exc)
            n = 0
        if n:
            out[c.name] = n
    return out
