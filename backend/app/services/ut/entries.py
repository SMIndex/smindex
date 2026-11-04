"""ut_entries: one row per merged entry of every candidate wallet, with the
forward moves, the hit flag against the coin's threshold, the moved-market
flag, the nearest event the entry preceded, and the close time.

Candidates: every wallet with an opening fill in hl_fills in the last 90
days (the featured pipeline's fills), plus wallets the trades feed saw with
a position over $250K in the last 30 days (queued for a two-page fills read
when they have no fills yet). Market makers and vaults are marked excluded
before their entries are built: they make most of the fill volume and are
never scored (spec section 5), so building their entries would only fill the
disk. Spot and outcome markets are not positions and are skipped.

Forward moves use 1 m candles where stored (the last 7 days of the coins
flagged wallets trade) and 1 h candles otherwise; the bar holding the entry
is never credited (rules.forward_moves).
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta
from statistics import median

from sqlalchemy import bindparam, text

from app.db.database import get_session_factory
from app.services.ut import common, rules
from app.utils.logger import get_logger

logger = get_logger(__name__)

FEED_BIG_POSITION_USD = 250_000.0
RECOMPUTE_HOURS = 26            # entries younger than this get their 24 h moves recomputed
EVENT_WINDOW_MS = 2 * 3_600_000


# ---------------------------------------------------------------------------
# candidates and exclusions
# ---------------------------------------------------------------------------

async def candidates() -> dict[str, str]:
    """wallet -> source ('fills' or 'feed250k')."""
    since = common.window_start()
    sf = get_session_factory()
    out: dict[str, str] = {}
    async with sf() as s:
        rows = (await s.execute(text(
            "SELECT DISTINCT wallet FROM hl_fills WHERE time >= :a AND dir LIKE 'Open%'"), {"a": since})).scalars().all()
        for w in rows:
            out[w.lower()] = "fills"
        # feed wallets over $250K: last position read, and the hourly fill sampler
        big: set[str] = set()
        for w, state in (await s.execute(text(
                "SELECT wallet, state FROM hl_open_positions WHERE read_ms >= UNIX_TIMESTAMP(UTC_TIMESTAMP() - INTERVAL 30 DAY) * 1000"))).all():
            try:
                for ap in (json.loads(state) or {}).get("assetPositions") or []:
                    if abs(float((ap.get("position") or {}).get("positionValue") or 0)) >= FEED_BIG_POSITION_USD:
                        big.add(w.lower())
                        break
            except (ValueError, TypeError):
                continue
        rows = (await s.execute(text(
            "SELECT DISTINCT wallet_address FROM hl_fill_events WHERE ts_ms >= UNIX_TIMESTAMP(UTC_TIMESTAMP() - INTERVAL 30 DAY) * 1000 "
            "AND px * sz >= :n AND dir LIKE 'Open%'"), {"n": FEED_BIG_POSITION_USD})).scalars().all()
        big |= {w.lower() for w in rows}
        for w in big:
            if w not in out:
                out[w] = "feed250k"
    return out


async def queue_fills_for(wallets: list[str]) -> int:
    if not wallets:
        return 0
    sf = get_session_factory()
    async with sf() as s:
        await s.execute(text(
            "INSERT IGNORE INTO ut_queue (kind, item, queued_at) VALUES ('fills', :w, UTC_TIMESTAMP())"), [{"w": w} for w in wallets])
        await s.commit()
    return len(wallets)


async def mark_exclusions(wallets: list[str], sources: dict[str, str]) -> dict:
    """ut_wallet_meta.maker_share / median_hold_sec / is_vault / is_mm /
    excluded for every candidate, from hl_fills (crossed = 0 is a maker
    fill), round_trips (hold) and wallet_identity (role vault), with the
    featured pipeline's own measured flag when it has one."""
    since = common.window_start()
    sf = get_session_factory()
    async with sf() as s:
        maker = {w.lower(): (float(m) if m is not None else None) for w, m in (await s.execute(text(
            "SELECT wallet, SUM(crossed = 0) / COUNT(*) FROM hl_fills WHERE time >= :a AND crossed IS NOT NULL GROUP BY wallet"), {"a": since})).all()}
        holds: dict[str, list[float]] = {}
        for w, sec in (await s.execute(text(
                "SELECT wallet, TIMESTAMPDIFF(SECOND, open_time, close_time) FROM round_trips WHERE venue='hl' AND close_time >= :a"), {"a": since})).all():
            holds.setdefault(w.lower(), []).append(float(sec))
        vaults = {w.lower() for w in (await s.execute(text("SELECT wallet FROM wallet_identity WHERE role='vault'"))).scalars().all()}
        fw = {w.lower(): (ms, hs, av, flags) for w, ms, hs, av, flags in (await s.execute(text(
            "SELECT f.wallet, f.maker_share, f.median_hold_sec, f.account_value, f.flags FROM featured_wallets f JOIN ("
            " SELECT wallet, MAX(as_of) mx FROM featured_wallets WHERE venue='hl' GROUP BY wallet) l ON l.wallet=f.wallet AND l.mx=f.as_of "
            "WHERE f.venue='hl'"))).all()}
        subs = {w.lower() for w in (await s.execute(text("SELECT wallet FROM wallet_identity WHERE role='subAccount'"))).scalars().all()}
    rows = []
    counts = {"market_maker": 0, "vault": 0, "sub_account": 0, "scored": 0}
    for w in wallets:
        ms_ = maker.get(w)
        hs = median(holds[w]) if holds.get(w) else None
        f = fw.get(w)
        featured_mm = False
        av = None
        if f:
            if ms_ is None and f[0] is not None:
                ms_ = float(f[0])
            if hs is None and f[1] is not None:
                hs = float(f[1])
            av = float(f[2]) if f[2] is not None else None
            try:
                flags = json.loads(f[3]) if isinstance(f[3], str) else (f[3] or {})
                mm = flags.get("mm") if isinstance(flags, dict) else None
                featured_mm = (mm is False) or (isinstance(mm, dict) and mm.get("ok") is False)
            except (ValueError, TypeError):
                featured_mm = False
        ex = rules.exclusion(maker_share=ms_, median_hold_sec=hs, is_vault=(w in vaults), featured_mm=featured_mm)
        if ex is None and w in subs:
            ex = "sub_account"      # the master carries the sub-account's fills in the featured pipeline
        counts[ex or "scored"] += 1
        rows.append({"w": w, "ms": ms_, "hs": (int(hs) if hs is not None else None), "v": 1 if w in vaults else 0,
                     "mm": 1 if ex == "market_maker" else 0, "ex": ex, "av": av, "src": sources.get(w)})
    async with sf() as s:
        for i in range(0, len(rows), 500):
            await s.execute(text(
                "INSERT INTO ut_wallet_meta (wallet, maker_share, median_hold_sec, is_vault, is_mm, excluded, account_value, candidate_src) "
                "VALUES (:w, :ms, :hs, :v, :mm, :ex, :av, :src) ON DUPLICATE KEY UPDATE maker_share=VALUES(maker_share), "
                "median_hold_sec=VALUES(median_hold_sec), is_vault=VALUES(is_vault), is_mm=VALUES(is_mm), excluded=VALUES(excluded), "
                "account_value=COALESCE(VALUES(account_value), account_value), candidate_src=COALESCE(candidate_src, VALUES(candidate_src))"), rows[i:i + 500])
        await s.commit()
    return counts


