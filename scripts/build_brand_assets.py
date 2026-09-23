#!/usr/bin/env python3
"""Build the SMINDEX brand assets from docs/design/brand/ sources.

Two things this does that matter:

1. **Outlines the wordmark.** The supplied lockups draw "SMINDEX" with an SVG
   `<text>` element in IBM Plex Sans. That renders differently — or not at all —
   wherever the font is missing: OG card unfurlers, a cold page load before the
   webfont arrives, any rasteriser. The text is converted to a `<path>` using the
   real font outlines, so the lockup is identical everywhere with no font
   dependency.

2. **Strips the C2PA provenance blob.** The source files carry ~7.7 KB of
   base64 metadata each — around 95% of the file. It has no visual effect and
   would ship to every visitor.

Rasterisation uses Playwright/Chromium so the PNGs are pixel-identical to what a
browser draws from the same SVG, rather than a reimplementation in a drawing
library that could drift.

    python scripts/build_brand_assets.py
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "docs" / "design" / "brand"
OUT_PUB = ROOT / "frontend" / "public" / "brand"
OUT_ICONS = ROOT / "frontend" / "public" / "icons"

WORDMARK = "SMINDEX"
FONT_URL = ("https://fonts.gstatic.com/s/ibmplexsans/v23/"
            "zYXGKVElMYYaJe8bpLHnCwDKr932-G7dytD-Dmu1swZSAXcomDVmadSDNF5zAA.ttf")
FONT_CACHE = ROOT / "scripts" / ".cache" / "IBMPlexSans-SemiBold.ttf"


def strip_c2pa(svg: str) -> str:
    """Remove the provenance blob and the now-unused namespace declaration."""
    svg = re.sub(r"<metadata>.*?</metadata>", "", svg, flags=re.S)
    svg = svg.replace(' xmlns:c2pa="http://c2pa.org/manifest"', "")
    return re.sub(r"\n\s*\n", "\n", svg).strip() + "\n"


def get_font():
    from fontTools.ttLib import TTFont
    if not FONT_CACHE.exists():
        import urllib.request
        FONT_CACHE.parent.mkdir(parents=True, exist_ok=True)
        print(f"  fetching {FONT_CACHE.name}…")
        urllib.request.urlretrieve(FONT_URL, FONT_CACHE)
    return TTFont(FONT_CACHE)


def outline(text: str, font, size: float, x: float, y: float,
            letter_spacing: float = 0.0) -> str:
    """Return an SVG path `d` for `text` laid out at (x, y) baseline.

    SVG y grows downward and font units grow upward, so the glyph outlines are
    flipped on y and offset by the baseline.
    """
    from fontTools.pens.svgPathPen import SVGPathPen
    from fontTools.pens.transformPen import TransformPen
    from fontTools.misc.transform import Transform

    upem = font["head"].unitsPerEm
    scale = size / upem
    cmap = font.getBestCmap()
    gs = font.getGlyphSet()
    hmtx = font["hmtx"]

    parts, pen_x = [], x
    for ch in text:
        gname = cmap.get(ord(ch))
        if gname is None:
            raise SystemExit(f"glyph missing for {ch!r}")
        pen = SVGPathPen(gs)
        tp = TransformPen(pen, Transform(scale, 0, 0, -scale, pen_x, y))
        gs[gname].draw(tp)
        d = pen.getCommands()
        if d:
            parts.append(d)
        pen_x += hmtx[gname][0] * scale + letter_spacing
    return " ".join(parts)


def build_lockup(src: Path, font, fill: str) -> str:
    """Replace the <text> wordmark with an outlined <path>."""
    svg = strip_c2pa(src.read_text(encoding="utf-8"))
    m = re.search(
        r'<text x="([\d.]+)" y="([\d.]+)"[^>]*?font-size="([\d.]+)"'
        r'[^>]*?letter-spacing="([\d.]+)"[^>]*?>([^<]+)</text>', svg)
    if not m:
        raise SystemExit(f"could not parse the <text> wordmark in {src.name}")
    x, y, size, ls, txt = (float(m.group(1)), float(m.group(2)),
                           float(m.group(3)), float(m.group(4)), m.group(5))
    d = outline(txt, font, size, x, y, ls)
    path = (f'<path d="{d}" fill="{fill}"/>'
            f'<!-- wordmark "{txt}" outlined from IBM Plex Sans SemiBold: '
            f'no font dependency at render time -->')
    return svg.replace(m.group(0), path)


def render_png(svg_path: Path, out: Path, size: int, pad_ratio: float = 0.0):
    """Rasterise an SVG at `size` px using Chromium, so the PNG matches the
    browser exactly. `pad_ratio` insets the art for a maskable icon."""
    from playwright.sync_api import sync_playwright
    svg = svg_path.read_text(encoding="utf-8")
    inset = int(size * pad_ratio)
    art = size - 2 * inset
    html = (
        "<!doctype html><meta charset='utf-8'>"
        "<style>html,body{margin:0;padding:0;background:transparent}"
        f"#w{{width:{size}px;height:{size}px;display:grid;place-items:center;"
        "background:transparent}"
        f"#w>svg{{width:{art}px;height:{art}px;display:block}}</style>"
        f"<div id='w'>{svg}</div>")
    with sync_playwright() as pw:
        b = pw.chromium.launch()
        pg = b.new_page(viewport={"width": size, "height": size},
                        device_scale_factor=1)
        pg.set_content(html, wait_until="load")
        pg.wait_for_timeout(150)
        out.parent.mkdir(parents=True, exist_ok=True)
        pg.locator("#w").screenshot(path=str(out), omit_background=True)
        b.close()
    print(f"    {out.relative_to(ROOT)}  {size}x{size}")


def build_og_card(out: Path, w: int = 1200, h: int = 630):
    """Mark + outlined wordmark on the brand ground, at the size link
    unfurlers expect. Rendered in Chromium from the same SVGs the app uses, so
    the card cannot drift from the UI."""
    from playwright.sync_api import sync_playwright
    mark = (OUT_PUB / "smindex-mark.svg").read_text(encoding="utf-8")
    lockup = (OUT_PUB / "smindex-logo-dark.svg").read_text(encoding="utf-8")
    html = (
        "<!doctype html><meta charset='utf-8'><style>"
        "html,body{margin:0;padding:0}"
        f"#c{{width:{w}px;height:{h}px;background:#0C1418;display:flex;"
        "flex-direction:column;align-items:center;justify-content:center;gap:34px;"
        "font-family:system-ui,sans-serif}"
        "#c .mk svg{width:150px;height:150px;display:block}"
        "#c .lk svg{width:620px;height:auto;display:block}"
        "#c .tag{color:#9DB4BF;font-size:27px;letter-spacing:.3px}"
        "</style>"
        f"<div id='c'><div class='mk'>{mark}</div>"
        f"<div class='lk'>{lockup}</div>"
        "<div class='tag'>The smart money index for onchain perps</div></div>")
    with sync_playwright() as pw:
        b = pw.chromium.launch()
        pg = b.new_page(viewport={"width": w, "height": h}, device_scale_factor=1)
        pg.set_content(html, wait_until="load")
        pg.wait_for_timeout(200)
        out.parent.mkdir(parents=True, exist_ok=True)
        pg.locator("#c").screenshot(path=str(out))
        b.close()
    print(f"    {out.relative_to(ROOT)}  {w}x{h}")


def main() -> int:
    if not SRC.exists():
        print(f"missing source dir {SRC}")
        return 1
    OUT_PUB.mkdir(parents=True, exist_ok=True)
    font = get_font()

    print("outlining lockups (wordmark -> paths):")
    for name, fill in (("smindex-logo-light", "#121722"),
                       ("smindex-logo-dark", "#EAF2F5")):
        s = build_lockup(SRC / f"{name}.svg", font, fill)
        (OUT_PUB / f"{name}.svg").write_text(s, encoding="utf-8")
        before = (SRC / f"{name}.svg").stat().st_size
        after = (OUT_PUB / f"{name}.svg").stat().st_size
        print(f"    {name}.svg  {before}B -> {after}B  (text -> path, C2PA stripped)")

    print("copying mark + icon (metadata stripped):")
    for name in ("smindex-mark", "smindex-icon-512"):
        s = strip_c2pa((SRC / f"{name}.svg").read_text(encoding="utf-8"))
        (OUT_PUB / f"{name}.svg").write_text(s, encoding="utf-8")
        print(f"    {name}.svg  {(SRC/f'{name}.svg').stat().st_size}B -> "
              f"{(OUT_PUB/f'{name}.svg').stat().st_size}B")

    print("rasterising favicons from the mark:")
    for px in (16, 32, 48):
        render_png(OUT_PUB / "smindex-mark.svg", OUT_PUB / f"favicon-{px}.png", px)

    print("rasterising PWA icons from icon-512:")
    render_png(OUT_PUB / "smindex-icon-512.svg", OUT_ICONS / "icon-192.png", 192)
    render_png(OUT_PUB / "smindex-icon-512.svg", OUT_ICONS / "icon-512.png", 512)
    # maskable: safe zone is the central 80%, so inset 10% each side
    render_png(OUT_PUB / "smindex-icon-512.svg",
               OUT_ICONS / "icon-maskable-512.png", 512, pad_ratio=0.10)

    print("rendering the OG share card (mark + wordmark, 1200x630):")
    build_og_card(OUT_PUB / "og-card.png")

    # the app also serves icons/icon.svg — point it at the new mark
    (OUT_ICONS / "icon.svg").write_text(
        strip_c2pa((SRC / "smindex-icon-512.svg").read_text(encoding="utf-8")),
        encoding="utf-8")
    print(f"    {(OUT_ICONS/'icon.svg').relative_to(ROOT)}  (from icon-512)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
