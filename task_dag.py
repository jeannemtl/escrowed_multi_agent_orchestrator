#!/usr/bin/env python3
"""
Generalized Task DAG
====================
Accepts ANY kind of task with explicit dependencies.
Computes dependency layers for batch attestation.

This module takes dependencies as explicit declarations from the task creator.

Task types can be anything:
  - skill creation (artifact: SKILL.md)
  - package installation (artifact: pip show)
  - file creation (artifact: file exists)
  - test execution (artifact: exit code 0)
  - process launch (artifact: pgrep)
  - research/analysis (artifact: markdown file)
  - OS configuration (artifact: config file)
  - etc.

Each task declares:
  - id, name, agent_id, description
  - dependencies: list of task IDs this task needs completed first
  - verify: verification spec (type + params) for artifact checking
  - cost_weight: business criticality (0.3-3.0)
"""

import json
from dataclasses import dataclass, field
from typing import Optional
from enum import Enum


class TaskStatus(Enum):
    PENDING = "pending"          # Not yet dispatched
    DISPATCHED = "dispatched"    # Sent to an agent, waiting for completion
    SUBMITTED = "submitted"      # Agent claims done, awaiting verification
    VERIFIED = "verified"        # Artifacts verified, awaiting batch attestation
    ATTESTED = "attested"        # Solana batch tx confirmed for this layer
    CONFIRMED = "confirmed"      # Fully resolved, children promoted
    FAILED = "failed"            # Verification failed or agent error
    BLOCKED = "blocked"          # Blocked for human review


@dataclass
class VerifySpec:
    """How to verify that a task's artifacts are real."""
    method: str  # "file_exists" | "process_running" | "test_passes" | "skill_loads" | "package_installed" | "command_success" | "custom"
    params: dict = field(default_factory=dict)  # method-specific params

    def to_dict(self) -> dict:
        return {"method": self.method, "params": self.params}


@dataclass
class Task:
    id: str
    name: str
    agent_id: str                # which agent should handle this
    description: str
    dependencies: list[str] = field(default_factory=list)
    verify: Optional[VerifySpec] = None
    cost_weight: float = 1.0
    sla_ms: float = 30000        # expected completion time
    layer: int = 0
    status: TaskStatus = TaskStatus.PENDING
    submitted_artifacts: dict = field(default_factory=dict)  # what the agent claims it produced
    verification_result: Optional[dict] = None
    solana_signature: Optional[str] = None
    solana_latency_ms: float = 0.0
    cost_cents: int = 0
    submitted_at: float = 0.0
    verified_at: float = 0.0

    # Agent-assigned metadata for downstream consumption
    output_summary: str = ""
    output_metadata: dict = field(default_factory=dict)

    @property
    def downstream_count(self) -> int:
        """Set externally by DAG builder."""
        return getattr(self, '_downstream_count', 0)

    @downstream_count.setter
    def downstream_count(self, val: int):
        self._downstream_count = val

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "agent_id": self.agent_id,
            "description": self.description,
            "dependencies": self.dependencies,
            "verify": self.verify.to_dict() if self.verify else None,
            "cost_weight": self.cost_weight,
            "sla_ms": self.sla_ms,
            "layer": self.layer,
            "status": self.status.value,
            "submitted_artifacts": self.submitted_artifacts,
            "verification_result": self.verification_result,
            "solana_signature": self.solana_signature,
            "solana_latency_ms": self.solana_latency_ms,
            "cost_cents": self.cost_cents,
            "output_summary": self.output_summary,
            "output_metadata": self.output_metadata,
        }


def compute_downstream(tasks: dict[str, Task]) -> None:
    """Compute how many tasks depend on each task (direct + transitive)."""
    # Direct dependents
    direct_dependents: dict[str, list[str]] = {tid: [] for tid in tasks}
    for tid, task in tasks.items():
        for dep in task.dependencies:
            if dep in direct_dependents:
                direct_dependents[dep].append(tid)

    # Transitive closure via BFS
    for tid in tasks:
        visited = set()
        queue = list(direct_dependents.get(tid, []))
        while queue:
            child = queue.pop(0)
            if child in visited:
                continue
            visited.add(child)
            queue.extend(direct_dependents.get(child, []))
        tasks[tid].downstream_count = len(visited)


