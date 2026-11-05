"""Research: 1-minute HYPEUSDT candles from Binance USDM futures (public, no key).
Hyperliquid serves only the most recent 5,000 candles per interval, so the long
1-minute history for the dip-ladder test comes from here. Read-only.

    python scripts/research_fetch_binance_1m.py OUT.json START_MS
"""
import json, time, urllib.request, sys
out = sys.argv[1]; start = int(sys.argv[2]); end = int(time.time() * 1000)
bars = []; t = start; calls = 0
while t < end:
    url = f"https://fapi.binance.com/fapi/v1/klines?symbol=HYPEUSDT&interval=1m&startTime={t}&limit=1500"
    for attempt in range(5):
        try:
            body = json.load(urllib.request.urlopen(url, timeout=30)); break
        except Exception as e:
            print("retry", e, flush=True); time.sleep(10)
    else:
        break
    calls += 1
    if not body: break
    for k in body:
        bars.append({"t": int(k[0]), "o": k[1], "h": k[2], "l": k[3], "c": k[4], "v": k[5]})
    t = int(body[-1][0]) + 60000
    if calls % 50 == 0: print(calls, "calls", len(bars), "bars", flush=True)
    time.sleep(0.25)
json.dump(bars, open(out, "w"))
print("done", calls, "calls", len(bars), "bars", bars[0]["t"], bars[-1]["t"], flush=True)
