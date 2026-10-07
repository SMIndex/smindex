"""Beta readiness 4.5: manual copy kill switch + allowlist + pass-value audit.
Throwaway schema only (DATABASE_URL must not be perpl_terminal). Same gate-input
mocks as tests/test_live_copy_gates.py; NO signing, NO orders.
  1. COPY_LIVE_ENABLED=False          -> risk_blocked / live_disabled
  2. follower not in LIVE_COPY_ALLOWLIST -> risk_blocked / not_in_live_allowlist
  3. all gates pass                   -> submitted; gate_snapshot carries the values
  4. through the real router (POST /api/copy/orders, real JWT) the audit row
     'confirm_live_copy' has detail.gates, and the response never returns it
"""
import asyncio
import json
import os
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
assert "perpl_terminal" not in os.environ["DATABASE_URL"].split("/")[-1], "refusing to run against prod"

import httpx  # noqa: E402
from jose import jwt as jose_jwt  # noqa: E402
from sqlalchemy import text  # noqa: E402

from app.config import settings  # noqa: E402
from app.db import database as dbm  # noqa: E402

FOLLOWER = "0x7e57c0de0000000000000000000000000000c0de"
OUTSIDER = "0x7e57c0de00000000000000000000000000000bad"
LEADER = "0x7e57c0de000000000000000000000000000f00d5"
res = []


def check(name, ok, detail=""):
    res.append(ok)
    print(("PASS " if ok else "FAIL ") + name + (f"  [{detail}]" if detail else ""))


async def main():
    await dbm.init_db(os.environ["DATABASE_URL"])
    from app.services.copy import live_orders
    from app.services import market_registry
    from app.services.hyperliquid import prices as hl_prices

    async def yes(*a, **k): return True
    async def lev(*a, **k): return 15.0
    async def basis(*a, **k): return 5.0
    live_orders._perpl_eligible = yes
    market_registry.is_market_active = yes
    live_orders.market_max_leverage = lev
    hl_prices.basis_bps_for_market = basis
    settings.COPY_MODE = "live_manual"
    settings.COPY_REQUIRE_INTERNAL_ALLOWLIST = False
    base = dict(trader_wallet=LEADER, market_id=1, symbol="BTC", side="long", leverage=2, allocation_usd=10,
                intended_size=0.0002, intended_price=100000.0, order_type="market")

    settings.COPY_LIVE_ENABLED = False
    settings.LIVE_COPY_ALLOWLIST = [FOLLOWER]
    o = await live_orders.create_attempt(follower_wallet=FOLLOWER, idempotency_key="beta:ks", **base)
    check("kill switch off blocks", o["status"] == "risk_blocked" and o.get("skip_reason") == "live_disabled", f"{o['status']}/{o.get('skip_reason')}")

    settings.COPY_LIVE_ENABLED = True
    o = await live_orders.create_attempt(follower_wallet=OUTSIDER, idempotency_key="beta:al", **base)
    check("allowlist blocks a wallet not on it", o["status"] == "risk_blocked" and o.get("skip_reason") == "not_in_live_allowlist", f"{o['status']}/{o.get('skip_reason')}")
    settings.LIVE_COPY_ALLOWLIST = []
    o = await live_orders.create_attempt(follower_wallet=FOLLOWER, idempotency_key="beta:empty", **base)
    check("empty allowlist blocks everyone (fail-closed)", o["status"] == "risk_blocked" and o.get("skip_reason") == "not_in_live_allowlist", f"{o['status']}/{o.get('skip_reason')}")

    settings.LIVE_COPY_ALLOWLIST = [FOLLOWER]
    o = await live_orders.create_attempt(follower_wallet=FOLLOWER, idempotency_key="beta:pass", **base)
    snap = o.get("gate_snapshot") or {}
    check("all gates pass -> submitted with gate_snapshot", o["status"] == "submitted" and bool(snap), json.dumps(snap)[:160])

    # real router + real JWT dependency -> audit detail.gates
    sf = dbm.get_session_factory()
    async with sf() as s:
        r = await s.execute(text("INSERT INTO users (wallet_address, created_at, updated_at) VALUES (:w, UTC_TIMESTAMP(), UTC_TIMESTAMP())"), {"w": FOLLOWER})
        await s.commit()
        uid = r.lastrowid
    tok = jose_jwt.encode({"sub": str(uid), "wallet": FOLLOWER, "exp": datetime.now(timezone.utc) + timedelta(hours=1)},
                          settings.JWT_SECRET, algorithm="HS256")
    from app.main import app
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
        body = dict(trader_wallet=LEADER, market_id=1, symbol="BTC", side="long", intended_size=0.0002,
                    intended_price=100000.0, leverage=2, allocation_usd=10, idempotency_key="beta:router", action="open")
        r = await c.post("/api/copy/orders", json=body, headers={"Authorization": f"Bearer {tok}"})
    check("router response never returns gate_snapshot", r.status_code < 300 and "gate_snapshot" not in r.text, f"{r.status_code}")
    async with sf() as s:
        row = (await s.execute(text("SELECT detail FROM audit_logs WHERE action='confirm_live_copy' ORDER BY id DESC LIMIT 1"))).scalar()
    d = json.loads(row) if isinstance(row, str) else (row or {})
    check("audit confirm_live_copy carries detail.gates", bool(d.get("gates")), json.dumps(d.get("gates"))[:200])
    print(f"\nTOTAL {len(res)} PASS {sum(res)} FAIL {len(res) - sum(res)}")


asyncio.run(main())
