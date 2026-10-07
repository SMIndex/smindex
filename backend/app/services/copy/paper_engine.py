"""Paper copy engine for copy v1 (simulation only — NEVER places real orders).

Flow per leader event:
  1. find active paper subscriptions for that trader
  2. for each: evaluate risk -> write ONE copy_orders row (idempotent on
     idempotency_key) -> if blocked write a risk_events row -> if allowed mutate
     the simulated copy_paper_positions.

Idempotency: copy_orders.idempotency_key = "evt:<event_id>:sub:<sub_id>" means the
same (leader_event, subscription) can never create a duplicate order or mutate a
position twice — re-running process_leader_event is safe.
"""
from sqlalchemy import select

from app.db.database import get_session_factory
from app.db.copy_models import CopyOrder, CopySubscription
from app.services.copy import risk, positions, audit, leader_events
from app.services.copy.subscriptions import _dict as _sub_dict
from app.utils.logger import get_logger

logger = get_logger(__name__)


def _idem_key(event_id: int, subscription_id: int) -> str:
    return f"evt:{event_id}:sub:{subscription_id}"


async def process_leader_event(leader_event_id: int, market_state: dict | None = None) -> dict:
    """Evaluate every active paper subscription for the event's trader."""
    evt = await leader_events.get_event(leader_event_id)
    if not evt:
        return {"error": "event_not_found", "leader_event_id": leader_event_id}

    sf = get_session_factory()
    async with sf() as session:
        subs = (await session.execute(
            select(CopySubscription).where(
                CopySubscription.trader_wallet == evt["trader_wallet"],
                CopySubscription.mode == "paper",
                CopySubscription.status == "active",
            )
        )).scalars().all()
        sub_dicts = [_sub_dict(s) for s in subs]

    results = []
    for sub in sub_dicts:
        try:
            results.append(await process_event_for_subscription(evt, sub, market_state))
        except Exception:
            logger.exception("paper_engine: subscription %s failed", sub.get("id"))
    return {"leader_event_id": leader_event_id, "event_type": evt["event_type"],
            "subscriptions": len(sub_dicts), "results": results}


async def process_event_for_subscription(leader_event: dict, subscription: dict,
                                         market_state: dict | None = None) -> dict:
    idem = _idem_key(leader_event["id"], subscription["id"])

    # Idempotency guard: a copy_order for this (event, sub) already exists.
    sf = get_session_factory()
    async with sf() as session:
        existing = (await session.execute(
            select(CopyOrder).where(CopyOrder.idempotency_key == idem)
        )).scalar_one_or_none()
        if existing:
            return {"subscription_id": subscription["id"], "duplicate": True,
                    "copy_order_id": existing.id, "status": existing.status}

    decision = await risk.evaluate_paper_copy(subscription, leader_event, market_state)
    allowed = decision["allowed"]
    status = "simulated" if allowed else "skipped"
    ref_price = (market_state or {}).get("mark_price") or leader_event.get("price")

    # Write the copy_orders row (one decision = one row).
    sf = get_session_factory()
    async with sf() as session:
        order = CopyOrder(
            subscription_id=subscription["id"],
            follower_wallet=subscription["follower_wallet"],
            trader_wallet=leader_event["trader_wallet"],
            leader_event_id=leader_event["id"],
            mode="paper",
            market_id=leader_event["market_id"],
            symbol=leader_event["symbol"],
            side=leader_event["side"],
            intended_size=decision.get("computed_size"),
            intended_price=ref_price,
            status=status,
            skip_reason=None if allowed else decision["reason"],
            idempotency_key=idem,
        )
        session.add(order)
        try:
            await session.commit()
            await session.refresh(order)
            order_id = order.id
        except Exception:
            await session.rollback()
            dup = (await session.execute(
                select(CopyOrder).where(CopyOrder.idempotency_key == idem)
            )).scalar_one_or_none()
            return {"subscription_id": subscription["id"], "duplicate": True,
                    "copy_order_id": dup.id if dup else None,
                    "status": dup.status if dup else "unknown"}

    # Blocked / skipped -> record a risk_events row, no position change.
    if not allowed:
        await audit.write_risk_event(
            subscription["follower_wallet"], decision["reason"],
            subscription_id=subscription["id"],
            detail={"leader_event_id": leader_event["id"], "action": decision["action"],
                    "risk_snapshot": decision["risk_snapshot"]},
        )
        return {"subscription_id": subscription["id"], "status": "skipped",
                "reason": decision["reason"], "copy_order_id": order_id}

    # Allowed -> mutate the simulated position.
    action = decision["action"]
    market_id = leader_event["market_id"]
    pos_result = None

    if action == "open":
        pos_result = await positions.open_paper_position(
            subscription_id=subscription["id"],
            follower_wallet=subscription["follower_wallet"],
            trader_wallet=leader_event["trader_wallet"],
            leader_event_id=leader_event["id"],
            market_id=market_id, symbol=leader_event["symbol"], side=leader_event["side"],
            entry_price=ref_price, size=decision.get("computed_size"),
            leverage=decision.get("computed_leverage"), allocation_usd=decision.get("computed_margin"),
        )
    elif action == "increase":
        pos_result = await positions.increase_paper_position(
            subscription["id"], market_id, decision.get("computed_size") or 0, ref_price)
        if pos_result is None:  # nothing to increase -> treat as fresh open
            pos_result = await positions.open_paper_position(
                subscription_id=subscription["id"],
                follower_wallet=subscription["follower_wallet"],
                trader_wallet=leader_event["trader_wallet"],
                leader_event_id=leader_event["id"],
                market_id=market_id, symbol=leader_event["symbol"], side=leader_event["side"],
                entry_price=ref_price, size=decision.get("computed_size"),
                leverage=decision.get("computed_leverage"), allocation_usd=decision.get("computed_margin"),
            )
    elif action == "reduce":
        pos_result = await positions.reduce_paper_position(
            subscription["id"], market_id, decision.get("computed_size") or 0, ref_price)
    elif action == "close":
        reason = "liquidation" if leader_event["event_type"] == "liquidated" else "leader_close"
        pos_result = await positions.close_paper_position(
            subscription["id"], market_id, ref_price, reason)

    await audit.write_audit(
        subscription["follower_wallet"], f"paper_copy_{action}",
        entity_type="copy_paper_position",
        entity_id=(pos_result or {}).get("id"),
        detail={"leader_event_id": leader_event["id"], "copy_order_id": order_id},
    )
    return {"subscription_id": subscription["id"], "status": "simulated",
            "action": action, "copy_order_id": order_id, "position": pos_result}
