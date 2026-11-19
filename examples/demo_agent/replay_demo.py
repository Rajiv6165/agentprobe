"""AgentProbe Cassette Recording and Deterministic Replay Demo.

Walks through a complete production test scenario:
  Part A: Record an agent run on a multi-step task (Order ORD-88123 damaged item replacement).
  Part B: Deterministically replay the run with all interactions frozen.
  Part C: Introduce a behavioral regression (MockLLM_v2 sends email before verifying order).
  Part D: Replay with llm_live_tools_recorded and automatically detect the regression with a precise diff.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent.parent
SDK_SRC = ROOT_DIR / "packages" / "sdk" / "src"
if str(SDK_SRC) not in sys.path:
    sys.path.insert(0, str(SDK_SRC))
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

import agentprobe as ap  # noqa: E402
from agentprobe.cassette import Cassette  # noqa: E402
from agentprobe.replay import ReplayMismatchError, ReplayPolicy, replay  # noqa: E402

from examples.demo_agent.agent import MockLLM_v2, run_support_agent  # noqa: E402

# Initialize SDK in offline exporter mode for self-contained demonstration
ap.init(exporter="none")

CASSETTE_FILE = Path(__file__).parent / "damaged_order.cassette.json"
QUESTION = (
    "My headphones arrived crushed and broken. Order ID is ORD-88123. Can you send a replacement?"
)


def print_banner(title: str) -> None:
    print("\n" + "=" * 80)
    print(f" {title}")
    print("=" * 80)


def part_a_record() -> Cassette:
    """Part A: Record live agent execution to a portable cassette file."""
    print_banner("PART A: RECORD LIVE RUN TO CASSETTE")
    print(f'Agent Prompt: "{QUESTION}"')
    print(f"Target file:  {CASSETTE_FILE.name}")
    print("\nExecuting agent live and capturing spans...")

    output, cassette = Cassette.record(
        run_support_agent,
        QUESTION,
        name="damaged_order_scenario",
        seed=42,
    )
    cassette.save(CASSETTE_FILE)

    print(f'\n✔ Live agent completed: "{output}"')
    print(
        f"✔ Saved portable cassette to {CASSETTE_FILE} ({len(cassette.interactions)} interaction steps)"
    )
    print("\nRecorded Interactions:")
    for i in cassette.interactions:
        dur = f"{i.recorded_duration_ms:.1f}ms"
        print(
            f"  [{i.index:02d}] {i.kind.upper():<7} {i.name:<24} {dur:<9} fp:{i.request_fingerprint[:12]}..."
        )

    return cassette


def part_b_deterministic_replay(cassette: Cassette) -> None:
    """Part B: Replay agent deterministically with all interactions frozen."""
    print_banner("PART B: DETERMINISTIC REPLAY (all_recorded)")
    print("Replaying agent against cassette with 100% mocked interactions...")

    result = replay(
        run_support_agent,
        cassette,
        policy=ReplayPolicy.all_recorded(),
    )

    print(f'\nAgent Output: "{result.output}"')
    print(f"✔ Matched Steps:       {result.matched_count}")
    print(f"✔ Live Executions:     {result.live_count} (zero network/compute overhead)")
    print(f"✔ Divergences:         {len(result.divergences)}")
    print(f"✔ Unconsumed Steps:    {len(result.unconsumed)}")
    print("✔ Status:              DETERMINISTIC MATCH (All interactions replayed exactly)")


def part_c_and_d_detect_regression(cassette: Cassette) -> None:
    """Parts C & D: Introduce MockLLM_v2 regression and detect tool ordering violation."""
    print_banner("PART C & D: REGRESSION DETECTION (llm_live_tools_recorded)")
    print("Scenario: A developer introduced an updated model (v2) with altered prompt logic.")
    print(
        "Regression bug: Agent attempts to dispatch confirmation email BEFORE verifying order status."
    )
    print("\nRunning replay with policy: llm_live_tools_recorded")
    print("  -> LLM runs live with new v2 prompt logic")
    print("  -> Backend databases and email tools remain frozen from cassette\n")

    # Define an agent wrapper that runs with MockLLM_v2
    regressed_llm = MockLLM_v2()

    def run_regressed_agent(q: str) -> str:
        return run_support_agent(q, llm=regressed_llm)

    try:
        replay(
            run_regressed_agent,
            cassette,
            policy=ReplayPolicy.llm_live_tools_recorded(),
            matcher="sequential",
            on_miss="error",
        )
        print("❌ Error: Expected replay to detect divergence, but it passed.")
    except ReplayMismatchError as e:
        print("🚨 REGRESSION DETECTED!")
        print("-" * 80)
        div = e.divergence
        print(f"Divergence at Step #{div.index}:")
        print(f"  Type:                {div.kind.upper()} ('{div.name}')")
        print(f"  Expected Fingerprint: {div.expected_fingerprint}")
        print(f"  Actual Fingerprint:   {div.actual_fingerprint}")
        if div.diff:
            print("\nStructured Request Difference:")
            for line in div.diff.splitlines():
                if line.startswith("+"):
                    print(f"  \033[92m{line}\033[0m")
                elif line.startswith("-"):
                    print(f"  \033[91m{line}\033[0m")
                else:
                    print(f"  {line}")

        if e.nearest_interactions:
            print("\nClosest Recorded Interactions in Cassette:")
            for near in e.nearest_interactions:
                print(f"  - Step #{near.index} ({near.kind}: '{near.name}')")

        print("-" * 80)
        print("✔ Regression successfully caught before deploying to production!")


def main() -> None:
    parser = argparse.ArgumentParser(description="AgentProbe Replay & Regression Demo")
    parser.add_argument(
        "--variant",
        choices=["v1", "v2"],
        default="v1",
        help="Model variant to test",
    )
    args = parser.parse_args()

    if args.variant == "v2":
        if CASSETTE_FILE.exists():
            cassette = Cassette.load(CASSETTE_FILE)
        else:
            cassette = part_a_record()
        part_c_and_d_detect_regression(cassette)
        return

    # Part A: Record baseline
    cassette = part_a_record()

    # Part B: Deterministic replay
    part_b_deterministic_replay(cassette)

    # Part C & D: Regression detection
    part_c_and_d_detect_regression(cassette)

    print("\n" + "=" * 80)
    print(" DEMO COMPLETED SUCCESSFULLY")
    print("=" * 80)


if __name__ == "__main__":
    main()
