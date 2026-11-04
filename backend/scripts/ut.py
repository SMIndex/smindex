"""Unusual Timing CLI (build brief section 3). Read-only except `run`,
`validate` (writes VALIDATION.md and PNGs) and `alerts test` (one row in
ut_alerts_sent that the API poller sends to the admin chat).

    python scripts/ut.py report [--level strong] [--top 50]
    python scripts/ut.py wallet <address> [--png DIR]
    python scripts/ut.py cluster <id>
    python scripts/ut.py events [--coin BTC] [--days 30]
    python scripts/ut.py drift [--top 20]
    python scripts/ut.py validate [--out DIR]
    python scripts/ut.py alerts test
    python scripts/ut.py run daily|hourly|<step>   (the worker's pass, or one step: coin_stats events entries forward close scores clusters drift)
    python scripts/ut.py status
"""
import argparse
import asyncio
import json
import os
import sys
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import text                          # noqa: E402

from app.config import settings                      # noqa: E402
from app.db import database                          # noqa: E402
from app.services.ut import common, rules, worker, validate  # noqa: E402
from app.services.ut.schema import DDL               # noqa: E402

OUT_DEFAULT = os.environ.get("UT_OUT_DIR") or os.path.join(common.DATA_DIR, "..")


def fmt_usd(v) -> str:
    return "n/a" if v is None else f"${float(v):,.0f}"


def pct(v) -> str:
    return "n/a" if v is None else f"{float(v) * 100:.0f}%"


def line_for(r) -> str:
    """One report line: level, score, hits / entries, base rate, events, misses, worst loss, moved market."""
    return (f"{r['wallet']}  {str(r['level']).upper():7s} score {float(r['score']):5.1f}  hits {r['hits']}/{r['entries']} "
            f"(base {float(r['blended_base_rate'] or 0) * 100:.1f}%, p {float(r['p_value']) if r['p_value'] is not None else float('nan'):.2e})  "
            f"events {r['events_preceded']} ({r['event_kinds']} kinds)  misses {r['misses']}  worst loss {fmt_usd(r['worst_loss_usd'])}  "
            f"moved market {r['moved_market_count']}" + (f"  cluster #{r['cluster_id']}" if r['cluster_id'] else ""))


async def cmd_report(args) -> None:
    sf = database.get_session_factory()
    async with sf() as s:
        as_of = (await s.execute(text("SELECT MAX(as_of) FROM ut_wallet_scores"))).scalar()
        if not as_of:
            print("no scores yet")
            return
        q = "SELECT * FROM ut_wallet_scores WHERE as_of=:d AND level <> 'none'"
        if args.level:
            lv = {"watch": ("watch", "strong", "cluster"), "strong": ("strong", "cluster"), "cluster": ("cluster",)}[args.level]
            q += " AND level IN (" + ",".join(f"'{x}'" for x in lv) + ")"
        q += " ORDER BY score DESC, hits DESC LIMIT :n"
        rows = (await s.execute(text(q), {"d": as_of, "n": args.top})).mappings().all()
        total = (await s.execute(text("SELECT COUNT(*), SUM(level='watch'), SUM(level='strong'), SUM(level='cluster') FROM ut_wallet_scores WHERE as_of=:d"), {"d": as_of})).first()
        meta = {w: (fd, fl) for w, fd, fl in (await s.execute(text("SELECT wallet, first_deposit_at, funder_label FROM ut_wallet_meta"))).all()}
    print(f"Unusual timing report as of {as_of}: {total[0]} wallets scored, watch {int(total[1] or 0)}, strong {int(total[2] or 0)}, cluster {int(total[3] or 0)}")
    print("Levels: Watch = unusual timing on several trades, could be skill. Strong = repeated timing before events plus a fresh wallet, "
          "size shock or burst profile. Cluster = several wallets moving together with the same pattern.\n")
    for i, r in enumerate(rows, 1):
        print(f"{i:3d}. {line_for(r)}")
        fd, fl = meta.get(r["wallet"], (None, None))
        ev = json.loads(r["evidence"]) if isinstance(r["evidence"], str) else (r["evidence"] or {})
        age = f"{(datetime.utcnow() - fd).days} days" if fd else "unknown"
        print(f"      signals S1..S7 {''.join(str(r[f's{i}']) for i in range(1, 8))}  wallet age {age}  funder {fl or ev.get('funder') or 'unknown'}  "
              f"first deposit {fmt_usd(ev.get('first_deposit_usd'))}  hold median {r['median_hold_min']} min  top3 share {pct(r['top3_profit_share'])}  exit fast {r['exit_speed_hits']}")
        evs = ev.get("events") or []
        if evs:
            print("      events preceded: " + "; ".join(f"{e['kind']} {e['coin']} {e['time']} ({e['minutes_before']} min before, {e['entry_side']})" for e in evs[:6]))
        tt = ev.get("top_trades") or []
        if tt:
            print("      top trades: " + "; ".join(f"{t['coin']} {t['side']} {fmt_usd(t['pnl_usd'])} ({t['open']} to {t['close']})" for t in tt))
        for n in ev.get("notes") or []:
            print(f"      note: {n}")


