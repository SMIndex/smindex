"""Evaluation framework for the paper strategies (docs 01–06).

A strategy implements `evaluate(ctx) -> EvalResult`. The evaluator builds one
`StrategyContext` per coin from the strat_ tables, calls each paper strategy on
its cadence, logs the result to strat_signals, emits outbox alerts, and routes
fired intents through RiskEngine (annotate-only in paper) → paper trade manager.

No waiting on history: every condition evaluates on whatever data exists today.
Lookbacks longer than the available history use the available window and carry
a "warming: M of N <unit>" label (`Cond.warming`); the same labels are collected
on the signal row (`EvalResult.warming`). A condition whose input feed has NO
rows at all is labelled "unavailable (not in score)" and its `met` stays None —
it is neither a pass nor a fail and its component is not scored.

`Cond` is one spec-§5 rule with its live value/threshold and a tri-state `met`:
True / False / None (None = unavailable, NEVER a fail and NOT counted).
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Optional

from ..intents import OrderIntent

UNAVAILABLE = "unavailable (not in score)"


def avail(have: int, need: int, unit: str = "bars") -> Optional[str]:
    """Warming label for a lookback: None when fully warm, 'warming: M of N unit'
    while short, UNAVAILABLE when there is nothing at all."""
    if have <= 0:
        return UNAVAILABLE
    if have < need:
        return f"warming: {have} of {need} {unit}"
    return None


@dataclass(slots=True)
class Cond:
    name: str
    subline: str = ""
    value: Optional[str] = None        # live value, e.g. "82nd pct"
    threshold: Optional[str] = None    # e.g. "need 60"
    met: Optional[bool] = None         # True pass / False fail / None unavailable
    warming: Optional[str] = None      # 'warming: M of N bars' | UNAVAILABLE | None

    def as_dict(self) -> dict:
        return {"name": self.name, "subline": self.subline, "value": self.value,
                "threshold": self.threshold, "met": (None if self.met is None else bool(self.met)),
                "warming": self.warming}


@dataclass(slots=True)
class EvalResult:
    coin: str
    direction: Optional[str] = None            # 'long' | 'short' | None
    conditions: list[Cond] = field(default_factory=list)
    components: dict[str, Optional[float]] = field(default_factory=dict)  # regime/bias/trigger/timing/...
    total_score: Optional[float] = None
    fire_threshold: float = 0.65
    fired: bool = False
    waiting_for: str = "Waiting for first evaluation"
    intents: list[OrderIntent] = field(default_factory=list)
    alerts: list[tuple] = field(default_factory=list)   # (kind, text) — pre/fire/skip/gauge
    not_evaluated: list[str] = field(default_factory=list)  # condition names with no data at all
    warming: list[str] = field(default_factory=list)        # 'Cond: warming: M of N bars' labels
    labels: dict = field(default_factory=dict)              # e.g. {'cohort': '7d metrics', 'liq_coverage': 'partial'}

    def score_from_components(self) -> None:
        """total = sum of evaluated components; None components are skipped (never
        treated as zero). Whether to fire is each strategy's decision via `fired`."""
        vals = [v for v in self.components.values() if v is not None]
        self.total_score = round(sum(vals), 4) if vals else None

    def collect_warming(self) -> None:
        """Copy every condition's warming/unavailable label onto the signal row."""
        self.warming = [f"{c.name}: {c.warming}" for c in self.conditions if c.warming]
        self.not_evaluated = [c.name for c in self.conditions if c.warming == UNAVAILABLE]


@dataclass(slots=True)
class StrategyContext:
    coin: str
    now_ms: int
    params: dict                        # strat_parameters for this strategy (key->float/str)
    candles_15m: list[dict] = field(default_factory=list)   # [{ts,o,h,l,c,v}] oldest→newest (last 300)
    closes_15m_30d: list[float] = field(default_factory=list)  # 15m closes, up to 31d (doc 05 RV24h pct(30d)) — 300-bar window is a loader cap, not history
    ohlc_15m_30d: list[dict] = field(default_factory=list)     # [{h,l,c}] same 31d window (s06 EMA-band occupancy label)
    candles_1h: list[dict] = field(default_factory=list)
    candles_4h: list[dict] = field(default_factory=list)
    oi_1m: list[dict] = field(default_factory=list)         # [{ts,oi_notional,funding,mark,oracle}]
    trades_1m: list[dict] = field(default_factory=list)     # [{ts,taker_buy_notional,taker_sell_notional,count}]
    book_5s: list[dict] = field(default_factory=list)       # [{ts,mid,bid_0_1,bid_0_3,bid_0_5,ask_0_1,ask_0_3,ask_0_5}]
    book_last: Optional[dict] = None                        # latest strat_book_5s row
    gauge: Optional[dict] = None                            # latest strat_gauge row
    regime: Optional[dict] = None                           # latest strat_regime row
    bias: Optional[dict] = None                             # latest strat_bias row
    liquidations: list[dict] = field(default_factory=list)  # recent strat_liquidations
    liq_coverage: str = "partial"
    liq_clusters: list[dict] = field(default_factory=list)  # [{liq_px, notional, side}] tracked-cohort liq map
    cohort: Optional[dict] = None                           # s03 cohort signal (strategies/cohort.py)
    funding_rows: int = 0                                   # HL funding history rows (z-score warming)

    def p(self, key: str, default: float = 0.0) -> float:
        v = self.params.get(key, default)
        try:
            return float(v)
        except (TypeError, ValueError):
            return default


class PaperStrategy:
    id: str = "base"
    #: evaluation cadence — 'candle_15m' (on each closed 15m candle), 'minute'
    cadence: str = "candle_15m"

    def param_defaults(self) -> dict:
        return {}

    def evaluate(self, ctx: StrategyContext) -> EvalResult:  # noqa: ARG002
        return EvalResult(coin=ctx.coin)


def pct(x: float) -> str:
    return f"{x:.0f}%"


# ---- shared helpers -------------------------------------------------------
def ohlc(candles: list[dict]):
    """(highs, lows, closes) lists from candle dicts."""
    return ([float(x["h"]) for x in candles], [float(x["l"]) for x in candles],
            [float(x["c"]) for x in candles])


def bias_align(bias_score: Optional[float], direction: Optional[str], gate: float = -0.2) -> Optional[bool]:
    """bias x direction >= gate (doc 00 §3). None when no bias row or no direction."""
    if direction is None or bias_score is None:
        return None
    return (float(bias_score) * (1 if direction == "long" else -1)) >= gate


def regime_blockers(reg: Optional[dict]) -> list[str]:
    """strat_regime.blockers is a JSON column; raw `SELECT *` via text() hands it
    back as a str — decode, never iterate the characters."""
    b = (reg or {}).get("blockers") or []
    if isinstance(b, str):
        try:
            b = json.loads(b)
        except ValueError:
            b = [b]
    return [str(x) for x in b] if isinstance(b, (list, tuple)) else [str(b)]


def regime_cond(ctx: StrategyContext) -> Cond:
    reg = ctx.regime or {}
    allowed = reg.get("allowed")
    blockers = regime_blockers(reg)
    val = "allowed" if allowed else ("blocked: " + ", ".join(str(b) for b in blockers) if blockers else "blocked")
    return Cond("Regime gate", "TRADE_ALLOWED (blocks macro windows / weekend / stale oracle)",
                value=(val if ctx.regime else "no regime row"), met=(bool(allowed) if ctx.regime else None),
                warming=(None if ctx.regime else UNAVAILABLE))