# ---------------------------------------------------------------------------
# merged entries per wallet
# ---------------------------------------------------------------------------

async def build_wallet(wallet: str, perps: set[str]) -> int:
    """Merge the wallet's opening fills since its cursor into ut_entries."""
    since = common.window_start()
    sf = get_session_factory()
    async with sf() as s:
        cur = (await s.execute(text("SELECT entries_cursor FROM ut_wallet_meta WHERE wallet=:w"), {"w": wallet})).scalar()
        start = max(since, cur - timedelta(minutes=2)) if cur else since
        fills = (await s.execute(text(
            "SELECT time, coin, side, px, sz, start_position, tid FROM hl_fills WHERE wallet=:w AND time >= :a AND coin IN :c ORDER BY time, tid"
        ).bindparams(bindparam("c", expanding=True)), {"w": wallet, "a": start, "c": sorted(perps) or ["-"]})).all()
    if not fills:
        return 0
    rows = [{"time": common.ms(t), "coin": c, "side": sd, "px": float(px), "sz": float(sz), "start_position": float(sp), "tid": int(tid)}
            for t, c, sd, px, sz, sp, tid in fills]
    ents = rules.merge_entries(rows)
    last_fill = max(r["time"] for r in rows)
    out = [{"w": wallet, "c": e["coin"], "s": e["side"], "t": common.dt(e["time"]), "px": e["px"], "sz": e["size"],
            "n": round(e["notional"], 2), "add": 1 if e["is_add"] else 0, "nf": e["n_fills"]} for e in ents]
    async with sf() as s:
        for i in range(0, len(out), 500):
            await s.execute(text(
                "INSERT INTO ut_entries (wallet, coin, side, time, px, size, notional, is_add, n_fills) "
                "VALUES (:w, :c, :s, :t, :px, :sz, :n, :add, :nf) ON DUPLICATE KEY UPDATE px=VALUES(px), size=VALUES(size), "
                "notional=VALUES(notional), n_fills=VALUES(n_fills), computed_at=IF(n_fills <> VALUES(n_fills), NULL, computed_at)"), out[i:i + 500])
        await s.execute(text("UPDATE ut_wallet_meta SET entries_cursor=:t WHERE wallet=:w"), {"t": common.dt(last_fill), "w": wallet})
        await s.commit()
    return len(out)


