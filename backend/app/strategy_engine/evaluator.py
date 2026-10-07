"""Evaluation runner. Called by the worker scheduler.

Every registered strategy evaluates on its cadence, ALWAYS (no mode gate, no
history gate): build the StrategyContext from the strat_ / analytics tables,
evaluate, log every evaluation to strat_signals (conditions + warming labels +
score components + the waiting-for sentence), emit Telegram-outbox alerts in the
doc formats, and route FIRED intents through RiskEngine.size_for →
ExecutionRouter(paper) → PaperExecutor / PaperTradeManager.

Paper = paper: the risk engine still computes size (1.5% risk, 3x cap) and
records every rule outcome, but a rule that would have blocked is written on the
signal as "risk engine would have: <rule>" and the paper trade proceeds. The only
thing that stops a paper fill is requested_mode == 'off' (evaluations continue).
Nothing reaches a venue — the router has only paper + null adapters.
"""
from __future__ import annotations

import datetime as _dt
import json
import logging
import time

from sqlalchemy import select, text
from sqlalchemy.dialects.mysql import insert as mysql_insert

from app.config import settings
from app.db.strategy_models import StratSignal, StratStrategyState, StratTelegramOutbox
from . import mode as mode_mod
from .risk import engine as risk
from .execution.paper import PaperExecutor
from .execution.router import ExecutionRouter
from .execution.manager import PaperTradeManager
from .strategies import cohort as cohort_mod
from .strategies.base import StrategyContext
from .strategies.s05_session_open import SessionOpenMomentum
from .strategies.s01_liq_sweep import LiqSweepReversal
from .strategies.s04_vol_compression import VolCompressionBreakout
from .strategies.s02_funding_flow import FundingSettlementFlow
from .strategies.s03_whale_follow import WhaleFollow
from .strategies.s06_hull_fisher_ema import HullFisherEma

logger = logging.getLogger("strategy_engine.evaluator")

# Registry — id -> instance. D-99: s05c (SessionOpenMomentum(chase=True)) is
# permanently disabled and NOT constructed here; the class keeps its chase code
# path (nothing is deleted) so its trade history and the backtest runner still
# resolve, but the scheduler never sees the row again.
from .mode import DISABLED_STRATEGIES

_ALL = [
    SessionOpenMomentum(), SessionOpenMomentum(chase=True), LiqSweepReversal(), VolCompressionBreakout(),
    FundingSettlementFlow(), WhaleFollow(), HullFisherEma(gated=True), HullFisherEma(gated=False),
]
REGISTRY = {s.id: s for s in _ALL if s.id not in DISABLED_STRATEGIES}

_H = 3_600_000


def _trunc(s: str, n: int = 255) -> str:
    return s if len(s) <= n else s[: n - 1] + "…"


