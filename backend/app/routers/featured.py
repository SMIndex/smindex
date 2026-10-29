"""Featured traders API (BUILD_BRIEF section 3). Every data route needs a
signed-in wallet that is whitelisted or an admin; otherwise 403
{"reason": "whitelist"}. /preview and /whitelist/request are open."""
from __future__ import annotations

import json
import time
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel
from sqlalchemy import bindparam, text

from app.db.database import get_session_factory
from app.db.models import User
from app.routers.auth import get_optional_user
from app.services.featured import jobs, live, roundtrips as rt, whitelist
from app.services.featured.preview import PREVIEW
from app.utils import resp_cache
from app.utils.logger import get_logger

logger = get_logger(__name__)

router = APIRouter(prefix="/featured", tags=["featured"])
admin_router = APIRouter(prefix="/admin/featured", tags=["featured-admin"])

_rows_cache = resp_cache.RespCache("featured_rows", ttl=60)
_trips_cache = resp_cache.RespCache("featured_trips", ttl=60)
_candles_cache = resp_cache.RespCache("featured_candles", ttl=600)
_smi_cache = resp_cache.RespCache("featured_smi", ttl=120)

ACTIVE_MS = {"24h": 86_400_000, "7d": 7 * 86_400_000, "14d": 14 * 86_400_000}
WITHIN_MS = {"1h": 3_600_000, "24h": 86_400_000, "7d": 7 * 86_400_000, "30d": 30 * 86_400_000}
SIZE_BANDS = {"lt10k": (0, 10_000), "10k-100k": (10_000, 100_000), "100k-1m": (100_000, 1_000_000), "gt1m": (1_000_000, 1e18)}
HOLD_BANDS = {"scalper": (0, 3600), "intraday": (3600, 86400), "swing": (86400, 1e12)}
SORTABLE = {"score", "win_rate_90d", "net_pnl_90d", "profit_factor_90d", "avg_win_usd", "max_drawdown_90d",
            "trades_per_week", "open_count", "last_trade_ms", "trades_90d", "account_value", "median_leverage", "copiable_pct"}


class Forbidden(Exception):
    """403 {"reason": "whitelist"} (main.py registers the handler)."""


async def require_featured(user: User | None = Depends(get_optional_user)) -> User:
    if user is None or not await whitelist.is_allowed(user.wallet_address):
        raise Forbidden()
    return user


async def require_admin(user: User | None = Depends(get_optional_user)) -> User:
    if user is None or not whitelist.is_admin(user.wallet_address):
        raise HTTPException(status_code=403, detail="Admin access required")
    return user


def _iso(dt) -> str | None:
    if dt is None:
        return None
    if isinstance(dt, str):
        return dt
    return dt.isoformat(timespec="seconds")


def _ms(dt) -> int | None:
    return rt.dt_to_ms(dt) if dt else None


def _fl(v):
    return float(v) if v is not None else None


def _momentum(v30, v90, rel: bool = True) -> str | None:
    """'up' / 'down' / 'flat' when the 30-day value differs from the 90-day by
    more than 10 %; None when either is missing."""
    if v30 is None or v90 is None:
        return None
    base = abs(v90) if rel else 1.0
    if base < 1e-9:
        return "up" if v30 > 0 else ("down" if v30 < 0 else "flat")
    d = (v30 - v90) / base
    return "up" if d > 0.10 else ("down" if d < -0.10 else "flat")


# ---------------------------------------------------------------------------
# rows
# ---------------------------------------------------------------------------

