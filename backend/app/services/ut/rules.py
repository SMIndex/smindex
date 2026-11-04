"""Unusual Timing arithmetic, no I/O (spec sections 4 to 7). Everything the
acceptance tests check by hand lives here: entry merging, forward moves,
per-coin thresholds, base rates, the binomial tail, the moved-market flag,
signals S1 to S7, the score and the level, and the cluster components.

Times are epoch milliseconds, prices floats, percentages in percent.
"""
from __future__ import annotations

import bisect
import math
from collections import defaultdict
from statistics import median

MERGE_GAP_MS = 60_000           # entries less than 60 s apart, same coin and side, are one entry
H30 = 30 * 60_000
H2 = 2 * 3_600_000
H24 = 24 * 3_600_000
HORIZONS = {"30m": H30, "2h": H2, "24h": H24}
THRESH_FLOOR = 3.0
THRESH_CAP = 10.0
MOVED_MARKET_SHARE = 0.20       # entry notional over 20 % of the coin's 2 h volume
MIN_ENTRIES = 10
MIN_HITS = 4
WEIGHTS = {"s1": 35, "s2": 25, "s3": 15, "s4": 10, "s5": 5, "s6": 10, "s7": 5}
WEIGHT_TOTAL = sum(WEIGHTS.values())
LEVELS = (("cluster", 85.0), ("strong", 70.0), ("watch", 50.0))
EPS = 1e-12


# ---------------------------------------------------------------------------
# entries
# ---------------------------------------------------------------------------

def is_spot_coin(coin: str) -> bool:
    return coin.startswith("@") or "/" in coin


def opening_pieces(fill: dict) -> list[dict]:
    """The part of one fill that opens or adds to a position, as 0 or 1
    piece {time, coin, side, px, sz, is_add, tid}. A buy with a flat or long
    start position opens or adds long; a sell with a flat or short start
    position opens or adds short; a fill that crosses zero (a flip) opens the
    part beyond the old position on the new side. Closing fills give nothing.
    Keys follow hl_fills (side 'B' or 'A', start_position signed)."""
    coin = str(fill.get("coin") or "")
    if is_spot_coin(coin):
        return []
    sz = float(fill.get("sz") or 0)
    px = float(fill.get("px") or 0)
    if sz <= EPS or px <= EPS:
        return []
    sp = float(fill.get("start_position") or 0)
    buy = str(fill.get("side") or "").upper().startswith("B")
    t = int(fill.get("time_ms") or fill.get("time") or 0)
    tid = int(fill.get("tid") or 0)
    if buy:
        if sp >= -EPS:
            return [{"time": t, "coin": coin, "side": "long", "px": px, "sz": sz, "is_add": sp > EPS, "tid": tid}]
        over = sz - abs(sp)
        return [{"time": t, "coin": coin, "side": "long", "px": px, "sz": over, "is_add": False, "tid": tid}] if over > EPS else []
    if sp <= EPS:
        return [{"time": t, "coin": coin, "side": "short", "px": px, "sz": sz, "is_add": sp < -EPS, "tid": tid}]
    over = sz - abs(sp)
    return [{"time": t, "coin": coin, "side": "short", "px": px, "sz": over, "is_add": False, "tid": tid}] if over > EPS else []


def merge_entries(fills: list[dict]) -> list[dict]:
    """One wallet's fills (any order, any coins) to merged entries, oldest
    first. Opening pieces of the same coin and side chain into one entry
    while each piece is within MERGE_GAP_MS of the previous piece; the
    opposite side, another coin, or a longer gap starts a new entry. Entry
    price is the notional-weighted average, time is the first piece's time."""
    pieces: list[dict] = []
    for f in fills:
        pieces.extend(opening_pieces(f))
    pieces.sort(key=lambda p: (p["time"], p["tid"]))
    last: dict[tuple[str, str], dict] = {}
    out: list[dict] = []
    for p in pieces:
        key = (p["coin"], p["side"])
        cur = last.get(key)
        if cur is not None and p["time"] - cur["_last_t"] <= MERGE_GAP_MS:
            cur["notional"] += p["px"] * p["sz"]
            cur["size"] += p["sz"]
            cur["n_fills"] += 1
            cur["_last_t"] = p["time"]
            continue
        e = {"wallet": None, "coin": p["coin"], "side": p["side"], "time": p["time"], "size": p["sz"],
             "notional": p["px"] * p["sz"], "is_add": bool(p["is_add"]), "n_fills": 1, "_last_t": p["time"]}
        out.append(e)
        last[key] = e
    for e in out:
        e["px"] = e["notional"] / e["size"] if e["size"] > EPS else 0.0
        e.pop("_last_t", None)
    out.sort(key=lambda e: e["time"])
    return out


