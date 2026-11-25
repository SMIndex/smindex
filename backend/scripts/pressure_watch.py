#!/usr/bin/env python3
"""Pressure Watch: positioning before the move, per coin.

Watches BTC ETH SOL HYPE ZEC MON and the RWA markets xyz:NVDA xyz:BRENTOIL
xyz:AMD io:ANTH on Hyperliquid for positions built before a move, and for
positions built against the trend (possible reversal).

Read-only against the venue (info reads only, paced under the shared weight
window, background level). Writes only its own pw_* tables and one static page.

    python scripts/pressure_watch.py collect     # every 5 min: OI, funding, premium
    python scripts/pressure_watch.py sweep       # every 10 min: rotating position checks (leaderboard + active traders)
    python scripts/pressure_watch.py universe    # daily: account values from the public leaderboard
    python scripts/pressure_watch.py cycle       # every 20 min: signals, page, alerts
    python scripts/pressure_watch.py backtest    # tests every signal on stored history
    python scripts/pressure_watch.py funding     # one-off funding history backfill
"""
from __future__ import annotations

import bisect
import pickle
import html
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import defaultdict, deque
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pymysql

BACKEND = Path(__file__).resolve().parents[1]
ENV = BACKEND / ".env"
OUT_DIR = BACKEND.parent / "frontend" / "dist" / "research"
PAGE = OUT_DIR / "pressure.html"
STATE_DIR = BACKEND / "data" / "pressure_watch"
BT_FILE = STATE_DIR / "backtest.json"
HL_URL = "https://api.hyperliquid.xyz/info"
HEALTH = "http://127.0.0.1:8002/health"
WINDOW_LIMIT = 600

CRYPTO = ["BTC", "ETH", "SOL", "HYPE", "ZEC", "MON"]
RWA = ["xyz:NVDA", "xyz:BRENTOIL", "xyz:AMD", "io:ANTH"]
COINS = CRYPTO + RWA
LABEL = {"BTC": "BTC", "ETH": "ETH", "SOL": "SOL", "HYPE": "HYPE", "ZEC": "ZEC", "MON": "MON",
         "xyz:NVDA": "NVIDIA", "xyz:BRENTOIL": "Brent oil", "xyz:AMD": "AMD", "io:ANTH": "Anthropic"}
DEXES = {"": CRYPTO, "xyz": ["xyz:NVDA", "xyz:BRENTOIL", "xyz:AMD"], "io": ["io:ANTH"]}
# single-event alert floors in USD
BIG_ALERT = {"BTC": 2_000_000, "ETH": 1_000_000, "SOL": 500_000, "HYPE": 500_000, "ZEC": 300_000,
             "MON": 150_000, "xyz:NVDA": 300_000, "xyz:BRENTOIL": 300_000, "xyz:AMD": 150_000,
             "io:ANTH": 100_000}
NOW = datetime.now(timezone.utc).replace(tzinfo=None, microsecond=0)


# ------------------------------------------------------------------ plumbing
def env():
    e = {}
    for line in ENV.read_text(encoding="utf-8").splitlines():
        if "=" in line and not line.strip().startswith("#"):
            k, v = line.split("=", 1)
            e[k.strip()] = v.strip()
    return e


E = env()


def db():
    body = E["DATABASE_URL"].split("://", 1)[1]
    creds, hostpart = body.split("@", 1)
    user, pwd = creds.split(":", 1)
    hostport, name = hostpart.split("/", 1)
    host, port = hostport.split(":")
    return pymysql.connect(host=host, port=int(port), user=user, password=urllib.parse.unquote(pwd),
                           database=name.split("?")[0], charset="utf8mb4", autocommit=True)


CONN = None


def q(sql, args=None):
    global CONN
    if CONN is None:
        CONN = db()
    with CONN.cursor() as c:
        c.execute(sql, args)
        return c.fetchall()


def qmany(sql, rows):
    global CONN
    if CONN is None:
        CONN = db()
    if not rows:
        return
    with CONN.cursor() as c:
        for i in range(0, len(rows), 500):
            c.executemany(sql, rows[i:i + 500])


SCHEMA = [
    """CREATE TABLE IF NOT EXISTS pw_ctx (
        coin varchar(32) NOT NULL, t datetime NOT NULL, mark double, oracle double,
        oi_coins double, funding double, premium double, vol24 double,
        PRIMARY KEY (coin, t)) ENGINE=InnoDB""",
    """CREATE TABLE IF NOT EXISTS pw_funding (
        coin varchar(32) NOT NULL, t datetime NOT NULL, rate double, premium double,
        PRIMARY KEY (coin, t)) ENGINE=InnoDB""",
    """CREATE TABLE IF NOT EXISTS pw_sweep (
        cycle_ts datetime NOT NULL, dex varchar(8) NOT NULL, wallet char(42) NOT NULL,
        account_value double, ok tinyint NOT NULL,
        PRIMARY KEY (cycle_ts, dex, wallet), KEY ix_w (wallet, cycle_ts)) ENGINE=InnoDB""",
    """CREATE TABLE IF NOT EXISTS pw_positions (
        cycle_ts datetime NOT NULL, coin varchar(32) NOT NULL, wallet char(42) NOT NULL,
        side varchar(5) NOT NULL, size double NOT NULL, notional double NOT NULL,
        entry_px double, leverage double, liq_px double, margin_used double, upnl double,
        margin_mode varchar(8),
        PRIMARY KEY (cycle_ts, coin, wallet), KEY ix_cw (coin, wallet, cycle_ts)) ENGINE=InnoDB""",
    """CREATE TABLE IF NOT EXISTS pw_events (
        id bigint NOT NULL AUTO_INCREMENT, detected_at datetime NOT NULL, wallet char(42) NOT NULL,
        coin varchar(32) NOT NULL, event_type varchar(12) NOT NULL, side varchar(5) NOT NULL,
        size_before double, size_after double, notional_delta double, ref_px double,
        margin_before double, margin_after double,
        PRIMARY KEY (id), KEY ix_ct (coin, detected_at)) ENGINE=InnoDB""",
    """CREATE TABLE IF NOT EXISTS pw_alerts (
        id bigint NOT NULL AUTO_INCREMENT, t datetime NOT NULL, coin varchar(32) NOT NULL,
        kind varchar(24) NOT NULL, akey varchar(128) NOT NULL, body text,
        PRIMARY KEY (id), KEY ix_k (akey, t)) ENGINE=InnoDB""",
    """CREATE TABLE IF NOT EXISTS pw_wallets (
        wallet char(42) NOT NULL, account_value double, lb_at datetime,
        PRIMARY KEY (wallet), KEY ix_av (lb_at, account_value)) ENGINE=InnoDB""",
    """CREATE TABLE IF NOT EXISTS pw_check (
        dex varchar(8) NOT NULL, wallet char(42) NOT NULL, last_at datetime NOT NULL, ok tinyint NOT NULL,
        PRIMARY KEY (dex, wallet)) ENGINE=InnoDB""",
    """CREATE TABLE IF NOT EXISTS pw_state (
        dex varchar(8) NOT NULL, wallet char(42) NOT NULL, coin varchar(32) NOT NULL,
        side varchar(5) NOT NULL, size double NOT NULL, notional double NOT NULL,
        entry_px double, leverage double, liq_px double, margin_used double, upnl double,
        margin_mode varchar(8), account_value double, checked_at datetime NOT NULL,
        PRIMARY KEY (dex, wallet, coin), KEY ix_c (coin)) ENGINE=InnoDB""",
    """CREATE TABLE IF NOT EXISTS pw_readings (
        t datetime NOT NULL, coin varchar(32) NOT NULL, verdict varchar(40) NOT NULL,
        score double, body json, PRIMARY KEY (coin, t)) ENGINE=InnoDB""",
]


def ensure_schema():
    for s in SCHEMA:
        q(s)


_own = deque()


_health = {"at": 0.0, "v": None}


def health_used():
    """The API's own window use, re-read at most every 5 s (the endpoint can
    take seconds to answer when the app is busy)."""
    if time.time() - _health["at"] < 5 and _health["v"] is not None:
        return _health["v"]
    try:
        d = json.loads(urllib.request.urlopen(HEALTH, timeout=10).read())
        h = d.get("hl_weight", d)
        _health["v"] = float(h.get("window_weight_used", 0))
    except Exception:  # noqa: BLE001
        _health["v"] = None
    _health["at"] = time.time()
    return _health["v"]


def hl(body, w):
    """One info read. Waits until the API's window plus this job's own last
    minute plus w fits under the background limit."""
    for _ in range(240):
        now = time.time()
        while _own and now - _own[0][0] > 60:
            _own.popleft()
        mine = sum(x[1] for x in _own)
        used = health_used()
        if used is not None and used + mine + w <= WINDOW_LIMIT:
            break
        time.sleep(3)
    else:
        raise RuntimeError("weight window never had room")
    req = urllib.request.Request(HL_URL, data=json.dumps(body).encode(),
                                 headers={"content-type": "application/json", "user-agent": "smindex-pressure"})
    for _ in range(3):
        try:
            resp = json.loads(urllib.request.urlopen(req, timeout=30).read())
            _own.append((time.time(), w))
            time.sleep(0.2)
            return resp
        except urllib.error.HTTPError as e:
            if e.code == 429:
                _own.append((time.time(), w))
                time.sleep(60)
                continue
            raise
    raise RuntimeError("HL 429 x3")


