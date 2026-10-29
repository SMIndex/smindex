"""Featured traders daily pipeline, Hyperliquid side (BUILD_BRIEF section 2).

    candidates -> fills (+funding) -> round trips -> metrics (lite)
               -> for wallets that pass every rule that needs no venue read:
                  portfolio (equity), userRole (identity), subAccounts (fold)
               -> round trips + metrics again -> featured_wallets row
               -> dropped-wallet Telegram notes -> candle excursions

Budget: every Hyperliquid read goes through the shared client at BACKGROUND
priority (under the existing background cap) and this module paces itself to
about FEATURED_WPM weight per minute (5 % of the venue's 1,200), doubles its
pause after a 429 or a shed, and stops for the day at DAILY_WEIGHT_CAP. The
weight of every run is written to featured_job_runs.

Identity (60 weight) and portfolio (20) are read only for wallets that already
pass the sample, label, market-maker, farmer, bot, wash, hedger and
liquidation rules on their fills, so the day's budget goes to fills, where
the information is. Perpl wallets: services/featured/perpl.py.
"""
from __future__ import annotations

import asyncio
import json
import math
import os
import statistics
import tempfile
import time
from datetime import datetime, timedelta, timezone

from sqlalchemy import bindparam, text

from app.config import settings
from app.db.database import get_session_factory
from app.services.featured import roundtrips as rt
from app.services.hyperliquid import client as hl_client
from app.utils.logger import get_logger

logger = get_logger(__name__)

RUN_AT_UTC = (2, 0)                      # 02:00 UTC, after the hourly leaderboard ingest
FEATURED_WPM = 60.0                      # 5 % of 1,200
DAILY_WEIGHT_CAP = int(os.environ.get("FEATURED_DAILY_WEIGHT_CAP", "28000"))
CANDLE_WEIGHT_CAP = 3000                 # of the daily cap, for trip excursions
FILE_CANDIDATE_CAP = 2000
FIRST_WINDOW_DAYS = 90
PAGES_MAX = 5                            # 5 x 2,000 = the venue's 10,000-fill window
FILLS_RETENTION_DAYS = 180
IDENTITY_TTL_DAYS = 30
LEADERBOARD_URL = "https://stats-data.hyperliquid.xyz/Mainnet/leaderboard"

_task: asyncio.Task | None = None
_stop: asyncio.Event | None = None
_running = False
_last_run: dict = {}
_pause_mult = 1.0


class Run:
    """Counters for one job run (one featured_job_runs row)."""

    def __init__(self, job: str) -> None:
        self.job = job
        self.started = datetime.utcnow()
        self.requests = 0
        self.weight = 0
        self.notes: list[str] = []
        self.id: int | None = None

    async def open(self) -> None:
        sf = get_session_factory()
        async with sf() as s:
            r = await s.execute(text(
                "INSERT INTO featured_job_runs (job, started_at) VALUES (:j, :t)"), {"j": self.job, "t": self.started})
            self.id = r.lastrowid
            await s.commit()

    async def close(self, ok: bool, note: str | None = None) -> None:
        if note:
            self.notes.append(note)
        sf = get_session_factory()
        async with sf() as s:
            await s.execute(text(
                "UPDATE featured_job_runs SET finished_at=:f, ok=:ok, requests=:r, weight=:w, notes=:n WHERE id=:i"),
                {"f": datetime.utcnow(), "ok": 1 if ok else 0, "r": self.requests, "w": self.weight,
                 "n": "; ".join(self.notes)[:255] or None, "i": self.id})
            await s.commit()


async def weight_today() -> int:
    sf = get_session_factory()
    async with sf() as s:
        v = (await s.execute(text(
            "SELECT COALESCE(SUM(weight),0) FROM featured_job_runs WHERE started_at >= UTC_DATE()"))).scalar()
    return int(v or 0)


async def hl_post(run: Run, payload: dict, *, timeout: float = 30.0):
    """One paced Hyperliquid read. Returns the decoded body or None on a 429 /
    shed (after doubling the pause). Raises on other HTTP errors."""
    global _pause_mult
    info_type = str(payload.get("type") or "")
    try:
        resp = await hl_client.post_info(payload, priority=hl_client.BACKGROUND, timeout=timeout)
    except hl_client.HLWeightShed as exc:
        _pause_mult = min(_pause_mult * 2, 16.0)
        run.notes.append(f"shed x{_pause_mult:.0f}")
        logger.warning("featured %s: shed (%s), pause x%.0f", run.job, exc, _pause_mult)
        await asyncio.sleep(30 * _pause_mult)
        return None
    run.requests += 1
    if resp.status_code == 429:
        _pause_mult = min(_pause_mult * 2, 16.0)
        run.weight += hl_client.base_weight(info_type)
        run.notes.append(f"429 x{_pause_mult:.0f}")
        logger.warning("featured %s: 429 on %s, pause x%.0f", run.job, info_type, _pause_mult)
        await asyncio.sleep(60 * _pause_mult)
        return None
    resp.raise_for_status()
    body = await hl_client.decode_json(resp)
    items = len(body) if isinstance(body, list) else 0
    w = hl_client.base_weight(info_type) + hl_client.extra_weight(info_type, items)
    run.weight += w
    _pause_mult = max(1.0, _pause_mult * 0.9)
    # pacing: FEATURED_WPM weight per minute -> sleep weight / WPM minutes
    await asyncio.sleep(60.0 * w / FEATURED_WPM * _pause_mult)
    return body


# ---------------------------------------------------------------------------
# candidates
# ---------------------------------------------------------------------------

