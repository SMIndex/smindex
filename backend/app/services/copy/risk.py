"""Risk evaluation for paper copy (copy v1).

evaluate_paper_copy(subscription, leader_event, market_state, follower_state)
returns a structured decision. Opens/increases are gated by all configured limits;
risk-reducing events (reduced/closed/liquidated) are always allowed so a follower
can always wind down, even when open-side limits would block.

These same gates will feed the future LIVE engine — but live execution stays
disabled (COPY_LIVE_ENABLED=false). This module places NO orders.
"""
from datetime import datetime

from sqlalchemy import select, func

from app.db.database import get_session_factory
from app.db.copy_models import CopyPaperPosition
from app.utils.logger import get_logger
from app.services.copy import positions as pos_service
from app.services import market_registry

logger = get_logger(__name__)

CLOSING_EVENTS = {"reduced", "closed", "liquidated"}
_ACTION = {
    "opened": "open", "increased": "increase",
    "reduced": "reduce", "closed": "close", "liquidated": "close",
}


def _decision(allowed, action, reason, *, margin=None, size=None, leverage=None, snapshot=None) -> dict:
    return {
        "allowed": allowed,
        "action": action,
        "reason": reason,
        "computed_margin": margin,
        "computed_size": size,
        "computed_leverage": leverage,
        "risk_snapshot": snapshot or {},
    }


async def _realized_total(subscription_id: int) -> float:
    sf = get_session_factory()
    async with sf() as s:
        v = (await s.execute(
            select(func.coalesce(func.sum(CopyPaperPosition.realized_pnl), 0)).where(
                CopyPaperPosition.subscription_id == subscription_id,
                CopyPaperPosition.status.in_(["closed", "liquidated"]),
            )
        )).scalar()
    return float(v or 0)


async def _realized_today(subscription_id: int) -> float:
    start = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
    sf = get_session_factory()
    async with sf() as s:
        v = (await s.execute(
            select(func.coalesce(func.sum(CopyPaperPosition.realized_pnl), 0)).where(
                CopyPaperPosition.subscription_id == subscription_id,
                CopyPaperPosition.status.in_(["closed", "liquidated"]),
                CopyPaperPosition.closed_at >= start,
            )
        )).scalar()
    return float(v or 0)


async def evaluate_paper_copy(subscription: dict, leader_event: dict,
                              market_state: dict | None = None,
                              follower_state: dict | None = None) -> dict:
    event_type = leader_event["event_type"]
    market_id = leader_event["market_id"]
    side = leader_event["side"]
    price = leader_event.get("price")
    action = _ACTION.get(event_type, "skip")
    snap: dict = {"event_type": event_type, "sizing_mode": subscription.get("sizing_mode")}

    # --- Risk-reducing events are ALWAYS allowed ---
    if event_type in CLOSING_EVENTS:
        open_pos = await pos_service.get_open_paper_position(subscription["id"], market_id)
        size = None
        if open_pos:
            # v1 heuristic: 'reduced' trims half; full close otherwise.
            # TODO(v1): mirror the leader's actual reduced fraction once leader size is tracked.
            size = float(open_pos["size"]) * (0.5 if event_type == "reduced" else 1.0)
        snap["has_open_position"] = bool(open_pos)
        return _decision(True, action, "risk_reducing", size=size, snapshot=snap)

    # --- Opening / increasing: enforce all limits ---
    if subscription.get("status") != "active":
        return _decision(False, "skip", "subscription_not_active", snapshot=snap)
    if subscription.get("mode", "paper") != "paper":
        return _decision(False, "skip", "not_paper_mode", snapshot=snap)

    # Market must still be listed/active on Perpl (delisted -> block, never open stale).
    if not await market_registry.is_market_active(market_id):
        snap["market_inactive"] = True
        return _decision(False, "skip", "market_inactive", snapshot=snap)

    allowed_markets = subscription.get("allowed_markets")
    if allowed_markets and market_id not in allowed_markets:
        snap["allowed_markets"] = allowed_markets
        return _decision(False, "skip", "market_not_allowed", snapshot=snap)

    # Leverage: intended comes from the leader event raw if present, else max.
    max_lev = float(subscription["max_leverage"])
    intended_lev = float((leader_event.get("raw") or {}).get("leverage") or max_lev)
    snap.update({"intended_leverage": intended_lev, "max_leverage": max_lev})
    if intended_lev > max_lev + 1e-9:
        return _decision(False, "skip", "max_leverage_exceeded", snapshot=snap)
    computed_leverage = min(intended_lev, max_lev)

    # Margin / max_margin_per_trade
    computed_margin = float(subscription["allocation_usd"])
    mmpt = subscription.get("max_margin_per_trade")
    snap["computed_margin"] = computed_margin
    if mmpt is not None and computed_margin > float(mmpt) + 1e-9:
        snap["max_margin_per_trade"] = float(mmpt)
        return _decision(False, "skip", "max_margin_exceeded", snapshot=snap)

    # Max daily loss
    mdl = subscription.get("max_daily_loss")
    if mdl is not None:
        daily = await _realized_today(subscription["id"])
        snap["daily_realized"] = daily
        if daily <= -float(mdl):
            return _decision(False, "skip", "max_daily_loss_reached", snapshot=snap)

    # Max total loss
    mtl = subscription.get("max_total_loss")
    if mtl is not None:
        total = await _realized_total(subscription["id"])
        snap["total_realized"] = total
        if total <= -float(mtl):
            return _decision(False, "skip", "max_total_loss_reached", snapshot=snap)

    # Slippage (only if we can estimate it)
    sbps = subscription.get("slippage_bps")
    mark = (market_state or {}).get("mark_price")
    if sbps is not None and mark and price:
        est = abs(float(mark) - float(price)) / float(price) * 10000
        snap["slippage_bps_est"] = est
        if est > float(sbps):
            return _decision(False, "skip", "slippage_exceeded", snapshot=snap)

    # Sizing (fixed). TODO(v1): proportional sizing needs leader equity/size context.
    computed_size = None
    ref_price = mark or price
    if ref_price and float(ref_price) > 0:
        computed_size = (computed_margin * computed_leverage) / float(ref_price)
    snap["ref_price"] = float(ref_price) if ref_price else None

    return _decision(True, action, "ok",
                     margin=computed_margin, size=computed_size,
                     leverage=computed_leverage, snapshot=snap)
