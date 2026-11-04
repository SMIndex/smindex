"""Unusual Timing worker process (service smindex-ut): the compute schedule
in services/ut/worker.py. No Hyperliquid reads happen here.

    python scripts/ut_worker.py            # the schedule (daily 03:30, hourly :20 UTC)
    python scripts/ut_worker.py daily      # one daily pass now, then exit
    python scripts/ut_worker.py hourly     # one hourly pass now, then exit
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.config import settings                      # noqa: E402
from app.db import database                          # noqa: E402
from app.services.ut import worker                   # noqa: E402
from app.services.ut.schema import DDL               # noqa: E402
from app.utils.logger import get_logger              # noqa: E402
from sqlalchemy import text                          # noqa: E402

logger = get_logger("ut_worker")


async def main(mode: str) -> None:
    await database.init_db(settings.DATABASE_URL)
    async with database.engine.begin() as conn:
        for stmt in DDL:
            await conn.execute(text(stmt))
    try:
        if mode == "daily":
            print(await worker.run_daily())
        elif mode == "hourly":
            print(await worker.run_hourly())
        else:
            logger.info("ut worker started (daily %02d:%02d UTC, hourly :%02d)", worker.DAILY_AT[0], worker.DAILY_AT[1], worker.HOURLY_MIN)
            await worker.loop()
    finally:
        await database.close_db()


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1] if len(sys.argv) > 1 else "loop"))
