"""Round trips, metrics, rules, score, verdict and the copy replay. Pure
Python over plain dicts: no database, no network, so the acceptance tests
(scripts/test_featured_roundtrips.py, test_featured_rules.py) run on fixtures
and the daily job runs the same code on the stored fills.

A round trip is open to flat on one coin. Position arithmetic uses the
venue's own startPosition on every fill plus the signed fill size, never the
dir string, so a flip ("Long > Short") splits into the close of one trip and
the open of the next, with the closedPnl on the closed part and the fee
pro-rated by size. Fills that reduce a position whose opening is before the
stored history are not approximated: they are counted in `skipped_pre` and
the first complete trip starts at the next open from flat.
"""
from __future__ import annotations

import math
import statistics
from collections import defaultdict
from datetime import datetime, timedelta, timezone

EPS = 1e-9
DAY_MS = 86_400_000
WINDOW_90_MS = 90 * DAY_MS
WINDOW_30_MS = 30 * DAY_MS
WINDOW_14_MS = 14 * DAY_MS
PF_CAP = 999.99

# Perpl taker OPEN fee, bps (verified 2026-08: T1 taker open 6.9 bps, close 0).
PERPL_TAKER_OPEN_BPS = 6.9
# Stand-in for "entry at the next trade price 1.5 s after the trader's fill":
# no tick history is stored, so the replay enters at the trader's average
# entry moved this many bps against the copier.
REPLAY_SLIP_BPS = 3.0

# Rule thresholds (spec, Qualification rules)
RULES = {
    "min_trips": 30, "min_days": 30, "recent_days": 14,
    "min_profit_usd": 500.0, "min_profit_pct_eq": 5.0,
    "wr_consistent": 0.70, "wr_asymmetric": 0.45, "asym_win_loss_ratio": 2.0,
    "pf_consistent": 1.5, "pf_asymmetric": 2.0,
    "worst_trade_pct_eq": 20.0, "max_dd_pct": 30.0,
    "mm_maker_share": 0.60, "mm_hold_sec": 600, "mm_pnl_per_volume": 0.0002,
    "farmer_pnl_per_volume": 0.0005,
    "bot_trips_per_day": 100, "bot_hold_sec": 120,
    "wash_counterparty_share": 0.20,
    "one_big_win_share": 0.50,
    "lev_penalty_above": 20.0,
}


def _f(v, d=0.0) -> float:
    try:
        return float(v) if v is not None else d
    except (TypeError, ValueError):
        return d


def ms_to_dt(ms: int) -> datetime:
    return datetime.fromtimestamp(ms / 1000, timezone.utc).replace(tzinfo=None)


def dt_to_ms(dt: datetime) -> int:
    return int(dt.replace(tzinfo=timezone.utc).timestamp() * 1000)


# ---------------------------------------------------------------------------
# round trips
# ---------------------------------------------------------------------------

def norm_fill(f: dict, account: str | None = None) -> dict:
    """One Hyperliquid fill (userFillsByTime shape, or an hl_fills row with the
    same keys) to the internal shape."""
    liq = f.get("liquidation")
    acct = (account or f.get("account") or "").lower()
    if isinstance(liq, dict):
        # the venue stamps BOTH sides of a liquidation; only the liquidated
        # account counts as liquidated (the other side merely filled it)
        who = str(liq.get("liquidatedUser") or "").lower()
        liq = (who == acct) if (acct and who) else bool(liq)
    return {
        "time": int(f.get("time") or f.get("time_ms") or 0),
        "coin": str(f.get("coin") or ""),
        "dir": str(f.get("dir") or ""),
        "side": str(f.get("side") or ""),
        "px": _f(f.get("px")),
        "sz": _f(f.get("sz")),
        "closed_pnl": _f(f.get("closedPnl", f.get("closed_pnl"))),
        "fee": _f(f.get("fee")),
        "crossed": (None if f.get("crossed") is None else bool(f.get("crossed"))),
        "start_position": _f(f.get("startPosition", f.get("start_position"))),
        "tid": int(f.get("tid") or 0),
        "oid": (int(f["oid"]) if f.get("oid") is not None else None),
        "hash": f.get("hash"),
        "liquidation": bool(liq),
        "account": acct or None,
    }