# ---------------------------------------------------------------------------
# forward moves from candles
# ---------------------------------------------------------------------------

def sorted_candles(candles: list[dict]) -> tuple[list[dict], list[int]]:
    """Bars sorted by start time plus the list of start times, for the
    bisect fast path of forward_moves and volume_after."""
    rows = sorted(candles, key=lambda c: int(c["t"]))
    return rows, [int(c["t"]) for c in rows]


def window(candles: list[dict], ts: list[int] | None, t0: int, t1: int) -> list[dict]:
    """Bars with t0 <= t < t1. With ts (from sorted_candles) it is a bisect
    slice; without, a scan of unsorted input."""
    if ts is not None:
        return candles[bisect.bisect_left(ts, t0):bisect.bisect_left(ts, t1)]
    return sorted((c for c in candles if t0 <= int(c["t"]) < t1), key=lambda c: int(c["t"]))


def volume_after(candles: list[dict], t0: int, horizon_ms: int = H2, ts: list[int] | None = None, venue_only: bool = False) -> float | None:
    """USD volume of the bars in the horizon after t0. With venue_only, only
    bars read from the venue (src 'hl') count and the window must be made
    of them entirely; otherwise None (unknown volume never flags)."""
    win = window(candles, ts, t0, t0 + horizon_ms)
    if not win:
        return None
    if venue_only and any(c.get("src", "hl") != "hl" for c in win):
        return None
    return sum(float(c.get("v") or 0) * float(c["c"]) for c in win)


def forward_moves(entry_px: float, side: str, t0: int, candles: list[dict], ts: list[int] | None = None) -> dict:
    """MFE and close-to-close return at 30 m, 2 h and 24 h after t0, in
    percent in the trade's direction, from candles {t, T, h, l, c} (ms, start
    and end). Only candles that START at or after t0 count, so the bar that
    holds the entry (and the prices before it) is never credited; a horizon
    with no such bar inside it gives None. peak_at_2h is the start time of
    the bar holding the 2 h extreme. Pass ts from sorted_candles to skip the
    per-call scan."""
    out = {"mfe_30m": None, "mfe_2h": None, "mfe_24h": None, "ret_30m": None, "ret_2h": None, "ret_24h": None, "peak_at_2h": None}
    if entry_px <= EPS:
        return out
    long = side == "long"
    rows = window(candles, ts, t0, t0 + H24)
    for name, h in HORIZONS.items():
        win = [c for c in rows if int(c["t"]) < t0 + h]
        if not win:
            continue
        if long:
            best = max(win, key=lambda c: float(c["h"]))
            mfe = (float(best["h"]) / entry_px - 1.0) * 100.0
        else:
            best = min(win, key=lambda c: float(c["l"]))
            mfe = (1.0 - float(best["l"]) / entry_px) * 100.0
        out["mfe_" + name] = round(max(0.0, mfe), 4)
        if name == "2h":
            out["peak_at_2h"] = int(best["t"])
        closed = [c for c in win if int(c.get("T") or c["t"]) <= t0 + h]
        if closed:
            last = closed[-1]
            r = (float(last["c"]) / entry_px - 1.0) * 100.0
            out["ret_" + name] = round(r if long else -r, 4)
    return out


def is_hit(mfe_2h: float | None, threshold_pct: float, moved_market_flag: bool = False) -> bool:
    """A hit is a big move within 2 h in the trade's direction that the
    entry did not make itself (moved-market entries count separately)."""
    return (not moved_market_flag) and mfe_2h is not None and mfe_2h >= threshold_pct


MM_MAKER_SHARE = 0.60
MM_HOLD_SEC = 600


