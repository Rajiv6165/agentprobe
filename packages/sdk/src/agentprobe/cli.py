"""AgentProbe command line interface using standard library argparse."""

from __future__ import annotations

import argparse
import asyncio
import importlib
import inspect
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, NoReturn

from agentprobe.cassette.models import Cassette
from agentprobe.replay.engine import areplay, replay
from agentprobe.replay.policy import ReplayPolicy
from agentprobe.replay.result import ReplayMismatchError


class AnsiColor:
    """Terminal styling utilities supporting standard ANSI escape codes."""

    def __init__(self, enabled: bool) -> None:
        self.enabled = enabled

    def green(self, text: str) -> str:
        return f"\033[92m{text}\033[0m" if self.enabled else text

    def red(self, text: str) -> str:
        return f"\033[91m{text}\033[0m" if self.enabled else text

    def yellow(self, text: str) -> str:
        return f"\033[93m{text}\033[0m" if self.enabled else text

    def blue(self, text: str) -> str:
        return f"\033[94m{text}\033[0m" if self.enabled else text

    def cyan(self, text: str) -> str:
        return f"\033[96m{text}\033[0m" if self.enabled else text

    def bold(self, text: str) -> str:
        return f"\033[1m{text}\033[0m" if self.enabled else text

    def dim(self, text: str) -> str:
        return f"\033[2m{text}\033[0m" if self.enabled else text


def _get_color() -> AnsiColor:
    return AnsiColor(enabled=sys.stdout.isatty())


def _error_exit(msg: str, code: int = 2) -> NoReturn:
    color = _get_color()
    sys.stderr.write(f"{color.red('error:')} {msg}\n")
    sys.exit(code)


def handle_cassette_pull(args: argparse.Namespace) -> int:
    """Pull recorded cassette from remote API server and save to disk."""
    color = _get_color()
    run_id = args.run_id
    endpoint = args.endpoint or os.getenv("AGENTPROBE_ENDPOINT", "http://localhost:8000")
    api_key = args.api_key or os.getenv("AGENTPROBE_API_KEY")

    endpoint = endpoint.rstrip("/")
    url = f"{endpoint}/v1/runs/{run_id}/cassette"

    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
    }
    if api_key:
        headers["X-AgentProbe-Key"] = api_key

    req = urllib.request.Request(url, data=b"{}", headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req) as resp:
            raw_data = resp.read()
            data = json.loads(raw_data.decode("utf-8"))
    except urllib.error.HTTPError as e:
        err_msg = e.read().decode("utf-8", errors="replace")
        try:
            parsed = json.loads(err_msg)
            msg = parsed.get("error", {}).get("message", err_msg)
        except Exception:
            msg = err_msg
        _error_exit(f"Failed to pull cassette (HTTP {e.code}): {msg}")
    except Exception as e:
        _error_exit(f"Failed to connect to {endpoint}: {e}")

    content = data.get("content", data)
    output_path = Path(args.output or f"{run_id}.cassette.json")
    output_path.parent.mkdir(parents=True, exist_ok=True)

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(content, f, indent=2)

    print(f"{color.green('✔')} Cassette successfully saved to {color.bold(str(output_path))}")
    print(f"  {color.dim('Name:')} {content.get('name')}")
    print(
        f"  {color.dim('Agent:')} {content.get('agent_name')} (v{content.get('agent_version', 'latest')})"
    )
    print(f"  {color.dim('Interactions:')} {len(content.get('interactions', []))}")
    return 0


