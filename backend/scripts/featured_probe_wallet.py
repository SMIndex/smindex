"""Run the featured pipeline for ONE Hyperliquid wallet against the configured
database and print the metrics (a smoke test of the venue shapes and the
whole process_wallet path; it spends real Hyperliquid weight, about 100).

    python scripts/featured_probe_wallet.py 0xabc...
"""
import asyncio
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.config import settings                     # noqa: E402
from app.db import database                          # noqa: E402
from app.services.featured import jobs               # noqa: E402
from app.services.featured.schema import DDL         # noqa: E402
from sqlalchemy import text                          # noqa: E402


async def main(wallet: str) -> None:
    await database.init_db(settings.DATABASE_URL)
    async with database.engine.begin() as c:
        for s in DDL:
            await c.execute(text(s))
    run = jobs.Run("probe")
    await run.open()
    coins = await jobs.perpl_coins()
    print("perpl coins:", sorted(coins))
    m = await jobs.process_wallet(run, {"wallet": wallet.lower(), "display_name": None}, coins, 10_000)
    await run.close(True, "probe")
    keep = {k: v for k, v in m.items() if k not in ("coin_stats", "hours_utc", "weekly", "flags", "score_inputs")}
    print(json.dumps(keep, indent=1, default=str))
    print("flags:")
    for k, v in m["flags"].items():
        print(f"  {k:14s} pass={v['pass']!s:5s} value={v['value']}")
    print(f"weight used {run.weight}, requests {run.requests}, notes {run.notes}")
    trips = await jobs.load_trips("hl", wallet.lower())
    print(f"stored trips: {len(trips)}; newest 3:")
    for t in trips[-3:]:
        print("  ", t["coin"], t["side"], t["open_time"], t["close_time"], round(t["pnl_usd"], 2), t["n_fills"], t.get("leverage"))
    await database.close_db()


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1]))
