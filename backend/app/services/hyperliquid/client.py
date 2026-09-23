"""Shared Hyperliquid info client — ONE httpx.AsyncClient + weight budget
(WALLET_EXPLORER_REPORT Part 1).

Every HL info-API call in this codebase goes through post_info(). The client:

  * accounts request WEIGHT per the venue's published table (the research
    doc's contract): 2 for allMids/l2Book/clearinghouseState/orderStatus/
    spotClearinghouseState/exchangeStatus, 60 for userRole, 20 for all other
    info types; PLUS response-size extras — 1 weight per 20 returned items
    for userFills/userFillsByTime/historicalOrders/userFunding/
    userNonFundingLedgerUpdates/userTwapSliceFills/fundingHistory/delegator*
    and 1 per 60 candles for candleSnapshot (extras are measured from the
    actual response and added to the same rolling window; ceil = conservative).
  * enforces a rolling 60s weight window against a configurable soft ceiling
    (settings.HL_WEIGHT_CEILING, default 1000 of the venue's 1200/min/IP).
  * runs three priority classes:
      critical    copy/live paths (tracker poll, foreground profile/portfolio)
                  — may borrow the 1000..1200 headroom, never shed
      background  sweep, samplers, dating, funding polls — waits at the soft
                  ceiling (up to a bounded time), never borrows headroom
      explorer    the wallet-explorer endpoints — lowest: admitted only while
                  EXPLORER_HEADROOM stays free under the soft ceiling and shed
                  (HLWeightShed) after a SHORT wait, so it can never crowd out
                  the classes above
  * honors Retry-After on 429 with per-class exponential backoff via a
    circuit breaker: a 429 seen by class c opens the breaker for c AND every
    lower class (the venue limit is per-IP, but critical is never blocked for
    long by a background 429). While a breaker is open, explorer sheds
    immediately and the others wait it out. The 429 RESPONSE is still
    returned/raised to the caller — existing per-module 429 discipline
    (backoff-once / halt-cycle) keeps working unchanged.
  * exposes metrics on GET /health and logs weight-used-per-minute.

The leaderboard file lives on a different host (stats-data.hyperliquid.xyz)
with no info-API weight — stream() shares the connection pool but does not
touch the window.
"""
import asyncio
import math
import time
from datetime import datetime, timezone

import httpx

from app.utils.logger import get_logger

logger = get_logger(__name__)

INFO_URL = "https://api.hyperliquid.xyz/info"
HARD_CEILING = 1200                 # the venue's per-IP limit — never crossed
EXPLORER_HEADROOM = 150             # explorer must leave this free under soft
WINDOW_SEC = 60.0

# priority classes, highest first
CRITICAL = "critical"
BACKGROUND = "background"
EXPLORER = "explorer"
_CLASS_ORDER = (CRITICAL, BACKGROUND, EXPLORER)

# how long an acquire may wait for window room before giving up
MAX_WAIT_SEC = {CRITICAL: 30.0, BACKGROUND: 60.0, EXPLORER: 2.0}
# breaker: base open-time when the venue sends no Retry-After; doubled per
# consecutive 429 (per class), capped. Critical's breaker is short — its own
# call sites carry their own retry discipline and must not be starved.
BREAKER_BASE_SEC = 10.0
BREAKER_MAX_SEC = {CRITICAL: 10.0, BACKGROUND: 120.0, EXPLORER: 120.0}
_ACQUIRE_POLL_SEC = 0.25

# ---- weight table (research-doc contract) --------------------------------
WEIGHT_2 = {"allMids", "l2Book", "clearinghouseState", "orderStatus",
            "spotClearinghouseState", "exchangeStatus"}
WEIGHT_60 = {"userRole"}
DEFAULT_WEIGHT = 20
# +1 weight per 20 returned items:
PER_ITEM_20 = {"userFills", "userFillsByTime", "historicalOrders",
               "userFunding", "userNonFundingLedgerUpdates",
               "userTwapSliceFills", "fundingHistory",
               "delegatorHistory", "delegatorRewards", "delegations"}
# +1 weight per 60 returned candles:
PER_ITEM_60 = {"candleSnapshot"}


def base_weight(info_type: str) -> int:
    if info_type in WEIGHT_2:
        return 2
    if info_type in WEIGHT_60:
        return 60
    return DEFAULT_WEIGHT


