"""Beta 7.3: each screen with its main API forced to HTTP 500 must show an honest
error/empty state within 12 s (no infinite spinner, no blank page)."""
import re, json
from pathlib import Path
from playwright.sync_api import sync_playwright
OUT = Path(__file__).parent / "beta_1007"
CASES = [("/analytics", "**/api/analytics/pulse*"), ("/analytics/BTC", "**/api/analytics/assets/**"),
         ("/analytics/movers", "**/api/analytics/movers*"), ("/copy/discover", "**/api/traders*"),
         ("/copy/trader/0xb83de012dba672c76a7dbbbf3e459cb59d7d6e36?exchange=hl", "**/api/traders/**"),
         ("/wallet/0xecb63caa47c7c4e77f60f1ce858cf28dc2b82b00", "**/api/explorer/**"), ("/trade", "**/api/markets*"),
         ("/portfolio", "**/api/traders/**"), ("/settings", "**/api/**")]
HONEST = re.compile(r"unavailable|failed|error|try again|retry|couldn.t|could not|not available|connect your wallet|no data|went wrong", re.I)
res = {}
with sync_playwright() as p:
    b = p.chromium.launch()
    for path, pattern in CASES:
        ctx = b.new_context(viewport={"width": 1440, "height": 900}, service_workers="block")
        ctx.add_init_script("try{localStorage.setItem('perpl_access_verified','true')}catch(e){}")
        ctx.route(pattern, lambda r: r.fulfill(status=500, body='{"detail":"simulated outage"}', headers={"content-type": "application/json"}))
        pg = ctx.new_page(); crash = []
        pg.on("pageerror", lambda e: crash.append(str(e)[:120]))
        pg.goto("https://smindex.xyz" + path, wait_until="domcontentloaded"); pg.wait_for_timeout(12000)
        txt = pg.evaluate("() => (document.querySelector('main') || document.body).innerText")
        honest = HONEST.findall(txt)
        loading = len(re.findall(r"\bLoading\b", txt))
        name = path.split("?")[0].strip("/").replace("/", "_").replace(":", "_")[:40]
        pg.screenshot(path=str(OUT / f"apidown_{name}.png"))
        res[path] = {"honest_phrases": sorted(set(h.lower() for h in honest))[:4], "loading_text_left": loading, "blank": len(txt.strip()) < 40, "uncaught": crash[:2]}
        print(f"{path[:46]:46} {res[path]}")
        ctx.close()
    b.close()
(OUT / "api_down.json").write_text(json.dumps(res, indent=1))
