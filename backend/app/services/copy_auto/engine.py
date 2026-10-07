"""Auto-copy engine (spec 2.3). Runs INSIDE the API process (single uvicorn
worker), fed by the HL tracker's real-time ws events only (D4: real-time tier
leaders; poll-tier events carry absolute sizes and are 15 s+ late — ignored).

Per leader event, per subscription (serialized by a per-subscription lock, so a
leader close waits for our in-flight open to finish — spec 2.12):
  1. claim (subscription, leader event, action) in auto_copy_log — the UNIQUE key
     makes one leader event create at most one order per subscription, across
     duplicate ws frames and restarts;
  2. normalize the event (flips split into close + fresh open);
  3. close / reduce mirror what WE hold (paused subscriptions still close);
  4. open / add gather real inputs (chain account, Perpl mark, basis, leader
     leverage + equity) and run the 11 gates in spec order;
  5. Shadow: log "would have placed" + keep a shadow position; Live: place
     through executor, record the REAL fill (partial fills = real size), attach
     the protective SL, and cancel it on close.
Every decision -> auto_copy_log + Telegram per the user's settings.
"""
import asyncio
import json
import time
from datetime import datetime

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app.config import settings
from app.db.database import get_session_factory
from app.services.copy_auto import executor, rules
from app.utils.logger import get_logger

logger = get_logger(__name__)

_locks: dict[int, asyncio.Lock] = {}
stats = {"events": 0, "decisions": 0, "copied": 0, "shadow": 0, "skipped": 0, "failed": 0}


def live_switch_on() -> bool:
    return bool(settings.COPY_LIVE_ENABLED and getattr(settings, "AUTO_COPY_ENABLED", False))


async def _subs_for(leader: str) -> list[dict]:
    sf = get_session_factory()
    async with sf() as s:
        return [dict(r) for r in (await s.execute(text(
            "SELECT * FROM auto_copy_subs WHERE leader_wallet = :l AND status IN ('active','paused')"),
            {"l": leader.lower()})).mappings().all()]


async def _claim(sub: dict, evt: dict, action_key: str, act: rules.Action) -> int | None:
    sf = get_session_factory()
    async with sf() as s:
        try:
            r = await s.execute(text(
                "INSERT INTO auto_copy_log (sub_id, user_id, follower_wallet, leader_wallet, leader_event_id, "
                "action_key, event_type, coin, market_id, side, mode, decision, created_at) VALUES "
                "(:sid, :u, :f, :l, :e, :k, :et, :c, :m, :sd, :mode, 'pending', UTC_TIMESTAMP())"),
                {"sid": sub["id"], "u": sub["user_id"], "f": sub["follower_wallet"], "l": sub["leader_wallet"],
                 "e": evt["id"], "k": action_key, "et": act.kind, "c": evt["symbol"],
                 "m": evt.get("market_id") or 0, "sd": act.side, "mode": sub["mode"]})
            await s.commit()
            return int(r.lastrowid)
        except IntegrityError:
            return None     # already decided for this subscription + event + action


async def _finish(log_id: int, sub: dict, decision: str, gate: str | None, reason: str,
                  detail: dict, alert_msg: str | None) -> None:
    stats["decisions"] += 1
    logger.info("auto-copy: sub=%s log=%s %s%s — %s", sub["id"], log_id, decision,
                f" [{gate}]" if gate else "", reason[:160])
    stats[decision] = stats.get(decision, 0) + 1
    sf = get_session_factory()
    async with sf() as s:
        await s.execute(text(
            "UPDATE auto_copy_log SET decision=:d, gate=:g, reason=:r, detail=:det WHERE id=:i"),
            {"d": decision, "g": gate, "r": reason[:250], "det": json.dumps(detail, default=str), "i": log_id})
        if decision == "failed":
            await s.execute(text("UPDATE auto_copy_subs SET consecutive_failures = consecutive_failures + 1 "
                                 "WHERE id = :i"), {"i": sub["id"]})
        elif decision == "copied":
            await s.execute(text("UPDATE auto_copy_subs SET consecutive_failures = 0 WHERE id = :i"),
                            {"i": sub["id"]})
        await s.commit()
    if alert_msg:
        kind = {"copied": "copy_trade", "shadow": "copy_trade", "skipped": "copy_skipped",
                "failed": "copy_failed"}.get(decision, "copy_skipped")
        from app.services.alerts import producers
        asyncio.get_event_loop().create_task(
            producers.copy_event(sub["follower_wallet"], kind, alert_msg, f"auto:{log_id}"))


