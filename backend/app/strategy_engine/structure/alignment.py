"""Alignment table (doc 10 §2.11) + the per-coin Structure bundle every model reads."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

import numpy as np

from . import day_type as dt_mod
from .candles import Candle, aggregate_daily, atr_series, closed
from .pools import Pool, build_pools, equal_levels, liquidation_clusters, pools_above, pools_below, reference_levels
from .ranges import Range, current_range
from .sessions import asia_range, day_start, session_of
from .swings import Swing, swings
from .trend import TrendState, structure
from .vwap import daily_vwap, profiles, session_vwap
from .zones import Zone, nearest_zone, zones


@dataclass
class Structure:
    coin: str
    now_ms: int
    price: float
    c15: list[Candle]
    c1h: list[Candle]
    c4h: list[Candle]
    c1d: list[Candle]
    atr: dict = field(default_factory=dict)            # tf -> float|None
    atr_series: dict = field(default_factory=dict)     # tf -> np.ndarray
    sw: dict = field(default_factory=dict)             # tf -> list[Swing]
    st: dict = field(default_factory=dict)             # tf -> TrendState
    zones: dict = field(default_factory=dict)          # tf -> list[Zone]
    range_4h: Optional[Range] = None
    range_1h: Optional[Range] = None
    refs: dict = field(default_factory=dict)
    pools: list[Pool] = field(default_factory=list)
    clusters: list[Pool] = field(default_factory=list)
    session: str = "dead"
    minutes_into_session: int = 0
    asia: Optional[dict] = None
    vwap_day: Optional[dict] = None
    vwap_session: Optional[dict] = None
    profiles: dict = field(default_factory=dict)
    daily_bias: str = "neutral"
    day_type: str = "range"
    day_inputs: Optional[dt_mod.DayInputs] = None
    event_within_2h: bool = False
    event_within_30m: bool = False
    alignment: dict = field(default_factory=dict)

    def trend(self, tf: str) -> str:
        s = self.st.get(tf)
        return s.trend if s else "range"

    def daily_midpoint(self, n: int = 20) -> Optional[float]:
        d = self.c1d[-n:]
        if not d:
            return None
        return (max(c.h for c in d) + min(c.l for c in d)) / 2.0


def _daily_bias(st1d: TrendState) -> str:
    return {"up": "up", "down": "down"}.get(st1d.trend, "neutral")


def build_structure(coin: str, now_ms: int, c15: list[Candle], c1h: list[Candle], c4h: list[Candle],
                    liq_rows: Optional[list[dict]] = None, oi_notional: Optional[float] = None,
                    events_ts: Optional[list[int]] = None, day_type: Optional[str] = None,
                    cluster_threshold: Optional[float] = None) -> Structure:
    """Everything structural from CLOSED candles. `day_type` may be passed by the
    caller (running label computed with feed inputs) — defaults to the
    structural-only classification."""
    c15, c1h, c4h = closed(c15, now_ms), closed(c1h, now_ms), closed(c4h, now_ms)
    c1d = aggregate_daily(c4h)
    price = c15[-1].c if c15 else (c1h[-1].c if c1h else 0.0)
    s = Structure(coin, now_ms, price, c15, c1h, c4h, c1d)
    for tf, cs in (("15m", c15), ("1h", c1h), ("4h", c4h), ("1d", c1d)):
        a = atr_series(cs, 14)
        s.atr_series[tf] = a
        s.atr[tf] = float(a[-1]) if a.size and np.isfinite(a[-1]) else None
        s.sw[tf] = swings(cs, tf)
        s.st[tf] = structure(cs, s.sw[tf], tf)
    for tf, cs in (("15m", c15), ("1h", c1h), ("4h", c4h)):
        s.zones[tf] = zones(cs, s.atr_series[tf], tf, lookback=200 if tf != "15m" else 300)
    s.range_4h = current_range(c4h, s.sw["4h"], "4h", price, now_ms) if c4h else None
    s.range_1h = current_range(c1h, s.sw["1h"], "1h", price, now_ms) if c1h else None
    s.refs = reference_levels(c15, c1h, now_ms)
    eq: list[Pool] = []
    for tf, cs in (("1h", c1h), ("4h", c4h)):
        a = s.atr.get(tf) or 0.0
        eq += equal_levels(s.sw[tf], a, "high", cs, tf)
        eq += equal_levels(s.sw[tf], a, "low", cs, tf)
    s.clusters = liquidation_clusters(liq_rows or [], price, oi_notional, threshold=cluster_threshold)
    s.pools = build_pools(s.refs, eq, s.clusters)
    s.session, s.minutes_into_session = session_of(now_ms)
    ds = day_start(now_ms)
    s.asia = asia_range(c15, ds) if now_ms >= ds + 7 * 3_600_000 else None
    if s.asia and s.atr.get("15m"):
        a15 = s.atr["15m"]
        # "wicks outside the range by > 0.1 ATR" (doc 15): the Asia high/low ARE the extreme
        # wicks, so measured against the high/low this was constant 0. The range that wicks
        # can poke out of is the body range (max close/open … min close/open) — audit D-47.
        cs = s.asia["candles"]
        body_hi = max(max(c.o, c.c) for c in cs) if cs else s.asia["high"]
        body_lo = min(min(c.o, c.c) for c in cs) if cs else s.asia["low"]
        s.asia["wicks_outside"] = sum(1 for c in cs if c.h > body_hi + 0.1 * a15 or c.l < body_lo - 0.1 * a15)
        s.asia.pop("candles", None)
    elif s.asia:
        s.asia.pop("candles", None)
    s.vwap_day = daily_vwap(c15, now_ms)
    s.vwap_session = session_vwap(c15, now_ms, s.session) if s.session != "dead" else None
    s.profiles = profiles(c15, now_ms)
    s.daily_bias = _daily_bias(s.st["1d"])
    ev = sorted(events_ts or [])
    s.event_within_2h = any(abs(t - now_ms) <= 2 * 3_600_000 for t in ev)
    s.event_within_30m = any(abs(t - now_ms) <= 30 * 60_000 for t in ev)
    s.day_inputs = structural_day_inputs(s)
    s.day_type = day_type or dt_mod.classify(s.day_inputs)
    s.alignment = alignment_table(s)
    return s


def structural_day_inputs(s: Structure) -> dt_mod.DayInputs:
    pd = (s.profiles or {}).get("prior_day") or {}
    return dt_mod.DayInputs(
        event_within_2h=s.event_within_2h, event_within_30m=s.event_within_30m,
        atr_1h=s.atr.get("1h"), daily_open=s.refs.get("daily_open"),
        prior_vah=pd.get("vah"), prior_val=pd.get("val"),
        bos_1h_up_today=dt_mod.bos_today(s.st["1h"].events, s.now_ms, "up"),
        bos_1h_down_today=dt_mod.bos_today(s.st["1h"].events, s.now_ms, "down"),
        trend_1h=s.trend("1h"), vol_2h_ratio=dt_mod.volume_2h_ratio(s.c15, s.now_ms),
        price_move_2h=(s.c15[-1].c - s.c15[-9].c) if len(s.c15) >= 9 else None,
    )


def _zone_d(z: Optional[Zone]) -> Optional[dict]:
    return None if z is None else {"type": z.type, "tf": z.tf, "top": z.top, "bottom": z.bottom, "status": z.status}


def _pool_d(p: Optional[Pool]) -> Optional[dict]:
    return None if p is None else {"type": p.type, "level": p.level, "strength": p.strength}


def _ev_d(st: TrendState) -> Optional[dict]:
    e = st.last_event
    return None if e is None else {"type": e.type, "direction": e.direction, "ts": e.ts, "level": e.level}


def alignment_table(s: Structure) -> dict:
    p = s.price
    live = [z for tf in ("1h", "4h") for z in s.zones.get(tf, []) if z.live]
    return {
        "daily_bias": s.daily_bias,
        "trend_4h": s.trend("4h"), "trend_1h": s.trend("1h"),
        "range_4h": s.range_4h.as_dict(p) if s.range_4h else None,
        "range_1h": s.range_1h.as_dict(p) if s.range_1h else None,
        "nearest_zone_above": _zone_d(nearest_zone(live, p, True)),
        "nearest_zone_below": _zone_d(nearest_zone(live, p, False)),
        "nearest_pool_above": _pool_d(next(iter(pools_above(s.pools, p)), None)),
        "nearest_pool_below": _pool_d(next(iter(pools_below(s.pools, p)), None)),
        "last_event_4h": _ev_d(s.st["4h"]), "last_event_1h": _ev_d(s.st["1h"]),
        "session": s.session, "minutes_into_session": s.minutes_into_session,
        "day_type": s.day_type,
        "event_within_2h": s.event_within_2h, "event_within_30m": s.event_within_30m,
        "asia_range": ({"high": s.asia["high"], "low": s.asia["low"]} if s.asia else None),
        "weekly_open": s.refs.get("weekly_open"), "daily_open": s.refs.get("daily_open"),
        "pdh": s.refs.get("pdh"), "pdl": s.refs.get("pdl"), "pwh": s.refs.get("pwh"), "pwl": s.refs.get("pwl"),
        "price": p,
        "swings": {tf: len(s.sw.get(tf, [])) for tf in ("15m", "1h", "4h", "1d")},
        "atr": {tf: s.atr.get(tf) for tf in ("15m", "1h", "4h")},
        "pools_above": len(pools_above(s.pools, p)), "pools_below": len(pools_below(s.pools, p)),
    }


def format_table(coin: str, a: dict) -> str:
    """One alignment table per coin for the log (doc 17 step 7a)."""
    def f(v):
        if v is None:
            return "-"
        if isinstance(v, float):
            return f"{v:,.2f}"
        return str(v)
    r4, r1 = a.get("range_4h") or {}, a.get("range_1h") or {}
    za, zb = a.get("nearest_zone_above") or {}, a.get("nearest_zone_below") or {}
    pa, pb = a.get("nearest_pool_above") or {}, a.get("nearest_pool_below") or {}
    e4, e1 = a.get("last_event_4h") or {}, a.get("last_event_1h") or {}
    asia = a.get("asia_range") or {}
    rows = [
        ("coin / price", f"{coin} {f(a.get('price'))}"),
        ("swings 15m/1h/4h/1d", "/".join(str(a.get("swings", {}).get(t, 0)) for t in ("15m", "1h", "4h", "1d"))),
        ("daily_bias", a.get("daily_bias")),
        ("trend_4h / trend_1h", f"{a.get('trend_4h')} / {a.get('trend_1h')}"),
        ("range_4h", f"{f(r4.get('low'))} .. {f(r4.get('high'))} mid {f(r4.get('mid'))} {r4.get('position', '-')} pct {f(r4.get('pct'))}"),
        ("range_1h", f"{f(r1.get('low'))} .. {f(r1.get('high'))} mid {f(r1.get('mid'))} {r1.get('position', '-')} pct {f(r1.get('pct'))}"),
        ("nearest_zone_above", f"{za.get('type', '-')} {za.get('tf', '')} {f(za.get('bottom'))}..{f(za.get('top'))} {za.get('status', '')}"),
        ("nearest_zone_below", f"{zb.get('type', '-')} {zb.get('tf', '')} {f(zb.get('bottom'))}..{f(zb.get('top'))} {zb.get('status', '')}"),
        ("nearest_pool_above", f"{pa.get('type', '-')} {f(pa.get('level'))} s={f(pa.get('strength'))} (of {a.get('pools_above', 0)})"),
        ("nearest_pool_below", f"{pb.get('type', '-')} {f(pb.get('level'))} s={f(pb.get('strength'))} (of {a.get('pools_below', 0)})"),
        ("last_event_4h", f"{e4.get('type', '-')} {e4.get('direction', '')} @ {f(e4.get('level'))}"),
        ("last_event_1h", f"{e1.get('type', '-')} {e1.get('direction', '')} @ {f(e1.get('level'))}"),
        ("session", f"{a.get('session')} +{a.get('minutes_into_session')}m"),
        ("day_type", a.get("day_type")),
        ("event_within_2h / 30m", f"{a.get('event_within_2h')} / {a.get('event_within_30m')}"),
        ("asia_range", f"{f(asia.get('low'))} .. {f(asia.get('high'))}"),
        ("weekly_open / daily_open", f"{f(a.get('weekly_open'))} / {f(a.get('daily_open'))}"),
        ("pdh / pdl", f"{f(a.get('pdh'))} / {f(a.get('pdl'))}"),
        ("pwh / pwl", f"{f(a.get('pwh'))} / {f(a.get('pwl'))}"),
    ]
    w = max(len(k) for k, _ in rows)
    return "\n".join(f"  {k.ljust(w)} | {v}" for k, v in rows)
