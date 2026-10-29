"""Featured traders tables (migration v33). Column set from
docs/featured-traders/BUILD_BRIEF.md section 1, plus:

  * hl_funding: userFunding rows, so a round trip's PnL is net of funding
  * featured_candidates: the day's candidate set and the cheap pre-filter verdict
  * featured_job_runs: one row per job run with the Hyperliquid weight it used
    (acceptance test 6 reads this)
  * featured_counterparties: per-day counterparty counts the trades feed
    stores for listed accounts (the wash check reads this)
  * round_trips.mae_pct / mfe_pct: worst and best excursion inside the trip
    (from the trip's own fills, or from candles once read), used by the copy
    replay's stop loss
"""

DDL = [
    "CREATE TABLE IF NOT EXISTS hl_fills ("
    " wallet CHAR(42) NOT NULL, tid BIGINT NOT NULL, time DATETIME(3) NOT NULL,"
    " coin VARCHAR(32) NOT NULL, dir VARCHAR(24) NULL, side CHAR(1) NOT NULL,"
    " px DECIMAL(24,8) NOT NULL, sz DECIMAL(24,8) NOT NULL,"
    " closed_pnl DECIMAL(20,6) NOT NULL DEFAULT 0, fee DECIMAL(20,6) NOT NULL DEFAULT 0,"
    " fee_token VARCHAR(16) NULL, crossed TINYINT(1) NULL,"
    " start_position DECIMAL(24,8) NOT NULL DEFAULT 0, hash CHAR(66) NULL, oid BIGINT NULL,"
    " liquidation TINYINT(1) NOT NULL DEFAULT 0,"
    " PRIMARY KEY (wallet, tid), KEY ix_hlf2_wallet_time (wallet, time), KEY ix_hlf2_time (time)"
    ") ENGINE=InnoDB DEFAULT CHARSET=utf8mb4",

    "CREATE TABLE IF NOT EXISTS hl_funding ("
    " wallet CHAR(42) NOT NULL, time DATETIME(3) NOT NULL, coin VARCHAR(32) NOT NULL,"
    " usdc DECIMAL(20,6) NOT NULL, szi DECIMAL(24,8) NULL, rate DECIMAL(18,10) NULL, hash CHAR(66) NULL,"
    " PRIMARY KEY (wallet, time, coin), KEY ix_hlfund_wallet_time (wallet, time)"
    ") ENGINE=InnoDB DEFAULT CHARSET=utf8mb4",

    "CREATE TABLE IF NOT EXISTS wallet_identity ("
    " wallet CHAR(42) NOT NULL PRIMARY KEY,"
    " venue ENUM('hl','perpl') NOT NULL DEFAULT 'hl',"
    " role ENUM('user','agent','vault','subAccount','missing','unknown') NOT NULL DEFAULT 'unknown',"
    " master CHAR(42) NULL, checked_at DATETIME NULL, sub_accounts JSON NULL, subs_checked_at DATETIME NULL,"
    " fills_cursor_ms BIGINT NULL, funding_cursor_ms BIGINT NULL, fills_checked_at DATETIME NULL,"
    " history_from DATETIME(3) NULL, portfolio JSON NULL, portfolio_at DATETIME NULL,"
    " KEY ix_wi_master (master)"
    ") ENGINE=InnoDB DEFAULT CHARSET=utf8mb4",

    "CREATE TABLE IF NOT EXISTS round_trips ("
    " id BIGINT AUTO_INCREMENT PRIMARY KEY,"
    " venue ENUM('hl','perpl') NOT NULL, wallet CHAR(42) NOT NULL, coin VARCHAR(32) NOT NULL,"
    " side ENUM('long','short') NOT NULL,"
    " open_time DATETIME(3) NOT NULL, close_time DATETIME(3) NOT NULL,"
    " n_fills SMALLINT NOT NULL, n_adds SMALLINT NOT NULL, n_closes SMALLINT NOT NULL,"
    " maker_fills SMALLINT NOT NULL DEFAULT 0,"
    " max_size DECIMAL(24,8) NOT NULL DEFAULT 0, max_size_usd DECIMAL(20,2) NOT NULL,"
    " volume_usd DECIMAL(20,2) NOT NULL DEFAULT 0,"
    " avg_entry DECIMAL(24,8) NOT NULL, avg_exit DECIMAL(24,8) NOT NULL,"
    " margin_usd DECIMAL(20,2) NULL, leverage DECIMAL(6,2) NULL,"
    " pnl_gross DECIMAL(20,6) NOT NULL DEFAULT 0, pnl_usd DECIMAL(20,6) NOT NULL,"
    " pnl_pct_margin DECIMAL(10,4) NULL, ret_pct DECIMAL(10,4) NOT NULL DEFAULT 0,"
    " fees DECIMAL(20,6) NOT NULL DEFAULT 0, funding DECIMAL(20,6) NOT NULL DEFAULT 0,"
    " entry_taker TINYINT(1) NULL, averaged_down TINYINT(1) NOT NULL DEFAULT 0,"
    " added_to_winner TINYINT(1) NOT NULL DEFAULT 0, liquidation TINYINT(1) NOT NULL DEFAULT 0,"
    " mae_pct DECIMAL(8,4) NULL, mfe_pct DECIMAL(8,4) NULL, mae_src ENUM('fills','candles') NULL,"
    " entry_vs_24h_range DECIMAL(6,4) NULL, smi_at_entry DECIMAL(6,2) NULL,"
    " accounts JSON NULL, created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,"
    " UNIQUE KEY ux_rt (venue, wallet, coin, open_time),"
    " KEY ix_rt_close (close_time), KEY ix_rt_wallet (wallet, close_time), KEY ix_rt_venue_close (venue, close_time)"
    ") ENGINE=InnoDB DEFAULT CHARSET=utf8mb4",

    "CREATE TABLE IF NOT EXISTS featured_candidates ("
    " as_of DATE NOT NULL, venue ENUM('hl','perpl') NOT NULL, wallet CHAR(42) NOT NULL,"
    " source VARCHAR(64) NOT NULL, prefilter TINYINT(1) NOT NULL DEFAULT 1, reason VARCHAR(64) NULL,"
    " month_pnl DECIMAL(20,2) NULL, month_vlm DECIMAL(20,2) NULL, account_value DECIMAL(20,2) NULL,"
    " display_name VARCHAR(100) NULL,"
    " PRIMARY KEY (as_of, venue, wallet), KEY ix_fc_wallet (wallet)"
    ") ENGINE=InnoDB DEFAULT CHARSET=utf8mb4",

    "CREATE TABLE IF NOT EXISTS featured_wallets ("
    " as_of DATE NOT NULL, venue ENUM('hl','perpl') NOT NULL, wallet CHAR(42) NOT NULL,"
    " label ENUM('consistent','asymmetric') NULL, qualified TINYINT(1) NOT NULL DEFAULT 0,"
    " inactive TINYINT(1) NOT NULL DEFAULT 0, one_big_win TINYINT(1) NOT NULL DEFAULT 0,"
    " score DECIMAL(5,2) NULL, verdict VARCHAR(240) NULL, style VARCHAR(16) NULL, display_name VARCHAR(100) NULL,"
    " trades_90d INT NOT NULL DEFAULT 0, days_active_90d INT NOT NULL DEFAULT 0,"
    " win_rate_90d DECIMAL(6,4) NULL, win_rate_30d DECIMAL(6,4) NULL,"
    " net_pnl_90d DECIMAL(20,2) NULL, net_pnl_30d DECIMAL(20,2) NULL, net_pnl_all DECIMAL(20,2) NULL,"
    " profit_factor_90d DECIMAL(8,2) NULL, profit_factor_30d DECIMAL(8,2) NULL,"
    " avg_win_usd DECIMAL(20,2) NULL, avg_loss_usd DECIMAL(20,2) NULL,"
    " avg_win_pct_eq DECIMAL(8,4) NULL, avg_loss_pct_eq DECIMAL(8,4) NULL,"
    " avg_win_move_pct DECIMAL(8,4) NULL, avg_loss_move_pct DECIMAL(8,4) NULL,"
    " max_drawdown_90d DECIMAL(8,4) NULL, dd_start DATE NULL, dd_end DATE NULL,"
    " worst_trade_usd DECIMAL(20,2) NULL, worst_trade_pct_eq DECIMAL(8,4) NULL,"
    " worst_day_usd DECIMAL(20,2) NULL, worst_week_usd DECIMAL(20,2) NULL,"
    " largest_trade_share DECIMAL(8,4) NULL, profitable_weeks_13 TINYINT NULL,"
    " longest_loss_streak SMALLINT NULL, loss_streak_at DATE NULL,"
    " median_hold_sec INT NULL, trades_per_week DECIMAL(8,2) NULL, trades_per_day_max DECIMAL(8,2) NULL,"
    " median_leverage DECIMAL(6,2) NULL, max_leverage DECIMAL(6,2) NULL,"
    " maker_share DECIMAL(6,4) NULL, pnl_per_volume DECIMAL(10,6) NULL, long_share DECIMAL(6,4) NULL,"
    " avg_equity_90d DECIMAL(20,2) NULL, account_value DECIMAL(20,2) NULL, volume_90d DECIMAL(20,2) NULL,"
    " top_coins JSON NULL, coin_stats JSON NULL, hours_utc JSON NULL, copiable_pct DECIMAL(6,2) NULL,"
    " liquidations_90d INT NOT NULL DEFAULT 0, usual_open SMALLINT NULL, history_from DATE NULL,"
    " flags JSON NULL, score_inputs JSON NULL, last_trade_at DATETIME(3) NULL, computed_at DATETIME NOT NULL,"
    " PRIMARY KEY (as_of, venue, wallet), KEY ix_fw_latest (venue, wallet, as_of), KEY ix_fw_q (as_of, qualified)"
    ") ENGINE=InnoDB DEFAULT CHARSET=utf8mb4",

    "CREATE TABLE IF NOT EXISTS featured_live ("
    " venue ENUM('hl','perpl') NOT NULL, wallet CHAR(42) NOT NULL,"
    " last_trade_at DATETIME(3) NULL, open_positions JSON NULL, open_count SMALLINT NOT NULL DEFAULT 0,"
    " unrealized_usd DECIMAL(20,2) NULL, account_value DECIMAL(20,2) NULL, positions_at DATETIME(3) NULL,"
    " updated_at DATETIME(3) NOT NULL,"
    " PRIMARY KEY (venue, wallet)"
    ") ENGINE=InnoDB DEFAULT CHARSET=utf8mb4",

    "CREATE TABLE IF NOT EXISTS featured_counterparties ("
    " wallet CHAR(42) NOT NULL, other CHAR(42) NOT NULL, day DATE NOT NULL, n INT NOT NULL DEFAULT 0,"
    " PRIMARY KEY (wallet, other, day), KEY ix_fcp_day (day)"
    ") ENGINE=InnoDB DEFAULT CHARSET=utf8mb4",

    "CREATE TABLE IF NOT EXISTS featured_job_runs ("
    " id INT AUTO_INCREMENT PRIMARY KEY, job VARCHAR(32) NOT NULL,"
    " started_at DATETIME NOT NULL, finished_at DATETIME NULL, ok TINYINT(1) NULL,"
    " requests INT NOT NULL DEFAULT 0, weight INT NOT NULL DEFAULT 0, notes VARCHAR(255) NULL,"
    " KEY ix_fjr_job (job, started_at)"
    ") ENGINE=InnoDB DEFAULT CHARSET=utf8mb4",

    "CREATE TABLE IF NOT EXISTS featured_whitelist_requests ("
    " id INT AUTO_INCREMENT PRIMARY KEY, email VARCHAR(254) NOT NULL, x_handle VARCHAR(16) NOT NULL,"
    " wallet CHAR(42) NOT NULL, status ENUM('pending','approved','rejected') NOT NULL DEFAULT 'pending',"
    " note VARCHAR(240) NULL, created_at DATETIME NOT NULL, updated_at DATETIME NULL, decided_at DATETIME NULL,"
    " decided_by VARCHAR(42) NULL, ip_hash CHAR(64) NULL, tg_message_id BIGINT NULL,"
    " UNIQUE KEY ux_wallet_open (wallet, status), KEY ix_fwr_status (status, created_at)"
    ") ENGINE=InnoDB DEFAULT CHARSET=utf8mb4",

    "CREATE TABLE IF NOT EXISTS featured_whitelist ("
    " wallet CHAR(42) NOT NULL PRIMARY KEY, approved_at DATETIME NOT NULL, approved_by VARCHAR(42) NULL,"
    " source ENUM('request','admin','import') NOT NULL DEFAULT 'request'"
    ") ENGINE=InnoDB DEFAULT CHARSET=utf8mb4",
]
