"""Model runner for M1–M6 (docs 10–17). Paper only — no venue client is imported.

ModelEvaluator   : every closed 15m, ONE Snapshot per coin (structure + feeds +
                   cohort), six Minds evaluate it, one strat_signals row per model
                   per coin (D-09) with the Mind fields, alerts → outbox, state →
                   mind_model_state, TAKE → a pending post-only paper trade.
ModelTradeManager: lifecycle of model trades (rows with strat_trades.model set,
                   D-02): pending fill / fallback / cancel, stop, T1 partial + BE,
                   T2/T3, trail (D-11), hard stop, Mind.manage in-trade checks each
                   closed 15m, r_multiple on exit (D-12).
Scheduled jobs   : 07:00/14:00 day-type freeze (D-23), Asia range 07:00 + weekly
                   levels Monday 00:00 (D-07), mind/learn Sunday 00:30.
"""
from __future__ import annotations

import dataclasses
import datetime as _dt
import json
import logging
import math
import time
from typing import Optional

import numpy as np
from sqlalchemy import text
from sqlalchemy.dialects.mysql import insert as mysql_insert

from app.config import settings
from app.db.strategy_models import MindModelState, MindWeight, StratSignal, StratTelegramOutbox

from .execution.manager import PaperTradeManager, _tick
from .execution.paper import MAKER_FEE, TAKER_FEE, fee_for, limit_fills_through, post_only_place, stop_triggered
from .mind import config as mind_cfg
from .mind import learn as learn_mod
from .mind.base import Position
from .mind.snapshot import Snapshot, build_snapshot
from .risk import engine as risk
from .strategies import cohort as cohort_mod
from .structure import calibration as calib_mod
from .strategies import model_base as mb
from .strategies.m1_sweep_reclaim import M1SweepReclaim
from .strategies.m2_bos_order_block import M2BreakOfStructure
from .strategies.m3_failed_auction import M3FailedAuction
from .strategies.m4_htf_choch import M4ChangeOfCharacter
from .strategies.m5_session_liquidity_run import M5SessionLiquidityRun
from .strategies.m6_weekly_open_reclaim import M6WeeklyOpenReclaim
from .strategies.model_base import ModelStrategy, fmt_px
from .structure.alignment import format_table
from .structure.candles import TF_MS, to_candles
from .structure.sessions import day_start, weekday

logger = logging.getLogger("strategy_engine.models")

MIN_MS = 60_000
H_MS = 3_600_000
DAY_MS = 24 * H_MS
VENUE = "hl"
MAX_LEVERAGE = 3.0
# models whose doc says "trail ... INSTEAD of fixed T2" once the trail is armed
TRAIL_REPLACES_T2 = {"M2", "M5"}
# M1 second attempt (doc 11): the FVG-mid order waits for price to come back for
# one expected hold after the first order expires (D-30)
M1_FALLBACK_WAIT_CANDLES = 6

MODEL_REGISTRY: dict[str, ModelStrategy] = {m.id: m for m in [
    M1SweepReclaim(), M2BreakOfStructure(), M3FailedAuction(),
    M4ChangeOfCharacter(), M5SessionLiquidityRun(), M6WeeklyOpenReclaim(),
]}
MODEL_BY_CODE: dict[str, ModelStrategy] = {m.model: m for m in MODEL_REGISTRY.values()}


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def _py(v):
    """numpy / Decimal → plain python for JSON + bind params."""
    if isinstance(v, np.generic):
        return v.item()
    return v


def jsonable(obj):
    """Recursive JSON-safe copy of a setup/lifecycle dict (numpy scalars, tuples,
    dataclasses with as_dict, NaN → None)."""
    if obj is None or isinstance(obj, (bool, str, int)):
        return obj
    if isinstance(obj, np.generic):
        obj = obj.item()
        if isinstance(obj, (bool, int)):
            return obj
    if isinstance(obj, float):
        return None if (obj != obj or obj in (float("inf"), float("-inf"))) else obj
    if isinstance(obj, dict):
        return {str(k): jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple, set)):
        return [jsonable(v) for v in obj]
    if hasattr(obj, "as_dict"):
        return jsonable(obj.as_dict())
    if dataclasses.is_dataclass(obj):
        return jsonable(dataclasses.asdict(obj))
    return str(obj)


def _loads(v):
    if v is None:
        return None
    if isinstance(v, (dict, list)):
        return v
    try:
        return json.loads(v)
    except (TypeError, ValueError):
        return None


def _ms_to_dt(ms: int) -> _dt.datetime:
    return _dt.datetime.fromtimestamp(ms / 1000.0, tz=_dt.timezone.utc).replace(tzinfo=None)


def _exit_side(direction: str) -> str:
    return "sell" if direction == "long" else "buy"


def _entry_side(direction: str) -> str:
    return "buy" if direction == "long" else "sell"


async def outbox(s, now_ms: int, kind: str, sid: str, coin: str, message: str, dedupe_key: str) -> None:
    stmt = mysql_insert(StratTelegramOutbox).values(
        ts=now_ms, kind=kind[:32], strategy_id=sid[:32], coin=coin, message=message,
        dedupe_key=dedupe_key[:128], sent=False)
    await s.execute(stmt.on_duplicate_key_update(ts=now_ms))


async def upsert_state(s, model: str, coin: str, key: str, value, now_ms: int) -> None:
    v = jsonable(value)
    stmt = mysql_insert(MindModelState).values(model=model, coin=coin, key=key[:48], value_json=v, updated_ts=now_ms)
    await s.execute(stmt.on_duplicate_key_update(value_json=v, updated_ts=now_ms))


async def load_state(s, model: str, coin: str) -> dict:
    """mind_model_state rows for (model|'*', coin|'*') merged; model/coin-specific wins."""
    rows = (await s.execute(text(
        "SELECT model, coin, `key`, value_json FROM mind_model_state "
        "WHERE model IN (:m, '*') AND coin IN (:c, '*')"), {"m": model, "c": coin})).all()
    rows.sort(key=lambda r: (r[0] != "*", r[1] != "*"))   # '*' first so specific overrides
    out: dict = {}
    for _m, _c, k, v in rows:
        out[str(k)] = _loads(v) if isinstance(v, str) else v
    return out


def _feed_vetoes(snap: Snapshot) -> list[tuple[str, str]]:
    """D-31 / D-51: only OI, taker delta and candles are REQUIRED feeds — their
    absence is a recorded veto, never an assumed pass. Everything else (book,
    liquidations, gauge, cohort) is optional: a missing optional feed sets the
    affected reason to 0 and never vetoes (audit D-51 removed `feed_missing_book`;
    a resting paper order simply waits for the mid tape to resume)."""
    out: list[tuple[str, str]] = []
    cutoff = snap.now_ms - H_MS
    if not any(int(r["ts"]) > cutoff for r in (snap.oi_rows or [])):
        out.append(("feed_missing_oi", "no OI rows in the last hour — OI gates unverifiable"))
    if not any(int(r["ts"]) > cutoff for r in (snap.trades_rows or [])):
        out.append(("feed_missing_taker", "no taker-flow rows in the last hour — delta gates unverifiable"))
    return out