async def cmd_wallet(args) -> None:
    w = args.address.lower()
    sf = database.get_session_factory()
    async with sf() as s:
        meta = (await s.execute(text("SELECT * FROM ut_wallet_meta WHERE wallet=:w"), {"w": w})).mappings().first()
        hist = (await s.execute(text("SELECT as_of, level, score, hits, entries, misses, p_value, cluster_id FROM ut_wallet_scores WHERE wallet=:w ORDER BY as_of DESC LIMIT 30"), {"w": w})).all()
        latest = (await s.execute(text("SELECT * FROM ut_wallet_scores WHERE wallet=:w ORDER BY as_of DESC LIMIT 1"), {"w": w})).mappings().first()
        ents = (await s.execute(text(
            "SELECT e.*, v.kind ev_kind FROM ut_entries e LEFT JOIN ut_events v ON v.id=e.event_id WHERE e.wallet=:w AND e.time >= :a ORDER BY e.time"),
            {"w": w, "a": common.window_start()})).mappings().all()
        cl = None
        if latest and latest["cluster_id"]:
            cl = (await s.execute(text("SELECT * FROM ut_clusters WHERE id=:i"), {"i": latest["cluster_id"]})).mappings().first()
    print(f"Wallet {w}")
    if meta:
        print(f"  first deposit {meta['first_deposit_at']} {fmt_usd(meta['first_deposit_usd'])} ({meta['first_deposit_kind'] or 'n/a'})  funder {meta['funder'] or 'unknown'} "
              f"{('(' + meta['funder_label'] + ')') if meta['funder_label'] else ''}  excluded {meta['excluded'] or 'no'}  maker share {pct(meta['maker_share'])}  "
              f"median hold {meta['median_hold_sec']} s  account value {fmt_usd(meta['account_value'])}  history from {meta['history_from']}")
    else:
        print("  no meta row (not a candidate yet)")
    if latest:
        print("  latest: " + line_for(latest))
        print(f"  level words: {rules.LEVEL_WORDS.get(latest['level'])}")
    print("  score history:")
    for d, lv, sc, k, n, mi, p, cid in hist:
        print(f"    {d}  {lv:7s} {float(sc):5.1f}  {k}/{n} hits, {mi} misses, p {p if p is None else format(float(p), '.2e')}  cluster {cid or '-'}")
    if cl:
        print(f"  cluster #{cl['id']}: {cl['member_count']} wallets, total notional {fmt_usd(cl['total_notional'])}, lead {cl['lead_wallet']}, edges {cl['edge_kinds']}")
    print(f"  entries (last 90 days): {len(ents)}, hits {sum(1 for e in ents if e['hit'])}, misses {sum(1 for e in ents if e['hit'] == 0)}, "
          f"not measured {sum(1 for e in ents if e['hit'] is None)}, moved market {sum(1 for e in ents if e['moved_market'])}")
    print("    time (UTC)        coin       side   notional     mfe30m  mfe2h  mfe24h  ret2h   hit  moved  event            closed after peak")
    for e in ents:
        print(f"    {e['time']:%Y-%m-%d %H:%M}  {e['coin'][:10]:10s} {e['side']:6s} {float(e['notional']):>11,.0f}  "
              f"{(e['mfe_30m'] if e['mfe_30m'] is not None else float('nan')):6.2f} {(e['mfe_2h'] if e['mfe_2h'] is not None else float('nan')):6.2f} "
              f"{(e['mfe_24h'] if e['mfe_24h'] is not None else float('nan')):7.2f} {(e['ret_2h'] if e['ret_2h'] is not None else float('nan')):6.2f}   "
              f"{'HIT' if e['hit'] else ('-' if e['hit'] == 0 else '?'):3s}  {'yes' if e['moved_market'] else 'no ':5s}  "
              f"{(e['ev_kind'] or '-'):16s} {('%d min' % e['close_after_peak_min']) if e['close_after_peak_min'] is not None else '-'}")
    if args.png:
        os.makedirs(args.png, exist_ok=True)
        coins = {}
        for e in ents:
            coins[e["coin"]] = coins.get(e["coin"], 0) + 1
        for coin in sorted(coins, key=coins.get, reverse=True)[:3]:
            cand = await common.load_candles(coin, "1h", common.window_start(), common.now_utc())
            es = [{"side": e["side"], "time": e["time"], "px": float(e["px"]), "hit": bool(e["hit"])} for e in ents if e["coin"] == coin]
            fn = os.path.join(args.png, f"ut_{w[:10]}_{coin.replace(':', '_').replace('/', '_')}.png")
            print(f"  png {fn}: {'written' if validate._png(fn, w, es, cand) else 'no candles or matplotlib missing'}")