async def build_all() -> dict:
    """Candidates, exclusions, then entries for every scored candidate. Stops
    when the disk is under the guard."""
    if not common.disk_ok():
        logger.warning("ut entries: disk under %.1f GB free, build skipped", common.MIN_FREE_GB)
        return {"skipped": "disk", "free_gb": round(common.free_gb(), 2)}
    srcs = await candidates()
    wallets = sorted(srcs)
    counts = await mark_exclusions(wallets, srcs)
    sf = get_session_factory()
    async with sf() as s:
        excluded = {w for w in (await s.execute(text("SELECT wallet FROM ut_wallet_meta WHERE excluded IS NOT NULL"))).scalars().all()}
        have_fills = {w.lower() for w in (await s.execute(text(
            "SELECT DISTINCT wallet FROM hl_fills WHERE time >= :a"), {"a": common.window_start()})).scalars().all()}
    queued = await queue_fills_for([w for w, src in srcs.items() if src == "feed250k" and w not in have_fills])
    perps = await common.perp_coins()
    n_entries = 0
    built = 0
    for i, w in enumerate(wallets):
        if w in excluded:
            continue
        if i % 200 == 0 and not common.disk_ok():
            logger.warning("ut entries: disk guard hit after %d wallets", built)
            counts["stopped"] = "disk"
            break
        n_entries += await build_wallet(w, perps)
        built += 1
    out = {"candidates": len(wallets), "built": built, "entries_upserted": n_entries, "fills_queued": queued, **counts}
    logger.info("ut entries: %s", out)
    return out


# ---------------------------------------------------------------------------
# forward moves, hits, events, close times
# ---------------------------------------------------------------------------

async def _events_for(coin: str, t0: datetime, t1: datetime) -> list[dict]:
    sf = get_session_factory()
    async with sf() as s:
        rows = (await s.execute(text(
            "SELECT id, time, kind, direction, coin FROM ut_events WHERE (coin=:c OR coin='ALL') AND direction IN ('up','down') "
            "AND time >= :a AND time <= :b ORDER BY time"), {"c": coin, "a": t0, "b": t1})).all()
    return [{"id": i, "t": common.ms(t), "kind": k, "direction": d, "coin": c} for i, t, k, d, c in rows]


