"""Throwaway, read-only probe (2026-10-22): can Hyperliquid's public ws `trades`
feed give near-real-time last active for every listed HL wallet + sub-accounts?

One extra ws connection, subscribes `trades` for every active coin (main perps,
HIP-3 builder perps, spot), matches users [buyer, seller] against listed wallets
and their sub-accounts. Truth: every 5 min, REST userFillsByTime for 10 random
listed wallets (all their accounts). Own httpx client, not the app's shared
client. Run from a COPY of the tree. No DB writes.
"""
import asyncio
import json
import os
import random
import resource
import statistics
import sys
import time

import httpx
import websockets

sys.path.insert(0, os.getcwd())
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine  # noqa: E402

from app.config import settings  # noqa: E402
from app.db import database  # noqa: E402

database.engine = create_async_engine(settings.DATABASE_URL, pool_size=1, max_overflow=0)
database.async_session_factory = async_sessionmaker(database.engine, expire_on_commit=False)
from app.services.hyperliquid import last_trade as lt  # noqa: E402

WS = "wss://api.hyperliquid.xyz/ws"
INFO = "https://api.hyperliquid.xyz/info"
LISTEN_SEC = int(os.environ.get("LISTEN", "3600"))
TRUTH_FIRST = int(os.environ.get("TRUTH_FIRST", "600"))
SUB_LIMIT = int(os.environ.get("SUB_LIMIT", "100000"))
TRUTH_EVERY = 300
TRUTH_N = 10
PACE = 3.0
SEED = int(os.environ.get("SEED", str(int(time.time()))))
OUT = os.environ.get("OUT", "/tmp/tf_test_out.json")
now_ms = lambda: int(time.time() * 1000)  # noqa: E731

rest = {"requests": 0, "weight": 0, "http_429": 0, "errors": 0}
http = httpx.AsyncClient(timeout=20.0)


async def info(body):
    for _ in range(3):
        r = await http.post(INFO, json=body)
        rest["requests"] += 1
        if r.status_code == 429:
            rest["http_429"] += 1
            await asyncio.sleep(60)
            continue
        r.raise_for_status()
        d = r.json()
        w = 20
        if body["type"] in ("userFills", "userFillsByTime") and isinstance(d, list):
            w += len(d) // 20
        rest["weight"] += w
        return d
    raise RuntimeError("429 x3")


def rss_mb():
    for line in open("/proc/self/status"):
        if line.startswith("VmRSS"):
            return round(int(line.split()[1]) / 1024, 1)


