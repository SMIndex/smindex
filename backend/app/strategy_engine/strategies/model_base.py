"""Shared base for the structural models M1–M6 (docs 10–17).

A model = a setup finder + a Mind (reasons / vetoes / multipliers / in-trade
checks) + an execution recipe. `ModelStrategy.evaluate(snap)` is pure: it reads
one Snapshot and returns a ModelEval (signal-row fields, alerts, an optional
ModelIntent and state updates). The runner (model_runner.py) does the IO.

No setup → the Mind still evaluates (D-09) with a synthetic veto
`no_setup: <what is missing>`, so every row has raw/conviction/reasons/vetoes/
multipliers/thesis populated and is honestly a non-trade.
"""
from __future__ import annotations

import datetime as _dt
from dataclasses import dataclass, field
from typing import Callable, Optional

from app.strategy_engine.mind.base import ContextRule, Decision, InTradeCheck, Mind, Reason, Veto, clip
from app.strategy_engine.mind.snapshot import Snapshot
from app.strategy_engine.structure.candles import Candle, MIN_MS, TF_MS
from app.strategy_engine.structure.pools import Pool
from app.strategy_engine.structure.sessions import day_start, utc_hour, weekday
from app.strategy_engine.structure.swings import Swing

H_MS = 3_600_000
DAY_MS = 24 * H_MS


# ---------------------------------------------------------------------------
# results
# ---------------------------------------------------------------------------
@dataclass
class ModelIntent:
    direction: str                      # long | short
    entry_px: float
    stop_px: float
    t1: Optional[float]
    t2: Optional[float]
    t3: Optional[float] = None
    entry_valid_until_ms: int = 0       # resting post-only order cancelled after this
    hard_stop_ts: int = 0               # position force-closed (time_stop) at this ts
    trail_tf: str = "15m"               # swing tf for the trail (D-11)
    trail_after: str = "t1"             # t1 | t2 | near_t2 | never
    partial_pct: float = 40.0           # % closed at T1
    fallback_entry: Optional[dict] = None   # {px, valid_until_ms} — second attempt if the first expires unfilled (M1 FVG mid)
    entry_offset_note: str = ""
    # spec v1.3 Part 2 (D-88): the stop the model computed before the minimum-distance
    # floor, and whether the floor moved it. stop_px is always the FINAL stop.
    stop_structural: Optional[float] = None
    stop_floor_applied: bool = False


# spec v1.3 Part 2 (D-88): minimum stop distance = STOP_FLOOR_ATR × ATR(stop_floor_tf)
STOP_FLOOR_ATR = 0.5


@dataclass
class ModelEval:
    model: str
    coin: str
    direction: Optional[str]
    level_type: Optional[str]
    level_price: Optional[float]
    setup: dict
    decision: Decision
    waiting_for: str
    alerts: list[tuple[str, str, str]] = field(default_factory=list)   # (kind, dedupe_key, message)
    intent: Optional[ModelIntent] = None
    state_updates: dict = field(default_factory=dict)                  # mind_model_state key -> value


# ---------------------------------------------------------------------------
# formatting
# ---------------------------------------------------------------------------
def fmt_px(v) -> str:
    if v is None:
        return "-"
    v = float(v)
    if v >= 1000:
        return f"{v:,.1f}"
    if v >= 10:
        return f"{v:.2f}"
    return f"{v:.4f}"


def fmt_pct(v, digits: int = 2) -> str:
    """fraction → signed percent string."""
    if v is None:
        return "n/a"
    return f"{float(v) * 100:+.{digits}f}%"


def fmt_usd(v) -> str:
    if v is None:
        return "$0"
    v = float(v)
    if abs(v) >= 1_000_000:
        return f"${v / 1_000_000:.2f}M"
    if abs(v) >= 1_000:
        return f"${v / 1_000:.0f}k"
    return f"${v:.0f}"


def fmt_atr(v) -> str:
    return "n/a" if v is None else f"{float(v):.2f}"


def fmt_x(v, digits: int = 2) -> str:
    return "n/a" if v is None else f"{float(v):.{digits}f}"


def weekday_name(ts: int) -> str:
    return ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")[weekday(ts)]


def utc_str(ts: int) -> str:
    return _dt.datetime.fromtimestamp(ts / 1000.0, tz=_dt.timezone.utc).strftime("%Y-%m-%d %H:%M")


