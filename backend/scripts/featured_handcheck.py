"""Featured traders acceptance test 4: recompute win rate, profit factor and
net profit for qualified Hyperliquid wallets INDEPENDENTLY of the pipeline,
straight from Hyperliquid's own fills (and funding), and compare with the
stored featured_wallets row. Deliberately a second, simpler implementation:
a trade is open-to-flat per coin tracked only by the venue's startPosition
and the signed fill size; pnl = sum(closedPnl) - sum(fee) + funding inside
the trade. No import from services/featured.

    venv/bin/python scripts/featured_handcheck.py [n_wallets]
"""
import asyncio
import json
import os
import sys
import time
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import httpx                                         # noqa: E402
from sqlalchemy import text                          # noqa: E402

from app.config import settings                      # noqa: E402
from app.db import database                          # noqa: E402

INFO = "https://api.hyperliquid.xyz/info"
DAY = 86_400_000


async def info(c: httpx.AsyncClient, body: dict):
    for _ in range(4):
        r = await c.post(INFO, json=body, timeout=30)
        if r.status_code == 429:
            await asyncio.sleep(20)
            continue
        r.raise_for_status()
        await asyncio.sleep(3)
        return r.json()
    raise RuntimeError("429")


async def fills_90d(c: httpx.AsyncClient, acct: str, now_ms: int) -> list[dict]:
    out, start = [], now_ms - 90 * DAY
    for _ in range(5):
        b = await info(c, {"type": "userFillsByTime", "user": acct, "startTime": start, "endTime": now_ms, "aggregateByTime": True})
        out += b
        if len(b) < 2000:
            break
        start = int(b[-1]["time"]) + 1
    return out


async def funding_90d(c: httpx.AsyncClient, acct: str, now_ms: int) -> list[dict]:
    out, start = [], now_ms - 90 * DAY
    for _ in range(5):
        b = await info(c, {"type": "userFunding", "user": acct, "startTime": start, "endTime": now_ms})
        out += b
        if len(b) < 500:
            break
        start = int(b[-1]["time"]) + 1
    return out


def trades_from(fills: list[dict], funding: list[dict], now_ms: int) -> list[dict]:
    """Independent open-to-flat tracker. Returns closed trades (pnl, close time)."""
    by = defaultdict(list)
    for f in fills:
        by[f["coin"]].append(f)
    fund_by = defaultdict(list)
    for e in funding:
        d = e.get("delta") or {}
        if d.get("type") in (None, "funding") and d.get("coin"):
            fund_by[d["coin"]].append((int(e["time"]), float(d.get("usdc") or 0)))
    trades = []
    for coin, fs in by.items():
        fs.sort(key=lambda f: (int(f["time"]), int(f.get("tid") or 0)))
        open_t = None
        pnl = fee = 0.0
        for f in fs:
            start = float(f["startPosition"])
            sz = float(f["sz"]) * (1 if f["side"] == "B" else -1)
            end = start + sz
            if abs(end) < 1e-9:
                end = 0.0
            if open_t is None:
                if start != 0:
                    # a position opened before the 90-day window: skip until flat
                    if end == 0.0 or (start > 0) != (end > 0):
                        if end != 0.0:
                            open_t, pnl, fee = int(f["time"]), 0.0, 0.0   # the flip opens a new trade
                    continue
                open_t, pnl, fee = int(f["time"]), 0.0, 0.0
            pnl += float(f.get("closedPnl") or 0)
            fee += float(f.get("fee") or 0)
            if end == 0.0 or (start != 0 and (start > 0) != (end > 0)):
                close_t = int(f["time"])
                fund = sum(u for t, u in fund_by.get(coin, ()) if open_t <= t <= close_t)
                trades.append({"coin": coin, "open": open_t, "close": close_t, "pnl": pnl - fee + fund})
                open_t = None
                if end != 0.0:            # flip: the rest opens the next trade
                    open_t, pnl, fee = close_t, 0.0, 0.0
    return [t for t in trades if t["close"] > now_ms - 90 * DAY]


async def main(n: int) -> None:
    await database.init_db(settings.DATABASE_URL)
    sf = database.get_session_factory()
    async with sf() as s:
        rows = (await s.execute(text(
            "SELECT f.wallet, f.trades_90d, f.win_rate_90d, f.profit_factor_90d, f.net_pnl_90d, i.sub_accounts, f.computed_at FROM featured_wallets f "
            "LEFT JOIN wallet_identity i ON i.wallet=f.wallet WHERE f.venue='hl' AND f.qualified=1 "
            "AND f.as_of=(SELECT MAX(as_of) FROM featured_wallets) ORDER BY f.score DESC LIMIT :n"), {"n": n})).all()
    print(f"| wallet | trades stored / hand | win rate stored / hand | PF stored / hand | net stored / hand | within 1% |")
    print("| --- | --- | --- | --- | --- | --- |")
    ok_all = True
    async with httpx.AsyncClient() as c:
        for w, n_tr, wr, pf, net, subs, computed_at in rows:
            # the same 90-day window the stored row used (its computation time), so
            # trades that left or entered the window since are not counted as drift
            now_ms = int(computed_at.replace(tzinfo=__import__("datetime").timezone.utc).timestamp() * 1000)
            accts = [w] + [a for a in (json.loads(subs) if subs else []) if a]
            fills, funding = [], []
            for a in accts:
                fills += await fills_90d(c, a, now_ms)
                funding += await funding_90d(c, a, now_ms)
            tr = trades_from(fills, funding, now_ms)
            wins = sum(1 for t in tr if t["pnl"] > 0)
            gw = sum(t["pnl"] for t in tr if t["pnl"] > 0)
            gl = -sum(t["pnl"] for t in tr if t["pnl"] < 0)
            h_wr = wins / len(tr) if tr else None
            h_pf = gw / gl if gl > 1e-9 else None
            h_net = sum(t["pnl"] for t in tr)

            def close(a, b, rel=0.01):
                if a is None or b is None:
                    return a is None and b is None
                return abs(float(a) - float(b)) <= rel * max(1.0, abs(float(b)))
            ok = close(wr, h_wr) and close(pf, h_pf) and close(net, h_net) and abs(int(n_tr) - len(tr)) <= 1
            ok_all &= ok
            print(f"| {w[:10]}... | {n_tr} / {len(tr)} | {float(wr or 0):.4f} / {h_wr if h_wr is None else round(h_wr, 4)} | "
                  f"{float(pf or 0):.2f} / {h_pf if h_pf is None else round(h_pf, 2)} | {float(net or 0):,.2f} / {h_net:,.2f} | {'yes' if ok else 'NO'} |")
    print("\nALL WITHIN 1%" if ok_all else "\nMISMATCH")
    await database.close_db()


if __name__ == "__main__":
    asyncio.run(main(int(sys.argv[1]) if len(sys.argv) > 1 else 5))
