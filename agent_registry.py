#!/usr/bin/env python3
"""
Agent Registry
==============
Tracks the 10 agents: their capabilities, current load, and task history.

Agents are identified by string IDs (e.g. "agent-1" through "agent-10").
Each agent has:
  - capabilities: set of task types it can handle (e.g. "skill_creation", "terminal", "research")
  - current_load: how many tasks it's currently working on
  - max_concurrent: max parallel tasks (usually 1 for Hermes subagents)
  - task_history: list of completed task IDs
  - status: "idle", "busy", "offline"

The registry is used by the bridge to:
  - Track which agents are available for dispatch
  - Reassign tasks when agents fail
  - Report per-agent stats to the dashboard
"""

import time
import json
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


class AgentStatus(Enum):
    IDLE = "idle"
    BUSY = "busy"
    OFFLINE = "offline"


@dataclass
class Agent:
    id: str
    name: str = ""
    capabilities: set = field(default_factory=set)
    max_concurrent: int = 1
    status: AgentStatus = AgentStatus.IDLE
    current_tasks: list[str] = field(default_factory=list)
    completed_tasks: list[str] = field(default_factory=list)
    failed_tasks: list[str] = field(default_factory=list)
    last_seen: float = 0.0

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name or self.id,
            "capabilities": sorted(self.capabilities),
            "max_concurrent": self.max_concurrent,
            "status": self.status.value,
            "current_tasks": self.current_tasks,
            "completed_count": len(self.completed_tasks),
            "failed_count": len(self.failed_tasks),
            "last_seen": self.last_seen,
        }


class AgentRegistry:
    """
    Registry of all agents available for task dispatch.

    Agents can be:
      - Pre-registered (at startup) with known capabilities
      - Discovered dynamically (agents announce themselves)
      - Hermes profiles (via `hermes profile list`)
      - delegate_task subagents (spawned on demand)
    """

    def __init__(self):
        self.agents: dict[str, Agent] = {}

    def register(
        self,
        agent_id: str,
        name: str = "",
        capabilities: set = None,
        max_concurrent: int = 1,
    ) -> Agent:
        """Register a new agent or update an existing one."""
        agent = Agent(
            id=agent_id,
            name=name or agent_id,
            capabilities=capabilities or set(),
            max_concurrent=max_concurrent,
        )
        self.agents[agent_id] = agent
        return agent

    def register_from_hermes_profiles(self) -> int:
        """
        Discover agents from `hermes profile list`.
        Each Hermes profile becomes an agent.
        """
        import subprocess
        try:
            result = subprocess.run(
                ["hermes", "profile", "list"],
                capture_output=True, text=True, timeout=10,
            )
            if result.returncode != 0:
                return 0

            count = 0
            for line in result.stdout.strip().split("\n"):
                line = line.strip()
                if not line or line.startswith("Name") or line.startswith("-"):
                    continue
                # Parse profile name from table output
                parts = line.split()
                if parts:
                    profile_name = parts[0]
                    self.register(f"profile:{profile_name}", name=profile_name)
                    count += 1
            return count
        except Exception:
            return 0

    def get(self, agent_id: str) -> Optional[Agent]:
        return self.agents.get(agent_id)

    def get_available(self, capability: str = None) -> list[Agent]:
        """Get idle agents, optionally filtered by capability."""
        available = []
        for agent in self.agents.values():
            if agent.status != AgentStatus.IDLE:
                continue
            if len(agent.current_tasks) >= agent.max_concurrent:
                continue
            if capability and capability not in agent.capabilities:
                continue
            available.append(agent)
        return available

    def assign_task(self, agent_id: str, task_id: str) -> bool:
        """Mark a task as assigned to an agent."""
        agent = self.agents.get(agent_id)
        if agent is None:
            return False
        if len(agent.current_tasks) >= agent.max_concurrent:
            return False
        agent.current_tasks.append(task_id)
        agent.status = AgentStatus.BUSY
        agent.last_seen = time.time()
        return True

    def complete_task(self, agent_id: str, task_id: str, success: bool = True):
        """Mark a task as completed (or failed) for an agent."""
        agent = self.agents.get(agent_id)
        if agent is None:
            return
        if task_id in agent.current_tasks:
            agent.current_tasks.remove(task_id)
        if success:
            agent.completed_tasks.append(task_id)
        else:
            agent.failed_tasks.append(task_id)
        if not agent.current_tasks:
            agent.status = AgentStatus.IDLE
        agent.last_seen = time.time()

    def reassign(self, task_id: str, from_agent: str, to_agent: str) -> bool:
        """Move a task from one agent to another."""
        self.complete_task(from_agent, task_id, success=False)
        return self.assign_task(to_agent, task_id)

    def get_load_summary(self) -> dict:
        """Per-agent load summary for dashboard."""
        return {
            "total_agents": len(self.agents),
            "idle": sum(1 for a in self.agents.values() if a.status == AgentStatus.IDLE),
            "busy": sum(1 for a in self.agents.values() if a.status == AgentStatus.BUSY),
            "offline": sum(1 for a in self.agents.values() if a.status == AgentStatus.OFFLINE),
            "agents": {aid: a.to_dict() for aid, a in self.agents.items()},
        }

    def to_dict(self) -> dict:
        return {
            "agents": {aid: a.to_dict() for aid, a in self.agents.items()},
            "load_summary": self.get_load_summary(),
        }


