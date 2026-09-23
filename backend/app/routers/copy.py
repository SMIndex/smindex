"""Copy subscriptions + dashboard read paths (copy v1).

PAPER-ONLY. Every subscription is forced to copy_type/mode='paper' and
live_enabled=False. A request for copy_type='live' returns 400. No route here
places or touches a real order; uses only the v1 tables. Every create/update/
pause/resume/stop writes an audit_logs row.
"""
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select, desc

from app.config import settings
from app.db.database import get_session_factory
from app.db.models import User
from app.db.copy_models import CopyOrder
from app.routers.auth import get_authenticated_user, get_optional_user as _copy_optional_user
from app.services.copy import subscriptions as sub_service
from app.services.copy import audit
from app.services.copy import positions as pos_service
from app.services.copy import live_orders
from app.services.copy import live_positions
from app.services import market_registry
from app.utils.logger import get_logger

logger = get_logger(__name__)

router = APIRouter(prefix="/copy", tags=["copy-v1"])

MAX_SLIPPAGE_BPS = 500  # 5% = 500 bps


# --------------------------- schemas ---------------------------

class CreateSubscriptionRequest(BaseModel):
    trader_wallet: str
    exchange: str = "perpl"                      # signal source: perpl | hl (execution stays Perpl)
    copy_type: Optional[str] = None              # None -> settings.COPY_MODE (live_manual)
    sizing_mode: str = "fixed"                   # fixed | proportional
    allocation_usd: float = Field(10.0, gt=0)
    max_leverage: float = Field(5.0, ge=1, le=20)
    max_margin_per_trade: Optional[float] = Field(default=None, gt=0)
    max_daily_loss: Optional[float] = Field(default=None, gt=0)
    max_total_loss: Optional[float] = Field(default=None, gt=0)
    slippage_bps: Optional[int] = Field(default=None, ge=0, le=MAX_SLIPPAGE_BPS)
    allowed_markets: Optional[list[int]] = None
    copy_new_only: bool = True
    sl_pct: Optional[float] = Field(default=None, gt=0)
    tp_pct: Optional[float] = Field(default=None, gt=0)
    max_basis_bps: Optional[int] = Field(default=None, ge=0, le=10_000)


class UpdateSubscriptionRequest(BaseModel):
    sizing_mode: Optional[str] = None
    allocation_usd: Optional[float] = Field(default=None, gt=0)
    max_leverage: Optional[float] = Field(default=None, ge=1, le=20)
    max_margin_per_trade: Optional[float] = Field(default=None, gt=0)
    max_daily_loss: Optional[float] = Field(default=None, gt=0)
    max_total_loss: Optional[float] = Field(default=None, gt=0)
    slippage_bps: Optional[int] = Field(default=None, ge=0, le=MAX_SLIPPAGE_BPS)
    allowed_markets: Optional[list[int]] = None
    copy_new_only: Optional[bool] = None
    sl_pct: Optional[float] = Field(default=None, gt=0)
    tp_pct: Optional[float] = Field(default=None, gt=0)


async def _validate_markets(markets: Optional[list[int]]) -> None:
    """Validate allowed_markets against the LIVE Perpl registry (active markets).
    Rejects unknown/inactive ids (e.g. a market Perpl delisted)."""
    if markets:
        _, invalid = await market_registry.validate_market_ids(markets)
        if invalid:
            raise HTTPException(
                status_code=400,
                detail=f"Inactive or unknown market ids: {invalid}",
            )


VALID_MODES = {"paper", "live_manual", "live_auto"}


def _resolve_mode(copy_type: Optional[str]) -> str:
    """Normalize the requested copy_type into a stored mode.

    None -> settings.COPY_MODE (live_manual). 'live' is a legacy alias for
    'live_manual'. 'live_auto' is refused (auto execution not implemented).
    Configuring a live_manual subscription (saved risk settings) is allowed
    regardless of COPY_LIVE_ENABLED — that flag gates ORDER PLACEMENT, not config.
    """
    ct = (copy_type or settings.COPY_MODE or "live_manual").lower()
    if ct == "live":
        ct = "live_manual"
    if ct == "live_auto":
        raise HTTPException(status_code=400, detail="Auto copy (live_auto) is not implemented yet")
    if ct not in ("paper", "live_manual"):
        raise HTTPException(status_code=400, detail=f"Invalid copy_type: {copy_type}")
    return ct


