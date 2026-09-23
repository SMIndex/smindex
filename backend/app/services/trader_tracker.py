"""Tracks top traders' positions and detects entries/exits.

Polls top 10 leaderboard wallets every 30s via on-chain reads.
Diffs snapshots to detect new positions (entry) and closed positions (exit).
Broadcasts trader_activity events via WS.
"""
import asyncio
import time
from concurrent.futures import ThreadPoolExecutor

from app.services.perpl_client import perpl_client
from app.services.chain_reader import _get_w3_contract, MARKETS, POS_TYPES
from app.utils.logger import get_logger

logger = get_logger(__name__)

async def _trigger_auto_copies(wallet: str, market_id: int, symbol: str, side: str):
    """Queue auto-copy for all followers tracking this leader."""
    try:
        from app.routers.autocopy import get_all_configs
        from app.services.auto_copy import queue_copy_async
        from app.services import sl_tp_service
        from app.db.database import get_session_factory
        from app.db.models import User
        from sqlalchemy import select

        all_configs = await get_all_configs()
        followers = all_configs.get(wallet.lower(), [])
        for cfg in followers:
            await queue_copy_async(cfg["follower_wallet"], wallet, market_id, symbol, side, cfg["allocation_usd"], cfg["max_leverage"])

            # Notify follower that auto-copy was queued
            try:
                from app.services.telegram_bot import notify_copy_queued
                await notify_copy_queued(
                    cfg["follower_wallet"], wallet, symbol, side,
                    cfg["allocation_usd"], cfg["max_leverage"],
                )
            except Exception:
                pass

            # Auto-create SL/TP if configured
            sl_pct = cfg.get("sl_pct")
            tp_pct = cfg.get("tp_pct")
            if sl_pct or tp_pct:
                # Look up user_id from wallet
                sf = get_session_factory()
                async with sf() as session:
                    result = await session.execute(
                        select(User).where(User.wallet_address == cfg["follower_wallet"])
                    )
                    user = result.scalar_one_or_none()
                if not user:
                    continue

                # Get current mark price for calculating SL/TP levels
                from app.services.ws_manager import ws_manager
                state = ws_manager.market_state_cache.get(market_id, {})
                mark_price = state.get("mark_price", 0)
                if not mark_price:
                    continue

                if sl_pct:
                    sl_trigger = mark_price * (1 - sl_pct / 100) if side == "long" else mark_price * (1 + sl_pct / 100)
                    await sl_tp_service.add_order(user.id, cfg["follower_wallet"], market_id, side, "sl", sl_trigger, source="auto_copy")

                if tp_pct:
                    tp_trigger = mark_price * (1 + tp_pct / 100) if side == "long" else mark_price * (1 - tp_pct / 100)
                    await sl_tp_service.add_order(user.id, cfg["follower_wallet"], market_id, side, "tp", tp_trigger, source="auto_copy")
    except Exception:
        logger.exception("Auto-copy trigger failed")


# === Copy v1 paper engine hook ===
# NOTE: _trigger_auto_copies above is LEGACY (writes pending_copies + SL/TP) and is
# NO LONGER CALLED. v1 routes leader detection into the paper engine below — paper
# only, no PendingCopy, no SL/TP, no real orders.
async def _run_paper_copy(trader_wallet: str, market_id: int, symbol: str, side: str,
                          event_type: str, size=None, price=None, deposit=None):
    """Record a normalized leader_trade_event and run the paper copy engine.

    Idempotent: re-detecting the same state dedupes on unique_event_key, and the
    engine dedupes per (event, subscription) on copy_orders.idempotency_key.
    """
    try:
        from app.services.copy import leader_events, paper_engine
        from app.services.ws_manager import ws_manager as _wm

        mark = None
        if _wm and market_id in _wm.market_state_cache:
            mark = _wm.market_state_cache[market_id].get("mark_price")
        market_state = {"mark_price": mark} if mark else None
        eff_price = price if price is not None else mark

        # Leverage = notional / on-chain deposit at detection time. Historical
        # events never carried this (raw was null) — recorded going forward so
        # Wallet Insights can show real leverage per trade.
        raw = None
        if deposit and eff_price and size:
            raw = {"deposit": round(deposit, 4),
                   "leverage": round((size * eff_price) / deposit, 2)}

        evt, _created = await leader_events.record_event(
            trader_wallet, market_id, symbol, side, event_type, size=size, price=eff_price,
            raw=raw)
        # Safe to process even on a duplicate event — the engine is idempotent.
        await paper_engine.process_leader_event(evt["id"], market_state)
        # LIVE positions: leader close/reduce -> flag a SUGGESTION on matching open
        # live copied positions (display + confirm; NEVER places an order).
        from app.services.copy import live_positions
        await live_positions.handle_leader_event(evt)
    except Exception:
        logger.exception("paper copy processing failed (%s %s)", event_type, symbol)