def day_key(ts: int) -> str:
    return _dt.datetime.fromtimestamp(ts / 1000.0, tz=_dt.timezone.utc).strftime("%Y-%m-%d")


def week_key(ts: int) -> str:
    d = _dt.datetime.fromtimestamp(ts / 1000.0, tz=_dt.timezone.utc)
    y, w, _ = d.isocalendar()
    return f"{y}-W{w:02d}"


# ---------------------------------------------------------------------------
# direction helpers
# ---------------------------------------------------------------------------
def sgn(direction: str) -> float:
    return 1.0 if direction == "long" else -1.0


def opposite(direction: str) -> str:
    return "short" if direction == "long" else "long"


def trend_for(direction: str) -> str:
    """Trend label (TrendState.trend / daily_bias): 'up' | 'down'."""
    return "up" if direction == "long" else "down"


def day_trend_for(direction: str) -> str:
    """Day-type label (day_type.classify): 'trend_up' | 'trend_down'."""
    return "trend_up" if direction == "long" else "trend_down"


def side_word(direction: str) -> str:
    """The level side a long sweeps ('low') / a short sweeps ('high')."""
    return "low" if direction == "long" else "high"


# ---------------------------------------------------------------------------
# shared detector pieces (each model composes these with its own weights)
# ---------------------------------------------------------------------------
def reclaim_strength(setup: dict) -> float:
    """doc 11/15: clip((rq−0.4)/0.4) × 0.7 when the reclaim took all 3 candles."""
    rq = float(setup.get("reclaim_quality") or 0.0)
    v = clip((rq - 0.4) / 0.4)
    if int(setup.get("candles_to_reclaim") or 1) >= 3:
        v *= 0.7
    return v


def form_rule(losses: int = 3, wins: int = 5, loss_m: float = 0.8, win_m: float = 0.9) -> ContextRule:
    def m(snap: Snapshot) -> float:
        if snap.consecutive("loss") >= losses:
            return loss_m
        if snap.consecutive("win") >= wins:
            return win_m
        return 1.0
    return ContextRule("recent_form", m)


def event_2h_rule(m: float = 0.8) -> ContextRule:
    return ContextRule("event_2h", lambda snap: m if snap.s.event_within_2h else 1.0)


def event_30m_veto() -> Veto:
    return Veto("event_30m", "scheduled event within 30 minutes", lambda snap: bool(snap.s.event_within_30m))


def funding_extreme_veto(key: str, direction_of: Callable[[Snapshot], Optional[str]]) -> Veto:
    def d(snap: Snapshot) -> bool:
        dr = direction_of(snap)
        return bool(dr) and snap.funding_extreme(dr)
    return Veto(key, "funding crowding EXTREME on the trade's side", d)


def day_type_rule(table: dict[str, float], default: float = 1.0,
                  fn: Optional[Callable[[Snapshot], Optional[float]]] = None) -> ContextRule:
    """Multiplier from the running day type; `fn` may return a direction-aware override."""
    def m(snap: Snapshot) -> float:
        if fn is not None:
            v = fn(snap)
            if v is not None:
                return v
        return table.get(snap.day_type, default)
    return ContextRule("day_type", m)


def session_rule(table: dict[str, float], default: float = 1.0, first_hours: Optional[float] = None) -> ContextRule:
    def m(snap: Snapshot) -> float:
        if first_hours is not None and snap.session in ("london", "newyork") and snap.minutes_into_session <= 120:
            return first_hours
        return table.get(snap.session, default)
    return ContextRule("session", m)


def setup_val(key: str, default=0.0) -> Callable[[Snapshot], float]:
    return lambda snap: snap.setup.get(key, default) if snap.setup else default


def coincident_bonus(level: float, others: list[tuple[str, float]], atr: float, own_type: str) -> float:
    """+0.2 when a second level TYPE lies within 0.2 ATR of `level` (doc 11/13)."""
    if atr <= 0:
        return 0.0
    for t, px in others:
        if t != own_type and abs(px - level) <= 0.2 * atr:
            return 0.2
    return 0.0


