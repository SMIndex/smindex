"""Row shapes and pure helpers for the Unusual Timing web API (phase 2
page). Everything here reads the ut_ tables (plus the existing position
store hl_open_positions and round_trips for per-entry profit) and never
recomputes a score or a level. odds_text, apply_filters and sort_rows are
pure and unit-tested (scripts/test_ut_api.py).
"""
from __future__ import annotations

import json
import math
from datetime import datetime, timedelta
from statistics import median

from sqlalchemy import bindparam, text

from app.db.database import get_session_factory

LEVELS = ("watch", "strong", "cluster")
SORT_KEYS = {"score", "hit_rate", "avg_move_2h", "events_preceded", "misses", "age_days", "coins", "open", "last_entry_at", "wallet", "hits"}
EVENT_WORDS = {"big_move": "big move", "funding_flip": "funding flip", "macro": "macro", "listing": "listing", "delisting": "delisting", "manual": "manual"}
LEVEL_WORDS = {
    "watch": "Unusual timing on several trades. Could be skill.",
    "strong": "Repeated timing before events, plus a fresh wallet, size shock or burst profile.",
    "cluster": "Several wallets moving together with the same pattern.",
}


# ---------------------------------------------------------------------------
# pure helpers
# ---------------------------------------------------------------------------

def odds_text(p_value: float | None) -> str:
    """'1 in N' from a p-value, rounded to 2 significant figures; tiny
    values read 'under 1 in 1,000,000', None reads 'n/a'."""
    if p_value is None or (isinstance(p_value, float) and math.isnan(p_value)):
        return "n/a"
    p = float(p_value)
    if p <= 0:
        return "under 1 in 1,000,000"
    if p >= 1:
        return "1 in 1"
    n = 1.0 / p
    if n >= 1_000_000:
        return "under 1 in 1,000,000"
    if n >= 100:
        digits = int(math.floor(math.log10(n))) - 1
        n = round(n / 10 ** digits) * 10 ** digits
    else:
        n = round(n)
    return f"1 in {int(n):,}"


def apply_filters(rows: list[dict], *, level: str | None = None, min_hit: float | None = None, min_events: int | None = None,
                  max_age_days: float | None = None, coin: str | None = None, has_open: bool | None = None,
                  active_within_h: float | None = None, now: datetime | None = None) -> list[dict]:
    """The page filters. level keeps that level and above (watch < strong <
    cluster); min_hit is a hit rate in percent; max_age keeps wallets with
    a KNOWN age under the limit; active_within keeps wallets whose last
    entry is inside the window."""
    now = now or datetime.utcnow()
    out = []
    order = {lv: i for i, lv in enumerate(LEVELS)}
    for r in rows:
        if level and order.get(r.get("level"), -1) < order.get(level, 99):
            continue
        if min_hit and (r.get("hit_rate") or 0) * 100.0 < float(min_hit):
            continue
        if min_events and int(r.get("events_preceded") or 0) < int(min_events):
            continue
        if max_age_days is not None and max_age_days > 0:
            age = r.get("age_days")
            if age is None or float(age) >= float(max_age_days):
                continue
        if coin and coin.upper() not in [c.upper() for c in (r.get("coins") or [])]:
            continue
        if has_open and not r.get("open"):
            continue
        if active_within_h:
            last = r.get("last_entry_at")
            if not last:
                continue
            lt = datetime.fromisoformat(last) if isinstance(last, str) else last
            if (now - lt).total_seconds() > float(active_within_h) * 3600.0:
                continue
        out.append(r)
    return out


def sort_rows(rows: list[dict], sort: str = "score", direction: str = "desc") -> list[dict]:
    """Sort by one of SORT_KEYS; rows with an unknown value sink to the end
    in either direction."""
    key = sort if sort in SORT_KEYS else "score"
    rev = direction != "asc"
    known = [r for r in rows if _sort_val(r, key) is not None]
    unknown = [r for r in rows if _sort_val(r, key) is None]
    known.sort(key=lambda r: _sort_val(r, key), reverse=rev)
    return known + unknown


def _sort_val(r: dict, key: str):
    if key == "coins":
        return len(r.get("coins") or [])
    if key == "open":
        return 1 if r.get("open") else 0
    if key == "last_entry_at":
        return r.get("last_entry_at") or None
    if key == "wallet":
        return r.get("wallet") or None
    return r.get(key)


def _iso(dt) -> str | None:
    if dt is None:
        return None
    if isinstance(dt, str):
        return dt
    return dt.replace(microsecond=0).isoformat()