async def _stream_leaderboard_file(run: Run) -> dict[str, dict]:
    """Wallets from the leaderboard file (no info-API weight) that pass the
    cheap pre-filter: month PnL at least $500, not (volume over $5M with PnL
    under $1K). Capped at FILE_CANDIDATE_CAP by month PnL."""
    import ijson
    out: dict[str, dict] = {}
    fd, path = tempfile.mkstemp(suffix=".json")
    os.close(fd)
    try:
        async with hl_client.stream("GET", LEADERBOARD_URL, timeout=120.0,
                                    headers={"User-Agent": "Mozilla/5.0"}) as resp:
            if resp.status_code != 200:
                run.notes.append(f"leaderboard file {resp.status_code}")
                return out
            # the file is ~34 MB: write it off the event loop in 4 MB pieces
            # (a plain fh.write loop stalled the loop for 2 s on 2026-10-29)
            buf = bytearray()
            with open(path, "wb") as fh:
                async for chunk in resp.aiter_bytes():
                    buf += chunk
                    if len(buf) >= 4 * 1024 * 1024:
                        await asyncio.to_thread(fh.write, bytes(buf))
                        buf = bytearray()
                if buf:
                    await asyncio.to_thread(fh.write, bytes(buf))

        def parse() -> dict[str, dict]:
            res: dict[str, dict] = {}
            with open(path, "rb") as fh:
                for row in ijson.items(fh, "leaderboardRows.item"):
                    addr = str(row.get("ethAddress") or "").lower()
                    if not addr.startswith("0x"):
                        continue
                    perf = {k: v for k, v in (row.get("windowPerformances") or [])}
                    m = perf.get("month") or {}
                    pnl = float(m.get("pnl") or 0)
                    vlm = float(m.get("vlm") or 0)
                    av = float(row.get("accountValue") or 0)
                    if pnl < 500.0:
                        continue
                    if vlm > 5_000_000 and pnl < 1_000:
                        continue
                    res[addr] = {"month_pnl": pnl, "month_vlm": vlm, "account_value": av,
                                 "display_name": (row.get("displayName") or None)}
            if len(res) > FILE_CANDIDATE_CAP:
                keep = sorted(res.items(), key=lambda kv: -kv[1]["month_pnl"])[:FILE_CANDIDATE_CAP]
                res = dict(keep)
            return res
        out = await asyncio.to_thread(parse)
    except Exception as exc:
        run.notes.append(f"leaderboard file failed: {str(exc)[:60]}")
        logger.warning("featured candidates: leaderboard file failed: %s", exc)
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass
    return out


async def candidates(run: Run) -> list[dict]:
    """Union of the sources, pre-filtered, written to featured_candidates."""
    today = datetime.utcnow().date()
    sf = get_session_factory()
    src: dict[str, set[str]] = {}
    file_rows = await _stream_leaderboard_file(run)
    for w in file_rows:
        src.setdefault(w, set()).add("file")
    async with sf() as s:
        rows = (await s.execute(text(
            "SELECT DISTINCT LOWER(s.wallet_address) FROM leaderboard_snapshots s JOIN ("
            " SELECT period, MAX(timestamp) mt FROM leaderboard_snapshots WHERE exchange='hl' GROUP BY period"
            ") lb ON lb.period = s.period AND lb.mt = s.timestamp WHERE s.exchange='hl'"))).scalars().all()
        for w in rows:
            src.setdefault(w, set()).add("leaderboard")
        rows = (await s.execute(text(
            "SELECT DISTINCT LOWER(wallet) FROM analytics_wallet_state "
            "WHERE cycle_ts >= UTC_TIMESTAMP() - INTERVAL 7 DAY"))).scalars().all()
        for w in rows:
            src.setdefault(w, set()).add("cohort")
        cut = int((time.time() - 7 * 86400) * 1000)
        rows = (await s.execute(text(
            "SELECT LOWER(wallet_address), SUM(px * sz) v FROM hl_fill_events WHERE ts_ms >= :c "
            "GROUP BY wallet_address HAVING v > 50000"), {"c": cut})).all()
        for w, _v in rows:
            src.setdefault(w, set()).add("feed")
        prev_q = (await s.execute(text(
            "SELECT DISTINCT wallet FROM featured_wallets WHERE venue='hl' AND qualified=1 "
            "AND as_of >= :d"), {"d": today - timedelta(days=3)})).scalars().all()
        for w in prev_q:
            src.setdefault(w, set()).add("featured")
        ident = {w: (role, master) for w, role, master in (await s.execute(text(
            "SELECT wallet, role, master FROM wallet_identity"))).all()}
    # cohort flags (in-process, from the analytics sweep)
    try:
        from app.services.analytics import cohort
        mm_flag, hedger_flag = cohort.mm_flag, cohort.hedger_flag
    except Exception:
        mm_flag = hedger_flag = (lambda w: None)
    out: list[dict] = []
    for w, sources in src.items():
        fr = file_rows.get(w, {})
        reason = None
        role = ident.get(w, ("unknown", None))[0]
        if role in ("vault", "agent", "subAccount"):
            reason = f"role {role}"
        elif mm_flag(w) is True:
            reason = "cohort mm flag"
        elif hedger_flag(w) is True:
            reason = "cohort hedger flag"
        elif fr and fr["month_pnl"] < 500.0:
            reason = "30d pnl under 500"
        elif fr and (fr.get("month_vlm") or 0) <= 0:
            reason = "no volume"            # pnl with no trading: a vault or deposit artifact
        elif fr and fr["month_pnl"] / fr["month_vlm"] < 0.0005:
            reason = "pnl per volume under 0.05%"   # the farmer / market-maker rule on the leaderboard numbers
        out.append({"wallet": w, "source": ",".join(sorted(sources))[:64], "prefilter": reason is None,
                    "reason": reason, **{k: fr.get(k) for k in ("month_pnl", "month_vlm", "account_value", "display_name")}})
    async with sf() as s:
        await s.execute(text("DELETE FROM featured_candidates WHERE as_of = :d AND venue='hl'"), {"d": today})
        for i in range(0, len(out), 500):
            await s.execute(text(
                "INSERT INTO featured_candidates (as_of, venue, wallet, source, prefilter, reason, month_pnl, month_vlm, account_value, display_name) "
                "VALUES (:d, 'hl', :w, :s, :p, :r, :mp, :mv, :av, :dn)"),
                [{"d": today, "w": c["wallet"], "s": c["source"], "p": 1 if c["prefilter"] else 0, "r": c["reason"],
                  "mp": c["month_pnl"], "mv": c["month_vlm"], "av": c["account_value"], "dn": c["display_name"]}
                 for c in out[i:i + 500]])
        await s.commit()
    kept = [c for c in out if c["prefilter"]]
    # order: previously featured first, then by month pnl (file), then the rest
    kept.sort(key=lambda c: (0 if "featured" in c["source"] else 1, -(c["month_pnl"] or 0.0)))
    run.notes.append(f"candidates {len(out)}, prefilter {len(kept)}")
    logger.info("featured candidates: %d from %d sources, %d pass the pre-filter",
                len(out), len({s for c in out for s in c['source'].split(',')}), len(kept))
    return kept


# ---------------------------------------------------------------------------
# fills, funding, identity, portfolio
# ---------------------------------------------------------------------------

async def _identity_row(wallet: str) -> dict | None:
    sf = get_session_factory()
    async with sf() as s:
        r = (await s.execute(text(
            "SELECT role, master, checked_at, sub_accounts, subs_checked_at, fills_cursor_ms, funding_cursor_ms, "
            "fills_checked_at, history_from, portfolio, portfolio_at FROM wallet_identity WHERE wallet=:w"),
            {"w": wallet})).first()
    if not r:
        return None
    return {"role": r[0], "master": r[1], "checked_at": r[2],
            "sub_accounts": (json.loads(r[3]) if r[3] else []), "subs_checked_at": r[4],
            "fills_cursor_ms": r[5], "funding_cursor_ms": r[6], "fills_checked_at": r[7],
            "history_from": r[8], "portfolio": (json.loads(r[9]) if r[9] else None), "portfolio_at": r[10]}


