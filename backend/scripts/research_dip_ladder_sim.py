"""Research: synthetic test of the dip-ladder method measured from wallet 0x806f's fills
(HYPE, long only). SIMULATED orders on 1-minute bars; nothing is sent anywhere.

Rules (all measured from the fills, see measure()):
  * first buy of a fixed unit (notional) when the entry rule fires on a bar close
  * add one unit whenever price trades ADD_STEP below the previous buy (one add per bar)
  * close the whole stack when price trades TP above the average entry
  * never sell at a loss; cross margin at LEV; liquidation when equity falls to
    MAINT x notional (HYPE maintenance = half of 1/maxlev = 5 percent)
  * taker fee both ways

Bars: research_fetch_binance_1m.py output (1-minute). Fills: a TSV export of the
wallet's hl_fills rows (ms, time, side, dir, px, sz, closed_pnl, fee, start_position).

    python scripts/research_dip_ladder_sim.py --bars hype_1m.json [--measure fills.tsv] [--capital 6000] [--unit 700] [--rule dip60] [--start 2026-08-07]
"""
import argparse
import bisect
import csv
import json
import random
import statistics as st
from collections import deque
from datetime import datetime, timezone

MIN = 60_000


def dt(ms):
    return datetime.fromtimestamp(ms / 1000, timezone.utc)


def load(path):
    bars = json.load(open(path))
    for b in bars:
        for k in "ohlcv":
            b[k] = float(b[k])
    return bars


def rolling_high(bars, n):
    out = [0.0] * len(bars)
    dq = deque()
    for i, b in enumerate(bars):
        while dq and bars[dq[-1]]["h"] <= b["h"]:
            dq.pop()
        dq.append(i)
        while dq[0] <= i - n:
            dq.popleft()
        out[i] = bars[dq[0]]["h"]
    return out


def ctx(bars, i, hi24):
    c = bars[i]["c"]

    def ret(n):
        return c / bars[i - n]["c"] - 1 if i >= n else None
    h60 = max(b["h"] for b in bars[max(0, i - 60):i + 1])
    return {"r5": ret(5), "r15": ret(15), "r60": ret(60), "d60": c / h60 - 1, "d24": c / hi24[i] - 1}


RULES = {
    "any": lambda f: True,
    "dip60": lambda f: f["d60"] <= -0.005,
    "dip24": lambda f: f["d24"] <= -0.02,
    "red15": lambda f: f["r15"] is not None and f["r15"] <= -0.004,
}


def simulate(bars, *, capital, unit, lev=10.0, add_step=0.0073, tp=0.0068, fee=0.00045, maint=0.05,
             rule="any", max_units=None, cooldown_min=0, start_ms=None, end_ms=None, dip=0.005, flat_min=0):
    hi24 = rolling_high(bars, 1440)
    cash = capital
    qty = cost = 0.0
    last_buy = None
    last_buy_i = -10 ** 9
    last_close_i = -10 ** 9
    units = 0
    if rule == "dip60":
        entry_ok = lambda f: f["d60"] <= -dip
    else:
        entry_ok = RULES[rule]
    trips = []
    cur = None
    peak, maxdd, worst_unreal, max_units_used, max_ntl_eq = capital, 0.0, 0.0, 0, 0.0
    liquidated_at = None
    for i, b in enumerate(bars):
        if start_ms and b["t"] < start_ms:
            continue
        if end_ms and b["t"] > end_ms:
            break
        if i < 1440:
            continue
        if qty:
            avg = cost / qty
            unreal_low = qty * (b["l"] - avg)
            worst_unreal = min(worst_unreal, unreal_low)
            if cash + unreal_low <= maint * qty * b["l"]:
                liquidated_at = b["t"]
                trips.append({**cur, "exit_t": b["t"], "units": units, "pnl": -cash, "how": "LIQUIDATED"})
                cash = qty = cost = 0.0
                units = 0
                cur = None
                break
            if b["l"] <= last_buy * (1 - add_step) and (max_units is None or units < max_units) and i - last_buy_i >= cooldown_min:
                px = min(last_buy * (1 - add_step), b["o"])
                q = unit / px
                cash -= unit * fee
                qty += q
                cost += q * px
                units += 1
                last_buy, last_buy_i = px, i
                avg = cost / qty
                max_units_used = max(max_units_used, units)
                eq_now = cash + qty * (px - avg)
                max_ntl_eq = max(max_ntl_eq, qty * px / max(1e-9, eq_now))
            if qty and b["h"] >= avg * (1 + tp):
                px = max(avg * (1 + tp), b["o"])
                pnl = qty * (px - avg) - qty * px * fee
                cash += pnl
                trips.append({**cur, "exit_t": b["t"], "units": units, "pnl": pnl, "how": "tp", "avg": avg, "exit": px})
                qty = cost = 0.0
                units = 0
                cur = None
                last_buy = None
                last_close_i = i
        if not qty and cash > 20 and i - last_close_i >= flat_min:
            if entry_ok(ctx(bars, i, hi24)):
                px = b["c"]
                q = unit / px
                cash -= unit * fee
                qty, cost, units = q, q * px, 1
                last_buy, last_buy_i = px, i
                cur = {"entry_t": b["t"], "entry": px}
                max_units_used = max(max_units_used, 1)
        eq = cash + (qty * (b["c"] - cost / qty) if qty else 0.0)
        peak = max(peak, eq)
        if peak > 0:
            maxdd = max(maxdd, (peak - eq) / peak)
    open_pnl = qty * (bars[-1]["c"] - cost / qty) if qty else 0.0
    return {"end_cash": cash, "end_equity": cash + open_pnl, "open_units": units, "open_pnl": open_pnl, "trips": trips,
            "maxdd": maxdd, "worst_unreal": worst_unreal, "max_units": max_units_used, "max_ntl_eq": max_ntl_eq,
            "liquidated_at": liquidated_at}


