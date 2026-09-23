"""/api/strategies/* — read endpoints + admin mode/param writes.

Reads the strat_ tables the worker writes on EVERY evaluation. Paper is paper:
`effective_mode` is computed by mode.compute_effective_mode (paper → paper,
off → off, live → off: no execution adapter built). Every count/stat here is
COUNTED from strat_signals / strat_trades — nothing is hardcoded. mode PATCH
writes `requested_mode` only; parameter PATCH requires a reason and writes a
changelog row. Writes are admin-only. Nothing here places an order.
"""
from __future__ import annotations

import re
import time

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select, text

from app.config import settings
from app.db.database import get_session_factory
from app.db.models import User
from app.db.strategy_models import (
    StratStrategyState, StratRiskState, StratParameter, StratSignal, StratTrade,
    StratChangelog, StratFeedMeta,
)
from app.routers.auth import get_authenticated_user
from app.routers.admin import require_admin
from app.strategy_engine import mode as mode_mod
from app.strategy_engine import conditions as cond_mod
from app.strategy_engine.seed import STRATEGIES, MODELS
from app.strategy_engine.backtest.metrics import compute_metrics
from app.strategy_engine.contract import (
    StrategyState, RiskState, Signal, SignalCondition, SignalRow, StratStats, BacktestMeta,
    Trade, Parameter, ChangelogEntry, FeedHealth, StrategyDetail, ModePatch, ParameterPatch,
    MindReason, MindVeto, MindMultiplier, MindBreakdown, NormaliserRow, MindWeightRow, MindCalibrationRow,
    OutcomeSplit, ExecutionStats, AlertPref, AlertPrefsPatch,
)
from app.db.strategy_models import MindWeight, MindCalibration, MindModelState, StratAlertPref

router = APIRouter(prefix="/strategies", tags=["strategies"])

_NAMES = dict(STRATEGIES + MODELS)
# strategy id → model code for the six heuristic-mind rows (docs 10–16)
_MODEL_OF = {sid: f"M{i + 1}" for i, (sid, _) in enumerate(MODELS)}
_VALID_MODES = {"off", "paper", "live"}
_H = 3_600_000
_D = 86_400_000
_WINDOWS = {"7d": 7 * _D, "30d": 30 * _D, "90d": 90 * _D, "all": None}
_NUM = re.compile(r"[-+]?\d[\d,]*\.?\d*")


async def _risk_row(session):
    return (await session.execute(select(StratRiskState))).scalars().first()


def _utc_midnight_ms(now_ms: int) -> int:
    return now_ms - (now_ms % _D)


async def _sig_counts(s, now_ms: int) -> dict[str, dict]:
    """evals / setups (direction identified) / fires per strategy, last 24h."""
    out: dict[str, dict] = {}
    for r in (await s.execute(text(
            "SELECT strategy, COUNT(*), SUM(direction IS NOT NULL), SUM(fired) FROM strat_signals "
            "WHERE ts >= :a GROUP BY strategy"), {"a": now_ms - 24 * _H})).all():
        out[r[0]] = {"evals": int(r[1] or 0), "setups": int(r[2] or 0), "fires": int(r[3] or 0)}
    return out


async def _fill_counts(s) -> dict[str, dict]:
    """fills / fires per strategy, ALL time — the pullback-vs-chase question
    (s05 vs s05c) is answered by this ratio. Counted, never derived."""
    out: dict[str, dict] = {}
    for r in (await s.execute(text("SELECT strategy, SUM(fired) FROM strat_signals GROUP BY strategy"))).all():
        out.setdefault(r[0], {})["fires"] = int(r[1] or 0)
    for r in (await s.execute(text(
            "SELECT strategy, SUM(fill_ts IS NOT NULL), "
            " SUM(fill_ts IS NOT NULL AND exit_reason LIKE '%entry=taker%'), "
            " SUM(fill_ts IS NULL AND exit_ts IS NULL), "
            " SUM(fill_ts IS NULL AND exit_reason='entry_unfilled') "
            "FROM strat_trades WHERE mode='paper' GROUP BY strategy"))).all():
        d = out.setdefault(r[0], {})
        d.update(fills=int(r[1] or 0), taker=int(r[2] or 0), pending=int(r[3] or 0), unfilled=int(r[4] or 0))
    return out