async def _held(sub: dict, coin: str) -> dict | None:
    sf = get_session_factory()
    async with sf() as s:
        r = (await s.execute(text(
            "SELECT * FROM auto_copy_positions WHERE sub_id=:s AND coin=:c AND status='open' AND mode=:m "
            "ORDER BY id DESC LIMIT 1"), {"s": sub["id"], "c": coin, "m": sub["mode"]})).mappings().first()
    return dict(r) if r else None


async def _realized(sub: dict) -> tuple[float, float]:
    sf = get_session_factory()
    async with sf() as s:
        d, t = (await s.execute(text(
            "SELECT COALESCE(SUM(CASE WHEN closed_at >= UTC_DATE() THEN realized_pnl END),0), "
            "COALESCE(SUM(realized_pnl),0) FROM auto_copy_positions WHERE sub_id=:s AND mode=:m"),
            {"s": sub["id"], "m": sub["mode"]})).first()
    return float(d), float(t)


async def _pause(sub: dict, reason: str) -> None:
    sf = get_session_factory()
    async with sf() as s:
        r = await s.execute(text(
            "UPDATE auto_copy_subs SET status='paused', pause_reason=:r, updated_at=UTC_TIMESTAMP() "
            "WHERE id=:i AND status='active'"), {"r": reason[:120], "i": sub["id"]})
        await s.commit()
    if r.rowcount:
        sub["status"] = "paused"
        from app.services.alerts import producers
        await producers.copy_event(
            sub["follower_wallet"], "copy_paused",
            f"<b>Auto-copy paused</b> — {sub['leader_wallet'][:10]}… ({sub['mode']})\nReason: {reason}\n"
            "Open copied positions are kept and still close when the trader closes. Resume with one tap.",
            f"pause:{sub['id']}:{reason[:20]}")


async def _check_pause(sub: dict, **extra) -> None:
    sf = get_session_factory()
    async with sf() as s:
        fails = (await s.execute(text("SELECT consecutive_failures FROM auto_copy_subs WHERE id=:i"),
                                 {"i": sub["id"]})).scalar() or 0
    d, t = await _realized(sub)
    why = rules.pause_reason(consecutive_failures=int(fails), daily_realized=d, total_realized=t,
                             daily_loss_usd=sub["daily_loss_usd"], total_loss_usd=sub["total_loss_usd"], **extra)
    if why:
        await _pause(sub, why)


async def _leader_state(leader: str, coin: str) -> tuple[float | None, float | None]:
    """(leader leverage on this coin, leader account value) from one
    clearinghouseState read (weight 2, CRITICAL priority: this gates money)."""
    from app.services.hyperliquid import client as hl_client
    body = {"type": "clearinghouseState", "user": leader}
    if ":" in coin:
        body["dex"] = coin.split(":", 1)[0]
    try:
        r = await hl_client.post_info(body, priority=hl_client.CRITICAL, timeout=4.0)
        r.raise_for_status()
        st = r.json()
    except Exception:
        return None, None
    av = float((st.get("marginSummary") or {}).get("accountValue") or 0) or None
    for ap in st.get("assetPositions", []):
        p = ap.get("position") or {}
        if str(p.get("coin")).upper() == coin.upper():
            return float((p.get("leverage") or {}).get("value") or 0) or None, av
    return None, av


def _mark(market_id: int) -> float | None:
    from app.services import ws_manager as wsm
    return wsm.ws_manager.last_mark.get(market_id) if wsm.ws_manager else None


