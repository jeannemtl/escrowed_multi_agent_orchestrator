#!/usr/bin/env python3
"""
Artifact Verifier
=================
Verifies that agents actually produced real artifacts before their tasks
are included in a batch Solana attestation.

An agent's claim of "I'm done" is NOT trusted — the controller checks:
  - file_exists: specific file path exists
  - process_running: a process matching a pattern is running
  - test_passes: a test command exits 0
  - skill_loads: a Hermes skill directory has a valid SKILL.md
  - package_installed: pip/npm shows the package
  - command_success: an arbitrary shell command exits 0
  - custom: caller provides a verification function (not serializable, for local use)

If verification fails, the task is NOT included in the layer batch — it goes
to FAILED status and can be reassigned to another agent.
"""

import os
import subprocess
import json
from typing import Callable
from task_dag import Task, VerifySpec


def verify_file_exists(params: dict) -> dict:
    """Check that a file exists at the given path."""
    path = os.path.expanduser(params.get("path", ""))
    exists = os.path.exists(path)
    result = {"ok": exists, "method": "file_exists", "path": path}
    if exists:
        stat = os.stat(path)
        result["size_bytes"] = stat.st_size
        result["mtime"] = stat.st_mtime
        # Check it's not empty (unless explicitly allowed)
        if stat.st_size == 0 and not params.get("allow_empty", False):
            result["ok"] = False
            result["error"] = "file exists but is empty"
    else:
        result["error"] = "file does not exist"
    return result


def verify_process_running(params: dict) -> dict:
    """Check that a process matching the pattern is running."""
    pattern = params.get("pattern", "")
    try:
        result = subprocess.run(
            ["pgrep", "-f", pattern],
            capture_output=True,
            text=True,
            timeout=5,
        )
        pids = result.stdout.strip().split("\n") if result.stdout.strip() else []
        ok = len(pids) > 0 and result.returncode == 0
        return {
            "ok": ok,
            "method": "process_running",
            "pattern": pattern,
            "pids": pids[:5],  # cap for display
            "pid_count": len(pids),
        }
    except Exception as e:
        return {"ok": False, "method": "process_running", "error": str(e)}


