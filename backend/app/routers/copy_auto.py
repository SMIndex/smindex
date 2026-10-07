"""Auto-copy API (overnight Part C, spec 2.9-2.11). Owner = JWT user, always.
Prefix /copy-auto — /autocopy belongs to the dead v0 router."""
import json
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import text

from app.config import settings
from app.db.database import get_session_factory
from app.db.models import User
from app.routers.auth import get_authenticated_user
from app.services.copy_auto import keys, rules

router = APIRouter(prefix="/copy-auto", tags=["copy-auto"])

EDITABLE = ["sizing", "margin_usd", "allocation_usd", "max_leverage", "max_positions", "mirror_adds",
            "mirror_reduces", "reopen_on_flip", "drift_pct", "sl_margin_pct", "tp_pct", "daily_loss_usd",
            "total_loss_usd", "markets"]


def _allowlisted(user: User) -> bool:
    return settings.live_allowlist_ok(user.wallet_address.lower())


def _clean(fields: dict) -> dict:
    out = {}
    for k in EDITABLE:
        if k not in fields:
            continue
        v = fields[k]
        if k == "sizing":
            if v not in ("fixed", "proportional"):
                raise HTTPException(400, "sizing must be fixed or proportional")
        elif k in ("mirror_adds", "mirror_reduces", "reopen_on_flip"):
            v = bool(v)
        elif k == "markets":
            v = None if not v else json.dumps(sorted({int(x) for x in v}))
        elif v is None and k in ("sl_margin_pct", "tp_pct", "daily_loss_usd", "total_loss_usd"):
            pass
        else:
            try:
                v = int(v) if k == "max_positions" else float(v)
            except (TypeError, ValueError):
                raise HTTPException(400, f"{k} must be a number")
            lo, hi = rules.RANGES.get(k, (None, None))
            if lo is not None and not lo <= v <= hi:
                raise HTTPException(400, f"{k} must be between {lo:g} and {hi:g}")
        out[k] = v
    return out


def _row(r) -> dict:
    d = dict(r)
    d["markets"] = json.loads(d["markets"]) if d.get("markets") else None
    for k in ("created_at", "updated_at"):
        if d.get(k):
            d[k] = d[k].isoformat()
    d["summary"] = rules.summary_sentence(d)
    return d


# --- key ------------------------------------------------------------------

class KeyDeposit(BaseModel):
    api_key: str
    seed_hex: str
    pub_hex: str
    scope: int = 2
    label: str = "SMINDEX auto-copy"
    consent: bool = False


@router.get("/key")
async def key_status(user: User = Depends(get_authenticated_user)) -> dict:
    return await keys.status(user.id)


@router.post("/key")
async def key_deposit(req: KeyDeposit, user: User = Depends(get_authenticated_user)) -> dict:
    if not req.consent:
        raise HTTPException(400, "consent required")
    if not _allowlisted(user):
        raise HTTPException(403, "Auto-copy is allowlist-only for now")
    try:
        return await keys.store(user.id, user.wallet_address, req.api_key, req.seed_hex, req.pub_hex,
                                req.scope, req.label)
    except ValueError as exc:
        raise HTTPException(400, str(exc))


@router.delete("/key")
async def key_delete(user: User = Depends(get_authenticated_user)) -> dict:
    return await keys.delete(user.id)


# --- availability / presets ----------------------------------------------

@router.get("/availability/{leader}")
async def availability(leader: str, user: User = Depends(get_authenticated_user)) -> dict:
    from app.services.hyperliquid import tracker
    from app.services.copy_auto import engine
    ok, why = tracker.ws_slot_available(leader)
    allow = _allowlisted(user)
    reason = why if ok else why
    if not allow:
        ok, reason = False, "auto-copy is allowlist-only for now"
    if leader.lower() == user.wallet_address.lower():
        ok, reason = False, "you cannot auto-copy yourself"
    return {"available": ok, "reason": reason, "allowlisted": allow,
            "key": await keys.status(user.id), "live_switch_on": engine.live_switch_on()}


