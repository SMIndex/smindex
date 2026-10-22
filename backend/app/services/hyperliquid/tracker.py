"""Hyperliquid real-time trade detection: hybrid ws + polling watcher.

Tracked set = distinct HL wallets in watchlists(exchange='hl') ∪ active
copy_subscriptions(exchange='hl'), re-evaluated every 60s.

WS TIER (venue budget: 10 unique users per IP across user-specific
subscriptions): wallets ranked by watcher count; top <=10 get `userFills`
subscriptions on ONE shared ws connection. The first message per subscription
carries isSnapshot=true: that is HISTORY: we record fill ids (tid) as the
baseline and never emit events from it. On reconnect we resubscribe and the
fresh snapshot is diffed against seen tids so nothing double-fires; fills newer
than the drop are emitted, plus one userFillsByTime read per wallet for the gap
(source 'hl_ws_gap': recorded and alerted, never auto-copied). Keepalive is
Hyperliquid's own {"method":"ping"} every 30 s; 90 s of silence reconnects.

POLLING TIER: tracked wallets beyond the ws budget are diffed via
`clearinghouseState` every 15s with the same discipline as the Perpl
trader_tracker: first sighting seeds a silent baseline; a failed read is
NEVER treated as a closed position; per-minute request volume is logged.

Both tiers emit leader_trade_events rows with exchange='hl' and
unique_event_key 'hl:{wallet}:{coin}:{type}:{side}:{price}:{size}'
(source 'hl_ws' | 'hl_poll', raw payload attached). Entry/exit alert via the
phase-1 telegram queue (venue-tagged) + /ws/feed broadcasts; increase/reduce
recorded but UNALERTED (parity with the Perpl tracker).
"""
import asyncio
import json
import time
from datetime import datetime

import websockets
from sqlalchemy import select

from app.db.database import get_session_factory
from app.db.copy_models import Watchlist, CopySubscription, MarketMap
from app.db.models import TraderActivity
from app.services.copy import leader_events
from app.services.hyperliquid import client as hl_client
from app.utils.logger import get_logger

logger = get_logger(__name__)

WS_URL = "wss://api.hyperliquid.xyz/ws"
WS_TIER_MAX = 10          # HL hard cap: unique users per IP (user subscriptions)
POLL_INTERVAL = 15.0
# Hyperliquid's own keepalive: send {"method":"ping"}, it answers channel "pong".
# The websockets library ping (20 s / 10 s timeout) dropped the tier 129 times
# in 24 h on 2026-10-21 ("keepalive ping timeout", mostly during event loop
# stalls), so the library ping is off and HL_SILENT_SEC of silence reconnects.
HL_PING_SEC = 30.0
HL_SILENT_SEC = 90.0       # no frame at all (fills or pong) for this long: reconnect
GAP_MARGIN_MS = 5_000      # gap read starts this long before the drop
TRACKED_REFRESH = 60.0
SEEN_TIDS_MAX = 2000      # per wallet, ring-capped

_task: asyncio.Task | None = None
_poll_task: asyncio.Task | None = None
_refresh_task: asyncio.Task | None = None

_tracked: list[str] = []          # ranked by watcher count desc
_ws_wallets: list[str] = []       # current ws tier (<=10)
_seen_tids: dict[str, set] = {}   # wallet -> seen fill ids
_snapshot_done: dict[str, bool] = {}
_poll_baseline: dict[str, dict[str, dict]] = {}  # wallet -> {coin: {szi, entryPx}}
_ws_conn = None                    # live ws (for test-forced reconnect)
_mmap: dict[str, int | None] = {}
_mmap_at = 0.0