def report(label, s, capital):
    tk = s["trips"]
    wins = [x for x in tk if x["pnl"] > 0]
    print(f"\n== {label}")
    print(f"   start ${capital:,.0f} -> cash ${s['end_cash']:,.0f}, equity incl. open ${s['end_equity']:,.0f} "
          f"({(s['end_equity'] / capital - 1) * 100:+.1f}%)" + (f"   LIQUIDATED {dt(s['liquidated_at']).date()}" if s["liquidated_at"] else ""))
    print(f"   closed trips {len(tk)}, green {len(wins)}, red {len(tk) - len(wins)}; "
          f"profit per green trip median ${st.median([x['pnl'] for x in wins]) if wins else 0:,.2f}")
    print(f"   worst drawdown on real equity (open loss counted) {s['maxdd'] * 100:.1f}%, worst open loss ${s['worst_unreal']:,.0f}, "
          f"most units in a stack {s['max_units']}, max notional / equity {s['max_ntl_eq']:.1f}x")
    if s["open_units"]:
        print(f"   still open at the end: {s['open_units']} units, open P&L ${s['open_pnl']:,.0f}")
    months = {}
    for x in tk:
        m = dt(x["exit_t"]).strftime("%Y-%m")
        months[m] = months.get(m, 0.0) + x["pnl"]
    print("   by month: " + ", ".join(f"{m} ${p:+,.0f}" for m, p in sorted(months.items())))
    hold = [(x["exit_t"] - x["entry_t"]) / MIN for x in tk]
    if hold:
        print(f"   hold minutes: median {st.median(hold):.0f}, longest {max(hold) / 1440:.1f} days")


def measure(bars, fills_tsv):
    rows = list(csv.DictReader(open(fills_tsv), delimiter="\t"))
    firsts = [int(float(r["ms"])) for r in rows if r["dir"].startswith("Open") and float(r["start_position"]) == 0]
    ts = [b["t"] for b in bars]
    hi24 = rolling_high(bars, 1440)
    feats = []
    for ms in firsts:
        i = bisect.bisect_right(ts, ms) - 1
        if i >= 1440:
            feats.append(ctx(bars, i, hi24))
    random.seed(1)
    base = [ctx(bars, i, hi24) for i in random.sample(range(1440, len(bars)), 3000)]
    print(f"his first buys matched to bars: {len(feats)} of {len(firsts)}")

    def q(xs, p):
        return sorted(xs)[int(p * (len(xs) - 1))]
    for k in ("r5", "r15", "r60", "d60", "d24"):
        a = [f[k] * 100 for f in feats if f[k] is not None]
        bq = [f[k] * 100 for f in base if f[k] is not None]
        print(f"   {k:3s}  his entries: p25 {q(a, .25):+.2f}%  median {st.median(a):+.2f}%  p75 {q(a, .75):+.2f}%"
              f"   | any minute: p25 {q(bq, .25):+.2f}%  median {st.median(bq):+.2f}%")
    for name, fn in RULES.items():
        print(f"   rule {name:6s} fires on {sum(1 for f in feats if fn(f)) / len(feats) * 100:.0f}% of his entries "
              f"and {sum(1 for f in base if fn(f)) / len(base) * 100:.0f}% of all minutes")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--bars", required=True)
    ap.add_argument("--measure", help="fills tsv: print his entry context")
    ap.add_argument("--capital", type=float, default=6000)
    ap.add_argument("--unit", type=float, default=None, help="unit notional; default 1.2 percent of capital at 10x")
    ap.add_argument("--lev", type=float, default=10)
    ap.add_argument("--rule", default="any")
    ap.add_argument("--start", default=None)
    ap.add_argument("--end", default=None)
    ap.add_argument("--max-units", type=int, default=None)
    ap.add_argument("--add-step", type=float, default=0.0073)
    ap.add_argument("--tp", type=float, default=0.0068)
    a = ap.parse_args()
    bars = load(a.bars)
    print(f"bars {len(bars)} from {dt(bars[0]['t'])} to {dt(bars[-1]['t'])}")
    if a.measure:
        measure(bars, a.measure)
    s_ms = int(datetime.fromisoformat(a.start).replace(tzinfo=timezone.utc).timestamp() * 1000) if a.start else None
    e_ms = int(datetime.fromisoformat(a.end).replace(tzinfo=timezone.utc).timestamp() * 1000) if a.end else None
    unit = a.unit if a.unit else a.capital * 0.012 * 10
    s = simulate(bars, capital=a.capital, unit=unit, lev=a.lev, rule=a.rule, start_ms=s_ms, end_ms=e_ms,
                 max_units=a.max_units, add_step=a.add_step, tp=a.tp)
    report(f"{a.rule}: unit ${unit:,.0f} at {a.lev:.0f}x, add every -{a.add_step * 100:.2f}%, take profit +{a.tp * 100:.2f}%", s, a.capital)