def _new_trip(coin: str, side: str, t: int) -> dict:
    return {
        "coin": coin, "side": side, "open_time": t, "close_time": None,
        "n_fills": 0, "n_adds": 0, "n_closes": 0, "maker_fills": 0,
        "entry_qty": 0.0, "entry_notional": 0.0, "exit_qty": 0.0, "exit_notional": 0.0,
        "max_size": 0.0, "max_size_usd": 0.0, "volume_usd": 0.0,
        "pnl_gross": 0.0, "fees": 0.0, "funding": 0.0,
        "entry_taker": None, "averaged_down": False, "added_to_winner": False, "liquidation": False,
        "px_min": math.inf, "px_max": -math.inf, "fills": [], "accounts": set(),
    }


def _apply_part(trip: dict, f: dict, qty: float, opening: bool) -> None:
    """Apply `qty` of fill `f` to the trip as an opening or closing part."""
    share = qty / f["sz"] if f["sz"] > EPS else 1.0
    notional = qty * f["px"]
    trip["n_fills"] += 1
    trip["fees"] += f["fee"] * share
    trip["volume_usd"] += notional
    if f["crossed"] is False:
        trip["maker_fills"] += 1
    if f["liquidation"]:
        trip["liquidation"] = True
    if f["account"]:
        trip["accounts"].add(f["account"])
    trip["px_min"] = min(trip["px_min"], f["px"])
    trip["px_max"] = max(trip["px_max"], f["px"])
    if opening:
        if trip["entry_qty"] > EPS:
            trip["n_adds"] += 1
            avg = trip["entry_notional"] / trip["entry_qty"]
            worse = f["px"] < avg if trip["side"] == "long" else f["px"] > avg
            if worse:
                trip["averaged_down"] = True
            else:
                trip["added_to_winner"] = True
        else:
            trip["entry_taker"] = f["crossed"]
        trip["entry_qty"] += qty
        trip["entry_notional"] += notional
    else:
        trip["n_closes"] += 1
        trip["exit_qty"] += qty
        trip["exit_notional"] += notional
        trip["pnl_gross"] += f["closed_pnl"]
    trip["fills"].append({"time": f["time"], "px": f["px"], "sz": qty, "side": f["side"],
                          "part": "open" if opening else "close", "fee": f["fee"] * share,
                          "crossed": f["crossed"], "tid": f["tid"], "closed_pnl": (0.0 if opening else f["closed_pnl"])})


