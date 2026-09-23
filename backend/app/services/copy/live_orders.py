"""Live (live_manual) copy-order tracking for Copy Trading.

A live copy order is the record of a REAL Perpl order attempt that the user
explicitly confirmed. The actual order is placed CLIENT-SIDE (wallet-signed via
perplTrading); this module only TRACKS the attempt + result in copy_orders, and
writes risk_events (on block) + audit_logs (on user action).

Safety:
  * Real order PLACEMENT is gated by settings.COPY_LIVE_ENABLED. When false, an
    attempt is recorded as status='risk_blocked' (skip_reason='live_disabled') and
    NO placement is authorized — the API tells the client not to place.
  * Every attempt writes a copy_orders row; every block writes a risk_event; the
    user confirmation + result writes audit_logs (handled by the router).
  * idempotency_key makes confirm/retry create exactly one order row.
"""
from datetime import datetime

from sqlalchemy import select

import asyncio

from app.config import settings
from app.db.database import get_session_factory
from app.db.copy_models import CopyOrder, CopySubscription
from app.services.copy import audit
from app.services import market_registry
from app.utils.logger import get_logger

logger = get_logger(__name__)

GLOBAL_MAX_LEVERAGE = 20.0
# Audit A7 (owner decision 2026-08-19): ad-hoc copies (no subscription) get a
# DEFAULT cross-venue basis cap. The modal shows it and the user may override
# or clear it (explicit 0 = cleared); subscription-scoped behavior unchanged.
DEFAULT_ADHOC_BASIS_BPS = 30


async def _perpl_eligible(wallet: str) -> bool:
    """Perpl trading-eligibility check: does this wallet have an on-chain Perpl
    account (the same signal the terminal/manual path uses)? Runs the blocking
    web3 call in a thread. FAIL-CLOSED — if we can't verify, do NOT authorize a
    real order."""
    try:
        from app.services.chain_reader import is_perpl_eligible
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, is_perpl_eligible, wallet)
    except Exception as e:
        logger.warning("perpl eligibility check failed for %s: %s", (wallet or "")[:10], e)
        return False


def _f(x):
    return float(x) if x is not None else None


