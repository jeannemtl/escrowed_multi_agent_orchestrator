#!/usr/bin/env python3
"""Quick E2E test: send a real prompt to the bridge and watch the pipeline execute."""
import asyncio
import json
import websockets

async def main():
    uri = "ws://localhost:8765"
    async with websockets.connect(uri) as ws:
        # Send the prompt
        await ws.send(json.dumps({
            "type": "plan_tasks",
            "prompt": "Research NVIDIA Blackwell and AMD MI400, then compare their specs"
        }))
        print("Sent prompt, waiting for events...\n")
        
        events = []
        started = False
        try:
            while True:
                msg = await asyncio.wait_for(ws.recv(), timeout=180)
                data = json.loads(msg)
                events.append(data)
                etype = data.get("type", "?")
                payload = data.get("data", data)  # unwrap if nested
                
                if etype == "plan_ready":
                    tasks = payload.get("tasks", [])
                    total_layers = payload.get("total_layers", "?")
                    print(f"[PLAN READY] {len(tasks)} tasks in {total_layers} layers:")
                    for t in tasks:
                        deps = t.get("dependencies", [])
                        dep_str = f" (deps: {', '.join(deps)})" if deps else ""
                        print(f"  - {t['id']}: {t['name']}{dep_str}")
                    
                    # Send start to begin execution
                    if not started:
                        print("\nSending start...\n")
                        await ws.send(json.dumps({"type": "start"}))
                        started = True
                
                elif etype == "plan_error":
                    print(f"[PLAN ERROR] {payload.get('error', '?')}")
                    break
                
                elif etype == "pipeline_started":
                    print(f"[PIPELINE STARTED] {payload.get('total_tasks', '?')} tasks, {payload.get('total_layers', '?')} layers")
                
                elif etype == "layer_started":
                    print(f"[LAYER {payload.get('layer')}] Started: {payload.get('task_ids', payload.get('tasks', []))}")
                
                elif etype == "task_dispatched":
                    print(f"  → {payload.get('task_id')} dispatched to {payload.get('agent_id')}")
                
                elif etype == "model_streaming":
                    pass  # Skip streaming tokens
                
                elif etype == "task_progress":
                    stage = payload.get("stage", "?")
                    print(f"  … {payload.get('task_id')} [{stage}]")
                
                elif etype == "task_completed":
                    summary = str(payload.get("output_summary", ""))[:120]
                    print(f"  ✓ {payload.get('task_id')} completed: {summary}")
                
                elif etype == "task_verified":
                    print(f"  ✓ {payload.get('task_id')} verified")
                
                elif etype == "layer_attesting":
                    print(f"[ATTESTING] Layer {payload.get('layer')}...")
                
                elif etype == "layer_attested":
                    print(f"[ATTESTED] Layer {payload.get('layer')}: {str(payload.get('signature', ''))[:40]}...")
                
                elif etype == "stripe_charging":
                    cost = payload.get("amount_cents", 0)
                    print(f"[STRIPE] Charging ${int(cost)/100:.2f} for layer {payload.get('layer')}")
                
                elif etype == "layer_confirmed" or etype == "layer_complete":
                    print(f"[CONFIRMED] Layer {payload.get('layer')} confirmed!")
                
                elif etype == "pipeline_complete":
                    print(f"\n✅ PIPELINE COMPLETE!")
                    print(f"   Total cost: ${payload.get('total_cost', 0):.4f}")
                    print(f"   Layers: {payload.get('layers_completed', 0)}")
                    print(f"   Tasks: {payload.get('tasks_completed', 0)}")
                    break
                
                elif etype == "error":
                    print(f"[ERROR] {payload.get('message', payload)}")
                    break
                
                elif etype == "pipeline_halted":
                    print(f"[HALTED] {payload.get('reason', '?')}")
                    break
                
                else:
                    print(f"[{etype}] {json.dumps(payload)[:150]}")
                    
        except asyncio.TimeoutError:
            print("\n⏱️ Timed out waiting for events")
        except Exception as e:
            print(f"\n❌ Error: {e}")
        
        print(f"\nTotal events: {len(events)}")

asyncio.run(main())
