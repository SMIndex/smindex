"""Unusual Timing tables (migration v34). Column set from the build brief
section 1, plus three small tables the brief does not name:

  * ut_job_runs: one row per job run with the Hyperliquid weight it used
    (acceptance test 6 and the daily cap read this), same shape as
    featured_job_runs
  * ut_universe: the daily Hyperliquid universe snapshot (main perps, HIP-3
    perps, spot pairs) so a coin that appears or disappears becomes a listing
    or delisting event; first_seen for coins already present at the first
    snapshot comes from their first candle
  * ut_candle_wants: coins the worker wants 1 m candles for (coins traded by
    flagged wallets); the API reader serves the list on its next pass
"""

DDL = [
    "CREATE TABLE IF NOT EXISTS ut_events ("
    " id INT AUTO_INCREMENT PRIMARY KEY, time DATETIME(3) NOT NULL, coin VARCHAR(32) NULL,"
    " kind ENUM('listing','delisting','funding_flip','macro','big_move','manual') NOT NULL,"
    " direction ENUM('up','down','unknown') NOT NULL DEFAULT 'unknown', magnitude DECIMAL(8,4) NULL,"
    " source VARCHAR(40) NOT NULL, note VARCHAR(240) NULL,"
    " UNIQUE KEY ux_ev (kind, coin, time), KEY ix_coin_time (coin, time), KEY ix_time (time))"
    " ENGINE=InnoDB DEFAULT CHARSET=utf8mb4",

    "CREATE TABLE IF NOT EXISTS ut_candles ("
    " coin VARCHAR(32) NOT NULL, tf ENUM('1m','1h') NOT NULL, t DATETIME NOT NULL,"
    " o DECIMAL(24,8) NOT NULL, h DECIMAL(24,8) NOT NULL, l DECIMAL(24,8) NOT NULL, c DECIMAL(24,8) NOT NULL,"
    " v DECIMAL(24,4) NOT NULL DEFAULT 0, PRIMARY KEY (coin, tf, t)) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4",

    "CREATE TABLE IF NOT EXISTS ut_coin_stats ("
    " as_of DATE NOT NULL, coin VARCHAR(32) NOT NULL, big_move_pct DECIMAL(6,3) NOT NULL,"
    " base_rate_60m DECIMAL(8,6) NOT NULL, median_2h_range DECIMAL(6,3) NULL, regime ENUM('trend','range') NOT NULL DEFAULT 'range',"
    " hours INT NOT NULL DEFAULT 0, moves_up INT NOT NULL DEFAULT 0, moves_down INT NOT NULL DEFAULT 0,"
    " PRIMARY KEY (as_of, coin)) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4",

    "CREATE TABLE IF NOT EXISTS ut_entries ("
    " id BIGINT AUTO_INCREMENT PRIMARY KEY, wallet CHAR(42) NOT NULL, coin VARCHAR(32) NOT NULL,"
    " side ENUM('long','short') NOT NULL, time DATETIME(3) NOT NULL, px DECIMAL(24,8) NOT NULL, size DECIMAL(24,8) NOT NULL DEFAULT 0,"
    " notional DECIMAL(20,2) NOT NULL, leverage DECIMAL(6,2) NULL, is_add TINYINT(1) NOT NULL DEFAULT 0,"
    " size_vs_median DECIMAL(8,2) NULL, share_of_equity DECIMAL(6,4) NULL,"
    " mfe_30m DECIMAL(8,4) NULL, mfe_2h DECIMAL(8,4) NULL, mfe_24h DECIMAL(8,4) NULL,"
    " ret_30m DECIMAL(8,4) NULL, ret_2h DECIMAL(8,4) NULL, ret_24h DECIMAL(8,4) NULL,"
    " hit TINYINT(1) NULL, base_rate DECIMAL(8,6) NULL, moved_market TINYINT(1) NOT NULL DEFAULT 0,"
    " event_id INT NULL, minutes_before_event INT NULL, peak_at DATETIME(3) NULL,"
    " closed_at DATETIME(3) NULL, close_after_peak_min INT NULL, candle_src ENUM('1m','1h') NULL,"
    " n_fills SMALLINT NOT NULL DEFAULT 1, computed_at DATETIME NULL,"
    " UNIQUE KEY ux (wallet, coin, side, time), KEY ix_wallet (wallet, time), KEY ix_coin_time (coin, time),"
    " KEY ix_pending (computed_at, time)) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4",

    "CREATE TABLE IF NOT EXISTS ut_wallet_meta ("
    " wallet CHAR(42) NOT NULL PRIMARY KEY, first_deposit_at DATETIME NULL, first_deposit_usd DECIMAL(20,2) NULL,"
    " first_deposit_tx CHAR(66) NULL, first_deposit_kind VARCHAR(24) NULL, funder CHAR(42) NULL, funder_label VARCHAR(64) NULL,"
    " funder_checked_at DATETIME NULL, first_fill_at DATETIME NULL, history_from DATETIME NULL,"
    " is_mm TINYINT(1) NOT NULL DEFAULT 0, is_vault TINYINT(1) NOT NULL DEFAULT 0, ledger_items INT NULL,"
    " checked_at DATETIME NULL, KEY ix_funder (funder)) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4",

    "CREATE TABLE IF NOT EXISTS ut_wallet_scores ("
    " as_of DATE NOT NULL, wallet CHAR(42) NOT NULL, score DECIMAL(5,2) NOT NULL DEFAULT 0,"
    " level ENUM('none','watch','strong','cluster') NOT NULL DEFAULT 'none',"
    " entries INT NOT NULL DEFAULT 0, hits INT NOT NULL DEFAULT 0, hit_rate DECIMAL(6,4) NULL, blended_base_rate DECIMAL(8,6) NULL,"
    " p_value DOUBLE NULL, events_preceded INT NOT NULL DEFAULT 0, event_kinds INT NOT NULL DEFAULT 0,"
    " top3_profit_share DECIMAL(6,4) NULL, median_hold_min INT NULL, fresh_large TINYINT(1) NOT NULL DEFAULT 0,"
    " size_shock TINYINT(1) NOT NULL DEFAULT 0, co_entries INT NOT NULL DEFAULT 0, cluster_id INT NULL,"
    " exit_speed_hits INT NOT NULL DEFAULT 0, moved_market_count INT NOT NULL DEFAULT 0,"
    " misses INT NOT NULL DEFAULT 0, worst_loss_usd DECIMAL(20,2) NULL,"
    " s1 TINYINT(1) NOT NULL DEFAULT 0, s2 TINYINT(1) NOT NULL DEFAULT 0, s3 TINYINT(1) NOT NULL DEFAULT 0, s4 TINYINT(1) NOT NULL DEFAULT 0,"
    " s5 TINYINT(1) NOT NULL DEFAULT 0, s6 TINYINT(1) NOT NULL DEFAULT 0, s7 TINYINT(1) NOT NULL DEFAULT 0,"
    " excluded VARCHAR(40) NULL, evidence JSON NULL, computed_at DATETIME NOT NULL,"
    " PRIMARY KEY (as_of, wallet), KEY ix_level (as_of, level, score)) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4",

    "CREATE TABLE IF NOT EXISTS ut_clusters ("
    " id INT AUTO_INCREMENT PRIMARY KEY, as_of DATE NOT NULL, member_count INT NOT NULL, total_notional DECIMAL(20,2) NOT NULL DEFAULT 0,"
    " lead_wallet CHAR(42) NOT NULL, shared_events JSON NULL, edge_kinds JSON NULL, created_at DATETIME NOT NULL,"
    " KEY ix_asof (as_of)) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4",

    "CREATE TABLE IF NOT EXISTS ut_cluster_members ("
    " cluster_id INT NOT NULL, wallet CHAR(42) NOT NULL, edges INT NOT NULL DEFAULT 0,"
    " PRIMARY KEY (cluster_id, wallet), KEY ix_wallet (wallet)) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4",

    "CREATE TABLE IF NOT EXISTS ut_coentries ("
    " id BIGINT AUTO_INCREMENT PRIMARY KEY, wallet_a CHAR(42) NOT NULL, wallet_b CHAR(42) NOT NULL, coin VARCHAR(32) NOT NULL,"
    " side ENUM('long','short') NOT NULL, time_a DATETIME(3) NOT NULL, time_b DATETIME(3) NOT NULL, on_event TINYINT(1) NOT NULL DEFAULT 0,"
    " UNIQUE KEY ux_co (wallet_a, wallet_b, coin, side, time_a), KEY ix_pair (wallet_a, wallet_b))"
    " ENGINE=InnoDB DEFAULT CHARSET=utf8mb4",

    "CREATE TABLE IF NOT EXISTS ut_market_drift ("
    " coin VARCHAR(32) NOT NULL, t DATETIME NOT NULL, funding_trend_48h DECIMAL(12,8) NULL, oi_change_6h DECIMAL(8,4) NULL,"
    " oi_change_24h DECIMAL(8,4) NULL, abn_ret_12h DECIMAL(8,4) NULL, ut_wallets_6h INT NOT NULL DEFAULT 0,"
    " drift_score DECIMAL(5,2) NOT NULL DEFAULT 0, PRIMARY KEY (coin, t), KEY ix_t (t, drift_score))"
    " ENGINE=InnoDB DEFAULT CHARSET=utf8mb4",

    "CREATE TABLE IF NOT EXISTS ut_alerts_sent ("
    " id INT AUTO_INCREMENT PRIMARY KEY, wallet CHAR(42) NOT NULL, coin VARCHAR(32) NOT NULL, side ENUM('long','short') NOT NULL,"
    " notional DECIMAL(20,2) NOT NULL, level VARCHAR(10) NOT NULL, payload JSON NULL, trade_at DATETIME(3) NULL,"
    " created_at DATETIME(3) NOT NULL, sent_at DATETIME(3) NULL, chat_id VARCHAR(32) NULL, error VARCHAR(200) NULL,"
    " KEY ix_wallet_coin (wallet, coin, created_at), KEY ix_pending (sent_at, created_at))"
    " ENGINE=InnoDB DEFAULT CHARSET=utf8mb4",

    "CREATE TABLE IF NOT EXISTS ut_job_runs ("
    " id INT AUTO_INCREMENT PRIMARY KEY, job VARCHAR(32) NOT NULL, started_at DATETIME NOT NULL, finished_at DATETIME NULL,"
    " ok TINYINT(1) NULL, requests INT NOT NULL DEFAULT 0, weight INT NOT NULL DEFAULT 0, notes VARCHAR(255) NULL,"
    " KEY ix_started (started_at)) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4",

    "CREATE TABLE IF NOT EXISTS ut_universe ("
    " coin VARCHAR(40) NOT NULL PRIMARY KEY, kind ENUM('perp','hip3','spot') NOT NULL, first_seen DATETIME NOT NULL,"
    " last_seen DATETIME NOT NULL, first_seen_src ENUM('snapshot','candle') NOT NULL DEFAULT 'snapshot')"
    " ENGINE=InnoDB DEFAULT CHARSET=utf8mb4",

    "CREATE TABLE IF NOT EXISTS ut_candle_wants ("
    " coin VARCHAR(32) NOT NULL PRIMARY KEY, wanted_at DATETIME NOT NULL, served_at DATETIME NULL)"
    " ENGINE=InnoDB DEFAULT CHARSET=utf8mb4",
]
