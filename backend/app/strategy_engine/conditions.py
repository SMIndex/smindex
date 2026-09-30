"""Per-strategy condition catalog — one entry per spec §5 rule (docs 01–06).

This is the single source for (a) the Part-4 signal→field mapping table and
(b) the Part-6 UI conditions list. Rule counts must match the docs; the counts
are asserted in tests and reported. Each entry: name + subline (the threshold).
Until the engine writes evaluations, the UI renders these grey with
"Waiting for first evaluation".
"""
from __future__ import annotations

CONDITIONS: dict[str, list[dict]] = {
    "s01_liq_sweep": [
        {"name": "Cascade size", "subline": "market-liq notional 5m >= max($3M BTC/$1.5M ETH, 0.15% OI)"},
        {"name": "Distinct wallets", "subline": ">= 25 liquidated wallets in the window"},
        {"name": "Cascade move", "subline": "price move >= 1.2 ATR(14) during the window"},
        {"name": "Quiet period", "subline": "no new market-liq on that side for 45s"},
        {"name": "Taker exhaustion", "subline": "taker vol on liquidated side < 40% of cascade peak"},
        {"name": "Book refill", "subline": "bid/ask depth within 0.3% >= 60% of pre-cascade avg"},
        {"name": "No next cluster", "subline": "cluster within 1 ATR beyond wick < 50% of swept size"},
        {"name": "Regime gate", "subline": "shared regime gate = TRADE_ALLOWED"},
        {"name": "Funding lean", "subline": "funding was leaning the liquidated side (score only)"},
        {"name": "CEX confirm", "subline": "CEX aggregate liquidations confirm (score only)"},
        {"name": "Timing", "subline": "1m/5m Fisher(9) turning toward the reversal"},
    ],
    "s02_funding_flow": [
        {"name": "Crowding EXTREME", "subline": "CEX funding EXTREME (|z|>=2 either side or Binance >=0.05% / <=-0.03%)"},
        {"name": "OI confirmation", "subline": "HL OI risen >= 3% over prior 24h"},
        {"name": "No early trim", "subline": "price not moved against crowd > 1 ATR in last 2h"},
        {"name": "Regime gate", "subline": "TRADE_ALLOWED and no macro inside the window"},
        {"name": "Timing", "subline": "15m Fisher turning against crowd or Hull flat/against"},
    ],
    "s03_whale_follow": [
        {"name": "Fresh agreement", "subline": "fresh_agree >= 3 cohort wallets same dir within 60m"},
        {"name": "Net direction", "subline": "net_dir >= +0.3 (long) or <= -0.3 (short)"},
        {"name": "Pullback to VWAP", "subline": "within 0.5 ATR of cohort VWAP, not >1 ATR adverse"},
        {"name": "Regime gate", "subline": "TRADE_ALLOWED"},
        {"name": "Timing", "subline": "15m Fisher turning / Hull slope in cohort direction"},
    ],
    "s04_vol_compression": [
        {"name": "BBW rank", "subline": "Bollinger band-width percentile(200) <= 20%"},
        {"name": "ATR rank", "subline": "ATR(14) percentile(200) <= 25%"},
        {"name": "Compression duration", "subline": ">= 8 consecutive candles ATR rank <= 35%"},
        {"name": "Break distance", "subline": "close beyond box by >= 0.15 ATR"},
        {"name": "Break range", "subline": "break candle range >= 1.5x box mean ATR"},
        {"name": "OI rotation", "subline": "OI >= +1.5% since box start; not fall >0.5% on break"},
        {"name": "1h alignment", "subline": "1h Hull(21) slope agrees with the break"},
        {"name": "4h not against", "subline": "4h Hull(21) against < 0.5x 4h ATR/bar"},
        {"name": "Depth beyond box", "subline": "depth 0.5% beyond edge >= 80% of in-box depth"},
        {"name": "Regime + rising vol", "subline": "TRADE_ALLOWED and realized vol rising"},
    ],
    "s05_session_open": [
        {"name": "High-vol day", "subline": "RV24h >= 60th pct(30d) OR OR height >= 1.2 ATR"},
        {"name": "OR break", "subline": "15m close beyond opening range by >= 0.1 ATR"},
        {"name": "Bias alignment", "subline": "bias x direction >= -0.2"},
        {"name": "Trend/timing", "subline": "1h Hull agrees, or flat with 15m Fisher turning"},
        {"name": "Not crowded", "subline": "funding gauge not EXTREME on the trade side"},
        {"name": "Regime gate", "subline": "TRADE_ALLOWED (blocks macro windows)"},
        {"name": "Volume", "subline": "break-candle taker vol >= 1.3x 20-candle avg"},
    ],
    "s06_hull_fisher_ema": [
        {"name": "Direction", "subline": "close vs EMA200 with Hull(55) slope (2 bars)"},
        {"name": "Trend established", "subline": ">= 12 consecutive closes on the trend side of EMA200"},
        {"name": "Not overextended", "subline": "distance from EMA200 between 0.5 and 3.0 ATR"},
        {"name": "Pullback", "subline": "2-8 candles toward Hull(55), within 0.5 ATR, no close beyond EMA"},
        {"name": "Fisher trigger", "subline": "Fisher(9) reached <= -1.5 then crosses signal"},
        {"name": "Hull still up", "subline": "Hull(55) slope still with trend on the trigger"},
        {"name": "1h Hull", "subline": "1h Hull(21) slope agrees"},
        {"name": "Bias not against", "subline": "bias x direction >= -0.2"},
        {"name": "OI with price", "subline": "OI 4h change same sign as price, |OI| >= 1%"},
        {"name": "Funding not extreme", "subline": "gauge crowding_level != EXTREME on the trade side"},
        {"name": "Realized vol", "subline": "last 1h realized vol > its 24h average"},
        {"name": "No macro", "subline": "no macro event within next 2h / last 30m"},
        {"name": "Regime gate", "subline": "shared regime gate = TRADE_ALLOWED"},
    ],
}
# ungated 06: the same chart/timing rules WITHOUT the on-chain gate (doc 06 §15 prompt 2)
CONDITIONS["s06u_hull_fisher_ema"] = CONDITIONS["s06_hull_fisher_ema"][:8]
# s05c chase entry: identical rules to s05, only the entry price differs
CONDITIONS["s05c_session_open"] = list(CONDITIONS["s05_session_open"])


