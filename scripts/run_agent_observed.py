"""One-shot observed agent run with structured summary output.

Usage:
    python scripts/run_agent_observed.py --question "Can my landlord..." [--save] [--langsmith]

The script:
    1. Optionally configures LangSmith env vars.
    2. Runs the existing graph via graph.invoke().
    3. Measures total latency with time.perf_counter().
    4. Prints the final answer and structured run summary.
    5. Optionally saves the summary as JSON under reports/runs/.
"""

import argparse
import json
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

from src.agent.graph_skeleton import build_graph
from src.agent.observability import configure_langsmith, generate_run_summary
from src.agent.state import create_initial_state


def main() -> None:
    parser = argparse.ArgumentParser(description="Observed agent run")
    parser.add_argument("--question", required=True, help="User query")
    parser.add_argument("--save", action="store_true", help="Save summary JSON to reports/runs/")
    parser.add_argument(
        "--langsmith", action="store_true", help="Enable LangSmith tracing via env vars"
    )
    args = parser.parse_args()

    load_dotenv()

    if args.langsmith:
        configured = configure_langsmith(enable=True)
        if not configured:
            print(
                "Warning: LangSmith not configured — "
                "check LANGCHAIN_TRACING_V2 and LANGCHAIN_API_KEY env vars"
            )

    graph = build_graph().compile()

    state = create_initial_state()
    state["messages"] = [{"role": "user", "content": args.question}]

    t0 = time.perf_counter()
    result = graph.invoke(state)
    elapsed_ms = (time.perf_counter() - t0) * 1000

    summary = generate_run_summary(result, total_latency_ms=elapsed_ms)

    answer: str = result.get("answer", "") or ""
    if answer:
        print("Answer:")
        print("=" * 60)
        print(answer)
        print("=" * 60)
        print()

    print(f"Run Summary ({summary['status']}, {elapsed_ms:.0f}ms):")
    print(json.dumps(summary, indent=2, ensure_ascii=False, default=str))

    if args.save:
        out_dir = Path("reports/runs")
        out_dir.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
        out_path = out_dir / f"run_{timestamp}.json"
        out_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False, default=str))
        print(f"\nSaved: {out_path}")


if __name__ == "__main__":
    main()