POLL_INTERVAL = 30  # seconds
TOP_N = 10
# Wide tier: top-N leaderboard wallets by PnL polled every WIDE_EVERY ticks
# (2 min at 30s ticks). Feeds the Wallet Insights history without multiplying
# the per-tick RPC load — the core set (top 10 + followed/watched) stays at 30s.
WIDE_N = 100
WIDE_EVERY = 4

# Cache account IDs (don't change)
_account_id_cache: dict[str, int] = {}


def _get_account_id(contract, wallet: str) -> int:
    """Returns the Perpl account id, 0 if the wallet has no account, or -1 if
    the RPC read failed (must NOT be treated as 'no account' — that made every
    position look closed for one tick and fired phantom exit/entry alerts)."""
    from web3 import Web3
    if wallet in _account_id_cache:
        return _account_id_cache[wallet]
    try:
        acct = contract.functions.getAccountByAddr(Web3.to_checksum_address(wallet)).call()
        aid = acct[0]
        if aid > 0:
            _account_id_cache[wallet] = aid
        return aid
    except Exception:
        return -1


def _read_positions(wallet: str):
    """Read all positions for a wallet.

    Returns (positions, failed_market_ids). A market whose RPC read failed is
    reported in failed_market_ids instead of silently vanishing from the
    snapshot — vanishing looked identical to 'position closed' and produced
    phantom exit+entry alert pairs. Returns None when the account id read
    itself failed; the caller must skip this wallet for the tick."""
    w3, contract = _get_w3_contract()
    aid = _get_account_id(contract, wallet)
    if aid < 0:
        return None
    positions = {}
    failed: list[int] = []
    if not aid:
        return positions, failed

    for perp_id, mcfg in MARKETS.items():
        try:
            pos = contract.functions.getPosition(perp_id, aid).call()
            p = pos[0]
            if p[4] > 0:  # deposit > 0 = has position
                pd = 10 ** mcfg["price_decimals"]
                sd = 10 ** mcfg["size_decimals"]
                positions[perp_id] = {
                    "side": POS_TYPES.get(p[3], "long"),
                    "size": p[6] / sd,
                    "entry_price": p[5] / pd,
                    "deposit": p[4] / 1e6,
                }
        except Exception:
            failed.append(perp_id)
    return positions, failed


