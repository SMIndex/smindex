"""Bias score (doc 00 §4) — -1 (strong short) to +1 (strong long), every 5 min.

Weights are verbatim (doc 00 §4):
  whale cohort net-positioning change 24h  0.35
  HL funding sign/extremity (contrarian)   0.25   (from the funding gauge tilt)
  HL vs Binance basis (rich HL => short)   0.15
  crowd long/short ratio (contrarian)      0.15
  1h Hull slope direction                  0.10

Phase-1 availability: cohort (from the EXISTING analytics tables), funding tilt
(gauge), and 1h Hull (from strat_candles) are wired; **basis and crowd are
marked 'not_available'** (basis needs Binance marks; crowd needs a HL long/short
ratio feed — neither exists yet). The score is the weighted sum of the AVAILABLE
components RENORMALISED by their weight mass, so an unavailable component is
never silently treated as zero. Every component and its availability is returned
in `components` for the UI (honest).
"""
from __future__ import annotations

from typing import Optional

WEIGHTS = {
    "cohort": 0.35,
    "funding": 0.25,
    "basis": 0.15,
    "crowd": 0.15,
    "hull_1h": 0.10,
}


def compute_bias(
    cohort_net_dir: Optional[float] = None,     # -1..+1 (doc 03 tilt) or None
    funding_tilt: Optional[float] = None,       # -1..+1 (gauge) or None
    basis: Optional[float] = None,              # not available in Phase 1
    crowd: Optional[float] = None,              # not available in Phase 1
    hull_1h_dir: Optional[int] = None,          # +1/-1/0 from timing.hull_direction
) -> dict:
    raw = {
        "cohort": cohort_net_dir,
        "funding": funding_tilt,
        "basis": basis,
        "crowd": crowd,
        "hull_1h": (float(hull_1h_dir) if hull_1h_dir is not None else None),
    }
    components: dict[str, dict] = {}
    num = 0.0
    wmass = 0.0
    for key, w in WEIGHTS.items():
        v = raw[key]
        if v is None:
            components[key] = {"value": None, "weight": w, "available": False}
        else:
            v = max(-1.0, min(1.0, float(v)))
            components[key] = {"value": v, "weight": w, "available": True}
            num += w * v
            wmass += w
    score = (num / wmass) if wmass > 0 else None      # renormalised by available mass
    return {"bias_score": score, "weight_mass_available": wmass, "components": components}
