from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from .config import common

SKIP_BELOW = 0.55
FULL_FROM = 0.70
EXIT_THRESHOLD = 2
DEAD_TRADE_MULT = 1.5
FULL_RISK_PCT = 1.5
HALF_RISK_PCT = 0.75


def clip(x: float, lo: float = 0.0, hi: float = 1.0) -> float:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return lo
    if v != v:  # NaN
        return lo
    return max(lo, min(hi, v))


@dataclass
class Reason:
    key: str
    description: str
    detector: Callable[[Any], float]         # Snapshot -> 0..1
    weight: float
    min_weight: float = 0.3
    max_weight: float = 3.0
    inputs: tuple[str, ...] = ()             # feeds the detector reads (oi/taker/liq/liqmap/gauge/book/cohort/events)


@dataclass
class Veto:
    key: str
    description: str
    detector: Callable[[Any], bool]          # Snapshot -> True when the veto applies
    inputs: tuple[str, ...] = ()


@dataclass
class ContextRule:
    key: str
    multiplier: Callable[[Any], float]       # Snapshot -> multiplier


@dataclass
class InTradeCheck:
    key: str
    description: str
    detector: Callable[[Any, Any], bool]     # (Snapshot, Position) -> True when the thesis is failing
    counts: int = 1                          # some checks count as 2 failures (e.g. level_lost)


@dataclass
class Decision:
    take: bool
    conviction: float
    raw_conviction: float
    size_tier: str                                    # none | half | full
    reasons: list[tuple[str, float, float]]           # (key, strength, weight)
    vetoes_hit: list[str]
    multipliers: list[tuple[str, float]]
    thesis: str = ""
    veto_texts: dict = field(default_factory=dict)
    notes: list[dict] = field(default_factory=list)   # weight-0 annotations (e.g. alignment_branch), never scored
    # replay-only (D-68): reasons excluded from BOTH sums because a feed they read was
    # unavailable, and vetoes skipped for the same reason — never scored as 0 / False
    excluded: list[tuple[str, float, tuple]] = field(default_factory=list)     # (key, weight, feeds)
    unevaluated: list[tuple[str, tuple]] = field(default_factory=list)          # (veto key, feeds)

    @property
    def risk_pct(self) -> float:
        return FULL_RISK_PCT if self.size_tier == "full" else HALF_RISK_PCT if self.size_tier == "half" else 0.0

    def reasons_json(self) -> list[dict]:
        tw = sum(w for _, _, w in self.reasons) or 1.0
        return ([{"key": k, "strength": round(s, 4), "weight": round(w, 4), "contribution": round(s * w / tw, 4)}
                 for k, s, w in self.reasons]
                + [{"key": n.get("key", "note"), "note": n.get("note"), "weight": 0.0} for n in self.notes]
                + [{"key": k, "note": f"excluded: {','.join(f)} unavailable", "weight": 0.0, "excluded_weight": round(w, 4)}
                   for k, w, f in self.excluded])

    def effective_max_weight(self) -> float:
        """Σ weight of the reasons that were scored (the denominator actually used)."""
        return sum(w for _, _, w in self.reasons)

    def multipliers_json(self) -> list[dict]:
        return [{"key": k, "m": round(m, 4)} for k, m in self.multipliers]

    def vetoes_json(self) -> list[dict]:
        return ([{"key": k, "text": self.veto_texts.get(k, "")} for k in self.vetoes_hit]
                + [{"key": k, "unevaluated": True, "text": f"not evaluated: {','.join(f)} unavailable"} for k, f in self.unevaluated])

    def top3(self) -> str:
        rs = sorted(self.reasons, key=lambda r: -(r[1] * r[2]))[:3]
        return ", ".join(f"{k} {s:.2f}" for k, s, _ in rs)

    def weakest(self) -> str:
        if not self.reasons:
            return "-"
        k, s, _ = min(self.reasons, key=lambda r: r[1] * r[2])
        return f"{k} {s:.2f}"


@dataclass
class Position:
    """What Mind.manage needs about an open paper trade (model_runner fills it)."""
    trade_id: int
    model: str
    coin: str
    direction: str
    entry_px: float
    stop_px: float
    target_px: Optional[float]
    size: float
    fill_ts: int
    initial_risk: float                 # |entry - initial stop| in price
    expected_hold_min: int
    setup: dict = field(default_factory=dict)      # level, wick, ob_bottom, entry-time OI/cohort, ...
    lifecycle: dict = field(default_factory=dict)  # fail_streak, t1_done, checks history, ...
    now_ms: int = 0

    def minutes_in_trade(self) -> float:
        return (self.now_ms - self.fill_ts) / 60_000.0

    def progress_r(self, price: float) -> float:
        if self.initial_risk <= 0:
            return 0.0
        sign = 1.0 if self.direction == "long" else -1.0
        return (price - self.entry_px) * sign / self.initial_risk


@dataclass
class ManageResult:
    action: str                       # hold | exit
    reason: str = ""                  # thesis_failed | dead_trade | ''
    failing: list[str] = field(default_factory=list)
    fail_count: int = 0
    streak: int = 0
    progress_r: float = 0.0


