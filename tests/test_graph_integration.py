"""Real-service integration smoke tests for the LangGraph agent.

Mark all real-service tests with @pytest.mark.integration.
Normal pytest (pytest tests/ -v) skips integration — see pyproject.toml addopts.

Usage:
    pytest tests/test_graph_integration.py -m integration -v
"""

import logging
import os
from pathlib import Path

import pytest
from dotenv import load_dotenv

from src.agent.graph_skeleton import build_graph
from src.agent.state import create_initial_state
from src.rag.generation.generator import CITATION_RE

logger = logging.getLogger(__name__)

# ── Environment guards ──────────────────────────────────────────────────


def _get_env_check() -> list[tuple[str, bool, str]]:
    """Run all service availability checks. Returns list of (name, passed, message)."""
    load_dotenv()
    checks: list[tuple[str, bool, str]] = []

    api_key = os.environ.get("DEEPSEEK_API_KEY", "")
    checks.append(
        ("DEEPSEEK_API_KEY", bool(api_key), api_key[:8] + "..." if api_key else "not set")
    )

    try:
        from src.rag.retrieval.vector_store import COLLECTION_NAME, QDRANT_PATH
    except ImportError as e:
        checks.append(("Qdrant path load", False, str(e)))
        return checks

    qdrant_dir = Path(QDRANT_PATH)
    checks.append(("Qdrant storage dir", qdrant_dir.exists(), str(qdrant_dir.resolve())))

    if not qdrant_dir.exists():
        return checks

    try:
        from qdrant_client import QdrantClient, models

        client = QdrantClient(path=str(qdrant_dir.resolve()))
    except Exception as e:
        checks.append(("Qdrant connect", False, str(e)))
        return checks

    try:
        col_exists = client.collection_exists(COLLECTION_NAME)
        checks.append(("Collection exists", col_exists, COLLECTION_NAME))

        if not col_exists:
            return checks

        try:
            vic_count = client.count(
                collection_name=COLLECTION_NAME,
                count_filter=models.Filter(
                    must=[models.FieldCondition(key="state", match=models.MatchValue(value="VIC"))]
                ),
            ).count
        except Exception:
            vic_count = 0

        try:
            nsw_count = client.count(
                collection_name=COLLECTION_NAME,
                count_filter=models.Filter(
                    must=[models.FieldCondition(key="state", match=models.MatchValue(value="NSW"))]
                ),
            ).count
        except Exception:
            nsw_count = 0

        checks.append(("VIC chunks present", vic_count > 0, f"count={vic_count}"))
        checks.append(("NSW chunks present", nsw_count > 0, f"count={nsw_count}"))
    finally:
        client.close()

    return checks


def _skip_if_missing(required_checks: frozenset[str] | None = None):
    """Run env checks and skip if any required check fails.

    Args:
        required_checks: Set of check names to require. If None, require all.
    """
    checks = _get_env_check()
    failed: list[str] = []
    for name, passed, detail in checks:
        if required_checks is not None and name not in required_checks:
            continue
        if not passed:
            failed.append(f"{name}: {detail}")
    if failed:
        pytest.skip("; ".join(failed))

    return checks


@pytest.fixture(scope="session")
def real_services_available() -> dict:
    """Session-scoped fixture: verify DeepSeek + Qdrant are available.

    Runs checks, then closes the Qdrant client so the graph can open its own.
    Skips with detailed message if any required service is missing.
    """
    _skip_if_missing()
    try:
        from src.rag.retrieval.vector_store import COLLECTION_NAME, QDRANT_PATH

        return {
            "collection": COLLECTION_NAME,
            "qdrant_path": QDRANT_PATH,
        }
    except Exception as e:
        pytest.skip(f"Config load failed: {e}")
        return {}


# ── Helpers ─────────────────────────────────────────────────────────────


def _user_message(content: str) -> dict:
    return {"role": "user", "content": content}


def _get_last_assistant_message(messages: list) -> str:
    for m in reversed(messages):
        if isinstance(m, dict) and m.get("role") == "assistant":
            return m.get("content", "")
        if hasattr(m, "content") and getattr(m, "type", "") == "ai":
            return m.content
    return ""


