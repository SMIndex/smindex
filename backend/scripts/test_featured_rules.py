"""Featured traders acceptance test 2: every hard rule and the score with
crafted wallets. Each wallet is a list of round trips built to trip exactly
one rule (or none); the test asserts where it lands.

    python scripts/test_featured_rules.py
"""
import os
import random
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.services.featured.roundtrips import compute_metrics, suggested_settings, DAY_MS  # noqa: E402

NOW = 1_790_000_000_000          # fixed "now" (ms) so the windows are stable
PERPL = {"BTC", "ETH", "SOL", "HYPE", "ZEC", "MON", "PUMP"}
checks = []


def check(name, got, want):
    ok = got == want
    checks.append(ok)
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}: got {got!r} want {want!r}")


def trips(n, *, win_rate, avg_win, avg_loss, hold_sec, span_days=80, coins=("BTC", "ETH"),
          size_usd=10_000.0, maker_share=0.2, last_days_ago=1, leverage=4.0, liquidation_at=None, seed=1):
    """n trips spread over span_days, ending last_days_ago days before NOW.
    Wins/losses alternate to the requested win rate; sizes fixed."""
    rnd = random.Random(seed)
    out = []
    end = NOW - last_days_ago * DAY_MS
    start = end - span_days * DAY_MS
    n_win = round(n * win_rate)
    pattern = ([True] * n_win + [False] * (n - n_win))
    rnd.shuffle(pattern)
    for i, w in enumerate(pattern):
        close = start + int((end - start) * (i + 1) / n)
        open_ = close - hold_sec * 1000
        pnl = avg_win if w else -avg_loss
        coin = coins[i % len(coins)]
        ret = pnl / size_usd * 100.0
        fills = 4
        out.append({"coin": coin, "side": "long" if i % 3 else "short", "open_time": open_, "close_time": close,
                    "n_fills": fills, "n_adds": 1, "n_closes": 2, "maker_fills": round(fills * maker_share),
                    "max_size_usd": size_usd, "volume_usd": size_usd * 2, "avg_entry": 100.0, "avg_exit": 100.0 * (1 + ret / 100),
                    "pnl_gross": pnl + 2.0, "fees": 2.0, "funding": 0.0, "pnl_usd": pnl, "ret_pct": ret,
                    "hold_sec": hold_sec, "leverage": leverage, "averaged_down": False, "added_to_winner": True,
                    "liquidation": (liquidation_at == i), "mae_pct": abs(min(ret, 0)) + 0.2, "mfe_pct": max(ret, 0) + 0.2})
    return out


def run(ts, **kw):
    base = dict(now_ms=NOW, equity_avg=50_000.0, account_value=52_000.0, perpl_coins=PERPL,
                last_fill_ms=ts[-1]["close_time"] if ts else None, identity_role="user", hedger=False,
                wash_share=0.03, mm_flag=False)
    base.update(kw)
    return compute_metrics(ts, **base)


def failed_rules(m):
    return sorted(k for k, v in m["flags"].items() if v["pass"] is False)


print("Steady wallet: 61 trips, 78% wins, pf well over 1.5")
steady = run(trips(61, win_rate=0.78, avg_win=900.0, avg_loss=400.0, hold_sec=7 * 3600))
check("label consistent", steady["label"], "consistent")
check("qualified", steady["qualified"], True)
check("no failed rule", failed_rules(steady), [])
check("win rate", round(steady["win_rate_90d"], 2), 0.79)
check("profit factor", steady["profit_factor_90d"] > 1.5, True)
check("style swing-ish hold shown as intraday (7 h)", steady["style"], "intraday")
check("verdict mentions wins", "wins 79% of trades" in steady["verdict"].lower(), True)
check("copiable 100% (all coins on Perpl)", steady["copiable_pct"], 100.0)
check("score inside 0..100", 0 <= steady["score"] <= 100, True)

print("Big winners wallet: 41 trips, 54% wins, avg win 3.7x avg loss")
big = run(trips(41, win_rate=0.54, avg_win=1940.0, avg_loss=520.0, hold_sec=44 * 3600, seed=3))
check("label asymmetric", big["label"], "asymmetric")
check("qualified", big["qualified"], True)
check("verdict says bigger wins", "bigger than losses" in big["verdict"], True)
check("style swing", big["style"], "swing")

print("Market maker: 90% maker fills, 2 minute holds, tiny pnl per volume")
mm = run(trips(400, win_rate=0.9, avg_win=3.0, avg_loss=10.0, hold_sec=120, maker_share=0.9, size_usd=50_000.0))
check("mm rule fails", "mm" in failed_rules(mm), True)
check("not qualified", mm["qualified"], False)

print("Farmer: huge volume, flat pnl")
farmer = run(trips(200, win_rate=0.5, avg_win=20.0, avg_loss=19.0, hold_sec=900, size_usd=200_000.0))
check("farmer rule fails", "farmer" in failed_rules(farmer), True)
check("not qualified", farmer["qualified"], False)

print("Vault: a Steady record but userRole = vault")
vault = run(trips(61, win_rate=0.78, avg_win=900.0, avg_loss=400.0, hold_sec=7 * 3600), identity_role="vault")
check("role rule fails", "role" in failed_rules(vault), True)
check("not qualified", vault["qualified"], False)

