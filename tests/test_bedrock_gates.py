"""Offline regression tests for the Bedrock integration test gates.

These run in the normal suite (no AWS, no network, no Qdrant server) and
lock in two properties of tests/test_bedrock_integration.py:

1. Gate helpers return explicit skip reasons instead of raising, so a
   missing or broken local Qdrant corpus yields SKIPPED e2e tests, never
   vague fixture errors.
2. Provider-only smoke tests require AWS only; the Qdrant fixture is
   required exclusively by the VIC/NSW/citation pipeline tests, so
   Bedrock Converse can be validated before the retrieval stack exists.
"""

from __future__ import annotations

import inspect
from unittest.mock import MagicMock, patch

import src.rag.retrieval.vector_store as vs
from tests import test_bedrock_integration as tbi

# ── _bedrock_gate_reason (collection-time env gate) ────────────────────


class TestBedrockGateReason:
    def test_requires_opt_in(self, monkeypatch):
        monkeypatch.delenv("RUN_BEDROCK_INTEGRATION", raising=False)
        assert "RUN_BEDROCK_INTEGRATION" in tbi._bedrock_gate_reason()

    def test_requires_region(self, monkeypatch):
        monkeypatch.setenv("RUN_BEDROCK_INTEGRATION", "1")
        monkeypatch.delenv("AWS_REGION", raising=False)
        monkeypatch.delenv("AWS_DEFAULT_REGION", raising=False)
        monkeypatch.setenv("BEDROCK_MODEL_ID", "test.model:0")
        assert "AWS_REGION" in tbi._bedrock_gate_reason()

    def test_requires_model_id(self, monkeypatch):
        monkeypatch.setenv("RUN_BEDROCK_INTEGRATION", "1")
        monkeypatch.setenv("AWS_REGION", "ap-southeast-2")
        monkeypatch.delenv("BEDROCK_MODEL_ID", raising=False)
        assert "BEDROCK_MODEL_ID" in tbi._bedrock_gate_reason()

    def test_all_set_returns_empty(self, monkeypatch):
        monkeypatch.setenv("RUN_BEDROCK_INTEGRATION", "1")
        monkeypatch.setenv("AWS_REGION", "ap-southeast-2")
        monkeypatch.setenv("BEDROCK_MODEL_ID", "test.model:0")
        assert tbi._bedrock_gate_reason() == ""


# ── _qdrant_gate_reason (runtime corpus gate) ──────────────────────────


class TestQdrantGateReason:
    def test_missing_storage_dir_returns_reason(self, monkeypatch, tmp_path):
        monkeypatch.setattr(vs, "QDRANT_PATH", str(tmp_path / "nonexistent"))
        reason = tbi._qdrant_gate_reason()
        assert "Qdrant storage dir not found" in reason

    def test_connect_failure_returns_reason(self, monkeypatch, tmp_path):
        monkeypatch.setattr(vs, "QDRANT_PATH", str(tmp_path))
        with patch("qdrant_client.QdrantClient", side_effect=RuntimeError("cannot open")):
            reason = tbi._qdrant_gate_reason()
        assert "Qdrant connect failed" in reason

    def test_collection_check_failure_returns_reason_not_raises(self, monkeypatch, tmp_path):
        """Corrupt/incompatible storage must yield an explicit skip reason,
        never a fixture ERROR."""
        monkeypatch.setattr(vs, "QDRANT_PATH", str(tmp_path))
        fake_client = MagicMock()
        fake_client.collection_exists.side_effect = RuntimeError("simulated storage corruption")
        with patch("qdrant_client.QdrantClient", return_value=fake_client):
            reason = tbi._qdrant_gate_reason()
        assert "Qdrant collection check failed" in reason
        fake_client.close.assert_called_once()

    def test_missing_collection_returns_reason(self, monkeypatch, tmp_path):
        monkeypatch.setattr(vs, "QDRANT_PATH", str(tmp_path))
        fake_client = MagicMock()
        fake_client.collection_exists.return_value = False
        with patch("qdrant_client.QdrantClient", return_value=fake_client):
            reason = tbi._qdrant_gate_reason()
        assert "collection missing" in reason
        fake_client.close.assert_called_once()

    def test_empty_state_returns_reason(self, monkeypatch, tmp_path):
        monkeypatch.setattr(vs, "QDRANT_PATH", str(tmp_path))
        fake_client = MagicMock()
        fake_client.collection_exists.return_value = True
        fake_client.count.return_value = MagicMock(count=0)
        with patch("qdrant_client.QdrantClient", return_value=fake_client):
            reason = tbi._qdrant_gate_reason()
        assert "no VIC chunks indexed" in reason


# ── Fixture separation: smoke tests must not require Qdrant ────────────


class TestFixtureSeparation:
    SMOKE_TESTS = (
        tbi.test_bedrock_generate_returns_text,
        tbi.test_bedrock_selected_via_factory,
    )
    PIPELINE_TESTS = (
        tbi.test_vic_eviction_pipeline_with_bedrock,
        tbi.test_nsw_eviction_pipeline_with_bedrock,
        tbi.test_bedrock_answer_citations_survive_guard,
    )

    def test_smoke_tests_require_aws_only(self):
        for func in self.SMOKE_TESTS:
            params = inspect.signature(func).parameters
            assert "aws_credentials_or_skip" in params, func.__name__
            assert "qdrant_or_skip" not in params, func.__name__

    def test_pipeline_tests_require_aws_and_qdrant(self):
        for func in self.PIPELINE_TESTS:
            params = inspect.signature(func).parameters
            assert "aws_credentials_or_skip" in params, func.__name__
            assert "qdrant_or_skip" in params, func.__name__