async def cmd_cluster(args) -> None:
    sf = database.get_session_factory()
    async with sf() as s:
        cl = (await s.execute(text("SELECT * FROM ut_clusters WHERE id=:i"), {"i": args.id})).mappings().first()
        if not cl:
            print("no such cluster")
            return
        mem = (await s.execute(text(
            "SELECT m.wallet, m.edges, w.level, w.score, w.hits, w.entries, w.misses, w.worst_loss_usd, t.first_deposit_at, t.funder_label "
            "FROM ut_cluster_members m LEFT JOIN ut_wallet_scores w ON w.wallet=m.wallet AND w.as_of=:d LEFT JOIN ut_wallet_meta t ON t.wallet=m.wallet "
            "WHERE m.cluster_id=:i ORDER BY w.score DESC"), {"i": args.id, "d": cl["as_of"]})).all()
        evs = json.loads(cl["shared_events"] or "[]")
        ev_rows = []
        if evs:
            from sqlalchemy import bindparam
            ev_rows = (await s.execute(text("SELECT id, time, coin, kind, direction, magnitude FROM ut_events WHERE id IN :i").bindparams(bindparam("i", expanding=True)), {"i": evs})).all()
        pairs = (await s.execute(text(
            "SELECT c.wallet_a, c.wallet_b, c.coin, c.side, c.time_a, c.time_b, c.on_event FROM ut_coentries c JOIN ut_cluster_members a ON a.wallet=c.wallet_a AND a.cluster_id=:i "
            "JOIN ut_cluster_members b ON b.wallet=c.wallet_b AND b.cluster_id=:i ORDER BY c.time_a"), {"i": args.id})).all()
    print(f"Cluster #{cl['id']} as of {cl['as_of']}: {cl['member_count']} wallets, combined entry notional {fmt_usd(cl['total_notional'])}, "
          f"lead {cl['lead_wallet']}, edges {cl['edge_kinds']}")
    print("Members:")
    for w, e, lv, sc, k, n, mi, wl, fd, fl in mem:
        print(f"  {w}  edges {e}  {lv or '-':7s} {float(sc or 0):5.1f}  hits {k}/{n}  misses {mi}  worst loss {fmt_usd(wl)}  first deposit {fd or 'unknown'}  funder {fl or 'unknown'}")
    print(f"Shared events ({len(ev_rows)}):")
    for i, t, c, k, d, m in ev_rows:
        print(f"  #{i} {t:%Y-%m-%d %H:%M} {c} {k} {d} {m or ''}")
    print(f"Timeline of co-entries ({len(pairs)}):")
    for a, b, c, sd, ta, tb, oe in pairs[:80]:
        print(f"  {ta:%Y-%m-%d %H:%M:%S}  {a[:10]} and {b[:10]}  {c} {sd}  {int((tb - ta).total_seconds())} s apart{'  on event' if oe else ''}")


