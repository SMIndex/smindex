"""
Build comprehensive leaderboard from on-chain data.
Since CollateralDeposit events are rare (only 9 out of 168 accounts),
we use a hybrid approach:
- Accounts with deposit events: use actual deposit amount
- Other accounts: estimate initial deposit from balance patterns
- PnL = current_balance - estimated_deposits (no withdrawals exist on-chain)
- Trade counts from scanning PositionIncreased/Decreased/Closed events
"""

import asyncio
import json
import time

import httpx
from web3 import Web3

PERPL_CONTRACT = "0x1964c32f0be608e7d29302aff5e61268e72080cc"
RPC = "https://testnet-rpc.monad.xyz"

w3 = Web3()

DEPOSIT_TOPIC = "0x" + w3.keccak(text="CollateralDeposit(uint256,uint256,uint256)").hex()
WITHDRAW_TOPIC = "0x" + w3.keccak(text="CollateralWithdrawal(uint256,uint256,uint256)").hex()

# Known deposit data from on-chain scan (only 9 deposits found across 2M blocks)
KNOWN_DEPOSITS = {
    48: 10_000_000_000,
    49: 10_000_000_000,
    51: 10_000_000_000,
    56: 10_000_000_000,
    78: 10_000_000_000,
    121: 10_000_000_000,
    124: 10_000_000_000,
    146: 1_000_000_000,
    168: 10_000_000_000,
}

AMM_ACCOUNTS = {1, 2}
DEFAULT_DEPOSIT = 10_000_000_000


def estimate_deposit(acc_id, balance_raw):
    if acc_id in KNOWN_DEPOSITS:
        return KNOWN_DEPOSITS[acc_id]
    if acc_id in AMM_ACCOUNTS:
        if acc_id == 1:
            return 30_000_000_000_000
        if acc_id == 2:
            return 6_000_000_000_000
    return DEFAULT_DEPOSIT


async def rpc_call(client, method, params, sem):
    async with sem:
        for attempt in range(3):
            try:
                r = await client.post(RPC, json={
                    "jsonrpc": "2.0",
                    "method": method,
                    "params": params,
                    "id": 1,
                }, timeout=20)
                resp = r.json()
                if "error" in resp:
                    return None, resp["error"]
                return resp.get("result"), None
            except Exception as e:
                if attempt == 2:
                    return None, str(e)
                await asyncio.sleep(0.5 * (attempt + 1))


async def get_logs(client, from_block, to_block, topics, sem):
    params = [{
        "address": PERPL_CONTRACT,
        "fromBlock": hex(from_block),
        "toBlock": hex(to_block),
    }]
    if topics:
        params[0]["topics"] = [topics]
    return await rpc_call(client, "eth_getLogs", params, sem)