def verify_test_passes(params: dict) -> dict:
    """Run a test command and check exit code 0."""
    test_path = params.get("test_path", "")
    command = params.get("command", f"python3 -m pytest {test_path} -v")
    timeout = params.get("timeout", 120)

    try:
        result = subprocess.run(
            command,
            shell=True,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        return {
            "ok": result.returncode == 0,
            "method": "test_passes",
            "command": command,
            "exit_code": result.returncode,
            "stdout_tail": result.stdout[-500:] if result.stdout else "",
            "stderr_tail": result.stderr[-500:] if result.stderr else "",
        }
    except subprocess.TimeoutExpired:
        return {"ok": False, "method": "test_passes", "error": f"timed out after {timeout}s"}
    except Exception as e:
        return {"ok": False, "method": "test_passes", "error": str(e)}


def verify_skill_loads(params: dict) -> dict:
    """Check that a Hermes skill exists with a valid SKILL.md."""
    skill_name = params.get("skill_name", "")
    skill_path = os.path.expanduser(f"~/.hermes/skills/{skill_name}/SKILL.md")
    exists = os.path.exists(skill_path)
    result = {"ok": exists, "method": "skill_loads", "skill_name": skill_name, "path": skill_path}
    if exists:
        # Check it has frontmatter (--- ... ---)
        with open(skill_path) as f:
            content = f.read()
        has_frontmatter = content.strip().startswith("---")
        result["has_frontmatter"] = has_frontmatter
        result["size_bytes"] = len(content)
        if not has_frontmatter:
            result["ok"] = False
            result["error"] = "SKILL.md exists but missing YAML frontmatter"
    else:
        result["error"] = "SKILL.md not found"
    return result


def verify_package_installed(params: dict) -> dict:
    """Check that a package is installed via pip or npm."""
    package = params.get("package", "")
    manager = params.get("manager", "pip")

    if manager == "pip":
        command = f"pip show {package}"
    elif manager == "npm":
        command = f"npm list -g {package}"
    elif manager == "brew":
        command = f"brew list {package}"
    else:
        return {"ok": False, "method": "package_installed", "error": f"unknown manager: {manager}"}

    try:
        result = subprocess.run(
            command,
            shell=True,
            capture_output=True,
            text=True,
            timeout=10,
        )
        return {
            "ok": result.returncode == 0,
            "method": "package_installed",
            "package": package,
            "manager": manager,
            "exit_code": result.returncode,
        }
    except Exception as e:
        return {"ok": False, "method": "package_installed", "error": str(e)}


def verify_command_success(params: dict) -> dict:
    """Run an arbitrary command and check exit code 0."""
    command = params.get("command", "")
    timeout = params.get("timeout", 30)

    try:
        result = subprocess.run(
            command,
            shell=True,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        return {
            "ok": result.returncode == 0,
            "method": "command_success",
            "command": command,
            "exit_code": result.returncode,
            "stdout_tail": result.stdout[-300:] if result.stdout else "",
            "stderr_tail": result.stderr[-300:] if result.stderr else "",
        }
    except subprocess.TimeoutExpired:
        return {"ok": False, "method": "command_success", "error": f"timed out after {timeout}s"}
    except Exception as e:
        return {"ok": False, "method": "command_success", "error": str(e)}


# ── Dispatch ─────────────────────────────────────────────────────────

VERIFIERS: dict[str, Callable] = {
    "file_exists": verify_file_exists,
    "process_running": verify_process_running,
    "test_passes": verify_test_passes,
    "skill_loads": verify_skill_loads,
    "package_installed": verify_package_installed,
    "command_success": verify_command_success,
}


def verify_task(task: Task) -> dict:
    """
    Verify a task's artifacts using its VerifySpec.

    If the task has no verify spec, trust the agent's submission
    (with a warning). This allows research/analysis tasks that
    produce qualitative output.
    """
    if task.verify is None:
        return {
            "ok": True,
            "method": "none",
            "warning": "no verification spec — trusting agent submission",
        }

    verifier = VERIFIERS.get(task.verify.method)
    if verifier is None:
        return {
            "ok": False,
            "method": task.verify.method,
            "error": f"unknown verification method: {task.verify.method}",
        }

    return verifier(task.verify.params)


def verify_batch(tasks: list[Task]) -> dict:
    """
    Verify all tasks in a batch.
    Returns summary with per-task results and overall pass/fail.
    """
    results = {}
    passed = []
    failed = []

    for task in tasks:
        result = verify_task(task)
        results[task.id] = result
        if result["ok"]:
            passed.append(task.id)
        else:
            failed.append(task.id)

    return {
        "total": len(tasks),
        "passed": len(passed),
        "failed": len(failed),
        "passed_ids": passed,
        "failed_ids": failed,
        "results": results,
        "ok": len(failed) == 0,
    }


# ── Self-test ────────────────────────────────────────────────────────

if __name__ == "__main__":
    from task_dag import Task, VerifySpec

    print("=== Verifier Self-Test ===\n")

    # Test 1: file_exists (should pass — this file exists)
    t1 = Task(
        id="test_file",
        name="Test file exists",
        agent_id="test",
        description="verify this file",
        verify=VerifySpec(method="file_exists", params={"path": __file__}),
    )
    r1 = verify_task(t1)
    print(f"file_exists (self): {r1}")

    # Test 2: file_exists (should fail — bogus path)
    t2 = Task(
        id="test_missing",
        name="Test missing file",
        agent_id="test",
        description="verify nonexistent file",
        verify=VerifySpec(method="file_exists", params={"path": "/tmp/nonexistent_xyz_12345.py"}),
    )
    r2 = verify_task(t2)
    print(f"file_exists (missing): {r2}")

    # Test 3: package_installed (should pass — websockets is installed)
    t3 = Task(
        id="test_pkg",
        name="Test package",
        agent_id="test",
        description="verify websockets installed",
        verify=VerifySpec(method="package_installed", params={"package": "websockets"}),
    )
    r3 = verify_task(t3)
    print(f"package_installed (websockets): {r3}")

    # Test 4: command_success
    t4 = Task(
        id="test_cmd",
        name="Test command",
        agent_id="test",
        description="verify echo works",
        verify=VerifySpec(method="command_success", params={"command": "echo hello"}),
    )
    r4 = verify_task(t4)
    print(f"command_success (echo): {r4}")

    # Test 5: no verify spec (should trust with warning)
    t5 = Task(
        id="test_noverify",
        name="Test no verify",
        agent_id="test",
        description="research task with no artifact",
    )
    r5 = verify_task(t5)
    print(f"no_verify: {r5}")

    # Test 6: batch verification
    print("\n=== Batch Verification ===")
    batch_result = verify_batch([t1, t2, t3, t4, t5])
    print(f"Total: {batch_result['total']}, Passed: {batch_result['passed']}, Failed: {batch_result['failed']}")
    print(f"Failed IDs: {batch_result['failed_ids']}")
    print(f"Overall ok: {batch_result['ok']}")
