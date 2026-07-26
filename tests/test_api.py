"""Tests for API handler: health, validation, invoke, response filtering, fallback/clarification.

All tests mock graph.ainvoke — no real LLM or Qdrant calls.
"""

from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from src.api.handler import app
from src.api.models import AgentRequest, AgentResponse

# ── Helpers: mock graph return states ──────────────────────────────────


def _mock_success_state():
    return {
        "messages": [{"role": "user", "content": "What notice for eviction in VIC?"}],
        "jurisdiction": "VIC",
        "answer": "A rental provider must give 90 days notice [VIC RTA 1997 Sec 44(1)].",
        "retrieved_contexts": [
            {
                "chunk_id": "VIC-RTA1997-s44",
                "text": "A residential rental provider must give a renter at least 90 days notice...",
                "section_id": "44",
                "state": "VIC",
                "year": "1997",
            }
        ],
        "rewritten_queries": ["notice eviction VIC 90 days"],
        "in_scope": True,
        "citations_verified": True,
        "fallback_reason": "",
        "citation_errors": [],
        "tenancy_type": "",
        "dispute_category": "",
        "retry_count": 0,
        "is_complex_case": False,
    }


def _mock_fallback_state(reason="out_of_scope"):
    return {
        "messages": [
            {"role": "user", "content": "Give me a pizza recipe"},
            {
                "role": "assistant",
                "content": "I'm a tenancy law specialist and can't help with this topic. Please ask me about residential tenancy issues in VIC or NSW.",
            },
        ],
        "jurisdiction": "",
        "answer": "",
        "retrieved_contexts": [],
        "rewritten_queries": [],
        "in_scope": False,
        "citations_verified": False,
        "fallback_reason": reason,
        "citation_errors": [],
        "tenancy_type": "",
        "dispute_category": "",
        "retry_count": 0,
        "is_complex_case": False,
    }


def _mock_clarification_state():
    return {
        "messages": [
            {"role": "user", "content": "What notice for eviction?"},
            {
                "role": "assistant",
                "content": "To provide an accurate legal answer, I need more information. Please specify: state (e.g. VIC, NSW).",
            },
        ],
        "jurisdiction": "",
        "answer": "",
        "retrieved_contexts": [],
        "rewritten_queries": [],
        "in_scope": True,
        "citations_verified": False,
        "fallback_reason": "",
        "citation_errors": [],
        "tenancy_type": "",
        "dispute_category": "",
        "retry_count": 0,
        "is_complex_case": False,
    }


# ── Fixture: mock the handler's runtime deps ───────────────────────────


@pytest.fixture
def client():
    """TestClient with prepare_qdrant and get_compiled_graph mocked."""
    with (
        patch("src.api.handler.prepare_qdrant"),
        patch("src.api.handler.get_compiled_graph") as mock_get_graph,
    ):
        mock_graph = MagicMock()
        mock_graph.ainvoke = MagicMock()
        mock_get_graph.return_value = mock_graph
        yield TestClient(app), mock_graph


# ── Tests ──────────────────────────────────────────────────────────────


class TestHealth:
    def test_health_returns_correct_schema(self, client):
        test_client, _mock_graph = client
        response = test_client.get("/health")
        assert response.status_code == 200
        data = response.json()
        assert data == {
            "status": "healthy",
            "version": "0.1.0",
            "api_version": "1.0",
        }

    def test_health_no_extra_fields(self, client):
        test_client, _mock_graph = client
        response = test_client.get("/health")
        data = response.json()
        allowed = {"status", "version", "api_version"}
        assert set(data.keys()) == allowed


class TestRequestValidation:
    def test_valid_request_minimal(self):
        req = AgentRequest(question="What notice for eviction in VIC?")
        assert req.question == "What notice for eviction in VIC?"
        assert req.api_version == "1.0"
        assert req.request_id != ""

    def test_valid_request_with_jurisdiction(self):
        req = AgentRequest(question="test", jurisdiction="VIC")
        assert req.jurisdiction == "VIC"

    def test_invalid_jurisdiction_rejected(self):
        with pytest.raises(ValueError, match="jurisdiction"):
            AgentRequest(question="test", jurisdiction="QLD")

    def test_invalid_api_version_rejected(self):
        with pytest.raises(ValueError, match="api_version"):
            AgentRequest(question="test", api_version="2")

    def test_missing_question_rejected(self):
        with pytest.raises(ValueError):
            AgentRequest()

    def test_empty_question_rejected(self):
        with pytest.raises(ValueError):
            AgentRequest(question="")

    def test_whitespace_question_rejected(self):
        with pytest.raises(ValueError):
            AgentRequest(question="   ")

    def test_too_long_question_rejected(self):
        with pytest.raises(ValueError):
            AgentRequest(question="x" * 4001)

    def test_route_empty_question_422(self, client):
        test_client, _mock_graph = client
        response = test_client.post("/api/agent/invoke", json={"question": ""})
        assert response.status_code == 422

    def test_route_whitespace_question_422(self, client):
        test_client, _mock_graph = client
        response = test_client.post("/api/agent/invoke", json={"question": "   "})
        assert response.status_code == 422


