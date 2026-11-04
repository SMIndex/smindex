"""Research: the breakout book with DAILY decisions, as a small account with
leverage (owner, 2026-11-05: $6,000, 5x on each trade).

Rules per market (daily bars): 50/200 EMA filter, close above the highest
close of the last 10 days (long) or below the lowest (short), stop 2 ATR(20)
trailed every day to the best close since entry, time stop after 30 bars
within half an ATR of entry, no new entry when ATR20 is above 1.5 x ATR100.
Stops fill at the stop price, or at the open when the bar gaps through it.
10 bps costs per side.

Sizing modes, max 6 open positions:
  slot   : margin = equity / 6, notional = leverage x margin ("5x on each
           trade" on a slice of the account); liquidation when the adverse
           move reaches 0.9 / leverage before the stop
  whole  : notional = leverage x whole equity (what "5x on each trade" means
           taken literally); shown to make the point, not to trade
  risk   : notional from a fixed fraction of equity at risk to the stop

Candles come from the cache written by research_weekly_breakout_pnl.py; no
venue read. Read-only, places nothing.

    python scripts/research_daily_breakout_pnl.py [--capital 6000] [--lev 5] [--cache PATH]
"""
import argparse
import json
import math
import os
import statistics
import sys
import tempfile
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import research_weekly_breakout as rwb  # noqa: E402

DAY = 86_400_000
UNIVERSE = ["BTC", "ENA", "ETH", "HYPE", "LIT", "NEAR", "PUMP", "SOL", "TRUMP", "UNI", "WLD", "XMR", "XRP", "ZEC", "kPEPE", "xyz:AAPL", "xyz:AMD",
            "xyz:AMZN", "xyz:CL", "xyz:CRCL", "xyz:GOLD", "xyz:GOOGL", "xyz:INTC", "xyz:META", "xyz:MSFT", "xyz:MU", "xyz:NVDA", "xyz:SILVER",
            "xyz:TSLA", "xyz:XYZ100"]


def trades_for(bars: list[dict], *, every: int = 1, lookback: int = 10, stop_atr: float = 2.0, time_stop: int = 30, cost_bps: float = 10.0) -> list[dict]:
    """Trades with entry/exit prices and the worst adverse excursion in
    percent (for the liquidation check)."""
    o = [float(b["o"]) for b in bars]
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
    mae = 0.0

    def close(i, px, how):
        move = (px - entry) / entry if pos > 0 else (entry - px) / entry
        # stop_pct is the INITIAL stop distance: the risk unit the position was sized on
        out.append({"entry_t": t[entry_i], "exit_t": t[i], "move": move - cost_bps * 2 / 1e4, "side": "long" if pos > 0 else "short",
                    "mae": mae, "stop_pct": abs(entry - stop0) / entry, "how": how})

    for i in range(200, n):
        if pos:
            adverse = (entry - lo[i]) / entry if pos > 0 else (h[i] - entry) / entry
            mae = max(mae, adverse)
            if pos > 0 and lo[i] <= stop:
                close(i, min(stop, o[i]) if o[i] < stop else stop, "stop")
                pos = 0
            elif pos < 0 and h[i] >= stop:
                close(i, max(stop, o[i]) if o[i] > stop else stop, "stop")
                pos = 0
        if i % every != 0:
            continue
        veto = a20[i] > 1.5 * a100[i]
        if pos:
            if pos > 0:
                stop = max(stop, max(c[entry_i:i + 1]) - stop_atr * a20[i])
            else:
                stop = min(stop, min(c[entry_i:i + 1]) + stop_atr * a20[i])
            if i - entry_i >= time_stop and abs(c[i] - entry) < 0.5 * a20[i]:
                close(i, c[i], "time")
                pos = 0
            continue
        if veto:
            continue
        hi, lw = max(c[i - lookback:i]), min(c[i - lookback:i])
        if e50[i] > e200[i] and c[i] > hi:
            pos, entry, entry_i, mae = 1, c[i], i, 0.0
            stop0 = stop = entry - stop_atr * a20[i]
        elif e50[i] < e200[i] and c[i] < lw:
            pos, entry, entry_i, mae = -1, c[i], i, 0.0
            stop0 = stop = entry + stop_atr * a20[i]
    if pos:
        close(n - 1, c[n - 1], "open")
    return out


def simulate(trades: list[dict], capital: float, *, mode: str, lev: float = 5.0, risk: float = 0.01, max_open: int = 6) -> dict:
    trades = sorted(trades, key=lambda x: x["entry_t"])
    equity, peak, maxdd = capital, capital, 0.0
    open_until: list[int] = []
    taken = []
    liqs = 0
    for tr in trades:
        open_until = [x for x in open_until if x > tr["entry_t"]]
        if len(open_until) >= max_open or equity <= capital * 0.05:
            continue
        if mode == "slot":
            margin = equity / max_open
            notional = margin * lev
        elif mode == "whole":
            margin = equity
            notional = equity * lev
        else:
            notional = (equity * risk) / max(tr["stop_pct"], 1e-6)
            margin = notional / lev
        liq_move = 0.9 / lev
        # the fill already includes gaps through the stop (open worse than the stop); a fill that
        # lands beyond the liquidation level means the venue closed it first: the margin is gone
        if mode in ("slot", "whole") and -tr["move"] >= liq_move:
            pnl = -margin
            liqs += 1
        else:
            pnl = notional * tr["move"]
            pnl = max(pnl, -margin)            # a loss can never exceed the margin posted
        equity += pnl
        peak = max(peak, equity)
        maxdd = max(maxdd, (peak - equity) / peak)
        open_until.append(tr["exit_t"])
        taken.append({**tr, "pnl": pnl, "equity": equity, "notional": notional, "margin": margin})
    years: dict[int, float] = {}
    for x in taken:
        y = datetime.fromtimestamp(x["exit_t"] / 1000, timezone.utc).year
        years[y] = years.get(y, 0.0) + x["pnl"]
    span = (taken[-1]["exit_t"] - taken[0]["entry_t"]) / DAY if taken else 0
    return {"end": equity, "ret": equity / capital - 1, "cagr": ((equity / capital) ** (365 / span) - 1) if span > 365 and equity > 0 else None,
            "maxdd": maxdd, "trades": len(taken), "years": years, "taken": taken, "liqs": liqs, "span": span, "busted": equity <= capital * 0.05}


