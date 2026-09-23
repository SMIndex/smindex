"""Compute-core tests for the data-backed features (doc 00 §4, doc 02 §4)."""
import math

from app.strategy_engine.features import funding_gauge as fg
from app.strategy_engine.features import regime
from app.strategy_engine.features import bias
from app.strategy_engine import seed


# ---- funding gauge (doc 02 §4) ----
def test_gauge_extreme_by_zscore():
    hist = [0.0] * 30
    g = fg.compute_gauge(hl_predicted_funding=0.01, hl_8h_equiv_history_30d=[0.0] * 5 + [0.001] * 25, binance_rate=None)
    assert g["hl_funding_8h_equiv"] == 0.08
    assert g["funding_z"] is not None and g["funding_z"] >= 2.0
    assert g["crowding_level"] == "EXTREME"
    assert g["blocked_direction"] == "long"          # positive funding -> crowd long
    assert g["tilt"] < 0                              # contrarian short tilt
    _ = hist


def test_gauge_extreme_by_negative_zscore():
    """Owner rule change 2026-09-04: |z| >= 2 is EXTREME on either side. Real
    shape of the miss: BTC HL funding z −3.75 on 09-03 21:00 UTC read NORMAL."""
    hist = [0.0001] * 25 + [0.00012] * 5           # 8h-equiv, positive, tight (mean 1.03e-4, sd 7.5e-6)
    g = fg.compute_gauge(hl_predicted_funding=-0.00001, hl_8h_equiv_history_30d=hist, binance_rate=0.0001)
    assert g["funding_z"] is not None and g["funding_z"] <= -2.0      # 8h-equiv -8e-5 -> z ~ -24
    assert g["crowding_level"] == "EXTREME"
    assert g["blocked_direction"] == "short"         # negative funding -> crowd short
    assert g["tilt"] > 0                              # contrarian long tilt
    g2 = fg.compute_gauge(hl_predicted_funding=0.0000115, hl_8h_equiv_history_30d=hist, binance_rate=None)  # 8h 9.2e-5 -> z ~ -1.5
    assert -2.0 < g2["funding_z"] <= -1.0 and g2["crowding_level"] == "ELEVATED"


def test_gauge_extreme_by_binance_rate():
    g = fg.compute_gauge(hl_predicted_funding=-0.0001, hl_8h_equiv_history_30d=[-0.0008] * 30, binance_rate=-0.0004)
    assert g["crowding_level"] == "EXTREME"           # <= -0.0003
    assert g["blocked_direction"] == "short"


def test_gauge_insufficient_data_is_honest():
    g = fg.compute_gauge(hl_predicted_funding=None, hl_8h_equiv_history_30d=[], binance_rate=None)
    assert g["crowding_level"] == "insufficient_data"
    assert g["tilt"] is None


# ---- regime gate (doc 00 §4) ----
def test_regime_macro_block():
    now = 1_000_000_000_000
    r = regime.evaluate(now, event_ts_list=[now + 30 * 60_000], last_oracle_ms=now)
    assert "macro_event" in r["blockers"]
    assert r["allowed"] is False


def test_regime_weekend_block():
    # 2026-06-06 is a Saturday
    import datetime as _dt
    sat = int(_dt.datetime(2026, 6, 6, 3, 0, tzinfo=_dt.timezone.utc).timestamp() * 1000)
    r = regime.evaluate(sat, event_ts_list=[], last_oracle_ms=sat)
    assert "weekend" in r["blockers"] and r["allowed"] is False


def test_regime_oracle_stale_and_unavailable():
    now = 1_000_000_000_000
    assert "oracle_stale" in regime.evaluate(now, last_oracle_ms=now - 20_000)["blockers"]
    assert "oracle_unavailable" in regime.evaluate(now, last_oracle_ms=None)["blockers"]


def test_regime_allowed_weekday_fresh_oracle():
    # 2026-06-03 is a Wednesday, US session hour
    import datetime as _dt
    wed = int(_dt.datetime(2026, 6, 3, 14, 0, tzinfo=_dt.timezone.utc).timestamp() * 1000)
    r = regime.evaluate(wed, event_ts_list=[], last_oracle_ms=wed - 2000, spread=1.0, median_24h_spread=1.0,
                        session_quality=1.0, rv_vs_24h_avg=0.8)
    assert r["allowed"] is True
    assert r["score"] is not None


# ---- bias (doc 00 §4 weights, renormalised by available mass) ----
def test_bias_renormalises_available_components():
    b = bias.compute_bias(cohort_net_dir=1.0, funding_tilt=None, basis=None, crowd=None, hull_1h_dir=1)
    # available mass = 0.35 (cohort) + 0.10 (hull) = 0.45; score = (0.35*1 + 0.10*1)/0.45 = 1.0
    assert math.isclose(b["bias_score"], 1.0)
    assert b["components"]["basis"]["available"] is False
    assert b["components"]["crowd"]["available"] is False
    assert math.isclose(b["weight_mass_available"], 0.45)


def test_bias_none_when_nothing_available():
    b = bias.compute_bias()
    assert b["bias_score"] is None


# ---- parameter seeding counts (Part 3.8) ----
def test_parameter_counts_present_for_all_six():
    counts = seed.parameter_counts()
    assert set(counts) == {sid for sid, _ in seed.STRATEGIES}
    assert all(n >= 15 for n in counts.values())      # each strategy has a real param set
    assert sum(counts.values()) >= 120
