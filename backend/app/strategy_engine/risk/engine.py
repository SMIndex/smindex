"""RiskEngine (doc 00 §5). Pure rule core (fully unit-tested) + a thin DB-backed
wrapper. In PAPER these rules run in SIMULATION so paper results are realistic —
they are not live gates. Demotions move a strategy to `paper-paused` (never off);
re-arm is a manual toggle + reason.

State lives only in strat_risk_state (global) and strat_strategy_state (per
strategy). Nothing here places an order.
"""
from __future__ import annotations

import datetime as _dt
from dataclasses import dataclass, field, replace
from typing import Optional

from .sizing import Sized, size_for

_HOUR_MS = 3_600_000
DAILY_CAP_PCT = 4.0
MAX_CONCURRENT = 2
CONSEC_LOSS_LIMIT = 2
CONSEC_LOSS_PAUSE_MS = 4 * _HOUR_MS
ROLLING_PF_WINDOW = 20
ROLLING_PF_MIN = 0.9


def next_utc_midnight_ms(now_ms: int) -> int:
    d = _dt.datetime.fromtimestamp(now_ms / 1000.0, tz=_dt.timezone.utc)
    nxt = (d + _dt.timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    return int(nxt.timestamp() * 1000)


@dataclass(frozen=True, slots=True)
class OpenPos:
    strategy_id: str
    asset: str
    direction: str          # 'long' | 'short'


@dataclass(frozen=True, slots=True)
class GlobalRisk:
    equity_usd: float
    daily_realized_pnl: float = 0.0
    daily_cap_hit_until: Optional[int] = None
    consecutive_losses: int = 0
    paused_until: Optional[int] = None
    open_positions: tuple[OpenPos, ...] = ()


@dataclass(frozen=True, slots=True)
class StratRisk:
    strategy_id: str
    paper_paused: bool = False
    paused_reason: Optional[str] = None
    recent_pnls: tuple[float, ...] = ()      # rolling closed-trade net PnLs (most recent last)


def rolling_pf(pnls: tuple[float, ...], window: int = ROLLING_PF_WINDOW) -> Optional[float]:
    seg = pnls[-window:]
    if len(seg) < window:
        return None                           # not enough trades — never a fail
    gains = sum(p for p in seg if p > 0)
    losses = -sum(p for p in seg if p < 0)
    if losses <= 0:
        return float("inf")
    return gains / losses


def can_open(g: GlobalRisk, s: StratRisk, asset: str, direction: str, now_ms: int) -> tuple[bool, str]:
    """Doc 00 §5 concurrency/pause checks. Returns (allowed, reason)."""
    if s.paper_paused:
        return False, s.paused_reason or "strategy paper-paused"
    if g.daily_cap_hit_until and now_ms < g.daily_cap_hit_until:
        return False, "daily loss cap (4%) hit — paper until next UTC day"
    if g.paused_until and now_ms < g.paused_until:
        return False, "paused 4h after 2 consecutive losses"
    if len(g.open_positions) >= MAX_CONCURRENT:
        return False, f"max {MAX_CONCURRENT} concurrent positions"
    for op in g.open_positions:
        if op.asset == asset and op.direction == direction:
            return False, "no duplicate direction per asset"
    return True, "ok"


def register_exit(g: GlobalRisk, s: StratRisk, pnl_net: float, now_ms: int) -> tuple[GlobalRisk, StratRisk, list[str]]:
    """Apply a closed trade. Returns (global, strat, demotions[])."""
    demotions: list[str] = []
    daily = g.daily_realized_pnl + pnl_net
    cap_until = g.daily_cap_hit_until
    consec = g.consecutive_losses + 1 if pnl_net < 0 else 0
    paused_until = g.paused_until

    if daily <= -abs(g.equity_usd) * (DAILY_CAP_PCT / 100.0):
        cap_until = next_utc_midnight_ms(now_ms)
        demotions.append("daily loss cap (4%) reached — paper-paused until 00:00 UTC")
    if consec >= CONSEC_LOSS_LIMIT:
        paused_until = now_ms + CONSEC_LOSS_PAUSE_MS
        demotions.append("2 consecutive losses — paused 4h")

    pnls = (*s.recent_pnls, pnl_net)
    pf = rolling_pf(pnls)
    paper_paused = s.paper_paused
    reason = s.paused_reason
    if pf is not None and pf < ROLLING_PF_MIN:
        paper_paused = True
        reason = f"rolling 20-trade PF {pf:.2f} < 0.9 — paper-paused (re-arm to review)"
        demotions.append(reason)

    return (
        replace(g, daily_realized_pnl=daily, daily_cap_hit_until=cap_until,
                consecutive_losses=consec, paused_until=paused_until),
        replace(s, recent_pnls=pnls[-ROLLING_PF_WINDOW:], paper_paused=paper_paused, paused_reason=reason),
        demotions,
    )


def rearm(s: StratRisk, reason: str) -> StratRisk:
    """Manual re-arm (doc 00 §5): clears the paper-paused kill switch with a reason."""
    return replace(s, paper_paused=False, paused_reason=None)


def open_after_fill(g: GlobalRisk, pos: OpenPos) -> GlobalRisk:
    return replace(g, open_positions=(*g.open_positions, pos))


def close_position(g: GlobalRisk, strategy_id: str, asset: str) -> GlobalRisk:
    remaining = tuple(op for op in g.open_positions if not (op.strategy_id == strategy_id and op.asset == asset))
    return replace(g, open_positions=remaining)


class RiskEngine:
    """DB-backed wrapper around the pure rules. Loads state from
    strat_risk_state / strat_strategy_state, applies a rule, persists. Kept thin;
    the tested logic is the module functions above."""

    def __init__(self, session_factory) -> None:
        self._sf = session_factory

    def size_for(self, entry: float, stop: float, equity: float, risk_pct: float = 1.5, max_leverage: float = 3.0) -> Sized:
        return size_for(entry, stop, equity, risk_pct, max_leverage)

    # D-100 (2026-09-11): the DB gate now lives in evaluator._route /
    # evaluator._strat_risk (which load GlobalRisk + StratRisk and call can_open
    # before any paper entry) and in execution.manager._apply_risk (which
    # persists the outcome). This comment used to say the wrappers were "left
    # unimplemented" — that was the whole reason the risk engine never blocked
    # anything for six days of paper trading.

    async def rearm(self, sid: str, reason: str) -> bool:
        """Manual re-arm of a paper-paused strategy (doc 00 §5). Clears the kill
        switch, records who/why in strat_changelog and returns it to paper.
        There is no automatic path back — that is the point."""
        import json as _json
        import time as _time
        from sqlalchemy import text as _text
        if not reason or not reason.strip():
            raise ValueError("a re-arm needs a reason")
        async with self._sf() as s:
            row = (await s.execute(_text(
                "SELECT kill_switch_tripped FROM strat_strategy_state WHERE strategy_id=:s"),
                {"s": sid})).first()
            if not row:
                return False
            await s.execute(_text(
                "UPDATE strat_strategy_state SET kill_switch_tripped=0, effective_mode='paper', "
                "effective_reason='paper (manually re-armed)', "
                "waiting_for_sentence='Re-armed — waiting for the next setup' "
                "WHERE strategy_id=:s"), {"s": sid})
            await s.execute(_text(
                "INSERT INTO strat_changelog (ts, strategy_id, diff, reason, wallet) "
                "VALUES (:ts,:s,:d,:r,'manual-rearm')"),
                {"ts": int(_time.time() * 1000), "s": sid,
                 "d": _json.dumps({"rearmed": True, "was_tripped": bool(row[0]), "reason": reason}),
                 "r": f"manual re-arm: {reason}"})
            await s.commit()
        return True
