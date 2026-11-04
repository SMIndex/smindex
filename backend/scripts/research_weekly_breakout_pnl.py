"""Research: profit and loss of the weekly breakout book as an account.

Uses the same rules and data as research_weekly_breakout.py (liquidity
floors, daily candles from the venue, weekly decisions, 2.5 ATR stops, 10 bps
costs per side) and simulates an account: start capital, 0.5 percent of
CURRENT equity risked per trade, trades taken in date order across the
universe, at most 6 open at once. Two books are printed:

  * the fixed universe: every liquid market with 300 or more days of history
    (the honest book, nothing chosen by its past result)
  * the four markets picked by their backtest (ZEC, XRP, BTC, UNI), to show
    what selecting winners after the fact adds

Prints total return, CAGR, max drawdown, yearly returns, trades and the
per-market contribution in dollars. Read-only. Candles are cached in
--cache (default: the system temp folder) so the venue is read once.

    python scripts/research_weekly_breakout_pnl.py [--capital 100000] [--risk 0.005] [--cache PATH]
"""
import argparse
import asyncio
import json
import math
import os
import sys
import tempfile
import time
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import research_weekly_breakout as rwb  # noqa: E402

DAY = 86_400_000
PICKS = ["ZEC", "XRP", "BTC", "UNI"]


def trades_for(bars: list[dict], cost_bps: float = 10.0) -> list[dict]:
    """Same rules as rwb.backtest, returning the trade list with dates."""
    c = [float(b["c"]) for b in bars]
    h = [float(b["h"]) for b in bars]
    lo = [float(b["l"]) for b in bars]
    t = [int(b["t"]) for b in bars]
    n = len(c)
    out = []
    if n < 230:
        return out
    e50, e200 = rwb.ema(c, 50), rwb.ema(c, 200)
    a20, a100 = rwb.atr(h, lo, c, 20), rwb.atr(h, lo, c, 100)
    pos = 0
    entry = stop = stop0 = 0.0
    entry_i = 0

    def close(i, px):
        r = ((px - entry) if pos > 0 else (entry - px)) / abs(entry - stop0)
        r -= cost_bps * 2 / 1e4 * entry / abs(entry - stop0)
        out.append({"entry_t": t[entry_i], "exit_t": t[i], "r": r, "side": "long" if pos > 0 else "short",
                    "stop_pct": abs(entry - stop0) / entry})

    for i in range(200, n):
        if pos:
            if (pos > 0 and lo[i] <= stop) or (pos < 0 and h[i] >= stop):
                close(i, stop)
                pos = 0
        if i % 5 != 0:
            continue
        veto = a20[i] > 1.5 * a100[i]
        if pos:
            if pos > 0:
                stop = max(stop, max(c[entry_i:i + 1]) - 2.5 * a20[i])
            else:
                stop = min(stop, min(c[entry_i:i + 1]) + 2.5 * a20[i])
            if i - entry_i >= 30 and abs(c[i] - entry) < 0.5 * a20[i]:
                close(i, c[i])
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
    if pos:
        close(n - 1, c[n - 1])       # mark the open trade at the last close
        out[-1]["open"] = True
    return out


