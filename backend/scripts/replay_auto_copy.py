"""Overnight Part C5: replay 24h of STORED real-time leader events through the
real auto-copy engine in Shadow, against a throwaway schema.

Real data used: the events (leader_trade_events, hl_ws, with raw HL fills),
Perpl's own 1-minute candles for the price at each event minute (gate 5 +
shadow fills), the follower's real current Perpl account (balance/positions),
each leader's real current leverage/equity (one HL read per leader).
Substitutions (stated in the report): basis = leader fill vs Perpl candle close
at that minute; event age = 0.5 s (live processing is immediate).

Run on the server (throwaway DB in DATABASE_URL):
  venv/bin/python scripts/replay_auto_copy.py <source_db_url> <follower_wallet> <user_id> <hours> <leader> [<leader>...]
"""
import asyncio
import json
import sys
import time
import time as _time_mod
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import httpx  # noqa: E402
from sqlalchemy import create_engine, text  # noqa: E402

from app.config import settings  # noqa: E402
from app.db import database as dbm  # noqa: E402
import app.db.models, app.db.copy_models, app.db.social_models, app.db.strategy_models, app.db.migrations_models  # noqa: E402,F401  (register every table before create_all)
from app.services.copy_auto import engine, rules  # noqa: E402

from app.services.copy_auto.schema import DDL as V28  # noqa: E402

_cur = {"t": 0.0, "px": 0.0}
_candles: dict[int, dict[int, float]] = {}