async def _trade_stats(s, sid: str | None, since_ms: int | None) -> StratStats:
    q = "SELECT pnl_net, pnl_gross, fees, mae, mfe FROM strat_trades WHERE mode='paper' AND exit_ts IS NOT NULL"
    args: dict = {}
    if sid:
        q += " AND strategy=:s"; args["s"] = sid
    if since_ms is not None:
        q += " AND exit_ts >= :a"; args["a"] = since_ms
    rows = (await s.execute(text(q + " ORDER BY exit_ts"), args)).all()
    trades = [{"pnl_net": float(r[0] or 0), "pnl_gross": float(r[1] or 0), "fees": float(r[2] or 0),
               "mae": r[3], "mfe": r[4]} for r in rows]
    m = compute_metrics(trades)
    pf = m["profit_factor"]
    return StratStats(trades=m["trades"], win_rate=m["win_rate"],
                      pf=(None if pf is None else (999.0 if pf == float("inf") else round(pf, 2))),
                      expectancy=m["expectancy"], max_dd=m["max_drawdown"], fee_drag=m["fee_drag_pct"],
                      net=round(sum(t["pnl_net"] for t in trades), 2))


async def _latest_signals(s, sid: str) -> dict[str, StratSignal]:
    """Latest evaluation row per asset for one strategy."""
    out: dict[str, StratSignal] = {}
    for coin in settings.strategy_assets_list:
        row = (await s.execute(select(StratSignal).where(StratSignal.strategy == sid, StratSignal.asset == coin)
                               .order_by(StratSignal.ts.desc()).limit(1))).scalars().first()
        if row:
            out[coin] = row
    return out


def _warming_union(latest: dict[str, StratSignal]) -> tuple[list[str], dict]:
    warm: list[str] = []
    labels: dict = {}
    for row in latest.values():
        comp = row.components if isinstance(row.components, dict) else {}
        for w in comp.get("warming") or []:
            if w not in warm:
                warm.append(w)
        for k, v in (comp.get("labels") or {}).items():
            labels.setdefault(k, v)
    return warm, labels


async def _build_state(s, row: StratStrategyState, param_count: int, counts: dict, now_ms: int,
                       fills: dict | None = None) -> StrategyState:
    eff, reason = mode_mod.compute_effective_mode(row.requested_mode)
    c = counts.get(row.strategy_id, {})
    f = (fills or {}).get(row.strategy_id, {})
    fires_total, fills_total = f.get("fires", 0), f.get("fills", 0)
    st30 = await _trade_stats(s, row.strategy_id, now_ms - 30 * _D)
    open_n = int((await s.execute(text(
        "SELECT COUNT(*) FROM strat_trades WHERE mode='paper' AND exit_ts IS NULL AND strategy=:s"),
        {"s": row.strategy_id})).scalar() or 0)
    warm, labels = _warming_union(await _latest_signals(s, row.strategy_id))
    return StrategyState(
        id=row.strategy_id, name=_NAMES.get(row.strategy_id, row.strategy_id),
        requested_mode=row.requested_mode, effective_mode=eff, effective_reason=reason,
        rolling20_pf=row.rolling20_pf, kill_switch_tripped=False,
        waiting_for_sentence=row.waiting_for_sentence, last_eval_ts=row.last_eval_ts,
        parameter_count=param_count, strategy_implemented=True, model=_MODEL_OF.get(row.strategy_id),
        evals_24h=c.get("evals", 0), setups_24h=c.get("setups", 0), fires_24h=c.get("fires", 0),
        trades_30d=st30.trades, win_rate_30d=st30.win_rate, pf_30d=st30.pf, net_30d=st30.net,
        open_positions=open_n, warming=warm, labels=labels,
        fires_total=fires_total, fills_total=fills_total, fills_taker=f.get("taker", 0),
        entries_pending=f.get("pending", 0), entries_unfilled=f.get("unfilled", 0),
        fill_rate=(round(fills_total / fires_total, 4) if fires_total else None),
    )


@router.get("")
async def list_strategies() -> list[StrategyState]:
    sf = get_session_factory()
    now_ms = int(time.time() * 1000)
    async with sf() as s:
        rows = (await s.execute(select(StratStrategyState))).scalars().all()
        pcounts = {sid: 0 for sid, _ in STRATEGIES + MODELS}
        for r in (await s.execute(text("SELECT strategy_id, COUNT(*) FROM strat_parameters GROUP BY strategy_id"))).all():
            pcounts[r[0]] = int(r[1])
        counts = await _sig_counts(s, now_ms)
        fills = await _fill_counts(s)
        order = {sid: i for i, (sid, _) in enumerate(STRATEGIES + MODELS)}
        rows.sort(key=lambda r: order.get(r.strategy_id, 99))
        return [await _build_state(s, r, pcounts.get(r.strategy_id, 0), counts, now_ms, fills) for r in rows]


