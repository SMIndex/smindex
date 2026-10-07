"""Server-side account watch for Telegram alerts (spec 3.4) — works with no
tab open. Every ACCOUNT_SWEEP_SEC, for each user with an active Telegram link,
the user's Perpl account is read from chain (same reader as the terminal) and:

  * liq_warning   — per position, margin use = MMR / (deposit + uPnL); alert at
                    or above the user's threshold (default 80%). 100% = liquidation.
  * tpsl_triggered — a position that shrank or vanished since the last read is
                    matched against the TP/SL triggers the app registered for it
                    (alert_triggers). The trigger whose level the current mark has
                    crossed is the one that fired. No registered trigger = no
                    alert (a manual close is not a TP/SL).
  * crowded_trade — held asset appears in the pulse's crowded-trade clusters.
  * daily_summary — at the user's hour in their timezone.

A failed chain read is never treated as a closed position; the first read of a
user after boot is a silent baseline.
"""
import asyncio
from datetime import datetime, timedelta, timezone

from sqlalchemy import text

from app.db.database import get_session_factory
from app.services.alerts import gateway, prefs as prefs_mod
from app.utils.logger import get_logger

logger = get_logger(__name__)

TRIGGER_TOL = 0.005        # mark within 0.5% of (or through) the trigger = fired
_prev: dict[int, dict[tuple[int, str], dict]] = {}
held_assets: dict[str, set[str]] = {}       # wallet -> symbols held (last read)
_task: asyncio.Task | None = None
stats = {"cycles": 0, "users": 0, "reads_failed": 0, "last_cycle_ms": 0}


def _mm_hdths(market_id: int) -> int | None:
    try:
        from app.services import ws_manager as wsm
        cfg = (wsm.ws_manager._market_configs or {}).get(market_id) if wsm.ws_manager else None
        v = (cfg or {}).get("maintenance_margin")
        return int(v) if v else None
    except Exception:
        return None


def _mark(market_id: int) -> float | None:
    try:
        from app.services import ws_manager as wsm
        return wsm.ws_manager.last_mark.get(market_id) if wsm.ws_manager else None
    except Exception:
        return None


def margin_use_pct(p: dict) -> float | None:
    mm = _mm_hdths(int(p["market_id"]))
    equity = float(p.get("deposit") or 0) + float(p.get("pnl") or 0)
    notional = float(p.get("notional") or 0)
    if not mm or notional <= 0:
        return None
    mmr = notional * (100.0 / mm)
    return 100.0 if equity <= 0 else round(mmr / equity * 100.0, 1)


def trigger_fired(side: str, kind: str, trig: float, mark: float) -> bool:
    if side == "long":
        return mark <= trig * (1 + TRIGGER_TOL) if kind == "sl" else mark >= trig * (1 - TRIGGER_TOL)
    return mark >= trig * (1 - TRIGGER_TOL) if kind == "sl" else mark <= trig * (1 + TRIGGER_TOL)


async def _linked_users() -> list[tuple[int, str]]:
    sf = get_session_factory()
    async with sf() as s:
        rows = (await s.execute(text(
            "SELECT u.id, u.wallet_address FROM telegram_links t JOIN users u ON u.id = t.user_id "
            "WHERE t.is_active = 1 AND t.chat_id IS NOT NULL"))).all()
    return [(int(u), w.lower()) for u, w in rows]


