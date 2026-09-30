"""Per-user Telegram alert preferences (spec SMINDEX_FEATURES_8-10 Part 3).

One row per (user, alert_type) in telegram_alert_prefs; the reserved type
'_global' holds quiet hours, the hourly limit and the user's timezone. A user
with no rows gets the spec defaults. Writes invalidate the in-process cache,
so producers read a changed toggle on their very next event.

`delay` is the honest latency of each alert's producer — shown verbatim in the
settings help text.
"""
import json
import time

from sqlalchemy import text

from app.db.database import get_session_factory

ACCOUNT_SWEEP_SEC = 120          # alerts/account_watch.py cadence

ALERT_TYPES: dict[str, dict] = {
    # --- Copy trading ---
    "copy_trade": {
        "group": "copy", "label": "Copied trade opened / closed", "default": True, "params": {},
        "delay": "Real time — sent the moment the copy order fills or closes.",
    },
    "copy_skipped": {
        "group": "copy", "label": "Trade skipped (with reason)", "default": False, "params": {},
        "delay": "Real time — sent when a gate blocks a copy, with the gate and value.",
    },
    "copy_failed": {
        "group": "copy", "label": "Order failed", "default": True, "params": {}, "critical": True,
        "delay": "Real time — sent when a copy order is rejected or errors.",
    },
    "copy_paused": {
        "group": "copy", "label": "Subscription auto-paused", "default": True, "params": {},
        "delay": "Real time — sent when an auto-copy subscription pauses itself.",
    },
    # --- Positions (server side, no tab needed) ---
    "tpsl_triggered": {
        "group": "positions", "label": "TP or SL triggered", "default": True, "params": {},
        "delay": f"Checked every {ACCOUNT_SWEEP_SEC // 60} min from the chain — works with no tab open.",
    },
    "tpsl_leftover": {
        "group": "positions", "label": "Leftover TP/SL cancelled", "default": True, "params": {},
        "delay": "Real time — sent when SMINDEX cancels TP/SL left behind by a closed position.",
    },
    "liq_warning": {
        "group": "positions", "label": "Liquidation warning", "default": True,
        "params": {"margin_use_pct": 80}, "critical": True,
        "delay": f"Checked every {ACCOUNT_SWEEP_SEC // 60} min from the chain — works with no tab open.",
    },
    # --- Smart money ---
    "smi_cross": {
        "group": "smart_money", "label": "SMI crosses a level on an asset I watch", "default": False,
        "params": {"above": 70, "below": 30, "assets": []},
        "delay": "Every 20 min (each analytics cycle). Empty asset list = the assets you hold.",
    },
    "watched_wallet": {
        "group": "smart_money", "label": "A watched wallet opens or closes", "default": True,
        "params": {"min_notional_usd": 50000},
        "delay": "Seconds for live-tracked wallets; up to 20 min for the rest.",
    },
    "crowded_trade": {
        "group": "smart_money", "label": "Crowded trade warning on an asset I hold", "default": False,
        "params": {},
        "delay": "Every 20 min (each analytics cycle), at most once a day per asset.",
    },
    # --- Summary ---
    "daily_summary": {
        "group": "summary", "label": "Daily summary", "default": True, "params": {"hour": 8},
        "delay": "Once a day at your chosen hour, in your timezone.",
    },
}

GLOBAL_DEFAULTS = {"quiet_enabled": False, "quiet_start": 23, "quiet_end": 7,
                   "hourly_limit": 20, "tz": "UTC"}
GROUPS = [("copy", "Copy trading"), ("positions", "Positions"),
          ("smart_money", "Smart money"), ("summary", "Summary")]

_CACHE_TTL = 60.0
_cache: dict[int, tuple[float, dict]] = {}


def _defaults() -> dict:
    out = {t: {"enabled": m["default"], "params": dict(m["params"])} for t, m in ALERT_TYPES.items()}
    out["_global"] = {"enabled": True, "params": dict(GLOBAL_DEFAULTS)}
    return out


async def get_prefs(user_id: int) -> dict:
    """{alert_type: {enabled, params}} with defaults filled in, plus '_global'."""
    hit = _cache.get(user_id)
    if hit and time.monotonic() - hit[0] < _CACHE_TTL:
        return hit[1]
    prefs = _defaults()
    sf = get_session_factory()
    async with sf() as s:
        rows = (await s.execute(text(
            "SELECT alert_type, enabled, params FROM telegram_alert_prefs WHERE user_id = :u"),
            {"u": user_id})).all()
    for t, en, params in rows:
        if t not in prefs:
            continue
        p = json.loads(params) if isinstance(params, str) else (params or {})
        prefs[t] = {"enabled": bool(en), "params": {**prefs[t]["params"], **p}}
    _cache[user_id] = (time.monotonic(), prefs)
    return prefs


