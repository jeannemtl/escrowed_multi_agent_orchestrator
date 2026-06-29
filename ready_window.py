#!/usr/bin/env python3
"""
Ready-Window Batcher
====================
Collects verified tasks arriving within a time window and batches them
into a single Solana attestation per dependency layer.

Two modes:
  1. "layer_complete" — wait until ALL tasks in a layer are verified,
     then attest as one batch. Maximum batching, but slower tasks delay
     the whole layer.
  2. "time_window" — collect tasks for a fixed window (e.g. 15s), then
     attest whatever has arrived. Faster but may produce multiple txs
     per layer if tasks straggle.

Default: "layer_complete" with a timeout fallback. If not all tasks in
a layer are verified within `timeout` seconds, attest what we have and
mark the rest as failed.
"""

import asyncio
import time
import logging
from collections import defaultdict
from typing import Optional, Callable, Awaitable

log = logging.getLogger("ready-window")


class ReadyWindowBatcher:
    """
    Batches verified tasks into layer-grouped batches for Solana attestation.

    Usage:
        batcher = ReadyWindowBatcher(attest_callback)
        await batcher.submit_verified(task_id, layer, agent_id)
        # When all tasks in a layer are verified (or timeout):
        # batcher calls attest_callback(layer_idx, task_ids, agent_ids)
    """

    def __init__(
        self,
        attest_callback: Callable[[int, list[str], list[str]], Awaitable[dict]],
        mode: str = "layer_complete",
        timeout: float = 120.0,
    ):
        """
        Args:
            attest_callback: async function(layer_idx, task_ids, agent_ids) -> dict
                Called when a batch is ready to attest on Solana.
            mode: "layer_complete" or "time_window"
            timeout: max seconds to wait for a layer to complete (layer_complete mode)
                     or window duration (time_window mode)
        """
        self.attest_callback = attest_callback
        self.mode = mode
        self.timeout = timeout

        # Per-layer state
        self.layer_tasks: dict[int, set[str]] = defaultdict(set)  # all expected task IDs
        self.layer_verified: dict[int, set[str]] = defaultdict(set)  # verified so far
        self.layer_agents: dict[int, dict[str, str]] = defaultdict(dict)  # task_id → agent_id
        self.layer_attested: set[int] = set()
        self.layer_timers: dict[int, asyncio.Task] = {}

        # Results
        self.attestation_results: list[dict] = []

        # Callback when a layer is fully resolved (attested + remaining marked)
        self.on_layer_attested: Optional[Callable] = None

    def register_layer(self, layer_idx: int, task_ids: list[str]):
        """Register the expected tasks for a layer (called at DAG build time)."""
        self.layer_tasks[layer_idx] = set(task_ids)
        log.info(f"Layer {layer_idx}: registered {len(task_ids)} expected tasks")

    async def submit_verified(self, task_id: str, layer_idx: int, agent_id: str):
        """
        Submit a verified task to the batcher.
        When all tasks in the layer are verified (or timeout fires),
        the batch is attested.
        """
        if layer_idx in self.layer_attested:
            log.warning(f"Layer {layer_idx} already attested, ignoring {task_id}")
            return

        self.layer_verified[layer_idx].add(task_id)
        self.layer_agents[layer_idx][task_id] = agent_id

        expected = self.layer_tasks.get(layer_idx, set())
        verified = self.layer_verified[layer_idx]
        log.info(f"Layer {layer_idx}: {len(verified)}/{len(expected)} tasks verified "
                 f"(+{task_id} from {agent_id})")

        if self.mode == "layer_complete":
            if expected and verified >= expected:
                # All tasks verified — attest immediately
                await self._attest_layer(layer_idx)
            elif layer_idx not in self.layer_timers:
                # Start timeout for this layer
                self.layer_timers[layer_idx] = asyncio.create_task(
                    self._timeout_layer(layer_idx)
                )

        elif self.mode == "time_window":
            if layer_idx not in self.layer_timers:
                self.layer_timers[layer_idx] = asyncio.create_task(
                    self._window_timeout(layer_idx)
                )
            if expected and verified >= expected:
                # All arrived before window expired — attest now
                timer = self.layer_timers.pop(layer_idx, None)
                if timer:
                    timer.cancel()
                await self._attest_layer(layer_idx)

    async def _timeout_layer(self, layer_idx: int):
        """Timeout for layer_complete mode: attest what we have after timeout."""
        try:
            await asyncio.sleep(self.timeout)
            if layer_idx not in self.layer_attested:
                expected = self.layer_tasks.get(layer_idx, set())
                verified = self.layer_verified.get(layer_idx, set())
                missing = expected - verified
                log.warning(f"Layer {layer_idx}: timeout reached with {len(verified)}/{len(expected)} "
                           f"verified. Missing: {missing}")
                await self._attest_layer(layer_idx, force=True)
        except asyncio.CancelledError:
            pass

    async def _window_timeout(self, layer_idx: int):
        """Time window expired: attest what we have."""
        try:
            await asyncio.sleep(self.timeout)
            if layer_idx not in self.layer_attested:
                verified = self.layer_verified.get(layer_idx, set())
                if verified:
                    log.info(f"Layer {layer_idx}: time window expired, attesting {len(verified)} tasks")
                    await self._attest_layer(layer_idx, force=True)
        except asyncio.CancelledError:
            pass

    async def _attest_layer(self, layer_idx: int, force: bool = False):
        """Attest the current batch of verified tasks for this layer."""
        if layer_idx in self.layer_attested:
            return

        verified = self.layer_verified.get(layer_idx, set())
        if not verified:
            log.warning(f"Layer {layer_idx}: nothing to attest")
            return

        task_ids = sorted(verified)
        agent_ids = [self.layer_agents[layer_idx].get(tid, "unknown") for tid in task_ids]

        log.info(f"=== Attesting Layer {layer_idx}: {len(task_ids)} tasks from "
                 f"{len(set(agent_ids))} agents ===")

        # Call the attestation callback (Solana batch_attestor)
        result = await self.attest_callback(layer_idx, task_ids, agent_ids)

        result["layer"] = layer_idx
        result["task_ids"] = task_ids
        result["agent_ids"] = agent_ids
        self.attestation_results.append(result)
        self.layer_attested.add(layer_idx)

        # Cancel any pending timer
        timer = self.layer_timers.pop(layer_idx, None)
        if timer:
            timer.cancel()

        if self.on_layer_attested:
            await self.on_layer_attested(layer_idx, task_ids, result)

        # Mark missing tasks as failed (if force=True and some didn't arrive)
        expected = self.layer_tasks.get(layer_idx, set())
        missing = expected - verified
        if missing:
            log.warning(f"Layer {layer_idx}: {len(missing)} tasks not verified in time: {missing}")

    def get_status(self) -> dict:
        """Current state of all layers."""
        layers = {}
        for layer_idx in sorted(self.layer_tasks.keys()):
            expected = self.layer_tasks[layer_idx]
            verified = self.layer_verified.get(layer_idx, set())
            layers[layer_idx] = {
                "expected": len(expected),
                "verified": len(verified),
                "attested": layer_idx in self.layer_attested,
                "missing": len(expected - verified),
                "progress": f"{len(verified)}/{len(expected)}",
            }
        return {
            "mode": self.mode,
            "timeout": self.timeout,
            "layers": layers,
            "total_attestations": len(self.attestation_results),
        }