# ── Self-test ────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("=== Agent Registry Self-Test ===\n")

    reg = AgentRegistry()

    # Register 10 agents with different capabilities
    caps = {
        "agent-1": {"skill_creation", "terminal"},
        "agent-2": {"package_install", "terminal"},
        "agent-3": {"file_creation", "terminal"},
        "agent-4": {"script_writing", "terminal"},
        "agent-5": {"cron_setup", "terminal"},
        "agent-6": {"research", "web"},
        "agent-7": {"api_integration", "terminal"},
        "agent-8": {"testing", "terminal"},
        "agent-9": {"wiring", "integration", "terminal"},
        "agent-10": {"testing", "reporting", "terminal"},
    }

    for aid, capabilities in caps.items():
        reg.register(aid, name=f"Agent {aid.split('-')[1]}", capabilities=capabilities)

    print(f"Registered {len(reg.agents)} agents")
    print(f"Load: {reg.get_load_summary()}\n")

    # Assign some tasks
    reg.assign_task("agent-1", "create_scraper_skill")
    reg.assign_task("agent-2", "install_playwright")
    reg.assign_task("agent-4", "write_cleaner")
    reg.assign_task("agent-5", "setup_cron")

    print("After assigning 4 tasks:")
    print(f"  Idle: {reg.get_load_summary()['idle']}")
    print(f"  Busy: {reg.get_load_summary()['busy']}")

    # Complete some
    reg.complete_task("agent-1", "create_scraper_skill", success=True)
    reg.complete_task("agent-2", "install_playwright", success=True)

    print("\nAfter completing 2 tasks:")
    print(f"  Idle: {reg.get_load_summary()['idle']}")
    print(f"  Busy: {reg.get_load_summary()['busy']}")

    # Find available agents with terminal capability
    available = reg.get_available(capability="terminal")
    print(f"\nAvailable agents with 'terminal' cap: {[a.id for a in available]}")

    # Reassign a failed task
    print(f"\nReassigning 'setup_cron' from agent-5 to agent-3...")
    reg.reassign("setup_cron", "agent-5", "agent-3")
    print(f"  agent-5 status: {reg.get('agent-5').status.value}")
    print(f"  agent-3 status: {reg.get('agent-3').status.value}")

    print(f"\nFull registry:\n{json.dumps(reg.get_load_summary(), indent=2)}")