# --------------------------- subscriptions ---------------------------

@router.get("/subscriptions")
async def list_subscriptions(user: User = Depends(get_authenticated_user)) -> list[dict]:
    return await sub_service.list_subscriptions(user.wallet_address)


@router.post("/subscriptions")
async def create_subscription(
    req: CreateSubscriptionRequest,
    user: User = Depends(get_authenticated_user),
) -> dict:
    mode = _resolve_mode(req.copy_type)
    await _validate_markets(req.allowed_markets)
    if req.trader_wallet.lower() == user.wallet_address.lower():
        raise HTTPException(status_code=400, detail="Cannot copy yourself")

    try:
        if req.exchange not in ("perpl", "hl"):
            raise HTTPException(status_code=400, detail=f"Unknown exchange: {req.exchange}")
        sub = await sub_service.create_subscription(
            follower_wallet=user.wallet_address,
            trader_wallet=req.trader_wallet,
            exchange=req.exchange,
            mode=mode,
            sizing_mode=req.sizing_mode,
            allocation_usd=req.allocation_usd,
            max_leverage=req.max_leverage,
            max_margin_per_trade=req.max_margin_per_trade,
            max_daily_loss=req.max_daily_loss,
            max_total_loss=req.max_total_loss,
            slippage_bps=req.slippage_bps,
            allowed_markets=req.allowed_markets,
            copy_new_only=req.copy_new_only,
            sl_pct=req.sl_pct,
            tp_pct=req.tp_pct,
            max_basis_bps=req.max_basis_bps,
        )
    except sub_service.SubscriptionExists:
        raise HTTPException(status_code=409, detail="Already have a subscription for this trader")

    await audit.write_audit(
        user.wallet_address, "subscribe", entity_type="copy_subscription",
        entity_id=sub["id"],
        detail={"trader_wallet": req.trader_wallet.lower(), "copy_type": mode, "exchange": req.exchange},
    )
    return sub


@router.patch("/subscriptions/{sub_id}")
async def update_subscription(
    sub_id: int,
    req: UpdateSubscriptionRequest,
    user: User = Depends(get_authenticated_user),
) -> dict:
    fields = req.model_dump(exclude_unset=True)
    if "allowed_markets" in fields:
        await _validate_markets(fields["allowed_markets"])
    sub = await sub_service.update_subscription(sub_id, user.wallet_address, fields)
    if not sub:
        raise HTTPException(status_code=404, detail="Subscription not found")
    await audit.write_audit(
        user.wallet_address, "update_subscription", entity_type="copy_subscription",
        entity_id=sub_id, detail={"fields": list(fields.keys())},
    )
    return sub


async def _set_status(sub_id: int, user: User, status: str, action: str) -> dict:
    sub = await sub_service.set_status(sub_id, user.wallet_address, status)
    if not sub:
        raise HTTPException(status_code=404, detail="Subscription not found")
    await audit.write_audit(
        user.wallet_address, action, entity_type="copy_subscription", entity_id=sub_id,
    )
    return sub


@router.post("/subscriptions/{sub_id}/pause")
async def pause_subscription(sub_id: int, user: User = Depends(get_authenticated_user)) -> dict:
    return await _set_status(sub_id, user, "paused", "pause")


@router.post("/subscriptions/{sub_id}/resume")
async def resume_subscription(sub_id: int, user: User = Depends(get_authenticated_user)) -> dict:
    return await _set_status(sub_id, user, "active", "resume")


@router.post("/subscriptions/{sub_id}/stop")
async def stop_subscription(sub_id: int, user: User = Depends(get_authenticated_user)) -> dict:
    return await _set_status(sub_id, user, "stopped", "stop")