def exclusion(*, maker_share: float | None, median_hold_sec: float | None, is_vault: bool, featured_mm: bool = False) -> str | None:
    """Why a wallet is left out before scoring: market maker (maker share
    over 60 % or median hold under 10 min, or the featured pipeline's own
    flag) or vault. None when it is scored."""
    if is_vault:
        return "vault"
    if featured_mm:
        return "market_maker"
    if maker_share is not None and maker_share >= MM_MAKER_SHARE:
        return "market_maker"
    if median_hold_sec is not None and median_hold_sec < MM_HOLD_SEC:
        return "market_maker"
    return None


def moved_market(notional_usd: float, volume_2h_usd: float | None) -> bool:
    """The entry may be the move itself: its notional is over 20 % of the
    coin's volume in the 2 h after it. Unknown volume never flags."""
    if volume_2h_usd is None or volume_2h_usd <= EPS:
        return False
    return notional_usd > MOVED_MARKET_SHARE * volume_2h_usd


# ---------------------------------------------------------------------------
# per-coin statistics from 1 h candles
# ---------------------------------------------------------------------------

def median_2h_range_pct(c1h: list[dict]) -> float | None:
    rows = sorted(c1h, key=lambda c: int(c["t"]))
    vals = []
    for i in range(len(rows) - 1):
        base = float(rows[i]["c"])
        if base <= EPS or int(rows[i + 1]["t"]) - int(rows[i]["t"]) != 3_600_000:
            continue          # a gap (bars built from sparse fills): not a 2 h range
        hi = max(float(rows[i]["h"]), float(rows[i + 1]["h"]))
        lo = min(float(rows[i]["l"]), float(rows[i + 1]["l"]))
        vals.append((hi - lo) / base * 100.0)
    return median(vals) if vals else None


def threshold_pct(coin: str, med_2h_range: float | None) -> float:
    """Big-move threshold (MFE within 2 h): 3 % for BTC and ETH, 8 % for a
    coin whose 90-day median 2 h range is above 4 %, 5 % otherwise; floor 3,
    cap 10. A coin with no history gets 5 %."""
    base = coin.split(":")[-1].upper()
    if base in ("BTC", "ETH"):
        thr = 3.0
    elif med_2h_range is not None and med_2h_range > 4.0:
        thr = 8.0
    else:
        thr = 5.0
    return clamp_threshold(thr)


def clamp_threshold(thr: float) -> float:
    return max(THRESH_FLOOR, min(THRESH_CAP, float(thr)))


def big_moves(c1h: list[dict], thr: float) -> tuple[list[dict], int]:
    """Hourly points from which an entry would have been a hit: for bar i
    (entry at its close), up when max high of bars i+1..i+2 is thr % above
    the close, down when min low is thr % below. Returns ([{t, direction,
    magnitude}], bars_considered). t is the start of bar i+1, the first bar
    an entry at the close of i is credited with."""
    rows = sorted(c1h, key=lambda c: int(c["t"]))
    out = []
    n = 0
    for i in range(len(rows) - 2):
        base = float(rows[i]["c"])
        t_i = int(rows[i]["t"])
        if base <= EPS or int(rows[i + 1]["t"]) != t_i + 3_600_000 or int(rows[i + 2]["t"]) != t_i + 7_200_000:
            continue          # only hours followed by the two real next hours count (fills-built bars leave gaps)
        n += 1
        nxt = rows[i + 1:i + 3]
        up = (max(float(c["h"]) for c in nxt) / base - 1.0) * 100.0
        dn = (1.0 - min(float(c["l"]) for c in nxt) / base) * 100.0
        if up >= thr:
            out.append({"t": int(rows[i + 1]["t"]), "direction": "up", "magnitude": round(up, 4)})
        if dn >= thr:
            out.append({"t": int(rows[i + 1]["t"]), "direction": "down", "magnitude": round(dn, 4)})
    return out, n


def base_rate(c1h: list[dict], thr: float) -> tuple[float, int, int, int]:
    """Chance that a random entry of a random side is followed by a big move
    within 2 h: (hits up + hits down) / (2 x bars). Returns (rate, bars,
    up_count, down_count)."""
    moves, n = big_moves(c1h, thr)
    up = sum(1 for m in moves if m["direction"] == "up")
    dn = len(moves) - up
    if n <= 0:
        return 0.0, 0, 0, 0
    return (up + dn) / (2.0 * n), n, up, dn


