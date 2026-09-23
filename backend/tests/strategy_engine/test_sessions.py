"""DST + window tests for the session layer (doc 05 §13)."""
import datetime as _dt

from app.strategy_engine.features import sessions

_UTC = _dt.timezone.utc


def _utc(y, m, d, hh, mm):
    return _dt.datetime(y, m, d, hh, mm, tzinfo=_UTC)


def test_us_dst_march_transition_2026():
    # US DST begins Sun 2026-03-08 02:00 local. Before = standard, after = daylight.
    assert sessions.us_in_dst(_utc(2026, 3, 1, 12, 0)) is False    # standard
    assert sessions.us_in_dst(_utc(2026, 3, 15, 12, 0)) is True    # daylight


def test_us_dst_november_transition_2026():
    # US DST ends Sun 2026-11-01 02:00 local. Before = daylight, after = standard.
    assert sessions.us_in_dst(_utc(2026, 10, 25, 12, 0)) is True   # daylight
    assert sessions.us_in_dst(_utc(2026, 11, 8, 12, 0)) is False   # standard


def test_us_opening_range_shifts_with_dst():
    # daylight -> 13:30 UTC; standard -> 14:30 UTC
    w_summer = sessions.session_windows(_utc(2026, 7, 1, 0, 0))["US"]
    w_winter = sessions.session_windows(_utc(2026, 1, 1, 0, 0))["US"]
    assert w_summer["or_start"] == 13 * 60 + 30
    assert w_winter["or_start"] == 14 * 60 + 30
    # trade window ends 3.5h after range start
    assert w_summer["window_end"] - w_summer["or_start"] == 210


def test_eu_session_phases():
    assert sessions.current_session(_utc(2026, 6, 3, 7, 15)) == ("EU", "opening_range")
    assert sessions.current_session(_utc(2026, 6, 3, 9, 0)) == ("EU", "trade_window")


def test_us_session_daylight():
    assert sessions.current_session(_utc(2026, 6, 3, 13, 45)) == ("US", "opening_range")
    assert sessions.current_session(_utc(2026, 6, 3, 15, 0)) == ("US", "trade_window")


def test_monday_asia_only_on_monday():
    # 2026-06-01 is a Monday
    assert sessions.current_session(_utc(2026, 6, 1, 0, 15)) == ("ASIA_MON", "opening_range")
    # 2026-06-02 is a Tuesday -> no Asia session
    assert sessions.current_session(_utc(2026, 6, 2, 0, 15)) == (None, None)


def test_no_session_dead_hour():
    assert sessions.current_session(_utc(2026, 6, 3, 4, 0)) == (None, None)


def test_opening_range_from_candles():
    # two 15m candles closing 07:15 and 07:30 UTC form the EU OR
    base = _utc(2026, 6, 3, 7, 15)
    c1 = {"ts": int(base.timestamp() * 1000), "h": 101.0, "l": 99.0}
    c2 = {"ts": int((base + _dt.timedelta(minutes=15)).timestamp() * 1000), "h": 102.0, "l": 100.0}
    orng = sessions.opening_range_from_candles([c1, c2], 7 * 60, 7 * 60 + 30)
    assert orng == {"or_high": 102.0, "or_low": 99.0, "n": 2}
