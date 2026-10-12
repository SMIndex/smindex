"""Beta 2026-10-08 (d): the Discover list paints its last payload from
localStorage on a REVISIT, before the API answers.

Run 1 = fresh context (nothing stored) -> rows appear only after /api/traders.
Run 2 = same context, API responses to /api/traders DELAYED by 8 s -> rows must
already be on screen before the API answers, and on-chain fields must come back
as pending (no stale open-position claim).

    python tests/discover_snapshot_check.py
"""
import time

from playwright.sync_api import sync_playwright

INIT = "try{localStorage.setItem('perpl_access_verified','true')}catch(e){}"
ROW = "[class*=card], [class*=row], table tr"


def rows_visible(pg) -> int:
    return pg.evaluate("""() => document.querySelectorAll('[class*=card], [class*=row], table tr').length""")


with sync_playwright() as p:
    b = p.chromium.launch()
    ctx = b.new_context(viewport={"width": 1440, "height": 900}, service_workers="block")
    ctx.add_init_script(INIT)

    # --- run 1: fresh, fills the snapshot
    pg = ctx.new_page()
    pg.goto("https://smindex.xyz/", wait_until="domcontentloaded", timeout=90000)
    pg.wait_for_timeout(12000)
    snap = pg.evaluate("""() => Object.keys(localStorage).filter(k => k.startsWith('smindex_discover_v1:'))""")
    print("run 1 (fresh): rows on screen =", rows_visible(pg), " snapshot keys =", snap)
    pg.close()

    # --- run 2: same storage, /api/traders held back 8 s
    pg = ctx.new_page()

    def slow_traders(route):
        if "/api/traders" in route.request.url and "?" not in route.request.url.split("/api/traders")[1][:1]:
            time.sleep(8)
        route.continue_()
    pg.route("**/api/traders*", slow_traders)
    t0 = time.monotonic()
    pg.goto("https://smindex.xyz/", wait_until="domcontentloaded", timeout=90000)
    painted_at = None
    while time.monotonic() - t0 < 8:
        if rows_visible(pg) > 3:
            painted_at = time.monotonic() - t0
            break
        pg.wait_for_timeout(200)
    body = pg.inner_text("body")[:600]
    print(f"run 2 (revisit, /api/traders delayed 8 s): first rows painted after "
          f"{painted_at if painted_at is not None else 'NEVER (>8 s)'}"
          f"{'s' if painted_at is not None else ''}")
    print("   rows on screen at that moment:", rows_visible(pg))
    print("   body starts:", " ".join(body.split())[:200])
    pg.wait_for_timeout(9000)
    print("   rows after the live fetch landed:", rows_visible(pg))
    ctx.close()
    b.close()