def regime(c1h: list[dict]) -> str:
    """'trend' when the latest 7-day absolute return is at or above the 75th
    percentile of the coin's own 7-day absolute returns (daily steps), else
    'range'."""
    rows = sorted(c1h, key=lambda c: int(c["t"]))
    closes = [float(c["c"]) for c in rows]
    if len(closes) < 24 * 8:
        return "range"
    rets = []
    for i in range(24 * 7, len(closes), 24):
        a, b = closes[i - 24 * 7], closes[i]
        if a > EPS:
            rets.append(abs(b / a - 1.0))
    if len(rets) < 4:
        return "range"
    cur = abs(closes[-1] / closes[-1 - 24 * 7] - 1.0) if closes[-1 - 24 * 7] > EPS else 0.0
    srt = sorted(rets)
    q75 = srt[min(len(srt) - 1, int(0.75 * (len(srt) - 1)))]
    return "trend" if cur >= q75 and cur > 0 else "range"


# ---------------------------------------------------------------------------
# probability
# ---------------------------------------------------------------------------

def binom_sf(k: int, n: int, p: float) -> float:
    """P(X >= k) for X ~ Binomial(n, p), summed in log space. k <= 0 gives
    1, k > n gives 0, p at 0 or 1 handled exactly."""
    if k <= 0:
        return 1.0
    if k > n:
        return 0.0
    if p <= 0.0:
        return 0.0
    if p >= 1.0:
        return 1.0
    lp, lq = math.log(p), math.log1p(-p)
    total = 0.0
    for i in range(k, n + 1):
        total += math.exp(math.lgamma(n + 1) - math.lgamma(i + 1) - math.lgamma(n - i + 1) + i * lp + (n - i) * lq)
    return min(1.0, max(0.0, total))


# ---------------------------------------------------------------------------
# signals, score, level
# ---------------------------------------------------------------------------

def signals(w: dict) -> dict:
    """S1 to S7 from one wallet's aggregates:

      entries, hits, blended_base_rate                       (S1)
      events_preceded, event_kinds                           (S2)
      net_profit, top3_profit, median_hold_min               (S3)
      age_days_at_hit (min over hit entries, None = unknown),
      first_position_usd, candidates_median_first_position   (S4)
      size_shock_on_hit (bool)                               (S5)
      co_entries_flagged, shared_funder_flagged (bool)       (S6)
      exit_speed_hits                                        (S7)

    Returns flags s1..s7, p_value, hit_rate, score (0 to 100), level and an
    'insufficient' reason when the sample is too small. S1 is required for
    any level."""
    n = int(w.get("entries") or 0)
    k = int(w.get("hits") or 0)
    p = float(w.get("blended_base_rate") or 0.0)
    out: dict = {"hit_rate": (k / n if n else None), "p_value": None}
    if n < MIN_ENTRIES or k < MIN_HITS:
        out.update({f"s{i}": False for i in range(1, 8)})
        out.update({"score": 0.0, "level": "none", "insufficient": f"{k} hits on {n} entries"})
        return out
    out["p_value"] = binom_sf(k, n, p) if p > 0 else (0.0 if k > 0 else 1.0)
    hr = k / n
    s1 = n >= 4 and p > 0 and hr > 3.0 * p and out["p_value"] < 0.001
    s2 = int(w.get("events_preceded") or 0) >= 3 and int(w.get("event_kinds") or 0) >= 2
    net = float(w.get("net_profit") or 0.0)
    top3 = float(w.get("top3_profit") or 0.0)
    hold = w.get("median_hold_min")
    s3 = net > 0 and top3 / net > 0.70 and hold is not None and float(hold) < 360.0
    age = w.get("age_days_at_hit")
    first_pos = w.get("first_position_usd")
    med_first = w.get("candidates_median_first_position")
    s4 = (age is not None and float(age) < 7.0 and first_pos is not None
          and (float(first_pos) > 100_000.0 or (med_first is not None and float(med_first) > 0 and float(first_pos) > 10.0 * float(med_first))))
    s5 = bool(w.get("size_shock_on_hit"))
    s6 = int(w.get("co_entries_flagged") or 0) >= 3 or bool(w.get("shared_funder_flagged"))
    s7 = int(w.get("exit_speed_hits") or 0) >= 3
    flags = {"s1": s1, "s2": s2, "s3": s3, "s4": s4, "s5": s5, "s6": s6, "s7": s7}
    out.update(flags)
    score = round(sum(WEIGHTS[f] for f, on in flags.items() if on) / WEIGHT_TOTAL * 100.0, 2) if s1 else 0.0
    out["score"] = score
    out["level"] = level_for(score, in_cluster_of_3=bool(w.get("in_cluster_of_3")))
    return out


