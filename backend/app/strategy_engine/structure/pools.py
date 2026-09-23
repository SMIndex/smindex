from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from .candles import Candle, DAY_MS
from .sessions import day_start, week_start, session_high_low, candles_between
from .swings import Swing

CLUSTER_BAND_PCT = 0.0025       # 0.25% bands
CLUSTER_MIN_OI_FRAC = 0.001     # >= 0.1% of coin OI
EQUAL_TOL_ATR = 0.1


@dataclass
class Pool:
    type: str            # equal_highs | equal_lows | pdh | pdl | pwh | pwl | daily_open | weekly_open |
    #                      asia_high | asia_low | london_high | london_low | ny_high | ny_low | liq_cluster_long | liq_cluster_short
    level: float
    strength: float = 1.0
    side: Optional[str] = None      # for liq clusters: side of the liquidated positions
    notional: float = 0.0
    wallets: int = 0
    tf: Optional[str] = None
    created_ts: int = 0
    meta: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        d = {"type": self.type, "level": self.level, "strength": self.strength}
        if self.side:
            d["side"] = self.side
        if self.notional:
            d["notional"] = self.notional
            d["wallets"] = self.wallets
        if self.tf:
            d["tf"] = self.tf
        return d


def equal_levels(sw: list[Swing], atr: float, kind: str, candles: list[Candle], tf: str, lookback: int = 40) -> list[Pool]:
    """Two or more swing highs (lows) within 0.1 ATR of each other on 1h/4h, not
    yet consumed by a close beyond them."""
    if atr <= 0:
        return []
    pts = [s for s in sw if s.kind == kind][-lookback:]
    used: set[int] = set()
    out: list[Pool] = []
    for i, a in enumerate(pts):
        if i in used:
            continue
        grp = [a]
        for j in range(i + 1, len(pts)):
            if j in used:
                continue
            if abs(pts[j].price - a.price) <= EQUAL_TOL_ATR * atr:
                grp.append(pts[j])
                used.add(j)
        if len(grp) < 2:
            continue
        used.add(i)
        level = sum(s.price for s in grp) / len(grp)
        last_idx = max(s.index for s in grp)
        later = candles[last_idx + 1:]
        if kind == "high" and any(c.c > max(s.price for s in grp) for c in later):
            continue
        if kind == "low" and any(c.c < min(s.price for s in grp) for c in later):
            continue
        out.append(Pool("equal_highs" if kind == "high" else "equal_lows", level, float(len(grp)), tf=tf,
                        created_ts=grp[-1].confirmed_at))
    return out


def reference_levels(c15: list[Candle], c1h: list[Candle], now_ms: int) -> dict:
    """PDH/PDL, PWH/PWL, daily open, weekly open, session H/L for today (closed candles only)."""
    ds = day_start(now_ms)
    ws = week_start(now_ms)
    out: dict = {"daily_open": None, "weekly_open": None, "pdh": None, "pdl": None, "pwh": None, "pwl": None}
    prior = candles_between(c15, ds - DAY_MS, ds)
    if prior:
        out["pdh"], out["pdl"] = max(c.h for c in prior), min(c.l for c in prior)
    today = candles_between(c15, ds, now_ms)
    if today:
        out["daily_open"] = today[0].o
    pw = candles_between(c1h, ws - 7 * DAY_MS, ws)
    if pw:
        out["pwh"], out["pwl"] = max(c.h for c in pw), min(c.l for c in pw)
        out["pw_open"], out["pw_close"] = pw[0].o, pw[-1].c
    tw = candles_between(c1h, ws, now_ms) or candles_between(c15, ws, now_ms)
    if tw:
        out["weekly_open"] = tw[0].o
    for name, key in (("asia", "asia"), ("london", "london"), ("newyork", "ny")):
        r = session_high_low(c15, ds, name, until_ms=now_ms)
        out[f"{key}_high"] = r["high"] if r else None
        out[f"{key}_low"] = r["low"] if r else None
    return out


def liquidation_clusters(rows: list[dict], mid: float, oi_notional: Optional[float],
                         threshold: Optional[float] = None) -> list[Pool]:
    """0.25% bands over analytics_positions liq prices; a band qualifies when its
    notional >= `threshold` — the calibrated band_p80 (spec v1.1 Part C, D-66) —
    or, when no calibration exists, 0.1% of coin OI. rows: [{liq_px, notional, side, wallet}]."""
    if not rows or not mid or mid <= 0:
        return []
    if threshold is None:
        if not oi_notional or oi_notional <= 0:
            return []
        threshold = CLUSTER_MIN_OI_FRAC * oi_notional
    width = mid * CLUSTER_BAND_PCT
    bands: dict[tuple[int, str], dict] = {}
    for r in rows:
        px = r.get("liq_px")
        if px is None:
            continue
        px = float(px)
        if px <= 0:
            continue
        side = "long" if str(r.get("side") or "").lower().startswith("l") else "short"
        b = int(px // width)
        d = bands.setdefault((b, side), {"notional": 0.0, "wallets": set(), "pxn": 0.0})
        n = abs(float(r.get("notional") or 0))
        d["notional"] += n
        d["pxn"] += n * px
        if r.get("wallet"):
            d["wallets"].add(r["wallet"])
    out: list[Pool] = []
    thresh = threshold
    for (b, side), d in bands.items():
        if d["notional"] < thresh:
            continue
        level = d["pxn"] / d["notional"] if d["notional"] > 0 else (b + 0.5) * width
        out.append(Pool(f"liq_cluster_{side}", level, 0.0, side=side, notional=d["notional"], wallets=len(d["wallets"])))
    out.sort(key=lambda p: -p.notional)
    for rank, p in enumerate(out):
        p.strength = float(len(out) - rank)     # notional rank: biggest cluster = highest strength
    return out


def pools_above(pools: list[Pool], price: float) -> list[Pool]:
    return sorted([p for p in pools if p.level > price], key=lambda p: p.level - price)


def pools_below(pools: list[Pool], price: float) -> list[Pool]:
    return sorted([p for p in pools if p.level < price], key=lambda p: price - p.level)


def build_pools(refs: dict, eq: list[Pool], clusters: list[Pool]) -> list[Pool]:
    out: list[Pool] = []
    for k in ("pdh", "pdl", "pwh", "pwl", "daily_open", "weekly_open", "asia_high", "asia_low",
              "london_high", "london_low", "ny_high", "ny_low"):
        v = refs.get(k)
        if v is not None:
            out.append(Pool(k, float(v), 1.0))
    out.extend(eq)
    out.extend(clusters)
    return out
