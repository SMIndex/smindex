"""Canonical analytics asset names.

Default-dex coins are stored uppercase (BTC, KPEPE). Hyperliquid builder-dex
(HIP-3) coins keep the venue's own form: lowercase dex prefix, uppercase
ticker ('xyz:BRENTOIL'); all 256 listed on 2026-09-28 follow that shape.
Plain .upper() would turn them into 'XYZ:BRENTOIL', which no stored row uses.
"""


def norm_asset(name: str) -> str:
    name = (name or "").strip()
    if ":" in name:
        dex, ticker = name.split(":", 1)
        return f"{dex.lower()}:{ticker.upper()}"
    return name.upper()
