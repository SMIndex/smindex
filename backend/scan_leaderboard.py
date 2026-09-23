"""
Scan Perpl on-chain events to build a real leaderboard.
Reads the contract ABI, decodes PositionIncreased/Decreased/Closed events,
maps accountId -> wallet via getAccountById, and outputs top traders by PnL.
"""

import asyncio
import json
import math

import httpx
from web3 import Web3

PERPL_CONTRACT = "0x1964c32f0be608e7d29302aff5e61268e72080cc"
RPC = "https://testnet-rpc.monad.xyz"
SCAN_BLOCKS = 50_000

w3 = Web3()


def load_trading_events():
    with open("perpl_abi.json") as f:
        abi = json.load(f)

    events = {}
    for ev in abi:
        if ev.get("type") != "event":
            continue
        if ev["name"] not in [
            "PositionIncreased", "PositionDecreased", "PositionClosed",
            "PositionLiquidated", "PositionInverted", "PositionOpened",
            "MakerOrderFilled", "TakerOrderFilled",
        ]:
            continue
        param_types = ",".join(p["type"] for p in ev.get("inputs", []))
        sig = f'{ev["name"]}({param_types})'
        topic = "0x" + w3.keccak(text=sig).hex()
        events[topic] = ev
    return events


async def get_logs_batch(client, start, end, sem):
    async with sem:
        try:
            r = await client.post(RPC, json={
                "jsonrpc": "2.0", "method": "eth_getLogs",
                "params": [{"fromBlock": hex(start), "toBlock": hex(end), "address": PERPL_CONTRACT}],
                "id": 1,
            })
            return r.json().get("result", [])
        except Exception:
            return []


async def get_account_addr(client, acc_id, sem):
    async with sem:
        func_sig = w3.keccak(text="getAccountById(uint256)")[:4].hex()
        acc_padded = hex(acc_id)[2:].zfill(64)
        try:
            r = await client.post(RPC, json={
                "jsonrpc": "2.0", "method": "eth_call",
                "params": [{"to": PERPL_CONTRACT, "data": f"0x{func_sig}{acc_padded}"}, "latest"],
                "id": 1,
            })
            result = r.json()
            if "result" in result and result["result"] != "0x":
                data = bytes.fromhex(result["result"][2:])
                if len(data) >= 160:
                    val = int.from_bytes(data[128:160], "big")
                    return "0x" + hex(val)[2:].zfill(40)[-40:]
        except Exception:
            pass
    return None


def decode_log(ev, log_data):
    data = bytes.fromhex(log_data[2:])
    decoded = {}
    offset = 0
    for param in ev.get("inputs", []):
        if offset + 32 <= len(data):
            val = int.from_bytes(data[offset:offset + 32], "big")
            if param["type"].startswith("int"):
                if val >= 2 ** 255:
                    val -= 2 ** 256
            decoded[param["name"]] = val
        offset += 32
    return decoded


