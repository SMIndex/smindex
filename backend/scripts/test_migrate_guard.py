"""The startup DDL guard (app/db/migrate.py) decides from information_schema
whether a statement changes anything. This drives _needed() with a fake
executor that answers the information_schema queries from a small catalogue.

    python scripts/test_migrate_guard.py
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.db import migrate  # noqa: E402

CATALOG = {
    "copy_orders": {"mode": ("varchar(12)", False, "paper"), "status": ("varchar(20)", False, "simulated"), "id": ("int", False, None)},
    "leader_trade_events": {"symbol": ("varchar(20)", False, None)},
    "trader_profiles": {"last_fill_at": ("datetime", True, None)},
    "strat_strategy_state": {"effective_mode": ("varchar(8)", False, None), "requested_mode": ("varchar(8)", False, None)},
}
INDEXES = {"trader_profiles": {"PRIMARY", "ix_trader_profiles_last_fill", "uq_trader_profile_exch_wallet"}}


class Res:
    def __init__(self, v):
        self.v = v

    def scalar(self):
        return self.v

    def first(self):
        return self.v


class FakeEx:
    def __init__(self):
        self.ran = []

    async def execute(self, clause, params=None):
        sql = clause.text if hasattr(clause, "text") else str(clause)
        params = params or {}
        if "information_schema.tables" in sql:
            return Res(1 if params["t"] in CATALOG else 0)
        if "information_schema.columns" in sql:
            col = CATALOG.get(params["t"], {}).get(params["c"])
            return Res((col[0], "YES" if col[1] else "NO", col[2]) if col else None)
        if "information_schema.statistics" in sql:
            return Res(1 if params["i"] in INDEXES.get(params["t"], set()) else 0)
        if "information_schema.table_constraints" in sql:
            return Res(1 if params["c"] in INDEXES.get(params["t"], set()) else 0)
        self.ran.append(sql)
        return Res(None)

    async def rollback(self):
        pass


checks = []


def check(name, got, want):
    ok = got == want
    checks.append(ok)
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}: got {got!r} want {want!r}")


async def main():
    ex = FakeEx()
    n = migrate._needed
    check("existing table: CREATE IF NOT EXISTS is nothing to do", (await n(ex, "CREATE TABLE IF NOT EXISTS copy_orders (id INT)"))[0], False)
    check("missing table: CREATE runs", (await n(ex, "CREATE TABLE IF NOT EXISTS ut_new (id INT)"))[0], True)
    check("copy_orders MODIFY mode VARCHAR(12) NOT NULL DEFAULT 'paper' already so: nothing to do",
          (await n(ex, "ALTER TABLE copy_orders MODIFY COLUMN mode VARCHAR(12) NOT NULL DEFAULT 'paper'"))[0], False)
    check("copy_orders MODIFY status VARCHAR(20) already so: nothing to do",
          (await n(ex, "ALTER TABLE copy_orders MODIFY COLUMN status VARCHAR(20) NOT NULL DEFAULT 'simulated'"))[0], False)
    check("MODIFY to a wider type runs", (await n(ex, "ALTER TABLE copy_orders MODIFY COLUMN mode VARCHAR(16) NOT NULL DEFAULT 'paper'"))[0], True)
    check("MODIFY with another default runs", (await n(ex, "ALTER TABLE copy_orders MODIFY COLUMN mode VARCHAR(12) NOT NULL DEFAULT 'live'"))[0], True)
    check("leader_trade_events symbol VARCHAR(20) already so: nothing to do", (await n(ex, "ALTER TABLE leader_trade_events MODIFY COLUMN symbol VARCHAR(20) NOT NULL"))[0], False)
    check("ADD COLUMN that exists: nothing to do", (await n(ex, "ALTER TABLE copy_orders ADD COLUMN status VARCHAR(20) NULL"))[0], False)
    check("ADD COLUMN that is missing runs", (await n(ex, "ALTER TABLE copy_orders ADD COLUMN tp_price DECIMAL(30,10) NULL"))[0], True)
    check("multi ADD COLUMN, one missing: runs", (await n(ex, "ALTER TABLE copy_orders ADD COLUMN id INT, ADD COLUMN new_one INT NULL"))[0], True)
    check("multi MODIFY both narrow: runs", (await n(ex, "ALTER TABLE strat_strategy_state MODIFY effective_mode VARCHAR(16) NOT NULL, MODIFY requested_mode VARCHAR(16) NOT NULL"))[0], True)
    check("CREATE INDEX that exists: nothing to do", (await n(ex, "CREATE INDEX ix_trader_profiles_last_fill ON trader_profiles (exchange, last_fill_at)"))[0], False)
    check("ADD INDEX with ALGORITHM/LOCK options, exists: nothing to do",
          (await n(ex, "ALTER TABLE trader_profiles ADD INDEX ix_trader_profiles_last_fill (exchange, last_fill_at), ALGORITHM=INPLACE, LOCK=NONE"))[0], False)
    check("ADD CONSTRAINT that exists: nothing to do", (await n(ex, "ALTER TABLE trader_profiles ADD CONSTRAINT uq_trader_profile_exch_wallet UNIQUE (exchange, wallet_address)"))[0], False)
    check("DROP INDEX that is missing: nothing to do", (await n(ex, "ALTER TABLE trader_profiles DROP INDEX uq_watchlist_pair"))[0], False)
    check("ALTER on a missing table: nothing to do", (await n(ex, "ALTER TABLE no_such_table ADD COLUMN x INT"))[0], False)
    # ddl() must not send anything when nothing is to do
    before = len(ex.ran)
    ran = await migrate.ddl(ex, "ALTER TABLE copy_orders MODIFY COLUMN mode VARCHAR(12) NOT NULL DEFAULT 'paper'")
    check("ddl() sends nothing for a no-op (no SET lock_wait_timeout, no ALTER)", (ran, len(ex.ran) - before), (False, 0))
    ran = await migrate.ddl(ex, "ALTER TABLE copy_orders ADD COLUMN brand_new INT NULL")
    check("ddl() sets lock_wait_timeout then runs a real change", (ran, [r[:30] for r in ex.ran[-2:]]),
          (True, ["SET SESSION lock_wait_timeout ", "ALTER TABLE copy_orders ADD CO"]))
    check("summary counts", (migrate.stats["checked"], migrate.stats["applied"], migrate.stats["nothing_to_do"]), (2, 1, 1))
    n_ok, n_all = sum(checks), len(checks)
    print(f"\n{n_ok}/{n_all} checks passed")
    sys.exit(0 if n_ok == n_all else 1)


asyncio.run(main())