def level_candidates(snap: Snapshot, direction: str) -> list[tuple[str, float, float, dict]]:
    """(type, price, base location strength, meta) of every level a long could sweep
    below (or a short above), from the structure bundle. Strengths per doc 11 §5
    (M1/M5 use them; M3 has its own table)."""
    s = snap.s
    out: list[tuple[str, float, float, dict]] = []
    refs = s.refs or {}
    low = direction == "long"
    if low:
        for key, strength in (("pwl", 1.0), ("pdl", 0.8), ("asia_low", 0.6), ("london_low", 0.6)):
            if refs.get(key) is not None:
                out.append((key, float(refs[key]), strength, {}))
    else:
        for key, strength in (("pwh", 1.0), ("pdh", 0.8), ("asia_high", 0.6), ("london_high", 0.6)):
            if refs.get(key) is not None:
                out.append((key, float(refs[key]), strength, {}))
    for p in s.pools:
        if low and p.type == "equal_lows":
            out.append((f"equal_lows_{p.tf}", p.level, 1.0 if p.tf == "4h" else 0.8, {"tf": p.tf}))
        if (not low) and p.type == "equal_highs":
            out.append((f"equal_highs_{p.tf}", p.level, 1.0 if p.tf == "4h" else 0.8, {"tf": p.tf}))
    for z in s.zones.get("4h", []):
        if z.status in ("fresh", "tested", "open", "half_filled"):
            if low and z.direction == "bullish":
                out.append((f"4h_{z.type.lower()}_bottom", z.bottom, 1.0, {"zone": z.as_dict()}))
            if (not low) and z.direction == "bearish":
                out.append((f"4h_{z.type.lower()}_top", z.top, 1.0, {"zone": z.as_dict()}))
    thr = snap.band_p80()               # spec v1.1 Part C: eligible cluster threshold = band_p80 (was 0.15% OI)
    for c in s.clusters:
        if thr > 0 and c.notional >= thr:
            if low and c.side == "long" and c.level < snap.price:
                out.append(("liq_cluster_long", c.level, 0.7, {"notional": c.notional, "wallets": c.wallets}))
            if (not low) and c.side == "short" and c.level > snap.price:
                out.append(("liq_cluster_short", c.level, 0.7, {"notional": c.notional, "wallets": c.wallets}))
    return out


def pools_beyond(snap: Snapshot, price: float, direction: str) -> list[Pool]:
    """Pools in the direction of the trade (targets): above for longs, below for shorts."""
    if direction == "long":
        return sorted([p for p in snap.s.pools if p.level > price], key=lambda p: p.level - price)
    return sorted([p for p in snap.s.pools if p.level < price], key=lambda p: price - p.level)


def nearest_pool_beyond(snap: Snapshot, price: float, direction: str, min_dist: float = 0.0) -> Optional[Pool]:
    for p in pools_beyond(snap, price, direction):
        if abs(p.level - price) >= min_dist:
            return p
    return None


def candles_since(candles: list[Candle], ts: int) -> list[Candle]:
    """Closed candles that opened at or after `ts` (i.e. close ts > ts)."""
    return [c for c in candles if c.ts > ts]


def candles_after_index(candles: list[Candle], idx: int) -> list[Candle]:
    return candles[idx + 1:]


def index_of_ts(candles: list[Candle], ts: int) -> int:
    for i, c in enumerate(candles):
        if c.ts == ts:
            return i
    return -1


def higher_low_since(candles: list[Candle], fill_ts: int, ref_low: float, direction: str = "long") -> Optional[bool]:
    """D-25: with ≥2 closed candles since fill, True when some candle since entry
    has low > ref_low AND low ≥ the previous candle's low (a higher low formed);
    None when fewer than 2 closed candles exist yet. Mirror for shorts."""
    cs = candles_since(candles, fill_ts)
    if len(cs) < 2:
        return None
    for i in range(1, len(cs)):
        if direction == "long":
            if cs[i].l > ref_low and cs[i].l >= cs[i - 1].l:
                return True
        else:
            if cs[i].h < ref_low and cs[i].h <= cs[i - 1].h:
                return True
    return False


RECLAIM_ENTRY_OFFSET_ATR = 0.05
RECLAIM_ENTRY_NOTE = "min(50% of reclaim candle, reclaim close) − 0.05 ATR"