async def _latest_rows() -> dict:
    """Latest featured_wallets row per (venue, wallet) joined with featured_live."""
    sf = get_session_factory()
    async with sf() as s:
        rows = (await s.execute(text(
            "SELECT f.as_of, f.venue, f.wallet, f.label, f.qualified, f.inactive, f.one_big_win, f.score, f.verdict, f.style, f.display_name, "
            "f.trades_90d, f.days_active_90d, f.win_rate_90d, f.win_rate_30d, f.net_pnl_90d, f.net_pnl_30d, f.net_pnl_all, "
            "f.profit_factor_90d, f.profit_factor_30d, f.avg_win_usd, f.avg_loss_usd, f.avg_win_pct_eq, f.avg_loss_pct_eq, "
            "f.avg_win_move_pct, f.avg_loss_move_pct, f.max_drawdown_90d, f.dd_start, f.dd_end, f.worst_trade_usd, f.worst_trade_pct_eq, "
            "f.worst_day_usd, f.worst_week_usd, f.largest_trade_share, f.profitable_weeks_13, f.longest_loss_streak, f.loss_streak_at, "
            "f.median_hold_sec, f.trades_per_week, f.trades_per_day_max, f.median_leverage, f.max_leverage, f.maker_share, "
            "f.pnl_per_volume, f.long_share, f.avg_equity_90d, f.account_value, f.volume_90d, f.top_coins, f.coin_stats, f.hours_utc, "
            "f.copiable_pct, f.liquidations_90d, f.usual_open, f.history_from, f.flags, f.score_inputs, f.last_trade_at, f.computed_at, "
            "l.last_trade_at, l.open_positions, l.open_count, l.unrealized_usd, l.account_value, l.positions_at, l.updated_at "
            "FROM featured_wallets f JOIN ("
            " SELECT venue, wallet, MAX(as_of) mx FROM featured_wallets GROUP BY venue, wallet"
            ") x ON x.venue=f.venue AND x.wallet=f.wallet AND x.mx=f.as_of "
            "LEFT JOIN featured_live l ON l.venue=f.venue AND l.wallet=f.wallet"))).all()
    out = []
    now_ms = int(time.time() * 1000)
    for r in rows:
        flags = json.loads(r[55]) if r[55] else {}
        extra = flags.pop("_extra", {}) if isinstance(flags, dict) else {}
        coin_stats = json.loads(r[49]) if r[49] else []
        top = json.loads(r[48]) if r[48] else []
        on_perpl = sum(1 for c in coin_stats if c.get("coin") in top and c.get("on_perpl"))
        live_last = r[58]
        last_ms = max([m for m in (_ms(r[57]), _ms(live_last)) if m], default=None)
        live_av = _fl(r[62])
        out.append({
            "as_of": r[0].isoformat(), "venue": r[1], "wallet": r[2], "label": r[3], "qualified": bool(r[4]), "inactive": bool(r[5]),
            "one_big_win": bool(r[6]), "score": _fl(r[7]), "verdict": r[8], "style": r[9], "display_name": r[10],
            "trades_90d": r[11], "days_active_90d": r[12], "win_rate_90d": _fl(r[13]), "win_rate_30d": _fl(r[14]),
            "net_pnl_90d": _fl(r[15]), "net_pnl_30d": _fl(r[16]), "net_pnl_all": _fl(r[17]),
            "profit_factor_90d": _fl(r[18]), "profit_factor_30d": _fl(r[19]), "avg_win_usd": _fl(r[20]), "avg_loss_usd": _fl(r[21]),
            "avg_win_pct_eq": _fl(r[22]), "avg_loss_pct_eq": _fl(r[23]), "avg_win_move_pct": _fl(r[24]), "avg_loss_move_pct": _fl(r[25]),
            "max_drawdown_90d": _fl(r[26]), "dd_start": _iso(r[27]), "dd_end": _iso(r[28]), "worst_trade_usd": _fl(r[29]),
            "worst_trade_pct_eq": _fl(r[30]), "worst_day_usd": _fl(r[31]), "worst_week_usd": _fl(r[32]), "largest_trade_share": _fl(r[33]),
            "profitable_weeks_13": r[34], "longest_loss_streak": r[35], "loss_streak_at": _iso(r[36]), "median_hold_sec": r[37],
            "trades_per_week": _fl(r[38]), "trades_per_day_max": _fl(r[39]), "median_leverage": _fl(r[40]), "max_leverage": _fl(r[41]),
            "maker_share": _fl(r[42]), "pnl_per_volume": _fl(r[43]), "long_share": _fl(r[44]), "avg_equity_90d": _fl(r[45]),
            "account_value": (live_av if live_av is not None else _fl(r[46])), "volume_90d": _fl(r[47]),
            "top_coins": top, "top_coins_on_perpl": on_perpl, "coin_stats": coin_stats, "hours_utc": (json.loads(r[50]) if r[50] else [0] * 24),
            "copiable_pct": _fl(r[51]), "liquidations_90d": r[52], "usual_open": r[53], "history_from": _iso(r[54]),
            "flags": flags, "score_inputs": (json.loads(r[56]) if r[56] else {}), "computed_at": _iso(r[57 + 1 - 1]) and _iso(r[58 - 1]),
            "last_trade_at": (rt.ms_to_dt(last_ms).isoformat(timespec="seconds") if last_ms else None), "last_trade_ms": last_ms,
            "open_positions": (json.loads(r[59]) if r[59] else []), "open_count": int(r[60] or 0), "unrealized_usd": _fl(r[61]),
            "positions_at": _iso(r[63]), "live_updated_at": _iso(r[64]),
            "streak_now": extra.get("streak_now"), "confidence": extra.get("confidence"), "weekly": extra.get("weekly"),
            "averages_down_n": extra.get("averages_down_n"), "adds_to_winner_n": extra.get("adds_to_winner_n"),
            "momentum": {"win_rate": _momentum(_fl(r[14]), _fl(r[13])), "pf": _momentum(_fl(r[19]), _fl(r[18])),
                         "pnl": _momentum(_fl(r[16]), (_fl(r[15]) or 0) / 3.0)},
            "active": bool(last_ms and now_ms - last_ms <= ACTIVE_MS["14d"]),
        })
    for o in out:
        o["computed_at"] = None
    return {"rows": out, "built_at": datetime.utcnow().isoformat(timespec="seconds")}


