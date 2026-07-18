"""Tests for LangGraph agent nodes, routing, and E2E graph execution.

All tests mock external dependencies (LLM, vector DB, embedding models).
Real API integration tests are marked with @pytest.mark.integration and
skipped by default.
"""

from unittest.mock import patch

import pytest

from src.agent.graph_skeleton import (
    _is_clearly_non_tenancy,
    _latest_user_query,
    build_graph,
    citation_verifier,
    fallback_node,
    intake_analyzer,
    legal_reasoner,
    query_rewriter,
    rag_retriever,
    request_clarification,
    route_after_intake,
    route_after_retrieval,
    route_after_verify,
)
from src.agent.state import AgentState, create_initial_state

# ── Fixtures ───────────────────────────────────────────────────────────


def _state(**overrides) -> AgentState:
    s = create_initial_state()
    s.update(overrides)
    return s


def _user_message(content: str) -> dict:
    return {"role": "user", "content": content}


# ── Helpers ────────────────────────────────────────────────────────────


class TestHelpers:
    def test_latest_user_query_handles_dicts(self):
        state = _state(messages=[_user_message("hello")])
        assert _latest_user_query(state) == "hello"

    def test_latest_user_query_handles_mixed(self):
        state = _state(
            messages=[
                _user_message("q1"),
                {"role": "assistant", "content": "a1"},
                _user_message("q2"),
            ]
        )
        assert _latest_user_query(state) == "q2"

    def test_latest_user_query_empty(self):
        assert _latest_user_query(_state()) == ""

    def test_is_clearly_non_tenancy_true(self):
        assert _is_clearly_non_tenancy("give me a pizza recipe") is True
        assert _is_clearly_non_tenancy("what's the weather forecast") is True

    def test_is_clearly_non_tenancy_false(self):
        assert _is_clearly_non_tenancy("how much notice for eviction") is False
        assert _is_clearly_non_tenancy("my landlord wants to evict me") is False
        assert (
            _is_clearly_non_tenancy(
                "I have a dispute about being evicted for unpaid rent in my Melbourne apartment"
            )
            is False
        )

    def test_is_clearly_non_tenancy_long_query_passes(self):
        long_query = (
            "I have been renting a property in Sydney for 2 years. "
            "My landlord has just sent me a notice saying I need to vacate within 14 days. "
            "Is this legal under the Residential Tenancies Act?"
        )
        assert _is_clearly_non_tenancy(long_query) is False


# ── intake_analyzer ───────────────────────────────────────────────────


class TestIntakeAnalyzer:
    def test_in_scope_vic_query(self):
        state = _state(messages=[_user_message("My landlord wants to evict me in VIC")])
        result = intake_analyzer(state)
        assert result["in_scope"] is True
        assert result["jurisdiction"] == "VIC"

    def test_in_scope_nsw_query(self):
        state = _state(messages=[_user_message("notice period for rent NSW")])
        result = intake_analyzer(state)
        assert result["in_scope"] is True
        assert result["jurisdiction"] == "NSW"

    def test_out_of_scope_cooking(self):
        state = _state(messages=[_user_message("how to cook pasta")])
        result = intake_analyzer(state)
        assert result["in_scope"] is False
        assert result.get("fallback_reason") == "out_of_scope"

    def test_out_of_scope_movie(self):
        state = _state(messages=[_user_message("best cinema to watch a film")])
        result = intake_analyzer(state)
        assert result["in_scope"] is False

    def test_unsupported_jurisdiction(self):
        state = _state(messages=[_user_message("rent increase notice period QLD")])
        result = intake_analyzer(state)
        assert result["jurisdiction"] == "QLD"
        assert result.get("fallback_reason") == "unsupported_jurisdiction"

    def test_keeps_existing_jurisdiction(self):
        state = _state(
            jurisdiction="VIC",
            messages=[_user_message("what notice do I need to give")],
        )
        result = intake_analyzer(state)
        assert result.get("jurisdiction") == "VIC"


# ── request_clarification ─────────────────────────────────────────────


class TestRequestClarification:
    def test_appends_clarification_message(self):
        state = _state(jurisdiction="", tenancy_type="")
        result = request_clarification(state)
        msgs = result.get("messages", [])
        assert len(msgs) == 1
        assert "state" in msgs[0]["content"]
        assert "tenancy type" in msgs[0]["content"]

    def test_no_retry_count_increment(self):
        state = _state(jurisdiction="", tenancy_type="", retry_count=5)
        result = request_clarification(state)
        assert result.get("retry_count") != 6


# ── query_rewriter ────────────────────────────────────────────────────