@router.get("/presets")
async def presets() -> dict:
    return {"presets": rules.PRESETS, "defaults": rules.DEFAULTS, "ranges": rules.RANGES,
            "summaries": {k: rules.summary_sentence({**rules.DEFAULTS, **v}) for k, v in rules.PRESETS.items()}}


# --- subscriptions --------------------------------------------------------

class SubCreate(BaseModel):
    leader_wallet: str
    preset: Optional[str] = None     # None = start from the user's saved defaults
    settings: dict = {}
    mode: Optional[str] = None       # None = the user's default mode


# --- user settings (master toggle + defaults for new subscriptions) -------

class UserSettingsUpdate(BaseModel):
    enabled: Optional[bool] = None
    defaults: Optional[dict] = None


async def _settings_payload(user: User) -> dict:
    from app.services.copy_auto import engine, user_settings
    st = await user_settings.get(user.id)
    live_on = engine.live_switch_on()
    return {**st, "platform_live_on": live_on,
            "platform_paused": bool(st["enabled"] and not live_on),
            "allowlisted": _allowlisted(user), "key": await keys.status(user.id),
            "presets": rules.PRESETS, "ranges": rules.RANGES}


@router.get("/settings")
async def get_settings(user: User = Depends(get_authenticated_user)) -> dict:
    return await _settings_payload(user)


@router.put("/settings")
async def put_settings(req: UserSettingsUpdate, user: User = Depends(get_authenticated_user)) -> dict:
    from app.services.copy_auto import user_settings
    dfl = None
    if req.defaults is not None:
        dfl = {k: v for k, v in _clean(req.defaults).items() if k in user_settings.DEFAULT_KEYS}
        if "mode" in req.defaults:
            if req.defaults["mode"] not in ("shadow", "live"):
                raise HTTPException(400, "mode must be shadow or live")
            dfl["mode"] = req.defaults["mode"]
    await user_settings.save(user.id, enabled=req.enabled, defaults=dfl)
    return await _settings_payload(user)


class SubUpdate(BaseModel):
    settings: dict = {}
    mode: Optional[str] = None       # shadow | live
    status: Optional[str] = None     # active (resume) | paused


@router.get("/subs")
async def list_subs(user: User = Depends(get_authenticated_user)) -> dict:
    sf = get_session_factory()
    async with sf() as s:
        rows = (await s.execute(text(
            "SELECT * FROM auto_copy_subs WHERE user_id=:u AND status<>'stopped' ORDER BY id"), {"u": user.id})).mappings().all()
        stats = {r[0]: r for r in (await s.execute(text(
            "SELECT sub_id, COALESCE(SUM(CASE WHEN closed_at >= UTC_DATE() THEN realized_pnl END),0), "
            "COALESCE(SUM(realized_pnl),0), SUM(status='open') FROM auto_copy_positions WHERE user_id=:u "
            "GROUP BY sub_id"), {"u": user.id})).all()}
        last = {r[0]: r for r in (await s.execute(text(
            "SELECT l.sub_id, l.decision, l.event_type, l.coin, l.reason, l.created_at FROM auto_copy_log l JOIN ("
            " SELECT sub_id, MAX(id) mi FROM auto_copy_log WHERE user_id=:u GROUP BY sub_id) x ON x.mi = l.id"),
            {"u": user.id})).all()}
    out = []
    for r in rows:
        d = _row(r)
        st = stats.get(d["id"])
        d["pnl_today"] = float(st[1]) if st else 0.0
        d["pnl_total"] = float(st[2]) if st else 0.0
        d["open_positions"] = int(st[3] or 0) if st else 0
        lr = last.get(d["id"])
        d["last_action"] = ({"decision": lr[1], "event": lr[2], "coin": lr[3], "reason": lr[4],
                             "at": lr[5].isoformat()} if lr else None)
        out.append(d)
    return {"subs": out}


