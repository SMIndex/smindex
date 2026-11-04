"""ut_coentries, ut_clusters, ut_cluster_members (spec section 7).

Co-entries are computed per coin over every scored candidate's entries (the
featured independence check reads the same pairs). Cluster edges are kept
between flagged (S1) wallets only: 3 or more co-entries in 90 days, or 2 or
more on events; a funding edge when two flagged wallets' first deposit came
from the same address that is not a labelled exchange, bridge or mint. A
pattern edge (same leverage, sizes within 10 %, created within 48 h) is
annotated on a pair that already has one of the above and never makes a
component on its own. Connected components with two or more members are
clusters; Strong members of a cluster of three or more read Cluster."""
from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from statistics import median

from sqlalchemy import bindparam, text

from app.db.database import get_session_factory
from app.services.ut import common, rules, scores
from app.utils.logger import get_logger

logger = get_logger(__name__)

PAIR_CAP_PER_COIN = 200_000


async def coentries(since: datetime) -> dict:
    sf = get_session_factory()
    async with sf() as s:
        coins = (await s.execute(text("SELECT DISTINCT coin FROM ut_entries WHERE time >= :a"), {"a": since})).scalars().all()
        flagged = set((await s.execute(text("SELECT wallet FROM ut_wallet_meta WHERE flagged_at IS NOT NULL"))).scalars().all())
        await s.execute(text("DELETE FROM ut_coentries WHERE time_a < :a"), {"a": since})
        await s.commit()
    total = capped = 0
    for coin in coins:
        async with sf() as s:
            rows = (await s.execute(text(
                "SELECT e.wallet, e.side, e.time, e.event_id FROM ut_entries e JOIN ut_wallet_meta m ON m.wallet=e.wallet AND m.excluded IS NULL "
                "WHERE e.coin=:c AND e.time >= :a"), {"c": coin, "a": since})).all()
        ents = [{"wallet": w, "coin": coin, "side": sd, "time": common.ms(t), "event_id": ev} for w, sd, t, ev in rows]
        pairs = rules.co_entry_pairs(ents)
        if len(pairs) > PAIR_CAP_PER_COIN:
            pairs = [p for p in pairs if p[0] in flagged or p[1] in flagged]
            capped += 1
        out = [{"a": p[0], "b": p[1], "c": p[2], "s": p[3], "ta": common.dt(p[4]), "tb": common.dt(p[5]), "ev": 1 if p[6] else 0} for p in pairs]
        async with sf() as s:
            for i in range(0, len(out), 1000):
                await s.execute(text(
                    "INSERT IGNORE INTO ut_coentries (wallet_a, wallet_b, coin, side, time_a, time_b, on_event) VALUES (:a, :b, :c, :s, :ta, :tb, :ev)"),
                    out[i:i + 1000])
            await s.commit()
        total += len(out)
    return {"pairs": total, "coins": len(coins), "capped_coins": capped}


async def entry_counts(wallets: list[str], since: datetime) -> dict[str, dict]:
    """wallet -> {(coin, side): entries in the window}, for the chance test."""
    if not wallets:
        return {}
    sf = get_session_factory()
    async with sf() as s:
        rows = (await s.execute(text(
            "SELECT wallet, coin, side, COUNT(*) FROM ut_entries WHERE time >= :a AND wallet IN :w GROUP BY wallet, coin, side"
        ).bindparams(bindparam("w", expanding=True)), {"a": since, "w": wallets})).all()
    out: dict[str, dict] = {}
    for w, c, sd, n in rows:
        out.setdefault(w, {})[(c, sd)] = int(n)
    return out