def _f(v):
    return None if v is None else float(v)


def open_positions_from_state(state_json: str | None) -> list[dict]:
    """Positions out of a stored clearinghouseState (hl_open_positions.state)."""
    out = []
    try:
        body = json.loads(state_json) if state_json else {}
    except (ValueError, TypeError):
        return out
    for ap in body.get("assetPositions") or []:
        p = ap.get("position") or {}
        try:
            szi = float(p.get("szi") or 0)
        except (TypeError, ValueError):
            continue
        if not szi:
            continue
        out.append({"coin": str(p.get("coin")), "side": "long" if szi > 0 else "short", "size": abs(szi),
                    "notional": abs(float(p.get("positionValue") or 0)), "leverage": _f((p.get("leverage") or {}).get("value")),
                    "upnl": _f(p.get("unrealizedPnl")), "entry": _f(p.get("entryPx"))})
    return out


# ---------------------------------------------------------------------------
# rows from the tables
# ---------------------------------------------------------------------------

async def scores_as_of():
    sf = get_session_factory()
    async with sf() as s:
        return (await s.execute(text("SELECT MAX(as_of) FROM ut_wallet_scores"))).scalar()


async def build_rows(*, flagged_only: bool = True) -> dict:
    """One row per wallet from the latest ut_wallet_scores joined with
    ut_wallet_meta, the position store and the entries aggregates."""
    as_of = await scores_as_of()
    if not as_of:
        return {"as_of": None, "rows": [], "scored": 0, "built_at": _iso(datetime.utcnow())}
    sf = get_session_factory()
    since = datetime.utcnow() - timedelta(days=90)
    async with sf() as s:
        scored = (await s.execute(text("SELECT COUNT(*) FROM ut_wallet_scores WHERE as_of=:d"), {"d": as_of})).scalar()
        q = ("SELECT w.wallet, w.level, w.score, w.hits, w.entries, w.hit_rate, w.blended_base_rate, w.p_value, w.events_preceded, w.event_kinds, "
             "w.misses, w.worst_loss_usd, w.cluster_id, w.moved_market_count, w.evidence, w.top3_profit_share, w.median_hold_min, w.fresh_large, "
             "w.size_shock, w.co_entries, w.exit_speed_hits, w.s1, w.s2, w.s3, w.s4, w.s5, w.s6, w.s7, "
             "m.first_deposit_at, m.funder_label, m.funder, m.first_deposit_usd, m.account_value, m.checked_at "
             "FROM ut_wallet_scores w LEFT JOIN ut_wallet_meta m ON m.wallet=w.wallet WHERE w.as_of=:d")
        if flagged_only:
            q += " AND w.level <> 'none'"
        rows = (await s.execute(text(q), {"d": as_of})).mappings().all()
        wallets = [r["wallet"] for r in rows]
        agg = {}
        coins_by: dict[str, dict[str, int]] = {}
        if wallets:
            for w, n, r2, r24, last in (await s.execute(text(
                    "SELECT wallet, COUNT(*), AVG(ret_2h), AVG(ret_24h), MAX(time) FROM ut_entries WHERE time >= :a AND wallet IN :w GROUP BY wallet"
            ).bindparams(bindparam("w", expanding=True)), {"a": since, "w": wallets})).all():
                agg[w] = {"n": n, "avg_move_2h": _f(r2), "avg_move_24h": _f(r24), "last_entry_at": last}
            for w, c, n in (await s.execute(text(
                    "SELECT wallet, coin, COUNT(*) FROM ut_entries WHERE time >= :a AND wallet IN :w GROUP BY wallet, coin"
            ).bindparams(bindparam("w", expanding=True)), {"a": since, "w": wallets})).all():
                coins_by.setdefault(w, {})[c] = int(n)
            pos = {w: st for w, st in (await s.execute(text(
                "SELECT wallet, state FROM hl_open_positions WHERE wallet IN :w").bindparams(bindparam("w", expanding=True)), {"w": wallets})).all()}
            cl_sizes = {int(i): int(n) for i, n in (await s.execute(text(
                "SELECT id, member_count FROM ut_clusters WHERE as_of=:d"), {"d": as_of})).all()}
        else:
            pos, cl_sizes = {}, {}
    out = []
    now = datetime.utcnow()
    for r in rows:
        ev = r["evidence"]
        try:
            ev = json.loads(ev) if isinstance(ev, str) else (ev or {})
        except ValueError:
            ev = {}
        kinds = sorted({EVENT_WORDS.get(e.get("kind"), e.get("kind")) for e in (ev.get("events") or []) if e.get("kind")})
        a = agg.get(r["wallet"], {})
        coins = [c for c, _n in sorted(coins_by.get(r["wallet"], {}).items(), key=lambda kv: -kv[1])]
        opens = open_positions_from_state(pos.get(r["wallet"]))
        opens.sort(key=lambda p: -p["notional"])
        open_main = opens[0] if opens else None
        fd = r["first_deposit_at"]
        age = round((now - fd).total_seconds() / 86400.0, 1) if fd else None
        out.append({
            "wallet": r["wallet"], "venue": "hl", "level": r["level"], "score": _f(r["score"]), "hits": int(r["hits"] or 0), "entries": int(r["entries"] or 0),
            "hit_rate": _f(r["hit_rate"]), "base_rate": _f(r["blended_base_rate"]), "p_value": _f(r["p_value"]), "odds_text": odds_text(_f(r["p_value"])),
            "avg_move_2h": a.get("avg_move_2h"), "avg_move_24h": a.get("avg_move_24h"),
            "events_preceded": int(r["events_preceded"] or 0), "event_kinds": kinds, "misses": int(r["misses"] or 0),
            "worst_loss_usd": _f(r["worst_loss_usd"]), "age_days": age, "age_known": fd is not None, "ledger_checked": r["checked_at"] is not None,
            "funder_label": r["funder_label"] or ("unlabelled address" if r["funder"] else None), "funder": r["funder"],
            "first_deposit_usd": _f(r["first_deposit_usd"]), "coins": coins[:6], "open": open_main, "open_all": opens,
            "last_entry_at": _iso(a.get("last_entry_at")), "cluster_id": r["cluster_id"], "cluster_size": cl_sizes.get(int(r["cluster_id"])) if r["cluster_id"] else None,
            "moved_market_count": int(r["moved_market_count"] or 0),
            "signals": {"s1": bool(r["s1"]), "s2": bool(r["s2"]), "s3": bool(r["s3"]), "s4": bool(r["s4"]), "s5": bool(r["s5"]), "s6": bool(r["s6"]), "s7": bool(r["s7"])},
            "top3_profit_share": _f(r["top3_profit_share"]), "median_hold_min": r["median_hold_min"], "co_entries": int(r["co_entries"] or 0),
            "exit_speed_hits": int(r["exit_speed_hits"] or 0), "event_kinds_n": int(r["event_kinds"] or 0),
            "evidence": ev, "account_value": _f(r["account_value"]),
        })
    return {"as_of": str(as_of), "rows": out, "scored": int(scored or 0), "built_at": _iso(datetime.utcnow())}


