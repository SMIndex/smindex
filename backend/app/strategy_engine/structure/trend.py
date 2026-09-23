from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from .candles import Candle
from .swings import Swing


@dataclass(frozen=True)
class StructureEvent:
    ts: int
    tf: str
    type: str        # 'BOS' | 'CHoCH'
    direction: str   # 'up' | 'down'
    level: float
    index: int = 0

    def as_dict(self) -> dict:
        return {"ts": self.ts, "tf": self.tf, "type": self.type, "direction": self.direction, "level": self.level}


@dataclass
class TrendState:
    tf: str
    trend: str = "range"                     # 'up' | 'down' | 'range'
    last_high: Optional[Swing] = None
    last_low: Optional[Swing] = None
    events: list[StructureEvent] = field(default_factory=list)
    swing_count: int = 0

    @property
    def last_event(self) -> Optional[StructureEvent]:
        return self.events[-1] if self.events else None


TREND_SWINGS = 2      # spec v1.1 (D-64): the last 2 swing highs AND the last 2 swing lows (was 3)


def trend_label(sw_highs: list[Swing], sw_lows: list[Swing]) -> str:
    """up when the last 2 swing highs AND lows are ascending; down mirror; else range."""
    n = TREND_SWINGS
    if len(sw_highs) < n or len(sw_lows) < n:
        return "range"
    h = [s.price for s in sw_highs[-n:]]
    l = [s.price for s in sw_lows[-n:]]
    if all(h[i] < h[i + 1] for i in range(n - 1)) and all(l[i] < l[i + 1] for i in range(n - 1)):
        return "up"
    if all(h[i] > h[i + 1] for i in range(n - 1)) and all(l[i] > l[i + 1] for i in range(n - 1)):
        return "down"
    return "range"


def structure(candles: list[Candle], sw: list[Swing], tf: str) -> TrendState:
    """Walk the candles once: at each close the confirmed swings are known, the
    trend label is derived, and BOS/CHoCH are emitted (doc 10 §2.2).

    BOS up: close above the most recent confirmed swing high (not yet broken)
    while trend is up or range. CHoCH down: in an uptrend the first close below
    the most recent higher swing low; trend then reads range until a new BOS."""
    st = TrendState(tf=tf)
    by_conf: dict[int, list[Swing]] = {}
    for s in sw:
        by_conf.setdefault(s.confirmed_at, []).append(s)
    hs: list[Swing] = []
    ls: list[Swing] = []
    broken_high: Optional[Swing] = None
    broken_low: Optional[Swing] = None
    override: Optional[str] = None
    override_swings = 0
    trend = "range"
    for i, c in enumerate(candles):
        for s in by_conf.get(c.ts, []):
            (hs if s.kind == "high" else ls).append(s)
        label = trend_label(hs, ls)
        if override and len(hs) + len(ls) > override_swings and label != "range":
            override = None            # a NEW confirmed swing set with a non-range label supersedes the CHoCH override
        trend = override if override else label
        lh = hs[-1] if hs else None
        ll = ls[-1] if ls else None
        if lh is not None and lh is not broken_high and c.c > lh.price and trend in ("up", "range"):
            st.events.append(StructureEvent(c.ts, tf, "BOS", "up", lh.price, i))
            broken_high = lh
            override = None
        elif ll is not None and ll is not broken_low and c.c < ll.price and trend in ("down", "range"):
            st.events.append(StructureEvent(c.ts, tf, "BOS", "down", ll.price, i))
            broken_low = ll
            override = None
        elif trend == "up" and ll is not None and ll is not broken_low and c.c < ll.price:
            st.events.append(StructureEvent(c.ts, tf, "CHoCH", "down", ll.price, i))
            broken_low = ll
            override = "range"
            override_swings = len(hs) + len(ls)
        elif trend == "down" and lh is not None and lh is not broken_high and c.c > lh.price:
            st.events.append(StructureEvent(c.ts, tf, "CHoCH", "up", lh.price, i))
            broken_high = lh
            override = "range"
            override_swings = len(hs) + len(ls)
    st.trend = override if override else trend_label(hs, ls)
    st.last_high = hs[-1] if hs else None
    st.last_low = ls[-1] if ls else None
    st.swing_count = len(hs) + len(ls)
    return st


def consecutive_bos(events: list[StructureEvent], direction: str) -> int:
    """Trailing run of BOS events in `direction` (any other event ends the run)."""
    n = 0
    for e in reversed(events):
        if e.type == "BOS" and e.direction == direction:
            n += 1
        else:
            break
    return n


def last_event_of(events: list[StructureEvent], type_: str, direction: Optional[str] = None) -> Optional[StructureEvent]:
    for e in reversed(events):
        if e.type == type_ and (direction is None or e.direction == direction):
            return e
    return None