# --------------------------- dashboard reads ---------------------------

def _f(x):
    return float(x) if x is not None else None


def _order_dict(o: CopyOrder) -> dict:
    return {
        "id": o.id,
        "subscription_id": o.subscription_id,
        "follower_wallet": o.follower_wallet,
        "trader_wallet": o.trader_wallet,
        "leader_event_id": o.leader_event_id,
        "mode": o.mode,
        "market_id": o.market_id,
        "symbol": o.symbol,
        "side": o.side,
        "intended_size": _f(o.intended_size),
        "intended_price": _f(o.intended_price),
        "leverage": _f(o.leverage),
        "allocation_usd": _f(o.allocation_usd),
        "status": o.status,
        "skip_reason": o.skip_reason,
        "perpl_request_id": o.perpl_request_id,
        "perpl_order_id": o.perpl_order_id,
        "perpl_fill_id": o.perpl_fill_id,
        "fill_price": _f(o.fill_price),
        "fill_size": _f(o.fill_size),
        "submitted_at": o.submitted_at.isoformat() if o.submitted_at else None,
        "filled_at": o.filled_at.isoformat() if o.filled_at else None,
        "error_message": o.error_message,
        "created_at": o.created_at.isoformat() if o.created_at else None,
    }


@router.get("/orders")
async def list_copy_orders(
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
    subscription_id: Optional[int] = Query(None),
    trader_wallet: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
    mode: Optional[str] = Query(None, description="paper | live_manual | live_auto; or 'live' for any live"),
    user: User = Depends(get_authenticated_user),
) -> list[dict]:
    """Copy order log (copy_orders), newest first. Live attempts + paper sims."""
    sf = get_session_factory()
    async with sf() as session:
        stmt = select(CopyOrder).where(CopyOrder.follower_wallet == user.wallet_address.lower())
        if subscription_id is not None:
            stmt = stmt.where(CopyOrder.subscription_id == subscription_id)
        if trader_wallet:
            stmt = stmt.where(CopyOrder.trader_wallet == trader_wallet.lower())
        if status:
            stmt = stmt.where(CopyOrder.status == status)
        if mode == "live":
            stmt = stmt.where(CopyOrder.mode.in_(["live_manual", "live_auto"]))
        elif mode:
            stmt = stmt.where(CopyOrder.mode == mode)
        stmt = stmt.order_by(desc(CopyOrder.created_at)).offset(skip).limit(limit)
        rows = (await session.execute(stmt)).scalars().all()
    return [_order_dict(o) for o in rows]


# --------------------------- live_manual copy orders ---------------------------

class CreateCopyOrderRequest(BaseModel):
    """User-confirmed live copy attempt. The client places the REAL order itself;
    this records the attempt + runs the risk/live gate."""
    trader_wallet: str
    market_id: int
    symbol: str
    side: str                                    # long | short
    intended_size: float = Field(gt=0)
    intended_price: Optional[float] = Field(default=None, gt=0)
    leverage: float = Field(ge=1, le=20)
    allocation_usd: float = Field(gt=0)
    subscription_id: Optional[int] = None
    leader_event_id: Optional[int] = None
    idempotency_key: str                         # client-generated, unique per confirmation
    action: str = "open"                         # open | close | reduce
    live_position_id: Optional[int] = None       # required for close/reduce
    order_type: str = "market"                   # market | limit
    tp_price: Optional[float] = Field(default=None, gt=0)   # optional TP trigger
    sl_price: Optional[float] = Field(default=None, gt=0)   # optional SL trigger
    # audit A7: ad-hoc basis cap. Omitted -> server default (30 bps);
    # explicit 0 -> user cleared the guard; >0 -> user's own cap.
    basis_cap_bps: Optional[int] = Field(default=None, ge=0, le=1000)


