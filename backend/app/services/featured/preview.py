"""Static sample payload for the faded page behind the whitelist gate. These
numbers are the layout samples from design/Main.dc.html: not real wallets,
not real results, never served as data anywhere else."""

_ROW = {
    "venue": "hl", "wallet": "0x0000000000000000000000000000000000000000", "display_name": None, "label": "consistent",
    "one_big_win": False, "inactive": False, "qualified": True, "score": 91.0, "verdict": "Sample row. Request access to see real wallets.",
    "style": "intraday", "confidence": "medium", "win_rate_90d": 0.78, "trades_90d": 64, "net_pnl_90d": 41280.0, "net_pnl_30d": 12900.0,
    "net_pnl_all": 96000.0, "profit_factor_90d": 2.6, "profit_factor_30d": 2.9, "avg_win_usd": 1120.0, "avg_loss_usd": 430.0,
    "avg_win_pct_eq": 2.1, "avg_loss_pct_eq": 0.8, "max_drawdown_90d": 9.0, "trades_per_week": 5.0, "median_leverage": 4.0,
    "median_hold_sec": 7 * 3600, "top_coins": ["BTC", "ETH", "SOL"], "top_coins_on_perpl": 3, "open_count": 2, "unrealized_usd": 860.0,
    "last_trade_at": None, "last_trade_ms": None, "streak_now": "W5", "account_value": 52400.0, "copiable_pct": 100.0,
    "momentum": {"win_rate": "up", "pf": "up", "pnl": "up"}, "checks_passed": 8, "rank": 1, "active": True, "as_of": None,
}

_SAMPLES = [
    dict(_ROW),
    dict(_ROW, venue="perpl", label="asymmetric", score=88.0, win_rate_90d=0.54, trades_90d=41, net_pnl_90d=18640.0, net_pnl_30d=7210.0,
         profit_factor_90d=3.4, avg_win_usd=1940.0, avg_loss_usd=520.0, avg_win_pct_eq=6.4, avg_loss_pct_eq=1.7, max_drawdown_90d=14.0,
         trades_per_week=3.2, median_leverage=3.0, median_hold_sec=int(1.8 * 86400), top_coins=["BTC", "ETH"], top_coins_on_perpl=2,
         open_count=1, unrealized_usd=-140.0, streak_now="L2", rank=2, style="swing"),
    dict(_ROW, score=84.0, win_rate_90d=0.83, trades_90d=112, net_pnl_90d=6120.0, net_pnl_30d=1480.0, profit_factor_90d=1.9,
         avg_win_usd=140.0, avg_loss_usd=96.0, avg_win_pct_eq=1.1, avg_loss_pct_eq=0.7, max_drawdown_90d=6.0, trades_per_week=8.7,
         median_leverage=5.0, median_hold_sec=2 * 3600, top_coins=["HYPE", "SOL", "ZEC"], top_coins_on_perpl=2, open_count=0,
         unrealized_usd=None, streak_now="W3", rank=3),
    dict(_ROW, label="asymmetric", one_big_win=True, score=71.0, win_rate_90d=0.49, trades_90d=37, net_pnl_90d=212400.0, net_pnl_30d=-9800.0,
         profit_factor_90d=2.2, avg_win_usd=18300.0, avg_loss_usd=7900.0, avg_win_pct_eq=4.0, avg_loss_pct_eq=1.7, max_drawdown_90d=22.0,
         trades_per_week=2.9, median_leverage=8.0, median_hold_sec=int(3.1 * 86400), top_coins=["BTC", "XMR", "PUMP"], top_coins_on_perpl=2,
         open_count=3, unrealized_usd=4120.0, streak_now="L1", momentum={"win_rate": "down", "pf": "down", "pnl": "down"}, rank=4, style="swing"),
    dict(_ROW, venue="perpl", score=69.0, win_rate_90d=0.72, trades_90d=33, net_pnl_90d=2310.0, net_pnl_30d=940.0, profit_factor_90d=1.7,
         avg_win_usd=118.0, avg_loss_usd=74.0, avg_win_pct_eq=2.4, avg_loss_pct_eq=1.5, max_drawdown_90d=11.0, trades_per_week=2.6,
         median_leverage=2.0, median_hold_sec=14 * 3600, top_coins=["ETH", "MON"], top_coins_on_perpl=2, open_count=4, unrealized_usd=61.0,
         streak_now="W2", rank=5),
]

PREVIEW = {
    "sample": True,
    "rows": _SAMPLES,
    "count": len(_SAMPLES), "total": 412, "as_of": None, "metrics_as_of": "sample",
    "trades": [
        {"id": 0, "venue": "hl", "wallet": _ROW["wallet"], "coin": "BTC", "side": "long", "label": "consistent", "pnl_usd": 273.0,
         "pnl_pct_margin": 6.0, "avg_entry": 83120.0, "avg_exit": 84410.0, "n_adds": 2, "n_closes": 1, "hold_sec": 6 * 3600 + 36 * 60,
         "max_size_usd": 18200.0, "leverage": 4.0, "entry_taker": True, "added_to_winner": True, "averaged_down": False,
         "close_time": None, "on_perpl": True},
        {"id": 0, "venue": "perpl", "wallet": _ROW["wallet"], "coin": "ETH", "side": "short", "label": "asymmetric", "pnl_usd": -88.0,
         "pnl_pct_margin": -2.9, "avg_entry": 2604.0, "avg_exit": 2628.0, "n_adds": 0, "n_closes": 1, "hold_sec": 41 * 60,
         "max_size_usd": 9000.0, "leverage": 3.0, "entry_taker": True, "added_to_winner": False, "averaged_down": False,
         "close_time": None, "on_perpl": True},
    ],
    "open_now": [
        {"coin": "BTC", "long": 14, "short": 5, "size_usd": 1_840_000.0, "avg_entry_long": 83410.0, "smi": 61},
        {"coin": "ETH", "long": 5, "short": 8, "size_usd": 620_000.0, "avg_entry_short": 2598.0, "smi": 44},
        {"coin": "SOL", "long": 9, "short": 1, "size_usd": 210_000.0, "avg_entry_long": 141.2, "smi": 72},
    ],
}
