"""Tracks user positions on-chain and detects closes.

Polls every 15s for users who have open positions (in trade_history or copy_positions).
When a position that was open on-chain disappears, records the close trade and updates
copy_positions status.
"""
import asyncio
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

from app.utils.logger import get_logger

logger = get_logger(__name__)

POLL_INTERVAL = 15  # seconds


def _read_user_positions(wallet: str) -> dict:
    """Read all on-chain positions for a wallet. Returns {market_id: {side, size, entry_price, mark_price, deposit, pnl}}"""
    from app.services.chain_reader import _get_w3_contract, MARKETS, POS_TYPES
    from web3 import Web3

    try:
        w3, contract = _get_w3_contract()
        addr = Web3.to_checksum_address(wallet)
        acct = contract.functions.getAccountByAddr(addr).call()
        account_id = acct[0]
        if account_id == 0:
            return {}
    except Exception:
        return {}

    positions = {}
    for perp_id, mcfg in MARKETS.items():
        try:
            pos = contract.functions.getPosition(perp_id, account_id).call()
            p = pos[0]
            if p[4] > 0:  # deposit > 0 = position exists
                pd = 10 ** mcfg["price_decimals"]
                sd = 10 ** mcfg["size_decimals"]
                positions[perp_id] = {
                    "side": POS_TYPES.get(p[3], "long"),
                    "size": p[6] / sd,
                    "entry_price": p[5] / pd,
                    "mark_price": pos[1] / pd,
                    "deposit": p[4] / 1e6,
                    "pnl": p[8] / 1e6,
                }
        except Exception:
            pass
    return positions


