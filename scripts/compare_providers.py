"""Provider comparison harness — prepared for Bedrock validation, NOT run yet.

Runs 5 fixed tenancy cases through the compiled LangGraph agent once per
requested provider. Jurisdiction is NOT preset: every case question
carries its own jurisdiction token (or is deliberately out of scope), so
the normal intake path is exercised. Each case is summarised with the
existing observability generate_run_summary(), which provides verified /
unverified citations, Act vs Regulation citation counts,
citation_verified_rate, latency, and status — no citation logic is
duplicated here.

Bedrock results (latency, token usage, cost, answers, citations) are
UNAVAILABLE until AWS credentials, region, and model access are
configured. The script refuses to run a provider whose environment is
not configured and never fabricates results. Token usage and cost are
not instrumented; read them from provider consoles.

Usage:
    python scripts/compare_providers.py --providers deepseek
    python scripts/compare_providers.py --providers deepseek bedrock
"""

import argparse
import json
import os
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

COMPARISON_CASES = [
    {
        "id": "vic-eviction",
        "jurisdiction": "VIC",
        "question": (
            "My landlord wants to evict me because I am 10 days behind on rent "
            "at my standard residential rental apartment in Melbourne VIC"
        ),
    },
    {
        "id": "nsw-eviction",
        "jurisdiction": "NSW",
        "question": (
            "My landlord wants to evict me because I am 14 days behind on rent "
            "at my apartment in Sydney NSW"
        ),
    },
    {
        "id": "vic-regulation-citation",
        "jurisdiction": "VIC",
        "question": "Does my rental home have to meet minimum standards for heating in VIC?",
    },
    {
        "id": "nsw-regulation-citation",
        "jurisdiction": "NSW",
        "question": "What condition report is required when a new tenant moves in NSW?",
    },
    {
        "id": "out-of-scope-fallback",
        "jurisdiction": "",
        "question": "What is a good pizza recipe?",
    },
]


def _provider_unavailable_reason(name: str) -> str:
    """Return '' if the provider env is configured, else the missing-config reason.

    For Bedrock this checks region/model config, then resolves credentials
    via the boto3 chain (env, SSO, profiles, roles) — never via raw env
    checks alone.
    """
    if name == "deepseek":
        if not os.environ.get("DEEPSEEK_API_KEY"):
            return "DEEPSEEK_API_KEY not set"
        return ""
    if name == "bedrock":
        if not (
            os.environ.get("AWS_REGION", "").strip()
            or os.environ.get("AWS_DEFAULT_REGION", "").strip()
        ):
            return "AWS_REGION or AWS_DEFAULT_REGION not set"
        if not os.environ.get("BEDROCK_MODEL_ID", "").strip():
            return "BEDROCK_MODEL_ID not set"
        import boto3

        if boto3.Session().get_credentials() is None:
            return "AWS credentials could not be resolved by the boto3 credential chain"
        return ""
    return f"unknown provider {name!r}"


def _run_case(graph, case: dict) -> dict:
    from src.agent.observability import generate_run_summary
    from src.agent.state import create_initial_state

    state = create_initial_state()
    state["messages"] = [{"role": "user", "content": case["question"]}]

    t0 = time.perf_counter()
    result = graph.invoke(state)
    latency_ms = (time.perf_counter() - t0) * 1000

    summary = generate_run_summary(result, total_latency_ms=latency_ms)
    summary["case_id"] = case["id"]
    summary["expected_jurisdiction"] = case["jurisdiction"]
    summary["answer"] = result.get("answer", "") or ""
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description="LLM provider comparison harness")
    parser.add_argument(
        "--providers",
        nargs="+",
        default=["deepseek"],
        choices=["deepseek", "bedrock"],
        help="Providers to compare (default: deepseek)",
    )
    args = parser.parse_args()

    load_dotenv()

    from src.agent.graph_skeleton import build_graph

    report: dict = {
        "generated_at": datetime.now(UTC).isoformat(),
        "cases": [c["id"] for c in COMPARISON_CASES],
        "token_usage_and_cost": ("unavailable — not instrumented; read from provider consoles"),
        "providers": {},
    }

    for provider in args.providers:
        reason = _provider_unavailable_reason(provider)
        if reason:
            print(f"[{provider}] UNAVAILABLE — {reason}. Results not fabricated.")
            report["providers"][provider] = {"status": "unavailable", "reason": reason}
            continue

        os.environ["LLM_PROVIDER"] = provider
        graph = build_graph().compile()
        results = []
        for case in COMPARISON_CASES:
            print(f"[{provider}] running case: {case['id']}")
            results.append(_run_case(graph, case))
        report["providers"][provider] = {"status": "completed", "results": results}

    out_dir = Path("reports/provider_comparison")
    out_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
    out_path = out_dir / f"comparison_{timestamp}.json"
    out_path.write_text(json.dumps(report, indent=2, ensure_ascii=False, default=str))
    print(f"Saved: {out_path}")


if __name__ == "__main__":
    main()