def f(v, d=None):
    try:
        if v is None:
            return d
        return float(v)
    except (TypeError, ValueError):
        return d


# ------------------------------------------------------------------ collect
def collect():
    ensure_schema()
    t = NOW.replace(second=0)
    rows = []
    for dex, coins in DEXES.items():
        body = {"type": "metaAndAssetCtxs"}
        if dex:
            body["dex"] = dex
        m = hl(body, 20)
        for u, c in zip(m[0]["universe"], m[1]):
            name = u["name"]
            if dex and ":" not in name:
                name = f"{dex}:{name}"
            if name in coins:
                rows.append((name, t, f(c.get("markPx")), f(c.get("oraclePx")), f(c.get("openInterest")),
                             f(c.get("funding")), f(c.get("premium")), f(c.get("dayNtlVlm"))))
    qmany("INSERT INTO pw_ctx (coin,t,mark,oracle,oi_coins,funding,premium,vol24) VALUES (%s,%s,%s,%s,%s,%s,%s,%s) "
          "ON DUPLICATE KEY UPDATE mark=VALUES(mark)", rows)
    q("DELETE FROM pw_ctx WHERE t < %s", (NOW - timedelta(days=150),))
    print("ctx rows", len(rows))


def funding_backfill():
    ensure_schema()
    start = int((NOW - timedelta(days=100)).timestamp() * 1000)
    for coin in COINS:
        last = q("SELECT MAX(t) FROM pw_funding WHERE coin=%s", (coin,))[0][0]
        s = int(last.replace(tzinfo=timezone.utc).timestamp() * 1000) + 1 if last else start
        n = 0
        while True:
            r = hl({"type": "fundingHistory", "coin": coin, "startTime": s}, 20 + 1)
            if not r:
                break
            rows = [(coin, datetime.utcfromtimestamp(x["time"] / 1000).replace(minute=0, second=0, microsecond=0),
                     f(x.get("fundingRate")), f(x.get("premium"))) for x in r]
            qmany("INSERT IGNORE INTO pw_funding (coin,t,rate,premium) VALUES (%s,%s,%s,%s)", rows)
            n += len(rows)
            if len(r) < 500:
                break
            s = r[-1]["time"] + 1
        print("funding", coin, n)


# ------------------------------------------------------------------ RWA sweep
# ------------------------------------------------------------------ wallet universe and wide sweep
LB_URL = "https://stats-data.hyperliquid.xyz/Mainnet/leaderboard"
# (dex, source, rule, interval minutes). Most overdue first, inside a weight budget per run.
SWEEP_PLAN = [
    ("", "fills", 200, 20),          # top 200 by recent volume on the 6 crypto coins
    ("", "lb", 1_000_000, 60),       # every account worth $1M+
    ("", "lb", 250_000, 480),        # $250K to $1M
    ("xyz", "fills", 160, 20),       # top 160 by recent volume on NVDA, BRENT, AMD
    ("xyz", "lb", 2_000_000, 360),   # every account worth $2M+
    ("io", "fills", 50, 20),         # top 50 on Anthropic
]
SWEEP_BUDGET = int(os.environ.get("PW_SWEEP_BUDGET", "1300"))   # weight per 10 min run = 130 a minute
SWEEP_SECONDS = 540
SWEEP_WINDOW_LIMIT = 500


def universe():
    """Account values of every Hyperliquid account on the public leaderboard
    (stats-data host: no info-API weight), streamed, never loaded whole."""
    import tempfile
    import ijson
    ensure_schema()
    req = urllib.request.Request(LB_URL, headers={"user-agent": "smindex-pressure"})
    with tempfile.TemporaryFile() as tmp:
        with urllib.request.urlopen(req, timeout=120) as r:
            while True:
                b = r.read(1 << 20)
                if not b:
                    break
                tmp.write(b)
        tmp.seek(0)
        rows = []
        for item in ijson.items(tmp, "leaderboardRows.item"):
            av = f(item.get("accountValue"), 0.0)
            if av >= 100_000:
                rows.append((item["ethAddress"].lower(), av, NOW))
    qmany("INSERT INTO pw_wallets (wallet, account_value, lb_at) VALUES (%s,%s,%s) "
          "ON DUPLICATE KEY UPDATE account_value=VALUES(account_value), lb_at=VALUES(lb_at)", rows)
    print("universe", len(rows), "accounts worth $100K+")


def plan_wallets():
    """{(dex, wallet): interval_min} from SWEEP_PLAN, the shortest interval winning."""
    want = {}
    lb_cut = NOW - timedelta(days=3)
    for dex, src, rule, every in SWEEP_PLAN:
        if src == "fills":
            coins = CRYPTO if dex == "" else DEXES[dex]
            ph = ",".join(["%s"] * len(coins))
            since = int((NOW - timedelta(days=3 if dex == "" else 14)).timestamp() * 1000)
            ws = [r[0].lower() for r in q(
                f"SELECT wallet_address, SUM(px*sz) v FROM hl_fill_events WHERE coin IN ({ph}) AND ts_ms>=%s "
                f"GROUP BY wallet_address ORDER BY v DESC LIMIT %s", (*coins, since, rule))]
        else:
            hi = {250_000: 1_000_000}.get(rule)
            sql = "SELECT wallet FROM pw_wallets WHERE lb_at>=%s AND account_value>=%s"
            args = [lb_cut, rule]
            if hi:
                sql += " AND account_value<%s"
                args.append(hi)
            ws = [r[0] for r in q(sql, args)]
        for w in ws:
            k = (dex, w)
            want[k] = min(want.get(k, every), every)
    # anything we hold a position for keeps being checked at least every 2 h
    for dex, w in q("SELECT DISTINCT dex, wallet FROM pw_state"):
        k = (dex, w)
        want[k] = min(want.get(k, 120), 120)
    # main-dex wallets the analytics sweep already reads every 20 min are skipped there
    c = q("SELECT MAX(cycle_ts) FROM analytics_wallet_state")[0][0]
    if c:
        for (w,) in q("SELECT wallet FROM analytics_wallet_state WHERE cycle_ts=%s", (c,)):
            want.pop(("", w.lower()), None)
    return want


def wide_sweep():
    global WINDOW_LIMIT
    WINDOW_LIMIT = SWEEP_WINDOW_LIMIT
    ensure_schema()
    t0 = time.time()
    want = plan_wallets()
    last = {(d, w): (t, ok) for d, w, t, ok in q("SELECT dex, wallet, last_at, ok FROM pw_check")}
    due = []
    for k, every in want.items():
        lt = last.get(k)
        if lt is None:
            due.append((NOW, k))   # first check: after every overdue tracked wallet
        elif NOW - lt[0] >= timedelta(minutes=every - 2):
            due.append((lt[0] + timedelta(minutes=every), k))
    due.sort()
    spent, n_ok, n_ev = 0, 0, 0
    for _, (dex, w) in due:
        if spent + 2 > SWEEP_BUDGET or time.time() - t0 > SWEEP_SECONDS:
            break
        body = {"type": "clearinghouseState", "user": w}
        if dex:
            body["dex"] = dex
        now = datetime.now(timezone.utc).replace(tzinfo=None, microsecond=0)
        spent += 2
        try:
            st = hl(body, 2)
        except Exception:  # noqa: BLE001
            q("INSERT INTO pw_check (dex,wallet,last_at,ok) VALUES (%s,%s,%s,0) "
              "ON DUPLICATE KEY UPDATE last_at=VALUES(last_at), ok=0", (dex, w, now))
            continue
        n_ok += 1
        n_ev += apply_state(dex, w, st, now, baseline=(dex, w) not in last or not last[(dex, w)][1])
    print(f"sweep due {len(due)} of {len(want)} planned, checked {n_ok}, weight {spent}, events {n_ev}, {time.time() - t0:.0f}s")
    q("DELETE FROM pw_events WHERE detected_at < %s", (NOW - timedelta(days=150),))


def apply_state(dex, w, st, now, baseline):
    coins = CRYPTO if dex == "" else DEXES[dex]
    av = f((st.get("marginSummary") or {}).get("accountValue"))
    cur, rows, marks = {}, [], {}
    for ap in st.get("assetPositions") or []:
        p = ap.get("position") or {}
        coin = p.get("coin", "")
        if dex and ":" not in coin:
            coin = f"{dex}:{coin}"
        if coin not in coins:
            continue
        szi = f(p.get("szi"), 0.0)
        if not szi:
            continue
        notional = abs(f(p.get("positionValue"), 0.0))
        side = "long" if szi > 0 else "short"
        lev = p.get("leverage") or {}
        cur[coin] = {"side": side, "size": abs(szi), "notional": notional, "entry": f(p.get("entryPx")),
                     "margin": f(p.get("marginUsed"))}
        marks[coin] = notional / abs(szi)
        rows.append((dex, w, coin, side, abs(szi), notional, f(p.get("entryPx")), f(lev.get("value")),
                     f(p.get("liquidationPx")), f(p.get("marginUsed")), f(p.get("unrealizedPnl")), lev.get("type"), av, now))
    prev = {}
    for coin, side, size, notional, entry, marg in q(
            "SELECT coin, side, size, notional, entry_px, margin_used FROM pw_state WHERE dex=%s AND wallet=%s", (dex, w)):
        prev[coin] = {"side": side, "size": size, "notional": notional, "entry": entry, "margin": marg}
    events = [] if baseline else diff_positions(now, w, prev, cur, marks)
    gone = [c for c in prev if c not in cur]
    if gone:
        ph = ",".join(["%s"] * len(gone))
        q(f"DELETE FROM pw_state WHERE dex=%s AND wallet=%s AND coin IN ({ph})", (dex, w, *gone))
    qmany("INSERT INTO pw_state (dex,wallet,coin,side,size,notional,entry_px,leverage,liq_px,margin_used,upnl,margin_mode,"
          "account_value,checked_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) ON DUPLICATE KEY UPDATE side=VALUES(side), "
          "size=VALUES(size), notional=VALUES(notional), entry_px=VALUES(entry_px), leverage=VALUES(leverage), "
          "liq_px=VALUES(liq_px), margin_used=VALUES(margin_used), upnl=VALUES(upnl), margin_mode=VALUES(margin_mode), "
          "account_value=VALUES(account_value), checked_at=VALUES(checked_at)", rows)
    qmany("INSERT INTO pw_events (detected_at,wallet,coin,event_type,side,size_before,size_after,notional_delta,ref_px,"
          "margin_before,margin_after) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)", events)
    q("INSERT INTO pw_check (dex,wallet,last_at,ok) VALUES (%s,%s,%s,1) "
      "ON DUPLICATE KEY UPDATE last_at=VALUES(last_at), ok=1", (dex, w, now))
    return len(events)


