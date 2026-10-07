"""Spec v1.1 Part C (D-66) — calibrated liquidation normalisers: the percentile
maths, the daily/weekly schedule, and that every model site divides by the
calibrated value when one exists (and by the pre-v1.1 OI fraction when not)."""
from __future__ import annotations

import datetime as dt

import numpy as np
import pytest

from app.strategy_engine.structure import calibration as cal
from app.strategy_engine.structure.pools import liquidation_clusters
from app.strategy_engine.strategies import model_base as mb
from app.strategy_engine.strategies.m1_sweep_reclaim import M1SweepReclaim
from app.strategy_engine.strategies.m5_session_liquidity_run import M5SessionLiquidityRun

from . import test_models_m1_m6 as T
from .model_fixtures import with_setup


def _ms(y, m, d, hh=0, mm=0):
    return int(dt.datetime(y, m, d, hh, mm, tzinfo=dt.timezone.utc).timestamp() * 1000)


# ------------------------------------------------------------------ rolling sums
def test_rolling_5m_sums_windows_and_nonzero_only():
    t0 = _ms(2026, 9, 1, 12, 0)
    # three fills: two inside one 5-min span, one 30 min later
    ts = [t0 + 10_000, t0 + 3 * 60_000, t0 + 33 * 60_000]
    nv = [100.0, 50.0, 7.0]
    sums = cal.rolling_5m_sums(ts, nv)
    assert sums.size > 0 and (sums > 0).all()
    assert float(sums.max()) == pytest.approx(150.0)          # both first fills inside one window
    assert 7.0 in [float(x) for x in sums]                    # the lone fill has its own windows
    assert 157.0 not in [float(x) for x in sums]              # 30 min apart never share a window


def test_rolling_5m_sums_empty():
    assert cal.rolling_5m_sums([], []).size == 0
    assert cal.percentile(np.zeros(0), 90) is None


def test_percentile_is_numpy_linear():
    v = np.asarray([1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 9.0, 10.0])
    assert cal.percentile(v, 90) == pytest.approx(9.1)
    assert cal.percentile(v, 80) == pytest.approx(8.2)


# ------------------------------------------------------------------ schedule
def test_is_due_first_run_and_daily_0005():
    assert cal.is_due(_ms(2026, 9, 6, 9, 0), None)
    assert not cal.is_due(_ms(2026, 9, 6, 9, 0), _ms(2026, 9, 6, 8, 0))           # same day, already done
    assert not cal.is_due(_ms(2026, 9, 7, 0, 0), _ms(2026, 9, 6, 8, 0))           # 00:00 is before 00:05
    assert cal.is_due(_ms(2026, 9, 7, 0, 15), _ms(2026, 9, 6, 8, 0))              # first boundary after 00:05
    assert cal.is_due(_ms(2026, 9, 7, 13, 0), _ms(2026, 9, 6, 23, 45))            # missed 00:05 → still due today


def test_coverage_due_weekly():
    assert cal.coverage_due(_ms(2026, 9, 6), None)
    assert not cal.coverage_due(_ms(2026, 9, 6), _ms(2026, 9, 1))                 # same ISO week (Mon 08-31 .. Sun 09-06)
    assert cal.coverage_due(_ms(2026, 9, 7), _ms(2026, 9, 6))                     # Monday starts a new ISO week


# ------------------------------------------------------------------ snapshot helpers
def test_snapshot_normalisers_fallback_to_oi_fraction_when_uncalibrated():
    _, snap = T.m1_scenario()
    assert snap.calib == {} and snap.liq_scale == 1.0
    assert snap.liq_p90("long") == pytest.approx(snap.oi_frac(0.10))
    assert snap.band_p80() == pytest.approx(snap.oi_frac(0.10))


def test_snapshot_normalisers_use_calibrated_values():
    _, snap = T.m1_scenario()
    snap = with_setup(snap, {}, calib={"liq_5m_p90_long": {"value": 12_345.0}, "liq_5m_p90_short": {"value": 999.0},
                                        "band_p80": {"value": 555.0}, "live_coverage": {"value": None}})
    assert snap.liq_p90("long") == 12_345.0 and snap.liq_p90("short") == 999.0 and snap.band_p80() == 555.0
    # a NULL / zero value is "not calibrated", not a zero denominator
    snap = with_setup(snap, {}, calib={"liq_5m_p90_long": {"value": None}, "band_p80": {"value": 0.0}})
    assert snap.liq_p90("long") == pytest.approx(snap.oi_frac(0.10))
    assert snap.band_p80() == pytest.approx(snap.oi_frac(0.10))


