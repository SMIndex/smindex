"""Read-only: compare the Discover list numbers with the venues' own data (2026-10-21).

    cd backend && python scripts/verify_discover_numbers.py [seed]

Perpl: leaderboard all/pnl (the same board SMINDEX passes through) for profit, return
and volume; a direct on-chain read of every market for open positions; the day board
and position entry blocks as the venue evidence for last active (Perpl publishes no
per-wallet last-trade time, and its fill events do not index the account).
Hyperliquid: stats-data leaderboard allTime (the window SMINDEX ingests) plus
info portfolio allTime and userFills summed over the wallet AND its sub-accounts
(the leaderboard counts both), and clearinghouseState of the wallet itself.
"""
import json
import os
import random
import sys
import time
from datetime import datetime, timezone

import requests
from web3 import Web3

sys.path.insert(0, os.getcwd())
SITE = "https://smindex.xyz"
PERPL = "https://app.perpl.xyz"
HL = "https://api.hyperliquid.xyz/info"
S = requests.Session()
S.headers["User-Agent"] = "smindex-verify/1.0"


def j(url, **kw):
    for i in range(4):
        r = S.get(url, timeout=60, **kw) if "json" not in kw else S.post(url, timeout=60, **kw)
        if r.status_code == 429:
            time.sleep(5 * (i + 1))
            continue
        r.raise_for_status()
        return r.json()
    r.raise_for_status()


def hl(body):
    for i in range(4):
        r = S.post(HL, json=body, timeout=60)
        if r.status_code == 429:
            time.sleep(5 * (i + 1))
            continue
        r.raise_for_status()
        return r.json()
    r.raise_for_status()


def iso_ms(s):
    if not s:
        return None
    return datetime.fromisoformat(s.replace("Z", "")).replace(tzinfo=timezone.utc).timestamp() * 1000


def last_active(t):
    # same rule as the page (lib/discoverView.ts lastActiveIso)
    la = (t.get("activity") or {}).get("last_active_at")
    lf = t.get("last_fill_at")
    iso = la or lf
    if la and lf and (lf > la or (la.endswith("T23:59:59") and la[:10] == lf[:10])):
        iso = lf
    return iso_ms(iso)


def fmt_t(ms):
    return datetime.fromtimestamp(ms / 1000, timezone.utc).strftime("%m-%d %H:%M") if ms else "none"


def money(ours, venue):
    d = ours - venue
    ok = abs(d) <= max(1.0, 0.01 * abs(venue))
    return d, ok


def row(name, ours, venue, diff, ok, note=""):
    return {"field": name, "ours": ours, "venue": venue, "diff": diff, "ok": ok, "note": note}


seed = int(sys.argv[1]) if len(sys.argv) > 1 else int(time.time())
print(f"seed {seed}")
rng = random.Random(seed)

ours_p = j(f"{SITE}/api/traders", params={"sort": "pnl", "period": "all", "limit": 24, "exchange": "perpl"})
ours_h = j(f"{SITE}/api/traders", params={"sort": "pnl", "period": "all", "limit": 50, "exchange": "hl"})
pick_p = rng.sample(ours_p, 5)
pick_h = rng.sample(ours_h, 5)
# The page shows a loading mark while the server reads a cold wallet and fills it on the next poll; wait the same way.
for _ in range(12):
    hl_act = j(f"{SITE}/api/traders/hl-active", params={"wallets": ",".join(t["wallet_address"] for t in pick_h)})
    if not any(v.get("pending") for v in hl_act.values()):
        break
    time.sleep(5)

# ---------------- Perpl ----------------
board = {e["a"].lower(): e for e in j(f"{PERPL}/api/v1/trading/leaderboard/all/pnl").get("d", [])}
day = {e["a"].lower(): e for e in j(f"{PERPL}/api/v1/trading/leaderboard/day/pnl").get("d", [])}
ctx = j(f"{PERPL}/api/v1/pub/context")
market_ids = sorted({m["id"] for m in ctx["markets"]})
names = {m["id"]: m["name"] for m in ctx["markets"]}

from app.config import settings  # noqa: E402  (RPC URL only, never printed)
w3 = Web3(Web3.HTTPProvider(settings.MONAD_RPC_URL, request_kwargs={"timeout": 20}))
abi = json.load(open("perpl_abi.json"))
c = w3.eth.contract(address=Web3.to_checksum_address("0x34b6552d57a35a1d042ccae1951bd1c370112a6f"), abi=abi)
head = w3.eth.get_block("latest")


def block_ms(b):
    try:
        return w3.eth.get_block(b)["timestamp"] * 1000
    except Exception:
        return None


