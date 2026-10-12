"""Beta 8.2: cold load on smindex.xyz: fresh browser context per run (no cache,
service worker blocked), desktop 1440. LCP from PerformanceObserver; time to data
= when the page's main API response finished (ms from navigation start).
3 runs per route, median reported."""
import json
import statistics
import sys
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

ROUTES = {"/": "/api/traders", "/analytics": "/api/analytics/pulse",
          "/wallet/0xecb63caa47c7c4e77f60f1ce858cf28dc2b82b00": "/api/explorer/hl/",
          "/copy": "/api/traders", "/trade": "/api/orderbook/"}
INIT = """try{localStorage.setItem('perpl_access_verified','true')}catch(e){}
window.__lcp = 0; new PerformanceObserver((l) => { for (const e of l.getEntries()) window.__lcp = e.startTime; })
  .observe({type: 'largest-contentful-paint', buffered: true});"""
res = {}
with sync_playwright() as p:
    b = p.chromium.launch()
    for path, api in ROUTES.items():
        runs = []
        for i in range(3):
            ctx = b.new_context(viewport={"width": 1440, "height": 900}, service_workers="block")
            ctx.add_init_script(INIT)
            pg = ctx.new_page()
            t0 = {"t": None}; data = {"t": None}
            pg.on("requestfinished", lambda r, api=api: data.__setitem__("t", time.monotonic()) if (api in r.url and data["t"] is None) else None)
            start = time.monotonic()
            pg.goto("https://smindex.xyz" + path, wait_until="domcontentloaded", timeout=90000)
            dcl = time.monotonic() - start
            deadline = time.monotonic() + 60
            while data["t"] is None and time.monotonic() < deadline:
                pg.wait_for_timeout(250)
            pg.wait_for_timeout(1500)
            lcp = pg.evaluate("window.__lcp") / 1000
            ttd = (data["t"] - start) if data["t"] else None
            runs.append({"dcl": round(dcl, 2), "lcp": round(lcp, 2), "time_to_data": round(ttd, 2) if ttd else None})
            ctx.close()
        med = lambda k: statistics.median([r[k] for r in runs if r[k] is not None]) if any(r[k] is not None for r in runs) else None
        res[path] = {"runs": runs, "median_lcp": med("lcp"), "median_time_to_data": med("time_to_data"), "median_dcl": med("dcl")}
        print(f"{path[:50]:50} LCP {res[path]['median_lcp']}s  data {res[path]['median_time_to_data']}s  DCL {res[path]['median_dcl']}s  runs {runs}", flush=True)
    b.close()
(Path(__file__).parent / (sys.argv[1] if len(sys.argv) > 1 else "cold_load_1007.json")).write_text(json.dumps(res, indent=1))
