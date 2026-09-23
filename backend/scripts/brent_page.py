#!/usr/bin/env python3
"""Regenerate the BRENTOIL positioning payload served at smindex.xyz/brent.html

Writes `brent.json` into the frontend dist directory. The page fetches that file
once a minute and swaps the rendered fragments in, so the browser never reloads
and the scroll position is kept.

The payload carries pre-rendered HTML fragments rather than raw rows on purpose:
the rendering then lives in exactly one place instead of being duplicated in
Python here and JavaScript on the page, where the two would drift.

Read-only against the database and the venue. Writes one file, atomically.

    python backend/scripts/brent_page.py --out /var/www/terminal/frontend/dist
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.request
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

import pymysql

DASH = '<span class="dim">&mdash;</span>'
COIN_LIKE = "%BRENT%"
COIN = "xyz:BRENTOIL"
DEX = "xyz"
HL_INFO = "https://api.hyperliquid.xyz/info"
ENV = Path("/var/www/terminal/backend/.env")


# ------------------------------------------------------------------ plumbing
def db():
    env = {}
    for line in ENV.read_text(encoding="utf-8").splitlines():
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


def hl(payload, tries=3):
    for i in range(tries):
        try:
            r = urllib.request.Request(HL_INFO, data=json.dumps(payload).encode(),
                                       headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(r, timeout=20) as resp:
                return json.load(resp)
        except Exception:
            if i == tries - 1:
                raise
            time.sleep(1.5 * (i + 1))


def usd(v, dp=0):
    if v is None:
        return '<span class="dim">—</span>'
    return ("−$" if v < 0 else "$") + f"{abs(v):,.{dp}f}"


def ts(ms, fmt="%m-%d %H:%M"):
    return datetime.fromtimestamp(ms / 1000, timezone.utc).strftime(fmt)


def dur(ms):
    h = ms / 3600000
    if h < 1:
        return f"{ms/60000:.0f} min"
    return f"{h:.1f}h" if h < 48 else f"{h/24:.1f}d"


# ------------------------------------------------------------------ episodes
OS_, OL_, CS_, CL_ = "Open Short", "Open Long", "Close Short", "Close Long"
FLS, FSL = "Long > Short", "Short > Long"


def episodes_for(fills, w):
    """Walk a wallet's fills into flat->flat episodes. A flip closes one episode
    and opens the next within the same fill."""
    eps, cur = [], None

    def start(side, t, px, sz, cr):
        return dict(wallet=w, side=side, open_ts=t, sz=sz, notl=px * sz, fills=1,
                    pnl=0.0, last_open=t, exit_sz=0.0, exit_notl=0.0,
                    n_open=1, taker=1 if cr else 0)

    def fin(ep, t):
        ep["close_ts"] = t
        ep["entry"] = ep["notl"] / ep["sz"] if ep["sz"] else 0
        ep["exit"] = ep["exit_notl"] / ep["exit_sz"] if ep["exit_sz"] else None
        eps.append(ep)

    for t, d, px, sz, pnl, cr in fills:
        if d in (OS_, OL_):
            side = "short" if d == OS_ else "long"
            if cur and cur["side"] == side:
                cur["sz"] += sz
                cur["notl"] += px * sz
                cur["fills"] += 1
                cur["n_open"] += 1
                cur["last_open"] = t
                cur["taker"] += 1 if cr else 0
            else:
                if cur:
                    fin(cur, t)
                cur = start(side, t, px, sz, cr)
        elif d in (CS_, CL_):
            if cur:
                cur["exit_sz"] += sz
                cur["exit_notl"] += px * sz
                cur["pnl"] += pnl
                cur["fills"] += 1
                if cur["exit_sz"] >= cur["sz"] - 1e-6:
                    fin(cur, t)
                    cur = None
        elif d in (FLS, FSL):
            if cur:
                cur["exit_sz"] += sz
                cur["exit_notl"] += px * sz
                cur["pnl"] += pnl
                cur["fills"] += 1
                fin(cur, t)
            cur = start("short" if d == FLS else "long", t, px, sz, cr)
    if cur:
        cur["close_ts"] = None
        cur["entry"] = cur["notl"] / cur["sz"] if cur["sz"] else 0
        cur["exit"] = cur["exit_notl"] / cur["exit_sz"] if cur["exit_sz"] else None
        eps.append(cur)
    return eps


# ---------------------------------------------------------------- collection
def collect():
    now = datetime.now(timezone.utc)
    now_ms = int(now.timestamp() * 1000)
    conn = db()
    cur = conn.cursor()

    cur.execute("SELECT wallet_address, ts_ms, dir, px, sz, closed_pnl, crossed "
                "FROM hl_fill_events WHERE coin LIKE %s ORDER BY wallet_address, ts_ms, id",
                (COIN_LIKE,))
    by_w = defaultdict(list)
    for w, t, d, px, sz, pnl, cr in cur.fetchall():
        by_w[w.lower()].append((int(t), str(d or ""), float(px), float(sz),
                                float(pnl or 0), bool(cr)))

    eps = []
    for w, fl in by_w.items():
        eps += episodes_for(fl, w)

    wallets = sorted(by_w)
    cur.execute("SELECT wallet_address, trades_7d, win_rate_7d, profit_factor, maker_ratio, "
                "avg_trade_notional FROM trader_fill_stats WHERE wallet_address IN %s",
                (tuple(wallets),))
    stats = {r[0].lower(): r for r in cur.fetchall()}
    cur.execute("SELECT wallet, is_likely_hedger FROM analytics_wallet_flags "
                "WHERE wallet IN %s AND is_likely_hedger IS NOT NULL", (tuple(wallets),))
    hedge = {r[0].lower(): bool(r[1]) for r in cur.fetchall()}

    # price tape from our own fills
    cur.execute("SELECT FLOOR(ts_ms/3600000)*3600000 h, MIN(px), MAX(px), "
                "SUM(px*sz)/SUM(sz), SUM(sz) FROM hl_fill_events "
                "WHERE coin LIKE %s GROUP BY h ORDER BY h", (COIN_LIKE,))
    series = [dict(t=int(h), lo=float(lo), hi=float(hi), vwap=float(vw), vol=float(v))
              for h, lo, hi, vw, v in cur.fetchall()]
    cur.execute("SELECT MIN(ts_ms), COUNT(*) FROM hl_fill_events WHERE coin LIKE %s",
                (COIN_LIKE,))
    cov_from, n_fills = cur.fetchone()

    # big short-opening fills
    cur.execute("SELECT wallet_address, ts_ms, dir, px, sz FROM hl_fill_events "
                "WHERE coin LIKE %s AND dir IN ('Open Short','Long > Short') "
                "AND px*sz >= 100000 ORDER BY ts_ms DESC", (COIN_LIKE,))
    bigfills = [dict(wallet=w.lower(), ts=int(t), dir=d, px=float(px), sz=float(sz),
                     notional=float(px) * float(sz)) for w, t, d, px, sz in cur.fetchall()]
    conn.close()

    # live venue state
    live, acct = {}, {}
    for w in wallets:
        try:
            d = hl({"type": "clearinghouseState", "user": w, "dex": DEX})
        except Exception:
            continue
        ms = d.get("marginSummary") or {}
        acct[w] = float(ms.get("accountValue") or 0)
        for ap in d.get("assetPositions", []):
            p = ap.get("position") or {}
            if p.get("coin") == COIN:
                live[w] = dict(szi=float(p.get("szi") or 0),
                               entry=float(p.get("entryPx")) if p.get("entryPx") else None,
                               value=float(p.get("positionValue") or 0),
                               upnl=float(p.get("unrealizedPnl") or 0),
                               lev=(p.get("leverage") or {}).get("value"))

    mark = prev = None
    try:
        m = hl({"type": "metaAndAssetCtxs", "dex": DEX})
        for i, u in enumerate(m[0]["universe"]):
            if "BRENT" in u["name"].upper():
                c = m[1][i]
                mark = float(c.get("markPx") or 0)
                prev = float(c.get("prevDayPx") or 0)
                break
    except Exception:
        pass
    if mark is None and series:
        mark = series[-1]["vwap"]

    return dict(now=now, now_ms=now_ms, eps=eps, by_w=by_w, stats=stats, hedge=hedge,
                series=series, cov_from=int(cov_from) if cov_from else None,
                n_fills=int(n_fills or 0), bigfills=bigfills, live=live, acct=acct,
                mark=mark, prev=prev, wallets=wallets)


# ------------------------------------------------------------------ renderers
def build(d):
    now_ms = d["now_ms"]
    live, eps, series = d["live"], d["eps"], d["series"]
    mark, prev = d["mark"], d["prev"]
    high = max((s["hi"] for s in series), default=mark or 0)

    def ago(ms):
        return dur(now_ms - ms)

    def tags(w):
        st = d["stats"].get(w)
        mk = float(st[4]) if st and st[4] is not None else None
        out = []
        if mk is not None and mk >= .6 and (st[1] or 0) >= 500:
            out.append('<span class="tag tag-mm">MM</span>')
        if mk == 0.0:
            out.append('<span class="tag tag-taker">pure taker</span>')
        if d["hedge"].get(w):
            out.append('<span class="tag tag-hdg">hedger</span>')
        return "".join(out) or '<span class="dim">—</span>'

    def maker(w):
        st = d["stats"].get(w)
        return f"{float(st[4]):.2f}" if st and st[4] is not None else "—"

    def open_cell(first, last, n):
        if not first:
            return '<span class="dim">not observed</span>'
        span = (dur(last - first) + " build") if (n and n > 1 and last > first) else "single fill"
        return f'{ts(first)}<span class="sub2">{span} · {ago(first)} ago</span>'

    def close_cell(close_ts, open_ts):
        if close_ts:
            return (f'{ts(close_ts)}<span class="sub2">held {dur(close_ts-open_ts)}'
                    f' · {ago(close_ts)} ago</span>')
        return ('<span class="live">still open</span>'
                + (f'<span class="sub2">{ago(open_ts)} so far</span>' if open_ts else ""))

    # per-wallet current episode + covered size
    cur_ep, open_sz = {}, defaultdict(float)
    for e in eps:
        if e["side"] == "short":
            open_sz[e["wallet"]] += e["sz"]
        if e["close_ts"] is None:
            p = cur_ep.get(e["wallet"])
            if p is None or e["notl"] > p["notl"]:
                cur_ep[e["wallet"]] = e

    shorts = sorted([(v["value"], w, v) for w, v in live.items() if v["szi"] < 0], reverse=True)
    longs = sorted([(v["value"], w, v) for w, v in live.items() if v["szi"] > 0], reverse=True)

    def pos_rows(items, is_short):
        out = []
        for i, (val, w, v) in enumerate(items, 1):
            ep = cur_ep.get(w)
            first = ep["open_ts"] if ep else None
            last = ep["last_open"] if ep else None
            n = ep["n_open"] if ep else None
            cov = (open_sz[w] / abs(v["szi"]) * 100) if v["szi"] else 0
            covcell = (f'<td class="num mono {"lowcov" if cov < 50 else ""}">{cov:.0f}%</td>'
                       if is_short else "")
            makercell = f'<td class="num mono">{maker(w)}</td>' if is_short else ""
            out.append(
                f'<tr><td class="num dim">{i}</td>'
                f'<td><code class="addr" title="{w}">{w[:12]}…</code></td>'
                f'<td class="num mono strong">{v["szi"]:,.0f}</td>'
                f'<td class="num mono strong">{usd(val)}</td>'
                f'<td class="num mono">{(v["entry"] or 0):.2f}</td>'
                f'<td class="mono when">{open_cell(first, last, n)}</td>'
                f'<td class="mono when">{close_cell(None, first)}</td>'
                f'<td class="num mono {"pos" if v["upnl"] >= 0 else "neg"}">{usd(v["upnl"])}</td>'
                f'<td class="num mono">{v["lev"] or "—"}×</td>'
                f'{makercell}{covcell}<td>{tags(w)}</td></tr>')
        return "\n".join(out)

    trips = sorted([e for e in eps if e["notl"] >= 25000], key=lambda e: -e["notl"])
    trip_rows = []
    for i, t in enumerate(trips, 1):
        held = (f'<span class="mono">{dur(t["close_ts"]-t["open_ts"])}</span>' if t["close_ts"]
                else f'<span class="live">{dur(now_ms-t["open_ts"])}</span>')
        rz = usd(t["pnl"]) if t["close_ts"] else '<span class="dim">unrealised</span>'
        cls = "pos" if t["pnl"] > 0 else ("neg" if t["pnl"] < 0 else "")
        exit_s = f'{t["exit"]:.2f}' if t["exit"] else DASH
        trip_rows.append(
            f'<tr><td class="num dim">{i}</td>'
            f'<td><code class="addr" title="{t["wallet"]}">{t["wallet"][:12]}…</code></td>'
            f'<td><span class="side side-{t["side"]}">{t["side"]}</span></td>'
            f'<td class="num mono strong">{t["sz"]:,.0f}</td>'
            f'<td class="num mono strong">{usd(t["notl"])}</td>'
            f'<td class="num mono">{t["entry"]:.2f}</td>'
            f'<td class="num mono">{exit_s}</td>'
            f'<td class="mono when">{open_cell(t["open_ts"], t["last_open"], t["n_open"])}</td>'
            f'<td class="mono when">{close_cell(t["close_ts"], t["open_ts"])}</td>'
            f'<td class="num">{held}</td>'
            f'<td class="num mono {cls}">{rz}</td>'
            f'<td class="num mono dim">{t["fills"]}</td>'
            f'<td>{tags(t["wallet"])}</td></tr>')

    def ep_at(w, t):
        best = None
        for e in eps:
            if e["wallet"] != w or e["side"] != "short":
                continue
            if e["open_ts"] <= t <= (e["close_ts"] or now_ms):
                if best is None or e["open_ts"] > best["open_ts"]:
                    best = e
        return best

    fill_rows = []
    for i, r in enumerate(d["bigfills"], 1):
        e = ep_at(r["wallet"], r["ts"])
        fill_rows.append(
            f'<tr><td class="num dim">{i}</td>'
            f'<td class="mono">{ts(r["ts"])}<span class="sub2">{ago(r["ts"])} ago</span></td>'
            f'<td><code class="addr" title="{r["wallet"]}">{r["wallet"][:12]}…</code></td>'
            f'<td class="mono">{r["dir"]}</td>'
            f'<td class="num mono">{r["px"]:.3f}</td>'
            f'<td class="num mono strong">{r["sz"]:,.0f}</td>'
            f'<td class="num mono strong">{usd(r["notional"])}</td>'
            f'<td class="mono when">{ts(e["open_ts"]) if e else DASH}</td>'
            f'<td class="mono when">'
            f'{close_cell(e["close_ts"], e["open_ts"]) if e else DASH}</td>'
            f'<td>{tags(r["wallet"])}</td></tr>')

    dd = defaultdict(list)
    for s in series:
        dd[ts(s["t"], "%m-%d")].append(s)
    day_rows = []
    for day, ss in sorted(dd.items()):
        o, cl = ss[0]["vwap"], ss[-1]["vwap"]
        ch = (cl / o - 1) * 100
        op = sum(1 for e in trips if ts(e["open_ts"], "%m-%d") == day)
        cd = sum(1 for e in trips if e["close_ts"] and ts(e["close_ts"], "%m-%d") == day)
        day_rows.append(
            f'<tr><td class="mono">{day}</td><td class="num mono">{o:.3f}</td>'
            f'<td class="num mono">{max(x["hi"] for x in ss):.3f}</td>'
            f'<td class="num mono">{min(x["lo"] for x in ss):.3f}</td>'
            f'<td class="num mono">{cl:.3f}</td>'
            f'<td class="num mono {"pos" if ch >= 0 else "neg"}">{ch:+.2f}%</td>'
            f'<td class="num mono dim">{sum(x["vol"] for x in ss):,.0f}</td>'
            f'<td class="num mono">{op or "—"}</td>'
            f'<td class="num mono">{cd or "—"}</td></tr>')

    # chart
    CW, CH, PL, PR, PT, PB = 1180, 300, 52, 118, 18, 34
    chart = ""
    if len(series) > 1:
        t0, t1 = series[0]["t"], series[-1]["t"]
        ylo = min(s["lo"] for s in series) - .4
        yhi = max(s["hi"] for s in series) + .4
        X = lambda t: PL + (t - t0) / (t1 - t0) * (CW - PL - PR)
        Y = lambda p: PT + (yhi - p) / (yhi - ylo) * (CH - PT - PB)
        pts = " ".join(f'{X(s["t"]):.1f},{Y(s["vwap"]):.1f}' for s in series)
        area = f'{X(t0):.1f},{Y(ylo):.1f} ' + pts + f' {X(t1):.1f},{Y(ylo):.1f}'
        yg = "".join(
            f'<line x1="{PL}" y1="{Y(p):.1f}" x2="{CW-PR}" y2="{Y(p):.1f}" class="grid"/>'
            f'<text x="{PL-8}" y="{Y(p)+3.5:.1f}" class="ax" text-anchor="end">{p}</text>'
            for p in range(int(ylo) + 1, int(yhi) + 1) if p % 2 == 0)
        seen, xg = set(), ""
        for s in series:
            lab = ts(s["t"], "%m-%d")
            if lab in seen:
                continue
            seen.add(lab)
            xg += (f'<line x1="{X(s["t"]):.1f}" y1="{PT}" x2="{X(s["t"]):.1f}" '
                   f'y2="{CH-PB}" class="grid"/>'
                   f'<text x="{X(s["t"]):.1f}" y="{CH-PB+16}" class="ax" '
                   f'text-anchor="middle">{lab}</text>')
        marks = ""
        for val, w, v in shorts:
            ep = cur_ep.get(w)
            if not ep or not (t0 <= ep["open_ts"] <= t1):
                continue
            st = d["stats"].get(w)
            mk = float(st[4]) if st and st[4] is not None else None
            cls = ("mk-taker" if mk == 0.0 else
                   "mk-mm" if (mk is not None and mk >= .6 and (st[1] or 0) >= 500) else "mk-oth")
            r = 3.2 + min(6.5, (val / 1_000_000) * 2.2)
            marks += (f'<circle cx="{X(ep["open_ts"]):.1f}" cy="{Y(v["entry"] or 0):.1f}" '
                      f'r="{r:.1f}" class="mk {cls}"><title>{w[:12]}… — {v["szi"]:,.0f} units, '
                      f'entry {(v["entry"] or 0):.2f}, opened {ts(ep["open_ts"])}</title></circle>')
        chart = (f'<svg viewBox="0 0 {CW} {CH}" class="chart" role="img" '
                 f'aria-label="Brent hourly price with short entry markers">'
                 f'<defs><linearGradient id="fade" x1="0" y1="0" x2="0" y2="1">'
                 f'<stop offset="0" class="g0"/><stop offset="1" class="g1"/></linearGradient></defs>'
                 f'{yg}{xg}<polygon points="{area}" fill="url(#fade)"/>'
                 f'<polyline points="{pts}" class="line" fill="none"/>'
                 f'<line x1="{PL}" y1="{Y(high):.1f}" x2="{CW-PR}" y2="{Y(high):.1f}" class="hiline"/>'
                 f'<text x="{CW-PR+7}" y="{Y(high)+3.5:.1f}" class="axhi">{high:.2f} high</text>'
                 f'<line x1="{PL}" y1="{Y(mark):.1f}" x2="{CW-PR}" y2="{Y(mark):.1f}" class="nowline"/>'
                 f'<text x="{CW-PR+7}" y="{Y(mark)+3.5:.1f}" class="axnow">{mark:.2f} now</text>'
                 f'{marks}</svg>')

    closed = [e for e in trips if e["close_ts"]]
    n_nocov = sum(1 for val, w, v in shorts if open_sz[w] < abs(v["szi"]) * .5)

    return {
        "generated": d["now"].isoformat(),
        "generated_ms": now_ms,
        "stats": {
            "mark": f"{mark:.3f}" if mark else "—",
            "chg": f"{(mark/prev-1)*100:.2f}" if (mark and prev) else "—",
            "prev": f"{prev:.3f}" if prev else "—",
            "high": f"{high:.2f}",
            "fromhigh": f"{(mark/high-1)*100:.1f}" if (mark and high) else "—",
            "nshort": len(shorts), "nlong": len(longs),
            "totshort": usd(sum(x[0] for x in shorts)),
            "totlong": usd(sum(x[0] for x in longs)),
            "upnl": usd(sum(x[2]["upnl"] for x in shorts)),
            "nwallets": len(d["wallets"]), "nfills": f'{d["n_fills"]:,}',
            "nbig": len(d["bigfills"]), "covfrom": ts(d["cov_from"]) if d["cov_from"] else "—",
            "ntrips": len(trips), "nclosed": len(closed),
            "nlive": len(trips) - len(closed),
            "realized": usd(sum(e["pnl"] for e in closed)),
            "nnocov": n_nocov,
        },
        "html": {
            "chart": chart,
            "shortRows": pos_rows(shorts, True),
            "longRows": pos_rows(longs, False),
            "tripRows": "\n".join(trip_rows),
            "fillRows": "\n".join(fill_rows),
            "dayRows": "\n".join(day_rows),
        },
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="/var/www/terminal/frontend/dist")
    a = ap.parse_args()
    t0 = time.time()
    payload = build(collect())
    out = Path(a.out) / "brent.json"
    tmp = out.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, separators=(",", ":")), encoding="utf-8")
    os.replace(tmp, out)          # atomic: readers never see a half-written file
    print(f"{datetime.now(timezone.utc):%H:%M:%S} wrote {out} "
          f"({out.stat().st_size:,}B, {time.time()-t0:.1f}s, "
          f"{payload['stats']['nshort']} short / {payload['stats']['nlong']} long)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