stats = {
    "ws_fills_seen": 0, "ws_snapshot_fills_skipped": 0, "events_recorded": 0,
    "events_duplicate": 0, "alerts_enqueued": 0, "poll_requests": 0,
    "poll_events": 0, "reconnects": 0, "mm_rows_skipped": 0,
    "drops_keepalive": 0, "drops_silent": 0, "drops_other": 0,
    "gap_recoveries": 0, "gap_reads": 0, "gap_read_errors": 0, "gap_fills_recovered": 0,
}
_drop_ms: int | None = None        # when the last live ws connection dropped (gap start)
_live_tier: set[str] = set()       # wallets on the last live connection (only they have a gap)
# Market-maker wallets on the tracked set (same definition as analytics
# cohort clause A: maker_ratio >= 0.6 AND trades_7d >= 500, trader_fill_stats).
# Their increase/reduce rows are not persisted: one maker wallet wrote 186,231
# of 285K leader_trade_events rows on 2026-09-21 (every partial fill a row).
# Opens/closes still record and alert. No copy path reads HL events.
_mm_wallets: set[str] = set()
# Leaders with an active or paused auto-copy subscription: always tracked,
# ranked FIRST for the 10 real-time ws slots (spec D4), never MM-suppressed.
_auto_copy_leaders: set[str] = set()
_poll_req_window: list[float] = []
# Newest fill time (ms) seen per tracked wallet, every fill incl. the ws
# snapshot and market-maker increase/reduce rows that are not persisted.
# Read by hl/last_trade.py for the Discover "last active" (zero requests).
last_fill_ms: dict[str, int] = {}


def _note_fill_time(wallet: str, ms: int | None) -> None:
    if ms and ms > last_fill_ms.get(wallet, 0):
        last_fill_ms[wallet] = ms


def get_stats() -> dict:
    return {**stats, "tracked": len(_tracked), "ws_tier": list(_ws_wallets),
            "poll_tier_size": max(0, len(_tracked) - len(_ws_wallets))}


async def _market_map() -> dict[str, int | None]:
    global _mmap, _mmap_at
    if time.monotonic() - _mmap_at > 60 or not _mmap:
        sf = get_session_factory()
        async with sf() as session:
            rows = (await session.execute(
                select(MarketMap).where(MarketMap.exchange == "hl")
            )).scalars().all()
        _mmap = {r.native_symbol.upper(): r.perpl_market_id for r in rows}
        _mmap_at = time.monotonic()
    return _mmap


async def _load_tracked() -> list[str]:
    """Distinct HL wallets ranked by watcher count (watchlist + active subs)."""
    sf = get_session_factory()
    counts: dict[str, int] = {}
    async with sf() as session:
        wl = (await session.execute(
            select(Watchlist.trader_wallet).where(Watchlist.exchange == "hl")
        )).scalars().all()
        subs = (await session.execute(
            select(CopySubscription.trader_wallet).where(
                CopySubscription.exchange == "hl",
                CopySubscription.status == "active",
            )
        )).scalars().all()
    for w in list(wl) + list(subs):
        w = w.lower()
        counts[w] = counts.get(w, 0) + 1
    try:
        await _load_auto_copy_leaders()
    except Exception as exc:
        logger.warning("HL tracker: auto-copy leader lookup failed: %s", exc)
    for w in _auto_copy_leaders:
        counts[w] = counts.get(w, 0) + AUTO_COPY_PRIORITY
    try:
        await _load_mm_wallets(list(counts))
    except Exception as exc:
        # keep the previous set; never let this break the tracked-set refresh
        logger.warning("HL tracker: market-maker lookup failed: %s", exc)
    return [w for w, _ in sorted(counts.items(), key=lambda kv: -kv[1])]


AUTO_COPY_PRIORITY = 100000


async def _load_auto_copy_leaders() -> None:
    global _auto_copy_leaders
    from sqlalchemy import text
    sf = get_session_factory()
    async with sf() as session:
        rows = (await session.execute(text(
            "SELECT DISTINCT leader_wallet FROM auto_copy_subs WHERE status IN ('active','paused')"))).scalars().all()
    _auto_copy_leaders = {w.lower() for w in rows}


def ws_slot_available(leader: str) -> tuple[bool, str]:
    """Auto-copy button availability (spec D4): auto-copied leaders take the
    real-time slots first; a new one fits while fewer than WS_TIER_MAX exist."""
    lw = leader.lower()
    if lw in _auto_copy_leaders:
        return True, "already on the real-time tier"
    if len(_auto_copy_leaders) >= WS_TIER_MAX:
        return False, f"all {WS_TIER_MAX} real-time slots are used by auto-copied traders"
    return True, "a real-time slot is free"


