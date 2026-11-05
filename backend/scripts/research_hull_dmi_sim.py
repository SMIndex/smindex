"""Research: Hull Suite + DMI confirmation backtest (owner's rules, 2026-11-05).

Signal: Hull Suite HMA(31) on close, as in the Pine script
    HMA(src, n) = WMA(2 * WMA(src, n // 2) - WMA(src, n), round(sqrt(n)))
    green when HULL[0] > HULL[2]; a flip to green is a buy signal, a flip to red a sell signal.
Confirmation 1: the trade is taken at the close of the 2nd or 3rd candle after the signal,
    the first of them that closes above the signal candle's close (long) or below (short).
Confirmation 2: DMI (Wilder, 14/14 as in the Pine script) agrees at that candle:
    +DI > -DI for a long, -DI > +DI for a short, and ADX >= adx_min (0 = off).
Exit: the opposite Hull flip (at that candle's close), an optional stop loss, or liquidation.
Sizing: notional = equity x leverage on every trade. Fee per side. Simulated orders only.

    python hull_dmi_sim.py --bars data/BTC_1h.json [--length 31] [--lev 1] [--dir all] [--stop 0] [--adx-min 0]
"""
import argparse
import json
import math
import statistics as st
from datetime import datetime, timezone


def dt(s):
    return datetime.fromtimestamp(s, timezone.utc)


def wma(xs, n):
    out = [None] * len(xs)
    den = n * (n + 1) / 2
    for i in range(n - 1, len(xs)):
        s = 0.0
        for k in range(n):
            s += xs[i - k] * (n - k)
        out[i] = s / den
    return out


