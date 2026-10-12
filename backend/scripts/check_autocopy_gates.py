"""Read-only: would an auto-copy entry pass the gates right now, per market?

Runs the SHIPPING rules (services/copy_auto/rules.py) over live inputs for a
hypothetical open on every active Perpl market, and prints the first gate that
would stop it. Nothing is written, no order frame is built, and the venue is
never contacted for anything but public data.

Inputs are the same ones the engine gathers, with two substitutions a
standalone process has to make (the engine reads them from its own in-memory
ws caches, which only exist inside the API process):
  * Perpl mark  <- /api/markets        (what ws_manager.last_mark caches)
  * basis bps   <- /api/hl/basis       (what hl_prices.basis_bps_for_market computes)
Leader leverage is left unknown, which is the engine's own conservative path:
min(your max, market max).

    venv/bin/python scripts/check_autocopy_gates.py <sub_id>
"""
import asyncio
import json
import os
import sys
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import text                                   # noqa: E402

from app.config import settings                               # noqa: E402
from app.db.database import get_session_factory, init_db      # noqa: E402
from app.services.copy_auto import executor, rules            # noqa: E402

API = "http://127.0.0.1:8002"
SUB_ID = int(sys.argv[1]) if len(sys.argv) > 1 else 1


def api(path):
    with urllib.request.urlopen(f"{API}{path}", timeout=20) as r:
        return json.load(r)


async def main():
    await init_db(settings.DATABASE_URL)
    sf = get_session_factory()
    async with sf() as s:
        sub = dict((await s.execute(text("SELECT * FROM auto_copy_subs WHERE id=:i"),
                                    {"i": SUB_ID})).mappings().first())
        in_use_margin, opens = (await s.execute(text(
            "SELECT COALESCE(SUM(margin_usd),0), COUNT(*) FROM auto_copy_positions "
            "WHERE sub_id=:s AND status='open' AND mode=:m"),
            {"s": SUB_ID, "m": sub["mode"]})).first()

    wallet = sub["follower_wallet"]
    print(f"sub {SUB_ID}: {sub['mode']} | leader {sub['leader_wallet'][:12]} | "
          f"status {sub['status']} | sizing {sub['sizing']} margin ${sub['margin_usd']} "
          f"allocation ${sub['allocation_usd']} max_lev {sub['max_leverage']}x "
          f"max_pos {sub['max_positions']} drift {sub['drift_pct']}%")

    mk = api("/api/markets")
    mk = mk.get("markets", mk) if isinstance(mk, dict) else mk
    marks = {m["market_id"]: m.get("mark_price") or m.get("price") for m in mk}
    basis = {r["perpl_market_id"]: r["basis_bps"] for r in api("/api/hl/basis")}

    from app.services import chain_reader
    acct = await asyncio.to_thread(chain_reader.get_trader_positions_only, wallet)
    free = float(acct["balance"]) if acct and acct.get("balance") is not None else None
    on_chain = {int(p["market_id"]) for p in (acct or {}).get("positions") or []}
    print(f"chain: free margin ${free} | open on-chain markets {sorted(on_chain) or 'none'}")
    print(f"copied open now: {opens} (margin in use ${float(in_use_margin):.2f})\n")

    from app.services.copy import live_orders
    allowed = json.loads(sub["markets"]) if sub.get("markets") else None
    rows = []
    for m in sorted(mk, key=lambda x: x["market_id"]):
        mid, sym = m["market_id"], m.get("symbol") or m.get("name")
        mp = await executor.market_params(mid)
        market_max = await live_orders.market_max_leverage(mid)
        lev, _ = rules.choose_leverage(None, float(sub["max_leverage"]), market_max)
        mark = float(marks.get(mid) or 0)
        size, margin, why = (rules.size_open(
            sizing=sub["sizing"], margin_usd=float(sub["margin_usd"]),
            allocation_usd=float(sub["allocation_usd"]), margin_in_use=float(in_use_margin),
            leverage=lev, price=mark, leader_size=1.0, leader_account_value=100000.0,
            size_decimals=mp["size_decimals"], min_scaled=mp["min_scaled"])
            if mp and mark else (0.0, 0.0, "no market params or mark"))
        g = rules.GateInput(
            side="long", kill_switch_on=rules_live_switch(), allowlisted=settings.live_allowlist_ok(wallet),
            live=sub["mode"] == "live", market_id=mid, markets_allowed=allowed,
            market_in_use=mid in on_chain, leader_px=mark, perpl_px=mark,
            drift_pct=float(sub["drift_pct"]), basis_bps=basis.get(mid),
            open_positions=int(opens), max_positions=int(sub["max_positions"]),
            daily_realized=0.0, total_realized=0.0, daily_loss_usd=sub["daily_loss_usd"],
            total_loss_usd=sub["total_loss_usd"], free_margin=free, margin_needed=margin,
            leverage=lev, market_max_leverage=market_max, event_age_sec=1.0)
        gate, _ = rules.run_gates(g)
        if why and not gate:
            gate = f"sizing: {why}"
        rows.append((mid, sym, mark, lev, size, margin, basis.get(mid), gate))

    print(f"{'mkt':>4} {'sym':<6} {'mark':>12} {'lev':>5} {'size':>12} {'margin':>8} "
          f"{'basis':>7}  verdict")
    passes = 0
    for mid, sym, mark, lev, size, margin, b, gate in rows:
        if gate is None:
            passes += 1
        print(f"{mid:>4} {sym:<6} {mark:>12,.6g} {lev:>4.0f}x {size:>12,.6g} "
              f"${margin:>7.2f} {('%.1f' % b) if b is not None else '  n/a':>7}  "
              f"{'WOULD OPEN' if gate is None else gate}")
    print(f"\n{passes} of {len(rows)} live Perpl markets would pass every gate right now.")


def rules_live_switch():
    from app.services.copy_auto.engine import live_switch_on
    return live_switch_on()


asyncio.run(main())