def seed_state_from_snapshots():
    """One-off: carry the first RWA sweeps (pw_positions / pw_sweep) into
    pw_state / pw_check so those wallets keep their baseline."""
    for dex in ("xyz", "io"):
        c = q("SELECT MAX(cycle_ts) FROM pw_sweep WHERE dex=%s", (dex,))[0][0]
        if not c:
            continue
        q("INSERT IGNORE INTO pw_check (dex,wallet,last_at,ok) SELECT dex, wallet, cycle_ts, ok FROM pw_sweep "
          "WHERE dex=%s AND cycle_ts=%s", (dex, c))
        q("INSERT IGNORE INTO pw_state (dex,wallet,coin,side,size,notional,entry_px,leverage,liq_px,margin_used,upnl,"
          "margin_mode,checked_at) SELECT %s, wallet, coin, side, size, notional, entry_px, leverage, liq_px, margin_used, "
          "upnl, margin_mode, cycle_ts FROM pw_positions WHERE cycle_ts=%s AND coin LIKE %s", (dex, c, dex + ":%"))
    print("seeded", q("SELECT COUNT(*) FROM pw_state")[0][0], "positions")


def diff_positions(t, w, prev, cur, marks):
    """Same event vocabulary as analytics_flow_events, plus MARGIN_ADD / MARGIN_CUT
    when collateral moves by 10%+ while size stays within 1%."""
    ev = []
    for coin, c in cur.items():
        p = prev.get(coin)
        px = marks.get(coin) or (c["notional"] / c["size"] if c["size"] else None)
        if p is None:
            ev.append((t, w, coin, "OPEN", c["side"], 0.0, c["size"], c["notional"], c["entry"], None, c["margin"]))
        elif p["side"] != c["side"]:
            ev.append((t, w, coin, "FLIP", c["side"], p["size"], c["size"], (p["size"] + c["size"]) * (px or 0), px, p["margin"], c["margin"]))
        else:
            d = c["size"] - p["size"]
            if abs(d) > 1e-4 * max(p["size"], 1e-9) and abs(d) * (px or 0) >= 1000:
                ev.append((t, w, coin, "INCREASE" if d > 0 else "REDUCE", c["side"], p["size"], c["size"], d * (px or 0), px,
                           p["margin"], c["margin"]))
            elif p["margin"] and c["margin"] and abs(d) <= 0.01 * p["size"]:
                dm = c["margin"] - p["margin"]
                if abs(dm) >= 0.10 * p["margin"] and abs(dm) >= 1000:
                    ev.append((t, w, coin, "MARGIN_ADD" if dm > 0 else "MARGIN_CUT", c["side"], p["size"], c["size"], 0.0, px,
                               p["margin"], c["margin"]))
    for coin, p in prev.items():
        if coin not in cur:
            px = marks.get(coin) or (p["notional"] / p["size"] if p["size"] else None)
            ev.append((t, w, coin, "CLOSE", p["side"], p["size"], 0.0, -p["size"] * (px or 0), px, p["margin"], None))
    return ev


# ------------------------------------------------------------------ features
def signed_usd(etype, side, nd, sb, sa, px):
    """Signed USD flow of one event: + means buying (adds long or covers short)."""
    if etype == "FLIP":
        if sb is not None and sa is not None and px:
            mag = (sb + sa) * px
        else:
            mag = abs(nd or 0)
        return mag if side == "long" else -mag
    if etype in ("OPEN", "INCREASE", "CLOSE", "REDUCE"):
        nd = nd or 0.0
        return nd if side == "long" else -nd
    return 0.0


def load_events(coin, since):
    """[(ts, wallet, etype, side, usd_signed, usd_abs, sb, sa, px)] for crypto from
    analytics_flow_events, for RWA from pw_events."""
    if coin in CRYPTO:
        rows = q("SELECT detected_at, wallet, event_type, side, size_before, size_after, notional_delta, ref_px "
                 "FROM analytics_flow_events WHERE asset=%s AND detected_at>=%s ORDER BY detected_at", (coin, since))
    else:
        rows = q("SELECT detected_at, wallet, event_type, side, size_before, size_after, notional_delta, ref_px "
                 "FROM pw_events WHERE coin=%s AND detected_at>=%s ORDER BY detected_at", (coin, since))
    out = []
    for ts, w, et, side, sb, sa, nd, px in rows:
        su = signed_usd(et, side, f(nd), f(sb), f(sa), f(px))
        out.append((ts, w, et, side, su, abs(su), f(sb), f(sa), f(px)))
    return out


def load_events_cached(coin, since):
    """Crypto events for the live cycle from a local cache: the first run reads
    the window once, later runs read only rows with a higher id (primary key
    range, cheap)."""
    if coin not in CRYPTO:
        return load_events(coin, since)
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    fp = STATE_DIR / f"ev_{coin}.pkl"
    cache = None
    if fp.exists():
        try:
            cache = pickle.loads(fp.read_bytes())
        except Exception:  # noqa: BLE001
            cache = None
    cols = "id, detected_at, wallet, event_type, side, size_before, size_after, notional_delta, ref_px"
    if not cache or cache.get("since") > since:
        rows = q(f"SELECT {cols} FROM analytics_flow_events WHERE asset=%s AND detected_at>=%s", (coin, since))
        cache = {"since": since, "rows": []}
    else:
        rows = q(f"SELECT {cols} FROM analytics_flow_events WHERE id>%s AND asset=%s", (cache["last_id"], coin))
    for i, ts, w, et, side, sb, sa, nd, px in rows:
        su = signed_usd(et, side, f(nd), f(sb), f(sa), f(px))
        cache["rows"].append((ts, w, et, side, su, abs(su), f(sb), f(sa), f(px), i))
    cache["rows"] = sorted((r for r in cache["rows"] if r[0] >= since), key=lambda r: (r[0], r[9]))
    cache["since"] = since
    cache["last_id"] = max([cache.get("last_id") or 0] + [r[0] for r in rows])
    tmp = fp.with_suffix(".tmp")
    tmp.write_bytes(pickle.dumps(cache, protocol=pickle.HIGHEST_PROTOCOL))
    os.replace(tmp, fp)
    return [r[:9] for r in cache["rows"]]


def load_candles(coin, since):
    rows = q("SELECT t, c FROM ut_candles WHERE coin=%s AND tf='1h' AND t>=%s ORDER BY t", (coin, since))
    return [(t, float(c)) for t, c in rows]


def load_oi(coin, since):
    """Hourly OI in coins: ut_market_drift (oi there is USD = oi*mark) plus pw_ctx."""
    d = {}
    for t, oi, mark in q("SELECT t, oi, mark FROM ut_market_drift WHERE coin=%s AND t>=%s", (coin, since)):
        if oi and mark:
            d[t] = float(oi) / float(mark)
    for t, oic in q("SELECT t, oi_coins FROM pw_ctx WHERE coin=%s AND t>=%s AND MINUTE(t)<5", (coin, since)):
        if oic:
            d[t.replace(minute=0)] = float(oic)
    return sorted(d.items())


def load_funding(coin, since):
    d = {t: float(r) for t, r in q("SELECT t, rate FROM pw_funding WHERE coin=%s AND t>=%s AND rate IS NOT NULL", (coin, since))}
    for t, fr in q("SELECT t, funding FROM ut_market_drift WHERE coin=%s AND t>=%s AND funding IS NOT NULL", (coin, since)):
        d.setdefault(t, float(fr))
    return sorted(d.items())


class Series:
    def __init__(self, pairs):
        self.t = [p[0] for p in pairs]
        self.v = [p[1] for p in pairs]

    def at(self, t, max_age=timedelta(hours=2)):
        i = bisect.bisect_right(self.t, t) - 1
        if i < 0 or t - self.t[i] > max_age:
            return None
        return self.v[i]

    def window(self, a, b):
        i = bisect.bisect_right(self.t, a)
        j = bisect.bisect_right(self.t, b)
        return self.v[i:j]


def pct_rank(hist, x):
    if x is None or len(hist) < 20:
        return None
    s = sorted(hist)
    return bisect.bisect_left(s, x) / len(s) + (bisect.bisect_right(s, x) - bisect.bisect_left(s, x)) / (2 * len(s))