async def _check_triggers(uid: int, wallet: str, key: tuple[int, str], before: dict, after: dict | None) -> None:
    mid, side = key
    sf = get_session_factory()
    async with sf() as s:
        trig = (await s.execute(text(
            "SELECT id, kind, trigger_px FROM alert_triggers WHERE user_id = :u AND market_id = :m "
            "AND side = :sd AND active = 1"), {"u": uid, "m": mid, "sd": side})).all()
    if not trig:
        return
    mark = _mark(mid) or (float(after["mark_price"]) if after else None)
    if not mark:
        return
    fired = [(tid, k, float(px)) for tid, k, px in trig if trigger_fired(side, k, float(px), mark)]
    if not fired:
        return
    tid, kind, px = min(fired, key=lambda f: abs(f[2] - mark))
    sym = before.get("symbol") or f"M{mid}"
    left = f"{float(after['size']):g} left" if after else "position closed"
    msg = (f"<b>{'Stop loss' if kind == 'sl' else 'Take profit'} triggered</b> — {sym} {side}\n"
           f"Trigger <code>{px:g}</code> · mark now <code>{mark:g}</code>\n"
           f"Size before <code>{float(before['size']):g}</code> · {left}")
    await gateway.send_alert(alert_type="tpsl_triggered", message=msg, user_id=uid,
                             dedupe_key=f"tpsl:{tid}", dedupe_hours=24)
    async with sf() as s:
        await s.execute(text("UPDATE alert_triggers SET active = 0, fired_at = UTC_TIMESTAMP() "
                             "WHERE id = :i"), {"i": tid})
        if after is None:   # position gone: its sibling legs can no longer fire
            await s.execute(text("UPDATE alert_triggers SET active = 0 WHERE user_id = :u "
                                 "AND market_id = :m AND side = :sd AND active = 1"),
                            {"u": uid, "m": mid, "sd": side})
        await s.commit()


async def _sweep_user(uid: int, wallet: str, p: dict) -> None:
    from app.services import chain_reader
    detail = await asyncio.to_thread(chain_reader.get_trader_positions_only, wallet)
    if detail is None:
        stats["reads_failed"] += 1
        return      # a failed read is never a flat account
    positions = detail.get("positions") or []
    cur = {(int(x["market_id"]), x["side"]): x for x in positions}
    held_assets[wallet] = {str(x["symbol"]).upper() for x in positions}

    if p["liq_warning"]["enabled"]:
        thr = float(p["liq_warning"]["params"].get("margin_use_pct", 80))
        for (mid, side), x in cur.items():
            use = margin_use_pct(x)
            if use is not None and use >= thr:
                msg = (f"<b>Liquidation warning</b> — {x['symbol']} {side}\n"
                       f"Margin use <code>{use:.0f}%</code> (alert at {thr:.0f}%, 100% = liquidation)\n"
                       f"Mark <code>{float(x['mark_price']):g}</code> · deposit <code>${float(x['deposit']):,.2f}</code> "
                       f"· uPnL <code>${float(x['pnl']):,.2f}</code>")
                await gateway.send_alert(alert_type="liq_warning", message=msg, user_id=uid,
                                         dedupe_key=f"liq:{mid}:{side}:{int(use // 5)}", dedupe_hours=1)

    before = _prev.get(uid)
    _prev[uid] = cur
    if before is None or not p["tpsl_triggered"]["enabled"]:
        return
    for key, b in before.items():
        a = cur.get(key)
        if a is None or float(a["size"]) < float(b["size"]) * 0.999:
            await _check_triggers(uid, wallet, key, b, a)


async def _crowded(users: list[tuple[int, str]]) -> None:
    targets = {u["user_id"]: u for u in await prefs_mod.users_with("crowded_trade")}
    if not targets:
        return
    from app.routers import analytics as an
    try:
        pulse = await an.pulse()
    except Exception:
        return
    clusters = (pulse.get("crowded_trades") or {}).get("clusters") or []
    day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    for uid, wallet in users:
        if uid not in targets:
            continue
        held = held_assets.get(wallet) or set()
        for c in clusters:
            if str(c["asset"]).upper() in held:
                msg = (f"<b>Crowded trade</b> on {c['asset']} (you hold it)\n"
                       f"{c['wallet_count']} smart-money wallets {c['side']} between "
                       f"<code>{c['entry_lo']:g}</code>–<code>{c['entry_hi']:g}</code>, "
                       f"${c['notional'] / 1e6:,.1f}M together")
                await gateway.send_alert(alert_type="crowded_trade", message=msg, user_id=uid,
                                         dedupe_key=f"crowd:{c['asset']}:{c['side']}:{day}", dedupe_hours=24)