async def _identity_set(wallet: str, **cols) -> None:
    sf = get_session_factory()
    sets = ", ".join(f"{k}=:{k}" for k in cols)
    async with sf() as s:
        await s.execute(text(
            f"INSERT INTO wallet_identity (wallet, venue, {', '.join(cols)}) VALUES (:w, 'hl', {', '.join(':' + k for k in cols)}) "
            f"ON DUPLICATE KEY UPDATE {sets}"), {"w": wallet, **cols})
        await s.commit()


async def ingest_fills(run: Run, account: str, ident: dict | None) -> dict:
    """userFillsByTime since the stored cursor (first run: 90 days), up to
    PAGES_MAX pages. Upserts hl_fills. Returns {new, pages, history_from_ms}."""
    now_ms = int(time.time() * 1000)
    cursor = (ident or {}).get("fills_cursor_ms")
    start = (int(cursor) + 1) if cursor else now_ms - FIRST_WINDOW_DAYS * rt.DAY_MS
    first = cursor is None
    new = pages = 0
    earliest = None
    last_time = cursor
    sf = get_session_factory()
    total = 0
    while pages < PAGES_MAX:
        body = await hl_post(run, {"type": "userFillsByTime", "user": account, "startTime": start,
                                   "endTime": now_ms, "aggregateByTime": True})
        if body is None:
            break
        pages += 1
        batch = [f for f in body if f.get("tid") is not None]
        total += len(body)
        if batch:
            rows = []
            for f in batch:
                n = rt.norm_fill(f)
                rows.append({"w": account, "tid": n["tid"], "t": rt.ms_to_dt(n["time"]), "coin": n["coin"], "dir": n["dir"][:24],
                             "side": n["side"][:1] or "B", "px": n["px"], "sz": n["sz"], "pnl": n["closed_pnl"], "fee": n["fee"],
                             "ft": (f.get("feeToken") or None), "cr": (None if n["crossed"] is None else int(n["crossed"])),
                             "sp": n["start_position"], "h": n["hash"], "oid": n["oid"], "liq": 1 if n["liquidation"] else 0})
            async with sf() as s:
                for i in range(0, len(rows), 500):
                    await s.execute(text(
                        "INSERT INTO hl_fills (wallet, tid, time, coin, dir, side, px, sz, closed_pnl, fee, fee_token, crossed, "
                        "start_position, hash, oid, liquidation) VALUES (:w, :tid, :t, :coin, :dir, :side, :px, :sz, :pnl, :fee, :ft, :cr, "
                        ":sp, :h, :oid, :liq) ON DUPLICATE KEY UPDATE closed_pnl=VALUES(closed_pnl), fee=VALUES(fee), "
                        "start_position=VALUES(start_position)"), rows[i:i + 500])
                await s.commit()
            new += len(rows)
            times = [int(f["time"]) for f in batch]
            earliest = min(earliest, min(times)) if earliest is not None else min(times)
            last_time = max(last_time or 0, max(times))
        if len(body) < 2000:
            break
        start = int(body[-1]["time"]) + 1
    history_from_ms = None
    if first and total >= 10_000 and earliest:
        history_from_ms = earliest       # the venue's 10,000-fill window cut the history here
    cols = {"fills_checked_at": datetime.utcnow()}
    if last_time:
        cols["fills_cursor_ms"] = int(last_time)
    elif first:
        cols["fills_cursor_ms"] = now_ms - 60_000     # nothing in 90 days: do not re-read 90 days tomorrow
    if history_from_ms:
        cols["history_from"] = rt.ms_to_dt(history_from_ms)
    await _identity_set(account, **cols)
    return {"new": new, "pages": pages, "history_from_ms": history_from_ms}


async def ingest_funding(run: Run, account: str, ident: dict | None) -> int:
    now_ms = int(time.time() * 1000)
    cursor = (ident or {}).get("funding_cursor_ms")
    start = (int(cursor) + 1) if cursor else now_ms - FIRST_WINDOW_DAYS * rt.DAY_MS
    n = 0
    last = cursor
    sf = get_session_factory()
    for _ in range(PAGES_MAX):
        body = await hl_post(run, {"type": "userFunding", "user": account, "startTime": start, "endTime": now_ms})
        if body is None:
            break
        rows = []
        for e in body:
            d = e.get("delta") or {}
            if d.get("type") not in (None, "funding"):
                continue
            tm = int(e.get("time") or 0)
            if not tm or not d.get("coin"):
                continue
            rows.append({"w": account, "t": rt.ms_to_dt(tm), "coin": str(d["coin"]), "usdc": rt._f(d.get("usdc")),
                         "szi": rt._f(d.get("szi")), "rate": rt._f(d.get("fundingRate")), "h": e.get("hash")})
            last = max(last or 0, tm)
        if rows:
            async with sf() as s:
                for i in range(0, len(rows), 500):
                    await s.execute(text(
                        "INSERT INTO hl_funding (wallet, time, coin, usdc, szi, rate, hash) VALUES (:w, :t, :coin, :usdc, :szi, :rate, :h) "
                        "ON DUPLICATE KEY UPDATE usdc=VALUES(usdc)"), rows[i:i + 500])
                await s.commit()
            n += len(rows)
        if len(body) < 500:
            break
        start = int(body[-1]["time"]) + 1
    await _identity_set(account, funding_cursor_ms=int(last) if last else now_ms - 60_000)
    return n


async def identity(run: Run, wallet: str, ident: dict | None) -> dict:
    """userRole (60 weight), cached IDENTITY_TTL_DAYS; subAccounts (20) for
    users, same cache. Returns the refreshed identity row."""
    now = datetime.utcnow()
    if not ident or not ident.get("checked_at") or now - ident["checked_at"] > timedelta(days=IDENTITY_TTL_DAYS):
        body = await hl_post(run, {"type": "userRole", "user": wallet})
        if body is not None:
            role = str(body.get("role") or "unknown")
            data = body.get("data") or {}
            master = None
            if role == "agent":
                master = str(data.get("user") or "").lower() or None
            elif role == "subAccount":
                master = str(data.get("master") or "").lower() or None
            if role not in ("user", "agent", "vault", "subAccount", "missing"):
                role = "unknown"
            await _identity_set(wallet, role=role, master=master, checked_at=now)
    ident = await _identity_row(wallet) or {}
    if ident.get("role") == "user" and (not ident.get("subs_checked_at")
                                        or now - ident["subs_checked_at"] > timedelta(days=IDENTITY_TTL_DAYS)):
        body = await hl_post(run, {"type": "subAccounts", "user": wallet})
        if body is not None:
            subs = sorted({str(x.get("subAccountUser") or "").lower() for x in (body or []) if x.get("subAccountUser")})
            await _identity_set(wallet, sub_accounts=json.dumps(subs), subs_checked_at=now)
            for sub in subs:
                await _identity_set(sub, role="subAccount", master=wallet, checked_at=now)
    return await _identity_row(wallet) or {}