def handle_cassette_inspect(args: argparse.Namespace) -> int:
    """Inspect and format human-readable summary of a local cassette file."""
    color = _get_color()
    file_path = Path(args.file)
    if not file_path.exists():
        _error_exit(f"Cassette file not found: {file_path}")

    try:
        cassette = Cassette.load(file_path)
    except Exception as e:
        _error_exit(f"Failed to parse cassette file: {e}")

    llm_count = sum(1 for i in cassette.interactions if i.kind == "llm")
    tool_count = sum(1 for i in cassette.interactions if i.kind == "tool")
    capture_count = sum(1 for i in cassette.interactions if i.kind == "capture")
    tools_used = sorted({i.name for i in cassette.interactions if i.kind == "tool"})

    total_tokens = 0
    for i in cassette.interactions:
        usage = i.metadata.get("usage", {})
        if isinstance(usage, dict):
            total_tokens += usage.get("total_tokens", 0)

    any_redacted = any(i.redacted for i in cassette.interactions)

    print(f"{color.bold('Cassette Details:')}")
    print(f"  {color.dim('File:')}          {file_path.name}")
    print(f"  {color.dim('Name:')}          {cassette.name}")
    print(f"  {color.dim('Schema:')}        v{cassette.schema_version}")
    print(f"  {color.dim('Created:')}       {cassette.created_at}")
    print(f"  {color.dim('Source Run:')}    {cassette.source_run_id or 'none'}")
    print(
        f"  {color.dim('Agent:')}         {cassette.agent_name} (v{cassette.agent_version or 'unknown'})"
    )
    print(
        f"  {color.dim('Total Steps:')}   {len(cassette.interactions)} (LLM: {llm_count}, Tool: {tool_count}, Capture: {capture_count})"
    )
    print(f"  {color.dim('Tools Used:')}    {', '.join(tools_used) if tools_used else 'none'}")
    print(f"  {color.dim('Total Tokens:')}  {total_tokens}")
    print(
        f"  {color.dim('Redactions:')}    {color.yellow('Yes') if any_redacted else color.green('No')}"
    )
    print()

    print(f"{color.bold('Interaction Steps:')}")
    for i in cassette.interactions:
        kind_color = (
            color.cyan if i.kind == "llm" else (color.blue if i.kind == "tool" else color.yellow)
        )
        redact_str = f" {color.yellow('[REDACTED]')}" if i.redacted else ""
        fp_short = i.request_fingerprint[:12] if i.request_fingerprint else "no-fp"
        print(
            f"  [{i.index:02d}] {kind_color(i.kind.upper()):<7} "
            f"{color.bold(i.name):<22} "
            f"{color.dim(f'{i.recorded_duration_ms:.1f}ms'):<9} "
            f"{color.dim(fp_short)}{redact_str}"
        )

    return 0


def handle_replay(args: argparse.Namespace) -> int:
    """Replay an instrumented agent against a cassette."""
    color = _get_color()
    target: str = args.target
    if ":" not in target:
        _error_exit("Target must be in 'module:function' format (e.g. 'my_agent:run_agent')")

    module_name, func_name = target.split(":", 1)

    # Ensure current working directory is on sys.path
    cwd = os.getcwd()
    if cwd not in sys.path:
        sys.path.insert(0, cwd)

    try:
        mod = importlib.import_module(module_name)
    except Exception as e:
        _error_exit(f"Could not import module '{module_name}': {e}")

    try:
        fn = getattr(mod, func_name)
    except AttributeError:
        _error_exit(f"Module '{module_name}' has no attribute '{func_name}'")

    if not callable(fn):
        _error_exit(f"'{target}' is not callable")

    cassette_path = Path(args.cassette)
    if not cassette_path.exists():
        _error_exit(f"Cassette file not found: {cassette_path}")

    try:
        cas = Cassette.load(cassette_path)
    except Exception as e:
        _error_exit(f"Failed to load cassette: {e}")

    # Resolve policy
    policy_str: str = args.policy
    if policy_str == "all_recorded":
        pol = ReplayPolicy.all_recorded()
    elif policy_str == "llm_live_tools_recorded":
        pol = ReplayPolicy.llm_live_tools_recorded()
    elif policy_str == "tools_live_llm_recorded":
        pol = ReplayPolicy.tools_live_llm_recorded()
    else:
        _error_exit(f"Unknown replay policy '{policy_str}'")

    on_miss: Any = args.on_miss
    if on_miss not in ("error", "live", "skip"):
        _error_exit(f"Invalid --on-miss option '{on_miss}'")

    print(
        f"{color.bold('Starting Replay:')} {color.cyan(target)} "
        f"with cassette {color.bold(cassette_path.name)} (policy: {policy_str})"
    )

    matcher_type = getattr(args, "matcher", "fingerprint")

    try:
        if inspect.iscoroutinefunction(fn):
            result = asyncio.run(
                areplay(
                    fn,
                    cas,
                    policy=pol,
                    on_miss=on_miss,
                    matcher=matcher_type,
                )
            )
        else:
            result = replay(
                fn,
                cas,
                policy=pol,
                on_miss=on_miss,
                matcher=matcher_type,
            )
    except ReplayMismatchError as e:
        print()
        print(f"{color.red(color.bold('Divergence Report:'))}")
        div = e.divergence
        print(f"  • Step {div.index} [{div.kind} '{div.name}'] (resolution: {div.resolution})")
        if div.diff:
            print(f"    {color.dim('Diff:')}")
            for line in div.diff.splitlines():
                if line.startswith("+"):
                    print(f"      {color.green(line)}")
                elif line.startswith("-"):
                    print(f"      {color.red(line)}")
                else:
                    print(f"      {color.dim(line)}")
        print()
        print(f"{color.red('FAILED')}: Replay halted with divergence: {e}")
        return 1
    except Exception as e:
        _error_exit(f"Replay execution failed with error: {e}", code=1)

    print()
    print(f"{color.bold('Replay Results:')}")
    print(f"  {color.dim('Replayed Run ID:')}  {result.new_run_id}")
    print(f"  {color.dim('Matched Steps:')}    {color.green(str(result.matched_count))}")
    print(f"  {color.dim('Live Executions:')}  {result.live_count}")
    print(f"  {color.dim('Mocked Steps:')}     {result.mocked_count}")
    print(
        f"  {color.dim('Divergences:')}      {color.red(str(len(result.divergences))) if result.divergences else color.green('0')}"
    )
    print(
        f"  {color.dim('Unconsumed Steps:')} {color.yellow(str(len(result.unconsumed))) if result.unconsumed else '0'}"
    )
    print()

    if result.divergences:
        print(f"{color.red(color.bold('Divergence Report:'))}")
        for div in result.divergences:
            print(f"  • Step {div.index} [{div.kind} '{div.name}'] (resolution: {div.resolution})")
            if div.diff:
                print(f"    {color.dim('Diff:')}")
                for line in div.diff.splitlines():
                    if line.startswith("+"):
                        print(f"      {color.green(line)}")
                    elif line.startswith("-"):
                        print(f"      {color.red(line)}")
                    else:
                        print(f"      {color.dim(line)}")
        print()
        print(
            f"{color.red('FAILED')}: Replay finished with {len(result.divergences)} divergence(s)."
        )
        return 1

    print(f"{color.green('SUCCESS')}: Replay finished deterministically with 0 divergences.")
    return 0