def _row_public(o: dict) -> dict:
    """Tab 1 row: drops the heavy detail fields."""
    keep = {k: v for k, v in o.items() if k not in ("coin_stats", "hours_utc", "weekly", "flags", "score_inputs")}
    keep["checks_passed"] = sum(1 for v in o["flags"].values() if isinstance(v, dict) and v.get("pass") is True)
    return keep


@router.get("/wallets")
async def list_wallets(
    request: Request, user: User = Depends(require_featured),
    venue: str = Query("all"), label: str = Query("consistent,asymmetric"),
    minWinRate: float = Query(0, ge=0, le=100), minPf: float = Query(0, ge=0), minTrades: int = Query(30, ge=0),
    minProfit: float = Query(0, ge=0), maxDd: float = Query(0, ge=0, le=100), size: str = Query(""),
    hold: str = Query(""), coins: str = Query(""), perplOnly: int = Query(0), active: str = Query("14d"),
    maxLev: float = Query(0, ge=0), hideSmall: int = Query(1), sort: str = Query("score"), dir: str = Query("desc"),
) -> dict:
    data = await _rows_cache.get("all", _latest_rows)
    rows = [o for o in data["rows"] if o["qualified"]]
    total = len(rows)
    now_ms = int(time.time() * 1000)
    labels = {x.strip() for x in label.split(",") if x.strip()} or {"consistent", "asymmetric"}
    coin_set = {c.strip().upper() for c in coins.split(",") if c.strip()}
    out = []
    for o in rows:
        if venue in ("hl", "perpl") and o["venue"] != venue:
            continue
        if o["label"] not in labels:
            continue
        if minWinRate and ((o["win_rate_90d"] or 0) * 100 < minWinRate):
            continue
        if minPf and ((o["profit_factor_90d"] or 0) < minPf):
            continue
        if minTrades and o["trades_90d"] < minTrades:
            continue
        if minProfit and ((o["net_pnl_90d"] or 0) < minProfit):
            continue
        if maxDd and ((o["max_drawdown_90d"] or 0) > maxDd):
            continue
        if size in SIZE_BANDS:
            lo, hi = SIZE_BANDS[size]
            av = o["account_value"]
            if av is None or not (lo <= av < hi):
                continue
        if hold in HOLD_BANDS:
            lo, hi = HOLD_BANDS[hold]
            mh = o["median_hold_sec"]
            if mh is None or not (lo <= mh < hi):
                continue
        if coin_set and not (coin_set & set(o["top_coins"])):
            continue
        if perplOnly and (o["copiable_pct"] or 0) < 99.5:
            continue
        if active in ACTIVE_MS:
            if not o["last_trade_ms"] or now_ms - o["last_trade_ms"] > ACTIVE_MS[active]:
                continue
        if maxLev and (o["median_leverage"] or 0) > maxLev:
            continue
        if hideSmall and (o["net_pnl_90d"] or 0) < 500:
            continue
        out.append(o)
    key = sort if sort in SORTABLE else "score"
    sign = 1 if dir == "asc" else -1

    def sv(o):
        v = o.get(key)
        return (v is None, (v or 0) * sign)
    out.sort(key=sv)
    for i, o in enumerate(out, 1):
        o = o
    rows_pub = [{**_row_public(o), "rank": i} for i, o in enumerate(out, 1)]
    as_of = max((o["as_of"] for o in rows), default=None)
    return {"rows": rows_pub, "count": len(rows_pub), "total": total, "as_of": as_of,
            "metrics_as_of": (f"{as_of} 02:00 UTC" if as_of else None), "built_at": data["built_at"],
            "server_time": datetime.utcnow().isoformat(timespec="seconds")}


async def _trips_for(venue: str, wallet: str) -> list[dict]:
    return await _trips_cache.get(f"{venue}:{wallet}", lambda: jobs.load_trips(venue, wallet, int(time.time() * 1000) - 100 * rt.DAY_MS))


def _trip_public(t: dict) -> dict:
    return {k: v for k, v in t.items() if k != "fills"} | {
        "open_time": rt.ms_to_dt(t["open_time"]).isoformat(timespec="milliseconds"),
        "close_time": rt.ms_to_dt(t["close_time"]).isoformat(timespec="milliseconds")}


async def _user_defaults(user: User) -> dict | None:
    try:
        from app.services.copy_auto import user_settings
        s = await user_settings.get(user.id)
        return s.get("defaults") if s else None
    except Exception:
        return None