def _finish(trip: dict, t: int) -> dict:
    trip["close_time"] = t
    eq, xq = trip["entry_qty"], trip["exit_qty"]
    avg_entry = trip["entry_notional"] / eq if eq > EPS else 0.0
    avg_exit = trip["exit_notional"] / xq if xq > EPS else avg_entry
    sign = 1.0 if trip["side"] == "long" else -1.0
    ret_pct = (avg_exit - avg_entry) / avg_entry * 100.0 * sign if avg_entry > EPS else 0.0
    # adverse / favourable excursion seen in the trip's own fill prices
    if avg_entry > EPS and trip["px_min"] != math.inf:
        if sign > 0:
            mae = (avg_entry - trip["px_min"]) / avg_entry * 100.0
            mfe = (trip["px_max"] - avg_entry) / avg_entry * 100.0
        else:
            mae = (trip["px_max"] - avg_entry) / avg_entry * 100.0
            mfe = (avg_entry - trip["px_min"]) / avg_entry * 100.0
        trip["mae_pct"], trip["mfe_pct"], trip["mae_src"] = max(0.0, mae), max(0.0, mfe), "fills"
    else:
        trip["mae_pct"] = trip["mfe_pct"] = None
        trip["mae_src"] = None
    trip["avg_entry"], trip["avg_exit"], trip["ret_pct"] = avg_entry, avg_exit, ret_pct
    trip["pnl_usd"] = trip["pnl_gross"] - trip["fees"] + trip["funding"]
    trip["hold_sec"] = max(0, (t - trip["open_time"]) // 1000)
    trip["accounts"] = sorted(trip["accounts"])
    for k in ("px_min", "px_max"):
        trip.pop(k, None)
    return trip


def build_trips(fills: list[dict], funding: list[dict] | None = None) -> dict:
    """fills: normalised (norm_fill) or raw venue fills, any order, any coins.
    Returns {"trips": [closed trips oldest first], "open": {coin: open trip
    state}, "skipped_pre": n}. Funding rows ({time, coin, usdc}) inside a
    trip's window are added to that trip (HL: positive usdc = received)."""
    rows = [f if "start_position" in f and "time" in f and "closed_pnl" in f else norm_fill(f) for f in fills]
    rows.sort(key=lambda r: (r["time"], r["tid"]))
    by_coin: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        # a Hyperliquid outcome market settles at price 0 (lost) or 1 (won)
        # with a "Settlement" fill: price 0 is a real close, never dropped
        if r["sz"] > EPS and (r["px"] > EPS or r["dir"] == "Settlement"):
            by_coin[r["coin"]].append(r)
    trips: list[dict] = []
    open_now: dict[str, dict] = {}
    skipped_pre = 0
    for coin, cf in by_coin.items():
        trip: dict | None = None
        pos_known = False
        pos = 0.0
        seen_flat = False          # a trip starts only after the coin has been flat once inside the stored history
        for f in cf:
            start = f["start_position"]
            if pos_known and abs(start - pos) > max(1e-6, abs(pos) * 1e-6):
                # the venue's start position disagrees with ours (a fill we
                # never stored): trust the venue and restart from it
                if trip is not None and abs(start) < EPS:
                    trip = None
                pos = start
            delta = f["sz"] if f["side"] == "B" else -f["sz"]
            end = start + delta
            if abs(end) < EPS:
                end = 0.0
            if not seen_flat and abs(start) < EPS:
                seen_flat = True
            close_qty = 0.0
            if abs(start) > EPS and (start > 0) != (delta > 0):
                close_qty = min(abs(start), abs(delta))
            open_qty = abs(delta) - close_qty
            if close_qty > EPS:
                if trip is None:
                    skipped_pre += 1
                else:
                    _apply_part(trip, f, close_qty, opening=False)
                if end == 0.0 or (start > 0) != (end > 0):
                    if trip is not None:
                        trips.append(_finish(trip, f["time"]))
                    trip = None
                    seen_flat = True
            if open_qty > EPS:
                side = "long" if end > 0 else "short"
                if trip is None and not seen_flat:
                    skipped_pre += 1        # adds to a position opened before the stored history
                else:
                    if trip is None:
                        trip = _new_trip(coin, side, f["time"])
                    _apply_part(trip, f, open_qty, opening=True)
            if trip is not None:
                trip["max_size"] = max(trip["max_size"], abs(end))
                trip["max_size_usd"] = max(trip["max_size_usd"], abs(end) * f["px"])
            pos, pos_known = end, True
        if trip is not None:
            trip["size_now"] = pos
            open_now[coin] = trip
    if funding:
        by_coin_f: dict[str, list[dict]] = defaultdict(list)
        for r in funding:
            by_coin_f[str(r.get("coin") or "")].append(r)
        for t in trips:
            for r in by_coin_f.get(t["coin"], ()):
                tm = int(r.get("time") or 0)
                if t["open_time"] <= tm <= t["close_time"]:
                    t["funding"] += _f(r.get("usdc"))
            t["pnl_usd"] = t["pnl_gross"] - t["fees"] + t["funding"]
    trips.sort(key=lambda t: (t["close_time"], t["open_time"]))
    return {"trips": trips, "open": open_now, "skipped_pre": skipped_pre}


def apply_leverage(trip: dict, leverage: float | None) -> None:
    """Margin and PnL in % of margin once the trip's leverage is known."""
    if leverage and leverage > 0:
        trip["leverage"] = float(leverage)
        trip["margin_usd"] = trip["max_size_usd"] / float(leverage)
        trip["pnl_pct_margin"] = (trip["pnl_usd"] / trip["margin_usd"] * 100.0) if trip["margin_usd"] > EPS else None
    else:
        trip.setdefault("leverage", None)
        trip.setdefault("margin_usd", None)
        trip.setdefault("pnl_pct_margin", None)


# ---------------------------------------------------------------------------
# metrics
# ---------------------------------------------------------------------------

def _median(xs: list[float]) -> float | None:
    return statistics.median(xs) if xs else None


def _streaks(trips: list[dict]) -> tuple[int, int | None, str]:
    """(longest losing streak, close_time ms where it ended, current streak
    like 'W5' or 'L2')."""
    longest, at, run = 0, None, 0
    for t in trips:
        if t["pnl_usd"] < 0:
            run += 1
            if run > longest:
                longest, at = run, t["close_time"]
        else:
            run = 0
    cur, n = "", 0
    for t in reversed(trips):
        w = t["pnl_usd"] > 0
        if not cur:
            cur = "W" if w else "L"
            n = 1
        elif (cur == "W") == w:
            n += 1
        else:
            break
    return longest, at, (f"{cur}{n}" if cur else "")


def _drawdown(trips: list[dict], equity_base: float) -> tuple[float | None, int | None, int | None]:
    """Peak to trough of equity_base + cumulative net PnL, in % of the peak.
    Returns (dd_pct, peak_time_ms, trough_time_ms)."""
    if not trips:
        return None, None, None
    base = equity_base if equity_base and equity_base > 0 else None
    if base is None:
        # no equity known: use the gross exposure median as a stand-in so a
        # drawdown is still expressed as a percent of something real
        sizes = [t["max_size_usd"] for t in trips if t["max_size_usd"] > 0]
        base = statistics.median(sizes) if sizes else 1.0
    eq = base
    peak, peak_t, worst, dd_from, dd_to = eq, trips[0]["open_time"], 0.0, None, None
    for t in trips:
        eq += t["pnl_usd"]
        if eq > peak:
            peak, peak_t = eq, t["close_time"]
        dd = (peak - eq) / peak * 100.0 if peak > 0 else 0.0
        if dd > worst:
            worst, dd_from, dd_to = dd, peak_t, t["close_time"]
    return worst, dd_from, dd_to


def _weekly(trips: list[dict], now_ms: int, weeks: int = 13) -> list[dict]:
    out = []
    for i in range(weeks - 1, -1, -1):
        end = now_ms - i * 7 * DAY_MS
        start = end - 7 * DAY_MS
        pnl = sum(t["pnl_usd"] for t in trips if start < t["close_time"] <= end)
        n = sum(1 for t in trips if start < t["close_time"] <= end)
        out.append({"start": start, "end": end, "pnl": round(pnl, 2), "n": n})
    return out


def _style(median_hold_sec: float | None) -> str:
    if median_hold_sec is None:
        return "trader"
    if median_hold_sec < 3600:
        return "scalper"
    if median_hold_sec < 86400:
        return "intraday"
    return "swing"


def compute_metrics(trips_all: list[dict], *, now_ms: int, equity_avg: float | None,
                    account_value: float | None, perpl_coins: set[str], last_fill_ms: int | None,
                    identity_role: str | None, hedger: bool | None, wash_share: float | None,
                    mm_flag: bool | None, history_from_ms: int | None = None,
                    net_pnl_all: float | None = None, extra_liquidations: int = 0) -> dict:
    """Every number, rule, label and the score for one wallet. `trips_all`:
    closed trips (any age) oldest first. Rules are evaluated on the 90-day
    window, momentum on 30 days."""
    t90 = [t for t in trips_all if t["close_time"] > now_ms - WINDOW_90_MS]
    t30 = [t for t in trips_all if t["close_time"] > now_ms - WINDOW_30_MS]
    eq = equity_avg if equity_avg and equity_avg > 0 else None

    def block(ts: list[dict]) -> dict:
        wins = [t for t in ts if t["pnl_usd"] > 0]
        losses = [t for t in ts if t["pnl_usd"] < 0]
        gw = sum(t["pnl_usd"] for t in wins)
        gl = -sum(t["pnl_usd"] for t in losses)
        pf = (gw / gl) if gl > EPS else (PF_CAP if gw > 0 else None)
        return {
            "n": len(ts), "wins": len(wins), "losses": len(losses),
            "win_rate": (len(wins) / len(ts)) if ts else None,
            "net": sum(t["pnl_usd"] for t in ts),
            "gross_win": gw, "gross_loss": gl, "pf": pf,
            "avg_win": (gw / len(wins)) if wins else None,
            "avg_loss": (gl / len(losses)) if losses else None,
            "avg_win_move": (sum(t["ret_pct"] for t in wins) / len(wins)) if wins else None,
            "avg_loss_move": (sum(-t["ret_pct"] for t in losses) / len(losses)) if losses else None,
        }

    b90, b30 = block(t90), block(t30)
    days_active = len({ms_to_dt(t["open_time"]).date() for t in t90} | {ms_to_dt(t["close_time"]).date() for t in t90})
    span_days = ((t90[-1]["close_time"] - t90[0]["open_time"]) / DAY_MS) if t90 else 0.0
    holds = [t["hold_sec"] for t in t90]
    median_hold = _median(holds)
    per_day: dict = defaultdict(int)
    for t in t90:
        per_day[ms_to_dt(t["close_time"]).date()] += 1
    trades_per_day_max = max(per_day.values()) if per_day else 0
    trades_per_week = (len(t90) / max(span_days, 7.0) * 7.0) if t90 else 0.0
    fills_total = sum(t["n_fills"] for t in t90)
    maker_share = (sum(t["maker_fills"] for t in t90) / fills_total) if fills_total else None
    volume = sum(t["volume_usd"] for t in t90)
    pnl_per_volume = (b90["net"] / volume) if volume > EPS else None
    long_share = (sum(1 for t in t90 if t["side"] == "long") / len(t90)) if t90 else None
    levs = [t["leverage"] for t in t90 if t.get("leverage")]
    median_lev, max_lev = _median(levs), (max(levs) if levs else None)
    worst_trade = min((t["pnl_usd"] for t in t90), default=None)
    by_day: dict = defaultdict(float)
    for t in t90:
        by_day[ms_to_dt(t["close_time"]).date()] += t["pnl_usd"]
    worst_day = min(by_day.values(), default=None)
    weekly = _weekly(t90, now_ms)
    worst_week = min((w["pnl"] for w in weekly), default=None)
    profitable_weeks = sum(1 for w in weekly if w["pnl"] > 0)
    largest = max((t["pnl_usd"] for t in t90), default=0.0)
    largest_share = (largest / b90["net"]) if b90["net"] > EPS and largest > 0 else None
    dd, dd_from, dd_to = _drawdown(t90, eq)
    longest_streak, streak_at, streak_now = _streaks(t90)
    hours = [0] * 24
    for t in t90:
        hours[ms_to_dt(t["open_time"]).hour] += 1
    coin_stats: dict[str, dict] = {}
    for t in t90:
        c = coin_stats.setdefault(t["coin"], {"coin": t["coin"], "trades": 0, "wins": 0, "net": 0.0, "on_perpl": t["coin"] in perpl_coins})
        c["trades"] += 1
        c["wins"] += 1 if t["pnl_usd"] > 0 else 0
        c["net"] += t["pnl_usd"]
    coins_sorted = sorted(coin_stats.values(), key=lambda c: -c["net"])
    for c in coins_sorted:
        c["win_rate"] = c["wins"] / c["trades"] if c["trades"] else None
        c["net"] = round(c["net"], 2)
    top_coins = [c["coin"] for c in coins_sorted[:3]]
    positive = sum(c["net"] for c in coins_sorted if c["net"] > 0)
    copiable = (sum(c["net"] for c in coins_sorted if c["net"] > 0 and c["on_perpl"]) / positive * 100.0) if positive > EPS else None
    liquidations = sum(1 for t in t90 if t["liquidation"]) + int(extra_liquidations or 0)
    recent_ms = last_fill_ms if last_fill_ms else (t90[-1]["close_time"] if t90 else None)
    usual_open = _usual_concurrent(t90)

    pct_eq = (lambda v: (v / eq * 100.0) if (eq and v is not None) else None)
    # ---- hard rules ----
    flags: dict[str, dict] = {}

    def rule(name: str, ok: bool | None, value):
        flags[name] = {"pass": ok, "value": value}

    role_ok = identity_role in (None, "user", "unknown") and identity_role != "vault"
    rule("role", (identity_role == "user") if identity_role not in (None, "unknown") else None, identity_role or "unknown")
    rule("sample", len(t90) >= RULES["min_trips"] and span_days >= RULES["min_days"], {"trips": len(t90), "days": round(span_days, 1)})
    rule("recent", (recent_ms is not None and now_ms - recent_ms <= WINDOW_14_MS), (ms_to_dt(recent_ms).isoformat() if recent_ms else None))
    profit_ok = b90["net"] > RULES["min_profit_usd"] and (eq is None or b90["net"] > eq * RULES["min_profit_pct_eq"] / 100.0)
    rule("net_profit", profit_ok, {"usd": round(b90["net"], 2), "pct_eq": (round(pct_eq(b90["net"]), 2) if eq else None)})
    wt_pct = pct_eq(worst_trade) if worst_trade is not None else None
    rule("worst_trade", (wt_pct is None) or (-wt_pct < RULES["worst_trade_pct_eq"]), {"usd": worst_trade, "pct_eq": wt_pct})
    rule("drawdown", (dd is None) or (dd < RULES["max_dd_pct"]), dd)
    mm_measured = ((maker_share is not None and maker_share >= RULES["mm_maker_share"])
                   or (median_hold is not None and median_hold < RULES["mm_hold_sec"])
                   or (pnl_per_volume is not None and pnl_per_volume < RULES["mm_pnl_per_volume"]))
    rule("mm", not (mm_measured or mm_flag is True), {"maker_share": maker_share, "median_hold_sec": median_hold,
                                                      "pnl_per_volume": pnl_per_volume, "cohort_flag": mm_flag})
    rule("farmer", not (pnl_per_volume is not None and pnl_per_volume < RULES["farmer_pnl_per_volume"]), pnl_per_volume)
    rule("bot", not (trades_per_day_max > RULES["bot_trips_per_day"] or (median_hold is not None and median_hold < RULES["bot_hold_sec"])),
         {"trips_per_day_max": trades_per_day_max, "median_hold_sec": median_hold})
    rule("hedger", (hedger is not True), hedger)
    rule("wash", (wash_share is None) or (wash_share < RULES["wash_counterparty_share"]), wash_share)
    rule("liquidations", liquidations == 0, liquidations)
    if history_from_ms:
        flags["history"] = {"pass": True, "value": ms_to_dt(history_from_ms).date().isoformat()}

    # ---- label ----
    wr, pf = b90["win_rate"], b90["pf"]
    label = None
    if wr is not None and pf is not None:
        if wr >= RULES["wr_consistent"] and pf > RULES["pf_consistent"]:
            label = "consistent"
        elif (RULES["wr_asymmetric"] <= wr < RULES["wr_consistent"] and pf > RULES["pf_asymmetric"]
              and b90["avg_win"] and b90["avg_loss"] and b90["avg_win"] >= RULES["asym_win_loss_ratio"] * b90["avg_loss"]):
            label = "asymmetric"
    rule("label", label is not None, {"win_rate": wr, "pf": pf, "avg_win": b90["avg_win"], "avg_loss": b90["avg_loss"]})

    hard = ["role", "sample", "net_profit", "worst_trade", "drawdown", "mm", "farmer", "bot", "hedger", "wash", "liquidations", "label"]
    # role unknown (None) does not block: identity is read only for wallets that pass everything else
    qualified = all(flags[k]["pass"] is not False for k in hard) and role_ok
    inactive = qualified and flags["recent"]["pass"] is False
    one_big_win = bool(largest_share is not None and largest_share > RULES["one_big_win_share"])

    # ---- score ----
    def clamp(x: float) -> float:
        return max(0.0, min(1.0, x))
    s_pf = clamp(((pf or 0.0) - 1.0) / 3.0)
    s_wr = clamp(((wr or 0.0) - 0.40) / 0.50)
    s_cons = clamp(profitable_weeks / 13.0)
    s_dd = clamp(1.0 - (dd or 0.0) / RULES["max_dd_pct"])
    s_sample = clamp(len(t90) / 100.0)
    s_fit = clamp((copiable or 0.0) / 100.0)
    score = 100.0 * (0.30 * s_pf + 0.20 * s_wr + 0.15 * s_cons + 0.15 * s_dd + 0.10 * s_sample + 0.10 * s_fit)
    if median_lev and median_lev > RULES["lev_penalty_above"]:
        score *= 0.85
    if one_big_win:
        score *= 0.80
    score_inputs = {"pf": s_pf, "win_rate": s_wr, "consistency": s_cons, "drawdown": s_dd, "sample": s_sample,
                    "copy_fit": s_fit, "lev_penalty": bool(median_lev and median_lev > RULES["lev_penalty_above"]),
                    "one_big_win": one_big_win}

    style = _style(median_hold)
    confidence = "high" if len(t90) >= 100 else ("medium" if len(t90) >= 60 else "low")
    verdict = make_verdict(style=style, top_coins=top_coins, median_hold=median_hold, win_rate=wr,
                           worst_trade_pct_eq=wt_pct, losing_weeks=13 - profitable_weeks, copiable=copiable,
                           avg_win=b90["avg_win"], avg_loss=b90["avg_loss"], label=label)
    return {
        "label": label, "qualified": qualified, "inactive": inactive, "one_big_win": one_big_win,
        "score": round(score, 2), "score_inputs": score_inputs, "verdict": verdict, "style": style, "confidence": confidence,
        "trades_90d": len(t90), "days_active_90d": days_active, "span_days": round(span_days, 1),
        "win_rate_90d": wr, "win_rate_30d": b30["win_rate"],
        "net_pnl_90d": round(b90["net"], 2), "net_pnl_30d": round(b30["net"], 2),
        "net_pnl_all": (round(net_pnl_all, 2) if net_pnl_all is not None else round(sum(t["pnl_usd"] for t in trips_all), 2)),
        "profit_factor_90d": (round(min(pf, PF_CAP), 2) if pf is not None else None),
        "profit_factor_30d": (round(min(b30["pf"], PF_CAP), 2) if b30["pf"] is not None else None),
        "avg_win_usd": b90["avg_win"], "avg_loss_usd": b90["avg_loss"],
        "avg_win_pct_eq": pct_eq(b90["avg_win"]), "avg_loss_pct_eq": pct_eq(b90["avg_loss"]),
        "avg_win_move_pct": b90["avg_win_move"], "avg_loss_move_pct": b90["avg_loss_move"],
        "max_drawdown_90d": dd, "dd_start": (ms_to_dt(dd_from).date().isoformat() if dd_from else None),
        "dd_end": (ms_to_dt(dd_to).date().isoformat() if dd_to else None),
        "worst_trade_usd": worst_trade, "worst_trade_pct_eq": wt_pct,
        "worst_day_usd": worst_day, "worst_week_usd": worst_week,
        "largest_trade_share": largest_share, "profitable_weeks_13": profitable_weeks, "weekly": weekly,
        "longest_loss_streak": longest_streak, "loss_streak_at": (ms_to_dt(streak_at).date().isoformat() if streak_at else None),
        "streak_now": streak_now,
        "median_hold_sec": (int(median_hold) if median_hold is not None else None),
        "trades_per_week": round(trades_per_week, 2), "trades_per_day_max": trades_per_day_max,
        "median_leverage": median_lev, "max_leverage": max_lev,
        "maker_share": maker_share, "pnl_per_volume": pnl_per_volume, "long_share": long_share,
        "avg_equity_90d": eq, "account_value": account_value, "volume_90d": round(volume, 2),
        "top_coins": top_coins, "coin_stats": coins_sorted, "hours_utc": hours, "copiable_pct": copiable,
        "liquidations_90d": liquidations, "usual_open": usual_open,
        "averages_down_n": sum(1 for t in t90 if t["averaged_down"]),
        "adds_to_winner_n": sum(1 for t in t90 if t["added_to_winner"]),
        "last_trade_at": (ms_to_dt(recent_ms).isoformat() if recent_ms else None),
        "flags": flags,
        "history_from": (ms_to_dt(history_from_ms).date().isoformat() if history_from_ms else None),
    }


def _usual_concurrent(trips: list[dict]) -> int | None:
    """Median number of trips open at the same time (the trader's usual
    number of concurrent positions), from the open/close times."""
    if not trips:
        return None
    ev = []
    for t in trips:
        ev.append((t["open_time"], 1))
        ev.append((t["close_time"], -1))
    ev.sort()
    cur, samples = 0, []
    for _, d in ev:
        cur += d
        if d == 1:
            samples.append(cur)
    return int(statistics.median(samples)) if samples else None


def make_verdict(*, style: str, top_coins: list[str], median_hold: float | None, win_rate: float | None,
                 worst_trade_pct_eq: float | None, losing_weeks: int, copiable: float | None,
                 avg_win: float | None, avg_loss: float | None, label: str | None) -> str:
    coins = ", ".join(top_coins[:2]) if top_coins else "mixed coins"
    if len(top_coins) >= 2:
        coins = f"{top_coins[0]} and {top_coins[1]}"
    hold = ""
    if median_hold is not None:
        if median_hold < 3600:
            hold = f"holds about {max(1, int(round(median_hold / 60)))} minutes"
        elif median_hold < 86400:
            hold = f"holds about {max(1, int(round(median_hold / 3600)))} hours"
        else:
            d = median_hold / 86400
            hold = f"holds {int(d)} to {int(d) + 1} days" if d < 5 else f"holds about {int(round(d))} days"
    wr = f"wins {round(win_rate * 100)}% of trades" if win_rate is not None else "win rate unknown"
    if label == "asymmetric" and avg_win and avg_loss and avg_loss > 0:
        wr += f" but wins {avg_win / avg_loss:.1f}x bigger than losses"
    worst = f"worst loss {abs(worst_trade_pct_eq):.1f}% of equity" if worst_trade_pct_eq is not None else "worst loss not measured against equity"
    weeks = f"{losing_weeks} losing week{'s' if losing_weeks != 1 else ''} out of 13"
    cop = ""
    if copiable is not None:
        cop = " Everything it trades is on Perpl." if copiable >= 99.5 else f" {round(copiable)}% of its profit is on Perpl coins."
    name = {"scalper": "Scalper", "intraday": "Intraday trader", "swing": "Swing trader"}.get(style, "Trader")
    return f"{name} on {coins}. {hold.capitalize() + '. ' if hold else ''}{wr.capitalize()}, {worst}, {weeks}.{cop}"[:240]


# ---------------------------------------------------------------------------
# copy replay
# ---------------------------------------------------------------------------

def replay(trips: list[dict], *, margin_usd: float, leverage: float, stop_pct: float | None,
           perpl_coins: set[str], now_ms: int | None = None,
           fee_bps: float = PERPL_TAKER_OPEN_BPS, slip_bps: float = REPLAY_SLIP_BPS) -> dict:
    """What the last 90 days would have returned at the copier's settings.
    Each trip is resized to margin x leverage, entered slip_bps against the
    copier, charged the Perpl taker open fee, stopped at stop_pct of entry
    when the trip's adverse excursion reached it before the trader's exit,
    and skipped when the coin is not on Perpl."""
    now_ms = now_ms or int(datetime.now(timezone.utc).timestamp() * 1000)
    notional = float(margin_usd) * float(leverage)
    fee = notional * fee_bps / 1e4
    cum = 0.0
    peak = 0.0
    worst_dip = 0.0
    n = wins = stopped = skipped = 0
    fees = 0.0
    curve = []
    for t in trips:
        if t["close_time"] <= now_ms - WINDOW_90_MS:
            continue
        if t["coin"] not in perpl_coins:
            skipped += 1
            continue
        n += 1
        ret = t["ret_pct"] - slip_bps / 100.0
        mae = (t.get("mae_pct") or 0.0) + slip_bps / 100.0
        if stop_pct is not None and stop_pct > 0 and mae >= stop_pct:
            pnl = -notional * stop_pct / 100.0
            stopped += 1
        else:
            pnl = notional * ret / 100.0
        pnl -= fee
        fees += fee
        if pnl > 0:
            wins += 1
        cum += pnl
        peak = max(peak, cum)
        worst_dip = max(worst_dip, peak - cum)
        curve.append({"t": t["close_time"], "v": round(cum, 2)})
    return {"pnl": round(cum, 2), "worst_dip": round(worst_dip, 2), "stopped_out": stopped, "trades": n,
            "wins": wins, "skipped": skipped, "fees": round(fees, 2), "margin_usd": margin_usd, "leverage": leverage,
            "stop_pct": stop_pct, "curve": curve[-200:]}


def suggested_settings(metrics: dict, user_defaults: dict | None) -> dict:
    """Defaults for the replay and the copy sheet: the user's saved auto-copy
    settings when present, else $50 margin, 3x, stop at the trader's average
    loss (price move), with the trader's median leverage capping leverage."""
    margin = 50.0
    lev = 3.0
    stop = metrics.get("avg_loss_move_pct")
    if user_defaults:
        margin = _f(user_defaults.get("margin_usd"), margin) or margin
        lev = _f(user_defaults.get("max_leverage"), lev) or lev
        if user_defaults.get("sl_margin_pct"):
            # auto-copy's stop is % of margin: as a price move that is / leverage
            stop = _f(user_defaults.get("sl_margin_pct")) / lev if lev else stop
    med_lev = metrics.get("median_leverage")
    if med_lev:
        lev = min(lev, float(med_lev)) if user_defaults else lev
    stop = round(stop, 2) if stop else None
    suggested_stop = round(stop * 1.5, 2) if stop else None
    return {"margin_usd": margin, "leverage": lev, "stop_pct": stop, "suggested_stop_pct": suggested_stop,
            "max_open": metrics.get("usual_open"), "trader_median_leverage": med_lev}
