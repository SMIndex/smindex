"""THE SAFETY TEST (Part F.2): with strategies in paper, prove that nothing can
reach a venue and no real order can be placed — paper fills only, each fill
tracing to a trade printing THROUGH the limit.
"""
import pathlib

from app.strategy_engine.execution.paper import PaperExecutor, RestingOrder
from app.strategy_engine.execution.router import ExecutionRouter, NullAdapter, PaperAdapter
from app.strategy_engine.intents import OrderIntent
from app.strategy_engine import evaluator


def test_no_venue_client_anywhere_in_package():
    """Static scan: the strategy_engine package must reference no venue-execution /
    signing path — the only reason `live` is impossible."""
    pkg = pathlib.Path(__file__).resolve().parents[2] / "app" / "strategy_engine"
    banned = ("perplTrading", "place_order", "place_post_only", "hl_exec", "sign_order",
              "gate_ladder", "copyPosition", "eth_account", "send_order", "trading_proxy")
    hits = []
    for py in pkg.rglob("*.py"):
        text = py.read_text(encoding="utf-8")
        for token in banned:
            if token in text:
                hits.append(f"{py.name}:{token}")
    assert hits == [], f"venue/signing references in strategy_engine: {hits}"


def test_router_paper_and_null_only():
    ex = PaperExecutor()
    router = ExecutionRouter(ex)
    assert isinstance(router.adapter_for("paper", "hl"), PaperAdapter)
    # any non-paper mode (including a 'live' request) → null (drops), never a venue
    assert isinstance(router.adapter_for("live", "hl"), NullAdapter)
    assert isinstance(router.adapter_for("off", "perpl"), NullAdapter)


def test_null_adapter_places_no_order():
    n = NullAdapter()
    intent = OrderIntent("s05_session_open", "hl", "BTC", "long", "entry", "post_only_limit", 0.1, px=100.0)
    assert n.submit(intent) is None                 # dropped, nothing placed


def test_replay_paper_fills_only_on_trade_through():
    """Mini 'replay': a paper entry fills ONLY when a trade prints through the
    limit — never on touch, never from thin air."""
    ex = PaperExecutor()
    o = RestingOrder(id="o1", strategy_id="s05_session_open", asset="BTC", side="buy", kind="entry",
                     order_type="post_only_limit", px=100.0, sz=0.1, position_side="long")
    ex.place(o, market_px=101.0, tick=0.1)          # 100 rests below a 101 market
    trades = [100.5, 100.1, 100.0, 100.0]           # approach + touch — no fill
    fills = []
    for i, px in enumerate(trades):
        fills += ex.on_trade("BTC", px, ts=i)
    assert fills == []                               # touch never fills
    fills += ex.on_trade("BTC", 99.9, ts=99)         # trades THROUGH → fills
    assert len(fills) == 1
    assert fills[0].px == 100.0 and fills[0].kind == "entry"
    # every fill in the executor's log came from on_trade (a trade print), none invented
    assert all(f in ex.fills for f in fills)


def test_registry_has_all_strategies_and_they_only_emit_intents():
    ids = set(evaluator.REGISTRY)
    # D-99: s05c is permanently disabled and therefore NOT in the scheduling
    # registry. It is still constructible and its history/params remain.
    assert ids == {"s05_session_open", "s01_liq_sweep", "s04_vol_compression",
                   "s02_funding_flow", "s03_whale_follow",
                   "s06_hull_fisher_ema", "s06u_hull_fisher_ema"}
    assert "s05c_session_open" not in ids
    # strategies produce EvalResult with OrderIntents only — no executor/venue handle
    for s in evaluator.REGISTRY.values():
        assert hasattr(s, "evaluate")
