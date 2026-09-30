"""Auto-copy decision rules (spec 2.4-2.8) as pure functions — no I/O, so every
rule is unit-tested in tests/test_copy_auto_rules.py. The engine gathers the
inputs (chain reads, marks, basis, leader state) and calls these in order."""
import math
from dataclasses import dataclass

STALE_SEC = 10.0                 # gate 11
DEFAULT_BASIS_BPS = 30.0         # gate 6 (existing guard value)
FAILS_BEFORE_PAUSE = 3           # auto-pause rule

PRESETS = {
    "careful":  {"margin_usd": 5.0,  "max_leverage": 3.0, "max_positions": 2, "sl_margin_pct": 30.0, "daily_loss_usd": 15.0},
    "standard": {"margin_usd": 10.0, "max_leverage": 5.0, "max_positions": 3, "sl_margin_pct": 50.0, "daily_loss_usd": 30.0},
    "active":   {"margin_usd": 25.0, "max_leverage": 5.0, "max_positions": 5, "sl_margin_pct": 50.0, "daily_loss_usd": 75.0},
}
DEFAULTS = {"mode": "shadow", "sizing": "fixed", "margin_usd": 10.0, "allocation_usd": 50.0,
            "max_leverage": 5.0, "max_positions": 3, "mirror_adds": True, "mirror_reduces": True,
            "reopen_on_flip": True, "drift_pct": 0.5, "sl_margin_pct": 50.0, "tp_pct": None,
            "daily_loss_usd": 30.0, "total_loss_usd": 100.0, "markets": None}
RANGES = {"margin_usd": (5.0, 100000.0), "allocation_usd": (5.0, 1e7), "max_leverage": (1.0, 100.0),
          "max_positions": (1, 10), "drift_pct": (0.1, 2.0), "sl_margin_pct": (10.0, 90.0),
          "tp_pct": (1.0, 1000.0), "daily_loss_usd": (1.0, 1e7), "total_loss_usd": (1.0, 1e7)}


@dataclass
class Action:
    kind: str                    # open | add | reduce | close
    side: str                    # position side we act on: long | short
    leader_size: float           # size of the leader's move (coin units)
    leader_prev: float           # leader |position| before the fill
    px: float                    # leader fill price
    note: str = ""


def normalize(event_type: str, side: str, fill: dict | None) -> list[Action]:
    """HL tracker event -> ordered list of actions to mirror.
    A flip arrives as ONE event ('closed', old side) whose fill `sz` spans the
    close AND the new open (dir 'Long > Short'); split it here from the raw fill."""
    f = fill or {}
    px = float(f.get("px") or 0)
    sz = float(f.get("sz") or 0)
    start = abs(float(f.get("startPosition") or 0))
    d = str(f.get("dir") or "")
    if d in ("Long > Short", "Short > Long"):
        old = "long" if d.startswith("Long") else "short"
        new = "short" if old == "long" else "long"
        acts = [Action("close", old, start, start, px, "flip")]
        if sz - start > 1e-12:
            acts.append(Action("open", new, sz - start, 0.0, px, "flip"))
        return acts
    kind = {"opened": "open", "increased": "add", "reduced": "reduce", "closed": "close"}.get(event_type)
    if kind is None:
        return []
    return [Action(kind, side, sz, start, px)]


def choose_leverage(leader_lev: float | None, user_max: float, market_max: float) -> tuple[float, str]:
    """Lowest of the three (spec 2.5). Unknown leader leverage is stated, never invented."""
    cands = [x for x in (leader_lev, user_max, market_max) if x and x > 0]
    lev = min(cands) if cands else 1.0
    note = "" if leader_lev else "leader leverage unknown; used min(your max, market max)"
    return max(1.0, math.floor(lev * 100) / 100), note


def size_open(*, sizing: str, margin_usd: float, allocation_usd: float, margin_in_use: float,
              leverage: float, price: float, leader_size: float, leader_account_value: float | None,
              size_decimals: int, min_scaled: int = 0) -> tuple[float, float, str | None]:
    """-> (size, margin, skip_reason). Never sizes down silently past a limit."""
    if price <= 0 or leverage <= 0:
        return 0.0, 0.0, "no price"
    if sizing == "proportional":
        if not leader_account_value or leader_account_value <= 0:
            return 0.0, 0.0, "leader account value unknown"
        size = leader_size * (allocation_usd / leader_account_value)
        margin = size * price / leverage
    else:
        margin = margin_usd
        size = margin * leverage / price
    if margin_in_use + margin > allocation_usd + 1e-9:
        return 0.0, 0.0, "allocation fully used"
    q = 10 ** size_decimals
    size = math.floor(size * q + 1e-9) / q
    if size <= 0 or (min_scaled and size * q < min_scaled):
        return 0.0, 0.0, "below venue minimum"
    return size, round(size * price / leverage, 6), None


def scaled_change(our_size: float, leader_move: float, leader_prev: float, size_decimals: int) -> float:
    """Mirror the leader's FRACTION (spec 2.4: leader cut 30% -> we cut 30%)."""
    if leader_prev <= 0 or our_size <= 0:
        return 0.0
    frac = min(1.0, leader_move / leader_prev)
    q = 10 ** size_decimals
    return math.floor(our_size * frac * q + 1e-9) / q


def stop_price(side: str, entry: float, leverage: float, sl_margin_pct: float | None) -> float | None:
    """SL at a price move equal to sl_margin_pct of the margin (50% at 5x = 10%)."""
    if not sl_margin_pct or entry <= 0 or leverage <= 0:
        return None
    move = (sl_margin_pct / 100.0) / leverage
    return entry * (1 - move) if side == "long" else entry * (1 + move)