class UpdateCopyOrderRequest(BaseModel):
    status: str                                  # filled | placed | failed
    perpl_request_id: Optional[str] = None
    perpl_order_id: Optional[str] = None
    perpl_fill_id: Optional[str] = None
    fill_price: Optional[float] = None
    fill_size: Optional[float] = None
    error_message: Optional[str] = None
    tp_order_id: Optional[str] = None            # real Perpl trigger order id
    sl_order_id: Optional[str] = None
    # audit A1: TP/SL placement failed after the fill (post-retry) — position
    # is unprotected; marker propagates to the live-position row + telegram.
    triggers_failed: Optional[bool] = None


@router.post("/orders")
async def create_copy_order(
    req: CreateCopyOrderRequest,
    user: User = Depends(get_authenticated_user),
) -> dict:
    """Record a user-confirmed live copy attempt (live_manual) + risk/live gate.

    Returns the copy_orders row. The client must ONLY place the real Perpl order
    when status == 'submitted'. status 'risk_blocked' (incl. skip_reason
    'live_disabled') means do NOT place. Always writes copy_orders; blocks write a
    risk_event; this confirmation writes an audit_logs row.
    """
    # NOTE: do NOT hard-reject an inactive market here — the risk engine records a
    # tracked 'market_inactive' block (copy_orders + risk_event), not a 400.
    if req.action not in ("open", "close", "reduce"):
        raise HTTPException(status_code=400, detail="action must be open|close|reduce")
    if req.action == "open" and req.trader_wallet.lower() == user.wallet_address.lower():
        raise HTTPException(status_code=400, detail="Cannot copy yourself")
    if req.side not in ("long", "short"):
        raise HTTPException(status_code=400, detail="side must be long|short")
    if req.action in ("close", "reduce") and not req.live_position_id:
        raise HTTPException(status_code=400, detail="live_position_id required for close/reduce")
    if req.order_type not in ("market", "limit"):
        raise HTTPException(status_code=400, detail="order_type must be market|limit")
    if req.order_type == "limit" and req.action == "open" and not req.intended_price:
        raise HTTPException(status_code=400, detail="limit orders require intended_price")

    order = await live_orders.create_attempt(
        follower_wallet=user.wallet_address,
        trader_wallet=req.trader_wallet,
        market_id=req.market_id, symbol=req.symbol, side=req.side,
        intended_size=req.intended_size, intended_price=req.intended_price,
        leverage=req.leverage, allocation_usd=req.allocation_usd,
        subscription_id=req.subscription_id, leader_event_id=req.leader_event_id,
        idempotency_key=req.idempotency_key, mode="live_manual",
        action=req.action, live_position_id=req.live_position_id,
        order_type=req.order_type, tp_price=req.tp_price, sl_price=req.sl_price,
        basis_cap_bps=req.basis_cap_bps,
    )
    # Blocked attempts already write a 'live_copy_blocked' audit inside live_orders;
    # only record the positive user confirmation here when actually authorized.
    if order["status"] == "submitted":
        act = "confirm_live_copy" if req.action == "open" else "live_close_confirmed"
        await audit.write_audit(
            user.wallet_address, act, entity_type="copy_order",
            entity_id=order["id"],
            detail={"trader_wallet": req.trader_wallet.lower(), "market_id": req.market_id,
                    "side": req.side, "action": req.action, "status": order["status"]},
        )
    return order


