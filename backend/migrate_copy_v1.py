"""Copy Trading v1 — backfill migration SKELETON.

Splits the legacy `wallet_follows` table into the two v1 tables:
  * wallet_follows                          -> watchlists        (the WATCH relationship)
  * wallet_follows (with copy config)       -> copy_subscriptions (the COPY config, mode='paper')

It also (TODO) upserts trader_profiles for every leader_wallet seen.

SAFETY:
  * This is a SKELETON. The actual INSERT logic is left as TODO blocks.
  * It NEVER runs automatically and is NOT imported by the app.
  * Default mode is DRY-RUN (prints what it would do). Real writes require an
    explicit `--execute` flag AND the TODO blocks to be implemented.
  * It only READS wallet_follows and WRITES new v1 tables. It does not modify or
    delete any legacy table.

Run manually (dry-run):   python backend/migrate_copy_v1.py
Run for real (later):     python backend/migrate_copy_v1.py --execute
"""
import argparse
import asyncio

from sqlalchemy import select

from app.config import settings
from app.db import init_db, get_session_factory
from app.db.models import WalletFollow
# v1 targets (writes go here once TODOs are implemented):
from app.db.copy_models import Watchlist, CopySubscription, TraderProfile  # noqa: F401


# A wallet_follow is considered to carry "copy config" (→ copy_subscription) when
# it has an allocation set (or auto_copy was on / sl-tp configured). Pure follows
# with no economic config become watch-only.
def _has_copy_config(wf: WalletFollow) -> bool:
    return bool(
        (wf.allocation_usd and wf.allocation_usd > 0)
        or wf.auto_copy
        or wf.sl_pct is not None
        or wf.tp_pct is not None
    )


async def backfill(execute: bool) -> None:
    await init_db(settings.DATABASE_URL)
    sf = get_session_factory()

    async with sf() as session:
        follows = (await session.execute(select(WalletFollow))).scalars().all()

    print(f"Found {len(follows)} wallet_follows rows to migrate.")
    watch_n = sub_n = 0

    for wf in follows:
        follower = wf.follower_wallet.lower()
        trader = wf.leader_wallet.lower()

        # 1) Every follow → a watchlist row (watch ≠ copy).
        watch_n += 1
        print(f"  WATCH  follower={follower[:10]} trader={trader[:10]}")
        # TODO(execute): upsert TraderProfile(wallet_address=trader, source='leaderboard')
        # TODO(execute): insert Watchlist(follower_wallet=follower, trader_wallet=trader,
        #                created_at=wf.created_at)  — respect uq_watchlist_pair (skip if exists)

        # 2) Follows that carried copy config → a paper copy_subscription.
        if _has_copy_config(wf):
            sub_n += 1
            print(
                f"  COPY   follower={follower[:10]} trader={trader[:10]} "
                f"alloc={wf.allocation_usd} max_lev={wf.max_leverage} "
                f"auto_copy={wf.auto_copy} sl={wf.sl_pct} tp={wf.tp_pct} "
                f"-> mode='paper', status={'active' if wf.is_active else 'paused'}, live_enabled=False"
            )
            # TODO(execute): insert CopySubscription(
            #     follower_wallet=follower, trader_wallet=trader,
            #     mode='paper', status='active' if wf.is_active else 'paused',
            #     sizing_mode='fixed',
            #     allocation_usd=wf.allocation_usd, max_leverage=wf.max_leverage,
            #     sl_pct=wf.sl_pct, tp_pct=wf.tp_pct,
            #     copy_new_only=True, live_enabled=False,  # FORCED False in v1
            #     created_at=wf.created_at,
            # )  — respect uq_copy_sub_pair (skip if exists)

    print(f"\nWould create: {watch_n} watchlists, {sub_n} copy_subscriptions.")
    if not execute:
        print("DRY-RUN: no rows written. Re-run with --execute (after implementing the TODO blocks).")
    else:
        # Guard: refuse to "succeed" silently while the write TODOs are unimplemented.
        raise NotImplementedError(
            "Write blocks are not implemented yet. Implement the TODO(execute) sections "
            "before running with --execute."
        )


def main() -> None:
    parser = argparse.ArgumentParser(description="Copy v1 backfill (skeleton).")
    parser.add_argument("--execute", action="store_true", help="Perform writes (requires implemented TODOs).")
    args = parser.parse_args()
    asyncio.run(backfill(args.execute))


if __name__ == "__main__":
    main()