async def _fills_count(wallet: str) -> int:
    sf = get_session_factory()
    async with sf() as s:
        return int((await s.execute(text(
            "SELECT COUNT(*) FROM hl_fills WHERE wallet=:w AND time >= UTC_TIMESTAMP() - INTERVAL 90 DAY"), {"w": wallet})).scalar() or 0)


async def subaccounts_only(run: Run, wallet: str) -> list[str]:
    """subAccounts (20 weight) without userRole, cached IDENTITY_TTL_DAYS."""
    now = datetime.utcnow()
    body = await hl_post(run, {"type": "subAccounts", "user": wallet})
    if body is None:
        return []
    subs = sorted({str(x.get("subAccountUser") or "").lower() for x in (body or []) if x.get("subAccountUser")})
    await _identity_set(wallet, sub_accounts=json.dumps(subs), subs_checked_at=now)
    for sub in subs:
        await _identity_set(sub, role="subAccount", master=wallet, checked_at=now)
    return subs


async def portfolio(run: Run, wallet: str, ident: dict | None) -> dict | None:
    """{avg_equity_90d, account_value, net_pnl_all, points} from `portfolio`,
    cached for the day."""
    now = datetime.utcnow()
    if ident and ident.get("portfolio") and ident.get("portfolio_at") and now - ident["portfolio_at"] < timedelta(hours=20):
        return ident["portfolio"]
    body = await hl_post(run, {"type": "portfolio", "user": wallet})
    if body is None:
        return (ident or {}).get("portfolio")
    per = {k: v for k, v in body if isinstance(v, dict)} if isinstance(body, list) else {}
    cut = (time.time() - 90 * 86400) * 1000

    def hist(key: str, period: str) -> list[tuple[int, float]]:
        out = []
        for p in (per.get(period) or {}).get(key) or []:
            try:
                out.append((int(p[0]), float(p[1])))
            except (TypeError, ValueError, IndexError):
                continue
        return out
    pts = [p for p in hist("accountValueHistory", "allTime") if p[0] >= cut]
    if len(pts) < 5:
        pts = hist("accountValueHistory", "month") or pts
    vals = [v for _, v in pts if v > 0]
    pnl_all = hist("pnlHistory", "allTime")
    res = {"avg_equity_90d": (statistics.mean(vals) if vals else None),
           "account_value": (pts[-1][1] if pts else None),
           "net_pnl_all": (pnl_all[-1][1] if pnl_all else None),
           "points": [[t, round(v, 2)] for t, v in pts][-120:]}
    await _identity_set(wallet, portfolio=json.dumps(res), portfolio_at=now)
    return res


# ---------------------------------------------------------------------------
# round trips + metrics
# ---------------------------------------------------------------------------

async def perpl_coins() -> set[str]:
    sf = get_session_factory()
    async with sf() as s:
        rows = (await s.execute(text(
            "SELECT native_symbol FROM market_map WHERE exchange='hl' AND perpl_market_id IS NOT NULL"))).scalars().all()
    coins = {str(r).upper() for r in rows}
    try:
        from app.services import market_registry
        for m in await market_registry.get_active_markets():
            coins.add(str(m.get("symbol") or "").upper())
    except Exception:
        pass
    return {c for c in coins if c}


async def _load_fills(accounts: list[str], since_ms: int) -> tuple[list[dict], list[dict]]:
    sf = get_session_factory()
    since = rt.ms_to_dt(since_ms)
    async with sf() as s:
        q = text("SELECT wallet, tid, time, coin, dir, side, px, sz, closed_pnl, fee, crossed, start_position, hash, oid, liquidation "
                 "FROM hl_fills WHERE wallet IN :accts AND time >= :t ORDER BY time, tid").bindparams(bindparam("accts", expanding=True))
        rows = (await s.execute(q, {"accts": accounts, "t": since})).all()
        fq = text("SELECT wallet, time, coin, usdc FROM hl_funding WHERE wallet IN :accts AND time >= :t").bindparams(bindparam("accts", expanding=True))
        frows = (await s.execute(fq, {"accts": accounts, "t": since})).all()
    fills = [{"account": r[0], "tid": int(r[1]), "time": rt.dt_to_ms(r[2]), "coin": r[3], "dir": r[4] or "", "side": r[5],
              "px": float(r[6]), "sz": float(r[7]), "closed_pnl": float(r[8]), "fee": float(r[9]),
              "crossed": (None if r[10] is None else bool(r[10])), "start_position": float(r[11]), "hash": r[12],
              "oid": r[13], "liquidation": bool(r[14])} for r in rows]
    funding = [{"time": rt.dt_to_ms(r[1]), "coin": r[2], "usdc": float(r[3])} for r in frows]
    return fills, funding


async def _leverage_hints(wallet: str, trips: list[dict]) -> None:
    """Leverage per trip from the analytics sweep's position rows that fall
    inside the trip (cohort wallets only); trips without a hint keep NULL."""
    if not trips:
        return
    lo = min(t["open_time"] for t in trips)
    sf = get_session_factory()
    async with sf() as s:
        rows = (await s.execute(text(
            "SELECT asset, UNIX_TIMESTAMP(cycle_ts) * 1000, leverage FROM analytics_positions "
            "WHERE wallet = :w AND cycle_ts >= :t AND leverage IS NOT NULL"), {"w": wallet, "t": rt.ms_to_dt(lo)})).all()
    if not rows:
        for t in trips:
            rt.apply_leverage(t, t.get("leverage"))
        return
    by_asset: dict[str, list[tuple[int, float]]] = {}
    for a, ms, lev in rows:
        by_asset.setdefault(str(a), []).append((int(ms), float(lev)))
    for t in trips:
        hints = [lev for ms, lev in by_asset.get(t["coin"], ()) if t["open_time"] <= ms <= t["close_time"]]
        rt.apply_leverage(t, statistics.median(hints) if hints else t.get("leverage"))