async def _load_candles(mid: int, t0_ms: int, t1_ms: int) -> None:
    out: dict[int, float] = {}
    async with httpx.AsyncClient(timeout=20) as c:
        a = t0_ms - 120_000
        while a < t1_ms:
            b = min(a + 1000 * 60_000, t1_ms + 120_000)
            r = await c.get(f"{settings.PERPL_REST_URL}/api/v1/market-data/{mid}/candles/60/{a}-{b}")
            for k in (r.json() or {}).get("d", []):
                out[int(k["t"]) // 60_000] = float(k["c"])
            a = b
            await asyncio.sleep(0.7)            # public limit ~100 req/min
    _candles[mid] = out


def _candle_px(mid: int) -> float | None:
    from app.services import market_registry
    m = (market_registry._cache.get("by_id") or {}).get(mid)
    pdp = 10 ** int(m["price_decimals"]) if m else 1
    series = _candles.get(mid) or {}
    minute = int(_cur["t"] // 60)
    for k in (minute, minute - 1, minute + 1):
        if k in series:
            return series[k] / pdp
    return None


class _Clock:
    @staticmethod
    def time():
        return _cur["t"] + 0.5

    monotonic = staticmethod(_time_mod.monotonic)


async def main():
    src_url, follower, uid, hours, leaders = sys.argv[1], sys.argv[2].lower(), int(sys.argv[3]), float(sys.argv[4]), [w.lower() for w in sys.argv[5:]]
    await dbm.init_db(settings.DATABASE_URL)
    async with dbm.engine.begin() as conn:
        for stmt in V28:
            await conn.execute(text(stmt))
        await conn.execute(text("DELETE FROM auto_copy_log"))
        await conn.execute(text("DELETE FROM auto_copy_positions"))
        await conn.execute(text("DELETE FROM auto_copy_subs"))
        for i, l in enumerate(leaders, 1):
            vals = {**rules.DEFAULTS, **rules.PRESETS["standard"]}
            await conn.execute(text(
                "INSERT INTO auto_copy_subs (id, user_id, follower_wallet, leader_wallet, mode, status, sizing, margin_usd, "
                "allocation_usd, max_leverage, max_positions, mirror_adds, mirror_reduces, reopen_on_flip, drift_pct, "
                "sl_margin_pct, daily_loss_usd, total_loss_usd, created_at, updated_at) VALUES (:i, :u, :f, :l, 'shadow', "
                "'active', 'fixed', :m, :a, :lv, :mp, 1, 1, 1, :d, :sl, :dl, :tl, UTC_TIMESTAMP(), UTC_TIMESTAMP())"),
                {"i": i, "u": uid, "f": follower, "l": l, "m": vals["margin_usd"], "a": vals["allocation_usd"],
                 "lv": vals["max_leverage"], "mp": vals["max_positions"], "d": vals["drift_pct"],
                 "sl": vals["sl_margin_pct"], "dl": vals["daily_loss_usd"], "tl": vals["total_loss_usd"]})

    src = create_engine(src_url.replace("mysql+aiomysql://", "mysql+pymysql://"))
    with src.connect() as c:
        rows = c.execute(text(
            "SELECT id, trader_wallet, market_id, symbol, side, event_type, size, price, source, raw "
            "FROM leader_trade_events WHERE exchange='hl' AND source='hl_ws' AND trader_wallet IN :ls "
            "AND detected_at >= UTC_TIMESTAMP() - INTERVAL :h HOUR ORDER BY id").bindparams(
            __import__("sqlalchemy").bindparam("ls", expanding=True)), {"ls": leaders, "h": hours}).mappings().all()
    events = [{**dict(r), "raw": json.loads(r["raw"]) if isinstance(r["raw"], str) else r["raw"]} for r in rows]
    print(f"events: {len(events)} ({Counter(e['trader_wallet'][:10] for e in events)})")

    # real Perpl 1-minute candles for every mapped market that has an open/add/flip
    from app.services import market_registry
    await market_registry.refresh_markets(force=True)
    need = {e["market_id"] for e in events if e["market_id"] and (
        e["event_type"] in ("opened", "increased") or str((e["raw"] or {}).get("fill", {}).get("dir", "")).find(">") > 0
        or e["event_type"] in ("closed", "reduced"))}
    ts = [int((e["raw"] or {}).get("fill", {}).get("time") or 0) for e in events if (e["raw"] or {}).get("fill")]
    for mid in sorted(need):
        await _load_candles(mid, min(ts), max(ts))
    print(f"candles loaded for markets {sorted(need)}: " + ", ".join(f"{m}:{len(_candles[m])}" for m in sorted(need)))

    engine._mark = _candle_px
    engine.time = _Clock
    import app.services.hyperliquid.prices as hp

    async def _basis(mid):
        px = _candle_px(mid)
        return None if not px else (_cur["px"] - px) / px * 1e4
    hp.basis_bps_for_market = _basis
    ls_cache = {}
    real_leader_state = engine._leader_state

    async def _leader_state(leader, coin):
        if (leader, coin) not in ls_cache:
            ls_cache[(leader, coin)] = await real_leader_state(leader, coin)
        return ls_cache[(leader, coin)]
    engine._leader_state = _leader_state

    t0 = time.monotonic()
    for i, e in enumerate(events):
        f = (e["raw"] or {}).get("fill") or {}
        _cur["t"] = float(f.get("time") or 0) / 1000.0
        _cur["px"] = float(f.get("px") or 0)
        await engine.on_leader_event(e)
        if i % 50 == 0:
            await asyncio.sleep(0.05)             # pace: shared box
    dt = time.monotonic() - t0

    async with dbm.engine.begin() as conn:
        by = (await conn.execute(text(
            "SELECT s.leader_wallet, l.decision, COALESCE(l.gate,'-'), COUNT(*) FROM auto_copy_log l "
            "JOIN auto_copy_subs s ON s.id=l.sub_id GROUP BY 1,2,3 ORDER BY 1,4 DESC"))).all()
        shadow = (await conn.execute(text(
            "SELECT l.created_at, s.leader_wallet, l.event_type, l.coin, l.side, l.reason FROM auto_copy_log l "
            "JOIN auto_copy_subs s ON s.id=l.sub_id WHERE l.decision IN ('shadow','copied') ORDER BY l.id"))).all()
        pos = (await conn.execute(text(
            "SELECT leader_wallet, coin, side, size, entry_px, status, ROUND(realized_pnl,4) FROM auto_copy_positions ORDER BY id"))).all()
        opens_skipped = (await conn.execute(text(
            "SELECT s.leader_wallet, l.coin, l.side, l.gate, l.reason FROM auto_copy_log l JOIN auto_copy_subs s "
            "ON s.id=l.sub_id WHERE l.event_type='open' AND l.decision='skipped' ORDER BY l.id"))).all()
    print(f"\nprocessed in {dt:.1f}s")
    print("\n=== decisions by leader / decision / gate ===")
    for r in by:
        print(f"  {r[0][:10]}  {r[1]:8} {r[2]:26} {r[3]}")
    print("\n=== every open that was SKIPPED (gate, reason) ===")
    for r in opens_skipped:
        print(f"  {r[0][:10]} {r[1]:10} {r[2]:5} {r[3]:24} {r[4]}")
    print("\n=== shadow placements ===")
    for r in shadow:
        print(f"  {r[1][:10]} {r[2]:6} {r[3]:10} {r[4]:5} {r[5]}")
    print("\n=== shadow positions ===")
    for r in pos:
        print(f"  {r[0][:10]} {r[1]:10} {r[2]:5} size={r[3]:g} entry={r[4]:g} {r[5]} pnl={r[6]}")
    await dbm.engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