@router.get("/risk")
async def get_risk() -> RiskState:
    sf = get_session_factory()
    now_ms = int(time.time() * 1000)
    start_eq = float(settings.STRATEGY_PAPER_EQUITY_USD)
    async with sf() as s:
        r = await _risk_row(s)
        tot = (await s.execute(text(
            "SELECT COALESCE(SUM(pnl_net),0), COALESCE(SUM(CASE WHEN exit_ts>=:m THEN pnl_net ELSE 0 END),0), "
            "COALESCE(SUM(CASE WHEN exit_ts>=:mo THEN fees ELSE 0 END),0), "
            "COALESCE(SUM(CASE WHEN exit_ts>=:mo THEN pnl_gross ELSE 0 END),0) "
            "FROM strat_trades WHERE mode='paper' AND exit_ts IS NOT NULL"),
            {"m": _utc_midnight_ms(now_ms), "mo": now_ms - 30 * _D})).first()
        open_n = int((await s.execute(text(
            "SELECT COUNT(*) FROM strat_trades WHERE mode='paper' AND exit_ts IS NULL"))).scalar() or 0)
        pnl_total, pnl_today, fees_m, gross_m = (float(x or 0) for x in tot)
        return RiskState(
            equity_usd=round(start_eq + pnl_total, 2),
            daily_realized_pnl=round(pnl_today, 2),
            daily_cap_hit_until=None, consecutive_losses=int(r.consecutive_losses) if r else 0,
            paused_until=None, open_positions_count=open_n,
            paper_equity_start=start_eq, paper_pnl_total=round(pnl_total, 2), paper_pnl_today=round(pnl_today, 2),
            paper_fees_month=round(fees_m, 4), paper_gross_month=round(gross_m, 2),
        )


@router.get("/feed-health")
async def get_feed_health() -> list[FeedHealth]:
    sf = get_session_factory()
    tables = {
        "hl_candles": "strat_candles", "hl_trades_1m": "strat_trades_1m",
        "hl_book_5s": "strat_book_5s", "hl_oi_1m": "strat_oi_1m",
        "funding": "strat_funding", "liquidations": "strat_liquidations",
    }
    # Per-source staleness thresholds (Part A.1): 1-minute aggregates 180s;
    # 5s/tick feeds 60s; candles 2x the fastest timeframe (15m -> 1800s);
    # liquidations exempt (partial coverage is sparse, not stale).
    thresholds = {
        "hl_candles": 1800, "hl_trades_1m": 180, "hl_book_5s": 60,
        "hl_oi_1m": 180, "funding": 180, "liquidations": None,
    }
    now_ms = int(time.time() * 1000)
    out: list[FeedHealth] = []
    async with sf() as s:
        cov = {f.feed: f.coverage for f in (await s.execute(select(StratFeedMeta))).scalars().all()}
        for name, table in tables.items():
            row = (await s.execute(text(f"SELECT COUNT(*), MAX(ts) FROM {table}"))).first()
            cnt = int(row[0] or 0)
            last_ts = int(row[1]) if row[1] is not None else None
            age = (now_ms - last_ts) / 1000.0 if last_ts else None
            coverage = "partial" if name == "liquidations" else cov.get(name, "full")
            thr = thresholds.get(name)
            is_stale = bool(thr is not None and age is not None and age > thr)
            out.append(FeedHealth(feed=name, coverage=coverage, rows=cnt, last_ts=last_ts,
                                  age_s=age, stale_threshold_s=thr, stale=is_stale))
    return out


def _mind_reasons(row: StratSignal) -> list[MindReason]:
    rj = row.reasons_json if isinstance(row.reasons_json, list) else []
    total_w = sum(float(r.get("weight") or 0) for r in rj if isinstance(r, dict)) or 1.0
    out = []
    for r in rj:
        if not isinstance(r, dict) or "note" in r:   # weight-0 annotations (alignment_branch) are not reasons
            continue
        st, w = float(r.get("strength") or 0), float(r.get("weight") or 0)
        out.append(MindReason(key=str(r.get("key")), description=r.get("description"), strength=st, weight=w,
                              contribution=round(st * w / total_w, 4)))
    return out


