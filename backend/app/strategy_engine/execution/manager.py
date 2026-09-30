"""Paper trade lifecycle manager (Phase 2). DB is the source of truth so trades
survive worker restarts; the price tape is strat_book_5s.mid (5-second resolution).

Reuses the tested pure functions in paper.py (fill-through, stop-trigger, fees).
A strat_trades row moves through:
  pending  : fill_ts IS NULL,     exit_ts IS NULL   (post-only entry resting)
  open     : fill_ts IS NOT NULL, exit_ts IS NULL   (filled, managing stop/target/time)
  closed   : exit_ts IS NOT NULL                    (stop | target | time_stop | entry_unfilled)

On every close of a FILLED trade the RiskEngine rules are evaluated and ENFORCED
(doc 00 §5, D-100 2026-09-11): daily loss cap, consecutive-loss pause and the
rolling-20 PF kill switch are PERSISTED to strat_risk_state /
strat_strategy_state and block the next paper entry exactly as they would block
a live one. They used to be annotate-only, which is why s05c kept opening
positions through a 26-trade losing run. Nothing here touches a venue.
"""
from __future__ import annotations

import json
import logging
from typing import Optional

from sqlalchemy import text

from .paper import (
    TAKER_FEE, fee_for, limit_fills_through, stop_triggered, would_cross, requote_inside,
)
from ..risk import engine as risk

logger = logging.getLogger("strategy_engine.execution.manager")

_HOUR_MS = 3_600_000
DEFAULT_HOLD_H = 3          # fallback time stop if a strategy omits time_stop_h
MAX_REQUOTES = 3            # paper executor rule (doc 00 §6): 1 tick inside, max 3


def _entry_side(direction: str) -> str:
    return "buy" if direction == "long" else "sell"


def _tick(px: float) -> float:
    return px * 0.0001 or 0.1


class OrderSanityError(ValueError):
    """A trade whose geometry is impossible: T1 not beyond the entry in the trade
    direction, or the stop on the wrong side of the entry (D-103)."""


def check_order_geometry(direction: str, entry_px: float, stop_px: Optional[float],
                         target_px: Optional[float]) -> Optional[str]:
    """Return a defect string if the order geometry is wrong, else None.

    D-103: live paper trade #10 (s05c ETH long) had entry 2,541.25 and T1
    2,541.20 — a target FIVE CENTS BELOW its own long entry — so it registered a
    'target' hit instantly for a loss. Any order that cannot make money if it
    works is a defect, not a trade; it is logged and skipped.
    Pure and unit-tested (test_order_sanity.py)."""
    if not entry_px or entry_px <= 0:
        return f"entry price is {entry_px!r}"
    long = direction == "long"
    if target_px is not None:
        if long and target_px <= entry_px:
            return (f"T1 {target_px:.6g} is not above the long entry {entry_px:.6g} "
                    f"({target_px - entry_px:+.6g})")
        if not long and target_px >= entry_px:
            return (f"T1 {target_px:.6g} is not below the short entry {entry_px:.6g} "
                    f"({target_px - entry_px:+.6g})")
    if stop_px is not None:
        if long and stop_px >= entry_px:
            return (f"stop {stop_px:.6g} is not below the long entry {entry_px:.6g} "
                    f"({stop_px - entry_px:+.6g})")
        if not long and stop_px <= entry_px:
            return (f"stop {stop_px:.6g} is not above the short entry {entry_px:.6g} "
                    f"({stop_px - entry_px:+.6g})")
    return None


def compute_close(direction: str, entry_px: float, exit_px: float, size: float, reason: str,
                  entry_taker: bool = False) -> tuple[float, float, float]:
    """Pure PnL + fee math for a paper close (doc 00 §7). Entry is maker unless
    `entry_taker` (a chase entry that had to cross — s05c); the exit is taker
    for stop/time_stop, maker for target. Returns (pnl_gross, fees, pnl_net).
    Unit-tested in test_manager.py."""
    sign = 1.0 if direction == "long" else -1.0
    pnl_gross = (exit_px - entry_px) * size * sign
    entry_fee = abs(entry_px * size) * TAKER_FEE if entry_taker else fee_for("entry", entry_px * size)
    exit_fee = fee_for("stop" if reason in ("stop", "time_stop") else "target", exit_px * size)
    fees = entry_fee + exit_fee
    return pnl_gross, fees, pnl_gross - fees


