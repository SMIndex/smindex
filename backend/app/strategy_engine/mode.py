"""effective_mode computation — no gates.

`requested_mode` is user-writable; `effective_mode` is computed here:
  off   -> off    (evaluation still runs and logs; paper fills are not opened)
  paper -> paper  (paper is paper: no promotion gates, no PF gates, no data-age
                   preconditions, no custody checks; the risk engine annotates)
  live  -> off    (the ONLY remaining gate: no execution adapter is built)

2026-09-11 (D-99/D-100): paper is no longer un-gated. The risk engine's
kill-switch and daily-cap now block paper entries exactly as they would block
live entries — the executor is the ONLY difference between paper and live.
DISABLED_STRATEGIES are never scheduled at all.
"""
from __future__ import annotations

HAS_EXECUTION_ADAPTER = False       # no exec/hl adapter exists — live stays impossible
LIVE_DISABLED_REASON = "no execution adapter built"

# All six implemented (s06 has gated + ungated instances). Kept for the tests /
# UI counts; it no longer gates anything.
STRATEGY_IMPLEMENTED: dict[str, bool] = {
    "s01_liq_sweep": True, "s02_funding_flow": True, "s03_whale_follow": True,
    "s04_vol_compression": True, "s05_session_open": True, "s05c_session_open": False,
    "s06_hull_fisher_ema": True, "s06u_hull_fisher_ema": True,
}

# D-99: permanently disabled. s05c was an undocumented chase-entry variant of
# s05 — it appears in NO strategy doc (01–06 or 10–17); it was added as an
# owner-approved A/B on 2026-09-04 and never promoted to a documented strategy.
# It is the single largest loser in the live paper window. Its trade history is
# KEPT; it is simply never scheduled or evaluated again.
DISABLED_STRATEGIES: dict[str, str] = {
    "s05c_session_open": ("undocumented chase-entry variant of s05 (not in any strategy doc); "
                          "disabled 2026-09-11 after -$328 over 63 paper trades with a "
                          "rolling-20 PF of 0.04"),
}


def is_disabled(strategy_id: str) -> bool:
    return strategy_id in DISABLED_STRATEGIES


# Models M1–M6 (D-01): parallel registry, paper only, no live mode exists.
MODEL_IMPLEMENTED: dict[str, bool] = {
    "m1_sweep_reclaim": True, "m2_bos_order_block": True, "m3_failed_auction": True,
    "m4_htf_choch": True, "m5_session_liquidity_run": True, "m6_weekly_open_reclaim": True,
}


def compute_effective_mode(
    requested: str,
    *,
    engine_enabled: bool = True,          # noqa: ARG001 — kept for call-site compatibility
    live_allowlist_ok: bool = False,      # noqa: ARG001
    kill_switch_tripped: bool = False,
    daily_cap_hit: bool = False,
    strategy_implemented: bool = True,    # noqa: ARG001
    strategy_id: str | None = None,
) -> tuple[str, str]:
    """Returns (effective_mode, reason). off | paper-paused | paper. `live` is
    never returned while HAS_EXECUTION_ADAPTER is False.

    D-99/D-100: the kill switch and the daily cap are ENFORCED in paper. They
    return `paper-paused`, which the evaluator treats as "evaluate and log, but
    open no new entries" — identical to what they would do in live."""
    if strategy_id and is_disabled(strategy_id):
        return "off", f"disabled: {DISABLED_STRATEGIES[strategy_id]}"
    if requested == "off":
        return "off", "requested off — evaluating only, no paper fills"
    if requested == "live" and not HAS_EXECUTION_ADAPTER:
        return "off", LIVE_DISABLED_REASON        # unreachable venue wins over everything
    if kill_switch_tripped:
        return "paper-paused", "kill switch tripped (rolling-20 PF < 0.9) — manual re-arm required"
    if daily_cap_hit:
        return "paper-paused", "daily loss cap hit — resumes at 00:00 UTC"
    if requested == "paper":
        return "paper", "paper"
    return "live", "live"
