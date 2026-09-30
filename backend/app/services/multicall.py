"""Multicall3 batching for read-only Perpl views (optimization pass 2026-09-24).

`batch_call(w3, fns)` runs web3 ContractFunction objects through
Multicall3.aggregate3 with allowFailure=true and returns, in order,
`(ok, value)` where `value` has the same shape `fn.call()` would return
(single output unwrapped, several outputs as a list). A sub-call that reverts
comes back `(False, None)` — the same outcome as the per-call `except` the
callers already had. A failure of the multicall itself raises, so callers can
fall back to their sequential path.

Deployed and verified on Monad mainnet (chain 143): 3,808 bytes of code at
MULTICALL3 (eth_getCode, 2026-09-24).
"""
from __future__ import annotations

from typing import Any

from eth_utils.abi import get_abi_output_types
from web3 import Web3

MULTICALL3 = Web3.to_checksum_address("0xcA11bde05977b3631167028862bE2a173976CA11")
_AGG3_ABI = [{
    "inputs": [{"components": [{"name": "target", "type": "address"},
                               {"name": "allowFailure", "type": "bool"},
                               {"name": "callData", "type": "bytes"}],
                "name": "calls", "type": "tuple[]"}],
    "name": "aggregate3",
    "outputs": [{"components": [{"name": "success", "type": "bool"},
                                {"name": "returnData", "type": "bytes"}],
                 "name": "returnData", "type": "tuple[]"}],
    "stateMutability": "payable", "type": "function",
}]
CHUNK = 40      # keeps each eth_call well inside the node's gas cap


def batch_call(w3: Web3, fns: list[Any], chunk: int = CHUNK) -> list[tuple[bool, Any]]:
    if not fns:
        return []
    mc = w3.eth.contract(address=MULTICALL3, abi=_AGG3_ABI)
    out: list[tuple[bool, Any]] = []
    for i in range(0, len(fns), chunk):
        part = fns[i:i + chunk]
        calls = [(fn.address, True, fn._encode_transaction_data()) for fn in part]
        results = mc.functions.aggregate3(calls).call()
        for fn, (ok, data) in zip(part, results):
            if not ok or not data:
                out.append((False, None))
                continue
            try:
                vals = w3.codec.decode(get_abi_output_types(fn.abi), data)
            except Exception:  # noqa: BLE001
                out.append((False, None))
                continue
            out.append((True, vals[0] if len(vals) == 1 else list(vals)))
    return out
