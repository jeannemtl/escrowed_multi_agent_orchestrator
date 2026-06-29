#!/usr/bin/env python3
"""
E2E test for the bridge with escrow mode.
Exercises: load_tasks -> start -> register_agent -> submit_task -> verify -> attest -> escrow release.
No external API keys needed (mock Stripe + mock Solana).
"""
import asyncio
import json
import websockets

BRIDGE_URL = "ws://localhost:8765"

# Good research output (passes verification: >100 chars, has structure, no hallucination markers)
GOOD_OUTPUT = """# Research: NVIDIA Blackwell Architecture

## Overview
NVIDIA's Blackwell is the successor to Hopper, announced at GTC 2024.
It features two dies connected via NV-HBI, delivering 208 billion transistors.

## Key Specs
- 208B transistors (2x Hopper)
- 192GB HBM3e memory
- 8TB/s memory bandwidth
- 20 PFLOPS FP4 performance

## Sources
- NVIDIA GTC 2024 keynote
- NVIDIA technical whitepaper

## Implications
Blackwell targets large-scale AI training and inference, with major efficiency
gains over Hopper for trillion-parameter models.
"""

# Bad output (hallucination marker)
BAD_OUTPUT = "I do not have web access and cannot retrieve this information."


TASKS = [
    {
        "id": "task_00",
        "name": "Research NVIDIA Blackwell",
        "agent_id": "agent-1",
        "description": "Research NVIDIA Blackwell architecture and specs",
        "dependencies": [],
        "verify": {"method": "min_length", "params": {"min_chars": 100}},
    },
    {
        "id": "task_01",
        "name": "Research AMD MI400",
        "agent_id": "agent-2",
        "description": "Research AMD MI400 architecture and specs",
        "dependencies": [],
        "verify": {"method": "min_length", "params": {"min_chars": 100}},
    },
    {
        "id": "task_02",
        "name": "Synthesize comparison",
        "agent_id": "agent-3",
        "description": "Compare NVIDIA Blackwell vs AMD MI400",
        "dependencies": ["task_00", "task_01"],
        "verify": {"method": "min_length", "params": {"min_chars": 100}},
    },
]