def extra_weight(info_type: str, items: int) -> int:
    """Response-size extra weight (0 for fixed-weight types)."""
    if items <= 0:
        return 0
    if info_type in PER_ITEM_20:
        return math.ceil(items / 20)
    if info_type in PER_ITEM_60:
        return math.ceil(items / 60)
    return 0


class HLWeightShed(Exception):
    """Raised when an explorer-class request is shed (window full / breaker
    open / wait exceeded). Callers translate this into an honest 503/retry —
    never into fabricated data."""


class _Window:
    """Rolling 60s weight window. Not thread-safe — single event loop."""

    def __init__(self) -> None:
        self._entries: list[list[float]] = []   # [monotonic_ts, weight]
        self._used = 0.0

    def _evict(self, now: float) -> None:
        cut = now - WINDOW_SEC
        while self._entries and self._entries[0][0] <= cut:
            self._used -= self._entries[0][1]
            self._entries.pop(0)
        if not self._entries:
            self._used = 0.0

    def used(self, now: float | None = None) -> float:
        self._evict(now if now is not None else time.monotonic())
        return self._used

    def add(self, weight: float, now: float | None = None) -> None:
        now = now if now is not None else time.monotonic()
        self._evict(now)
        self._entries.append([now, weight])
        self._used += weight


_window = _Window()
_client: httpx.AsyncClient | None = None
_breaker: dict[str, dict] = {c: {"until": 0.0, "n": 0} for c in _CLASS_ORDER}
_stats: dict[str, dict] = {c: {"requests": 0, "weight": 0.0, "shed": 0,
                               "http_429": 0, "wait_timeouts": 0}
                           for c in _CLASS_ORDER}
_minute_log = {"minute": 0, "weight": 0.0, "requests": 0, "peak": 0.0}


def _soft_ceiling() -> int:
    from app.config import settings
    return min(int(getattr(settings, "HL_WEIGHT_CEILING", 1000)), HARD_CEILING)


def _class_ceiling(cls: str) -> float:
    soft = _soft_ceiling()
    if cls == CRITICAL:
        return HARD_CEILING
    if cls == BACKGROUND:
        return soft
    return max(soft - EXPLORER_HEADROOM, 0)


def _get_client() -> httpx.AsyncClient:
    global _client
    if _client is None or _client.is_closed:
        _client = httpx.AsyncClient(timeout=15.0)
    return _client