async def _load_mm_wallets(wallets: list[str]) -> None:
    global _mm_wallets
    if not wallets:
        _mm_wallets = set()
        return
    from sqlalchemy import text
    from app.services.analytics.cohort import MM_MAKER_RATIO_MIN, MM_TRADES_7D_MIN
    params = {f"w{i}": w for i, w in enumerate(wallets)}
    params.update({"mr": MM_MAKER_RATIO_MIN, "tr": MM_TRADES_7D_MIN})
    sf = get_session_factory()
    async with sf() as session:
        rows = (await session.execute(text(
            "SELECT wallet_address FROM trader_fill_stats "
            "WHERE maker_ratio >= :mr AND trades_7d >= :tr "
            f"AND wallet_address IN ({','.join(':w%d' % i for i in range(len(wallets)))})"),
            params)).scalars().all()
    new = {w.lower() for w in rows}
    if new != _mm_wallets:
        logger.info("HL tracker: %d market-maker wallets (increase/reduce rows not persisted): %s",
                    len(new), sorted(w[:10] for w in new))
    _mm_wallets = new


# Alert burst-merge (HL_QUALITY_REPORT A3d): several fills for the same
# wallet+coin+side within the window send ONE telegram alert with summed size,
# volume-weighted price and summed closedPnl. Event ROWS are never merged,
# only the notification. Live evidence justifying this: 3 exit alerts for one
# wallet inside 5 minutes on 2026-08-12.
ALERT_MERGE_WINDOW_SEC = 20.0
_alert_buf: dict[tuple, dict] = {}


async def _flush_alert(key: tuple) -> None:
    await asyncio.sleep(ALERT_MERGE_WINDOW_SEC)
    buf = _alert_buf.pop(key, None)
    if not buf:
        return
    wallet, coin, side, kind = key
    size = buf["size"]
    px = (buf["notional"] / size) if size else (buf["last_px"] or 0)
    from app.services import telegram_bot
    try:
        if kind == "opened":
            await telegram_bot.notify_leader_entry(
                wallet, buf["market_id"], coin, side, px or 0, size or 0,
                venue="Hyperliquid", exchange="hl",
                size_label=buf["size_label"], fills_count=buf["count"],
                action=buf.get("action"), post_position=buf.get("post_pos"))
        else:
            await telegram_bot.notify_leader_exit(
                wallet, buf["market_id"], coin, side, px or 0,
                pnl=buf["pnl"], size=size or 0,
                venue="Hyperliquid", exchange="hl",
                size_label=buf["size_label"], price_label=buf["price_label"],
                note=buf["note"], fills_count=buf["count"],
                action=buf.get("action"), post_position=buf.get("post_pos"))
        stats["alerts_enqueued"] += 1
    except Exception:
        logger.exception("hl alert flush failed for %s %s", wallet[:10], coin)


def _queue_alert(wallet: str, market_id: int, coin: str, side: str, kind: str,
                 px: float | None, sz: float | None, *, pnl: float | None = None,
                 size_label: str = "Size", price_label: str = "Exit price",
                 note: str | None = None, action: str | None = None,
                 post_pos: float | None = None) -> None:
    key = (wallet.lower(), coin, side, kind)
    buf = _alert_buf.get(key)
    if buf is None:
        buf = {"market_id": market_id, "size": 0.0, "notional": 0.0, "pnl": None,
               "count": 0, "last_px": px, "size_label": size_label,
               "price_label": price_label, "note": note,
               "action": action, "post_pos": post_pos}
        _alert_buf[key] = buf
        asyncio.get_event_loop().create_task(_flush_alert(key))
    buf["count"] += 1
    if sz:
        buf["size"] += sz
        buf["notional"] += (px or 0) * sz
    if px:
        buf["last_px"] = px
    if pnl is not None:
        buf["pnl"] = (buf["pnl"] or 0.0) + pnl
    if note:
        buf["note"] = note
    if action and (buf.get("action") is None or "Flipped" in action):
        buf["action"] = action        # a flip inside a burst labels the burst
    if post_pos is not None:
        buf["post_pos"] = post_pos    # last fill's post-position wins


