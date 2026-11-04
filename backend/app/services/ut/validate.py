"""The four validation checks (spec section 9), written to VALIDATION.md
with the hand-review PNGs next to it:

  1. precision: the 20 biggest moves of the last 90 days on the 30 most
     active coins; for each, the wallets that entered within 60 min before
     in the right direction, and whether the method flags them from entries
     BEFORE that move (scores recomputed with a cutoff at the move)
  2. shuffle: every wallet's entry times shifted by a random 1 to 3 days
     (one shift per wallet), hits recomputed against the real candles, the
     same scoring; flagged on shuffled data is the false-positive floor
  3. known public cases: whether the published wallets are in our data
  4. hand review: 10 flagged wallets, entries plotted on price

Numbers are written as they come out, including a failed ratio.
"""
from __future__ import annotations

import os
import random
from datetime import datetime, timedelta

from sqlalchemy import bindparam, text

from app.db.database import get_session_factory
from app.services.ut import common, rules, scores
from app.utils.logger import get_logger

logger = get_logger(__name__)

SHUFFLE_SEED = 20261104
KNOWN_CASES = [
    {"name": "October 2026 BTC short cluster (Lookonchain, 2026-10-07)", "addresses": [],
     "note": "four new wallets, about $1M USDC via CCTP, 148.49 BTC short at 40x before BTC fell under $84,000; the published "
             "reports repeat the figures but not the addresses, so the check looks for fresh wallets that shorted BTC in the hour before the drop"},
    {"name": "HOOD short before earnings / LIT long around the listing (AMBCrypto, wallets ending 177D, bc7b, acf9)", "addresses": [],
     "suffixes": ["177d", "bc7b", "acf9"],
     "note": "the HOOD short was 2026-04-28 and the LIT trade 2026-01-15; stored fills start 2026-07-31, so neither trade is in our data"},
]


async def _top_moves(n_coins: int = 30, n_moves: int = 20) -> list[dict]:
    sf = get_session_factory()
    since = common.window_start()
    async with sf() as s:
        coins = (await s.execute(text(
            "SELECT coin FROM ut_entries WHERE time >= :a GROUP BY coin ORDER BY COUNT(*) DESC LIMIT :n"), {"a": since, "n": n_coins})).scalars().all()
        if not coins:
            return []
        rows = (await s.execute(text(
            "SELECT id, coin, time, direction, magnitude FROM ut_events WHERE kind='big_move' AND time >= :a AND coin IN :c "
            "ORDER BY magnitude DESC LIMIT :n").bindparams(bindparam("c", expanding=True)), {"a": since, "c": list(coins), "n": n_moves})).all()
    return [{"id": i, "coin": c, "time": t, "direction": d, "magnitude": float(m)} for i, c, t, d, m in rows]


async def precision_check() -> dict:
    moves = await _top_moves()
    sf = get_session_factory()
    out = []
    flagged_total = pred_total = entrants_total = 0
    for mv in moves:
        want = "long" if mv["direction"] == "up" else "short"
        async with sf() as s:
            ents = (await s.execute(text(
                "SELECT DISTINCT e.wallet FROM ut_entries e JOIN ut_wallet_meta m ON m.wallet=e.wallet AND m.excluded IS NULL "
                "WHERE e.coin=:c AND e.side=:s AND e.time >= :a AND e.time < :b"),
                {"c": mv["coin"], "s": want, "a": mv["time"] - timedelta(minutes=60), "b": mv["time"]})).scalars().all()
        entrants = sorted(set(ents))
        pred = []
        if entrants:
            res = await scores.build(cutoff=mv["time"], write=False, only=entrants)
            rows = res["rows"]
            for w in entrants:
                r = rows.get(w)
                if r and r["level"] != "none":
                    pred.append((w, r["level"], r["score"], r["hits"], r["entries"]))
        flagged_total += len(pred)
        entrants_total += len(entrants)
        out.append({**mv, "entrants": len(entrants), "flagged_before": pred})
    return {"moves": out, "entrants": entrants_total, "flagged_before": flagged_total}


