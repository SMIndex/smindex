"""Research: which Hyperliquid markets fit the weekly breakout book.

Screen every listed market (main perps and every builder dex, RWA names
included) in three steps, all reads through the shared client at
BACKGROUND priority:

  1. liquidity from one metaAndAssetCtxs per dex: 24 h notional volume and
     open interest in USD; a market below the floors is out, my size would
     be the market
  2. daily candles (as deep as the venue serves) for the survivors and the
     exact rules backtested per market: 50/200-day EMA filter, 20-day close
     breakout, 2.5 ATR(20) stop trailed weekly, 6-week time stop, ATR20 over
     1.5 x ATR100 veto, weekly decisions, 10 bps costs per side
  3. today's state: filter direction, distance to the 20-day trigger, veto

Prints a ranked table. No order is placed. Read-only.

    python scripts/research_weekly_breakout.py [--min-vol 20e6] [--min-oi 10e6] [--days 1500]
"""
import argparse
import asyncio
import math
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.services.hyperliquid import client as hl  # noqa: E402

DAY = 86_400_000


async def post(payload: dict):
    for attempt in range(4):
        try:
            r = await hl.post_info(payload, priority=hl.BACKGROUND, timeout=30.0)
        except hl.HLWeightShed:
            await asyncio.sleep(20)
            continue
        if r.status_code == 429 or r.status_code >= 500:
            await asyncio.sleep(30)
            continue
        r.raise_for_status()
        return await hl.decode_json(r)
    return None


async def contexts() -> list[dict]:
    out = []
    dexes = [None] + sorted({str(d["name"]) for d in (await post({"type": "perpDexs"}) or []) if isinstance(d, dict) and d.get("name")})
    for dex in dexes:
        body = await post({"type": "metaAndAssetCtxs", "dex": dex} if dex else {"type": "metaAndAssetCtxs"})
        if not isinstance(body, list) or len(body) < 2:
            continue
        uni, ctxs = body[0].get("universe", []), body[1]
        for i, a in enumerate(uni):
            if i >= len(ctxs) or a.get("isDelisted"):
                continue
            c = ctxs[i] or {}
            name = str(a.get("name"))
            if dex and ":" not in name:
                name = f"{dex}:{name}"
            try:
                mark = float(c.get("markPx") or 0)
                out.append({"coin": name, "dex": dex or "main", "mark": mark, "vol24": float(c.get("dayNtlVlm") or 0),
                            "oi_usd": float(c.get("openInterest") or 0) * mark, "funding_8h": float(c.get("funding") or 0),
                            "max_lev": int(a.get("maxLeverage") or 0)})
            except (TypeError, ValueError):
                continue
        await asyncio.sleep(6)       # 20 weight at 200 a minute
    return out


def ema(xs, n):
    k = 2.0 / (n + 1)
    out, e = [], None
    for x in xs:
        e = x if e is None else x * k + e * (1 - k)
        out.append(e)
    return out


def atr(h, l, c, n):
    tr = [h[0] - l[0]] + [max(h[i] - l[i], abs(h[i] - c[i - 1]), abs(l[i] - c[i - 1])) for i in range(1, len(c))]
    out, a = [], None
    for i, x in enumerate(tr):
        if i < n:
            a = x if a is None else (a * i + x) / (i + 1)
        else:
            a = (a * (n - 1) + x) / n
        out.append(a)
    return out


