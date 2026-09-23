#!/usr/bin/env python3
"""BRENT round-trip report for tracked wallets. READ ONLY.

Reads fills, reconstructs round trips, writes a markdown report. It never
writes to the database and never changes schema; the only network call is a
read-only Hyperliquid `clearinghouseState` for wallets that still hold a
position.

Two things worth knowing before reading the numbers:

* **The two fill sources are not equivalent.** `hl_fill_events` stores a
  trimmed fill (no `startPosition`, no `fee`). `leader_trade_events.raw` keeps
  the venue's whole fill object, including both. So fees and start-position are
  only available for wallets covered by the leader tracker, and the report says
  so per wallet rather than printing a zero.
* **Coin naming differs by table**: `xyz:BRENTOIL` in `hl_fill_events`,
  `XYZ:BRENTOIL` in `leader_trade_events`. Matching is case-insensitive here;
  a case-sensitive query silently returns nothing from one of them.

BRENTOIL is a HIP-3 builder-dex market, so the canonical `clearinghouseState`
does NOT contain it — verified: the canonical call returns BTC/ETH/SOL/… and no
BRENT, while `{"dex": "xyz"}` returns the position. Open positions are read with
the dex parameter.

    python backend/scripts/brent_trades_report.py --hours 24 --min-notional 10000
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

ROOT = Path(__file__).resolve().parents[2]
REPORTS = ROOT / "backend" / "reports"
HL_INFO = "https://api.hyperliquid.xyz/info"


# --------------------------------------------------------------- plumbing ---
def db():
    env = {}
    for line in (ROOT / "backend" / ".env").read_text(encoding="utf-8").splitlines():
        if "=" in line and not line.strip().startswith("#"):
            k, v = line.split("=", 1)
            env[k.strip()] = v.strip()
    url = env.get("DATABASE_URL", "")
    body = url.split("://", 1)[1]
    creds, hostpart = body.split("@", 1)
    user, pwd = creds.split(":", 1)
    hostport, name = hostpart.split("/", 1)
    host, port = hostport.split(":")
    return pymysql.connect(host=host, port=int(port), user=user, password=pwd,
                           database=name.split("?")[0], charset="utf8mb4")


def hl_post(payload: dict) -> dict:
    req = urllib.request.Request(
        HL_INFO, data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


def ts(ms) -> str:
    return datetime.fromtimestamp(ms / 1000, timezone.utc).strftime("%m-%d %H:%M")


def usd(v, d=2) -> str:
    if v is None:
        return "—"
    s = f"{abs(v):,.{d}f}"
    return f"-${s}" if v < 0 else f"${s}"


# ------------------------------------------------------------- collection ---
def brent_variants(cur) -> list[str]:
    """Every BRENT spelling actually present, in either table."""
    found = set()
    cur.execute("SELECT DISTINCT coin FROM hl_fill_events WHERE coin LIKE %s", ("%BRENT%",))
    found |= {r[0] for r in cur.fetchall()}
    cur.execute("SELECT DISTINCT symbol FROM leader_trade_events WHERE symbol LIKE %s", ("%BRENT%",))
    found |= {r[0] for r in cur.fetchall()}
    return sorted(found)


def load_fills(cur, since_ms: int) -> tuple[list[dict], dict]:
    """Union of both sources, de-duplicated on the venue trade id (tid).

    leader_trade_events wins on a tid collision: its `raw` carries the full
    venue fill (fee, startPosition) that hl_fill_events drops.
    """
    fills: dict[str, dict] = {}
    cov = {"leader": defaultdict(lambda: [None, None, 0]),
           "hlfill": defaultdict(lambda: [None, None, 0])}

    cur.execute(
        "SELECT trader_wallet, detected_at, raw FROM leader_trade_events "
        "WHERE UPPER(symbol) LIKE %s AND detected_at >= %s ORDER BY detected_at",
        ("%BRENT%", datetime.fromtimestamp(since_ms / 1000, timezone.utc).replace(tzinfo=None)))
    for wallet, det, raw in cur.fetchall():
        try:
            f = (json.loads(raw) if isinstance(raw, (str, bytes)) else raw).get("fill") or {}
        except Exception:
            continue
        tid = str(f.get("tid") or "")
        if not tid:
            continue
        w = wallet.lower()
        t = int(f.get("time") or 0)
        fills[tid] = dict(
            wallet=w, tid=tid, ts=t, coin=f.get("coin", ""), dir=f.get("dir", ""),
            px=float(f.get("px") or 0), sz=float(f.get("sz") or 0),
            closed_pnl=float(f.get("closedPnl") or 0),
            fee=float(f.get("fee") or 0), fee_known=True,
            start_position=(float(f["startPosition"]) if f.get("startPosition") not in (None, "") else None),
            src="leader")
        c = cov["leader"][w]
        c[0] = t if c[0] is None else min(c[0], t)
        c[1] = t if c[1] is None else max(c[1], t)
        c[2] += 1

    cur.execute(
        "SELECT wallet_address, ts_ms, coin, dir, px, sz, closed_pnl, tid "
        "FROM hl_fill_events WHERE UPPER(coin) LIKE %s AND ts_ms >= %s ORDER BY ts_ms",
        ("%BRENT%", since_ms))
    for wallet, t, coin, d, px, sz, pnl, tid in cur.fetchall():
        w = wallet.lower()
        c = cov["hlfill"][w]
        t = int(t)
        c[0] = t if c[0] is None else min(c[0], t)
        c[1] = t if c[1] is None else max(c[1], t)
        c[2] += 1
        key = str(tid or f"{w}-{t}-{px}-{sz}")
        if key in fills:            # leader row already has fee + startPosition
            continue
        fills[key] = dict(
            wallet=w, tid=key, ts=t, coin=coin, dir=d or "",
            px=float(px or 0), sz=float(sz or 0),
            closed_pnl=float(pnl or 0),
            fee=0.0, fee_known=False,     # this table does not store fees
            start_position=None, src="hlfill")
    return sorted(fills.values(), key=lambda f: f["ts"]), cov


# --------------------------------------------------------- reconstruction ---
def round_trips(fills: list[dict]) -> list[dict]:
    """Walk one wallet's fills in time order and cut them into round trips.

    `dir` alone is enough: "Open/Close Long|Short" says both the side and
    whether size is being added or taken off, and a "Long > Short" flip closes
    one trip and opens the next. `startPosition` is used only as a cross-check
    where the tracker supplied it — it is absent from hl_fill_events.
    """
    trips, cur = [], None

    def close(trip, f):
        trip["exit_ts"] = f["ts"]
        trip["exit_notional"] += f["px"] * f["sz"]
        trip["exit_size"] += f["sz"]
        trip["exit_px"] = trip["exit_notional"] / trip["exit_size"] if trip["exit_size"] else None
        trip["pnl"] += f["closed_pnl"]
        trip["fees"] += f["fee"]
        trip["fee_known"] &= f["fee_known"]
        trip["fills"] += 1

    def start(f, side):
        return dict(wallet=f["wallet"], coin=f["coin"], side=side,
                    open_ts=f["ts"], exit_ts=None,
                    entry_notional=f["px"] * f["sz"], entry_size=f["sz"],
                    exit_notional=0.0, exit_size=0.0, exit_px=None,
                    pnl=f["closed_pnl"], fees=f["fee"], fee_known=f["fee_known"],
                    fills=1, start_position=f.get("start_position"))

    for f in fills:
        d = (f["dir"] or "").strip()
        low = d.lower()
        if ">" in d:                                   # flip: close then open
            side_from = "long" if low.startswith("long") else "short"
            side_to = "short" if side_from == "long" else "long"
            if cur is not None:
                close(cur, f)
                cur["entry_px"] = cur["entry_notional"] / cur["entry_size"] if cur["entry_size"] else None
                cur["status"] = "closed"
                trips.append(cur)
            cur = start(f, side_to)
            continue
        side = "long" if "long" in low else "short" if "short" in low else None
        opening = low.startswith("open")
        if opening or (cur is None and side):
            if cur is not None and cur["side"] == side:
                cur["entry_notional"] += f["px"] * f["sz"]
                cur["entry_size"] += f["sz"]
                cur["fees"] += f["fee"]
                cur["fee_known"] &= f["fee_known"]
                cur["fills"] += 1
            else:
                if cur is not None:
                    cur["entry_px"] = cur["entry_notional"] / cur["entry_size"] if cur["entry_size"] else None
                    cur["status"] = "closed"
                    trips.append(cur)
                cur = start(f, side or "?")
        else:                                          # closing
            if cur is None:
                # a close with no open in the window: the position was built
                # before it started. Reported as a partial trip, not dropped.
                cur = dict(wallet=f["wallet"], coin=f["coin"],
                           side="long" if "long" in low else "short",
                           open_ts=None, exit_ts=None,
                           entry_notional=0.0, entry_size=0.0,
                           exit_notional=0.0, exit_size=0.0, exit_px=None,
                           pnl=0.0, fees=0.0, fee_known=f["fee_known"], fills=0,
                           start_position=f.get("start_position"),
                           pre_window=True)
            close(cur, f)
            remaining = cur["entry_size"] - cur["exit_size"]
            if cur["entry_size"] > 0 and remaining <= 1e-9:
                cur["entry_px"] = cur["entry_notional"] / cur["entry_size"]
                cur["status"] = "closed"
                trips.append(cur)
                cur = None
    if cur is not None:
        cur["entry_px"] = (cur["entry_notional"] / cur["entry_size"]) if cur["entry_size"] else None
        cur["status"] = "open"
        trips.append(cur)
    return trips


def open_positions(wallets: list[str], dexes: list[str]) -> dict:
    """Live state for still-open positions. BRENTOIL is HIP-3, so the canonical
    clearinghouseState does not contain it — the builder dex must be named."""
    out: dict[tuple[str, str], dict] = {}
    for w in wallets:
        for dex in dexes:
            try:
                d = hl_post({"type": "clearinghouseState", "user": w, "dex": dex})
            except Exception as exc:                   # noqa: BLE001
                print(f"  ! clearinghouseState failed for {w[:10]} dex={dex}: {exc}",
                      file=sys.stderr)
                continue
            for ap in d.get("assetPositions", []):
                p = ap.get("position") or {}
                if "BRENT" not in str(p.get("coin", "")).upper():
                    continue
                out[(w, p["coin"])] = dict(
                    szi=float(p.get("szi") or 0),
                    entry=float(p.get("entryPx") or 0) if p.get("entryPx") else None,
                    value=float(p.get("positionValue") or 0),
                    upnl=float(p.get("unrealizedPnl") or 0),
                    lev=(p.get("leverage") or {}).get("value"))
    return out


# -------------------------------------------------------------------- run ---
def main() -> int:
    ap = argparse.ArgumentParser(description="BRENT round-trip report (read-only)")
    ap.add_argument("--hours", type=int, default=24)
    ap.add_argument("--min-notional", type=float, default=10000.0)
    ap.add_argument("--json", metavar="PATH",
                    help="also dump every trip (including sub-threshold) as JSON")
    a = ap.parse_args()

    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    since_ms = now_ms - a.hours * 3600_000

    conn = db()
    cur = conn.cursor()
    variants = brent_variants(cur)
    fills, cov = load_fills(cur, since_ms)

    by_wallet: dict[str, list[dict]] = defaultdict(list)
    for f in fills:
        by_wallet[f["wallet"]].append(f)

    trips: list[dict] = []
    for w, fl in by_wallet.items():
        trips.extend(round_trips(sorted(fl, key=lambda x: x["ts"])))

    live = open_positions(sorted({t["wallet"] for t in trips if t["status"] == "open"}),
                          sorted({c.split(":")[0] for c in variants if ":" in c}))
    for t in trips:
        if t["status"] == "open":
            k = (t["wallet"], t["coin"])
            m = live.get(k) or next((v for (w2, c2), v in live.items()
                                     if w2 == t["wallet"]), None)
            t["live"] = m
            t["upnl"] = m["upnl"] if m else None

    for t in trips:
        # A closed trip is sized by what it built. An OPEN trip must be sized by
        # what is still open (built minus partially closed) — using entry_size
        # would report a 9,296-lot position that is actually 679 lots.
        if t["status"] == "open":
            t["size"] = max(0.0, t["entry_size"] - t["exit_size"])
            t["built"] = t["entry_size"]
        else:
            t["size"] = t["entry_size"] or t["exit_size"]
            t["built"] = t["entry_size"]
        px = t["entry_px"] or t["exit_px"] or 0
        t["notional"] = t["size"] * px
    kept = [t for t in trips if t["notional"] >= a.min_notional]
    kept.sort(key=lambda t: -t["notional"])

    # ---- coverage header ----
    cov_rows = []
    for w in sorted(set(list(cov["leader"]) + list(cov["hlfill"]))):
        l, h = cov["leader"].get(w), cov["hlfill"].get(w)
        cov_rows.append((w, l, h))

    L: list[str] = []
    def w_(s=""):
        L.append(s)
        print(s)

    w_(f"# BRENT trade report — {datetime.now(timezone.utc):%Y-%m-%d %H:%M} UTC")
    w_()
    w_(f"Window **{a.hours}h** (since {ts(since_ms)} UTC) · min notional "
       f"**{usd(a.min_notional, 0)}** · read-only, no writes, no schema change.")
    w_()
    w_("## Coverage — read this before concluding a wallet did not trade")
    w_()
    w_("A wallet missing from the table below has **no fill coverage**, which is "
       "not the same as no trade. Two sources are unioned and de-duplicated on "
       "the venue trade id:")
    w_()
    w_("| wallet | leader tracker (fills, window) | hl fill sampler (fills, window) | fees available |")
    w_("|---|---|---|---|")
    for wal, l, h in cov_rows:
        ls = f"{l[2]} fills, {ts(l[0])}→{ts(l[1])}" if l else "— none"
        hs = f"{h[2]} fills, {ts(h[0])}→{ts(h[1])}" if h else "— none"
        w_(f"| `{wal}` | {ls} | {hs} | {'yes' if l else '**no**'} |")
    w_()
    w_(f"BRENT symbols present in the data: {', '.join('`'+v+'`' for v in variants)}. "
       "Note the case differs by table (`xyz:` vs `XYZ:`); matching is "
       "case-insensitive here.")
    w_()
    w_("`hl_fill_events` stores a trimmed fill with **no fee and no "
       "startPosition**; only `leader_trade_events.raw` keeps the venue's full "
       "object. Rows sourced only from the sampler therefore show fees as "
       "*n/a* rather than `$0.00`.")
    w_()
    w_("Open positions are read live from `clearinghouseState` **with the "
       "builder-dex parameter** — BRENTOIL is a HIP-3 market and the canonical "
       "call does not return it (verified: canonical shows BTC/ETH/SOL/… and no "
       "BRENT; `dex=xyz` returns the position).")
    w_()
    w_(f"## Trades — {len(kept)} of {len(trips)} round trips above the notional floor")
    w_()
    if not kept:
        w_("_No round trip cleared the notional filter in this window._")
    else:
        w_("| # | wallet | action | entry px | entry time | exit px | exit time | size | notional | pnl | fees | status |")
        w_("|---|---|---|---:|---|---:|---|---:|---:|---:|---:|---|")
        for i, t in enumerate(kept, 1):
            size = t["size"]
            if t["status"] == "closed":
                pnl_s = usd(t["pnl"])
            else:
                # realised from the partial closes inside this trip. The venue's
                # unrealised belongs to the FULL position (which may be far
                # larger than this window's slice) and is reported only in the
                # open-position table below, where it sits next to that size.
                pnl_s = usd(t["pnl"]) + " *(realised so far)*"
            epx = f"{t['entry_px']:,.4f}" if t["entry_px"] else "—"
            if t["status"] == "open":
                xpx = f"{t['exit_px']:,.4f} *(partial)*" if t["exit_px"] else "—"
                ext = ts(t["exit_ts"]) + " *(partial)*" if t["exit_ts"] else "—"
            else:
                xpx = f"{t['exit_px']:,.4f}" if t["exit_px"] else "—"
                ext = ts(t["exit_ts"]) if t["exit_ts"] else "—"
            ent = ts(t["open_ts"]) if t["open_ts"] else "*pre-window*"
            fee = usd(-abs(t["fees"])) if t["fee_known"] else "*n/a*"
            w_(f"| {i} | `{t['wallet'][:10]}…` | {t['side']} | {epx} | {ent} | "
               f"{xpx} | {ext} | {size:,.2f} | {usd(t['notional'], 0)} | "
               f"{pnl_s} | {fee} | {t['status']} |")
    w_()
    opens = [t for t in kept if t["status"] == "open"]
    if opens:
        w_("### Open positions — live venue state")
        w_()
        w_("Sizes here are the **whole** venue position, which may be larger "
           "than the slice built inside this window; the unrealised figure "
           "belongs to this size, not to the row above.")
        w_()
        w_("| wallet | coin | size (szi) | entry | position value | unrealised | lev |")
        w_("|---|---|---:|---:|---:|---:|---:|")
        for t in opens:
            m = t.get("live")
            if not m:
                w_(f"| `{t['wallet'][:10]}…` | {t['coin']} | — | — | — | *venue read failed* | — |")
                continue
            w_(f"| `{t['wallet'][:10]}…` | {t['coin']} | {m['szi']:,.2f} | "
               f"{m['entry']:,.4f} | {usd(m['value'], 0)} | {usd(m['upnl'])} | {m['lev']}x |")
        w_()
    closed = [t for t in kept if t["status"] == "closed"]
    w_(f"**Totals** — {len(closed)} closed, {len(opens)} open. "
       f"Realised P&L {usd(sum(t['pnl'] for t in closed))} across closed trips "
       f"({usd(sum(t['pnl'] for t in opens))} realised inside open ones). "
       f"Unrealised {usd(sum((t.get('upnl') or 0) for t in opens))} — that is "
       f"**position-level**, covering size built before this window too. "
       f"Fees {usd(-abs(sum(t['fees'] for t in kept if t['fee_known'])))} "
       f"(known for {sum(1 for t in kept if t['fee_known'])} of {len(kept)} rows).")

    if a.json:
        payload = dict(
            generated_at=datetime.now(timezone.utc).isoformat(),
            hours=a.hours, min_notional=a.min_notional,
            variants=variants,
            coverage=[dict(wallet=w,
                           leader=(dict(fills=l[2], first=l[0], last=l[1]) if l else None),
                           sampler=(dict(fills=h[2], first=h[0], last=h[1]) if h else None))
                      for w, l, h in cov_rows],
            open_positions=[dict(wallet=w, coin=c, **v) for (w, c), v in live.items()],
            trips=[{k: v for k, v in t.items() if k != "live"} for t in
                   sorted(trips, key=lambda x: -x["notional"])])
        Path(a.json).parent.mkdir(parents=True, exist_ok=True)
        Path(a.json).write_text(json.dumps(payload, indent=1, default=str), encoding="utf-8")
        print(f"json: {a.json}")

    REPORTS.mkdir(parents=True, exist_ok=True)
    out = REPORTS / f"brent_trades_{datetime.now(timezone.utc):%Y%m%d_%H%M%S}.md"
    out.write_text("\n".join(L) + "\n", encoding="utf-8")
    print(f"\nwritten: {out}")
    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