@router.patch("/orders/{order_id}")
async def update_copy_order(
    order_id: int,
    req: UpdateCopyOrderRequest,
    user: User = Depends(get_authenticated_user),
) -> dict:
    """Record the REAL placement result (filled | placed | failed) from the client
    after it placed the order via the existing Perpl trading path. 'placed' = a
    resting limit order (real order id, no fill yet) — no position is created."""
    if req.status not in ("filled", "placed", "failed"):
        raise HTTPException(status_code=400, detail="status must be filled|placed|failed")
    # A 'placed' (resting) claim needs a real Perpl order id — same honesty rule
    # as fills: nothing unconfirmed gets an authoritative-sounding status.
    if req.status == "placed" and not req.perpl_order_id:
        req.status = "failed"
        req.error_message = req.error_message or "placed rejected: missing Perpl order id"

    order = await live_orders.update_result(
        order_id, user.wallet_address, status=req.status,
        perpl_request_id=req.perpl_request_id, perpl_order_id=req.perpl_order_id,
        perpl_fill_id=req.perpl_fill_id, fill_price=req.fill_price,
        fill_size=req.fill_size, error_message=req.error_message,
        tp_order_id=req.tp_order_id, sl_order_id=req.sl_order_id,
    )
    if not order:
        raise HTTPException(status_code=404, detail="Copy order not found")

    # HARD RULE: a live position may only be OPENED from a real Perpl confirmation
    # (non-null perpl_order_id or perpl_fill_id). A filled OPEN lacking both is a phantom
    # fill — downgrade it to failed and create NO live position. (Scoped to opens: closes
    # update an already-verified existing position, they don't create a new live row.)
    action = order.get("action", "open")
    has_perpl_confirmation = bool(req.perpl_order_id) or bool(req.perpl_fill_id)
    if req.status == "filled":
        if action == "open":
            if not has_perpl_confirmation:
                order = await live_orders.update_result(
                    order_id, user.wallet_address, status="failed",
                    perpl_request_id=req.perpl_request_id,
                    error_message="filled rejected: missing Perpl confirmation",
                )
                await audit.write_audit(
                    user.wallet_address, "live_copy_filled_rejected", entity_type="copy_order",
                    entity_id=order_id,
                    detail={"reason": "missing Perpl confirmation", "action": "open"},
                )
                return order
            pos = await live_positions.open_from_order(order, triggers_failed=bool(req.triggers_failed))
            await audit.write_audit(user.wallet_address, "live_position_opened",
                                    entity_type="copy_live_position", entity_id=pos["id"],
                                    detail={"copy_order_id": order_id, "perpl_order_id": req.perpl_order_id,
                                            "triggers_failed": bool(req.triggers_failed)})
            if req.triggers_failed:
                # audit A1: NEVER silent — telegram the follower that the
                # position has no armed stop (best-effort, non-blocking).
                try:
                    from app.services.telegram_bot import notify_wallet
                    await notify_wallet(user.wallet_address,
                        f"⚠️ <b>TP/SL placement failed</b> — your copied "
                        f"{order.get('side','').upper()} {order.get('symbol')} position "
                        f"(#{pos['id']}) is <b>unprotected</b>. Set a stop from the terminal.")
                except Exception:
                    logger.exception("triggers_failed telegram notify failed")
            # Audit F5 — AUTOMATIC intended-vs-executed reconciliation (was only
            # readable by a human in the table). Detection-only, never blocks:
            # a mismatch writes a risk_event + log for admin review. Tolerances:
            # size may be SMALLER (partial fill) but not >0.1% larger than
            # intended; fill price within 2% of intended (2x the venue's 1%
            # market-slippage cap, so honest fills never alarm).
            try:
                mism = []
                isz, fsz = order.get("intended_size"), order.get("fill_size")
                ipx, fpx = order.get("intended_price"), order.get("fill_price")
                if isz and fsz and float(fsz) > float(isz) * 1.001:
                    mism.append(f"fill_size {fsz} > intended {isz}")
                if ipx and fpx and abs(float(fpx) - float(ipx)) / float(ipx) > 0.02:
                    mism.append(f"fill_price {fpx} vs intended {ipx} (>2%)")
                if mism:
                    logger.warning("EXECUTION MISMATCH copy_order %s (%s): %s",
                                   order_id, user.wallet_address[:10], "; ".join(mism))
                    await audit.write_risk_event(
                        user.wallet_address.lower(), "execution_mismatch",
                        subscription_id=order.get("subscription_id"),
                        detail={"severity": "review", "copy_order_id": order_id,
                                "message": "; ".join(mism)},
                    )
            except Exception:
                logger.exception("reconciliation check failed (non-fatal)")
        elif action in ("close", "reduce") and order.get("live_position_id"):
            # HARD RULE (same as opens): a live position is only mutated on a real
            # Perpl confirmation. A filled close/reduce lacking a real order/fill id
            # is unverified — mark it failed and leave the position untouched, so a
            # client cannot fabricate a close (and an arbitrary realized PnL).
            if not has_perpl_confirmation:
                order = await live_orders.update_result(
                    order_id, user.wallet_address, status="failed",
                    perpl_request_id=req.perpl_request_id,
                    error_message="close rejected: missing Perpl confirmation",
                )
                await audit.write_audit(
                    user.wallet_address, "live_close_rejected", entity_type="copy_order",
                    entity_id=order_id,
                    detail={"reason": "missing Perpl confirmation", "action": action},
                )
                return order
            qty = req.fill_size if req.fill_size is not None else order.get("intended_size")
            pos = await live_positions.apply_close_result(
                order["live_position_id"], user.wallet_address, action=action,
                close_price=req.fill_price, reduce_size=qty, reason="manual",
            )
            if pos:
                ev = "live_position_closed" if pos["status"] == "closed" else "live_position_reduced"
                await audit.write_audit(user.wallet_address, ev,
                                        entity_type="copy_live_position", entity_id=pos["id"],
                                        detail={"copy_order_id": order_id, "status": pos["status"]})
    elif req.status == "placed":
        # Resting limit order confirmed in the book — position comes later on fill.
        await audit.write_audit(user.wallet_address, "live_order_placed",
                                entity_type="copy_order", entity_id=order_id,
                                detail={"action": action, "perpl_order_id": req.perpl_order_id,
                                        "partial_fill_size": req.fill_size})
    else:  # failed
        ev = "live_close_failed" if action in ("close", "reduce") else "live_copy_failed"
        await audit.write_audit(user.wallet_address, ev, entity_type="copy_order",
                                entity_id=order_id,
                                detail={"action": action, "error": req.error_message})
    return order


