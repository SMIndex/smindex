"""Snapshot (doc 10 §4.7): the per-coin bundle a Mind reads — structure +
alignment, OI / funding / taker / liquidation / cohort / depth readings and
the model's recent form. Pure: built from rows the runner loads (or tests
synthesise). Every accessor returns None (or 0) when the feed is empty — a
detector then reads 0, never an invented value."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from app.strategy_engine.strategies import common as cm
from app.strategy_engine.structure import day_type as dt_mod
from app.strategy_engine.structure.alignment import Structure, alignment_table, build_structure
from app.strategy_engine.structure.candles import Candle, MIN_MS, TF_MS

H_MS = 3_600_000


@dataclass
class Snapshot:
    coin: str
    now_ms: int
    s: Structure
    oi_rows: list[dict] = field(default_factory=list)        # {ts, oi_notional, funding, mark}
    trades_rows: list[dict] = field(default_factory=list)    # {ts, taker_buy_notional, taker_sell_notional}
    book_rows: list[dict] = field(default_factory=list)      # {ts, mid, bid_0_3, ask_0_3, bid_0_5, ask_0_5}
    liq_rows: list[dict] = field(default_factory=list)       # {ts, side, notional, liquidated_user}
    gauge: Optional[dict] = None                              # latest strat_gauge row
    gauge_24h: list[dict] = field(default_factory=list)      # funding_z rows last 24h
    oi_7d_high: Optional[float] = None
    cohort: dict = field(default_factory=dict)
    recent_form: list[str] = field(default_factory=list)     # last 5 outcomes of THIS model: 'win'|'loss'
    events_ts: list[int] = field(default_factory=list)
    setup: dict = field(default_factory=dict)                 # model-specific facts for detectors + thesis
    extra_vetoes: list[tuple[str, str]] = field(default_factory=list)
    model_state: dict = field(default_factory=dict)           # persisted per model/coin (attempts, freezes)
    calib: dict = field(default_factory=dict)                 # strat_calibration rows {key: {value, ...}} (spec v1.1 Part C)
    liq_scale: float = 1.0                                    # 1 / live_coverage applied to live-feed liquidation rows
    unavailable: set = field(default_factory=set)             # replay-only (D-68): feeds with no data at this boundary

    # ---- convenience -------------------------------------------------------
    @property
    def price(self) -> float:
        return self.s.price

    @property
    def a(self) -> dict:
        return self.s.alignment

    def atr(self, tf: str) -> float:
        return float(self.s.atr.get(tf) or 0.0)

    @property
    def mid(self) -> Optional[float]:
        b = self.book_rows[-1] if self.book_rows else None
        return float(b["mid"]) if b and b.get("mid") is not None else None

    # ---- OI -----------------------------------------------------------------
    def oi_at(self, ts: int) -> Optional[float]:
        best = None
        for r in self.oi_rows:
            if r.get("oi_notional") is None:
                continue
            if int(r["ts"]) <= ts:
                best = float(r["oi_notional"])
            else:
                break
        return best

    @property
    def oi_now(self) -> Optional[float]:
        for r in reversed(self.oi_rows):
            if r.get("oi_notional") is not None:
                return float(r["oi_notional"])
        return None

    def oi_change(self, t0: int, t1: Optional[int] = None) -> Optional[float]:
        """Fractional OI change from the reading at/before t0 to the one at/before t1 (default now)."""
        t1 = self.now_ms if t1 is None else t1
        a, b = self.oi_at(t0), self.oi_at(t1)
        if a is None or b is None or a <= 0:
            return None
        return b / a - 1.0

    def oi_change_span(self, span_ms: int) -> Optional[float]:
        return self.oi_change(self.now_ms - span_ms)

    def oi_changes(self) -> dict:
        return {k: self.oi_change_span(v) for k, v in (("15m", 15 * MIN_MS), ("1h", H_MS), ("2h", 2 * H_MS),
                                                       ("4h", 4 * H_MS), ("24h", 24 * H_MS))}

    # ---- taker flow --------------------------------------------------------
    def _taker_index(self):
        # prefix sums over the (sorted) tape so a 7-day tape costs O(log n) per query (D-46)
        idx = getattr(self, "_tk_idx", None)
        if idx is None or idx[3] != len(self.trades_rows):
            ts, pb, ps = [], [0.0], [0.0]
            for r in self.trades_rows:
                ts.append(int(r["ts"]))
                pb.append(pb[-1] + float(r.get("taker_buy_notional") or 0))
                ps.append(ps[-1] + float(r.get("taker_sell_notional") or 0))
            idx = (ts, pb, ps, len(self.trades_rows))
            self._tk_idx = idx
        return idx

    def taker(self, t0: int, t1: int) -> Optional[dict]:
        """Taker buy/sell notional for rows with t0 < ts <= t1."""
        import bisect
        ts, pb, ps, _n = self._taker_index()
        i, j = bisect.bisect_right(ts, t0), bisect.bisect_right(ts, t1)
        n = j - i
        if n <= 0:
            return None
        buy, sell = pb[j] - pb[i], ps[j] - ps[i]
        tot = buy + sell
        return {"buy": buy, "sell": sell, "ratio": (buy / tot if tot > 0 else 0.5), "delta": buy - sell, "n": n}

    def taker_candle(self, c: Candle, tf: str = "15m") -> Optional[dict]:
        return self.taker(c.ts - TF_MS[tf], c.ts)

    def buy_ratio(self, t0: int, t1: int) -> Optional[float]:
        t = self.taker(t0, t1)
        return None if t is None else t["ratio"]

    def taker_span(self, span_ms: int) -> Optional[dict]:
        return self.taker(self.now_ms - span_ms, self.now_ms)

    def cvd(self, candles: list[Candle], tf: str = "15m") -> list[Optional[float]]:
        """Cumulative taker delta per candle (None where no tape)."""
        out: list[Optional[float]] = []
        acc = 0.0
        have = False
        for c in candles:
            t = self.taker_candle(c, tf)
            if t is None:
                out.append(acc if have else None)
                continue
            have = True
            acc += t["delta"]
            out.append(acc)
        return out

    # ---- liquidations -------------------------------------------------------
    def liq(self, side: str, t0: int, t1: int) -> tuple[float, int]:
        """(notional, distinct wallets) of liquidated `side` positions, t0 < ts <= t1."""
        n = 0.0
        w: set = set()
        rows = [r for r in self.liq_rows if t0 < int(r["ts"]) <= t1]
        # Part C: live-feed rows (partial coverage) are scaled by 1/live_coverage ONLY where the
        # archive has not been loaded for that window. Where archive rows exist, the live rows that
        # collided on (ts, user, px) shadowed the archive rows at load time (D-70), so live + archive
        # together already ARE the full tape and nothing is scaled.
        archived = any(r.get("source") == "oxarchive" for r in rows)
        for r in rows:
            if str(r.get("side")) == side:
                scale = 1.0 if (archived or r.get("source") == "oxarchive") else self.liq_scale
                n += float(r.get("notional") or 0) * scale
                if r.get("liquidated_user"):
                    w.add(r["liquidated_user"])
        return n, len(w)

    def liq_span(self, side: str, span_ms: int) -> tuple[float, int]:
        return self.liq(side, self.now_ms - span_ms, self.now_ms)

    def oi_frac(self, pct: float) -> float:
        """`pct` percent of coin OI in notional (0 when OI unknown → fuel reads 0)."""
        oi = self.oi_now
        return oi * pct / 100.0 if oi else 0.0

    # ---- calibrated normalisers (spec v1.1 Part C, D-66) ----------------------
    def calib_value(self, key: str) -> Optional[float]:
        d = self.calib.get(key) if self.calib else None
        v = d.get("value") if isinstance(d, dict) else d
        return float(v) if v is not None and float(v) > 0 else None

    def liq_p90(self, side: str) -> float:
        """liq_5m_p90 for `side` (long|short); the pre-v1.1 0.10% OI when not calibrated."""
        v = self.calib_value(f"liq_5m_p90_{side}")
        return v if v is not None else self.oi_frac(0.10)

    def band_p80(self) -> float:
        """band_p80 (80th pct of non-zero map band notional); 0.10% OI when not calibrated."""
        v = self.calib_value("band_p80")
        return v if v is not None else self.oi_frac(0.10)

    def is_unavailable(self, *feeds: str) -> bool:
        """Replay-only: True when any of `feeds` has no data at this boundary (D-68)."""
        return bool(self.unavailable) and any(f in self.unavailable for f in feeds)

    # ---- funding / crowding --------------------------------------------------
    @property
    def funding_z(self) -> Optional[float]:
        if self.gauge and self.gauge.get("funding_z") is not None:
            return float(self.gauge["funding_z"])
        return None

    @property
    def crowding_level(self) -> Optional[str]:
        return self.gauge.get("crowding_level") if self.gauge else None

    @property
    def blocked_direction(self) -> Optional[str]:
        return self.gauge.get("blocked_direction") if self.gauge else None

    def funding_extreme(self, direction: str) -> bool:
        """Crowding EXTREME on `direction`'s side (the crowd is already positioned that way)."""
        return self.crowding_level == "EXTREME" and self.blocked_direction == direction

    def funding_z_max_24h(self) -> Optional[float]:
        zs = [float(r["funding_z"]) for r in self.gauge_24h if r.get("funding_z") is not None]
        return max(zs) if zs else self.funding_z

    def funding_z_min_24h(self) -> Optional[float]:
        zs = [float(r["funding_z"]) for r in self.gauge_24h if r.get("funding_z") is not None]
        return min(zs) if zs else self.funding_z

    # ---- depth ----------------------------------------------------------------
    def depth(self, key: str) -> Optional[float]:
        b = self.book_rows[-1] if self.book_rows else None
        return float(b[key]) if b and b.get(key) is not None else None

    def depth_avg(self, key: str, span_ms: int = H_MS) -> Optional[float]:
        vals = [float(r[key]) for r in self.book_rows if r.get(key) is not None and int(r["ts"]) >= self.now_ms - span_ms]
        return sum(vals) / len(vals) if vals else None

    # ---- cohort ----------------------------------------------------------------
    @property
    def cohort_net_dir(self) -> Optional[float]:
        return self.cohort.get("net_dir")

    @property
    def cohort_net_long_change_24h(self) -> Optional[float]:
        nd, old = self.cohort.get("net_dir"), self.cohort.get("net_dir_24h_ago")
        if nd is None or old is None:
            return None
        return float(nd) - float(old)

    def cohort_fresh_adds(self, direction: str) -> int:
        return int(self.cohort.get("fresh_long" if direction == "long" else "fresh_short") or 0)

    # ---- form -----------------------------------------------------------------
    def consecutive(self, outcome: str) -> int:
        n = 0
        for o in reversed(self.recent_form):
            if o == outcome:
                n += 1
            else:
                break
        return n

    # ---- session / time -----------------------------------------------------------
    @property
    def session(self) -> str:
        return self.s.session

    @property
    def minutes_into_session(self) -> int:
        return self.s.minutes_into_session

    @property
    def day_type(self) -> str:
        return self.s.day_type

    @property
    def daily_bias(self) -> str:
        return self.s.daily_bias

    def session_score(self) -> float:
        """1.0 first 120 min of London/NY; 0.6 rest of those; 0.3 Asia; 0.1 dead."""
        if self.session in ("london", "newyork"):
            return 1.0 if self.minutes_into_session <= 120 else 0.6
        return 0.3 if self.session == "asia" else 0.1

    def session_mult(self) -> float:
        if self.session in ("london", "newyork"):
            return 1.1 if self.minutes_into_session <= 120 else 1.0
        return 0.8 if self.session == "asia" else 0.7

    # ---- day type with feed inputs -----------------------------------------------------
    def finalize_day_type(self, override: Optional[str] = None) -> str:
        """Complete the DayInputs with feed readings and (re)classify; the running
        label per doc 10 §3. `override` = a frozen 07:00/14:00 label to keep."""
        di = self.s.day_inputs or dt_mod.DayInputs()
        di.funding_z = self.funding_z
        di.oi_change_2h = self.oi_change_span(2 * H_MS)
        di.oi_change_4h = self.oi_change_span(4 * H_MS)
        t2 = self.taker_span(2 * H_MS)
        di.delta_skew_2h = t2["ratio"] if t2 else None
        closes = [c.c for c in self.s.c15]
        if len(closes) >= 97 * 2:
            _rv, pct, have = cm.rv24h_pct(closes, days=30)
            di.rv_pct = pct if have >= 5 and pct == pct else None
        self.s.day_inputs = di
        self.s.day_type = override or dt_mod.classify(di)
        self.s.alignment = alignment_table(self.s)
        return self.s.day_type