def _sig(row: StratSignal) -> Signal:
    comp = row.components if isinstance(row.components, dict) else {}
    if getattr(row, "model", None):
        vj = row.vetoes_json if isinstance(row.vetoes_json, list) else []
        mj = row.multipliers_json if isinstance(row.multipliers_json, list) else []
        return Signal(
            id=row.id, ts=row.ts, strategy=row.strategy, asset=row.asset, venue=row.venue,
            mode=row.mode, direction=row.direction, total_score=row.total_score, fired=bool(row.fired),
            reason=row.reason, labels={"day_type": row.day_type or "", "session": str(comp.get("session") or "")},
            components={"atr15": comp.get("atr15")}, model=row.model, level_type=row.level_type,
            level_price=row.level_price, day_type=row.day_type, raw_conviction=row.raw_conviction,
            conviction=row.conviction, size_tier=row.size_tier, reasons=_mind_reasons(row),
            vetoes=[MindVeto(key=str(v.get("key")), text=v.get("text"), hit=bool(v.get("hit", True)))
                    for v in vj if isinstance(v, dict)],
            multipliers=[MindMultiplier(key=str(m.get("key")), multiplier=float(m.get("m", m.get("multiplier")) or 1.0))
                         for m in mj if isinstance(m, dict)],
            thesis=row.thesis, setup=dict(comp.get("setup") or {}),
        )
    return Signal(
        id=row.id, ts=row.ts, strategy=row.strategy, asset=row.asset, venue=row.venue,
        mode=row.mode, direction=row.direction, regime_score=row.regime_score,
        bias_score=row.bias_score, trigger_score=row.trigger_score, timing_score=row.timing_score,
        total_score=row.total_score, fired=bool(row.fired), reason=row.reason,
        conditions=[SignalCondition(**c) for c in comp.get("conditions", [])],
        warming=list(comp.get("warming") or []), labels=dict(comp.get("labels") or {}),
        risk_note=comp.get("risk_note"), paper_fill=bool(comp.get("paper_fill")),
        fire_threshold=comp.get("fire_threshold"), components=dict(comp.get("components") or {}),
        intents=list(comp.get("intents") or []),
    )


def _trade(row: StratTrade) -> Trade:
    return Trade(
        id=row.id, signal_id=row.signal_id, strategy=row.strategy, asset=row.asset, venue=row.venue,
        mode=row.mode, direction=row.direction, entry_px=row.entry_px, stop_px=row.stop_px,
        target_px=row.target_px, size=row.size, leverage=row.leverage, fill_ts=row.fill_ts,
        exit_ts=row.exit_ts, exit_reason=row.exit_reason, pnl_gross=row.pnl_gross, fees=row.fees,
        pnl_net=row.pnl_net, mae=row.mae, mfe=row.mfe,
        model=getattr(row, "model", None), expected_hold_min=getattr(row, "expected_hold_min", None),
        r_multiple=getattr(row, "r_multiple", None),
        lifecycle=(row.lifecycle_json if isinstance(getattr(row, "lifecycle_json", None), dict) else {}),
        in_trade_checks=(row.in_trade_checks_json if isinstance(getattr(row, "in_trade_checks_json", None), list) else []),
    )


@router.get("/signals/recent")
async def recent_signals(hours: int = 24, limit: int = 300) -> list[SignalRow]:
    """Every evaluation (fired / skipped / not confirmed) across all strategies
    in the last `hours`, newest first; consecutive identical rows of the same
    strategy+asset (same fired flag + same reason sentence once its live numbers
    are masked, e.g. "next EU in 12h 08m" == "... in 12h 07m") collapsed with a
    count; the row keeps the LATEST sentence/score."""
    hours = max(1, min(hours, 24 * 7))
    sf = get_session_factory()
    since = int(time.time() * 1000) - hours * _H
    async with sf() as s:
        rows = (await s.execute(select(StratSignal).where(StratSignal.ts >= since)
                                .order_by(StratSignal.ts.asc(), StratSignal.id.asc()).limit(20_000))).scalars().all()
    last: dict[tuple[str, str], tuple[SignalRow, str]] = {}
    out: list[SignalRow] = []
    for r in rows:
        comp = r.components if isinstance(r.components, dict) else {}
        key = (r.strategy, r.asset)
        shape = _NUM.sub("#", r.reason or "")
        prev = last.get(key)
        if prev is not None and prev[0].fired == bool(r.fired) and prev[1] == shape and not r.fired:
            prev[0].ts_last = r.ts; prev[0].count += 1
            prev[0].reason = r.reason; prev[0].total_score = r.total_score
            prev[0].warming = list(comp.get("warming") or [])
            continue
        row = SignalRow(ts_first=r.ts, ts_last=r.ts, count=1, strategy=r.strategy, asset=r.asset,
                        direction=r.direction, fired=bool(r.fired), reason=r.reason, total_score=r.total_score,
                        warming=list(comp.get("warming") or []), paper_fill=bool(comp.get("paper_fill")))
        out.append(row)
        last[key] = (row, shape)
    out.sort(key=lambda x: x.ts_last, reverse=True)
    return out[:max(1, min(limit, 2000))]


