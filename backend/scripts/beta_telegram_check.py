"""Beta readiness section 6: Telegram alerts honour preferences, quiet hours and
the hourly limit + digest.

  real <user_id>   PROD, the user's linked chat. One labelled message per alert
                   type through the normal gateway (force=False, so preferences
                   apply); then copy_trade is switched OFF and a copy_trade alert
                   must be refused with no delivery. The user's copy_trade setting
                   is restored exactly afterwards.
  dev              THROWAWAY schema (DATABASE_URL must not be perpl_terminal), no
                   Telegram: a fake queue captures sends. Quiet hours hold
                   non-critical alerts and pass critical ones; 25 alerts against
                   a 20/hour limit -> 20 sent + 5 digested -> one digest message.
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from sqlalchemy import text  # noqa: E402

from app.config import settings  # noqa: E402
from app.db import database as dbm  # noqa: E402
from app.services import telegram_queue  # noqa: E402
from app.services.alerts import gateway, prefs  # noqa: E402

TAG = "<i>[beta readiness check 2026-10-07 — labelled test, no action needed]</i>"
res = []


def check(name, ok, detail=""):
    res.append(ok)
    print(("PASS " if ok else "FAIL ") + name + (f"  [{detail}]" if detail else ""))


async def q(sql, **kw):
    async with dbm.get_session_factory()() as s:
        r = await s.execute(text(sql), kw)
        rows = r.all() if r.returns_rows else []
        await s.commit()
        return rows


async def real(uid: int) -> None:
    await dbm.init_db(settings.DATABASE_URL)
    from telegram import Bot, LinkPreviewOptions
    bot = Bot(settings.TELEGRAM_BOT_TOKEN)

    async def send(chat_id, message, buttons):
        return await bot.send_message(chat_id=int(chat_id), text=message, parse_mode="HTML",
                                      link_preview_options=LinkPreviewOptions(is_disabled=True))
    telegram_queue.start(send)
    p = await prefs.get_prefs(uid)
    print("user prefs:", {k: v["enabled"] for k, v in p.items() if k != "_global"}, "| global", p["_global"]["params"])
    out = []
    for t, meta in list(prefs.ALERT_TYPES.items()) + [("test", {})]:
        r = await gateway.send_alert(alert_type=t, user_id=uid, dedupe_key=f"beta1007:{t}",
                                     message=f"<b>{t.replace('_', ' ')}</b> — check message\n{TAG}",
                                     force=(t == "test"))
        expect = "queued" if (t == "test" or p.get(t, {}).get("enabled")) else "disabled"
        if expect == "queued" and r["status"] == "quiet_hours":
            expect = "quiet_hours"
        out.append((t, r))
        check(f"{t}: preference {'on' if p.get(t, {}).get('enabled') else 'off'} -> {r['status']}", r["status"] == expect, f"expected {expect}")
    # switch copy_trade OFF -> nothing may be delivered; restore exactly afterwards
    orig = await q("SELECT enabled, params FROM telegram_alert_prefs WHERE user_id=:u AND alert_type='copy_trade'", u=uid)
    await prefs.set_pref(uid, "copy_trade", False, None)
    prefs._cache.pop(uid, None)
    before = (await q("SELECT COUNT(*) FROM telegram_alert_log WHERE user_id=:u", u=uid))[0][0]
    r = await gateway.send_alert(alert_type="copy_trade", user_id=uid, message=f"<b>must not arrive</b>\n{TAG}", dedupe_key="beta1007:off")
    after = (await q("SELECT COUNT(*) FROM telegram_alert_log WHERE user_id=:u", u=uid))[0][0]
    check("copy_trade switched OFF -> refused, nothing queued or logged", r["status"] == "disabled" and after == before, f"{r['status']} log {before}->{after}")
    if orig:
        await q("UPDATE telegram_alert_prefs SET enabled=:e, params=:p WHERE user_id=:u AND alert_type='copy_trade'", e=orig[0][0], p=orig[0][1], u=uid)
    else:
        await q("DELETE FROM telegram_alert_prefs WHERE user_id=:u AND alert_type='copy_trade'", u=uid)
    prefs._cache.pop(uid, None)
    check("copy_trade preference restored", (await prefs.get_prefs(uid))["copy_trade"]["enabled"] == p["copy_trade"]["enabled"])
    await telegram_queue._queue.join()
    await asyncio.sleep(1.5)
    ids = [r["log_id"] for _, r in out if r.get("log_id")]
    rows = {x[0]: x for x in await q(f"SELECT id, status, tg_message_id, delivered_at, error FROM telegram_alert_log WHERE id IN ({','.join(map(str, ids))})")} if ids else {}
    print(f"\n{'alert type':16} {'log id':>7} {'status':10} {'telegram message_id':>20}")
    for t, r in out:
        row = rows.get(r.get("log_id"))
        print(f"{t:16} {r.get('log_id', '-')!s:>7} {(row[1] if row else r['status']):10} {(row[2] if row else '-')!s:>20}{('  ERR ' + row[4]) if row and row[4] else ''}")
    delivered = [r for r in rows.values() if r[2]]
    check("every queued alert was delivered with a Telegram message_id", len(delivered) == len([1 for _, r in out if r["status"] == "queued"]), f"{len(delivered)} delivered")
    await telegram_queue.stop()


async def dev() -> None:
    assert "perpl_terminal" not in os.environ["DATABASE_URL"].split("/")[-1]
    await dbm.init_db(os.environ["DATABASE_URL"])
    sent = []
    telegram_queue.enqueue = lambda chat, message, buttons=None, dedupe_key=None, on_result=None: (sent.append(message) or True)
    await q("INSERT INTO users (wallet_address, created_at, updated_at) VALUES ('0x7e57c0de0000000000000000000000000000a1e7', UTC_TIMESTAMP(), UTC_TIMESTAMP())")
    uid = (await q("SELECT id FROM users WHERE wallet_address='0x7e57c0de0000000000000000000000000000a1e7'"))[0][0]
    cols = {r[0] for r in await q("SELECT column_name FROM information_schema.columns WHERE table_schema=DATABASE() AND table_name='telegram_links'")}
    vals = {"user_id": uid, "chat_id": "999000111", "is_active": 1}
    if "created_at" in cols:
        vals["created_at"] = None
    await q("INSERT INTO telegram_links (" + ",".join(vals) + ") VALUES (" + ",".join(
        "UTC_TIMESTAMP()" if k == "created_at" else f":{k}" for k in vals) + ")", **{k: v for k, v in vals.items() if k != "created_at"})
    from datetime import datetime, timezone
    h = datetime.now(timezone.utc).hour
    await prefs.set_pref(uid, "_global", None, {"quiet_enabled": True, "quiet_start": h, "quiet_end": (h + 2) % 24, "tz": "UTC"})
    prefs._cache.pop(uid, None)
    r1 = await gateway.send_alert(alert_type="copy_trade", user_id=uid, message="quiet test (non-critical)")
    r2 = await gateway.send_alert(alert_type="copy_failed", user_id=uid, message="quiet test (critical)")
    check("quiet hours hold a non-critical alert", r1["status"] == "quiet_hours", r1["status"])
    check("quiet hours let a critical alert (order failed) through", r2["status"] == "queued", r2["status"])
    await prefs.set_pref(uid, "_global", None, {"quiet_enabled": False, "hourly_limit": 20})
    prefs._cache.pop(uid, None)
    gateway._sent_times.pop(uid, None)
    sent.clear()
    st = [(await gateway.send_alert(alert_type="copy_trade", user_id=uid, message=f"burst {i}\nline"))["status"] for i in range(25)]
    check("hourly limit 20: 20 sent, 5 held for the digest", st.count("queued") == 20 and st.count("digested") == 5, f"{st.count('queued')} queued / {st.count('digested')} digested")
    n_before = len(sent)
    await gateway._flush_digests()
    dig = sent[n_before:]
    check("the 5 held alerts go out as ONE digest message", len(dig) == 1 and "5 alert(s)" in dig[0], (dig[0][:90] if dig else "none"))


if __name__ == "__main__":
    asyncio.run(real(int(sys.argv[2])) if sys.argv[1] == "real" else dev())
    print(f"\nTOTAL {len(res)} PASS {sum(res)} FAIL {len(res) - sum(res)}")