async def shuffle_check() -> dict:
    """Shift each scored wallet's entries by a random 1 to 3 days (same
    direction and size for all its entries), recompute hits against the
    coin's real candles with the same rules, then score. Returns the
    flagged count on shuffled data and the real count."""
    since = common.window_start()
    sf = get_session_factory()
    thr = await common.latest_thresholds()
    async with sf() as s:
        real = (await s.execute(text(
            "SELECT COUNT(*) FROM ut_wallet_scores WHERE as_of=(SELECT MAX(as_of) FROM ut_wallet_scores) AND level <> 'none'"))).scalar()
        real_s1 = (await s.execute(text(
            "SELECT COUNT(*) FROM ut_wallet_scores WHERE as_of=(SELECT MAX(as_of) FROM ut_wallet_scores) AND s1=1"))).scalar()
        wallets = (await s.execute(text(
            "SELECT wallet FROM ut_wallet_scores WHERE as_of=(SELECT MAX(as_of) FROM ut_wallet_scores)"))).scalars().all()
        ents = (await s.execute(text(
            "SELECT wallet, coin, side, time, px, notional, event_id FROM ut_entries WHERE time >= :a AND hit IS NOT NULL AND wallet IN :w"
        ).bindparams(bindparam("w", expanding=True)), {"a": since, "w": list(wallets) or ["-"]})).all()
    rnd = random.Random(SHUFFLE_SEED)
    shift = {w: rnd.choice([-1, 1]) * rnd.randint(24 * 60, 72 * 60) * 60_000 for w in wallets}
    cache: dict[str, list[dict]] = {}
    now_ms = common.ms(common.now_utc())
    per_w: dict[str, dict] = {w: {"n": 0, "k": 0, "br": 0.0, "events": set(), "kinds": set()} for w in wallets}
    for w, coin, side, t, px, notional, ev in ents:
        st = thr.get(coin)
        if not st:
            continue
        if coin not in cache:
            cache[coin] = rules.sorted_candles(await common.load_candles(coin, "1h", since - timedelta(days=4), common.now_utc()))
        tm = common.ms(t) + shift[w]
        if tm + 2 * 3_600_000 > now_ms:
            continue
        fm = rules.forward_moves(float(px), side, tm, cache[coin][0], cache[coin][1])
        if fm["mfe_2h"] is None:
            continue
        a = per_w[w]
        a["n"] += 1
        a["br"] += st["base_rate"]
        if rules.is_hit(fm["mfe_2h"], st["thr"]):
            a["k"] += 1
    flagged = s1 = 0
    for w, a in per_w.items():
        if a["n"] == 0:
            continue
        sig = rules.signals({"entries": a["n"], "hits": a["k"], "blended_base_rate": a["br"] / a["n"]})
        s1 += 1 if sig.get("s1") else 0
        flagged += 1 if sig["level"] != "none" else 0
    # a shuffled wallet cannot earn S2..S7 here (events and deposits are not shifted), so the
    # comparable real figure is S1: timing alone, which every listing requires
    ratio = (real_s1 / s1) if s1 else None
    return {"wallets": len(wallets), "real_listed": int(real or 0), "real_s1": int(real_s1 or 0), "shuffled_s1": s1,
            "shuffled_listed": flagged, "ratio_s1": ratio, "pass": (ratio is not None and ratio >= 5.0) or (s1 == 0 and real_s1 > 0)}