async def write_trips(venue: str, wallet: str, trips: list[dict]) -> None:
    if not trips:
        return
    sf = get_session_factory()
    rows = [{
        "venue": venue, "w": wallet, "coin": t["coin"], "side": t["side"],
        "ot": rt.ms_to_dt(t["open_time"]), "ct": rt.ms_to_dt(t["close_time"]),
        "nf": t["n_fills"], "na": t["n_adds"], "nc": t["n_closes"], "mf": t["maker_fills"],
        "ms": t["max_size"], "msu": round(t["max_size_usd"], 2), "vol": round(t["volume_usd"], 2),
        "ae": t["avg_entry"], "ax": t["avg_exit"], "mg": t.get("margin_usd"), "lev": t.get("leverage"),
        "pg": t["pnl_gross"], "pu": t["pnl_usd"], "ppm": t.get("pnl_pct_margin"), "ret": t["ret_pct"],
        "fees": t["fees"], "fund": t["funding"], "et": (None if t["entry_taker"] is None else int(t["entry_taker"])),
        "ad": int(t["averaged_down"]), "aw": int(t["added_to_winner"]), "liq": int(t["liquidation"]),
        "mae": t.get("mae_pct"), "mfe": t.get("mfe_pct"), "msrc": t.get("mae_src"),
        "acc": json.dumps(t.get("accounts") or []),
    } for t in trips]
    async with sf() as s:
        for i in range(0, len(rows), 300):
            await s.execute(text(
                "INSERT INTO round_trips (venue, wallet, coin, side, open_time, close_time, n_fills, n_adds, n_closes, maker_fills, "
                "max_size, max_size_usd, volume_usd, avg_entry, avg_exit, margin_usd, leverage, pnl_gross, pnl_usd, pnl_pct_margin, ret_pct, "
                "fees, funding, entry_taker, averaged_down, added_to_winner, liquidation, mae_pct, mfe_pct, mae_src, accounts) "
                "VALUES (:venue, :w, :coin, :side, :ot, :ct, :nf, :na, :nc, :mf, :ms, :msu, :vol, :ae, :ax, :mg, :lev, :pg, :pu, :ppm, :ret, "
                ":fees, :fund, :et, :ad, :aw, :liq, :mae, :mfe, :msrc, :acc) "
                "ON DUPLICATE KEY UPDATE close_time=VALUES(close_time), n_fills=VALUES(n_fills), n_adds=VALUES(n_adds), n_closes=VALUES(n_closes), "
                "maker_fills=VALUES(maker_fills), max_size=VALUES(max_size), max_size_usd=VALUES(max_size_usd), volume_usd=VALUES(volume_usd), "
                "avg_entry=VALUES(avg_entry), avg_exit=VALUES(avg_exit), margin_usd=COALESCE(VALUES(margin_usd), margin_usd), "
                "leverage=COALESCE(VALUES(leverage), leverage), pnl_gross=VALUES(pnl_gross), pnl_usd=VALUES(pnl_usd), "
                "pnl_pct_margin=COALESCE(VALUES(pnl_pct_margin), pnl_pct_margin), ret_pct=VALUES(ret_pct), fees=VALUES(fees), funding=VALUES(funding), "
                "entry_taker=VALUES(entry_taker), averaged_down=VALUES(averaged_down), added_to_winner=VALUES(added_to_winner), "
                "liquidation=VALUES(liquidation), mae_pct=IF(mae_src='candles', mae_pct, VALUES(mae_pct)), "
                "mfe_pct=IF(mae_src='candles', mfe_pct, VALUES(mfe_pct)), mae_src=IF(mae_src='candles', mae_src, VALUES(mae_src)), "
                "accounts=VALUES(accounts)"), rows[i:i + 300])
        await s.commit()