async def _emit_event(wallet: str, coin: str, event_type: str, side: str,
                      price: float | None, size: float | None,
                      source: str, raw: dict | None,
                      closed_pnl: float | None = None,
                      size_label: str = "Size",
                      price_label: str = "Exit price",
                      note: str | None = None,
                      action: str | None = None,
                      post_pos: float | None = None) -> None:
    if (event_type in ("increased", "reduced") and wallet.lower() in _mm_wallets
            and wallet.lower() not in _auto_copy_leaders):   # a copied leader's adds/cuts are needed
        stats["mm_rows_skipped"] += 1
        return
    mmap = await _market_map()
    market_id = mmap.get(coin.upper()) or 0   # 0 = no Perpl market (unmapped HL coin)
    # Dedupe key shape (spec): hl:{wallet}:{coin}:{type}:{side}:{price}:{size}
    key = f"hl:{wallet.lower()}:{coin.upper()}:{event_type}:{side}:" \
          f"{'' if price is None else format(float(price), '.10g')}:" \
          f"{'' if size is None else format(float(size), '.10g')}"
    evt, created = await leader_events.record_event(
        wallet, market_id, coin.upper(), side, event_type,
        size=size, price=price, source=source, raw=raw,
        unique_key=key, exchange="hl",
    )
    if not created:
        stats["events_duplicate"] += 1
        return
    stats["events_recorded"] += 1
    if source == "hl_ws" and wallet.lower() in _auto_copy_leaders:
        from app.services.copy_auto import engine as _auto_engine
        asyncio.get_event_loop().create_task(_auto_engine.on_leader_event(evt))
    if source == "hl_poll":
        stats["poll_events"] += 1

    # trader_activity row + browser broadcasts (entry/exit only, like Perpl)
    if event_type in ("opened", "closed"):
        activity = {
            "type": "entry" if event_type == "opened" else "exit",
            "wallet": wallet.lower(),
            "market_id": market_id,
            "symbol": coin.upper(),
            "side": side,
            "size": size,
            "entry_price": price,
            "exchange": "hl",
            "timestamp": time.time(),
        }
        try:
            sf = get_session_factory()
            async with sf() as session:
                session.add(TraderActivity(
                    exchange="hl",
                    activity_type=activity["type"], wallet_address=wallet.lower(),
                    market_id=market_id, symbol=coin.upper(), side=side,
                    size=size, entry_price=price, timestamp=datetime.utcnow(),
                ))
                await session.commit()
        except Exception:
            logger.exception("hl trader_activity write failed for %s", wallet[:10])
        try:
            from app.ws.client_feed import client_manager
            await client_manager.broadcast("trader_activity", activity, market_id=market_id or None)
            await client_manager.broadcast("copy_updates", {
                "type": "leader_entry" if event_type == "opened" else "leader_exit",
                **activity,
            })
        except Exception:
            logger.exception("hl broadcast failed")

        # Telegram: buffered through the burst-merge layer (one alert per
        # wallet+coin+side per window; real closedPnl or NO pnl line, never 0)
        try:
            _queue_alert(wallet, market_id, coin.upper(), side, event_type,
                         price, size, pnl=closed_pnl, size_label=size_label,
                         price_label=price_label, note=note, action=action,
                         post_pos=post_pos)
        except Exception:
            logger.exception("hl alert enqueue failed for %s %s", wallet[:10], coin)


def _classify_fill(fill: dict) -> tuple[str, str] | None:
    """Map an HL fill to (event_type, side) using dir + startPosition."""
    d = str(fill.get("dir", ""))
    try:
        start = float(fill.get("startPosition") or 0)
        sz = float(fill.get("sz") or 0)
    except (TypeError, ValueError):
        start, sz = 0.0, 0.0
    if d == "Open Long":
        return ("opened" if start == 0 else "increased", "long")
    if d == "Open Short":
        return ("opened" if start == 0 else "increased", "short")
    if d == "Close Long":
        return ("closed" if abs(start) - sz <= 1e-9 else "reduced", "long")
    if d == "Close Short":
        return ("closed" if abs(start) - sz <= 1e-9 else "reduced", "short")
    if d in ("Long > Short", "Short > Long"):
        # flip: report the close side; the open side arrives as its own fill
        return ("closed", "long" if d.startswith("Long") else "short")
    return None