report = {"seed": seed, "perpl": [], "hl": []}
for t in pick_p:
    w = t["wallet_address"].lower()
    e = board.get(w)
    rows = []
    if e is None:
        rows.append(row("on venue board", "yes", "no", "", False))
    else:
        vp, vr, vv = int(e["p"]) / 1e6, e["r"] / 100, int(e["v"]) / 1e6
        d, ok = money(t["pnl_total"], vp); rows.append(row("profit $", round(t["pnl_total"], 2), round(vp, 2), round(d, 2), ok))
        rows.append(row("return %", t["roi"], vr, round(t["roi"] - vr, 4), abs(t["roi"] - vr) <= max(0.01, 0.01 * abs(vr))))
        d, ok = money(t["volume"], vv); rows.append(row("volume $", round(t["volume"]), round(vv), round(d), ok))
    try:
        acc = int(c.functions.getAccountByAddr(Web3.to_checksum_address(w)).call()[0])
    except Exception:
        acc = 0
    opens, entry_blocks = [], []
    if acc:
        for mid in market_ids:
            try:
                p = c.functions.getPosition(mid, acc).call()[0]
            except Exception:
                continue
            if p[4] > 0:
                opens.append(names.get(mid, mid)); entry_blocks.append(p[7])
    oc = t.get("active_positions_count")
    rows.append(row("open positions", oc, len(opens), "" if oc is None else oc - len(opens), oc == len(opens), ",".join(map(str, opens))))
    la = last_active(t)
    newest_entry = max((block_ms(b) for b in entry_blocks), default=None) if entry_blocks else None
    dv = int(day[w]["v"]) / 1e6 if w in day else 0.0
    # Evidence: traded in the day window <=> day volume > 0; newest position entry is a lower bound.
    notes, ok = [], True
    now = head["timestamp"] * 1000
    if dv > 0:
        notes.append(f"day-board volume ${dv:,.0f} (traded today)")
        if la is None or now - la > 26 * 3600e3:
            ok = False; notes.append("ours older than the day window")
    else:
        notes.append("not traded in the day window")
        if la is not None and now - la < 20 * 3600e3:
            notes.append("ours says <20h ago")
    if newest_entry:
        notes.append(f"newest open entry {fmt_t(newest_entry)}")
        if la is None or la + 3600e3 < newest_entry:
            ok = False; notes.append("ours older than an entry the chain shows")
    rows.append(row("last active", fmt_t(la), "see note", "", ok, "; ".join(notes)))
    report["perpl"].append({"wallet": w, "rank": t["rank"], "rows": rows})

# ---------------- Hyperliquid ----------------
lb = j("https://stats-data.hyperliquid.xyz/Mainnet/leaderboard")
lbw = {}
for r in lb.get("leaderboardRows", []):
    a = (r.get("ethAddress") or "").lower()
    if a in {t["wallet_address"].lower() for t in pick_h}:
        lbw[a] = dict(r.get("windowPerformances") or [])
for t in pick_h:
    w = t["wallet_address"].lower()
    rows = []
    perf = (lbw.get(w) or {}).get("allTime")
    accts = [w] + [x["subAccountUser"] for x in (hl({"type": "subAccounts", "user": w}) or [])]
    ppnl, pvol, vf = 0.0, 0.0, None
    for a in accts:
        pa = dict(hl({"type": "portfolio", "user": a})).get("allTime") or {}
        ppnl += float(pa["pnlHistory"][-1][1]) if pa.get("pnlHistory") else 0.0
        pvol += float(pa.get("vlm") or 0)
        vf = max([x for x in [vf] if x] + [f["time"] for f in hl({"type": "userFills", "user": a})], default=vf)
    sub_note = f"wallet + {len(accts) - 1} sub-accounts" if len(accts) > 1 else ""
    if perf:
        vp, vr, vv = float(perf["pnl"]), float(perf["roi"]) * 100, float(perf["vlm"])
        d, ok = money(t["pnl_total"], vp); rows.append(row("profit $ (board)", round(t["pnl_total"], 2), round(vp, 2), round(d, 2), ok))
        rows.append(row("return % (board)", round(t["roi"], 4), round(vr, 4), round(t["roi"] - vr, 4), abs(t["roi"] - vr) <= max(0.01, 0.01 * abs(vr))))
        d, ok = money(t["volume"], vv); rows.append(row("volume $ (board)", round(t["volume"]), round(vv), round(d), ok))
    else:
        rows.append(row("on venue board", "yes", "no", "", False))
    if ppnl is not None:
        d, ok = money(t["pnl_total"], ppnl); rows.append(row("profit $ (portfolio)", round(t["pnl_total"], 2), round(ppnl, 2), round(d, 2), ok, sub_note))
    d, ok = money(t["volume"], pvol); rows.append(row("volume $ (portfolio)", round(t["volume"]), round(pvol), round(d), ok, sub_note))
    st = hl({"type": "clearinghouseState", "user": w})
    vn = sum(1 for p in st.get("assetPositions", []) if float(p["position"]["szi"]) != 0)
    s = hl_act.get(w) or {}
    oc = s.get("count")
    rows.append(row("open positions", oc, vn, "" if oc is None else oc - vn, oc == vn, "pending" if s.get("pending") else ""))
    la = last_active(t)
    ok = la is not None and vf is not None and abs(la - vf) <= 3600e3
    rows.append(row("last active", fmt_t(la), fmt_t(vf), "" if not (la and vf) else f"{(la - vf) / 60000:+.0f} min", ok))
    report["hl"].append({"wallet": w, "rank": t["rank"], "rows": rows})

out = os.path.join(os.environ.get("OUT_DIR", "."), f"verify_numbers_{seed}.json")
json.dump(report, open(out, "w"), indent=1, default=str)
for ex in ("perpl", "hl"):
    print(f"\n== {ex}")
    for wr in report[ex]:
        print(f"{wr['wallet'][:6]}..{wr['wallet'][-4:]} rank {wr['rank']}")
        for r in wr["rows"]:
            print(f"   {'PASS' if r['ok'] else 'FAIL'} {r['field']:<22} ours {r['ours']!s:<16} venue {r['venue']!s:<16} diff {r['diff']!s:<10} {r['note']}")
print("\nsaved", out)