class TestInvokeSuccess:
    async def _mock_success_ainvoke(self, _state):
        return _mock_success_state()

    def test_success_status_and_answer(self, client):
        test_client, mock_graph = client
        mock_graph.ainvoke = self._mock_success_ainvoke

        response = test_client.post(
            "/api/agent/invoke",
            json={"question": "What notice for eviction in VIC?", "jurisdiction": "VIC"},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "success"
        assert data["answer"] is not None
        assert "90 days" in data["answer"]
        assert len(data["verified_citations"]) >= 1
        assert data["selected_jurisdiction"] == "VIC"
        assert data["latency_ms"] is not None
        assert data["api_version"] == "1.0"
        assert data["generated_at"] is not None

    def test_diagnostic_field_filtering(self, client):
        test_client, mock_graph = client
        mock_graph.ainvoke = self._mock_success_ainvoke

        response = test_client.post(
            "/api/agent/invoke",
            json={"question": "What notice for eviction in VIC?"},
        )
        data = response.json()
        forbidden = {
            "rewritten_queries",
            "retrieved_context_count",
            "top_retrieved_provisions",
            "unverified_citations",
            "regulation_context_count",
            "act_context_count",
            "answer_length",
        }
        for key in forbidden:
            assert key not in data, f"Forbidden field '{key}' leaked in response"


class TestInvokeFallback:
    async def _mock_fallback_ainvoke(self, _state):
        return _mock_fallback_state("out_of_scope")

    def test_out_of_scope_fallback(self, client):
        test_client, mock_graph = client
        mock_graph.ainvoke = self._mock_fallback_ainvoke

        response = test_client.post(
            "/api/agent/invoke",
            json={"question": "Give me a pizza recipe"},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "fallback"
        assert data["fallback_reason"] == "out_of_scope"
        assert data["answer"] is not None
        assert "tenancy" in data["answer"].lower()

    async def _mock_unsupported_ainvoke(self, _state):
        return _mock_fallback_state("unsupported_jurisdiction")

    def test_unsupported_jurisdiction_fallback(self, client):
        test_client, mock_graph = client
        mock_graph.ainvoke = self._mock_unsupported_ainvoke

        response = test_client.post(
            "/api/agent/invoke",
            json={"question": "QLD eviction rules for Brisbane tenants?"},
        )
        data = response.json()
        assert data["status"] == "fallback"
        assert data["fallback_reason"] == "unsupported_jurisdiction"


class TestInvokeClarification:
    async def _mock_clarification_ainvoke(self, _state):
        return _mock_clarification_state()

    def test_missing_jurisdiction_clarification(self, client):
        test_client, mock_graph = client
        mock_graph.ainvoke = self._mock_clarification_ainvoke

        response = test_client.post(
            "/api/agent/invoke",
            json={"question": "What notice for eviction?"},
        )
        data = response.json()
        assert data["status"] == "clarification"
        assert data["answer"] is None
        assert data["clarification"] is not None
        assert "state" in data["clarification"].lower()
        assert data["fallback_reason"] is None


class TestAgentResponseModel:
    def test_response_serialization(self):
        resp = AgentResponse(
            request_id="abc-123",
            status="success",
            answer="test",
            verified_citations=["[VIC RTA 1997 Sec 44]"],
            citation_verified_rate=1.0,
            selected_jurisdiction="VIC",
            latency_ms=500.0,
        )
        d = resp.model_dump()
        assert d["status"] == "success"
        assert d["api_version"] == "1.0"
        assert "generated_at" in d

    def test_response_without_answer(self):
        resp = AgentResponse(
            request_id="abc",
            status="fallback",
            fallback_reason="out_of_scope",
        )
        d = resp.model_dump()
        assert d["answer"] is None
        assert d["fallback_reason"] == "out_of_scope"


class TestExtractAnswer:
    def test_extracts_from_answer_field(self):
        from src.api.handler import _extract_answer

        state = {"answer": "Legal analysis here.", "messages": []}
        assert _extract_answer(state) == "Legal analysis here."

    def test_falls_back_to_assistant_message(self):
        from src.api.handler import _extract_answer

        state = {
            "answer": "",
            "messages": [
                {"role": "user", "content": "Question?"},
                {"role": "assistant", "content": "Assistant reply."},
            ],
        }
        assert _extract_answer(state) == "Assistant reply."

    def test_never_returns_user_message(self):
        from src.api.handler import _extract_answer

        state = {
            "answer": "",
            "messages": [
                {"role": "user", "content": "User question."},
            ],
        }
        assert _extract_answer(state) == ""

    def test_returns_empty_when_no_answer(self):
        from src.api.handler import _extract_answer

        state = {"answer": "", "messages": []}
        assert _extract_answer(state) == ""