@router.get("/wallets/{venue}/{wallet}")
async def wallet_detail(venue: str, wallet: str, user: User = Depends(require_featured),
                        page: int = Query(1, ge=1), size: int = Query(50, ge=1, le=200)) -> dict:
    wallet = wallet.lower()
    data = await _rows_cache.get("all", _latest_rows)
    row = next((o for o in data["rows"] if o["venue"] == venue and o["wallet"] == wallet), None)
    if row is None:
        raise HTTPException(status_code=404, detail="Wallet is not featured")
    trips = await _trips_for(venue, wallet)
    now_ms = int(time.time() * 1000)
    t90 = [t for t in trips if t["close_time"] > now_ms - rt.WINDOW_90_MS]
    cum, curve = 0.0, []
    for t in t90:
        cum += t["pnl_usd"]
        curve.append({"t": t["close_time"], "v": round(cum, 2), "win": t["pnl_usd"] > 0, "pnl": round(t["pnl_usd"], 2), "id": t["id"]})
    defaults = rt.suggested_settings(row, await _user_defaults(user))
    rep = rt.replay(t90, margin_usd=defaults["margin_usd"], leverage=defaults["leverage"], stop_pct=defaults["stop_pct"],
                    perpl_coins=await jobs.perpl_coins(), now_ms=now_ms)
    newest = list(reversed(t90))
    start = (page - 1) * size
    ident = await jobs._identity_row(wallet) if venue == "hl" else None
    portfolio = (ident or {}).get("portfolio") or {}
    one_big = [t for t in trips if row["avg_equity_90d"] and t["pnl_usd"] < -0.10 * row["avg_equity_90d"]]
    return {
        **row, "equity_curve": curve, "equity_points": portfolio.get("points") or [],
        "replay": rep, "replay_defaults": defaults,
        "closed_trades": [_trip_public(t) for t in newest[start:start + size]],
        "closed_total": len(t90), "page": page, "size": size,
        "big_losses_year": [_trip_public(t) for t in one_big[-5:]],
        "identity": ({"role": ident.get("role"), "master": ident.get("master"), "sub_accounts": ident.get("sub_accounts"),
                      "checked_at": _iso(ident.get("checked_at"))} if ident else None),
        "server_time": datetime.utcnow().isoformat(timespec="seconds"),
    }


@router.get("/wallets/{venue}/{wallet}/replay")
async def wallet_replay(venue: str, wallet: str, user: User = Depends(require_featured),
                        margin: float = Query(50, gt=0, le=1_000_000), lev: float = Query(3, ge=1, le=50),
                        stopPct: float | None = Query(None, ge=0, le=100)) -> dict:
    wallet = wallet.lower()
    t0 = time.monotonic()
    trips = await _trips_for(venue, wallet)
    rep = rt.replay(trips, margin_usd=margin, leverage=lev, stop_pct=(stopPct or None), perpl_coins=await jobs.perpl_coins())
    rep["ms"] = round((time.monotonic() - t0) * 1000, 1)
    rep["mae_from_candles"] = sum(1 for t in trips if t.get("mae_src") == "candles")
    return rep


@router.get("/live")
async def live_rows(wallets: str = Query(""), user: User = Depends(require_featured)) -> dict:
    ws = [w.strip().lower() for w in wallets.split(",") if w.strip()][:200]
    if not ws:
        return {"rows": {}, "server_time": datetime.utcnow().isoformat(timespec="seconds")}
    sf = get_session_factory()
    async with sf() as s:
        q = text("SELECT venue, wallet, last_trade_at, open_count, unrealized_usd, account_value, positions_at, updated_at "
                 "FROM featured_live WHERE wallet IN :w").bindparams(bindparam("w", expanding=True))
        rows = (await s.execute(q, {"w": ws})).all()
    return {"rows": {f"{r[0]}:{r[1]}": {"last_trade_at": _iso(r[2]), "open_count": int(r[3] or 0), "unrealized_usd": _fl(r[4]),
                                        "account_value": _fl(r[5]), "positions_at": _iso(r[6]), "updated_at": _iso(r[7])} for r in rows},
            "server_time": datetime.utcnow().isoformat(timespec="seconds")}


# ---------------------------------------------------------------------------
# Tab 2: trades
# ---------------------------------------------------------------------------

async def _watched(user: User) -> set[str]:
    sf = get_session_factory()
    async with sf() as s:
        rows = (await s.execute(text(
            "SELECT LOWER(trader_wallet) FROM watchlists WHERE LOWER(follower_wallet)=:w"), {"w": user.wallet_address.lower()})).scalars().all()
    return set(rows)