class TraderTracker:
    def __init__(self):
        # wallet -> {market_id: position_data}
        self._snapshots: dict[str, dict] = {}
        self._top_wallets: list[str] = []
        self._wide_wallets: list[str] = []
        self._tick_count = 0
        self._activities: list[dict] = []  # recent activities (max 50)

    async def run_periodic(self):
        logger.info("TraderTracker started (poll every %ds, top %d traders)", POLL_INTERVAL, TOP_N)
        await asyncio.sleep(15)  # wait for startup

        while True:
            try:
                await self._tick()
            except asyncio.CancelledError:
                break
            except Exception:
                logger.exception("TraderTracker error")
            await asyncio.sleep(POLL_INTERVAL)

    async def _tick(self):
        self._tick_count += 1
        # Get top wallets from leaderboard
        try:
            traders = await perpl_client.get_leaderboard_parsed("all", "pnl")
            self._top_wallets = [t["wallet_address"] for t in traders[:TOP_N]]
            # Wide tier: only green-PnL wallets qualify (losers add RPC load
            # without ever appearing in the green-filtered insights grid).
            self._wide_wallets = [
                t["wallet_address"] for t in traders[:WIDE_N] if t.get("pnl_total", 0) > 0
            ]
        except Exception:
            if not self._top_wallets:
                return

        # Also add ALL followed leader wallets (not just top 10)
        wallets_to_poll = set(w.lower() for w in self._top_wallets)
        if self._tick_count % WIDE_EVERY == 0:
            wallets_to_poll.update(w.lower() for w in self._wide_wallets)
        try:
            from app.db.database import get_session_factory
            from app.db.models import WalletFollow
            from sqlalchemy import select, distinct
            sf = get_session_factory()
            async with sf() as session:
                result = await session.execute(
                    select(distinct(WalletFollow.leader_wallet)).where(
                        WalletFollow.is_active == True
                    )
                )
                for row in result.all():
                    wallets_to_poll.add(row[0].lower())
        except Exception:
            logger.exception("Failed to load followed leader wallets")

        # v1: also poll watched + actively-copied trader wallets
        try:
            from app.db.database import get_session_factory
            from app.db.copy_models import Watchlist, CopySubscription
            from sqlalchemy import select, distinct
            sf = get_session_factory()
            async with sf() as session:
                result = await session.execute(select(distinct(Watchlist.trader_wallet)))
                for row in result.all():
                    wallets_to_poll.add(row[0].lower())
                result = await session.execute(
                    select(distinct(CopySubscription.trader_wallet)).where(
                        CopySubscription.status == "active"
                    )
                )
                for row in result.all():
                    wallets_to_poll.add(row[0].lower())
        except Exception:
            logger.exception("Failed to load v1 watched/copied trader wallets")

        all_wallets = list(wallets_to_poll)

        # Read positions in parallel
        loop = asyncio.get_event_loop()
        with ThreadPoolExecutor(max_workers=5) as pool:
            results = await asyncio.gather(*[
                loop.run_in_executor(pool, _read_positions, w)
                for w in all_wallets
            ])

        # Diff against previous snapshots
        from app.ws.client_feed import client_manager

        for wallet, result in zip(all_wallets, results):
            if result is None:
                # Account id read failed — keep the old snapshot untouched and
                # skip diffing so a flaky RPC can't fake a close.
                continue
            new_positions, failed_markets = result

            # First time we see this wallet: seed baseline silently. Otherwise
            # every backend restart / newly-followed leader would re-fire entry
            # alerts for positions opened long ago, with stale on-chain entry prices.
            if wallet not in self._snapshots:
                if failed_markets:
                    continue  # incomplete baseline — seed on a clean read instead
                self._snapshots[wallet] = new_positions
                continue
            old_positions = self._snapshots[wallet]

            # Markets whose read failed this tick keep their previous state —
            # a failed read is not a closed position.
            for mid in failed_markets:
                if mid in old_positions:
                    new_positions[mid] = old_positions[mid]

            # Detect entries (new market_id not in old)
            for mid, pos in new_positions.items():
                if mid not in old_positions:
                    activity = {
                        "type": "entry",
                        "wallet": wallet,
                        "market_id": mid,
                        "symbol": MARKETS.get(mid, {}).get("symbol", "?"),
                        "side": pos["side"],
                        "size": pos["size"],
                        "entry_price": pos["entry_price"],
                        "timestamp": time.time(),
                    }
                    self._activities = [activity, *self._activities][:50]
                    await client_manager.broadcast("trader_activity", activity, market_id=mid)
                    logger.info("Trader entry: %s %s %s @ %s", wallet[:10], pos["side"], MARKETS.get(mid, {}).get("symbol", "?"), pos["entry_price"])
                    # v1 paper copy (replaces legacy _trigger_auto_copies)
                    await _run_paper_copy(wallet, mid, MARKETS.get(mid, {}).get("symbol", "?"), pos["side"], "opened", size=pos["size"], price=pos["entry_price"], deposit=pos.get("deposit"))
                    await self._save_activity(activity)

                    # Telegram + WS notification to followers
                    try:
                        from app.services.telegram_bot import notify_leader_entry
                        from app.services.ws_manager import ws_manager as _wm_entry
                        deposit = pos.get("deposit", 0)
                        notional = pos["size"] * pos["entry_price"]
                        leverage = round(notional / deposit, 1) if deposit > 0 else 0
                        mark_now = None
                        if _wm_entry and mid in _wm_entry.market_state_cache:
                            mark_now = _wm_entry.market_state_cache[mid].get("mark_price")
                        # notify_* only ENQUEUES (telegram_queue) — no network
                        # I/O inside the detection tick. Failures here are
                        # logged with context, never silently swallowed.
                        await notify_leader_entry(
                            wallet, mid, MARKETS.get(mid, {}).get("symbol", "?"),
                            pos["side"], pos["entry_price"], pos["size"],
                            leverage=leverage, notional=notional, mark_price=mark_now,
                        )
                    except Exception:
                        logger.exception(
                            "entry alert enqueue failed for %s market %s", wallet[:10], mid
                        )
                    await client_manager.broadcast("copy_updates", {"type": "leader_entry", **activity})

            # Detect exits (old market_id not in new)
            for mid in old_positions:
                if mid not in new_positions:
                    old_pos = old_positions[mid]
                    activity = {
                        "type": "exit",
                        "wallet": wallet,
                        "market_id": mid,
                        "symbol": MARKETS.get(mid, {}).get("symbol", "?"),
                        "side": old_pos["side"],
                        "size": old_pos["size"],
                        "entry_price": old_pos["entry_price"],
                        "timestamp": time.time(),
                    }
                    self._activities = [activity, *self._activities][:50]
                    await client_manager.broadcast("trader_activity", activity, market_id=mid)
                    logger.info("Trader exit: %s closed %s %s", wallet[:10], old_pos["side"], MARKETS.get(mid, {}).get("symbol", "?"))
                    await self._save_activity(activity)

                    # Telegram + WS notification to followers on exit
                    try:
                        from app.services.telegram_bot import notify_leader_exit
                        close_price = old_pos.get("entry_price", 0)
                        from app.services.ws_manager import ws_manager as _wm
                        if _wm and mid in _wm.market_state_cache:
                            close_price = _wm.market_state_cache[mid].get("mark_price", close_price)
                        pnl = (
                            (close_price - old_pos["entry_price"]) * old_pos["size"]
                            if old_pos["side"] == "long"
                            else (old_pos["entry_price"] - close_price) * old_pos["size"]
                        )
                        await notify_leader_exit(
                            wallet, mid, MARKETS.get(mid, {}).get("symbol", "?"),
                            old_pos["side"], close_price, pnl=pnl,
                            entry_price=old_pos["entry_price"], size=old_pos["size"],
                        )
                    except Exception:
                        logger.exception(
                            "exit alert enqueue failed for %s market %s", wallet[:10], mid
                        )
                    await client_manager.broadcast("copy_updates", {"type": "leader_exit", **activity})

                    # v1 paper copy: simulate close for followers (price=None -> live mark)
                    await _run_paper_copy(
                        wallet, mid, MARKETS.get(mid, {}).get("symbol", "?"),
                        old_pos["side"], "closed", size=old_pos["size"], price=None,
                        deposit=old_pos.get("deposit"),
                    )

            # Detect size changes on markets present in BOTH snapshots (increase/reduce).
            # Terminal trader_activity feed is intentionally left untouched here — this
            # only drives the v1 paper copy engine.
            for mid, npos in new_positions.items():
                if mid not in old_positions:
                    continue
                old_sz = old_positions[mid].get("size", 0) or 0
                new_sz = npos.get("size", 0) or 0
                if old_sz <= 0:
                    continue
                if new_sz > old_sz * 1.0001:
                    await _run_paper_copy(wallet, mid, MARKETS.get(mid, {}).get("symbol", "?"),
                                          npos["side"], "increased", size=new_sz, price=None,
                                          deposit=npos.get("deposit"))
                elif new_sz < old_sz * 0.9999:
                    await _run_paper_copy(wallet, mid, MARKETS.get(mid, {}).get("symbol", "?"),
                                          npos["side"], "reduced", size=new_sz, price=None,
                                          deposit=npos.get("deposit"))

            self._snapshots[wallet] = new_positions

    async def _save_activity(self, activity: dict):
        try:
            from app.db.database import get_session_factory
            from app.db.models import TraderActivity
            sf = get_session_factory()
            async with sf() as session:
                session.add(TraderActivity(
                    activity_type=activity.get("type", ""),
                    wallet_address=activity.get("wallet", ""),
                    market_id=activity.get("market_id", 0),
                    symbol=activity.get("symbol", ""),
                    side=activity.get("side", ""),
                    size=activity.get("size"),
                    entry_price=activity.get("entry_price"),
                ))
                await session.commit()
        except Exception:
            pass

    def get_recent_activity(self) -> list[dict]:
        return self._activities


trader_tracker = TraderTracker()
