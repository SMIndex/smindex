"""MarketEvent union (doc 00 §3 data → engine input).

Frozen dataclasses, stdlib only (doc 00: minimise deps; numpy only where needed
for features). Every event carries the raw, real feed value — nothing is
fabricated. `ts` is UTC epoch milliseconds unless noted.

The engine (engine.py) dispatches each event to the registered strategies'
`on_candle` / `on_tick` / `on_fill` handlers. In Phase 1 no strategy is
registered, so events flow through and produce zero OrderIntents.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional, Union


@dataclass(frozen=True, slots=True)
class CandleClosed:
    """A closed OHLCV candle (doc 00 §3: HL `candle` / `candleSnapshot`)."""
    venue: str            # 'hl'
    coin: str             # 'BTC' | 'ETH'
    tf: str               # '15m' | '1h' | '4h'
    ts: int               # candle CLOSE time, epoch ms
    o: float
    h: float
    l: float
    c: float
    v: float


@dataclass(frozen=True, slots=True)
class Trade:
    """A trade print from the HL `trades` ws. `side` is the taker side."""
    coin: str
    ts: int
    px: float
    sz: float
    side: str             # 'buy' (taker bought) | 'sell' (taker sold)


@dataclass(frozen=True, slots=True)
class BookSnapshot:
    """L2 depth snapshot (doc 00 §3: `l2Book`), depth within 0.1/0.3/0.5% of mid.

    Depth values are notional (USD) resting within each band on each side.
    """
    coin: str
    ts: int
    mid: float
    bid_0_1: float
    bid_0_3: float
    bid_0_5: float
    ask_0_1: float
    ask_0_3: float
    ask_0_5: float


@dataclass(frozen=True, slots=True)
class AssetCtx:
    """Live funding/OI/mark context (doc 00 §3: `activeAssetCtx` / `metaAndAssetCtxs`)."""
    coin: str
    ts: int
    oi_notional: Optional[float]
    funding: Optional[float]
    predicted_funding: Optional[float]
    mark: Optional[float]
    oracle: Optional[float]
    premium: Optional[float]


@dataclass(frozen=True, slots=True)
class Liquidation:
    """A liquidation-flagged fill (doc 00 §3).

    coverage is 'partial' in Phase 1 — populated ONLY from liquidation-flagged
    fills on wallets already on the existing ≤10-slot ws tier (the docs'
    2,000-wallet userFills subscription violates the venue's 10-unique-users-
    per-IP limit verified in production). Every consumer must honour this label.
    """
    coin: str
    ts: int
    side: str                       # side of the liquidated position
    px: float
    sz: float
    notional: float
    liquidated_user: Optional[str]
    mark_px: Optional[float]
    method: Optional[str]           # 'market' | 'backstop'
    coverage: str = "partial"


@dataclass(frozen=True, slots=True)
class CohortEvent:
    """Smart-money cohort positioning change (doc 00 §4 bias, weight 0.35).

    Sourced from the EXISTING analytics tables (flow / asset rollups); no new
    cohort feed is built in Phase 1.
    """
    coin: str
    ts: int
    kind: str                       # e.g. 'net_positioning_change_24h'
    net_notional_delta: Optional[float]


@dataclass(frozen=True, slots=True)
class FundingGaugeChange:
    """Funding-gauge level change (doc 02 §13 prompt 1) — gauge only, no trade."""
    coin: str
    ts: int
    level: str                      # crowding level label
    tilt: float                     # -1..+1
    funding_z: Optional[float]
    blocked_direction: Optional[str]  # 'long' | 'short' | None


@dataclass(frozen=True, slots=True)
class Fill:
    """An order fill (doc 00 §6). No orders are placed in Phase 1, so no real
    Fill is produced yet — the type exists so the contract is complete."""
    strategy_id: str
    coin: str
    ts: int
    side: str
    px: float
    sz: float
    oid: str
    venue: str = "hl"
    is_liquidation: bool = False
    meta: dict = field(default_factory=dict)


MarketEvent = Union[
    CandleClosed,
    Trade,
    BookSnapshot,
    AssetCtx,
    Liquidation,
    CohortEvent,
    FundingGaugeChange,
    Fill,
]