def build_snapshot(coin: str, now_ms: int, c15: list[Candle], c1h: list[Candle], c4h: list[Candle], *,
                   oi_rows=None, trades_rows=None, book_rows=None, liq_rows=None, positions_rows=None,
                   gauge=None, gauge_24h=None, oi_7d_high=None, cohort=None, events_ts=None,
                   recent_form=None, model_state=None, day_type_override: Optional[str] = None,
                   calib=None, liq_scale: float = 1.0, unavailable=None) -> Snapshot:
    oi_rows = sorted([dict(r) for r in (oi_rows or [])], key=lambda r: int(r["ts"]))
    oi_now = next((float(r["oi_notional"]) for r in reversed(oi_rows) if r.get("oi_notional") is not None), None)
    calib = dict(calib or {})
    bp = calib.get("band_p80")
    bp = bp.get("value") if isinstance(bp, dict) else bp
    st = build_structure(coin, now_ms, c15, c1h, c4h, liq_rows=positions_rows or [], oi_notional=oi_now,
                         events_ts=events_ts or [], cluster_threshold=(float(bp) if bp else None))
    snap = Snapshot(coin, now_ms, st, oi_rows=oi_rows, calib=calib, liq_scale=float(liq_scale or 1.0),
                    unavailable=set(unavailable or ()),
                    trades_rows=sorted([dict(r) for r in (trades_rows or [])], key=lambda r: int(r["ts"])),
                    book_rows=sorted([dict(r) for r in (book_rows or [])], key=lambda r: int(r["ts"])),
                    liq_rows=sorted([dict(r) for r in (liq_rows or [])], key=lambda r: int(r["ts"])),
                    gauge=dict(gauge) if gauge else None, gauge_24h=[dict(r) for r in (gauge_24h or [])],
                    oi_7d_high=oi_7d_high, cohort=dict(cohort or {}), recent_form=list(recent_form or []),
                    events_ts=list(events_ts or []), model_state=dict(model_state or {}))
    snap.finalize_day_type(day_type_override)
    return snap
