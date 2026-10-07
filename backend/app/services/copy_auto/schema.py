"""Auto-copy tables (migration v28). Single source for main.py and the replay harness."""

DDL = [
    "CREATE TABLE IF NOT EXISTS auto_copy_keys ("
    " user_id INT NOT NULL PRIMARY KEY, wallet VARCHAR(42) NOT NULL,"
    " api_key_enc TEXT NOT NULL, seed_enc TEXT NOT NULL, pub_hex VARCHAR(70) NOT NULL,"
    " scope TINYINT NOT NULL, label VARCHAR(64) NULL, created_at DATETIME NOT NULL,"
    " last_used_at DATETIME NULL, last_error VARCHAR(255) NULL) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4",

    "CREATE TABLE IF NOT EXISTS auto_copy_subs ("
    " id INT AUTO_INCREMENT PRIMARY KEY, user_id INT NOT NULL, follower_wallet VARCHAR(42) NOT NULL,"
    " leader_wallet VARCHAR(42) NOT NULL, mode VARCHAR(8) NOT NULL DEFAULT 'shadow',"
    " status VARCHAR(8) NOT NULL DEFAULT 'active', pause_reason VARCHAR(120) NULL,"
    " sizing VARCHAR(12) NOT NULL DEFAULT 'fixed', margin_usd DOUBLE NOT NULL DEFAULT 10,"
    " allocation_usd DOUBLE NOT NULL DEFAULT 50, max_leverage DOUBLE NOT NULL DEFAULT 5,"
    " max_positions INT NOT NULL DEFAULT 3, mirror_adds TINYINT(1) NOT NULL DEFAULT 1,"
    " mirror_reduces TINYINT(1) NOT NULL DEFAULT 1, reopen_on_flip TINYINT(1) NOT NULL DEFAULT 1,"
    " drift_pct DOUBLE NOT NULL DEFAULT 0.5, sl_margin_pct DOUBLE NULL DEFAULT 50, tp_pct DOUBLE NULL,"
    " daily_loss_usd DOUBLE NULL DEFAULT 30, total_loss_usd DOUBLE NULL DEFAULT 100, markets JSON NULL,"
    " consecutive_failures INT NOT NULL DEFAULT 0, created_at DATETIME NOT NULL, updated_at DATETIME NOT NULL,"
    " UNIQUE KEY uq_acs_pair (follower_wallet, leader_wallet), KEY ix_acs_leader (leader_wallet, status))"
    " ENGINE=InnoDB DEFAULT CHARSET=utf8mb4",

    "CREATE TABLE IF NOT EXISTS auto_copy_positions ("
    " id INT AUTO_INCREMENT PRIMARY KEY, sub_id INT NOT NULL, user_id INT NOT NULL,"
    " follower_wallet VARCHAR(42) NOT NULL, leader_wallet VARCHAR(42) NOT NULL, coin VARCHAR(20) NOT NULL,"
    " market_id INT NOT NULL, side VARCHAR(8) NOT NULL, size DOUBLE NOT NULL, entry_px DOUBLE NOT NULL,"
    " margin_usd DOUBLE NOT NULL, leverage DOUBLE NOT NULL, sl_oid VARCHAR(40) NULL, sl_px DOUBLE NULL,"
    " mode VARCHAR(8) NOT NULL, status VARCHAR(10) NOT NULL, realized_pnl DOUBLE NOT NULL DEFAULT 0,"
    " opened_at DATETIME NOT NULL, closed_at DATETIME NULL, open_event_id INT NULL, close_reason VARCHAR(80) NULL,"
    " KEY ix_acp_sub (sub_id, coin, status), KEY ix_acp_follower (follower_wallet, market_id, status))"
    " ENGINE=InnoDB DEFAULT CHARSET=utf8mb4",

    "CREATE TABLE IF NOT EXISTS auto_copy_log ("
    " id BIGINT AUTO_INCREMENT PRIMARY KEY, sub_id INT NOT NULL, user_id INT NOT NULL,"
    " follower_wallet VARCHAR(42) NOT NULL, leader_wallet VARCHAR(42) NOT NULL, leader_event_id INT NOT NULL,"
    " action_key VARCHAR(16) NOT NULL, event_type VARCHAR(8) NOT NULL, coin VARCHAR(20) NOT NULL,"
    " market_id INT NOT NULL, side VARCHAR(8) NOT NULL, mode VARCHAR(8) NOT NULL,"
    " decision VARCHAR(8) NOT NULL, gate VARCHAR(32) NULL, reason VARCHAR(255) NULL, detail JSON NULL,"
    " created_at DATETIME NOT NULL,"
    " UNIQUE KEY uq_acl_once (sub_id, leader_event_id, action_key),"
    " KEY ix_acl_follower (follower_wallet, created_at)) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4",

    # v29 (2026-09-30): per-user master toggle + defaults for new subscriptions
    "CREATE TABLE IF NOT EXISTS auto_copy_user_settings ("
    " user_id INT NOT NULL PRIMARY KEY, enabled TINYINT(1) NOT NULL DEFAULT 1,"
    " defaults JSON NULL, updated_at DATETIME NOT NULL) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4",
]
