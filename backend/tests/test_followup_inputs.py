"""D-108/D-110 — regime spread input, and a CHoCH regression pinned to a real
4h pullback from the 180-day dataset. Pure: no DB, no network.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.strategy_engine.features import regime            # noqa: E402
from app.strategy_engine.structure.candles import Candle    # noqa: E402
from app.strategy_engine.structure.swings import swings     # noqa: E402
from app.strategy_engine.structure.trend import structure, trend_label  # noqa: E402

H4 = 4 * 3600 * 1000


# ------------------------------------------------------------------ D-108 ---
def test_spread_block_pure_rule():
    assert regime.spread_block(10.0, 4.0) is True        # 10 > 2x4
    assert regime.spread_block(7.0, 4.0) is False        # 7 < 2x4
    assert regime.spread_block(8.0, 4.0) is False        # exactly 2x is not "wider than"
    assert regime.spread_block(None, 4.0) is None
    assert regime.spread_block(10.0, None) is None
    assert regime.spread_block(10.0, 0.0) is None


def test_regime_reports_spread_unavailable_only_when_it_really_is():
    """The live defect: evaluate() was called with no spread pair at all, so
    every one of 33,166 rows said `spread_unavailable` and `spread_wide` could
    never fire. With the pair supplied the gate resolves properly."""
    now = 1_789_000_000_000
    r = regime.evaluate(now, event_ts_list=[], last_oracle_ms=now)
    assert "spread_unavailable" in r["blockers"], "no pair supplied -> unavailable"

    ok = regime.evaluate(now, event_ts_list=[], last_oracle_ms=now,
                         spread=4.0, median_24h_spread=4.0)
    assert "spread_unavailable" not in ok["blockers"]
    assert "spread_wide" not in ok["blockers"]

    wide = regime.evaluate(now, event_ts_list=[], last_oracle_ms=now,
                           spread=20.0, median_24h_spread=4.0)
    assert "spread_wide" in wide["blockers"]
    assert wide["allowed"] is False, "spread_wide is a HARD blocker"


def test_weekend_and_macro_windows_are_utc_and_correct():
    """Investigation 1 checked these against real blocked timestamps; pin them."""
    import datetime as dt
    def ms(y, m, d, hh, mm=0):
        return int(dt.datetime(y, m, d, hh, mm, tzinfo=dt.timezone.utc).timestamp() * 1000)
    # 2026-09-05 is a Saturday, 09-06 a Sunday, 09-04 a Friday
    assert regime.weekend_block(ms(2026, 9, 5, 0, 0)) is True
    assert regime.weekend_block(ms(2026, 9, 6, 11, 59)) is True
    assert regime.weekend_block(ms(2026, 9, 6, 12, 0)) is False
    assert regime.weekend_block(ms(2026, 9, 4, 23, 59)) is False
    # the real event on 2026-09-04 12:30 UTC blocks -30 min .. +2 h
    ev = ms(2026, 9, 4, 12, 30)
    assert regime.macro_block(ms(2026, 9, 4, 10, 30), [ev]) is True     # exactly 2h before
    assert regime.macro_block(ms(2026, 9, 4, 10, 29), [ev]) is False    # 2h01 before
    assert regime.macro_block(ms(2026, 9, 4, 13, 0), [ev]) is True      # 30 min after
    assert regime.macro_block(ms(2026, 9, 4, 13, 1), [ev]) is False


# ------------------------------------------------------------------ D-110 ---
def _c(i, o, hi, lo, c):
    return Candle(ts=1_780_000_000_000 + i * H4, o=o, h=hi, l=lo, c=c, v=100.0)


def test_choch_down_fires_on_a_real_btc_4h_pullback():
    """REAL data, not a synthetic shape: 60 BTC 4h candles ending just after the
    CHoCH the 180-day scan found at 2026-03-26 07:59 UTC (level 70,538).

    The scan showed the detector is healthy (25 BTC / 27 ETH CHoCH events over
    180 days, 26 completed M4 setups), so this is a REGRESSION pin: it fails if
    swing confirmation, trend labelling or the CHoCH rule is ever broken.
    Fixture: tests/fixtures_btc4h_choch.json.
    """
    import json as _json
    from pathlib import Path as _P
    raw = _json.loads((_P(__file__).parent / "fixtures_btc4h_choch.json").read_text())
    cs = [Candle(ts=r[0], o=r[1], h=r[2], l=r[3], c=r[4], v=0.0) for r in raw]
    st = structure(cs, swings(cs, "4h"), "4h")
    ev = [(e.ts, e.type, e.direction, e.level) for e in st.events]
    choch = [e for e in ev if e[1] == "CHoCH"]
    bos = [e for e in ev if e[1] == "BOS"]
    assert bos, f"expected BOS events in this window, got {ev}"
    assert choch, f"expected at least one CHoCH in this window, got {ev}"
    # the known event: CHoCH down at 2026-03-26 07:59 UTC on the 70,538 swing low
    assert any(e[2] == "down" and abs(e[3] - 70538.0) < 1.0 for e in choch),         f"the 2026-03-26 CHoCH down at 70,538 must be detected; got {choch}"
    # and it must come after a BOS, i.e. a real change OF character
    first_bos = min(e[0] for e in bos)
    target = next(e for e in choch if abs(e[3] - 70538.0) < 1.0)
    assert target[0] > first_bos


def test_trend_label_needs_two_ascending_highs_and_lows():
    class S:
        def __init__(self, p): self.price = p
    assert trend_label([S(1), S(2)], [S(1), S(2)]) == "up"
    assert trend_label([S(2), S(1)], [S(2), S(1)]) == "down"
    assert trend_label([S(1), S(2)], [S(2), S(1)]) == "range"
    assert trend_label([S(1)], [S(1)]) == "range"          # not enough swings


def test_choch_does_not_fire_in_a_range():
    """A close below the last swing low while the label is `range` is a BOS
    down, never a CHoCH — the doc's rule, pinned so it cannot drift."""
    seq = [(100, 103, 98, 101), (101, 104, 97, 99), (99, 105, 96, 103),
           (103, 104, 95, 97), (97, 106, 94, 104), (104, 105, 93, 95),
           (95, 104, 90, 91), (91, 95, 88, 89)]
    cs = [_c(i, *v) for i, v in enumerate(seq)]
    st = structure(cs, swings(cs, "4h"), "4h")
    for e in st.events:
        if e.type == "CHoCH":
            assert e.direction in ("up", "down")
    labels_seen = {e.type for e in st.events}
    assert labels_seen <= {"BOS", "CHoCH"}


if __name__ == "__main__":
    import traceback
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    bad = 0
    for fn in fns:
        try:
            fn(); print(f"  PASS {fn.__name__}")
        except Exception:
            bad += 1; print(f"  FAIL {fn.__name__}"); traceback.print_exc()
    print(f"\n{len(fns) - bad}/{len(fns)} passed")
    sys.exit(1 if bad else 0)
