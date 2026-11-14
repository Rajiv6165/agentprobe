"""Runner script for the AgentProbe customer support demo agent."""

from __future__ import annotations

import os
import sys
import time

import agentprobe as ap

# Add repository root and examples to sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))
from examples.demo_agent.agent import run_support_agent

SAMPLE_QUESTIONS = [
    "Where is my order ORD-99241 and when will it arrive?",
    "What is your refund policy for opened software licenses?",
    "Can you update the shipping address for order ORD-10052 and email confirmation to user@example.com?",
    "How do I reset my multi-factor authentication (MFA) credentials?",
    "Order ORD-88123 is damaged. Can you check its status and email support@example.com about a replacement?",
]


def main() -> None:
    endpoint = os.getenv("AGENTPROBE_ENDPOINT", "http://localhost:8000").rstrip("/")
    api_key = os.getenv("AGENTPROBE_API_KEY")
    project = os.getenv("AGENTPROBE_PROJECT", "demo-support")

    print("=" * 80)
    print(" AgentProbe Demo Agent: Autonomous Customer Service Support")
    print(f" Target Endpoint: {endpoint}")
    print(f" Project:         {project}")
    print(" Mode:            Deterministic MockLLM (no external API keys required)")
    print("=" * 80)

    # Initialize SDK
    ap.init(
        project=project,
        endpoint=endpoint,
        api_key=api_key,
        exporter="http",
        flush_interval_seconds=0.5,
    )

    for idx, question in enumerate(SAMPLE_QUESTIONS, start=1):
        print(f"\n[{idx}/5] Question: {question}")
        start_time = time.perf_counter()
        answer = run_support_agent(question)
        elapsed_s = round(time.perf_counter() - start_time, 2)
        print(f"Answer: {answer}")
        print(f"Latency: {elapsed_s}s")

    # Flush all traces to API
    print("\nFlushing traces to AgentProbe Ingestion Service...")
    ap.flush(timeout=5.0)
    ap.shutdown()

    # Query API for the ingested runs to display links
    import httpx

    headers = {"X-AgentProbe-Key": api_key} if api_key else {}
    try:
        with httpx.Client(base_url=endpoint, headers=headers, timeout=5.0) as client:
            resp = client.get("/v1/runs", params={"project": project, "limit": 5})
            if resp.status_code == 200:
                runs_data = resp.json().get("runs", [])
                print("\n" + "=" * 80)
                print(" Ingestion Summary & Result Links:")
                print("=" * 80)
                for r in reversed(runs_data):
                    r_id = r["id"]
                    agent_name = r["agent_name"]
                    status = r["status"]
                    steps = r["totals"]["step_count"]
                    tokens = r["totals"]["total_tokens"]
                    cost = r["totals"]["cost"]
                    print(f"  * Run ID:   {r_id}")
                    print(
                        f"    Agent:    {agent_name} | Status: {status} | Steps: {steps} | "
                        f"Tokens: {tokens} | Cost: ${cost:.5f}"
                    )
                    print(f"    Tree URL: {endpoint}/v1/runs/{r_id}/tree")
                    print(f"    Run URL:  {endpoint}/v1/runs/{r_id}")
                    print("    " + "-" * 72)
    except Exception as e:  # noqa: BLE001
        print(f"Notice: Could not fetch run URLs from endpoint {endpoint}: {e}")

    print("\nDemo run finished successfully.")


if __name__ == "__main__":
    main()
