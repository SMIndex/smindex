"""Overnight Part B verification: one real Telegram message of EVERY alert type
to a linked user's chat, through the real gateway (prefs -> delivery log ->
queue -> Telegram), printing each delivery's Telegram message_id.

Real data wherever it exists (copy orders, SMI values, pulse crowding, the
daily summary); otherwise the message says [verification] explicitly.
Run on the server:  venv/bin/python scripts/verify_telegram_alerts.py <user_id>
"""
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import text  # noqa: E402

from app.config import settings  # noqa: E402
from app.db import database as dbm  # noqa: E402
from app.services import telegram_queue  # noqa: E402
from app.services.alerts import gateway, account_watch  # noqa: E402

TAG = "<i>[verification — overnight pass 2026-09-29]</i>"


async def main(uid: int) -> None:
    await dbm.init_db(settings.DATABASE_URL)
    from telegram import Bot, LinkPreviewOptions
    bot = Bot(settings.TELEGRAM_BOT_TOKEN)

    async def send(chat_id, message, buttons):
        return await bot.send_message(chat_id=int(chat_id), text=message, parse_mode="HTML",
                                      link_preview_options=LinkPreviewOptions(is_disabled=True))
    telegram_queue.start(send)

    async with dbm.get_session_factory()() as s:
        wallet = (await s.execute(text("SELECT wallet_address FROM users WHERE id=:u"), {"u": uid})).scalar()
        orders = {r["status"]: r for r in (await s.execute(text(
            "SELECT id, symbol, side, action, status, skip_reason, fill_size, fill_price, perpl_order_id, "
            "error_message, created_at FROM copy_orders WHERE follower_wallet=:w ORDER BY id DESC"),
            {"w": wallet})).mappings().all()[::-1]}
        smi = (await s.execute(text(
            "SELECT cycle_ts, smi FROM analytics_smi WHERE asset='BTC' ORDER BY cycle_ts DESC LIMIT 2"))).all()
        ev = (await s.execute(text(
            "SELECT trader_wallet, symbol, side, event_type, size, price, detected_at FROM leader_trade_events "
            "WHERE exchange='hl' AND event_type IN ('opened','closed') ORDER BY id DESC LIMIT 1"))).mappings().first()

    msgs = []
    f = orders.get("filled")
    msgs.append(("copy_trade", f"<b>Copy {'opened' if f['action']=='open' else 'closed'}</b> — {f['symbol']} {f['side']}\n"
                 f"Filled <code>{float(f['fill_size']):g}</code> @ <code>{float(f['fill_price']):g}</code> · order "
                 f"<code>{f['perpl_order_id']}</code>\nReal copy order #{f['id']} ({f['created_at']:%Y-%m-%d %H:%M} UTC)\n{TAG}"))
    b = orders.get("risk_blocked")
    msgs.append(("copy_skipped", f"<b>Copy skipped</b> — {b['symbol']} {b['side']} ({b['action']})\nGate: "
                 f"<code>{b['skip_reason']}</code>\nReal copy order #{b['id']}\n{TAG}"))
    x = orders.get("failed")
    msgs.append(("copy_failed", f"<b>Copy order failed</b> — {x['symbol']} {x['side']} ({x['action']})\n"
                 f"{(x['error_message'] or 'no error text recorded')[:160]}\nReal copy order #{x['id']}\n{TAG}"))
    msgs.append(("copy_paused", "<b>Auto-copy paused</b> — example: daily loss limit $30 reached\n"
                 "No live auto-copy subscription exists yet, so this one is a labelled sample.\n" + TAG))
    msgs.append(("tpsl_triggered", "<b>Stop loss triggered</b> — BTC long\nTrigger <code>81000</code> · mark now "
                 "<code>80980</code>\nLabelled sample: no registered TP/SL has fired on your account yet.\n" + TAG))
    msgs.append(("tpsl_leftover", "<b>Leftover TP/SL cancelled</b> — BTC\n2 orders cancelled: full close\n"
                 "Labelled sample of the message the app sends after it auto-cancels.\n" + TAG))
    msgs.append(("liq_warning", "<b>Liquidation warning</b> — BTC long\nMargin use <code>84%</code> (alert at 80%)\n"
                 "Labelled sample: your account has no open position right now.\n" + TAG))
    if len(smi) == 2:
        msgs.append(("smi_cross", f"<b>SMI update</b> on BTC\nSmart Money Index <code>{float(smi[1][1]):.1f}</code> → "
                     f"<code>{float(smi[0][1]):.1f}</code> (real values, cycle {smi[0][0]:%H:%M} UTC; "
                     "a real alert fires only when a level is crossed)\n" + TAG))
    from app.routers import analytics as an
    try:
        clusters = ((await an.pulse()).get("crowded_trades") or {}).get("clusters") or []
    except Exception:
        clusters = []
    if clusters:
        c = clusters[0]
        msgs.append(("crowded_trade", f"<b>Crowded trade</b> on {c['asset']}\n{c['wallet_count']} smart-money wallets "
                     f"{c['side']} between <code>{c['entry_lo']:g}</code>–<code>{c['entry_hi']:g}</code> "
                     f"(real cluster from the pulse; you don't hold it, so this one is sent as verification)\n{TAG}"))
    if ev:
        msgs.append(("watched_wallet", f"<b>Watched wallet {ev['event_type']}</b> — {ev['symbol']} {ev['side']}\n"
                     f"{ev['trader_wallet'][:10]}… size <code>{float(ev['size'] or 0):g}</code> @ "
                     f"<code>{float(ev['price'] or 0):g}</code>\nReal HL event from {ev['detected_at']:%Y-%m-%d %H:%M} UTC\n{TAG}"))
    msgs.append(("daily_summary", (await account_watch.build_daily_summary(uid, wallet)) + "\n" + TAG))
    msgs.append(("test", "<b>SMINDEX test message</b>\nYour alerts reach this chat.\n" + TAG))

    results = []
    for kind, m in msgs:
        r = await gateway.send_alert(alert_type=kind, message=m, user_id=uid, force=True,
                                     dedupe_key=f"verify:{kind}:20260929")
        results.append((kind, r))
    await telegram_queue._queue.join()
    await asyncio.sleep(1.0)
    async with dbm.get_session_factory()() as s:
        ids = [r["log_id"] for _, r in results if r.get("log_id")]
        rows = {row[0]: row for row in (await s.execute(text(
            "SELECT id, alert_type, status, tg_message_id, delivered_at, error FROM telegram_alert_log "
            f"WHERE id IN ({','.join(str(i) for i in ids)})"))).all()} if ids else {}
    print(f"{'alert type':16} {'log id':>7} {'status':8} {'telegram message_id':>20}  delivered_at (UTC)")
    for kind, r in results:
        row = rows.get(r.get("log_id"))
        print(f"{kind:16} {r.get('log_id', '-')!s:>7} {(row[2] if row else r.get('status')):8} "
              f"{(row[3] if row else '-')!s:>20}  {(row[4] if row else '')!s}{('  ERR ' + row[5]) if row and row[5] else ''}")
    await telegram_queue.stop()


if __name__ == "__main__":
    asyncio.run(main(int(sys.argv[1])))