# ── Self-test ────────────────────────────────────────────────────────

if __name__ == "__main__":
    import json

    async def mock_attest(layer_idx, task_ids, agent_ids):
        """Mock Solana attestation — returns fake signature."""
        print(f"  [MOCK SOLANA] layer:{layer_idx} count:{len(task_ids)} agents:{set(agent_ids)}")
        await asyncio.sleep(0.1)  # simulate network latency
        return {
            "ok": True,
            "signature": f"mock_sig_layer_{layer_idx}",
            "latency_ms": 13000.0,
            "memo": f"layer:{layer_idx} count:{len(task_ids)} tasks:{','.join(task_ids)}",
        }

    async def test():
        print("=== Ready-Window Batcher Test ===\n")

        batcher = ReadyWindowBatcher(mock_attest, mode="layer_complete", timeout=5.0)

        # Register two layers
        batcher.register_layer(0, ["t01", "t02", "t03", "t04"])
        batcher.register_layer(1, ["t05", "t06"])

        # Simulate agents completing tasks at different times
        print("Simulating task completions...")

        await batcher.submit_verified("t01", 0, "agent-1")
        await asyncio.sleep(0.5)
        await batcher.submit_verified("t04", 0, "agent-4")
        await asyncio.sleep(0.5)
        await batcher.submit_verified("t02", 0, "agent-2")
        await asyncio.sleep(0.5)
        await batcher.submit_verified("t03", 0, "agent-1")  # layer 0 complete!

        # Layer 1 (depends on layer 0 — but batcher doesn't know that,
        # the bridge handles ordering)
        await asyncio.sleep(0.5)
        await batcher.submit_verified("t05", 1, "agent-9")
        await asyncio.sleep(0.3)
        await batcher.submit_verified("t06", 1, "agent-3")  # layer 1 complete!

        await asyncio.sleep(1.0)  # let things settle

        print(f"\nStatus: {json.dumps(batcher.get_status(), indent=2)}")
        print(f"\nAttestation results: {len(batcher.attestation_results)}")
        for r in batcher.attestation_results:
            print(f"  Layer {r['layer']}: {len(r['task_ids'])} tasks, "
                  f"sig={r['signature']}, latency={r['latency_ms']}ms")

    asyncio.run(test())