def _print_trace(question: str, result: dict) -> None:
    """Print a compact trace of the E2E graph execution."""
    separator = "-" * 60
    print(f"\n{separator}")
    print(f"TRACE: {question[:100]}")
    print(f"{separator}")
    print(f"Jurisdiction: {result.get('jurisdiction', '?')}")

    queries = result.get("rewritten_queries", [])
    print(f"Rewritten queries ({len(queries)}):")
    for i, q in enumerate(queries, 1):
        print(f"  [{i}] {q[:120]}")

    contexts = result.get("retrieved_contexts", [])
    print(f"Retrieved contexts: {len(contexts)}")
    for i, c in enumerate(contexts[:5], 1):
        sid = c.get("section_id", "?")
        title = c.get("section_title", c.get("act", "?"))
        print(f"  [{i}] Sec {sid} — {title}")

    answer = result.get("answer", "")
    print(f"Final answer ({len(answer)} chars):")
    print(answer[:800] + ("..." if len(answer) > 800 else ""))

    errors = result.get("citation_errors", [])
    all_citations = CITATION_RE.findall(answer)
    verified_est = len(all_citations) - len(errors)
    print(
        f"Citations: {len(all_citations)} total, ~{verified_est} verified, {len(errors)} unverified"
    )
    if all_citations:
        print(f"  Detected: {all_citations}")
    if errors:
        print(f"  Unverified: {errors}")

    fb = result.get("fallback_reason", "")
    if fb:
        print(f"Fallback: {fb}")
    print(f"{separator}\n")


# ── Real E2E: VIC ───────────────────────────────────────────────────────


@pytest.mark.integration
class TestRealE2EVIC:
    def test_vic_eviction_full_graph(self, real_services_available):
        """Full graph E2E: VIC tenancy question → answer with verified citations."""
        question = "My landlord wants to evict me for unpaid rent in VIC"
        state = create_initial_state()
        state["messages"] = [_user_message(question)]

        graph = build_graph().compile()
        result = graph.invoke(state)

        _print_trace(question, result)

        assert result["in_scope"] is True, "VIC tenancy question should be in scope"
        assert result["jurisdiction"] == "VIC", f"Expected VIC, got {result.get('jurisdiction')}"

        queries = result.get("rewritten_queries", [])
        assert len(queries) == 3, f"Expected 3 rewritten queries, got {len(queries)}"

        contexts = result.get("retrieved_contexts", [])
        assert len(contexts) > 0, "Expected non-empty retrieved contexts"

        answer = result.get("answer", "")
        assert len(answer) > 0, "Expected non-empty answer"

        fb = result.get("fallback_reason", "")
        assert fb in ("", None), f"Graph fell back: {fb}"

        errors = result.get("citation_errors", [])
        all_cits = CITATION_RE.findall(answer)
        if all_cits:
            assert len(errors) < len(all_cits), (
                f"All {len(all_cits)} citations unverified: {errors}"
            )
        for err in errors:
            assert err not in answer, f"Unverified citation still in answer: {err}"


# ── Real E2E: NSW ───────────────────────────────────────────────────────


@pytest.mark.integration
class TestRealE2ENSW:
    def test_nsw_eviction_full_graph(self, real_services_available):
        """Full graph E2E: NSW tenancy question → answer with verified citations."""
        question = "My landlord wants to evict me for unpaid rent in NSW"
        state = create_initial_state()
        state["messages"] = [_user_message(question)]

        graph = build_graph().compile()
        result = graph.invoke(state)

        _print_trace(question, result)

        assert result["in_scope"] is True, "NSW tenancy question should be in scope"
        assert result["jurisdiction"] == "NSW", f"Expected NSW, got {result.get('jurisdiction')}"

        queries = result.get("rewritten_queries", [])
        assert len(queries) == 3, f"Expected 3 rewritten queries, got {len(queries)}"

        contexts = result.get("retrieved_contexts", [])
        assert len(contexts) > 0, "Expected non-empty retrieved contexts"

        answer = result.get("answer", "")
        assert len(answer) > 0, "Expected non-empty answer"

        fb = result.get("fallback_reason", "")
        assert fb in ("", None), f"Graph fell back: {fb}"

        errors = result.get("citation_errors", [])
        all_cits = CITATION_RE.findall(answer)
        if all_cits:
            assert len(errors) < len(all_cits), (
                f"All {len(all_cits)} citations unverified: {errors}"
            )
        for err in errors:
            assert err not in answer, f"Unverified citation still in answer: {err}"


# ── Clarification path (no external services needed) ────────────────────