class Mind:
    """Deterministic evaluation (doc 10 §4.3): raw = Σ strength·weight / Σ weight;
    conviction = min(1.0, raw × Π multipliers) (cap: spec v1.3 D-90); any veto →
    no trade; <0.55 skip; 0.55–0.70 half size; ≥0.70 full."""

    def __init__(self, model: str, reasons: list[Reason], vetoes: list[Veto], rules: list[ContextRule],
                 checks: list[InTradeCheck], exit_threshold: int | None = None) -> None:
        self.model = model
        self.reasons = reasons
        self.vetoes = vetoes
        self.rules = rules
        self.checks = checks
        cfg = common()
        self.exit_threshold = int(exit_threshold if exit_threshold is not None else cfg.get("exit_threshold", EXIT_THRESHOLD))
        self.skip_below = float(cfg.get("conviction_skip_below", SKIP_BELOW))
        self.full_from = float(cfg.get("conviction_full_from", FULL_FROM))
        self.dead_mult = float(cfg.get("dead_trade_hold_multiple", DEAD_TRADE_MULT))

    # ---- weights --------------------------------------------------------
    def weights(self) -> dict[str, float]:
        return {r.key: r.weight for r in self.reasons}

    def apply_weights(self, w: dict[str, float]) -> None:
        for r in self.reasons:
            if r.key in w:
                r.weight = max(r.min_weight, min(r.max_weight, float(w[r.key])))

    # ---- evaluate -------------------------------------------------------
    def evaluate(self, snap) -> Decision:
        reasons: list[tuple[str, float, float]] = []
        # replay-only (D-68): Snapshot.unavailable names feeds with no data at this boundary.
        # A reason reading one of them leaves BOTH sums; a veto reading one is skipped.
        # The live worker never sets it, so live evaluation is unchanged.
        unavailable = set(getattr(snap, "unavailable", None) or ())
        excluded: list[tuple[str, float, tuple]] = []
        unevaluated: list[tuple[str, tuple]] = []
        for r in self.reasons:
            if unavailable and r.inputs and (set(r.inputs) & unavailable):
                excluded.append((r.key, r.weight, tuple(sorted(set(r.inputs) & unavailable))))
                continue
            try:
                st = clip(r.detector(snap))
            except Exception:  # noqa: BLE001 — a detector fault reads as 0, never as a crash
                st = 0.0
            reasons.append((r.key, st, r.weight))
        tw = sum(w for _, _, w in reasons)
        raw = sum(s * w for _, s, w in reasons) / tw if tw > 0 else 0.0
        mults: list[tuple[str, float]] = []
        prod = 1.0
        for rule in self.rules:
            try:
                m = float(rule.multiplier(snap))
            except Exception:  # noqa: BLE001
                m = 1.0
            if m != m or m <= 0:
                m = 1.0
            mults.append((rule.key, m))
            prod *= m
        conviction = min(1.0, raw * prod)          # spec v1.3 Part 4 (D-90): capped after the multipliers
        hit: list[str] = []
        texts: dict[str, str] = {}
        for v in self.vetoes:
            if unavailable and v.inputs and (set(v.inputs) & unavailable):
                unevaluated.append((v.key, tuple(sorted(set(v.inputs) & unavailable))))
                continue
            try:
                ok = bool(v.detector(snap))
            except Exception:  # noqa: BLE001
                ok = False
            if ok:
                hit.append(v.key)
                texts[v.key] = v.description
        extra = getattr(snap, "extra_vetoes", None) or []
        for k, t in extra:
            hit.append(k)
            texts[k] = t
        if hit or conviction < self.skip_below:
            tier = "none"
        elif conviction < self.full_from:
            tier = "half"
        else:
            tier = "full"
        take = not hit and conviction >= self.skip_below
        return Decision(take, round(conviction, 4), round(raw, 4), tier, reasons, hit, mults, veto_texts=texts,
                        excluded=excluded, unevaluated=unevaluated)

    # ---- manage ---------------------------------------------------------
    def manage(self, snap, pos: Position, price: float) -> ManageResult:
        """Every closed 15m in position. failing >= exit_threshold for 2
        consecutive candles → exit thesis_failed; universal dead-trade at 1.5×
        expected hold with progress < 0.5R."""
        failing: list[str] = []
        count = 0
        for ch in self.checks:
            try:
                bad = bool(ch.detector(snap, pos))
            except Exception:  # noqa: BLE001
                bad = False
            if bad:
                failing.append(ch.key)
                count += ch.counts
        streak = int(pos.lifecycle.get("fail_streak", 0))
        streak = streak + 1 if count >= self.exit_threshold else 0
        pos.lifecycle["fail_streak"] = streak
        prog = pos.progress_r(price)
        if streak >= 2:
            return ManageResult("exit", "thesis_failed", failing, count, streak, prog)
        if pos.minutes_in_trade() >= self.dead_mult * pos.expected_hold_min and prog < 0.5:
            return ManageResult("exit", "dead_trade", failing, count, streak, prog)
        return ManageResult("hold", "", failing, count, streak, prog)