def reclaim_entry_px(direction: str, rc_high: float, rc_low: float, rc_close: float, atr: float) -> float:
    """Spec v1.3 Part 3 (D-89), M1 + M5 entry: min(50% of the reclaim candle, its
    close) − 0.05 ATR for longs (max(...) + 0.05 ATR for shorts) so the resting
    post-only order always sits below (above) the market."""
    mid = (float(rc_high) + float(rc_low)) / 2.0
    if direction == "long":
        return min(mid, float(rc_close)) - RECLAIM_ENTRY_OFFSET_ATR * atr
    return max(mid, float(rc_close)) + RECLAIM_ENTRY_OFFSET_ATR * atr


def last_confirmed_swing(sw: list[Swing], kind: str, now_ms: int, after_ts: Optional[int] = None) -> Optional[Swing]:
    for s in reversed(sw):
        if s.kind == kind and s.confirmed_at <= now_ms and (after_ts is None or s.ts > after_ts):
            return s
    return None


def oi_change_between(snap: Snapshot, t0: int, t1: int) -> Optional[float]:
    return snap.oi_change(t0, t1)


def cvd_lower_extreme(cvd: list[Optional[float]], i_prior: int, i_new: int, direction: str) -> Optional[bool]:
    """Bearish divergence for longs (price lower low, CVD NOT lower low) is what
    docs call 'CVD lower low not confirmed'. Returns True when CVD made a LOWER
    low (confirmed), False when not, None when no tape."""
    if i_prior < 0 or i_new < 0 or i_prior >= len(cvd) or i_new >= len(cvd):
        return None
    a, b = cvd[i_prior], cvd[i_new]
    if a is None or b is None:
        return None
    return (b < a) if direction == "long" else (b > a)


def taker_ratio_candles(snap: Snapshot, candles: list[Candle], tf: str = "15m") -> Optional[float]:
    if not candles:
        return None
    t = snap.taker(candles[0].ts - TF_MS[tf], candles[-1].ts)
    return None if t is None else t["ratio"]


def attempts_today(state: dict, key: str, now_ms: int) -> int:
    v = state.get(key) or {}
    return int(v.get("n", 0)) if v.get("day") == day_key(now_ms) else 0


def bump_attempts(state: dict, key: str, now_ms: int) -> dict:
    n = attempts_today(state, key, now_ms) + 1
    return {key: {"day": day_key(now_ms), "n": n}}


def minutes_to_utc(now_ms: int, hour: int) -> float:
    """Minutes until the next `hour`:00 UTC."""
    ds = day_start(now_ms)
    t = ds + hour * H_MS
    if t <= now_ms:
        t += DAY_MS
    return (t - now_ms) / MIN_MS


def is_friday_after(now_ms: int, hour: int) -> bool:
    return weekday(now_ms) == 4 and utc_hour(now_ms) >= hour


