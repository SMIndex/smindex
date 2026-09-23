"""Same-entity clustering (ANALYTICS_TIER2_REPORT Part C2).

Nightly, DB-only (zero venue calls): scans the trailing 7d of stored fills
(`hl_fill_events` — i.e. only wallets the Tier-2 fill sampler covers; wallets
without sampled fills CANNOT be clustered and stay independent — documented
honest limitation) for pairs of cohort wallets that trade like one entity.

PAIR RULE (spec): >= PAIR_MIN_MATCHES (10) fills within ±MATCH_WINDOW_MS (2s)
on the same asset AND side, at proportional sizes.
  * side is derived from the fill dir ("Open Long"/"Close Short"/flips —
    the segment before '>' decides), same parsing as fill_stats.
  * matching: per (coin, side), fills sorted by time; a sliding 2s window
    records cross-wallet size pairs (capped at PAIR_CAP per pair so two
    always-on MMs cannot blow memory).
  * proportional: pair confidence = share of matched size-ratios within
    ±RATIO_TOL (30%) of the pair's median ratio. Two wallets that happen to
    trade the same seconds at unrelated sizes score low.

MERGE RULE (spec: "do not merge clusters below the confidence threshold"):
only pairs with confidence >= CONF_MERGE (0.75) enter the union-find that
forms clusters. Sub-threshold pairs are logged in the run stats as
`pairs_below_threshold` (visible evidence, never merged). A union-find
component larger than MAX_CLUSTER_SIZE (8) is NOT persisted either — first
real-data run chained 24 high-frequency wallets into one "entity" through
transitive links; that shape is common-signal co-trading, not one entity,
and merging it would silently erase a third of the active cohort's votes.
Oversized components are logged (`oversized_components`) for the report.

Persistence: analytics_wallet_clusters replaced wholesale per run —
(cluster_id, wallet, confidence = the wallet's best qualifying pair
confidence, evidence JSON = partners + matched counts + median ratio).

Consumers (asset endpoint): count-weighted metrics take ONE VOTE PER CLUSTER;
the UI tooltip states "N wallets deduped as M entities". Rollups/SMI counts
are NOT deduped in this pass (their cohort basis is documented per-wallet);
noted in the report.
"""
import asyncio
import json
import statistics
import time
from datetime import datetime

from sqlalchemy import text

from app.db.database import get_session_factory
from app.utils.logger import get_logger

logger = get_logger(__name__)

INTERVAL_SEC = 24 * 3600
FIRST_RUN_DELAY_SEC = 1500          # after boot: cohort + samplers settle
WINDOW_DAYS = 7
MATCH_WINDOW_MS = 2000
PAIR_MIN_MATCHES = 10
RATIO_TOL = 0.30
CONF_MERGE = 0.75
MAX_CLUSTER_SIZE = 8                # bigger components = common-signal noise
PAIR_CAP = 500                      # matched samples kept per pair

_task: asyncio.Task | None = None
_last_run: dict = {}
# wallet -> cluster_id, refreshed after each run + on boot (for consumers)
_cluster_of: dict[str, int] = {}
_loaded = False


def _side_of(direction: str) -> str:
    head = str(direction).split(">")[0]
    return "long" if "Long" in head else "short"