def liquidation_row(wallet: str, f: dict) -> dict | None:
    """strat_liquidations row from a userFills fill carrying `liquidation`
    ({markPx, method, liquidatedUser}): the ONLY liquidation source the public
    HL ws offers (doc 00 §3). `side` = the side that was LIQUIDATED: a liquidated
    long is closed by a sell, so the liquidated party's fill side A → long,
    B → short; when the tracked wallet is the counterparty (the common case,
    it is the maker the liquidation traded through) its side is the opposite.
    Returns None for fills without the field. Pure; unit-tested."""
    liq = f.get("liquidation")
    if not isinstance(liq, dict):
        return None
    try:
        px = float(f.get("px") or 0)
        sz = float(f.get("sz") or 0)
        ts = int(f.get("time") or 0)
    except (TypeError, ValueError):
        return None
    if not px or not sz or not ts:
        return None
    lu = str(liq.get("liquidatedUser") or "").lower() or None
    own = (lu is None) or (lu == wallet.lower())
    fill_side = f.get("side")                     # A = sell, B = buy (the tracked wallet's side)
    liquidated_sell = (fill_side == "A") if own else (fill_side == "B")
    try:
        mark = float(liq.get("markPx")) if liq.get("markPx") is not None else None
    except (TypeError, ValueError):
        mark = None
    return {
        "ts": ts, "coin": str(f.get("coin", "")).upper(), "side": "long" if liquidated_sell else "short",
        "px": px, "sz": sz, "notional": px * sz, "liquidated_user": lu or wallet.lower(),
        "mark_px": mark, "method": str(liq.get("method") or "")[:16] or None, "coverage": "partial",
    }


async def _ingest_liquidation(wallet: str, f: dict) -> None:
    """Liquidation-flagged fill → strat_liquidations (coverage='partial': only the
    <=10 wallets on this ws tier are seen, mostly as counterparties). Snapshot
    fills are ingested too (they are real, timestamped history), an existence
    check on (ts, coin, liquidated_user, px, sz) makes reconnect/resubscribe
    replays idempotent. Isolated: a failure here never touches the fill path."""
    from app.config import settings
    if not settings.STRATEGIES_ENABLED:
        return
    row = liquidation_row(wallet, f)
    if row is None or row["coin"] not in _strategy_assets():
        return
    from app.db.strategy_models import StratLiquidation
    sf = get_session_factory()
    async with sf() as session:
        exists = (await session.execute(
            select(StratLiquidation.id).where(
                StratLiquidation.ts == row["ts"], StratLiquidation.coin == row["coin"],
                StratLiquidation.liquidated_user == row["liquidated_user"],
                StratLiquidation.px == row["px"], StratLiquidation.sz == row["sz"]).limit(1)
        )).first()
        if exists:
            return
        session.add(StratLiquidation(**row))
        await session.commit()
    stats["liquidations_recorded"] = stats.get("liquidations_recorded", 0) + 1


def _strategy_assets() -> set[str]:
    from app.config import settings
    return set(settings.strategy_assets_list)


