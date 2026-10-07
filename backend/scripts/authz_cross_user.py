"""Cross-user authorization test (beta readiness 3.1). NEVER point at prod data.

Runs the real FastAPI app in-process (no lifespan, no background loops) against a
throwaway SCHEMA-ONLY database given by DATABASE_URL (the caller sets it to the
scratch schema; the script refuses the prod schema name). Two fresh users:
  A seeds one row per private resource (marker string AUTHZ_A_MARKER);
  B reads every private list/singleton -> must not contain A's marker, ids or wallet;
  B mutates every A id -> must be refused, and A's rows must be unchanged after;
  anonymous calls every authenticated route -> must be refused.
Usage (server):  DATABASE_URL=mysql+aiomysql://u:p@127.0.0.1/authz_beta \
                 venv/bin/python scripts/authz_cross_user.py
"""
import asyncio
import json
import os
import secrets
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

DB_URL = os.environ["DATABASE_URL"]
assert "perpl_terminal" not in DB_URL.split("/")[-1], "refusing to run against the prod schema"

import httpx  # noqa: E402
from jose import jwt as jose_jwt  # noqa: E402
from sqlalchemy import text  # noqa: E402

from app.config import settings  # noqa: E402
from app.db import database as dbm  # noqa: E402

MARK = "AUTHZ_A_MARKER"
results: list[tuple[str, str, str]] = []


def rec(name: str, ok: bool, detail: str = "") -> None:
    results.append(("PASS" if ok else "FAIL", name, detail))
    print(("PASS " if ok else "FAIL ") + name + (f"  [{detail}]" if detail else ""))


def wallet() -> str:
    return "0x" + secrets.token_hex(20)


async def make_user(w: str) -> tuple[int, str]:
    sf = dbm.get_session_factory()
    async with sf() as s:
        now = datetime.utcnow()
        r = await s.execute(text("INSERT INTO users (wallet_address, created_at, updated_at) VALUES (:w, :n, :n)"),
                            {"w": w, "n": now})
        await s.commit()
        uid = int(r.lastrowid)
    tok = jose_jwt.encode({"sub": str(uid), "wallet": w, "iat": datetime.now(timezone.utc),
                           "exp": datetime.now(timezone.utc) + timedelta(hours=1)}, settings.JWT_SECRET, algorithm="HS256")
    return uid, tok


async def sql(q: str, **kw):
    sf = dbm.get_session_factory()
    async with sf() as s:
        r = await s.execute(text(q), kw)
        try:
            rows = [dict(x) for x in r.mappings().all()]
        except Exception:
            rows = []
        await s.commit()
        return rows


async def seed_required(table: str, overrides: dict) -> int:
    """Insert one row filling every NOT NULL column without a default."""
    cols = await sql("SELECT column_name c, data_type t, is_nullable n, column_default d, extra e, character_maximum_length l "
                     "FROM information_schema.columns WHERE table_schema = DATABASE() AND table_name = :t", t=table)
    vals = dict(overrides)
    for c in cols:
        name = c["c"]
        if name in vals or c["n"] == "YES" or c["d"] is not None or "auto_increment" in (c["e"] or ""):
            continue
        t = c["t"]
        vals[name] = (datetime.utcnow() if t in ("datetime", "timestamp", "date") else
                      0 if t in ("int", "bigint", "tinyint", "smallint", "double", "float", "decimal") else
                      "{}" if t == "json" else
                      ("long" if name == "side" else MARK[: int(c["l"] or len(MARK))]))
    names = ", ".join(f"`{k}`" for k in vals)
    params = ", ".join(f":{k}" for k in vals)
    sf = dbm.get_session_factory()
    async with sf() as s:
        r = await s.execute(text(f"INSERT INTO `{table}` ({names}) VALUES ({params})"), vals)
        await s.commit()
        return int(r.lastrowid)