def test_live_rows_scaled_by_inverse_coverage_only_where_no_archive_row_exists():
    _, snap = T.m1_scenario()
    t = snap.liq_rows[0]["ts"]
    # live-only window: scaled by 1/live_coverage
    rows = [{"ts": t, "side": "long", "notional": 100.0, "liquidated_user": "0xa", "source": None}]
    snap = with_setup(snap, {}, liq_rows=rows, liq_scale=4.0)
    n, w = snap.liq("long", t - 1, t + 1)
    assert n == pytest.approx(400.0) and w == 1
    # D-70: an archive row in the window means live + archive already form the full tape — nothing scaled
    rows.append({"ts": t, "side": "long", "notional": 100.0, "liquidated_user": "0xb", "source": "oxarchive"})
    snap = with_setup(snap, {}, liq_rows=rows, liq_scale=4.0)
    n, w = snap.liq("long", t - 1, t + 1)
    assert n == pytest.approx(200.0) and w == 2


def test_oxarchive_plan_window_and_snapshot_ts_parsing():
    from app.strategy_engine.data import oxarchive as ox
    # the plan's age limit: 403 history_window_exceeded carries the earliest allowed ISO timestamp
    msg = ("Your plan includes the most recent 30 days of history: this request starts at 2026-03-10T00:00:00Z, "
           "before the earliest allowed timestamp 2026-08-07T11:29:49Z. Requests cannot be chunked across the age boundary.")
    m = ox._EARLIEST_RE.search(msg)
    assert m and ox._iso_ms(m.group(1)) == 1786102189000
    # levels snapshots carry 'YYYY-MM-DD HH:MM:SS.mmm' (UTC), fills carry ISO-Z; both → epoch ms
    assert ox._any_ms("2026-09-06 04:01:30.000") == 1788667290000
    assert ox._any_ms("2026-09-06T04:42:11.948Z") == 1788669731948
    assert ox._any_ms(1788669731948) == 1788669731948 and ox._any_ms(1788669731) == 1788669731000
    r = ox.parse_fill({"coin": "BTC", "timestamp": "2026-09-06T04:42:11.948Z", "price": "79903", "size": "0.01877",
                       "side": "B", "mark_price": "79885", "direction": "Close Short", "liquidated_user": "0xC79B"})
    assert r["ts"] == 1788669731948 and r["side"] == "short" and r["source"] == "oxarchive" and r["liquidated_user"] == "0xc79b"


# ------------------------------------------------------------------ model sites
def test_oxarchive_load_clamps_to_plan_window_and_keeps_split_fills():
    """D-69/D-70 against a fake API: the first request is refused (plan window), the
    loader clamps and continues; a fill shadowed by a LIVE row on (ts, user, px) is
    skipped; two archive fills with the same (ts, user, px, sz) are BOTH kept on the
    first load and neither is re-inserted on the second."""
    import asyncio
    from app.strategy_engine.data import oxarchive as ox

    DAY = ox.DAY_MS
    now = 1_788_694_741_449
    earliest = now - 30 * DAY
    fills = [  # one shadowed by live, two identical split fills, one plain
        {"coin": "BTC", "timestamp": now - 5 * DAY, "price": "80000", "size": "0.5", "direction": "Close Long", "liquidated_user": "0xlive"},
        {"coin": "BTC", "timestamp": now - 4 * DAY, "price": "80100", "size": "0.1", "direction": "Close Long", "liquidated_user": "0xsplit"},
        {"coin": "BTC", "timestamp": now - 4 * DAY, "price": "80100", "size": "0.1", "direction": "Close Long", "liquidated_user": "0xsplit"},
        {"coin": "BTC", "timestamp": now - 2 * DAY, "price": "80200", "size": "0.2", "direction": "Close Short", "liquidated_user": "0xplain"},
    ]

    class FakeClient(ox.Client):
        def __init__(self):
            super().__init__("k", rps=1e9)
            self.calls = []

        async def get(self, path, params):
            self.calls.append(dict(params))
            self.requests += 1
            if params["start"] < earliest:
                self.earliest_allowed_ms = earliest
                raise ox.HistoryWindowExceeded(earliest, "history_window_exceeded")
            return {"data": [f for f in fills if params["start"] <= f["timestamp"] < params["end"]], "meta": {}}

    class FakeSession:
        def __init__(self):
            self.rows = [(now - 5 * DAY, "0xlive", 80000.0, 0.7, None)]   # a live row with the same (ts, user, px)
            self.inserted = []

        async def execute(self, stmt, params=None):
            sql = str(stmt)
            if sql.startswith("SELECT ts, liquidated_user"):
                rows = self.rows

                class R:
                    def all(self_inner): return rows
                return R()
            if sql.startswith("INSERT INTO strat_liquidations"):
                for p in params:
                    self.inserted.append(p)
                    self.rows.append((p["ts"], p["liquidated_user"], p["px"], p["sz"], "oxarchive"))
                return None
            raise AssertionError(sql)

        async def commit(self): pass

    s, c = FakeSession(), FakeClient()
    rep = asyncio.get_event_loop().run_until_complete(ox.load_liquidations(s, ["BTC"], days=180, now_ms=now, client=c))
    assert rep["status"] == "ok_plan_window" and rep["earliest_allowed_ms"] == earliest
    assert c.calls[0]["start"] == now - 180 * DAY and c.calls[1]["start"] == earliest + ox.CLAMP_MARGIN_MS
    r = rep["per_coin"]["BTC"]
    assert (r["inserted"], r["skipped_live"], r["skipped_archive"]) == (3, 1, 0)
    assert r["coverage"] == {"archive_rows": 1, "archive_notional": pytest.approx(80200 * 0.2)}
    # second load: nothing new, the split pair is recognised by multiplicity
    rep2 = asyncio.get_event_loop().run_until_complete(ox.load_liquidations(s, ["BTC"], days=180, now_ms=now, client=c))
    r2 = rep2["per_coin"]["BTC"]
    assert (r2["inserted"], r2["skipped_live"], r2["skipped_archive"]) == (0, 1, 3)
    assert len(s.inserted) == 3


