"""Unusual Timing web API (phase 2 page): read-only, cached 60 s. Owner
2026-11-04: admin only for now (ADMIN_ADDRESSES), the whitelist gate comes
back when the page opens to whitelisted users; a non-admin gets the same
403 {"reason": "whitelist"} as the rest of Featured. Numbers
and levels come from the ut_ tables; nothing is recomputed here and no
Hyperliquid read happens on these routes.

  GET /api/ut/summary
  GET /api/ut/wallets?level&min_hit&min_events&max_age_days&coin&has_open&active_within_h&sort&dir
  GET /api/ut/wallet/{address}
  GET /api/ut/cluster/{id}
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import text

from app.db.database import get_session_factory
from app.db.models import User
from app.db.models import User as _U  # noqa: F401
from app.routers.auth import get_optional_user
from app.routers.featured import Forbidden
from app.services.featured import whitelist


async def require_featured(user: User | None = Depends(get_optional_user)) -> User:
    """Admin only for now (owner 2026-11-04)."""
    if user is None or not whitelist.is_admin(user.wallet_address):
        raise Forbidden()
    return user

from app.services.ut import api as utapi
from app.utils import resp_cache

router = APIRouter(prefix="/ut", tags=["unusual-timing"])

_rows_cache = resp_cache.RespCache("ut_rows", ttl=60)
_summary_cache = resp_cache.RespCache("ut_summary", ttl=60)
_wallet_cache = resp_cache.RespCache("ut_wallet", ttl=60)
_cluster_cache = resp_cache.RespCache("ut_cluster", ttl=60)
_candles_cache = resp_cache.RespCache("ut_candles", ttl=300)


async def _rows() -> dict:
    return await _rows_cache.get("all", lambda: utapi.build_rows(flagged_only=True))


async def _summary_build() -> dict:
    data = await _rows()
    rows = data["rows"]
    by_level = {lv: sum(1 for r in rows if r["level"] == lv) for lv in utapi.LEVELS}
    opens = [(r, p) for r in rows for p in (r.get("open_all") or [])]
    sf = get_session_factory()
    async with sf() as s:
        clusters = (await s.execute(text("SELECT COUNT(*), MAX(member_count) FROM ut_clusters WHERE as_of = (SELECT MAX(as_of) FROM ut_clusters)"))).first()
        last = (await s.execute(text(
            "SELECT wallet, coin, side, notional, level, payload, sent_at, created_at FROM ut_alerts_sent WHERE sent_at IS NOT NULL "
            "AND wallet NOT LIKE '0x0000%' AND level <> 'test' ORDER BY id DESC LIMIT 1"))).first()
        drift = (await s.execute(text(
            "SELECT coin, drift_score, funding_trend_48h, oi_change_6h, oi_change_24h, abn_ret_12h, ut_wallets_6h, t FROM ut_market_drift "
            "WHERE t = (SELECT MAX(t) FROM ut_market_drift WHERE drift_score > 0) ORDER BY drift_score DESC LIMIT 8"))).all()
        nxt = (await s.execute(text(
            "SELECT time, kind, coin, note FROM ut_events WHERE time > UTC_TIMESTAMP() AND kind IN ('macro','manual') ORDER BY time LIMIT 1"))).first()
        feed = (await s.execute(text("SELECT last_msg_at FROM hl_feed_status WHERE id=1"))).scalar()
    la = None
    if last:
        try:
            p = json.loads(last[5]) if isinstance(last[5], str) else (last[5] or {})
        except ValueError:
            p = {}
        la = {"wallet": last[0], "coin": last[1], "side": last[2], "notional": float(last[3] or 0), "level": last[4],
              "leverage": p.get("leverage"), "at": utapi._iso(last[6] or last[7])}
    return {
        "scores_as_of": data["as_of"], "scored": data["scored"], "flagged": len(rows), "by_level": by_level,
        "in_clusters": sum(1 for r in rows if r.get("cluster_id")),
        "clusters": int(clusters[0] or 0) if clusters else 0, "largest_cluster": int(clusters[1] or 0) if clusters and clusters[1] else 0,
        "open_now": {"count": len(opens), "notional": round(sum(p["notional"] for _r, p in opens), 2),
                     "short": sum(1 for _r, p in opens if p["side"] == "short"), "long": sum(1 for _r, p in opens if p["side"] == "long")},
        "last_alert": la,
        "drift": [{"coin": c, "score": float(sc), "funding_trend_48h": utapi._f(f), "oi_change_6h": utapi._f(o6), "oi_change_24h": utapi._f(o24),
                   "abn_ret_12h": utapi._f(z), "flagged_entries_6h": int(n or 0), "at": utapi._iso(t)} for c, sc, f, o6, o24, z, n, t in drift],
        "next_event": ({"time": utapi._iso(nxt[0]), "kind": nxt[1], "coin": nxt[2], "note": nxt[3]} if nxt else None),
        "live_at": utapi._iso(feed), "server_time": utapi._iso(datetime.utcnow()),
    }


@router.get("/summary")
async def summary(user: User = Depends(require_featured)) -> dict:
    return await _summary_cache.get("all", _summary_build)


def _row_public(r: dict) -> dict:
    return {k: v for k, v in r.items() if k not in ("evidence", "open_all")}


@router.get("/wallets")
async def wallets(user: User = Depends(require_featured), level: str | None = Query(None), min_hit: float | None = Query(None),
                  min_events: int | None = Query(None), max_age_days: float | None = Query(None), coin: str | None = Query(None),
                  has_open: bool | None = Query(None), active_within_h: float | None = Query(None),
                  sort: str = Query("score"), dir: str = Query("desc")) -> dict:
    data = await _rows()
    lv = (level or "").lower() or None
    if lv and lv not in utapi.LEVELS:
        raise HTTPException(status_code=422, detail="level must be watch, strong or cluster")
    rows = utapi.apply_filters(data["rows"], level=lv, min_hit=min_hit, min_events=min_events, max_age_days=max_age_days, coin=coin,
                               has_open=has_open, active_within_h=active_within_h)
    rows = utapi.sort_rows(rows, sort, dir)
    return {"scores_as_of": data["as_of"], "scored": data["scored"], "total": len(data["rows"]), "count": len(rows),
            "rows": [_row_public(r) for r in rows], "built_at": data["built_at"]}


async def _wallet_build(address: str) -> dict:
    data = await _rows()
    row = next((r for r in data["rows"] if r["wallet"] == address), None)
    if row is None:
        # a wallet that was scored but not listed still has a dossier
        allrows = await _rows_cache.get("scored", lambda: utapi.build_rows(flagged_only=False))
        row = next((r for r in allrows["rows"] if r["wallet"] == address), None)
    if row is None:
        raise HTTPException(status_code=404, detail="no unusual-timing record for this wallet")
    entries = await utapi.wallet_entries(address)
    trips = await utapi.wallet_trip_stats(address)
    main_coin = row["coins"][0] if row["coins"] else None
    candles = await _candles_cache.get(main_coin, lambda: utapi.candle_series(main_coin)) if main_coin else None
    ev = row.get("evidence") or {}
    hits = [e for e in entries if e["hit"]]
    shock = max([e["size_vs_median"] for e in hits if e["size_vs_median"] is not None], default=None)
    sig = row["signals"]
    signals = [
        {"key": "s1", "name": "Pre-move timing", "pass": sig["s1"],
         "value": f"{(row['hit_rate'] or 0) * 100:.0f}% vs {(row['base_rate'] or 0) * 100:.1f}% by chance, {row['odds_text']}"},
        {"key": "s2", "name": "Event proximity", "pass": sig["s2"], "value": f"{row['events_preceded']} events, {row['event_kinds_n']} kind{'s' if row['event_kinds_n'] != 1 else ''}"},
        {"key": "s3", "name": "Burst profile", "pass": sig["s3"],
         "value": (f"top 3 = {row['top3_profit_share'] * 100:.0f}% of profit" if row["top3_profit_share"] is not None else "no net profit")
                  + (f", median hold {row['median_hold_min'] / 60:.1f} h" if row["median_hold_min"] is not None else "")},
        {"key": "s4", "name": "Fresh and large", "pass": sig["s4"],
         "value": (f"{row['age_days']:.0f} d old" if row["age_days"] is not None else "age unknown until the ledger is read")
                  + (f", first position ${ev.get('first_position_usd'):,.0f}" if ev.get("first_position_usd") else "")},
        {"key": "s5", "name": "Size shock", "pass": sig["s5"], "value": (f"{shock:.1f}x own median on a hit" if shock else "no size jump on a hit")},
        {"key": "s6", "name": "Cluster", "pass": sig["s6"], "value": (f"{row['co_entries']} co-entries with flagged wallets" if row["co_entries"] else "no co-entries with flagged wallets")},
        {"key": "s7", "name": "Exit speed", "pass": sig["s7"], "value": f"closes within 2 h of the peak on {row['exit_speed_hits']} of {row['hits']} hits"},
    ]
    return {**_row_public(row), "entries_list": entries, "signals_detail": signals, **trips, "candles": candles,
            "level_words": utapi.LEVEL_WORDS.get(row["level"], "Not listed."), "scores_as_of": data["as_of"],
            "first_deposit_usd": row.get("first_deposit_usd"), "evidence_events": ev.get("events") or [], "top_trades": ev.get("top_trades") or []}


@router.get("/wallet/{address}")
async def wallet(address: str, user: User = Depends(require_featured)) -> dict:
    a = address.lower()
    if not (a.startswith("0x") and len(a) == 42):
        raise HTTPException(status_code=422, detail="address must be 0x and 40 hex characters")
    return await _wallet_cache.get(a, lambda: _wallet_build(a))


async def _cluster_build(cid: int) -> dict:
    data = await _rows()
    # members below Watch still belong to the cluster: use every scored wallet
    allrows = await _rows_cache.get("scored", lambda: utapi.build_rows(flagged_only=False))
    by = {r["wallet"]: _row_public(r) | {"open_all": r.get("open_all")} for r in allrows["rows"]}
    out = await utapi.cluster_detail(cid, by)
    if out is None:
        raise HTTPException(status_code=404, detail="no such cluster")
    out["scores_as_of"] = data["as_of"]
    for m in out["members"]:
        m.pop("open_all", None)
    return out


@router.get("/cluster/{cid}")
async def cluster(cid: int, user: User = Depends(require_featured)) -> dict:
    return await _cluster_cache.get(str(cid), lambda: _cluster_build(cid))
