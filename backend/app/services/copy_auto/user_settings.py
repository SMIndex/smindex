"""Per-user auto-copy settings: the master on/off toggle and the defaults a new
subscription starts from. No row = on, Standard preset, Shadow."""
import json

from sqlalchemy import text

from app.db.database import get_session_factory
from app.services.copy_auto import rules

DEFAULT_KEYS = ["mode", "sizing", "margin_usd", "allocation_usd", "max_leverage", "max_positions",
                "mirror_adds", "mirror_reduces", "reopen_on_flip", "drift_pct", "sl_margin_pct", "tp_pct",
                "daily_loss_usd", "total_loss_usd"]


def standard_defaults() -> dict:
    d = {**rules.DEFAULTS, **rules.PRESETS["standard"]}
    return {k: d.get(k) for k in DEFAULT_KEYS}


async def get(user_id: int) -> dict:
    sf = get_session_factory()
    async with sf() as s:
        r = (await s.execute(text(
            "SELECT enabled, defaults, updated_at FROM auto_copy_user_settings WHERE user_id=:u"),
            {"u": user_id})).first()
    base = standard_defaults()
    if not r:
        return {"enabled": True, "defaults": base, "updated_at": None}
    stored = json.loads(r[1]) if r[1] else {}
    return {"enabled": bool(r[0]), "defaults": {**base, **{k: v for k, v in stored.items() if k in DEFAULT_KEYS}},
            "updated_at": r[2].isoformat() if r[2] else None}


async def enabled(user_id: int) -> bool:
    sf = get_session_factory()
    async with sf() as s:
        v = (await s.execute(text("SELECT enabled FROM auto_copy_user_settings WHERE user_id=:u"),
                             {"u": user_id})).scalar()
    return True if v is None else bool(v)


async def save(user_id: int, *, enabled: bool | None = None, defaults: dict | None = None) -> dict:
    cur = await get(user_id)
    en = cur["enabled"] if enabled is None else bool(enabled)
    dfl = cur["defaults"] if defaults is None else {**cur["defaults"], **defaults}
    sf = get_session_factory()
    async with sf() as s:
        await s.execute(text(
            "INSERT INTO auto_copy_user_settings (user_id, enabled, defaults, updated_at) "
            "VALUES (:u, :e, :d, UTC_TIMESTAMP()) ON DUPLICATE KEY UPDATE enabled=VALUES(enabled), "
            "defaults=VALUES(defaults), updated_at=UTC_TIMESTAMP()"),
            {"u": user_id, "e": 1 if en else 0, "d": json.dumps(dfl)})
        await s.commit()
    return await get(user_id)
