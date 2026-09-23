"""Weekly learning + calibration (doc 10 §4.6 / §5). Runs Sunday 00:30 UTC from
the scheduler. Pure statistics functions first (unit-tested), then the DB
runner. Weight changes are gated by config learning_enabled AND 60 days since
the model's first trade; calibration rows and flags are written regardless."""
from __future__ import annotations

import datetime as _dt
import json
import logging
from typing import Optional

from sqlalchemy import text
from sqlalchemy.dialects.mysql import insert as mysql_insert

from app.db.strategy_models import MindCalibration, MindModelState, MindWeight, StratChangelog
from .base import Mind
from .config import common

logger = logging.getLogger(__name__)

STRONG = 0.7
WEAK = 0.3
MIN_TRADES = 100
REVIEW_AFTER = 200
STEP = 0.05
MIN_DIFF_R = 0.1
REVIEW_DIFF_R = 0.05
MAX_CHANGE_30D = 0.25
CAL_MIN_TRADES = 100
CAL_MIN_SPREAD_R = 0.2
BUCKETS = (("0.55-0.65", 0.55, 0.65), ("0.65-0.75", 0.65, 0.75), ("0.75+", 0.75, 9.0))
LEARN_WALLET = "mind-learn"
DAY_MS = 86_400_000


def _mean(xs: list[float]) -> Optional[float]:
    return sum(xs) / len(xs) if xs else None


def reason_stats(trades: list[dict]) -> dict[str, dict]:
    """trades: [{reasons: [{key, strength}], r: float}]. Per reason key:
    n_strong, n_weak, mean_r_strong, mean_r_weak, diff (strong − weak)."""
    acc: dict[str, dict] = {}
    for t in trades:
        r = t.get("r")
        if r is None:
            continue
        for rs in t.get("reasons") or []:
            k, s = rs.get("key"), rs.get("strength")
            if k is None or s is None:
                continue
            d = acc.setdefault(k, {"strong": [], "weak": []})
            if s >= STRONG:
                d["strong"].append(float(r))
            elif s <= WEAK:
                d["weak"].append(float(r))
    out = {}
    for k, d in acc.items():
        ms, mw = _mean(d["strong"]), _mean(d["weak"])
        out[k] = {"n_strong": len(d["strong"]), "n_weak": len(d["weak"]), "mean_r_strong": ms, "mean_r_weak": mw,
                  "diff": (ms - mw) if ms is not None and mw is not None else None}
    return out


def propose_weights(stats: dict[str, dict], mind: Mind, n_trades: int, weights_30d_ago: dict[str, float]) -> tuple[dict[str, float], list[str], list[dict]]:
    """Returns (new_weights, review_flags, log_rows). Only reasons whose strong AND
    weak buckets exist move; ±0.05 per week in the sign of the R difference when
    |diff| > 0.1R; clipped to [min,max]; total change over 30 days capped at 25%."""
    new: dict[str, float] = {}
    review: list[str] = []
    log: list[dict] = []
    if n_trades < MIN_TRADES:
        return new, review, log
    for r in mind.reasons:
        st = stats.get(r.key)
        if not st or st["diff"] is None:
            continue
        d = st["diff"]
        if n_trades >= REVIEW_AFTER and abs(d) < REVIEW_DIFF_R:
            review.append(r.key)
        if abs(d) <= MIN_DIFF_R:
            continue
        w0 = r.weight
        w1 = w0 + STEP * (1 if d > 0 else -1)
        w1 = max(r.min_weight, min(r.max_weight, w1))
        base = weights_30d_ago.get(r.key, w0)
        if base > 0 and abs(w1 - base) / base > MAX_CHANGE_30D:
            cap = base * (1 + MAX_CHANGE_30D) if w1 > base else base * (1 - MAX_CHANGE_30D)
            w1 = max(r.min_weight, min(r.max_weight, cap))
        if abs(w1 - w0) < 1e-9:
            continue
        new[r.key] = round(w1, 4)
        log.append({"reason": r.key, "from": w0, "to": round(w1, 4), "diff_r": round(d, 4),
                    "n_strong": st["n_strong"], "n_weak": st["n_weak"]})
    return new, review, log


def bucket_of(conviction: Optional[float]) -> Optional[str]:
    if conviction is None:
        return None
    for name, lo, hi in BUCKETS:
        if lo <= conviction < hi:
            return name
    return None


def calibration(trades: list[dict]) -> dict[str, dict]:
    """trades: [{conviction, r}] → per bucket {trades, win_rate, mean_r}."""
    acc: dict[str, list[float]] = {b[0]: [] for b in BUCKETS}
    for t in trades:
        b = bucket_of(t.get("conviction"))
        if b is None or t.get("r") is None:
            continue
        acc[b].append(float(t["r"]))
    out = {}
    for b, rs in acc.items():
        out[b] = {"trades": len(rs), "win_rate": (sum(1 for x in rs if x > 0) / len(rs)) if rs else None,
                  "mean_r": _mean(rs)}
    return out


def not_discriminating(cal: dict[str, dict], n_trades: int) -> bool:
    if n_trades < CAL_MIN_TRADES:
        return False
    top, bot = cal.get("0.75+", {}).get("mean_r"), cal.get("0.55-0.65", {}).get("mean_r")
    if top is None or bot is None:
        return False
    return (top - bot) < CAL_MIN_SPREAD_R


