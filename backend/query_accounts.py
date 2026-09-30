"""Query all Perpl accounts from the on-chain contract on Monad testnet."""

import asyncio
import json
import time
import httpx

RPC_URL = "https://testnet-rpc.monad.xyz"
CONTRACT = "0x1964c32f0be608e7d29302aff5e61268e72080cc"

# Function selectors
SEL_NUM_ACCOUNTS = "0x0f03e4c3"   # numberOfAccounts()
SEL_GET_ACCOUNT  = "0x05aca141"   # getAccountById(uint256)

# AUSD has 6 decimals
DECIMALS = 6

# Rate limiting
RATE_LIMIT = 12
rate_lock = asyncio.Lock()
last_times: list[float] = []
call_id = 0


def next_id():
    global call_id
    call_id += 1
    return call_id


def encode_uint256(v: int) -> str:
    return hex(v)[2:].zfill(64)


async def rate_wait():
    async with rate_lock:
        now = time.monotonic()
        while last_times and last_times[0] < now - 1.0:
            last_times.pop(0)
        if len(last_times) >= RATE_LIMIT:
            sleep_for = last_times[0] - (now - 1.0) + 0.05
            if sleep_for > 0:
                await asyncio.sleep(sleep_for)
        last_times.append(time.monotonic())


async def eth_call(client: httpx.AsyncClient, data: str, retries: int = 4) -> str:
    for attempt in range(retries):
        await rate_wait()
        payload = {
            "jsonrpc": "2.0",
            "id": next_id(),
            "method": "eth_call",
            "params": [{"to": CONTRACT, "data": data}, "latest"],
        }
        try:
            resp = await client.post(RPC_URL, json=payload)
            result = resp.json()
            if "error" in result:
                err = result["error"]
                if err.get("code") == -32011:
                    await asyncio.sleep(1.5 + attempt)
                    continue
                raise Exception(f"RPC error: {err}")
            if "result" not in result:
                if attempt < retries - 1:
                    await asyncio.sleep(0.5)
                    continue
                raise Exception(f"No result: {result}")
            return result["result"]
        except httpx.HTTPError:
            if attempt < retries - 1:
                await asyncio.sleep(1.0 + attempt)
                continue
            raise
    raise Exception(f"Failed after {retries} retries")


def count_positions_from_bitmap(word5_int: int) -> int:
    """Count non-zero 16-bit slots in the lower 128 bits (market position bitmap)."""
    count = 0
    for i in range(8):
        chunk = (word5_int >> (i * 16)) & 0xFFFF
        if chunk:
            count += 1
    return count


async def get_number_of_accounts(client: httpx.AsyncClient) -> int:
    raw = await eth_call(client, SEL_NUM_ACCOUNTS)
    return int(raw, 16)


async def get_account(client: httpx.AsyncClient, account_id: int) -> dict:
    """Parse getAccountById return data:
    word[0] = account_id
    word[1] = balance (AUSD, 6 decimals)
    word[2] = margin/reserved amount
    word[3] = (unused)
    word[4] = wallet address
    word[5] = positions bitmap (lower 128 bits) + flags (upper 128 bits)
    word[6] = additional count/data
    word[7-8] = (unused)
    """
    data = "0x" + SEL_GET_ACCOUNT[2:] + encode_uint256(account_id)
    raw = await eth_call(client, data)
    hex_data = raw[2:]
    words = [hex_data[i:i+64] for i in range(0, len(hex_data), 64)]

    if len(words) < 6:
        return {
            "account_id": account_id,
            "wallet": "0x" + "0" * 40,
            "balance": 0,
            "balance_formatted": "0.000000",
            "margin_used": 0,
            "margin_formatted": "0.000000",
            "positions": 0,
        }

    balance_raw = int(words[1], 16)
    # Handle signed: if top bit set, it's negative
    if balance_raw >= 2**255:
        balance_raw -= 2**256

    margin_raw = int(words[2], 16)
    if margin_raw >= 2**255:
        margin_raw -= 2**256

    wallet = "0x" + words[4][-40:]
    positions_bitmap = int(words[5], 16)
    positions = count_positions_from_bitmap(positions_bitmap)

    return {
        "account_id": account_id,
        "wallet": wallet,
        "balance": balance_raw,
        "balance_formatted": f"{balance_raw / 10**DECIMALS:.{DECIMALS}f}",
        "margin_used": margin_raw,
        "margin_formatted": f"{margin_raw / 10**DECIMALS:.{DECIMALS}f}",
        "positions": positions,
    }


async def main():
    async with httpx.AsyncClient(timeout=30.0) as client:
        total = await get_number_of_accounts(client)
        print(f"Total accounts: {total}")

        # Process in batches of 5
        accounts = []
        errors = 0
        batch_size = 5

        for batch_start in range(1, total + 1, batch_size):
            batch_end = min(batch_start + batch_size, total + 1)
            batch_ids = list(range(batch_start, batch_end))
            tasks = [get_account(client, i) for i in batch_ids]
            results = await asyncio.gather(*tasks, return_exceptions=True)

            for i, r in enumerate(results):
                aid = batch_ids[i]
                if isinstance(r, Exception):
                    errors += 1
                    print(f"  Account {aid} error: {r}")
                else:
                    accounts.append(r)

            done = min(batch_end - 1, total)
            print(f"  Fetched {done}/{total}...", end="\r")

        print(f"\nFetched {len(accounts)} accounts successfully, {errors} errors.\n")

        # Sort by balance descending
        accounts.sort(key=lambda a: a["balance"], reverse=True)

        print(f"{'ID':>4}  {'Wallet':<44}  {'Balance (AUSD)':>18}  {'Margin':>16}  {'Pos':>4}")
        print("-" * 94)
        for a in accounts:
            print(
                f"{a['account_id']:>4}  {a['wallet']:<44}  {a['balance_formatted']:>18}  "
                f"{a['margin_formatted']:>16}  {a['positions']:>4}"
            )

        # Stats
        nonzero = [a for a in accounts if a["balance"] > 0]
        with_pos = [a for a in accounts if a["positions"] > 0]
        print(f"\nAccounts with balance > 0: {len(nonzero)}")
        print(f"Accounts with open positions: {len(with_pos)}")
        total_bal = sum(a["balance"] for a in accounts)
        print(f"Total balance across all accounts: {total_bal / 10**DECIMALS:,.2f} AUSD")

        out_path = "E:/wamp64/www/perpl/backend/account_data.json"
        with open(out_path, "w") as f:
            json.dump(accounts, f, indent=2)
        print(f"\nSaved {len(accounts)} accounts to account_data.json")


if __name__ == "__main__":
    asyncio.run(main())
