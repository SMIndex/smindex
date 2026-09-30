"""Phase-3 depth helpers (ANALYTICS_BUILD_REPORT Phase 3).

Pure computation + bounded DB reads — no venue requests anywhere here.

  * crowding: per (asset, side) greedy entry-price clustering. Entries are
    sorted ascending; a cluster is the maximal run whose entries stay within
    ENTRY_TOL of the cluster's lowest entry (so any two members are within
    ~2x ENTRY_TOL of each other). Crowded = >= MIN_WALLETS distinct wallets.
    Score = distinct wallet count (documented; notional shown alongside).
  * conviction: position notional / account value, from the wallet-state
    rows the sweep persists (migration v10). Wallets the sweep served from
    the tracker ws path carry no account value — honestly absent.
  * trigger observations: resting TP/SL seen in the foreground profile cache
    at sweep time (zero extra requests). Coverage is inherently sparse —
    the panel claims clusters only at >= TRIGGER_MIN_CLAIM observed triggers
    and otherwise states the measured prevalence.
  * latency: in-process ring buffers per endpoint; p50/p95 exposed via
    /api/analytics/status (spec: "state the p95 latency measured").
"""
import time
from collections import deque

from sqlalchemy import text

from app.db.database import get_session_factory

ENTRY_TOL = 0.01                 # ±1% (spec)
CROWDING_MIN_WALLETS = 3         # >= K cohort wallets at similar entries
TRIGGER_MIN_CLAIM = 5            # min observed triggers before claiming clusters
CONVICTION_FLAG_PCT = 25.0       # spec: flag > 25% of account value

ACCOUNT_BANDS = {
    "lt_10k": (0.0, 1e4), "10k_100k": (1e4, 1e5), "100k_1m": (1e5, 1e6),
    "1m_10m": (1e6, 1e7), "gt_10m": (1e7, float("inf")),
}
LEV_BANDS = {
    "0_2": (0.0, 2.0), "2_5": (2.0, 5.0), "5_10": (5.0, 10.0),
    "10_plus": (10.0, float("inf")),
}

_latency: dict[str, deque] = {}


def record_latency(endpoint: str, ms: float) -> None:
    _latency.setdefault(endpoint, deque(maxlen=500)).append(ms)


def latency_stats() -> dict:
    out = {}
    for ep, buf in _latency.items():
        vals = sorted(buf)
        if not vals:
            continue
        out[ep] = {
            "n": len(vals),
            "p50_ms": round(vals[len(vals) // 2], 1),
            "p95_ms": round(vals[min(len(vals) - 1, int(len(vals) * 0.95))], 1),
            "max_ms": round(vals[-1], 1),
        }
    return out


def crowding_clusters(rows: list[dict]) -> list[dict]:
    """rows: raw position dicts (wallet/side/entry_px/notional) for ONE asset.
    Returns crowded clusters sorted by wallet count desc, notional desc."""
    clusters: list[dict] = []
    for side in ("long", "short"):
        pts = sorted((r for r in rows if r["side"] == side and r["entry_px"]),
                     key=lambda r: r["entry_px"])
        i = 0
        while i < len(pts):
            base = pts[i]["entry_px"]
            j = i
            members = []
            while j < len(pts) and pts[j]["entry_px"] <= base * (1 + 2 * ENTRY_TOL):
                members.append(pts[j])
                j += 1
            wallets = sorted({m["wallet"].lower() for m in members})
            if len(wallets) >= CROWDING_MIN_WALLETS:
                clusters.append({
                    "side": side,
                    "entry_lo": round(members[0]["entry_px"], 6),
                    "entry_hi": round(members[-1]["entry_px"], 6),
                    "wallet_count": len(wallets),
                    "notional": round(sum(m["notional"] for m in members), 2),
                    "wallets": [{
                        "wallet": m["wallet"],
                        "entry_px": m["entry_px"],
                        "notional": round(m["notional"], 2),
                        "leverage": m.get("leverage"),
                    } for m in sorted(members, key=lambda m: -m["notional"])],
                })
            i = j
    clusters.sort(key=lambda c: (-c["wallet_count"], -c["notional"]))
    return clusters


async def account_values(wallets: list[str]) -> dict[str, float]:
    """Latest known account value per wallet from analytics_wallet_state
    (one bounded query; wallets without a row are simply absent)."""
    wl = sorted({w.lower() for w in wallets})
    if not wl:
        return {}
    placeholders = ",".join(f":w{i}" for i in range(len(wl)))
    params = {f"w{i}": w for i, w in enumerate(wl)}
    sf = get_session_factory()
    async with sf() as s:
        rows = (await s.execute(text(
            "SELECT s.wallet, s.account_value FROM analytics_wallet_state s "
            "JOIN (SELECT wallet, MAX(cycle_ts) mt FROM analytics_wallet_state "
            f"      WHERE wallet IN ({placeholders}) GROUP BY wallet) l "
            "ON l.wallet = s.wallet AND l.mt = s.cycle_ts"), params)).all()
    return {w.lower(): float(av) for w, av in rows
            if av is not None and float(av) > 0}


def conviction(notional: float, leverage: float | None,
               account_value: float | None) -> dict | None:
    """MARGIN-based conviction (owner decision 2026-08-26): the position's
    margin (notional / leverage) as a share of account value — "uses X% of
    account as margin". None when leverage OR account value is unknown
    (tracker-ws rows carry no leverage) — never invented."""
    if not account_value or account_value <= 0 or not leverage or leverage <= 0:
        return None
    pct = round(100.0 * (notional / leverage) / account_value, 1)
    return {"pct": pct, "flagged": pct > CONVICTION_FLAG_PCT}


async def trigger_snapshot(asset: str, days: int = 7) -> dict:
    """Observed resting TP/SL for one asset + observation coverage.
    kind 'chk' rows mark wallets checked (denominator); real rows are tp/sl."""
    sf = get_session_factory()
    async with sf() as s:
        trig = (await s.execute(text(
            "SELECT wallet, side, kind, trigger_px, size, observed_at "
            "FROM analytics_trigger_obs WHERE asset = :a AND kind IN ('tp','sl') "
            "AND observed_at >= UTC_TIMESTAMP() - INTERVAL :d DAY "
            "ORDER BY trigger_px"), {"a": asset, "d": days})).mappings().all()
        cov = (await s.execute(text(
            "SELECT COUNT(DISTINCT wallet), "
            " COUNT(DISTINCT CASE WHEN kind IN ('tp','sl') THEN wallet END) "
            "FROM analytics_trigger_obs "
            "WHERE observed_at >= UTC_TIMESTAMP() - INTERVAL :d DAY"),
            {"d": days})).first()
    return {
        "triggers": [{
            "wallet": r["wallet"], "side": r["side"], "kind": r["kind"],
            "trigger_px": float(r["trigger_px"]),
            "size": float(r["size"]) if r["size"] is not None else None,
            "observed_at": r["observed_at"].isoformat(),
        } for r in trig],
        "wallets_checked": int(cov[0] or 0),
        "wallets_with_triggers": int(cov[1] or 0),
        "min_claim": TRIGGER_MIN_CLAIM,
        "window_days": days,
    }