def iso_week(ts_ms: int) -> str:
    d = _dt.datetime.utcfromtimestamp(ts_ms / 1000).isocalendar()
    return f"{d[0]}-W{d[1]:02d}"


# --------------------------------------------------------------------------- DB
async def load_closed_trades(session, model: str) -> list[dict]:
    rows = (await session.execute(text(
        "SELECT t.id, t.exit_ts, t.fill_ts, t.r_multiple, s.conviction, s.reasons_json "
        "FROM strat_trades t LEFT JOIN strat_signals s ON s.id = t.signal_id "
        "WHERE t.model = :m AND t.mode = 'paper' AND t.exit_ts IS NOT NULL AND t.r_multiple IS NOT NULL "
        "ORDER BY t.exit_ts"), {"m": model})).mappings().all()
    out = []
    for r in rows:
        rj = r["reasons_json"]
        if isinstance(rj, str):
            try:
                rj = json.loads(rj)
            except ValueError:
                rj = None
        out.append({"id": r["id"], "exit_ts": int(r["exit_ts"]), "fill_ts": int(r["fill_ts"] or 0),
                    "r": float(r["r_multiple"]), "conviction": (float(r["conviction"]) if r["conviction"] is not None else None),
                    "reasons": rj or []})
    return out


async def weights_at(session, model: str, ts_ms: int, current: dict[str, float]) -> dict[str, float]:
    """Weights in force at ts_ms: replay mind-learn changelog rows after ts backwards."""
    rows = (await session.execute(text(
        "SELECT diff FROM strat_changelog WHERE wallet = :w AND strategy_id = :m AND ts > :ts ORDER BY ts DESC"),
        {"w": LEARN_WALLET, "m": model, "ts": ts_ms})).scalars().all()
    w = dict(current)
    for d in rows:
        if isinstance(d, str):
            try:
                d = json.loads(d)
            except ValueError:
                continue
        for ch in (d or {}).get("changes") or []:
            if ch.get("reason") in w:
                w[ch["reason"]] = float(ch["from"])
    return w


async def set_flag(session, model: str, flag: str, value: dict, now_ms: int) -> None:
    stmt = mysql_insert(MindModelState).values(model=model, coin="*", key=f"flag:{flag}", value_json=value,
                                               updated_ts=now_ms)
    await session.execute(stmt.on_duplicate_key_update(value_json=value, updated_ts=now_ms))


async def run_weekly(session, minds: dict[str, Mind], now_ms: int) -> dict:
    """Sunday 00:30 job. Returns a summary dict (logged by the scheduler)."""
    cfg = common()
    enabled = bool(cfg.get("learning_enabled", False))
    start_days = int(cfg.get("learning_start_after_days", 60))
    week = iso_week(now_ms - 7 * DAY_MS)          # the week that just closed
    summary: dict = {"week": week, "models": {}}
    for model, mind in minds.items():
        trades = await load_closed_trades(session, model)
        n = len(trades)
        last_week = [t for t in trades if iso_week(t["exit_ts"]) == week]
        cal_week = calibration(last_week)
        for b, v in cal_week.items():
            stmt = mysql_insert(MindCalibration).values(model=model, week=week, bucket=b, trades=v["trades"],
                                                        win_rate=v["win_rate"], mean_r=v["mean_r"])
            await session.execute(stmt.on_duplicate_key_update(trades=v["trades"], win_rate=v["win_rate"], mean_r=v["mean_r"]))
        cal_all = calibration(trades)
        nd = not_discriminating(cal_all, n)
        await set_flag(session, model, "mind_not_discriminating", {"active": nd, "n": n, "cal": cal_all}, now_ms)
        first_ts = min((t["fill_ts"] for t in trades if t["fill_ts"]), default=None)
        gate_ok = first_ts is not None and now_ms - first_ts >= start_days * DAY_MS
        stats = reason_stats(trades)
        w30 = await weights_at(session, model, now_ms - 30 * DAY_MS, mind.weights())
        new, review, log = propose_weights(stats, mind, n, w30)
        await set_flag(session, model, "review", {"reasons": review, "n": n}, now_ms)
        applied = False
        if new and enabled and gate_ok:
            mind.apply_weights(new)
            for k, w in new.items():
                await session.execute(text(
                    "UPDATE mind_weights SET weight = :w, updated_at = :ts WHERE model = :m AND reason_key = :k"),
                    {"w": w, "ts": now_ms, "m": model, "k": k})
            await session.execute(mysql_insert(StratChangelog).values(
                ts=now_ms, strategy_id=model, wallet=LEARN_WALLET,
                diff={"week": week, "changes": log, "n_trades": n},
                reason=f"{model} weekly learn {week}: {len(log)} weight change(s) from {n} closed trades"))
            applied = True
        elif log:
            await session.execute(mysql_insert(StratChangelog).values(
                ts=now_ms, strategy_id=model, wallet=LEARN_WALLET + ":proposed",
                diff={"week": week, "changes": log, "n_trades": n, "applied": False,
                      "why": ("learning_disabled" if not enabled else "first_60_days")},
                reason=f"{model} weekly learn {week}: {len(log)} proposed change(s) NOT applied"))
        summary["models"][model] = {"n": n, "week_trades": len(last_week), "proposed": len(log), "applied": applied,
                                    "review": review, "not_discriminating": nd}
    await session.commit()
    return summary