def test_band_p80_takes_one_map_source_per_boundary_by_precedence():
    import asyncio

    class _Res:
        def __init__(self, rows): self._rows = rows
        def all(self): return self._rows

    class _S:
        async def execute(self, *_a, **_k):
            return _Res([(1000, "fills", 10.0), (1000, "levels", 20.0), (1000, "live", 30.0),
                         (2000, "fills", 40.0), (2000, "live", 50.0),
                         (3000, "fills", 60.0)])

    out = asyncio.get_event_loop().run_until_complete(cal._band_notionals(_S(), "BTC", 0, 10_000))
    assert out == [20.0, 50.0, 60.0]          # levels > live > fills, decided per boundary


def test_m1_fuel_divides_by_calibrated_liq_5m_p90_of_swept_side():
    _, snap = T.m1_scenario()                       # 4,000 notional of long liquidations inside the sweep window
    m = M1SweepReclaim()
    base, _ = m.find_setup(with_setup(snap, {}))
    assert base["fuel_norm"] == pytest.approx(snap.oi_frac(0.10))
    s2 = with_setup(snap, {}, calib={"liq_5m_p90_long": {"value": 8_000.0}, "liq_5m_p90_short": {"value": 1.0}})
    setup, _ = m.find_setup(s2)
    assert setup["fuel_norm"] == 8_000.0
    assert setup["fuel_strength"] == pytest.approx(0.5)          # 4,000 / 8,000 — the SHORT value is not used


def test_m5_fuel_divides_by_0_8_times_liq_5m_p90():
    _, snap, _lo = T.m5_scenario()
    m = M5SessionLiquidityRun()
    s2 = with_setup(snap, {}, calib={"liq_5m_p90_long": {"value": 10_000.0}, "liq_5m_p90_short": {"value": 10_000.0}})
    setup, missing = m.find_setup(s2)
    assert setup is not None, missing
    assert setup["fuel_norm"] == pytest.approx(8_000.0)


def test_pools_cluster_eligibility_uses_threshold_when_given():
    rows = [{"liq_px": 99.0, "notional": 600.0, "side": "long", "wallet": "0xa"},
            {"liq_px": 99.0, "notional": 600.0, "side": "long", "wallet": "0xb"}]
    # OI-fraction rule (no calibration): 0.1% of 1,000,000 = 1,000 → the 1,200 band qualifies
    assert len(liquidation_clusters(rows, 100.0, 1_000_000.0)) == 1
    # calibrated band_p80 above the band → no cluster, below → cluster; OI is irrelevant once calibrated
    assert liquidation_clusters(rows, 100.0, 1_000_000.0, threshold=1_500.0) == []
    assert len(liquidation_clusters(rows, 100.0, None, threshold=1_000.0)) == 1