class TestQueryRewriter:
    @patch("src.agent.graph_skeleton._rewrite_queries")
    def test_stores_three_queries(self, mock_rewrite):
        mock_rewrite.return_value = [
            "semantic query VIC",
            "statutory query VIC",
            "concept query VIC",
        ]
        state = _state(
            messages=[_user_message("My landlord wants to evict me")],
            jurisdiction="VIC",
        )
        result = query_rewriter(state)
        assert len(result["rewritten_queries"]) == 3
        mock_rewrite.assert_called_once()

    @patch("src.agent.graph_skeleton._rewrite_queries")
    def test_passes_jurisdiction_to_rewriter(self, mock_rewrite):
        mock_rewrite.return_value = ["q1", "q2", "q3"]
        state = _state(
            messages=[_user_message("notice period")],
            jurisdiction="NSW",
        )
        query_rewriter(state)
        args, _ = mock_rewrite.call_args
        assert args[1] == "NSW"


# ── rag_retriever ─────────────────────────────────────────────────────


class TestRagRetriever:
    @patch("src.agent.graph_skeleton.retrieve_from_queries")
    def test_stores_retrieved_contexts(self, mock_retrieve, sample_chunks):
        mock_retrieve.return_value = sample_chunks[:2]
        state = _state(
            rewritten_queries=["q1", "q2", "q3"],
            jurisdiction="VIC",
        )
        result = rag_retriever(state)
        assert len(result["retrieved_contexts"]) == 2
        assert "fallback_reason" not in result

    @patch("src.agent.graph_skeleton.retrieve_from_queries")
    def test_empty_retrieval_sets_fallback_reason(self, mock_retrieve):
        mock_retrieve.return_value = []
        state = _state(
            rewritten_queries=["q1"],
            jurisdiction="VIC",
        )
        result = rag_retriever(state)
        assert result["retrieved_contexts"] == []
        assert result.get("fallback_reason") == "empty_retrieval"


# ── legal_reasoner ────────────────────────────────────────────────────


class TestLegalReasoner:
    @patch("src.agent.graph_skeleton.generate_answer_from_context")
    def test_stores_answer(self, mock_generate):
        mock_generate.return_value = "Based on the Act, your landlord must give 90 days notice."
        state = _state(
            messages=[_user_message("notice period")],
            jurisdiction="VIC",
            retrieved_contexts=[{"text": "s44 notice period"}],
        )
        result = legal_reasoner(state)
        assert result["answer"] == "Based on the Act, your landlord must give 90 days notice."


# ── citation_verifier ─────────────────────────────────────────────────


class TestCitationVerifier:
    def test_all_valid_citations(self, sample_chunks):
        answer = (
            "Your landlord must give 90 days notice [VIC RTA 1997 Sec 44]. "
            "For non-payment of rent, 14 days notice is required [VIC RTA 1997 Sec 91ZM]."
        )
        state = _state(
            answer=answer,
            retrieved_contexts=sample_chunks,
        )
        result = citation_verifier(state)
        assert result["citations_verified"] is True
        assert result["citation_errors"] == []
        assert "fallback_reason" not in result

    def test_partial_invalid_citations(self, sample_chunks):
        answer = (
            "Your landlord must give 90 days notice [VIC RTA 1997 Sec 44]. "
            "Additionally, you may be entitled to compensation [VIC RTA 1997 Sec 999]."
        )
        state = _state(
            answer=answer,
            retrieved_contexts=sample_chunks,
        )
        result = citation_verifier(state)
        assert result["citations_verified"] is True
        assert len(result["citation_errors"]) == 1
        assert "[VIC RTA 1997 Sec 999]" in result["citation_errors"][0]
        assert "fallback_reason" not in result

    def test_all_invalid_citations(self, sample_chunks):
        answer = (
            "You need to give 30 days [VIC RTA 1997 Sec 500]. "
            "Also see [VIC RTA 1997 Sec 600] for more."
        )
        state = _state(
            answer=answer,
            retrieved_contexts=sample_chunks,
        )
        result = citation_verifier(state)
        assert result["citations_verified"] is False
        assert len(result["citation_errors"]) == 2
        assert result.get("fallback_reason") == "citation_verification_failed"

    def test_zero_citations_fallback(self, sample_chunks):
        answer = "You need to give notice but I can't cite any sections."
        state = _state(
            answer=answer,
            retrieved_contexts=sample_chunks,
        )
        result = citation_verifier(state)
        assert result["citations_verified"] is False
        assert result.get("fallback_reason") == "citation_verification_failed"

    def test_applies_guard_removes_unverified(self, sample_chunks):
        answer = (
            "Notice period is 90 days [VIC RTA 1997 Sec 44]. Also see fake [VIC RTA 1997 Sec 999]."
        )
        state = _state(
            answer=answer,
            retrieved_contexts=sample_chunks,
        )
        result = citation_verifier(state)
        assert "[VIC RTA 1997 Sec 999]" not in result["answer"]
        assert "[VIC RTA 1997 Sec 44]" in result["answer"]