def features(coin, ts_list, ev, px, oi, fund, big_floor_hist=30):
    """Feature rows for each anchor time; every value uses data at or before t only."""
    ev_t = [e[0] for e in ev]
    cum = [0.0]
    for e in ev:
        cum.append(cum[-1] + e[4] / (e[8] or 1.0) if e[8] else cum[-1])   # coin units
    out = []
    hist_ret, hist_flow, hist_flip, hist_big, hist_fund, hist_open = [], [], [], [], [], []
    for t in ts_list:
        p0 = px.at(t)
        p24 = px.at(t - timedelta(hours=24))
        if not p0 or not p24:
            continue
        i24 = bisect.bisect_right(ev_t, t - timedelta(hours=24))
        i12 = bisect.bisect_right(ev_t, t - timedelta(hours=12))
        j = bisect.bisect_right(ev_t, t)
        flow_coins = cum[j] - cum[i24]
        flow24 = flow_coins * p0
        flips12 = sum(e[4] for e in ev[i12:j] if e[2] == "FLIP")
        opens = [e for e in ev[i24:j] if e[2] in ("OPEN", "INCREASE", "FLIP")]
        # big-position floor: 95th percentile of the coin's open sizes over the prior 30 days
        floor = None
        if len(hist_open) >= 50:
            s = sorted(hist_open[-5000:])
            floor = s[int(0.95 * (len(s) - 1))]
        big24 = sum(e[4] for e in opens if floor and e[5] >= floor)
        ret24 = p0 / p24 - 1
        o0, o24 = oi.at(t), oi.at(t - timedelta(hours=24))
        oi24 = (o0 / o24 - 1) if o0 and o24 else None
        fr = fund.window(t - timedelta(hours=24), t)
        fund24 = sum(fr) / len(fr) if fr else None
        row = {"t": t, "px": p0, "ret24": ret24, "flow24": flow24, "flips12": flips12, "big24": big24, "oi24": oi24,
               "fund24": fund24, "big_floor": floor,
               "r_ret": pct_rank(hist_ret[-720:], ret24), "r_flow": pct_rank(hist_flow[-720:], flow24),
               "r_flip": pct_rank(hist_flip[-720:], flips12), "r_big": pct_rank(hist_big[-720:], big24),
               "r_fund": pct_rank(hist_fund[-720:], fund24)}
        out.append(row)
        hist_ret.append(ret24)
        hist_flow.append(flow24)
        hist_flip.append(flips12)
        hist_big.append(big24)
        if fund24 is not None:
            hist_fund.append(fund24)
        hist_open.extend(e[5] for e in ev[bisect.bisect_right(ev_t, t - timedelta(hours=1)):j] if e[2] in ("OPEN", "INCREASE", "FLIP"))
    return out


# Each rule returns +1 (expect up), -1 (expect down) or 0. Plain names are shown on the page.
def _r(x, lo=None, hi=None):
    if x is None:
        return False
    return (lo is None or x <= lo) and (hi is None or x >= hi)


RULES = {
    "R1": ("Big wallets buy into a fall (or sell into a rise)",
           "Price in its weakest 20% of 24 h moves for this coin, while tracked big wallets added the most net long "
           "in the top 20% of their own history. Mirror for shorts.",
           lambda r: 1 if _r(r["r_ret"], lo=0.2) and _r(r["r_flow"], hi=0.8) else
           (-1 if _r(r["r_ret"], hi=0.8) and _r(r["r_flow"], lo=0.2) else 0)),
    "R2": ("Shorts flip to long in a fall (or longs flip to short in a rise)",
           "Price in its weakest 30% of 24 h moves and the net value of short-to-long flips in 12 h in the top 10% of "
           "history. Mirror for shorts.",
           lambda r: 1 if _r(r["r_ret"], lo=0.3) and r["flips12"] > 0 and _r(r["r_flip"], hi=0.9) else
           (-1 if _r(r["r_ret"], hi=0.7) and r["flips12"] < 0 and _r(r["r_flip"], lo=0.1) else 0)),
    "R3": ("Unusually large new longs in a fall (or new shorts in a rise)",
           "Price in its weakest 30% of 24 h moves and net value of unusually large new positions (each in the top 5% "
           "of the coin's sizes) in the top 20% of history. Mirror for shorts.",
           lambda r: 1 if _r(r["r_ret"], lo=0.3) and r["big24"] > 0 and _r(r["r_big"], hi=0.8) else
           (-1 if _r(r["r_ret"], hi=0.7) and r["big24"] < 0 and _r(r["r_big"], lo=0.2) else 0)),
    "R4": ("Price falls while open positions rise",
           "Price down 2% or more in 24 h while total open positions (in coins) rose 3% or more: someone is taking "
           "the other side of the selling. Expect up.",
           lambda r: 1 if r["oi24"] is not None and r["ret24"] <= -0.02 and r["oi24"] >= 0.03 else 0),
    "R5": ("Crowded side paying to hold",
           "Shorts paying the most funding in the coin's history (lowest 10%) while price is weak: squeeze risk, expect "
           "up. Mirror when longs pay the most while price is strong.",
           lambda r: 1 if _r(r["r_fund"], lo=0.1) and _r(r["r_ret"], lo=0.4) else
           (-1 if _r(r["r_fund"], hi=0.9) and _r(r["r_ret"], hi=0.6) else 0)),
    "C1": ("Big wallets build before the move",
           "Tracked big wallets' 24 h net buying in the top 10% of history (expect up) or net selling in the bottom 10% "
           "(expect down), whatever the trend.",
           lambda r: 1 if _r(r["r_flow"], hi=0.9) else (-1 if _r(r["r_flow"], lo=0.1) else 0)),
    "C2": ("Unusually large new positions",
           "Net value of unusually large new positions in 24 h in the top 10% of history (expect up) or bottom 10% "
           "(expect down).",
           lambda r: 1 if r["big24"] > 0 and _r(r["r_big"], hi=0.9) else (-1 if r["big24"] < 0 and _r(r["r_big"], lo=0.1) else 0)),
}