def level_for(score: float, *, in_cluster_of_3: bool = False) -> str:
    if score >= 85.0:
        return "cluster"
    if score >= 70.0:
        return "cluster" if in_cluster_of_3 else "strong"
    if score >= 50.0:
        return "watch"
    return "none"


LEVEL_WORDS = {
    "watch": "Unusual timing on several trades. Could be skill.",
    "strong": "Repeated timing before events, plus a fresh wallet, size shock or burst profile.",
    "cluster": "Several wallets moving together with the same pattern.",
    "none": "Not listed.",
}


# ---------------------------------------------------------------------------
# clusters
# ---------------------------------------------------------------------------

CROWD_MIN = 4               # this many distinct wallets inside the window = a crowd, not a pair


def mark_crowds(entries: list[dict], window_ms: int = 60_000, crowd_min: int = CROWD_MIN) -> int:
    """Sets e['crowd'] = True on every entry that has at least crowd_min
    distinct wallets (itself included) entering the same coin and side within
    window_ms before or after it. Returns the number marked. A move that
    pulls many wallets in at once says nothing about any two of them."""
    by_key: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for e in entries:
        e["crowd"] = False
        by_key[(e["coin"], e["side"])].append(e)
    marked = 0
    for rows in by_key.values():
        rows.sort(key=lambda e: e["time"])
        lo = 0
        for i, e in enumerate(rows):
            while rows[lo]["time"] < e["time"] - window_ms:
                lo += 1
            hi = i
            while hi + 1 < len(rows) and rows[hi + 1]["time"] - e["time"] <= window_ms:
                hi += 1
            if len({r["wallet"] for r in rows[lo:hi + 1]}) >= crowd_min:
                e["crowd"] = True
                marked += 1
    return marked


def co_entry_pairs(entries: list[dict], window_ms: int = 60_000) -> list[tuple]:
    """(wallet_a, wallet_b, coin, side, time_a, time_b, on_event, crowd) for
    every pair of entries by different wallets, same coin and side, within
    window_ms. entries carry wallet, coin, side, time, event_id and, after
    mark_crowds, crowd."""
    by_key: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for e in entries:
        by_key[(e["coin"], e["side"])].append(e)
    out = []
    for (coin, side), rows in by_key.items():
        rows.sort(key=lambda e: e["time"])
        for i, a in enumerate(rows):
            j = i + 1
            while j < len(rows) and rows[j]["time"] - a["time"] <= window_ms:
                b = rows[j]
                if b["wallet"] != a["wallet"]:
                    wa, wb = sorted((a["wallet"], b["wallet"]))
                    ta, tb = (a["time"], b["time"]) if wa == a["wallet"] else (b["time"], a["time"])
                    on_ev = bool(a.get("event_id") or b.get("event_id"))
                    out.append((wa, wb, coin, side, ta, tb, on_ev, bool(a.get("crowd") or b.get("crowd"))))
                j += 1
    return out


CHANCE_MULT = 5.0           # observed co-entries must be at least this many times what chance predicts
EDGE_MIN_FINAL = 5          # and at least this many in total once the baseline test is applied
BASELINE_FLOOR = 1.0        # a day-shift baseline below one co-entry counts as one


