"""Desktop wallet connect in shell B: picker visible, WalletConnect QR modal
opens, no CSP violations. --local serves dist/ (with prod's CSP header copied)."""
import sys
from pathlib import Path
from playwright.sync_api import sync_playwright
import urllib.request, ssl, certifi
DIST = Path(__file__).resolve().parents[1] / "dist"; BASE = "https://smindex.xyz"; LOCAL = "--local" in sys.argv
CSP = urllib.request.urlopen(urllib.request.Request(BASE + "/", method="HEAD"), context=ssl.create_default_context(cafile=certifi.where())).headers.get("Content-Security-Policy")
INIT = "try{localStorage.setItem('perpl_access_verified','true')}catch(e){} window.__csp=[]; document.addEventListener('securitypolicyviolation', e => window.__csp.push(e.violatedDirective + ' ' + e.blockedURI));"
with sync_playwright() as p:
    b = p.chromium.launch()
    ctx = b.new_context(viewport={"width": 1440, "height": 900}, service_workers="block"); ctx.add_init_script(INIT)
    if LOCAL:
        idx = (DIST / "index.html").read_bytes()
        def asset(r):
            n = r.request.url.split("/assets/", 1)[1].split("?")[0]; f = DIST / "assets" / n
            if not f.exists(): return r.continue_()
            return r.fulfill(status=200, body=f.read_bytes(), headers={"content-type": "application/javascript" if n.endswith(".js") else "text/css" if n.endswith(".css") else "application/octet-stream", "content-security-policy": CSP})
        ctx.route(BASE + "/assets/**", asset)
        ctx.route(lambda u: u.startswith(BASE) and "/api/" not in u and "/assets/" not in u and "/ws/" not in u,
                  lambda r: r.fulfill(status=200, body=idx, headers={"content-type": "text/html", "content-security-policy": CSP}) if r.request.resource_type == "document" else r.continue_())
    pg = ctx.new_page(); errs = []
    pg.on("console", lambda m: errs.append(m.text[:160]) if m.type == "error" else None)
    pg.goto(BASE + "/copy/discover", wait_until="domcontentloaded"); pg.wait_for_timeout(4000)
    pg.locator(".side button:has-text('Connect')").first.click(); pg.wait_for_timeout(800)
    menu = pg.locator(".side .foot .absolute").first
    bb = menu.bounding_box(); vp = pg.viewport_size
    print("picker box", bb, "fully visible:", bool(bb) and bb["y"] >= 0 and bb["y"] + bb["height"] <= vp["height"] and bb["x"] >= 0)
    pg.screenshot(path=str(Path(__file__).parent / "beta_1007" / f"wallet_picker_{'local' if LOCAL else 'prod'}.png"))
    pg.get_by_text("Mobile & QR code").first.click(timeout=8000); pg.wait_for_timeout(10000)
    print("walletconnect modal:", pg.evaluate("() => !!document.querySelector('w3m-modal, appkit-modal, wcm-modal')"),
          "| csp violations:", pg.evaluate("window.__csp"), "| console errors:", [e for e in errs if 'market configs' not in e][:5])
    pg.screenshot(path=str(Path(__file__).parent / "beta_1007" / f"walletconnect_{'local' if LOCAL else 'prod'}.png"))
    b.close()