print("Sub-account: folded into its master, never listed alone")
sub = run(trips(61, win_rate=0.78, avg_win=900.0, avg_loss=400.0, hold_sec=7 * 3600), identity_role="subAccount")
check("role rule fails", "role" in failed_rules(sub), True)
check("not qualified", sub["qualified"], False)

print("5-trade wallet: 5 wins, one week old")
small = run(trips(5, win_rate=1.0, avg_win=600.0, avg_loss=1.0, hold_sec=3600, span_days=6))
check("sample rule fails", "sample" in failed_rules(small), True)
check("net profit rule passes (3,000 = 6% of equity)", small["flags"]["net_profit"]["pass"], True)
check("not qualified", small["qualified"], False)
check("29 trips over 40 days still fail sample", "sample" in failed_rules(run(trips(29, win_rate=0.8, avg_win=500.0, avg_loss=200.0, hold_sec=3600, span_days=40))), True)
check("30 trips over 29 days fail sample (days)", "sample" in failed_rules(run(trips(30, win_rate=0.8, avg_win=500.0, avg_loss=200.0, hold_sec=3600, span_days=29))), True)

print("Big-loss wallet: one trade loses 25% of equity")
bl = trips(61, win_rate=0.78, avg_win=900.0, avg_loss=400.0, hold_sec=7 * 3600)
bl[10]["pnl_usd"] = -12_500.0
bl[10]["ret_pct"] = -12.5
bigloss = run(bl)
check("worst trade rule fails", "worst_trade" in failed_rules(bigloss), True)
check("not qualified", bigloss["qualified"], False)
check("worst trade shown, never hidden", bigloss["worst_trade_usd"], -12_500.0)

print("Liquidated wallet")
liq = run(trips(61, win_rate=0.78, avg_win=900.0, avg_loss=400.0, hold_sec=7 * 3600, liquidation_at=5))
check("liquidations rule fails", "liquidations" in failed_rules(liq), True)

print("Stale wallet: last trade 20 days ago, everything else passing")
stale = run(trips(61, win_rate=0.78, avg_win=900.0, avg_loss=400.0, hold_sec=7 * 3600, last_days_ago=20))
check("recent rule fails", stale["flags"]["recent"]["pass"], False)
check("still qualified (record stays)", stale["qualified"], True)
check("marked inactive", stale["inactive"], True)

print("Bot: 150 trips a day")
bot = run(trips(1500, win_rate=0.6, avg_win=50.0, avg_loss=30.0, hold_sec=60, span_days=10, size_usd=2_000.0))
check("bot rule fails", "bot" in failed_rules(bot), True)

print("Hedger and wash flags")
check("hedger flag blocks", "hedger" in failed_rules(run(trips(61, win_rate=0.78, avg_win=900.0, avg_loss=400.0, hold_sec=7 * 3600), hedger=True)), True)
check("wash 25% blocks", "wash" in failed_rules(run(trips(61, win_rate=0.78, avg_win=900.0, avg_loss=400.0, hold_sec=7 * 3600), wash_share=0.25)), True)
check("cohort MM flag blocks", "mm" in failed_rules(run(trips(61, win_rate=0.78, avg_win=900.0, avg_loss=400.0, hold_sec=7 * 3600), mm_flag=True)), True)

print("Drawdown over 30%")
dd = trips(61, win_rate=0.78, avg_win=900.0, avg_loss=400.0, hold_sec=7 * 3600)
for i in range(20, 32):
    dd[i]["pnl_usd"] = -1_600.0
    dd[i]["ret_pct"] = -1.6
ddm = run(dd)
check("drawdown rule fails", "drawdown" in failed_rules(ddm), True)

print("One big win: shown but ranked down")
obw = trips(61, win_rate=0.78, avg_win=300.0, avg_loss=250.0, hold_sec=7 * 3600)
obw[30]["pnl_usd"] = 20_000.0
obw[30]["ret_pct"] = 20.0
o = run(obw)
check("one_big_win label", o["one_big_win"], True)
check("still qualified", o["qualified"], True)
check("score penalised vs the same wallet without it", o["score"] < run(trips(61, win_rate=0.78, avg_win=300.0, avg_loss=250.0, hold_sec=7 * 3600))["score"] + 25, True)

print("Score orders Steady above Big winners above a borderline wallet")
border = run(trips(31, win_rate=0.71, avg_win=300.0, avg_loss=190.0, hold_sec=3 * 3600))
check("steady score > border score", steady["score"] > border["score"], True)
check("inputs exposed", sorted(steady["score_inputs"]), ["consistency", "copy_fit", "drawdown", "lev_penalty", "one_big_win", "pf", "sample", "win_rate"])

print("Replay defaults")
d = suggested_settings(steady, None)
check("default $50, 3x", (d["margin_usd"], d["leverage"]), (50.0, 3.0))
check("stop = trader's average loss move", d["stop_pct"], round(steady["avg_loss_move_pct"], 2))
d2 = suggested_settings(steady, {"margin_usd": 25, "max_leverage": 10, "sl_margin_pct": 20})
check("saved settings win, leverage capped at the trader's median 4x", (d2["margin_usd"], d2["leverage"]), (25.0, 4.0))

n = sum(1 for c in checks if not c)
print(f"\n{len(checks) - n} passed, {n} failed")
sys.exit(1 if n else 0)
