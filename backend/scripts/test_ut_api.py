"""Unusual Timing web API helpers: odds_text, apply_filters, sort_rows
(app/services/ut/api.py). Pure, no database.

    python scripts/test_ut_api.py
"""
import os
import sys
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.services.ut import api  # noqa: E402

checks = []


def check(name, got, want):
    ok = got == want
    checks.append(ok)
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}: got {got!r} want {want!r}")


print("odds_text")
check("p 0.001 -> 1 in 1,000", api.odds_text(0.001), "1 in 1,000")
check("p 0.00025 -> 1 in 4,000", api.odds_text(0.00025), "1 in 4,000")
check("p 0.0000277 -> 1 in 36,000 (2 significant figures)", api.odds_text(0.0000277), "1 in 36,000")
check("p 0.1 -> 1 in 10", api.odds_text(0.1), "1 in 10")
check("p 0.3 -> 1 in 3", api.odds_text(0.3), "1 in 3")
check("p 0 -> under 1 in 1,000,000", api.odds_text(0.0), "under 1 in 1,000,000")
check("p 1e-9 -> under 1 in 1,000,000", api.odds_text(1e-9), "under 1 in 1,000,000")
check("None -> n/a", api.odds_text(None), "n/a")
check("p 1 -> 1 in 1", api.odds_text(1.0), "1 in 1")

NOW = datetime(2026, 11, 4, 12, 0, 0)
R = [
    {"wallet": "0xa", "level": "cluster", "score": 91.0, "hit_rate": 0.82, "events_preceded": 5, "age_days": 11.0, "coins": ["XMR", "BTC"],
     "open": {"coin": "XMR"}, "last_entry_at": (NOW - timedelta(minutes=24)).isoformat(), "misses": 2, "avg_move_2h": 7.4, "hits": 9},
    {"wallet": "0xb", "level": "strong", "score": 78.0, "hit_rate": 0.58, "events_preceded": 3, "age_days": 14.0, "coins": ["XMR"],
     "open": {"coin": "XMR"}, "last_entry_at": (NOW - timedelta(hours=3)).isoformat(), "misses": 5, "avg_move_2h": 5.9, "hits": 7},
    {"wallet": "0xc", "level": "watch", "score": 58.0, "hit_rate": 0.36, "events_preceded": 2, "age_days": None, "coins": ["ETH", "SOL"],
     "open": None, "last_entry_at": (NOW - timedelta(days=3)).isoformat(), "misses": 9, "avg_move_2h": 3.2, "hits": 5},
    {"wallet": "0xd", "level": "watch", "score": 52.0, "hit_rate": 0.44, "events_preceded": 1, "age_days": 140.0, "coins": ["ETH"],
     "open": None, "last_entry_at": None, "misses": 7, "avg_move_2h": None, "hits": 4},
]

print("apply_filters")
f = lambda **kw: [r["wallet"] for r in api.apply_filters(R, now=NOW, **kw)]  # noqa: E731
check("no filter keeps all", f(), ["0xa", "0xb", "0xc", "0xd"])
check("level strong keeps strong and cluster", f(level="strong"), ["0xa", "0xb"])
check("level watch keeps everything listed", f(level="watch"), ["0xa", "0xb", "0xc", "0xd"])
check("level cluster keeps cluster", f(level="cluster"), ["0xa"])
check("min_hit 50 keeps hit rates of 50 percent and more", f(min_hit=50), ["0xa", "0xb"])
check("min_events 3", f(min_events=3), ["0xa", "0xb"])
check("max_age_days 30 keeps KNOWN ages under 30", f(max_age_days=30), ["0xa", "0xb"])
check("max_age_days 90 drops the 140-day wallet and the unknown age", f(max_age_days=90), ["0xa", "0xb"])
check("coin ETH (case-insensitive)", f(coin="eth"), ["0xc", "0xd"])
check("has_open", f(has_open=True), ["0xa", "0xb"])
check("active within 1 h", f(active_within_h=1), ["0xa"])
check("active within 24 h", f(active_within_h=24), ["0xa", "0xb"])
check("active within 7 d drops the wallet with no entry", f(active_within_h=168), ["0xa", "0xb", "0xc"])
check("filters combine", f(level="watch", coin="XMR", has_open=True, min_events=4), ["0xa"])

print("sort_rows")
s = lambda k, d="desc": [r["wallet"] for r in api.sort_rows(R, k, d)]  # noqa: E731
check("score desc", s("score"), ["0xa", "0xb", "0xc", "0xd"])
check("score asc", s("score", "asc"), ["0xd", "0xc", "0xb", "0xa"])
check("hit_rate desc", s("hit_rate"), ["0xa", "0xb", "0xd", "0xc"])
check("misses asc", s("misses", "asc"), ["0xa", "0xb", "0xd", "0xc"])
check("age desc puts the unknown age last", s("age_days"), ["0xd", "0xb", "0xa", "0xc"])
check("age asc puts the unknown age last too", s("age_days", "asc"), ["0xa", "0xb", "0xd", "0xc"])
check("avg_move_2h desc, None last", s("avg_move_2h"), ["0xa", "0xb", "0xc", "0xd"])
check("coins desc (count)", s("coins")[:2], ["0xa", "0xc"])
check("open desc puts open positions first", s("open")[:2], ["0xa", "0xb"])
check("last_entry_at desc, None last", s("last_entry_at"), ["0xa", "0xb", "0xc", "0xd"])
check("unknown sort key falls back to score", s("nonsense"), ["0xa", "0xb", "0xc", "0xd"])
check("wallet asc", s("wallet", "asc"), ["0xa", "0xb", "0xc", "0xd"])

print("open positions from a stored state")
st = '{"assetPositions":[{"position":{"coin":"XMR","szi":"-120.5","positionValue":"2100000.0","leverage":{"value":40},"unrealizedPnl":"-24000","entryPx":"300"}},' \
     '{"position":{"coin":"BTC","szi":"0","positionValue":"0"}}]}'
ops = api.open_positions_from_state(st)
check("one open position, the flat one dropped", len(ops), 1)
check("short, notional, leverage, upnl", (ops[0]["side"], ops[0]["notional"], ops[0]["leverage"], ops[0]["upnl"]), ("short", 2100000.0, 40.0, -24000.0))
check("bad json gives no positions", api.open_positions_from_state("{not json"), [])

n_ok, n = sum(checks), len(checks)
print(f"\n{n_ok}/{n} checks passed")
sys.exit(0 if n_ok == n else 1)
