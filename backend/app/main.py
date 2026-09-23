import time as _time_module
from collections import defaultdict
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.base import BaseHTTPMiddleware

from app.config import settings
from app.db import init_db, close_db, get_session_factory
from app.services.ws_manager import WSManager
from app.ws.client_feed import client_feed_endpoint
from app.ws.trading_proxy import trading_proxy_endpoint
from app.ws.market_data_proxy import market_data_proxy_endpoint
from app.utils.logger import get_logger

logger = get_logger(__name__)


# Module-level orderbook cache so other modules (e.g. MCP server) can read it
# without going through HTTP. The background refresh thread inside create_app()
# writes to this dict.
ob_cache: dict[int, dict] = {}


# ---- Feature 3: Global Rate Limiting (in-memory, per-IP) ----

class RateLimitMiddleware(BaseHTTPMiddleware):
    """Simple in-memory rate limiter. Skips WebSocket upgrade requests."""

    def __init__(self, app, read_limit: int = 60, write_limit: int = 10, window: int = 60,
                 explorer_limit: int = 20):
        super().__init__(app)
        self.read_limit = read_limit   # GET/HEAD/OPTIONS per minute per IP
        self.write_limit = write_limit  # POST/PUT/PATCH/DELETE per minute per IP
        # Wallet Explorer Part 5: the public explorer endpoints get their own
        # tighter bucket (each hit can cost venue weight/RPC) — 20 req/min/IP
        self.explorer_limit = explorer_limit
        self.window = window
        self._read_hits: dict[str, list[float]] = defaultdict(list)
        self._write_hits: dict[str, list[float]] = defaultdict(list)
        self._explorer_hits: dict[str, list[float]] = defaultdict(list)

    async def dispatch(self, request: Request, call_next):
        # Skip WebSocket upgrades
        if request.headers.get("upgrade", "").lower() == "websocket":
            return await call_next(request)

        # Behind nginx request.client.host is always 127.0.0.1 — use the
        # nginx-set X-Real-IP (overwritten by proxy_set_header, not spoofable)
        # so each user gets their own bucket.
        client_ip = (
            request.headers.get("x-real-ip")
            or (request.client.host if request.client else "unknown")
        )
        now = _time_module.time()
        cutoff = now - self.window

        is_write = request.method in ("POST", "PUT", "PATCH", "DELETE")
        is_explorer = request.url.path.startswith("/api/explorer/")
        if is_explorer and not is_write:
            bucket = self._explorer_hits[client_ip]
            limit = self.explorer_limit
        else:
            bucket = self._write_hits[client_ip] if is_write else self._read_hits[client_ip]
            limit = self.write_limit if is_write else self.read_limit

        # Prune old entries
        bucket[:] = [t for t in bucket if t > cutoff]

        if len(bucket) >= limit:
            from fastapi.responses import JSONResponse
            return JSONResponse(
                status_code=429,
                content={"detail": f"Rate limit exceeded. Max {limit} requests per minute."},
                headers={"Retry-After": str(self.window)},
            )

        bucket.append(now)
        response = await call_next(request)
        # explorer cache headers (Part 5; density pass): under every
        # server-side TTL. Every explorer response is now public — the
        # connect-wallet gate is gone and no explorer payload varies by
        # Authorization, so a single public directive is the honest one.
        if is_explorer and not is_write and response.status_code == 200:
            response.headers.setdefault("Cache-Control", "public, max-age=120")
        return response