class PaperTradeManager:
    """Drives pending/open strat_trades against the book_5s mid tape. All methods
    take an AsyncSession and operate within the evaluator's transaction."""

    def __init__(self, assets: list[str]) -> None:
        self._assets = assets

    # ---- price tape -------------------------------------------------------
    async def _mids(self, s, coin: str, after_ms: int, until_ms: int) -> list[tuple[int, float]]:
        rows = (await s.execute(text(
            "SELECT ts, mid FROM strat_book_5s WHERE coin=:c AND ts>:a AND ts<=:b ORDER BY ts"),
            {"c": coin, "a": after_ms, "b": until_ms})).all()
        return [(int(r[0]), float(r[1])) for r in rows]

    async def _last_mid(self, s, coin: str) -> Optional[float]:
        r = (await s.execute(text(
            "SELECT mid FROM strat_book_5s WHERE coin=:c ORDER BY ts DESC LIMIT 1"), {"c": coin})).first()
        return float(r[0]) if r else None

    # ---- open from a fired intent ----------------------------------------
    async def open_from_intent(self, s, sid: str, coin: str, signal_id: Optional[int],
                               direction: str, entry_px: float, stop_px: Optional[float],
                               target_px: Optional[float], size: float, leverage: Optional[float],
                               time_stop_h: int, venue: str, now_ms: int,
                               entry_valid_min: Optional[int] = None, chase: bool = False) -> None:
        """Write a PENDING post-only entry. Post-only requote: if the limit would
        cross the current mid, re-quote 1 tick inside (max 3), else skip.
        `entry_valid_min` = how long the resting entry stays valid (doc per
        strategy); default = the hold.
        `chase` (s05c): same placement rule, but the resting order FOLLOWS the
        market — each manage() cycle it is unfilled it is re-quoted 1 tick inside
        the latest mid (the executor's re-quote), and once the 3 re-quotes are
        spent it crosses as a taker (fee recorded via the `entry=taker` marker)."""
        # D-103: geometry sanity BEFORE any row is written or any price is chased.
        defect = check_order_geometry(direction, entry_px, stop_px, target_px)
        if defect:
            logger.error("ORDER GEOMETRY DEFECT — trade skipped [%s %s %s]: %s",
                         sid, coin, direction, defect)
            await s.execute(text(
                "INSERT INTO strat_changelog (ts, strategy_id, diff, reason, wallet) "
                "VALUES (:ts,:s,:d,:r,'order-sanity')"),
                {"ts": now_ms, "s": sid,
                 "d": json.dumps({"defect": defect, "coin": coin, "direction": direction,
                                  "entry_px": entry_px, "stop_px": stop_px,
                                  "target_px": target_px, "signal_id": signal_id}),
                 "r": f"order geometry defect — trade skipped: {defect}"})
            return
        side = _entry_side(direction)
        mid = await self._last_mid(s, coin)
        px = entry_px
        requotes = 0
        if mid is not None and px:
            tick = _tick(px)
            while would_cross(side, px, mid):
                if requotes >= MAX_REQUOTES:
                    logger.info("paper entry skipped (post-only would cross x3): %s %s", sid, coin)
                    return
                px = requote_inside(side, mid, tick)
                requotes += 1
        # The hold TTL (hours) rides in exit_reason as a marker on the pending/open
        # row so manage() knows the hold without re-reading params; it is overwritten
        # with the real reason at close.
        marker = f"pending:hold_h={time_stop_h}" + (f":ttl_m={int(entry_valid_min)}" if entry_valid_min else "")
        if chase:
            marker += f":chase=1:rq={requotes}:rq_ts={now_ms}"
        await s.execute(text(
            "INSERT INTO strat_trades (signal_id, strategy, asset, venue, mode, direction, "
            "entry_px, stop_px, target_px, size, leverage, fill_ts, exit_ts, exit_reason) "
            "VALUES (:sig,:st,:a,:v,'paper',:d,:e,:sp,:tp,:sz,:lev,NULL,NULL,:r)"),
            {"sig": signal_id, "st": sid, "a": coin, "v": venue, "d": direction,
             "e": px, "sp": stop_px, "tp": target_px, "sz": size, "lev": leverage, "r": marker})
        logger.info("paper entry rested: %s %s %s px=%.4f sz=%.6f lev=%s%s", sid, coin, direction, px, size, leverage,
                    " (chase)" if chase else "")

    # ---- per-cycle management --------------------------------------------
    async def manage(self, s, now_ms: int) -> None:
        await self._daily_reset(s, now_ms)
        # PENDING entries → fill or TTL-cancel
        pend = (await s.execute(text(
            "SELECT id, strategy, asset, direction, entry_px, exit_reason, "
            " (SELECT ts FROM strat_signals WHERE id=strat_trades.signal_id) AS sig_ts "
            "FROM strat_trades WHERE mode='paper' AND fill_ts IS NULL AND exit_ts IS NULL "
            "AND model IS NULL"))).mappings().all()   # model rows (M1–M6) → model_runner (D-02)
        for t in pend:
            await self._resolve_pending(s, dict(t), now_ms)
        # OPEN positions → stop / target / time stop
        opens = (await s.execute(text(
            "SELECT id, strategy, asset, direction, entry_px, stop_px, target_px, size, "
            "leverage, fill_ts, exit_reason FROM strat_trades "
            "WHERE mode='paper' AND fill_ts IS NOT NULL AND exit_ts IS NULL AND model IS NULL"))).mappings().all()
        for t in opens:
            await self._resolve_open(s, dict(t), now_ms)

    @staticmethod
    def _marker(exit_reason: Optional[str]) -> dict:
        """Parse 'pending:hold_h=N[:ttl_m=M][:chase=1:rq=R:rq_ts=T][:entry=taker]'
        → {'hold_h': N, 'ttl_m': M|None, 'chase': bool, 'rq': R, 'rq_ts': T|None,
        'entry_taker': bool}."""
        out = {"hold_h": DEFAULT_HOLD_H, "ttl_m": None, "chase": False, "rq": 0, "rq_ts": None, "entry_taker": False}
        if exit_reason and exit_reason.startswith("pending:"):
            for part in exit_reason[len("pending:"):].split(":"):
                k, _, v = part.partition("=")
                try:
                    if k == "hold_h":
                        out["hold_h"] = int(v)
                    elif k == "ttl_m":
                        out["ttl_m"] = int(v)
                    elif k == "chase":
                        out["chase"] = v == "1"
                    elif k == "rq":
                        out["rq"] = int(v)
                    elif k == "rq_ts":
                        out["rq_ts"] = int(v)
                    elif k == "entry":
                        out["entry_taker"] = v == "taker"
                except ValueError:
                    pass
        return out

    @staticmethod
    def _marker_str(m: dict) -> str:
        s = f"pending:hold_h={m['hold_h']}" + (f":ttl_m={m['ttl_m']}" if m.get("ttl_m") else "")
        if m.get("chase"):
            s += f":chase=1:rq={m.get('rq', 0)}:rq_ts={m.get('rq_ts') or 0}"
        if m.get("entry_taker"):
            s += ":entry=taker"
        return s

    def _hold_ms(self, exit_reason: Optional[str]) -> int:
        return self._marker(exit_reason)["hold_h"] * _HOUR_MS

    def _ttl_ms(self, exit_reason: Optional[str]) -> int:
        m = self._marker(exit_reason)
        return m["ttl_m"] * 60_000 if m["ttl_m"] else m["hold_h"] * _HOUR_MS

    async def _resolve_pending(self, s, t: dict, now_ms: int) -> None:
        coin, direction, px = t["asset"], t["direction"], float(t["entry_px"])
        side = _entry_side(direction)
        m = self._marker(t["exit_reason"])
        anchor = int(t["sig_ts"]) if t.get("sig_ts") else now_ms - self._ttl_ms(t["exit_reason"])
        # a re-quoted chase order only sees the tape AFTER its last re-quote
        walk_from = max(anchor, m["rq_ts"] or 0) if m["chase"] else anchor
        tape = await self._mids(s, coin, walk_from, now_ms)
        for ts, mid in tape:
            if limit_fills_through(side, px, mid):
                # keep the hold marker in exit_reason so _resolve_open knows the TTL;
                # it is overwritten with the real reason at close.
                await s.execute(text("UPDATE strat_trades SET fill_ts=:f WHERE id=:id"),
                                {"f": ts, "id": t["id"]})
                logger.info("paper entry FILLED: %s %s @ %.4f", t["strategy"], coin, px)
                return
        if m["chase"] and tape and now_ms - anchor < self._ttl_ms(t["exit_reason"]):
            ts, mid = tape[-1]
            if m["rq"] < MAX_REQUOTES:
                # executor re-quote: 1 tick inside the latest mid, following the market
                new_px = requote_inside(side, mid, _tick(px))
                m2 = {**m, "rq": m["rq"] + 1, "rq_ts": ts}
                await s.execute(text("UPDATE strat_trades SET entry_px=:e, exit_reason=:r WHERE id=:id"),
                                {"e": new_px, "r": self._marker_str(m2), "id": t["id"]})
                logger.info("paper chase re-quoted (%d/%d): %s %s %.4f -> %.4f", m2["rq"], MAX_REQUOTES,
                            t["strategy"], coin, px, new_px)
            else:
                # re-quotes spent — it must cross: taker fill at the latest mid
                m2 = {**m, "entry_taker": True}
                await s.execute(text("UPDATE strat_trades SET fill_ts=:f, entry_px=:e, exit_reason=:r WHERE id=:id"),
                                {"f": ts, "e": mid, "r": self._marker_str(m2), "id": t["id"]})
                logger.info("paper chase entry CROSSED (taker): %s %s @ %.4f", t["strategy"], coin, mid)
            return
        # not filled — TTL cancel
        if now_ms - anchor >= self._ttl_ms(t["exit_reason"]):
            await s.execute(text(
                "UPDATE strat_trades SET exit_ts=:x, exit_reason='entry_unfilled', "
                "pnl_gross=0, fees=0, pnl_net=0 WHERE id=:id"),
                {"x": now_ms, "id": t["id"]})
            logger.info("paper entry unfilled (TTL): %s %s", t["strategy"], coin)

    async def _resolve_open(self, s, t: dict, now_ms: int) -> None:
        coin, direction = t["asset"], t["direction"]
        entry_px, size = float(t["entry_px"]), float(t["size"])
        stop_px = float(t["stop_px"]) if t["stop_px"] is not None else None
        target_px = float(t["target_px"]) if t["target_px"] is not None else None
        fill_ts = int(t["fill_ts"])
        hold_ms = self._hold_ms(t.get("exit_reason"))
        sign = 1.0 if direction == "long" else -1.0

        exit_px = exit_ts = None
        exit_reason = None
        best = worst = 0.0  # MFE / MAE in USD over the hold
        for ts, mid in await self._mids(s, coin, fill_ts, now_ms):
            upnl = (mid - entry_px) * size * sign
            best = max(best, upnl)
            worst = min(worst, upnl)
            if stop_px is not None and stop_triggered(direction, stop_px, mid):
                exit_px, exit_ts, exit_reason = stop_px, ts, "stop"; break
            tside = "sell" if direction == "long" else "buy"
            if target_px is not None and limit_fills_through(tside, target_px, mid):
                exit_px, exit_ts, exit_reason = target_px, ts, "target"; break
            if ts - fill_ts >= hold_ms:
                exit_px, exit_ts, exit_reason = mid, ts, "time_stop"; break
        if exit_px is None:
            # persist running MAE/MFE, keep open
            await s.execute(text("UPDATE strat_trades SET mae=:mae, mfe=:mfe WHERE id=:id"),
                            {"mae": worst, "mfe": best, "id": t["id"]})
            return
        await self._close(s, t, exit_px, exit_ts, exit_reason, worst, best, now_ms)

    async def _close(self, s, t: dict, exit_px: float, exit_ts: int, reason: str,
                     mae: float, mfe: float, now_ms: int) -> None:
        direction, entry_px, size = t["direction"], float(t["entry_px"]), float(t["size"])
        entry_taker = self._marker(t.get("exit_reason"))["entry_taker"]
        pnl_gross, fees, pnl_net = compute_close(direction, entry_px, exit_px, size, reason, entry_taker=entry_taker)
        if entry_taker:
            reason = f"{reason}:entry=taker"          # keep the fee kind visible after close
        await s.execute(text(
            "UPDATE strat_trades SET exit_ts=:x, exit_reason=:r, pnl_gross=:g, fees=:f, "
            "pnl_net=:n, mae=:mae, mfe=:mfe WHERE id=:id"),
            {"x": exit_ts, "r": reason, "g": pnl_gross, "f": fees, "n": pnl_net,
             "mae": mae, "mfe": mfe, "id": t["id"]})
        logger.info("paper trade CLOSED: %s %s %s pnl_net=%.4f (%s)",
                    t["strategy"], t["asset"], direction, pnl_net, reason)
        await self._apply_risk(s, t["strategy"], pnl_net, now_ms)

    # ---- risk rules (doc 00 §5) — ANNOTATE ONLY in paper -------------------
    async def _apply_risk(self, s, sid: str, pnl_net: float, now_ms: int) -> None:
        """Apply the risk rules to a closed trade and PERSIST the outcome
        (D-100). daily_cap_hit_until, paused_until and kill_switch_tripped are
        written; the next _route() call reads them and refuses the entry."""
        rs = (await s.execute(text(
            "SELECT equity_usd, daily_realized_pnl, consecutive_losses FROM strat_risk_state LIMIT 1"))).first()
        from app.config import settings
        equity = (rs[0] if rs and rs[0] else settings.STRATEGY_PAPER_EQUITY_USD)
        g = risk.GlobalRisk(equity_usd=equity, daily_realized_pnl=(rs[1] or 0.0) if rs else 0.0,
                            daily_cap_hit_until=None, consecutive_losses=(rs[2] or 0) if rs else 0,
                            paused_until=None)
        # rolling PF from the last 20 closed trades of THIS strategy
        pnls = [float(r[0]) for r in (await s.execute(text(
            "SELECT pnl_net FROM strat_trades WHERE strategy=:s AND exit_ts IS NOT NULL "
            "AND fill_ts IS NOT NULL ORDER BY exit_ts DESC LIMIT 20"), {"s": sid})).all()][::-1]
        sr = risk.StratRisk(sid, recent_pnls=tuple(pnls[:-1]))  # exclude the just-closed (register_exit re-adds)
        g2, _s2, demotions = risk.register_exit(g, sr, pnl_net, now_ms)
        # D-100: persist the cap/pause the rules just produced (was: forced NULL).
        await s.execute(text(
            "UPDATE strat_risk_state SET daily_realized_pnl=:d, consecutive_losses=:cl, "
            "daily_cap_hit_until=:cap, paused_until=:pu "
            "WHERE id=(SELECT id FROM (SELECT id FROM strat_risk_state LIMIT 1) x)"),
            {"d": g2.daily_realized_pnl, "cl": g2.consecutive_losses,
             "cap": g2.daily_cap_hit_until, "pu": g2.paused_until})
        pf = risk.rolling_pf(tuple(pnls))
        tripped = 1 if (_s2.paper_paused) else 0
        # D-100: kill_switch_tripped is persisted and the row is moved to
        # paper-paused; it stays until a manual re-arm with a reason.
        if tripped:
            await s.execute(text(
                "UPDATE strat_strategy_state SET rolling20_pf=:pf, kill_switch_tripped=1, "
                "effective_mode='paper-paused', effective_reason=:r, "
                "waiting_for_sentence='Kill switch tripped — manual re-arm required' "
                "WHERE strategy_id=:s"),
                {"pf": (None if pf in (None, float("inf")) else pf),
                 "r": (_s2.paused_reason or "rolling-20 PF < 0.9"), "s": sid})
        else:
            await s.execute(text(
                "UPDATE strat_strategy_state SET rolling20_pf=:pf WHERE strategy_id=:s"),
                {"pf": (None if pf in (None, float("inf")) else pf), "s": sid})
        for d in demotions:
            logger.warning("risk engine ENFORCED [%s]: %s", sid, d)
            await s.execute(text(
                "INSERT INTO strat_changelog (ts, strategy_id, diff, reason, wallet) "
                "VALUES (:ts, :s, :diff, :r, 'risk-engine')"),
                {"ts": now_ms, "s": sid,
                 "diff": json.dumps({"risk_engine_enforced": d, "pnl_net": pnl_net,
                                     "daily_realized_pnl": g2.daily_realized_pnl,
                                     "consecutive_losses": g2.consecutive_losses,
                                     "daily_cap_hit_until": g2.daily_cap_hit_until,
                                     "paused_until": g2.paused_until,
                                     "kill_switch_tripped": bool(tripped),
                                     "rolling20_pf": pf if pf != float("inf") else "inf"}),
                 "r": f"risk engine ENFORCED: {d}"})

    async def _daily_reset(self, s, now_ms: int) -> None:
        """Reset daily realized PnL at each UTC day boundary (compare updated_ts date)."""
        import datetime as _dt
        row = (await s.execute(text(
            "SELECT daily_realized_pnl, daily_cap_hit_until, "
            "DATE(updated_ts) AS d FROM strat_risk_state LIMIT 1"))).first()
        if not row:
            return
        today = _dt.datetime.fromtimestamp(now_ms / 1000.0, tz=_dt.timezone.utc).date()
        last = row[2]
        if last is not None and last < today:
            cap = row[1]
            new_cap = cap if (cap and now_ms < cap) else None
            await s.execute(text(
                "UPDATE strat_risk_state SET daily_realized_pnl=0, daily_cap_hit_until=:c "
                "WHERE id=(SELECT id FROM (SELECT id FROM strat_risk_state LIMIT 1) x)"), {"c": new_cap})
