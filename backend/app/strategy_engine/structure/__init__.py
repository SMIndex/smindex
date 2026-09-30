"""Structure engine (doc 10 §2): swings, trend/BOS/CHoCH, displacement, zones,
pools, ranges, sweeps, VWAP/profile, sessions, day type, alignment table.

Pure functions over CLOSED candles only (candle ts = close time; a candle is
closed when ts <= now_ms). No I/O here — model_runner loads the rows."""
