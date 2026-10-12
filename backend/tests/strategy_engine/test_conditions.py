"""Condition catalog counts (Part 4 mapping / Part 6 UI rule lists)."""
from app.strategy_engine import conditions as C
from app.strategy_engine.seed import STRATEGIES

EXPECTED = {
    "s01_liq_sweep": 11, "s02_funding_flow": 5, "s03_whale_follow": 5,
    "s04_vol_compression": 10, "s05_session_open": 7, "s06_hull_fisher_ema": 13,
    "s06u_hull_fisher_ema": 8,   # ungated: same chart/timing rules, no on-chain gate
    "s05c_session_open": 7,      # chase entry: identical rules to s05
}


def test_condition_counts_match_docs():
    assert C.condition_counts() == EXPECTED
    assert sum(C.condition_counts().values()) == 66   # 59 + s05c (7, identical to s05)


def test_every_strategy_has_conditions():
    ids = {sid for sid, _ in STRATEGIES}
    assert set(C.CONDITIONS) == ids
    for sid, rows in C.CONDITIONS.items():
        assert rows, sid
        for r in rows:
            assert r["name"] and r["subline"]