@router.post("/subs")
async def create_sub(req: SubCreate, user: User = Depends(get_authenticated_user)) -> dict:
    from app.services.hyperliquid import tracker
    if not _allowlisted(user):
        raise HTTPException(403, "Auto-copy is allowlist-only for now")
    leader = req.leader_wallet.lower()
    if leader == user.wallet_address.lower():
        raise HTTPException(400, "You cannot auto-copy yourself")
    ok, why = tracker.ws_slot_available(leader)
    if not ok:
        raise HTTPException(409, why)
    from app.services.copy_auto import user_settings
    ud = (await user_settings.get(user.id))["defaults"]
    vals = {**rules.DEFAULTS, **ud}
    if req.preset in rules.PRESETS:
        vals.update(rules.PRESETS[req.preset])
    vals.update({k: v for k, v in _clean(req.settings).items()})
    if isinstance(vals.get("markets"), list):
        vals["markets"] = json.dumps(vals["markets"])
    mode = req.mode or ud.get("mode") or "shadow"
    if mode not in ("shadow", "live"):
        raise HTTPException(400, "mode must be shadow or live")
    if mode == "live" and not (await keys.status(user.id)).get("present"):
        mode = "shadow"      # no key yet: start in Shadow, the card offers Live once a key exists
    cols = ["sizing", "margin_usd", "allocation_usd", "max_leverage", "max_positions", "mirror_adds",
            "mirror_reduces", "reopen_on_flip", "drift_pct", "sl_margin_pct", "tp_pct", "daily_loss_usd",
            "total_loss_usd", "markets"]
    sf = get_session_factory()
    async with sf() as s:
        exists = (await s.execute(text(
            "SELECT id, status, mode FROM auto_copy_subs WHERE follower_wallet=:f AND leader_wallet=:l"),
            {"f": user.wallet_address.lower(), "l": leader})).first()
        params = {"u": user.id, "f": user.wallet_address.lower(), "l": leader, "mode": mode,
                  **{c: vals.get(c) for c in cols}}
        if exists and mode != exists[2] and await _open_count(exists[0], exists[2]):
            raise HTTPException(409, "close this trader's open copied positions before changing mode")
        if exists:
            await s.execute(text(
                "UPDATE auto_copy_subs SET mode=:mode, status='active', pause_reason=NULL, consecutive_failures=0, "
                + ", ".join(f"{c}=:{c}" for c in cols) + ", updated_at=UTC_TIMESTAMP() WHERE id=:id"),
                {**params, "id": exists[0]})
            sid = exists[0]
        else:
            r = await s.execute(text(
                "INSERT INTO auto_copy_subs (user_id, follower_wallet, leader_wallet, mode, status, "
                + ", ".join(cols) + ", created_at, updated_at) VALUES (:u, :f, :l, :mode, 'active', "
                + ", ".join(f":{c}" for c in cols) + ", UTC_TIMESTAMP(), UTC_TIMESTAMP())"), params)
            sid = r.lastrowid
        await s.commit()
    await tracker._load_auto_copy_leaders()     # takes a real-time slot on the next refresh (<= 60 s)
    return await _get(user, sid)


async def _get(user: User, sid: int) -> dict:
    sf = get_session_factory()
    async with sf() as s:
        r = (await s.execute(text("SELECT * FROM auto_copy_subs WHERE id=:i AND user_id=:u"),
                             {"i": sid, "u": user.id})).mappings().first()
    if not r:
        raise HTTPException(404, "not found")
    return _row(r)