def q(xs):
    if not xs:
        return None
    xs = sorted(xs)
    return {"n": len(xs), "min": xs[0], "p50": xs[len(xs) // 2], "p95": xs[int(len(xs) * .95)],
            "p99": xs[int(len(xs) * .99)], "max": xs[-1]}


async def main():
    out = {"seed": SEED}
    rng = random.Random(SEED)
    listed = await lt.listed_wallets()
    acct_of = {}
    for i, w in enumerate(listed):
        acct_of[w] = w
        if i >= SUB_LIMIT:
            continue
        try:
            body = await info({"type": "subAccounts", "user": w})
            for x in body or []:
                if x.get("subAccountUser"):
                    acct_of[str(x["subAccountUser"]).lower()] = w
        except Exception:
            rest["errors"] += 1
        await asyncio.sleep(PACE)
    accts_of = {}
    for a, w in acct_of.items():
        accts_of.setdefault(w, []).append(a)
    out["listed"] = len(listed)
    out["accounts"] = len(acct_of)
    out["setup_rest"] = dict(rest)
    print("setup", out, flush=True)

    coins = {"main": [], "hip3": [], "spot": []}
    m = await info({"type": "meta"})
    coins["main"] = [u["name"] for u in m["universe"] if not u.get("isDelisted")]
    for d in await info({"type": "perpDexs"}):
        if not d:
            continue
        u = (await info({"type": "meta", "dex": d["name"]}))["universe"]
        coins["hip3"] += [x["name"] for x in u if not x.get("isDelisted")]
        await asyncio.sleep(1)
    coins["spot"] = [u["name"] for u in (await info({"type": "spotMeta"}))["universe"]]
    kind = {c: k for k, v in coins.items() for c in v}
    out["coins_subscribed"] = {k: len(v) for k, v in coins.items()}
    out["coin_examples"] = {k: v[:3] for k, v in coins.items()}

    seen, seen_hash = set(), set()
    acct_fills, acct_latest = {}, {}
    delays_listed, delays_all = [], []
    delays_kind = {}
    per_sec = {}
    st = {"sub_ok": 0, "sub_err": 0, "msgs": 0, "bytes": 0, "trades": 0}
    err_samples = []
    trades_kind = {"main": 0, "hip3": 0, "spot": 0}
    listed_kind = {"main": 0, "hip3": 0, "spot": 0}
    listed_coins = {}
    truth_rows, missed, truth_rounds = [], [], []
    live = {"from": None}

    async def truth_round(t_end):
        sample = rng.sample(listed, TRUTH_N)
        lo = max(live["from"] + 30_000, t_end - 600_000)
        r_info = {"at": time.strftime("%H:%M:%S", time.gmtime(t_end / 1000)), "accounts": 0, "fills": 0, "missed": 0}
        for w in sample:
            accts = accts_of[w]
            r_info["accounts"] += len(accts)
            rest_latest = None
            for a in accts:
                try:
                    fl = await info({"type": "userFillsByTime", "user": a, "startTime": lo, "endTime": t_end})
                except Exception as exc:
                    rest["errors"] += 1
                    print("truth err", a[:8], exc, flush=True)
                    fl = []
                if isinstance(fl, list) and len(fl) >= 2000:
                    r_info["capped_pages"] = r_info.get("capped_pages", 0) + 1
                for f in fl or []:
                    r_info["fills"] += 1
                    tm = int(f["time"])
                    rest_latest = max(rest_latest or 0, tm)
                    if (f["coin"], int(f["tid"])) in seen:
                        r_info["tid_match"] = r_info.get("tid_match", 0) + 1
                    else:
                        r_info["missed"] += 1
                        if len(missed) < 400:
                            missed.append({"acct": a[:8], "sub": a != w, "coin": f["coin"],
                                           "kind": kind.get(f["coin"], "unsubscribed"), "dir": f.get("dir"),
                                           "time": tm, "crossed": f.get("crossed"), "liq": bool(f.get("liquidation")),
                                           "hash": f.get("hash"), "tid": f.get("tid"), "twap": f.get("twapId"),
                                           "hash_seen": f.get("hash") in seen_hash})
                await asyncio.sleep(PACE)
            feed_latest = max((t for a in accts for t in acct_fills.get(a, ()) if lo <= t <= t_end), default=None)
            truth_rows.append({"wallet": w[:6] + ".." + w[-4:], "subs": len(accts) - 1,
                               "rest_latest": rest_latest, "feed_latest": feed_latest,
                               "gap_s": None if not (rest_latest and feed_latest) else round((feed_latest - rest_latest) / 1000, 1)})
        truth_rounds.append(r_info)
        print("truth", r_info, "rest", rest, "rss", rss_mb(), flush=True)

    cpu0 = time.process_time()
    async with websockets.connect(WS, ping_interval=20, ping_timeout=20, max_size=16_000_000) as ws:
        all_coins = coins["main"] + coins["hip3"] + coins["spot"]
        t_sub0 = now_ms()

        async def subscribe():
            for c in all_coins:
                await ws.send(json.dumps({"method": "subscribe", "subscription": {"type": "trades", "coin": c}}))
                await asyncio.sleep(0.1)     # 600/min, under the 2000 sent/min cap
        sub_task = asyncio.create_task(subscribe())
        start = time.time()
        next_truth = start + TRUTH_FIRST
        truth_task = None
        while time.time() - start < LISTEN_SEC:
            try:
                raw = await asyncio.wait_for(ws.recv(), 5)
            except asyncio.TimeoutError:
                continue
            rcv = now_ms()
            st["msgs"] += 1
            st["bytes"] += len(raw)
            per_sec[rcv // 1000] = per_sec.get(rcv // 1000, 0) + 1
            if live["from"] is None and sub_task.done():
                live["from"] = rcv
                print("all subscribed", st, err_samples, flush=True)
            msg = json.loads(raw)
            ch = msg.get("channel")
            if ch == "subscriptionResponse":
                st["sub_ok"] += 1
                continue
            if ch == "error":
                st["sub_err"] += 1
                if len(err_samples) < 5:
                    err_samples.append(str(msg.get("data"))[:200])
                continue
            if ch == "trades":
                for t in msg.get("data") or []:
                    tm = int(t["time"])
                    if tm < t_sub0:
                        continue          # subscription snapshot of older trades
                    st["trades"] += 1
                    k = kind.get(t["coin"], "main")
                    trades_kind[k] += 1
                    d = rcv - tm
                    delays_all.append(d)
                    delays_kind.setdefault(k, []).append(d)
                    hit = False
                    for u in t.get("users") or []:
                        u = u.lower()
                        if u in acct_of:
                            hit = True
                            acct_fills.setdefault(u, []).append(tm)
                            if tm > acct_latest.get(u, 0):
                                acct_latest[u] = tm
                    if hit:
                        seen.add((t["coin"], int(t["tid"])))
                        seen_hash.add(t.get("hash"))
                        delays_listed.append(d)
                        listed_kind[k] += 1
                        listed_coins[t["coin"]] = listed_coins.get(t["coin"], 0) + 1
            if live["from"] and time.time() >= next_truth and (truth_task is None or truth_task.done()):
                next_truth = time.time() + TRUTH_EVERY
                truth_task = asyncio.create_task(truth_round(now_ms() - 60_000))
        if truth_task and not truth_task.done():
            await truth_task
    cpu = time.process_time() - cpu0
    wall = time.time() - start

    out["subscriptions"] = {"ok": st["sub_ok"], "error": st["sub_err"], "error_samples": err_samples}
    out["delay_ms_listed"] = q(delays_listed)
    out["delay_ms_all_trades"] = q(delays_all)
    out["delay_ms_by_kind"] = {k: q(v) for k, v in delays_kind.items()}
    out["trades_total"] = st["trades"]
    out["trades_by_kind"] = trades_kind
    out["listed_trades_by_kind"] = listed_kind
    out["listed_top_coins"] = sorted(listed_coins.items(), key=lambda x: -x[1])[:15]
    out["listed_coins_n"] = len(listed_coins)
    secs = sorted(per_sec)[30:-1] if len(per_sec) > 40 else sorted(per_sec)
    out["msgs"] = {"total": st["msgs"], "avg_per_s": round(st["msgs"] / wall, 1),
                   "peak_per_s": max((per_sec[s] for s in secs), default=0),
                   "mb_in": round(st["bytes"] / 1e6, 1), "avg_kb_per_s": round(st["bytes"] / wall / 1024, 1)}
    out["process"] = {"cpu_pct_of_one_core": round(100 * cpu / wall, 1), "rss_mb_end": rss_mb(),
                      "rss_mb_peak": round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024, 1),
                      "wall_s": round(wall)}
    per_w = {}
    for a, ts in acct_fills.items():
        per_w.setdefault(acct_of[a], []).extend(ts)
    rows = []
    for w in listed:
        ts = sorted(per_w.get(w, []))
        mins = len({t // 60000 for t in ts})
        gaps = [(b - a) / 1000 for a, b in zip(ts, ts[1:]) if b - a > 1000]
        rows.append((w, len(ts), mins, round(statistics.median(gaps)) if gaps else None))
    traded = [r for r in rows if r[1]]
    out["wallet_activity"] = {
        "listed": len(listed), "traded_in_window": len(traded),
        "active_minutes": {"0": len(listed) - len(traded),
                           "1-5": sum(1 for r in traded if r[2] <= 5),
                           "6-30": sum(1 for r in traded if 5 < r[2] <= 30),
                           "31+": sum(1 for r in traded if r[2] > 30)},
        "top": [(r[0][:6] + ".." + r[0][-4:], r[1], r[2], r[3]) for r in sorted(traded, key=lambda r: -r[1])[:10]],
    }
    out["truth_rounds"] = truth_rounds
    out["truth_rows"] = truth_rows
    out["missed"] = missed
    out["rest"] = dict(rest)
    out["seen_listed_trades"] = len(seen)
    json.dump(out, open(OUT, "w"), default=str)
    print("DONE " + OUT, flush=True)


asyncio.run(main())