@router.get("/live-status")
async def live_copy_status(user=Depends(_copy_optional_user)) -> dict:
    """Whether live copy EXECUTION is available for THIS user right now:
    app auth AND global kill switch AND staged-rollout allowlist. OPTIONAL
    auth — an anonymous browser gets the enabled flag plus authenticated=false
    (the 2026-08-14 hard-required JWT here made every logged-out browser show
    'Live Copy Unavailable' with no reason — nginx log: all 422)."""
    from app.config import settings
    enabled = settings.is_live_copy_enabled()
    authed = user is not None
    allowlisted = bool(authed and settings.live_allowlist_ok(user.wallet_address))
    return {
        "authenticated": authed,
        "live_enabled": enabled,
        "allowlisted": allowlisted,
        "live_available": enabled and allowlisted,
    }


@router.get("/execution-context")
async def execution_context(
    market_id: int = Query(..., ge=1),
    user=Depends(_copy_optional_user),
) -> dict:
    """Real execution-side numbers for the live copy modal, all traced:
    - mark_price / funding_rate: Perpl market state (3s poll cache)
    - hl_mark: leader-venue (Hyperliquid) live mid from the existing allMids ws
    - basis_bps: Perpl mid vs Hyperliquid mid (same source as the basis gate)
    - max_leverage: round(initial_margin/100) from the live market registry
    - available_balance: the caller's on-chain Perpl free balance (auth only)
    OPTIONAL auth: market fields are public; balance needs a JWT. Every field
    is null when its feed is unavailable — the client fails closed."""
    import asyncio as _asyncio
    from app.services import market_registry as _mr
    from app.services.hyperliquid import prices as _hl_px
    from app.services import ws_manager as _wsm_mod

    _wm = getattr(_wsm_mod, "ws_manager", None)
    state = (_wm.market_state_cache.get(market_id) or {}) if _wm else {}
    mark = state.get("mark_price") or None
    funding = state.get("funding_rate")

    hl_mark = None
    try:
        coin = await _hl_px._coin_for_market(market_id)
        if coin:
            hl_mark = _hl_px.get_mid(coin)
    except Exception:
        hl_mark = None
    try:
        basis = await _hl_px.basis_bps_for_market(market_id)
    except Exception:
        basis = None

    max_lev = await live_orders.market_max_leverage(market_id)

    balance = None
    account_id = 0
    if user is not None:
        try:
            from app.services.chain_reader import _get_w3_contract
            from web3 import Web3

            def _read_balance():
                w3, contract = _get_w3_contract()
                acct = contract.functions.getAccountByAddr(
                    Web3.to_checksum_address(user.wallet_address)).call()
                return acct[0], (acct[1] / 1e6)
            # Hard 4s cap: Monad RPC 429 retry storms must not stall the modal —
            # a missing balance renders "—" and blocks submit (fail closed).
            account_id, balance = await _asyncio.wait_for(
                _asyncio.get_event_loop().run_in_executor(None, _read_balance), timeout=4.0)
            if account_id == 0:
                balance = None
        except Exception:
            logger.warning("execution-context balance read failed for %s", user.wallet_address[:10])

    # Guarded like every other feed in this endpoint: a dead Perpl upstream
    # must yield nulls, not a 500 (audit F2 — proven in dev: this exact call
    # raised httpx.ConnectError through the handler while every other field
    # was individually guarded).
    try:
        m = await _mr.get_market_by_id(market_id)
    except Exception:
        logger.warning("execution-context market lookup failed for %s", market_id)
        m = None
    # audit A8: venue minimum posting size (base units) so the client can block
    # dust before signing. Live value is currently 0 on every market (check is
    # dormant until the venue sets one) — null when 0/absent.
    min_size = None
    try:
        cfg = ((m or {}).get("raw") or {}).get("config", {})
        mpa = cfg.get("min_posting_amount")
        szd = cfg.get("size_decimals")
        if mpa and szd is not None:
            min_size = mpa / (10 ** szd)
    except Exception:
        min_size = None

    return {
        "market_id": market_id,
        "symbol": (m or {}).get("symbol"),
        "market_active": bool((m or {}).get("is_active")),
        "min_size": min_size,
        "authenticated": user is not None,
        "mark_price": mark,
        "hl_mark": hl_mark,
        "funding_rate": funding if funding is not None else None,
        "basis_bps": round(basis, 2) if basis is not None else None,
        "max_leverage": max_lev,
        "account_id": account_id,
        "available_balance": round(balance, 2) if balance is not None else None,
        "ts": datetime.utcnow().isoformat() + "Z",
    }