@asynccontextmanager
async def lifespan(app: FastAPI):
    # --- Startup ---
    logger.info("Starting Perpl Copy Trading Backend")

    # Security guard: never run with the default JWT secret. It signs app tokens
    # AND derives the Perpl-cookie encryption key — a default value forges tokens
    # and derives the encryption key. Refuse to start (prod already sets a real one).
    if settings.JWT_SECRET in ("", "change-me-in-production"):
        raise RuntimeError(
            "JWT_SECRET is unset or the insecure default. Set a strong JWT_SECRET in .env before starting."
        )

    # Initialize MySQL via SQLAlchemy
    await init_db(settings.DATABASE_URL)
    logger.info("Connected to MySQL database")

    # MCP token hardening migration: add expires_at + scopes columns if missing.
    # Idempotent — checks information_schema first so it works on any MySQL 8.x.
    try:
        from sqlalchemy import text
        sf = get_session_factory()
        async with sf() as session:
            existing = await session.execute(text(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_schema = DATABASE() AND table_name = 'mcp_tokens'"
            ))
            cols = {row[0].lower() for row in existing.fetchall()}
            if cols and "expires_at" not in cols:
                await session.execute(text(
                    "ALTER TABLE mcp_tokens ADD COLUMN expires_at DATETIME NULL"
                ))
                logger.info("mcp_tokens.expires_at added")
            if cols and "scopes" not in cols:
                await session.execute(text(
                    "ALTER TABLE mcp_tokens ADD COLUMN scopes VARCHAR(128) NULL"
                ))
                logger.info("mcp_tokens.scopes added")
            await session.commit()
    except Exception as exc:
        logger.warning("mcp_tokens migration skipped: %s", exc)

    # Copy Trading: live order tracking columns on copy_orders (idempotent).
    # Adds real-Perpl execution fields + widens mode/status for live_manual.
    try:
        from sqlalchemy import text
        sf = get_session_factory()
        async with sf() as session:
            existing = await session.execute(text(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_schema = DATABASE() AND table_name = 'copy_orders'"
            ))
            cols = {row[0].lower() for row in existing.fetchall()}
            if cols:  # only if the table already exists (else create_all builds it)
                add = {
                    "action": "VARCHAR(16) NOT NULL DEFAULT 'open'",
                    "live_position_id": "INT NULL",
                    "leverage": "DECIMAL(12,4) NULL",
                    "allocation_usd": "DECIMAL(20,8) NULL",
                    "perpl_request_id": "VARCHAR(80) NULL",
                    "perpl_order_id": "VARCHAR(80) NULL",
                    "perpl_fill_id": "VARCHAR(80) NULL",
                    "fill_price": "DECIMAL(30,10) NULL",
                    "fill_size": "DECIMAL(30,10) NULL",
                    "submitted_at": "DATETIME NULL",
                    "filled_at": "DATETIME NULL",
                    "error_message": "TEXT NULL",
                }
                for col, ddl in add.items():
                    if col not in cols:
                        await session.execute(text(f"ALTER TABLE copy_orders ADD COLUMN {col} {ddl}"))
                        logger.info("copy_orders.%s added", col)
                # widen mode (10->12) and status (12->20) for live_manual statuses
                await session.execute(text("ALTER TABLE copy_orders MODIFY COLUMN mode VARCHAR(12) NOT NULL DEFAULT 'paper'"))
                await session.execute(text("ALTER TABLE copy_orders MODIFY COLUMN status VARCHAR(20) NOT NULL DEFAULT 'simulated'"))
            # widen copy_subscriptions.mode (10->12) so 'live_manual' (11 chars) fits
            sub_cols = await session.execute(text(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_schema = DATABASE() AND table_name = 'copy_subscriptions'"
            ))
            if {row[0].lower() for row in sub_cols.fetchall()}:
                await session.execute(text("ALTER TABLE copy_subscriptions MODIFY COLUMN mode VARCHAR(12) NOT NULL DEFAULT 'live_manual'"))
            await session.commit()
    except Exception as exc:
        logger.warning("copy_orders migration skipped: %s", exc)

    # --- migration v2: exchange dimension (2026-08) -------------------------
    # Adds multi-exchange support (Hyperliquid leaderboard integration):
    #   * `exchange` column (DEFAULT 'perpl' — every existing row/legacy write
    #     stays Perpl-scoped and every existing query behaves identically)
    #   * trader_profiles: (exchange, wallet) identity + admin is_hidden
    #   * watchlists: unique pair widened with exchange
    #   * leaderboard_snapshots: (exchange, period, timestamp) index
    #   * market_map: HL native symbol -> Perpl market id (seeded below)
    # Every statement is guarded against information_schema, safe to re-run.
    try:
        from sqlalchemy import text
        sf = get_session_factory()
        async with sf() as session:
            async def _cols(table: str) -> set[str]:
                res = await session.execute(text(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_schema = DATABASE() AND table_name = :t"), {"t": table})
                return {r[0].lower() for r in res.fetchall()}

            async def _indexes(table: str) -> set[str]:
                res = await session.execute(text(
                    "SELECT DISTINCT index_name FROM information_schema.statistics "
                    "WHERE table_schema = DATABASE() AND table_name = :t"), {"t": table})
                return {r[0] for r in res.fetchall()}

            exchange_tables = [
                "leaderboard_snapshots", "trader_profiles", "watchlists",
                "copy_subscriptions", "leader_trade_events", "trader_activity",
            ]
            for tbl in exchange_tables:
                cols = await _cols(tbl)
                if cols and "exchange" not in cols:
                    await session.execute(text(
                        f"ALTER TABLE {tbl} ADD COLUMN exchange VARCHAR(20) NOT NULL DEFAULT 'perpl'"
                    ))
                    logger.info("migration v2: %s.exchange added", tbl)

            # symbol width: HL native symbols (e.g. "xyz:BRENTOIL") exceed 10
            for tbl in ("leader_trade_events", "trader_activity"):
                if await _cols(tbl):
                    await session.execute(text(
                        f"ALTER TABLE {tbl} MODIFY COLUMN symbol VARCHAR(20) NOT NULL"
                    ))

            # copy_subscriptions.max_basis_bps (phase 5 basis gate)
            cs_cols = await _cols("copy_subscriptions")
            if cs_cols and "max_basis_bps" not in cs_cols:
                await session.execute(text(
                    "ALTER TABLE copy_subscriptions ADD COLUMN max_basis_bps INT NULL"
                ))
                logger.info("migration v2: copy_subscriptions.max_basis_bps added")

            # trader_profiles: is_hidden / hidden_at + identity unique swap
            tp_cols = await _cols("trader_profiles")
            if tp_cols:
                if "is_hidden" not in tp_cols:
                    await session.execute(text(
                        "ALTER TABLE trader_profiles ADD COLUMN is_hidden TINYINT(1) NOT NULL DEFAULT 0"
                    ))
                    logger.info("migration v2: trader_profiles.is_hidden added")
                if "hidden_at" not in tp_cols:
                    await session.execute(text(
                        "ALTER TABLE trader_profiles ADD COLUMN hidden_at DATETIME NULL"
                    ))
                    logger.info("migration v2: trader_profiles.hidden_at added")
                idx = await _indexes("trader_profiles")
                if "uq_trader_profile_exch_wallet" not in idx:
                    # find + drop the old single-column unique on wallet_address
                    res = await session.execute(text(
                        "SELECT index_name FROM information_schema.statistics "
                        "WHERE table_schema = DATABASE() AND table_name = 'trader_profiles' "
                        "AND non_unique = 0 AND index_name <> 'PRIMARY' "
                        "GROUP BY index_name "
                        "HAVING COUNT(*) = 1 AND MAX(column_name) = 'wallet_address'"
                    ))
                    for (old_idx,) in res.fetchall():
                        await session.execute(text(
                            f"ALTER TABLE trader_profiles DROP INDEX `{old_idx}`"))
                        logger.info("migration v2: dropped old unique %s", old_idx)
                    await session.execute(text(
                        "ALTER TABLE trader_profiles ADD CONSTRAINT uq_trader_profile_exch_wallet "
                        "UNIQUE (exchange, wallet_address)"
                    ))
                    logger.info("migration v2: trader_profiles unique(exchange, wallet) added")

            # watchlists: widen the unique pair with exchange
            wl_idx = await _indexes("watchlists")
            if wl_idx and "uq_watchlist_pair_exch" not in wl_idx:
                if "uq_watchlist_pair" in wl_idx:
                    await session.execute(text(
                        "ALTER TABLE watchlists DROP INDEX uq_watchlist_pair"))
                await session.execute(text(
                    "ALTER TABLE watchlists ADD CONSTRAINT uq_watchlist_pair_exch "
                    "UNIQUE (follower_wallet, trader_wallet, exchange)"
                ))
                logger.info("migration v2: watchlists unique widened with exchange")

            # leaderboard_snapshots: query index for per-exchange windows
            lb_idx = await _indexes("leaderboard_snapshots")
            if lb_idx and "ix_lb_snap_exch_period_ts" not in lb_idx:
                await session.execute(text(
                    "CREATE INDEX ix_lb_snap_exch_period_ts "
                    "ON leaderboard_snapshots (exchange, period, timestamp)"
                ))
                logger.info("migration v2: leaderboard_snapshots exchange index added")

            # market_map seed (table itself is created by create_all above).
            # HL coins -> Perpl market ids; MON has no HL market, unmapped HL
            # coins simply have no row. INSERT ... SELECT guards make re-runs no-ops.
            if await _cols("market_map"):
                seed = [("hl", "BTC", 1, "BTC"), ("hl", "ETH", 20, "ETH"),
                        ("hl", "SOL", 31, "SOL"), ("hl", "HYPE", 40, "HYPE"),
                        ("hl", "ZEC", 50, "ZEC")]
                for exch, native, pmid, canon in seed:
                    await session.execute(text(
                        "INSERT INTO market_map (exchange, native_symbol, perpl_market_id, canonical_symbol) "
                        "SELECT :e, :n, :p, :c FROM DUAL WHERE NOT EXISTS "
                        "(SELECT 1 FROM market_map WHERE exchange = :e AND native_symbol = :n)"
                    ), {"e": exch, "n": native, "p": pmid, "c": canon})
            await session.commit()
            logger.info("migration v2 (exchange dimension) complete")
    except Exception as exc:
        logger.warning("migration v2 (exchange dimension) FAILED: %s", exc)

    # --- migration v3: persistent HL open-date cache (2026-08-12) ------------
    # One small additive table; a (wallet, coin, side_sign) position streak is
    # dated once ever (PROFILE_QUALITY_REPORT A2). Guarded: creates only when
    # missing, logs exactly once on the boot that creates it.
    try:
        from sqlalchemy import text
        sf = get_session_factory()
        async with sf() as session:
            exists = (await session.execute(text(
                "SELECT COUNT(*) FROM information_schema.tables "
                "WHERE table_schema = DATABASE() AND table_name = 'hl_position_open_cache'"
            ))).scalar()
            if not exists:
                await session.execute(text(
                    "CREATE TABLE hl_position_open_cache ("
                    "  id INT AUTO_INCREMENT PRIMARY KEY,"
                    "  wallet VARCHAR(42) NOT NULL,"
                    "  coin VARCHAR(20) NOT NULL,"
                    "  side_sign TINYINT NOT NULL,"
                    "  opened_at DATETIME NULL,"
                    "  opened_before DATETIME NULL,"
                    "  source VARCHAR(10) NULL,"
                    "  resolved_at DATETIME NOT NULL,"
                    "  UNIQUE KEY uq_hl_open_cache (wallet, coin, side_sign)"
                    ")"
                ))
                await session.commit()
                logger.info("migration v3: hl_position_open_cache created")
    except Exception as exc:
        logger.warning("migration v3 (hl open-date cache) FAILED: %s", exc)

    # --- migration v4: live copy execution v2 (2026-08-14) -------------------
    # Additive columns on copy_orders: order shape (market|limit), intended
    # TP/SL trigger prices, and the real Perpl trigger order ids once placed.
    # Guarded per-column via information_schema; logs once per column added.
    try:
        from sqlalchemy import text
        v4_cols = [
            ("order_type", "VARCHAR(10) NULL"),
            ("tp_price", "DECIMAL(30,10) NULL"),
            ("sl_price", "DECIMAL(30,10) NULL"),
            ("tp_order_id", "VARCHAR(80) NULL"),
            ("sl_order_id", "VARCHAR(80) NULL"),
        ]
        sf = get_session_factory()
        async with sf() as session:
            for col, ddl in v4_cols:
                exists = (await session.execute(text(
                    "SELECT COUNT(*) FROM information_schema.columns "
                    "WHERE table_schema = DATABASE() AND table_name = 'copy_orders' "
                    f"AND column_name = '{col}'"
                ))).scalar()
                if not exists:
                    await session.execute(text(
                        f"ALTER TABLE copy_orders ADD COLUMN {col} {ddl}"))
                    await session.commit()
                    logger.info("migration v4: copy_orders.%s added", col)
    except Exception as exc:
        logger.warning("migration v4 (live copy v2 columns) FAILED: %s", exc)

    # --- migration v5: trader recent-activity timestamp (2026-08-14) ---------
    # trader_profiles.last_fill_at (UTC, nullable) + composite index for the
    # recent_activity leaderboard sort. Guarded via information_schema.
    try:
        from sqlalchemy import text
        sf = get_session_factory()
        async with sf() as session:
            col = (await session.execute(text(
                "SELECT COUNT(*) FROM information_schema.columns "
                "WHERE table_schema = DATABASE() AND table_name = 'trader_profiles' "
                "AND column_name = 'last_fill_at'"
            ))).scalar()
            if not col:
                await session.execute(text(
                    "ALTER TABLE trader_profiles ADD COLUMN last_fill_at DATETIME NULL"))
                await session.commit()
                logger.info("migration v5: trader_profiles.last_fill_at added")
            idx = (await session.execute(text(
                "SELECT COUNT(*) FROM information_schema.statistics "
                "WHERE table_schema = DATABASE() AND table_name = 'trader_profiles' "
                "AND index_name = 'ix_trader_profiles_last_fill'"
            ))).scalar()
            if not idx:
                await session.execute(text(
                    "CREATE INDEX ix_trader_profiles_last_fill "
                    "ON trader_profiles (exchange, last_fill_at)"))
                await session.commit()
                logger.info("migration v5: ix_trader_profiles_last_fill added")
    except Exception as exc:
        logger.warning("migration v5 (last_fill_at) FAILED: %s", exc)

    # --- migration v6: trader activity analytics (2026-08-19) ----------------
    # Three NEW tables (created by create_all above from their models):
    # trader_activity_metrics (Tier-1 snapshot-derived), trader_fill_stats
    # (Tier-2 fills-based), hl_fill_events (rolling raw-fill store, 8d).
    # This block verifies presence and logs exactly once per created table.
    try:
        from sqlalchemy import text
        sf = get_session_factory()
        async with sf() as session:
            for tbl in ("trader_activity_metrics", "trader_fill_stats", "hl_fill_events"):
                n = (await session.execute(text(
                    "SELECT COUNT(*) FROM information_schema.tables "
                    "WHERE table_schema = DATABASE() AND table_name = :t"
                ), {"t": tbl})).scalar()
                if not n:
                    logger.error("migration v6: %s MISSING after create_all", tbl)
                elif not getattr(app.state, "_v6_logged", False):
                    logger.info("migration v6: %s present", tbl)
        app.state._v6_logged = True
    except Exception as exc:
        logger.warning("migration v6 (analytics tables) check FAILED: %s", exc)

    # --- migration v7: unprotected-position marker (2026-08-19, audit A1) ----
    try:
        from sqlalchemy import text
        sf = get_session_factory()
        async with sf() as session:
            n = (await session.execute(text(
                "SELECT COUNT(*) FROM information_schema.columns "
                "WHERE table_schema = DATABASE() AND table_name = 'copy_live_positions' "
                "AND column_name = 'triggers_failed'"
            ))).scalar()
            if not n:
                await session.execute(text(
                    "ALTER TABLE copy_live_positions "
                    "ADD COLUMN triggers_failed TINYINT(1) NOT NULL DEFAULT 0"))
                await session.commit()
                logger.info("migration v7: copy_live_positions.triggers_failed added")
    except Exception as exc:
        logger.warning("migration v7 (triggers_failed) FAILED: %s", exc)

    # --- migration v8: smart-money analytics tables (2026-08-26, Phase 1) ----
    # Guarded inline DDL: CREATE TABLE IF NOT EXISTS x3 (positions snapshot
    # store, unified flow-event stream, per-cycle asset rollups). Raw SQL — the
    # analytics services use text() queries exclusively, no ORM models needed.
    try:
        from sqlalchemy import text
        sf = get_session_factory()
        async with sf() as session:
            await session.execute(text(
                "CREATE TABLE IF NOT EXISTS analytics_positions ("
                " cycle_ts DATETIME NOT NULL,"
                " wallet VARCHAR(42) NOT NULL,"
                " asset VARCHAR(20) NOT NULL,"
                " side VARCHAR(5) NOT NULL,"
                " size DOUBLE NOT NULL,"
                " notional DOUBLE NOT NULL,"
                " entry_px DOUBLE NULL,"
                " leverage DOUBLE NULL,"
                " liq_px DOUBLE NULL,"
                " upnl DOUBLE NULL,"
                " margin_mode VARCHAR(8) NULL,"
                " source VARCHAR(6) NOT NULL,"
                " PRIMARY KEY (cycle_ts, wallet, asset),"
                " INDEX ix_ap_asset_cycle (asset, cycle_ts)"
                ") ENGINE=InnoDB DEFAULT CHARSET=utf8mb4"))
            await session.execute(text(
                "CREATE TABLE IF NOT EXISTS analytics_flow_events ("
                " id BIGINT NOT NULL AUTO_INCREMENT,"
                " detected_at DATETIME(3) NOT NULL,"
                " wallet VARCHAR(42) NOT NULL,"
                " asset VARCHAR(20) NOT NULL,"
                " event_type VARCHAR(8) NOT NULL,"
                " side VARCHAR(5) NOT NULL,"
                " size_before DOUBLE NULL,"
                " size_after DOUBLE NULL,"
                " notional_delta DOUBLE NULL,"
                " ref_px DOUBLE NULL,"
                " ref_px_approx TINYINT(1) NOT NULL DEFAULT 0,"
                " resolution VARCHAR(3) NOT NULL,"
                " src_event_id BIGINT NULL,"
                " PRIMARY KEY (id),"
                " UNIQUE KEY ux_afe_src_event (src_event_id),"
                " INDEX ix_afe_asset_time (asset, detected_at),"
                " INDEX ix_afe_wallet_time (wallet, detected_at)"
                ") ENGINE=InnoDB DEFAULT CHARSET=utf8mb4"))
            await session.execute(text(
                "CREATE TABLE IF NOT EXISTS analytics_asset_rollups ("
                " cycle_ts DATETIME NOT NULL,"
                " asset VARCHAR(20) NOT NULL,"
                " cohort_variant VARCHAR(4) NOT NULL,"
                " wallets_long INT NOT NULL DEFAULT 0,"
                " wallets_short INT NOT NULL DEFAULT 0,"
                " notional_long DOUBLE NOT NULL DEFAULT 0,"
                " notional_short DOUBLE NOT NULL DEFAULT 0,"
                " avg_lev_long DOUBLE NULL,"
                " avg_lev_short DOUBLE NULL,"
                " upnl_long DOUBLE NOT NULL DEFAULT 0,"
                " upnl_short DOUBLE NOT NULL DEFAULT 0,"
                " cohort_oi DOUBLE NOT NULL DEFAULT 0,"
                " venue_oi DOUBLE NULL,"
                " venue_funding DOUBLE NULL,"
                " fresh_pct_24h DOUBLE NULL,"
                " flags JSON NULL,"
                " PRIMARY KEY (cycle_ts, asset, cohort_variant),"
                " INDEX ix_aar_asset_cycle (asset, cycle_ts)"
                ") ENGINE=InnoDB DEFAULT CHARSET=utf8mb4"))
            await session.commit()
            if not getattr(app.state, "_v8_logged", False):
                logger.info("migration v8: analytics tables present "
                            "(positions/flow_events/asset_rollups)")
        app.state._v8_logged = True
    except Exception as exc:
        logger.warning("migration v8 (analytics tables) FAILED: %s", exc)

    # --- migration v9: Smart Money Index store (2026-08-26, Phase 2.1) ------
    try:
        from sqlalchemy import text
        sf = get_session_factory()
        async with sf() as session:
            await session.execute(text(
                "CREATE TABLE IF NOT EXISTS analytics_smi ("
                " cycle_ts DATETIME NOT NULL,"
                " asset VARCHAR(20) NOT NULL,"
                " smi DOUBLE NOT NULL,"
                " c1 DOUBLE NOT NULL,"
                " c2 DOUBLE NOT NULL,"
                " c3 DOUBLE NOT NULL,"
                " c4 DOUBLE NOT NULL,"
                " c5 DOUBLE NOT NULL,"
                " inputs JSON NULL,"
                " PRIMARY KEY (cycle_ts, asset),"
                " INDEX ix_smi_asset_cycle (asset, cycle_ts)"
                ") ENGINE=InnoDB DEFAULT CHARSET=utf8mb4"))
            await session.commit()
            if not getattr(app.state, "_v9_logged", False):
                logger.info("migration v9: analytics_smi present")
        app.state._v9_logged = True
    except Exception as exc:
        logger.warning("migration v9 (analytics_smi) FAILED: %s", exc)

    # --- migration v10: Phase-3 depth stores (2026-08-26) --------------------
    # wallet_state: account value per wallet per cycle (conviction meter +
    # account-value band filter). trigger_obs: resting TP/SL observed in the
    # foreground profile cache at sweep time ('chk' rows = wallets checked,
    # so trigger prevalence has an honest denominator).
    try:
        from sqlalchemy import text
        sf = get_session_factory()
        async with sf() as session:
            await session.execute(text(
                "CREATE TABLE IF NOT EXISTS analytics_wallet_state ("
                " cycle_ts DATETIME NOT NULL,"
                " wallet VARCHAR(42) NOT NULL,"
                " account_value DOUBLE NULL,"
                " gross_notional DOUBLE NOT NULL DEFAULT 0,"
                " n_assets INT NOT NULL DEFAULT 0,"
                " PRIMARY KEY (cycle_ts, wallet),"
                " INDEX ix_aws_wallet_cycle (wallet, cycle_ts)"
                ") ENGINE=InnoDB DEFAULT CHARSET=utf8mb4"))
            await session.execute(text(
                "CREATE TABLE IF NOT EXISTS analytics_trigger_obs ("
                " id BIGINT NOT NULL AUTO_INCREMENT,"
                " observed_at DATETIME NOT NULL,"
                " wallet VARCHAR(42) NOT NULL,"
                " asset VARCHAR(20) NOT NULL,"
                " side VARCHAR(5) NOT NULL,"
                " kind VARCHAR(3) NOT NULL,"
                " trigger_px DOUBLE NOT NULL DEFAULT 0,"
                " size DOUBLE NULL,"
                " PRIMARY KEY (id),"
                " INDEX ix_ato_asset_time (asset, observed_at),"
                " INDEX ix_ato_wallet (wallet)"
                ") ENGINE=InnoDB DEFAULT CHARSET=utf8mb4"))
            await session.commit()
            if not getattr(app.state, "_v10_logged", False):
                logger.info("migration v10: analytics_wallet_state + "
                            "analytics_trigger_obs present")
        app.state._v10_logged = True
    except Exception as exc:
        logger.warning("migration v10 (phase-3 depth tables) FAILED: %s", exc)

    # --- migration v11: SMI validation pipeline (Tier-2 Part A) --------------
    # price_history: one venue mark per asset per sweep cycle (written by the
    # sweep from its existing 60s venue cache — zero new venue calls).
    # smi_outcomes: forward returns per SMI observation, filled only once the
    # horizon elapsed. smi_stats: the nightly study snapshot. Publication of
    # any of it is gated server-side in services/analytics/smi_study.py.
    try:
        from sqlalchemy import text
        sf = get_session_factory()
        async with sf() as session:
            await session.execute(text(
                "CREATE TABLE IF NOT EXISTS analytics_price_history ("
                " cycle_ts DATETIME NOT NULL,"
                " asset VARCHAR(20) NOT NULL,"
                " mark DOUBLE NOT NULL,"
                " source VARCHAR(8) NOT NULL DEFAULT 'venue',"
                " PRIMARY KEY (asset, cycle_ts)"
                ") ENGINE=InnoDB DEFAULT CHARSET=utf8mb4"))
            await session.execute(text(
                "CREATE TABLE IF NOT EXISTS analytics_smi_outcomes ("
                " cycle_ts DATETIME NOT NULL,"
                " asset VARCHAR(20) NOT NULL,"
                " smi DOUBLE NOT NULL,"
                " c1 DOUBLE NOT NULL, c2 DOUBLE NOT NULL, c3 DOUBLE NOT NULL,"
                " c4 DOUBLE NOT NULL, c5 DOUBLE NOT NULL,"
                " ret_4h DOUBLE NULL, ret_24h DOUBLE NULL, ret_72h DOUBLE NULL,"
                " done TINYINT(1) NOT NULL DEFAULT 0,"
                " PRIMARY KEY (asset, cycle_ts),"
                " INDEX ix_aso_done (done, cycle_ts)"
                ") ENGINE=InnoDB DEFAULT CHARSET=utf8mb4"))
            await session.execute(text(
                "CREATE TABLE IF NOT EXISTS analytics_smi_stats ("
                " asset VARCHAR(20) NOT NULL,"
                " kind VARCHAR(16) NOT NULL,"
                " bucket VARCHAR(16) NOT NULL,"
                " horizon VARCHAR(4) NOT NULL,"
                " n INT NOT NULL DEFAULT 0,"
                " hit_n INT NULL,"
                " hit_rate DOUBLE NULL,"
                " mean_ret DOUBLE NULL,"
                " median_ret DOUBLE NULL,"
                " corr DOUBLE NULL,"
                " computed_at DATETIME NOT NULL,"
                " PRIMARY KEY (asset, kind, bucket, horizon)"
                ") ENGINE=InnoDB DEFAULT CHARSET=utf8mb4"))
            await session.commit()
            if not getattr(app.state, "_v11_logged", False):
                logger.info("migration v11: analytics_price_history + "
                            "analytics_smi_outcomes + analytics_smi_stats present")
        app.state._v11_logged = True
    except Exception as exc:
        logger.warning("migration v11 (SMI validation tables) FAILED: %s", exc)

    # --- migration v12: cohort precision (Tier-2 Part C) ---------------------
    # wallet_flags: three-valued hedger verdict (+evidence) per wallet;
    # wallet_clusters: same-entity clustering from fill-timing evidence.
    try:
        from sqlalchemy import text
        sf = get_session_factory()
        async with sf() as session:
            await session.execute(text(
                "CREATE TABLE IF NOT EXISTS analytics_wallet_flags ("
                " wallet VARCHAR(42) NOT NULL,"
                " is_likely_hedger TINYINT(1) NULL,"
                " hedger_evidence JSON NULL,"
                " hedger_checked_at DATETIME NULL,"
                " PRIMARY KEY (wallet)"
                ") ENGINE=InnoDB DEFAULT CHARSET=utf8mb4"))
            await session.execute(text(
                "CREATE TABLE IF NOT EXISTS analytics_wallet_clusters ("
                " cluster_id INT NOT NULL,"
                " wallet VARCHAR(42) NOT NULL,"
                " confidence DOUBLE NOT NULL,"
                " evidence JSON NULL,"
                " computed_at DATETIME NOT NULL,"
                " PRIMARY KEY (wallet),"
                " INDEX ix_awc_cluster (cluster_id)"
                ") ENGINE=InnoDB DEFAULT CHARSET=utf8mb4"))
            await session.commit()
            if not getattr(app.state, "_v12_logged", False):
                logger.info("migration v12: analytics_wallet_flags + "
                            "analytics_wallet_clusters present")
        app.state._v12_logged = True
    except Exception as exc:
        logger.warning("migration v12 (cohort precision tables) FAILED: %s", exc)

    # --- migration v13: wallet quality metrics (Tier-2 Part D) ---------------
    # Guarded ALTER: profit factor / avg win-loss / max drawdown / flip
    # accuracy columns on trader_fill_stats (computed from fills already
    # sampled — zero new venue calls).
    try:
        from sqlalchemy import text
        sf = get_session_factory()
        async with sf() as session:
            n = (await session.execute(text(
                "SELECT COUNT(*) FROM information_schema.columns "
                "WHERE table_schema = DATABASE() AND table_name = 'trader_fill_stats' "
                "AND column_name = 'profit_factor'"))).scalar()
            if not n:
                await session.execute(text(
                    "ALTER TABLE trader_fill_stats "
                    "ADD COLUMN profit_factor DECIMAL(12,4) NULL,"
                    "ADD COLUMN avg_win DECIMAL(20,8) NULL,"
                    "ADD COLUMN avg_loss DECIMAL(20,8) NULL,"
                    "ADD COLUMN avg_win_loss_ratio DECIMAL(12,4) NULL,"
                    "ADD COLUMN max_drawdown_7d DECIMAL(20,8) NULL,"
                    "ADD COLUMN max_drawdown_pct_7d DECIMAL(12,4) NULL,"
                    "ADD COLUMN flip_accuracy DECIMAL(12,4) NULL,"
                    "ADD COLUMN flip_n INT NULL"))
                await session.commit()
                logger.info("migration v13: trader_fill_stats quality columns added")
    except Exception as exc:
        logger.warning("migration v13 (quality metrics columns) FAILED: %s", exc)

    # --- migration v14: strat_trades.exit_reason 64 -> 128 (2026-09-04) -----
    # The s05c chase marker 'pending:hold_h=..:ttl_m=..:chase=1:rq=..:rq_ts=..:
    # entry=taker' is 69 chars; the taker-cross UPDATE hit "Data too long"
    # (1406) on prod. Guarded MODIFY, applied by hand on prod the same hour.
    try:
        from sqlalchemy import text
        sf = get_session_factory()
        async with sf() as session:
            n = (await session.execute(text(
                "SELECT CHARACTER_MAXIMUM_LENGTH FROM information_schema.columns "
                "WHERE table_schema = DATABASE() AND table_name = 'strat_trades' "
                "AND column_name = 'exit_reason'"))).scalar()
            if n is not None and int(n) < 128:
                await session.execute(text(
                    "ALTER TABLE strat_trades MODIFY exit_reason VARCHAR(128) NULL"))
                await session.commit()
                logger.info("migration v14: strat_trades.exit_reason widened to 128")
    except Exception as exc:
        logger.warning("migration v14 (exit_reason width) FAILED: %s", exc)

    # --- migration v15: models M1-M6 / Mind columns (docs 10-16, 2026-09-06) --
    # Shared with the strategy worker (app/db/migrations_models.py) so either
    # process brings the schema forward.
    try:
        from app.db.migrations_models import apply_v15, apply_v16, apply_v17
        sf = get_session_factory()
        async with sf() as session:
            if await apply_v15(session):
                logger.info("migration v15: strat_signals/strat_trades Mind columns applied")
            if await apply_v16(session):
                logger.info("migration v16: strat_liquidations.source applied")
            if await apply_v17(session):
                logger.info("migration v17: spec v1.3 reclaim/stop-floor/post-only columns applied")
    except Exception as exc:
        logger.warning("migration v15/v16/v17 FAILED: %s", exc)

    # --- migration v18: wallet explorer address metadata (Part 2) -----------
    # userRole costs weight 60 — resolved ONCE per address ever, persisted.
    try:
        from sqlalchemy import text
        sf = get_session_factory()
        async with sf() as session:
            await session.execute(text(
                "CREATE TABLE IF NOT EXISTS explorer_address_meta ("
                " address VARCHAR(42) NOT NULL,"
                " role VARCHAR(16) NOT NULL,"
                " master_address VARCHAR(42) NULL,"
                " raw JSON NULL,"
                " checked_at DATETIME NOT NULL,"
                " PRIMARY KEY (address)"
                ") ENGINE=InnoDB DEFAULT CHARSET=utf8mb4"))
            await session.commit()
            if not getattr(app.state, "_v18_logged", False):
                logger.info("migration v18: explorer_address_meta present")
        app.state._v18_logged = True
    except Exception as exc:
        logger.warning("migration v18 (explorer_address_meta) FAILED: %s", exc)

    # --- migration v19: Perpl account id cached forever (explorer Part 3) ----
    try:
        from sqlalchemy import text
        sf = get_session_factory()
        async with sf() as session:
            n = (await session.execute(text(
                "SELECT COUNT(*) FROM information_schema.columns "
                "WHERE table_schema = DATABASE() AND table_name = 'explorer_address_meta' "
                "AND column_name = 'perpl_account_id'"))).scalar()
            if not n:
                await session.execute(text(
                    "ALTER TABLE explorer_address_meta "
                    "ADD COLUMN perpl_account_id BIGINT NULL"))
                await session.commit()
                logger.info("migration v19: explorer_address_meta.perpl_account_id added")
    except Exception as exc:
        logger.warning("migration v19 (perpl_account_id) FAILED: %s", exc)

    # --- migration v20: Perpl event indexer sink (explorer Part 4) -----------
    # Mirrors indexer/perpl_indexer.py ensure_tables() — either process
    # brings the schema forward (same pattern as the strategy migrations).
    try:
        from sqlalchemy import text
        sf = get_session_factory()
        async with sf() as session:
            await session.execute(text(
                "CREATE TABLE IF NOT EXISTS perpl_events ("
                " id BIGINT AUTO_INCREMENT PRIMARY KEY,"
                " at DATETIME NOT NULL,"
                " block BIGINT NOT NULL,"
                " tx VARCHAR(66) NOT NULL,"
                " log_index INT NOT NULL,"
                " account_id BIGINT NULL,"
                " attributed TINYINT(1) NOT NULL DEFAULT 0,"
                " address VARCHAR(42) NULL,"
                " tx_from VARCHAR(42) NULL,"
                " market_id INT NULL,"
                " event_name VARCHAR(48) NOT NULL,"
                " event_type VARCHAR(32) NOT NULL,"
                " amount DECIMAL(30,8) NULL,"
                " balance_after DECIMAL(30,8) NULL,"
                " fee DECIMAL(30,8) NULL,"
                " bfa DECIMAL(30,8) NULL,"
                " raw JSON NULL,"
                " UNIQUE KEY uq_pe_txlog (tx, log_index),"
                " INDEX ix_pe_addr_at (address, at),"
                " INDEX ix_pe_acct_at (account_id, at),"
                " INDEX ix_pe_block (block),"
                " INDEX ix_pe_txfrom (tx_from)"
                ") ENGINE=InnoDB DEFAULT CHARSET=utf8mb4"))
            await session.execute(text(
                "CREATE TABLE IF NOT EXISTS perpl_addr_map ("
                " account_id BIGINT NOT NULL,"
                " address VARCHAR(42) NOT NULL,"
                " first_seen_block BIGINT NOT NULL,"
                " first_seen_at DATETIME NOT NULL,"
                " PRIMARY KEY (account_id),"
                " INDEX ix_pam_addr (address)"
                ") ENGINE=InnoDB DEFAULT CHARSET=utf8mb4"))
            await session.execute(text(
                "CREATE TABLE IF NOT EXISTS perpl_indexer_state ("
                " id TINYINT NOT NULL,"
                " last_block BIGINT NOT NULL DEFAULT 0,"
                " head_block BIGINT NULL,"
                " first_event_block BIGINT NULL,"
                " note VARCHAR(128) NULL,"
                " updated_at DATETIME NOT NULL,"
                " PRIMARY KEY (id)"
                ") ENGINE=InnoDB DEFAULT CHARSET=utf8mb4"))
            await session.execute(text(
                "INSERT IGNORE INTO perpl_indexer_state (id, last_block, updated_at) "
                "VALUES (1, 0, UTC_TIMESTAMP())"))
            await session.commit()
            if not getattr(app.state, "_v20_logged", False):
                logger.info("migration v20: perpl_events + perpl_addr_map + "
                            "perpl_indexer_state present")
        app.state._v20_logged = True
    except Exception as exc:
        logger.warning("migration v20 (perpl indexer tables) FAILED: %s", exc)

    # --- migration v21: widen strat_strategy_state mode columns (D-100) -------
    # `paper-paused` (12 chars) does not fit the original varchar(8). The kill
    # switch cannot be enforced until the column can hold the state it produces.
    try:
        async with engine.begin() as conn:
            row = (await conn.execute(text(
                "SELECT CHARACTER_MAXIMUM_LENGTH FROM information_schema.columns "
                "WHERE table_schema=DATABASE() AND table_name='strat_strategy_state' "
                "AND column_name='effective_mode'"))).first()
            if row and int(row[0]) < 16:
                await conn.execute(text(
                    "ALTER TABLE strat_strategy_state "
                    "MODIFY effective_mode VARCHAR(16) NOT NULL, "
                    "MODIFY requested_mode VARCHAR(16) NOT NULL"))
                logger.info("migration v21: strat_strategy_state mode columns widened to VARCHAR(16)")
            elif not getattr(app.state, "_v21_logged", False):
                logger.info("migration v21: strat_strategy_state mode columns already >= 16")
        app.state._v21_logged = True
    except Exception as exc:
        logger.warning("migration v21 (widen mode columns) FAILED: %s", exc)

    # --- migration v22: strat_book_5s.spread (D-108) -------------------------
    # The regime gate's `spread_wide` condition never fired because the quote
    # spread was never stored. hl_ws computed best_bid/best_ask and dropped them.
    try:
        async with engine.begin() as conn:
            row = (await conn.execute(text(
                "SELECT COUNT(*) FROM information_schema.columns "
                "WHERE table_schema=DATABASE() AND table_name='strat_book_5s' "
                "AND column_name='spread'"))).first()
            if not row or int(row[0]) == 0:
                await conn.execute(text("ALTER TABLE strat_book_5s ADD COLUMN spread DOUBLE NULL"))
                logger.info("migration v22: strat_book_5s.spread added")
            elif not getattr(app.state, "_v22_logged", False):
                logger.info("migration v22: strat_book_5s.spread present")
        app.state._v22_logged = True
    except Exception as exc:
        logger.warning("migration v22 (book spread) FAILED: %s", exc)

    # --- migration v23: analytics_flow_events(detected_at) index (D-113) -----
    # The table had (asset, detected_at) and (wallet, detected_at) only. Every
    # query that filters on detected_at ALONE — /movers, /flows, /pulse — could
    # use neither, so MySQL full-scanned 2,183,263 rows with a filesort to
    # return 12,692. EXPLAIN after: type=range, key=ix_afe_time, rows=23,816.
    try:
        async with engine.begin() as conn:
            row = (await conn.execute(text(
                "SELECT COUNT(*) FROM information_schema.statistics "
                "WHERE table_schema=DATABASE() AND table_name='analytics_flow_events' "
                "AND index_name='ix_afe_time'"))).first()
            if not row or int(row[0]) == 0:
                # ONLINE and fail-fast: never queue behind a long transaction
                # and block the table for everyone else (D-112).
                await conn.execute(text("SET SESSION lock_wait_timeout=15"))
                await conn.execute(text(
                    "ALTER TABLE analytics_flow_events ADD INDEX ix_afe_time (detected_at), "
                    "ALGORITHM=INPLACE, LOCK=NONE"))
                logger.info("migration v23: analytics_flow_events.ix_afe_time added")
            elif not getattr(app.state, "_v23_logged", False):
                logger.info("migration v23: analytics_flow_events.ix_afe_time present")
        app.state._v23_logged = True
    except Exception as exc:
        logger.warning("migration v23 (flow events time index) FAILED: %s", exc)

    # Startup cleanup: liquidation_snapshots only needs the latest few rows per
    # market. The periodic pruning in liquidation_calc keeps it tight going
    # forward, but if rows accumulated (e.g. from before the pruning was added),
    # do a simple batched delete. Uses per-market MAX(id) so it works without
    # temp tables (safe on low-disk servers).
    try:
        from sqlalchemy import text
        sf = get_session_factory()
        async with sf() as session:
            row_count = await session.execute(text(
                "SELECT COUNT(*) FROM liquidation_snapshots"
            ))
            total = row_count.scalar() or 0
            if total > 100:
                # Find the cutoff: keep top 5 per market. The simplest safe
                # approach: get each market's 5th-newest id, delete below that.
                cutoffs = await session.execute(text(
                    "SELECT market_id, id FROM ("
                    "  SELECT market_id, id, ROW_NUMBER() OVER "
                    "    (PARTITION BY market_id ORDER BY id DESC) AS rn "
                    "  FROM liquidation_snapshots"
                    ") ranked WHERE rn = 5"
                ))
                for market_id, cutoff_id in cutoffs.fetchall():
                    await session.execute(text(
                        "DELETE FROM liquidation_snapshots "
                        "WHERE market_id = :mid AND id < :cid"
                    ), {"mid": market_id, "cid": cutoff_id})
                await session.commit()
                remaining = await session.execute(text(
                    "SELECT COUNT(*) FROM liquidation_snapshots"
                ))
                logger.info(
                    "liquidation_snapshots cleanup: %d -> %d rows",
                    total, remaining.scalar() or 0,
                )
    except Exception as exc:
        logger.warning("liquidation_snapshots cleanup skipped: %s", exc)

    # Start WebSocket manager
    ws_manager = WSManager()
    app.state.ws_manager = ws_manager
    await ws_manager.start()

    # Start SL/TP monitoring service
    from app.services import sl_tp_service
    await sl_tp_service.start()

    # Start Telegram bot
    from app.services import telegram_bot
    await telegram_bot.start()

    # Strategy Telegram outbox drain (Part 5): moves worker-written
    # strat_telegram_outbox rows into the existing telegram queue. Admin-linked
    # chats only (no per-user preference store). Off unless a token is set.
    if settings.TELEGRAM_BOT_TOKEN:
        import asyncio as _asyncio
        from app.services import strat_outbox
        app.state.strat_outbox_stop = _asyncio.Event()
        app.state.strat_outbox_task = _asyncio.create_task(strat_outbox.run(app.state.strat_outbox_stop))

    # D-113: keep the /pulse cache warm so no user request ever pays the
    # ~23.6 s cold rebuild that follows each new sweep cycle.
    try:
        import asyncio as _asyncio2
        from app.routers import analytics as _an
        app.state.pulse_warm_stop = _asyncio2.Event()
        app.state.pulse_warm_task = _asyncio2.create_task(
            _an.warm_pulse_loop(app.state.pulse_warm_stop))
        logger.info("pulse cache warmer started (rebuilds on each new sweep cycle)")
    except Exception as exc:
        logger.warning("pulse warmer failed to start: %s", exc)

    # Start price alert monitoring service
    from app.services import price_alert_service
    await price_alert_service.start()

    # Start wallet insights service (win-rate trade reconstruction)
    from app.services import wallet_insights
    await wallet_insights.start()

    # Hyperliquid leaderboard ingest (phase 3): hourly, first run ~60s after boot
    from app.services.hyperliquid import leaderboard as hl_leaderboard
    hl_leaderboard.start()

    # Perpl leaderboard background refresher: keeps the discover-list combos
    # warm every 30s so the request path never waits on the Perpl upstream.
    from app.routers.leaders import start_lb_refresher
    start_lb_refresher()

    # Hyperliquid live mids + funding (phase 5): allMids ws + 60s funding poll
    from app.services.hyperliquid import prices as hl_prices
    hl_prices.start()

    # Hyperliquid trade detection (phase 6): shared user-fills ws (<=10 users)
    # + 15s clearinghouseState polling for the overflow
    from app.services.hyperliquid import tracker as hl_tracker
    hl_tracker.start()

    # Tier-2 fill-stats sampler (analytics): hourly, budget-capped, cursors
    from app.services.hyperliquid import fill_stats as hl_fill_stats
    hl_fill_stats.start()

    # Venue-truth reconciliation sweep (audit A3/A4): every 10 min,
    # allowlisted wallets only — flags copy-record vs on-chain drift
    from app.services.copy import venue_reconcile
    venue_reconcile.start()

    # Smart-money analytics position sweep (Phase 1): 20-min full-cohort
    # clearinghouseState cycle, hard 450 req/cycle budget, flow diffing
    if settings.ANALYTICS_ENABLED:
        from app.services.analytics import position_sweep as analytics_sweep
        analytics_sweep.start()

    # SMI validation study (Tier-2 Part A): nightly forward-return join +
    # bucket stats, DB-only, publication hard-gated (n>=30, span>=21d)
    if settings.ANALYTICS_ENABLED:
        from app.services.analytics import smi_study as analytics_smi_study
        analytics_smi_study.start()

    # Same-entity clustering (Tier-2 Part C2): nightly over stored 7d fills,
    # DB-only; count-dedup consumers hydrate from the stored table
    if settings.ANALYTICS_ENABLED:
        from app.services.analytics import clustering as analytics_clustering
        analytics_clustering.start()

    # Warm the Perpl market registry once at boot (non-fatal): consumers that
    # read the cache directly (e.g. the analytics context endpoint's
    # Perpl-mapping, which must not fetch on the request path) otherwise see
    # an empty cache until the first registry-using route is hit.
    try:
        from app.services import market_registry
        await market_registry.refresh_markets()
    except Exception as exc:
        logger.warning("market registry boot warm failed (non-fatal): %s", exc)

    logger.info("Startup complete")
    yield

    # --- Shutdown ---
    logger.info("Shutting down")

    # Stop WebSocket manager
    if hasattr(app.state, "ws_manager"):
        await app.state.ws_manager.stop()

    # Stop wallet insights service
    from app.services import wallet_insights
    await wallet_insights.stop()
    from app.services.hyperliquid import leaderboard as _hl_lb
    await _hl_lb.stop()
    from app.routers.leaders import stop_lb_refresher
    await stop_lb_refresher()
    from app.services.hyperliquid import fill_stats as _hl_fs
    await _hl_fs.stop()
    from app.services.copy import venue_reconcile as _vr
    await _vr.stop()
    if settings.ANALYTICS_ENABLED:
        from app.services.analytics import position_sweep as _asweep
        await _asweep.stop()
        from app.services.analytics import smi_study as _astudy
        await _astudy.stop()
        from app.services.analytics import clustering as _aclust
        await _aclust.stop()
    from app.services.hyperliquid import prices as _hl_px
    await _hl_px.stop()
    from app.services.hyperliquid import tracker as _hl_trk
    await _hl_trk.stop()

    # Stop Telegram bot
    if getattr(app.state, "strat_outbox_stop", None) is not None:
        app.state.strat_outbox_stop.set()
    from app.services import telegram_bot
    await telegram_bot.stop()

    # Close Perpl REST client
    from app.services.perpl_client import perpl_client
    await perpl_client.close()

    # Close the shared HL info client (Wallet Explorer Part 1)
    from app.services.hyperliquid import client as _hl_client
    await _hl_client.aclose()

    # Close database
    await close_db()
    logger.info("Shutdown complete")


def create_app() -> FastAPI:
    app = FastAPI(
        title="Perpl Copy Trading Platform",
        version="1.0.0",
        lifespan=lifespan,
    )

    # CORS middleware
    origins = [
        origin.strip()
        for origin in settings.CORS_ORIGINS.split(",")
        if origin.strip()
    ]
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Rate limiting middleware (120 reads/min, 30 writes/min per client IP)
    app.add_middleware(RateLimitMiddleware, read_limit=120, write_limit=30, window=60)

    # Include routers
    from app.routers.auth import router as auth_router
    from app.routers.markets import router as markets_router
    from app.routers.leaders import router as leaders_router
    from app.routers.copytrade import router as copytrade_router
    from app.routers.heatmap import router as heatmap_router
    from app.routers.whales import router as whales_router
    from app.routers.positions import router as positions_router
    from app.routers.autocopy import router as autocopy_router
    from app.routers.social import router as social_router
    from app.routers.sl_tp import router as sl_tp_router
    from app.routers.health_dashboard import router as health_router
    from app.routers.telegram import router as telegram_router
    from app.routers.funding_comparison import router as funding_compare_router
    from app.routers.orders import router as orders_router
    from app.routers.price_alerts import router as price_alerts_router
    from app.routers.journal import router as journal_router
    from app.routers.mcp_tokens import router as mcp_tokens_router
    from app.routers.mcp_stage import router as mcp_stage_router
    from app.routers.trades import router as trades_router
    from app.routers.perpl_report import router as perpl_report_router
    from app.routers.terminal_stats import router as terminal_stats_router
    # Copy Trading v1 (paper-only) — discovery / watchlist / subscriptions
    from app.routers.traders import router as traders_router
    from app.routers.watchlist import router as watchlist_router
    from app.routers.copy import router as copy_v1_router
    from app.routers.insights import router as insights_router
    from app.routers.strategies import router as strategies_router

    app.include_router(auth_router, prefix="/api")
    app.include_router(markets_router, prefix="/api")
    app.include_router(leaders_router, prefix="/api")
    app.include_router(copytrade_router, prefix="/api")
    app.include_router(heatmap_router, prefix="/api")
    app.include_router(whales_router, prefix="/api")
    app.include_router(positions_router, prefix="/api")
    app.include_router(autocopy_router, prefix="/api")
    app.include_router(social_router, prefix="/api")
    app.include_router(sl_tp_router, prefix="/api")
    app.include_router(health_router, prefix="/api")
    app.include_router(telegram_router, prefix="/api")
    app.include_router(funding_compare_router, prefix="/api")
    app.include_router(orders_router, prefix="/api")
    app.include_router(price_alerts_router, prefix="/api")
    app.include_router(journal_router, prefix="/api")
    app.include_router(mcp_tokens_router, prefix="/api")
    app.include_router(mcp_stage_router, prefix="/api")
    app.include_router(trades_router, prefix="/api")
    app.include_router(perpl_report_router, prefix="/api")
    app.include_router(terminal_stats_router, prefix="/api")
    # Copy Trading v1 (paper-only)
    app.include_router(traders_router, prefix="/api")
    app.include_router(watchlist_router, prefix="/api")
    app.include_router(copy_v1_router, prefix="/api")
    app.include_router(insights_router, prefix="/api")
    app.include_router(strategies_router, prefix="/api")
    # Admin moderation (phase 7): hide/unhide trader profiles, server-enforced
    from app.routers.admin import router as admin_router
    app.include_router(admin_router, prefix="/api")
    # Smart-money analytics (Phase 1) — env-gated for later access control
    if settings.ANALYTICS_ENABLED:
        from app.routers.analytics import router as analytics_router
        app.include_router(analytics_router, prefix="/api")

    # Wallet Explorer (Part 2) — HL side; venue calls at explorer priority only
    from app.routers.explorer import router as explorer_router
    app.include_router(explorer_router, prefix="/api")

    # Mount MCP server (Model Context Protocol — Claude Desktop / Code / Web).
    # Exposes /mcp/sse (SSE stream) and /mcp/messages (POST endpoint).
    # Auth: Authorization: Bearer <perpl_mcp_xxx> (issued from /api/mcp-tokens).
    from app.mcp.http_app import http_app as mcp_http_app
    app.mount("/mcp", mcp_http_app)

    # WebSocket endpoints
    app.websocket("/ws/feed")(client_feed_endpoint)
    app.websocket("/ws/trading")(trading_proxy_endpoint)
    app.websocket("/ws/market-data")(market_data_proxy_endpoint)

    # Health check (+ shared HL client weight metrics, Wallet Explorer Part 1;
    # + indexer lag, Part 4)
    @app.get("/health")
    async def health_check() -> dict:
        out = {"status": "ok", "service": "perpl-copy-trading"}
        try:
            from app.services.hyperliquid import client as _hlc
            out["hl_weight"] = _hlc.get_metrics()
        except Exception:
            pass
        try:
            from sqlalchemy import text as _text
            sf = get_session_factory()
            async with sf() as session:
                row = (await session.execute(_text(
                    "SELECT last_block, head_block, first_event_block, note, updated_at "
                    "FROM perpl_indexer_state WHERE id = 1"))).first()
            if row:
                lag = (int(row[1]) - int(row[0])) if row[0] is not None and row[1] is not None else None
                pct = None
                if row[0] and row[1] and row[2] and int(row[1]) > int(row[2]):
                    pct = round(100.0 * (int(row[0]) - int(row[2])) /
                                (int(row[1]) - int(row[2])), 2)
                out["perpl_indexer"] = {
                    "last_block": row[0], "head_block": row[1],
                    "first_event_block": row[2], "lag_blocks": lag,
                    "backfill_pct": pct, "note": row[3],
                    "updated_at": row[4].isoformat() if row[4] else None,
                }
        except Exception:
            pass
        return out

    # Access code verification
    @app.post("/api/verify-access")
    async def verify_access(data: dict) -> dict:
        from sqlalchemy import text
        code = data.get("code", "").strip().upper()
        if not code:
            return {"valid": False}
        try:
            sf = get_session_factory()
            async with sf() as session:
                result = await session.execute(
                    text("SELECT id FROM access_codes WHERE code = :code AND is_active = TRUE"),
                    {"code": code},
                )
                row = result.fetchone()
                return {"valid": row is not None}
        except Exception:
            return {"valid": False}

    # Exchange registry (phase 3): the Discover page renders its tabs from this
    # list — adding an exchange is data, not new UI code. copy_execution tells
    # the UI whether "copy on Perpl" actions apply (always Perpl-only today).
    @app.get("/api/exchanges")
    async def list_exchanges() -> list[dict]:
        return [
            {
                "id": "perpl", "label": "Perpl",
                "periods": ["all", "day"],
                "has_live_positions": True, "copy_execution": True,
            },
            {
                "id": "hl", "label": "Hyperliquid",
                "periods": ["all", "day", "week", "month"],
                # live open-position summaries served via /api/traders/hl-active
                "has_live_positions": True, "copy_execution": False,
            },
        ]

    # Cross-venue basis (phase 5): HL mid vs Perpl mark per mapped market.
    @app.get("/api/hl/basis")
    async def hl_basis() -> list[dict]:
        from sqlalchemy import select as _select
        from app.db.copy_models import MarketMap
        from app.services.hyperliquid import prices as hl_prices
        from app.services.ws_manager import ws_manager as _wm
        sf = get_session_factory()
        async with sf() as session:
            rows = (await session.execute(
                _select(MarketMap).where(MarketMap.exchange == "hl",
                                         MarketMap.perpl_market_id.isnot(None))
            )).scalars().all()
        out = []
        for r in rows:
            coin = r.native_symbol.upper()
            hl_mid = hl_prices.get_mid(coin)
            perpl_state = (_wm.market_state_cache.get(r.perpl_market_id) or {}) if _wm else {}
            perpl_mark = perpl_state.get("mark_price")
            basis = None
            if hl_mid and perpl_mark:
                basis = round((hl_mid - perpl_mark) / perpl_mark * 10_000, 2)
            out.append({
                "coin": coin,
                "perpl_market_id": r.perpl_market_id,
                "hl_mid": hl_mid,
                "perpl_mark": perpl_mark,
                "basis_bps": basis,
                "hl_funding_hourly": hl_prices.get_funding(coin),
                "perpl_funding": perpl_state.get("funding_rate"),
            })
        return out

    # Current block number
    @app.get("/api/block")
    async def get_block() -> dict:
        from app.services.chain_reader import _get_w3_contract
        w3, _ = _get_w3_contract()
        return {"block": w3.eth.block_number}

    # Candle data proxy
    _CANDLE_RESOLUTIONS = {60, 300, 900, 1800, 3600, 7200, 14400, 28800, 43200, 86400}
    # Shared pooled client — a fresh AsyncClient per request cost a TLS handshake
    # on every 30s recent-sync from every connected browser.
    import httpx as _candle_httpx
    _candle_client = _candle_httpx.AsyncClient(timeout=10.0)

    @app.get("/api/candles/{market_id}/{resolution}/{time_range}")
    async def get_candles(market_id: int, resolution: int, time_range: str) -> dict:
        from fastapi import HTTPException
        # Validate against the official spec: known resolution + <=1024 candles.
        if resolution not in _CANDLE_RESOLUTIONS:
            raise HTTPException(status_code=400, detail=f"Unsupported resolution {resolution}s")
        try:
            frm, to = (int(x) for x in time_range.split("-", 1))
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid time_range (expected <from>-<to> in ms)")
        if to <= frm or (to - frm) // 1000 // resolution > 1024:
            raise HTTPException(status_code=400, detail="Requested range exceeds 1024 candles")
        url = f"{settings.PERPL_REST_URL}/api/v1/market-data/{market_id}/candles/{resolution}/{time_range}"
        resp = await _candle_client.get(url)
        if resp.status_code == 429:
            raise HTTPException(status_code=429, detail="Perpl rate limit — try again shortly")
        resp.raise_for_status()
        return resp.json()

    def _get_real_max_leverage(market_id: int, cfg: dict) -> int:
        """Max leverage from Perpl's per-market `initial_margin`.

        Per the Perpl margin docs, IMF (initial margin fraction) IS the maximum
        leverage, and the config `initial_margin` field carries it in HUNDREDTHS
        — the same encoding as the order `lv` field (1000 = 10.00x). So:
            max leverage = initial_margin / 100
        (BTC 1500->15x, MON 1000->10x, ETH/SOL 1200->12x, ZEC 800->8x). This
        also reproduces the old hardcoded {1:10,10:5,20:10,30:20} caps under the
        margins Perpl used at the time (1000/500/1000/2000), confirming the
        encoding. Fully dynamic — no hardcoded map."""
        init = cfg.get("initial_margin", 0)
        if not init or init <= 0:
            return 10  # only if Perpl omitted the field
        return max(1, round(init / 100))

    # On-chain order book with simple TTL cache
    import time as _time

    _ob_cache = ob_cache  # alias to module-level dict (shared with MCP server)
    # NOTE: display no longer uses this cache (live L2 WS book instead); it only
    # feeds flow_tracker / whale extraction. The bg thread refreshes every ~10s.
    _OB_TTL = 30.0

    def _read_orderbook(market_id: int) -> dict:
        from app.services.chain_reader import _get_w3_contract, MARKETS
        from concurrent.futures import ThreadPoolExecutor
        import httpx as _httpx

        w3, contract = _get_w3_contract()
        mcfg = MARKETS.get(market_id)
        if not mcfg:
            return {"bids": [], "asks": []}
        pd = 10 ** mcfg["price_decimals"]
        sd = 10 ** mcfg["size_decimals"]

        mark_raw = 0
        try:
            ctx = _httpx.get(f"{settings.PERPL_REST_URL}/api/v1/pub/context", timeout=5).json()
            for m in ctx.get("markets", []):
                if m["id"] == market_id:
                    mark_raw = m.get("state", {}).get("mrk", 0)
                    break
        except Exception as e:
            logger.warning("Failed to fetch mark price for orderbook: %s", e)

        # Step 1: Collect all price levels (sequential — must walk linked list)
        price_levels = []
        price = 0
        for _ in range(40):  # reduced from 60
            try:
                next_price = contract.functions.getNextPriceAboveWithOrders(market_id, price).call()
                if next_price == 0 or next_price >= 2**24 - 1:
                    break
                price_levels.append(next_price)
                price = next_price
            except Exception:
                break

        # Step 2: Fetch orders at each level in parallel
        WHALE_THRESHOLD_USD = settings.WHALE_THRESHOLD_USD  # from config (default 50000)

        def _get_level(p):
            try:
                orders = contract.functions.getOrdersAtPriceLevel(market_id, p, 0, 50).call()
                total_size = sum(o[3] for o in orders[0] if o[0] > 0) / sd
                price_usd = p / pd
                # Identify whale orders
                whales = []
                for o in orders[0]:
                    if o[0] > 0:
                        order_size = o[3] / sd
                        order_usd = order_size * price_usd
                        if order_usd >= WHALE_THRESHOLD_USD:
                            whales.append({"size": round(order_size, mcfg["size_decimals"]), "size_usd": round(order_usd, 2)})
                if total_size > 0:
                    return {"price": round(price_usd, mcfg["price_decimals"]), "size": round(total_size, mcfg["size_decimals"]), "raw": p, "whales": whales}
            except Exception:
                pass  # Individual price level fetch can fail silently
            return None

        bids, asks, whale_bids, whale_asks = [], [], [], []
        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(_get_level, price_levels))

        for level in results:
            if level:
                is_bid = mark_raw and level["raw"] <= mark_raw
                entry = {"price": level["price"], "size": level["size"]}
                if is_bid:
                    bids.append(entry)
                else:
                    asks.append(entry)
                # Collect whales
                for w in level.get("whales", []):
                    whale_entry = {"price": level["price"], "size": w["size"], "size_usd": w["size_usd"], "side": "bid" if is_bid else "ask"}
                    if is_bid:
                        whale_bids.append(whale_entry)
                    else:
                        whale_asks.append(whale_entry)

        bids.sort(key=lambda x: x["price"], reverse=True)
        asks.sort(key=lambda x: x["price"])
        return {"bids": bids[:20], "asks": asks[:20], "whales": whale_bids + whale_asks}

    # Background thread refreshes all orderbooks continuously
    import threading as _threading

    def _orderbook_refresh_loop():
        from app.services.chain_reader import MARKETS
        from app.services.flow_tracker import flow_tracker
        _time.sleep(5)
        while True:
            for mid in MARKETS:
                try:
                    result = _read_orderbook(mid)
                    result["_ts"] = _time.time()
                    _ob_cache[mid] = result
                    flow_tracker.record(mid, result.get("bids", []), result.get("asks", []))
                except Exception:
                    pass
            _time.sleep(10)

    _threading.Thread(target=_orderbook_refresh_loop, daemon=True).start()

    @app.get("/api/orderbook/{market_id}")
    async def get_orderbook(market_id: int) -> dict:
        """Always returns from cache instantly. Background thread keeps it fresh."""
        cached = _ob_cache.get(market_id)
        if cached:
            return {"bids": cached["bids"], "asks": cached["asks"], "whales": cached.get("whales", [])}
        return {"bids": [], "asks": [], "whales": []}

    # Log copy trade (authenticated — uses caller's wallet, not request body)
    from app.routers.auth import get_authenticated_user as _get_auth_user
    from app.db.models import User as _User
    from fastapi import Depends as _Depends

    @app.post("/api/copy-log")
    async def log_copy_trade(data: dict, user: _User = _Depends(_get_auth_user)) -> dict:
        from app.db.models import CopyTradeLog
        try:
            sf = get_session_factory()
            async with sf() as session:
                session.add(CopyTradeLog(
                    user_wallet=user.wallet_address.lower(),
                    leader_wallet=data.get("leader_wallet", ""),
                    market_id=data.get("market_id", 0),
                    symbol=data.get("symbol", ""),
                    side=data.get("side", ""),
                    leverage=data.get("leverage", 0),
                    amount_usd=data.get("amount_usd", 0),
                    status=data.get("status", "submitted"),
                    error=data.get("error"),
                ))
                await session.commit()
        except Exception:
            pass
        return {"ok": True}

    # Check order forwarding status
    @app.get("/api/check-forwarding/{wallet_address}")
    async def check_forwarding(wallet_address: str) -> dict:
        import asyncio
        from app.services.chain_reader import _get_w3_contract
        from web3 import Web3

        def _check():
            w3, contract = _get_w3_contract()
            addr = Web3.to_checksum_address(wallet_address)
            acct = contract.functions.getAccountByAddr(addr).call()
            account_id = acct[0]
            # flags field (acct[3]) — 0 means forwarding not enabled
            # The 'fw' field in the WS account snapshot also tells us
            # For now check the account extra tuple
            return {
                "account_id": account_id,
                "forwarding_enabled": acct[3] != 0,  # flags field
                "contract_address": "0x34b6552d57a35a1d042ccae1951bd1c370112a6f",
            }

        return await asyncio.get_event_loop().run_in_executor(None, _check)

    # Order flow imbalance
    @app.get("/api/order-flow/{market_id}")
    async def get_order_flow(market_id: int) -> list[dict]:
        from app.services.flow_tracker import flow_tracker
        return flow_tracker.get_flow(market_id)

    # Trader activity
    @app.get("/api/trader-activity")
    async def get_trader_activity() -> list[dict]:
        from app.services.trader_tracker import trader_tracker
        return trader_tracker.get_recent_activity()

    # Funding rate history
    @app.get("/api/funding-history/{market_id}")
    async def get_funding_history(market_id: int) -> list[dict]:
        from app.services.funding_tracker import funding_tracker
        await funding_tracker.load_from_db(market_id)
        return funding_tracker.get_history(market_id)

    # Live market configs from Perpl context
    @app.get("/api/market-configs")
    async def get_market_configs() -> list[dict]:
        from app.services.perpl_client import perpl_client
        ctx = await perpl_client.get_context()
        # Instance-level trigger-order cap (mainnet: 16) — surfaced per market row
        # so the frontend can guard SL/TP placement client-side.
        instances = ctx.get("instances") or [{}]
        max_triggers = instances[0].get("max_account_trigger_orders", 16)
        configs = []
        for m in ctx.get("markets", []):
            cfg = m.get("config", {})
            if not cfg.get("is_open"):
                continue
            configs.append({
                "market_id": m["id"],
                "symbol": m.get("symbol") or m.get("name") or f"MKT-{m['id']}",
                "price_decimals": cfg.get("price_decimals", 1),
                "size_decimals": cfg.get("size_decimals", 5),
                "initial_margin": cfg.get("initial_margin", 1000),
                "maintenance_margin": cfg.get("maintenance_margin", 2000),
                "max_leverage": _get_real_max_leverage(m["id"], cfg),
                "maker_fee": cfg.get("maker_fee", 0) / 100,
                "taker_fee": cfg.get("taker_fee", 0) / 100,
                "order_ttl_blocks": m.get("order_ttl_blocks", 6),
                "max_price_impact_pct": m.get("order_max_price_impact_percent", 5),
                # Server-side market-order slippage cap, bps (live value: 100 = 1%)
                "max_market_slippage_bps": m.get("order_max_market_slippage_bps", 100),
                "max_trigger_orders": max_triggers,
                "funding_interval_sec": m.get("funding_interval_sec", 3600),
            })
        return configs

    return app


app = create_app()
