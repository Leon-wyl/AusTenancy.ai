"""Handler event-contract tests: Mangum → API Gateway HTTP API v2 events.

These call handler(event, context) directly in Python.  NOT RIE tests.
RIE container tests are in Task 11.
"""

import json
from pathlib import Path
from unittest.mock import patch

FIXTURES_DIR = Path(__file__).parent / "fixtures"


def _load_fixture(name: str) -> dict:
    with open(FIXTURES_DIR / name, encoding="utf-8") as f:
        return json.load(f)


class TestLambdaHandler:
    def test_health_event(self):
        """Mangum handler processes GET /health event with correct schema."""
        with patch("src.api.handler.prepare_qdrant"):
            from src.api.handler import handler as lambda_handler

        event = _load_fixture("api_gateway_v2_health.json")
        response = lambda_handler(event, None)

        assert response["statusCode"] == 200
        body = json.loads(response["body"])
        assert body == {
            "status": "healthy",
            "version": "0.1.0",
            "api_version": "1.0",
        }

    def test_invoke_event_mocked_graph(self):
        """Mangum handler processes POST /api/agent/invoke event with mocked graph."""
        async def mock_ainvoke(_state):
            return {
                "messages": [{"role": "user", "content": "test"}],
                "jurisdiction": "VIC",
                "answer": "Test answer [VIC RTA 1997 Sec 44].",
                "retrieved_contexts": [
                    {
                        "chunk_id": "x",
                        "text": "test",
                        "section_id": "44",
                        "state": "VIC",
                        "year": "1997",
                    }
                ],
                "rewritten_queries": [],
                "in_scope": True,
                "citations_verified": True,
                "fallback_reason": "",
                "citation_errors": [],
                "tenancy_type": "",
                "dispute_category": "",
                "retry_count": 0,
                "is_complex_case": False,
            }

        with (
            patch("src.api.handler.prepare_qdrant"),
            patch("src.api.handler.get_compiled_graph") as mock_get_graph,
        ):
            from unittest.mock import MagicMock

            mock_graph = MagicMock()
            mock_graph.ainvoke = mock_ainvoke
            mock_get_graph.return_value = mock_graph

            from src.api.handler import handler as lambda_handler

            event = _load_fixture("api_gateway_v2_event.json")
            response = lambda_handler(event, None)

            assert response["statusCode"] == 200
            body = json.loads(response["body"])
            assert body["status"] == "success"
            assert "Test answer" in body["answer"]
            assert body["selected_jurisdiction"] == "VIC"
            assert body["api_version"] == "1.0"

    def test_non_json_body_rejected(self):
        """Malformed event body returns 4xx."""
        with (
            patch("src.api.handler.prepare_qdrant"),
            patch("src.api.handler.get_compiled_graph") as mock_get_graph,
        ):
            from unittest.mock import MagicMock

            mock_graph = MagicMock()
            mock_graph.ainvoke = MagicMock()
            mock_get_graph.return_value = mock_graph

            from src.api.handler import handler as lambda_handler

            event = _load_fixture("api_gateway_v2_event.json")
            event["body"] = "not valid json"
            response = lambda_handler(event, None)

            assert response["statusCode"] in (400, 422, 500)