async def known_cases() -> list[dict]:
    sf = get_session_factory()
    out = []
    for case in KNOWN_CASES:
        found = []
        async with sf() as s:
            for a in case.get("addresses", []):
                n = (await s.execute(text("SELECT COUNT(*) FROM hl_fills WHERE wallet=:w"), {"w": a.lower()})).scalar()
                found.append({"wallet": a, "fills": int(n or 0)})
            for suf in case.get("suffixes", []):
                ws = (await s.execute(text(
                    "SELECT DISTINCT wallet FROM hl_fills WHERE wallet LIKE :p LIMIT 5"), {"p": f"%{suf}"})).scalars().all()
                found.append({"suffix": suf, "wallets_in_data": list(ws)})
        extra = None
        if "BTC short" in case["name"]:
            async with sf() as s:
                drop = (await s.execute(text(
                    "SELECT id, time, magnitude FROM ut_events WHERE kind='big_move' AND coin='BTC' AND direction='down' "
                    "AND time BETWEEN '2026-10-06 00:00:00' AND '2026-10-08 00:00:00' ORDER BY magnitude DESC LIMIT 1"))).first()
                if drop:
                    ents = (await s.execute(text(
                        "SELECT e.wallet, e.time, e.notional, e.leverage, m.first_deposit_at FROM ut_entries e LEFT JOIN ut_wallet_meta m ON m.wallet=e.wallet "
                        "WHERE e.coin='BTC' AND e.side='short' AND e.time >= :a AND e.time < :b ORDER BY e.notional DESC LIMIT 15"),
                        {"a": drop[1] - timedelta(minutes=60), "b": drop[1]})).all()
                    extra = {"drop_event": {"time": str(drop[1]), "magnitude": float(drop[2])},
                             "btc_shorts_in_the_hour_before": [{"wallet": w, "time": str(t), "notional": float(n), "leverage": (float(l) if l else None),
                                                                 "first_deposit": (str(fd) if fd else "unknown")} for w, t, n, l, fd in ents]}
                else:
                    extra = {"drop_event": None}
        out.append({**case, "found": found, "extra": extra})
    return out


def _png(path: str, wallet: str, entries: list[dict], candles: list[dict]) -> bool:
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        from matplotlib.dates import DateFormatter
    except ImportError:
        return False
    if not candles:
        return False
    fig, ax = plt.subplots(figsize=(11, 4.2), dpi=110)
    xs = [datetime.utcfromtimestamp(c["t"] / 1000) for c in candles]
    ax.plot(xs, [c["c"] for c in candles], color="#555", linewidth=0.9, label="price (1h close)")
    for e in entries:
        col = "#0B8F5F" if e["side"] == "long" else "#CF3548"
        mk = "^" if e["side"] == "long" else "v"
        ax.scatter([e["time"]], [e["px"]], marker=mk, s=70 if e["hit"] else 28, color=col, edgecolors="black" if e["hit"] else "none",
                   linewidths=0.6, zorder=3, alpha=0.95 if e["hit"] else 0.55)
    ax.set_title(f"{wallet}  entries vs price (filled markers = hit)")
    ax.xaxis.set_major_formatter(DateFormatter("%m-%d"))
    ax.grid(alpha=0.25)
    fig.tight_layout()
    fig.savefig(path)
    plt.close(fig)
    return True


async def hand_review(out_dir: str, n: int = 10) -> list[dict]:
    sf = get_session_factory()
    async with sf() as s:
        rows = (await s.execute(text(
            "SELECT wallet, level, score, hits, entries, misses FROM ut_wallet_scores WHERE as_of=(SELECT MAX(as_of) FROM ut_wallet_scores) "
            "AND level <> 'none' ORDER BY score DESC, hits DESC LIMIT :n"), {"n": n})).all()
    out = []
    os.makedirs(out_dir, exist_ok=True)
    for w, lvl, sc, k, nn, mi in rows:
        async with sf() as s:
            ents = (await s.execute(text(
                "SELECT coin, side, time, px, hit, moved_market FROM ut_entries WHERE wallet=:w AND time >= :a ORDER BY time"),
                {"w": w, "a": common.window_start()})).all()
        coins: dict[str, int] = {}
        for c, *_ in ents:
            coins[c] = coins.get(c, 0) + 1
        top = max(coins, key=coins.get) if coins else None
        files = []
        for coin in sorted(coins, key=coins.get, reverse=True)[:2]:
            cand = await common.load_candles(coin, "1h", common.window_start(), common.now_utc())
            es = [{"side": sd, "time": t, "px": float(px), "hit": bool(h)} for c, sd, t, px, h, _m in ents if c == coin]
            fn = os.path.join(out_dir, f"ut_{w[:10]}_{coin.replace(':', '_').replace('/', '_')}.png")
            if _png(fn, w, es, cand):
                files.append(os.path.basename(fn))
        moved = sum(1 for e in ents if int(e[5] or 0))
        out.append({"wallet": w, "level": lvl, "score": float(sc), "hits": k, "entries": nn, "misses": mi, "top_coin": top,
                    "coins": len(coins), "moved_market": moved, "pngs": files})
    return out