@router.get("/trades/open")
async def open_trades() -> list[Trade]:
    sf = get_session_factory()
    async with sf() as s:
        rows = (await s.execute(select(StratTrade).where(StratTrade.mode == "paper", StratTrade.exit_ts.is_(None))
                                .order_by(StratTrade.id.desc()))).scalars().all()
        return [_trade(r) for r in rows]


@router.get("/{sid}")
async def get_strategy(sid: str) -> StrategyDetail:
    if sid not in _NAMES:
        raise HTTPException(404, "unknown strategy")
    from app.strategy_engine.backtest.runner import cached_report
    sf = get_session_factory()
    now_ms = int(time.time() * 1000)
    async with sf() as s:
        row = (await s.execute(select(StratStrategyState).where(StratStrategyState.strategy_id == sid))).scalars().first()
        if not row:
            raise HTTPException(404, "strategy state not found")
        params = (await s.execute(select(StratParameter).where(StratParameter.strategy_id == sid))).scalars().all()
        counts = await _sig_counts(s, now_ms)
        sigs = (await s.execute(select(StratSignal).where(StratSignal.strategy == sid).order_by(StratSignal.ts.desc()).limit(50))).scalars().all()
        trades = (await s.execute(select(StratTrade).where(StratTrade.strategy == sid).order_by(StratTrade.id.desc()).limit(50))).scalars().all()
        latest = await _latest_signals(s, sid)
        # the catalog list, overlaid with the most recent live values (first asset with a row)
        conds = [SignalCondition(**c) for c in cond_mod.CONDITIONS.get(sid, cond_mod.MODEL_CONDITIONS.get(sid, []))]
        if latest and sid not in _MODEL_OF:
            first = next(iter(latest.values()))
            conds = _sig(first).conditions or conds
        stats = {w: await _trade_stats(s, sid, (now_ms - span) if span else None) for w, span in _WINDOWS.items()}
        state = await _build_state(s, row, len(params), counts, now_ms, await _fill_counts(s))
    bt = cached_report(sid, max_age_s=10 ** 9)          # any cached report; the endpoint refreshes >24h
    return StrategyDetail(
        state=state,
        parameters=[Parameter(key=p.key, value=p.value, unit=p.unit, default=p.default) for p in params],
        conditions=conds,
        recent_signals=[_sig(x) for x in sigs],
        open_positions=[_trade(x) for x in trades if x.exit_ts is None],
        recent_trades=[_trade(x) for x in trades if x.exit_ts is not None],
        latest={coin: _sig(x) for coin, x in latest.items()},
        stats=stats,
        backtest=(BacktestMeta(generated_ts=bt["generated_ts"], start_ms=bt["start_ms"], end_ms=bt["end_ms"],
                               trades=bt.get("trades", 0), evals=bt.get("evals", 0), coverage=bt.get("coverage", {}))
                  if bt else None),
    )