@router.patch("/subs/{sid}")
async def update_sub(sid: int, req: SubUpdate, user: User = Depends(get_authenticated_user)) -> dict:
    cur = await _get(user, sid)
    sets, params = [], {"i": sid, "u": user.id}
    for k, v in _clean(req.settings).items():
        sets.append(f"{k}=:{k}")
        params[k] = v
    if req.mode is not None:
        if req.mode not in ("shadow", "live"):
            raise HTTPException(400, "mode must be shadow or live")
        if req.mode == "live" and not (await keys.status(user.id)).get("present"):
            raise HTTPException(400, "Deposit your auto-copy Perpl key before switching to Live")
        if req.mode != cur["mode"]:
            open_n = cur.get("id") and await _open_count(sid, cur["mode"])
            if open_n:
                raise HTTPException(409, f"close the {open_n} open {cur['mode']} position(s) first "
                                         "(they belong to the current mode)")
        sets.append("mode=:mode")
        params["mode"] = req.mode
    if req.status is not None:
        if req.status not in ("active", "paused"):
            raise HTTPException(400, "status must be active or paused")
        sets.append("status=:st")
        params["st"] = req.status
        sets.append("pause_reason=:pr")
        params["pr"] = None if req.status == "active" else "paused by you"
        if req.status == "active":
            sets.append("consecutive_failures=0")
    if sets:
        sf = get_session_factory()
        async with sf() as s:
            await s.execute(text(f"UPDATE auto_copy_subs SET {', '.join(sets)}, updated_at=UTC_TIMESTAMP() "
                                 "WHERE id=:i AND user_id=:u"), params)
            await s.commit()
    return await _get(user, sid)


async def _open_count(sid: int, mode: str) -> int:
    sf = get_session_factory()
    async with sf() as s:
        return int((await s.execute(text(
            "SELECT COUNT(*) FROM auto_copy_positions WHERE sub_id=:s AND mode=:m AND status='open'"),
            {"s": sid, "m": mode})).scalar() or 0)


@router.delete("/subs/{sid}")
async def stop_sub(sid: int, user: User = Depends(get_authenticated_user)) -> dict:
    await _get(user, sid)
    sf = get_session_factory()
    async with sf() as s:
        await s.execute(text("UPDATE auto_copy_subs SET status='stopped', updated_at=UTC_TIMESTAMP() "
                             "WHERE id=:i AND user_id=:u"), {"i": sid, "u": user.id})
        await s.commit()
    from app.services.hyperliquid import tracker
    await tracker._load_auto_copy_leaders()
    return {"stopped": True}


@router.post("/pause-all")
async def pause_all(user: User = Depends(get_authenticated_user)) -> dict:
    sf = get_session_factory()
    async with sf() as s:
        r = await s.execute(text("UPDATE auto_copy_subs SET status='paused', pause_reason='paused by you (pause all)', "
                                 "updated_at=UTC_TIMESTAMP() WHERE user_id=:u AND status='active'"), {"u": user.id})
        await s.commit()
    return {"paused": r.rowcount}


@router.get("/log")
async def decision_log(limit: int = 100, user: User = Depends(get_authenticated_user)) -> dict:
    sf = get_session_factory()
    async with sf() as s:
        rows = (await s.execute(text(
            "SELECT id, sub_id, leader_wallet, leader_event_id, action_key, event_type, coin, market_id, side, mode, "
            "decision, gate, reason, created_at FROM auto_copy_log WHERE user_id=:u ORDER BY id DESC LIMIT :l"),
            {"u": user.id, "l": max(1, min(limit, 500))})).mappings().all()
    return {"rows": [{**dict(r), "created_at": r["created_at"].isoformat()} for r in rows]}


@router.get("/positions")
async def positions(user: User = Depends(get_authenticated_user)) -> dict:
    sf = get_session_factory()
    async with sf() as s:
        rows = (await s.execute(text(
            "SELECT * FROM auto_copy_positions WHERE user_id=:u AND (status='open' OR closed_at >= UTC_TIMESTAMP() - "
            "INTERVAL 7 DAY) ORDER BY id DESC"), {"u": user.id})).mappings().all()
    out = []
    for r in rows:
        d = dict(r)
        for k in ("opened_at", "closed_at"):
            d[k] = d[k].isoformat() if d.get(k) else None
        out.append(d)
    return {"positions": out}