async def cmd_events(args) -> None:
    sf = database.get_session_factory()
    q = "SELECT id, time, coin, kind, direction, magnitude, source, note FROM ut_events WHERE time >= :a"
    p = {"a": datetime.utcnow() - timedelta(days=args.days)}
    if args.coin:
        q += " AND (coin=:c OR coin='ALL')"
        p["c"] = args.coin
    q += " ORDER BY time DESC LIMIT 500"
    async with sf() as s:
        rows = (await s.execute(text(q), p)).all()
        counts = (await s.execute(text("SELECT kind, COUNT(*) FROM ut_events WHERE time >= :a GROUP BY kind"), {"a": p["a"]})).all()
    print("Events in the last %d days: %s" % (args.days, ", ".join(f"{k} {n}" for k, n in counts)))
    for i, t, c, k, d, m, src, note in rows:
        print(f"  #{i:<6d} {t:%Y-%m-%d %H:%M}  {c or 'ALL':12s} {k:12s} {d:7s} {('%.2f%%' % m) if m is not None else '':8s} {src:14s} {note or ''}")


async def cmd_drift(args) -> None:
    sf = database.get_session_factory()
    async with sf() as s:
        t = (await s.execute(text("SELECT MAX(t) FROM ut_market_drift WHERE drift_score > 0"))).scalar()
        rows = (await s.execute(text(
            "SELECT coin, funding_trend_48h, oi_change_6h, oi_change_24h, abn_ret_12h, ut_wallets_6h, drift_score FROM ut_market_drift WHERE t=:t "
            "ORDER BY drift_score DESC LIMIT :n"), {"t": t, "n": args.top})).all()
    print(f"Pre-event drift at {t} UTC (score 0 to 100: abnormal 12 h return, OI 24 h and 6 h, funding trend 48 h, flagged wallets in 6 h)")
    print("  coin          score  z12h    oi6h%   oi24h%  funding48h     UT wallets 6h")
    for c, f, o6, o24, z, n, sc in rows:
        print(f"  {c:12s} {float(sc):6.1f}  {(float(z) if z is not None else float('nan')):6.2f}  {(float(o6) if o6 is not None else float('nan')):6.1f}  "
              f"{(float(o24) if o24 is not None else float('nan')):6.1f}  {(float(f) if f is not None else float('nan')):+.6f}   {n}")


async def cmd_validate(args) -> None:
    path = await validate.run(args.out)
    print(open(path, encoding="utf-8").read())
    print(f"written {path}")


async def cmd_alerts_test(args) -> None:
    """One sample alert row; the API poller sends it to the admin chat. Then
    the proof: the row's sent_at and chat_id, the queue stats, and the log
    lines of this delivery."""
    sf = database.get_session_factory()
    payload = {"test": True, "level": "watch", "score": 57.1, "hit_rate": 0.5, "hits": 6, "entries": 12, "events_preceded": 3, "misses": 6,
               "worst_loss_usd": -1234.5, "age_days": 4, "cluster_id": None, "is_add": False, "leverage": 5}
    async with sf() as s:
        r = await s.execute(text(
            "INSERT INTO ut_alerts_sent (wallet, coin, side, notional, level, payload, trade_at, created_at) VALUES "
            "('0x0000000000000000000000000000000000000000', 'BTC', 'short', 150000, 'watch', :p, UTC_TIMESTAMP(3), UTC_TIMESTAMP(3))"),
            {"p": json.dumps(payload)})
        await s.commit()
        rid = r.lastrowid
    print(f"queued test alert #{rid}; waiting for the API poller")
    for _ in range(30):
        await asyncio.sleep(1)
        async with sf() as s:
            row = (await s.execute(text("SELECT sent_at, chat_id, error FROM ut_alerts_sent WHERE id=:i"), {"i": rid})).first()
        if row and (row[0] or row[2]):
            break
    print(f"row #{rid}: sent_at {row[0]} chat_id {row[1]} error {row[2]}")
    admin = (settings.ADMIN_TELEGRAM_CHAT_ID or "").strip()
    print(f"ADMIN_TELEGRAM_CHAT_ID configured: {'yes' if admin else 'no'}; row chat matches admin: {str(row[1]) == admin}")
    async with sf() as s:
        others = (await s.execute(text("SELECT COUNT(*) FROM ut_alerts_sent WHERE chat_id IS NOT NULL AND chat_id NOT IN ('queued', :a)"), {"a": admin or "-"})).scalar()
    print(f"alert rows ever stamped with a chat other than the admin chat: {others}")
    print(f"proof lines: grep 'ut alert #{rid}' /var/log/perpl-terminal.log and the next 'telegram_queue stats' line")


