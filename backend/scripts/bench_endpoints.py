#!/usr/bin/env python3
"""Endpoint latency benchmark for the optimization pass (2026-09-24).

Hits every GET endpoint the UI calls, through nginx + TLS on the real host,
one "first" call then N repeats, and writes per-endpoint first/p50/p95/max
plus status codes to a JSON file. Read-only: GET only, no POSTs.

SMINDEX's real traffic is too small for per-endpoint p95 from the access log,
so this is the before/after instrument; the timed nginx log is reported beside
it with its request counts.

    python bench_endpoints.py --host https://smindex.xyz --token-file /root/optbench/token \
        --repeats 10 --out /root/optbench/before.json
"""
from __future__ import annotations

import argparse
import json
import statistics
import time
import urllib.error
import urllib.request

A = "0x<admin-wallet>"      # admin wallet (Perpl + copy data)
PW = "0x368cd71cc5320b32b48ea823988be0d8ae44e185"     # Perpl wallet with indexed history


def endpoints(hl_wallet: str, now_s: int, lb: list[str]) -> list[tuple[str, str, bool, bool]]:
    """(page, path, needs_auth, explorer_paced)"""
    now_ms, day_ms = now_s * 1000, (now_s - 86400) * 1000     # the UI sends candle ranges in ms
    E = []
    add = lambda page, path, auth=False, paced=False: E.append((page, path, auth, paced))
    # 1 trade terminal
    for p in ("/api/market-configs", "/api/markets", "/api/exchanges", "/api/funding-compare",
              f"/api/candles/1/300/{day_ms}-{now_ms}", f"/api/candles/1/900/{day_ms}-{now_ms}",
              "/api/orderbook/1", "/api/order-flow/1", "/api/heatmap/1", "/api/funding-history/1",
              "/api/terminal-stats", "/api/block", "/api/whales/alerts", "/api/hl/basis"):
        add("terminal", p)
    for p in ("/api/positions/my", "/api/account-health", "/api/sl-tp/orders?status=active",
              "/api/trades/history", "/api/orders/history", "/api/watchlist", "/api/price-alerts"):
        add("terminal", p, auth=True)
    # 2 copy discover / profile
    # the discover list batches its visible wallets: equity 20 at a time, hl-active 25
    for p in ("/api/traders", f"/api/traders/equity?wallets={','.join(lb[:20])}&timeframe=7d&points=60&exchange=hl",
              f"/api/traders/hl-active?wallets={','.join(lb[:25])}",
              f"/api/traders/{hl_wallet}?exchange=hl", f"/api/traders/{hl_wallet}/hl-state",
              f"/api/traders/{hl_wallet}/hl-history", f"/api/traders/{hl_wallet}/stats?days=30",
              f"/api/leaders/positions/{A}", "/api/leaders", "/api/insights/wallets"):
        add("copy", p)
    for p in ("/api/copy/subscriptions", "/api/copy/stats", "/api/copy/positions",
              "/api/copy/live-status", "/api/copy/live-positions", "/api/copy/my-copies?status=all&limit=100",
              "/api/copy/execution-context"):
        add("copy", p, auth=True)
    # 3 strategies
    for p in ("/api/strategies", "/api/strategies/risk", "/api/strategies/trades/open",
              "/api/strategies/signals/recent", "/api/strategies/feed-health",
              "/api/strategies/s01_liq_sweep", "/api/strategies/m1_sweep_reclaim"):
        add("strategies", p)
    # 4 analytics
    for p in ("/api/analytics/flows", "/api/analytics/pulse", "/api/analytics/movers",
              "/api/analytics/assets", "/api/analytics/assets/BTC", "/api/analytics/smi/BTC",
              "/api/analytics/context/BTC"):
        add("analytics", p)
    # 5 wallet explorer (public, 20 req/min/IP bucket -> paced)
    for p in (f"/api/explorer/hl/{hl_wallet}", f"/api/explorer/perpl/{PW}",
              f"/api/explorer/perpl/{PW}/history", f"/api/explorer/activity/{PW}",
              f"/api/explorer/hl/{hl_wallet}/fills", f"/api/explorer/hl/{hl_wallet}/orders",
              f"/api/explorer/hl/{hl_wallet}/ledger", f"/api/explorer/hl/{hl_wallet}/funding",
              f"/api/explorer/hl/{hl_wallet}/extras"):
        add("explorer", p, paced=True)
    add("misc", "/api/admin/me", auth=True)
    return E


def hit(url: str, token: str | None) -> tuple[float, int, int]:
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"} if token else {})
    t = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            body = r.read()
            return time.perf_counter() - t, r.status, len(body)
    except urllib.error.HTTPError as e:
        e.read()
        return time.perf_counter() - t, e.code, 0
    except Exception:  # noqa: BLE001
        return time.perf_counter() - t, 0, 0


def pct(xs: list[float], q: float) -> float:
    xs = sorted(xs)
    if not xs:
        return 0.0
    k = min(len(xs) - 1, max(0, round(q * (len(xs) - 1))))
    return xs[k]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="https://smindex.xyz")
    ap.add_argument("--token-file")
    ap.add_argument("--hl-wallet", required=True)
    ap.add_argument("--repeats", type=int, default=10)
    ap.add_argument("--only", help="comma-separated page names")
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    token = open(a.token_file).read().strip() if a.token_file else None
    now_s = int(time.time())
    with urllib.request.urlopen(a.host + "/api/traders?exchange=hl&limit=25", timeout=60) as r:
        lb = [x.get("wallet_address") for x in json.load(r)][:25]
    eps = endpoints(a.hl_wallet, now_s, lb)
    if a.only:
        keep = set(a.only.split(","))
        eps = [e for e in eps if e[0] in keep]
    res = []
    for page, path, auth, paced in eps:
        gap = 3.5 if paced else 1.05    # app limiter (main.py): 120 reads/min/IP, explorer 20/min/IP
        first, st, size = hit(a.host + path, token if auth else None)
        times, codes = [], [st]
        for _ in range(a.repeats):
            time.sleep(gap)
            dt, c, size2 = hit(a.host + path, token if auth else None)
            times.append(dt)
            codes.append(c)
            size = size2 or size
        r = dict(page=page, path=path, auth=auth, first_ms=round(first * 1000),
                 p50_ms=round(statistics.median(times) * 1000), p95_ms=round(pct(times, 0.95) * 1000),
                 max_ms=round(max(times) * 1000), bytes=size,
                 codes=sorted({c: codes.count(c) for c in codes}.items()))
        res.append(r)
        print(f"{page:10s} {r['p95_ms']:6d} p95 {r['p50_ms']:6d} p50 {r['first_ms']:6d} first "
              f"{size:9d}B {r['codes']} {path[:70]}", flush=True)
    json.dump(dict(host=a.host, at=now_s, repeats=a.repeats, results=res), open(a.out, "w"), indent=1)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