@router.get("/live-positions")
async def list_live_positions(
    status: str = Query("all", description="all | open | partially_closed | closed | failed"),
    user: User = Depends(get_authenticated_user),
) -> list[dict]:
    """Real (live_manual) copied positions for the authenticated follower."""
    return await live_positions.list_positions(user.wallet_address, status=status)


@router.get("/positions")
async def list_copy_positions(
    status: str = Query("all"),
    subscription_id: Optional[int] = Query(None),
    trader_wallet: Optional[str] = Query(None),
    user: User = Depends(get_authenticated_user),
) -> list[dict]:
    """Simulated (paper) copy positions from the v1 copy_paper_positions table.
    Unrealized PnL is computed from the live mark when available. Does NOT read the
    legacy copy_positions table (v1 uses new tables only)."""
    return await pos_service.list_positions(
        user.wallet_address, status=status,
        subscription_id=subscription_id, trader_wallet=trader_wallet,
    )


@router.get("/risk-events")
async def list_risk_events(
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
    user: User = Depends(get_authenticated_user),
) -> list[dict]:
    return await audit.list_risk_events(user.wallet_address, skip, limit)


@router.get("/audit-logs")
async def list_audit_logs(
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
    user: User = Depends(get_authenticated_user),
) -> list[dict]:
    return await audit.list_audit_logs(user.wallet_address, skip, limit)