async def _handle_fills(wallet: str, fills: list[dict], is_snapshot: bool,
                        source: str = "hl_ws", gap_since_ms: int | None = None) -> list[dict]:
    """Returns the snapshot fills newer than gap_since_ms that were held back
    (not marked seen) so the reconnect gap recovery can emit them."""
    seen = _seen_tids.setdefault(wallet, set())
    held: list[dict] = []
    for f in fills:
        try:
            _note_fill_time(wallet, int(f.get("time") or 0))
        except (TypeError, ValueError):
            pass
        tid = f.get("tid") or f.get("hash") or f"{f.get('oid')}:{f.get('time')}"
        if f.get("liquidation"):
            try:
                await _ingest_liquidation(wallet, f)
            except Exception:
                logger.exception("liquidation ingest failed for %s %s", wallet[:10], f.get("coin"))
        if is_snapshot or not _snapshot_done.get(wallet, False):
            if (is_snapshot and gap_since_ms is not None and tid not in seen
                    and int(f.get("time") or 0) >= gap_since_ms):
                held.append(f)        # traded while the ws was down: emitted by the gap recovery
                continue
            seen.add(tid)
            stats["ws_snapshot_fills_skipped"] += 1
            continue
        if tid in seen:
            continue
        seen.add(tid)
        if len(seen) > SEEN_TIDS_MAX:
            # ring-cap: drop arbitrary old entries (set has no order; fine: the
            # dedupe backstop is the DB unique_event_key)
            for _ in range(len(seen) - SEEN_TIDS_MAX):
                seen.pop()
        stats["ws_fills_seen"] += 1
        cls = _classify_fill(f)
        if not cls:
            continue
        event_type, side = cls
        try:
            px = float(f.get("px") or 0) or None
            sz = float(f.get("sz") or 0) or None
        except (TypeError, ValueError):
            px = sz = None
        # honest alert context (report A3): real closedPnl from the fill,
        # fill-size labeling, flip action, and derivable post-fill position
        closed_pnl = None
        if event_type in ("closed", "reduced"):
            try:
                cp = f.get("closedPnl")
                closed_pnl = float(cp) if cp is not None else None
            except (TypeError, ValueError):
                closed_pnl = None
        note = None
        action = None
        post_pos = None
        try:
            start_signed = float(f.get("startPosition") or 0)
            sz_signed = (sz or 0) if f.get("side") == "B" else -(sz or 0)
            post_pos = start_signed + sz_signed
        except (TypeError, ValueError):
            post_pos = None
        d = str(f.get("dir", ""))
        if ">" in d and sz:
            start = abs(float(f.get("startPosition") or 0))
            if start and sz > start:
                action = (f"Flipped {d.replace(' > ', '→')} "
                          f"(closed {start:g}, opened {sz - start:g})")
                note = action
        try:
            await _emit_event(wallet, str(f.get("coin", "")), event_type, side,
                              px, sz, source, {"fill": f},
                              closed_pnl=closed_pnl, size_label="Fill size",
                              note=note, action=action, post_pos=post_pos)
        except Exception:
            # isolate: one bad row (e.g. schema mismatch) must not tear down
            # the shared ws connection for every tracked wallet
            logger.exception("hl event emit failed for %s %s", wallet[:10], f.get("coin"))
    return held


async def _gap_recover(wallet: str, since_ms: int, held: list[dict]) -> None:
    """One newest-fill REST read for the time the ws was down, plus the fills the
    resubscribe snapshot already carried for that window. Emitted with source
    'hl_ws_gap': recorded and alerted, never auto-copied (the engine only acts
    on 'hl_ws'), deduped by fill id and the DB event key."""
    fills = list(held)
    try:
        resp = await hl_client.post_info(
            {"type": "userFillsByTime", "user": wallet, "startTime": since_ms, "aggregateByTime": False},
            priority=hl_client.CRITICAL, timeout=12.0)
        stats["gap_reads"] += 1
        if resp.status_code == 200:
            body = await hl_client.decode_json(resp)
            if isinstance(body, list):
                fills.extend(body)
        else:
            stats["gap_read_errors"] += 1
    except Exception as exc:
        stats["gap_read_errors"] += 1
        logger.warning("HL tracker gap read failed for %s: %s", wallet[:10], exc)
    seen = _seen_tids.setdefault(wallet, set())
    fresh = []
    for f in sorted(fills, key=lambda x: int(x.get("time") or 0)):
        tid = f.get("tid") or f.get("hash") or f"{f.get('oid')}:{f.get('time')}"
        if tid not in seen and all((g.get("tid") or g.get("hash")) != tid for g in fresh):
            fresh.append(f)
    if fresh:
        stats["gap_fills_recovered"] += len(fresh)
        await _handle_fills(wallet, fresh, False, source="hl_ws_gap")
        logger.info("HL tracker gap recovery: %s %d fill(s) recovered (gap %.0fs)",
                    wallet[:10], len(fresh), (time.time() * 1000 - since_ms) / 1000)