def build_parser() -> argparse.ArgumentParser:
    """Construct command-line arguments parser."""
    parser = argparse.ArgumentParser(
        prog="agentprobe",
        description="AgentProbe CLI: Portable cassette management and deterministic agent replay",
    )
    subparsers = parser.add_subparsers(dest="subcommand", required=True)

    # agentprobe cassette
    cassette_parser = subparsers.add_parser("cassette", help="Cassette operations")
    cassette_sub = cassette_parser.add_subparsers(dest="cassette_action", required=True)

    # agentprobe cassette pull <run_id>
    pull_parser = cassette_sub.add_parser("pull", help="Pull a recorded cassette from remote API")
    pull_parser.add_argument("run_id", help="UUID of the recorded execution run")
    pull_parser.add_argument("-o", "--output", help="Output file path (.cassette.json)")
    pull_parser.add_argument("--endpoint", help="AgentProbe API endpoint URL")
    pull_parser.add_argument("--api-key", help="AgentProbe API authentication key")

    # agentprobe cassette inspect <file>
    inspect_parser = cassette_sub.add_parser("inspect", help="Inspect contents of a local cassette")
    inspect_parser.add_argument("file", help="Path to .cassette.json file")

    # agentprobe replay <target> --cassette <file>
    replay_parser = subparsers.add_parser("replay", help="Replay agent against a recorded cassette")
    replay_parser.add_argument("target", help="Agent entrypoint in 'module:function' format")
    replay_parser.add_argument(
        "-c", "--cassette", required=True, help="Path to .cassette.json file"
    )
    replay_parser.add_argument(
        "--policy",
        choices=["all_recorded", "llm_live_tools_recorded", "tools_live_llm_recorded"],
        default="all_recorded",
        help="Replay policy (default: all_recorded)",
    )
    replay_parser.add_argument(
        "--on-miss",
        choices=["error", "live", "skip"],
        default="error",
        help="Behavior on unexpected interaction (default: error)",
    )
    replay_parser.add_argument(
        "--matcher",
        choices=["fingerprint", "sequential"],
        default="fingerprint",
        help="Matching strategy: 'fingerprint' for content hashing (default), or 'sequential' for strict index-based ordering",
    )

    return parser


def main() -> None:
    """Entry point for the agentprobe console script."""
    parser = build_parser()
    args = parser.parse_args()

    if args.subcommand == "cassette":
        if args.cassette_action == "pull":
            code = handle_cassette_pull(args)
            sys.exit(code)
        elif args.cassette_action == "inspect":
            code = handle_cassette_inspect(args)
            sys.exit(code)
    elif args.subcommand == "replay":
        code = handle_replay(args)
        sys.exit(code)

    sys.exit(0)


if __name__ == "__main__":
    main()