def get_dependency_depth(tasks: dict[str, Task], task_id: str, cache: dict = None) -> int:
    """Depth in the DAG: root tasks = 0, their dependents = 1, etc."""
    if cache is None:
        cache = {}
    if task_id in cache:
        return cache[task_id]
    task = tasks[task_id]
    if not task.dependencies:
        cache[task_id] = 0
        return 0
    depth = 1 + max(get_dependency_depth(tasks, d, cache) for d in task.dependencies if d in tasks)
    cache[task_id] = depth
    return depth


def compute_layers(tasks: dict[str, Task]) -> list[list[str]]:
    """
    Compute dependency layers via topological sort.
    Layer 0 = no dependencies, Layer 1 = deps only in Layer 0, etc.

    All tasks in a layer can be batch-attested as ONE Solana transaction.
    """
    layers: list[list[str]] = []
    assigned: set[str] = set()

    while len(assigned) < len(tasks):
        current_layer = []
        for tid, task in tasks.items():
            if tid in assigned:
                continue
            # Check all deps are assigned
            if all(d in assigned for d in task.dependencies if d in tasks):
                current_layer.append(tid)

        if not current_layer:
            # Circular dependency — force-assign remaining
            remaining = [tid for tid in tasks if tid not in assigned]
            current_layer = remaining

        for tid in current_layer:
            tasks[tid].layer = len(layers)
            assigned.add(tid)
        layers.append(current_layer)

    # Compute downstream counts now that DAG is built
    compute_downstream(tasks)

    return layers


def build_dag(tasks_raw: list[dict]) -> tuple[dict[str, Task], list[list[str]]]:
    """
    Build a DAG from raw task definitions.

    Each raw task dict:
    {
        "id": "install_playwright",
        "name": "Install Playwright + Chromium",
        "agent_id": "agent-2",
        "description": "pip install playwright && playwright install chromium",
        "dependencies": [],  # explicit task IDs
        "verify": {
            "method": "package_installed",
            "params": {"package": "playwright"}
        },
        "cost_weight": 0.5,
        "sla_ms": 60000
    }
    """
    tasks: dict[str, Task] = {}
    for raw in tasks_raw:
        tid = raw["id"]
        verify = None
        if raw.get("verify"):
            verify = VerifySpec(
                method=raw["verify"]["method"],
                params=raw["verify"].get("params", {}),
            )
        tasks[tid] = Task(
            id=tid,
            name=raw["name"],
            agent_id=raw.get("agent_id", "unassigned"),
            description=raw["description"],
            dependencies=raw.get("dependencies", []),
            verify=verify,
            cost_weight=raw.get("cost_weight", 1.0),
            sla_ms=raw.get("sla_ms", 30000),
        )

    layers = compute_layers(tasks)
    return tasks, layers


def get_layer_summary(tasks: dict[str, Task], layers: list[list[str]]) -> list[dict]:
    """Summary of each layer for display/logging."""
    summaries = []
    for i, layer in enumerate(layers):
        layer_tasks = [tasks[tid] for tid in layer]
        agents = list(set(t.agent_id for t in layer_tasks))
        summaries.append({
            "layer": i,
            "count": len(layer_tasks),
            "task_ids": layer,
            "agents": agents,
            "task_names": [t.name for t in layer_tasks],
            "total_downstream": sum(t.downstream_count for t in layer_tasks),
            "verify_methods": [t.verify.method if t.verify else "none" for t in layer_tasks],
        })
    return summaries


def get_ready_tasks(tasks: dict[str, Task]) -> list[str]:
    """Get tasks that can be dispatched now (all deps confirmed, status=pending)."""
    ready = []
    for tid, task in tasks.items():
        if task.status != TaskStatus.PENDING:
            continue
        if all(tasks[d].status == TaskStatus.CONFIRMED for d in task.dependencies if d in tasks):
            ready.append(tid)
    return ready


# ── Greedy scheduler ──────────────────────────────────────────────────

def score_task(tasks: dict[str, Task], task_id: str) -> float:
    """
    Score a task for dispatch priority.
    Higher = more important to dispatch first.

    Factors:
      - downstream_clearance: tasks unblocking more work get priority
      - cost_weight: business-critical tasks get priority
      - SLA: faster tasks get slight priority (quick wins)
      - depth: deeper tasks get priority (they're on the critical path)
    """
    task = tasks[task_id]
    depth = get_dependency_depth(tasks, task_id)
    downstream = task.downstream_count

    score = (
        downstream * 1000          # unblock the most work first
        + (task.cost_weight * 500) # business criticality
        - (task.sla_ms / 10)       # faster tasks slight edge
        + (depth * 50)             # deeper = more critical path
    )
    return score


