"""Migration v15 — models M1–M6 / Mind columns (doc 10 §5).

Shared by the API lifespan AND the strategy worker startup so the worker does
not depend on an API restart (the API is MM-slot gated). Guarded ALTERs via
information_schema; idempotent; new tables come from create_all (init_db).
"""
import logging

from sqlalchemy import text

logger = logging.getLogger(__name__)

SIGNAL_COLS = [
    ("model", "VARCHAR(8) NULL"),
    ("level_type", "VARCHAR(32) NULL"),
    ("level_price", "DOUBLE NULL"),
    ("day_type", "VARCHAR(16) NULL"),
    ("raw_conviction", "DOUBLE NULL"),
    ("conviction", "DOUBLE NULL"),
    ("size_tier", "VARCHAR(8) NULL"),
    ("reasons_json", "JSON NULL"),
    ("vetoes_json", "JSON NULL"),
    ("multipliers_json", "JSON NULL"),
    ("thesis", "TEXT NULL"),
]
TRADE_COLS = [
    ("model", "VARCHAR(8) NULL"),
    ("expected_hold_min", "INT NULL"),
    ("r_multiple", "DOUBLE NULL"),
    ("in_trade_checks_json", "JSON NULL"),
    ("lifecycle_json", "JSON NULL"),
]


async def _missing(session, table: str, cols: list[tuple[str, str]]) -> list[tuple[str, str]]:
    rows = (await session.execute(text(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_schema = DATABASE() AND table_name = :t"), {"t": table})).scalars().all()
    have = {str(r).lower() for r in rows}
    return [(c, d) for c, d in cols if c.lower() not in have]


async def apply_v15(session) -> bool:
    """Returns True when anything changed."""
    changed = False
    for table, cols in (("strat_signals", SIGNAL_COLS), ("strat_trades", TRADE_COLS)):
        miss = await _missing(session, table, cols)
        if miss:
            await session.execute(text(
                f"ALTER TABLE {table} " + ", ".join(f"ADD COLUMN {c} {d}" for c, d in miss)))
            changed = True
            logger.info("migration v15: %s += %s", table, [c for c, _ in miss])
    n = (await session.execute(text(
        "SELECT CHARACTER_MAXIMUM_LENGTH FROM information_schema.columns "
        "WHERE table_schema = DATABASE() AND table_name = 'strat_trades' "
        "AND column_name = 'exit_reason'"))).scalar()
    if n is not None and int(n) < 128:
        await session.execute(text("ALTER TABLE strat_trades MODIFY exit_reason VARCHAR(128) NULL"))
        changed = True
        logger.info("migration v15: strat_trades.exit_reason widened to 128 (v14 catch-up)")
    idx = (await session.execute(text(
        "SELECT COUNT(*) FROM information_schema.statistics WHERE table_schema = DATABASE() "
        "AND table_name = 'strat_signals' AND index_name = 'ix_strat_signals_model_ts'"))).scalar()
    if not idx:
        await session.execute(text("CREATE INDEX ix_strat_signals_model_ts ON strat_signals (model, ts)"))
        changed = True
    if changed:
        await session.commit()
    return changed


LIQ_COLS = [
    ("source", "VARCHAR(16) NULL"),
]


async def apply_v16(session) -> bool:
    """Spec v1.1 (2026-09-06): strat_liquidations.source ('oxarchive' history rows
    vs NULL/'live' ws fills). Guarded, idempotent; new tables are create_all."""
    miss = await _missing(session, "strat_liquidations", LIQ_COLS)
    if not miss:
        return False
    await session.execute(text(
        "ALTER TABLE strat_liquidations " + ", ".join(f"ADD COLUMN {c} {d}" for c, d in miss)))
    await session.commit()
    logger.info("migration v16: strat_liquidations += %s", [c for c, _ in miss])
    return True


# spec v1.3 (2026-09-07, D-91): reclaim type on the signal row (M1/M5), stop floor +
# post-only rejection/re-quote on the trade row (all models)
SIGNAL_COLS_V17 = [
    ("reclaim_candles", "INT NULL"),
    ("confirmation_used", "TINYINT(1) NULL"),
]
TRADE_COLS_V17 = [
    ("stop_floor_applied", "TINYINT(1) NULL"),
    ("entry_requoted", "TINYINT(1) NULL"),
    ("entry_px_orig", "DOUBLE NULL"),
]


async def apply_v17(session) -> bool:
    changed = False
    for table, cols in (("strat_signals", SIGNAL_COLS_V17), ("strat_trades", TRADE_COLS_V17)):
        miss = await _missing(session, table, cols)
        if miss:
            await session.execute(text(
                f"ALTER TABLE {table} " + ", ".join(f"ADD COLUMN {c} {d}" for c, d in miss)))
            changed = True
            logger.info("migration v17: %s += %s", table, [c for c, _ in miss])
    if changed:
        await session.commit()
    return changed