# Models M1–M6 (docs 11–16 §Sequence) — parallel catalog (D-01). The Mind reasons
# are NOT conditions: they are scored, not gated; the UI renders them from
# reasons_json. These are the setup gates that must all hold before the Mind runs.
MODEL_CONDITIONS: dict[str, list[dict]] = {
    "m1_sweep_reclaim": [
        {"name": "Level", "subline": "PWL/PDL/Asia-London low, 1h/4h equal lows, 4h OB/FVG edge, or liq cluster >= 0.15% OI"},
        {"name": "Sweep depth", "subline": "wick 0.1–0.5 ATR(15m) beyond the level (mirror for shorts)"},
        {"name": "Reclaim", "subline": "close back inside within 3 candles, body/range >= 0.5"},
        {"name": "Discount", "subline": "price in the discount half of the 4h range (premium for shorts)"},
        {"name": "Daily bias", "subline": "daily bias not against the reversal"},
        {"name": "Attempts", "subline": "< 3 attempts per coin per day"},
    ],
    "m2_bos_order_block": [
        {"name": "HTF context", "subline": "4h trend with the trade or fresh 4h CHoCH confirmed by 1h BOS"},
        {"name": "Displacement BOS", "subline": "1h BOS within 24h whose candle is a displacement (range >= 1.5 ATR, body >= 60%)"},
        {"name": "OI on break", "subline": "OI >= +1% over the break (<= 0 = short covering veto)"},
        {"name": "Delta on break", "subline": "taker buy ratio >= 0.65 on the break (mirror)"},
        {"name": "Funding young", "subline": "funding z < 1.5 on the trade side"},
        {"name": "Zone", "subline": "live 1h OB + FVG from the displacement, untaken this cycle"},
        {"name": "Retrace", "subline": "calm 15m retrace >= 50% toward FVG mid, no close beyond the OB, buy ratio 0.35–0.55"},
    ],
    "m3_failed_auction": [
        {"name": "Day type", "subline": "range day, or trend day inside a live 4h supply/demand zone"},
        {"name": "Premium", "subline": "price in the premium of the 4h range (discount for longs)"},
        {"name": "Level", "subline": "4h swing, 4h OB/FVG, 1h/4h equal highs, PDH/PWH or prior-session high — predating this session"},
        {"name": "Push OI", "subline": "OI >= +0.5% over the hour into the high"},
        {"name": "Swing failure", "subline": "15m higher high than the prior swing high, close back below the level"},
        {"name": "CVD divergence", "subline": "CVD at the new high below CVD at the prior high"},
        {"name": "Retest", "subline": "later candle within 0.2 ATR of the failed high, no close above"},
    ],
    "m4_htf_choch": [
        {"name": "Extension", "subline": ">= 5 consecutive 4h BOS or >= 8% in 3 days"},
        {"name": "OI extreme", "subline": "OI within 3% of its 7-day high"},
        {"name": "Funding peak", "subline": "funding z >= 1.0 at some point in 24h"},
        {"name": "4h CHoCH", "subline": "4h close beyond the most recent 4h higher low (within 72h)"},
        {"name": "1h zone", "subline": "bounce into the live 1h supply zone left by the CHoCH displacement"},
        {"name": "Weak bounce", "subline": "bounce buy ratio <= 0.55, OI not +2%"},
        {"name": "Cohort", "subline": "cohort net-long change 24h <= 0"},
    ],
    "m5_session_liquidity_run": [
        {"name": "Window", "subline": "London 07:00–09:00 or New York 13:00–15:00 UTC"},
        {"name": "Asia range", "subline": "frozen 07:00, height 0.8–4.0 ATR(15m)"},
        {"name": "Open location", "subline": "session opened inside the range or within 0.2 ATR of an edge"},
        {"name": "Raid", "subline": "15m wick 0.1–0.6 ATR beyond the Asia edge"},
        {"name": "Reclaim", "subline": "close back inside within 3 candles, body/range >= 0.5, before the window end"},
        {"name": "One attempt", "subline": "one attempt per window per coin"},
    ],
    "m6_weekly_open_reclaim": [
        {"name": "Weekly levels", "subline": "WO / PWH / PWL frozen Monday 00:00 UTC"},
        {"name": "Loss", "subline": ">= 1 4h close beyond the WO Mon–Wed, excursion >= 0.8 ATR(4h)"},
        {"name": "Reclaim", "subline": "15m close then 1h close back across the WO, Mon 12:00–Thu 23:59"},
        {"name": "OI", "subline": "OI >= +1% over the 4h into the reclaim"},
        {"name": "Delta", "subline": "buy ratio >= 0.55 over the 4h (mirror)"},
        {"name": "Funding", "subline": "funding z <= 1.0 on the trade side"},
        {"name": "Cohort", "subline": "cohort net change 24h >= 0"},
        {"name": "One per week", "subline": "one attempt per coin per direction per week"},
    ],
}


def condition_counts() -> dict[str, int]:
    return {sid: len(rows) for sid, rows in CONDITIONS.items()}