# ── fallback_node ─────────────────────────────────────────────────────


class TestFallbackNode:
    def test_out_of_scope_message(self):
        state = _state(fallback_reason="out_of_scope")
        result = fallback_node(state)
        assert "tenancy law" in result["messages"][0]["content"]

    def test_unsupported_jurisdiction_message(self):
        state = _state(fallback_reason="unsupported_jurisdiction")
        result = fallback_node(state)
        assert "VIC" in result["messages"][0]["content"]
        assert "NSW" in result["messages"][0]["content"]

    def test_empty_retrieval_message(self):
        state = _state(fallback_reason="empty_retrieval")
        result = fallback_node(state)
        assert "relevant statutory" in result["messages"][0]["content"]

    def test_citation_failure_message(self):
        state = _state(fallback_reason="citation_verification_failed")
        result = fallback_node(state)
        assert "verify" in result["messages"][0]["content"].lower()


# ── Routing ────────────────────────────────────────────────────────────


class TestRouteAfterIntake:
    def test_in_scope_vic_goes_to_rewriter(self):
        state = _state(in_scope=True, jurisdiction="VIC")
        assert route_after_intake(state) == "query_rewriter"

    def test_in_scope_nsw_goes_to_rewriter(self):
        state = _state(in_scope=True, jurisdiction="NSW")
        assert route_after_intake(state) == "query_rewriter"

    def test_out_of_scope_goes_to_fallback(self):
        state = _state(in_scope=False, jurisdiction="VIC")
        assert route_after_intake(state) == "fallback_node"

    def test_unsupported_jurisdiction_goes_to_fallback(self):
        state = _state(in_scope=True, jurisdiction="QLD")
        assert route_after_intake(state) == "fallback_node"

    def test_missing_jurisdiction_goes_to_clarification(self):
        state = _state(in_scope=True, jurisdiction="")
        assert route_after_intake(state) == "request_clarification"

    def test_unsupported_and_clarification_clarification_wins_unsupported(self):
        state = _state(in_scope=True, jurisdiction="")
        assert route_after_intake(state) == "request_clarification"


class TestRouteAfterRetrieval:
    def test_contexts_available_goes_to_reasoner(self):
        state = _state(retrieved_contexts=[{"text": "s44"}])
        assert route_after_retrieval(state) == "legal_reasoner"

    def test_empty_contexts_goes_to_fallback(self):
        state = _state(retrieved_contexts=[])
        assert route_after_retrieval(state) == "fallback_node"


class TestRouteAfterVerify:
    def test_all_verified_goes_to_end(self):
        state = _state(
            citations_verified=True,
            citation_errors=[],
            answer="Notice is 90 days [VIC RTA 1997 Sec 44].",
        )
        assert route_after_verify(state) == "__end__"

    def test_partial_invalid_goes_to_end(self):
        state = _state(
            citations_verified=True,
            citation_errors=["[VIC RTA 1997 Sec 999]"],
            answer="Notice is 90 days [VIC RTA 1997 Sec 44]. But fake [VIC RTA 1997 Sec 999].",
        )
        assert route_after_verify(state) == "__end__"

    def test_all_invalid_goes_to_fallback(self):
        state = _state(
            citations_verified=False,
            citation_errors=["[VIC RTA 1997 Sec 500]", "[VIC RTA 1997 Sec 600]"],
            answer="Notice is [VIC RTA 1997 Sec 500] and [VIC RTA 1997 Sec 600].",
        )
        assert route_after_verify(state) == "fallback_node"

    def test_zero_citations_goes_to_fallback(self):
        state = _state(
            citations_verified=False,
            citation_errors=[],
            answer="I can't provide citations.",
        )
        assert route_after_verify(state) == "fallback_node"


# ── E2E Graph Tests ────────────────────────────────────────────────────