def report(label: str, s: dict, capital: float) -> None:
    tk = s["taken"]
    hold = [(x["exit_t"] - x["entry_t"]) / DAY for x in tk]
    wins = [x["pnl"] for x in tk if x["pnl"] > 0]
    losses = [x["pnl"] for x in tk if x["pnl"] <= 0]
    worst = min(losses) if losses else 0
    print(f"\n== {label}")
    print(f"   start ${capital:,.0f} -> end ${s['end']:,.0f}   total {s['ret'] * 100:+.1f}%   CAGR {('%.1f%%' % (s['cagr'] * 100)) if s['cagr'] is not None else 'n/a'}   "
          f"max drawdown {s['maxdd'] * 100:.1f}%   trades {s['trades']} ({s['trades'] / max(1, s['span'] / 365):.0f} a year)   liquidations {s['liqs']}{'   ACCOUNT BUSTED' if s['busted'] else ''}")
    print(f"   win rate {len(wins) / max(1, len(tk)) * 100:.0f}%, avg win ${statistics.mean(wins) if wins else 0:,.0f}, avg loss ${statistics.mean(losses) if losses else 0:,.0f}, "
          f"worst trade ${worst:,.0f} ({worst / max(1, capital) * 100:+.1f}% of start capital), hold median {statistics.median(hold) if hold else 0:.0f} d, "
          f"typical notional ${statistics.median([x['notional'] for x in tk]) if tk else 0:,.0f}")
    print("   by year: " + ", ".join(f"{y}: ${p:+,.0f}" for y, p in sorted(s["years"].items())))
    # worst losing streak in R-ish terms: consecutive losses and their total
    streak = best = 0
    cur_loss = worst_streak_loss = 0.0
    for x in tk:
        if x["pnl"] <= 0:
            streak += 1
            cur_loss += x["pnl"]
            best = max(best, streak)
            worst_streak_loss = min(worst_streak_loss, cur_loss)
        else:
            streak, cur_loss = 0, 0.0
    print(f"   longest losing streak {best} trades, worst streak loss ${worst_streak_loss:,.0f}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--capital", type=float, default=6000)
    ap.add_argument("--lev", type=float, default=5)
    ap.add_argument("--cache", default=os.path.join(tempfile.gettempdir(), "ut_daily_candles.json"))
    args = ap.parse_args()
    cache = json.load(open(args.cache))
    coins = [c for c in UNIVERSE if c in cache and len(cache[c]) >= 300]
    daily = [dict(t, coin=c) for c in coins for t in trades_for(cache[c], every=1, lookback=10, stop_atr=2.0)]
    weekly = [dict(t, coin=c) for c in coins for t in trades_for(cache[c], every=5, lookback=20, stop_atr=2.5)]
    print(f"universe {len(coins)} markets, {len(daily)} daily-rule signals, {len(weekly)} weekly-rule signals over {max(len(cache[c]) for c in coins)} days")
    cap, lev = args.capital, args.lev
    report(f"DAILY rules, {lev:.0f}x on a slot (account / 6 per trade)", simulate(daily, cap, mode="slot", lev=lev), cap)
    report(f"DAILY rules, {lev:.0f}x on the WHOLE account per trade (literal reading)", simulate(daily, cap, mode="whole", lev=lev), cap)
    report("DAILY rules, 2% of equity at risk per trade (leverage as needed, capped by the 5x margin)", simulate(daily, cap, mode="risk", lev=lev, risk=0.02), cap)
    report("DAILY rules, 1% of equity at risk per trade", simulate(daily, cap, mode="risk", lev=lev, risk=0.01), cap)
    report(f"WEEKLY rules, {lev:.0f}x on a slot (for comparison)", simulate(weekly, cap, mode="slot", lev=lev), cap)
    report("WEEKLY rules, 1% of equity at risk per trade", simulate(weekly, cap, mode="risk", lev=lev, risk=0.01), cap)
    # per market under the slot mode, daily
    s = simulate(daily, cap, mode="slot", lev=lev)
    by = {}
    for x in s["taken"]:
        d = by.setdefault(x["coin"], [0, 0.0])
        d[0] += 1
        d[1] += x["pnl"]
    print("\nDAILY slot mode by market: " + ", ".join(f"{k} {v[0]}t ${v[1]:+,.0f}" for k, v in sorted(by.items(), key=lambda kv: -kv[1][1])))


if __name__ == "__main__":
    main()