async def on_leader_event(evt: dict) -> None:
    """Entry point (HL tracker, real-time ws events only)."""
    try:
        if evt.get("source") != "hl_ws":
            return
        subs = await _subs_for(evt["trader_wallet"])
        if not subs:
            return
        stats["events"] += 1
        fill = (evt.get("raw") or {}).get("fill") or {}
        acts = rules.normalize(evt["event_type"], evt["side"], fill)
        for sub in subs:
            lock = _locks.setdefault(sub["id"], asyncio.Lock())
            async with lock:
                for i, act in enumerate(acts):
                    try:
                        await _process(sub, evt, fill, act, f"{act.kind}:{i}")
                    except Exception:
                        logger.exception("auto-copy: processing failed sub=%s event=%s", sub["id"], evt.get("id"))
                if rules.leader_was_liquidated(fill, evt["trader_wallet"]):
                    await _check_pause(sub, leader_liquidated=True)
    except Exception:
        logger.exception("auto-copy: on_leader_event failed")


async def _process(sub: dict, evt: dict, fill: dict, act: rules.Action, action_key: str) -> None:
    log_id = await _claim(sub, evt, action_key, act)
    if log_id is None:
        return
    coin, mid = evt["symbol"], int(evt.get("market_id") or 0)
    live = sub["mode"] == "live"
    held = await _held(sub, coin)
    tag = "" if live else "[shadow] "
    if act.kind in ("close", "reduce", "add") and not held:
        await _finish(log_id, sub, "skipped", "not_copied",
                      "no copied position (opened before you subscribed, or detached)", {"coin": coin}, None)
        return
    if act.kind in ("close", "reduce"):
        await _mirror_exit(log_id, sub, act, held, mid, coin, live, tag)
        return
    await _mirror_entry(log_id, sub, evt, fill, act, held, mid, coin, live, tag)


async def _mirror_exit(log_id, sub, act, held, mid, coin, live, tag) -> None:
    if act.kind == "reduce" and not sub["mirror_reduces"]:
        await _finish(log_id, sub, "skipped", "mirror_reduces_off", "reduce mirroring is off", {}, None)
        return
    mp = await executor.market_params(mid) if mid else None
    sd = mp["size_decimals"] if mp else 6
    our = float(held["size"])
    qty = our if act.kind == "close" else rules.scaled_change(our, act.leader_size, act.leader_prev, sd)
    if qty <= 0:
        await _finish(log_id, sub, "skipped", "reduce_rounds_to_zero", "leader's cut is smaller than one lot of ours", {}, None)
        return
    full = qty >= our - 10 ** -sd / 2
    mark = _mark(mid) or act.px
    detail = {"our_size": our, "qty": qty, "full": full, "mark": mark, "leader_px": act.px, "note": act.note}
    px = mark
    if live:
        try:
            r = await executor.close_market(sub["user_id"], sub["follower_wallet"], market_id=mid, side=held["side"],
                                            size=our if full else qty, mark=mark, mp=mp)
            qty = float(r.get("fs") or 0) / 10 ** sd or qty
            px = float(r.get("fp") or 0) / 10 ** mp["price_decimals"] or mark
            detail["order"] = r
        except Exception as exc:
            await _finish(log_id, sub, "failed", None, f"close failed: {exc}", detail,
                          f"<b>Auto-copy close FAILED</b> — {coin} {held['side']}\n{str(exc)[:160]}\nCheck the position on Perpl.")
            await _check_pause(sub, key_rejected=isinstance(exc, executor.KeyRejected))
            return
        if full and held.get("sl_oid"):
            try:
                await executor.cancel(sub["user_id"], sub["follower_wallet"], market_id=mid, oid=held["sl_oid"])
                detail["sl_cancelled"] = held["sl_oid"]
            except Exception as exc:
                detail["sl_cancel_error"] = str(exc)[:160]
    sign = 1 if held["side"] == "long" else -1
    pnl = round((px - float(held["entry_px"])) * min(qty, our) * sign, 6)
    left = max(0.0, our - qty)
    sf = get_session_factory()
    async with sf() as s:
        if full or left <= 0:
            await s.execute(text("UPDATE auto_copy_positions SET status='closed', size=0, realized_pnl=realized_pnl+:p, "
                                 "closed_at=UTC_TIMESTAMP(), close_reason=:r WHERE id=:i"),
                            {"p": pnl, "r": "leader " + ("flipped" if act.note == "flip" else "closed"), "i": held["id"]})
        else:
            await s.execute(text("UPDATE auto_copy_positions SET size=:z, realized_pnl=realized_pnl+:p WHERE id=:i"),
                            {"z": left, "p": pnl, "i": held["id"]})
        await s.commit()
    detail["realized_pnl"] = pnl
    await _finish(log_id, sub, "copied" if live else "shadow", None,
                  f"{'closed' if full else 'reduced'} {qty:g} @ {px:g}", detail,
                  f"<b>{tag}Auto-copy {'closed' if full else 'reduced'}</b> — {coin} {held['side']}\n"
                  f"{qty:g} @ <code>{px:g}</code> · P/L <code>${pnl:+,.2f}</code>")
    await _check_pause(sub)