@router.get("/{sid}/breakdown")
async def get_breakdown(sid: str) -> MindBreakdown:
    """Breakdown tab for a model row: live mind_weights, calibration by
    conviction bucket (mind_calibration), weight history (changelog rows the
    weekly learner writes as wallet 'mind-learn'), R by size tier — all counted."""
    code = _MODEL_OF.get(sid)
    if not code:
        raise HTTPException(404, "not a model strategy")
    sf = get_session_factory()
    async with sf() as s:
        w = (await s.execute(select(MindWeight).where(MindWeight.model == code).order_by(MindWeight.reason_key))).scalars().all()
        cal = (await s.execute(select(MindCalibration).where(MindCalibration.model == code)
                               .order_by(MindCalibration.week.desc(), MindCalibration.bucket))).scalars().all()
        hist = (await s.execute(select(StratChangelog).where(StratChangelog.strategy_id == sid, StratChangelog.wallet == "mind-learn")
                                .order_by(StratChangelog.ts.desc()).limit(100))).scalars().all()
        flags = {}
        for r in (await s.execute(select(MindModelState).where(MindModelState.model == code, MindModelState.coin == "*",
                                                               MindModelState.key.like("learn%")))).scalars().all():
            flags[r.key] = r.value_json
        rows = (await s.execute(text(
            "SELECT size_tier, r_multiple, t.stop_floor_applied, g.confirmation_used FROM strat_trades t "
            "JOIN strat_signals g ON g.id=t.signal_id "
            "WHERE t.model=:m AND t.mode='paper' AND t.exit_ts IS NOT NULL AND t.fill_ts IS NOT NULL"), {"m": code})).all()
        by_tier: dict = {}
        rs = [float(r[1]) for r in rows if r[1] is not None]
        for tier, r, _sf, _cu in rows:
            if r is None:
                continue
            d = by_tier.setdefault(tier or "?", {"trades": 0, "sum_r": 0.0, "wins": 0})
            d["trades"] += 1; d["sum_r"] += float(r); d["wins"] += 1 if r > 0 else 0
        r_by_tier = {k: {"trades": v["trades"], "mean_r": round(v["sum_r"] / v["trades"], 3),
                         "win_rate": round(v["wins"] / v["trades"], 3)} for k, v in by_tier.items() if v["trades"]}
        # spec v1.3 Part 5: outcome splits by stop floor (D-88, every model) and by reclaim
        # type (D-89, M1/M5 only — NULL confirmation_used elsewhere is skipped, never invented)
        splits = _split_rows(rows, 2, {1: "stop floor applied", 0: "structural stop kept"}, "stop_floor")
        if code in ("M1", "M5"):
            splits += _split_rows(rows, 3, {1: "same-candle + confirmation", 0: "later-candle reclaim"}, "reclaim_type")
        exec_rows = (await s.execute(text(
            "SELECT COUNT(*), SUM(entry_requoted = 1), "
            "SUM(entry_requoted = 1 AND fill_ts IS NOT NULL), "
            "AVG(CASE WHEN entry_requoted = 1 AND fill_ts IS NOT NULL AND entry_px_orig IS NOT NULL THEN entry_px - entry_px_orig END), "
            "AVG(CASE WHEN entry_requoted = 1 AND fill_ts IS NOT NULL AND entry_px_orig IS NOT NULL AND entry_px_orig <> 0 "
            "THEN (entry_px - entry_px_orig) / entry_px_orig * 10000 END) "
            "FROM strat_trades WHERE model=:m AND mode='paper' AND entry_requoted IS NOT NULL"), {"m": code})).first()
        n_all = int(exec_rows[0] or 0) if exec_rows else 0
        execution = ExecutionStats(
            takes=n_all, requoted=int(exec_rows[1] or 0) if exec_rows else 0,
            requoted_fraction=(round(int(exec_rows[1] or 0) / n_all, 4) if n_all else None),
            requoted_filled=int(exec_rows[2] or 0) if exec_rows else 0,
            fill_minus_original=(round(float(exec_rows[3]), 6) if exec_rows and exec_rows[3] is not None else None),
            fill_minus_original_bps=(round(float(exec_rows[4]), 3) if exec_rows and exec_rows[4] is not None else None),
        )
        norms = await _normaliser_rows(s, code)
    return MindBreakdown(
        model=code,
        weights=[MindWeightRow(reason_key=x.reason_key, weight=x.weight, reason_text=x.reason_text, updated_at=x.updated_at) for x in w],
        calibration=[MindCalibrationRow(week=x.week, bucket=x.bucket, trades=x.trades, win_rate=x.win_rate, mean_r=x.mean_r) for x in cal],
        weight_history=[ChangelogEntry(ts=h.ts, strategy_id=h.strategy_id, diff=h.diff, reason=h.reason, wallet=h.wallet) for h in hist],
        learn_flags=flags, trades_closed=len(rs), mean_r=(round(sum(rs) / len(rs), 3) if rs else None), r_by_tier=r_by_tier,
        normalisers=norms, splits=splits, execution=execution,
    )


def _split_rows(rows, idx: int, labels: dict, group: str) -> list:
    """Outcome rows (n / win rate / mean R) of closed trades grouped by a 0/1 column
    (MySQL BOOLEAN comes back 0/1 — compare with int, never `is True`)."""
    out = []
    for flag, label in labels.items():
        sel = [float(r[1]) for r in rows if r[1] is not None and r[idx] is not None and int(r[idx]) == flag]
        out.append(OutcomeSplit(group=group, label=label, trades=len(sel),
                                win_rate=(round(sum(1 for x in sel if x > 0) / len(sel), 3) if sel else None),
                                mean_r=(round(sum(sel) / len(sel), 3) if sel else None)))
    return out