async def _ws_loop() -> None:
    global _ws_conn, _drop_ms, _live_tier
    backoff = 1.0
    while True:
        wallets = list(_ws_wallets)
        if not wallets:
            await asyncio.sleep(5)
            continue
        gap_since = (_drop_ms - GAP_MARGIN_MS) if _drop_ms else None
        live = False
        try:
            async with websockets.connect(WS_URL, ping_interval=None,
                                          close_timeout=5) as ws:
                _ws_conn = ws
                for w in wallets:
                    _snapshot_done[w] = False
                    await ws.send(json.dumps({
                        "method": "subscribe",
                        "subscription": {"type": "userFills", "user": w},
                    }))
                logger.info("HL tracker ws connected (%d user subscriptions)", len(wallets))
                backoff = 1.0
                live = True
                prev_tier, _live_tier = _live_tier, set(wallets)
                if gap_since is None:
                    prev_tier = set()
                last_rx = time.monotonic()
                last_ping = time.monotonic()
                while True:
                    try:
                        raw = await asyncio.wait_for(ws.recv(), timeout=5.0)
                    except asyncio.TimeoutError:
                        raw = None
                    now = time.monotonic()
                    if raw is not None:
                        last_rx = now
                    if now - last_ping >= HL_PING_SEC:
                        await ws.send('{"method":"ping"}')
                        last_ping = now
                    if now - last_rx > HL_SILENT_SEC:
                        stats["drops_silent"] += 1
                        raise ConnectionError(f"no frame for {HL_SILENT_SEC:.0f}s")
                    if list(_ws_wallets) != wallets:
                        logger.info("HL tracker ws tier changed, reconnecting")
                        _drop_ms = int(time.time() * 1000)   # wallets staying on the tier get a gap read
                        break
                    if raw is None:
                        continue
                    try:
                        msg = json.loads(raw)
                    except Exception:
                        continue
                    ch = msg.get("channel")
                    if ch == "userFills":
                        data = msg.get("data") or {}
                        user = str(data.get("user", "")).lower()
                        fills = data.get("fills") or []
                        snap = bool(data.get("isSnapshot"))
                        gs = gap_since if (snap and user in prev_tier) else None
                        held = await _handle_fills(user, fills, snap, gap_since_ms=gs)
                        if snap:
                            _snapshot_done[user] = True
                            logger.info("HL tracker baseline seeded for %s (%d snapshot fills)",
                                        user[:10], len(fills))
                            if gs is not None:
                                stats["gap_recoveries"] += 1
                                # in the background: the REST read must not stall this reader
                                asyncio.get_event_loop().create_task(_gap_recover(user, gap_since, held))
        except asyncio.CancelledError:
            _ws_conn = None
            raise
        except Exception as exc:
            stats["reconnects"] += 1
            text_ = str(exc)
            if "no frame for" in text_:
                pass
            elif "keepalive" in text_:
                stats["drops_keepalive"] += 1
            else:
                stats["drops_other"] += 1
            if live:
                _drop_ms = int(time.time() * 1000)
            logger.warning("HL tracker ws dropped (%s), reconnect in %.0fs", exc, backoff)
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 30.0)
        finally:
            _ws_conn = None


