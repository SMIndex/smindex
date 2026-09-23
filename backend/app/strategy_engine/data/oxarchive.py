"""0xArchive Hyperliquid liquidation history (spec v1.1 Part B, D-65).

Loads liquidation fills for the strategy assets into `strat_liquidations`
(same schema as the live ws feed, `source='oxarchive'`, `coverage='full'`),
deduplicated against every existing row on (ts, liquidated_user, coin, px);
rebuilds the 15m liquidation-map history (`strat_liq_map_hist`) from the loaded
fills — or from 0xArchive projected-levels snapshots when the key can reach
them — and measures the live feed's coverage against the archive.

API contract (docs.0xarchive.io, read 2026-09-06):
  base   https://api.0xarchive.io          header X-API-Key
  GET /v1/hyperliquid/liquidations/{symbol}?start=<ms>&end=<ms>&limit=1000&cursor=
      rows: symbol, coin, timestamp (ISO UTC), liquidated_user, liquidator_user,
            side ('A' sell | 'B' buy), price, size, mark_price, closed_pnl,
            direction ('Close Long' | 'Close Short'), trade_id, tx_hash
      envelope: {success, data[], meta{count, next_cursor, request_id}}
  GET /v1/hyperliquid/liquidations/{symbol}/levels/history?start&end&limit<=100&cursor
      data[]: {mid_price, snapshot_ts, block_number, total_long, total_short,
               levels[{price, long_notional, short_notional, long_count, short_count}]}
  Free tier: 30-day span per request, 15 rps, 3 concurrent; 429 → Retry-After.
  Plan age limit (measured 2026-09-06 with the owner's key): 403
  `history_window_exceeded` — "Your plan includes the most recent 30 days of
  history … earliest allowed timestamp <ISO>. Requests cannot be chunked across
  the age boundary." The loaders clamp their start to that timestamp and report
  it (`earliest_allowed_ms`) instead of failing (D-69).
  `snapshot_ts` comes back as 'YYYY-MM-DD HH:MM:SS.mmm' (UTC), not epoch.

Without OXARCHIVE_API_KEY every entry point returns a `key_missing` report and
writes nothing — never a fabricated row.
"""
from __future__ import annotations

import asyncio
import datetime as dt
import logging
import os
import re
from collections import Counter, defaultdict
from typing import Optional

import httpx
from sqlalchemy import text

logger = logging.getLogger("strategy_engine.oxarchive")

BASE_URL = "https://api.0xarchive.io"
ENV_KEY = "OXARCHIVE_API_KEY"
CHUNK_DAYS = 30                      # free-tier span limit per request
PAGE_LIMIT = 1000
LEVELS_PAGE_LIMIT = 100
DAY_MS = 86_400_000
M15 = 15 * 60_000
BAND_PCT = 0.0025                    # same 0.25% grid as pools.liquidation_clusters
MAP_HORIZON_MS = 24 * 3_600_000      # D-65: fills within the next 24h stand in for "positions later liquidated"
CLAMP_MARGIN_MS = 5 * 60_000         # D-69: the plan's age boundary is now−30d and moves while we page
_TIMEOUT = 30.0


def api_key() -> Optional[str]:
    k = os.environ.get(ENV_KEY, "").strip()
    return k or None


def _iso_ms(s: str) -> int:
    d = dt.datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    if d.tzinfo is None:
        d = d.replace(tzinfo=dt.timezone.utc)
    return int(d.timestamp() * 1000)


def _any_ms(v) -> int:
    """epoch ms / epoch s / ISO or 'YYYY-MM-DD HH:MM:SS.mmm' → epoch ms."""
    s = str(v or "").strip()
    if not s:
        return 0
    if s.replace(".", "", 1).isdigit():
        f = float(s)
        return int(f if f > 10**11 else f * 1000)
    return _iso_ms(s)


_EARLIEST_RE = re.compile(r"earliest allowed timestamp (\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z?)")


class HistoryWindowExceeded(Exception):
    """403 history_window_exceeded: the plan does not reach that far back."""

    def __init__(self, earliest_ms: Optional[int], message: str) -> None:
        super().__init__(message)
        self.earliest_ms = earliest_ms