# spec v1.1 Part C (D-66): which calibrated value each model's liquidation inputs divide by
_NORMALISER_USE = {   # keyed by model code (what get_breakdown resolves the strategy id to)
    "M1": {"liq_5m_p90": "fuel denominator (swept side)",
           "band_p80": "eligible cluster threshold; cluster_below_uncleared 50% reference"},
    "M2": {"liq_5m_p90": "cluster_cleared denominator (opposite side)"},
    "M3": {"band_p80": "cluster_fuel denominator; eligible cluster threshold"},
    "M4": {"band_p80": "T2 cluster threshold; cluster_reward denominator = 5 × band_p80"},
    "M5": {"liq_5m_p90": "fuel denominator = 0.8 × liq_5m_p90 (raided side)"},
    "M6": {},
}


async def _normaliser_rows(s, code: str) -> list[NormaliserRow]:
    from app.strategy_engine.structure import calibration as calib_mod
    from app.strategy_engine.mind import config as mind_cfg
    use = _NORMALISER_USE.get(code, {})
    out: list[NormaliserRow] = []
    for coin in mind_cfg.assets():
        vals = await calib_mod.load(s, coin)
        for key in calib_mod.KEYS:
            d = vals.get(key) or {}
            base = key.rsplit("_", 1)[0] if key.startswith("liq_5m_p90") else key
            used = use.get(base, "") if key != "live_coverage" else "live-feed liquidation rows scaled by 1/live_coverage where no 0xArchive row exists"
            if key != "live_coverage" and not used:
                used = "pools.py cluster eligibility (all models)" if key == "band_p80" else "not used by this model"
            out.append(NormaliserRow(coin=coin, key=key, value=d.get("value"), sample_count=int(d.get("sample_count") or 0),
                                     window_days=d.get("window_days"), computed_at=d.get("computed_at"),
                                     note=d.get("note"), used_by=used))
    return out


@router.get("/alerts/prefs")
async def get_alert_prefs(user: User = Depends(get_authenticated_user)) -> list[AlertPref]:
    """Per-model Telegram switches for the caller (doc 17 step 6). Absent row = enabled."""
    sf = get_session_factory()
    wallet = user.wallet_address.lower()
    async with sf() as s:
        rows = (await s.execute(select(StratAlertPref).where(StratAlertPref.wallet == wallet))).scalars().all()
    have = {r.model: bool(r.enabled) for r in rows}
    return [AlertPref(model=code, enabled=have.get(code, True)) for code in _MODEL_OF.values()]


@router.put("/alerts/prefs")
async def put_alert_prefs(body: AlertPrefsPatch, user: User = Depends(get_authenticated_user)) -> list[AlertPref]:
    sf = get_session_factory()
    wallet = user.wallet_address.lower()
    valid = set(_MODEL_OF.values())
    async with sf() as s:
        for p in body.prefs:
            if p.model not in valid:
                raise HTTPException(422, f"unknown model {p.model}")
            row = (await s.execute(select(StratAlertPref).where(StratAlertPref.wallet == wallet, StratAlertPref.model == p.model))).scalars().first()
            if row:
                row.enabled = bool(p.enabled)
            else:
                s.add(StratAlertPref(wallet=wallet, model=p.model, enabled=bool(p.enabled)))
        await s.commit()
    return await get_alert_prefs(user)


@router.get("/{sid}/signals")
async def get_signals(sid: str, limit: int = 100) -> list[Signal]:
    if sid not in _NAMES:
        raise HTTPException(404, "unknown strategy")
    sf = get_session_factory()
    async with sf() as s:
        rows = (await s.execute(select(StratSignal).where(StratSignal.strategy == sid).order_by(StratSignal.ts.desc()).limit(min(limit, 500)))).scalars().all()
        return [_sig(r) for r in rows]


@router.get("/{sid}/trades")
async def get_trades(sid: str, limit: int = 100) -> list[Trade]:
    if sid not in _NAMES:
        raise HTTPException(404, "unknown strategy")
    sf = get_session_factory()
    async with sf() as s:
        rows = (await s.execute(select(StratTrade).where(StratTrade.strategy == sid).order_by(StratTrade.id.desc()).limit(min(limit, 500)))).scalars().all()
        return [_trade(r) for r in rows]


