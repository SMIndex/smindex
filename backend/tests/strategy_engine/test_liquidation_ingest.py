"""s01 liquidation feed hookup (owner decision D, 2026-09-04) + s06 band label (B).

The fill fixtures are REAL userFills payloads captured by the copy-tracker ws
tier on prod (leader_trade_events.raw, source hl_ws) — the `liquidation`
object shape is exactly what Hyperliquid sends.
"""
from app.services.hyperliquid.tracker import liquidation_row
from app.strategy_engine.strategies.s06_hull_fisher_ema import band_occupancy

MAKER = "0x2e2f5ea6a0c1b9a1f0ec0e2b9b2f0c5a4d3e2f10"

# prod row id 4123055: tracked maker SOLD (side A, "Open Short") to a liquidation
# of another user → that user was BUYING to close → a SHORT was liquidated.
COUNTERPARTY_FILL = {
    "px": "0.004373", "sz": "22802.0", "dir": "Open Short", "fee": "-0.002991", "oid": 535702544257,
    "tid": 372067046451935, "coin": "PUMP", "side": "A", "time": 1788498851443, "crossed": False,
    "feeToken": "USDC", "closedPnl": "0.0",
    "liquidation": {"markPx": "0.004371", "method": "market",
                    "liquidatedUser": "0x2daacd91eafa0d52c158cd2484e874b8d487bb00"},
    "startPosition": "-375797614.0",
}


def test_counterparty_fill_maps_to_liquidated_side():
    row = liquidation_row(MAKER, COUNTERPARTY_FILL)
    assert row["coin"] == "PUMP" and row["ts"] == 1788498851443
    assert row["side"] == "short"                       # maker sold → liquidated user bought → short liquidated
    assert row["liquidated_user"] == "0x2daacd91eafa0d52c158cd2484e874b8d487bb00"
    assert row["method"] == "market" and row["mark_px"] == 0.004371
    assert abs(row["notional"] - 0.004373 * 22802.0) < 1e-9
    assert row["coverage"] == "partial"
    # maker BOUGHT → liquidated user sold → a LONG was liquidated
    row_b = liquidation_row(MAKER, {**COUNTERPARTY_FILL, "side": "B", "dir": "Open Long"})
    assert row_b["side"] == "long"


def test_own_liquidation_uses_own_fill_side():
    f = {**COUNTERPARTY_FILL, "dir": "Close Long", "liquidation": {"markPx": "0.00437", "method": "backstop",
                                                                   "liquidatedUser": MAKER.upper()}}
    row = liquidation_row(MAKER, f)
    assert row["side"] == "long" and row["method"] == "backstop"   # own sell = own long liquidated
    assert row["liquidated_user"] == MAKER


def test_non_liquidation_fill_is_ignored():
    f = {k: v for k, v in COUNTERPARTY_FILL.items() if k != "liquidation"}
    assert liquidation_row(MAKER, f) is None


def test_band_occupancy_short_window_is_none_and_share_is_bounded():
    assert band_occupancy([{"h": 2, "l": 1, "c": 1.5}] * 50, 0.5, 3.0) is None
    # long trending series: closes climb 0.1%/bar with a 0.2% range — EMA200 lags, distance grows past 3 ATR
    bars = []
    px = 1000.0
    for i in range(1200):
        px *= 1.001
        bars.append({"h": px * 1.001, "l": px * 0.999, "c": px})
    share, n = band_occupancy(bars, 0.5, 3.0)
    assert n == 800 and 0.0 <= share <= 1.0
    assert share < 0.5                                   # a persistent trend sits mostly OUTSIDE the band
