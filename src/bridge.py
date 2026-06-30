#!/usr/bin/env python3
"""
Multi-Agent Orchestrator Bridge
================================
The central WebSocket server that connects agents → verifier → ready-window
batcher → Solana attestation → kanban completion.

Flow:
  1. Client sends "load_tasks" with task definitions
  2. Bridge builds DAG, computes layers, registers with ready-window batcher
  3. Client sends "start" → bridge dispatches layer-0 tasks to agents
  4. Agents POST "submit_task" with their completed work + artifact claims
  5. Bridge runs verifier on each submission
  6. Verified tasks go to ready-window batcher
  7. When a layer is complete → batch Solana attestation (1 tx per layer)
  8. All tasks in layer marked CONFIRMED → next layer tasks become READY
  9. Repeat until all layers processed

WebSocket protocol:
  Client → Bridge:
    {"type": "load_tasks", "tasks": [...]}
    {"type": "start"}
    {"type": "submit_task", "task_id": "...", "agent_id": "...", "artifacts": {...}}
    {"type": "get_status"}
    {"type": "register_agent", "agent_id": "...", "capabilities": [...]}

  Bridge → Client:
    {"type": "dag_ready", "data": {...}}
    {"type": "task_dispatched", "data": {...}}
    {"type": "task_verified", "data": {...}}
    {"type": "layer_attesting", "data": {...}}
    {"type": "layer_attested", "data": {...}}
    {"type": "layer_complete", "data": {...}}
    {"type": "pipeline_complete", "data": {...}}
"""

import asyncio
import json
import os
import sys
import time
import logging
import argparse
import websockets

# Load .env file if it exists (no python-dotenv dependency)
_env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".env")
if os.path.exists(_env_path):
    with open(_env_path) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, _, val = line.partition("=")
                os.environ.setdefault(key.strip(), val.strip())

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger("bridge")

# Add sibling agent-escrow directory first (lower priority)
sys.path.append(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "agent-escrow"))
# Add own directory last but at front (higher priority for local modules)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# Agent escrow integration (local modules)
ESCROW_AVAILABLE = False
try:
    from escrow import Escrow as AgentEscrow
    from agent_wallet import AgentWalletManager
    from attestor import SolanaAttestor as EscrowAttestor
    ESCROW_AVAILABLE = True
    log.info("Agent escrow system loaded")
except Exception as e:
    log.warning(f"Agent escrow not available ({e}), using legacy charger")

from task_dag import (
    Task, TaskStatus, VerifySpec,
    build_dag, compute_layers, get_layer_summary,
    get_ready_tasks, select_next_task, score_task,
    get_dependency_depth, compute_downstream,
)
from verifier import verify_task, verify_batch
from ready_window import ReadyWindowBatcher
from agent_registry import AgentRegistry, AgentStatus
from stripe_charger import (
    create_charger, compute_layer_cost_cents,
    MockCharger, StripeTestCharger,
)


# ── Cost model ───────────────────────────────────────────────────────

BASE_RATE_CENTS = 10

def _clean_output(content: str) -> str:
    """Strip memory/context injections and system artifacts from LLM output."""
    import re
    # Remove <memory-context>...</memory-context> blocks
    content = re.sub(r'<memory-context>.*?</memory-context>', '', content, flags=re.DOTALL)
    # Remove [MEMORY]...[/MEMORY] blocks
    content = re.sub(r'\[MEMORY\].*?\[/MEMORY\]', '', content, flags=re.DOTALL)
    # Remove <!-- Spectral Memory ... --> blocks
    content = re.sub(r'<!-- Spectral Memory.*?-->', '', content, flags=re.DOTALL)
    # Remove [System note:...] lines
    content = re.sub(r'\[System note:.*?\]', '', content, flags=re.DOTALL)
    # Remove lines that look like spectral memory channels (KEY=VALUE)
    content = re.sub(r'\n(USER\.\w+|TASK\.\w+|PROJ\.\w+|PREF\.\w+)=[^\n]+', '', content)
    # Collapse extra whitespace from removals
    content = re.sub(r'\n{3,}', '\n\n', content)
    return content.strip()

def compute_task_cost(task: Task, solana_latency_ms: float, depth: int) -> int:
    """Compute cost in cents for a verified task."""
    depth_mult = 1.0 + (depth * 0.5)
    latency_factor = 1.0 + (solana_latency_ms / 5000.0)
    downstream_bonus = 1.0 + (task.downstream_count * 0.1)
    charge = BASE_RATE_CENTS * depth_mult * latency_factor * task.cost_weight * downstream_bonus
    return max(1, round(charge))


def compute_layer_cost(tasks: list[Task], solana_latency_ms: float) -> int:
    """Cost for an entire layer = sum of task costs × batch efficiency discount."""
    total = 0
    for task in tasks:
        depth = task.layer
        total += compute_task_cost(task, solana_latency_ms, depth)
    # Batch discount: more tasks per layer = more efficient
    batch_efficiency = 1.0 / (1.0 + len(tasks) * 0.05)
    return max(1, round(total * batch_efficiency))


# ── Solana attestation ───────────────────────────────────────────────

SOLANA_AVAILABLE = False
try:
    from solana_attestor import attest_layer as solana_attest_layer, attest_layer_sync
    SOLANA_AVAILABLE = True
    log.info("Solana attestor loaded")
except Exception as e:
    log.warning(f"Solana not available ({e}), will use mock attestation")


async def mock_attest_layer(layer_idx: int, task_ids: list[str], agent_ids: list[str]) -> dict:
    """Mock Solana attestation when Solana is not available."""
    memo = f"layer:{layer_idx} count:{len(task_ids)} agents:{','.join(set(agent_ids))} tasks:{','.join(t[:8] for t in task_ids)}"
    log.info(f"[MOCK SOLANA] {memo}")
    await asyncio.sleep(0.5)  # simulate network latency
    return {
        "ok": True,
        "layer": layer_idx,
        "task_ids": task_ids,
        "signature": f"mock_sig_layer_{layer_idx}_{int(time.time())}",
        "latency_ms": 13000.0,  # realistic devnet latency
        "memo": memo[:566],
        "slot": -1,
    }