async def _mirror_entry(log_id, sub, evt, fill, act, held, mid, coin, live, tag) -> None:
    from app.services.copy_auto import user_settings
    if not await user_settings.enabled(sub["user_id"]):
        await _finish(log_id, sub, "skipped", "user_auto_copy_off",
                      "skipped: user auto-copy off (closes still mirrored)", {}, None)
        return
    if sub["status"] != "active":
        await _finish(log_id, sub, "skipped", "paused", f"subscription paused ({sub.get('pause_reason')}); closes only", {}, None)
        return
    if act.kind == "add" and not sub["mirror_adds"]:
        await _finish(log_id, sub, "skipped", "mirror_adds_off", "add mirroring is off", {}, None)
        return
    if act.kind == "open" and act.note == "flip" and not sub["reopen_on_flip"]:
        await _finish(log_id, sub, "skipped", "reopen_on_flip_off", "re-open on flip is off", {}, None)
        return
    mp = await executor.market_params(mid) if mid else None
    from app.services import chain_reader
    from app.services.copy import live_orders
    from app.services.hyperliquid import prices as hl_prices
    acct = await asyncio.to_thread(chain_reader.get_trader_positions_only, sub["follower_wallet"])
    positions = (acct or {}).get("positions") or []
    free = float(acct["balance"]) if acct and acct.get("balance") is not None else None
    sf = get_session_factory()
    async with sf() as s:
        others = (await s.execute(text(
            "SELECT COUNT(*) FROM auto_copy_positions WHERE follower_wallet=:f AND market_id=:m AND status='open' "
            "AND mode='live' AND sub_id<>:s"), {"f": sub["follower_wallet"], "m": mid, "s": sub["id"]})).scalar()
        opens, in_use_margin = (await s.execute(text(
            "SELECT COUNT(*), COALESCE(SUM(margin_usd),0) FROM auto_copy_positions WHERE sub_id=:s AND status='open' "
            "AND mode=:mo"), {"s": sub["id"], "mo": sub["mode"]})).first()
    on_chain = any(int(p["market_id"]) == mid for p in positions)
    # slot rule: one source per market per account. For an ADD the market is ours already.
    in_use = bool(others) or (act.kind == "open" and (on_chain or bool(held)))
    mark = _mark(mid) if mid else None
    basis = await hl_prices.basis_bps_for_market(mid) if mid else None
    market_max = await live_orders.market_max_leverage(mid) if mid else 1.0
    if act.kind == "open":
        leader_lev, leader_av = await _leader_state(sub["leader_wallet"], coin)
        lev, lev_note = rules.choose_leverage(leader_lev, float(sub["max_leverage"]), market_max)
        size, margin, why = (rules.size_open(
            sizing=sub["sizing"], margin_usd=float(sub["margin_usd"]), allocation_usd=float(sub["allocation_usd"]),
            margin_in_use=float(in_use_margin), leverage=lev, price=mark or act.px, leader_size=act.leader_size,
            leader_account_value=leader_av, size_decimals=mp["size_decimals"], min_scaled=mp["min_scaled"])
            if mp else (0.0, 0.0, "market not listed on Perpl"))
    else:
        lev, lev_note, leader_lev = float(held["leverage"]), "", None
        size = rules.scaled_change(float(held["size"]), act.leader_size, act.leader_prev, mp["size_decimals"]) if mp else 0.0
        margin = round(size * (mark or act.px) / lev, 6) if size else 0.0
        why = None if size > 0 else "add rounds to zero"
        if not why and float(in_use_margin) + margin > float(sub["allocation_usd"]) + 1e-9:
            why = "allocation fully used"
    age = time.time() - (float(fill.get("time") or 0) / 1000.0 or time.time())
    d, t = await _realized(sub)
    g = rules.GateInput(
        side=act.side, kill_switch_on=live_switch_on(), allowlisted=settings.live_allowlist_ok(sub["follower_wallet"]),
        live=live, market_id=mid, markets_allowed=json.loads(sub["markets"]) if sub.get("markets") else None,
        market_in_use=in_use, leader_px=act.px, perpl_px=mark, drift_pct=float(sub["drift_pct"]),
        basis_bps=basis, open_positions=int(opens) if act.kind == "open" else 0,
        max_positions=int(sub["max_positions"]), daily_realized=d, total_realized=t,
        daily_loss_usd=sub["daily_loss_usd"], total_loss_usd=sub["total_loss_usd"],
        free_margin=free, margin_needed=margin, leverage=lev, market_max_leverage=market_max, event_age_sec=age)
    gate, vals = rules.run_gates(g)
    detail = {"gates": vals, "size": size, "margin": margin, "leverage": lev, "lev_note": lev_note,
              "leader_leverage": leader_lev, "leader_fill": {k: fill.get(k) for k in ("px", "sz", "dir", "startPosition", "time", "tid")}}
    if why and not gate:
        gate = "sizing"
    if gate:
        reason = why if gate == "sizing" else gate
        await _finish(log_id, sub, "skipped", gate, reason, detail,
                      f"<b>{tag}Auto-copy skipped</b> — {coin} {act.side} ({act.kind})\nGate: <code>{reason}</code>")
        if gate in ("g8_daily_loss", "g8_total_loss"):
            await _check_pause(sub)
        return
    sl_px = rules.stop_price(act.side, mark, lev, sub["sl_margin_pct"]) if act.kind == "open" else None
    detail["sl_price"] = sl_px
    if not live:
        await _record_entry(sub, evt, act, held, mid, coin, size, mark, lev, margin, None, sl_px, "shadow")
        await _finish(log_id, sub, "shadow", None, f"would have placed {act.kind} {size:g} @ ~{mark:g} {lev:g}x", detail,
                      f"<b>[shadow] Auto-copy would {act.kind}</b> — {coin} {act.side}\n"
                      f"{size:g} @ ~<code>{mark:g}</code> · {lev:g}x · margin ${margin:,.2f}"
                      + (f" · SL <code>{sl_px:.6g}</code>" if sl_px else ""))
        return
    try:
        r = await executor.open_market(sub["user_id"], sub["follower_wallet"], market_id=mid, side=act.side,
                                       size=size, leverage=lev, mark=mark, mp=mp)
    except Exception as exc:
        await _finish(log_id, sub, "failed", None, f"order failed: {exc}", detail,
                      f"<b>Auto-copy order FAILED</b> — {coin} {act.side} ({act.kind})\n{str(exc)[:160]}")
        await _check_pause(sub, key_rejected=isinstance(exc, executor.KeyRejected))
        return
    fs = float(r.get("fs") or 0) / 10 ** mp["size_decimals"]
    fp = float(r.get("fp") or 0) / 10 ** mp["price_decimals"] or mark
    detail["order"] = r
    sl_oid = None
    if act.kind == "open" and sl_px and fs > 0:
        try:
            sl = await executor.stop_loss(sub["user_id"], sub["follower_wallet"], market_id=mid, side=act.side,
                                          size=fs, trigger=rules.stop_price(act.side, fp, lev, sub["sl_margin_pct"]), mp=mp)
            sl_oid = sl.get("oid")
        except Exception as exc:
            detail["sl_error"] = str(exc)[:160]
    await _record_entry(sub, evt, act, held, mid, coin, fs, fp, lev, round(fs * fp / lev, 6), sl_oid, sl_px, "live")
    await _finish(log_id, sub, "copied", None, f"{act.kind} filled {fs:g} @ {fp:g}", detail,
                  f"<b>Auto-copy {'opened' if act.kind == 'open' else 'added'}</b> — {coin} {act.side}\n"
                  f"Filled <code>{fs:g}</code> @ <code>{fp:g}</code> · {lev:g}x"
                  + (" · SL armed" if sl_oid else (" · <b>SL NOT armed</b>: " + detail.get("sl_error", "") if act.kind == "open" and sl_px else "")))