def hma(xs, n):
    half = wma(xs, n // 2)
    full = wma(xs, n)
    diff = [None if (a is None or b is None) else 2 * a - b for a, b in zip(half, full)]
    m = round(math.sqrt(n))
    out = [None] * len(xs)
    den = m * (m + 1) / 2
    for i in range(len(xs)):
        if i < m - 1 or any(diff[i - k] is None for k in range(m)):
            continue
        out[i] = sum(diff[i - k] * (m - k) for k in range(m)) / den
    return out


def rma(xs, n):
    """Pine ta.rma: first value is the SMA of the first n values, then Wilder smoothing."""
    out = [None] * len(xs)
    alpha = 1.0 / n
    prev = None
    for i, x in enumerate(xs):
        if x is None:
            continue
        if prev is None:
            window = xs[max(0, i - n + 1):i + 1]
            if len(window) < n or any(v is None for v in window):
                continue
            prev = sum(window) / n
        else:
            prev = alpha * x + (1 - alpha) * prev
        out[i] = prev
    return out


def dmi(h, l, c, di_len=14, adx_len=14):
    n = len(c)
    up = [None] + [h[i] - h[i - 1] for i in range(1, n)]
    down = [None] + [l[i - 1] - l[i] for i in range(1, n)]
    plus_dm = [None if u is None else (u if (u > d and u > 0) else 0.0) for u, d in zip(up, down)]
    minus_dm = [None if d is None else (d if (d > u and d > 0) else 0.0) for u, d in zip(up, down)]
    tr = [None] + [max(h[i] - l[i], abs(h[i] - c[i - 1]), abs(l[i] - c[i - 1])) for i in range(1, n)]
    trur = rma(tr, di_len)
    p = rma(plus_dm, di_len)
    m = rma(minus_dm, di_len)
    plus = [None if (a is None or t is None or t == 0) else 100 * a / t for a, t in zip(p, trur)]
    minus = [None if (a is None or t is None or t == 0) else 100 * a / t for a, t in zip(m, trur)]
    dx = [None if (a is None or b is None) else abs(a - b) / ((a + b) if (a + b) != 0 else 1) for a, b in zip(plus, minus)]
    adx = [None if v is None else 100 * v for v in rma(dx, adx_len)]
    return plus, minus, adx


def supertrend(h, l, c, length=10, mult=3.0):
    """TradingView ta.supertrend: Wilder ATR, carried bands, direction flips on the close.
    Returns (line, dir) with dir 1 = up (line below price), -1 = down."""
    n = len(c)
    tr = [None] + [max(h[i] - l[i], abs(h[i] - c[i - 1]), abs(l[i] - c[i - 1])) for i in range(1, n)]
    atr = rma(tr, length)
    line = [None] * n; d = [0] * n
    up = dn = None; trend = 1
    for i in range(n):
        if atr[i] is None:
            continue
        hl2 = (h[i] + l[i]) / 2
        u = hl2 - mult * atr[i]; dd = hl2 + mult * atr[i]
        if up is not None and c[i - 1] > up:
            u = max(u, up)
        if dn is not None and c[i - 1] < dn:
            dd = min(dd, dn)
        if up is not None and dn is not None:
            if trend == -1 and c[i] > dn:
                trend = 1
            elif trend == 1 and c[i] < up:
                trend = -1
        up, dn = u, dd
        d[i] = trend; line[i] = u if trend == 1 else dd
    return line, d


def simulate(bars, *, length=31, lev=1.0, direction="all", stop=0.0, adx_min=0.0, fee=0.00045, capital=6000.0,
             confirm=(2, 3), maint=0.05, di_len=14, adx_len=14, exit_mode="hull", st_len=10, st_mult=3.0, tv_mode=False):
    o = [b[1] for b in bars]; h = [b[2] for b in bars]; l = [b[3] for b in bars]; c = [b[4] for b in bars]; t = [b[0] for b in bars]
    n = len(c)
    hull = hma(c, length)
    plus, minus, adx = dmi(h, l, c, di_len, adx_len)
    st_line, st_dir = supertrend(h, l, c, st_len, st_mult)
    green = [None] * n
    for i in range(2, n):
        if hull[i] is not None and hull[i - 2] is not None:
            green[i] = hull[i] > hull[i - 2]
    equity = capital
    pos = 0; entry = 0.0; qty = 0.0; entry_i = 0; stop_px = None
    pending = None           # {"side": +1/-1, "i": signal bar, "px": signal close}
    trades = []; signals = 0; confirmed = 0; dropped_dmi = 0; dropped_price = 0
    peak = capital; maxdd = 0.0; fees = 0.0; liqs = 0

    def close_pos(i, px, how):
        nonlocal equity, pos, qty, fees
        gross = qty * (px - entry) * pos
        f = qty * px * fee
        equity += gross - f
        fees += f
        trades.append({"side": "long" if pos > 0 else "short", "entry_t": t[entry_i], "exit_t": t[i], "entry": entry, "exit": px,
                       "pnl": gross - f - qty * entry * fee, "fee": f + qty * entry * fee, "how": how, "notional": qty * entry, "eq": equity})
        pos = 0; qty = 0.0

    for i in range(1, n):
        if green[i] is None or green[i - 1] is None:
            continue
        flip_up = green[i] and not green[i - 1]
        flip_dn = (not green[i]) and green[i - 1]
        # 1. manage the open position on this bar: stop / liquidation first (intrabar), then the opposite flip at the close
        if pos:
            adverse = (entry - l[i]) / entry if pos > 0 else (h[i] - entry) / entry
            liq_move = max(0.0, 1.0 / lev - maint)
            if lev > 1 and adverse >= liq_move:
                px = entry * (1 - liq_move) if pos > 0 else entry * (1 + liq_move)
                close_pos(i, px, "LIQUIDATED"); liqs += 1
            elif stop_px is not None and ((pos > 0 and l[i] <= stop_px) or (pos < 0 and h[i] >= stop_px)):
                px = min(stop_px, o[i]) if pos > 0 else max(stop_px, o[i])
                close_pos(i, px, "stop")
            else:
                hull_exit = (pos > 0 and flip_dn) or (pos < 0 and flip_up)
                st_exit = st_dir[i] != 0 and st_dir[i - 1] != 0 and ((pos > 0 and st_dir[i] == -1 and st_dir[i - 1] == 1) or (pos < 0 and st_dir[i] == 1 and st_dir[i - 1] == -1))
                if exit_mode == "hull" and hull_exit:
                    close_pos(i, c[i], "flip")
                elif exit_mode == "st" and st_exit:
                    close_pos(i, c[i], "supertrend")
                elif exit_mode == "either" and (hull_exit or st_exit):
                    close_pos(i, c[i], "flip" if hull_exit else "supertrend")
        # 2. new signal
        if flip_up or flip_dn:
            side = 1 if flip_up else -1
            if direction == "all" or (direction == "long" and side > 0) or (direction == "short" and side < 0):
                signals += 1
                if tv_mode:
                    # the TradingView strategy: enter on the flip candle itself, no confirmation, no DMI
                    if not pos:
                        pos = side; entry = c[i]; entry_i = i
                        notional = equity * lev
                        qty = notional / entry
                        fees += notional * fee
                        equity -= notional * fee
                        stop_px = (entry * (1 - stop) if side > 0 else entry * (1 + stop)) if stop > 0 else None
                        confirmed += 1
                    pending = None
                else:
                    pending = {"side": side, "i": i, "px": c[i]}
            else:
                pending = None
        # 3. confirmation window
        if pending and not pos and not tv_mode:
            k = i - pending["i"]
            if k in confirm:
                side = pending["side"]
                price_ok = c[i] > pending["px"] if side > 0 else c[i] < pending["px"]
                if price_ok:
                    dmi_ok = plus[i] is not None and minus[i] is not None and adx[i] is not None and \
                        ((plus[i] > minus[i]) if side > 0 else (minus[i] > plus[i])) and adx[i] >= adx_min
                    if dmi_ok:
                        pos = side; entry = c[i]; entry_i = i
                        notional = equity * lev
                        qty = notional / entry
                        fees += notional * fee
                        equity -= notional * fee
                        stop_px = (entry * (1 - stop) if side > 0 else entry * (1 + stop)) if stop > 0 else None
                        pending = None; confirmed += 1
                    else:
                        dropped_dmi += 1
                        if k == max(confirm):
                            pending = None
                elif k == max(confirm):
                    dropped_price += 1; pending = None
            elif k > max(confirm):
                pending = None
        # equity curve
        eq = equity + (qty * (c[i] - entry) * pos if pos else 0.0)
        peak = max(peak, eq); maxdd = max(maxdd, (peak - eq) / peak if peak > 0 else 0)
        if equity <= capital * 0.02:
            break
    open_pnl = qty * (c[-1] - entry) * pos if pos else 0.0
    return {"trades": trades, "end": equity + open_pnl, "maxdd": maxdd, "fees": fees, "signals": signals, "confirmed": confirmed,
            "dropped_dmi": dropped_dmi, "dropped_price": dropped_price, "liqs": liqs, "open": pos, "open_pnl": open_pnl,
            "hull": hull, "green": green, "plus": plus, "minus": minus, "adx": adx, "st_line": st_line, "st_dir": st_dir}


def report(label, s, capital):
    tk = s["trades"]; wins = [x for x in tk if x["pnl"] > 0]; losses = [x for x in tk if x["pnl"] <= 0]
    gw = sum(x["pnl"] for x in wins); gl = -sum(x["pnl"] for x in losses)
    print(f"\n== {label}")
    print(f"   ${capital:,.0f} -> ${s['end']:,.0f} ({(s['end'] / capital - 1) * 100:+.1f}%)   trades {len(tk)} (signals {s['signals']}, dropped: price {s['dropped_price']}, dmi {s['dropped_dmi']})   "
          f"win rate {len(wins) / max(1, len(tk)) * 100:.0f}%   profit factor {(gw / gl) if gl else float('inf'):.2f}   max DD {s['maxdd'] * 100:.1f}%   fees ${s['fees']:,.0f}   liquidations {s['liqs']}")
    if tk:
        hold = [(x["exit_t"] - x["entry_t"]) / 3600 for x in tk]
        print(f"   avg win ${st.mean([x['pnl'] for x in wins]) if wins else 0:,.0f}, avg loss ${st.mean([x['pnl'] for x in losses]) if losses else 0:,.0f}, "
              f"largest loss ${min(x['pnl'] for x in tk):,.0f}, hold median {st.median(hold):.1f} h, longs {sum(1 for x in tk if x['side'] == 'long')}, shorts {sum(1 for x in tk if x['side'] == 'short')}")
        years = {}
        for x in tk:
            y = dt(x["exit_t"]).year; years[y] = years.get(y, 0.0) + x["pnl"]
        print("   by year: " + ", ".join(f"{y} ${p:+,.0f}" for y, p in sorted(years.items())))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--bars", required=True)
    ap.add_argument("--length", type=int, default=31)
    ap.add_argument("--lev", type=float, default=1.0)
    ap.add_argument("--dir", default="all")
    ap.add_argument("--stop", type=float, default=0.0, help="stop loss as a fraction, 0 = none")
    ap.add_argument("--adx-min", type=float, default=0.0)
    ap.add_argument("--capital", type=float, default=6000)
    ap.add_argument("--exit", default="hull", choices=["hull", "st", "either"], help="exit on the opposite Hull flip, the Supertrend flip, or whichever comes first")
    ap.add_argument("--st-len", type=int, default=10)
    ap.add_argument("--st-mult", type=float, default=3.0)
    ap.add_argument("--tv", action="store_true", help="TradingView mode: enter on the flip candle, no confirmation, no DMI")
    a = ap.parse_args()
    j = json.load(open(a.bars))
    s = simulate(j["bars"], length=a.length, lev=a.lev, direction=a.dir, stop=a.stop, adx_min=a.adx_min, capital=a.capital, exit_mode=a.exit, st_len=a.st_len, st_mult=a.st_mult, tv_mode=a.tv)
    report(f"{j['coin']} {j['tf']} ({len(j['bars'])} bars from {dt(j['bars'][0][0]).date()}): Hull {a.length}, DMI 14/14, {a.dir}, {a.lev:.0f}x, stop {a.stop * 100:.0f}%", s, a.capital)
    for x in s["trades"][:5]:
        print("  ", x["side"], dt(x["entry_t"]).strftime("%Y-%m-%d %H:%M"), "->", dt(x["exit_t"]).strftime("%Y-%m-%d %H:%M"), f"{x['entry']:.2f} -> {x['exit']:.2f}", f"${x['pnl']:+,.0f}", x["how"])