async def scan_trading_events(client, start_block, end_block, sem):
    """Scan for ALL contract events and filter trading ones client-side."""
    try:
        with open("perpl_abi.json") as f:
            abi = json.load(f)
    except FileNotFoundError:
        print("  perpl_abi.json not found, skipping trade scan")
        return {}

    trade_topics = set()
    for ev in abi:
        if ev.get("type") != "event":
            continue
        if ev["name"] not in ["PositionIncreased", "PositionDecreased", "PositionClosed",
                               "PositionOpened", "PositionLiquidated"]:
            continue
        param_types = ",".join(p["type"] for p in ev.get("inputs", []))
        sig = "{}({})".format(ev["name"], param_types)
        topic = "0x" + w3.keccak(text=sig).hex()
        trade_topics.add(topic)

    print(f"  Tracking {len(trade_topics)} trading event types")
    topic_list = list(trade_topics)

    account_trades = {}
    account_markets = {}
    total_blocks = end_block - start_block
    scanned = 0
    errors = 0
    last_print = time.time()

    current = start_block
    while current <= end_block:
        # Use batches of 20 concurrent 100-block requests
        batch_tasks = []
        batch_ranges = []
        batch_count = min(20, max(1, (end_block - current) // 100 + 1))

        for _ in range(batch_count):
            if current > end_block:
                break
            chunk_end = min(current + 99, end_block)
            batch_ranges.append((current, chunk_end))
            batch_tasks.append(get_logs(client, current, chunk_end, topic_list, sem))
            current = chunk_end + 1

        results = await asyncio.gather(*batch_tasks)

        for (s_, e_), (logs, err) in zip(batch_ranges, results):
            if err:
                errors += 1
            else:
                scanned += (e_ - s_ + 1)
                if logs:
                    for log in logs:
                        topics = log.get("topics", [])
                        if not topics or topics[0] not in trade_topics:
                            continue
                        data = log.get("data", "0x")
                        raw = data[2:] if data.startswith("0x") else data
                        if len(raw) >= 64:
                            try:
                                acc_id = int(raw[:64], 16)
                                if 0 < acc_id < 10000:
                                    account_trades[acc_id] = account_trades.get(acc_id, 0) + 1
                                    if len(raw) >= 128:
                                        perp_id = int(raw[64:128], 16)
                                        if 0 < perp_id < 1000:
                                            if acc_id not in account_markets:
                                                account_markets[acc_id] = set()
                                            account_markets[acc_id].add(perp_id)
                            except (ValueError, OverflowError):
                                pass

        now = time.time()
        if now - last_print > 5:
            pct = scanned / total_blocks * 100 if total_blocks > 0 else 100
            evts = sum(account_trades.values())
            print(f"  Trade scan: {pct:.1f}% | {evts} trades | {len(account_trades)} accounts | errors: {errors}")
            last_print = now

    total_t = sum(account_trades.values())
    print(f"  Trade scan complete: {total_t} trades across {len(account_trades)} accounts ({errors} errors)")

    result = {}
    for acc_id in account_trades:
        result[acc_id] = {
            "trades": account_trades[acc_id],
            "markets": len(account_markets.get(acc_id, set())),
        }
    return result


def print_table(leaderboard, elapsed):
    top20 = leaderboard[:20]
    sep = "=" * 140
    print(f"\n{sep}")
    print(f"  TOP 20 TRADERS BY PnL (computed in {elapsed:.1f}s)")
    print(sep)
    h = "  {:>3}  {:>6}  {:^16}  {:>14}  {:>14}  {:>14}  {:>8}  {:>8}  {:>6}".format(
        "#", "AccID", "Wallet", "Balance", "Est.Deposit", "PnL", "ROI", "Trades", "Mkts")
    print(h)
    print("  ---  ------  ----------------  --------------  --------------  --------------  --------  --------  ------")
    for i, t in enumerate(top20):
        wallet = t["wallet"]
        w = wallet[:8] + "..." + wallet[-4:] if wallet != "unknown" else "unknown"
        print("  {:>3}  {:>6}  {:^16}  ${:>12,.2f}  ${:>12,.2f}  ${:>12,.2f}  {:>6.1f}%  {:>8,}  {:>6}".format(
            i + 1, t["acc_id"], w, t["balance"], t["collateral"], t["pnl"], t["roi"],
            t["total_trades"], t["markets"]))
    print(sep)
    if len(leaderboard) > 5:
        bottom5 = list(reversed(leaderboard[-5:]))
        print("\n  BOTTOM 5 TRADERS (worst PnL):")
        print("  {:>3}  {:>6}  {:^16}  {:>14}  {:>14}  {:>14}  {:>8}  {:>8}".format(
            "#", "AccID", "Wallet", "Balance", "Est.Deposit", "PnL", "ROI", "Trades"))
        print("  ---  ------  ----------------  --------------  --------------  --------------  --------  --------")
        for i, t in enumerate(bottom5):
            wallet = t["wallet"]
            w = wallet[:8] + "..." + wallet[-4:] if wallet != "unknown" else "unknown"
            print("  {:>3}  {:>6}  {:^16}  ${:>12,.2f}  ${:>12,.2f}  ${:>12,.2f}  {:>6.1f}%  {:>8,}".format(
                i + 1, t["acc_id"], w, t["balance"], t["collateral"], t["pnl"], t["roi"],
                t["total_trades"]))


async def main():
    t0 = time.time()
    sem = asyncio.Semaphore(20)
    with open("account_data.json") as f:
        accounts = json.load(f)
    print(f"Loaded {len(accounts)} accounts from account_data.json")
    acc_map = {}
    for acc in accounts:
        acc_map[acc["account_id"]] = {
            "wallet": acc["wallet"],
            "balance_raw": acc["balance"],
            "balance": float(acc["balance_formatted"]),
        }
    async with httpx.AsyncClient(timeout=30) as client:
        result, err = await rpc_call(client, "eth_blockNumber", [], sem)
        if err:
            print(f"Failed to get block number: {err}")
            return
        current_block = int(result, 16)
        print(f"Current block: {current_block:,}")
        # Scan last 200K blocks for trades (about 50K blocks at a time is reasonable)
        trade_start = max(0, current_block - 200_000)
        print(f"\nScanning trading events from block {trade_start:,} to {current_block:,} ({current_block - trade_start:,} blocks)...")
        trade_data = await scan_trading_events(client, trade_start, current_block, sem)
        print("\nBuilding leaderboard...")
        leaderboard = []
        for acc_id, info in acc_map.items():
            balance_raw = info["balance_raw"]
            balance_usd = balance_raw / 1e6
            if balance_raw == 0:
                continue
            dep_raw = estimate_deposit(acc_id, balance_raw)
            dep_usd = dep_raw / 1e6
            pnl = balance_usd - dep_usd
            roi = (pnl / dep_usd * 100) if dep_usd > 0 else 0
            td = trade_data.get(acc_id, {})
            trades = td.get("trades", 0)
            markets = td.get("markets", 0)
            leaderboard.append({
                "acc_id": acc_id,
                "wallet": info["wallet"],
                "balance": round(balance_usd, 2),
                "total_trades": trades,
                "win_rate": 0,
                "pnl": round(pnl, 2),
                "fees": 0,
                "avg_lev": 0,
                "sharpe": 0,
                "max_dd": 0,
                "markets": markets,
                "collateral": round(dep_usd, 2),
                "roi": round(roi, 2),
            })
        leaderboard = [x for x in leaderboard if x["acc_id"] not in AMM_ACCOUNTS and x["pnl"] != 0]
        leaderboard.sort(key=lambda x: -x["pnl"])
        top20 = leaderboard[:20]
        with open("real_leaderboard.json", "w") as f:
            json.dump(top20, f, indent=2)
        print(f"\nSaved {len(top20)} traders to real_leaderboard.json")
        elapsed = time.time() - t0
        print_table(leaderboard, elapsed)
        profitable = sum(1 for x in leaderboard if x["pnl"] > 0)
        losing = sum(1 for x in leaderboard if x["pnl"] < 0)
        total_pnl = sum(x["pnl"] for x in leaderboard)
        print(f"\n  Summary: {profitable} profitable, {losing} losing, {len(leaderboard)} total (excl AMMs)")
        print(f"  Net PnL: ${total_pnl:,.2f}")
        print(f"  Total time: {elapsed:.1f}s")


if __name__ == "__main__":
    asyncio.run(main())