def day_shift_baselines(entries: list[dict], edges: dict, runs: int = 4, seed: int = 20261104, shift_ms: int = 86_400_000) -> dict:
    """How many co-entries each edge's pair makes when one wallet's entries
    are moved by a day (same time of day, same coins, same habits): the
    co-entries the two produce without being together. `runs` random
    halvings each shift half the wallets; a pair gets a baseline from the
    runs that shift exactly one of its two wallets (missed by all runs with
    probability 1 / 2 ** runs: then None). {(a, b): mean count or None}."""
    import random
    wallets = sorted({e["wallet"] for e in entries})
    base: dict = {k: [] for k in edges}
    for k in range(runs):
        rnd = random.Random(seed + k)
        par = {w: rnd.randint(0, 1) for w in wallets}
        shifted = [dict(e, time=e["time"] + (shift_ms if par[e["wallet"]] else 0)) for e in entries]
        mark_crowds(shifted)
        cnt: dict = {}
        for p in co_entry_pairs(shifted):
            if not p[7]:
                cnt[(p[0], p[1])] = cnt.get((p[0], p[1]), 0) + 1
        for (a, b) in edges:
            if par.get(a) != par.get(b):
                base[(a, b)].append(cnt.get((a, b), 0))
    return {k: (sum(v) / len(v) if v else None) for k, v in base.items()}


def edges_after_baseline(edges: dict, baselines: dict) -> dict:
    """Keeps an edge when its co-entries are at least EDGE_MIN_FINAL and at
    least CHANCE_MULT times its day-shift baseline (floor BASELINE_FLOOR);
    an edge with no baseline must still reach EDGE_MIN_FINAL."""
    out = {}
    for k, v in edges.items():
        bl = baselines.get(k)
        if v["n"] < EDGE_MIN_FINAL and v["on_event"] < 2:
            continue
        if bl is not None and v["n"] < CHANCE_MULT * max(bl, BASELINE_FLOOR):
            continue
        out[k] = dict(v, baseline=bl)
    return out


def expected_coentries(counts_a: dict, counts_b: dict, window_ms: int = 60_000, span_ms: int = 90 * 86_400_000) -> float:
    """Co-entries two independent wallets would produce by chance: per coin
    and side, n_a x n_b x (2 x window / span), summed. counts are
    {(coin, side): entries}."""
    tot = 0.0
    for k, na in counts_a.items():
        nb = counts_b.get(k)
        if nb:
            tot += na * nb * (2.0 * window_ms / span_ms)
    return tot


def coentry_edges(pairs: list[tuple], min_total: int = 3, min_on_event: int = 2,
                  counts: dict | None = None) -> dict[tuple[str, str], dict]:
    """Pairs to graph edges: kept with 3 or more co-entries, or 2 or more on
    events, AND (when per-wallet entry counts are given) at least CHANCE_MULT
    times the co-entries chance predicts for two wallets that busy on the same
    coins. Two active traders on BTC co-enter inside 60 s many times a quarter
    without knowing each other; the chance test keeps them apart (2026-11-04:
    without it 148 flagged wallets fell into one component).
    {(a, b): {n, on_event, expected}}."""
    agg: dict[tuple[str, str], dict] = {}
    for p in pairs:
        wa, wb, on_ev = p[0], p[1], p[6]
        if len(p) > 7 and p[7]:
            continue              # a crowd co-entry counts for no pair
        d = agg.setdefault((wa, wb), {"n": 0, "on_event": 0, "expected": None})
        d["n"] += 1
        d["on_event"] += 1 if on_ev else 0
    out = {}
    for (a, b), v in agg.items():
        if not (v["n"] >= min_total or v["on_event"] >= min_on_event):
            continue
        if counts is not None:
            v["expected"] = expected_coentries(counts.get(a, {}), counts.get(b, {}))
            if v["n"] < CHANCE_MULT * v["expected"]:
                continue
        out[(a, b)] = v
    return out


def components(nodes: list[str], edges: list[tuple[str, str]]) -> list[list[str]]:
    """Connected components (union-find); singletons included, each sorted."""
    parent = {n: n for n in nodes}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for a, b in edges:
        if a in parent and b in parent:
            ra, rb = find(a), find(b)
            if ra != rb:
                parent[rb] = ra
    groups: dict[str, list[str]] = defaultdict(list)
    for n in nodes:
        groups[find(n)].append(n)
    return [sorted(g) for g in groups.values()]