def drift_ok(side: str, leader_px: float, perpl_px: float, drift_pct: float) -> bool:
    """Gate 5: Perpl no more than drift_pct WORSE than the leader's fill."""
    if leader_px <= 0 or perpl_px <= 0:
        return False
    lim = drift_pct / 100.0
    eps = leader_px * 1e-12          # the limit itself counts as within (float: 100*1.005 = 100.4999…)
    if side == "long":
        return perpl_px <= leader_px * (1 + lim) + eps
    return perpl_px >= leader_px * (1 - lim) - eps


@dataclass
class GateInput:
    side: str                     # side of the position we would open
    kill_switch_on: bool          # global copy switch + AUTO_COPY_ENABLED (live only)
    allowlisted: bool
    live: bool
    market_id: int
    markets_allowed: list | None
    market_in_use: bool
    leader_px: float
    perpl_px: float | None
    drift_pct: float
    basis_bps: float | None
    open_positions: int
    max_positions: int
    daily_realized: float
    total_realized: float
    daily_loss_usd: float | None
    total_loss_usd: float | None
    free_margin: float | None
    margin_needed: float
    leverage: float
    market_max_leverage: float
    event_age_sec: float


def run_gates(g: GateInput) -> tuple[str | None, dict]:
    """Spec 2.6 gates in order. -> (first failing gate id or None, values seen).
    Gate 2 (Live) does not stop Shadow here: Shadow runs the full pipeline and
    logs 'would have placed' (overnight prompt, which overrides the spec)."""
    v = {}
    v["g1_kill_switch_allowlist"] = {"kill_switch_on": g.kill_switch_on, "allowlisted": g.allowlisted, "live": g.live}
    if not g.allowlisted or (g.live and not g.kill_switch_on):
        return ("g1_allowlist" if not g.allowlisted else "g1_kill_switch"), v
    v["g3_market"] = {"market_id": g.market_id, "allowed": g.markets_allowed}
    if not g.market_id:
        return "g3_not_listed_on_perpl", v
    if g.markets_allowed and g.market_id not in g.markets_allowed:
        return "g3_market_not_enabled", v
    v["g4_slot"] = {"market_in_use": g.market_in_use}
    if g.market_in_use:
        return "g4_market_already_in_use", v
    v["g5_drift"] = {"leader_px": g.leader_px, "perpl_px": g.perpl_px, "limit_pct": g.drift_pct}
    if g.perpl_px is None or not drift_ok(g.side, g.leader_px, g.perpl_px, g.drift_pct):
        return "g5_price_moved", v
    v["g6_basis"] = {"basis_bps": g.basis_bps, "cap_bps": DEFAULT_BASIS_BPS}
    if g.basis_bps is None or abs(g.basis_bps) > DEFAULT_BASIS_BPS:
        return "g6_basis", v
    v["g7_positions"] = {"open": g.open_positions, "max": g.max_positions}
    if g.open_positions >= g.max_positions:
        return "g7_max_positions", v
    v["g8_loss"] = {"daily": g.daily_realized, "total": g.total_realized,
                    "daily_limit": g.daily_loss_usd, "total_limit": g.total_loss_usd}
    if g.daily_loss_usd and g.daily_realized <= -g.daily_loss_usd:
        return "g8_daily_loss", v
    if g.total_loss_usd and g.total_realized <= -g.total_loss_usd:
        return "g8_total_loss", v
    v["g9_margin"] = {"free": g.free_margin, "needed": g.margin_needed}
    if g.free_margin is None or g.free_margin < g.margin_needed:
        return "g9_margin", v
    v["g10_leverage"] = {"leverage": g.leverage, "market_max": g.market_max_leverage}
    if g.leverage > g.market_max_leverage + 1e-9:
        return "g10_leverage", v
    v["g11_staleness"] = {"age_sec": round(g.event_age_sec, 2), "max_sec": STALE_SEC}
    if g.event_age_sec > STALE_SEC:
        return "g11_too_late", v
    return None, v


def pause_reason(*, consecutive_failures: int, daily_realized: float, total_realized: float,
                 daily_loss_usd: float | None, total_loss_usd: float | None,
                 key_rejected: bool = False, leader_liquidated: bool = False) -> str | None:
    """Spec 2.8 — the subscription pauses itself; open positions are kept and
    still closed when the leader closes."""
    if key_rejected:
        return "Perpl key rejected or expired"
    if leader_liquidated:
        return "leader was liquidated"
    if consecutive_failures >= FAILS_BEFORE_PAUSE:
        return f"{consecutive_failures} failed orders in a row"
    if daily_loss_usd and daily_realized <= -daily_loss_usd:
        return f"daily loss limit ${daily_loss_usd:g} reached"
    if total_loss_usd and total_realized <= -total_loss_usd:
        return f"total loss limit ${total_loss_usd:g} reached"
    return None


def leader_was_liquidated(fill: dict, leader: str) -> bool:
    """HL puts a `liquidation` object on EVERY fill that involves a liquidation,
    including the counterparty's (a market maker taking over a liquidated
    position). Only the leader's own liquidation counts (spec 2.8)."""
    liq = (fill or {}).get("liquidation") or {}
    return isinstance(liq, dict) and str(liq.get("liquidatedUser", "")).lower() == (leader or "").lower()


def summary_sentence(s: dict) -> str:
    """The setup sheet's one-line summary (spec 2.11)."""
    size = (f"${s['margin_usd']:g}" if s.get("sizing", "fixed") == "fixed"
            else f"a share of ${s['allocation_usd']:g}")
    sl = f", stop at {s['sl_margin_pct']:g}% of margin" if s.get("sl_margin_pct") else ", no stop loss"
    return (f"Copies opens with {size} at up to {s['max_leverage']:g}x, max {s['max_positions']} positions"
            f"{sl}, stops for the day after -${s['daily_loss_usd']:g}.")
