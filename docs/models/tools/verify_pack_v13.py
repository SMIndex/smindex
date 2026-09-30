"""Verification pack for REPLAY-6M-v1.3 (read-only, spec v1.3 columns added). Reads perpl_replay (adverse) and
perpl_replay_n (neutral) and writes:
  /root/replay-v1.3-takes.csv   one row per fired model signal (both fill models)
  /root/replay-v1.3-pack.json   candles around 8 seeded-random M1 takes, table counts
Never writes to any database. usage: verify_pack_v13.py"""
import csv, datetime as dt, hashlib, json, os, random, re, sys
import pymysql
from urllib.parse import urlparse, unquote

SEED = 42
N_SAMPLE = 8
M15 = 900_000
WINDOW_START = 1773171900000   # 2026-03-10 19:45:00 UTC (first boundary evaluated = start + 15m)
WINDOW_END = 1788723900000     # 2026-09-06 19:45:00 UTC (last boundary)

p = urlparse(os.environ["DATABASE_URL"].replace("mysql+aiomysql://", "mysql://"))
def conn(db):
    return pymysql.connect(host=p.hostname, port=p.port or 3306, user=unquote(p.username),
                           password=unquote(p.password), charset="utf8mb4", database=db)

def iso(ms):
    return "" if ms is None else dt.datetime.fromtimestamp(int(ms) / 1000, dt.timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

COLS = ["model", "coin", "fill_model", "signal_ts", "level_type", "level_price", "direction", "entry_px",
        "entry_fill_ts", "stop_px", "t1_px", "exit_ts", "exit_px", "exit_reason", "r_multiple", "conviction",
        "size_tier", "stop_floor_applied", "stop_structural", "entry_requoted", "entry_px_orig", "reclaim_candles",
        "confirmation_used", "reasons_json"]   # last six = spec v1.3 (D-87/D-88/D-89)

rows_out = []
pack = {"seed": SEED, "samples": [], "tables": {}, "fired_without_trade": []}

for fill, db in (("adverse", "perpl_replay"), ("neutral", "perpl_replay_n")):
    c = conn(db); cur = c.cursor(pymysql.cursors.DictCursor)
    cur.execute("""SELECT s.id AS signal_id, s.model, s.asset, s.ts, s.level_type, s.level_price, s.direction AS s_dir,
                          s.conviction, s.size_tier, s.reasons_json, s.strategy, s.reclaim_candles, s.confirmation_used,
                          t.id AS trade_id, t.direction AS t_dir, t.entry_px, t.stop_px, t.fill_ts, t.exit_ts,
                          t.exit_reason, t.r_multiple, t.lifecycle_json, t.stop_floor_applied, t.entry_requoted, t.entry_px_orig
                   FROM strat_signals s LEFT JOIN strat_trades t ON t.signal_id = s.id
                   WHERE s.model IS NOT NULL AND s.fired = 1 ORDER BY s.model, s.asset, s.ts""")
    sig = cur.fetchall()
    for r in sig:
        life = json.loads(r["lifecycle_json"]) if r["lifecycle_json"] else {}
        ex = life.get("exit") or {}
        exit_reason = r["exit_reason"] or ""
        if r["trade_id"] is None:
            # which trade of the same model+coin was open (exit_ts NULL) when this signal was evaluated
            cur.execute("""SELECT id, fill_ts, exit_ts, exit_reason FROM strat_trades
                           WHERE strategy=%s AND asset=%s AND mode='paper'
                             AND JSON_EXTRACT(lifecycle_json,'$.placed_ts') <= %s AND (exit_ts IS NULL OR exit_ts >= %s)
                           ORDER BY id DESC LIMIT 1""", (r["strategy"], r["asset"], r["ts"], r["ts"] - 60_000))
            o = cur.fetchone()
            exit_reason = f"no_trade (trade #{o['id']} open at evaluation, its exit_ts {iso(o['exit_ts'])})" if o else "no_trade (no open trade found)"
            pack["fired_without_trade"].append({"fill_model": fill, "signal_id": r["signal_id"], "model": r["model"],
                                                "coin": r["asset"], "signal_ts": iso(r["ts"]), "open_trade": o and {k: (iso(v) if k.endswith("_ts") else v) for k, v in o.items()}})
        rows_out.append({
            "model": r["model"], "coin": r["asset"], "fill_model": fill, "signal_ts": iso(r["ts"]),
            "level_type": r["level_type"] or "", "level_price": r["level_price"] if r["level_price"] is not None else "",
            "direction": r["t_dir"] or r["s_dir"] or "", "entry_px": r["entry_px"] if r["entry_px"] is not None else "",
            "entry_fill_ts": iso(r["fill_ts"]), "stop_px": life.get("initial_stop", r["stop_px"] if r["stop_px"] is not None else ""),   # initial stop (strat_trades.stop_px is the LAST stop after BE/trail moves)
            "t1_px": life.get("t1", ""), "exit_ts": iso(r["exit_ts"]), "exit_px": ex.get("px", ""),
            "exit_reason": exit_reason, "r_multiple": r["r_multiple"] if r["r_multiple"] is not None else "",
            "conviction": r["conviction"], "size_tier": r["size_tier"] or "",
            "stop_floor_applied": "" if r["stop_floor_applied"] is None else int(r["stop_floor_applied"]),
            "stop_structural": life.get("stop_structural", ""),
            "entry_requoted": "" if r["entry_requoted"] is None else int(r["entry_requoted"]),
            "entry_px_orig": "" if r["entry_px_orig"] is None else r["entry_px_orig"],
            "reclaim_candles": "" if r["reclaim_candles"] is None else int(r["reclaim_candles"]),
            "confirmation_used": "" if r["confirmation_used"] is None else int(r["confirmation_used"]),
            "reasons_json": r["reasons_json"] or "",
            "_signal_id": r["signal_id"], "_trade_id": r["trade_id"], "_ts": r["ts"], "_life": life,
        })

    if fill == "adverse":
        # 8 seeded-random M1 takes (trades) -> 12 fifteen-minute candles around the signal boundary
        m1 = sorted([x for x in rows_out if x["fill_model"] == "adverse" and x["model"] == "M1" and x["_trade_id"]], key=lambda x: x["_trade_id"])
        picks = random.Random(SEED).sample(m1, N_SAMPLE)
        for x in sorted(picks, key=lambda x: x["_ts"]):
            b = (x["_ts"] // M15) * M15                       # signal boundary (candle close time)
            cur.execute("""SELECT ts, o, h, l, c, v FROM strat_replay_candles
                           WHERE source='binance' AND coin=%s AND tf='15m' AND ts > %s AND ts <= %s ORDER BY ts""",
                        (x["coin"], b - 9 * M15, b + 3 * M15))   # candle ts = close time - 1 ms; 8 before the trigger candle, trigger, 3 after
            cands = cur.fetchall()
            setup = x["_life"].get("setup", {})
            pack["samples"].append({
                "trade_id": x["_trade_id"], "signal_id": x["_signal_id"], "coin": x["coin"], "direction": x["direction"],
                "signal_ts": x["signal_ts"], "level_type": x["level_type"], "level_price": x["level_price"],
                "wick_ts": iso(setup.get("wick_ts")), "wick_price": setup.get("wick_price"),
                "reclaim_ts": iso(setup.get("reclaim_ts")), "entry_px": x["entry_px"], "stop_px": x["stop_px"], "t1_px": x["t1_px"],
                "entry_fill_ts": x["entry_fill_ts"], "exit_ts": x["exit_ts"], "exit_px": x["exit_px"],
                "exit_reason": x["exit_reason"], "r_multiple": x["r_multiple"], "conviction": x["conviction"],
                "confirmation_ts": iso(setup.get("confirmation_ts")), "reclaim_candles": x["reclaim_candles"], "confirmation_used": x["confirmation_used"],
                "stop_floor_applied": x["stop_floor_applied"], "stop_structural": x["stop_structural"],
                "entry_requoted": x["entry_requoted"], "entry_px_orig": x["entry_px_orig"], "post_only": x["_life"].get("post_only"),
                "candles": [{"ts_close": iso(cd["ts"]), "o": cd["o"], "h": cd["h"], "l": cd["l"], "c": cd["c"],
                             "tag": ("SIGNAL" if b - M15 < cd["ts"] <= b else "") + (" wick" if setup.get("wick_ts") and abs(int(setup["wick_ts"]) - cd["ts"]) < 1000 else "")
                                    + (" reclaim" if setup.get("reclaim_ts") and abs(int(setup["reclaim_ts"]) - cd["ts"]) < 1000 else "")
                                    + (" confirm" if setup.get("confirmation_ts") and setup.get("confirmation_used") and abs(int(setup["confirmation_ts"]) - cd["ts"]) < 1000 else "")}
                            for cd in cands],
            })
        # tag the fill / exit candles explicitly
        for smp in pack["samples"]:
            cur.execute("SELECT fill_ts, exit_ts FROM strat_trades WHERE id=%s", (smp["trade_id"],))
            f = cur.fetchone()
            for cd in smp["candles"]:
                close_ms = int(dt.datetime.strptime(cd["ts_close"], "%Y-%m-%d %H:%M:%S").replace(tzinfo=dt.timezone.utc).timestamp() * 1000)
                if f["fill_ts"] and close_ms - M15 < int(f["fill_ts"]) <= close_ms:
                    cd["tag"] = (cd["tag"] + " FILL").strip()
                if f["exit_ts"] and close_ms - M15 < int(f["exit_ts"]) <= close_ms:
                    cd["tag"] = (cd["tag"] + " EXIT").strip()

        # the same eight v1.2 trades (seed 42 over strat_trades_v12, the v1.2 rows kept in the replay DB) under v1.3:
        # what the v1.3 pass did at that coin + signal boundary (signal row, fired?, trade if any)
        cur.execute("""SELECT t.id, t.asset, t.signal_id, s.ts, t.entry_px, t.r_multiple, t.exit_reason FROM strat_trades_v12 t
                       JOIN strat_signals_v12 s ON s.id = t.signal_id WHERE t.model='M1' ORDER BY t.id""")
        v12 = cur.fetchall()
        pack["v12_samples_under_v13"] = []
        for o in sorted(random.Random(SEED).sample(v12, N_SAMPLE), key=lambda o: o["ts"]):
            cur.execute("""SELECT s.id, s.ts, s.fired, s.direction, s.conviction, s.reason, s.reclaim_candles, s.confirmation_used,
                                  t.id AS trade_id, t.entry_px, t.entry_px_orig, t.entry_requoted, t.stop_floor_applied, t.fill_ts, t.exit_reason, t.r_multiple
                           FROM strat_signals s LEFT JOIN strat_trades t ON t.signal_id = s.id
                           WHERE s.model='M1' AND s.asset=%s AND s.ts BETWEEN %s AND %s ORDER BY s.ts""", (o["asset"], o["ts"] - 15 * M15, o["ts"] + 3 * M15))
            near = cur.fetchall()
            pack["v12_samples_under_v13"].append({
                "v12_trade_id": o["id"], "coin": o["asset"], "v12_signal_ts": iso(o["ts"]), "v12_entry_px": o["entry_px"],
                "v12_r_multiple": o["r_multiple"], "v12_exit_reason": o["exit_reason"],
                "v13_rows_within_-15..+3_candles": [{"signal_id": n["id"], "signal_ts": iso(n["ts"]), "fired": int(n["fired"] or 0), "direction": n["direction"], "conviction": n["conviction"],
                                                     "reason": (n["reason"] or "")[:160], "reclaim_candles": n["reclaim_candles"], "confirmation_used": n["confirmation_used"],
                                                     "trade_id": n["trade_id"], "entry_px": n["entry_px"], "entry_px_orig": n["entry_px_orig"], "entry_requoted": n["entry_requoted"],
                                                     "stop_floor_applied": n["stop_floor_applied"], "fill_ts": iso(n["fill_ts"]), "exit_reason": n["exit_reason"], "r_multiple": n["r_multiple"]}
                                                    for n in near if n["fired"] or n["trade_id"]],
            })

        # underlying tables over the replay window (+ totals incl. warm-up)
        T = {}
        for tf in ("15m", "1h", "4h", "1d"):
            cur.execute("""SELECT coin, COUNT(*) n, MIN(ts) mn, MAX(ts) mx FROM strat_replay_candles
                           WHERE source='binance' AND tf=%s AND ts BETWEEN %s AND %s GROUP BY coin""", (tf, WINDOW_START, WINDOW_END))
            for q in cur.fetchall():
                T[f"strat_replay_candles binance {tf} {q['coin']} (window)"] = {"rows": q["n"], "min_ts": iso(q["mn"]), "max_ts": iso(q["mx"])}
        cur.execute("SELECT coin, tf, COUNT(*) n, MIN(ts) mn, MAX(ts) mx FROM strat_replay_candles WHERE source='binance' GROUP BY coin, tf")
        for q in cur.fetchall():
            T[f"strat_replay_candles binance {q['tf']} {q['coin']} (whole table incl. warm-up)"] = {"rows": q["n"], "min_ts": iso(q["mn"]), "max_ts": iso(q["mx"])}
        cur.execute("SELECT coin, source, COUNT(*) n, MIN(ts) mn, MAX(ts) mx FROM strat_replay_oi WHERE ts BETWEEN %s AND %s GROUP BY coin, source", (WINDOW_START, WINDOW_END))
        for q in cur.fetchall():
            T[f"strat_replay_oi {q['source']} {q['coin']} (window)"] = {"rows": q["n"], "min_ts": iso(q["mn"]), "max_ts": iso(q["mx"])}
        cur.execute("SELECT coin, COALESCE(source,'live') src, COUNT(*) n, MIN(ts) mn, MAX(ts) mx FROM strat_liquidations WHERE ts BETWEEN %s AND %s GROUP BY coin, source", (WINDOW_START, WINDOW_END))
        for q in cur.fetchall():
            T[f"strat_liquidations {q['src']} {q['coin']} (window)"] = {"rows": q["n"], "min_ts": iso(q["mn"]), "max_ts": iso(q["mx"])}
        cur.execute("SELECT coin, source, COUNT(*) n, MIN(ts) mn, MAX(ts) mx FROM strat_liq_map_hist WHERE ts BETWEEN %s AND %s GROUP BY coin, source", (WINDOW_START, WINDOW_END))
        for q in cur.fetchall():
            T[f"strat_liq_map_hist {q['source']} {q['coin']} (window)"] = {"rows": q["n"], "min_ts": iso(q["mn"]), "max_ts": iso(q["mx"])}
        cur.execute("SELECT coin, `key`, COUNT(*) n, SUM(value IS NULL) nulls, MIN(as_of) mn, MAX(as_of) mx FROM strat_calibration_hist GROUP BY coin, `key`")
        for q in cur.fetchall():
            T[f"strat_calibration_hist {q['coin']} {q['key']}"] = {"rows": q["n"], "null_rows": int(q["nulls"]), "min_ts": iso(q["mn"]), "max_ts": iso(q["mx"])}
        cur.execute("SELECT COUNT(*) n, MIN(ts) mn, MAX(ts) mx, SUM(fired) f FROM strat_signals WHERE model IS NOT NULL")
        q = cur.fetchone(); T["strat_signals (model rows)"] = {"rows": q["n"], "fired": int(q["f"]), "min_ts": iso(q["mn"]), "max_ts": iso(q["mx"])}
        cur.execute("SELECT COUNT(*) n FROM strat_trades WHERE model IS NOT NULL"); T["strat_trades (model rows)"] = {"rows": cur.fetchone()["n"]}
        pack["tables"][db] = T
    else:
        cur.execute("SELECT COUNT(*) n, SUM(fired) f FROM strat_signals WHERE model IS NOT NULL"); q = cur.fetchone()
        cur.execute("SELECT COUNT(*) n FROM strat_trades WHERE model IS NOT NULL"); q2 = cur.fetchone()
        pack["tables"][db] = {"strat_signals (model rows)": {"rows": q["n"], "fired": int(q["f"])}, "strat_trades (model rows)": {"rows": q2["n"]},
                              "note": "clone of perpl_replay feed tables (v12_setup.py), only signals/trades differ"}
    c.close()

with open("/root/replay-v1.3-takes.csv", "w", newline="", encoding="utf-8") as f:
    w = csv.DictWriter(f, fieldnames=COLS, extrasaction="ignore"); w.writeheader()
    for x in rows_out: w.writerow(x)
pack["csv_rows"] = len(rows_out)
pack["csv_sha256"] = hashlib.sha256(open("/root/replay-v1.3-takes.csv", "rb").read()).hexdigest()
with open("/root/replay-v1.3-pack.json", "w", encoding="utf-8") as f:
    json.dump(pack, f, indent=1, default=str)
print("csv rows", len(rows_out), "sha256", pack["csv_sha256"])
print("fired without trade", len(pack["fired_without_trade"]))