async def _poll_wallet(wallet: str) -> None:
    stats["poll_requests"] += 1
    _poll_req_window.append(time.monotonic())
    try:
        resp = await hl_client.post_info(
            {"type": "clearinghouseState", "user": wallet},
            priority=hl_client.CRITICAL, timeout=12.0)
        resp.raise_for_status()
        state = resp.json()
    except Exception as exc:
        logger.debug("hl poll failed for %s: %s (skipped, baseline untouched)", wallet[:10], exc)
        return
    positions: dict[str, dict] = {}
    for ap in state.get("assetPositions", []):
        p = ap.get("position") or {}
        try:
            szi = float(p.get("szi") or 0)
        except (TypeError, ValueError):
            continue
        if szi == 0:
            continue
        positions[str(p.get("coin", "")).upper()] = {
            "szi": szi, "entry": float(p.get("entryPx") or 0),
        }

    old = _poll_baseline.get(wallet)
    _poll_baseline[wallet] = positions
    if old is None:
        return  # first sighting: silent baseline
    if any(abs(p["szi"] - (old.get(c) or {}).get("szi", 0)) > abs(p["szi"]) * 1e-4 for c, p in positions.items()) \
            or any(c not in positions for c in old):
        _note_fill_time(wallet, int(time.time() * 1000))   # poll tier: detection time

    for coin, pos in positions.items():
        side = "long" if pos["szi"] > 0 else "short"
        if coin not in old:
            await _emit_event(wallet, coin, "opened", side, pos["entry"],
                              abs(pos["szi"]), "hl_poll", {"state": pos},
                              size_label="Position size", post_pos=pos["szi"])
        else:
            prev = old[coin]
            if abs(pos["szi"]) > abs(prev["szi"]) * 1.0001:
                await _emit_event(wallet, coin, "increased", side, pos["entry"],
                                  abs(pos["szi"]), "hl_poll", {"state": pos, "prev": prev})
            elif abs(pos["szi"]) < abs(prev["szi"]) * 0.9999:
                await _emit_event(wallet, coin, "reduced", side, pos["entry"],
                                  abs(pos["szi"]), "hl_poll", {"state": pos, "prev": prev})
    for coin, prev in old.items():
        if coin not in positions:
            side = "long" if prev["szi"] > 0 else "short"
            # poll tier knows neither exit price nor pnl, label the avg entry
            # honestly and send no PnL line (never a fabricated 0)
            await _emit_event(wallet, coin, "closed", side, prev["entry"],
                              abs(prev["szi"]), "hl_poll", {"prev": prev},
                              size_label="Position size",
                              price_label="Avg entry (exit px unknown)",
                              post_pos=0.0)


async def _poll_loop() -> None:
    last_rate_log = time.monotonic()
    while True:
        poll_set = [w for w in _tracked if w not in _ws_wallets]
        for w in poll_set:
            await _poll_wallet(w)
        now = time.monotonic()
        if now - last_rate_log > 60:
            cutoff = now - 60
            _poll_req_window[:] = [t for t in _poll_req_window if t > cutoff]
            logger.info("HL tracker poll tier: %d wallets, %d req/min",
                        len(poll_set), len(_poll_req_window))
            last_rate_log = now
        await asyncio.sleep(POLL_INTERVAL)


async def _refresh_loop() -> None:
    global _tracked, _ws_wallets
    while True:
        try:
            _tracked = await _load_tracked()
            _ws_wallets = _tracked[:WS_TIER_MAX]
        except Exception:
            logger.exception("HL tracker tracked-set refresh failed")
        await asyncio.sleep(TRACKED_REFRESH)


async def force_reconnect() -> None:
    """Test hook: hard-close the shared ws to exercise reconnect + re-baseline."""
    if _ws_conn is not None:
        await _ws_conn.close()


def start() -> None:
    global _task, _poll_task, _refresh_task
    loop = asyncio.get_event_loop()
    if _refresh_task is None or _refresh_task.done():
        _refresh_task = loop.create_task(_refresh_loop())
    if _task is None or _task.done():
        _task = loop.create_task(_ws_loop())
    if _poll_task is None or _poll_task.done():
        _poll_task = loop.create_task(_poll_loop())
    logger.info("HL tracker started (ws tier <=%d, poll %ss)", WS_TIER_MAX, POLL_INTERVAL)


async def stop() -> None:
    global _task, _poll_task, _refresh_task
    for t in (_task, _poll_task, _refresh_task):
        if t and not t.done():
            t.cancel()
            try:
                await t
            except asyncio.CancelledError:
                pass
    _task = _poll_task = _refresh_task = None