def _nearest_event(events: list[dict], t: int, side: str) -> tuple[int | None, int | None]:
    """The nearest event within 2 h AFTER the entry whose direction matches
    the side (an entry long before an up move)."""
    want = "up" if side == "long" else "down"
    best = None
    for e in events:
        if e["t"] <= t or e["t"] - t > EVENT_WINDOW_MS or e["direction"] != want:
            continue
        if best is None or e["t"] < best["t"]:
            best = e
    if best is None:
        return None, None
    return best["id"], int((best["t"] - t) // 60_000)


async def compute_forward(coins: list[str] | None = None, *, cutoff: datetime | None = None) -> dict:
    """Fill mfe/ret/hit/base_rate/moved_market/event for entries not computed
    yet or younger than RECOMPUTE_HOURS. Per coin: 1 m bars where present,
    1 h bars for the rest."""
    thr = await common.latest_thresholds()
    now = common.now_utc()
    sf = get_session_factory()
    async with sf() as s:
        pend = (await s.execute(text(
            "SELECT coin, COUNT(*) FROM ut_entries WHERE (computed_at IS NULL OR time >= :r) AND time <= :n GROUP BY coin"),
            {"r": now - timedelta(hours=RECOMPUTE_HOURS), "n": now - timedelta(minutes=5)})).all()
    todo = [c for c, _ in pend if (coins is None or c in coins) and c in thr]
    done = 0
    for coin in todo:
        st = thr[coin]
        t_lo, t_hi = common.window_start() - timedelta(days=1), now
        c1h, ts1h = rules.sorted_candles(await common.load_candles(coin, "1h", t_lo, t_hi))
        c1m, ts1m = rules.sorted_candles(await common.load_candles(coin, "1m", t_lo, t_hi))
        if not c1h and not c1m:
            continue
        m1_from = c1m[0]["t"] if c1m else None
        m1_to = c1m[-1]["T"] if c1m else None
        events = await _events_for(coin, t_lo, t_hi + timedelta(hours=3))
        async with sf() as s:
            rows = (await s.execute(text(
                "SELECT id, wallet, side, time, px, notional FROM ut_entries WHERE coin=:c AND (computed_at IS NULL OR time >= :r) AND time <= :n"),
                {"c": coin, "r": now - timedelta(hours=RECOMPUTE_HOURS), "n": now - timedelta(minutes=5)})).all()
        upd = []
        for eid, w, side, t, px, notional in rows:
            tm = common.ms(t)
            # 1 m bars only when the 2 h window after the entry has at least 70 % of its minutes
            # (bars built from fills exist only for minutes with a print)
            use_1m = (m1_from is not None and tm >= m1_from and tm + 2 * 3_600_000 <= (m1_to or 0) + 60_000
                      and len(rules.window(c1m, ts1m, tm, tm + 2 * 3_600_000)) >= 84)
            fm = rules.forward_moves(float(px), side, tm, c1m if use_1m else c1h, ts1m if use_1m else ts1h)
            # moved-market divides by the VENUE 2 h volume (owner, 2026-11-04): only bars read from
            # Hyperliquid count; a window covered by fills-built bars has no venue volume, so no flag
            vol = rules.volume_after(c1h, tm, ts=ts1h, venue_only=True)
            moved = rules.moved_market(float(notional), vol)
            hit = rules.is_hit(fm["mfe_2h"], st["thr"], moved_market_flag=moved)
            ev_id, mins = _nearest_event(events, tm, side)
            upd.append({"i": eid, "m30": fm["mfe_30m"], "m2": fm["mfe_2h"], "m24": fm["mfe_24h"], "r30": fm["ret_30m"], "r2": fm["ret_2h"],
                        "r24": fm["ret_24h"], "hit": (1 if hit else 0) if fm["mfe_2h"] is not None else None, "br": st["base_rate"],
                        "mm": 1 if moved else 0, "ev": ev_id, "mins": mins, "pk": common.dt(fm["peak_at_2h"]) if fm["peak_at_2h"] else None,
                        "src": "1m" if use_1m else "1h", "now": now})
        async with sf() as s:
            for i in range(0, len(upd), 500):
                await s.execute(text(
                    "UPDATE ut_entries SET mfe_30m=:m30, mfe_2h=:m2, mfe_24h=:m24, ret_30m=:r30, ret_2h=:r2, ret_24h=:r24, hit=:hit, base_rate=:br, "
                    "moved_market=:mm, event_id=:ev, minutes_before_event=:mins, peak_at=:pk, candle_src=:src, computed_at=:now WHERE id=:i"), upd[i:i + 500])
            await s.commit()
        done += len(upd)
    logger.info("ut forward moves: %d entries on %d coins", done, len(todo))
    return {"entries": done, "coins": len(todo)}


async def close_times(wallets: list[str] | None = None) -> int:
    """closed_at and leverage from the round trip that holds the entry;
    close_after_peak_min once the peak is known. Also size_vs_median (own
    30-day median entry notional before the entry) and share_of_equity
    (account value from the featured pipeline, when known)."""
    since = common.window_start()
    sf = get_session_factory()
    async with sf() as s:
        if wallets is None:
            wallets = (await s.execute(text(
                "SELECT DISTINCT wallet FROM ut_entries WHERE time >= :a AND (closed_at IS NULL OR size_vs_median IS NULL)"), {"a": since})).scalars().all()
    n = 0
    for w in wallets:
        async with sf() as s:
            trips = (await s.execute(text(
                "SELECT coin, side, open_time, close_time, leverage FROM round_trips WHERE venue='hl' AND wallet=:w AND close_time >= :a"),
                {"w": w, "a": since})).all()
            ents = (await s.execute(text(
                "SELECT id, coin, side, time, notional, peak_at, closed_at FROM ut_entries WHERE wallet=:w AND time >= :a ORDER BY time"),
                {"w": w, "a": since})).all()
            av = (await s.execute(text("SELECT account_value FROM ut_wallet_meta WHERE wallet=:w"), {"w": w})).scalar()
        by_key: dict[tuple[str, str], list] = {}
        for c, sd, ot, ct, lev in trips:
            by_key.setdefault((c, sd), []).append((ot, ct, lev))
        hist: list[tuple[datetime, float]] = []
        upd = []
        for eid, c, sd, t, notional, peak, closed in ents:
            closed_at, lev = closed, None
            for ot, ct, lv in by_key.get((c, sd), []):
                if ot <= t <= ct:
                    closed_at, lev = ct, (float(lv) if lv is not None else None)
                    break
            cap = None
            if closed_at is not None and peak is not None:
                cap = int((closed_at - peak).total_seconds() // 60)
            prior = [x for ts, x in hist if ts >= t - timedelta(days=30)]
            svm = (float(notional) / median(prior)) if len(prior) >= 3 and median(prior) > 0 else None
            soe = (float(notional) / float(av)) if av and float(av) > 0 else None
            hist.append((t, float(notional)))
            upd.append({"i": eid, "ca": closed_at, "lev": lev, "cap": cap, "svm": (round(svm, 2) if svm is not None else None),
                        "soe": (round(min(soe, 99.0), 4) if soe is not None else None)})
        async with sf() as s:
            for i in range(0, len(upd), 500):
                await s.execute(text(
                    "UPDATE ut_entries SET closed_at=:ca, leverage=COALESCE(:lev, leverage), close_after_peak_min=:cap, size_vs_median=:svm, "
                    "share_of_equity=:soe WHERE id=:i"), upd[i:i + 500])
            await s.commit()
        n += len(upd)
    return n