async def build(as_of: date | None = None) -> dict:
    as_of = as_of or common.now_utc().date()
    since = common.now_utc() - timedelta(days=common.WINDOW_DAYS)
    co = await coentries(since)
    labels = scores.funder_labels()
    sf = get_session_factory()
    async with sf() as s:
        flagged = sorted(set((await s.execute(text(
            "SELECT wallet FROM ut_wallet_scores WHERE as_of=:d AND s1=1"), {"d": as_of})).scalars().all()))
        if not flagged:
            await s.execute(text("DELETE FROM ut_cluster_members WHERE cluster_id IN (SELECT id FROM ut_clusters WHERE as_of=:d)"), {"d": as_of})
            await s.execute(text("DELETE FROM ut_clusters WHERE as_of=:d"), {"d": as_of})
            await s.commit()
            return {**co, "clusters": 0, "members": 0}
        pairs = (await s.execute(text(
            "SELECT wallet_a, wallet_b, coin, side, time_a, time_b, on_event FROM ut_coentries WHERE time_a >= :a AND wallet_a IN :f AND wallet_b IN :f"
        ).bindparams(bindparam("f", expanding=True)), {"a": since, "f": flagged})).all()
        meta = {w: (f, fd, lev) for w, f, fd, lev in (await s.execute(text(
            "SELECT m.wallet, m.funder, m.first_deposit_at, (SELECT ROUND(AVG(leverage),0) FROM round_trips r WHERE r.wallet=m.wallet AND r.venue='hl' "
            "AND r.close_time >= :a) FROM ut_wallet_meta m WHERE m.wallet IN :f").bindparams(bindparam("f", expanding=True)), {"a": since, "f": flagged})).all()}
        sizes = {w: float(x) for w, x in (await s.execute(text(
            "SELECT wallet, AVG(notional) FROM ut_entries WHERE time >= :a AND wallet IN :f GROUP BY wallet"
        ).bindparams(bindparam("f", expanding=True)), {"a": since, "f": flagged})).all()}
        ev_by_w = {}
        for w, ev in (await s.execute(text(
                "SELECT DISTINCT wallet, event_id FROM ut_entries WHERE time >= :a AND event_id IS NOT NULL AND wallet IN :f"
        ).bindparams(bindparam("f", expanding=True)), {"a": since, "f": flagged})).all():
            ev_by_w.setdefault(w, set()).add(int(ev))
    counts = await entry_counts(flagged, since)
    co_edges = rules.coentry_edges([(a, b, c, sd, common.ms(ta), common.ms(tb), bool(oe)) for a, b, c, sd, ta, tb, oe in pairs], counts=counts)
    edges: dict[tuple[str, str], set[str]] = {k: {"co_entry"} for k in co_edges}
    by_funder: dict[str, list[str]] = {}
    for w, (f, _fd, _lev) in meta.items():
        if f and labels.get(f, (None, None))[1] not in scores.SHARED_FUNDER_SKIP_KINDS:
            by_funder.setdefault(f, []).append(w)
    for ws in by_funder.values():
        for i in range(len(ws)):
            for j in range(i + 1, len(ws)):
                a, b = sorted((ws[i], ws[j]))
                edges.setdefault((a, b), set()).add("funding")
    for (a, b), kinds in edges.items():
        ma, mb = meta.get(a), meta.get(b)
        if not ma or not mb:
            continue
        same_lev = ma[2] is not None and mb[2] is not None and float(ma[2]) == float(mb[2])
        sa, sb = sizes.get(a), sizes.get(b)
        close_size = sa and sb and abs(sa - sb) / max(sa, sb) <= 0.10
        close_birth = ma[1] and mb[1] and abs((ma[1] - mb[1]).total_seconds()) <= 48 * 3600
        if same_lev and close_size and close_birth:
            kinds.add("pattern")
    comps = [c for c in rules.components(flagged, list(edges)) if len(c) >= 2]
    async with sf() as s:
        await s.execute(text("DELETE FROM ut_cluster_members WHERE cluster_id IN (SELECT id FROM ut_clusters WHERE as_of=:d)"), {"d": as_of})
        await s.execute(text("DELETE FROM ut_clusters WHERE as_of=:d"), {"d": as_of})
        await s.execute(text("UPDATE ut_wallet_scores SET cluster_id=NULL WHERE as_of=:d"), {"d": as_of})
        members_total = 0
        for comp in comps:
            shared = set.intersection(*[ev_by_w.get(w, set()) for w in comp]) if len(comp) > 1 else set()
            shared_any = {}
            for w in comp:
                for ev in ev_by_w.get(w, set()):
                    shared_any[ev] = shared_any.get(ev, 0) + 1
            shared_events = sorted(ev for ev, n in shared_any.items() if n >= 2)
            kinds = sorted({k for (a, b), ks in edges.items() if a in comp and b in comp for k in ks})
            births = [(meta[w][1], w) for w in comp if meta.get(w) and meta[w][1]]
            lead = min(births)[1] if births else comp[0]
            total_notional = (await s.execute(text(
                "SELECT COALESCE(SUM(notional),0) FROM ut_entries WHERE time >= :a AND wallet IN :f"
            ).bindparams(bindparam("f", expanding=True)), {"a": since, "f": comp})).scalar()
            r = await s.execute(text(
                "INSERT INTO ut_clusters (as_of, member_count, total_notional, lead_wallet, shared_events, edge_kinds, created_at) "
                "VALUES (:d, :n, :tn, :lead, :se, :ek, UTC_TIMESTAMP())"),
                {"d": as_of, "n": len(comp), "tn": float(total_notional or 0), "lead": lead, "se": json.dumps(shared_events),
                 "ek": json.dumps(kinds)})
            cid = r.lastrowid
            for w in comp:
                n_edges = sum(1 for (a, b) in edges if w in (a, b) and a in comp and b in comp)
                await s.execute(text("INSERT INTO ut_cluster_members (cluster_id, wallet, edges) VALUES (:c, :w, :e)"), {"c": cid, "w": w, "e": n_edges})
                await s.execute(text(
                    "UPDATE ut_wallet_scores SET cluster_id=:c, level=IF(level='strong' AND :n >= 3, 'cluster', level) WHERE as_of=:d AND wallet=:w"),
                    {"c": cid, "n": len(comp), "d": as_of, "w": w})
            members_total += len(comp)
        await s.commit()
    out = {**co, "flagged": len(flagged), "edges": len(edges), "clusters": len(comps), "members": members_total}
    logger.info("ut clusters: %s", out)
    return out