# ------------------------------------------------------------------ backtest
def backtest():
    ensure_schema()
    since = datetime(2026, 8, 27, 18)
    res = {"ran_at": NOW.isoformat(), "coins": CRYPTO, "since": since.isoformat(), "rules": {}}
    all_rows = {}
    for coin in CRYPTO:
        ev = load_events(coin, since - timedelta(days=1))
        cand = load_candles(coin, since - timedelta(days=2))
        px = Series(cand)
        oi = Series(load_oi(coin, since - timedelta(days=2)))
        fund = Series(load_funding(coin, since - timedelta(days=2)))
        anchors = [t for t, _ in cand if t >= since]
        rows = features(coin, anchors, ev, px, oi, fund)
        for r in rows:
            for h in (12, 24):
                p1 = px.at(r["t"] + timedelta(hours=h), max_age=timedelta(hours=1))
                r[f"fwd{h}"] = (p1 / r["px"] - 1) if p1 else None
        all_rows[coin] = rows
        print("bt", coin, len(ev), "events", len(rows), "anchors", flush=True)
    # skip the first 14 days: ranks need history
    warm = since + timedelta(days=14)
    ts_all = sorted({r["t"] for rows in all_rows.values() for r in rows if r["t"] >= warm})
    if not ts_all:
        raise SystemExit("no anchors")
    mid = ts_all[len(ts_all) // 2]
    res["test_from"], res["split_at"], res["test_to"] = ts_all[0].isoformat(), mid.isoformat(), ts_all[-1].isoformat()
    for h in (12, 24):
        base = {1: [], -1: []}
        for rows in all_rows.values():
            for r in rows:
                if r["t"] >= warm and r[f"fwd{h}"] is not None:
                    base[1].append(r[f"fwd{h}"] > 0)
                    base[-1].append(r[f"fwd{h}"] < 0)
        res[f"base{h}"] = {"up": sum(base[1]) / len(base[1]), "down": sum(base[-1]) / len(base[-1]), "n": len(base[1])}
    for key, (name, desc, fn) in RULES.items():
        out = {"name": name, "desc": desc}
        for h in (12, 24):
            hits = {"A": [], "B": []}
            rets = {"A": [], "B": []}
            per_coin = defaultdict(int)
            examples = []
            for coin, rows in all_rows.items():
                last_fire = {1: None, -1: None}
                for r in rows:
                    if r["t"] < warm or r[f"fwd{h}"] is None:
                        continue
                    d = fn(r)
                    if not d:
                        continue
                    lf = last_fire[d]
                    if lf and r["t"] - lf < timedelta(hours=12):
                        continue
                    last_fire[d] = r["t"]
                    half = "A" if r["t"] < mid else "B"
                    ok = (r[f"fwd{h}"] * d) > 0
                    hits[half].append((ok, d))
                    rets[half].append(r[f"fwd{h}"] * d)
                    per_coin[coin] += 1
                    if h == 24:
                        examples.append({"coin": coin, "t": r["t"].isoformat(), "dir": d, "ret24": r["ret24"],
                                         "flow24": r["flow24"], "fwd24": r["fwd24"]})
            stat = {}
            allh = hits["A"] + hits["B"]
            allr = rets["A"] + rets["B"]
            for k, hh, rr in (("A", hits["A"], rets["A"]), ("B", hits["B"], rets["B"]), ("all", allh, allr)):
                n = len(hh)
                b = res[f"base{h}"]
                exp = (sum(b["up"] if d == 1 else b["down"] for _, d in hh) / n) if n else None
                stat[k] = {"n": n, "hit": (sum(o for o, _ in hh) / n) if n else None, "base": exp,
                           "avg": (sum(rr) / n) if n else None,
                           "longs": sum(1 for _, d in hh if d == 1), "shorts": sum(1 for _, d in hh if d == -1)}
            a, bb, al = stat["A"], stat["B"], stat["all"]
            passed = (al["n"] >= 12 and a["n"] >= 5 and bb["n"] >= 5 and al["hit"] >= 0.55
                      and al["hit"] - al["base"] >= 0.05 and a["hit"] > a["base"] and bb["hit"] > bb["base"]
                      and a["avg"] > 0 and bb["avg"] > 0)
            stat["pass"] = bool(passed)
            stat["per_coin"] = dict(per_coin)
            out[f"h{h}"] = stat
            if h == 24:
                out["examples"] = examples[-12:]
        out["pass"] = out["h24"]["pass"]
        res["rules"][key] = out
        print(key, "24h", out["h24"]["all"], "pass", out["pass"], "| 12h", out["h12"]["all"], out["h12"]["pass"], flush=True)
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    BT_FILE.write_text(json.dumps(res, default=str, indent=1))
    print("backtest written", BT_FILE)


# ------------------------------------------------------------------ live readings
def wallet_tags():
    tags = defaultdict(list)
    for w, mm, vault in q("SELECT wallet, is_mm, is_vault FROM ut_wallet_meta WHERE is_mm=1 OR is_vault=1"):
        if mm:
            tags[w.lower()].append("market maker")
        if vault:
            tags[w.lower()].append("vault")
    for w, lvl in q("SELECT wallet, level FROM ut_wallet_scores WHERE as_of=(SELECT MAX(as_of) FROM ut_wallet_scores) AND level IN ('watch','strong','cluster')"):
        tags[w.lower()].append(f"timing {lvl}")
    for (w,) in q("SELECT wallet FROM featured_wallets WHERE as_of=(SELECT MAX(as_of) FROM featured_wallets) AND qualified=1 AND venue='hl'"):
        tags[w.lower()].append("featured winner")
    return tags


def current_positions(coin):
    """Latest known position per wallet: the analytics sweep (400 wallets, 20 min)
    for crypto, plus every wallet the wide sweep holds; the fresher read wins."""
    pos, newest = {}, None
    if coin in CRYPTO:
        c = q("SELECT MAX(cycle_ts) FROM analytics_positions WHERE asset=%s AND cycle_ts>=%s", (coin, NOW - timedelta(hours=2)))[0][0]
        if c:
            newest = c
            for r in q("SELECT wallet, side, size, notional, entry_px, leverage, liq_px, upnl FROM analytics_positions "
                       "WHERE asset=%s AND cycle_ts=%s", (coin, c)):
                pos[r[0].lower()] = {"side": r[1], "size": f(r[2]), "notional": f(r[3]), "entry": f(r[4]), "lev": f(r[5]),
                                     "liq": f(r[6]), "upnl": f(r[7]), "at": c}
    for r in q("SELECT wallet, side, size, notional, entry_px, leverage, liq_px, upnl, checked_at FROM pw_state "
               "WHERE coin=%s AND checked_at>=%s", (coin, NOW - timedelta(hours=12))):
        w = r[0].lower()
        if w in pos and pos[w]["at"] >= r[8]:
            continue
        pos[w] = {"side": r[1], "size": f(r[2]), "notional": f(r[3]), "entry": f(r[4]), "lev": f(r[5]),
                  "liq": f(r[6]), "upnl": f(r[7]), "at": r[8]}
        newest = max(newest, r[8]) if newest else r[8]
    return newest, pos


def wide_events(coin, since):
    """Crypto events found by the wide sweep (wallets outside the analytics sweep)."""
    out = []
    for ts, w, et, side, sb, sa, nd, px in q(
            "SELECT detected_at, wallet, event_type, side, size_before, size_after, notional_delta, ref_px "
            "FROM pw_events WHERE coin=%s AND detected_at>=%s", (coin, since)):
        su = signed_usd(et, side, f(nd), f(sb), f(sa), f(px))
        out.append((ts, w, et, side, su, abs(su), f(sb), f(sa), f(px)))
    return out


def first_open_times(ev):
    """Most recent OPEN or FLIP per wallet inside the loaded window: when the
    current position began."""
    out = {}
    for e in ev:
        if e[2] in ("OPEN", "FLIP"):
            out[e[1].lower()] = e[0]
    return out


def us_market_open(t):
    if t.weekday() >= 5:
        return False
    mins = t.hour * 60 + t.minute
    return 14 * 60 + 30 <= mins < 21 * 60   # NYSE in UTC (EST months)


def readings(bt):
    passed = {k for k, v in (bt or {}).get("rules", {}).items() if v.get("pass")}
    failed = {k for k, v in (bt or {}).get("rules", {}).items()
              if v["h24"]["all"]["n"] and v["h24"]["all"]["hit"] < v["h24"]["all"]["base"]}
    tags = wallet_tags()
    out = []
    for coin in COINS:
        since = (NOW - timedelta(days=32)).replace(minute=0, second=0)
        ev = load_events_cached(coin, since)
        if coin in CRYPTO:
            ev = sorted(ev + wide_events(coin, since), key=lambda e: e[0])
        cand = load_candles(coin, since)
        ctx = q("SELECT t, mark, oi_coins, funding, premium, vol24 FROM pw_ctx WHERE coin=%s AND t>=%s ORDER BY t", (coin, NOW - timedelta(days=4)))
        # live price from the newest ctx row when fresher than the last candle
        pairs = list(cand)
        if ctx and (not pairs or ctx[-1][0] > pairs[-1][0]):
            pairs.append((ctx[-1][0], float(ctx[-1][1])))
        px = Series(pairs)
        oi = Series(load_oi(coin, since))
        fund = Series(load_funding(coin, since))
        anchors = [t for t, _ in cand if t >= since + timedelta(days=1)]
        if pairs:
            anchors.append(pairs[-1][0])
        feats = features(coin, anchors, ev, px, oi, fund)
        r = feats[-1] if feats else None
        hist_days = (NOW - ev[0][0]).days if ev else 0
        votes = []
        if r:
            for k, (name, desc, fn) in RULES.items():
                d = fn(r)
                if d:
                    votes.append({"rule": k, "name": name, "dir": d, "tested": k in passed and coin in CRYPTO,
                                  "failed": k in failed})
        tested = [v for v in votes if v["tested"]]
        score = sum(v["dir"] for v in tested)
        raw = sum(v["dir"] for v in votes if not v["failed"])
        if coin in CRYPTO and score:
            verdict = "Build-up long" if score > 0 else "Build-up short"
        elif coin in CRYPTO:
            verdict = ("Leaning long (unproven)" if raw > 0 else "Leaning short (unproven)" if raw < 0 else "No clear build-up")
        else:
            verdict = ("Leaning long" if raw > 0 else "Leaning short" if raw < 0 else "No clear build-up") + " (untested)"
        # wallets behind the 24 h flow
        per_w = defaultdict(float)
        per_w_n = defaultdict(int)
        flips = []
        bigs = []
        margins = []
        for e in ev:
            if e[0] < NOW - timedelta(hours=24):
                continue
            per_w[e[1].lower()] += e[4]
            per_w_n[e[1].lower()] += 1
            if e[2] == "FLIP":
                flips.append(e)
            if r and r.get("big_floor") and e[2] in ("OPEN", "INCREASE", "FLIP") and e[5] >= r["big_floor"]:
                bigs.append(e)
        if coin in RWA:
            margins = q("SELECT detected_at, wallet, event_type, side, size_after, margin_before, margin_after, ref_px FROM pw_events "
                        "WHERE coin=%s AND event_type IN ('MARGIN_ADD','MARGIN_CUT') AND detected_at>=%s ORDER BY detected_at DESC LIMIT 10",
                        (coin, NOW - timedelta(hours=24)))
        cyc, pos = current_positions(coin)
        top = sorted(per_w.items(), key=lambda x: -abs(x[1]))[:10]
        opened = first_open_times(ev)
        mark = px.v[-1] if px.v else None
        # liquidation bands +-20% in 2% steps
        bands = defaultdict(lambda: [0.0, 0.0])
        for p in pos.values():
            if mark and p["liq"] and p["notional"]:
                d = p["liq"] / mark - 1
                if -0.2 <= d <= 0.2:
                    b = round(d * 50) / 50
                    bands[b][0 if p["side"] == "long" else 1] += p["notional"]
        oi_usd = (float(ctx[-1][2]) * float(ctx[-1][1])) if ctx and ctx[-1][2] and ctx[-1][1] else None
        longs = sum(p["notional"] for p in pos.values() if p["side"] == "long")
        shorts = sum(p["notional"] for p in pos.values() if p["side"] == "short")
        # off-hours build for US stocks: flow while NYSE is shut
        off = None
        if coin in ("xyz:NVDA", "xyz:AMD"):
            off = sum(e[4] for e in ev if e[0] >= NOW - timedelta(hours=24) and not us_market_open(e[0]))
        last_ctx = ctx[-1] if ctx else None
        oi_series = [(t, v * (px.at(t, max_age=timedelta(hours=3)) or 0)) for t, v in zip(oi.t, oi.v) if t >= NOW - timedelta(hours=73)]
        oi_series = [(t, v) for t, v in oi_series if v]
        out.append({
            "coin": coin, "label": LABEL[coin], "kind": "crypto" if coin in CRYPTO else "rwa",
            "mark": mark, "r": r, "votes": votes, "score": score, "verdict": verdict, "hist_days": hist_days,
            "top": [(w, v, per_w_n[w], pos.get(w), opened.get(w), tags.get(w, [])) for w, v in top],
            "flips": [(e[0], e[1].lower(), e[3], e[4], e[6], e[7], e[8], tags.get(e[1].lower(), [])) for e in flips[-10:]][::-1],
            "bigs": [(e[0], e[1].lower(), e[2], e[3], e[4], e[8], tags.get(e[1].lower(), [])) for e in bigs[-10:]][::-1],
            "margins": margins, "bands": dict(bands), "longs": longs, "shorts": shorts, "n_pos": len(pos), "pos_cycle": cyc, "oi_usd": oi_usd,
            "off_hours_flow": off, "ctx": last_ctx, "window_from": since.strftime("%d %b"),
            "price_72h": [(t, v) for t, v in pairs if t >= NOW - timedelta(hours=72)],
            "oi_72h": [(t, v) for t, v in oi_series if t >= NOW - timedelta(hours=72)],
            "flow_72h": [(x["t"], x["flow24"]) for x in feats if x["t"] >= NOW - timedelta(hours=72)],
        })
        q("INSERT INTO pw_readings (t,coin,verdict,score,body) VALUES (%s,%s,%s,%s,%s) ON DUPLICATE KEY UPDATE verdict=VALUES(verdict)",
          (NOW.replace(second=0), coin, verdict[:40], score,
           json.dumps({"votes": votes, "r": {k: v for k, v in (r or {}).items() if k != "t"}}, default=str)))
    return out


# ------------------------------------------------------------------ alerts
def tg(text):
    tok, chat = E.get("TELEGRAM_BOT_TOKEN"), E.get("ADMIN_TELEGRAM_CHAT_ID")
    if not tok or not chat:
        return False
    data = urllib.parse.urlencode({"chat_id": chat, "text": text, "parse_mode": "HTML",
                                   "disable_web_page_preview": "true"}).encode()
    try:
        urllib.request.urlopen(f"https://api.telegram.org/bot{tok}/sendMessage", data=data, timeout=15).read()
        return True
    except Exception as e:  # noqa: BLE001
        print("telegram failed", e)
        return False


def alert_once(coin, kind, key, body, hours):
    if q("SELECT 1 FROM pw_alerts WHERE akey=%s AND t>=%s LIMIT 1", (key, NOW - timedelta(hours=hours))):
        return
    if tg(body):
        q("INSERT INTO pw_alerts (t,coin,kind,akey,body) VALUES (%s,%s,%s,%s,%s)", (NOW, coin, kind, key, body))


def alerts(rd):
    link = "https://smindex.xyz/research/pressure.html"
    for x in rd:
        coin, lab = x["coin"], x["label"]
        tested = [v for v in x["votes"] if v["tested"]]
        if tested and x["score"]:
            d = "LONG" if x["score"] > 0 else "SHORT"
            why = "; ".join(v["name"] for v in tested)
            alert_once(coin, "verdict", f"verdict:{coin}:{d}",
                       f"<b>Pressure Watch: {html.escape(lab)} build-up {d}</b>\nPrice {fmt_px(x['mark'])}, 24 h {pct(x['r']['ret24'])}\n"
                       f"Tested signals: {html.escape(why)}\n{link}#{anchor(coin)}", 12)
        for e in x["bigs"][:5]:
            t, w, et, side, usd, px_, tg_ = e
            if abs(usd) >= BIG_ALERT[coin] and t >= NOW - timedelta(minutes=45):
                alert_once(coin, "big", f"big:{coin}:{w}:{t:%Y%m%d%H%M}",
                           f"<b>Pressure Watch: big {html.escape(lab)} {'buy' if usd > 0 else 'sell'}</b>\n{et.lower()} {side} {usd_s(abs(usd))} "
                           f"at {fmt_px(px_)} by {w[:10]}{(' (' + ', '.join(tg_) + ')') if tg_ else ''}\n{t:%d %b %H:%M} UTC\n{link}#{anchor(coin)}", 24)
        for e in x["flips"][:5]:
            t, w, side, usd, sb, sa, px_, tg_ = e
            if abs(usd) >= BIG_ALERT[coin] and t >= NOW - timedelta(minutes=45):
                alert_once(coin, "flip", f"flip:{coin}:{w}:{t:%Y%m%d%H%M}",
                           f"<b>Pressure Watch: {html.escape(lab)} flip to {side.upper()}</b>\n{w[:10]}{(' (' + ', '.join(tg_) + ')') if tg_ else ''} "
                           f"flipped to {side} ({usd_s(abs(usd))}) at {fmt_px(px_)}\n{t:%d %b %H:%M} UTC\n{link}#{anchor(coin)}", 24)


# ------------------------------------------------------------------ page
def esc(s):
    return html.escape(str(s))


def anchor(coin):
    return coin.replace(":", "-").lower()


def usd_s(v, sign=False):
    if v is None:
        return "-"
    s = "+" if sign and v > 0 else ("-" if v < 0 else "")
    a = abs(v)
    if a >= 1e9:
        return f"{s}${a / 1e9:.2f}B"
    if a >= 1e6:
        return f"{s}${a / 1e6:.2f}M"
    if a >= 1e3:
        return f"{s}${a / 1e3:.1f}K"
    return f"{s}${a:.0f}"


def pct(v, dp=1):
    return "-" if v is None else f"{v * 100:+.{dp}f}%"


def fmt_px(v):
    if v is None:
        return "-"
    if v >= 1000:
        return f"{v:,.1f}"
    if v >= 10:
        return f"{v:,.2f}"
    if v >= 1:
        return f"{v:,.4f}"
    return f"{v:.6f}"


def spark(points, w=280, h=56, color="var(--ink)", zero=False):
    pts = [(t, v) for t, v in points if v is not None]
    if len(pts) < 2:
        return '<span class="dim">not enough data yet</span>'
    t0, t1 = pts[0][0], pts[-1][0]
    span = max((t1 - t0).total_seconds(), 1)
    vs = [v for _, v in pts]
    lo, hi = min(vs + ([0] if zero else [])), max(vs + ([0] if zero else []))
    if hi == lo:
        hi = lo + 1
    def xy(t, v):
        return (4 + (t - t0).total_seconds() / span * (w - 8), 4 + (hi - v) / (hi - lo) * (h - 8))
    d = " ".join(f"{x:.1f},{y:.1f}" for x, y in (xy(t, v) for t, v in pts))
    z = ""
    if zero and lo < 0 < hi:
        _, zy = xy(t0, 0)
        z = f'<line x1="4" x2="{w - 4}" y1="{zy:.1f}" y2="{zy:.1f}" stroke="var(--line)" stroke-dasharray="3 3"/>'
    return f'<svg viewBox="0 0 {w} {h}" width="{w}" height="{h}" role="img">{z}<polyline fill="none" stroke="{color}" stroke-width="1.6" points="{d}"/></svg>'


def verdict_cls(v):
    if "long" in v.lower():
        return "up"
    if "short" in v.lower():
        return "down"
    return "flat"


def tagspan(tg_):
    return "".join(f'<span class="tag">{esc(t)}</span>' for t in tg_)


def wl(w):
    return f'<a href="https://app.hyperliquid.xyz/explorer/address/{esc(w)}" rel="noopener" target="_blank"><code>{esc(w[:8])}..{esc(w[-4:])}</code></a>'


def plain(v):
    a = v["h24"]
    al, A, B = a["all"], a["A"], a["B"]
    if not al["n"]:
        return "never fired in the test period."
    if v["pass"]:
        return f'works: right {al["hit"] * 100:.0f}% of {al["n"]} times vs {al["base"] * 100:.0f}% by chance, in both halves. Used in verdicts.'
    ea = A["n"] and A["hit"] > A["base"]
    eb = B["n"] and B["hit"] > B["base"]
    if al["hit"] < al["base"]:
        return f'did not work: right {al["hit"] * 100:.0f}% of {al["n"]} times, worse than chance ({al["base"] * 100:.0f}%).'
    if ea and eb:
        return f'steady but weak: better than chance in both halves, {al["hit"] * 100:.0f}% vs {al["base"] * 100:.0f}% overall, short of the pass bar.'
    return (f'worked in one half only ({A["hit"] * 100:.0f}% then {B["hit"] * 100:.0f}%, chance about {al["base"] * 100:.0f}%): '
            'the pattern of a signal that does not last.')


def render(rd, bt):
    coverage = {"wallets": q("SELECT COUNT(*) FROM (SELECT wallet FROM pw_check WHERE last_at>=%s AND ok=1 UNION "
                             "SELECT LOWER(wallet) FROM analytics_wallet_state WHERE cycle_ts=(SELECT MAX(cycle_ts) FROM analytics_wallet_state)) x",
                             (NOW - timedelta(hours=12),))[0][0]}
    passed = [k for k, v in (bt or {}).get("rules", {}).items() if v.get("pass")]
    css = """
:root{--bg:#0d1117;--card:#151b23;--ink:#e6edf3;--dim:#8b949e;--line:#30363d;--up:#3fb950;--down:#f85149;--acc:#58a6ff;--warn:#d29922}
:root[data-theme="light"]{--bg:#ffffff;--card:#f6f8fa;--ink:#1f2328;--dim:#59636e;--line:#d0d7de;--up:#1a7f37;--down:#cf222e;--acc:#0969da;--warn:#9a6700}
@media (prefers-color-scheme: light){:root:not([data-theme="dark"]){--bg:#ffffff;--card:#f6f8fa;--ink:#1f2328;--dim:#59636e;--line:#d0d7de;--up:#1a7f37;--down:#cf222e;--acc:#0969da;--warn:#9a6700}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:14px/1.5 -apple-system,Segoe UI,Roboto,sans-serif}
main{max-width:1180px;margin:0 auto;padding:16px}h1{font-size:22px;margin:4px 0}h2{font-size:18px;margin:28px 0 8px;border-top:1px solid var(--line);padding-top:18px}
h3{font-size:15px;margin:16px 0 6px}.dim{color:var(--dim)}a{color:var(--acc)}code{font-size:12px}
.card{background:var(--card);border:1px solid var(--line);border-radius:8px;padding:12px;margin:10px 0}
table{border-collapse:collapse;width:100%;font-size:13px}th,td{padding:6px 8px;border-bottom:1px solid var(--line);text-align:left;vertical-align:top}
th{color:var(--dim);font-weight:600}td.n,th.n{text-align:right;font-variant-numeric:tabular-nums}
.scroll{overflow-x:auto}.up{color:var(--up)}.down{color:var(--down)}.flat{color:var(--dim)}
.pill{display:inline-block;padding:2px 8px;border-radius:10px;border:1px solid currentColor;font-weight:600;font-size:12px;white-space:nowrap}
.tag{display:inline-block;margin:0 4px 2px 0;padding:0 6px;border-radius:6px;background:var(--line);font-size:11px}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(280px,1fr));gap:10px}.kv b{display:block;font-size:16px}
.bar{height:10px;display:inline-block;vertical-align:middle}.ok{color:var(--up);font-weight:600}.no{color:var(--dim)}
nav a{margin-right:10px;white-space:nowrap}
"""
    h = ['<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">',
         '<meta name="robots" content="noindex"><meta http-equiv="refresh" content="300">',
         f'<title>Pressure Watch</title><style>{css}</style></head><body><main>',
         '<h1>Pressure Watch</h1>',
         f'<p class="dim">Positions built before the move, and positions built against the trend, on 10 Hyperliquid markets. '
         f'Data as of <b>{NOW:%d %b %Y %H:%M} UTC</b>. The page rebuilds every 20 minutes and reloads itself every 5.</p>',
         '<div class="card"><b>How to read it.</b> Each market gets a verdict. For the 6 crypto coins the verdict counts only signals that '
         'passed a test on 3 months of our own history (section "Do the signals work?"). The 4 stock and oil markets have no position history '
         'yet (collection started 25 Nov 2026), so their verdict is marked untested and is a reading, not a forecast. '
         f'"Big wallets" are the wallets we follow: <b>{coverage["wallets"]:,}</b> accounts checked in the last 12 hours '
         f'(every Hyperliquid account worth $1M+ every hour, $250K+ every 8 hours, the most active traders of each market every 20 minutes). '
         'The "Covered" column shows how much of each market they hold. Not advice.</div>',
         '<nav>' + "".join(f'<a href="#{anchor(x["coin"])}">{esc(x["label"])}</a>' for x in rd) + '<a href="#test">Do the signals work?</a></nav>',
         '<h2>All markets now</h2><div class="scroll"><table><tr><th>Market</th><th class="n">Price</th><th class="n">24 h</th>'
         '<th class="n">Open positions 24 h</th><th class="n">Funding (yearly)</th><th class="n">Big wallets net 24 h</th>'
         '<th class="n">Flips 12 h</th><th class="n">Covered</th><th>Verdict</th><th>Why</th></tr>']
    for x in rd:
        r = x["r"] or {}
        fr = r.get("fund24")
        why = "; ".join(f'<span class="{"up" if v["dir"] > 0 else "down"}">{esc(v["name"])}</span>' + ("" if v["tested"] else ' <span class="dim">(failed test, not counted)</span>' if v["failed"] else ' <span class="dim">(unproven)</span>')
                        for v in x["votes"]) or '<span class="dim">no signal firing</span>'
        h.append(f'<tr><td><a href="#{anchor(x["coin"])}">{esc(x["label"])}</a></td><td class="n">{fmt_px(x["mark"])}</td>'
                 f'<td class="n {"up" if (r.get("ret24") or 0) > 0 else "down"}">{pct(r.get("ret24"))}</td>'
                 f'<td class="n">{pct(r.get("oi24"))}</td><td class="n">{pct(fr * 24 * 365 if fr is not None else None, 0)}</td>'
                 f'<td class="n {"up" if (r.get("flow24") or 0) > 0 else "down"}">{usd_s(r.get("flow24"), True)}</td>'
                 f'<td class="n">{usd_s(r.get("flips12"), True)}</td>'
                 f'<td class="n">{(str(round((x["longs"] + x["shorts"]) / 2 / x["oi_usd"] * 100)) + "%") if x["oi_usd"] else "-"}</td>'
                 f'<td><span class="pill {verdict_cls(x["verdict"])}">{esc(x["verdict"])}</span></td><td>{why}</td></tr>')
    h.append('</table></div><p class="dim">Open positions 24 h = change in total open contracts on Hyperliquid, in coins. Big wallets net = what the followed '
             'wallets bought (+) or sold (-) in 24 h, counting opens, adds, cuts, closes and flips. Flips = net value of wallets that switched side '
             '(+ = shorts turned long). Covered = share of all open positions on Hyperliquid held by the followed wallets.</p>')

    for x in rd:
        r = x["r"] or {}
        h.append(f'<h2 id="{anchor(x["coin"])}">{esc(x["label"])} <span class="dim" style="font-size:13px">{esc(x["coin"])}</span> '
                 f'<span class="pill {verdict_cls(x["verdict"])}">{esc(x["verdict"])}</span></h2>')
        fr = r.get("fund24")
        h.append('<div class="grid">'
                 f'<div class="card kv"><span class="dim">Price, last 72 h</span><b>{fmt_px(x["mark"])} <span class="{"up" if (r.get("ret24") or 0) > 0 else "down"}" style="font-size:13px">{pct(r.get("ret24"))} 24 h</span></b>{spark(x["price_72h"])}</div>'
                 f'<div class="card kv"><span class="dim">Total open positions (USD), last 72 h</span><b>{usd_s(x["oi_72h"][-1][1] if x["oi_72h"] else None)} <span style="font-size:13px">{pct(r.get("oi24"))} 24 h</span></b>{spark(x["oi_72h"], color="var(--warn)")}</div>'
                 f'<div class="card kv"><span class="dim">Big wallets, rolling 24 h net buying</span><b class="{"up" if (r.get("flow24") or 0) > 0 else "down"}">{usd_s(r.get("flow24"), True)}</b>{spark(x["flow_72h"], color="var(--acc)", zero=True)}</div>'
                 '</div>')
        lines = []
        if x["kind"] == "rwa":
            lines.append(f'Position history for this market started 25 Nov 2026 ({x["hist_days"]} days so far). Readings need about 3 days before the ranks mean anything; signals stay untested until there are weeks of data.')
        if x["off_hours_flow"] is not None:
            lines.append(f'Built while the US stock market was closed (last 24 h): <b class="{"up" if x["off_hours_flow"] > 0 else "down"}">{usd_s(x["off_hours_flow"], True)}</b>.')
        cov = ((x["longs"] + x["shorts"]) / 2 / x["oi_usd"]) if x["oi_usd"] else None
        lines.append(f'Followed wallets holding this market now: {x["n_pos"]:,}, longs {usd_s(x["longs"])} vs shorts {usd_s(x["shorts"])}'
                     + (f', about {cov * 100:.0f}% of all open positions on Hyperliquid' if cov else '')
                     + (f' (snapshot {x["pos_cycle"]:%d %b %H:%M} UTC).' if x["pos_cycle"] else '.'))
        if fr is not None:
            lines.append(f'Funding, 24 h average: {fr * 100:.4f}% per hour ({pct(fr * 24 * 365, 0)} a year); '
                         + ('longs pay shorts.' if fr > 0 else 'shorts pay longs.'))
        if r.get("big_floor"):
            lines.append(f'"Unusually large" for this market = a single open or add of {usd_s(r["big_floor"])} or more (top 5% over the last 30 days).')
        h.append('<ul>' + "".join(f'<li>{l}</li>' for l in lines) + '</ul>')
        if x["votes"]:
            h.append('<h3>Signals firing now</h3><ul>' + "".join(
                f'<li><span class="{"up" if v["dir"] > 0 else "down"}">{"Expect up" if v["dir"] > 0 else "Expect down"}</span>: {esc(v["name"])} '
                + ('<span class="ok">passed the history test</span>' if v["tested"] else
                   '<span class="down">failed the history test (wrong more often than chance), not counted</span>' if v["failed"] else
                   '<span class="no">not proven</span>') + '</li>'
                for v in x["votes"]) + '</ul>')
        if x["top"]:
            h.append('<h3>Wallets behind the last 24 h</h3><div class="scroll"><table><tr><th>Wallet</th><th class="n">Net 24 h</th><th class="n">Changes</th>'
                     '<th>Position now</th><th class="n">Entry</th><th class="n">Leverage</th><th class="n">Liquidation</th><th>Opened</th><th>Tags</th></tr>')
            for w, v, n, p, op, tg_ in x["top"]:
                if p:
                    pn = f'<span class="{"up" if p["side"] == "long" else "down"}">{p["side"]}</span> {usd_s(p["notional"])}'
                    ent, lev, liq = fmt_px(p["entry"]), (f'{p["lev"]:.0f}x' if p["lev"] else "-"), fmt_px(p["liq"])
                else:
                    pn, ent, lev, liq = '<span class="dim">flat</span>', "-", "-", "-"
                h.append(f'<tr><td>{wl(w)}</td><td class="n {"up" if v > 0 else "down"}">{usd_s(v, True)}</td><td class="n">{n}</td><td>{pn}</td>'
                         f'<td class="n">{ent}</td><td class="n">{lev}</td><td class="n">{liq}</td><td>{op.strftime("%d %b %H:%M") if op else "before " + x["window_from"]}</td><td>{tagspan(tg_)}</td></tr>')
            h.append('</table></div>')
        if x["flips"]:
            h.append('<h3>Side switches, last 24 h</h3><div class="scroll"><table><tr><th>Time (UTC)</th><th>Wallet</th><th>Now</th><th class="n">Value</th><th class="n">Price</th><th>Tags</th></tr>')
            for t, w, side, usd, sb, sa, px_, tg_ in x["flips"]:
                h.append(f'<tr><td>{t:%d %b %H:%M}</td><td>{wl(w)}</td><td class="{"up" if side == "long" else "down"}">{"short to long" if side == "long" else "long to short"}</td>'
                         f'<td class="n">{usd_s(abs(usd))}</td><td class="n">{fmt_px(px_)}</td><td>{tagspan(tg_)}</td></tr>')
            h.append('</table></div>')
        if x["bigs"]:
            h.append('<h3>Unusually large new positions, last 24 h</h3><div class="scroll"><table><tr><th>Time (UTC)</th><th>Wallet</th><th>What</th><th class="n">Value</th><th class="n">Price</th><th>Tags</th></tr>')
            for t, w, et, side, usd, px_, tg_ in x["bigs"]:
                h.append(f'<tr><td>{t:%d %b %H:%M}</td><td>{wl(w)}</td><td class="{"up" if usd > 0 else "down"}">{esc(et.lower())} {esc(side)}</td>'
                         f'<td class="n">{usd_s(abs(usd))}</td><td class="n">{fmt_px(px_)}</td><td>{tagspan(tg_)}</td></tr>')
            h.append('</table></div>')
        if x["margins"]:
            h.append('<h3>Collateral moves (size unchanged), last 24 h</h3><div class="scroll"><table><tr><th>Time (UTC)</th><th>Wallet</th><th>Side</th><th>What</th><th class="n">Margin before</th><th class="n">after</th></tr>')
            for t, w, et, side, sa, mb, ma, px_ in x["margins"]:
                h.append(f'<tr><td>{t:%d %b %H:%M}</td><td>{wl(w.lower())}</td><td>{esc(side)}</td><td>{"added collateral" if et == "MARGIN_ADD" else "removed collateral"}</td>'
                         f'<td class="n">{usd_s(f(mb))}</td><td class="n">{usd_s(f(ma))}</td></tr>')
            h.append('</table></div>')
        if x["bands"]:
            mx = max(max(v) for v in x["bands"].values()) or 1
            h.append('<h3>Where followed positions would be force-closed (within 20% of price)</h3><div class="scroll"><table><tr><th class="n">Distance</th><th class="n">Price</th><th>Longs liquidated</th><th>Shorts liquidated</th></tr>')
            for b in sorted(x["bands"], reverse=True):
                lv, sv = x["bands"][b]
                h.append(f'<tr><td class="n">{b * 100:+.0f}%</td><td class="n">{fmt_px(x["mark"] * (1 + b)) if x["mark"] else "-"}</td>'
                         f'<td><span class="bar" style="width:{lv / mx * 160:.0f}px;background:var(--down)"></span> {usd_s(lv) if lv else ""}</td>'
                         f'<td><span class="bar" style="width:{sv / mx * 160:.0f}px;background:var(--up)"></span> {usd_s(sv) if sv else ""}</td></tr>')
            h.append('</table></div><p class="dim">Large clusters near price can act as a magnet: a move into them forces more selling (longs) or buying (shorts).</p>')

    # backtest section
    h.append('<h2 id="test">Do the signals work?</h2>')
    if not bt:
        h.append('<p>The history test has not run yet.</p>')
    else:
        b24 = bt["base24"]
        h.append(f'<p>Every signal was replayed on the 6 crypto coins using only what was known at each hour, from {bt["test_from"][:10]} to {bt["test_to"][:10]}. '
                 f'A signal counts as a hit when price moved its way after 24 hours. On a random hour, price was up after 24 h {b24["up"] * 100:.0f}% of the time '
                 f'and down {b24["down"] * 100:.0f}%, so a signal must beat that. To pass it needs 12+ signals, a 55%+ hit rate, at least 5 points better than chance, '
                 f'and it must beat chance in both halves of the period (before and after {bt["split_at"][:10]}). Repeat signals within 12 h are counted once.</p>')
        h.append('<div class="scroll"><table><tr><th>Signal</th><th class="n">Times</th><th class="n">Hit 24 h</th><th class="n">Chance</th><th class="n">Avg move its way</th>'
                 '<th class="n">1st half</th><th class="n">2nd half</th><th class="n">Hit 12 h</th><th>Result</th></tr>')
        for k, v in bt["rules"].items():
            a = v["h24"]
            al, A, B = a["all"], a["A"], a["B"]
            def hp(s):
                return f'{s["hit"] * 100:.0f}% of {s["n"]}' if s["n"] else "-"
            h.append(f'<tr><td><b>{esc(v["name"])}</b><br><span class="dim">{esc(v["desc"])}</span></td><td class="n">{al["n"]}</td>'
                     f'<td class="n">{al["hit"] * 100:.0f}%</td><td class="n">{al["base"] * 100:.0f}%</td><td class="n">{pct(al["avg"], 2)}</td>'
                     f'<td class="n">{hp(A)}</td><td class="n">{hp(B)}</td><td class="n">{hp(v["h12"]["all"])}</td>'
                     f'<td>{"<span class=ok>PASS, used in verdicts</span>" if v["pass"] else "<span class=no>did not pass, shown only</span>"}</td></tr>'
                     if al["n"] else
                     f'<tr><td><b>{esc(v["name"])}</b><br><span class="dim">{esc(v["desc"])}</span></td><td class="n">0</td><td colspan="6" class="dim">never fired in the test period</td><td class="no">not testable</td></tr>')
        h.append('</table></div>')
        h.append(f'<p>Signals used in crypto verdicts: <b>{", ".join(esc(bt["rules"][k]["name"]) for k in passed) or "none passed yet"}</b>. '
                 'The test is re-run every day at 02:10 UTC, so a signal can join or drop out as history grows. '
                 'While none passes, verdicts read "Leaning ... (unproven)": the signals are shown so you can watch them, but they are not proven.</p>')
        h.append('<div class="card"><b>What the test says in plain words.</b><ul>' + "".join(
            f'<li><b>{esc(v["name"])}</b>: {plain(v)}</li>' for v in bt["rules"].values()) + '</ul>'
            '<span class="dim">Many followed wallets are market makers and hedgers, whose flow is not a bet. That is one reason "follow the big wallets" can fail.</span></div>')
    h.append('<h2>What this page cannot see</h2><ul>'
             '<li>Only followed wallets: accounts worth $250K+ on the Hyperliquid leaderboard plus the most active traders of each market. Smaller wallets, and positions held off Hyperliquid, are not counted.</li>'
             '<li>Accounts outside the 20-minute group are checked every 1 to 8 hours, so their changes show up later and as one step.</li>'
             '<li>A position opened and closed between two checks of the same wallet is missed.</li>'
             '<li>Total open positions history: hourly from 4 Nov 2026 for crypto, every 5 minutes from 25 Nov 2026 for all 10 markets.</li>'
             '<li>Signals that passed on past data can stop working. Treat a verdict as a reason to look, not a reason to trade.</li></ul>')
    h.append('<p class="dim">SMIndex research. Read-only data from Hyperliquid. Not financial advice.</p></main></body></html>')
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    tmp = PAGE.with_suffix(".tmp")
    tmp.write_text("\n".join(h), encoding="utf-8")
    os.replace(tmp, PAGE)
    print("page", PAGE, sum(len(s) for s in h), "bytes")


def cycle():
    ensure_schema()
    bt = json.loads(BT_FILE.read_text()) if BT_FILE.exists() else None
    rd = readings(bt)
    render(rd, bt)
    if os.environ.get("PW_NO_ALERTS") != "1":
        alerts(rd)
    q("DELETE FROM pw_readings WHERE t < %s", (NOW - timedelta(days=150),))


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    {"collect": collect, "cycle": cycle, "backtest": backtest, "funding": funding_backfill,
     "universe": universe, "sweep": wide_sweep, "seed": seed_state_from_snapshots,
     "render": lambda: render(readings(json.loads(BT_FILE.read_text()) if BT_FILE.exists() else None),
                              json.loads(BT_FILE.read_text()) if BT_FILE.exists() else None)}.get(
        cmd, lambda: sys.exit(__doc__))()
