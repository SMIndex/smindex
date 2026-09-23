#!/usr/bin/env python3
"""Fresh large shorts across tracked wallets. READ ONLY.

What this answers: which wallets opened or added to a short of >= $100k in a
single trade, recently, and what our own tables already know about them.

What it does NOT answer, and cannot: whether anyone traded on private
information. A large fresh short is a place to look, not evidence. The most
common innocent explanations are already surfaced per row so they can be ruled
out rather than ignored — a market maker quoting both sides, a hedger offsetting
spot, a wallet that shorts this size routinely, a cluster of addresses run by one
desk. A wallet flagged `MM` doing this is noise; a wallet with no history in the
coin, flat beforehand, sized far above its own norm, is the shape worth reading.

Three sources, because each misses something the others catch:

* **Fills** (`hl_fill_events` + `leader_trade_events.raw`) — exact, to the
  trade, but only for wallets under fill coverage. `hl_fill_events` stores no
  `startPosition`, so "was it flat before" is only exact where the leader
  tracker supplied the full venue object.
* **Sweep flow events** (`analytics_flow_events`) — covers every swept wallet,
  but at 20-30 minute resolution, so "fresh" there means "inside that cycle",
  not "at 14:02:11".
* **Position diff** — a wallet that is short now and was not 2h ago, caught even
  when neither of the above saw the fill.

HIP-3 coins (`xyz:...`) are read with the builder-dex parameter; the canonical
clearinghouseState does not return them.

    python backend/scripts/fresh_shorts_scan.py --hours 2 --min-notional 100000
    python backend/scripts/fresh_shorts_scan.py --coin xyz:BRENTOIL
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.request
from collections import defaultdict
from datetime import datetime, timezone, timedelta
from pathlib import Path

import pymysql

# PERPL_ROOT lets the same file run from the repo locally and from /root on prod
_env_root = os.environ.get("PERPL_ROOT")
ROOT = Path(_env_root) if _env_root else Path(__file__).resolve().parents[2]
REPORTS = ROOT / "backend" / "reports"
HL_INFO = "https://api.hyperliquid.xyz/info"
SWEEP_RES_MIN = 20          # nominal; the real spacing is printed per run


def db():
    env = {}
    for line in (ROOT / "backend" / ".env").read_text(encoding="utf-8").splitlines():
        if "=" in line and not line.strip().startswith("#"):
            k, v = line.split("=", 1)
            env[k.strip()] = v.strip()
    body = env["DATABASE_URL"].split("://", 1)[1]
    creds, hostpart = body.split("@", 1)
    user, pwd = creds.split(":", 1)
    hostport, name = hostpart.split("/", 1)
    host, port = hostport.split(":")
    return pymysql.connect(host=host, port=int(port), user=user, password=pwd,
                           database=name.split("?")[0], charset="utf8mb4")


def hl(payload):
    r = urllib.request.Request(HL_INFO, data=json.dumps(payload).encode(),
                               headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(r, timeout=30) as resp:
        return json.load(resp)


def ts(ms):
    return "—" if not ms else datetime.fromtimestamp(ms / 1000, timezone.utc).strftime("%m-%d %H:%M:%S")


def usd(v, d=0):
    if v is None:
        return "—"
    return ("-$" if v < 0 else "$") + f"{abs(v):,.{d}f}"


def ago(ms, now_ms):
    if not ms:
        return "—"
    m = (now_ms - ms) / 60000
    if m < 90:
        return f"{m:.0f}m"
    if m < 60 * 48:
        return f"{m/60:.1f}h"
    return f"{m/1440:.1f}d"


# ---------------------------------------------------------------- sources ---
def from_fills(cur, since_ms, min_notional, coin_filter):
    """Short-opening fills. `Open Short` covers a new short and an add; a
    `Long > Short` flip also establishes a short. `Close Long` does NOT — that
    reduces a long and is not a short position."""
    hits = []
    seen = set()

    cur.execute(
        # no side filter: `dir` inside raw is the authoritative classifier and a
        # Long > Short flip may be recorded with either side value
        "SELECT trader_wallet, raw FROM leader_trade_events WHERE detected_at >= %s",
        (datetime.fromtimestamp(since_ms / 1000, timezone.utc).replace(tzinfo=None),))
    for wallet, raw in cur.fetchall():
        try:
            f = (json.loads(raw) if isinstance(raw, (str, bytes)) else raw).get("fill") or {}
        except Exception:
            continue
        d = str(f.get("dir") or "")
        if d not in ("Open Short", "Long > Short"):
            continue
        coin = f.get("coin", "")
        if coin_filter and coin.upper() != coin_filter.upper():
            continue
        n = float(f.get("px") or 0) * float(f.get("sz") or 0)
        if n < min_notional:
            continue
        tid = str(f.get("tid") or "")
        seen.add(tid)
        sp = f.get("startPosition")
        sp = float(sp) if sp not in (None, "") else None
        hits.append(dict(src="fill/tracker", wallet=wallet.lower(), coin=coin,
                         ts=int(f.get("time") or 0), px=float(f.get("px") or 0),
                         sz=float(f.get("sz") or 0), notional=n, dir=d,
                         start_position=sp,
                         before=("flat" if sp is not None and abs(sp) < 1e-9 else
                                 "long" if sp is not None and sp > 0 else
                                 "short" if sp is not None else "unknown"),
                         prev_notional=(abs(sp) * float(f.get("px") or 0)
                                        if sp is not None and sp < 0 else 0.0),
                         exact_time=True))

    q = ("SELECT wallet_address, ts_ms, coin, dir, px, sz, tid FROM hl_fill_events "
         "WHERE ts_ms >= %s AND dir IN ('Open Short','Long > Short') AND px*sz >= %s")
    args = [since_ms, min_notional]
    if coin_filter:
        q += " AND UPPER(coin) = UPPER(%s)"
        args.append(coin_filter)
    cur.execute(q, args)
    for wallet, t, coin, d, px, sz, tid in cur.fetchall():
        if str(tid or "") in seen:
            continue
        hits.append(dict(src="fill/sampler", wallet=wallet.lower(), coin=coin,
                         ts=int(t), px=float(px), sz=float(sz),
                         notional=float(px) * float(sz), dir=d,
                         start_position=None,
                         before="unknown",   # this table drops startPosition
                         prev_notional=None,
                         exact_time=True))
    return hits


def from_flow(cur, since_dt, min_notional, coin_filter):
    q = ("SELECT wallet, asset, event_type, size_before, size_after, notional_delta, "
         "ref_px, detected_at FROM analytics_flow_events "
         "WHERE detected_at >= %s AND side='short' "
         "AND event_type IN ('OPEN','INCREASE','FLIP') AND ABS(notional_delta) >= %s")
    args = [since_dt, min_notional]
    if coin_filter:
        q += " AND UPPER(asset) = UPPER(%s)"
        args.append(coin_filter)
    out = []
    cur.execute(q, args)
    for w, a, et, sb, sa, nd, rpx, det in cur.fetchall():
        # size_before/size_after are UNSIGNED magnitudes — direction is in
        # `side`. So sb > 0 on a side='short' row means the wallet was ALREADY
        # short by that size, not long.
        sb = float(sb or 0)
        if et == "FLIP":
            before = "long (flip)"
        elif abs(sb) < 1e-9:
            before = "flat"
        else:
            before = "short"
        out.append(dict(src="sweep/flow", wallet=w.lower(), coin=a,
                        ts=int(det.replace(tzinfo=timezone.utc).timestamp() * 1000),
                        px=float(rpx or 0), sz=abs(float(sa or 0) - sb),
                        notional=abs(float(nd or 0)), dir=et,
                        start_position=sb, before=before,
                        prev_notional=(sb * float(rpx or 0)) if before == "short" else 0.0,
                        exact_time=False))
    return out


def from_position_diff(cur, now_cycle, past_cycle, min_notional, coin_filter):
    """Short now, not short then — catches wallets whose fill nobody saw."""
    def snap(cycle):
        q = ("SELECT wallet, asset, side, size, notional, entry_px, leverage "
             "FROM analytics_positions WHERE cycle_ts = %s")
        args = [cycle]
        if coin_filter:
            q += " AND UPPER(asset) = UPPER(%s)"
            args.append(coin_filter)
        cur.execute(q, args)
        return {(r[0].lower(), r[1]): r for r in cur.fetchall()}

    now_s, past_s = snap(now_cycle), snap(past_cycle)

    # A wallet with NO rows at all in the prior cycle was not swept then — it
    # has just rotated into the cohort. Its whole existing book would otherwise
    # read as brand-new shorts. `observed_before` separates "was flat" (swept,
    # no position) from "we weren't looking" (not swept).
    cur.execute("SELECT DISTINCT wallet FROM analytics_positions WHERE cycle_ts = %s",
                (past_cycle,))
    observed_before = {r[0].lower() for r in cur.fetchall()}

    out = []
    for key, r in now_s.items():
        if str(r[2]).lower() != "short":
            continue
        if key[0] not in observed_before:
            continue   # not observed last cycle — cannot call this fresh
        notional = abs(float(r[4] or 0))
        prev = past_s.get(key)
        prev_short = prev is not None and str(prev[2]).lower() == "short"
        prev_notional = abs(float(prev[4] or 0)) if prev_short else 0.0
        delta = notional - prev_notional
        if delta < min_notional:
            continue
        out.append(dict(src="sweep/diff", wallet=key[0], coin=key[1],
                        ts=int(now_cycle.replace(tzinfo=timezone.utc).timestamp() * 1000),
                        px=float(r[5] or 0), sz=abs(float(r[3] or 0)), notional=delta,
                        dir="position appeared" if not prev else "position grew",
                        start_position=(-prev_notional if prev_short else 0.0),
                        before=("flat/none" if prev is None else
                                "long (flip)" if str(prev[2]).lower() == "long" else
                                "short" if prev_short else "flat"),
                        prev_notional=prev_notional,
                        exact_time=False))
    return out


# ------------------------------------------------------------- enrichment ---
def enrich(cur, wallets, pairs, now_ms):
    ctx = defaultdict(dict)
    if not wallets:
        return ctx
    wl = tuple(wallets)

    cur.execute("SELECT MAX(cycle_ts) FROM analytics_positions")
    latest = cur.fetchone()[0]
    cur.execute("SELECT wallet, asset, side, size, notional, entry_px, leverage, upnl "
                "FROM analytics_positions WHERE cycle_ts=%s AND wallet IN %s", (latest, wl))
    for w, a, side, size, notl, epx, lev, upnl in cur.fetchall():
        ctx[w.lower()].setdefault("pos", {})[a] = dict(
            side=side, size=float(size or 0), notional=float(notl or 0),
            entry=float(epx or 0), lev=float(lev or 0), upnl=float(upnl or 0))

    cur.execute("SELECT s.wallet, s.account_value, s.gross_notional, s.n_assets FROM analytics_wallet_state s "
                "JOIN (SELECT wallet, MAX(cycle_ts) mt FROM analytics_wallet_state "
                "      WHERE wallet IN %s GROUP BY wallet) x "
                "  ON x.wallet=s.wallet AND x.mt=s.cycle_ts", (wl,))
    for w, av, gross, n in cur.fetchall():
        ctx[w.lower()]["account_value"] = float(av or 0)
        ctx[w.lower()]["gross"] = float(gross or 0)
        ctx[w.lower()]["n_assets"] = n

    cur.execute("SELECT wallet_address, trades_7d, win_rate_7d, profit_factor, "
                "maker_ratio, avg_trade_notional, sample_capped "
                "FROM trader_fill_stats WHERE wallet_address IN %s", (wl,))
    for w, tr, wr, pf, mk, avgn, capped in cur.fetchall():
        ctx[w.lower()]["stats"] = dict(
            trades=tr, win=float(wr) if wr is not None else None,
            pf=float(pf) if pf is not None else None,
            maker=float(mk) if mk is not None else None,
            avg_notional=float(avgn) if avgn is not None else None,
            capped=bool(capped))

    cur.execute("SELECT wallet, cluster_id, confidence FROM analytics_wallet_clusters "
                "WHERE wallet IN %s", (wl,))
    for w, cid, conf in cur.fetchall():
        ctx[w.lower()]["cluster"] = dict(id=cid, conf=float(conf or 0))

    # how far back our own history for this wallet goes
    cur.execute("SELECT wallet, MIN(cycle_ts) FROM analytics_positions "
                "WHERE wallet IN %s GROUP BY wallet", (wl,))
    for w, first in cur.fetchall():
        ctx[w.lower()]["history_from"] = first

    # Previous trade in the same coin. Only the (wallet, coin) pairs that
    # actually appear as hits — the full cross product would be hundreds of
    # scans over multi-million-row tables.
    for w, c in pairs:
        cur.execute("SELECT MAX(ts_ms) FROM hl_fill_events "
                    "WHERE wallet_address=%s AND coin=%s", (w, c))
        a = cur.fetchone()[0]
        cur.execute("SELECT MAX(detected_at) FROM analytics_flow_events "
                    "WHERE wallet=%s AND asset=%s", (w, c))
        b = cur.fetchone()[0]
        b = int(b.replace(tzinfo=timezone.utc).timestamp() * 1000) if b else None
        last = max([x for x in (int(a) if a else None, b) if x], default=None)
        if last:
            ctx[w].setdefault("last_in_coin", {})[c] = last
    return ctx


def mm_hedger_flags(cur, wallets, ctx):
    """MM / hedger verdicts — the single most useful false-positive filter here.

    Computed from persisted data using the thresholds in
    `app/services/analytics/cohort.py`, rather than importing that module: its
    clause-B evidence (`_sweep_mm`) is in-process state owned by the live sweep,
    so an out-of-process script importing it would see clause A only. Both
    clauses come off the same rows the module itself hydrates from.
    """
    MM_MAKER_RATIO_MIN, MM_TRADES_7D_MIN = 0.6, 500
    MM_NOTIONAL_X, MM_MIN_ASSETS = 25.0, 8

    hedge = {}
    if wallets:
        cur.execute("SELECT wallet, is_likely_hedger FROM analytics_wallet_flags "
                    "WHERE wallet IN %s AND is_likely_hedger IS NOT NULL",
                    (tuple(wallets),))
        hedge = {w.lower(): bool(h) for w, h in cur.fetchall()}

    out = {}
    for w in wallets:
        c = ctx.get(w, {})
        st = c.get("stats") or {}
        # clause A — maker-dominant, very high trade count
        a = None
        if st.get("maker") is not None:
            a = (st["maker"] >= MM_MAKER_RATIO_MIN
                 and (st.get("trades") or 0) >= MM_TRADES_7D_MIN)
        # clause B — gross notional a large multiple of equity, spread wide
        b = None
        av = c.get("account_value")
        if av is not None and c.get("gross") is not None and av > 0:
            b = (c["gross"] >= MM_NOTIONAL_X * av
                 and (c.get("n_assets") or 0) >= MM_MIN_ASSETS)
        mm = True if (a is True or b is True) else (False if (a is False and b is False) else None)
        out[w] = (mm, hedge.get(w))
    return out


def live_positions(wallets, coins):
    """Fresh venue state. HIP-3 markets need their builder dex named."""
    dexes = sorted({c.split(":")[0] for c in coins if ":" in c})
    out = {}
    for w in wallets:
        for payload in [{"type": "clearinghouseState", "user": w}] + \
                       [{"type": "clearinghouseState", "user": w, "dex": d} for d in dexes]:
            try:
                d = hl(payload)
            except Exception:
                continue
            for ap in d.get("assetPositions", []):
                p = ap.get("position") or {}
                if p.get("coin") in coins:
                    out[(w, p["coin"])] = dict(
                        szi=float(p.get("szi") or 0),
                        entry=float(p.get("entryPx") or 0) if p.get("entryPx") else None,
                        value=float(p.get("positionValue") or 0),
                        upnl=float(p.get("unrealizedPnl") or 0),
                        lev=(p.get("leverage") or {}).get("value"))
    return out


# -------------------------------------------------------------------- run ---
def main() -> int:
    ap = argparse.ArgumentParser(description="Fresh large shorts (read-only)")
    ap.add_argument("--hours", type=float, default=2.0)
    ap.add_argument("--min-notional", type=float, default=100000.0)
    ap.add_argument("--coin", default="all")
    ap.add_argument("--json", metavar="PATH",
                    help="also dump the enriched rows as JSON for downstream use")
    a = ap.parse_args()
    coin_filter = None if a.coin.lower() == "all" else a.coin

    now = datetime.now(timezone.utc)
    now_ms = int(now.timestamp() * 1000)
    since_ms = now_ms - int(a.hours * 3600_000)
    since_dt = datetime.fromtimestamp(since_ms / 1000, timezone.utc).replace(tzinfo=None)

    conn = db()
    cur = conn.cursor()

    cur.execute("SELECT DISTINCT cycle_ts FROM analytics_positions ORDER BY cycle_ts DESC LIMIT 12")
    cycles = [r[0] for r in cur.fetchall()]
    now_cycle = cycles[0] if cycles else None
    past_cycle = next((c for c in cycles if c <= since_dt), cycles[-1] if cycles else None)
    spacing = None
    if len(cycles) > 2:
        gaps = [(cycles[i] - cycles[i + 1]).total_seconds() / 60 for i in range(len(cycles) - 1)]
        spacing = sum(gaps) / len(gaps)

    hits = from_fills(cur, since_ms, a.min_notional, coin_filter)
    hits += from_flow(cur, since_dt, a.min_notional, coin_filter)
    if now_cycle and past_cycle and now_cycle != past_cycle:
        hits += from_position_diff(cur, now_cycle, past_cycle, a.min_notional, coin_filter)

    # one row per (wallet, coin, source-event); prefer the exact-time source
    best = {}
    for h in hits:
        k = (h["wallet"], h["coin"], round(h["notional"], 2))
        if k not in best or (h["exact_time"] and not best[k]["exact_time"]):
            best[k] = h
    hits = sorted(best.values(), key=lambda h: -h["notional"])

    wallets = sorted({h["wallet"] for h in hits})
    coins = sorted({h["coin"] for h in hits})
    pairs = sorted({(h["wallet"], h["coin"]) for h in hits})
    ctx = enrich(cur, wallets, pairs, now_ms)
    flags = mm_hedger_flags(cur, wallets, ctx)
    live = live_positions(wallets, coins) if wallets else {}

    L = []
    def w_(s=""):
        L.append(s); print(s)

    w_(f"# Fresh large shorts — {now:%Y-%m-%d %H:%M} UTC")
    w_()
    w_(f"Window **{a.hours:g}h** · single-trade floor **{usd(a.min_notional)}** · "
       f"coin **{a.coin}** · read-only, no writes.")
    w_()
    w_("## What this can and cannot tell you")
    w_()
    w_("A large fresh short is **a place to look, not evidence**. Nothing here "
       "establishes that anyone traded on private information, and the most "
       "common innocent explanations are surfaced per row so they can be ruled "
       "out: a market maker quoting both sides, a hedger offsetting spot, a "
       "wallet that trades this size routinely, or several addresses run by one "
       "desk. A row flagged `MM` doing this is noise. The shape worth reading is "
       "a wallet that was **flat or long beforehand**, is sized **far above its "
       "own average trade**, and has **no recent history in that coin**.")
    w_()
    w_("## Coverage — what \"fresh\" means per row")
    w_()
    w_("| source | resolution | covers | limitation |")
    w_("|---|---|---|---|")
    w_("| `fill/tracker` | exact trade time | leader-tracked wallets | carries `startPosition`, so *flat before* is exact |")
    w_("| `fill/sampler` | exact trade time | fill-sampled wallets | `hl_fill_events` drops `startPosition` — *before* shows **unknown** |")
    w_(f"| `sweep/flow` | ~{spacing:.0f} min cycle | every swept wallet | 'fresh' = inside that cycle, not to the second |"
       if spacing else "| `sweep/flow` | ~20 min cycle | every swept wallet | cycle resolution |")
    w_("| `sweep/diff` | cycle to cycle | every swept wallet | catches a position nobody saw fill |")
    w_()
    if now_cycle and past_cycle:
        w_(f"Sweep cycles compared: **{past_cycle}** → **{now_cycle}** "
           f"(measured spacing {spacing:.0f} min over the last {len(cycles)} cycles).")
        w_()

    if not hits:
        w_(f"**No wallet opened or added to a short of {usd(a.min_notional)}+ in a single "
           f"trade in the last {a.hours:g}h"
           + (f" on `{a.coin}`" if coin_filter else "") + ".**")
        w_()
        w_("That is a real result, not a gap: all three sources were queried and "
           "returned nothing above the floor.")
    else:
        new_shorts = [h for h in hits if h["before"] in ("flat", "flat/none", "long", "long (flip)")]
        adds = [h for h in hits if h not in new_shorts]
        w_(f"## {len(hits)} qualifying trades, {len(wallets)} wallets")
        w_()
        w_(f"- **{len(new_shorts)} genuinely NEW shorts** — wallet was flat or long "
           f"immediately before. This is the set worth reading.")
        w_(f"- **{len(adds)} adds to an existing short** — they clear the $100k bar, "
           f"but a large wallet topping up a position it already held is ordinary. "
           f"The `add %` column says how much it moved the needle.")
        w_()
        w_("Both are listed below, new shorts first.")
        w_()
        rows = new_shorts + adds
        w_("| # | kind | wallet | coin | when | src | before | add % | entry | size | notional | position now | acct value | PF | win (n) | avg trade | flags | last in coin |")
        w_("|---|---|---|---|---|---|---|---:|---:|---:|---:|---:|---:|---:|---|---:|---|---|")
        for i, h in enumerate(rows, 1):
            c = ctx.get(h["wallet"], {})
            st = c.get("stats") or {}
            pos = (c.get("pos") or {}).get(h["coin"])
            lv = live.get((h["wallet"], h["coin"]))
            posnow = (f"{lv['szi']:,.2f} ({usd(lv['value'])})" if lv else
                      (f"{pos['size']:,.2f} ({usd(abs(pos['notional']))})" if pos else "—"))
            mm, hg = flags.get(h["wallet"], (None, None))
            fl = []
            if mm is True: fl.append("**MM**")
            elif mm is None: fl.append("mm?")
            if hg is True: fl.append("**hedger**")
            if c.get("cluster"): fl.append(f"cluster {c['cluster']['id']}")
            avg = st.get("avg_notional")
            ratio = (h["notional"] / avg) if avg else None
            avg_s = (f"{usd(avg)} ({ratio:.0f}×)" if ratio else usd(avg) if avg else "—")
            last = (c.get("last_in_coin") or {}).get(h["coin"])
            pf_s = f"{st['pf']:.2f}" if st.get("pf") else "—"
            win_s = (f"{st['win']*100:.0f}% ({st['trades']:,})"
                     if st.get("win") is not None else "—")
            lev_s = (lv or {}).get("lev") or (pos or {}).get("lev") or "—"
            is_new = h["before"] in ("flat", "flat/none", "long", "long (flip)")
            kind = "**NEW**" if is_new else ("add" if h["before"] == "short" else "?")
            pv = h.get("prev_notional")
            addp = "—" if is_new else (f"+{h['notional']/pv*100:,.1f}%" if pv else "—")
            w_(f"| {i} | {kind} | `{h['wallet'][:10]}…` | {h['coin']} | {ts(h['ts'])} | {h['src']} | "
               f"**{h['before']}** | {addp} | {h['px']:,.4f} | {h['sz']:,.2f} | {usd(h['notional'])} | "
               f"{posnow} | {usd(c.get('account_value'))} | {pf_s} | {win_s} | "
               f"{avg_s} | {' '.join(fl) or '—'} | {ago(last, now_ms)} |")
        w_()
        per = defaultdict(lambda: [0, 0.0])
        for h in hits:
            per[h["coin"]][0] += 1
            per[h["coin"]][1] += h["notional"]
        w_("### Per coin")
        w_()
        w_("| coin | trades | total notional |")
        w_("|---|---:|---:|")
        for coin, (n, tot) in sorted(per.items(), key=lambda kv: -kv[1][1]):
            w_(f"| {coin} | {n} | {usd(tot)} |")
    w_()

    if a.json:
        blob = []
        for h in hits:
            c = ctx.get(h["wallet"], {})
            st = c.get("stats") or {}
            mm, hg = flags.get(h["wallet"], (None, None))
            lv = live.get((h["wallet"], h["coin"]))
            pos = (c.get("pos") or {}).get(h["coin"])
            blob.append({**h,
                         "account_value": c.get("account_value"),
                         "gross": c.get("gross"), "n_assets": c.get("n_assets"),
                         "pf": st.get("pf"), "win": st.get("win"),
                         "trades": st.get("trades"), "maker": st.get("maker"),
                         "avg_notional": st.get("avg_notional"),
                         "mm": mm, "hedger": hg,
                         "cluster": (c.get("cluster") or {}).get("id"),
                         "pos_now": (lv["value"] if lv else
                                     (abs(pos["notional"]) if pos else None)),
                         "pos_sz": (lv["szi"] if lv else
                                    (pos["size"] if pos else None)),
                         "upnl": (lv["upnl"] if lv else
                                  (pos["upnl"] if pos else None)),
                         "last_in_coin": (c.get("last_in_coin") or {}).get(h["coin"])})
        Path(a.json).write_text(json.dumps({
            "generated": now.isoformat(), "hours": a.hours,
            "min_notional": a.min_notional, "coin": a.coin,
            "sweep_spacing_min": spacing,
            "cycle_from": str(past_cycle), "cycle_to": str(now_cycle),
            "rows": blob}, default=str, indent=1), encoding="utf-8")
        print(f"json: {a.json}")

    REPORTS.mkdir(parents=True, exist_ok=True)
    out = REPORTS / f"fresh_shorts_{now:%Y%m%d_%H%M%S}.md"
    out.write_text("\n".join(L) + "\n", encoding="utf-8")
    print(f"\nwritten: {out}")
    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