async def _record_entry(sub, evt, act, held, mid, coin, size, px, lev, margin, sl_oid, sl_px, mode) -> None:
    sf = get_session_factory()
    async with sf() as s:
        if held and act.kind == "add":
            old = float(held["size"])
            new = old + size
            entry = (float(held["entry_px"]) * old + px * size) / new if new else px
            await s.execute(text("UPDATE auto_copy_positions SET size=:z, entry_px=:e, margin_usd=margin_usd+:m "
                                 "WHERE id=:i"), {"z": new, "e": entry, "m": margin, "i": held["id"]})
        else:
            await s.execute(text(
                "INSERT INTO auto_copy_positions (sub_id, user_id, follower_wallet, leader_wallet, coin, market_id, side, "
                "size, entry_px, margin_usd, leverage, sl_oid, sl_px, mode, status, realized_pnl, opened_at, open_event_id) "
                "VALUES (:s, :u, :f, :l, :c, :m, :sd, :z, :e, :mg, :lv, :so, :sp, :mo, 'open', 0, UTC_TIMESTAMP(), :ev)"),
                {"s": sub["id"], "u": sub["user_id"], "f": sub["follower_wallet"], "l": sub["leader_wallet"],
                 "c": coin, "m": mid, "sd": act.side, "z": size, "e": px, "mg": margin, "lv": lev,
                 "so": str(sl_oid) if sl_oid is not None else None, "sp": sl_px, "mo": mode, "ev": evt["id"]})
        await s.commit()


