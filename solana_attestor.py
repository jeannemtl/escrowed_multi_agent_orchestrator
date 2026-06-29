#!/usr/bin/env python3
"""
Batch Solana Attestor
======================
Submits an entire dependency LAYER as a single Solana memo transaction.

Instead of 50 individual transactions (50 × 13s = 10+ min),
we batch each layer into 1 transaction (5 layers × 13s = ~65s).

The memo encodes all task IDs in the layer:
  "layer:0 tasks:t001,t005,t012 count:3 depth:0 downstream:15"

Solana's role: enforce that no layer resolves before its parent layer
is finalized on-chain. The controller reads confirmed slots to know
which layers are done before dispatching dependents.
"""

import time, json, asyncio
from solana.rpc.api import Client
from solana.rpc.websocket_api import connect as ws_connect
from solders.keypair import Keypair
from solders.instruction import Instruction, AccountMeta
from solders.signature import Signature
from solders.pubkey import Pubkey
from solders.transaction import Transaction
from solana.rpc.types import TxOpts

MEMO_PROGRAM_ID = "MemoSq4gqABAXKb96qnH8TysNcWxMyWCqXgDLGmfcHr"
DEVNET_RPC = "https://api.devnet.solana.com"
DEVNET_WS = "wss://api.devnet.solana.com/"
KEYPAIR_PATH = "/tmp/multi-agent-orchestrator-keypair.json"

_keypair = None


def load_keypair(path: str = KEYPAIR_PATH) -> Keypair:
    global _keypair
    if _keypair is None:
        with open(path) as f:
            raw = json.load(f)
        _keypair = Keypair.from_bytes(bytes(raw))
    return _keypair


async def attest_layer(
    layer_idx: int,
    task_ids: list[str],
    depth: int = 0,
    downstream_count: int = 0,
    keypair_path: str = KEYPAIR_PATH,
    rpc_url: str = DEVNET_RPC,
    ws_url: str = DEVNET_WS,
    timeout: float = 30.0,
) -> dict:
    """
    Submit a batch attestation for an entire dependency layer.
    
    Returns: {
        "ok": bool,
        "layer": int,
        "task_ids": list[str],
        "signature": str,
        "latency_ms": float,
        "slot": int,
        "memo": str,
    }
    """
    client = Client(rpc_url)
    memo_program = Pubkey.from_string(MEMO_PROGRAM_ID)
    kp = load_keypair(keypair_path)

    # Build memo: encode all task IDs in this layer
    ids_str = ",".join(t[:8] for t in task_ids)  # truncate to fit 566 byte limit
    memo_text = (
        f"layer:{layer_idx} count:{len(task_ids)} "
        f"depth:{depth} downstream:{downstream_count} "
        f"tasks:{ids_str}"
    )[:566]

    ix = Instruction(
        program_id=memo_program,
        accounts=[AccountMeta(pubkey=kp.pubkey(), is_signer=True, is_writable=False)],
        data=bytes(memo_text, "utf-8"),
    )

    # Get blockhash
    blockhash_resp = client.get_latest_blockhash()
    blockhash = blockhash_resp.value.blockhash

    # Build and sign
    tx = Transaction.new_with_payer([ix], kp.pubkey())
    tx.sign([kp], blockhash)

    submit_time = time.time()

    try:
        async with ws_connect(ws_url) as ws:
            await ws.signature_subscribe(
                Signature.from_string(str(tx.signatures[0]))
            )
            await ws.recv()  # skip subscription ack

            client.send_transaction(tx, TxOpts(skip_preflight=True, skip_confirmation=True))

            msg = await asyncio.wait_for(ws.recv(), timeout=timeout)
            confirm_time = time.time()
            latency_ms = (confirm_time - submit_time) * 1000

            slot = -1
            try:
                slot = msg[0].result.value.slot
            except (AttributeError, IndexError):
                pass

    except asyncio.TimeoutError:
        return {"ok": False, "error": "confirmation timed out", "layer": layer_idx}
    except Exception as e:
        return {"ok": False, "error": str(e), "layer": layer_idx}

    return {
        "ok": True,
        "layer": layer_idx,
        "task_ids": task_ids,
        "signature": str(tx.signatures[0]),
        "latency_ms": round(latency_ms, 1),
        "slot": slot,
        "memo": memo_text,
    }


def attest_layer_sync(**kwargs) -> dict:
    """Synchronous wrapper."""
    return asyncio.run(attest_layer(**kwargs))


def check_balance(rpc_url: str = DEVNET_RPC) -> float:
    kp = load_keypair()
    client = Client(rpc_url)
    resp = client.get_balance(kp.pubkey())
    return resp.value / 1e9


if __name__ == "__main__":
    bal = check_balance()
    print(f"Balance: {bal} SOL")
    if bal < 0.01:
        print("Need devnet SOL. Fund at https://faucet.solana.com")
        print(f"Address: {load_keypair().pubkey()}")
    else:
        print("Testing batch layer attestation (3 tasks)...")
        result = attest_layer_sync(
            layer_idx=0,
            task_ids=["t001", "t005", "t012"],
            depth=0,
            downstream_count=15,
        )
        print(json.dumps(result, indent=2))
