"""Paper execution model (doc 00 §6, §7) — pure, deterministic, no venue calls.

- Maker limits (entries, targets) fill ONLY when a trade prints THROUGH the limit
  (strictly beyond), never on touch — the optimistic-fill guard from doc 00 §6.
- Post-only: a limit that would cross the market is re-quoted 1 tick inside, max 3
  re-quotes, then the order is skipped (strategies 01–06). Model entries (M1–M6,
  spec v1.3 D-87) re-quote ONCE via `post_only_place` and then rest until the
  entry validity expires.
- Stops are the one taker order (trigger market): a long stop fires when price
  trades at/below the trigger; a short stop when at/above.
- Fees (doc 00 §7): maker 0.015% on entry/target fills, taker 0.045% on stops.
- Time stops enforced by the caller/engine via `is_expired`.

No I/O here; the executor emits Fill events and returns closed-trade records for
the caller to persist to strat_trades. Tested: fill-through vs touch, requote,
fees, time stop.
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Optional

MAKER_FEE = 0.00015      # 0.015%
TAKER_FEE = 0.00045      # 0.045%


def fee_for(kind: str, notional: float) -> float:
    """Entry/target = maker; stop = taker (doc 00 §7)."""
    rate = TAKER_FEE if kind == "stop" else MAKER_FEE
    return abs(notional) * rate


def limit_fills_through(order_side: str, limit_px: float, trade_px: float) -> bool:
    """A resting maker limit fills only when a trade prints THROUGH it (strict),
    never on touch. buy: trade below limit; sell: trade above limit."""
    if order_side == "buy":
        return trade_px < limit_px
    return trade_px > limit_px


def stop_triggered(position_side: str, trigger_px: float, trade_px: float) -> bool:
    """Trigger (taker) stop. long: fires at/below trigger; short: at/above."""
    if position_side == "long":
        return trade_px <= trigger_px
    return trade_px >= trigger_px


def would_cross(order_side: str, limit_px: float, market_px: float) -> bool:
    """Post-only would-cross: a buy at/above market, or a sell at/below market."""
    if order_side == "buy":
        return limit_px >= market_px
    return limit_px <= market_px


def requote_inside(order_side: str, market_px: float, tick: float) -> float:
    """Re-quote 1 tick inside the market (doc 00 §6)."""
    return market_px - tick if order_side == "buy" else market_px + tick


def post_only_place(order_side: str, limit_px: float, market_px: Optional[float], tick: float) -> tuple[float, bool]:
    """Spec v1.3 Part 1 (D-87), model entries: Hyperliquid Alo behaviour on the
    mid-only tape. A buy limit at/above the touch (market_px) or a sell at/below
    it is REJECTED and re-quoted ONCE one tick inside the touch; the returned
    price then rests and fills only when a later print goes through it
    (`limit_fills_through`). Returns (resting_px, rejected). No market price →
    the order rests as quoted (nothing to test against; never invent one)."""
    if market_px is None or not would_cross(order_side, limit_px, market_px):
        return float(limit_px), False
    return float(requote_inside(order_side, market_px, tick)), True


@dataclass(slots=True)
class RestingOrder:
    id: str
    strategy_id: str
    asset: str
    side: str                # 'buy' | 'sell'
    kind: str                # 'entry' | 'target' | 'stop'
    order_type: str          # 'post_only_limit' | 'trigger_market' | 'reduce_limit'
    px: float
    sz: float
    position_side: str       # 'long' | 'short' (the position this serves)
    reduce_only: bool = False
    placed_ms: int = 0
    requotes: int = 0
    status: str = "resting"  # resting | filled | cancelled | skipped
    venue: str = "hl"


@dataclass(slots=True)
class PaperFill:
    strategy_id: str
    asset: str
    kind: str
    side: str
    px: float
    sz: float
    fee: float
    ts: int
    venue: str = "hl"


@dataclass(slots=True)
class PaperExecutor:
    """Manages resting orders per (strategy, asset) driven by trade prints. Emits
    PaperFills. The caller feeds trades via on_trade and clock via on_clock (time
    stops), and persists closed trades. Zero venue calls (proven in the safety test)."""
    max_requotes: int = 3
    resting: list[RestingOrder] = field(default_factory=list)
    fills: list[PaperFill] = field(default_factory=list)

    def place(self, order: RestingOrder, market_px: float, tick: float) -> RestingOrder:
        """Place a maker limit with post-only requote handling; stops rest as-is."""
        if order.order_type == "post_only_limit":
            while would_cross(order.side, order.px, market_px):
                if order.requotes >= self.max_requotes:
                    order.status = "skipped"
                    return order
                order.px = requote_inside(order.side, market_px, tick)
                order.requotes += 1
        self.resting.append(order)
        return order

    def on_trade(self, asset: str, trade_px: float, ts: int) -> list[PaperFill]:
        """Process one trade print: fill maker limits that traded through, and
        fire trigger stops. Returns the fills produced."""
        produced: list[PaperFill] = []
        still: list[RestingOrder] = []
        for o in self.resting:
            if o.asset != asset or o.status != "resting":
                still.append(o)
                continue
            hit = False
            if o.order_type in ("post_only_limit", "reduce_limit"):
                hit = limit_fills_through(o.side, o.px, trade_px)
                fill_px = o.px
            elif o.order_type == "trigger_market":
                hit = stop_triggered(o.position_side, o.px, trade_px)
                fill_px = o.px       # modelled at the trigger
            if hit:
                o.status = "filled"
                notional = fill_px * o.sz
                f = PaperFill(o.strategy_id, o.asset, o.kind, o.side, fill_px, o.sz,
                              fee_for(o.kind, notional), ts, o.venue)
                produced.append(f)
                self.fills.append(f)
            else:
                still.append(o)
        self.resting = still
        return produced

    def cancel(self, order_id: str) -> None:
        for o in self.resting:
            if o.id == order_id and o.status == "resting":
                o.status = "cancelled"
        self.resting = [o for o in self.resting if o.status == "resting"]

    @staticmethod
    def is_expired(placed_ms: int, now_ms: int, max_hold_ms: int) -> bool:
        return (now_ms - placed_ms) >= max_hold_ms
