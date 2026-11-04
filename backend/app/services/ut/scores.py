"""ut_wallet_scores: S1 to S7 per wallet per day (rules.signals), with the
evidence JSON every report line is built from: events preceded, top trades,
funder, first deposit, misses and worst loss, moved-market count.

Two passes: pass 1 scores every wallet with enough sample on S1 to S5 and
S7 and marks the S1 wallets as flagged; pass 2 adds S6 (co-entries with
other flagged wallets, shared funder) and the final score and level.
Clusters (clusters.py) then set cluster_id and lift Strong wallets in a
cluster of three or more to Cluster.

Reads the worker cannot do itself are queued for the API reader: the ledger
of every S1 wallet with no first-deposit row yet, and 1 m candles for the
coins flagged wallets traded in the last 7 days.
"""
from __future__ import annotations

import csv
import json
import os
from datetime import date, datetime, timedelta
from statistics import median

from sqlalchemy import bindparam, text

from app.db.database import get_session_factory
from app.services.ut import common, rules
from app.utils.logger import get_logger

logger = get_logger(__name__)

SHARED_FUNDER_SKIP_KINDS = {"cex", "bridge", "mint", "service"}
SERVICE_FUNDER_MIN = 5          # an address that funded this many candidate wallets is a service, not a person


def funder_labels() -> dict[str, tuple[str, str]]:
    path = os.path.join(common.DATA_DIR, "funders.csv")
    out: dict[str, tuple[str, str]] = {}
    if not os.path.exists(path):
        return out
    with open(path, newline="", encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            a = (r.get("address") or "").strip().lower()
            if a.startswith("0x") and len(a) == 42:
                out[a] = ((r.get("label") or "").strip()[:64], (r.get("kind") or "").strip().lower())
    return out


async def _wallet_rows(wallet: str, since: datetime, cutoff: datetime | None) -> dict:
    sf = get_session_factory()
    upto = cutoff or common.now_utc()
    async with sf() as s:
        ents = (await s.execute(text(
            "SELECT e.id, e.coin, e.side, e.time, e.notional, e.hit, e.base_rate, e.moved_market, e.event_id, e.minutes_before_event, "
            "e.peak_at, e.closed_at, e.close_after_peak_min, e.size_vs_median, e.share_of_equity, e.mfe_2h, e.leverage, v.kind, v.coin, v.time, v.direction "
            "FROM ut_entries e LEFT JOIN ut_events v ON v.id = e.event_id WHERE e.wallet=:w AND e.time >= :a AND e.time < :b ORDER BY e.time"),
            {"w": wallet, "a": since, "b": upto})).all()
        trips = (await s.execute(text(
            "SELECT coin, side, open_time, close_time, pnl_usd, max_size_usd, leverage FROM round_trips WHERE venue='hl' AND wallet=:w "
            "AND close_time >= :a AND close_time < :b"), {"w": wallet, "a": since, "b": upto})).all()
        meta = (await s.execute(text(
            "SELECT first_deposit_at, first_deposit_usd, first_deposit_kind, funder, funder_label, checked_at, account_value, history_from "
            "FROM ut_wallet_meta WHERE wallet=:w"), {"w": wallet})).first()
        first_fill = (await s.execute(text("SELECT MIN(time) FROM hl_fills WHERE wallet=:w"), {"w": wallet})).scalar()
    return {"entries": ents, "trips": trips, "meta": meta, "first_fill": first_fill}


def aggregate(wallet: str, d: dict, labels: dict[str, tuple[str, str]]) -> dict:
    """The per-wallet aggregates rules.signals reads, plus the evidence."""
    ents = d["entries"]
    measured = [e for e in ents if e[5] is not None]        # entries with a forward move computed
    hits = [e for e in measured if int(e[5]) == 1]
    n, k = len(measured), len(hits)
    br = (sum(float(e[6]) for e in measured) / n) if n else 0.0
    events = {}
    for e in measured:
        if e[8] is not None:
            events[int(e[8])] = {"id": int(e[8]), "kind": e[17], "coin": e[18], "time": e[19].strftime("%Y-%m-%d %H:%M") if e[19] else None,
                                 "direction": e[20], "minutes_before": e[9], "entry_coin": e[1], "entry_side": e[2]}
    trips = d["trips"]
    pnls = sorted((float(t[4]) for t in trips), reverse=True)
    net = sum(pnls)
    top3 = sum(p for p in pnls[:3] if p > 0)
    holds = [(t[3] - t[2]).total_seconds() / 60.0 for t in trips]
    med_hold = median(holds) if holds else None
    meta = d["meta"]
    first_dep = meta[0] if meta else None
    age_at_hit = None
    if first_dep is not None and hits:
        age_at_hit = min((e[3] - first_dep).total_seconds() / 86400.0 for e in hits)
    first_pos = None
    if first_dep is not None and d["first_fill"] is not None and ents and d["first_fill"] <= first_dep + timedelta(days=2) \
            and d["first_fill"] >= first_dep - timedelta(hours=1):
        first_pos = float(ents[0][4])
    size_shock = any((e[13] is not None and float(e[13]) > 5.0) or (e[14] is not None and float(e[14]) > 0.8) for e in hits)
    exit_fast = sum(1 for e in hits if e[12] is not None and int(e[12]) <= 120 and e[11] is not None and e[11] > e[3])
    funder = meta[3] if meta else None
    flabel, fkind = labels.get(funder or "", (meta[4] if meta and meta[4] else None, None)) if funder else (None, None)
    top_trades = [{"coin": t[0], "side": t[1], "open": t[2].strftime("%Y-%m-%d %H:%M"), "close": t[3].strftime("%Y-%m-%d %H:%M"),
                   "pnl_usd": round(float(t[4]), 2), "size_usd": round(float(t[5]), 2)}
                  for t in sorted(trips, key=lambda t: float(t[4]), reverse=True)[:3]]
    hit_rows = [{"coin": e[1], "side": e[2], "time": e[3].strftime("%Y-%m-%d %H:%M"), "notional": round(float(e[4]), 2),
                 "mfe_2h": (float(e[15]) if e[15] is not None else None), "event": (e[17] if e[8] else None)} for e in hits[:12]]
    return {
        "entries": n, "hits": k, "blended_base_rate": br, "events_preceded": len(events),
        "event_kinds": len({v["kind"] for v in events.values()}), "net_profit": net, "top3_profit": top3,
        "median_hold_min": med_hold, "age_days_at_hit": age_at_hit, "first_position_usd": first_pos,
        "size_shock_on_hit": size_shock, "exit_speed_hits": exit_fast, "misses": n - k,
        "worst_loss_usd": (min(pnls) if pnls else None), "moved_market_count": sum(1 for e in ents if int(e[7] or 0) == 1),
        "top3_profit_share": (top3 / net if net > 0 else None), "funder": funder, "funder_label": flabel, "funder_kind": fkind,
        "first_deposit_at": first_dep, "first_deposit_usd": (float(meta[1]) if meta and meta[1] is not None else None),
        "first_deposit_kind": (meta[2] if meta else None), "ledger_checked": bool(meta and meta[5]),
        "history_from": (meta[7] if meta else None) or d["first_fill"],
        "coins_7d": sorted({e[1] for e in ents if e[3] >= common.now_utc() - timedelta(days=7)}),
        "evidence": {"events": sorted(events.values(), key=lambda v: v["time"] or ""), "top_trades": top_trades, "hits": hit_rows,
                     "funder": funder, "funder_label": flabel, "first_deposit": (first_dep.strftime("%Y-%m-%d %H:%M") if first_dep else None),
                     "first_deposit_usd": (float(meta[1]) if meta and meta[1] is not None else None),
                     "first_position_usd": first_pos, "notes": []},
    }


async def score_wallet(wallet: str, *, since: datetime, cutoff: datetime | None, labels: dict, med_first: float | None,
                       flagged_co: int = 0, shared_funder: bool = False, in_cluster_of_3: bool = False) -> dict:
    d = await _wallet_rows(wallet, since, cutoff)
    agg = aggregate(wallet, d, labels)
    agg["candidates_median_first_position"] = med_first
    agg["co_entries_flagged"] = flagged_co
    agg["shared_funder_flagged"] = shared_funder
    agg["in_cluster_of_3"] = in_cluster_of_3
    sig = rules.signals(agg)
    return {**agg, **sig}


async def _scored_wallets(since: datetime) -> list[str]:
    sf = get_session_factory()
    async with sf() as s:
        rows = (await s.execute(text(
            "SELECT e.wallet FROM ut_entries e JOIN ut_wallet_meta m ON m.wallet = e.wallet AND m.excluded IS NULL "
            "WHERE e.time >= :a AND e.hit IS NOT NULL GROUP BY e.wallet HAVING COUNT(*) >= :n AND SUM(e.hit) >= :k"),
            {"a": since, "n": rules.MIN_ENTRIES, "k": rules.MIN_HITS})).scalars().all()
    return sorted(set(rows))


async def _co_entries_among(flagged: set[str], since: datetime) -> dict[str, int]:
    """Co-entries per flagged wallet with other flagged wallets, counted only on
    pairs that pass the cluster edge rule (3+ or 2+ on events, and at least
    5x what chance predicts for two wallets that busy on the same coins)."""
    if not flagged:
        return {}
    from app.services.ut import clusters as _cl
    edges = await _cl.flagged_edges(sorted(flagged), since)
    out: dict[str, int] = {}
    for (a, b), v in edges.items():
        out[a] = out.get(a, 0) + int(v["n"])
        out[b] = out.get(b, 0) + int(v["n"])
    return out


async def service_funders() -> set[str]:
    """Addresses that funded SERVICE_FUNDER_MIN or more candidate wallets:
    treated like a labelled exchange (never a shared-funder link)."""
    sf = get_session_factory()
    async with sf() as s:
        rows = (await s.execute(text(
            "SELECT funder FROM ut_wallet_meta WHERE funder IS NOT NULL GROUP BY funder HAVING COUNT(*) >= :n"), {"n": SERVICE_FUNDER_MIN})).scalars().all()
    return set(rows)


async def _shared_funders(flagged: set[str], labels: dict) -> set[str]:
    if not flagged:
        return set()
    sf = get_session_factory()
    async with sf() as s:
        rows = (await s.execute(text(
            "SELECT wallet, funder FROM ut_wallet_meta WHERE funder IS NOT NULL AND wallet IN :f"
        ).bindparams(bindparam("f", expanding=True)), {"f": sorted(flagged)})).all()
    services = await service_funders()
    by_f: dict[str, list[str]] = {}
    for w, f in rows:
        kind = labels.get(f, (None, None))[1]
        if kind in SHARED_FUNDER_SKIP_KINDS or f in services:
            continue
        by_f.setdefault(f, []).append(w)
    return {w for ws in by_f.values() if len(ws) >= 2 for w in ws}


async def _queue(kind: str, items: list[str]) -> int:
    if not items:
        return 0
    sf = get_session_factory()
    async with sf() as s:
        await s.execute(text("INSERT IGNORE INTO ut_queue (kind, item, queued_at) VALUES (:k, :i, UTC_TIMESTAMP())"),
                        [{"k": kind, "i": it} for it in items])
        if kind == "candles_1m":
            # re-serve every six hours: a served coin is queued again
            await s.execute(text("UPDATE ut_queue SET done_at=NULL WHERE kind='candles_1m' AND item IN :i AND done_at < UTC_TIMESTAMP() - INTERVAL 5 HOUR"
                                 ).bindparams(bindparam("i", expanding=True)), {"i": items})
        await s.commit()
    return len(items)


async def build(as_of: date | None = None, *, cutoff: datetime | None = None, write: bool = True, only: list[str] | None = None) -> dict:
    """Score every wallet with enough sample. With cutoff, only entries and
    trips before it count (the validation's predictive check); write=False
    returns the rows without touching the table; only= restricts the set
    (S6 then counts co-entries inside that set only)."""
    as_of = as_of or common.now_utc().date()
    since = (cutoff or common.now_utc()) - timedelta(days=common.WINDOW_DAYS)
    labels = funder_labels()
    wallets = await _scored_wallets(since)
    if only is not None:
        keep = {w.lower() for w in only}
        wallets = [w for w in wallets if w in keep]
    # pass 1: S1 and the sample-wide median first position
    pass1: dict[str, dict] = {}
    for w in wallets:
        pass1[w] = await score_wallet(w, since=since, cutoff=cutoff, labels=labels, med_first=None)
    firsts = [r["first_position_usd"] for r in pass1.values() if r.get("first_position_usd")]
    med_first = median(firsts) if len(firsts) >= 5 else None
    flagged = {w for w, r in pass1.items() if r.get("s1")}
    co = await _co_entries_among(flagged, since)
    shared = await _shared_funders(flagged, labels)
    # pass 2: everything, with S6
    rows: dict[str, dict] = {}
    for w in wallets:
        rows[w] = await score_wallet(w, since=since, cutoff=cutoff, labels=labels, med_first=med_first,
                                     flagged_co=co.get(w, 0), shared_funder=(w in shared))
    if not write:
        return {"as_of": str(as_of), "rows": rows, "flagged": flagged}
    sf = get_session_factory()
    out_rows = []
    for w, r in rows.items():
        ev = dict(r["evidence"])
        if r.get("insufficient"):
            ev["notes"].append(r["insufficient"])
        if not r.get("ledger_checked"):
            ev["notes"].append("wallet age unknown until the ledger is read")
        out_rows.append({
            "d": as_of, "w": w, "sc": r["score"], "lv": r["level"], "n": r["entries"], "k": r["hits"], "hr": r["hit_rate"],
            "br": round(r["blended_base_rate"], 6), "p": r["p_value"], "ep": r["events_preceded"], "ek": r["event_kinds"],
            # DECIMAL(6,4): a tiny positive net profit makes the top-3 share huge; stored capped, the S3 test used the raw ratio
            "t3": (min(float(r["top3_profit_share"]), 99.9999) if r["top3_profit_share"] is not None else None),
            "mh": (int(r["median_hold_min"]) if r["median_hold_min"] is not None else None),
            "fl": 1 if r["s4"] else 0, "ss": 1 if r["s5"] else 0, "co": r["co_entries_flagged"], "es": r["exit_speed_hits"],
            "mm": r["moved_market_count"], "mi": r["misses"], "wl": (max(float(r["worst_loss_usd"]), -1e17) if r["worst_loss_usd"] is not None else None),
            "s1": int(r["s1"]), "s2": int(r["s2"]), "s3": int(r["s3"]), "s4": int(r["s4"]), "s5": int(r["s5"]), "s6": int(r["s6"]), "s7": int(r["s7"]),
            "ev": json.dumps(ev, default=str), "now": common.now_utc()})
    async with sf() as s:
        await s.execute(text("DELETE FROM ut_wallet_scores WHERE as_of=:d"), {"d": as_of})
        for i in range(0, len(out_rows), 300):
            await s.execute(text(
                "INSERT INTO ut_wallet_scores (as_of, wallet, score, level, entries, hits, hit_rate, blended_base_rate, p_value, events_preceded, "
                "event_kinds, top3_profit_share, median_hold_min, fresh_large, size_shock, co_entries, exit_speed_hits, moved_market_count, misses, "
                "worst_loss_usd, s1, s2, s3, s4, s5, s6, s7, evidence, computed_at) VALUES (:d, :w, :sc, :lv, :n, :k, :hr, :br, :p, :ep, :ek, :t3, :mh, "
                ":fl, :ss, :co, :es, :mm, :mi, :wl, :s1, :s2, :s3, :s4, :s5, :s6, :s7, :ev, :now)"), out_rows[i:i + 300])
        # funder labels and the flagged mark on the meta row
        for w in flagged:
            await s.execute(text("UPDATE ut_wallet_meta SET flagged_at=COALESCE(flagged_at, UTC_TIMESTAMP()) WHERE wallet=:w"), {"w": w})
        for w, r in rows.items():
            if r.get("funder") and r.get("funder_label"):
                await s.execute(text("UPDATE ut_wallet_meta SET funder_label=:l WHERE wallet=:w"), {"l": r["funder_label"], "w": w})
        await s.commit()
    # reads for the API: ledgers of S1 wallets without one, 1 m candles of the coins flagged wallets trade
    need_ledger = [w for w in flagged if not rows[w].get("ledger_checked")]
    coins_1m = sorted({c for w in flagged for c in rows[w].get("coins_7d", [])})
    q1 = await _queue("ledger", need_ledger)
    q2 = await _queue("candles_1m", coins_1m)
    levels = {}
    for r in rows.values():
        levels[r["level"]] = levels.get(r["level"], 0) + 1
    out = {"as_of": str(as_of), "scored": len(rows), "s1_flagged": len(flagged), "levels": levels, "queued_ledger": q1, "queued_candles_1m": q2,
           "median_first_position": med_first}
    logger.info("ut scores: %s", out)
    return out
