"""Flow-events engine (ANALYTICS_BUILD_REPORT Phase 1.3).

Diffs each sweep cycle against the wallet's previous cycle per asset and emits
analytics_flow_events rows: OPEN / CLOSE / INCREASE / REDUCE / FLIP.

Ref-price honesty (spec 1.3): sweep resolution never sees the true fill —
  OPEN      ref = venue avg entry of the new position   (ref_px_approx=0: the
            first cycle after an open, avg entry IS the open's fill average)
  CLOSE     ref = live mid at diff time                 (ref_px_approx=1)
  INCREASE/REDUCE/FLIP ref = live mid at diff time      (ref_px_approx=1)

ws unification: real fill events already recorded by the HL tracker
(leader_trade_events, exchange='hl') are mapped into the same table at
resolution 'ws' — opened→OPEN, closed→CLOSE, increased→INCREASE,
reduced→REDUCE. src_event_id (UNIQUE) anchors the mapping so a row is never
mapped twice. Mapping is forward-only from service boot (no historical
backfill — those events pre-date the position baseline and cannot be diffed
against anything).
  hl_ws sources carry a real fill price        -> ref_px_approx=0
  hl_poll closes label the avg entry, not exit -> ref_px_approx=1
  size_before/size_after are unknowable from a bare event row -> NULL, never
  invented.

DEDUPE KEY (spec 1.3, documented): a sweep-derived event is SUPPRESSED when a
'ws'-resolution event exists for the same (wallet, asset, direction-class)
with detected_at inside the current diff window (prev cycle ts .. now), where
direction-class is build = {OPEN, INCREASE} and cut = {CLOSE, REDUCE}. A FLIP
is suppressed when the window holds ws events of BOTH classes for that
wallet+asset (a real flip produces a close fill and an open fill).

First cycle after process boot = baseline only: the sweep passes
emit_events=False (snapshot-skip discipline, identical to the trackers).
"""
from datetime import datetime

from sqlalchemy import text

from app.db.database import get_session_factory
from app.utils.logger import get_logger

logger = get_logger(__name__)

SIZE_EPS = 1e-4          # relative size-change threshold (matches tracker's 0.01%)
# Emit floor for INCREASE/REDUCE only (dev cycle-2 evidence 2026-08-26: 404 of
# 624 change events were < $1000 — MM requote jitter from 8 wallets, not flow).
# OPEN/CLOSE/FLIP always emit; positions rows still record every size change.
MIN_CHANGE_NOTIONAL = 1000.0

BUILD = ("OPEN", "INCREASE")
CUT = ("CLOSE", "REDUCE")

_last_mapped_event_id: int | None = None


def _mid(asset: str) -> float | None:
    from app.services.hyperliquid import prices as hl_prices
    return hl_prices.get_mid(asset)


def diff_wallet(wallet: str, prev: dict[str, dict], cur: dict[str, dict],
                detected_at: datetime) -> list[dict]:
    """Pure diff of one wallet's per-asset positions between two cycles.
    prev/cur: {asset: {side, size, notional, entry_px}} — flat markers and
    zero-size rows must already be stripped by the caller."""
    events: list[dict] = []

    def ev(asset: str, etype: str, side: str, before: float, after: float,
           ndelta: float, ref: float | None, approx: bool) -> dict:
        return {"detected_at": detected_at, "wallet": wallet, "asset": asset,
                "event_type": etype, "side": side, "size_before": before,
                "size_after": after, "notional_delta": round(ndelta, 2),
                "ref_px": ref, "ref_px_approx": 1 if approx else 0,
                "resolution": "20m"}

    for asset, c in cur.items():
        p = prev.get(asset)
        if p is None:
            events.append(ev(asset, "OPEN", c["side"], 0.0, c["size"],
                             c["notional"], c["entry_px"] or None, False))
        elif p["side"] != c["side"]:
            events.append(ev(asset, "FLIP", c["side"], p["size"], c["size"],
                             c["notional"] + p["notional"], _mid(asset), True))
        else:
            rel = abs(c["size"] - p["size"]) / p["size"] if p["size"] else 0.0
            if rel <= SIZE_EPS:
                continue
            # floor measured as the SIZE change in dollars (unit price from the
            # current cycle's venue notional) — a pure price move can't trip it
            unit_px = (c["notional"] / c["size"]) if c["size"] else 0.0
            if abs(c["size"] - p["size"]) * unit_px < MIN_CHANGE_NOTIONAL:
                continue
            if c["size"] > p["size"]:
                events.append(ev(asset, "INCREASE", c["side"], p["size"], c["size"],
                                 c["notional"] - p["notional"], _mid(asset), True))
            else:
                events.append(ev(asset, "REDUCE", c["side"], p["size"], c["size"],
                                 c["notional"] - p["notional"], _mid(asset), True))
    for asset, p in prev.items():
        if asset not in cur:
            events.append(ev(asset, "CLOSE", p["side"], p["size"], 0.0,
                             -p["notional"], _mid(asset), True))
    return events