def parse_fill(r: dict) -> Optional[dict]:
    """0xArchive liquidation row → strat_liquidations row. `side` is the side that
    was LIQUIDATED: 'Close Long' = a long was liquidated (sold), 'Close Short' =
    a short was liquidated; falls back to the fill side (A = sell closes a long)."""
    try:
        ts = int(r["timestamp"]) if str(r.get("timestamp", "")).isdigit() else _iso_ms(r["timestamp"])
        px = float(r["price"])
        sz = float(r["size"])
    except (KeyError, TypeError, ValueError):
        return None
    if px <= 0 or sz <= 0 or ts <= 0:
        return None
    direction = str(r.get("direction") or "")
    if direction.lower().startswith("close long"):
        side = "long"
    elif direction.lower().startswith("close short"):
        side = "short"
    else:
        side = "long" if r.get("side") == "A" else "short"
    try:
        mark = float(r["mark_price"]) if r.get("mark_price") is not None else None
    except (TypeError, ValueError):
        mark = None
    coin = str(r.get("coin") or r.get("symbol") or "").upper()
    return {
        "ts": ts, "coin": coin, "side": side, "px": px, "sz": sz, "notional": px * sz,
        "liquidated_user": (str(r.get("liquidated_user") or "").lower() or None),
        "mark_px": mark, "method": "market", "coverage": "full", "source": "oxarchive",
    }


class Client:
    def __init__(self, key: str, rps: float = 10.0) -> None:
        self._key = key
        self._min_gap = 1.0 / rps
        self._last = 0.0
        self._http = httpx.AsyncClient(base_url=BASE_URL, headers={"X-API-Key": key}, timeout=_TIMEOUT)
        self.requests = 0
        self.earliest_allowed_ms: Optional[int] = None   # set when the plan's age limit is hit

    async def close(self) -> None:
        await self._http.aclose()

    async def get(self, path: str, params: dict) -> dict:
        for attempt in range(6):
            wait = self._min_gap - (asyncio.get_event_loop().time() - self._last)
            if wait > 0:
                await asyncio.sleep(wait)
            self._last = asyncio.get_event_loop().time()
            self.requests += 1
            r = await self._http.get(path, params=params)
            if r.status_code == 403:
                try:
                    js = r.json()
                except ValueError:
                    js = {}
                if js.get("error_code") == "history_window_exceeded":
                    m = _EARLIEST_RE.search(str(js.get("error") or ""))
                    earliest = _iso_ms(m.group(1)) if m else None
                    self.earliest_allowed_ms = earliest
                    raise HistoryWindowExceeded(earliest, str(js.get("error") or "history_window_exceeded"))
            if r.status_code == 429:
                ra = r.headers.get("Retry-After")
                delay = float(ra) if ra and ra.replace(".", "", 1).isdigit() else 2.0 * (2 ** attempt)
                logger.warning("0xArchive 429 on %s — sleeping %.1fs", path, delay)
                await asyncio.sleep(delay)
                continue
            if r.status_code >= 500:
                await asyncio.sleep(2.0 * (2 ** attempt))
                continue
            r.raise_for_status()
            return r.json()
        raise RuntimeError(f"0xArchive: {path} failed after retries")

    async def _paged(self, path: str, start_ms: int, end_ms: int, limit: int):
        """Yields raw data rows over [start, end) in <=30-day chunks with cursor
        paging. A plan age limit (403) clamps the start ONCE to the earliest
        allowed timestamp — the caller reads `earliest_allowed_ms`."""
        a = start_ms
        if self.earliest_allowed_ms and a < self.earliest_allowed_ms + CLAMP_MARGIN_MS:
            a = self.earliest_allowed_ms + CLAMP_MARGIN_MS
        clamps = 0
        while a < end_ms:
            b = min(a + CHUNK_DAYS * DAY_MS, end_ms)
            cursor = None
            while True:
                params = {"start": a, "end": b, "limit": limit}
                if cursor:
                    params["cursor"] = cursor
                try:
                    js = await self.get(path, params)
                except HistoryWindowExceeded as e:
                    # the boundary is "now − 30 d" and MOVES while we page, so clamp with a margin
                    if clamps >= 3 or not e.earliest_ms:
                        raise
                    logger.warning("0xArchive %s: plan history starts %s — clamping start (was %s)", path,
                                   dt.datetime.fromtimestamp(e.earliest_ms / 1000, dt.timezone.utc).isoformat(),
                                   dt.datetime.fromtimestamp(a / 1000, dt.timezone.utc).isoformat())
                    a = max(a, e.earliest_ms) + CLAMP_MARGIN_MS
                    clamps += 1
                    break
                for r in js.get("data") or []:
                    yield r
                cursor = (js.get("meta") or {}).get("next_cursor")
                if not cursor:
                    a = b
                    break

    async def liquidations(self, symbol: str, start_ms: int, end_ms: int):
        """Yields parsed strat_liquidations rows over [start, end)."""
        async for r in self._paged(f"/v1/hyperliquid/liquidations/{symbol}", start_ms, end_ms, PAGE_LIMIT):
            row = parse_fill(r)
            if row is not None:
                yield row

    async def levels_history(self, symbol: str, start_ms: int, end_ms: int):
        """Yields projected-levels snapshots (position-based liquidation map) over [start, end)."""
        async for snap in self._paged(f"/v1/hyperliquid/liquidations/{symbol}/levels/history",
                                      start_ms, end_ms, LEVELS_PAGE_LIMIT):
            yield snap