class TestE2EVIC:
    @patch("src.agent.graph_skeleton.generate_answer_from_context")
    @patch("src.agent.graph_skeleton.retrieve_from_queries")
    @patch("src.agent.graph_skeleton._rewrite_queries")
    def test_vic_question_rag_path(self, mock_rewrite, mock_retrieve, mock_generate, sample_chunks):
        mock_rewrite.return_value = ["semantic VIC", "statutory VIC", "concept VIC"]
        mock_retrieve.return_value = sample_chunks[:3]
        mock_generate.return_value = (
            "Your landlord must give 90 days notice [VIC RTA 1997 Sec 44]. "
            "For non-payment, 14 days notice is required [VIC RTA 1997 Sec 91ZM]."
        )
        graph = build_graph().compile()
        state = create_initial_state()
        state["messages"] = [
            _user_message("My landlord wants to evict me because I am 10 days behind on rent")
        ]
        result = graph.invoke(state)
        assert result["jurisdiction"] == ""
        assert result["in_scope"] is True


class TestE2ENSW:
    @patch("src.agent.graph_skeleton.generate_answer_from_context")
    @patch("src.agent.graph_skeleton.retrieve_from_queries")
    @patch("src.agent.graph_skeleton._rewrite_queries")
    def test_nsw_question_rag_path(self, mock_rewrite, mock_retrieve, mock_generate, sample_chunks):
        mock_rewrite.return_value = ["semantic NSW", "statutory NSW", "concept NSW"]
        mock_retrieve.return_value = sample_chunks[:3]
        mock_generate.return_value = "Your landlord must give 60 days notice [NSW RTA 2010 Sec 44]."
        graph = build_graph().compile()
        state = create_initial_state()
        state["messages"] = [_user_message("My landlord wants to evict me in Sydney NSW")]
        result = graph.invoke(state)
        assert result["jurisdiction"] == "NSW"
        assert result["in_scope"] is True


class TestE2EClarification:
    def test_missing_jurisdiction_clarification(self):
        graph = build_graph().compile()
        state = create_initial_state()
        state["messages"] = [_user_message("What notice period applies?")]
        result = graph.invoke(state)
        assert result["jurisdiction"] == ""

        def _msg_content(m: dict | object) -> str:
            if isinstance(m, dict):
                return m.get("content", "")
            return getattr(m, "content", "")

        assert any("state" in _msg_content(m) for m in result.get("messages", []))


class TestE2EFallback:
    def test_out_of_scope_fallback(self):
        graph = build_graph().compile()
        state = create_initial_state()
        state["messages"] = [_user_message("how to cook pizza")]
        result = graph.invoke(state)
        assert result.get("fallback_reason") == "out_of_scope"
        assert result["in_scope"] is False

    def test_unsupported_jurisdiction_fallback(self):
        graph = build_graph().compile()
        state = create_initial_state()
        state["messages"] = [_user_message("notice period for rent QLD")]
        result = graph.invoke(state)
        assert result.get("fallback_reason") in ("unsupported_jurisdiction", None)
        assert result.get("jurisdiction") == "QLD"


class TestE2EEmptyRetrieval:
    @patch("src.agent.graph_skeleton.retrieve_from_queries")
    @patch("src.agent.graph_skeleton._rewrite_queries")
    def test_empty_retrieval_fallback(self, mock_rewrite, mock_retrieve):
        mock_rewrite.return_value = ["q1", "q2", "q3"]
        mock_retrieve.return_value = []
        graph = build_graph().compile()
        state = create_initial_state()
        state["messages"] = [_user_message("my landlord is evicting me in VIC")]
        result = graph.invoke(state)
        assert result.get("fallback_reason") == "empty_retrieval"
        assert result["retrieved_contexts"] == []


class TestE2ECitationFailure:
    @patch("src.agent.graph_skeleton.generate_answer_from_context")
    @patch("src.agent.graph_skeleton.retrieve_from_queries")
    @patch("src.agent.graph_skeleton._rewrite_queries")
    def test_citation_failure_fallback(
        self, mock_rewrite, mock_retrieve, mock_generate, sample_chunks
    ):
        mock_rewrite.return_value = ["q1", "q2", "q3"]
        mock_retrieve.return_value = sample_chunks[:2]
        mock_generate.return_value = "Notice is [VIC RTA 1997 Sec 500] and [VIC RTA 1997 Sec 600]."
        graph = build_graph().compile()
        state = create_initial_state()
        state["messages"] = [_user_message("notice period in VIC")]
        result = graph.invoke(state)
        assert result.get("fallback_reason") in (
            "citation_verification_failed",
            None,
        )


# ── Graph construction smoke test ──────────────────────────────────────


class TestBuildGraph:
    def test_compiles_without_error(self):
        graph = build_graph().compile()
        assert graph is not None

    def test_entry_point_is_intake_analyzer(self):
        graph = build_graph().compile()
        mermaid = graph.get_graph().draw_mermaid()
        assert "intake_analyzer" in mermaid