async def wallet_entries(wallet: str) -> list[dict]:
    """Every entry of the last 90 days with the trip's profit where a round
    trip holds the entry (same join as entries.close_times)."""
    sf = get_session_factory()
    since = datetime.utcnow() - timedelta(days=90)
    async with sf() as s:
        ents = (await s.execute(text(
            "SELECT e.id, e.coin, e.side, e.time, e.px, e.notional, e.leverage, e.ret_2h, e.ret_24h, e.mfe_2h, e.minutes_before_event, v.kind, "
            "e.close_after_peak_min, e.closed_at, e.hit, e.moved_market, e.is_add, e.size_vs_median FROM ut_entries e LEFT JOIN ut_events v ON v.id=e.event_id "
            "WHERE e.wallet=:w AND e.time >= :a ORDER BY e.time DESC"), {"w": wallet, "a": since})).all()
        trips = (await s.execute(text(
            "SELECT coin, side, open_time, close_time, pnl_usd, leverage, liquidation FROM round_trips WHERE venue='hl' AND wallet=:w AND close_time >= :a"),
            {"w": wallet, "a": since})).all()
    by_key: dict[tuple[str, str], list] = {}
    for c, sd, ot, ct, pnl, lev, liq in trips:
        by_key.setdefault((c, sd), []).append((ot, ct, _f(pnl), _f(lev)))
    out = []
    for (eid, c, sd, t, px, notional, lev, r2, r24, m2, mins, kind, cap, closed, hit, moved, is_add, svm) in ents:
        pnl = None
        for ot, ct, p, _lv in by_key.get((c, sd), []):
            if ot <= t <= ct:
                pnl = p
                break
        out.append({"id": eid, "time": _iso(t), "coin": c, "side": sd, "px": _f(px), "notional": _f(notional), "leverage": _f(lev),
                    "ret_2h": _f(r2), "ret_24h": _f(r24), "mfe_2h": _f(m2), "minutes_before_event": mins,
                    "event_kind": EVENT_WORDS.get(kind, kind) if kind else None, "close_after_peak_min": cap, "closed_at": _iso(closed),
                    "pnl": pnl, "hit": (None if hit is None else bool(hit)), "moved_market": bool(moved), "is_add": bool(is_add),
                    "size_vs_median": _f(svm)})
    return out