async def real_attest_layer(layer_idx: int, task_ids: list[str], agent_ids: list[str]) -> dict:
    """Real Solana attestation using the local solana_attestor module."""
    # Compute downstream count for memo
    downstream_count = sum(1 for _ in task_ids)  # simplified
    depth = layer_idx
    result = await solana_attest_layer(
        layer_idx=layer_idx,
        task_ids=task_ids,
        depth=depth,
        downstream_count=downstream_count,
    )
    return result


# ── Main Bridge ──────────────────────────────────────────────────────

class MultiAgentBridge:
    """
    Central orchestrator that coordinates:
      - Task DAG with dependency layers
      - Agent registry with workload tracking
      - Artifact verification before accepting completions
      - Ready-window batching for Solana attestation
      - Per-layer cost computation
    """

    def __init__(self, use_solana: bool = False, batch_timeout: float = 120.0,
                 stripe_mode: str = "mock", budget_cents: int = 500,
                 use_escrow: bool = False):
        self.tasks: dict[str, Task] = {}
        self.layers: list[list[str]] = []
        self.registry = AgentRegistry()
        self.use_solana = use_solana and SOLANA_AVAILABLE
        self.batch_timeout = batch_timeout
        self.total_cost_cents = 0
        self.current_layer = 0
        self.pipeline_started = False
        self.batcher: ReadyWindowBatcher = None
        self.attestation_function = real_attest_layer if self.use_solana else mock_attest_layer
        # Multi-client support: track all connected websockets + agent routing
        self.clients: set = set()  # all connected websockets
        self.agent_sockets: dict[str, object] = {}  # agent_id -> websocket
        self.dashboard_socket = None  # the "main" dashboard client

        # Escrow mode: per-agent wallets with verify→attest→release
        self.use_escrow = use_escrow and ESCROW_AVAILABLE
        self.escrow = None
        self.wallets = None
        self.escrow_attestor = None

        if self.use_escrow:
            self.escrow = AgentEscrow(mode=stripe_mode)
            self.wallets = AgentWalletManager(mode=stripe_mode)
            self.escrow_attestor = EscrowAttestor(mode="mock")
            # Deposit budget into escrow
            self.escrow.deposit(budget_cents)
            log.info(f"Escrow mode: ${budget_cents/100:.2f} deposited, per-agent wallets active")
        else:
            # Legacy charger
            self.charger = create_charger(stripe_mode, budget_cents)

        self.stripe_mode = stripe_mode
        self.budget_cents = budget_cents
        self.pipeline_halted = False  # set True when budget exceeded

    async def _attest_callback(self, layer_idx: int, task_ids: list[str], agent_ids: list[str]) -> dict:
        """Called by the ready-window batcher when a layer is ready to attest.

        Escrow flow:
          1. Attest on Solana (proof of work)
          2. Charge via Stripe (settlement)
          3. If charge succeeds → promote layer (dispatch downstream)
          4. If charge fails (budget exceeded) → HALT pipeline
        """
        if self.pipeline_halted:
            log.warning(f"Pipeline halted (budget exceeded), skipping layer {layer_idx}")
            return {"ok": False, "error": "pipeline_halted"}

        log.info(f"Attesting layer {layer_idx}: {len(task_ids)} tasks from agents {set(agent_ids)}")

        if self.clients:
            await self._broadcast({"type": "layer_attesting", "data": {
                "layer": layer_idx,
                "task_ids": task_ids,
                "agent_ids": list(set(agent_ids)),
                "use_solana": self.use_solana,
            }})

        # ── Step 1: Solana attestation (proof of work) ──
        result = await self.attestation_function(layer_idx, task_ids, agent_ids)

        if not result.get("ok"):
            log.error(f"Layer {layer_idx} attestation failed: {result.get('error')}")
            if self.clients:
                await self._broadcast({"type": "error", "data": {
                    "message": f"Layer {layer_idx} attestation failed",
                    "error": result.get("error", "unknown"),
                }})
            return result

        # ── Update task statuses with Solana proof ──
        layer_tasks = [self.tasks[tid] for tid in task_ids if tid in self.tasks]
        latency = result.get("latency_ms", 13000)
        solana_sig = result.get("signature", "")

        # Compute cost using the escrow cost model
        layer_cost = compute_layer_cost_cents(len(task_ids), layer_idx)
        self.total_cost_cents += layer_cost

        for tid in task_ids:
            if tid in self.tasks:
                task = self.tasks[tid]
                task.status = TaskStatus.ATTESTED
                task.solana_signature = solana_sig
                task.solana_latency_ms = latency
                task.cost_cents = layer_cost // max(len(task_ids), 1)

        log.info(f"Layer {layer_idx} attested on Solana: sig={solana_sig[:20]}... "
                 f"latency={latency:.0f}ms cost={layer_cost}c")

        # ── Step 2: Payment settlement ──
        if self.use_escrow:
            # ESCROW MODE: release per-agent payments from escrow
            # Each agent gets their share, credited to their wallet
            per_task_cost = layer_cost // max(len(task_ids), 1)

            if self.clients:
                await self._broadcast({"type": "stripe_charging", "data": {
                    "layer": layer_idx,
                    "amount_cents": layer_cost,
                    "per_agent_cents": per_task_cost,
                    "solana_signature": solana_sig,
                    "mode": "escrow",
                    "agent_count": len(set(agent_ids)),
                }})

            # Release to each agent's wallet
            agent_releases = []
            for tid in task_ids:
                if tid in self.tasks:
                    task = self.tasks[tid]
                    # Create wallet if not exists
                    if task.agent_id not in self.wallets.wallets:
                        self.wallets.create_wallet(task.agent_id)

                    release = self.escrow.release(
                        agent_id=task.agent_id,
                        amount_cents=per_task_cost,
                        solana_sig=solana_sig,
                        task_id=tid,
                        wallet_manager=self.wallets,
                    )
                    agent_releases.append({
                        "agent_id": task.agent_id,
                        "task_id": tid,
                        "amount_cents": per_task_cost,
                        "ok": release.get("ok", False),
                        "transfer_id": release.get("transfer_id", ""),
                    })

            # Check if escrow is exhausted
            escrow_status = self.escrow.status()
            remaining_budget = escrow_status["available_cents"]

            if remaining_budget <= 0 and any(not r["ok"] for r in agent_releases):
                self.pipeline_halted = True
                log.error(f"⚠️  PIPELINE HALTED: escrow exhausted at layer {layer_idx}")
                if self.clients:
                    await self._broadcast({"type": "pipeline_halted", "data": {
                        "layer": layer_idx,
                        "reason": "escrow_exhausted",
                        "solana_signature": solana_sig,
                        "amount_cents": layer_cost,
                        "remaining_cents": remaining_budget,
                        "message": f"Escrow exhausted. On-chain proof: {solana_sig[:30]}...",
                    }})
                return {"ok": False, "error": "escrow_exhausted"}

            # Broadcast escrow release with wallet balances
            if self.clients:
                await self._broadcast({"type": "escrow_released", "data": {
                    "layer": layer_idx,
                    "releases": agent_releases,
                    "wallets": self.wallets.get_all_summaries(),
                    "escrow": escrow_status,
                }})

            charge_id = f"escrow_{solana_sig[:12]}"
        else:
            # LEGACY MODE: single Stripe charge
            if self.clients:
                await self._broadcast({"type": "stripe_charging", "data": {
                    "layer": layer_idx,
                    "amount_cents": layer_cost,
                    "solana_signature": solana_sig,
                    "mode": self.stripe_mode,
                    "budget_cents": self.budget_cents,
                    "total_spent_cents": self.charger.total_spent,
                }})

            charge_result = await self.charger.charge(
                amount_cents=layer_cost,
                layer_idx=layer_idx,
                task_ids=task_ids,
                solana_sig=solana_sig,
            )

            if not charge_result.get("ok"):
                self.pipeline_halted = True
                log.error(f"⚠️  PIPELINE HALTED at layer {layer_idx}: {charge_result.get('error')}")
                for tid in task_ids:
                    if tid in self.tasks:
                        self.tasks[tid].solana_signature = solana_sig
                if self.clients:
                    await self._broadcast({"type": "pipeline_halted", "data": {
                        "layer": layer_idx,
                        "reason": charge_result.get("error", "unknown"),
                        "solana_signature": solana_sig,
                        "amount_cents": layer_cost,
                        "total_spent_cents": self.charger.total_spent,
                        "budget_cents": self.budget_cents,
                        "remaining_cents": self.budget_cents - self.charger.total_spent,
                        "message": (
                            f"Pipeline halted: layer {layer_idx} costs ${layer_cost/100:.2f} "
                            f"but only ${(self.budget_cents - self.charger.total_spent)/100:.2f} "
                            f"remaining in budget. On-chain proof: {solana_sig[:30]}..."
                        ),
                    }})
                return charge_result

            charge_id = charge_result.get("charge_id", "")
            remaining_budget = charge_result.get("remaining_budget_cents", 0)

        for tid in task_ids:
            if tid in self.tasks:
                task = self.tasks[tid]
                task.status = TaskStatus.CONFIRMED
                task.stripe_charge_id = charge_id  # type: ignore[attr-defined]
                self.registry.complete_task(task.agent_id, tid, success=True)

        log.info(f"Layer {layer_idx} escrow settled: charge={charge_id} "
                 f"remaining_budget=${remaining_budget/100:.2f}")

        if self.clients:
            layer_data = {
                "layer": layer_idx,
                "signature": solana_sig,
                "latency_ms": latency,
                "cost_cents": layer_cost,
                "total_cost_cents": self.total_cost_cents,
                "task_ids": task_ids,
                "agent_ids": list(set(agent_ids)),
                "stripe_charge_id": charge_id,
                "stripe_mode": "escrow" if self.use_escrow else self.stripe_mode,
                "remaining_budget_cents": remaining_budget,
                "budget_cents": self.budget_cents,
            }
            # Include escrow + wallet info when in escrow mode
            if self.use_escrow:
                layer_data["escrow"] = self.escrow.status()
                layer_data["wallets"] = self.wallets.get_all_summaries()
            await self._broadcast({"type": "layer_attested", "data": layer_data})

        # ── Step 4: Dispatch next layer ──
        await self._dispatch_ready_tasks()
        return result

    def _setup_batcher(self):
        """Initialize the ready-window batcher with the attest callback."""
        self.batcher = ReadyWindowBatcher(
            attest_callback=self._attest_callback,
            mode="layer_complete",
            timeout=self.batch_timeout,
        )

        # Register all layers
        for i, layer_ids in enumerate(self.layers):
            self.batcher.register_layer(i, layer_ids)

        # When a layer is attested, try to dispatch next layer
        async def on_attested(layer_idx, task_ids, result):
            log.info(f"Layer {layer_idx} attested, checking for ready tasks...")
            await self._dispatch_ready_tasks()
            # Check if pipeline is complete
            if all(t.status == TaskStatus.CONFIRMED for t in self.tasks.values()):
                await self._pipeline_complete()

        self.batcher.on_layer_attested = on_attested

    def load_tasks(self, tasks_raw: list[dict]) -> dict:
        """Build the DAG from task definitions."""
        self.tasks, self.layers = build_dag(tasks_raw)
        self._setup_batcher()

        # Auto-register agents from task assignments
        for task in self.tasks.values():
            if task.agent_id not in self.registry.agents:
                self.registry.register(task.agent_id)

        summaries = get_layer_summary(self.tasks, self.layers)
        log.info(f"Loaded {len(self.tasks)} tasks into {len(self.layers)} layers")
        for s in summaries:
            log.info(f"  Layer {s['layer']}: {s['count']} tasks | agents={s['agents']} | "
                     f"downstream={s['total_downstream']}")

        return {
            "total_tasks": len(self.tasks),
            "total_layers": len(self.layers),
            "layers": summaries,
            "agents": self.registry.get_load_summary(),
            "solana_enabled": self.use_solana,
            "individual_attestations": len(self.tasks),
            "batched_attestations": len(self.layers),
            "savings": len(self.tasks) - len(self.layers),
        }

    async def start_pipeline(self):
        """Start dispatching tasks from layer 0."""
        self.pipeline_started = True
        log.info("=== Pipeline Started ===")
        await self._dispatch_ready_tasks()

    async def _dispatch_ready_tasks(self):
        """Dispatch all ready tasks to their assigned agents."""
        ready = get_ready_tasks(self.tasks)
        if not ready:
            # Check if all done
            pending = [t for t in self.tasks.values() if t.status in (TaskStatus.PENDING, TaskStatus.DISPATCHED)]
            if not pending:
                await self._pipeline_complete()
            return

        for tid in ready:
            task = self.tasks[tid]
            if task.status != TaskStatus.PENDING:
                continue

            # Assign to agent
            if self.registry.assign_task(task.agent_id, tid):
                task.status = TaskStatus.DISPATCHED
                log.info(f"Dispatched {tid} ({task.name}) -> {task.agent_id}")

                dispatch_msg = {"type": "task_dispatched", "data": {
                    "task_id": tid,
                    "agent_id": task.agent_id,
                    "task_name": task.name,
                    "layer": task.layer,
                    "description": task.description,
                    "verify_method": task.verify.method if task.verify else "none",
                    "verify_params": task.verify.params if task.verify else {},
                    "upstream_results": [
                        {
                            "task_id": dep_id,
                            "summary": self.tasks[dep_id].output_summary[:1000],
                        }
                        for dep_id in task.dependencies
                        if dep_id in self.tasks and self.tasks[dep_id].output_summary
                    ],
                }}

                # Send to the specific agent's socket only
                agent_ws = self.agent_sockets.get(task.agent_id)
                if agent_ws:
                    await self._send_to(agent_ws, dispatch_msg)
                # Broadcast to dashboard (non-agent clients) for visibility
                # Agent already got it via _send_to; dashboard needs it too
                await self._broadcast(dispatch_msg)
            else:
                log.warning(f"Agent {task.agent_id} not available for task {tid}")

    async def submit_task(self, task_id: str, agent_id: str, artifacts: dict) -> dict:
        """
        Agent submits completed work.
        The bridge verifies artifacts before accepting.
        """
        task = self.tasks.get(task_id)
        if task is None:
            return {"ok": False, "error": f"unknown task: {task_id}"}

        if task.status not in (TaskStatus.DISPATCHED, TaskStatus.SUBMITTED, TaskStatus.VERIFIED):
            # Already confirmed/attested — likely a duplicate submission from dedupe race
            if task.status in (TaskStatus.CONFIRMED, TaskStatus.ATTESTED):
                log.info(f"Task {task_id} already {task.status.value}, ignoring duplicate submission from {agent_id}")
                return {"ok": True, "verified": True, "skipped": True}
            return {"ok": False, "error": f"task {task_id} not in dispatchable state (status={task.status.value})"}

        task.submitted_artifacts = artifacts
        task.submitted_at = time.time()
        task.status = TaskStatus.SUBMITTED
        task.output_summary = artifacts.get("summary", "")
        task.output_metadata = artifacts.get("metadata", {})

        log.info(f"Agent {agent_id} submitted task {task_id}, verifying artifacts...")

        # ── Verify artifacts ──
        verification = verify_task(task)
        task.verification_result = verification
        task.verified_at = time.time()

        if self.clients:
            await self._broadcast({"type": "task_verified", "data": {
                "task_id": task_id,
                "agent_id": agent_id,
                "ok": verification["ok"],
                "method": verification.get("method", "none"),
                "error": verification.get("error"),
                "layer": task.layer,
                "summary": task.output_summary if task.output_summary else "",
                "metadata": task.output_metadata,
            }})

        if verification["ok"]:
            task.status = TaskStatus.VERIFIED
            log.info(f"Task {task_id} verified ✓")

            # Submit to ready-window batcher
            await self.batcher.submit_verified(task_id, task.layer, agent_id)
            return {"ok": True, "verified": True}
        else:
            task.status = TaskStatus.FAILED
            self.registry.complete_task(agent_id, task_id, success=False)
            log.warning(f"Task {task_id} verification FAILED: {verification.get('error')}")

            # TODO: reassign logic — find another agent
            return {"ok": False, "verified": False, "error": verification.get("error")}

    async def _pipeline_complete(self):
        """Called when all tasks are confirmed."""
        summary = {
            "total_tasks": len(self.tasks),
            "total_layers": len(self.layers),
            "total_cost_cents": self.total_cost_cents,
            "attestations": len(self.batcher.attestation_results) if self.batcher else 0,
            "individual_vs_batched": {
                "individual_attestations": len(self.tasks),
                "batched_attestations": len(self.layers),
                "savings": len(self.tasks) - len(self.layers),
            },
            "agent_stats": self.registry.get_load_summary(),
            "results": [r for r in self.batcher.attestation_results] if self.batcher else [],
        }
        log.info(f"=== PIPELINE COMPLETE ===")
        log.info(f"Tasks: {summary['total_tasks']} | Layers: {summary['total_layers']} | "
                 f"Attestations: {summary['attestations']} | Cost: ${summary['total_cost_cents']/100:.2f}")

        if self.clients:
            await self._broadcast({"type": "pipeline_complete", "data": summary})

    def get_status(self) -> dict:
        by_status = {}
        for t in self.tasks.values():
            s = t.status.value
            by_status.setdefault(s, []).append(t.id)

        return {
            "total_tasks": len(self.tasks),
            "total_layers": len(self.layers),
            "by_status": {k: v for k, v in by_status.items()},
            "current_layer": self.current_layer,
            "total_cost_cents": self.total_cost_cents,
            "solana_enabled": self.use_solana,
            "batcher_status": self.batcher.get_status() if self.batcher else None,
            "agent_registry": self.registry.get_load_summary(),
            "layers": get_layer_summary(self.tasks, self.layers),
        }

    async def _send_to(self, ws, msg: dict):
        """Send to a specific websocket."""
        try:
            await ws.send(json.dumps(msg, default=str))
        except Exception:
            pass

    async def _broadcast(self, msg: dict):
        """Broadcast to all connected clients."""
        text = json.dumps(msg, default=str)
        dead = set()
        for ws in list(self.clients):  # copy to avoid mutation during iteration
            try:
                await ws.send(text)
            except Exception:
                dead.add(ws)
        self.clients -= dead

    async def run_comparison_race(self, prompt: str, ws, image_b64: str = ""):
        """Run Cerebras and GPU GLM simultaneously, with layers, attestation, escrow.

        If image_b64 is provided, uses vision model + planner to decompose the image
        into image-specific subtasks instead of the generic hardcoded templates.
        """
        from agent_client import load_api_keys, call_glm, call_gemma4, MODEL_CONFIG
        import hashlib
        import time as _time

        keys = load_api_keys()
        if not keys.get("gemma4") or not keys.get("glm"):
            await self._send_to(ws, {"type": "comparison_error", "data": {"error": "Missing API keys for one or both providers"}})
            return

        # Build layered task structure — from image (planner) or from prompt (templates)
        if image_b64:
            log.info("Race: decomposing image with vision model + planner...")
            await self._broadcast({"type": "comparison_planning", "data": {
                "stage": "analyzing_image",
                "message": "Analyzing image with vision model, then decomposing into race tasks...",
            }})
            try:
                from planner import plan_from_image
                planned = await plan_from_image(image_b64, user_prompt=prompt)
                layers = self._planner_tasks_to_layers(planned) if planned else None
                if not layers:
                    log.warning("Image planning returned no tasks, falling back to prompt templates")
                    layers = self._get_comparison_layers(prompt)
            except Exception as e:
                log.error(f"Image planning failed: {e}, falling back to prompt templates")
                layers = self._get_comparison_layers(prompt)
        else:
            layers = self._get_comparison_layers(prompt)

        # Announce the race with full layer structure
        await self._broadcast({
            "type": "comparison_start",
            "data": {
                "layers": [
                    {
                        "layer": l["layer"],
                        "name": l["name"],
                        "tasks": [{"id": t["id"], "name": t["name"]} for t in l["tasks"]],
                    }
                    for l in layers
                ],
                "cerebras_model": MODEL_CONFIG["gemma4"]["model"],
                "glm_model": MODEL_CONFIG["glm"]["model"],
                "budget_cents": 500,  # $5.00 each side
            }
        })

        SYSTEM = "You are a research agent. Provide detailed, structured findings. Be thorough and specific."

        async def run_side(provider, api_key, caller, label):
            """Run all layers on one provider, streaming progress + attestation + escrow."""
            config = MODEL_CONFIG[provider]
            all_results = []
            all_layer_attestations = []
            start = _time.time()
            budget_remaining = 500  # cents
            agent_wallet = 0  # cents

            for layer in layers:
                layer_idx = layer["layer"]
                layer_tasks = layer["tasks"]
                layer_results = []

                # Announce layer starting
                await self._broadcast({
                    "type": "comparison_layer",
                    "data": {
                        "side": label,
                        "layer": layer_idx,
                        "layer_name": layer["name"],
                        "stage": "dispatching",
                        "task_count": len(layer_tasks),
                        "elapsed_s": _time.time() - start,
                    }
                })

                # Run all tasks in this layer (sequentially within a side)
                for task in layer_tasks:
                    await self._broadcast({
                        "type": "comparison_progress",
                        "data": {
                            "side": label,
                            "layer": layer_idx,
                            "task_id": task["id"],
                            "task_name": task["name"],
                            "stage": "starting",
                            "elapsed_s": _time.time() - start,
                        }
                    })

                    t_start = _time.time()
                    try:
                        result = await caller(
                            api_key=api_key,
                            system_prompt=SYSTEM,
                            user_prompt=task["prompt"],
                            config=config,
                            agent_id=f"race-{label}",
                            ws=None,
                            task_id=task["id"],
                        )
                        elapsed = _time.time() - t_start
                        ok = result.get("ok", False)
                        content = result.get("content", "")
                        # Strip memory/context injections from the output
                        content = _clean_output(content)

                        task_result = {
                            "task_id": task["id"],
                            "task_name": task["name"],
                            "ok": ok,
                            "latency_ms": result.get("latency_ms", elapsed * 1000),
                            "latency_s": elapsed,
                            "content_length": len(content),
                            "content_preview": content[:5000],
                            "layer": layer_idx,
                        }
                        layer_results.append(task_result)
                        all_results.append(task_result)

                        await self._broadcast({
                            "type": "comparison_progress",
                            "data": {
                                "side": label,
                                "layer": layer_idx,
                                "task_id": task["id"],
                                "task_name": task["name"],
                                "stage": "done",
                                "ok": ok,
                                "latency_s": elapsed,
                                "latency_ms": task_result["latency_ms"],
                                "content_preview": content[:5000],
                                "content_length": len(content),
                                "elapsed_s": _time.time() - start,
                            }
                        })
                    except Exception as e:
                        elapsed = _time.time() - t_start
                        layer_results.append({
                            "task_id": task["id"],
                            "task_name": task["name"],
                            "ok": False,
                            "latency_ms": elapsed * 1000,
                            "error": str(e),
                            "layer": layer_idx,
                        })
                        all_results.append(layer_results[-1])
                        await self._broadcast({
                            "type": "comparison_progress",
                            "data": {
                                "side": label,
                                "layer": layer_idx,
                                "task_id": task["id"],
                                "task_name": task["name"],
                                "stage": "error",
                                "error": str(e),
                                "elapsed_s": _time.time() - start,
                            }
                        })

                # Layer complete — verify + attest
                layer_latency = sum(r.get("latency_ms", 0) for r in layer_results)
                verified_count = sum(1 for r in layer_results if r.get("ok"))
                cost_cents = min(50, budget_remaining)  # $0.50 per layer
                budget_remaining -= cost_cents
                agent_wallet += cost_cents

                # Mock Solana signature
                sig_input = f"{label}-{layer_idx}-{_time.time()}"
                sig = hashlib.sha256(sig_input.encode()).hexdigest()[:44]

                attestation = {
                    "layer": layer_idx,
                    "layer_name": layer["name"],
                    "task_count": len(layer_results),
                    "verified_count": verified_count,
                    "latency_ms": layer_latency,
                    "cost_cents": cost_cents,
                    "signature": sig,
                    "remaining_budget_cents": budget_remaining,
                    "agent_wallet_cents": agent_wallet,
                }
                all_layer_attestations.append(attestation)

                await self._broadcast({
                    "type": "comparison_attested",
                    "data": {
                        "side": label,
                        **attestation,
                        "elapsed_s": _time.time() - start,
                    }
                })

                # Escrow release
                await self._broadcast({
                    "type": "comparison_escrow",
                    "data": {
                        "side": label,
                        "layer": layer_idx,
                        "released_cents": cost_cents,
                        "agent_wallet_cents": agent_wallet,
                        "remaining_budget_cents": budget_remaining,
                        "total_budget_cents": 500,
                        "signature": sig,
                    }
                })

            total = _time.time() - start

            # Broadcast per-side completion immediately
            await self._broadcast({
                "type": "comparison_side_done",
                "data": {
                    "side": label,
                    "total_time_s": total,
                    "successful": sum(1 for r in all_results if r.get("ok")),
                    "total_tasks": len(all_results),
                    "model": config["model"],
                }
            })

            return {
                "provider": provider,
                "model": config["model"],
                "label": label,
                "total_time_s": total,
                "total_time_ms": total * 1000,
                "tasks": all_results,
                "layers": all_layer_attestations,
                "successful": sum(1 for r in all_results if r.get("ok")),
                "total_cost_cents": 500 - budget_remaining,
                "agent_wallet_cents": agent_wallet,
            }

        # Run both sides simultaneously
        cerebras_task = asyncio.create_task(
            run_side("gemma4", keys["gemma4"], call_gemma4, "cerebras")
        )
        glm_task = asyncio.create_task(
            run_side("glm", keys["glm"], call_glm, "gpu")
        )

        c_result, g_result = await asyncio.gather(cerebras_task, glm_task)

        speedup = g_result["total_time_s"] / c_result["total_time_s"] if c_result["total_time_s"] > 0 else 0

        await self._broadcast({
            "type": "comparison_complete",
            "data": {
                "cerebras": c_result,
                "gpu": g_result,
                "total_speedup": speedup,
                "winner": "cerebras" if speedup > 1 else "gpu",
                "layers": [
                    {"layer": l["layer"], "name": l["name"]} for l in layers
                ],
            }
        })
        log.info(f"Comparison race done: Cerebras {c_result['total_time_s']:.1f}s vs GPU {g_result['total_time_s']:.1f}s = {speedup:.1f}x")

    def _planner_tasks_to_layers(self, planned_tasks: list[dict]) -> list:
        """Convert planner output (flat task list with dependencies) into race layers.

        Planner returns: [{"id":"task_00","name":"...","description":"...","dependencies":[]}]
        Race needs:     [{"layer":0,"name":"...","tasks":[{"id":"...","name":"...","prompt":"..."}]}]
        """
        from task_dag import build_dag, compute_layers

        if not planned_tasks:
            return []

        # Ensure each task has a description (used as the prompt for the race agent)
        for t in planned_tasks:
            if "description" not in t or not t["description"]:
                t["description"] = t.get("name", "Research task")

        # Use the DAG builder to compute dependency-based layers
        tasks_dict, layer_ids = build_dag(planned_tasks)

        # Layer name heuristics
        layer_names = [
            "Parallel Research",
            "Synthesis & Analysis",
            "Refinement & Reporting",
            "Final Review",
        ]

        race_layers = []
        for i, task_ids in enumerate(layer_ids):
            name = layer_names[i] if i < len(layer_names) else f"Layer {i}"
            race_tasks = []
            for tid in task_ids:
                t = tasks_dict[tid]
                race_tasks.append({
                    "id": t.id,
                    "name": t.name,
                    "prompt": t.description,
                })
            race_layers.append({
                "layer": i,
                "name": name,
                "tasks": race_tasks,
            })
        return race_layers

    def _get_comparison_layers(self, prompt: str) -> list:
        """Decompose prompt into layered DAG structure for the race."""
        if prompt and len(prompt) > 20:
            return [
                {
                    "layer": 0,
                    "name": "Parallel Research",
                    "tasks": [
                        {
                            "id": "race_00",
                            "name": "Research: Architecture & Technology",
                            "prompt": f"Research the technical architecture and key innovations of: {prompt}. Focus on specs, design choices, and performance. Return a structured summary.",
                        },
                        {
                            "id": "race_01",
                            "name": "Research: Market & Competition",
                            "prompt": f"Research the market positioning and competitive landscape for: {prompt}. Cover pricing, alternatives, and key differentiators. Return a structured summary.",
                        },
                    ],
                },
                {
                    "layer": 1,
                    "name": "Synthesis & Analysis",
                    "tasks": [
                        {
                            "id": "race_02",
                            "name": "Synthesis: Competitive Brief",
                            "prompt": f"Synthesize the research findings about {prompt[:200]} into a comprehensive competitive brief. Combine technical and market analysis into actionable insights.",
                        },
                    ],
                },
            ]
        # Default Cerebras-themed tasks
        return [
            {
                "layer": 0,
                "name": "Parallel Research",
                "tasks": [
                    {
                        "id": "race_00",
                        "name": "Research Cerebras CS-3 wafer-scale architecture",
                        "prompt": "Research Cerebras Systems' CS-3 wafer-scale chip architecture. Find specs, performance benchmarks, and key technical innovations. Return a structured summary.",
                    },
                    {
                        "id": "race_01",
                        "name": "Research Cerebras Inference API & pricing",
                        "prompt": "Research Cerebras Inference API, supported models, pricing ($0.25/M tokens), and how it achieves latency advantages. Return a structured summary.",
                    },
                ],
            },
            {
                "layer": 1,
                "name": "Synthesis & Analysis",
                "tasks": [
                    {
                        "id": "race_02",
                        "name": "Competitive positioning brief",
                        "prompt": "Create a competitive positioning brief comparing Cerebras to GPU-based inference (NVIDIA, OpenAI). Cover speed, cost, and architecture differences. Use the research findings to support your analysis.",
                    },
                ],
            },
        ]