async def load_trips(venue: str, wallet: str, since_ms: int | None = None) -> list[dict]:
    """Stored closed trips for a wallet, oldest first (the shape compute_metrics
    and replay read). since_ms = None loads everything stored."""
    sf = get_session_factory()
    async with sf() as s:
        rows = (await s.execute(text(
            "SELECT id, coin, side, open_time, close_time, n_fills, n_adds, n_closes, maker_fills, max_size, max_size_usd, volume_usd, "
            "avg_entry, avg_exit, margin_usd, leverage, pnl_gross, pnl_usd, pnl_pct_margin, ret_pct, fees, funding, entry_taker, "
            "averaged_down, added_to_winner, liquidation, mae_pct, mfe_pct, mae_src, accounts FROM round_trips "
            "WHERE venue=:v AND wallet=:w AND close_time >= :t ORDER BY close_time, open_time"),
            {"v": venue, "w": wallet, "t": rt.ms_to_dt(since_ms or 0)})).all()
    out = []
    for r in rows:
        ot, ct = rt.dt_to_ms(r[3]), rt.dt_to_ms(r[4])
        out.append({"id": r[0], "coin": r[1], "side": r[2], "open_time": ot, "close_time": ct, "n_fills": r[5], "n_adds": r[6],
                    "n_closes": r[7], "maker_fills": r[8], "max_size": float(r[9]), "max_size_usd": float(r[10]),
                    "volume_usd": float(r[11]), "avg_entry": float(r[12]), "avg_exit": float(r[13]),
                    "margin_usd": (float(r[14]) if r[14] is not None else None), "leverage": (float(r[15]) if r[15] is not None else None),
                    "pnl_gross": float(r[16]), "pnl_usd": float(r[17]), "pnl_pct_margin": (float(r[18]) if r[18] is not None else None),
                    "ret_pct": float(r[19]), "fees": float(r[20]), "funding": float(r[21]),
                    "entry_taker": (None if r[22] is None else bool(r[22])), "averaged_down": bool(r[23]),
                    "added_to_winner": bool(r[24]), "liquidation": bool(r[25]),
                    "mae_pct": (float(r[26]) if r[26] is not None else None), "mfe_pct": (float(r[27]) if r[27] is not None else None),
                    "mae_src": r[28], "accounts": (json.loads(r[29]) if r[29] else []), "hold_sec": max(0, (ct - ot) // 1000)})
    return out


async def rebuild_trips(wallet: str, accounts: list[str]) -> dict:
    """Round trips for a wallet (master + sub-accounts) from stored fills, written
    to round_trips. Returns build_trips' result."""
    since = int(time.time() * 1000) - FILLS_RETENTION_DAYS * rt.DAY_MS
    fills, funding = await _load_fills(accounts, since)
    res = await asyncio.to_thread(rt.build_trips, fills, funding)     # thousands of fills: off the loop
    await _leverage_hints(wallet, res["trips"])
    await write_trips("hl", wallet, res["trips"])
    return res


async def _context(wallet: str, accounts: list[str]) -> dict:
    """Hedger flag, wash share, cohort MM flag, newest fill, for compute_metrics."""
    sf = get_session_factory()
    async with sf() as s:
        hedger = (await s.execute(text(
            "SELECT is_likely_hedger FROM analytics_wallet_flags WHERE wallet=:w"), {"w": wallet})).scalar()
        cp = (await s.execute(text(
            "SELECT other, SUM(n) FROM featured_counterparties WHERE wallet=:w AND day >= UTC_DATE() - INTERVAL 90 DAY "
            "GROUP BY other ORDER BY 2 DESC LIMIT 1"), {"w": wallet})).first()
        cp_total = (await s.execute(text(
            "SELECT COALESCE(SUM(n),0) FROM featured_counterparties WHERE wallet=:w AND day >= UTC_DATE() - INTERVAL 90 DAY"),
            {"w": wallet})).scalar()
        q = text("SELECT MAX(time) FROM hl_fills WHERE wallet IN :a").bindparams(bindparam("a", expanding=True))
        last = (await s.execute(q, {"a": accounts})).scalar()
        extra_liq = 0
    wash = (float(cp[1]) / float(cp_total)) if cp and cp_total and float(cp_total) >= 50 else None
    try:
        from app.services.analytics import cohort
        mm = cohort.mm_flag(wallet)
    except Exception:
        mm = None
    try:
        from app.services.hyperliquid import last_trade as lt
        feed_ms, _src = lt.last_trade(wallet)
    except Exception:
        feed_ms = None
    last_ms = max([x for x in ((rt.dt_to_ms(last) if last else None), feed_ms) if x], default=None)
    return {"hedger": (None if hedger is None else bool(hedger)), "wash_share": wash, "mm_flag": mm,
            "last_fill_ms": last_ms, "extra_liquidations": extra_liq}


async def write_wallet_row(venue: str, wallet: str, m: dict, display_name: str | None) -> None:
    today = datetime.utcnow().date()
    sf = get_session_factory()
    cols = {
        "as_of": today, "venue": venue, "wallet": wallet, "label": m["label"], "qualified": int(m["qualified"]),
        "inactive": int(m["inactive"]), "one_big_win": int(m["one_big_win"]), "score": m["score"], "verdict": m["verdict"],
        "style": m["style"], "display_name": display_name, "trades_90d": m["trades_90d"], "days_active_90d": m["days_active_90d"],
        "win_rate_90d": m["win_rate_90d"], "win_rate_30d": m["win_rate_30d"], "net_pnl_90d": m["net_pnl_90d"],
        "net_pnl_30d": m["net_pnl_30d"], "net_pnl_all": m["net_pnl_all"], "profit_factor_90d": m["profit_factor_90d"],
        "profit_factor_30d": m["profit_factor_30d"], "avg_win_usd": m["avg_win_usd"], "avg_loss_usd": m["avg_loss_usd"],
        "avg_win_pct_eq": m["avg_win_pct_eq"], "avg_loss_pct_eq": m["avg_loss_pct_eq"], "avg_win_move_pct": m["avg_win_move_pct"],
        "avg_loss_move_pct": m["avg_loss_move_pct"], "max_drawdown_90d": m["max_drawdown_90d"], "dd_start": m["dd_start"],
        "dd_end": m["dd_end"], "worst_trade_usd": m["worst_trade_usd"], "worst_trade_pct_eq": m["worst_trade_pct_eq"],
        "worst_day_usd": m["worst_day_usd"], "worst_week_usd": m["worst_week_usd"], "largest_trade_share": m["largest_trade_share"],
        "profitable_weeks_13": m["profitable_weeks_13"], "longest_loss_streak": m["longest_loss_streak"], "loss_streak_at": m["loss_streak_at"],
        "median_hold_sec": m["median_hold_sec"], "trades_per_week": m["trades_per_week"], "trades_per_day_max": m["trades_per_day_max"],
        "median_leverage": m["median_leverage"], "max_leverage": m["max_leverage"], "maker_share": m["maker_share"],
        "pnl_per_volume": m["pnl_per_volume"], "long_share": m["long_share"], "avg_equity_90d": m["avg_equity_90d"],
        "account_value": m["account_value"], "volume_90d": m["volume_90d"], "top_coins": json.dumps(m["top_coins"]),
        "coin_stats": json.dumps(m["coin_stats"]), "hours_utc": json.dumps(m["hours_utc"]), "copiable_pct": m["copiable_pct"],
        "liquidations_90d": m["liquidations_90d"], "usual_open": m["usual_open"], "history_from": m["history_from"],
        "flags": json.dumps({**m["flags"], "_extra": {"averages_down_n": m["averages_down_n"], "adds_to_winner_n": m["adds_to_winner_n"],
                                                       "streak_now": m["streak_now"], "confidence": m["confidence"],
                                                       "weekly": m["weekly"], "span_days": m["span_days"]}}),
        "score_inputs": json.dumps(m["score_inputs"]), "last_trade_at": (m["last_trade_at"].replace("T", " ") if m["last_trade_at"] else None),
        "computed_at": datetime.utcnow(),
    }
    for k in ("pnl_per_volume",):
        if cols[k] is not None and abs(cols[k]) >= 1000:
            cols[k] = None
    names = ", ".join(cols)
    vals = ", ".join(f":{k}" for k in cols)
    upd = ", ".join(f"{k}=VALUES({k})" for k in cols if k not in ("as_of", "venue", "wallet"))
    async with sf() as s:
        await s.execute(text(f"INSERT INTO featured_wallets ({names}) VALUES ({vals}) ON DUPLICATE KEY UPDATE {upd}"), cols)
        await s.commit()


async def process_wallet(run: Run, cand: dict, coins: set[str], budget_left: int) -> dict:
    """The whole pipeline for one Hyperliquid wallet. Returns the metrics."""
    wallet = cand["wallet"]
    ident = await _identity_row(wallet)
    accounts = [wallet] + [a for a in (ident or {}).get("sub_accounts", []) if a]
    now_ms = int(time.time() * 1000)
    # 1. fills + funding for every account
    for acct in accounts:
        aid = ident if acct == wallet else await _identity_row(acct)
        await ingest_fills(run, acct, aid)
        await ingest_funding(run, acct, aid)
    ident = await _identity_row(wallet) or {}
    # 1b. a master with no fills of its own but volume on the leaderboard trades
    # through sub-accounts (seen 2026-10-29: $19.8B month volume, 0 master fills):
    # one subAccounts read (20 weight), then their fills join the record
    if not ident.get("subs_checked_at") and len(accounts) == 1 and (cand.get("month_vlm") is None or float(cand.get("month_vlm") or 0) > 0):
        n_master = await _fills_count(wallet)
        if n_master == 0:
            await subaccounts_only(run, wallet)
            ident = await _identity_row(wallet) or {}
            subs = [a for a in ident.get("sub_accounts", []) if a]
            for sub in subs:
                await ingest_fills(run, sub, await _identity_row(sub))
                await ingest_funding(run, sub, await _identity_row(sub))
            accounts = [wallet] + subs
    # 2. trips + lite metrics (no equity, no role)
    built = await rebuild_trips(wallet, accounts)
    trips = built["trips"]
    ctx = await _context(wallet, accounts)
    hist_ms = rt.dt_to_ms(ident["history_from"]) if ident.get("history_from") else None
    pf = ident.get("portfolio") or {}
    m = await asyncio.to_thread(rt.compute_metrics, trips, now_ms=now_ms, equity_avg=pf.get("avg_equity_90d"),
                                account_value=pf.get("account_value"), perpl_coins=coins, identity_role=ident.get("role"),
                                history_from_ms=hist_ms, net_pnl_all=pf.get("net_pnl_all"), **ctx)
    needs_venue = ["sample", "label", "mm", "farmer", "bot", "hedger", "wash", "liquidations"]
    lite_pass = all(m["flags"][k]["pass"] is not False for k in needs_venue) and m["net_pnl_90d"] > rt.RULES["min_profit_usd"]
    # 3. identity + portfolio only for wallets worth the weight
    if lite_pass and run.weight < budget_left:
        ident = await identity(run, wallet, ident)
        if ident.get("role") == "user":
            subs = [a for a in ident.get("sub_accounts", []) if a and a not in accounts]
            for sub in subs:
                await ingest_fills(run, sub, await _identity_row(sub))
                await ingest_funding(run, sub, await _identity_row(sub))
            accounts = [wallet] + [a for a in ident.get("sub_accounts", []) if a]
            pf = await portfolio(run, wallet, ident) or {}
            if subs:
                built = await rebuild_trips(wallet, accounts)
                trips = built["trips"]
            ctx = await _context(wallet, accounts)
        m = await asyncio.to_thread(rt.compute_metrics, trips, now_ms=now_ms, equity_avg=pf.get("avg_equity_90d"),
                                    account_value=pf.get("account_value"), perpl_coins=coins, identity_role=ident.get("role"),
                                    history_from_ms=hist_ms, net_pnl_all=pf.get("net_pnl_all"), **ctx)
        if ident.get("role") in ("agent", "subAccount") and ident.get("master"):
            m["flags"]["role"]["value"] = f"{ident['role']} of {ident['master'][:10]}"
    else:
        # identity never read: say so instead of "user"
        if m["flags"]["role"]["value"] == "unknown":
            m["flags"]["role"]["pass"] = None
    await write_wallet_row("hl", wallet, m, cand.get("display_name"))
    return m


# ---------------------------------------------------------------------------
# dropped wallets -> Telegram note to copiers
# ---------------------------------------------------------------------------

def _dropped_reason(m_flags: dict) -> str:
    names = {"sample": "fewer than 30 trades", "recent": "no trade in 14 days", "net_profit": "net profit",
             "worst_trade": "worst trade", "drawdown": "drawdown", "mm": "market-maker pattern", "farmer": "volume farming",
             "bot": "bot pattern", "hedger": "hedge found", "wash": "wash trading", "liquidations": "liquidated",
             "label": "win rate or profit factor", "role": "account type"}
    for k, v in m_flags.items():
        if isinstance(v, dict) and v.get("pass") is False and k in names:
            val = v.get("value")
            if k == "drawdown" and isinstance(val, (int, float)):
                return f"drawdown {val:.0f}%"
            if k == "worst_trade" and isinstance(val, dict) and val.get("pct_eq") is not None:
                return f"worst trade {abs(val['pct_eq']):.0f}% of equity"
            return names[k]
    return "no longer passes the rules"


async def notify_dropped(run: Run) -> int:
    """Wallets qualified yesterday and not today: one Telegram note to every
    user copying them (manual copy subscriptions and auto-copy subs). The
    copy is not stopped."""
    today = datetime.utcnow().date()
    sf = get_session_factory()
    async with sf() as s:
        prev_day = (await s.execute(text(
            "SELECT MAX(as_of) FROM featured_wallets WHERE as_of < :d"), {"d": today})).scalar()
        if not prev_day:
            return 0
        prev = {(v, w) for v, w in (await s.execute(text(
            "SELECT venue, wallet FROM featured_wallets WHERE as_of=:d AND qualified=1 AND inactive=0"), {"d": prev_day})).all()}
        cur = {(v, w): json.loads(f or "{}") for v, w, f in (await s.execute(text(
            "SELECT venue, wallet, flags FROM featured_wallets WHERE as_of=:d AND (qualified=0 OR inactive=1)"), {"d": today})).all()}
    dropped = [(v, w) for (v, w) in prev if (v, w) in cur]
    n = 0
    from app.services.alerts import gateway
    for venue, wallet in dropped:
        reason = _dropped_reason(cur[(venue, wallet)])
        async with sf() as s:
            users = set((await s.execute(text(
                "SELECT u.id FROM copy_subscriptions cs JOIN users u ON LOWER(u.wallet_address)=LOWER(cs.follower_wallet) "
                "WHERE LOWER(cs.trader_wallet)=:w AND cs.status='active'"), {"w": wallet})).scalars().all())
            users |= set((await s.execute(text(
                "SELECT user_id FROM auto_copy_subs WHERE LOWER(leader_wallet)=:w AND status='active'"), {"w": wallet})).scalars().all())
        for uid in users:
            msg = (f"<b>Featured traders</b>: {wallet[:6]}...{wallet[-4:]} is no longer featured: {reason}.\n"
                   f"Your copy is not stopped. Review it on SMINDEX.")
            await gateway.send_alert(alert_type="featured_dropped", message=msg, user_id=int(uid),
                                     dedupe_key=f"{wallet}:{today}", dedupe_hours=24, force=True)
            n += 1
    if dropped:
        run.notes.append(f"dropped {len(dropped)}, notes {n}")
    return n


# ---------------------------------------------------------------------------
# candle excursions for qualified wallets' trips
# ---------------------------------------------------------------------------

def _interval_for(hold_sec: int) -> tuple[str, int]:
    for name, sec in (("1m", 60), ("5m", 300), ("15m", 900), ("1h", 3600), ("4h", 14400), ("1d", 86400)):
        if hold_sec / sec <= 480:
            return name, sec
    return "1d", 86400


async def candle_excursions(run: Run, cap: int) -> int:
    """mae_pct / mfe_pct from candles for trips of qualified wallets that only
    have the fills-based estimate, newest first, under `cap` weight."""
    sf = get_session_factory()
    today = datetime.utcnow().date()
    async with sf() as s:
        rows = (await s.execute(text(
            "SELECT r.id, r.coin, r.side, r.open_time, r.close_time, r.avg_entry FROM round_trips r JOIN featured_wallets f "
            "ON f.venue=r.venue AND f.wallet=r.wallet AND f.as_of=:d AND f.qualified=1 "
            "WHERE r.venue='hl' AND (r.mae_src IS NULL OR r.mae_src='fills') AND r.close_time >= UTC_TIMESTAMP() - INTERVAL 90 DAY "
            "ORDER BY r.close_time DESC LIMIT 400"), {"d": today})).all()
    done = 0
    start_w = run.weight
    for rid, coin, side, ot, ct, entry in rows:
        if run.weight - start_w >= cap:
            break
        o_ms, c_ms = rt.dt_to_ms(ot), rt.dt_to_ms(ct)
        name, sec = _interval_for(max(60, (c_ms - o_ms) // 1000))
        body = await hl_post(run, {"type": "candleSnapshot", "req": {"coin": coin, "interval": name,
                                                                     "startTime": o_ms - sec * 1000, "endTime": c_ms + sec * 1000}})
        if not body:
            continue
        his = [float(c.get("h") or 0) for c in body if int(c.get("t") or 0) <= c_ms and int(c.get("T") or c.get("t") or 0) >= o_ms]
        los = [float(c.get("l") or 0) for c in body if int(c.get("t") or 0) <= c_ms and int(c.get("T") or c.get("t") or 0) >= o_ms]
        if not his or not los or not entry:
            continue
        e = float(entry)
        if side == "long":
            mae, mfe = (e - min(los)) / e * 100, (max(his) - e) / e * 100
        else:
            mae, mfe = (max(his) - e) / e * 100, (e - min(los)) / e * 100
        async with sf() as s:
            await s.execute(text("UPDATE round_trips SET mae_pct=:a, mfe_pct=:b, mae_src='candles' WHERE id=:i"),
                            {"a": round(max(0.0, mae), 4), "b": round(max(0.0, mfe), 4), "i": rid})
            await s.commit()
        done += 1
    if done:
        run.notes.append(f"candles {done}")
    return done


# ---------------------------------------------------------------------------
# the daily run
# ---------------------------------------------------------------------------

async def run_daily(max_wallets: int | None = None) -> dict:
    """One full pass. Safe to call from the scheduler and from
    scripts/featured_backtest.py. Returns a summary."""
    global _running, _last_run
    if _running:
        return {"skipped": "already running"}
    _running = True
    run = Run("daily")
    await run.open()
    summary = {"started": run.started.isoformat()}
    try:
        used_before = await weight_today()
        budget_left = max(0, DAILY_WEIGHT_CAP - used_before - CANDLE_WEIGHT_CAP)
        cands = await candidates(run)
        if max_wallets:
            cands = cands[:max_wallets]
        coins = await perpl_coins()
        done = qualified = deferred = 0
        for c in cands:
            if run.weight >= budget_left:
                deferred += 1
                continue
            try:
                m = await process_wallet(run, c, coins, budget_left)
                done += 1
                qualified += 1 if (m["qualified"] and not m["inactive"]) else 0
            except Exception as exc:
                logger.warning("featured: wallet %s failed: %s", c["wallet"][:10], exc)
                run.notes.append(f"{c['wallet'][:8]} failed")
            if _stop is not None and _stop.is_set():
                run.notes.append("stopped")
                break
        try:
            notes = await notify_dropped(run)
        except Exception as exc:
            logger.warning("featured: dropped-wallet notes failed: %s", exc)
            notes = 0
        try:
            cand_n = await candle_excursions(run, CANDLE_WEIGHT_CAP)
        except Exception as exc:
            logger.warning("featured: candle excursions failed: %s", exc)
            cand_n = 0
        try:
            await retention()
        except Exception as exc:
            logger.warning("featured: retention failed: %s", exc)
        summary.update({"candidates": len(cands), "processed": done, "qualified": qualified, "deferred": deferred,
                        "weight": run.weight, "requests": run.requests, "dropped_notes": notes, "candles": cand_n})
        await run.close(True, f"processed {done}, qualified {qualified}, deferred {deferred}")
        logger.info("featured daily: %s", summary)
    except Exception as exc:
        logger.exception("featured daily failed")
        await run.close(False, str(exc)[:120])
        summary["error"] = str(exc)[:200]
    finally:
        _running = False
        _last_run = summary
    return summary


async def retention() -> None:
    sf = get_session_factory()
    async with sf() as s:
        await s.execute(text("DELETE FROM hl_fills WHERE time < UTC_TIMESTAMP() - INTERVAL :d DAY"), {"d": FILLS_RETENTION_DAYS})
        await s.execute(text("DELETE FROM hl_funding WHERE time < UTC_TIMESTAMP() - INTERVAL :d DAY"), {"d": FILLS_RETENTION_DAYS})
        await s.execute(text("DELETE FROM featured_candidates WHERE as_of < UTC_DATE() - INTERVAL 30 DAY"))
        await s.execute(text("DELETE FROM featured_counterparties WHERE day < UTC_DATE() - INTERVAL 120 DAY"))
        await s.execute(text("DELETE FROM featured_job_runs WHERE started_at < UTC_TIMESTAMP() - INTERVAL 180 DAY"))
        await s.commit()


async def _loop(stop: asyncio.Event) -> None:
    await asyncio.sleep(120)
    while not stop.is_set():
        now = datetime.now(timezone.utc)
        sf = get_session_factory()
        async with sf() as s:
            ran_today = (await s.execute(text(
                "SELECT COUNT(*) FROM featured_job_runs WHERE job='daily' AND started_at >= UTC_DATE() AND ok IS NOT NULL"))).scalar()
        due = now.replace(hour=RUN_AT_UTC[0], minute=RUN_AT_UTC[1], second=0, microsecond=0)
        if now >= due and not ran_today:
            await run_daily()
        else:
            nxt = due if now < due else due + timedelta(days=1)
            wait = min(3600.0, max(30.0, (nxt - datetime.now(timezone.utc)).total_seconds()))
            try:
                await asyncio.wait_for(stop.wait(), timeout=wait)
            except asyncio.TimeoutError:
                pass
            continue
        try:
            await asyncio.wait_for(stop.wait(), timeout=600)
        except asyncio.TimeoutError:
            pass


def start(stop: asyncio.Event) -> None:
    global _task, _stop
    _stop = stop
    if _task is None or _task.done():
        _task = asyncio.get_event_loop().create_task(_loop(stop))
        logger.info("featured traders daily job started (02:00 UTC, cap %d weight/day)", DAILY_WEIGHT_CAP)


async def stop() -> None:
    if _stop is not None:
        _stop.set()
    if _task and not _task.done():
        _task.cancel()
        try:
            await _task
        except (asyncio.CancelledError, Exception):
            pass


def status() -> dict:
    return {"running": _running, "last_run": _last_run, "pause_mult": _pause_mult}