async def suppress_ws_duplicates(events: list[dict], window_start: datetime) -> tuple[list[dict], int]:
    """Apply the documented dedupe key against ws-resolution rows in the diff
    window. Returns (kept_events, suppressed_count)."""
    if not events:
        return events, 0
    wallets = sorted({e["wallet"] for e in events})
    placeholders = ",".join(f":w{i}" for i in range(len(wallets)))
    params: dict = {f"w{i}": w for i, w in enumerate(wallets)}
    params["ws_start"] = window_start
    sf = get_session_factory()
    async with sf() as s:
        rows = (await s.execute(text(
            "SELECT wallet, asset, event_type FROM analytics_flow_events "
            f"WHERE resolution = 'ws' AND detected_at >= :ws_start "
            f"AND wallet IN ({placeholders})"
        ), params)).all()
    seen: dict[tuple[str, str], set] = {}
    for w, a, et in rows:
        cls = "build" if et in BUILD else ("cut" if et in CUT else None)
        if cls:
            seen.setdefault((w.lower(), a), set()).add(cls)

    kept, suppressed = [], 0
    for e in events:
        classes = seen.get((e["wallet"].lower(), e["asset"]), set())
        if e["event_type"] in BUILD and "build" in classes:
            suppressed += 1
        elif e["event_type"] in CUT and "cut" in classes:
            suppressed += 1
        elif e["event_type"] == "FLIP" and {"build", "cut"} <= classes:
            suppressed += 1
        else:
            kept.append(e)
    return kept, suppressed


async def persist(events: list[dict]) -> int:
    if not events:
        return 0
    sf = get_session_factory()
    async with sf() as s:
        await s.execute(text(
            "INSERT INTO analytics_flow_events "
            "(detected_at, wallet, asset, event_type, side, size_before, "
            " size_after, notional_delta, ref_px, ref_px_approx, resolution) "
            "VALUES (:detected_at, :wallet, :asset, :event_type, :side, "
            " :size_before, :size_after, :notional_delta, :ref_px, "
            " :ref_px_approx, :resolution)"), events)
        await s.commit()
    return len(events)


async def map_ws_events(cohort_wallets: set[str]) -> int:
    """Map NEW leader_trade_events hl rows (cohort wallets only) into the flow
    table at resolution 'ws'. Forward-only from boot (module doc)."""
    global _last_mapped_event_id
    sf = get_session_factory()
    async with sf() as s:
        if _last_mapped_event_id is None:
            _last_mapped_event_id = (await s.execute(text(
                "SELECT COALESCE(MAX(id), 0) FROM leader_trade_events "
                "WHERE exchange = 'hl'"))).scalar() or 0
            logger.info("flow_events ws-mapping baseline: leader_trade_events id > %d",
                        _last_mapped_event_id)
            return 0
        rows = (await s.execute(text(
            "SELECT id, trader_wallet, symbol, event_type, side, size, price, "
            " source, detected_at FROM leader_trade_events "
            "WHERE exchange = 'hl' AND id > :last ORDER BY id LIMIT 2000"
        ), {"last": _last_mapped_event_id})).all()

    if not rows:
        return 0
    typemap = {"opened": "OPEN", "closed": "CLOSE",
               "increased": "INCREASE", "reduced": "REDUCE"}
    out = []
    max_id = _last_mapped_event_id
    for (eid, wallet, symbol, etype, side, size, price, source, detected_at) in rows:
        max_id = max(max_id, eid)
        et = typemap.get(etype)
        if et is None or (wallet or "").lower() not in cohort_wallets:
            continue
        px = float(price) if price is not None else None
        sz = float(size) if size is not None else None
        ndelta = None
        if px is not None and sz is not None:
            ndelta = round(px * sz * (1 if et in BUILD else -1), 2)
        out.append({
            "detected_at": detected_at, "wallet": wallet.lower(),
            "asset": (symbol or "").upper(), "event_type": et,
            "side": side or "", "size_before": None, "size_after": None,
            "notional_delta": ndelta, "ref_px": px,
            # hl_ws = real fill px; hl_poll closes label avg entry, not exit
            "ref_px_approx": 0 if source == "hl_ws" else 1,
            "resolution": "ws", "src_event_id": eid,
        })
    inserted = 0
    if out:
        sf = get_session_factory()
        async with sf() as s:
            await s.execute(text(
                "INSERT IGNORE INTO analytics_flow_events "
                "(detected_at, wallet, asset, event_type, side, size_before, "
                " size_after, notional_delta, ref_px, ref_px_approx, resolution, "
                " src_event_id) "
                "VALUES (:detected_at, :wallet, :asset, :event_type, :side, "
                " :size_before, :size_after, :notional_delta, :ref_px, "
                " :ref_px_approx, :resolution, :src_event_id)"), out)
            await s.commit()
        inserted = len(out)
    _last_mapped_event_id = max_id
    return inserted