@router.get("/{sid}/backtest")
async def run_backtest(sid: str, days_back: int = 365, refresh: bool = False) -> dict:
    """Coverage-honest backtest report for one strategy. The worker replays
    every strategy at startup and daily (backtest/results/{sid}.md); this
    serves that cache when it is < 24h old, otherwise replays inline. Series
    that predate their feeds evaluate as 'unavailable (not in score)' with the
    coverage stated in the report header. Read-only; places nothing."""
    if sid not in _NAMES:
        raise HTTPException(404, "unknown strategy")
    if sid in _MODEL_OF:
        raise HTTPException(404, "models M1-M6 run forward in paper only; no replay backtest exists (doc 17)")
    from app.strategy_engine.backtest.runner import run_report, cached_report
    if not refresh:
        c = cached_report(sid)
        if c:
            return {"strategy": sid, "days_back": days_back, "report": c["report"], "cached": True,
                    "generated_ts": c["generated_ts"], "coverage": c.get("coverage", {})}
    now = int(time.time() * 1000)
    start = now - max(1, min(days_back, 1095)) * _D
    sf = get_session_factory()
    async with sf() as s:
        report = await run_report(s, sid, start, now)
    c = cached_report(sid) or {}
    return {"strategy": sid, "days_back": days_back, "report": report, "cached": False,
            "generated_ts": c.get("generated_ts"), "coverage": c.get("coverage", {})}


@router.get("/{sid}/parameters")
async def get_parameters(sid: str) -> list[Parameter]:
    if sid not in _NAMES:
        raise HTTPException(404, "unknown strategy")
    sf = get_session_factory()
    async with sf() as s:
        rows = (await s.execute(select(StratParameter).where(StratParameter.strategy_id == sid).order_by(StratParameter.key))).scalars().all()
        return [Parameter(key=p.key, value=p.value, unit=p.unit, default=p.default) for p in rows]


@router.get("/{sid}/changelog")
async def get_changelog(sid: str, limit: int = 100) -> list[ChangelogEntry]:
    sf = get_session_factory()
    async with sf() as s:
        rows = (await s.execute(select(StratChangelog).where(StratChangelog.strategy_id == sid).order_by(StratChangelog.ts.desc()).limit(min(limit, 500)))).scalars().all()
        return [ChangelogEntry(ts=r.ts, strategy_id=r.strategy_id, diff=r.diff, reason=r.reason, wallet=r.wallet) for r in rows]


@router.patch("/{sid}/mode")
async def patch_mode(sid: str, body: ModePatch, admin: User = Depends(require_admin)) -> StrategyState:
    if sid not in _NAMES:
        raise HTTPException(404, "unknown strategy")
    if body.requested_mode not in _VALID_MODES:
        raise HTTPException(422, "invalid mode")
    if body.requested_mode == "live":
        raise HTTPException(409, mode_mod.LIVE_DISABLED_REASON)
    sf = get_session_factory()
    now_ms = int(time.time() * 1000)
    async with sf() as s:
        row = (await s.execute(select(StratStrategyState).where(StratStrategyState.strategy_id == sid))).scalars().first()
        if not row:
            raise HTTPException(404, "strategy state not found")
        before = row.requested_mode
        row.requested_mode = body.requested_mode      # ONLY requested is written; effective is computed
        s.add(StratChangelog(ts=now_ms, strategy_id=sid,
                             diff={"requested_mode": {"from": before, "to": body.requested_mode}},
                             reason="mode change", wallet=admin.wallet_address.lower()))
        params = (await s.execute(select(StratParameter).where(StratParameter.strategy_id == sid))).scalars().all()
        await s.commit()
        await s.refresh(row)
        counts = await _sig_counts(s, now_ms)
        return await _build_state(s, row, len(params), counts, now_ms)


@router.patch("/{sid}/parameters")
async def patch_parameter(sid: str, body: ParameterPatch, admin: User = Depends(require_admin)) -> Parameter:
    if sid not in _NAMES:
        raise HTTPException(404, "unknown strategy")
    if not body.reason or not body.reason.strip():
        raise HTTPException(422, "a reason is required for a parameter change")
    sf = get_session_factory()
    async with sf() as s:
        p = (await s.execute(select(StratParameter).where(
            StratParameter.strategy_id == sid, StratParameter.key == body.key))).scalars().first()
        if not p:
            raise HTTPException(404, "unknown parameter")
        before = p.value
        p.value = body.value
        s.add(StratChangelog(ts=int(time.time() * 1000), strategy_id=sid,
                             diff={body.key: {"from": before, "to": body.value}},
                             reason=body.reason.strip(), wallet=admin.wallet_address.lower()))
        await s.commit()
        return Parameter(key=p.key, value=p.value, unit=p.unit, default=p.default)