def select_next_task(tasks: dict[str, Task]) -> Optional[str]:
    """Select the highest-scoring ready task."""
    ready = get_ready_tasks(tasks)
    if not ready:
        return None
    return max(ready, key=lambda tid: score_task(tasks, tid))


# ── Self-test ────────────────────────────────────────────────────────

if __name__ == "__main__":
    sample_tasks = [
        {
            "id": "install_playwright",
            "name": "Install Playwright + Chromium",
            "agent_id": "agent-2",
            "description": "pip install playwright && playwright install chromium",
            "dependencies": [],
            "verify": {"method": "package_installed", "params": {"package": "playwright"}},
            "cost_weight": 0.5,
        },
        {
            "id": "create_scraper_skill",
            "name": "Create skill: web-scraper-v2",
            "agent_id": "agent-1",
            "description": "Write SKILL.md for web-scraper-v2 skill",
            "dependencies": [],
            "verify": {"method": "skill_loads", "params": {"skill_name": "web-scraper-v2"}},
            "cost_weight": 1.0,
        },
        {
            "id": "write_cleaner",
            "name": "Write data cleaning script",
            "agent_id": "agent-4",
            "description": "Write clean.py for pipeline",
            "dependencies": [],
            "verify": {"method": "file_exists", "params": {"path": "/tmp/pipeline/scripts/clean.py"}},
            "cost_weight": 0.8,
        },
        {
            "id": "wire_pipeline",
            "name": "Wire scraper → cleaner → output",
            "agent_id": "agent-9",
            "description": "Connect components into pipeline.yaml",
            "dependencies": ["create_scraper_skill", "install_playwright", "write_cleaner"],
            "verify": {"method": "file_exists", "params": {"path": "/tmp/pipeline/config/pipeline.yaml"}},
            "cost_weight": 2.0,
        },
        {
            "id": "setup_cron",
            "name": "Set up cron schedule",
            "agent_id": "agent-5",
            "description": "Create crontab entry for daily pipeline run",
            "dependencies": [],
            "verify": {"method": "command_success", "params": {"command": "crontab -l | grep pipeline"}},
            "cost_weight": 0.3,
        },
        {
            "id": "configure_cron",
            "name": "Configure cron to call the pipeline",
            "agent_id": "agent-3",
            "description": "Point cron at the wired pipeline",
            "dependencies": ["setup_cron", "wire_pipeline"],
            "verify": {"method": "file_exists", "params": {"path": "/tmp/pipeline/config/cron_entry.sh"}},
            "cost_weight": 1.5,
        },
        {
            "id": "e2e_test",
            "name": "End-to-end test run + report",
            "agent_id": "agent-10",
            "description": "Run the full pipeline and generate report",
            "dependencies": ["configure_cron"],
            "verify": {"method": "test_passes", "params": {"test_path": "/tmp/pipeline/tests/test_e2e.py"}},
            "cost_weight": 3.0,
        },
    ]

    tasks, layers = build_dag(sample_tasks)
    summaries = get_layer_summary(tasks, layers)

    print(f"=== Multi-Agent Task DAG: {len(tasks)} tasks, {len(layers)} layers ===\n")
    for s in summaries:
        print(f"Layer {s['layer']}: {s['count']} tasks | agents={s['agents']} | "
              f"downstream={s['total_downstream']}")
        for tid in s['task_ids']:
            t = tasks[tid]
            deps_str = f" deps={t.dependencies}" if t.dependencies else ""
            verify_str = f" verify={t.verify.method}" if t.verify else ""
            print(f"  {tid}: {t.name} [{t.agent_id}]{deps_str}{verify_str}")
        print()

    print(f"Layers (Solana batch attestations needed): {len(layers)}")
    print(f"vs individual attestations: {len(tasks)}")
    print(f"Savings: {len(tasks) - len(layers)} fewer confirmations")

    print("\n=== Greedy Scheduler — Ready Tasks ===")
    # Simulate: mark all layer-0 tasks as confirmed, check what becomes ready
    for tid in layers[0]:
        tasks[tid].status = TaskStatus.CONFIRMED
    ready = get_ready_tasks(tasks)
    for tid in ready:
        print(f"  {tid}: score={score_task(tasks, tid):.1f} (downstream={tasks[tid].downstream_count})")