async def run(out_dir: str) -> str:
    os.makedirs(out_dir, exist_ok=True)
    pc = await precision_check()
    sh = await shuffle_check()
    kc = await known_cases()
    hr = await hand_review(out_dir)
    lines = ["# Unusual Timing validation", "", f"Run {common.now_utc():%Y-%m-%d %H:%M} UTC. Window 90 days. Numbers as computed; nothing adjusted.", ""]
    lines += ["## 1. Precision on the 20 biggest moves (30 most active coins)", "",
              f"Entrants within 60 min before, right direction: {pc['entrants']}; of these, flagged from earlier trades only: {pc['flagged_before']}.", "",
              "| # | coin | move time (UTC) | dir | size % | entrants | flagged before the move |", "| --- | --- | --- | --- | --- | --- | --- |"]
    for i, m in enumerate(pc["moves"], 1):
        fb = ", ".join(f"{w[:10]} ({lvl} {sc:.0f}, {k}/{n})" for w, lvl, sc, k, n in m["flagged_before"]) or "none"
        lines.append(f"| {i} | {m['coin']} | {m['time']:%Y-%m-%d %H:%M} | {m['direction']} | {m['magnitude']:.2f} | {m['entrants']} | {fb} |")
    lines += ["", "## 2. Shuffle test", "",
              f"Wallets scored: {sh['wallets']}. Real run: {sh['real_s1']} wallets with S1 (timing edge), {sh['real_listed']} listed (Watch or above). "
              f"Shuffled run (each wallet's entries moved by a random 1 to 3 days, seed {SHUFFLE_SEED}): {sh['shuffled_s1']} with S1, {sh['shuffled_listed']} listed.",
              f"Ratio real S1 / shuffled S1: {('%.1fx' % sh['ratio_s1']) if sh['ratio_s1'] is not None else 'undefined (0 shuffled)'}; "
              f"required at least 5x: {'PASS' if sh['pass'] else 'FAIL'}.",
              "The shuffled wallets keep their real events, deposits and trips, so only S1 is comparable; a listing needs S1, so the S1 ratio is the floor that matters.", ""]
    lines += ["## 3. Known public cases", ""]
    for c in kc:
        lines.append(f"- **{c['name']}**: {c['note']}.")
        for f in c["found"]:
            if "wallet" in f:
                lines.append(f"  - {f['wallet']}: {f['fills']} fills stored")
            else:
                lines.append(f"  - wallets ending {f['suffix']} in stored fills: {', '.join(f['wallets_in_data']) or 'none'}")
        ex = c.get("extra") or {}
        if ex.get("drop_event"):
            d = ex["drop_event"]
            lines.append(f"  - BTC down move on record: {d['time']} UTC, {d['magnitude']:.2f}% within 2 h; BTC shorts by candidates in the hour before:")
            for e in ex["btc_shorts_in_the_hour_before"]:
                lines.append(f"    - {e['wallet']} at {e['time']} ${e['notional']:,.0f} lev {e['leverage'] or 'n/a'} first deposit {e['first_deposit']}")
            if not ex["btc_shorts_in_the_hour_before"]:
                lines.append("    - none among the candidates (the four wallets were new and never on a leaderboard; our fills cover leaderboard and featured wallets)")
        elif "drop_event" in ex:
            lines.append("  - no BTC down move of threshold size is on record for 2026-10-06/07 in the stored candles")
    lines += ["", "## 4. Hand review (10 flagged wallets, entries plotted on price)", "",
              "| wallet | level | score | hits / entries | misses | moved market | coins | top coin | PNG |", "| --- | --- | --- | --- | --- | --- | --- | --- | --- |"]
    for h in hr:
        lines.append(f"| {h['wallet']} | {h['level']} | {h['score']:.0f} | {h['hits']} / {h['entries']} | {h['misses']} | {h['moved_market']} | {h['coins']} | {h['top_coin']} | {', '.join(h['pngs']) or 'no candles'} |")
    if not hr:
        lines.append("| no wallet is listed yet | | | | | | | | |")
    lines += ["", "The reading by eye is written in REPORT.md next to each PNG (timing, luck, or market impact)."]
    path = os.path.join(out_dir, "VALIDATION.md")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")
    return path