# ---------------------------------------------------------------------------
# evaluator
# ---------------------------------------------------------------------------
class ModelEvaluator:
    def __init__(self, session_factory, assets: list[str]) -> None:
        self._sf = session_factory
        self._assets = [a.upper() for a in (assets or mind_cfg.assets())]
        self._mgr = ModelTradeManager(self._assets)
        self._weights_loaded = False
        self._last_table_hour: Optional[int] = None
        self._last_15m: Optional[int] = None
        self._last_jobs_15m: Optional[int] = None
        self._snapshots: dict[str, Snapshot] = {}
        # spec v1.1 Part C (D-66): calibrated liquidation normalisers per coin
        self._calib: dict[str, dict] = {}
        self._calib_ts: Optional[int] = None          # last daily recompute (00:05 UTC / first run)
        self._calib_seen: dict[str, Optional[int]] = {}   # replay: last as_of logged per coin (D-76)
        self._coverage_ts: Optional[int] = None       # last weekly live_coverage recompute
        # replay-only (D-68): feeds absent for the whole replay are excluded from conviction,
        # their vetoes skipped as unevaluated; never set in the live worker
        self.replay_mode = False
        self.replay_unavailable: set[str] = set()
        self._replay_first_liq: dict[str, Optional[int]] = {}
        self._replay_first_event: Optional[int] = None
        for m in MODEL_REGISTRY.values():
            cfg = mind_cfg.model_cfg(m.model)
            if cfg.get("mode", "paper") != "paper":
                raise RuntimeError(f"models.yaml: {m.model} mode={cfg.get('mode')!r} — only 'paper' exists (D-22)")

    # ---- weights -------------------------------------------------------------
    async def ensure_weights(self, s, now_ms: int) -> None:
        if self._weights_loaded:
            return
        for m in MODEL_REGISTRY.values():
            rows = (await s.execute(text(
                "SELECT reason_key, weight FROM mind_weights WHERE model=:m"), {"m": m.model})).all()
            have = {str(r[0]): float(r[1]) for r in rows}
            if have:
                m.mind.apply_weights(have)
            for r in m.mind.reasons:
                if r.key not in have:
                    stmt = mysql_insert(MindWeight).values(model=m.model, reason_key=r.key, weight=r.weight,
                                                           updated_at=now_ms, reason_text=r.description[:255])
                    await s.execute(stmt.on_duplicate_key_update(reason_text=r.description[:255]))
        self._weights_loaded = True

    # ---- calibration (spec v1.1 Part C) ------------------------------------------
    async def ensure_calibration(self, s, now_ms: int) -> None:
        """Daily at 00:05 UTC and on first run: liq_5m_p90 (both sides) + band_p80;
        weekly (first boundary of the ISO week): live_coverage. Values are logged
        by calibration.recompute and shown on each model's Breakdown tab."""
        daily_keys = tuple(k for k in calib_mod.KEYS if k != "live_coverage")
        try:
            if self.replay_mode:
                # spec v1.2 Part 4 (D-76): point-in-time lookup only — the history is built
                # before the replay from data strictly before each day; nothing is recomputed
                # inside the replay and no future row can be read
                for coin in self._assets:
                    hist = await calib_mod.load_as_of(s, coin, now_ms)
                    cov = (await calib_mod.load(s, coin)).get("live_coverage")
                    if cov is not None:
                        hist["live_coverage"] = cov
                    self._calib[coin] = hist
                    self._log_calib_lookup(coin, now_ms, hist)
                return
            if calib_mod.is_due(now_ms, self._calib_ts):
                keys = calib_mod.KEYS if self._calib_ts is None else daily_keys
                if self._calib_ts is None:
                    await self._refresh_archive(s, now_ms)
                await calib_mod.recompute(s, self._assets, now_ms, keys=keys)
                self._calib_ts = now_ms
                self._coverage_ts = now_ms
            elif calib_mod.coverage_due(now_ms, self._coverage_ts):
                await self._refresh_archive(s, now_ms)
                await calib_mod.recompute(s, self._assets, now_ms, keys=("live_coverage",))
                self._coverage_ts = now_ms
            # D-104: independent of the weekly cadence, keep the archive within
            # 15 minutes of the tape and shout if it drifts past an hour.
            await self._archive_incremental(s, now_ms)
            # D-106: daily summary at 00:05 UTC (self-rate-limited by day label).
            try:
                from . import daily_summary as _ds
                sent = await _ds.maybe_send(s, now_ms, getattr(self, "_summary_day", None))
                if sent:
                    self._summary_day = sent
            except Exception as e:  # noqa: BLE001 — a report must never break a boundary
                logger.warning("daily summary failed: %s", e)
            for coin in self._assets:
                self._calib[coin] = await calib_mod.load(s, coin)
        except Exception as e:  # noqa: BLE001 — models fall back to the fixed OI fractions (logged)
            logger.exception("models: calibration failed: %s", e)
            await s.rollback()

    def _log_calib_lookup(self, coin: str, now_ms: int, hist: dict) -> None:
        """Replay: one INFO line per coin per as_of day, one WARNING per boundary whose
        lookup has a NULL value or no row (D-76: the report counts these)."""
        nulls = [k for k in calib_mod.HIST_KEYS if (hist.get(k) or {}).get("value") is None]
        as_of = max((int(v.get("as_of") or 0) for k, v in hist.items() if k in calib_mod.HIST_KEYS), default=0)
        if nulls:
            logger.warning("calibration lookup NULL %s @ %s: %s (as_of %s)", coin, mb.utc_str(now_ms), ",".join(nulls),
                           mb.utc_str(as_of) if as_of else "none")
        seen = self._calib_seen.setdefault(coin, None)
        if as_of and as_of != seen:
            self._calib_seen[coin] = as_of
            logger.info("calibration lookup %s @ %s: as_of %s %s", coin, mb.utc_str(now_ms), mb.utc_str(as_of),
                        ", ".join(f"{k}={calib_mod._fmt((hist.get(k) or {}).get('value'))}" for k in calib_mod.HIST_KEYS))

    async def _refresh_archive(self, s, now_ms: int) -> None:
        """Weekly (and first-run) 0xArchive pull of the coverage window so
        live_coverage compares the live feed against fresh archive rows (D-69).
        No key → nothing loaded, coverage stays NULL (logged by the loader)."""
        from .data.oxarchive import load_liquidations
        rep = await load_liquidations(s, list(self._assets), days=calib_mod.COVERAGE_DAYS + 1, now_ms=now_ms)
        if rep.get("status") != "key_missing":
            logger.info("models: 0xArchive coverage refresh %s: %s (requests=%s)", rep.get("status"),
                        {c: v.get("inserted") for c, v in (rep.get("per_coin") or {}).items()}, rep.get("requests"))

    # ---- D-104: self-healing incremental archive loader -------------------------
    ARCHIVE_TICK_MS = 15 * 60 * 1000        # run every 15 minutes
    ARCHIVE_STALE_MS = 60 * 60 * 1000       # alert if the newest row is older than this

    async def _archive_incremental(self, s, now_ms: int) -> None:
        """Catch the archive up from the LAST STORED ROW every 15 minutes, and
        raise a Telegram alert when it falls more than 60 minutes behind.

        D-104 (2026-09-11): before this, the archive pull only ran on worker
        start-up and once a week, so after the 2026-09-08 restart the newest
        archive liquidation row sat at 2026-09-08 11:30 UTC for two days while
        four models silently scored their liquidation reasons off stale data.
        Self-healing: it needs no manual restart and it says so out loud when it
        cannot heal (no key, plan window, API error)."""
        last = getattr(self, "_archive_tick", None)
        if last is not None and now_ms - last < self.ARCHIVE_TICK_MS:
            return
        self._archive_tick = now_ms
        newest = (await s.execute(text(
            "SELECT MAX(ts) FROM strat_liquidations WHERE source='oxarchive'"))).scalar()
        newest = int(newest) if newest else None
        age_min = ((now_ms - newest) / 60000.0) if newest else None
        # catch up from the last stored row (+1 ms), never re-pull the whole window
        # a little overlap so a boundary row is never skipped; the loader dedupes
        days = 2.0 if newest is None else max(0.05, (now_ms - newest) / 86_400_000.0 + 0.05)
        days = round(float(days), 6)
        try:
            from .data.oxarchive import load_liquidations
            rep = await load_liquidations(s, list(self._assets), days=days, now_ms=now_ms)
        except Exception as e:  # noqa: BLE001 — a feed must never kill the boundary
            logger.warning("archive incremental failed: %s", e)
            rep = {"status": f"error: {e}"}
        after = (await s.execute(text(
            "SELECT MAX(ts) FROM strat_liquidations WHERE source='oxarchive'"))).scalar()
        after = int(after) if after else None
        age_after = ((now_ms - after) / 60000.0) if after else None
        logger.info("archive incremental: status=%s age_before=%s age_after=%s inserted=%s",
                    rep.get("status"),
                    f"{age_min:.0f}m" if age_min is not None else "empty",
                    f"{age_after:.0f}m" if age_after is not None else "empty",
                    {c: v.get("inserted") for c, v in (rep.get("per_coin") or {}).items()})
        if after is None or (now_ms - after) > self.ARCHIVE_STALE_MS:
            msg = (f"0xArchive liquidation feed STALE: newest row "
                   f"{('%.0f min old' % age_after) if age_after is not None else 'none at all'} "
                   f"(threshold 60 min). Loader status: {rep.get('status')}.")
            logger.error(msg)
            await s.execute(text(
                "INSERT INTO strat_telegram_outbox (ts, kind, strategy_id, coin, message, dedupe_key, sent) "
                "VALUES (:ts,'feed_stale','archive','-',:m,:k,0) "
                "ON DUPLICATE KEY UPDATE ts=VALUES(ts)"),
                {"ts": now_ms, "m": msg, "k": f"archive_stale:{now_ms // (60 * 60 * 1000)}"})

    def _liq_scale(self, coin: str) -> float:
        cov = (self._calib.get(coin) or {}).get("live_coverage") or {}
        v = cov.get("value")
        return (1.0 / float(v)) if v and float(v) > 0 else 1.0

    # ---- data ------------------------------------------------------------------
    async def _candles(self, s, coin: str, tf: str, now_ms: int, limit: int):
        rows = (await s.execute(text(
            "SELECT ts,o,h,l,c,v FROM strat_candles WHERE coin=:c AND tf=:tf AND ts<=:now "
            "ORDER BY ts DESC LIMIT :n"), {"c": coin, "tf": tf, "now": now_ms, "n": limit})).mappings().all()
        return to_candles([dict(r) for r in rows])

    async def _cohort_24h_ago(self, s, cohort: dict, coin: str, now_ms: int) -> Optional[float]:
        wl = [w["wallet"] for w in cohort.get("wallets", [])]
        if not wl:
            return None
        rows = (await s.execute(cohort_mod._q(
            "SELECT wallet, side, notional, cycle_ts FROM analytics_positions "
            "WHERE asset=:c AND wallet IN :wl AND cycle_ts=(SELECT MAX(cycle_ts) FROM analytics_positions "
            "WHERE cycle_ts <= :t AND asset=:c)"),
            {"c": coin, "wl": tuple(wl), "t": _ms_to_dt(now_ms - DAY_MS)})).mappings().all()
        if not rows:
            return None
        return cohort_mod.signal_from_rows(cohort, rows, [])["net_dir"]

    # ---- replay-only data (D-67/D-68): Binance replay candles + OI, reconstructed liquidation map
    async def _replay_candles(self, s, coin: str, tf: str, now_ms: int, limit: int):
        rows = (await s.execute(text(
            "SELECT ts,o,h,l,c,v FROM strat_replay_candles WHERE source='binance' AND coin=:c AND tf=:tf AND ts<=:now "
            "ORDER BY ts DESC LIMIT :n"), {"c": coin, "tf": tf, "now": now_ms, "n": limit})).mappings().all()
        return to_candles([dict(r) for r in rows])

    async def _replay_oi(self, s, coin: str, a: int, b: int) -> list[dict]:
        """Live 1m OI where the store has it, else Binance 5m openInterestHist (funding unknown)."""
        live = (await s.execute(text(
            "SELECT ts,oi_notional,funding,mark FROM strat_oi_1m WHERE coin=:c AND ts>:a AND ts<=:b ORDER BY ts"),
            {"c": coin, "a": a, "b": b})).mappings().all()
        if live:
            return [dict(r) for r in live]
        rep = (await s.execute(text(
            "SELECT ts,oi_notional,mark FROM strat_replay_oi WHERE source='binance' AND coin=:c AND ts>:a AND ts<=:b ORDER BY ts"),
            {"c": coin, "a": a, "b": b})).mappings().all()
        return [{"ts": int(r["ts"]), "oi_notional": r["oi_notional"], "funding": None, "mark": r["mark"]} for r in rep]

    async def build(self, s, coin: str, now_ms: int, cohort: dict) -> Snapshot:
        if self.replay_mode:
            c15 = await self._replay_candles(s, coin, "15m", now_ms, 2000)
            c1h = await self._replay_candles(s, coin, "1h", now_ms, 500)
            c4h = await self._replay_candles(s, coin, "4h", now_ms, 400)
            oi = await self._replay_oi(s, coin, now_ms - 25 * H_MS, now_ms)
            oi7 = max((float(r["oi_notional"]) for r in await self._replay_oi(s, coin, now_ms - 7 * DAY_MS, now_ms)
                       if r.get("oi_notional") is not None), default=None)
        else:
            c15 = await self._candles(s, coin, "15m", now_ms, 2000)
            c1h = await self._candles(s, coin, "1h", now_ms, 500)
            c4h = await self._candles(s, coin, "4h", now_ms, 400)
            oi = (await s.execute(text(
                "SELECT ts,oi_notional,funding,mark FROM strat_oi_1m WHERE coin=:c AND ts>:a AND ts<=:b ORDER BY ts"),
                {"c": coin, "a": now_ms - 25 * H_MS, "b": now_ms})).mappings().all()
            oi7 = (await s.execute(text(
                "SELECT MAX(oi_notional) FROM strat_oi_1m WHERE coin=:c AND ts>:a AND ts<=:b"),
                {"c": coin, "a": now_ms - 7 * DAY_MS, "b": now_ms})).scalar()
        # 7 days of taker tape: M4 `divergence` compares CVD at the last two 4h swing highs
        # (typically 1–5 days apart); a 24h tape left it constant 0 (audit D-46)
        tr = (await s.execute(text(
            "SELECT ts,taker_buy_notional,taker_sell_notional FROM strat_trades_1m WHERE coin=:c AND ts>:a AND ts<=:b ORDER BY ts"),
            {"c": coin, "a": now_ms - 7 * DAY_MS, "b": now_ms})).mappings().all()
        bk = (await s.execute(text(
            "SELECT ts,mid,bid_0_1,bid_0_3,bid_0_5,ask_0_1,ask_0_3,ask_0_5 FROM strat_book_5s "
            "WHERE coin=:c AND ts>:a AND ts<=:b ORDER BY ts"),
            {"c": coin, "a": now_ms - H_MS, "b": now_ms})).mappings().all()
        lq = (await s.execute(text(
            "SELECT ts,side,px,sz,notional,liquidated_user,source FROM strat_liquidations WHERE coin=:c AND ts>:a AND ts<=:b ORDER BY ts"),
            {"c": coin, "a": now_ms - 6 * H_MS, "b": now_ms})).mappings().all()
        if self.replay_mode:
            from .data.oxarchive import map_rows
            pos = await map_rows(s, coin, (now_ms // (15 * MIN_MS)) * 15 * MIN_MS)
        else:
            pos = (await s.execute(text(
                "SELECT wallet, liq_px, notional, side FROM analytics_positions WHERE asset=:c AND liq_px IS NOT NULL "
                "AND cycle_ts=(SELECT MAX(cycle_ts) FROM analytics_positions)"), {"c": coin})).mappings().all()
            if pos and c15:   # D-66: keep a 'live' map history for band_p80
                try:
                    from .data.oxarchive import persist_live_map
                    await persist_live_map(s, coin, (now_ms // (15 * MIN_MS)) * 15 * MIN_MS, [dict(r) for r in pos], c15[-1].c)
                except Exception as e:  # noqa: BLE001 — history is a side table, never blocks the snapshot
                    logger.warning("models: live map persist failed for %s: %s", coin, e)
        if self.replay_mode:   # never read a gauge row from after the boundary
            gauge = (await s.execute(text(
                "SELECT * FROM strat_gauge WHERE coin=:c AND ts<=:b ORDER BY ts DESC LIMIT 1"), {"c": coin, "b": now_ms})).mappings().first()
        else:
            gauge = (await s.execute(text(
                "SELECT * FROM strat_gauge WHERE coin=:c ORDER BY ts DESC LIMIT 1"), {"c": coin})).mappings().first()
        g24 = (await s.execute(text(
            "SELECT ts, funding_z FROM strat_gauge WHERE coin=:c AND ts>:a AND ts<=:b ORDER BY ts"),
            {"c": coin, "a": now_ms - DAY_MS, "b": now_ms})).mappings().all()
        ev = (await s.execute(text(
            "SELECT utc_ts FROM strat_events WHERE utc_ts BETWEEN :a AND :b"),
            {"a": now_ms - 2 * H_MS, "b": now_ms + 2 * H_MS})).all()
        try:
            csig = await cohort_mod.cohort_signal(s, cohort, coin, now_ms)
            csig["net_dir_24h_ago"] = await self._cohort_24h_ago(s, cohort, coin, now_ms)
        except Exception as e:  # noqa: BLE001 — cohort is optional context, never blocks the row
            logger.warning("models: cohort signal failed for %s: %s", coin, e)
            csig = {}
        unavailable = (await self._unavailable(s, coin, now_ms, oi, tr, bk, lq, pos, gauge, csig)) if self.replay_mode else set()
        return build_snapshot(coin, now_ms, c15, c1h, c4h, oi_rows=oi, trades_rows=tr, book_rows=bk, liq_rows=lq,
                              positions_rows=[dict(r) for r in pos], gauge=gauge, gauge_24h=g24,
                              oi_7d_high=(float(oi7) if oi7 is not None else None), cohort=csig,
                              events_ts=[int(r[0]) for r in ev], calib=self._calib.get(coin),
                              liq_scale=self._liq_scale(coin), unavailable=unavailable)

    async def _unavailable(self, s, coin: str, now_ms: int, oi, tr, bk, lq, pos, gauge, csig) -> set[str]:
        """Replay-only (D-68): a feed is unavailable at this boundary when the store
        has no rows for it in the snapshot window. Liquidations are special: an
        empty 6h can be a real quiet tape, so they count as unavailable only before
        the first liquidation row the store holds for the coin."""
        out = set(self.replay_unavailable)
        if not oi:
            out.add("oi")
        if not tr:
            out.add("taker")
        if not bk:
            out.add("book")
        if gauge is None:
            out.add("gauge")
        if not pos:
            out.add("liqmap")
        if not csig or csig.get("net_dir") is None:
            out.add("cohort")
        if coin not in self._replay_first_liq:
            self._replay_first_liq[coin] = (await s.execute(text(
                "SELECT MIN(ts) FROM strat_liquidations WHERE coin=:c"), {"c": coin})).scalar()
        first = self._replay_first_liq[coin]
        if not lq and (first is None or now_ms < int(first)):
            out.add("liq")
        if self._replay_first_event is None:
            self._replay_first_event = (await s.execute(text("SELECT MIN(utc_ts) FROM strat_events"))).scalar() or -1
        if self._replay_first_event < 0 or now_ms < int(self._replay_first_event):
            out.add("events")
        return out

    async def _recent_form(self, s, model: str) -> list[str]:
        rows = (await s.execute(text(
            "SELECT pnl_net FROM strat_trades WHERE model=:m AND mode='paper' AND exit_ts IS NOT NULL "
            "AND fill_ts IS NOT NULL ORDER BY exit_ts DESC LIMIT 5"), {"m": model})).all()
        return ["win" if (r[0] or 0) > 0 else "loss" for r in reversed(rows)]

    # ---- run -------------------------------------------------------------------
    async def run(self, now_ms: Optional[int] = None) -> None:
        """Called every scheduler tick. Pending/open resolution each tick (D-14);
        snapshots + Mind evaluation + Mind.manage + jobs on each closed 15m."""
        now_ms = now_ms or int(time.time() * 1000)
        cur = now_ms // (15 * MIN_MS)
        on_15m = cur != self._last_15m
        async with self._sf() as s:
            await self.ensure_weights(s, now_ms)
            if on_15m:
                self._last_15m = cur
                await self.ensure_calibration(s, now_ms)
                try:
                    cohort = await cohort_mod.build_cohort(s, now_ms)
                except Exception as e:  # noqa: BLE001
                    logger.warning("models: build_cohort failed: %s", e)
                    cohort = {"wallets": []}
                snaps: dict[str, Snapshot] = {}
                for coin in self._assets:
                    try:
                        snaps[coin] = await self.build(s, coin, now_ms, cohort)
                    except Exception as e:  # noqa: BLE001
                        logger.exception("models: snapshot failed for %s: %s", coin, e)
                self._snapshots = snaps
                self._log_tables(now_ms, snaps)
                for coin, snap in snaps.items():
                    for m in MODEL_REGISTRY.values():
                        try:
                            await self._evaluate_one(s, m, coin, snap, now_ms)
                        except Exception as e:  # noqa: BLE001
                            logger.exception("models: %s %s evaluation failed: %s", m.model, coin, e)
                await s.commit()
            try:
                await self._mgr.manage(s, now_ms, on_15m, self._snapshots)
                await s.commit()
            except Exception as e:  # noqa: BLE001
                logger.exception("models: trade manager failed: %s", e)
                await s.rollback()
            if on_15m:
                try:
                    await self._jobs(s, now_ms)
                    await s.commit()
                except Exception as e:  # noqa: BLE001
                    logger.exception("models: scheduled jobs failed: %s", e)
                    await s.rollback()

    def _log_tables(self, now_ms: int, snaps: dict[str, Snapshot]) -> None:
        hour = now_ms // H_MS
        if self._last_table_hour == hour:
            return
        self._last_table_hour = hour
        for coin, snap in snaps.items():
            logger.info("alignment table %s @ %s\n%s", coin, mb.utc_str(now_ms), format_table(coin, snap.a))

    async def _evaluate_one(self, s, m: ModelStrategy, coin: str, base: Snapshot, now_ms: int) -> None:
        form = await self._recent_form(s, m.model)
        state = await load_state(s, m.model, coin)
        snap = dataclasses.replace(base, setup={}, extra_vetoes=_feed_vetoes(base), recent_form=form, model_state=state)
        ev = m.evaluate(snap)
        d = ev.decision
        comps = {"alignment": jsonable(base.a), "setup": jsonable(ev.setup), "form": form,
                 "session": snap.session, "atr15": snap.atr("15m")}
        sig = StratSignal(
            ts=now_ms, strategy=m.id, asset=coin, venue=VENUE, mode="paper", direction=ev.direction,
            regime_score=None, bias_score=None, trigger_score=None, timing_score=None,
            total_score=float(d.conviction), fired=bool(ev.intent is not None),
            reason=(ev.waiting_for or "")[:255], components=comps,
            model=m.model, level_type=(ev.level_type or None), level_price=(_py(ev.level_price) if ev.level_price is not None else None),
            day_type=snap.day_type, raw_conviction=float(d.raw_conviction), conviction=float(d.conviction),
            size_tier=d.size_tier, reasons_json=d.reasons_json(), vetoes_json=d.vetoes_json(),
            multipliers_json=d.multipliers_json(), thesis=d.thesis,
            # D-89: reclaim type (M1/M5 setups only; NULL when no setup / other models)
            reclaim_candles=(int(ev.setup["reclaim_candles"]) if ev.setup and ev.setup.get("reclaim_candles") is not None else None),
            confirmation_used=(bool(ev.setup["confirmation_used"]) if ev.setup and ev.setup.get("confirmation_used") is not None else None),
        )
        s.add(sig)
        await s.flush()
        for kind, key, msg in ev.alerts:
            await outbox(s, now_ms, kind, m.id, coin, msg, key)
        for k, v in (ev.state_updates or {}).items():
            await upsert_state(s, m.model, coin, k, v, now_ms)
        await s.execute(text(
            "UPDATE strat_strategy_state SET waiting_for_sentence=:w, last_eval_ts=:ts WHERE strategy_id=:sid"),
            {"w": f"{coin}: {ev.waiting_for}"[:255], "ts": now_ms, "sid": m.id})
        if ev.intent is not None:
            await self._open(s, m, coin, snap, ev, sig.id, now_ms)
        logger.info("model eval %s %s: conviction %.2f (%s) %s", m.model, coin, d.conviction, d.size_tier,
                    (ev.waiting_for or "")[:120])

    async def _open(self, s, m: ModelStrategy, coin: str, snap: Snapshot, ev, signal_id: int, now_ms: int) -> None:
        it = ev.intent
        d = ev.decision
        st = (await s.execute(text(
            "SELECT requested_mode FROM strat_strategy_state WHERE strategy_id=:sid"), {"sid": m.id})).first()
        if not st or str(st[0]) != "paper":
            logger.info("models: %s %s TAKE not routed — strategy state mode %s", m.model, coin, st[0] if st else None)
            return
        if not mind_cfg.model_cfg(m.model).get("enabled", True):
            logger.info("models: %s disabled in models.yaml — TAKE not routed", m.model)
            return
        dup = (await s.execute(text(
            "SELECT id FROM strat_trades WHERE model=:m AND asset=:c AND mode='paper' AND exit_ts IS NULL LIMIT 1"),
            {"m": m.model, "c": coin})).first()
        if dup:
            logger.info("models: %s %s already has trade %s — TAKE not duplicated", m.model, coin, dup[0])
            return
        rs = (await s.execute(text("SELECT equity_usd FROM strat_risk_state LIMIT 1"))).first()
        equity = float(rs[0]) if rs and rs[0] else float(settings.STRATEGY_PAPER_EQUITY_USD)
        entry, stop = float(it.entry_px), float(it.stop_px)
        # spec v1.3 Part 1 (D-87): post-only placement against the current touch —
        # a crossing limit is rejected and re-quoted once one tick inside; the order
        # then rests and fills only through a later print (same code live + replay)
        entry, po = await self._mgr.paper_post_only_quote(s, coin, _entry_side(it.direction), entry, now_ms, snap)
        if po["rejected"]:
            logger.info("model entry post-only REJECTED: %s %s %s limit %.4f vs market %.4f -> re-quoted %.4f",
                        m.model, coin, it.direction, po["original_px"], po["market_px"], entry)
        sized = risk.size_for(entry, stop, equity, risk_pct=d.risk_pct, max_leverage=MAX_LEVERAGE)
        if sized.size <= 0:
            logger.info("models: %s %s size 0 (entry %.4f stop %.4f) — not routed", m.model, coin, entry, stop)
            return
        life = {
            "phase": "pending", "placed_ts": now_ms, "entry_valid_until": int(it.entry_valid_until_ms or 0),
            "post_only": po, "stop_floor_applied": bool(getattr(it, "stop_floor_applied", False)),
            "stop_structural": getattr(it, "stop_structural", None),
            "t1": it.t1, "t2": it.t2, "t3": it.t3, "partial_pct": float(it.partial_pct),
            "trail_tf": it.trail_tf, "trail_after": it.trail_after, "trail_armed": False,
            "hard_stop_ts": int(it.hard_stop_ts or 0), "hard_stop_min": int(m.hard_stop_min),
            "initial_stop": stop, "initial_risk_dollars": float(sized.size * abs(entry - stop)),
            "size": float(sized.size), "remaining_size": float(sized.size),
            "t1_done": False, "t2_done": False, "be_done": False, "trail_ref": None,
            "partial_pnl": 0.0, "partial_fees": 0.0, "fail_streak": 0,
            "fallback": jsonable(it.fallback_entry), "fallback_used": False,
            "entry_offset_note": it.entry_offset_note, "conviction": d.conviction, "size_tier": d.size_tier,
            "setup": jsonable(ev.setup), "tape_ts": None,
        }
        await s.execute(text(
            "INSERT INTO strat_trades (signal_id, strategy, asset, venue, mode, direction, entry_px, stop_px, "
            "target_px, size, leverage, fill_ts, exit_ts, exit_reason, model, expected_hold_min, lifecycle_json, "
            "in_trade_checks_json, stop_floor_applied, entry_requoted, entry_px_orig) VALUES (:sig,:st,:a,:v,'paper',:d,:e,:sp,:tp,:sz,:lev,NULL,NULL,'pending:model',"
            ":m,:hold,:life,:chk,:sfa,:req,:orig)"),
            {"sig": signal_id, "st": m.id, "a": coin, "v": VENUE, "d": it.direction, "e": entry, "sp": stop,
             "tp": it.t1, "sz": float(sized.size), "lev": float(sized.leverage), "m": m.model,
             "hold": int(m.expected_hold_min), "life": json.dumps(life), "chk": json.dumps([]),
             "sfa": int(life["stop_floor_applied"]), "req": int(po["rejected"]), "orig": float(po["original_px"])})
        logger.info("model paper entry rested: %s %s %s @ %.4f stop %.4f sz %.6f lev %.2f (%s, conviction %.2f%s)",
                    m.model, coin, it.direction, entry, stop, sized.size, sized.leverage, d.size_tier, d.conviction,
                    ", stop floor" if life["stop_floor_applied"] else "")

    # ---- scheduled jobs (doc 17 step 4) ------------------------------------------
    async def _jobs(self, s, now_ms: int) -> None:
        cur = now_ms // (15 * MIN_MS)
        if self._last_jobs_15m == cur:
            return
        self._last_jobs_15m = cur
        boundary = cur * 15 * MIN_MS            # the 15m boundary just crossed
        ds = day_start(boundary)
        hour = (boundary - ds) // H_MS
        minute = ((boundary - ds) % H_MS) // MIN_MS
        day = mb.day_key(boundary)
        for coin, snap in self._snapshots.items():
            if minute == 0 and hour in (7, 14):
                key = f"day_type:{day}:{hour:02d}"
                if key not in await load_state(s, "*", coin):
                    await upsert_state(s, "*", coin, key, {"label": snap.day_type, "ts": boundary,
                                                            "inputs": jsonable(dataclasses.asdict(snap.s.day_inputs) if snap.s.day_inputs else {})}, now_ms)
                    logger.info("day type frozen %s %s: %s", coin, key, snap.day_type)
            if minute == 0 and hour == 7:
                key = f"asia_range:{day}"
                if snap.s.asia and key not in await load_state(s, "*", coin):
                    a = snap.s.asia
                    await upsert_state(s, "*", coin, key, a, now_ms)
                    a15 = snap.atr("15m") or 0.0
                    await outbox(s, now_ms, "M5_asia", "m5_session_liquidity_run", coin,
                                 f"[M5] {coin} Asia range frozen {fmt_px(a['low'])}–{fmt_px(a['high'])} "
                                 f"(height {mb.fmt_atr((a['high'] - a['low']) / a15) if a15 else 'n/a'} ATR, "
                                 f"{a.get('wicks_outside', 0)} wicks outside). London window 07:00–09:00.",
                                 f"M5:{coin}:asia:{day}")
                    logger.info("Asia range frozen %s %s: %s..%s", coin, day, a["low"], a["high"])
            # Monday 00:xx: at the 00:00 boundary the first Monday candle has not closed yet, so
            # refs.weekly_open is None and a minute==0 gate could never freeze (audit D-48).
            # Any 15m boundary of hour 0 qualifies; the state key keeps it to one freeze/week.
            if hour == 0 and weekday(boundary) == 0:
                wk = mb.week_key(boundary)
                key = f"weekly_levels:{wk}"
                refs = snap.s.refs or {}
                if refs.get("weekly_open") is not None and key not in await load_state(s, "*", coin):
                    lv = {"weekly_open": refs.get("weekly_open"), "pwh": refs.get("pwh"), "pwl": refs.get("pwl"),
                          "pw_open": refs.get("pw_open"), "pw_close": refs.get("pw_close"), "ts": boundary}
                    await upsert_state(s, "*", coin, key, lv, now_ms)
                    await outbox(s, now_ms, "M6_levels", "m6_weekly_open_reclaim", coin,
                                 f"[M6] {coin} weekly levels frozen: WO {fmt_px(lv['weekly_open'])} · PWH {fmt_px(lv['pwh'])} "
                                 f"· PWL {fmt_px(lv['pwl'])} (prior week {mb.fmt_pct(((lv['pw_close'] or 0) / lv['pw_open'] - 1) if lv.get('pw_open') else None)}).",
                                 f"M6:{coin}:levels:{wk}")
                    logger.info("weekly levels frozen %s %s: %s", coin, wk, lv)
        if weekday(boundary) == 6 and hour == 0 and minute == 30:
            wk = learn_mod.iso_week(boundary - 7 * DAY_MS)
            done = await load_state(s, "*", "*")
            if f"learn:{wk}" not in done:
                summary = await learn_mod.run_weekly(s, {m.model: m.mind for m in MODEL_REGISTRY.values()}, now_ms)
                await upsert_state(s, "*", "*", f"learn:{wk}", summary, now_ms)
                logger.info("mind weekly learn %s: %s", wk, json.dumps(summary, default=str)[:800])


# ---------------------------------------------------------------------------
# trade manager
# ---------------------------------------------------------------------------
class ModelTradeManager:
    """Lifecycle of strat_trades rows with `model` set. Uses the book_5s mid tape
    like PaperTradeManager (D-05); fills are strict (through, never on touch)."""

    def __init__(self, assets: list[str]) -> None:
        self._assets = assets
        self._risk = PaperTradeManager(assets)     # for _apply_risk (annotate-only) + _mids

    async def manage(self, s, now_ms: int, on_15m: bool, snaps: dict[str, Snapshot]) -> None:
        pend = (await s.execute(text(
            "SELECT id, strategy, asset, direction, entry_px, stop_px, size, model, lifecycle_json, signal_id "
            "FROM strat_trades WHERE mode='paper' AND model IS NOT NULL AND fill_ts IS NULL AND exit_ts IS NULL"))).mappings().all()
        for t in pend:
            await self._pending(s, dict(t), now_ms, on_15m, snaps.get(t["asset"]))
        opens = (await s.execute(text(
            "SELECT id, strategy, asset, direction, entry_px, stop_px, target_px, size, leverage, fill_ts, model, "
            "expected_hold_min, lifecycle_json, in_trade_checks_json, mae, mfe "
            "FROM strat_trades WHERE mode='paper' AND model IS NOT NULL AND fill_ts IS NOT NULL AND exit_ts IS NULL"))).mappings().all()
        for t in opens:
            await self._open(s, dict(t), now_ms, on_15m, snaps.get(t["asset"]))

    # ---- pending --------------------------------------------------------------
    async def _save_life(self, s, tid: int, life: dict, **cols) -> None:
        sets = ", ".join(f"{k}=:{k}" for k in cols)
        await s.execute(text(f"UPDATE strat_trades SET lifecycle_json=:life{', ' + sets if sets else ''} WHERE id=:id"),
                        {"life": json.dumps(jsonable(life)), "id": tid, **cols})

    async def _cancel(self, s, t: dict, life: dict, now_ms: int, reason: str) -> None:
        life["phase"] = "cancelled"
        await self._save_life(s, t["id"], life, exit_ts=now_ms, exit_reason=reason[:128], pnl_gross=0.0, fees=0.0, pnl_net=0.0)
        await outbox(s, now_ms, f"{t['model']}_cancel", t["strategy"], t["asset"],
                     f"[{t['model']}] {t['asset']} entry {t['direction']} @ {fmt_px(t['entry_px'])} cancelled — {reason.split(':', 1)[-1].replace('_', ' ')}.",
                     f"{t['model']}:{t['asset']}:cancel:{t['id']}")
        logger.info("model entry cancelled: %s %s %s (%s)", t["model"], t["asset"], t["direction"], reason)

    async def paper_post_only_quote(self, s, coin: str, side: str, px: float, now_ms: int,
                              snap: Optional[Snapshot]) -> tuple[float, dict]:
        """Spec v1.3 Part 1 (D-87): the touch is the LAST mid on the tape inside the
        boundary window (live: strat_book_5s; replay: the synthesised candle tape,
        whose last point is the 15m close), else the snapshot's last 15m close. A
        crossing limit is rejected and re-quoted once at one tick inside via
        `post_only_place`. Returns (resting_px, post_only record for the trade row)."""
        tape = await self._risk._mids(s, coin, now_ms - 15 * MIN_MS, now_ms)
        if tape:
            market = float(tape[-1][1])
        elif snap is not None and snap.price:
            market = float(snap.price)
        else:
            market = None
        new_px, rejected = post_only_place(side, float(px), market, _tick(float(px)))
        return new_px, {"rejected": bool(rejected), "original_px": float(px), "requote_px": (new_px if rejected else None),
                        "market_px": market, "ts": int(now_ms)}

    async def _pending(self, s, t: dict, now_ms: int, on_15m: bool, snap: Optional[Snapshot]) -> None:
        life = _loads(t["lifecycle_json"]) or {}
        coin, direction = t["asset"], t["direction"]
        px = float(t["entry_px"])
        side = _entry_side(direction)
        start = int(life.get("tape_ts") or life.get("placed_ts") or now_ms - 15 * MIN_MS)
        tape = await self._risk._mids(s, coin, start, now_ms)
        for ts, mid in tape:
            if limit_fills_through(side, px, mid):
                # D-59: the in-trade `cohort_flip` check reads "cohort net long change since ENTRY" —
                # re-anchor the reference at the fill (the setup captured it at signal time)
                if snap is not None and snap.cohort_net_dir is not None:
                    st = life.get("setup") or {}
                    st["cohort_net_dir_entry"] = snap.cohort_net_dir
                    life["setup"] = st
                await self._fill(s, t, life, ts, now_ms)
                return
        if tape:
            life["tape_ts"] = tape[-1][0]
        fb = life.get("fallback") or {}
        # M2: cancel on a 15m close beyond the OB (doc 12); M1: `second_close_below`
        # veto — a 15m close back beyond the swept level before fill (doc 11 §5.3,
        # D-43). Both checked each closed 15m while pending.
        cancel_lvl = fb.get("level") if fb.get("type") == "cancel_beyond" else fb.get("cancel_level")
        if on_15m and snap is not None and cancel_lvl is not None and snap.s.c15:
            c = snap.s.c15[-1]
            lvl = float(cancel_lvl)
            if c.ts > int(life.get("placed_ts") or 0) and (c.c < lvl if direction == "long" else c.c > lvl):
                if fb.get("type") == "cancel_beyond":
                    reason = "cancelled:closed_beyond_ob"
                else:
                    reason = "cancelled:second_close_below" if direction == "long" else "cancelled:second_close_above"
                await self._cancel(s, t, life, now_ms, reason)
                return
        # M6: no pullback within 4h → re-price at the 1h close ± 0.1 ATR (doc 16)
        if fb.get("type") == "reprice_at" and not life.get("fallback_used") and now_ms >= int(fb.get("at_ms") or 0):
            new_px, po = await self.paper_post_only_quote(s, coin, side, float(fb["price"]), now_ms, snap)
            life["fallback_used"] = True
            life["entry_offset_note"] = "re-priced at 1h close + 0.1 ATR (no pullback in 4h)"
            life["post_only_reprice"] = po
            await self._save_life(s, t["id"], life, entry_px=new_px)
            logger.info("model entry re-priced: %s %s %.4f -> %.4f%s", t["model"], coin, px, new_px,
                        " (post-only rejected, re-quoted)" if po["rejected"] else "")
            return
        valid_until = int(life.get("entry_valid_until") or 0)
        if valid_until and now_ms >= valid_until:
            # M1: second attempt at the mid of the 15m FVG left by the reclaim (doc 11)
            if fb.get("type") == "fvg_reclaim" and not life.get("fallback_used"):
                if snap is None:
                    return                                   # decide on the next closed 15m
                z = self._reclaim_fvg(snap, direction, int(fb.get("reclaim_ts") or 0), int(fb.get("candles") or 3))
                life["fallback_used"] = True
                if z is None:
                    await self._cancel(s, t, life, now_ms, "cancelled:entry_expired_no_fvg")
                    return
                life["entry_valid_until"] = valid_until + M1_FALLBACK_WAIT_CANDLES * TF_MS["15m"]
                life["entry_offset_note"] = "second attempt: 15m FVG mid"
                life["fvg"] = {"top": z.top, "bottom": z.bottom}
                new_px, po = await self.paper_post_only_quote(s, coin, side, float(z.mid), now_ms, snap)
                life["post_only_reprice"] = po
                await self._save_life(s, t["id"], life, entry_px=new_px)
                logger.info("model entry second attempt at FVG mid: %s %s %.4f -> %.4f%s", t["model"], coin, px, new_px,
                            " (post-only rejected, re-quoted)" if po["rejected"] else "")
                return
            await self._cancel(s, t, life, now_ms, "cancelled:entry_expired")
            return
        await self._save_life(s, t["id"], life)

    @staticmethod
    def _reclaim_fvg(snap: Snapshot, direction: str, reclaim_ts: int, candles: int):
        want = "bullish" if direction == "long" else "bearish"
        lo, hi = reclaim_ts - TF_MS["15m"], reclaim_ts + candles * TF_MS["15m"]
        zs = [z for z in snap.s.zones.get("15m", []) if z.type == "FVG" and z.direction == want and z.live
              and lo <= z.created_ts <= hi]
        if not zs:
            return None
        return max(zs, key=lambda z: z.created_ts)

    async def _fill(self, s, t: dict, life: dict, ts: int, now_ms: int) -> None:
        life["phase"] = "open"
        life["tape_ts"] = ts
        if not life.get("hard_stop_ts"):
            life["hard_stop_ts"] = ts + int(life.get("hard_stop_min") or 0) * MIN_MS
        await self._save_life(s, t["id"], life, fill_ts=ts, exit_reason="open:model")
        px = float(t["entry_px"])
        await outbox(s, now_ms, f"{t['model']}_fill", t["strategy"], t["asset"],
                     f"[{t['model']}] {t['asset']} FILLED {t['direction'].upper()} @ {fmt_px(px)} · stop {fmt_px(t['stop_px'])} "
                     f"· T1 {fmt_px(life.get('t1'))} · T2 {fmt_px(life.get('t2'))} · hard stop {mb.utc_str(life['hard_stop_ts'])} UTC.",
                     f"{t['model']}:{t['asset']}:fill:{t['id']}")
        logger.info("model paper entry FILLED: %s %s %s @ %.4f", t["model"], t["asset"], t["direction"], px)

    # ---- open -----------------------------------------------------------------
    async def _open(self, s, t: dict, now_ms: int, on_15m: bool, snap: Optional[Snapshot]) -> None:
        life = _loads(t["lifecycle_json"]) or {}
        coin, direction = t["asset"], t["direction"]
        entry = float(t["entry_px"])
        stop = float(t["stop_px"]) if t["stop_px"] is not None else float(life.get("initial_stop"))
        fill_ts = int(t["fill_ts"])
        sign = mb.sgn(direction)
        rem = float(life.get("remaining_size") or t["size"])
        size0 = float(life.get("size") or t["size"])
        mae = float(t["mae"] or 0.0)
        mfe = float(t["mfe"] or 0.0)
        start = int(life.get("tape_ts") or fill_ts)
        tape = await self._risk._mids(s, coin, start, now_ms)
        hard = int(life.get("hard_stop_ts") or 0)
        xs = _exit_side(direction)
        last_mid = None
        for ts, mid in tape:
            last_mid = mid
            upnl = (mid - entry) * size0 * sign
            mfe, mae = max(mfe, upnl), min(mae, upnl)
            if stop_triggered(direction, stop, mid):
                reason = "trail_stop" if life.get("trail_ref") is not None else ("stop_be" if life.get("be_done") else "stop")
                await self._close(s, t, life, stop, ts, reason, mae, mfe, now_ms, taker=True)
                return
            if hard and ts >= hard:
                await self._close(s, t, life, mid, ts, "time_stop", mae, mfe, now_ms, taker=True)
                return
            t1, t2, t3 = life.get("t1"), life.get("t2"), life.get("t3")
            if not life.get("t1_done") and t1 is not None and limit_fills_through(xs, float(t1), mid):
                qty = size0 * float(life.get("partial_pct") or 40.0) / 100.0
                qty = min(qty, rem)
                await self._partial(s, t, life, float(t1), qty, ts, now_ms, "t1")
                rem = float(life["remaining_size"])
                stop = entry                              # stop to breakeven (all docs)
                life["be_done"] = True
                life["t1_done"] = True
                if life.get("trail_after") == "t1":
                    life["trail_armed"] = True
                await self._save_life(s, t["id"], life, stop_px=stop)
                if rem <= 0:
                    await self._close(s, t, life, float(t1), ts, "target", mae, mfe, now_ms, taker=False, already_flat=True)
                    return
                continue
            t2_is_exit = not (life.get("trail_armed") and t["model"] in TRAIL_REPLACES_T2)
            if life.get("t1_done") and not life.get("t2_done") and t2 is not None and t2_is_exit \
                    and limit_fills_through(xs, float(t2), mid):
                if t3 is not None:
                    qty = min(size0 * float(life.get("partial_pct") or 40.0) / 100.0, rem)
                    await self._partial(s, t, life, float(t2), qty, ts, now_ms, "t2")
                    rem = float(life["remaining_size"])
                    life["t2_done"] = True
                    if life.get("trail_after") in ("t2", "near_t2"):
                        life["trail_armed"] = True
                    await self._save_life(s, t["id"], life)
                    if rem <= 0:
                        await self._close(s, t, life, float(t2), ts, "target", mae, mfe, now_ms, taker=False, already_flat=True)
                        return
                    continue
                await self._close(s, t, life, float(t2), ts, "target", mae, mfe, now_ms, taker=False)
                return
            if life.get("t2_done") and t3 is not None and limit_fills_through(xs, float(t3), mid):
                await self._close(s, t, life, float(t3), ts, "target", mae, mfe, now_ms, taker=False)
                return
        if tape:
            life["tape_ts"] = tape[-1][0]
        if last_mid is None:
            last_mid = snap.price if snap is not None else entry
        # ---- each closed 15m: trail + Mind.manage ------------------------------
        if on_15m and snap is not None and snap.s.c15 and snap.s.c15[-1].ts > fill_ts:
            setup = life.get("setup") or {}
            if setup.get("entry_candle_high") is None:
                for c in snap.s.c15:
                    if c.ts - TF_MS["15m"] < fill_ts <= c.ts:
                        setup["entry_candle_high"] = c.h if direction == "long" else c.l
                        break
                life["setup"] = setup
            # M1 near_t2: arm when price has come within 0.5 ATR of T2 (doc 11)
            if life.get("trail_after") == "near_t2" and not life.get("trail_armed") and life.get("t2") is not None:
                a15 = snap.atr("15m") or 0.0
                if a15 > 0 and abs(float(life["t2"]) - last_mid) <= 0.5 * a15:
                    life["trail_armed"] = True
            if life.get("trail_armed"):
                new_stop = self._trail_stop(snap, life.get("trail_tf") or "15m", direction, fill_ts, stop)
                if new_stop is not None:
                    life["trail_ref"] = new_stop
                    stop = new_stop
                    await outbox(s, now_ms, f"{t['model']}_stop_move", t["strategy"], coin,
                                 f"[{t['model']}] {coin} {direction} stop trailed to {fmt_px(stop)} ({life.get('trail_tf')} swing).",
                                 f"{t['model']}:{coin}:trail:{t['id']}:{snap.s.c15[-1].ts}")
            m = MODEL_BY_CODE.get(t["model"])
            if m is not None:
                pos = Position(trade_id=int(t["id"]), model=t["model"], coin=coin, direction=direction, entry_px=entry,
                               stop_px=stop, target_px=(float(life["t1"]) if life.get("t1") is not None else None),
                               size=rem, fill_ts=fill_ts, initial_risk=abs(entry - float(life.get("initial_stop") or stop)),
                               expected_hold_min=int(t["expected_hold_min"] or m.expected_hold_min),
                               setup=setup, lifecycle=life, now_ms=now_ms)
                snap_m = dataclasses.replace(snap, setup=setup)
                res = m.mind.manage(snap_m, pos, float(last_mid))
                checks = _loads(t["in_trade_checks_json"]) or []
                checks.append({"ts": snap.s.c15[-1].ts, "action": res.action, "reason": res.reason, "failing": res.failing,
                               "fail_count": res.fail_count, "streak": res.streak, "progress_r": round(res.progress_r, 3),
                               "price": last_mid, "stop": stop})
                await s.execute(text("UPDATE strat_trades SET in_trade_checks_json=:c WHERE id=:id"),
                                {"c": json.dumps(jsonable(checks)), "id": t["id"]})
                if res.action == "exit":
                    await self._close(s, t, life, float(last_mid), now_ms, res.reason, mae, mfe, now_ms, taker=True,
                                      note=", ".join(res.failing))
                    return
        await self._save_life(s, t["id"], life, stop_px=stop, mae=mae, mfe=mfe)

    @staticmethod
    def _trail_stop(snap: Snapshot, tf: str, direction: str, fill_ts: int, cur_stop: float) -> Optional[float]:
        """D-11: last confirmed swing low (higher low) of `tf` since fill, if it
        improves the stop; mirror for shorts."""
        sw = snap.s.sw.get(tf, [])
        kind = "low" if direction == "long" else "high"
        ref = mb.last_confirmed_swing(sw, kind, snap.now_ms, after_ts=fill_ts)
        if ref is None:
            return None
        if direction == "long" and ref.price > cur_stop and ref.price < snap.price:
            return float(ref.price)
        if direction == "short" and ref.price < cur_stop and ref.price > snap.price:
            return float(ref.price)
        return None

    async def _partial(self, s, t: dict, life: dict, px: float, qty: float, ts: int, now_ms: int, which: str) -> None:
        entry, sign = float(t["entry_px"]), mb.sgn(t["direction"])
        pnl = (px - entry) * qty * sign
        fee = fee_for("target", px * qty)
        life["partial_pnl"] = float(life.get("partial_pnl") or 0.0) + pnl
        life["partial_fees"] = float(life.get("partial_fees") or 0.0) + fee
        life["remaining_size"] = max(0.0, float(life.get("remaining_size") or t["size"]) - qty)
        life.setdefault("partials", []).append({"which": which, "ts": ts, "px": px, "qty": qty, "pnl": pnl, "fee": fee})
        life["phase"] = "partial"
        await outbox(s, now_ms, f"{t['model']}_partial", t["strategy"], t["asset"],
                     f"[{t['model']}] {t['asset']} {which.upper()} hit @ {fmt_px(px)}: closed {qty / float(life.get('size') or t['size']) * 100:.0f}% "
                     f"(+${pnl:.2f}){' · stop to breakeven' if which == 't1' else ''}.",
                     f"{t['model']}:{t['asset']}:{which}:{t['id']}")
        logger.info("model %s %s %s: %s partial %.6f @ %.4f pnl %.4f", t["model"], t["asset"], t["direction"], which, qty, px, pnl)

    async def _close(self, s, t: dict, life: dict, exit_px: float, exit_ts: int, reason: str, mae: float, mfe: float,
                     now_ms: int, taker: bool, already_flat: bool = False, note: str = "") -> None:
        entry, sign = float(t["entry_px"]), mb.sgn(t["direction"])
        size0 = float(life.get("size") or t["size"])
        rem = 0.0 if already_flat else float(life.get("remaining_size") or t["size"])
        pnl_rem = (exit_px - entry) * rem * sign
        exit_fee = (abs(exit_px * rem) * (TAKER_FEE if taker else MAKER_FEE)) if rem > 0 else 0.0
        entry_fee = abs(entry * size0) * MAKER_FEE
        pnl_gross = float(life.get("partial_pnl") or 0.0) + pnl_rem
        fees = entry_fee + float(life.get("partial_fees") or 0.0) + exit_fee
        pnl_net = pnl_gross - fees
        risk_d = float(life.get("initial_risk_dollars") or 0.0)
        r = (pnl_net / risk_d) if risk_d > 0 else None
        life["phase"] = "closed"
        life["remaining_size"] = 0.0
        life["exit"] = {"px": exit_px, "ts": exit_ts, "reason": reason, "taker": taker, "note": note}
        await self._save_life(s, t["id"], life, exit_ts=exit_ts, exit_reason=reason[:128], pnl_gross=pnl_gross, fees=fees,
                              pnl_net=pnl_net, mae=mae, mfe=mfe, r_multiple=r)
        held = (exit_ts - int(t["fill_ts"])) / MIN_MS if t.get("fill_ts") else 0
        await outbox(s, now_ms, f"{t['model']}_exit", t["strategy"], t["asset"],
                     f"[{t['model']}] {t['asset']} {t['direction'].upper()} closed @ {fmt_px(exit_px)} — {reason.replace('_', ' ')}"
                     f"{(' (' + note + ')') if note else ''}. Net ${pnl_net:+.2f} = {(f'{r:+.2f}R' if r is not None else 'n/a R')} · held {held:.0f} min "
                     f"(expected {t.get('expected_hold_min')}).",
                     f"{t['model']}:{t['asset']}:exit:{t['id']}")
        logger.info("model paper trade CLOSED: %s %s %s pnl_net=%.4f r=%s (%s)", t["model"], t["asset"], t["direction"],
                    pnl_net, (f"{r:.2f}" if r is not None else "n/a"), reason)
        await self._risk._apply_risk(s, t["strategy"], pnl_net, now_ms)