def _py(v):
    """numpy scalars (bool_, float64, int64) out of the indicator code are not
    JSON-serialisable and pymysql escapes np.bool_ as the string 'True' —
    coerce everything that goes into strat_signals to plain Python."""
    if isinstance(v, dict):
        return {k: _py(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [_py(x) for x in v]
    if v is None or type(v) in (str, bool, int, float):
        return v
    item = getattr(v, "item", None)               # numpy generic (incl. float64, a float subclass) → python scalar
    if callable(item):
        try:
            return _py(item())
        except Exception:  # noqa: BLE001
            pass
    if isinstance(v, bool):
        return bool(v)
    if isinstance(v, int):
        return int(v)
    if isinstance(v, float):
        return float(v)
    if isinstance(v, str):
        return str(v)
    return str(v)


class Evaluator:
    def __init__(self, session_factory, assets: list[str]) -> None:
        self._sf = session_factory
        self._assets = assets
        self._paper = PaperExecutor()
        self._router = ExecutionRouter(self._paper)
        self._mgr = PaperTradeManager(assets)
        self._order_seq = 0
        self._last_minute: dict[str, int] = {}
        self._funding_rows: dict[str, tuple[int, int]] = {}   # coin -> (ts, distinct hours)

    async def _ctx(self, s, coin: str, params: dict, now_ms: int, cohort: dict | None) -> StrategyContext:
        async def rows(sql, **kw):
            return (await s.execute(text(sql), {"c": coin, **kw})).mappings().all()

        c15 = [dict(r) for r in await rows("SELECT ts,o,h,l,c,v FROM strat_candles WHERE coin=:c AND tf='15m' ORDER BY ts DESC LIMIT 300")][::-1]
        ohlc_30d = [dict(r) for r in await rows("SELECT h,l,c FROM strat_candles WHERE coin=:c AND tf='15m' ORDER BY ts DESC LIMIT 3000")][::-1]
        c15_30d = [float(r["c"]) for r in ohlc_30d]
        c1h = [dict(r) for r in await rows("SELECT ts,o,h,l,c,v FROM strat_candles WHERE coin=:c AND tf='1h' ORDER BY ts DESC LIMIT 60")][::-1]
        c4h = [dict(r) for r in await rows("SELECT ts,o,h,l,c,v FROM strat_candles WHERE coin=:c AND tf='4h' ORDER BY ts DESC LIMIT 60")][::-1]
        oi = [dict(r) for r in await rows("SELECT ts,oi_notional,funding,mark,oracle FROM strat_oi_1m WHERE coin=:c ORDER BY ts DESC LIMIT 1500")][::-1]
        tr = [dict(r) for r in await rows("SELECT ts,taker_buy_notional,taker_sell_notional,count FROM strat_trades_1m WHERE coin=:c ORDER BY ts DESC LIMIT 400")][::-1]
        bk = [dict(r) for r in await rows("SELECT ts,mid,bid_0_1,bid_0_3,bid_0_5,ask_0_1,ask_0_3,ask_0_5 FROM strat_book_5s WHERE coin=:c ORDER BY ts DESC LIMIT 2200")][::-1]
        gauge = next(iter([dict(r) for r in await rows("SELECT * FROM strat_gauge WHERE coin=:c ORDER BY ts DESC LIMIT 1")]), None)
        regime = next(iter([dict(r) for r in await rows("SELECT * FROM strat_regime WHERE coin=:c ORDER BY ts DESC LIMIT 1")]), None)
        bias = next(iter([dict(r) for r in await rows("SELECT * FROM strat_bias WHERE coin=:c ORDER BY ts DESC LIMIT 1")]), None)
        liqs = [dict(r) for r in await rows("SELECT ts,side,px,sz,notional,liquidated_user,method,coverage FROM strat_liquidations WHERE coin=:c AND ts>=:a ORDER BY ts", a=now_ms - 10 * 60_000)]
        # tracked-cohort liquidation map (latest analytics cycle) for s01 "no next cluster"
        clusters = [dict(r) for r in await rows(
            "SELECT liq_px, notional, side FROM analytics_positions WHERE asset=:c AND liq_px IS NOT NULL "
            "AND cycle_ts=(SELECT MAX(cycle_ts) FROM analytics_positions)")]
        # HL funding history rows (z-score warming: 720 hourly rows = 30d)
        fr = self._funding_rows.get(coin)
        if not fr or now_ms - fr[0] > 10 * 60_000:
            n = (await s.execute(text(
                "SELECT COUNT(DISTINCT FLOOR(ts/3600000)) FROM strat_funding WHERE coin=:c AND venue='hl'"), {"c": coin})).scalar()
            fr = (now_ms, int(n or 0))
            self._funding_rows[coin] = fr
        coh = None
        if cohort is not None:
            try:
                sig = await cohort_mod.cohort_signal(s, cohort, coin, now_ms)
                coh = {**sig, "n_wallets": len(cohort.get("wallets", [])), "label": cohort.get("label", "")}
            except Exception:  # noqa: BLE001
                logger.exception("cohort_signal %s failed", coin)
        return StrategyContext(coin=coin, now_ms=now_ms, params=params, candles_15m=c15, closes_15m_30d=c15_30d, ohlc_15m_30d=ohlc_30d, candles_1h=c1h,
                               candles_4h=c4h, oi_1m=oi, trades_1m=tr, book_5s=bk, book_last=(bk[-1] if bk else None),
                               gauge=gauge, regime=regime, bias=bias, liquidations=liqs,
                               liq_coverage=(liqs[-1].get("coverage") or "partial") if liqs else "partial",
                               liq_clusters=clusters, cohort=coh, funding_rows=fr[1])

    async def _params(self, s, sid: str) -> dict:
        rows = (await s.execute(text("SELECT `key`,value FROM strat_parameters WHERE strategy_id=:s"), {"s": sid})).all()
        return {k: v for k, v in rows}

    async def run(self, on_candle_boundary: bool) -> None:
        now_ms = int(time.time() * 1000)
        minute = now_ms // 60_000
        async with self._sf() as s:
            # Manage the paper trade lifecycle first (fills, stops, targets, time
            # stops) so new fires see the current open positions.
            try:
                await self._mgr.manage(s, now_ms)
            except Exception:  # noqa: BLE001 — lifecycle fault never stops evaluation
                logger.exception("paper trade manage() failed")
            cohort = None
            try:
                cohort = await cohort_mod.build_cohort(s, now_ms)
            except Exception:  # noqa: BLE001
                logger.exception("cohort build failed")
            states = {r.strategy_id: r for r in (await s.execute(select(StratStrategyState))).scalars().all()}
            for sid, strat in REGISTRY.items():
                st = states.get(sid)
                if not st:
                    continue
                if strat.cadence == "candle_15m" and not on_candle_boundary:
                    continue
                if strat.cadence == "minute" and self._last_minute.get(sid) == minute:
                    continue
                self._last_minute[sid] = minute
                params = await self._params(s, sid)
                eff, reason = mode_mod.compute_effective_mode(st.requested_mode)
                st.effective_mode = eff
                st.effective_reason = reason
                last_sentence = None
                for coin in self._assets:
                    try:
                        ctx = await self._ctx(s, coin, params, now_ms, cohort if sid == "s03_whale_follow" else None)
                        res = strat.evaluate(ctx)
                        await self._log(s, sid, coin, res, now_ms, st, paper_fills=(st.requested_mode != "off"))
                        last_sentence = res.waiting_for
                    except Exception:  # noqa: BLE001 — one coin/strategy fault never stops the rest
                        logger.exception("evaluate %s %s failed", sid, coin)
                if last_sentence:
                    st.waiting_for_sentence = _trunc(last_sentence)
                st.last_eval_ts = now_ms
            await s.commit()

    async def _log(self, s, sid: str, coin: str, res, now_ms: int, st, paper_fills: bool = True) -> None:
        comp = res.components
        risk_note = None
        if res.fired and res.intents and paper_fills:
            risk_note = await self._risk_note(s, sid, coin, res, now_ms)
        reason = res.waiting_for
        if risk_note:
            reason = f"{reason} · risk engine would have: {risk_note}"
        comp = _py(comp)
        sig = StratSignal(
            ts=now_ms, strategy=sid, asset=coin, venue="hl", mode="paper", direction=_py(res.direction),
            regime_score=comp.get("regime"), bias_score=comp.get("bias"), trigger_score=comp.get("trigger"),
            timing_score=comp.get("timing"), total_score=_py(res.total_score), fired=bool(res.fired),
            reason=_trunc(reason),
            components=_py({"conditions": [c.as_dict() for c in res.conditions],
                        "components": comp, "not_evaluated": res.not_evaluated,
                        "warming": res.warming, "labels": res.labels, "risk_note": risk_note,
                        "fire_threshold": res.fire_threshold,
                        "intents": [{"side": i.side, "kind": i.kind, "px": i.px,
                                     "stop": i.meta.get("stop"), "target1": i.meta.get("target1"),
                                     "time_stop_h": i.meta.get("time_stop_h"),
                                     "entry_valid_min": i.meta.get("entry_valid_min"),
                                     "size_mult": i.meta.get("size_mult")} for i in res.intents if i.kind == "entry"],
                        "paper_fill": bool(res.fired and paper_fills)}))
        s.add(sig)
        for kind, txt in res.alerts:
            dedupe = f"{sid}:{coin}:{kind}:{now_ms // _H}"
            stmt = mysql_insert(StratTelegramOutbox).values(
                ts=now_ms, kind=f"s_{kind}", strategy_id=sid, coin=coin, message=txt, dedupe_key=dedupe, sent=False)
            stmt = stmt.on_duplicate_key_update(ts=now_ms)
            await s.execute(stmt)
        if res.fired and res.intents:
            await s.flush()                       # assign sig.id for the trade link
            if paper_fills:
                await self._route(s, sid, coin, res, now_ms, sig.id)
            else:
                logger.info("fire logged, no paper fill (requested_mode=off): %s %s", sid, coin)

    async def _risk_state(self, s):
        # D-100: daily_cap_hit_until / paused_until are now READ (they used to be
        # hardcoded None, which made can_open() structurally unable to refuse).
        rs = (await s.execute(text(
            "SELECT equity_usd,daily_realized_pnl,consecutive_losses,daily_cap_hit_until,paused_until "
            "FROM strat_risk_state LIMIT 1"))).first()
        equity = (rs[0] if rs and rs[0] else settings.STRATEGY_PAPER_EQUITY_USD)
        openrows = (await s.execute(text(
            "SELECT strategy,asset,direction FROM strat_trades WHERE mode='paper' AND exit_ts IS NULL"))).all()
        g = risk.GlobalRisk(equity_usd=equity,
                            daily_realized_pnl=(rs[1] if rs else 0.0) or 0.0,
                            daily_cap_hit_until=(int(rs[3]) if rs and rs[3] else None),
                            consecutive_losses=(rs[2] if rs else 0) or 0,
                            paused_until=(int(rs[4]) if rs and rs[4] else None),
                            open_positions=tuple(risk.OpenPos(o[0], o[1], o[2]) for o in openrows))
        return g, equity

    async def _strat_risk(self, s, sid: str) -> "risk.StratRisk":
        """Per-strategy risk state from the DB: the persisted kill switch plus the
        rolling closed-trade PnL window."""
        row = (await s.execute(text(
            "SELECT kill_switch_tripped, effective_reason FROM strat_strategy_state WHERE strategy_id=:s"),
            {"s": sid})).first()
        pnls = [float(r[0]) for r in (await s.execute(text(
            "SELECT pnl_net FROM strat_trades WHERE strategy=:s AND exit_ts IS NOT NULL "
            "AND fill_ts IS NOT NULL ORDER BY exit_ts DESC LIMIT 20"), {"s": sid})).all()][::-1]
        return risk.StratRisk(sid, paper_paused=bool(row[0]) if row else False,
                              paused_reason=(row[1] if row else None), recent_pnls=tuple(pnls))

    async def _risk_note(self, s, sid: str, coin: str, res, now_ms: int) -> str | None:
        """Every risk rule outcome for this fire, as an annotation (never a block)."""
        g, equity = await self._risk_state(s)
        pnls = [float(r[0]) for r in (await s.execute(text(
            "SELECT pnl_net FROM strat_trades WHERE strategy=:s AND exit_ts IS NOT NULL AND fill_ts IS NOT NULL "
            "ORDER BY exit_ts DESC LIMIT 20"), {"s": sid})).all()]
        pf = risk.rolling_pf(tuple(pnls))
        notes = []
        ok, reason = risk.can_open(g, risk.StratRisk(sid, recent_pnls=tuple(pnls)), coin, res.direction, now_ms)
        if not ok:
            notes.append(reason)
        if g.daily_realized_pnl <= -risk.DAILY_CAP_PCT / 100.0 * equity:
            notes.append(f"daily cap ({risk.DAILY_CAP_PCT}% of ${equity:,.0f}) hit")
        if g.consecutive_losses >= risk.CONSEC_LOSS_LIMIT:
            notes.append(f"{g.consecutive_losses} consecutive losses → 4h pause")
        if pf is not None and len(pnls) >= risk.ROLLING_PF_WINDOW and pf < risk.ROLLING_PF_MIN:
            notes.append(f"rolling20 PF {pf:.2f} < {risk.ROLLING_PF_MIN} → paper-pause")
        seen = []
        for n in notes:
            if n not in seen:
                seen.append(n)
        return "; ".join(seen) if seen else None

    async def _route(self, s, sid: str, coin: str, res, now_ms: int, signal_id) -> None:
        """Fired entry intent → risk gate → size_for (1.5% risk, 3x cap) →
        persistent paper trade (PaperTradeManager).

        D-100: the risk rules are ENFORCED here. can_open() blocks a paper entry
        exactly as it would block a live one — kill switch, daily loss cap, the
        4h consecutive-loss pause, max concurrent positions and the duplicate
        direction rule. The executor is now the only paper/live difference.
        A blocked entry is logged and recorded, never silently dropped."""
        g, equity = await self._risk_state(s)
        sr = await self._strat_risk(s, sid)
        allowed, why = risk.can_open(g, sr, coin, str(res.direction), now_ms)
        if not allowed:
            logger.warning("paper entry BLOCKED by risk engine [%s %s]: %s", sid, coin, why)
            await s.execute(text(
                "INSERT INTO strat_changelog (ts, strategy_id, diff, reason, wallet) "
                "VALUES (:ts,:s,:d,:r,'risk-engine')"),
                {"ts": now_ms, "s": sid,
                 "d": json.dumps({"blocked": True, "coin": coin, "direction": str(res.direction),
                                  "signal_id": signal_id, "rule": why}),
                 "r": f"risk engine BLOCKED paper entry: {why}"})
            return
        for intent in res.intents:
            if intent.kind != "entry":
                continue                          # stops/targets are managed per-position
            px = float(intent.px or 0.0)
            stop = float(intent.meta.get("stop", intent.px) or px)
            target = intent.meta.get("target1")
            target = float(target) if target is not None else None
            time_stop_h = int(intent.meta.get("time_stop_h", 3))
            sized = risk.size_for(px, stop, equity)
            mult = float(intent.meta.get("size_mult", 1.0) or 1.0)
            await self._mgr.open_from_intent(
                s, sid, coin, signal_id, str(res.direction), entry_px=px,
                stop_px=stop, target_px=target, size=float(sized.size * mult), leverage=float(sized.leverage),
                time_stop_h=time_stop_h, venue=intent.venue, now_ms=now_ms,
                entry_valid_min=intent.meta.get("entry_valid_min"), chase=bool(intent.meta.get("chase")))
            g = risk.open_after_fill(g, risk.OpenPos(sid, coin, res.direction))