async def wallet_trip_stats(wallet: str) -> dict:
    sf = get_session_factory()
    since = datetime.utcnow() - timedelta(days=90)
    async with sf() as s:
        rows = (await s.execute(text(
            "SELECT pnl_usd, leverage, liquidation FROM round_trips WHERE venue='hl' AND wallet=:w AND close_time >= :a"), {"w": wallet, "a": since})).all()
    pnls = [float(p) for p, _l, _q in rows]
    levs = [float(l) for _p, l, _q in rows if l is not None]
    return {"net_profit_90d": round(sum(pnls), 2) if pnls else None, "median_leverage": (median(levs) if levs else None),
            "liquidations": sum(1 for _p, _l, q in rows if q), "trips": len(rows)}


async def candle_series(coin: str, days: int = 90, max_points: int = 400) -> dict:
    """1 h closes of the coin over `days`, downsampled to at most max_points,
    plus the coin's events (direction known) in the window."""
    sf = get_session_factory()
    since = datetime.utcnow() - timedelta(days=days)
    async with sf() as s:
        rows = (await s.execute(text(
            "SELECT t, c FROM ut_candles WHERE coin=:c AND tf='1h' AND t >= :a ORDER BY t"), {"c": coin, "a": since})).all()
        evs = (await s.execute(text(
            "SELECT id, time, kind, direction, magnitude FROM ut_events WHERE (coin=:c OR coin='ALL') AND direction IN ('up','down') AND time >= :a ORDER BY time"),
            {"c": coin, "a": since})).all()
    pts = [(int(t.replace(tzinfo=None).timestamp()), float(c)) for t, c in rows]
    if len(pts) > max_points:
        step = len(pts) / float(max_points)
        pts = [pts[int(i * step)] for i in range(max_points)]
    return {"coin": coin, "points": pts,
            "events": [{"id": i, "t": int(t.timestamp()), "kind": EVENT_WORDS.get(k, k), "direction": d, "magnitude": _f(m)} for i, t, k, d, m in evs],
            "bars": len(rows)}


