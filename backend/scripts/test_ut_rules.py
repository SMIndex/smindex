"""Unusual Timing acceptance tests 1 and 2 (unit, no database):

  1. entry merging, MFE and returns against hand-computed candles, per-coin
     threshold floor and cap, base rate on a synthetic series, binomial
     p-value against scipy (when installed), moved-market flag
  2. S1 to S7 on crafted wallets: a lucky 3-hit wallet (not listed), a 6-hit
     fresh wallet (Strong), a market maker with great timing (excluded), a
     wallet whose own size is the move (moved-market, not a hit), two wallets
     with 4 co-entries (one cluster)

    python scripts/test_ut_rules.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.services.ut import rules  # noqa: E402

checks = []


def check(name, got, want):
    ok = got == want
    checks.append(ok)
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}: got {got!r} want {want!r}")


def close(name, got, want, tol=1e-6):
    ok = got is not None and want is not None and abs(got - want) <= tol
    checks.append(ok)
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}: got {got!r} want {want!r}")


T0 = 1_790_000_000_000
M = 60_000


def fill(t, coin, side, px, sz, sp, tid=None):
    return {"time": t, "coin": coin, "side": side, "px": px, "sz": sz, "start_position": sp, "tid": tid or t}


print("1a. entry merging")
fills = [
    fill(T0, "BTC", "B", 100.0, 1.0, 0.0),            # open long
    fill(T0 + 59_000, "BTC", "B", 102.0, 1.0, 1.0),   # 59 s later, add: same entry
    fill(T0 + 118_000, "BTC", "B", 104.0, 2.0, 2.0),  # 59 s after the previous piece: still the same entry
    fill(T0 + 300_000, "BTC", "A", 110.0, 1.0, 4.0),  # close part: no entry
    fill(T0 + 400_000, "BTC", "A", 111.0, 5.0, 3.0),  # sell 5 with 3 long: closes 3, opens short 2 (flip)
    fill(T0 + 400_000 + 61_000, "BTC", "A", 111.0, 1.0, -2.0),  # 61 s later: a new short entry
    fill(T0 + 500_000, "ETH", "B", 10.0, 3.0, 0.0),   # other coin
    fill(T0 + 500_000, "@107", "B", 1.0, 3.0, 0.0),   # spot: ignored
]
ents = rules.merge_entries(fills)
check("entries count", len(ents), 4)
e0 = ents[0]
check("first entry side/coin", (e0["coin"], e0["side"], e0["n_fills"]), ("BTC", "long", 3))
close("first entry size", e0["size"], 4.0)
close("first entry px (notional weighted)", e0["px"], (100 + 102 + 208) / 4.0)
check("first entry is_add", e0["is_add"], False)
e1 = ents[1]
check("flip opens the part beyond the old position", (e1["side"], round(e1["size"], 8), e1["is_add"]), ("short", 2.0, False))
e2 = ents[2]
check("61 s later is a new entry", (e2["side"], e2["size"], e2["is_add"], e2["time"]), ("short", 1.0, True, T0 + 461_000))
check("other coin is its own entry", (ents[3]["coin"], ents[3]["size"]), ("ETH", 3.0))

print("1b. MFE and returns against hand-computed candles")
# 1 m bars from T0 - 1 m; entry at T0 + 30 s inside the second bar (which must not count)
def bar(t, o, h, l, c, v=10.0):
    return {"t": t, "T": t + M - 1, "o": o, "h": h, "l": l, "c": c, "v": v}
candles = [bar(T0 - M, 100, 130, 99, 100), bar(T0, 100, 125, 98, 101)]   # second bar holds the entry: excluded
for i in range(1, 24 * 60 + 5):
    px = 100 + (3 if i == 10 else 0) + (5 if i == 90 else 0) + (9 if i == 600 else 0)
    candles.append(bar(T0 + i * M, px, px + 0.5, px - 0.5, px))
fm = rules.forward_moves(100.0, "long", T0 + 30_000, candles)
close("mfe_30m long = bar 10 high 103.5", fm["mfe_30m"], 3.5, 1e-4)
close("mfe_2h long = bar 90 high 105.5", fm["mfe_2h"], 5.5, 1e-4)
close("mfe_24h long = bar 600 high 109.5", fm["mfe_24h"], 9.5, 1e-4)
check("peak_at_2h is bar 90 start", fm["peak_at_2h"], T0 + 90 * M)
close("ret_2h long = close of last bar ending inside 2 h", fm["ret_2h"], 0.0, 1e-4)
fs = rules.forward_moves(100.0, "short", T0 + 30_000, candles)
close("mfe_2h short = 1 - min low / px (lows 99.5 in bars 1..)", fs["mfe_2h"], 0.5, 1e-4)
close("ret_30m short is the negated long return", fs["ret_30m"], -0.0, 1e-4)
fe = rules.forward_moves(100.0, "long", T0 + 30_000, candles[:2])
check("no bar after the entry: horizons None", (fe["mfe_30m"], fe["ret_2h"]), (None, None))

print("1c. per-coin threshold floor and cap")
check("BTC 3 %", rules.threshold_pct("BTC", 0.6), 3.0)
check("ETH 3 %", rules.threshold_pct("ETH", 1.2), 3.0)
check("alt default 5 %", rules.threshold_pct("SOL", 2.0), 5.0)
check("memecoin (median 2 h range over 4 %) 8 %", rules.threshold_pct("PUMP", 4.5), 8.0)
check("HIP-3 name uses the base symbol", rules.threshold_pct("xyz:BTC", 0.6), 3.0)
check("no history 5 %", rules.threshold_pct("NEW", None), 5.0)
check("floor 3", rules.clamp_threshold(1.0), 3.0)
check("cap 10", rules.clamp_threshold(14.0), 10.0)

print("1d. base rate on a synthetic 1 h series")
# 100 flat bars at 100, except one bar with a +6 % high at bar 50 and one bar with a -6 % low at bar 80
h1 = []
for i in range(100):
    hi = 106.0 if i == 50 else 100.5
    lo = 94.0 if i == 80 else 99.5
    h1.append({"t": T0 + i * 3_600_000, "T": T0 + (i + 1) * 3_600_000 - 1, "o": 100, "h": hi, "l": lo, "c": 100, "v": 1})
rate, bars, up, dn = rules.base_rate(h1, 5.0)
check("bars considered", bars, 98)
check("up moves: entries at the close of bars 48 and 49 see bar 50", up, 2)
check("down moves: entries at the close of bars 78 and 79 see bar 80", dn, 2)
close("rate = (2 + 2) / (2 x 98)", rate, 4 / 196.0)
moves, _ = rules.big_moves(h1, 5.0)
check("move times are the first credited bar", sorted(m["t"] for m in moves), [T0 + 49 * 3_600_000, T0 + 50 * 3_600_000, T0 + 79 * 3_600_000, T0 + 80 * 3_600_000])
med = rules.median_2h_range_pct(h1)
close("median 2 h range of the flat series = 1 %", med, 1.0, 1e-9)

print("1e. binomial tail")
try:
    from scipy.stats import binom
    for k, n, p in ((4, 10, 0.05), (6, 12, 0.03), (3, 30, 0.1), (0, 5, 0.2), (7, 7, 0.5), (1, 1, 0.001)):
        close(f"sf({k},{n},{p}) vs scipy", rules.binom_sf(k, n, p), float(binom.sf(k - 1, n, p)), 1e-12)
except ImportError:
    close("sf(4,10,0.05) known value", rules.binom_sf(4, 10, 0.05), 0.0010285, 1e-6)
    close("sf(6,12,0.03) known value", rules.binom_sf(6, 12, 0.03), 5.8e-7, 2e-7)
check("k <= 0 gives 1", rules.binom_sf(0, 10, 0.3), 1.0)
check("k > n gives 0", rules.binom_sf(11, 10, 0.3), 0.0)

print("1f. moved-market flag")
check("entry over 20 % of 2 h volume", rules.moved_market(250_000, 1_000_000), True)
check("entry at 20 % is not over", rules.moved_market(200_000, 1_000_000), False)
check("unknown volume never flags", rules.moved_market(250_000, None), False)
check("a moved-market entry is not a hit", rules.is_hit(9.0, 5.0, moved_market_flag=True), False)
check("a plain big move is a hit", rules.is_hit(5.0, 5.0), True)

print("2. signals on crafted wallets")
lucky = rules.signals({"entries": 12, "hits": 3, "blended_base_rate": 0.02, "events_preceded": 3, "event_kinds": 2})
check("lucky 3-hit wallet: insufficient, not listed", (lucky["level"], lucky["s1"], "insufficient" in lucky), ("none", False, True))

fresh = rules.signals({"entries": 12, "hits": 6, "blended_base_rate": 0.03, "events_preceded": 4, "event_kinds": 2,
                       "age_days_at_hit": 2.0, "first_position_usd": 150_000.0, "candidates_median_first_position": 8_000.0,
                       "exit_speed_hits": 3, "net_profit": 10_000.0, "top3_profit": 4_000.0, "median_hold_min": 900})
check("6-hit fresh wallet flags", (fresh["s1"], fresh["s2"], fresh["s3"], fresh["s4"], fresh["s5"], fresh["s6"], fresh["s7"]),
      (True, True, False, True, False, False, True))
close("6-hit fresh wallet score = 75 / 105", fresh["score"], round(75 / 105 * 100, 2), 1e-9)
check("6-hit fresh wallet level Strong", fresh["level"], "strong")
check("p-value under 0.001", fresh["p_value"] < 0.001, True)

skilled_only = rules.signals({"entries": 40, "hits": 12, "blended_base_rate": 0.03})
check("timing alone (S1 only) scores 33 and is not listed", (skilled_only["s1"], skilled_only["score"], skilled_only["level"]), (True, round(35 / 105 * 100, 2), "none"))

no_edge = rules.signals({"entries": 40, "hits": 5, "blended_base_rate": 0.08, "events_preceded": 5, "event_kinds": 3,
                         "age_days_at_hit": 1.0, "first_position_usd": 500_000.0, "size_shock_on_hit": True, "co_entries_flagged": 5})
check("without S1 nothing else lists the wallet", (no_edge["s1"], no_edge["score"], no_edge["level"]), (False, 0.0, "none"))

check("market maker with great timing is excluded", rules.exclusion(maker_share=0.8, median_hold_sec=3000, is_vault=False), "market_maker")
check("fast-hold wallet is excluded", rules.exclusion(maker_share=0.1, median_hold_sec=300, is_vault=False), "market_maker")
check("vault is excluded", rules.exclusion(maker_share=0.1, median_hold_sec=5000, is_vault=True), "vault")
check("ordinary wallet is scored", rules.exclusion(maker_share=0.1, median_hold_sec=5000, is_vault=False), None)

cl = rules.signals({"entries": 12, "hits": 6, "blended_base_rate": 0.03, "events_preceded": 4, "event_kinds": 2,
                    "age_days_at_hit": 2.0, "first_position_usd": 150_000.0, "co_entries_flagged": 4, "exit_speed_hits": 3,
                    "in_cluster_of_3": True})
check("Strong wallet in a cluster of 3 reads Cluster", (cl["level"], cl["score"] >= 70), ("cluster", True))

print("2b. two wallets with 4 co-entries form one cluster")
A, B, C = "0xa" + "1" * 39, "0xb" + "2" * 39, "0xc" + "3" * 39
ents = []
for i in range(4):
    base = T0 + i * 86_400_000
    ents.append({"wallet": A, "coin": "SOL", "side": "long", "time": base, "event_id": None})
    ents.append({"wallet": B, "coin": "SOL", "side": "long", "time": base + 40_000, "event_id": None})
ents.append({"wallet": C, "coin": "SOL", "side": "long", "time": T0 + 2 * 86_400_000 + 200_000, "event_id": None})   # 200 s away: not a co-entry
ents.append({"wallet": C, "coin": "SOL", "side": "short", "time": T0 + 50_000, "event_id": None})                     # opposite side: not a co-entry
pairs = rules.co_entry_pairs(ents)
check("4 co-entries between A and B only", (len(pairs), {(p[0], p[1]) for p in pairs}), (4, {(A, B)}))
edges = rules.coentry_edges(pairs)
check("edge kept (4 >= 3)", list(edges), [(A, B)])
comps = rules.components([A, B, C], list(edges))
check("one cluster of A and B, C alone", sorted(comps), sorted([[A, B], [C]]))
check("2 co-entries off events is no edge", rules.coentry_edges(pairs[:2]), {})
pairs_ev = [(A, B, "SOL", "long", T0, T0 + 1, True), (A, B, "SOL", "long", T0 + 86_400_000, T0 + 86_400_001, True)]
check("2 co-entries on events is an edge", list(rules.coentry_edges(pairs_ev)), [(A, B)])

n_ok, n = sum(checks), len(checks)
print(f"\n{n_ok}/{n} checks passed")
sys.exit(0 if n_ok == n else 1)
