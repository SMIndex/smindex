"""Featured traders acceptance test 1: round trips from a fixture of 40 fills
across 3 coins (adds, partial closes, a flip, a liquidation, fills that
reduce a position opened before the stored history, funding inside a trip).
Expected trips, sizes, PnL and fees are computed by hand below.

    python scripts/test_featured_roundtrips.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.services.featured.roundtrips import build_trips, replay  # noqa: E402

checks = []


def check(name, got, want, tol=1e-6):
    if isinstance(want, float) or isinstance(got, float):
        ok = got is not None and want is not None and abs(float(got) - float(want)) <= tol
    else:
        ok = got == want
    checks.append(ok)
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}: got {got!r} want {want!r}")


def fill(t, coin, side, sz, px, start, pnl=0.0, fee=0.0, crossed=True, liq=False, tid=None):
    return {"time": t, "coin": coin, "side": side, "sz": sz, "px": px, "startPosition": start,
            "closedPnl": pnl, "fee": fee, "crossed": crossed, "tid": tid or t,
            "dir": "", "liquidation": ({"liquidatedUser": "0x1"} if liq else None)}


F = []
# BTC trip 1: long, 2 adds (one to a winner, one averaging down), 2 partial closes
F += [fill(1000, "BTC", "B", 0.10, 100, 0.00, fee=1.0),
      fill(2000, "BTC", "B", 0.05, 110, 0.10, fee=0.5),
      fill(3000, "BTC", "B", 0.05, 95, 0.15, fee=0.5, crossed=False),
      fill(4000, "BTC", "A", 0.10, 120, 0.20, pnl=1.875, fee=0.5),
      fill(5000, "BTC", "A", 0.10, 130, 0.10, pnl=2.875, fee=0.5)]
# BTC trip 2 + 3: open long, then one fill flips long 0.10 into short 0.20
F += [fill(6000, "BTC", "B", 0.10, 100, 0.00, fee=1.0),
      fill(7000, "BTC", "A", 0.30, 90, 0.10, pnl=-1.0, fee=1.5),
      fill(8000, "BTC", "B", 0.20, 80, -0.20, pnl=2.0, fee=0.4)]
# ETH: two fills that reduce a position opened before the history (skipped), then a liquidation
F += [fill(1500, "ETH", "A", 1.0, 2000, 2.0, pnl=50.0, fee=1.0),
      fill(2500, "ETH", "A", 1.0, 2100, 1.0, pnl=100.0, fee=1.0),
      fill(3500, "ETH", "B", 2.0, 2000, 0.0, fee=2.0),
      fill(4500, "ETH", "A", 2.0, 1800, 2.0, pnl=-400.0, fee=2.0, liq=True)]
# SOL: short with an add (averaging down), two closes; funding inside the trip
F += [fill(1000, "SOL", "A", 10, 50, 0, fee=0.5),
      fill(2000, "SOL", "A", 10, 55, -10, fee=0.5),
      fill(3000, "SOL", "B", 5, 48, -20, pnl=22.5, fee=0.25),
      fill(4000, "SOL", "B", 15, 60, -15, pnl=-112.5, fee=0.75)]
# 12 simple open/close trips (24 fills) alternating win/loss on the three coins
t = 10_000
simple = []
for i in range(12):
    coin = ["BTC", "ETH", "SOL"][i % 3]
    px = {"BTC": 100.0, "ETH": 2000.0, "SOL": 50.0}[coin]
    win = i % 2 == 0
    side = "B" if i % 4 < 2 else "A"
    start_sign = 1 if side == "B" else -1
    exit_px = px * (1.02 if (win == (side == "B")) else 0.98)
    pnl = (exit_px - px) * start_sign * 1.0
    F.append(fill(t, coin, side, 1.0, px, 0.0, fee=0.1, tid=t))
    F.append(fill(t + 500, coin, "A" if side == "B" else "B", 1.0, exit_px, start_sign * 1.0, pnl=pnl, fee=0.1, tid=t + 500))
    simple.append((coin, "long" if side == "B" else "short", pnl - 0.2, t))
    t += 1000

print(f"fixture: {len(F)} fills")
check("fixture has 40 fills", len(F), 40)
funding = [{"time": 3500, "coin": "SOL", "usdc": 1.5}]
out = build_trips(F, funding)
trips = out["trips"]
by_key = {(x["coin"], x["open_time"]): x for x in trips}

print("round trips")
check("closed trips", len(trips), 5 + 12)
check("skipped pre-history reducing fills", out["skipped_pre"], 2)
check("nothing left open", out["open"], {})

t1 = by_key[("BTC", 1000)]
check("BTC trip 1 side", t1["side"], "long")
check("BTC trip 1 fills / adds / closes", (t1["n_fills"], t1["n_adds"], t1["n_closes"]), (5, 2, 2))
check("BTC trip 1 averaged down + added to winner", (t1["averaged_down"], t1["added_to_winner"]), (True, True))
check("BTC trip 1 avg entry", t1["avg_entry"], 101.25)
check("BTC trip 1 avg exit", t1["avg_exit"], 125.0)
check("BTC trip 1 max size", t1["max_size"], 0.20)
check("BTC trip 1 max size usd", t1["max_size_usd"], 19.0)
check("BTC trip 1 gross pnl", t1["pnl_gross"], 4.75)
check("BTC trip 1 fees", t1["fees"], 3.0)
check("BTC trip 1 net pnl", t1["pnl_usd"], 1.75)
check("BTC trip 1 maker fills", t1["maker_fills"], 1)
check("BTC trip 1 entry taker", t1["entry_taker"], True)
check("BTC trip 1 close time", t1["close_time"], 5000)
check("BTC trip 1 return pct", t1["ret_pct"], (125 - 101.25) / 101.25 * 100, 1e-9)
check("BTC trip 1 adverse excursion from fills", t1["mae_pct"], (101.25 - 95) / 101.25 * 100, 1e-9)

t2 = by_key[("BTC", 6000)]
check("flip: long trip closed by the flip fill", (t2["side"], t2["close_time"], t2["n_fills"]), ("long", 7000, 2))
check("flip: closed part carries the closedPnl", t2["pnl_gross"], -1.0)
check("flip: fee pro-rated (1.0 + 0.5 of 1.5)", t2["fees"], 1.5)
check("flip: net", t2["pnl_usd"], -2.5)
t3 = by_key[("BTC", 7000)]
check("flip: short trip opened by the same fill", (t3["side"], t3["open_time"], t3["close_time"]), ("short", 7000, 8000))
check("flip: short entry qty 0.20", t3["entry_qty"], 0.2)
check("flip: short entry at 90", t3["avg_entry"], 90.0)
check("flip: short fee 1.0 open part + 0.4 close", t3["fees"], 1.4)
check("flip: short net", t3["pnl_usd"], 0.6)
check("flip: short max size usd", t3["max_size_usd"], 18.0)

t4 = by_key[("ETH", 3500)]
check("ETH liquidation flagged", t4["liquidation"], True)
check("ETH liquidation net", t4["pnl_usd"], -404.0)
check("ETH pre-history pnl not counted", sum(x["pnl_gross"] for x in trips if x["coin"] == "ETH" and x["open_time"] < 10_000), -400.0)

t5 = by_key[("SOL", 1000)]
check("SOL short averaged down", (t5["side"], t5["averaged_down"], t5["n_adds"]), ("short", True, 1))
check("SOL avg entry", t5["avg_entry"], 52.5)
check("SOL avg exit", t5["avg_exit"], (5 * 48 + 15 * 60) / 20)
check("SOL gross", t5["pnl_gross"], -90.0)
check("SOL funding inside the trip", t5["funding"], 1.5)
check("SOL net = gross - fees + funding", t5["pnl_usd"], -90.0 - 2.0 + 1.5)
check("SOL hold seconds", t5["hold_sec"], 3)

print("simple trips")
for coin, side, net, t0 in simple:
    x = by_key[(coin, t0)]
    check(f"{coin} {side} @ {t0}", (x["side"], round(x["pnl_usd"], 6), x["n_fills"]), (side, round(net, 6), 2))

print("invariants")
counted = sum(f["closedPnl"] for f in F) - 150.0   # minus the two pre-history fills
check("sum of trip gross pnl equals counted fill closedPnl", round(sum(x["pnl_gross"] for x in trips), 6), round(counted, 6))
check("sum of trip fees equals counted fees", round(sum(x["fees"] for x in trips), 6), round(sum(f["fee"] for f in F) - 2.0, 6))
check("trips ordered by close time", [x["close_time"] for x in trips], sorted(x["close_time"] for x in trips))

print("replay (hand check)")
r = replay([t1], margin_usd=50, leverage=3, stop_pct=None, perpl_coins={"BTC"}, now_ms=6000, fee_bps=6.9, slip_bps=3)
want = 150 * ((t1["ret_pct"] - 0.03) / 100) - 150 * 6.9 / 1e4
check("one trip at $50 x3 no stop", r["pnl"], round(want, 2), 0.011)
r2 = replay([t1], margin_usd=50, leverage=3, stop_pct=5.0, perpl_coins={"BTC"}, now_ms=6000)
check("stop at 5% hits (mae 6.17%)", (r2["stopped_out"], r2["pnl"]), (1, round(-150 * 0.05 - 150 * 6.9 / 1e4, 2)))
r3 = replay([t1], margin_usd=50, leverage=3, stop_pct=None, perpl_coins=set(), now_ms=6000)
check("coin not on Perpl is skipped", (r3["skipped"], r3["trades"]), (1, 0))

n = sum(1 for c in checks if not c)
print(f"\n{len(checks) - n} passed, {n} failed")
sys.exit(1 if n else 0)
