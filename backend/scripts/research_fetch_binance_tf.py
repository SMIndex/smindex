"""Research: Binance USDM futures klines for several symbols and intervals, one compact
JSON file per pair, for the Hull + DMI backtest. Read-only.

    python scripts/research_fetch_binance_tf.py OUT_DIR
"""
import json, time, urllib.request, os, sys
OUT = sys.argv[1]
SYMS = {"BTC": "BTCUSDT", "ETH": "ETHUSDT", "SOL": "SOLUSDT", "ZEC": "ZECUSDT", "HYPE": "HYPEUSDT"}
TFS = {"15m": 900, "30m": 1800, "1h": 3600, "4h": 14400, "1d": 86400}
START = 1672531200000   # 2023-01-01
for coin, sym in SYMS.items():
    for tf, sec in TFS.items():
        bars = []; t = START; calls = 0
        while True:
            url = f"https://fapi.binance.com/fapi/v1/klines?symbol={sym}&interval={tf}&startTime={t}&limit=1500"
            body = None
            for attempt in range(5):
                try:
                    body = json.load(urllib.request.urlopen(url, timeout=30)); break
                except Exception as e:
                    print("retry", coin, tf, e, flush=True); time.sleep(5)
            if not body: break
            calls += 1
            for k in body:
                if int(k[6]) > time.time() * 1000: continue       # skip the unfinished candle
                bars.append([int(k[0]) // 1000, round(float(k[1]), 4), round(float(k[2]), 4), round(float(k[3]), 4), round(float(k[4]), 4)])
            if len(body) < 1500: break
            t = int(body[-1][0]) + sec * 1000
            time.sleep(0.2)
        json.dump({"coin": coin, "tf": tf, "source": f"Binance USDM futures {sym}", "bars": bars}, open(os.path.join(OUT, f"{coin}_{tf}.json"), "w"), separators=(",", ":"))
        print(coin, tf, len(bars), "bars", calls, "calls", flush=True)
print("done", flush=True)