def backtest(bars: list[dict], cost_bps: float = 10.0) -> dict:
    """Weekly decisions on daily bars. Returns trades, win rate, profit
    factor, max drawdown (on a 1 R per trade book), average R, and the state
    on the last bar."""
    c = [float(b["c"]) for b in bars]
    h = [float(b["h"]) for b in bars]
    lo = [float(b["l"]) for b in bars]
    n = len(c)
    if n < 230:
        return {"trades": 0, "state": "too short"}
    e50, e200 = ema(c, 50), ema(c, 200)
    a20, a100 = atr(h, lo, c, 20), atr(h, lo, c, 100)
    pos = 0            # +1 long, -1 short
    entry = stop = 0.0
    entry_i = 0
    rs = []
    equity, peak, maxdd = 0.0, 0.0, 0.0
    week_days = []
    for i in range(200, n):
        # exits checked every day on the stop
        if pos:
            if (pos > 0 and lo[i] <= stop) or (pos < 0 and h[i] >= stop):
                r = (stop - entry) / (entry - stop0) if pos > 0 else (entry - stop) / (stop0 - entry)
                r -= cost_bps * 2 / 1e4 / abs(entry - stop0) * entry
                rs.append(r)
                equity += r
                peak = max(peak, equity)
                maxdd = max(maxdd, peak - equity)
                pos = 0
        if i % 5 != 0:
            continue       # weekly decision day
        veto = a20[i] > 1.5 * a100[i]
        if pos:
            # trail: highest close since entry minus 2.5 ATR (mirror for shorts); time stop at 6 weeks
            if pos > 0:
                stop = max(stop, max(c[entry_i:i + 1]) - 2.5 * a20[i])
            else:
                stop = min(stop, min(c[entry_i:i + 1]) + 2.5 * a20[i])
            if i - entry_i >= 30 and abs(c[i] - entry) < 0.5 * a20[i]:
                r = ((c[i] - entry) if pos > 0 else (entry - c[i])) / abs(entry - stop0) - cost_bps * 2 / 1e4 / abs(entry - stop0) * entry
                rs.append(r)
                equity += r
                peak = max(peak, equity)
                maxdd = max(maxdd, peak - equity)
                pos = 0
            continue
        if veto:
            continue
        hi20, lo20 = max(c[i - 20:i]), min(c[i - 20:i])
        if e50[i] > e200[i] and c[i] > hi20:
            pos, entry, entry_i = 1, c[i], i
            stop0 = stop = entry - 2.5 * a20[i]
        elif e50[i] < e200[i] and c[i] < lo20:
            pos, entry, entry_i = -1, c[i], i
            stop0 = stop = entry + 2.5 * a20[i]
    wins = [r for r in rs if r > 0]
    losses = [-r for r in rs if r <= 0]
    i = n - 1
    hi20, lo20 = max(c[i - 20:i]), min(c[i - 20:i])
    filt = "long" if e50[i] > e200[i] else "short"
    dist = (hi20 / c[i] - 1) * 100 if filt == "long" else (1 - lo20 / c[i]) * 100
    return {"trades": len(rs), "win_rate": (len(wins) / len(rs) if rs else None),
            "pf": (sum(wins) / sum(losses) if losses else (math.inf if wins else None)),
            "avg_r": (sum(rs) / len(rs) if rs else None), "max_dd_r": maxdd, "net_r": sum(rs), "days": n,
            "filter": filt, "trigger_dist_pct": dist, "veto": a20[i] > 1.5 * a100[i], "atr_pct": a20[i] / c[i] * 100,
            "open_now": pos}


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--min-vol", type=float, default=20e6)
    ap.add_argument("--min-oi", type=float, default=10e6)
    ap.add_argument("--days", type=int, default=1500)
    args = ap.parse_args()
    t0 = time.time()
    ctx = await contexts()
    print(f"listed markets: {len(ctx)} ({sum(1 for c in ctx if c['dex'] == 'main')} main, {sum(1 for c in ctx if c['dex'] != 'main')} on builder dexes)")
    liquid = [c for c in ctx if c["vol24"] >= args.min_vol and c["oi_usd"] >= args.min_oi]
    liquid.sort(key=lambda c: -c["vol24"])
    print(f"liquid enough (24 h volume >= ${args.min_vol / 1e6:.0f}M and OI >= ${args.min_oi / 1e6:.0f}M): {len(liquid)}")
    rwa = [c for c in ctx if c["dex"] != "main"]
    rwa.sort(key=lambda c: -c["vol24"])
    print("\nbuilder-dex (RWA and stocks) top 12 by 24 h volume:")
    for c in rwa[:12]:
        print(f"  {c['coin']:18s} vol24 ${c['vol24'] / 1e6:7.1f}M  OI ${c['oi_usd'] / 1e6:7.1f}M  maxlev {c['max_lev']}  {'LIQUID' if c in liquid else 'thin'}")
    rows = []
    now = int(time.time() * 1000)
    for c in liquid:
        bars = await post({"type": "candleSnapshot", "req": {"coin": c["coin"], "interval": "1d", "startTime": now - args.days * DAY, "endTime": now}})
        await asyncio.sleep(8)
        if not bars:
            continue
        bt = backtest(bars)
        rows.append({**c, **bt, "fund_apr": c["funding_8h"] * 3 * 365 * 100})
    print(f"\nweight used: {hl.get_stats()['background']['weight'] if hasattr(hl, 'get_stats') else 'see /health'}; elapsed {time.time() - t0:.0f} s")
    rows.sort(key=lambda r: (-(r.get('pf') or 0) if r.get('trades', 0) >= 8 else 1, -r['vol24']))
    print("\nmarket            vol24    OI      days  trades  win%   PF    avgR   maxDD(R)  state                      veto  fundAPR")
    for r in rows:
        if r.get("trades", 0) == 0:
            st = r.get("state", "")
            print(f"{r['coin']:17s} {r['vol24'] / 1e6:6.0f}M {r['oi_usd'] / 1e6:6.0f}M {r.get('days', 0):5d}  {'no trades' if st != 'too short' else 'history too short (under 230 days)'}")
            continue
        pf = "inf" if r["pf"] == math.inf else f"{r['pf']:.2f}"
        state = f"{r['filter']} filter, {r['trigger_dist_pct']:+.1f}% to trigger" + (", IN TRADE" if r["open_now"] else "")
        print(f"{r['coin']:17s} {r['vol24'] / 1e6:6.0f}M {r['oi_usd'] / 1e6:6.0f}M {r['days']:5d}  {r['trades']:5d}  {r['win_rate'] * 100:4.0f}  {pf:>5s}  {r['avg_r']:+.2f}   {r['max_dd_r']:6.1f}   {state:28s} {'yes' if r['veto'] else 'no ':4s} {r['fund_apr']:+6.1f}%")


if __name__ == "__main__":
    asyncio.run(main())