def _clean_params(alert_type: str, params: dict) -> dict:
    """Keep only known keys, coerced to the default's type — nothing else is stored."""
    base = GLOBAL_DEFAULTS if alert_type == "_global" else ALERT_TYPES[alert_type]["params"]
    out = {}
    for k, v in (params or {}).items():
        if k not in base:
            continue
        d = base[k]
        try:
            if isinstance(d, bool):
                out[k] = bool(v)
            elif isinstance(d, int):
                out[k] = int(v)
            elif isinstance(d, float):
                out[k] = float(v)
            elif isinstance(d, list):
                out[k] = [str(x).strip() for x in (v or []) if str(x).strip()][:50]
            else:
                out[k] = str(v)[:64]
        except (TypeError, ValueError):
            continue
    return out


def validate(alert_type: str, params: dict) -> dict:
    """Range checks on top of type coercion; raises ValueError with a readable message."""
    if alert_type != "_global" and alert_type not in ALERT_TYPES:
        raise ValueError(f"unknown alert type {alert_type}")
    p = _clean_params(alert_type, params)
    if alert_type == "_global":
        for k in ("quiet_start", "quiet_end"):
            if k in p and not 0 <= p[k] <= 23:
                raise ValueError(f"{k} must be an hour 0-23")
        if "hourly_limit" in p and not 1 <= p["hourly_limit"] <= 60:
            raise ValueError("hourly_limit must be 1-60")
        if "tz" in p:
            from zoneinfo import ZoneInfo
            try:
                ZoneInfo(p["tz"])
            except Exception:
                raise ValueError(f"unknown timezone {p['tz']}")
    if alert_type == "liq_warning" and "margin_use_pct" in p and not 50 <= p["margin_use_pct"] <= 99:
        raise ValueError("margin_use_pct must be 50-99")
    if alert_type == "smi_cross":
        if "above" in p and not 50 < p["above"] <= 100:
            raise ValueError("above must be 51-100")
        if "below" in p and not 0 <= p["below"] < 50:
            raise ValueError("below must be 0-49")
    if alert_type == "watched_wallet" and "min_notional_usd" in p and p["min_notional_usd"] < 0:
        raise ValueError("min_notional_usd must be >= 0")
    if alert_type == "daily_summary" and "hour" in p and not 0 <= p["hour"] <= 23:
        raise ValueError("hour must be 0-23")
    return p


async def set_pref(user_id: int, alert_type: str, enabled: bool | None, params: dict | None) -> dict:
    clean = validate(alert_type, params or {})
    cur = (await get_prefs(user_id))[alert_type]
    new_enabled = cur["enabled"] if enabled is None else bool(enabled)
    new_params = {**cur["params"], **clean}
    sf = get_session_factory()
    async with sf() as s:
        await s.execute(text(
            "INSERT INTO telegram_alert_prefs (user_id, alert_type, enabled, params, updated_at) "
            "VALUES (:u, :t, :e, :p, UTC_TIMESTAMP()) "
            "ON DUPLICATE KEY UPDATE enabled = VALUES(enabled), params = VALUES(params), "
            "updated_at = VALUES(updated_at)"),
            {"u": user_id, "t": alert_type, "e": int(new_enabled), "p": json.dumps(new_params)})
        await s.commit()
    _cache.pop(user_id, None)
    return {"enabled": new_enabled, "params": new_params}


async def users_with(alert_type: str) -> list[dict]:
    """Every user with an active Telegram link and this alert enabled (defaults
    applied): [{user_id, wallet, chat_id, params, global}]."""
    sf = get_session_factory()
    async with sf() as s:
        rows = (await s.execute(text(
            "SELECT u.id, u.wallet_address, t.chat_id FROM telegram_links t "
            "JOIN users u ON u.id = t.user_id "
            "WHERE t.is_active = 1 AND t.chat_id IS NOT NULL"))).all()
    out = []
    for uid, wallet, chat in rows:
        p = await get_prefs(int(uid))
        if p[alert_type]["enabled"]:
            out.append({"user_id": int(uid), "wallet": wallet.lower(), "chat_id": str(chat),
                        "params": p[alert_type]["params"], "global": p["_global"]["params"]})
    return out