def test_eligible_cluster_levels_use_band_p80():
    _, snap = T.m1_scenario()
    from app.strategy_engine.structure.pools import Pool
    big = Pool("liq_cluster_long", 96.0, 0.0, side="long", notional=5_000.0, wallets=3)
    snap.s.clusters = [big]
    lv = [x for x in mb.level_candidates(with_setup(snap, {}, calib={"band_p80": {"value": 4_000.0}}), "long") if x[0] == "liq_cluster_long"]
    assert len(lv) == 1 and lv[0][1] == 96.0
    lv = [x for x in mb.level_candidates(with_setup(snap, {}, calib={"band_p80": {"value": 6_000.0}}), "long") if x[0] == "liq_cluster_long"]
    assert lv == []


def test_breakdown_normaliser_use_keyed_by_model_code():
    # get_breakdown resolves the strategy id to the model code ("M1") before calling
    # _normaliser_rows — the use map must be keyed the same way or every row reads
    # "not used by this model" (caught on prod 2026-09-06).
    from app.routers.strategies import _NORMALISER_USE, _MODEL_OF
    assert set(_NORMALISER_USE) == set(_MODEL_OF.values())
    assert "fuel" in _NORMALISER_USE["M1"]["liq_5m_p90"]
    assert "band_p80" in _NORMALISER_USE["M3"]


# ------------------------------------------------------------------ v1.2 Part 4: point-in-time history (D-76)
def _synthetic_tape(first_day: int, days: int, per_day: int = 40, base: float = 1000.0):
    """One fill every (1440/per_day) minutes, both sides alternating, notional growing by day so a
    window that leaks future data would read a higher percentile than the honest one."""
    ts, nv, sd = [], [], []
    step = 1440 // per_day
    for d in range(days):
        for k in range(per_day):
            ts.append(first_day + d * cal.DAY_MS + k * step * 60_000 + 1)
            nv.append(base * (1 + d))            # day 0 = 1000, day 1 = 2000, ...
            sd.append("long" if k % 2 == 0 else "short")
    bts = [first_day + d * cal.DAY_MS + h * 3_600_000 for d in range(days) for h in range(24)]
    bnv = [base * (1 + d) for d in range(days) for _ in range(24)]
    return ts, nv, sd, bts, bnv


def test_asof_values_null_below_30_days_and_uses_only_data_before_as_of():
    first = _ms(2026, 3, 1)
    ts, nv, sd, bts, bnv = _synthetic_tape(first, 60)
    # day 29 after the first row: window 29 d < 30 d minimum → NULL with the note
    v = cal.asof_values(ts, nv, sd, bts, bnv, first, first + 29 * cal.DAY_MS)
    assert all(v[k]["value"] is None for k in cal.HIST_KEYS)
    assert "below 30d minimum" in v["band_p80"]["note"]
    # day 30: values exist and come ONLY from days 0..29 (max notional 30 × 1000)
    v30 = cal.asof_values(ts, nv, sd, bts, bnv, first, first + 30 * cal.DAY_MS)
    assert all(v30[k]["value"] is not None for k in cal.HIST_KEYS)
    assert v30["band_p80"]["value"] <= 30 * 1000.0
    assert v30["liq_5m_p90_long"]["value"] <= 30 * 1000.0       # one fill per 5-min window at this cadence
    assert v30["band_p80"]["window_days"] == 30.0
    # a later day sees more history and a higher percentile — and never the rows at/after as_of
    v45 = cal.asof_values(ts, nv, sd, bts, bnv, first, first + 45 * cal.DAY_MS)
    assert v45["band_p80"]["value"] > v30["band_p80"]["value"]
    assert v45["band_p80"]["value"] <= 45 * 1000.0
    assert v45["band_p80"]["window_days"] == 45.0
    # no rows before as_of at all
    v0 = cal.asof_values(ts, nv, sd, bts, bnv, first, first)
    assert all(v0[k]["value"] is None for k in cal.HIST_KEYS) and "before as_of" in v0["band_p80"]["note"]


def test_asof_window_capped_at_180_days():
    first = _ms(2025, 12, 1)
    ts, nv, sd, bts, bnv = _synthetic_tape(first, 200, per_day=24)
    v = cal.asof_values(ts, nv, sd, bts, bnv, first, first + 200 * cal.DAY_MS)
    assert v["band_p80"]["window_days"] == 180.0
    # the oldest 20 days (notional 1000..20000) are outside the window: p80 is well above them
    assert v["band_p80"]["value"] > 20 * 1000.0


def test_day_floor():
    t = _ms(2026, 9, 6, 13, 7) + 123
    assert cal.day_floor(t) == _ms(2026, 9, 6)