# ── WebSocket Server ─────────────────────────────────────────────────

async def main():
    parser = argparse.ArgumentParser(description="Multi-Agent Orchestrator Bridge")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--solana", action="store_true", help="Use real Solana attestation")
    parser.add_argument("--stripe", choices=["mock", "test", "live"], default="mock",
                        help="Stripe mode: mock (no Stripe), test (test mode 4242), live (real money)")
    parser.add_argument("--budget", type=float, default=5.00,
                        help="Budget cap in dollars (e.g. 5.00 = $5). Pipeline stops when exceeded.")
    parser.add_argument("--batch-timeout", type=float, default=120.0,
                        help="Seconds to wait for a layer to complete before force-attesting")
    parser.add_argument("--escrow", action="store_true",
                        help="Use per-agent escrow wallets (verify→attest→release to agent)")
    args = parser.parse_args()

    budget_cents = int(args.budget * 100)
    bridge = MultiAgentBridge(
        use_solana=args.solana,
        batch_timeout=args.batch_timeout,
        stripe_mode=args.stripe,
        budget_cents=budget_cents,
        use_escrow=args.escrow,
    )

    log.info(f"Solana: {'ENABLED' if bridge.use_solana else 'MOCK (no real on-chain txs)'}")
    log.info(f"Escrow: {'ENABLED (per-agent wallets)' if bridge.use_escrow else 'disabled (legacy charger)'}")
    log.info(f"Stripe: {args.stripe.upper()} mode, budget=${args.budget:.2f}")
    log.info(f"Batch timeout: {args.batch_timeout}s")

    async def handle_connection(websocket):
        bridge.clients.add(websocket)
        log.info(f"Client connected from {websocket.remote_address} (total: {len(bridge.clients)})")

        try:
            async for message in websocket:
                try:
                    req = json.loads(message)
                except json.JSONDecodeError:
                    await websocket.send(json.dumps({"error": "invalid JSON"}))
                    continue

                msg_type = req.get("type", "")

                if msg_type == "load_tasks":
                    result = bridge.load_tasks(req.get("tasks", []))
                    await bridge._send_to(websocket, {"type": "dag_ready", "data": result})

                elif msg_type == "plan_tasks":
                    # Planner: decompose user prompt into subtasks with dependencies
                    user_prompt = req.get("prompt", "")
                    image_b64 = req.get("image", "")
                    if not user_prompt and not image_b64:
                        await bridge._send_to(websocket, {"type": "plan_error", "data": {
                            "error": "no prompt or image provided",
                        }})
                        continue

                    if image_b64:
                        log.info(f"Planning from image ({len(image_b64)} chars base64)...")
                        await bridge._send_to(websocket, {"type": "planning", "data": {
                            "stage": "starting",
                            "message": "Analyzing image with vision model...",
                            "planner": "vision + nemotron",
                        }})
                    else:
                        log.info(f"Planning: decomposing user prompt ({len(user_prompt)} chars)...")
                        await bridge._send_to(websocket, {"type": "planning", "data": {
                            "stage": "starting",
                            "message": "Connecting to Nemotron 3 Ultra via NemoClaw sandbox...",
                            "planner": "nemotron-3-ultra",
                        }})
                    try:
                        from planner import plan_tasks, plan_from_image
                        # Send progress after 5s if still planning
                        async def planning_progress():
                            await asyncio.sleep(5)
                            if image_b64:
                                msg = "Vision model analyzed image. Nemotron is decomposing into tasks..."
                            else:
                                msg = "Nemotron 3 Ultra is decomposing your prompt into tasks..."
                            await bridge._send_to(websocket, {"type": "planning", "data": {
                                "stage": "thinking",
                                "message": msg,
                                "planner": "nemotron-3-ultra",
                            }})
                            await asyncio.sleep(10)
                            await bridge._send_to(websocket, {"type": "planning", "data": {
                                "stage": "thinking",
                                "message": "Still reasoning... (NemoClaw inference routing)",
                                "planner": "nemotron-3-ultra",
                            }})
                        progress_task = asyncio.create_task(planning_progress())

                        if image_b64:
                            tasks = await plan_from_image(image_b64, user_prompt=user_prompt)
                        else:
                            tasks = await plan_tasks(user_prompt)
                        progress_task.cancel()

                        if not tasks:
                            await bridge._send_to(websocket, {"type": "plan_error", "data": {
                                "error": "planner returned no tasks",
                            }})
                            continue

                        # Load the planned tasks into the bridge
                        result = bridge.load_tasks(tasks)
                        result["tasks"] = [t.to_dict() for t in bridge.tasks.values()]

                        log.info(f"Planned {len(tasks)} tasks into {result['total_layers']} layers")
                        await bridge._send_to(websocket, {"type": "plan_ready", "data": result})

                    except Exception as e:
                        log.error(f"Planning failed: {e}")
                        await bridge._send_to(websocket, {"type": "plan_error", "data": {
                            "error": str(e),
                        }})

                elif msg_type == "start":
                    await bridge.start_pipeline()
                    await bridge._send_to(websocket, {"type": "pipeline_started", "data": {
                        "total_tasks": len(bridge.tasks),
                        "total_layers": len(bridge.layers),
                    }})

                elif msg_type == "submit_task":
                    result = await bridge.submit_task(
                        req.get("task_id"),
                        req.get("agent_id"),
                        req.get("artifacts", {}),
                    )
                    await bridge._send_to(websocket, {
                        "type": "submission_result",
                        "data": result,
                    })

                elif msg_type == "task_progress":
                    # Agent is reporting progress — broadcast to all clients
                    await bridge._broadcast({"type": "task_progress", "data": {
                        "task_id": req.get("task_id"),
                        "agent_id": req.get("agent_id"),
                        "stage": req.get("stage", ""),
                        "detail": req.get("detail", ""),
                        "timestamp": time.time(),
                    }})

                elif msg_type == "register_agent":
                    agent_id = req.get("agent_id")
                    bridge.registry.register(
                        agent_id,
                        capabilities=set(req.get("capabilities", [])),
                    )
                    # Route this agent's tasks to this socket
                    bridge.agent_sockets[agent_id] = websocket
                    log.info(f"Agent registered: {agent_id} (routed to this socket)")
                    await bridge._send_to(websocket, {"type": "agent_registered", "data": {
                        "agent_id": agent_id,
                    }})

                    # Re-dispatch any tasks already assigned to this agent
                    # (tasks may have been dispatched before the agent connected)
                    for tid, task in bridge.tasks.items():
                        if task.agent_id == agent_id and task.status == TaskStatus.DISPATCHED:
                            log.info(f"Re-dispatching {tid} to newly registered {agent_id}")
                            await bridge._send_to(websocket, {"type": "task_dispatched", "data": {
                                "task_id": tid,
                                "agent_id": agent_id,
                                "task_name": task.name,
                                "layer": task.layer,
                                "description": task.description,
                                "verify_method": task.verify.method if task.verify else "none",
                                "verify_params": task.verify.params if task.verify else {},
                                "upstream_results": [
                                    {
                                        "task_id": dep_id,
                                        "summary": bridge.tasks[dep_id].output_summary[:1000],
                                    }
                                    for dep_id in task.dependencies
                                    if dep_id in bridge.tasks and bridge.tasks[dep_id].output_summary
                                ],
                            }})

                elif msg_type == "get_status":
                    await bridge._send_to(websocket, {
                        "type": "status",
                        "data": bridge.get_status(),
                    })

                elif msg_type == "run_benchmark":
                    # Run speed benchmark: Cerebras Gemma 4 vs GPU GLM-5.2
                    log.info("Starting speed benchmark...")
                    await bridge._broadcast({"type": "benchmark_running", "data": {
                        "message": "Running Cerebras Gemma 4 vs GPU GLM-5.2 benchmark...",
                    }})
                    try:
                        import importlib
                        bench = importlib.import_module("benchmark_speed")
                        # Run benchmark inline (it's async)
                        import aiohttp
                        from agent_client import load_api_keys, call_glm, call_gemma4, MODEL_CONFIG

                        keys = load_api_keys()
                        results = {"cerebras": {}, "glm": {}, "total_speedup": 0}

                        # Cerebras
                        c_result = await bench.benchmark_provider("gemma4", keys.get("gemma4", ""))
                        results["cerebras"] = c_result

                        # GLM
                        g_result = await bench.benchmark_provider("glm", keys.get("glm", ""))
                        results["glm"] = g_result

                        speedup = g_result["total_time_s"] / c_result["total_time_s"] if c_result["total_time_s"] > 0 else 0
                        results["total_speedup"] = speedup

                        log.info(f"Benchmark complete: {speedup:.1f}x speedup")
                        await bridge._broadcast({
                            "type": "benchmark_results",
                            "data": results,
                        })
                    except Exception as e:
                        log.error(f"Benchmark failed: {e}")
                        await bridge._send_to(websocket, {
                            "type": "benchmark_error",
                            "data": {"error": str(e)},
                        })

                elif msg_type == "run_comparison":
                    # Live side-by-side race: Cerebras vs GPU, simultaneously
                    prompt = req.get("prompt", "")
                    image_b64 = req.get("image", "")
                    log.info("Starting live comparison race...")
                    asyncio.create_task(bridge.run_comparison_race(prompt, websocket, image_b64=image_b64))

                elif msg_type == "ping":
                    await bridge._send_to(websocket, { "type": "pong" })

        except websockets.exceptions.ConnectionClosed:
            pass
        finally:
            bridge.clients.discard(websocket)
            # Clean up agent socket mapping
            to_remove = [aid for aid, ws in bridge.agent_sockets.items() if ws is websocket]
            for aid in to_remove:
                del bridge.agent_sockets[aid]
            log.info(f"Client disconnected (remaining: {len(bridge.clients)})")

    async with websockets.serve(
        handle_connection, "localhost", args.port,
        ping_interval=30,
        ping_timeout=120,
    ):
        log.info(f"Multi-Agent Orchestrator Bridge on ws://localhost:{args.port}")
        log.info(f"Solana: {'ENABLED' if bridge.use_solana else 'MOCK'}")
        log.info("Send 'load_tasks' to build DAG, then 'start' to begin.")
        await asyncio.Future()


if __name__ == "__main__":
    asyncio.run(main())
