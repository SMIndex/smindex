"""Telegram alert settings — pure decision logic (overnight Part B).
Run: python tests/test_alerts_logic.py  (also collected by pytest)."""
import sys
from datetime import datetime
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services.alerts import account_watch as aw  # noqa: E402
from app.services.alerts import gateway as gw  # noqa: E402
from app.services.alerts import prefs as P  # noqa: E402
from app.services.alerts import producers as pr  # noqa: E402


def _at_hour(h):
    class _DT(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 9, 29, h, 30, tzinfo=tz)
    return mock.patch.object(gw, "datetime", _DT)


def test_quiet_hours_wrap_and_plain():
    g = {"quiet_enabled": True, "quiet_start": 23, "quiet_end": 7, "tz": "UTC"}
    for h, want in ((22, False), (23, True), (3, True), (6, True), (7, False), (12, False)):
        with _at_hour(h):
            assert gw._in_quiet_hours(g) is want, (h, want)
    g2 = {"quiet_enabled": True, "quiet_start": 1, "quiet_end": 5, "tz": "UTC"}
    for h, want in ((0, False), (1, True), (4, True), (5, False)):
        with _at_hour(h):
            assert gw._in_quiet_hours(g2) is want, (h, want)
    with _at_hour(3):
        assert gw._in_quiet_hours({**g, "quiet_enabled": False}) is False


def test_validate_rejects_bad_values_and_unknown_keys():
    assert P.validate("liq_warning", {"margin_use_pct": 85, "junk": 1}) == {"margin_use_pct": 85}
    for t, p in (("liq_warning", {"margin_use_pct": 101}), ("smi_cross", {"above": 40}),
                 ("smi_cross", {"below": 60}), ("daily_summary", {"hour": 24}),
                 ("_global", {"hourly_limit": 0}), ("_global", {"tz": "Mars/Base"}),
                 ("nope", {})):
        try:
            P.validate(t, p)
        except ValueError:
            continue
        raise AssertionError(f"accepted {t} {p}")
    assert P.validate("_global", {"tz": "Asia/Kolkata", "quiet_start": "22"}) == {"tz": "Asia/Kolkata", "quiet_start": 22}
    assert P.validate("smi_cross", {"assets": ["BTC", " ", "xyz:BRENTOIL"]}) == {"assets": ["BTC", "xyz:BRENTOIL"]}


def test_spec_defaults():
    d = P._defaults()
    on = {t for t, v in d.items() if t != "_global" and v["enabled"]}
    assert on == {"copy_trade", "copy_failed", "copy_paused", "tpsl_triggered", "tpsl_leftover",
                  "liq_warning", "watched_wallet", "daily_summary"}, on
    assert d["liq_warning"]["params"]["margin_use_pct"] == 80
    assert d["smi_cross"]["params"]["above"] == 70 and d["smi_cross"]["params"]["below"] == 30
    assert d["watched_wallet"]["params"]["min_notional_usd"] == 50000
    assert d["daily_summary"]["params"]["hour"] == 8
    assert d["_global"]["params"]["hourly_limit"] == 20 and d["_global"]["params"]["quiet_enabled"] is False


def test_trigger_fired_directions():
    f = aw.trigger_fired
    assert f("long", "sl", 100, 99.9) and f("long", "sl", 100, 100.4) and not f("long", "sl", 100, 101)
    assert f("long", "tp", 100, 100.1) and f("long", "tp", 100, 99.6) and not f("long", "tp", 100, 99)
    assert f("short", "sl", 100, 100.1) and not f("short", "sl", 100, 99)
    assert f("short", "tp", 100, 99.9) and not f("short", "tp", 100, 101)


def test_margin_use_pct():
    with mock.patch.object(aw, "_mm_hdths", return_value=2000):      # MMR = 5% of notional
        p = {"market_id": 1, "deposit": 10.0, "pnl": 0.0, "notional": 100.0}
        assert aw.margin_use_pct(p) == 50.0                             # 5 / 10
        assert aw.margin_use_pct({**p, "pnl": -4.0}) == 83.3            # 5 / 6
        assert aw.margin_use_pct({**p, "pnl": -10.0}) == 100.0          # equity gone
    with mock.patch.object(aw, "_mm_hdths", return_value=None):
        assert aw.margin_use_pct({"market_id": 1, "deposit": 1, "pnl": 0, "notional": 1}) is None


def test_watched_wallet_threshold():
    assert pr.watched_wallet_passes({"min_notional_usd": 50000}, 60000)
    assert not pr.watched_wallet_passes({"min_notional_usd": 50000}, 49999)
    assert not pr.watched_wallet_passes({"min_notional_usd": 50000}, None)
    assert pr.watched_wallet_passes({"min_notional_usd": 0}, None)


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("PASS", name)
    print("ALL ALERT LOGIC TESTS PASS")
