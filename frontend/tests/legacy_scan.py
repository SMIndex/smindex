"""Design-B legacy scan (2026-10-02): every route + reachable prompt in shell B,
serving the LOCAL build (index.html + /assets) over prod data, or prod as-is
with --prod. Screens: 1440 + 390, light + dark. Flags old-palette leftovers:
any element whose computed color/background is one of shell A's purples."""
import json, sys
from pathlib import Path
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
DIST = ROOT / "dist"
OUT = Path(__file__).parent / (sys.argv[1] if len(sys.argv) > 1 else "legacy_1002")
PROD = "--prod" in sys.argv
OUT.mkdir(exist_ok=True)
BASE = "https://smindex.xyz"
ROUTES = ["/copy/watchlist", "/copy/dashboard", "/copy/portfolio", "/copy/history", "/insights", "/heatmap",
          "/orders", "/multi-chart", "/compare", "/social", "/journal", "/markets", "/paper",
          "/trade", "/settings", "/analytics", "/copy/discover", "/portfolio", "/wallet"]
# shell A purples (dark + light accents), must not appear anywhere in shell B
A_PURPLES = ["rgb(131, 110, 249)", "rgb(167, 139, 250)", "rgb(110, 84, 255)", "rgb(139, 115, 255)", "rgb(90, 63, 224)",
             "rgb(9, 9, 15)", "rgb(19, 19, 29)", "rgb(26, 26, 38)"]
SCAN_JS = """(purples) => {
  const doc = document.documentElement;
  const overflow = Math.max(doc.scrollWidth, document.body.scrollWidth) - window.innerWidth;
  const bad = [];
  for (const el of document.querySelectorAll('body *')) {
    const r = el.getBoundingClientRect(); if (!r.width || !r.height) continue;
    const cs = getComputedStyle(el);
    for (const k of ['color', 'backgroundColor', 'borderTopColor']) {
      const v = cs[k];
      if (purples.some(p => v.startsWith(p.slice(0, -1)))) { bad.push(k + ':' + v + ' <' + el.tagName.toLowerCase() + '.' + String(el.className).slice(0, 60) + '>'); break; }
    }
    if (bad.length > 12) break;
  }
  const fonts = new Set([...document.querySelectorAll('main *')].slice(0, 400).map(e => getComputedStyle(e).fontFamily.split(',')[0].replace(/"/g, '')));
  return {overflow_px: overflow, old_palette: bad, fonts: [...fonts]};
}"""

def wire(ctx):
    if PROD:
        return
    idx = (DIST / "index.html").read_bytes()
    def doc(route):
        req = route.request
        if req.resource_type == "document":
            return route.fulfill(status=200, body=idx, headers={"content-type": "text/html"})
        return route.continue_()
    def asset(route):
        name = route.request.url.split("/assets/", 1)[1].split("?")[0]
        f = DIST / "assets" / name
        if not f.exists():
            return route.continue_()
        ct = "application/javascript" if name.endswith(".js") else "text/css" if name.endswith(".css") else None
        return route.fulfill(status=200, body=f.read_bytes(), headers={"content-type": ct} if ct else {})
    ctx.route(BASE + "/assets/**", asset)
    ctx.route(lambda u: u.startswith(BASE) and "/api/" not in u and "/assets/" not in u and "/ws/" not in u, doc)

def prompts(page, theme, w, results):
    """Prompts reachable without a wallet: wallet picker, market selector, price alert."""
    tries = [("/trade", "wallet_picker", ".side button:has-text('Connect'), .mmenu-btn"),
             ("/trade", "market_selector", "button:has-text('/USD')"),
             ("/trade", "price_alert", "button:has-text('Alerts')"),
             ("/copy/discover", "trader_copy_prompt", "main button:has-text('Copy')")]
    for path, name, sel in tries:
        try:
            page.goto(BASE + path, wait_until="domcontentloaded", timeout=45000)
            page.wait_for_timeout(5000)
            el = page.locator(sel).first
            el.click(timeout=5000)
            page.wait_for_timeout(1200)
            shot = OUT / f"prompt_{name}_{w}_{theme}.png"
            page.screenshot(path=str(shot))
            r = page.evaluate(SCAN_JS, A_PURPLES); r["shot"] = shot.name
        except Exception as exc:
            r = {"error": str(exc)[:160]}
        results[f"prompt:{name}:{w}:{theme}"] = r
        print("prompt", name, w, theme, {k: v for k, v in r.items() if k != "fonts"})

results = {}
with sync_playwright() as p:
    b = p.chromium.launch()
    for w in (1440, 390):
        for theme in ("light", "dark"):
            ctx = b.new_context(viewport={"width": w, "height": 900 if w > 800 else 844}, is_mobile=w < 800, has_touch=w < 800, service_workers="block")
            ctx.add_init_script(f"try{{localStorage.setItem('perpl_access_verified','true');localStorage.setItem('perpl-design','b');localStorage.setItem('perpl-design-theme','{theme}')}}catch(e){{}}")
            wire(ctx)
            page = ctx.new_page()
            for path in ROUTES:
                try:
                    page.goto(BASE + path, wait_until="domcontentloaded", timeout=45000)
                    page.wait_for_timeout(4500)
                    name = path.strip("/").replace("/", "_") or "root"
                    shot = OUT / f"{name}_{w}_{theme}.png"
                    page.screenshot(path=str(shot))
                    r = page.evaluate(SCAN_JS, A_PURPLES); r["shot"] = shot.name
                except Exception as exc:
                    r = {"error": str(exc)[:160]}
                results[f"{path}:{w}:{theme}"] = r
                print(path, w, theme, "overflow", r.get("overflow_px"), "old", len(r.get("old_palette", [])), r.get("old_palette", [])[:2])
            prompts(page, theme, w, results)
            ctx.close()
    b.close()
(OUT / "scan.json").write_text(json.dumps(results, indent=1))
print("saved", OUT)