async def cluster_detail(cid: int, rows_by_wallet: dict[str, dict]) -> dict | None:
    sf = get_session_factory()
    since = datetime.utcnow() - timedelta(days=90)
    async with sf() as s:
        cl = (await s.execute(text("SELECT id, as_of, member_count, total_notional, lead_wallet, shared_events, edge_kinds FROM ut_clusters WHERE id=:i"),
                              {"i": cid})).first()
        if not cl:
            return None
        members = [w for w in (await s.execute(text("SELECT wallet FROM ut_cluster_members WHERE cluster_id=:i"), {"i": cid})).scalars().all()]
        pairs = (await s.execute(text(
            "SELECT wallet_a, wallet_b, coin, side, time_a, time_b, on_event FROM ut_coentries WHERE time_a >= :a AND crowd = 0 AND wallet_a IN :m AND wallet_b IN :m ORDER BY time_a DESC"
        ).bindparams(bindparam("m", expanding=True)), {"a": since, "m": members or ["-"]})).all()
        funders = {w: f for w, f in (await s.execute(text(
            "SELECT wallet, funder FROM ut_wallet_meta WHERE wallet IN :m").bindparams(bindparam("m", expanding=True)), {"m": members or ["-"]})).all()}
        shared_ids = json.loads(cl[5] or "[]")
        ev_rows = []
        if shared_ids:
            ev_rows = (await s.execute(text(
                "SELECT id, time, coin, kind, direction, magnitude, note FROM ut_events WHERE id IN :i ORDER BY time DESC"
            ).bindparams(bindparam("i", expanding=True)), {"i": shared_ids})).all()
        ents = (await s.execute(text(
            "SELECT wallet, coin, side, time, notional, ret_2h, hit, minutes_before_event, event_id FROM ut_entries WHERE time >= :a AND wallet IN :m"
        ).bindparams(bindparam("m", expanding=True)), {"a": since, "m": members or ["-"]})).all()
        trips = (await s.execute(text(
            "SELECT wallet, pnl_usd FROM round_trips WHERE venue='hl' AND close_time >= :a AND wallet IN :m"
        ).bindparams(bindparam("m", expanding=True)), {"a": since, "m": members or ["-"]})).all()
    # edges
    edge_counts: dict[tuple[str, str], dict] = {}
    for a, b, c, sd, ta, tb, oe in pairs:
        d = edge_counts.setdefault((a, b), {"a": a, "b": b, "co_entries": 0, "on_events": 0, "shared_funder": False})
        d["co_entries"] += 1
        d["on_events"] += 1 if oe else 0
    for i in range(len(members)):
        for j in range(i + 1, len(members)):
            a, b = sorted((members[i], members[j]))
            fa, fb = funders.get(a), funders.get(b)
            if fa and fb and fa == fb:
                edge_counts.setdefault((a, b), {"a": a, "b": b, "co_entries": 0, "on_events": 0, "shared_funder": False})["shared_funder"] = True
    # co-entry groups: pairs within the same coin, side and minute window -> one row
    groups: dict[tuple, dict] = {}
    for a, b, c, sd, ta, tb, oe in pairs:
        key = (c, sd, int(ta.timestamp() // 120))
        g = groups.setdefault(key, {"time": ta, "coin": c, "side": sd, "wallets": set(), "first": ta, "last": tb, "on_event": False})
        g["wallets"].update((a, b))
        g["first"] = min(g["first"], ta)
        g["last"] = max(g["last"], tb)
        g["on_event"] = g["on_event"] or bool(oe)
    ent_idx: dict[tuple[str, str, str], list] = {}
    for w, c, sd, t, n, r2, hit, mins, evid in ents:
        ent_idx.setdefault((w, c, sd), []).append((t, _f(n), _f(r2), hit, mins, evid))
    co_rows = []
    for g in sorted(groups.values(), key=lambda g: g["first"], reverse=True):
        notional = 0.0
        rets, mins_l, hits = [], [], []
        for w in g["wallets"]:
            for t, n, r2, hit, mins, evid in ent_idx.get((w, g["coin"], g["side"]), []):
                if abs((t - g["first"]).total_seconds()) <= 120:
                    notional += n or 0
                    if r2 is not None:
                        rets.append(r2)
                    if mins is not None:
                        mins_l.append(mins)
                    if hit is not None:
                        hits.append(bool(hit))
        co_rows.append({"time": _iso(g["first"]), "coin": g["coin"], "side": g["side"], "wallets_in": len(g["wallets"]), "members": len(members),
                        "spread_s": int((g["last"] - g["first"]).total_seconds()), "notional": round(notional, 2),
                        "minutes_before": (min(mins_l) if mins_l else None), "event": g["on_event"],
                        "ret_2h": (sum(rets) / len(rets) if rets else None), "hit": (any(hits) if hits else None)})
    pnl_by = {}
    for w, p in trips:
        pnl_by[w] = pnl_by.get(w, 0.0) + float(p)
    mrows = [rows_by_wallet[w] for w in members if w in rows_by_wallet]
    misses = sum(r["misses"] for r in mrows)
    entries_n = sum(r["entries"] for r in mrows)
    worst = min([r["worst_loss_usd"] for r in mrows if r["worst_loss_usd"] is not None], default=None)
    # open now, same way
    ways: dict[tuple[str, str], list] = {}
    for r in mrows:
        for p in r.get("open_all") or []:
            ways.setdefault((p["coin"], p["side"]), []).append(p["notional"])
    same = max(ways.items(), key=lambda kv: (len(kv[1]), sum(kv[1])), default=None)
    return {
        "id": int(cl[0]), "as_of": str(cl[1]), "member_count": int(cl[2]), "total_notional": _f(cl[3]), "lead_wallet": cl[4],
        "edge_kinds": json.loads(cl[6] or "[]"), "members": mrows, "member_wallets": members,
        "edges": sorted(edge_counts.values(), key=lambda e: -e["co_entries"]), "co_entries": co_rows[:200],
        "events": [{"id": i, "time": _iso(t), "coin": c, "kind": EVENT_WORDS.get(k, k), "direction": d, "magnitude": _f(m), "note": n} for i, t, c, k, d, m, n in ev_rows],
        "combined_profit_90d": round(sum(pnl_by.values()), 2) if pnl_by else None,
        "combined_misses": misses, "combined_entries": entries_n, "combined_worst_loss_usd": worst,
        "same_way": ({"coin": same[0][0], "side": same[0][1], "count": len(same[1]), "notional": round(sum(same[1]), 2)} if same else None),
    }
