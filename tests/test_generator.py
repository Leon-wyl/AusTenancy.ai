"""Tests for Phase 3 generator — reranking, prompt engineering, and LLM generation."""

import os

import pytest

from src.generation import generator


# ── Unit: build_legal_prompt ───────────────────────────────────────────


class TestBuildLegalPrompt:
    def test_includes_all_section_ids(self, sample_chunks):
        prompt = generator.build_legal_prompt("Test query", sample_chunks)
        for c in sample_chunks:
            assert f"Sec {c['section_id']}" in prompt

    def test_includes_query_text(self, sample_chunks):
        query = "How much notice is required?"
        prompt = generator.build_legal_prompt(query, sample_chunks)
        assert query in prompt

    def test_empty_chunks_no_error(self):
        prompt = generator.build_legal_prompt("Some query", [])
        assert isinstance(prompt, str)
        assert "Some query" in prompt

    def test_context_blocks_are_numbered(self, sample_chunks):
        prompt = generator.build_legal_prompt("query", sample_chunks)
        for i in range(1, len(sample_chunks) + 1):
            assert f"--- Context {i} [" in prompt


# ── Unit: verify_citations ─────────────────────────────────────────────


class TestVerifyCitations:
    def test_valid_citation_verified(self, sample_chunks):
        answer = "The provider must give notice [VIC RTA 1997 Sec 44]."
        result = generator.verify_citations(answer, sample_chunks)
        assert "[VIC RTA 1997 Sec 44]" in result["verified"]
        assert result["unverified"] == []

    def test_invalid_citation_unverified(self, sample_chunks):
        answer = "According to [VIC RTA 1997 Sec 999] there is a rule."
        result = generator.verify_citations(answer, sample_chunks)
        assert "[VIC RTA 1997 Sec 999]" in result["unverified"]
        assert result["verified"] == []

    def test_alphanumeric_section_id(self, sample_chunks):
        answer = "For non-payment see [VIC RTA 1997 Sec 91ZM]."
        result = generator.verify_citations(answer, sample_chunks)
        assert "[VIC RTA 1997 Sec 91ZM]" in result["verified"]

    def test_padded_numeric_section_id(self, sample_chunks):
        answer = "Per [VIC RTA 1997 Sec 044] the provider must notify."
        result = generator.verify_citations(answer, sample_chunks)
        assert "[VIC RTA 1997 Sec 044]" in result["verified"]
        assert result["unverified"] == []

    def test_mixed_verified_unverified(self, sample_chunks):
        answer = (
            "Under [VIC RTA 1997 Sec 44] and [VIC RTA 1997 Sec 999], "
            "the notice is required."
        )
        result = generator.verify_citations(answer, sample_chunks)
        assert "[VIC RTA 1997 Sec 44]" in result["verified"]
        assert "[VIC RTA 1997 Sec 999]" in result["unverified"]
        assert len(result["verified"]) == 1
        assert len(result["unverified"]) == 1

    def test_answer_with_no_brackets(self, sample_chunks):
        answer = "The Residential Tenancies Act provides certain rights."
        result = generator.verify_citations(answer, sample_chunks)
        assert result["verified"] == []
        assert result["unverified"] == []

    def test_duplicate_citations_both_verified(self, sample_chunks):
        answer = "[VIC RTA 1997 Sec 44] is the rule. Again, [VIC RTA 1997 Sec 44] applies."
        result = generator.verify_citations(answer, sample_chunks)
        assert len(result["verified"]) == 2
        assert result["unverified"] == []

    def test_subsection_citation_verified(self, sample_chunks):
        answer = "The provider must give notice [VIC RTA 1997 Sec 44(1)]."
        result = generator.verify_citations(answer, sample_chunks)
        assert "[VIC RTA 1997 Sec 44(1)]" in result["verified"]
        assert result["unverified"] == []

    def test_multi_subsection_citation_verified(self, sample_chunks):
        answer = "Per [VIC RTA 1997 Sec 91ZM(7)] the threshold is 14 days."
        result = generator.verify_citations(answer, sample_chunks)
        assert "[VIC RTA 1997 Sec 91ZM(7)]" in result["verified"]

    def test_nested_subsection_citation_verified(self, sample_chunks):
        answer = (
            "Under [VIC RTA 1997 Sec 91ZM(7)] the threshold is 14 days "
            "and [VIC RTA 1997 Sec 91ZM(1)(a)] allows the notice."
        )
        result = generator.verify_citations(answer, sample_chunks)
        assert "[VIC RTA 1997 Sec 91ZM(7)]" in result["verified"]
        assert "[VIC RTA 1997 Sec 91ZM(1)(a)]" in result["verified"]