async def run_once() -> dict:
    from app.services.analytics import cohort
    t0 = time.monotonic()
    await cohort.ensure()
    cohort_set = {w.lower() for w in cohort.wallets()}
    cut_ms = int((time.time() - WINDOW_DAYS * 86400) * 1000)

    sf = get_session_factory()
    async with sf() as s:
        rows = (await s.execute(text(
            "SELECT wallet_address, ts_ms, coin, dir, sz FROM hl_fill_events "
            "WHERE ts_ms >= :cut ORDER BY coin, ts_ms"), {"cut": cut_ms}
        )).all()

    # group fills per (coin, side), cohort wallets only
    groups: dict[tuple[str, str], list[tuple[int, str, float]]] = {}
    for w, ts, coin, d, sz in rows:
        wl = w.lower()
        if wl not in cohort_set:
            continue
        try:
            fsz = float(sz)
        except (TypeError, ValueError):
            continue
        if fsz <= 0:
            continue
        groups.setdefault((coin, _side_of(d)), []).append((int(ts), wl, fsz))

    # sliding-window cross-wallet pair collection
    pair_samples: dict[tuple[str, str], list[tuple[float, float]]] = {}
    for (_coin, _side), fills in groups.items():
        fills.sort()
        n = len(fills)
        j = 0
        for i in range(n):
            ts_i, w_i, sz_i = fills[i]
            j = max(j, i + 1)
            k = i + 1
            while k < n and fills[k][0] - ts_i <= MATCH_WINDOW_MS:
                ts_k, w_k, sz_k = fills[k]
                if w_k != w_i:
                    key = (min(w_i, w_k), max(w_i, w_k))
                    lst = pair_samples.setdefault(key, [])
                    if len(lst) < PAIR_CAP:
                        # ratio oriented by the ordered key
                        a, b = (sz_i, sz_k) if w_i < w_k else (sz_k, sz_i)
                        lst.append((a, b))
                k += 1

    # score pairs
    qualifying: list[tuple[str, str, float, dict]] = []
    below = 0
    for (wa, wb), samples in pair_samples.items():
        if len(samples) < PAIR_MIN_MATCHES:
            continue
        ratios = [a / b for a, b in samples if b > 0]
        if not ratios:
            continue
        med = statistics.median(ratios)
        if med <= 0:
            continue
        inlier = sum(1 for r in ratios if abs(r - med) / med <= RATIO_TOL)
        conf = round(inlier / len(ratios), 4)
        ev = {"matched": len(samples), "median_ratio": round(med, 4),
              "inlier_share": conf}
        if conf >= CONF_MERGE:
            qualifying.append((wa, wb, conf, ev))
        else:
            below += 1

    # union-find over qualifying pairs only
    parent: dict[str, str] = {}

    def find(x: str) -> str:
        parent.setdefault(x, x)
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a: str, b: str) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra

    best_conf: dict[str, float] = {}
    partners: dict[str, list[dict]] = {}
    for wa, wb, conf, ev in qualifying:
        union(wa, wb)
        for w, other in ((wa, wb), (wb, wa)):
            best_conf[w] = max(best_conf.get(w, 0.0), conf)
            partners.setdefault(w, []).append({"partner": other, **ev})

    # components; oversized ones are refused (see module doc) and logged
    members: dict[str, list[str]] = {}
    for w in best_conf:
        members.setdefault(find(w), []).append(w)
    oversized = {r: ws for r, ws in members.items() if len(ws) > MAX_CLUSTER_SIZE}
    for r, ws in oversized.items():
        logger.warning("entity clustering: component of %d wallets exceeds "
                       "MAX_CLUSTER_SIZE=%d — NOT merged (common-signal "
                       "co-trading shape): %s", len(ws), MAX_CLUSTER_SIZE,
                       [w[:10] for w in ws])
    kept_roots = sorted(r for r in members if r not in oversized)
    root_id = {r: i + 1 for i, r in enumerate(kept_roots)}
    now = datetime.utcnow()
    out_rows = [{"cid": root_id[r], "w": w, "cf": best_conf[w],
                 "ev": json.dumps(partners[w][:10]), "ca": now}
                for r in kept_roots for w in sorted(members[r])]

    async with sf() as s:
        await s.execute(text("DELETE FROM analytics_wallet_clusters"))
        if out_rows:
            await s.execute(text(
                "INSERT INTO analytics_wallet_clusters "
                "(cluster_id, wallet, confidence, evidence, computed_at) "
                "VALUES (:cid, :w, :cf, :ev, :ca)"), out_rows)
        await s.commit()

    global _cluster_of, _loaded
    _cluster_of = {r["w"]: r["cid"] for r in out_rows}
    _loaded = True

    stats = {"ts": now.isoformat(),
             "fills_scanned": len(rows),
             "pairs_sampled": len(pair_samples),
             "pairs_qualifying": len(qualifying),
             "pairs_below_threshold": below,
             "clusters": len(kept_roots),
             "clustered_wallets": len(out_rows),
             "oversized_components": [len(ws) for ws in oversized.values()],
             "duration_sec": round(time.monotonic() - t0, 1)}
    _last_run.clear()
    _last_run.update(stats)
    logger.info("entity clustering: %s", stats)
    return stats


async def ensure_loaded() -> None:
    """Boot hydration for consumers: load the stored table once per process."""
    global _cluster_of, _loaded
    if _loaded:
        return
    try:
        sf = get_session_factory()
        async with sf() as s:
            rows = (await s.execute(text(
                "SELECT wallet, cluster_id FROM analytics_wallet_clusters"))).all()
        _cluster_of = {w.lower(): int(c) for w, c in rows}
    except Exception:
        logger.exception("cluster hydration failed (non-fatal — no dedup)")
    _loaded = True


def entity_of(wallet: str) -> str:
    """Entity key for count-dedup: cluster id when clustered, else the wallet
    itself (one vote per cluster, independent wallets unchanged)."""
    w = wallet.lower()
    cid = _cluster_of.get(w)
    return f"cluster:{cid}" if cid is not None else w


def cluster_map() -> dict[str, int]:
    return dict(_cluster_of)


async def _loop() -> None:
    await asyncio.sleep(FIRST_RUN_DELAY_SEC)
    while True:
        try:
            await run_once()
        except Exception:
            logger.exception("entity clustering failed — next attempt tomorrow")
        await asyncio.sleep(INTERVAL_SEC)


def start() -> None:
    global _task
    if _task is None or _task.done():
        _task = asyncio.get_event_loop().create_task(_loop())
        logger.info("entity clustering started (nightly, 7d fills, merge conf >= %s)",
                    CONF_MERGE)


async def stop() -> None:
    global _task
    if _task and not _task.done():
        _task.cancel()
        try:
            await _task
        except asyncio.CancelledError:
            pass
    _task = None


def get_last_run() -> dict:
    return dict(_last_run)