async def main():
    trading_events = load_trading_events()
    print(f"Tracking {len(trading_events)} event types")

    sem = asyncio.Semaphore(15)

    async with httpx.AsyncClient(timeout=15) as client:
        # Get current block
        r = await client.post(RPC, json={"jsonrpc": "2.0", "method": "eth_blockNumber", "params": [], "id": 1})
        current = int(r.json()["result"], 16)
        print(f"Current block: {current}")

        batches = [(b, min(b + 99, current)) for b in range(current - SCAN_BLOCKS, current, 100)]
        print(f"Scanning {len(batches)} batches ({SCAN_BLOCKS} blocks)...")

        results = await asyncio.gather(*[get_logs_batch(client, s, e, sem) for s, e in batches])

        account_stats = {}

        for logs in results:
            for log in logs:
                topic0 = log.get("topics", [""])[0]
                if topic0 not in trading_events:
                    continue

                ev = trading_events[topic0]
                decoded = decode_log(ev, log["data"])
                name = ev["name"]

                acc_id = decoded.get("accountId")
                if acc_id is None:
                    continue

                if acc_id not in account_stats:
                    account_stats[acc_id] = {
                        "opens": 0, "closes": 0, "decreases": 0, "increases": 0,
                        "total_pnl": 0, "wins": 0, "losses": 0,
                        "fees": 0, "leverages": [], "markets": set(), "pnl_list": [],
                    }

                s = account_stats[acc_id]

                if name == "PositionIncreased":
                    s["increases"] += 1
                    s["leverages"].append(decoded.get("leverageHdths", 0) / 100)
                    s["markets"].add(decoded.get("perpId", 0))
                    pnl = decoded.get("pnlCollateralizedCNS", 0)
                    s["total_pnl"] += pnl
                elif name == "PositionDecreased":
                    s["decreases"] += 1
                    pnl = decoded.get("deltaPnlCNS", 0)
                    s["total_pnl"] += pnl
                    s["pnl_list"].append(pnl)
                    s["markets"].add(decoded.get("perpId", 0))
                    if pnl >= 0:
                        s["wins"] += 1
                    else:
                        s["losses"] += 1
                elif name == "PositionClosed":
                    s["closes"] += 1
                    pnl = decoded.get("deltaPnlCNS", 0)
                    s["total_pnl"] += pnl
                    s["pnl_list"].append(pnl)
                    if pnl >= 0:
                        s["wins"] += 1
                    else:
                        s["losses"] += 1
                elif name == "PositionLiquidated":
                    s["closes"] += 1
                    s["losses"] += 1
                elif name in ("MakerOrderFilled", "TakerOrderFilled"):
                    s["fees"] += decoded.get("feeCNS", 0)

        # Filter to accounts with meaningful activity
        active_ids = [aid for aid, s in account_stats.items()
                      if s["increases"] + s["decreases"] + s["closes"] >= 5]
        print(f"Active accounts (5+ trades): {len(active_ids)}")

        # Fetch wallet addresses
        addr_tasks = [get_account_addr(client, aid, sem) for aid in sorted(active_ids)]
        addresses = await asyncio.gather(*addr_tasks)
        addr_map = {aid: addr for aid, addr in zip(sorted(active_ids), addresses) if addr}

        # Build leaderboard
        leaderboard = []
        for acc_id in active_ids:
            s = account_stats[acc_id]
            total_trades = s["increases"] + s["decreases"] + s["closes"]
            pnl_usd = s["total_pnl"] / 1e6
            wl = s["wins"] + s["losses"]
            win_rate = (s["wins"] / wl * 100) if wl > 0 else 0
            avg_lev = sum(s["leverages"]) / len(s["leverages"]) if s["leverages"] else 0
            fees_usd = s["fees"] / 1e6

            # Sharpe ratio
            sharpe = 0.0
            if len(s["pnl_list"]) >= 2:
                mean_r = sum(s["pnl_list"]) / len(s["pnl_list"])
                var = sum((r - mean_r) ** 2 for r in s["pnl_list"]) / (len(s["pnl_list"]) - 1)
                std = math.sqrt(var) if var > 0 else 0
                if std > 0:
                    sharpe = (mean_r / std) * math.sqrt(365)

            # Max drawdown
            peak = cum = max_dd = 0
            for p in s["pnl_list"]:
                cum += p
                if cum > peak:
                    peak = cum
                dd = peak - cum
                if dd > max_dd:
                    max_dd = dd

            leaderboard.append({
                "acc_id": acc_id,
                "wallet": addr_map.get(acc_id, "unknown"),
                "total_trades": total_trades,
                "win_rate": round(win_rate, 1),
                "pnl_usd": round(pnl_usd, 2),
                "fees_usd": round(fees_usd, 2),
                "avg_leverage": round(avg_lev, 1),
                "sharpe": round(sharpe, 2),
                "max_dd_usd": round(max_dd / 1e6, 2),
                "markets": len(s["markets"]),
            })

        leaderboard.sort(key=lambda x: -x["pnl_usd"])

        print(f"\nTOP TRADERS (by PnL, last {SCAN_BLOCKS} blocks):")
        for i, t in enumerate(leaderboard[:15]):
            w = t["wallet"][:8] + "..." + t["wallet"][-4:] if t["wallet"] != "unknown" else "unknown"
            print(
                f"  #{i+1:2d}  Acc#{t['acc_id']:<4d} {w:16s} "
                f"Trades={t['total_trades']:>5d}  WR={t['win_rate']:>5.1f}%  "
                f"PnL=${t['pnl_usd']:>12,.2f}  Sharpe={t['sharpe']:>6.2f}  "
                f"AvgLev={t['avg_leverage']:>5.1f}x  MaxDD=${t['max_dd_usd']:>10,.2f}  "
                f"Mkts={t['markets']}"
            )

        # Save as JSON for seed script
        with open("real_leaderboard.json", "w") as f:
            json.dump(leaderboard, f, indent=2)
        print(f"\nSaved {len(leaderboard)} traders to real_leaderboard.json")


if __name__ == "__main__":
    asyncio.run(main())