async def main() -> None:
    await dbm.init_db(DB_URL)
    from app.main import app
    A_w, B_w, L_w = wallet(), wallet(), wallet()
    A_id, A_tok = await make_user(A_w)
    B_id, B_tok = await make_user(B_w)
    HA = {"Authorization": f"Bearer {A_tok}"}
    HB = {"Authorization": f"Bearer {B_tok}"}
    tr = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=tr, base_url="http://authz") as c:

        async def call(m, path, h=None, body=None):
            try:
                r = await c.request(m, path, headers=h or {}, json=body, timeout=60)
                return r.status_code, r.text
            except Exception as exc:  # a crash inside the app = a 500 for the caller
                return 599, f"{type(exc).__name__}: {exc}"[:200]

        ids: dict[str, object] = {}
        # ---- A seeds one row per private resource (API where possible, SQL where gated) ----
        st, t = await call("POST", "/api/price-alerts", HA, {"market_id": 1, "symbol": "BTC", "condition": "above", "target_price": 123456.0})
        ids["alert"] = json.loads(t).get("id") if st < 300 else None
        st, t = await call("POST", "/api/journal", HA, {"market_id": 1, "symbol": "BTC", "side": "long", "entry_price": 1.0, "notes": MARK})
        ids["journal"] = json.loads(t).get("id") if st < 300 else None
        st, t = await call("POST", "/api/sl-tp/orders", HA, {"market_id": 1, "side": "long", "order_type": "sl", "trigger_price": 1.0, "size": 0.001})
        ids["sltp"] = json.loads(t).get("id") if st < 300 else None
        st, t = await call("POST", "/api/mcp-tokens", HA, {"label": MARK, "scopes": ["read"]})
        st, t = await call("GET", "/api/mcp-tokens", HA)
        lst = json.loads(t) if st < 300 else []
        lst = lst.get("tokens", lst) if isinstance(lst, dict) else lst
        ids["mcp"] = lst[0].get("id") if lst else None
        await call("POST", f"/api/watchlist/{L_w}", HA)
        await call("POST", "/api/copy/follow", HA, {"leader_wallet": L_w, "allocation_usd": 77})
        st, t = await call("POST", "/api/copy/subscriptions", HA, {"trader_wallet": L_w, "allocation_usd": 77})
        ids["copysub"] = json.loads(t).get("id") if st < 300 else None
        if not ids["copysub"]:
            ids["copysub"] = await seed_required("copy_subscriptions", {"follower_wallet": A_w.lower(), "trader_wallet": L_w, "status": "active", "allocation_usd": 77})
        ids["copyorder"] = await seed_required("copy_orders", {"follower_wallet": A_w.lower(), "trader_wallet": L_w, "idempotency_key": MARK + secrets.token_hex(4), "status": "pending"})
        ids["copypos"] = await seed_required("copy_paper_positions", {"subscription_id": ids["copysub"], "follower_wallet": A_w.lower(), "trader_wallet": L_w, "status": "open"}) if await sql("SHOW TABLES LIKE 'copy_paper_positions'") else None
        await call("POST", "/api/trades", HA, {"market_id": 1, "side": "long", "action": "close", "order_type": "market", "size": 0.001, "price": 1.0})
        await call("PUT", "/api/telegram/prefs/copy_trade", HA, {"enabled": False})
        await call("POST", "/api/telegram/triggers", HA, {"oid": 987654, "market_id": 1, "side": "long", "kind": "sl", "trigger_px": 1.0, "size": 0.001})
        await call("PUT", "/api/copy-auto/settings", HA, {"enabled": False, "defaults": {"margin_usd": 77}})
        ids["autosub"] = await seed_required("auto_copy_subs", {"user_id": A_id, "follower_wallet": A_w.lower(), "leader_wallet": L_w, "mode": "shadow", "status": "active",
                                                                "created_at": datetime.utcnow(), "updated_at": datetime.utcnow()})
        from app.services.auth_service import encrypt_token
        await sql("INSERT INTO auto_copy_keys (user_id, wallet, api_key_enc, seed_enc, pub_hex, scope, label, created_at) "
                  "VALUES (:u, :w, :k, :s, :p, 2, :l, UTC_TIMESTAMP())", u=A_id, w=A_w.lower(), k=encrypt_token(MARK + "-apikey"),
                  s=encrypt_token("00" * 32), p="ab" * 32, l=MARK)
        print("seeded:", json.dumps(ids, default=str))

        async def snapshot():
            return {
                "alerts": await sql("SELECT COUNT(*) n FROM price_alerts WHERE user_id = :u", u=A_id),
                "journal": await sql("SELECT notes FROM trade_journal WHERE user_id = :u", u=A_id) if await sql("SHOW TABLES LIKE 'trade_journal'") else [],
                "copysub": await sql("SELECT status, allocation_usd FROM copy_subscriptions WHERE id = :i", i=ids["copysub"]),
                "copyorder": await sql("SELECT status FROM copy_orders WHERE id = :i", i=ids["copyorder"]),
                "autosub": await sql("SELECT status, mode FROM auto_copy_subs WHERE id = :i", i=ids["autosub"]),
                "autokey": await sql("SELECT COUNT(*) n FROM auto_copy_keys WHERE user_id = :u", u=A_id),
                "autoset": await sql("SELECT enabled, defaults FROM auto_copy_user_settings WHERE user_id = :u", u=A_id),
                "tgpref": await sql("SELECT * FROM telegram_alert_prefs WHERE user_id = :u", u=A_id),
                "triggers": await sql("SELECT COUNT(*) n FROM alert_triggers WHERE user_id = :u", u=A_id),
                "mcp": await sql("SELECT COUNT(*) n FROM mcp_tokens WHERE user_id = :u", u=A_id) if await sql("SHOW TABLES LIKE 'mcp_tokens'") else [],
            }
        before = await snapshot()

        # ---- B reads every private list / singleton ----
        reads = ["/api/price-alerts", "/api/journal", "/api/sl-tp/orders", "/api/mcp-tokens", "/api/watchlist", "/api/copy/my-follows",
                 "/api/copy/my-copies", "/api/copy/subscriptions", "/api/copy/orders", "/api/copy/positions", "/api/copy/live-positions",
                 "/api/copy/history", "/api/copy/audit-logs", "/api/copy/risk-events", "/api/copy/stats", "/api/copy/followed-leaders/positions",
                 "/api/trades/history", "/api/trades/orders", "/api/trades/copy-performance", "/api/orders/history",
                 "/api/telegram/prefs", "/api/telegram/alert-log", "/api/telegram/status", "/api/copy-auto/settings", "/api/copy-auto/subs",
                 "/api/copy-auto/key", "/api/copy-auto/log", "/api/copy-auto/positions", "/api/autocopy/configs", "/api/autocopy/pending",
                 "/api/export/trades", "/api/export/copies", "/api/export/pnl", f"/api/copy/leaders/{L_w}/followers"]
        for p in reads:
            st, t = await call("GET", p, HB)
            leak = [k for k in (MARK, A_w.lower(), '"margin_usd": 77', '"margin_usd":77.0', '"allocation_usd":77') if k in t.replace(" ", "") or k in t]
            rec(f"B reads {p}", st < 500 and not leak, f"{st}{' LEAK ' + str(leak) if leak else ''}")
        st, t = await call("GET", "/api/copy-auto/key", HB)
        rec("B sees no auto-copy key", '"present":false' in t.replace(" ", ""), t[:80])
        st, t = await call("GET", "/api/copy-auto/key", HA)
        rec("A key status never returns secrets", MARK + "-apikey" not in t and "seed" not in t.lower(), t[:120])

        # ---- B mutates every A id ----
        muts = [("DELETE", f"/api/price-alerts/{ids['alert']}", None), ("DELETE", f"/api/journal/{ids['journal']}", None),
                ("PUT", f"/api/journal/{ids['journal']}", {"notes": "B was here"}), ("DELETE", f"/api/sl-tp/orders/{ids['sltp']}", None),
                ("DELETE", f"/api/mcp-tokens/{ids['mcp']}", None), ("PATCH", f"/api/copy/subscriptions/{ids['copysub']}", {"allocation_usd": 1}),
                ("POST", f"/api/copy/subscriptions/{ids['copysub']}/pause", None), ("POST", f"/api/copy/subscriptions/{ids['copysub']}/stop", None),
                ("PATCH", f"/api/copy/orders/{ids['copyorder']}", {"status": "failed"}), ("PATCH", f"/api/copy-auto/subs/{ids['autosub']}", {"status": "paused"}),
                ("DELETE", f"/api/copy-auto/subs/{ids['autosub']}", None), ("DELETE", "/api/telegram/triggers/987654", None),
                ("POST", "/api/copy-auto/pause-all", None), ("DELETE", "/api/copy-auto/key", None), ("PUT", "/api/copy-auto/settings", {"enabled": True}),
                ("PUT", "/api/telegram/prefs/copy_trade", {"enabled": True}), ("DELETE", f"/api/copy/follow/{L_w}", None),
                ("DELETE", f"/api/watchlist/{L_w}", None)]
        if ids.get("copypos"):
            muts.append(("PATCH", f"/api/copy/positions/{ids['copypos']}/close", {"close_price": 1.0}))
        for m, p, b in muts:
            st, t = await call(m, p, HB, b)
            # B's own-scope writes (settings/prefs/pause-all/own key) may succeed: they must not touch A (checked below)
            own_scope = p in ("/api/copy-auto/pause-all", "/api/copy-auto/key", "/api/copy-auto/settings", "/api/telegram/prefs/copy_trade") or \
                p.startswith(("/api/copy/follow/", "/api/watchlist/"))
            # an owner-scoped DELETE that matches nothing may answer 200; the row check below is what counts
            idem = m == "DELETE" and p.startswith("/api/telegram/triggers/")
            rec(f"B {m} {p}", own_scope or idem or 400 <= st < 500, str(st) + (" (owner-scoped no-op)" if idem and st < 300 else ""))
        after = await snapshot()
        for k in before:
            rec(f"A's {k} unchanged after B's attempts", before[k] == after[k], "" if before[k] == after[k] else f"{before[k]} -> {after[k]}")
        st, t = await call("GET", "/api/watchlist", HA)
        rec("A's watchlist intact", L_w in t.lower(), str(st))
        st, t = await call("GET", "/api/copy/my-follows", HA)
        rec("A's follow intact", L_w in t.lower(), str(st))

        # ---- anonymous on every authenticated route ----
        from app.routers.auth import get_authenticated_user  # noqa: F401

        def deps(d, acc):
            for x in d.dependencies:
                acc.add(getattr(x.call, "__name__", "")); deps(x, acc)
            return acc
        anon_bad = []
        n = 0
        for r in app.routes:
            if not hasattr(r, "dependant") or not getattr(r, "methods", None):
                continue
            if not ({"get_authenticated_user", "require_admin"} & deps(r.dependant, set())):
                continue
            path = r.path
            for seg in ("{alert_id}", "{entry_id}", "{order_id}", "{token_id}", "{sub_id}", "{sid}", "{position_id}", "{post_id}", "{oid}", "{id}"):
                path = path.replace(seg, "1")
            path = path.replace("{leader_wallet}", L_w).replace("{trader_wallet}", L_w).replace("{leader}", L_w) \
                       .replace("{alert_type}", "copy_trade").replace("{exchange}", "hl").replace("{wallet}", L_w)
            for m in r.methods:
                if m in ("HEAD", "OPTIONS"):
                    continue
                n += 1
                st, _ = await call(m, path, None, {} if m in ("POST", "PUT", "PATCH") else None)
                if st not in (401, 403, 422, 429):
                    anon_bad.append(f"{m} {path} -> {st}")
                st2, _ = await call(m, path, HB, {} if m in ("POST", "PUT", "PATCH") else None) if "require_admin" in deps(r.dependant, set()) else (403, "")
                if st2 not in (401, 403, 422, 429):
                    anon_bad.append(f"non-admin {m} {path} -> {st2}")
        rec(f"anonymous refused on all {n} authenticated routes (+ non-admin on admin routes)", not anon_bad, "; ".join(anon_bad))

    fails = [r for r in results if r[0] == "FAIL"]
    print(f"\nTOTAL {len(results)}  PASS {len(results) - len(fails)}  FAIL {len(fails)}")
    await dbm.close_db() if hasattr(dbm, "close_db") else None


asyncio.run(main())