def simulate(trades: list[dict], capital: float, risk: float, max_open: int = 6) -> dict:
    """Trades from all markets in entry order; each risks `risk` of equity at
    entry; at most max_open at once (later signals wait, here: skipped)."""
    trades = sorted(trades, key=lambda x: x["entry_t"])
    equity = capital
    peak = capital
    maxdd = 0.0
    open_until = []
    curve = []
    taken = []
    for tr in trades:
        open_until = [x for x in open_until if x > tr["entry_t"]]
        if len(open_until) >= max_open:
            continue
        risk_usd = equity * risk
        pnl = tr["r"] * risk_usd
        equity += pnl
        peak = max(peak, equity)
        maxdd = max(maxdd, (peak - equity) / peak)
        open_until.append(tr["exit_t"])
        taken.append({**tr, "pnl": pnl, "equity": equity})
        curve.append((tr["exit_t"], equity))
    years = {}
    for x in taken:
        y = datetime.utcfromtimestamp(x["exit_t"] / 1000).year
        years[y] = years.get(y, 0.0) + x["pnl"]
    span_days = (taken[-1]["exit_t"] - taken[0]["entry_t"]) / DAY if taken else 0
    cagr = ((equity / capital) ** (365.0 / span_days) - 1) if span_days > 365 else None
    return {"end": equity, "ret": equity / capital - 1, "cagr": cagr, "maxdd": maxdd, "trades": len(taken), "skipped": len(trades) - len(taken),
            "years": years, "taken": taken, "span_days": span_days}


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--capital", type=float, default=100_000)
    ap.add_argument("--risk", type=float, default=0.005)
    ap.add_argument("--min-vol", type=float, default=20e6)
    ap.add_argument("--min-oi", type=float, default=10e6)
    ap.add_argument("--min-days", type=int, default=300)
    ap.add_argument("--cache", default=os.path.join(tempfile.gettempdir(), "ut_daily_candles.json"))
    args = ap.parse_args()
    cache = {}
    if os.path.exists(args.cache):
        cache = json.load(open(args.cache))
    ctx = await rwb.contexts()
    liquid = [c for c in ctx if c["vol24"] >= args.min_vol and c["oi_usd"] >= args.min_oi]
    now = int(time.time() * 1000)
    per_market = {}
    for c in liquid:
        coin = c["coin"]
        bars = cache.get(coin)
        if bars is None:
            bars = await rwb.post({"type": "candleSnapshot", "req": {"coin": coin, "interval": "1d", "startTime": now - 1500 * DAY, "endTime": now}}) or []
            cache[coin] = bars
            await asyncio.sleep(8)
        if len(bars) >= args.min_days:
            per_market[coin] = trades_for(bars)
    json.dump(cache, open(args.cache, "w"))
    universe = sorted(per_market)
    print(f"fixed universe (liquid, {args.min_days}+ days of history): {len(universe)} markets: {', '.join(universe)}")
    for label, coins in (("FIXED UNIVERSE (honest book)", universe), ("THE FOUR PICKS (chosen by their backtest)", [c for c in PICKS if c in per_market])):
        trades = [dict(t, coin=coin) for coin in coins for t in per_market[coin]]
        s = simulate(trades, args.capital, args.risk)
        print(f"\n== {label}: {len(coins)} markets, {s['trades']} trades taken, {s['skipped']} skipped (6 open cap), span {s['span_days'] / 365:.1f} years")
        print(f"   start ${args.capital:,.0f} -> end ${s['end']:,.0f}   total {s['ret'] * 100:+.1f}%   CAGR {('%.1f%%' % (s['cagr'] * 100)) if s['cagr'] is not None else 'n/a'}   max drawdown {s['maxdd'] * 100:.1f}%")
        print("   by year: " + ", ".join(f"{y}: ${p:+,.0f}" for y, p in sorted(s["years"].items())))
        by_coin = {}
        for x in s["taken"]:
            d = by_coin.setdefault(x["coin"], {"n": 0, "pnl": 0.0, "wins": 0})
            d["n"] += 1
            d["pnl"] += x["pnl"]
            d["wins"] += 1 if x["pnl"] > 0 else 0
        print("   by market: " + ", ".join(f"{k} {v['n']}t ${v['pnl']:+,.0f}" for k, v in sorted(by_coin.items(), key=lambda kv: -kv[1]['pnl'])))
        wins = [x["pnl"] for x in s["taken"] if x["pnl"] > 0]
        losses = [x["pnl"] for x in s["taken"] if x["pnl"] <= 0]
        print(f"   win rate {len(wins) / max(1, len(s['taken'])) * 100:.0f}%, average win ${sum(wins) / max(1, len(wins)):,.0f}, average loss ${sum(losses) / max(1, len(losses)):,.0f}, "
              f"largest win ${max(wins) if wins else 0:,.0f}, largest loss ${min(losses) if losses else 0:,.0f}")
    # the same honest book at 1 percent risk, for scale
    trades = [dict(t, coin=coin) for coin in universe for t in per_market[coin]]
    s = simulate(trades, args.capital, 0.01)
    print(f"\n== FIXED UNIVERSE at 1.0% risk per trade: end ${s['end']:,.0f}   total {s['ret'] * 100:+.1f}%   max drawdown {s['maxdd'] * 100:.1f}%")


if __name__ == "__main__":
    asyncio.run(main())
