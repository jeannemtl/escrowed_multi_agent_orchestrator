#!/usr/bin/env python3
"""
Speed Benchmark: Cerebras Gemma 4 vs GPU-based GLM-5.2

Runs the same research prompt through both providers and captures
per-task latency, total pipeline time, and tokens/sec.

Usage:
  python3 benchmark_speed.py

Output: JSON results + formatted comparison table.
"""
import asyncio
import json
import os
import sys
import time
import logging

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# Load .env
_env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".env")
if os.path.exists(_env_path):
    with open(_env_path) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, _, val = line.partition("=")
                os.environ.setdefault(key.strip(), val.strip())

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("benchmark")

from agent_client import load_api_keys, call_glm, call_gemma4, MODEL_CONFIG

# Standard research tasks for benchmarking
BENCHMARK_TASKS = [
    {
        "id": "bench_00",
        "name": "Research NVIDIA Blackwell architecture",
        "prompt": "Research NVIDIA's Blackwell GPU architecture. Find specs, performance benchmarks, release timeline, and key technical innovations. Return a structured summary with sources.",
    },
    {
        "id": "bench_01",
        "name": "Research AMD MI400",
        "prompt": "Research AMD's MI400 GPU. Find specs, performance benchmarks, release timeline, and key technical innovations. Return a structured summary with sources.",
    },
    {
        "id": "bench_02",
        "name": "Compare Blackwell vs MI400",
        "prompt": "Using the research on NVIDIA Blackwell and AMD MI400, create a detailed comparison table covering: compute performance, memory bandwidth, power consumption, price, and availability. Highlight key differences.",
    },
]

SYSTEM_PROMPT = "You are a research agent. Provide detailed, structured findings with sources. Do not say you lack web access."


async def benchmark_provider(provider: str, api_key: str) -> dict:
    """Run all benchmark tasks on a single provider, sequentially."""
    results = []
    total_start = time.time()

    config = MODEL_CONFIG[provider]
    caller = call_gemma4 if provider == "gemma4" else call_glm

    for task in BENCHMARK_TASKS:
        log.info(f"  [{provider}] {task['id']}: {task['name']}")
        start = time.time()
        result = await caller(
            api_key=api_key,
            system_prompt=SYSTEM_PROMPT,
            user_prompt=task["prompt"],
            config=config,
            agent_id=f"bench-{provider}",
            ws=None,
            task_id=task["id"],
        )
        elapsed = time.time() - start
        task_result = {
            "task_id": task["id"],
            "task_name": task["name"],
            "ok": result.get("ok", False),
            "latency_ms": result.get("latency_ms", elapsed * 1000),
            "latency_s": elapsed,
            "content_length": len(result.get("content", "")) if result.get("ok") else 0,
            "provider": provider,
            "model": config["model"],
        }
        if result.get("ok"):
            tokens_est = len(result.get("content", "")) // 4  # rough estimate
            task_result["tokens_est"] = tokens_est
            task_result["tokens_per_sec"] = tokens_est / elapsed if elapsed > 0 else 0
        else:
            task_result["error"] = result.get("error", "unknown")

        results.append(task_result)
        status = "OK" if task_result["ok"] else "FAIL"
        log.info(f"    {status} in {elapsed:.1f}s ({task_result['content_length']} chars)")

    total_elapsed = time.time() - total_start
    return {
        "provider": provider,
        "model": config["model"],
        "total_time_s": total_elapsed,
        "total_time_ms": total_elapsed * 1000,
        "tasks": results,
        "successful_tasks": sum(1 for r in results if r["ok"]),
        "total_chars": sum(r["content_length"] for r in results),
        "avg_latency_ms": sum(r["latency_ms"] for r in results) / len(results) if results else 0,
    }


async def main():
    print("=" * 70)
    print("  Speed Benchmark: Cerebras Gemma 4 vs GPU GLM-5.2")
    print("=" * 70)

    keys = load_api_keys()
    print(f"\nCerebras key: {'yes' if keys.get('gemma4') else 'NO'}")
    print(f"OpenRouter key: {'yes' if keys.get('glm') else 'NO'}")

    if not keys.get("gemma4"):
        print("\nERROR: No Cerebras API key. Set CEREBRAS_API_KEY in .env")
        return
    if not keys.get("glm"):
        print("\nERROR: No OpenRouter API key. Set OPENROUTER_API_KEY in .env")
        return

    # Run Cerebras benchmark
    print(f"\n--- Cerebras Gemma 4 31B ---")
    cerebras_result = await benchmark_provider("gemma4", keys["gemma4"])

    # Run GLM benchmark
    print(f"\n--- GPU: GLM-5.2 via OpenRouter ---")
    glm_result = await benchmark_provider("glm", keys["glm"])

    # Comparison
    print(f"\n{'=' * 70}")
    print(f"  RESULTS COMPARISON")
    print(f"{'=' * 70}")
    print(f"{'Metric':<30} {'Cerebras Gemma 4':>20} {'GPU GLM-5.2':>20}")
    print(f"{'-' * 70}")
    print(f"{'Model':<30} {'gemma-4-31b':>20} {'z-ai/glm-5.2':>20}")
    print(f"{'Total time':<30} {cerebras_result['total_time_s']:>19.1f}s {glm_result['total_time_s']:>19.1f}s")
    print(f"{'Avg task latency':<30} {cerebras_result['avg_latency_ms']:>18.0f}ms {glm_result['avg_latency_ms']:>18.0f}ms")
    print(f"{'Successful tasks':<30} {cerebras_result['successful_tasks']:>17}/3 {glm_result['successful_tasks']:>17}/3")
    print(f"{'Total output chars':<30} {cerebras_result['total_chars']:>20} {glm_result['total_chars']:>20}")

    print(f"\n  Per-task breakdown:")
    print(f"  {'Task':<35} {'Cerebras':>12} {'GPU GLM':>12} {'Speedup':>10}")
    print(f"  {'-' * 70}")
    for i, (c, g) in enumerate(zip(cerebras_result["tasks"], glm_result["tasks"])):
        speedup = g["latency_ms"] / c["latency_ms"] if c["latency_ms"] > 0 else 0
        print(f"  {c['task_name'][:35]:<35} {c['latency_ms']/1000:>10.1f}s {g['latency_ms']/1000:>10.1f}s {speedup:>8.1f}x")

    total_speedup = glm_result["total_time_s"] / cerebras_result["total_time_s"] if cerebras_result["total_time_s"] > 0 else 0
    print(f"\n  Total speedup: {total_speedup:.1f}x faster on Cerebras")

    # Save JSON results
    output = {
        "timestamp": time.time(),
        "cerebras": cerebras_result,
        "glm": glm_result,
        "total_speedup": total_speedup,
    }
    results_path = os.path.join(os.path.dirname(__file__), "benchmark_results.json")
    with open(results_path, "w") as f:
        json.dump(output, f, indent=2, default=str)
    print(f"\n  Results saved to: {results_path}")


if __name__ == "__main__":
    asyncio.run(main())
