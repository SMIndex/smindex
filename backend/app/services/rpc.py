"""Monad RPC transport: ordered fallback list + jittered retry (optimization pass
2026-09-24, audit P-15: rpc.monad.xyz returned 429 on ~20 % of calls).

Transport ONLY. A contract revert comes back as a normal JSON-RPC error
response, which is returned untouched, so callers still see
ContractLogicError exactly as before (the copy eligibility gate depends on the
revert-vs-transport-error distinction). Only transport failures — connection
errors, timeouts, HTTP 429/5xx — move on to the next endpoint.

web3's own per-provider retry is disabled here: it retried the SAME node up to
5 times on 429, stacking with call-site retries into retry storms.
"""
from __future__ import annotations

import random
import time
from typing import Any

from web3 import HTTPProvider
from web3.providers.base import JSONBaseProvider

from app.config import settings
from app.utils.logger import get_logger

logger = get_logger(__name__)


def rpc_urls() -> list[str]:
    urls = [settings.MONAD_RPC_URL]
    for u in (settings.MONAD_RPC_FALLBACKS or "").split(","):
        u = u.strip()
        if u and u not in urls:
            urls.append(u)
    return urls


class FallbackHTTPProvider(JSONBaseProvider):
    """Try each endpoint in order; after a full failed round, sleep with jitter
    and try one more round, then raise the last transport error."""

    ROUNDS = 2

    def __init__(self, urls: list[str], timeout: float = 10.0, session: Any = None):
        super().__init__()
        self.urls = list(urls)
        self._providers = [
            HTTPProvider(u, request_kwargs={"timeout": timeout}, session=session,
                         exception_retry_configuration=None)
            for u in self.urls
        ]
        self.failovers = 0
        self._by_url: dict[str, int] = {}
        self._last_log = time.monotonic()

    def make_request(self, method, params):
        last: Exception | None = None
        for rnd in range(self.ROUNDS):
            for i, p in enumerate(self._providers):
                try:
                    resp = p.make_request(method, params)
                    if i or rnd:
                        self.failovers += 1
                        self._by_url[self.urls[i]] = self._by_url.get(self.urls[i], 0) + 1
                        self._maybe_log()
                    return resp
                except Exception as exc:  # noqa: BLE001 — transport errors only reach here
                    last = exc
            time.sleep(random.uniform(0.2, 0.6))
        raise last if last else RuntimeError("no RPC endpoints configured")

    def _maybe_log(self) -> None:
        # one summary line per minute — per-call logging was ~10k lines/hour
        # because the primary throttles that often (measured 2026-09-24)
        now = time.monotonic()
        if now - self._last_log >= 60 and self._by_url:
            logger.info("rpc failover last %.0fs: %s (total %d)", now - self._last_log,
                        ", ".join(f"{u} x{n}" for u, n in self._by_url.items()), self.failovers)
            self._by_url.clear()
            self._last_log = now

    def is_connected(self, show_traceback: bool = False) -> bool:
        return any(p.is_connected(show_traceback) for p in self._providers)