# ---------------------------------------------------------------------------
# base class
# ---------------------------------------------------------------------------
class ModelStrategy:
    """Subclasses set the class attributes, build `self.mind` in __init__ and
    implement find_setup / build_intent / thesis / alert_detected."""

    id: str = ""
    model: str = ""
    name: str = ""
    expected_hold_min: int = 90
    hard_stop_min: int = 240
    detect_alert_kind: str = "detected"
    stop_floor_tf: str = "15m"          # D-88: ATR timeframe of the minimum stop distance (15m: M1/M3/M5; 1h: M2/M4/M6)
    mind: Mind

    # -- to implement --------------------------------------------------------
    def find_setup(self, snap: Snapshot) -> tuple[Optional[dict], str]:
        """(setup facts, '') when the documented sequence is complete on this
        closed candle; (None, what is missing) otherwise. Pure."""
        raise NotImplementedError

    def build_intent(self, snap: Snapshot, setup: dict, d: Decision) -> Optional[ModelIntent]:
        raise NotImplementedError

    def thesis(self, snap: Snapshot, setup: dict, d: Decision) -> str:
        raise NotImplementedError

    def alert_detected(self, snap: Snapshot, setup: dict) -> str:
        return f"[{self.model}] {snap.coin}: {self.detect_alert_kind} at {fmt_px(setup.get('level'))}"

    def state_updates(self, snap: Snapshot, setup: Optional[dict], d: Optional[Decision], took: bool) -> dict:
        return {}

    def detected_key(self, snap: Snapshot, setup: dict) -> str:
        return f"{setup.get('level_type')}:{round(float(setup.get('level') or 0), 2)}:{setup.get('trigger_ts')}"

    def pre_alerts(self, snap: Snapshot) -> list[tuple[str, str, str]]:
        """Pre-alerts while the sequence is in progress (e.g. 'sweep detected,
        awaiting reclaim'). (kind, dedupe_key, message). Default none."""
        return []

    def apply_stop_floor(self, snap: Snapshot, setup: dict, intent: ModelIntent) -> ModelIntent:
        """Spec v1.3 Part 2 (D-88): after the structural stop, if the stop distance is
        below STOP_FLOOR_ATR × ATR(stop_floor_tf) move the stop out to EXACTLY that
        distance beyond the entry. Size and R use the final stop (the runner sizes
        from intent.stop_px); targets stay as the model computed them. ATR 0 or
        missing → no floor (never invent a distance)."""
        atr = float(snap.atr(self.stop_floor_tf) or 0.0)
        entry, stop = float(intent.entry_px), float(intent.stop_px)
        intent.stop_structural = stop
        setup["stop_structural"] = stop
        setup["stop_floor_applied"] = False
        setup["stop_floor_tf"] = self.stop_floor_tf
        if atr <= 0:
            return intent
        floor = STOP_FLOOR_ATR * atr
        if abs(entry - stop) < floor:
            intent.stop_px = entry - floor if intent.direction == "long" else entry + floor
            intent.stop_floor_applied = True
            setup["stop"] = intent.stop_px
            setup["stop_floor_applied"] = True
        return intent

    # -- evaluate ------------------------------------------------------------
    def evaluate(self, snap: Snapshot) -> ModelEval:
        setup, missing = self.find_setup(snap)
        coin = snap.coin
        if setup is None:
            snap.setup = {}
            snap.extra_vetoes = list(snap.extra_vetoes or []) + [("no_setup", f"no setup — {missing}")]
            d = self.mind.evaluate(snap)
            d.thesis = f"{self.model} {coin}: no setup — {missing}"
            try:
                pre = self.pre_alerts(snap)
            except Exception:  # noqa: BLE001 — a pre-alert fault never blocks the evaluation row
                pre = []
            return ModelEval(self.model, coin, None, None, None, {}, d, missing, alerts=pre,
                             state_updates=self.state_updates(snap, None, d, False))
        snap.setup = setup
        d = self.mind.evaluate(snap)
        d.thesis = self.thesis(snap, setup, d)
        if setup.get("alignment_branch"):
            d.notes.append({"key": "alignment_branch", "note": str(setup["alignment_branch"])})
        alerts: list[tuple[str, str, str]] = []
        key = self.detected_key(snap, setup)
        alerts.append((f"{self.model}_detected", f"{self.model}:{coin}:detected:{key}", self.alert_detected(snap, setup)))
        intent = None
        waiting = ""
        took = False
        if d.take:
            intent = self.build_intent(snap, setup, d)
            if intent is not None:
                intent = self.apply_stop_floor(snap, setup, intent)
            if intent is None:
                waiting = "setup complete but no valid entry recipe (stop/entry could not be placed)"
                alerts.append((f"{self.model}_skip", f"{self.model}:{coin}:skip:{key}",
                               f"[{self.model}] {coin} SKIP — {waiting}. Conviction {d.conviction:.2f}."))
            else:
                took = True
                waiting = f"TAKE {intent.direction} conviction {d.conviction:.2f} ({d.size_tier})"
                alerts.append((f"{self.model}_take", f"{self.model}:{coin}:take:{key}",
                               f"[{self.model}] {coin} TAKE {intent.direction.upper()} @ {fmt_px(intent.entry_px)} "
                               f"stop {fmt_px(intent.stop_px)} T1 {fmt_px(intent.t1)} T2 {fmt_px(intent.t2)} "
                               f"· conviction {d.conviction:.2f} ({d.size_tier})\n{d.thesis}"))
        else:
            if d.vetoes_hit:
                waiting = "vetoed: " + ", ".join(d.vetoes_hit)
            else:
                waiting = f"conviction {d.conviction:.2f} below {self.mind.skip_below:.2f}"
            alerts.append((f"{self.model}_skip", f"{self.model}:{coin}:skip:{key}",
                           f"[{self.model}] {coin} SKIP {setup.get('direction', '')} at {fmt_px(setup.get('level'))} — {waiting}. "
                           f"raw {d.raw_conviction:.2f} → {d.conviction:.2f}. Weakest: {d.weakest()}."))
        return ModelEval(self.model, coin, setup.get("direction"), setup.get("level_type"), setup.get("level"),
                         setup, d, waiting, alerts, intent, self.state_updates(snap, setup, d, took))