# --------------------------------------------------------------------------- load
async def _existing_keys(session, coin: str, start_ms: int, end_ms: int) -> tuple[set[tuple], Counter]:
    """(live keys (ts, user, px) — the owner's dedup key against the live feed;
    archive key COUNTS (ts, user, px, sz) — idempotence against an earlier archive
    load). The archive legitimately holds several distinct fills (different
    trade_id) with the same (ts, user, px) and even the same size — a liquidation
    split across makers — so archive rows are never collapsed on a key, only
    matched by multiplicity against what an earlier load already stored (D-70)."""
    rows = (await session.execute(text(
        "SELECT ts, liquidated_user, px, sz, source FROM strat_liquidations WHERE coin=:c AND ts>=:a AND ts<:b"),
        {"c": coin, "a": start_ms, "b": end_ms})).all()
    live: set[tuple] = set()
    arch: Counter = Counter()
    for ts, lu, px, sz, src in rows:
        if px is None:
            continue
        k = (int(ts), (lu or "").lower(), round(float(px), 8))
        if src == "oxarchive":
            arch[k + (round(float(sz or 0), 8),)] += 1
        else:
            live.add(k)
    return live, arch


# Coverage measured on the archive STREAM (every fill, before the live-key dedup):
# {coin: {now_ms, days, archive_rows, archive_notional}} — read by live_coverage().
_stream_coverage: dict[str, dict] = {}