class PositionTracker:
    def __init__(self):
        # wallet -> {market_id: position_data} — last known on-chain state
        self._snapshots: dict[str, dict] = {}
        self._tracked_wallets: set[str] = set()

    async def run_periodic(self):
        logger.info("PositionTracker started (poll every %ds)", POLL_INTERVAL)
        await asyncio.sleep(20)  # wait for other services to start

        while True:
            try:
                await self._tick()
            except asyncio.CancelledError:
                break
            except Exception:
                logger.exception("PositionTracker error")
            await asyncio.sleep(POLL_INTERVAL)

    async def _tick(self):
        wallets = await self._get_wallets_with_open_positions()
        if not wallets:
            return

        logger.info("Tracking %d wallets: %s", len(wallets), [w[:10] for w in wallets])
        self._tracked_wallets = set(wallets)

        # Read on-chain positions in parallel
        loop = asyncio.get_event_loop()
        with ThreadPoolExecutor(max_workers=5) as pool:
            results = await asyncio.gather(*[
                loop.run_in_executor(pool, _read_user_positions, w)
                for w in wallets
            ])

        now = datetime.now(timezone.utc)

        for wallet, current_positions in zip(wallets, results):
            old_positions = self._snapshots.get(wallet, None)

            # First tick for this wallet: record snapshot + close any stale copy_positions
            if old_positions is None:
                logger.info("First snapshot for %s: %d positions on-chain", wallet[:10], len(current_positions))
                self._snapshots[wallet] = current_positions
                # Close any copy_positions that are marked open but don't exist on-chain
                await self._close_stale_copy_positions(wallet, current_positions, now)
                continue

            # Detect closes: position was in old snapshot but gone now
            for mid, old_pos in old_positions.items():
                if mid not in current_positions:
                    await self._handle_close(wallet, mid, old_pos, now)

            self._snapshots[wallet] = current_positions

    async def _get_wallets_with_open_positions(self) -> list[str]:
        """Get unique wallets that have open positions we should track.

        Only returns wallets where opens > closes per market+side (i.e. likely
        still have an open position), plus wallets with copy_positions status=open.
        """
        from app.db.database import get_session_factory
        from app.db.models import TradeHistory, CopyPosition
        from sqlalchemy import select, distinct, func, and_, case

        sf = get_session_factory()
        wallets = set()

        async with sf() as session:
            # Count opens vs closes per wallet+market+side.
            # If opens > closes, that wallet likely still has an open position.
            result = await session.execute(
                select(
                    TradeHistory.wallet_address,
                    TradeHistory.market_id,
                    TradeHistory.side,
                    func.sum(case((TradeHistory.action == "open", 1), else_=0)).label("opens"),
                    func.sum(case((TradeHistory.action == "close", 1), else_=0)).label("closes"),
                )
                .group_by(
                    TradeHistory.wallet_address,
                    TradeHistory.market_id,
                    TradeHistory.side,
                )
                .having(
                    func.sum(case((TradeHistory.action == "open", 1), else_=0))
                    > func.sum(case((TradeHistory.action == "close", 1), else_=0))
                )
            )
            for row in result.all():
                wallets.add(row.wallet_address)

            # Also include wallets with copy_positions status='open'
            copy_wallets = await session.execute(
                select(distinct(CopyPosition.follower_wallet))
                .where(CopyPosition.status == "open")
            )
            for row in copy_wallets.all():
                wallets.add(row[0])

        return list(wallets)

    async def _handle_close(self, wallet: str, market_id: int, old_pos: dict, now: datetime):
        """Position closed on-chain. Record in trade_history + update copy_positions."""
        from app.services.chain_reader import MARKETS
        from app.db.database import get_session_factory
        from app.db.models import TradeHistory, CopyPosition, User
        from sqlalchemy import select, and_

        symbol = MARKETS.get(market_id, {}).get("symbol", f"MKT-{market_id}")
        side = old_pos["side"]
        size = old_pos["size"]
        entry_price = old_pos["entry_price"]

        # Get mark price from ws_manager for close price
        close_price = old_pos.get("mark_price", entry_price)
        try:
            from app.services.ws_manager import ws_manager as _wm
            if _wm and market_id in _wm.market_state_cache:
                close_price = _wm.market_state_cache[market_id].get("mark_price", close_price)
        except Exception:
            pass

        # Calculate PnL
        pnl = (close_price - entry_price) * size if side == "long" else (entry_price - close_price) * size
        notional = size * close_price

        logger.info(
            "Position closed: %s %s %s size=%.4f entry=%.2f close=%.2f pnl=%.2f",
            wallet[:10], side, symbol, size, entry_price, close_price, pnl,
        )

        sf = get_session_factory()
        async with sf() as session:
            # Look up user
            result = await session.execute(
                select(User).where(User.wallet_address == wallet.lower())
            )
            user = result.scalar_one_or_none()

            # Check if we already recorded this close (avoid duplicates)
            if user:
                existing = await session.execute(
                    select(TradeHistory).where(
                        and_(
                            TradeHistory.user_id == user.id,
                            TradeHistory.market_id == market_id,
                            TradeHistory.side == side,
                            TradeHistory.action == "close",
                            TradeHistory.created_at >= now.replace(second=0, microsecond=0),
                        )
                    )
                )
                if existing.scalar_one_or_none():
                    # Already recorded (user closed via terminal and we got the event)
                    pass
                else:
                    # Check if this is a copy trade
                    copy_pos = await session.execute(
                        select(CopyPosition).where(
                            and_(
                                CopyPosition.follower_wallet == wallet.lower(),
                                CopyPosition.market_id == market_id,
                                CopyPosition.side == side,
                                CopyPosition.status == "open",
                            )
                        )
                    )
                    is_copy = copy_pos.scalar_one_or_none() is not None

                    # Record close trade
                    session.add(TradeHistory(
                        user_id=user.id,
                        wallet_address=wallet.lower(),
                        market_id=market_id,
                        symbol=symbol,
                        side=side,
                        action="close",
                        order_type="market",
                        size=size,
                        price=round(close_price, 6),
                        leverage=old_pos.get("deposit", 0) and round(notional / old_pos["deposit"], 1) or None,
                        notional=round(notional, 2),
                        pnl=round(pnl, 2),
                        source="copy_trade" if is_copy else "manual",
                        created_at=now,
                    ))

            # Update copy_positions: mark as closed
            copy_rows = await session.execute(
                select(CopyPosition).where(
                    and_(
                        CopyPosition.follower_wallet == wallet.lower(),
                        CopyPosition.market_id == market_id,
                        CopyPosition.status == "open",
                    )
                )
            )
            for cp in copy_rows.scalars().all():
                cp.status = "closed"
                cp.close_price = round(close_price, 6)
                cp.realized_pnl = round(
                    (close_price - cp.entry_price) * cp.size if cp.side == "long"
                    else (cp.entry_price - close_price) * cp.size,
                    2,
                )
                cp.closed_at = now

            await session.commit()


    async def _close_stale_copy_positions(self, wallet: str, current_positions: dict, now: datetime):
        """On first snapshot, close any copy_positions marked open that don't exist on-chain."""
        from app.db.database import get_session_factory
        from app.db.models import CopyPosition, TradeHistory, User
        from app.services.chain_reader import MARKETS
        from sqlalchemy import select, and_

        sf = get_session_factory()
        async with sf() as session:
            result = await session.execute(
                select(CopyPosition).where(
                    and_(
                        CopyPosition.follower_wallet == wallet.lower(),
                        CopyPosition.status == "open",
                    )
                )
            )
            open_copies = result.scalars().all()

            closed_count = 0
            for cp in open_copies:
                # If this market+side has no on-chain position, it's closed
                on_chain = current_positions.get(cp.market_id)
                if on_chain and on_chain["side"] == cp.side:
                    continue  # still open on-chain

                # Get close price from ws_manager
                close_price = cp.entry_price
                try:
                    from app.services.ws_manager import ws_manager as _wm
                    if _wm and cp.market_id in _wm.market_state_cache:
                        close_price = _wm.market_state_cache[cp.market_id].get("mark_price", close_price)
                except Exception:
                    pass

                pnl = (
                    (close_price - cp.entry_price) * cp.size if cp.side == "long"
                    else (cp.entry_price - close_price) * cp.size
                )

                cp.status = "closed"
                cp.close_price = round(close_price, 6)
                cp.realized_pnl = round(pnl, 2)
                cp.closed_at = now
                closed_count += 1

                # Also record in trade_history
                user_result = await session.execute(
                    select(User).where(User.wallet_address == wallet.lower())
                )
                user = user_result.scalar_one_or_none()
                if user:
                    symbol = MARKETS.get(cp.market_id, {}).get("symbol", cp.symbol)
                    notional = cp.size * close_price
                    session.add(TradeHistory(
                        user_id=user.id,
                        wallet_address=wallet.lower(),
                        market_id=cp.market_id,
                        symbol=symbol,
                        side=cp.side,
                        action="close",
                        order_type="market",
                        size=cp.size,
                        price=round(close_price, 6),
                        leverage=round(notional / cp.allocation_usd, 1) if cp.allocation_usd else None,
                        notional=round(notional, 2),
                        pnl=round(pnl, 2),
                        source="copy_trade",
                        created_at=now,
                    ))

            if closed_count:
                await session.commit()
                logger.info("Closed %d stale copy_positions for %s", closed_count, wallet[:10])


position_tracker = PositionTracker()