async def main():
    print("=" * 60)
    print("  Bridge + Escrow E2E Test")
    print("=" * 60)

    async with websockets.connect(BRIDGE_URL, ping_interval=30, ping_timeout=120) as ws:
        # Step 1: Load tasks into DAG
        print("\n-- Step 1: Load tasks --")
        await ws.send(json.dumps({"type": "load_tasks", "tasks": TASKS}))

        # Wait for dag_ready
        while True:
            msg = json.loads(await ws.recv())
            if msg["type"] == "dag_ready":
                d = msg["data"]
                print(f"  DAG ready: {d['total_tasks']} tasks, {d['total_layers']} layers")
                for layer in d["layers"]:
                    print(f"    Layer {layer['layer']}: {layer['count']} tasks")
                break

        # Step 2: Register agents
        print("\n-- Step 2: Register agents --")
        for agent_id in ["agent-1", "agent-2", "agent-3"]:
            await ws.send(json.dumps({
                "type": "register_agent",
                "agent_id": agent_id,
                "capabilities": ["research"],
            }))
            # Read until agent_registered
            while True:
                msg = json.loads(await ws.recv())
                if msg["type"] == "agent_registered":
                    print(f"  Registered: {msg['data']['agent_id']}")
                    break
                elif msg["type"] == "task_dispatched":
                    d = msg["data"]
                    print(f"  (dispatched: {d['task_id']} -> {d['agent_id']})")

        # Step 3: Start pipeline
        print("\n-- Step 3: Start pipeline --")
        await ws.send(json.dumps({"type": "start"}))

        # Collect dispatched tasks
        dispatched = []
        while True:
            msg = json.loads(await ws.recv())
            if msg["type"] == "pipeline_started":
                print(f"  Pipeline started: {msg['data']['total_tasks']} tasks")
            elif msg["type"] == "task_dispatched":
                d = msg["data"]
                dispatched.append(d)
                print(f"  Dispatched: {d['task_id']} -> {d['agent_id']} (layer {d['layer']})")
                if len(dispatched) == 2:
                    break  # Layer 0 has 2 tasks

        # Step 4: Agent-1 submits good work
        print("\n-- Step 4: Agent-1 submits good work --")
        await ws.send(json.dumps({
            "type": "submit_task",
            "task_id": "task_00",
            "agent_id": "agent-1",
            "artifacts": {"summary": GOOD_OUTPUT},
        }))

        # Read until submission_result
        while True:
            msg = json.loads(await ws.recv())
            if msg["type"] == "submission_result":
                d = msg["data"]
                print(f"  task_00: verified={d.get('verified')} ok={d.get('ok')}")
                break
            elif msg["type"] == "task_verified":
                print(f"  task_00 verification: {msg['data'].get('ok')}")

        # Step 5: Agent-2 submits bad work
        print("\n-- Step 5: Agent-2 submits bad work --")
        await ws.send(json.dumps({
            "type": "submit_task",
            "task_id": "task_01",
            "agent_id": "agent-2",
            "artifacts": {"summary": BAD_OUTPUT},
        }))

        while True:
            msg = json.loads(await ws.recv())
            if msg["type"] == "submission_result":
                d = msg["data"]
                print(f"  task_01: verified={d.get('verified')} ok={d.get('ok')}")
                if d.get("error"):
                    print(f"  ERROR: {d['error']}")
                break
            elif msg["type"] == "task_verified":
                print(f"  task_01 verification: {msg['data'].get('ok')}")

        # Step 6: Agent-2 retries with good work
        print("\n-- Step 6: Agent-2 retries with good work --")
        good_amd = GOOD_OUTPUT.replace("NVIDIA Blackwell", "AMD MI400")
        await ws.send(json.dumps({
            "type": "submit_task",
            "task_id": "task_01",
            "agent_id": "agent-2",
            "artifacts": {"summary": good_amd},
        }))

        while True:
            msg = json.loads(await ws.recv())
            if msg["type"] == "submission_result":
                d = msg["data"]
                print(f"  task_01: verified={d.get('verified')} ok={d.get('ok')}")
                break
            elif msg["type"] == "task_verified":
                print(f"  task_01 verification: {msg['data'].get('ok')}")

        # Step 7: Wait for layer-0 attestation + escrow release + layer-1 dispatch
        print("\n-- Step 7: Wait for attestation + escrow release --")
        layer_attested = False
        escrow_released = False
        task_02_dispatched = False

        # Read events for up to 10 seconds
        try:
            while True:
                msg = await asyncio.wait_for(ws.recv(), timeout=15.0)
                data = json.loads(msg)
                etype = data.get("type", "")
                payload = data.get("data", {})

                if etype == "layer_attesting":
                    print(f"  Attesting layer {payload.get('layer')}...")
                elif etype == "stripe_charging":
                    print(f"  Charging: {payload.get('amount_cents')}c "
                          f"(mode={payload.get('mode')}, "
                          f"agents={payload.get('agent_count')})")
                elif etype == "escrow_released":
                    escrow_released = True
                    esc = payload.get("escrow", {})
                    print(f"  Escrow released! Available: {esc.get('available_usd')}")
                    for w in payload.get("wallets", []):
                        print(f"    {w['agent_id']}: balance={w['balance_usd']} "
                              f"tasks={w['verified_tasks']}/{w['total_tasks']}")
                elif etype == "layer_attested":
                    layer_attested = True
                    print(f"  Layer {payload.get('layer')} attested: "
                          f"{str(payload.get('signature', ''))[:40]}...")
                    if payload.get("escrow"):
                        print(f"  Escrow: {payload['escrow'].get('available_usd')}")
                elif etype == "task_dispatched":
                    if payload.get("task_id") == "task_02":
                        task_02_dispatched = True
                        print(f"  task_02 dispatched to {payload.get('agent_id')}")
                elif etype == "pipeline_complete":
                    print(f"\n  PIPELINE COMPLETE!")
                    break
                elif etype == "pipeline_halted":
                    print(f"\n  PIPELINE HALTED: {payload.get('reason')}")
                    break
                elif etype == "error":
                    print(f"  ERROR: {payload.get('message', payload)}")
        except asyncio.TimeoutError:
            print("  (timeout waiting for events)")

        # Summary
        print(f"\n{'=' * 60}")
        print(f"  RESULTS")
        print(f"{'=' * 60}")
        print(f"  Layer attested:  {layer_attested}")
        print(f"  Escrow released: {escrow_released}")
        print(f"  task_02 dispatched (layer 1): {task_02_dispatched}")

        if layer_attested and escrow_released and task_02_dispatched:
            print("\n  PASS: Escrow flow works end-to-end!")
        else:
            print("\n  FAIL: Escrow flow incomplete")

        # Get final status
        await ws.send(json.dumps({"type": "get_status"}))
        while True:
            msg = json.loads(await ws.recv())
            if msg["type"] == "status":
                status = msg["data"]
                print(f"\n  Task status: {status.get('by_status', {})}")
                print(f"  Total cost: {status.get('total_cost_cents', 0)}c")
                break


if __name__ == "__main__":
    asyncio.run(main())