@router.get("/trades")
async def list_trades(
    user: User = Depends(require_featured), venue: str = Query("all"), coin: str = Query(""), side: str = Query(""),
    result: str = Query("all"), minPnl: float = Query(0, ge=0), minPnlPct: float = Query(0, ge=0), hold: str = Query(""),
    within: str = Query("24h"), label: str = Query(""), watched: int = Query(0), cursor: int | None = Query(None),
    limit: int = Query(50, ge=1, le=200),
) -> dict:
    q = await live.qualified_wallets()
    keys = [k for k in q if not q[k]["inactive"]]
    if venue in ("hl", "perpl"):
        keys = [k for k in keys if k.startswith(venue + ":")]
    if label in ("consistent", "asymmetric"):
        keys = [k for k in keys if q[k]["label"] == label]
    if watched:
        wl = await _watched(user)
        keys = [k for k in keys if k.split(":", 1)[1] in wl]
    if not keys:
        return {"rows": [], "next_cursor": None, "count_window": 0, "wins_window": 0, "within": within}
    wallets = [k.split(":", 1)[1] for k in keys]
    where = ["r.wallet IN :w", "r.close_time >= :since"]
    params: dict = {"w": wallets, "since": rt.ms_to_dt(int(time.time() * 1000) - WITHIN_MS.get(within, WITHIN_MS["24h"])), "n": limit + 1}
    if venue in ("hl", "perpl"):
        where.append("r.venue=:v")
        params["v"] = venue
    coins = [c.strip().upper() for c in coin.split(",") if c.strip()]
    if coins:
        where.append("r.coin IN :coins")
        params["coins"] = coins
    if side in ("long", "short"):
        where.append("r.side=:side")
        params["side"] = side
    if result == "wins":
        where.append("r.pnl_usd > 0")
    elif result == "losses":
        where.append("r.pnl_usd < 0")
    if minPnl:
        where.append("ABS(r.pnl_usd) >= :mp")
        params["mp"] = minPnl
    if minPnlPct:
        where.append("ABS(COALESCE(r.pnl_pct_margin, r.ret_pct)) >= :mpp")
        params["mpp"] = minPnlPct
    if hold in HOLD_BANDS:
        lo, hi = HOLD_BANDS[hold]
        where.append("TIMESTAMPDIFF(SECOND, r.open_time, r.close_time) >= :hlo AND TIMESTAMPDIFF(SECOND, r.open_time, r.close_time) < :hhi")
        params["hlo"], params["hhi"] = lo, hi
    if cursor:
        where.append("r.id < :cur")
        params["cur"] = cursor
    sql = text(
        "SELECT r.id, r.venue, r.wallet, r.coin, r.side, r.open_time, r.close_time, r.n_fills, r.n_adds, r.n_closes, r.max_size_usd, "
        "r.avg_entry, r.avg_exit, r.leverage, r.pnl_usd, r.pnl_pct_margin, r.ret_pct, r.fees, r.funding, r.entry_taker, r.averaged_down, "
        "r.added_to_winner, r.liquidation, r.entry_vs_24h_range, r.smi_at_entry FROM round_trips r WHERE " + " AND ".join(where)
        + " ORDER BY r.close_time DESC, r.id DESC LIMIT :n").bindparams(bindparam("w", expanding=True))
    if coins:
        sql = sql.bindparams(bindparam("coins", expanding=True))
    cnt_sql = text("SELECT COUNT(*), SUM(r.pnl_usd > 0) FROM round_trips r WHERE " + " AND ".join(w for w in where if not w.startswith("r.id <"))
                   ).bindparams(bindparam("w", expanding=True))
    if coins:
        cnt_sql = cnt_sql.bindparams(bindparam("coins", expanding=True))
    sf = get_session_factory()
    async with sf() as s:
        rows = (await s.execute(sql, params)).all()
        cnt = (await s.execute(cnt_sql, {k: v for k, v in params.items() if k not in ("cur", "n")})).first()
    perpl = await jobs.perpl_coins()
    out = []
    for r in rows[:limit]:
        ot, ct = r[5], r[6]
        d = {"id": r[0], "venue": r[1], "wallet": r[2], "coin": r[3], "side": r[4], "open_time": ot.isoformat(timespec="milliseconds"),
             "close_time": ct.isoformat(timespec="milliseconds"), "hold_sec": int((ct - ot).total_seconds()), "n_fills": r[7], "n_adds": r[8],
             "n_closes": r[9], "max_size_usd": _fl(r[10]), "avg_entry": _fl(r[11]), "avg_exit": _fl(r[12]), "leverage": _fl(r[13]),
             "pnl_usd": _fl(r[14]), "pnl_pct_margin": _fl(r[15]), "ret_pct": _fl(r[16]), "fees": _fl(r[17]), "funding": _fl(r[18]),
             "entry_taker": (None if r[19] is None else bool(r[19])), "averaged_down": bool(r[20]), "added_to_winner": bool(r[21]),
             "liquidation": bool(r[22]), "entry_vs_24h_range": _fl(r[23]), "smi_at_entry": _fl(r[24])}
        out.append(live.trade_row(d, q.get(f"{r[1]}:{r[2]}")) | {"on_perpl": r[3].upper() in perpl})
    return {"rows": out, "next_cursor": (rows[limit - 1][0] if len(rows) > limit else None),
            "count_window": int(cnt[0] or 0) if cnt else 0, "wins_window": int(cnt[1] or 0) if cnt and cnt[1] is not None else 0,
            "within": within, "server_time": datetime.utcnow().isoformat(timespec="seconds")}


