"""Beta 7.4: service-worker update. Phase 'before': persistent profile, SW on,
loads prod, records the entry bundle + SW state. Phase 'after' (post-deploy):
same profile, cache NOT cleared, one navigation -> must load the new entry bundle."""
import json
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

PROFILE = Path(__file__).parent / "beta_1007" / f"pwa_profile_{sys.argv[2] if len(sys.argv) > 2 else 'a'}"
phase = sys.argv[1]
with sync_playwright() as p:
    ctx = p.chromium.launch_persistent_context(str(PROFILE), headless=True, viewport={"width": 1280, "height": 800})
    ctx.add_init_script("try{localStorage.setItem('perpl_access_verified','true')}catch(e){}")
    pg = ctx.pages[0] if ctx.pages else ctx.new_page()
    pg.goto("https://smindex.xyz/analytics", wait_until="load")
    pg.wait_for_timeout(5000)
    info = pg.evaluate("""async () => { const r = await navigator.serviceWorker.getRegistration();
      const s = [...document.querySelectorAll('script[type=module]')].map(x => x.src.split('/').pop());
      const keys = await caches.keys(); return {entry: s, sw_active: !!(r && r.active), caches: keys}; }""")
    print(phase, json.dumps(info))
    ctx.close()
