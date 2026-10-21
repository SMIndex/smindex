"""SMI sweep schedule + Hyperliquid budget cap checks (2026-10-21).

    cd backend && python scripts/test_sweep_schedule.py
"""
import os
import sys

sys.path.insert(0, os.getcwd())
from app.services.analytics import position_sweep as ps  # noqa: E402
from app.services.hyperliquid import client as hl  # noqa: E402

results = []


def check(name, ok, detail=""):
    results.append(ok)
    print(("PASS " if ok else "FAIL ") + name + (f"  ({detail})" if detail else ""))


IV = 1200
# measured cycle lengths today: 13.6 min average, 15.7 min longest
d = ps.next_start_delay(0, 13.6 * 60, IV)
check("13.6 min cycle: next start 20 min after this start", abs(d - (IV - 816)) < 1e-6, f"sleep {d:.0f}s")
d = ps.next_start_delay(0, 15.7 * 60, IV)
check("15.7 min cycle: still on the 20 min slot", abs(d - (IV - 942)) < 1e-6, f"sleep {d:.0f}s")
d = ps.next_start_delay(0, 25 * 60, IV)
check("25 min cycle: the 20 min slot is skipped, next at 40 min", abs(d - (40 * 60 - 25 * 60)) < 1e-6, f"sleep {d:.0f}s")
d = ps.next_start_delay(0, 47 * 60, IV)
check("47 min cycle: 2 slots skipped, next at 60 min", abs(d - (60 * 60 - 47 * 60)) < 1e-6, f"sleep {d:.0f}s")
d = ps.next_start_delay(0, 20 * 60, IV)
check("cycle ends exactly on the slot: no back-to-back start", d > 0, f"sleep {d:.0f}s")

check("background cap leaves 30 % free", hl.background_cap() == 840 and hl._class_ceiling(hl.BACKGROUND) <= 840,
      f"cap {hl.background_cap()}, background ceiling {hl._class_ceiling(hl.BACKGROUND)}")
check("explorer and critical keep their ceilings", hl._class_ceiling(hl.EXPLORER) == hl._soft_ceiling()
      and hl._class_ceiling(hl.CRITICAL) == hl.HARD_CEILING)

print(f"\n{sum(results)}/{len(results)} passed")
sys.exit(0 if all(results) else 1)