@pytest.mark.integration
class TestClarificationIntegration:
    def test_missing_jurisdiction_goes_to_clarification(self):
        """Graph routes to clarification when jurisdiction is missing."""
        state = create_initial_state()
        state["messages"] = [_user_message("What notice period applies?")]

        graph = build_graph().compile()
        result = graph.invoke(state)

        assert result["jurisdiction"] == "", "Jurisdiction should remain empty"
        assert result.get("fallback_reason") in (
            "",
            None,
        ), "Should not be in fallback path"

        last_msg = _get_last_assistant_message(result.get("messages", []))
        assert "state (e.g. VIC, NSW)" in last_msg.lower() or "state" in last_msg.lower()
        assert "tenancy type" in last_msg.lower()


# ── Fallback paths (no external services needed) ────────────────────────


@pytest.mark.integration
class TestFallbackIntegration:
    def test_out_of_scope_goes_to_fallback(self):
        """Non-tenancy query routes to fallback."""
        state = create_initial_state()
        state["messages"] = [_user_message("how to cook pizza")]

        graph = build_graph().compile()
        result = graph.invoke(state)

        assert result["in_scope"] is False
        assert result.get("fallback_reason") == "out_of_scope"

        last_msg = _get_last_assistant_message(result.get("messages", []))
        assert "tenancy law specialist" in last_msg.lower() or "tenancy" in last_msg.lower()

    def test_unsupported_jurisdiction_goes_to_fallback(self):
        """Unsupported jurisdiction routes to fallback."""
        state = create_initial_state()
        state["messages"] = [_user_message("notice period for rent QLD")]

        graph = build_graph().compile()
        result = graph.invoke(state)

        assert result["jurisdiction"] == "QLD"
        assert result.get("fallback_reason") == "unsupported_jurisdiction"

        last_msg = _get_last_assistant_message(result.get("messages", []))
        assert "vic" in last_msg.lower() or "nsw" in last_msg.lower()


# ── Real E2E: Regulation-specific questions ─────────────────────────────


@pytest.mark.integration
class TestRealE2EVICRegulation:
    def test_vic_rent_increase_notice_form(self, real_services_available):
        """VIC Regulation E2E: text message vs prescribed form for rent increase."""
        question = (
            "Is a text message enough to notify me of a rent increase in VIC, "
            "or is a specific form required?"
        )
        state = create_initial_state()
        state["messages"] = [_user_message(question)]

        graph = build_graph().compile()
        result = graph.invoke(state)

        _print_trace(question, result)

        assert result["in_scope"] is True
        assert result["jurisdiction"] == "VIC"
        assert len(result.get("rewritten_queries", [])) == 3
        assert len(result.get("retrieved_contexts", [])) > 0
        assert len(result.get("answer", "")) > 0
        assert result.get("fallback_reason") in ("", None)

        reg_chunks = [
            c
            for c in result.get("retrieved_contexts", [])
            if c.get("instrument_type") == "regulation"
        ]
        assert len(reg_chunks) > 0, "Regulation chunks should be retrieved"

        errors = result.get("citation_errors", [])
        answer = result.get("answer", "")
        all_cits = CITATION_RE.findall(answer)
        if all_cits:
            assert len(errors) < len(all_cits), (
                f"All {len(all_cits)} citations unverified: {errors}"
            )
        for err in errors:
            assert err not in answer, f"Unverified citation still in answer: {err}"


@pytest.mark.integration
class TestRealE2ENSWRegulation:
    def test_nsw_condition_report_format(self, real_services_available):
        """NSW Regulation E2E: prescribed condition report format."""
        question = "What condition report format must a landlord use in NSW?"
        state = create_initial_state()
        state["messages"] = [_user_message(question)]

        graph = build_graph().compile()
        result = graph.invoke(state)

        _print_trace(question, result)

        assert result["in_scope"] is True
        assert result["jurisdiction"] == "NSW"
        assert len(result.get("rewritten_queries", [])) == 3
        assert len(result.get("retrieved_contexts", [])) > 0
        assert len(result.get("answer", "")) > 0
        assert result.get("fallback_reason") in ("", None)

        reg_chunks = [
            c
            for c in result.get("retrieved_contexts", [])
            if c.get("instrument_type") == "regulation"
        ]
        assert len(reg_chunks) > 0, "Regulation chunks should be retrieved"

        errors = result.get("citation_errors", [])
        answer = result.get("answer", "")
        all_cits = CITATION_RE.findall(answer)
        if all_cits:
            assert len(errors) < len(all_cits), (
                f"All {len(all_cits)} citations unverified: {errors}"
            )
        for err in errors:
            assert err not in answer, f"Unverified citation still in answer: {err}"
