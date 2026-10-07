"""Event-driven alert producers (spec 3.3). Each checks the user's prefs via the
gateway; none of them can raise into its caller."""
from sqlalchemy import text

from app.db.database import get_session_factory
from app.services.alerts import gateway, prefs as prefs_mod
from app.utils.logger import get_logger

logger = get_logger(__name__)


async def smi_cycle(cycle_ts, rows: list[dict]) -> int:
    """SMI crossing on the assets a user watches (alert params) or, when that
    list is empty, the assets they hold. Called at the end of each SMI cycle."""
    try:
        users = await prefs_mod.users_with("smi_cross")
        if not users or not rows:
            return 0
        from app.services.alerts.account_watch import held_assets
        now = {r["a"]: float(r["smi"]) for r in rows}
        sf = get_session_factory()
        async with sf() as s:
            prev = {a: float(v) for a, v in (await s.execute(text(
                "SELECT m.asset, m.smi FROM analytics_smi m JOIN ("
                " SELECT asset, MAX(cycle_ts) mt FROM analytics_smi WHERE cycle_ts < :cy GROUP BY asset"
                ") p ON p.asset = m.asset AND p.mt = m.cycle_ts"), {"cy": cycle_ts})).all()}
        sent = 0
        for u in users:
            watch = {a.upper() if ":" not in a else a for a in (u["params"].get("assets") or [])} \
                or held_assets.get(u["wallet"]) or set()
            hi, lo = float(u["params"].get("above", 70)), float(u["params"].get("below", 30))
            for a in watch:
                if a not in now or a not in prev:
                    continue
                v, pv = now[a], prev[a]
                level = hi if (pv < hi <= v) else lo if (pv > lo >= v) else None
                if level is None:
                    continue
                direction = "above" if level == hi else "below"
                msg = (f"<b>SMI {direction} {level:g}</b> on {a}\n"
                       f"Smart Money Index <code>{pv:.1f}</code> → <code>{v:.1f}</code> "
                       f"(cycle {cycle_ts:%H:%M} UTC)")
                r = await gateway.send_alert(alert_type="smi_cross", message=msg, user_id=u["user_id"],
                                             dedupe_key=f"smi:{a}:{direction}", dedupe_hours=6)
                sent += r.get("status") == "queued"
        return sent
    except Exception:
        logger.exception("smi_cross producer failed")
        return 0


def watched_wallet_passes(params: dict, notional: float | None) -> bool:
    thr = float(params.get("min_notional_usd", 50000) or 0)
    return thr <= 0 or (notional is not None and notional >= thr)


async def watched_wallet_recipients(chat_ids: list[str]) -> dict[str, dict]:
    """chat_id -> {user_id, params} for the chats a leader alert fans out to."""
    if not chat_ids:
        return {}
    sf = get_session_factory()
    ph = ",".join(f":c{i}" for i in range(len(chat_ids)))
    async with sf() as s:
        rows = (await s.execute(text(
            f"SELECT chat_id, user_id FROM telegram_links WHERE is_active = 1 AND chat_id IN ({ph})"),
            {f"c{i}": c for i, c in enumerate(chat_ids)})).all()
    out = {}
    for chat, uid in rows:
        p = await prefs_mod.get_prefs(int(uid))
        out[str(chat)] = {"user_id": int(uid), "enabled": p["watched_wallet"]["enabled"],
                          "params": p["watched_wallet"]["params"]}
    return out


async def copy_event(wallet: str, kind: str, message: str, dedupe_key: str | None = None) -> None:
    """kind: copy_trade | copy_skipped | copy_failed | copy_paused."""
    await gateway.send_alert(alert_type=kind, message=message, wallet=wallet,
                             dedupe_key=dedupe_key, dedupe_hours=1 if dedupe_key else 0)
