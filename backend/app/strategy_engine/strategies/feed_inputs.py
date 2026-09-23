"""Which data feed each Mind reason / veto reads (spec v1.1 Part D.6, D-68).

Derived from the detector code, not from the docs. Used ONLY in replay: when a
feed is unavailable at a boundary (`Snapshot.unavailable`), a reason listing it
leaves both sums of raw conviction and a veto listing it is skipped and logged
as unevaluated. Live evaluation never sets `unavailable`, so this map has no
effect on the worker.

Feed names: oi (strat_oi_1m), taker (strat_trades_1m), liq (strat_liquidations
tape), liqmap (liquidation map = positions_rows → Structure.clusters), gauge
(strat_gauge), book (strat_book_5s), cohort (analytics_positions cohort signal),
events (strat_events calendar).

The fuel / cluster families divide by a calibrated normaliser that falls back to
0.10% of OI when no calibration row exists, so they list `oi` as well — otherwise
an OI outage would score them 0 through the fallback denominator, which is
exactly what D.6 forbids. `day_type` is structure-derived and is not a feed
(its funding / OI / taker inputs default to neutral when absent).
"""
from __future__ import annotations

REASON_INPUTS: dict[str, dict[str, tuple[str, ...]]] = {
    "M1": {
        # `location` is NOT feed-gated: its input is the swept level's type; a liq cluster is one
        # candidate among PWL / PDL / zones / equal lows / session levels and simply isn't a
        # candidate when the map is absent (the reason is still fully evaluable on the level swept)
        "fuel": ("liq", "oi"),                 # snap.liq(...) / liq_p90
        "cleared": ("oi",),                    # snap.oi_change over the sweep
        "delta_flip": ("taker",),              # snap.taker_candle
        "absorption": ("taker",),              # snap.cvd at the prior 15m swing
        "cohort": ("cohort",),
    },
    "M2": {
        "oi_new_positioning": ("oi",),
        "oi_holding": ("oi",),
        "delta_break": ("taker",),
        "funding_young": ("gauge",),
        "cluster_cleared": ("liq", "oi"),
    },
    "M3": {
        "trap": ("oi",),
        "cvd_divergence": ("taker",),
        "thin_bids": ("book",),
        "cluster_fuel": ("liqmap", "oi"),
        "funding_up": ("gauge",),
    },
    "M4": {
        "cohort_reducing": ("cohort",),
        "oi_extreme": ("oi",),
        "funding_elevated": ("gauge",),
        "weak_bounce": ("taker", "oi"),        # buy-ratio × OI-rebuild penalty
        "cluster_reward": ("liqmap", "oi"),
        "delta_flip": ("taker",),
        "divergence": ("taker",),
    },
    "M5": {
        "fuel": ("liq", "oi"),
        "cleared": ("oi",),
        "delta_flip": ("taker",),
        "cohort": ("cohort",),
    },
    "M6": {
        "oi_commitment": ("oi",),
        "delta_reclaim": ("taker",),
        "funding_room": ("gauge",),
        "cohort": ("cohort",),
    },
}

VETO_INPUTS: dict[str, dict[str, tuple[str, ...]]] = {
    "M1": {
        "event_30m": ("events",),
        "cluster_below_uncleared": ("liqmap", "oi"),
        "funding_extreme_same_side": ("gauge",),
    },
    "M2": {
        "short_covering": ("oi",),
        "oi_exit_retrace": ("oi",),
        "event_30m": ("events",),
    },
    "M3": {
        "acceptance": ("oi",),
        "squeeze_risk": ("gauge", "cohort"),
        "cohort_adding_longs": ("cohort",),
        "event_30m": ("events",),
        "funding_extreme_short_side": ("gauge",),
    },
    "M4": {
        "cohort_adding_longs": ("cohort",),
        "oi_rebuilding": ("oi",),
        "event_30m": ("events",),
    },
    "M5": {
        "event_30m": ("events",),
        "funding_extreme_same_side": ("gauge",),
    },
    "M6": {
        "oi_falling": ("oi",),
        "event_30m": ("events",),
        "funding_extreme_long_side": ("gauge",),
    },
}