def _log_minute(now: float, weight: float) -> None:
    """Weight-used-per-minute observability without a dedicated task: when the
    wall minute rolls over, the previous minute's totals are logged."""
    minute = int(time.time() // 60)
    if _minute_log["minute"] and minute != _minute_log["minute"]:
        logger.info(
            "hl client weight/min: %.0f used (peak window %.0f/%d), %d requests "
            "[crit %d/%.0fw, bg %d/%.0fw, exp %d/%.0fw; shed %d, 429 %d]",
            _minute_log["weight"], _minute_log["peak"], _soft_ceiling(),
            _minute_log["requests"],
            _stats[CRITICAL]["requests"], _stats[CRITICAL]["weight"],
            _stats[BACKGROUND]["requests"], _stats[BACKGROUND]["weight"],
            _stats[EXPLORER]["requests"], _stats[EXPLORER]["weight"],
            sum(s["shed"] for s in _stats.values()),
            sum(s["http_429"] for s in _stats.values()))
        _minute_log["weight"] = 0.0
        _minute_log["requests"] = 0
        _minute_log["peak"] = 0.0
    _minute_log["minute"] = minute
    _minute_log["weight"] += weight
    _minute_log["requests"] += 1
    _minute_log["peak"] = max(_minute_log["peak"], _window.used(now))


async def _acquire(weight: float, cls: str) -> None:
    """Admit `weight` into the rolling window for priority class `cls`, or
    raise HLWeightShed (explorer) / proceed anyway after MAX_WAIT (critical —
    never shed) / raise HLWeightShed (background timeout: callers treat it
    like any fetch failure, honest skip)."""
    deadline = time.monotonic() + MAX_WAIT_SEC[cls]
    while True:
        now = time.monotonic()
        br = _breaker[cls]
        if now < br["until"]:
            if cls == EXPLORER:
                _stats[cls]["shed"] += 1
                raise HLWeightShed(
                    f"hl breaker open for {br['until'] - now:.1f}s")
            if br["until"] > deadline and cls == BACKGROUND:
                _stats[cls]["wait_timeouts"] += 1
                raise HLWeightShed("hl breaker outlives max wait")
            await asyncio.sleep(min(_ACQUIRE_POLL_SEC * 4, br["until"] - now))
            continue
        if _window.used(now) + weight <= _class_ceiling(cls):
            _window.add(weight, now)
            _stats[cls]["requests"] += 1
            _stats[cls]["weight"] += weight
            _log_minute(now, weight)
            return
        if now >= deadline:
            if cls == CRITICAL:
                # never shed a copy/live-path request: admit into headroom
                _window.add(weight, now)
                _stats[cls]["requests"] += 1
                _stats[cls]["weight"] += weight
                _stats[cls]["wait_timeouts"] += 1
                _log_minute(now, weight)
                logger.warning("hl client: critical request admitted past "
                               "ceiling after %.0fs wait", MAX_WAIT_SEC[cls])
                return
            _stats[cls]["shed" if cls == EXPLORER else "wait_timeouts"] += 1
            raise HLWeightShed(
                f"hl weight window full ({_window.used(now):.0f}"
                f"/{_class_ceiling(cls):.0f} for {cls})")
        await asyncio.sleep(_ACQUIRE_POLL_SEC)


def _note_429(cls: str, retry_after: str | None) -> None:
    """Open the breaker for `cls` and every lower class. Retry-After wins over
    the exponential default; each class's cap applies."""
    try:
        ra = float(retry_after) if retry_after else None
    except (TypeError, ValueError):
        ra = None
    now = time.monotonic()
    for c in _CLASS_ORDER[_CLASS_ORDER.index(cls):]:
        br = _breaker[c]
        br["n"] += 1
        base = ra if ra is not None else BREAKER_BASE_SEC * (2 ** (br["n"] - 1))
        open_for = min(base, BREAKER_MAX_SEC[c])
        br["until"] = max(br["until"], now + open_for)
    _stats[cls]["http_429"] += 1


def _note_ok(cls: str) -> None:
    _breaker[cls]["n"] = 0


async def post_info(payload: dict, *, priority: str = BACKGROUND,
                    timeout: float = 15.0) -> httpx.Response:
    """POST to the HL info endpoint through the shared budget. Returns the
    httpx.Response UNTOUCHED (429s included) so existing call-site discipline
    is preserved; the breaker/backoff state updates as a side effect."""
    if priority not in MAX_WAIT_SEC:
        raise ValueError(f"unknown priority {priority!r}")
    info_type = str(payload.get("type") or "")
    await _acquire(base_weight(info_type), priority)
    resp = await _get_client().post(INFO_URL, json=payload, timeout=timeout)
    if resp.status_code == 429:
        _note_429(priority, resp.headers.get("Retry-After"))
        logger.warning("hl client 429 (%s, %s) — breaker open "
                       "%s=%.0fs retry-after=%s", info_type, priority, priority,
                       max(0.0, _breaker[priority]["until"] - time.monotonic()),
                       resp.headers.get("Retry-After"))
        return resp
    if resp.status_code == 200:
        _note_ok(priority)
        if info_type in PER_ITEM_20 or info_type in PER_ITEM_60:
            try:
                body = resp.json()
                items = len(body) if isinstance(body, list) else 0
            except Exception:
                items = 0
            ex = extra_weight(info_type, items)
            if ex:
                _window.add(ex)
                _stats[priority]["weight"] += ex
                _minute_log["weight"] += ex
    return resp


def stream(method: str, url: str, **kwargs):
    """Passthrough to the shared connection pool for NON-info hosts (the
    leaderboard file host has no info-API weight). Returns the httpx stream
    context manager."""
    return _get_client().stream(method, url, **kwargs)


def get_metrics() -> dict:
    """Weight/priority metrics for GET /health."""
    now = time.monotonic()
    per_class = {}
    for c in _CLASS_ORDER:
        br = _breaker[c]
        per_class[c] = {
            **{k: (round(v, 1) if isinstance(v, float) else v)
               for k, v in _stats[c].items()},
            "breaker_open_sec": round(max(0.0, br["until"] - now), 1) or 0,
            "consecutive_429": br["n"],
        }
    return {
        "window_weight_used": round(_window.used(now), 1),
        "soft_ceiling": _soft_ceiling(),
        "hard_ceiling": HARD_CEILING,
        "explorer_ceiling": round(_class_ceiling(EXPLORER)),
        "per_class": per_class,
        "as_of": datetime.now(timezone.utc).isoformat(),
    }


async def aclose() -> None:
    global _client
    if _client is not None and not _client.is_closed:
        await _client.aclose()
    _client = None
