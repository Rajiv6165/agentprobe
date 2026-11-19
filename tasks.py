#!/usr/bin/env python3
"""Cross-platform task runner for AgentProbe (stdlib only).

Usage:
    python tasks.py up           Start PostgreSQL and API in background
    python tasks.py down         Stop services
    python tasks.py test         Run test suites with coverage
    python tasks.py lint         Run ruff checks, format check, and mypy
    python tasks.py migrate      Run Alembic database migrations
    python tasks.py demo         Run customer support demo agent against API
    python tasks.py replay-demo  Run cassette replay regression demo
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent


def run_cmd(cmd: list[str], cwd: Path | None = None, env: dict[str, str] | None = None) -> int:
    """Execute a subprocess command, forwarding stdout/stderr."""
    work_dir = cwd or ROOT_DIR
    cmd_str = " ".join(cmd)
    print(f"\n--> [{work_dir.name}] {cmd_str}")
    cmd_env = os.environ.copy()
    if env:
        cmd_env.update(env)

    # Ensure repository packages and services are on PYTHONPATH
    sdk_src = str(ROOT_DIR / "packages" / "sdk" / "src")
    api_dir = str(ROOT_DIR / "services" / "api")
    current_pp = cmd_env.get("PYTHONPATH", "")
    paths = [str(ROOT_DIR), sdk_src, api_dir]
    if current_pp:
        paths.append(current_pp)
    cmd_env["PYTHONPATH"] = os.pathsep.join(paths)

    # Use python executable from current environment if sys.executable
    resolved_cmd = list(cmd)
    if resolved_cmd and resolved_cmd[0] == "python":
        resolved_cmd[0] = sys.executable

    res = subprocess.run(resolved_cmd, cwd=str(work_dir), env=cmd_env)
    if res.returncode != 0:
        print(f"Command failed with exit code {res.returncode}: {cmd_str}")
    return res.returncode


def get_docker_compose_cmd() -> list[str]:
    """Detect available docker compose binary."""
    if shutil.which("docker"):
        # Check if docker compose plugin works
        res = subprocess.run(
            ["docker", "compose", "version"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        if res.returncode == 0:
            return ["docker", "compose"]
    if shutil.which("docker-compose"):
        return ["docker-compose"]
    return ["docker", "compose"]


def task_up() -> int:
    """Start docker compose services in detached mode."""
    dc = get_docker_compose_cmd()
    return run_cmd([*dc, "up", "-d"])


def task_down() -> int:
    """Stop and remove docker compose containers."""
    dc = get_docker_compose_cmd()
    return run_cmd([*dc, "down"])


def task_test() -> int:
    """Run pytest suites for both SDK and API."""
    test_env = {
        "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1",
    }
    print("=== Running SDK Tests ===")
    sdk_code = run_cmd([
        "python", "-m", "pytest",
        "-p", "asyncio",
        "-p", "pytest_cov",
        "packages/sdk/tests",
        "--cov=agentprobe", "--cov-report=term-missing"
    ], env=test_env)
    if sdk_code != 0:
        return sdk_code

    print("\n=== Running API Tests ===")
    api_dir = ROOT_DIR / "services" / "api"
    api_code = run_cmd([
        "python", "-m", "pytest",
        "-p", "asyncio",
        "-p", "pytest_cov",
        "tests",
        "--cov=app", "--cov-report=term-missing"
    ], cwd=api_dir, env=test_env)
    return api_code


def task_lint() -> int:
    """Run ruff linter, ruff formatter check, mypy typechecker, and schema drift check."""
    print("=== Checking Ruff Lint ===")
    code = run_cmd(["python", "-m", "ruff", "check", "packages/sdk", "services/api", "examples"])
    if code != 0:
        return code

    print("\n=== Checking Ruff Formatting ===")
    code = run_cmd(["python", "-m", "ruff", "format", "--check", "packages/sdk", "services/api", "examples"])
    if code != 0:
        return code

    print("\n=== Checking Mypy Type Hints (Strict) ===")
    code = run_cmd(["python", "-m", "mypy", "packages/sdk/src/agentprobe"])
    if code != 0:
        return code

    print("\n=== Checking Cassette JSON Schema Drift ===")
    code = run_cmd([
        "python", "-c",
        (
            "import json, sys; sys.path.insert(0, 'packages/sdk/src'); "
            "from agentprobe.cassette.models import Cassette; "
            "disk_schema = json.loads(open('docs/cassette.schema.json', 'r', encoding='utf-8').read()); "
            "current_schema = Cassette.model_json_schema(); "
            "assert disk_schema == current_schema, 'docs/cassette.schema.json has drifted from Cassette.model_json_schema()'; "
            "print('✔ Cassette JSON schema matches model')"
        )
    ])
    return code


def task_migrate() -> int:
    """Run database migrations to head revision."""
    api_dir = ROOT_DIR / "services" / "api"
    return run_cmd(["python", "-m", "alembic", "upgrade", "head"], cwd=api_dir)


def task_demo() -> int:
    """Execute customer support demo agent."""
    return run_cmd(["python", "examples/demo_agent/main.py"])


def task_replay_demo() -> int:
    """Execute replay regression demo."""
    return run_cmd(["python", "examples/demo_agent/replay_demo.py"])


def main() -> None:
    parser = argparse.ArgumentParser(
        description="AgentProbe cross-platform task automation (stdlib only)."
    )
    parser.add_argument(
        "task",
        choices=["up", "down", "test", "lint", "migrate", "demo", "replay-demo"],
        help="Task to execute",
    )

    args = parser.parse_args()
    tasks_map = {
        "up": task_up,
        "down": task_down,
        "test": task_test,
        "lint": task_lint,
        "migrate": task_migrate,
        "demo": task_demo,
        "replay-demo": task_replay_demo,
    }

    exit_code = tasks_map[args.task]()
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