# ---------------------------------------------------------------------------
# in-trade check helpers (Position is mind.base.Position)
# ---------------------------------------------------------------------------
def check_no_higher_low(snap: Snapshot, pos) -> bool:
    """no_higher_low: ≥2 closed candles since fill and no higher low above the wick (D-25)."""
    ref = float(pos.setup.get("wick_price") or pos.stop_px)
    r = higher_low_since(snap.s.c15, pos.fill_ts, ref, pos.direction)
    return r is False


def check_oi_bleeding(threshold: float):
    """OI fell by more than `threshold` (fraction) since entry."""
    def d(snap: Snapshot, pos) -> bool:
        ch = snap.oi_change(pos.fill_ts)
        return ch is not None and ch <= -threshold
    return d


def check_delta_negative(snap: Snapshot, pos) -> bool:
    """Taker delta against the position on the last 2 closed 15m candles."""
    cs = snap.s.c15[-2:]
    if len(cs) < 2:
        return False
    vals = [snap.taker_candle(c) for c in cs]
    if any(v is None for v in vals):
        return False
    if pos.direction == "long":
        return all(v["delta"] < 0 for v in vals)
    return all(v["delta"] > 0 for v in vals)


def check_close_beyond_level(level_key: str, tf: str = "15m"):
    """Last closed candle of `tf` closed on the wrong side of setup[level_key]."""
    def d(snap: Snapshot, pos) -> bool:
        lvl = pos.setup.get(level_key)
        if lvl is None:
            return False
        cs = {"15m": snap.s.c15, "1h": snap.s.c1h, "4h": snap.s.c4h}[tf]
        if not cs or cs[-1].ts <= pos.fill_ts:
            return False
        c = cs[-1]
        return c.c < float(lvl) if pos.direction == "long" else c.c > float(lvl)
    return d


def check_no_progress(candles_n: int, ref_key: str = "entry_candle_high", tf: str = "15m"):
    """`candles_n` closed candles since entry without a new extreme beyond setup[ref_key]."""
    def d(snap: Snapshot, pos) -> bool:
        cs = {"15m": snap.s.c15, "1h": snap.s.c1h, "4h": snap.s.c4h}[tf]
        since = candles_since(cs, pos.fill_ts)
        if len(since) < candles_n:
            return False
        ref = pos.setup.get(ref_key)
        if ref is None:
            return False
        ref = float(ref)
        if pos.direction == "long":
            return not any(c.h > ref for c in since)
        return not any(c.l < ref for c in since)
    return d


def check_cohort_flip(threshold: float):
    """Cohort net_dir moved against the position by ≥ threshold since entry."""
    def d(snap: Snapshot, pos) -> bool:
        now = snap.cohort_net_dir
        at_entry = pos.setup.get("cohort_net_dir_entry")
        if now is None or at_entry is None:
            return False
        delta = float(now) - float(at_entry)
        return delta >= threshold if pos.direction == "short" else delta <= -threshold
    return d


def build_mind(model: str, reasons: list[tuple[str, str, float, Callable]], vetoes: list[Veto],
               rules: list[ContextRule], checks: list[tuple[str, str, Callable, int]]) -> Mind:
    from .feed_inputs import REASON_INPUTS, VETO_INPUTS
    ri, vi = REASON_INPUTS.get(model, {}), VETO_INPUTS.get(model, {})
    for v in vetoes:
        if not v.inputs and v.key in vi:
            v.inputs = tuple(vi[v.key])
    return Mind(model,
                [Reason(k, desc, fn, w, inputs=tuple(ri.get(k, ()))) for k, desc, w, fn in reasons],
                vetoes, rules,
                [InTradeCheck(k, desc, fn, n) for k, desc, fn, n in checks])