async def _hl_candles(coin: str, start_ms: int, end_ms: int) -> list[dict]:
    from app.services.hyperliquid import client as hl_client
    name, sec = jobs._interval_for(max(60, (end_ms - start_ms) // 1000))
    pad = sec * 1000 * 20
    try:
        resp = await hl_client.post_info({"type": "candleSnapshot", "req": {"coin": coin, "interval": name, "startTime": start_ms - pad,
                                                                            "endTime": end_ms + pad}}, priority=hl_client.EXPLORER, timeout=15.0)
    except hl_client.HLWeightShed:
        return []
    if resp.status_code != 200:
        return []
    body = await hl_client.decode_json(resp)
    return [{"t": int(c["t"]), "o": float(c["o"]), "h": float(c["h"]), "l": float(c["l"]), "c": float(c["c"])} for c in body or []]


async def _perpl_candles(coin: str, start_ms: int, end_ms: int) -> list[dict]:
    import httpx
    from app.config import settings
    from app.services import market_registry
    m = await market_registry.get_market_by_symbol(coin)
    if not m:
        return []
    name, sec = jobs._interval_for(max(60, (end_ms - start_ms) // 1000))
    res = {"1m": "1m", "5m": "5m", "15m": "15m", "1h": "1h", "4h": "4h", "1d": "1d"}[name]
    pad = sec * 1000 * 20
    url = f"{settings.PERPL_REST_URL}/api/v1/market-data/{m['market_id']}/candles/{res}/{start_ms - pad}-{end_ms + pad}"
    try:
        async with httpx.AsyncClient(timeout=10.0) as c:
            r = await c.get(url)
            if r.status_code != 200:
                return []
            body = r.json()
    except Exception:
        return []
    out = []
    for c in body if isinstance(body, list) else (body.get("candles") or []):
        try:
            out.append({"t": int(c.get("t") or c.get("time")), "o": float(c["o"]), "h": float(c["h"]), "l": float(c["l"]), "c": float(c["c"])})
        except (KeyError, TypeError, ValueError):
            continue
    return out


@router.get("/trades/{trade_id}")
async def trade_detail(trade_id: int, user: User = Depends(require_featured)) -> dict:
    sf = get_session_factory()
    async with sf() as s:
        r = (await s.execute(text(
            "SELECT id, venue, wallet, coin, side, open_time, close_time, n_fills, n_adds, n_closes, max_size_usd, avg_entry, avg_exit, leverage, "
            "pnl_usd, pnl_pct_margin, ret_pct, fees, funding, entry_taker, averaged_down, added_to_winner, liquidation, accounts, margin_usd, "
            "pnl_gross, mae_pct, mfe_pct, mae_src FROM round_trips WHERE id=:i"), {"i": trade_id})).first()
        if not r:
            raise HTTPException(status_code=404, detail="Trade not found")
        q = await live.qualified_wallets()
        if f"{r[1]}:{r[2]}" not in q and not whitelist.is_admin(user.wallet_address):
            raise HTTPException(status_code=404, detail="Trade not found")
        accounts = json.loads(r[23]) if r[23] else [r[2]]
        if r[2] not in accounts:
            accounts.append(r[2])
        fills, funding = [], []
        if r[1] == "hl":
            fq = text("SELECT time, side, px, sz, fee, crossed, closed_pnl, dir, start_position, wallet FROM hl_fills WHERE wallet IN :a AND coin=:c "
                      "AND time BETWEEN :o AND :x ORDER BY time, tid").bindparams(bindparam("a", expanding=True))
            fills = [{"time": f[0].isoformat(timespec="milliseconds"), "side": f[1], "px": _fl(f[2]), "sz": _fl(f[3]), "fee": _fl(f[4]),
                      "crossed": (None if f[5] is None else bool(f[5])), "closed_pnl": _fl(f[6]), "dir": f[7], "start_position": _fl(f[8]),
                      "account": f[9]} for f in (await s.execute(fq, {"a": accounts, "c": r[3], "o": r[5], "x": r[6]})).all()]
            uq = text("SELECT time, usdc, rate FROM hl_funding WHERE wallet IN :a AND coin=:c AND time BETWEEN :o AND :x ORDER BY time"
                      ).bindparams(bindparam("a", expanding=True))
            funding = [{"time": f[0].isoformat(timespec="seconds"), "usdc": _fl(f[1]), "rate": _fl(f[2])}
                       for f in (await s.execute(uq, {"a": accounts, "c": r[3], "o": r[5], "x": r[6]})).all()]
        others = (await s.execute(text(
            "SELECT coin, side, open_time, close_time, pnl_usd FROM round_trips WHERE venue=:v AND wallet=:w AND id<>:i "
            "AND open_time <= :x AND close_time >= :o ORDER BY open_time LIMIT 20"), {"v": r[1], "w": r[2], "i": trade_id, "o": r[5], "x": r[6]})).all()
    o_ms, c_ms = rt.dt_to_ms(r[5]), rt.dt_to_ms(r[6])
    key = f"{r[1]}:{r[3]}:{o_ms // 60000}:{c_ms // 60000}"
    candles = await _candles_cache.get(key, (lambda: _hl_candles(r[3], o_ms, c_ms)) if r[1] == "hl" else (lambda: _perpl_candles(r[3], o_ms, c_ms)))
    perpl = await jobs.perpl_coins()
    return {
        "id": r[0], "venue": r[1], "wallet": r[2], "coin": r[3], "side": r[4], "open_time": r[5].isoformat(timespec="milliseconds"),
        "close_time": r[6].isoformat(timespec="milliseconds"), "hold_sec": int((r[6] - r[5]).total_seconds()), "n_fills": r[7], "n_adds": r[8],
        "n_closes": r[9], "max_size_usd": _fl(r[10]), "avg_entry": _fl(r[11]), "avg_exit": _fl(r[12]), "leverage": _fl(r[13]),
        "pnl_usd": _fl(r[14]), "pnl_pct_margin": _fl(r[15]), "ret_pct": _fl(r[16]), "fees": _fl(r[17]), "funding": _fl(r[18]),
        "entry_taker": (None if r[19] is None else bool(r[19])), "averaged_down": bool(r[20]), "added_to_winner": bool(r[21]),
        "liquidation": bool(r[22]), "margin_usd": _fl(r[24]), "pnl_gross": _fl(r[25]), "mae_pct": _fl(r[26]), "mfe_pct": _fl(r[27]), "mae_src": r[28],
        "label": q.get(f"{r[1]}:{r[2]}", {}).get("label"), "display_name": q.get(f"{r[1]}:{r[2]}", {}).get("display_name"),
        "on_perpl": r[3].upper() in perpl, "fills": fills, "funding_rows": funding, "candles": candles,
        "other_positions": [{"coin": o[0], "side": o[1], "open_time": o[2].isoformat(timespec="seconds"),
                             "close_time": o[3].isoformat(timespec="seconds"), "pnl_usd": _fl(o[4])} for o in others],
        "server_time": datetime.utcnow().isoformat(timespec="seconds"),
    }


async def _smi_latest() -> dict[str, float]:
    sf = get_session_factory()
    async with sf() as s:
        rows = (await s.execute(text(
            "SELECT a.asset, a.smi FROM analytics_smi a JOIN (SELECT asset, MAX(cycle_ts) mx FROM analytics_smi "
            "WHERE cycle_ts >= UTC_TIMESTAMP() - INTERVAL 1 DAY GROUP BY asset) l ON l.asset=a.asset AND l.mx=a.cycle_ts"))).all()
    return {str(a).upper(): float(v) for a, v in rows}


@router.get("/open-now")
async def open_now(user: User = Depends(require_featured)) -> dict:
    q = await live.qualified_wallets()
    keys = [k for k in q if not q[k]["inactive"]]
    wallets = [k.split(":", 1)[1] for k in keys]
    if not wallets:
        return {"coins": [], "wallets": 0, "positions": 0}
    sf = get_session_factory()
    async with sf() as s:
        rq = text("SELECT venue, wallet, open_positions, positions_at FROM featured_live WHERE wallet IN :w AND open_count > 0"
                  ).bindparams(bindparam("w", expanding=True))
        rows = (await s.execute(rq, {"w": wallets})).all()
    smi = await _smi_cache.get("latest", _smi_latest)
    perpl = await jobs.perpl_coins()
    by: dict[str, dict] = {}
    n_pos = 0
    newest = None
    for venue, wallet, pos_json, at in rows:
        if f"{venue}:{wallet}" not in q:
            continue
        for p in (json.loads(pos_json) if pos_json else []):
            coin = str(p.get("coin") or "").upper()
            if not coin:
                continue
            c = by.setdefault(coin, {"coin": coin, "long": 0, "short": 0, "size_usd": 0.0, "entry_long": [0.0, 0.0], "entry_short": [0.0, 0.0],
                                     "wallets": [], "on_perpl": coin in perpl, "smi": smi.get(coin)})
            side = p.get("side")
            sz = float(p.get("size_usd") or 0)
            c["long" if side == "long" else "short"] += 1
            c["size_usd"] += sz
            e = c["entry_long" if side == "long" else "entry_short"]
            e[0] += float(p.get("entry") or 0) * sz
            e[1] += sz
            c["wallets"].append({"venue": venue, "wallet": wallet, "side": side, "size_usd": sz, "entry": p.get("entry"),
                                 "leverage": p.get("leverage"), "unrealized": p.get("unrealized"), "label": q[f"{venue}:{wallet}"]["label"]})
            n_pos += 1
        if at and (newest is None or at > newest):
            newest = at
    coins = []
    for c in by.values():
        c["avg_entry_long"] = (c["entry_long"][0] / c["entry_long"][1]) if c["entry_long"][1] else None
        c["avg_entry_short"] = (c["entry_short"][0] / c["entry_short"][1]) if c["entry_short"][1] else None
        c.pop("entry_long")
        c.pop("entry_short")
        c["size_usd"] = round(c["size_usd"], 2)
        coins.append(c)
    coins.sort(key=lambda c: -c["size_usd"])
    return {"coins": coins, "wallets": len(keys), "positions": n_pos, "as_of": _iso(newest),
            "server_time": datetime.utcnow().isoformat(timespec="seconds")}


@router.get("/preview")
async def preview() -> dict:
    """Static sample for the faded page behind the whitelist gate. Never real data."""
    return PREVIEW


# ---------------------------------------------------------------------------
# whitelist
# ---------------------------------------------------------------------------

class WhitelistRequest(BaseModel):
    email: str
    xHandle: str
    wallet: str
    hp: str | None = None


@router.post("/whitelist/request")
async def whitelist_request(body: WhitelistRequest, request: Request) -> dict:
    ip = request.headers.get("x-real-ip") or (request.client.host if request.client else "?")
    try:
        return await whitelist.create_request(email=body.email, x_handle=body.xHandle, wallet=body.wallet, hp=body.hp, ip=ip)
    except whitelist.RequestError as exc:
        raise HTTPException(status_code=exc.status, detail=exc.detail)


@router.get("/whitelist/me")
async def whitelist_me(user: User | None = Depends(get_optional_user)) -> dict:
    if user is None:
        return {"whitelisted": False, "signed_in": False, "admin": False}
    return {"whitelisted": await whitelist.is_allowed(user.wallet_address), "signed_in": True,
            "admin": whitelist.is_admin(user.wallet_address), "wallet": user.wallet_address}


class Note(BaseModel):
    note: str | None = None


class WalletBody(BaseModel):
    wallet: str


@admin_router.get("/whitelist")
async def admin_list(status: str | None = Query(None), q: str | None = Query(None), user: User = Depends(require_admin)) -> dict:
    sf = get_session_factory()
    async with sf() as s:
        wl = (await s.execute(text("SELECT wallet, approved_at, approved_by, source FROM featured_whitelist ORDER BY approved_at DESC"))).all()
    return {"requests": await whitelist.list_requests(status, q),
            "whitelist": [{"wallet": w, "approved_at": _iso(a), "approved_by": b, "source": src} for w, a, b, src in wl],
            "admin_chat_configured": bool((__import__("app.config", fromlist=["settings"]).settings.ADMIN_TELEGRAM_CHAT_ID or "").strip())}


@admin_router.post("/whitelist/{rid}/approve")
async def admin_approve(rid: int, body: Note | None = None, user: User = Depends(require_admin)) -> dict:
    try:
        return await whitelist.decide(rid, True, by=user.wallet_address, note=(body.note if body else None))
    except whitelist.RequestError as exc:
        raise HTTPException(status_code=exc.status, detail=exc.detail)


@admin_router.post("/whitelist/{rid}/reject")
async def admin_reject(rid: int, body: Note | None = None, user: User = Depends(require_admin)) -> dict:
    try:
        return await whitelist.decide(rid, False, by=user.wallet_address, note=(body.note if body else None))
    except whitelist.RequestError as exc:
        raise HTTPException(status_code=exc.status, detail=exc.detail)


@admin_router.post("/whitelist/add")
async def admin_add(body: WalletBody, user: User = Depends(require_admin)) -> dict:
    try:
        return await whitelist.add_wallet(body.wallet, by=user.wallet_address)
    except whitelist.RequestError as exc:
        raise HTTPException(status_code=exc.status, detail=exc.detail)


@admin_router.delete("/whitelist/{wallet}")
async def admin_remove(wallet: str, user: User = Depends(require_admin)) -> dict:
    return await whitelist.remove_wallet(wallet)


@admin_router.post("/run")
async def admin_run(user: User = Depends(require_admin), maxWallets: int | None = Query(None)) -> dict:
    """Start the daily pipeline now, in this process (shares the Hyperliquid
    weight window). Returns at once; progress in featured_job_runs."""
    import asyncio
    if jobs.status()["running"]:
        return {"status": "already running"}
    asyncio.get_event_loop().create_task(jobs.run_daily(max_wallets=maxWallets))
    return {"status": "started"}


@admin_router.post("/run-perpl")
async def admin_run_perpl(user: User = Depends(require_admin)) -> dict:
    from app.services.featured import perpl as fperpl
    return await fperpl.run_perpl(await jobs.perpl_coins())


@admin_router.get("/status")
async def admin_status(user: User = Depends(require_admin)) -> dict:
    sf = get_session_factory()
    async with sf() as s:
        runs = (await s.execute(text(
            "SELECT id, job, started_at, finished_at, ok, requests, weight, notes FROM featured_job_runs ORDER BY id DESC LIMIT 20"))).all()
        today = (await s.execute(text("SELECT COALESCE(SUM(weight),0), COUNT(*) FROM featured_job_runs WHERE started_at >= UTC_DATE()"))).first()
        counts = (await s.execute(text(
            "SELECT venue, SUM(qualified), COUNT(*) FROM featured_wallets WHERE as_of=(SELECT MAX(as_of) FROM featured_wallets) GROUP BY venue"))).all()
    return {"job": jobs.status(),
            "runs": [{"id": r[0], "job": r[1], "started_at": _iso(r[2]), "finished_at": _iso(r[3]), "ok": r[4], "requests": r[5],
                      "weight": r[6], "notes": r[7]} for r in runs],
            "weight_today": int(today[0] or 0), "runs_today": int(today[1] or 0),
            "latest": [{"venue": v, "qualified": int(q or 0), "rows": int(n or 0)} for v, q, n in counts]}