def order_dict(o: CopyOrder) -> dict:
    return {
        "id": o.id,
        "subscription_id": o.subscription_id,
        "follower_wallet": o.follower_wallet,
        "trader_wallet": o.trader_wallet,
        "leader_event_id": o.leader_event_id,
        "mode": o.mode,
        "action": o.action,
        "live_position_id": o.live_position_id,
        "market_id": o.market_id,
        "symbol": o.symbol,
        "side": o.side,
        "order_type": getattr(o, "order_type", None),
        "tp_price": _f(getattr(o, "tp_price", None)),
        "sl_price": _f(getattr(o, "sl_price", None)),
        "tp_order_id": getattr(o, "tp_order_id", None),
        "sl_order_id": getattr(o, "sl_order_id", None),
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


async def market_max_leverage(market_id: int) -> float:
    """Per-market REAL max leverage from Perpl's `initial_margin` (hundredths;
    same formula as /api/market-configs: round(initial_margin/100)). Falls back
    to the global cap when the registry has no config for the market."""
    try:
        m = await market_registry.get_market_by_id(market_id)
        init = ((m or {}).get("raw") or {}).get("config", {}).get("initial_margin")
        if init and init > 0:
            return float(max(1, round(init / 100)))
    except Exception:
        logger.exception("market_max_leverage lookup failed for %s", market_id)
    return GLOBAL_MAX_LEVERAGE


async def _live_realized(subscription_id: int, today_only: bool) -> float:
    """Realized PnL over CLOSED live copied positions for a subscription
    (all-time or UTC-today) — feeds the daily/total loss gates."""
    from datetime import date
    from app.db.copy_models import CopyLivePosition
    sf = get_session_factory()
    async with sf() as s:
        stmt = select(CopyLivePosition.realized_pnl).where(
            CopyLivePosition.subscription_id == subscription_id,
            CopyLivePosition.realized_pnl.isnot(None),
        )
        if today_only:
            stmt = stmt.where(CopyLivePosition.closed_at >= datetime.combine(
                date.today(), datetime.min.time()))
        vals = (await s.execute(stmt)).scalars().all()
    return float(sum(float(v) for v in vals))


async def _eval_risk(follower_wallet: str, subscription_id, market_id, leverage,
                     allocation_usd) -> tuple[bool, str | None, dict]:
    """Lightweight risk gate for a manual copy. If a subscription is referenced,
    enforce its limits; always enforce global sanity. Returns (allowed, reason, snap)."""
    snap: dict = {"market_id": market_id, "leverage": leverage, "allocation_usd": allocation_usd}

    # Market must still be active on Perpl (delisted markets are blocked, tracked).
    if not await market_registry.is_market_active(market_id):
        return False, "market_inactive", snap
    # Leverage bound = the market's REAL Perpl max (dynamic), never a hardcode.
    mkt_max = await market_max_leverage(market_id)
    snap["market_max_leverage"] = mkt_max
    if not leverage or leverage <= 0 or leverage > mkt_max + 1e-9:
        return False, "leverage_out_of_range", snap
    if not allocation_usd or allocation_usd <= 0:
        return False, "invalid_allocation", snap

    if subscription_id is not None:
        sf = get_session_factory()
        async with sf() as s:
            sub = (await s.execute(
                select(CopySubscription).where(
                    CopySubscription.id == subscription_id,
                    CopySubscription.follower_wallet == follower_wallet.lower(),
                )
            )).scalar_one_or_none()
        if not sub:
            return False, "subscription_not_found", snap
        snap["subscription_status"] = sub.status
        if sub.status != "active":
            return False, "subscription_not_active", snap
        if sub.allowed_markets and market_id not in sub.allowed_markets:
            snap["allowed_markets"] = sub.allowed_markets
            return False, "market_not_allowed", snap
        if sub.max_leverage is not None and leverage > float(sub.max_leverage) + 1e-9:
            snap["max_leverage"] = float(sub.max_leverage)
            return False, "max_leverage_exceeded", snap
        if sub.max_margin_per_trade is not None and allocation_usd > float(sub.max_margin_per_trade) + 1e-9:
            snap["max_margin_per_trade"] = float(sub.max_margin_per_trade)
            return False, "max_margin_exceeded", snap

        # Daily / total realized-loss limits over LIVE closed positions (the
        # paper engine had these; the live ladder now enforces them too).
        if sub.max_daily_loss is not None:
            daily = await _live_realized(sub.id, today_only=True)
            snap["daily_realized"] = round(daily, 2)
            if daily <= -float(sub.max_daily_loss):
                snap["max_daily_loss"] = float(sub.max_daily_loss)
                return False, "max_daily_loss_reached", snap
        if sub.max_total_loss is not None:
            total = await _live_realized(sub.id, today_only=False)
            snap["total_realized"] = round(total, 2)
            if total <= -float(sub.max_total_loss):
                snap["max_total_loss"] = float(sub.max_total_loss)
                return False, "max_total_loss_reached", snap

        # phase 5: cross-venue basis gate — HL-signal subscriptions only.
        # Fail-closed on unavailable basis: if the user set a cap we must be
        # able to measure it before letting the copy through.
        if getattr(sub, "exchange", "perpl") == "hl" and sub.max_basis_bps is not None:
            from app.services.hyperliquid import prices as hl_prices
            basis = await hl_prices.basis_bps_for_market(market_id)
            snap["basis_bps"] = round(basis, 2) if basis is not None else None
            snap["max_basis_bps"] = int(sub.max_basis_bps)
            if basis is None:
                return False, "basis_unavailable", snap
            if abs(basis) > float(sub.max_basis_bps):
                return False, "basis_exceeded", snap

    return True, None, snap


STALE_SUBMITTED_MIN = 15


async def expire_stale_submitted() -> int:
    """Mark 'submitted' live attempts that never got a client result as failed
    (safely retryable — a new confirmation creates a fresh attempt). An attempt
    the client abandoned (tab closed, crash) must not stay 'submitted' forever."""
    from sqlalchemy import update as sa_update
    from datetime import timedelta
    cutoff = datetime.utcnow() - timedelta(minutes=STALE_SUBMITTED_MIN)
    sf = get_session_factory()
    async with sf() as session:
        res = await session.execute(
            sa_update(CopyOrder)
            .where(CopyOrder.status == "submitted",
                   CopyOrder.mode == "live_manual",
                   CopyOrder.submitted_at < cutoff)
            .values(status="failed",
                    error_message=f"expired: no result reported within {STALE_SUBMITTED_MIN} min")
        )
        await session.commit()
        n = res.rowcount or 0
    if n:
        logger.info("expired %d stale submitted copy orders", n)
    return n


async def create_attempt(
    *, follower_wallet: str, trader_wallet: str, market_id: int, symbol: str,
    side: str, intended_size, intended_price, leverage, allocation_usd,
    subscription_id=None, leader_event_id=None, idempotency_key: str,
    mode: str = "live_manual", action: str = "open", live_position_id=None,
    order_type: str | None = None, tp_price=None, sl_price=None,
    basis_cap_bps: int | None = None,
) -> dict:
    """Create (or return existing) a live copy-order attempt after the user confirms.

    - Idempotent on idempotency_key (confirm/retry -> one row).
    - Risk gate: blocked -> status='risk_blocked' + risk_event, placement NOT authorized.
    - COPY_LIVE_ENABLED gate: off -> status='risk_blocked' (skip_reason='live_disabled').
    - Passes -> status='submitted', submitted_at set, placement authorized.

    Returns the order dict; caller authorizes real placement ONLY when
    status == 'submitted'.
    """
    follower = follower_wallet.lower()
    try:
        await expire_stale_submitted()
    except Exception:
        logger.exception("stale-submitted expiry failed (continuing)")
    sf = get_session_factory()
    async with sf() as session:
        existing = (await session.execute(
            select(CopyOrder).where(CopyOrder.idempotency_key == idempotency_key)
        )).scalar_one_or_none()
        if existing:
            return order_dict(existing)

    base = dict(
        follower=follower, trader_wallet=trader_wallet, market_id=market_id,
        symbol=symbol, side=side, intended_size=intended_size,
        intended_price=intended_price, leverage=leverage, allocation_usd=allocation_usd,
        subscription_id=subscription_id, leader_event_id=leader_event_id,
        idempotency_key=idempotency_key, mode=mode, action=action,
        live_position_id=live_position_id,
        order_type=order_type, tp_price=tp_price, sl_price=sl_price,
    )

    async def _block(skip_reason: str, event_type: str, message: str, snap: dict | None = None) -> dict:
        """Record a blocked attempt: risk_blocked copy_order + risk_event + audit."""
        order = await _persist(**base, status="risk_blocked", skip_reason=skip_reason)
        await audit.write_risk_event(
            follower, event_type, subscription_id=subscription_id,
            detail={
                "severity": "blocked", "message": message,
                "metadata": {"wallet": follower, "copy_mode": settings.COPY_MODE, "market_id": market_id},
                "copy_order_id": order["id"], **({"snap": snap} if snap else {}),
            },
        )
        await audit.write_audit(
            follower, "live_copy_blocked", entity_type="copy_order",
            entity_id=order["id"], detail={"reason": skip_reason},
        )
        return order

    # --- Gate 1: live order placement must be explicitly enabled ---
    if not settings.COPY_LIVE_ENABLED:
        return await _block("live_disabled", "live_copy_disabled", "Live copy is currently disabled")

    # --- Gate 1b: product must be in live_manual mode ---
    if settings.COPY_MODE != "live_manual":
        return await _block("live_mode_disabled", "live_mode_disabled",
                            "Live manual copy is not the active copy mode")

    # --- Gate 2: LIVE EXECUTION allowlist (staged rollout, FAIL-CLOSED) ---
    # AND-ed with the kill switch above; an empty allowlist blocks everyone.
    # Full-user enablement is a later owner decision (env change, no deploy).
    if not settings.live_allowlist_ok(follower):
        return await _block("not_in_live_allowlist", "not_in_live_allowlist",
                            "Live copy execution is in staged rollout — your wallet is not enabled yet")

    # --- Gate 2b: OPTIONAL internal private-beta allowlist (OFF by default) ---
    # Only enforced when COPY_REQUIRE_INTERNAL_ALLOWLIST=true. Not the normal gate.
    if not settings.internal_allowlist_ok(follower):
        return await _block("wallet_not_allowlisted", "wallet_not_allowlisted",
                            "Live copy beta access required")

    # --- Gate 3: Perpl trading approval (the normal per-wallet eligibility gate) ---
    # Approved Perpl traders (on-chain Perpl account) may live-copy; others can't.
    if not await _perpl_eligible(follower):
        return await _block("perpl_access_required", "perpl_access_required",
                            "Perpl trading access required")

    # --- Gate 4a: CLOSE / REDUCE — owner-only, position open, no duplicate ---
    if action in ("close", "reduce"):
        from app.services.copy import live_positions
        pos = await live_positions.get_position(live_position_id, follower) if live_position_id else None
        if not pos:
            return await _block("position_not_found", "position_not_found",
                                "Copied position not found")
        if pos["status"] not in ("open", "partially_closed"):
            return await _block("position_not_open", "position_not_open",
                                "Position is already closed")
        if await live_positions.has_pending_close(live_position_id):
            return await _block("close_already_pending", "close_already_pending",
                                "A close order is already in progress")
        # Closing reduces risk — no leverage/margin checks. Authorize.
        return await _persist(**base, status="submitted", submitted_at=datetime.utcnow())

    # --- Gate 4b: OPEN — risk checks (markets / leverage / margin / subscription) ---
    allowed, reason, snap = await _eval_risk(
        follower, subscription_id, market_id, float(leverage or 0), float(allocation_usd or 0)
    )
    if not allowed:
        msg = "Market is no longer active on Perpl" if reason == "market_inactive" else (reason or "risk_blocked")
        return await _block(reason or "risk_blocked", reason or "risk_blocked", msg, snap)

    # --- Gate 4b2 (audit A7): ad-hoc basis cap — DEFAULT 30 bps when no
    # subscription is referenced. Explicit basis_cap_bps=0 = user cleared the
    # guard (honored); >0 = user's own cap. Same fail-closed semantics as the
    # subscription gate: unmeasurable basis blocks when a cap is in force.
    if subscription_id is None:
        cap = DEFAULT_ADHOC_BASIS_BPS if basis_cap_bps is None else int(basis_cap_bps)
        if cap > 0:
            from app.services.hyperliquid import prices as hl_prices
            basis = await hl_prices.basis_bps_for_market(market_id)
            bsnap = {"basis_bps": round(basis, 2) if basis is not None else None,
                     "basis_cap_bps": cap, "cap_source": "default" if basis_cap_bps is None else "user"}
            if basis is None:
                return await _block("basis_unavailable", "basis_unavailable",
                                    "Cross-venue basis unavailable — blocked (fail closed)", bsnap)
            if abs(basis) > cap:
                return await _block("basis_exceeded", "basis_exceeded",
                                    f"Cross-venue basis {basis:.1f} bps exceeds the {cap} bps cap", bsnap)

    # --- Gate 4c: TP/SL trigger sanity vs the intended entry price ---
    # long: SL below entry, TP above; short: mirrored. A trigger on the wrong
    # side would fire instantly — block it instead of letting the venue decide.
    ref = float(intended_price or 0)
    if ref > 0 and (tp_price or sl_price):
        tp, sl = float(tp_price or 0), float(sl_price or 0)
        bad = (side == "long" and ((tp and tp <= ref) or (sl and sl >= ref))) or \
              (side == "short" and ((tp and tp >= ref) or (sl and sl <= ref)))
        if bad:
            return await _block("invalid_trigger_prices", "invalid_trigger_prices",
                                "TP/SL trigger prices are on the wrong side of the entry price",
                                {"side": side, "entry": ref, "tp": tp or None, "sl": sl or None})

    # --- Authorized: record submitted; client places the real order next. ---
    return await _persist(**base, status="submitted", submitted_at=datetime.utcnow())


async def _persist(follower, trader_wallet, market_id, symbol, side, intended_size,
                   intended_price, leverage, allocation_usd, subscription_id,
                   leader_event_id, idempotency_key, mode, action="open",
                   live_position_id=None, order_type=None, tp_price=None,
                   sl_price=None, *, status,
                   skip_reason=None, submitted_at=None) -> dict:
    sf = get_session_factory()
    async with sf() as session:
        o = CopyOrder(
            subscription_id=subscription_id,
            follower_wallet=follower,
            trader_wallet=trader_wallet.lower(),
            leader_event_id=leader_event_id,
            mode=mode, action=action, live_position_id=live_position_id,
            market_id=market_id, symbol=symbol, side=side,
            intended_size=intended_size, intended_price=intended_price,
            leverage=leverage, allocation_usd=allocation_usd,
            order_type=order_type, tp_price=tp_price, sl_price=sl_price,
            status=status, skip_reason=skip_reason,
            submitted_at=submitted_at,
            idempotency_key=idempotency_key,
        )
        session.add(o)
        try:
            await session.commit()
            await session.refresh(o)
            return order_dict(o)
        except Exception:
            await session.rollback()
            dup = (await session.execute(
                select(CopyOrder).where(CopyOrder.idempotency_key == idempotency_key)
            )).scalar_one_or_none()
            if dup:
                return order_dict(dup)
            raise


async def update_result(order_id: int, follower_wallet: str, *, status: str,
                        perpl_request_id=None, perpl_order_id=None, perpl_fill_id=None,
                        fill_price=None, fill_size=None, error_message=None,
                        tp_order_id=None, sl_order_id=None) -> dict | None:
    """Record the real placement result (filled | placed | failed) from the
    client. 'placed' = a resting limit order confirmed in the Perpl book (real
    order id, no fill yet — no live position until a fill is reported)."""
    sf = get_session_factory()
    async with sf() as session:
        o = (await session.execute(
            select(CopyOrder).where(
                CopyOrder.id == order_id,
                CopyOrder.follower_wallet == follower_wallet.lower(),
            )
        )).scalar_one_or_none()
        if not o:
            return None
        o.status = status
        if tp_order_id is not None:
            o.tp_order_id = tp_order_id
        if sl_order_id is not None:
            o.sl_order_id = sl_order_id
        if perpl_request_id is not None:
            o.perpl_request_id = perpl_request_id
        if perpl_order_id is not None:
            o.perpl_order_id = perpl_order_id
        if perpl_fill_id is not None:
            o.perpl_fill_id = perpl_fill_id
        if fill_price is not None:
            o.fill_price = fill_price
        if fill_size is not None:
            o.fill_size = fill_size
        if error_message is not None:
            o.error_message = error_message
        if status == "filled":
            o.filled_at = datetime.utcnow()
        await session.commit()
        await session.refresh(o)
        return order_dict(o)