async def build_daily_summary(uid: int, wallet: str) -> str:
    since = datetime.utcnow() - timedelta(hours=24)
    sf = get_session_factory()
    async with sf() as s:
        pnl = (await s.execute(text(
            "SELECT COALESCE(SUM(pnl), 0), COUNT(*) FROM trade_history WHERE user_id = :u "
            "AND action = 'close' AND created_at >= :c"), {"u": uid, "c": since})).first()
        copies = (await s.execute(text(
            "SELECT COUNT(*) FROM copy_orders WHERE follower_wallet = :w AND status = 'filled' "
            "AND created_at >= :c"), {"w": wallet, "c": since})).scalar()
        auto = (await s.execute(text(
            "SELECT COUNT(*) FROM auto_copy_log WHERE follower_wallet = :w AND decision = 'copied' "
            "AND created_at >= :c"), {"w": wallet, "c": since})).scalar() if await _table_exists(s, "auto_copy_log") else 0
        held = sorted(held_assets.get(wallet) or [])
        smi_lines = []
        for a in held[:8]:
            r = (await s.execute(text(
                "SELECT (SELECT smi FROM analytics_smi WHERE asset = :a ORDER BY cycle_ts DESC LIMIT 1), "
                "(SELECT smi FROM analytics_smi WHERE asset = :a AND cycle_ts <= :c ORDER BY cycle_ts DESC LIMIT 1)"),
                {"a": a, "c": since})).first()
            if r and r[0] is not None:
                d = f" ({float(r[0]) - float(r[1]):+.1f} 24h)" if r[1] is not None else ""
                smi_lines.append(f"{a} SMI {float(r[0]):.0f}{d}")
    return ("<b>Daily summary</b>\n"
            f"Realized P/L (24h): <code>${float(pnl[0]):+,.2f}</code> over {int(pnl[1])} close(s)\n"
            f"Copied trades (24h): <code>{int(copies or 0) + int(auto or 0)}</code>\n"
            + ("SMI on your assets: " + "; ".join(smi_lines) if smi_lines else "No open positions to score."))


async def _table_exists(s, name: str) -> bool:
    return bool((await s.execute(text(
        "SELECT COUNT(*) FROM information_schema.tables WHERE table_schema = DATABASE() "
        "AND table_name = :t"), {"t": name})).scalar())


async def _daily(users: list[tuple[int, str]]) -> None:
    from zoneinfo import ZoneInfo
    for u in await prefs_mod.users_with("daily_summary"):
        try:
            tz = ZoneInfo(u["global"].get("tz") or "UTC")
        except Exception:
            tz = timezone.utc
        local = datetime.now(tz)
        if local.hour != int(u["params"].get("hour", 8)):
            continue
        msg = await build_daily_summary(u["user_id"], u["wallet"])
        await gateway.send_alert(alert_type="daily_summary", message=msg, user_id=u["user_id"],
                                 dedupe_key=f"daily:{local.strftime('%Y-%m-%d')}", dedupe_hours=23)


async def run_once() -> None:
    import time
    t0 = time.monotonic()
    users = await _linked_users()
    stats["users"] = len(users)
    for uid, wallet in users:
        try:
            await _sweep_user(uid, wallet, await prefs_mod.get_prefs(uid))
        except Exception:
            logger.exception("account watch failed for user %s", uid)
    for fn in (_crowded, _daily):
        try:
            await fn(users)
        except Exception:
            logger.exception("account watch %s failed", fn.__name__)
    stats["cycles"] += 1
    stats["last_cycle_ms"] = int((time.monotonic() - t0) * 1000)


async def _loop(stop: asyncio.Event) -> None:
    await asyncio.sleep(90)
    while not stop.is_set():
        try:
            await run_once()
        except Exception:
            logger.exception("account watch cycle failed")
        try:
            await asyncio.wait_for(stop.wait(), timeout=prefs_mod.ACCOUNT_SWEEP_SEC)
        except asyncio.TimeoutError:
            pass


def start(stop: asyncio.Event) -> None:
    global _task
    if _task is None or _task.done():
        _task = asyncio.get_event_loop().create_task(_loop(stop))
        logger.info("alert account watch started (every %ss)", prefs_mod.ACCOUNT_SWEEP_SEC)