async def cmd_run(args) -> None:
    from app.services.ut import clusters, coin_stats, drift, entries, events, scores
    steps = {"coin_stats": lambda: coin_stats.build(), "events": lambda: events.build(full=True), "entries": lambda: entries.build_all(),
             "forward": lambda: entries.compute_forward(), "close": lambda: entries.close_times(), "scores": lambda: scores.build(),
             "clusters": lambda: clusters.build(), "drift": lambda: drift.build()}
    if args.which == "daily":
        print(json.dumps(await worker.run_daily(), indent=1, default=str))
    elif args.which == "hourly":
        print(json.dumps(await worker.run_hourly(), indent=1, default=str))
    else:
        print(json.dumps(await worker._step("ut_" + args.which, steps[args.which]()), indent=1, default=str))


async def cmd_status(args) -> None:
    sf = database.get_session_factory()
    async with sf() as s:
        runs = (await s.execute(text(
            "SELECT job, started_at, finished_at, ok, requests, weight, notes FROM ut_job_runs ORDER BY id DESC LIMIT 25"))).all()
        wt = (await s.execute(text("SELECT DATE(started_at), SUM(weight), SUM(requests) FROM ut_job_runs GROUP BY DATE(started_at) ORDER BY 1 DESC LIMIT 7"))).all()
        sizes = (await s.execute(text(
            "SELECT table_name, table_rows, ROUND((data_length + index_length) / 1048576, 1) FROM information_schema.tables "
            "WHERE table_schema = DATABASE() AND table_name LIKE 'ut\\_%' ORDER BY table_name"))).all()
        q = (await s.execute(text("SELECT kind, SUM(done_at IS NULL), COUNT(*) FROM ut_queue GROUP BY kind"))).all()
    print("Hyperliquid weight per day (ut jobs):")
    for d, w, r in wt:
        print(f"  {d}  weight {int(w or 0):6d}  requests {int(r or 0)}")
    print("Tables:")
    tot = 0.0
    for t, n, mb in sizes:
        tot += float(mb or 0)
        print(f"  {t:20s} rows ~{int(n or 0):>9,d}  {float(mb or 0):8.1f} MB")
    print(f"  total {tot:.1f} MB; disk free {common.free_gb():.2f} GB")
    print("Queue (pending / total): " + ", ".join(f"{k} {int(p or 0)}/{int(n)}" for k, p, n in q))
    print("Recent runs:")
    for j, a, b, ok, rq, w, n in runs:
        print(f"  {a:%m-%d %H:%M} {j:22s} {'ok ' if ok else ('...' if ok is None else 'ERR')} w {w:6d} req {rq:5d}  {(n or '')[:110]}")


async def main() -> None:
    ap = argparse.ArgumentParser(prog="ut")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("report"); p.add_argument("--level", choices=["watch", "strong", "cluster"]); p.add_argument("--top", type=int, default=50)
    p = sub.add_parser("wallet"); p.add_argument("address"); p.add_argument("--png", default=None)
    p = sub.add_parser("cluster"); p.add_argument("id", type=int)
    p = sub.add_parser("events"); p.add_argument("--coin"); p.add_argument("--days", type=int, default=30)
    p = sub.add_parser("drift"); p.add_argument("--top", type=int, default=20)
    p = sub.add_parser("validate"); p.add_argument("--out", default=OUT_DEFAULT)
    p = sub.add_parser("alerts"); p.add_argument("what", choices=["test"])
    p = sub.add_parser("run"); p.add_argument("which", choices=["daily", "hourly", "coin_stats", "events", "entries", "forward", "close", "scores", "clusters", "drift"])
    sub.add_parser("status")
    args = ap.parse_args()
    await database.init_db(settings.DATABASE_URL)
    async with database.engine.begin() as conn:
        for stmt in DDL:
            await conn.execute(text(stmt))
    try:
        await {"report": cmd_report, "wallet": cmd_wallet, "cluster": cmd_cluster, "events": cmd_events, "drift": cmd_drift,
               "validate": cmd_validate, "alerts": cmd_alerts_test, "run": cmd_run, "status": cmd_status}[args.cmd](args)
    finally:
        await database.close_db()


if __name__ == "__main__":
    asyncio.run(main())