# ── Unit: SYSTEM_PROMPT ────────────────────────────────────────────────


class TestSystemPrompt:
    def test_not_empty(self):
        assert isinstance(generator.SYSTEM_PROMPT, str)
        assert len(generator.SYSTEM_PROMPT) > 0

    def test_contains_uncertainty_fallback(self):
        expected = (
            "Based on the available statutory database, "
            "no definitive compliance conclusion can be drawn."
        )
        assert expected in generator.SYSTEM_PROMPT


# ── Integration: rerank_context (uses real FlashRank model) ────────────


class TestRerankContext:
    def test_returns_at_most_top_n(self, sample_chunks):
        top_n = 3
        result = generator.rerank_context(
            "notice period for rent increase",
            sample_chunks,
            top_n=top_n,
        )
        assert len(result) <= top_n

    def test_empty_chunks_returns_empty(self):
        result = generator.rerank_context("any query", [])
        assert result == []

    def test_adds_rerank_score_field(self, sample_chunks):
        result = generator.rerank_context("notice period", sample_chunks, top_n=3)
        for chunk in result:
            assert "rerank_score" in chunk
            assert isinstance(chunk["rerank_score"], float)
            assert 0.0 <= chunk["rerank_score"] <= 1.0

    def test_descending_score_order(self, sample_chunks):
        result = generator.rerank_context(
            "notice period for rent increase",
            sample_chunks,
            top_n=4,
        )
        scores = [c["rerank_score"] for c in result]
        for i in range(len(scores) - 1):
            assert scores[i] >= scores[i + 1], f"scores not descending at index {i}"


# ── Slow: DeepSeekLLMProvider ──────────────────────────────────────────


class TestDeepSeekLLMProvider:
    def test_raises_without_api_key(self, monkeypatch):
        monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
        with pytest.raises(ValueError, match="DEEPSEEK_API_KEY"):
            generator.DeepSeekLLMProvider()

    @pytest.mark.slow
    @pytest.mark.skipif(
        not os.environ.get("DEEPSEEK_API_KEY"),
        reason="DEEPSEEK_API_KEY not set — skipping LLM integration test",
    )
    def test_generates_non_empty_response(self):
        provider = generator.DeepSeekLLMProvider()
        response = provider.generate(
            system_prompt="You are a helpful assistant. Keep answers brief.",
            user_prompt="What is 2+2?",
        )
        assert isinstance(response, str)
        assert len(response) > 0


# ── E2E: full pipeline ─────────────────────────────────────────────────


@pytest.mark.slow
@pytest.mark.skipif(
    not os.environ.get("DEEPSEEK_API_KEY"),
    reason="DEEPSEEK_API_KEY not set — skipping E2E pipeline test",
)
class TestE2E:
    def test_vic_rent_arrears_pipeline(self, monkeypatch, qdrant_with_data):
        import src.retrieval.vector_store as vs

        monkeypatch.setattr(vs, "QDRANT_PATH", qdrant_with_data["path"])
        monkeypatch.setattr(vs, "COLLECTION_NAME", qdrant_with_data["collection"])

        result = generator.generate_compliance_answer(
            query="Can my landlord evict me for being 10 days behind on rent in VIC?",
            state_filter="VIC",
            top_k_retrieve=5,
        )

        assert "answer" in result
        assert isinstance(result["answer"], str)
        assert len(result["answer"]) > 0

        assert "citation_check" in result
        assert "verified" in result["citation_check"]
        assert "unverified" in result["citation_check"]

        assert len(result["retrieved_chunks"]) > 0