async def load_liquidations(session, coins: list[str], days: int = 180, now_ms: Optional[int] = None,
                            client: Optional[Client] = None, coverage_days: int = 3) -> dict:
    """Downloads `days` of liquidation fills per coin and inserts the rows that are
    not already present: an archive fill is skipped when a LIVE row has the same
    (ts, liquidated_user, coin, px) (the live row stands in for it) or when the
    same archive fill (…, sz) was loaded before. Returns
    {status, per_coin: {coin: {inserted, skipped_live, skipped_archive, months: {YYYY-MM: n},
    coverage: {archive_rows, archive_notional}}}, earliest_allowed_ms, effective_start_ms}."""
    key = api_key()
    if client is None and key is None:
        logger.warning("0xArchive: %s not set — liquidation history NOT loaded", ENV_KEY)
        return {"status": "key_missing", "per_coin": {}}
    now_ms = now_ms or int(dt.datetime.now(dt.timezone.utc).timestamp() * 1000)
    # D-104: `days` may be fractional (the incremental loader computes it from the
    # last stored row). The archive API rejects a non-integer `start` with 400, so
    # the boundary is floored to whole milliseconds here.
    start_ms = int(now_ms - float(days) * DAY_MS)
    cov_from = now_ms - coverage_days * DAY_MS
    own = client is None
    client = client or Client(key)
    report: dict = {"status": "ok", "per_coin": {}, "start_ms": start_ms, "end_ms": now_ms}
    try:
        for coin in coins:
            live_keys, arch_have = await _existing_keys(session, coin, start_ms, now_ms)
            seen: Counter = Counter()
            inserted = skipped_live = skipped_arch = 0
            cov_rows, cov_notional = 0, 0.0
            months: dict[str, int] = defaultdict(int)
            batch: list[dict] = []
            async for row in client.liquidations(coin, start_ms, now_ms):
                if row["coin"] != coin:
                    row["coin"] = coin
                if cov_from < row["ts"] <= now_ms:
                    cov_rows += 1
                    cov_notional += row["notional"]
                k = (row["ts"], row["liquidated_user"] or "", round(row["px"], 8))
                if k in live_keys:
                    skipped_live += 1
                    continue
                ka = k + (round(row["sz"], 8),)
                seen[ka] += 1
                if seen[ka] <= arch_have.get(ka, 0):     # this occurrence was stored by an earlier load
                    skipped_arch += 1
                    continue
                batch.append(row)
                months[dt.datetime.fromtimestamp(row["ts"] / 1000, dt.timezone.utc).strftime("%Y-%m")] += 1
                if len(batch) >= 500:
                    await _insert(session, batch)
                    inserted += len(batch)
                    batch = []
            if batch:
                await _insert(session, batch)
                inserted += len(batch)
            await session.commit()
            _stream_coverage[coin] = {"now_ms": now_ms, "days": coverage_days,
                                      "archive_rows": cov_rows, "archive_notional": cov_notional}
            report["per_coin"][coin] = {"inserted": inserted, "skipped_live": skipped_live, "skipped_archive": skipped_arch,
                                        "months": dict(sorted(months.items())),
                                        "coverage": {"archive_rows": cov_rows, "archive_notional": cov_notional}}
            logger.info("0xArchive %s: %d liquidation rows inserted, %d shadowed by live rows, %d already loaded, months=%s",
                        coin, inserted, skipped_live, skipped_arch, dict(sorted(months.items())))
    finally:
        report["requests"] = client.requests
        report["earliest_allowed_ms"] = client.earliest_allowed_ms
        if client.earliest_allowed_ms and client.earliest_allowed_ms > start_ms:
            report["status"] = "ok_plan_window"
            report["effective_start_ms"] = client.earliest_allowed_ms + 1000
            logger.warning("0xArchive: plan history window starts %s — %d of the requested %d days loaded",
                           dt.datetime.fromtimestamp(client.earliest_allowed_ms / 1000, dt.timezone.utc).isoformat(),
                           (now_ms - client.earliest_allowed_ms) // DAY_MS, days)
        else:
            report["effective_start_ms"] = start_ms
        if own:
            await client.close()
    return report


async def _insert(session, rows: list[dict]) -> None:
    await session.execute(text(
        "INSERT INTO strat_liquidations (ts, coin, side, px, sz, notional, liquidated_user, mark_px, method, coverage, source) "
        "VALUES (:ts, :coin, :side, :px, :sz, :notional, :liquidated_user, :mark_px, :method, :coverage, :source)"), rows)


async def month_counts(session, coins: list[str], source: Optional[str] = "oxarchive") -> dict[str, dict[str, int]]:
    """{coin: {YYYY-MM: rows}} for the given source (None = every source; 'live' includes the
    pre-v16 rows whose source is NULL)."""
    out: dict[str, dict[str, int]] = {}
    for coin in coins:
        cond = " AND (source=:s OR source IS NULL)" if source == "live" else (" AND source=:s" if source else "")
        q = ("SELECT DATE_FORMAT(FROM_UNIXTIME(ts/1000), '%Y-%m') AS m, COUNT(*) FROM strat_liquidations "
             "WHERE coin=:c" + cond + " GROUP BY m ORDER BY m")
        rows = (await session.execute(text(q), {"c": coin, "s": source})).all()
        out[coin] = {str(m): int(n) for m, n in rows}
    return out


# --------------------------------------------------------------------------- map history
def bands_from_fills(fills: list[dict], mid: float) -> dict[tuple[int, str], dict]:
    """0.25% fixed-grid bands (same grid as pools.liquidation_clusters) over fill prices."""
    if mid <= 0:
        return {}
    width = mid * BAND_PCT
    bands: dict[tuple[int, str], dict] = {}
    for f in fills:
        px = float(f["px"])
        if px <= 0:
            continue
        b = int(px // width)
        d = bands.setdefault((b, f["side"]), {"notional": 0.0, "pxn": 0.0, "count": 0})
        n = abs(float(f["notional"] or 0))
        d["notional"] += n
        d["pxn"] += n * px
        d["count"] += 1
    return bands


async def rebuild_map_from_fills(session, coin: str, start_ms: int, end_ms: int, horizon_ms: int = MAP_HORIZON_MS) -> int:
    """For every 15m boundary T in [start, end]: the positions "later liquidated" =
    fills in (T, T+horizon]; their fill prices bucketed into 0.25% bands of the
    boundary's mid (last 15m close from strat_replay_candles, else strat_candles).
    Rows written with source='fills'; existing rows for the window are replaced."""
    fills = (await session.execute(text(
        "SELECT ts, side, px, notional FROM strat_liquidations WHERE coin=:c AND ts>:a AND ts<=:b ORDER BY ts"),
        {"c": coin, "a": start_ms, "b": end_ms + horizon_ms})).mappings().all()
    fills = [dict(r) for r in fills]
    closes = await _closes(session, coin, start_ms, end_ms)
    if not fills or not closes:
        return 0
    await session.execute(text(
        "DELETE FROM strat_liq_map_hist WHERE coin=:c AND source='fills' AND ts>=:a AND ts<=:b"),
        {"c": coin, "a": start_ms, "b": end_ms})
    import bisect
    fts = [f["ts"] for f in fills]
    out: list[dict] = []
    n = 0
    t = (start_ms // M15) * M15
    while t <= end_ms:
        mid = closes.get(t)
        if mid:
            i, j = bisect.bisect_right(fts, t), bisect.bisect_right(fts, t + horizon_ms)
            for (b, side), d in bands_from_fills(fills[i:j], mid).items():
                out.append({"coin": coin, "ts": t, "side": side, "band_idx": b,
                            "band_px": d["pxn"] / d["notional"] if d["notional"] > 0 else (b + 0.5) * mid * BAND_PCT,
                            "notional": d["notional"], "count": d["count"], "source": "fills"})
        if len(out) >= 2000:
            await _insert_map(session, out)
            n += len(out)
            out = []
        t += M15
    if out:
        await _insert_map(session, out)
        n += len(out)
    await session.commit()
    return n


async def persist_live_map(session, coin: str, boundary_ms: int, positions_rows: list[dict], mid: float) -> int:
    """Live worker (D-66): the live liquidation map (analytics_positions liq_px rows)
    bucketed onto the same 0.25% grid and stored with source='live' at each 15m
    boundary, so band_p80 keeps a map history once the archive/fills rows age out."""
    fills = [{"px": r.get("liq_px"), "side": r.get("side"), "notional": r.get("notional")}
             for r in positions_rows if r.get("liq_px") and r.get("side")]
    rows = [{"coin": coin, "ts": boundary_ms, "side": side, "band_idx": b,
             "band_px": d["pxn"] / d["notional"] if d["notional"] > 0 else (b + 0.5) * mid * BAND_PCT,
             "notional": d["notional"], "count": d["count"], "source": "live"}
            for (b, side), d in bands_from_fills(fills, mid).items() if d["notional"] > 0]
    if rows:
        await _insert_map(session, rows)
    return len(rows)


async def rebuild_map_from_levels(session, coin: str, start_ms: int, end_ms: int, client: Client) -> int:
    """0xArchive projected-levels snapshots (position-based, ~5 min cadence) → the
    snapshot at/just before each 15m boundary, re-bucketed onto the 0.25% grid.
    source='levels'."""
    await session.execute(text(
        "DELETE FROM strat_liq_map_hist WHERE coin=:c AND source='levels' AND ts>=:a AND ts<=:b"),
        {"c": coin, "a": start_ms, "b": end_ms})
    n = 0
    pending: Optional[dict] = None
    t = (start_ms // M15) * M15
    out: list[dict] = []
    async for snap in client.levels_history(coin, start_ms, end_ms + M15):
        sts = _any_ms(snap.get("snapshot_ts"))
        if sts <= 0:
            continue
        while t <= end_ms and sts > t:
            if pending is not None:
                out += _levels_rows(coin, t, pending)
            t += M15
        pending = snap
        if len(out) >= 2000:
            await _insert_map(session, out)
            n += len(out)
            out = []
    while t <= end_ms and pending is not None:
        out += _levels_rows(coin, t, pending)
        t += M15
    if out:
        await _insert_map(session, out)
        n += len(out)
    await session.commit()
    return n


def _levels_rows(coin: str, t: int, snap: dict) -> list[dict]:
    mid = float(snap.get("mid_price") or 0)
    if mid <= 0:
        return []
    width = mid * BAND_PCT
    bands: dict[tuple[int, str], dict] = {}
    for lv in snap.get("levels") or []:
        px = float(lv.get("price") or 0)
        if px <= 0:
            continue
        b = int(px // width)
        for side, nk, ck in (("long", "long_notional", "long_count"), ("short", "short_notional", "short_count")):
            nv = float(lv.get(nk) or 0)
            if nv <= 0:
                continue
            d = bands.setdefault((b, side), {"notional": 0.0, "pxn": 0.0, "count": 0})
            d["notional"] += nv
            d["pxn"] += nv * px
            d["count"] += int(lv.get(ck) or 0)
    return [{"coin": coin, "ts": t, "side": side, "band_idx": b, "band_px": d["pxn"] / d["notional"],
             "notional": d["notional"], "count": d["count"], "source": "levels"} for (b, side), d in bands.items()]


async def _insert_map(session, rows: list[dict]) -> None:
    await session.execute(text(
        "INSERT INTO strat_liq_map_hist (coin, ts, side, band_px, band_idx, notional, count, source) "
        "VALUES (:coin, :ts, :side, :band_px, :band_idx, :notional, :count, :source) "
        "ON DUPLICATE KEY UPDATE band_px=VALUES(band_px), notional=VALUES(notional), count=VALUES(count)"), rows)


async def _closes(session, coin: str, start_ms: int, end_ms: int) -> dict[int, float]:
    """15m close per boundary: replay candles (binance) first, live store as fallback."""
    out: dict[int, float] = {}
    for tbl in ("strat_replay_candles", "strat_candles"):
        rows = (await session.execute(text(
            f"SELECT ts, c FROM {tbl} WHERE coin=:c AND tf='15m' AND ts>=:a AND ts<=:b"),
            {"c": coin, "a": start_ms - M15, "b": end_ms + M15})).all()
        for ts, c in rows:
            b = (int(ts) + 1) // M15 * M15          # close ts 09:14:59.999 → boundary 09:15
            out.setdefault(b, float(c))
    return out


async def map_rows(session, coin: str, ts: int, source: Optional[str] = None) -> list[dict]:
    """The reconstructed map at boundary `ts` as pools.liquidation_clusters rows
    ({liq_px, notional, side, wallet}); prefers the position-based sources —
    'levels' (0xArchive snapshots), then 'live' (our sampler) — and only then the
    forward-fills proxy 'fills' (D-69; same order as calibration.MAP_SOURCE_PRECEDENCE)."""
    src = source
    if src is None:
        for cand in ("levels", "live", "fills"):
            n = (await session.execute(text(
                "SELECT 1 FROM strat_liq_map_hist WHERE coin=:c AND ts=:t AND source=:s LIMIT 1"),
                {"c": coin, "t": ts, "s": cand})).scalar()
            if n:
                src = cand
                break
    if src is None:
        return []
    rows = (await session.execute(text(
        "SELECT band_px, notional, side, count FROM strat_liq_map_hist WHERE coin=:c AND ts=:t AND source=:s"),
        {"c": coin, "t": ts, "s": src})).all()
    return [{"liq_px": float(px), "notional": float(n), "side": side, "wallet": None, "count": int(cnt)}
            for px, n, side, cnt in rows]


# --------------------------------------------------------------------------- coverage
async def live_coverage(session, coin: str, days: int = 3, now_ms: Optional[int] = None) -> dict:
    """Live-feed coverage vs the archive over the last `days`: notional and row
    ratios (live / oxarchive) for the same window. The archive side is the full
    archive STREAM measured by the last load_liquidations in this process for the
    same window (D-70: stored archive rows exclude the ones shadowed by live rows,
    so a DB-only count would overstate coverage); DB rows are the fallback, marked
    `archive_basis='db'`. `None` when the archive has no rows there."""
    now_ms = now_ms or int(dt.datetime.now(dt.timezone.utc).timestamp() * 1000)
    a = now_ms - days * DAY_MS
    q = ("SELECT COUNT(*), COALESCE(SUM(notional), 0) FROM strat_liquidations WHERE coin=:c AND ts>:a AND ts<=:b AND ")
    live = (await session.execute(text(q + "(source IS NULL OR source='live')"), {"c": coin, "a": a, "b": now_ms})).first()
    ln, lv = int(live[0]), float(live[1])
    sc = _stream_coverage.get(coin)
    if sc and sc["days"] == days and abs(sc["now_ms"] - now_ms) <= 3_600_000:
        an, av, basis = int(sc["archive_rows"]), float(sc["archive_notional"]), "stream"
    else:
        arch = (await session.execute(text(q + "source='oxarchive'"), {"c": coin, "a": a, "b": now_ms})).first()
        an, av, basis = int(arch[0]), float(arch[1]), "db"
    return {"coin": coin, "days": days, "live_rows": ln, "live_notional": lv, "archive_rows": an, "archive_notional": av,
            "archive_basis": basis,
            "coverage_notional": (lv / av) if av > 0 else None, "coverage_rows": (ln / an) if an > 0 else None}