# --- venue-truth reconciliation (spec 2.3 / 2.12) ---------------------------

RECONCILE_SEC = 300
_rec_task: asyncio.Task | None = None


async def reconcile_once() -> dict:
    """Live copied positions vs the chain. Gone on-chain -> detached (the user
    closed it themselves, or it was liquidated/stopped); smaller -> resized to
    the chain size. A failed chain read never changes anything."""
    from app.services import chain_reader
    sf = get_session_factory()
    async with sf() as s:
        rows = [dict(r) for r in (await s.execute(text(
            "SELECT id, follower_wallet, market_id, side, size FROM auto_copy_positions "
            "WHERE status='open' AND mode='live'"))).mappings().all()]
    out = {"checked": len(rows), "detached": 0, "resized": 0}
    by_wallet: dict[str, list] = {}
    for r in rows:
        by_wallet.setdefault(r["follower_wallet"], []).append(r)
    for w, ps in by_wallet.items():
        acct = await asyncio.to_thread(chain_reader.get_trader_positions_only, w)
        if acct is None:
            continue
        chain = {(int(p["market_id"]), p["side"]): float(p["size"]) for p in acct.get("positions") or []}
        async with sf() as s:
            for p in ps:
                cz = chain.get((int(p["market_id"]), p["side"]))
                if cz is None:
                    await s.execute(text("UPDATE auto_copy_positions SET status='detached', closed_at=UTC_TIMESTAMP(), "
                                         "close_reason='not on chain (closed outside auto-copy)' WHERE id=:i"), {"i": p["id"]})
                    out["detached"] += 1
                elif cz < float(p["size"]) * 0.999:
                    await s.execute(text("UPDATE auto_copy_positions SET size=:z WHERE id=:i"), {"z": cz, "i": p["id"]})
                    out["resized"] += 1
            await s.commit()
    return out


async def _reconcile_loop(stop: asyncio.Event) -> None:
    await asyncio.sleep(60)
    while not stop.is_set():
        try:
            r = await reconcile_once()
            if r["detached"] or r["resized"]:
                logger.info("auto-copy reconcile: %s", r)
        except Exception:
            logger.exception("auto-copy reconcile failed")
        try:
            await asyncio.wait_for(stop.wait(), timeout=RECONCILE_SEC)
        except asyncio.TimeoutError:
            pass


def start(stop: asyncio.Event) -> None:
    global _rec_task
    if _rec_task is None or _rec_task.done():
        _rec_task = asyncio.get_event_loop().create_task(_reconcile_loop(stop))
        logger.info("auto-copy engine ready (live switch %s)", "ON" if live_switch_on() else "OFF")
