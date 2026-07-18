"""Tests for src.agent.observability — local run summaries and LangSmith config.

Tests must not require DeepSeek, Qdrant, LangSmith, or network access.
"""

import json
import os

import pytest

from src.agent.observability import (
    _extract_question,
    _has_clarification_message,
    _is_regulation_citation,
    compute_citation_stats,
    compute_context_stats,
    configure_langsmith,
    generate_run_summary,
)
from src.rag.generation.generator import format_citation_label

# ── Test fixtures ───────────────────────────────────────────────────────


@pytest.fixture
def act_chunks():
    """Three VIC Act chunks — no instrument_type (legacy)."""
    return [
        {
            "chunk_id": "VIC-RTA1997-s44",
            "text": "44 Rent increases\n(1) A provider must give 90 days notice.",
            "state": "VIC",
            "act": "Residential Tenancies Act 1997",
            "year": "1997",
            "section_id": "44",
            "section_title": "Rent increases",
        },
        {
            "chunk_id": "VIC-RTA1997-s91ZM",
            "text": "91ZM Non-payment of rent\n(1) A provider may give a notice to vacate.",
            "state": "VIC",
            "act": "Residential Tenancies Act 1997",
            "year": "1997",
            "section_id": "91ZM",
            "section_title": "Non-payment of rent",
        },
        {
            "chunk_id": "VIC-RTA1997-s213",
            "text": "213 Compensation for unpaid rent.",
            "state": "VIC",
            "act": "Residential Tenancies Act 1997",
            "year": "1997",
            "section_id": "213",
            "section_title": "Compensation for unpaid rent",
        },
    ]


@pytest.fixture
def reg_chunk():
    """Single VIC Regulation chunk — no schedule so label uses Reg format."""
    return {
        "chunk_id": "VIC-REG2021-Reg21",
        "text": "21 Notice of rent increase. The prescribed form is Form 5.",
        "state": "VIC",
        "act": "Residential Tenancies Regulations 2021",
        "year": "2021",
        "section_id": "21",
        "section_title": "Notice of rent increase",
        "instrument_type": "regulation",
    }


@pytest.fixture
def reg_chunk_2():
    """Second VIC Regulation chunk."""
    return {
        "chunk_id": "VIC-REG2021-Sch1-Form5",
        "text": "Schedule 1 Form 5\nPrescribed form for rent increase notice.",
        "state": "VIC",
        "act": "Residential Tenancies Regulations 2021",
        "year": "2021",
        "section_id": "sch1-form5",
        "section_title": "Form 5",
        "instrument_type": "regulation",
        "schedule": "Schedule 1",
    }


@pytest.fixture
def mixed_chunks(act_chunks, reg_chunk, reg_chunk_2):
    """Three Act + two Regulation chunks."""
    return act_chunks + [reg_chunk, reg_chunk_2]


@pytest.fixture
def answer_with_verified_citations():
    """Answer with two verified Act citations (matching act_chunks)."""
    return (
        "Under the Residential Tenancies Act 1997, a landlord must give "
        "at least 90 days notice of a proposed rent increase [VIC RTA 1997 Sec 44]. "
        "For non-payment of rent, a notice to vacate may be issued "
        "[VIC RTA 1997 Sec 91ZM]."
    )


@pytest.fixture
def answer_with_mixed_citations():
    """Answer with one Act and one Regulation citation."""
    return (
        "A landlord must give at least 90 days notice of a rent increase "
        "[VIC RTA 1997 Sec 44]. The prescribed form is Form 5 "
        "[VIC REG 2021 Reg 21]."
    )


@pytest.fixture
def answer_without_citations():
    """Answer with no citation brackets."""
    return "A landlord may increase rent with proper notice."


@pytest.fixture
def answer_with_unverified_citation():
    """Answer with a citation not in any context chunk."""
    return "The landlord must give notice [VIC RTA 1997 Sec 999] before increasing rent."


# ── State builders ──────────────────────────────────────────────────────


def _make_success_state(answer, contexts, jurisdiction="VIC"):
    return {
        "messages": [{"role": "user", "content": "Can my landlord increase rent?"}],
        "jurisdiction": jurisdiction,
        "tenancy_type": "residential",
        "dispute_category": "",
        "rewritten_queries": [
            "Under VIC RTA 1997, what notice is required for rent increases?",
            "VIC RTA 1997 Sec 44 rent increase notice period statutory",
            "rent increase written notice electronic service tenancy",
        ],
        "retrieved_contexts": contexts,
        "answer": answer,
        "retry_count": 0,
        "is_complex_case": False,
        "in_scope": True,
        "citations_verified": True,
        "fallback_reason": "",
        "citation_errors": [],
    }


def _make_fallback_state():
    return {
        "messages": [{"role": "user", "content": "recipe for cookies"}],
        "jurisdiction": "",
        "tenancy_type": "",
        "dispute_category": "",
        "rewritten_queries": [],
        "retrieved_contexts": [],
        "answer": "",
        "retry_count": 0,
        "is_complex_case": False,
        "in_scope": False,
        "citations_verified": False,
        "fallback_reason": "out_of_scope",
        "citation_errors": [],
    }


def _make_clarification_state():
    return {
        "messages": [
            {"role": "user", "content": "Can my landlord increase rent?"},
            {
                "role": "assistant",
                "content": (
                    "To provide an accurate legal answer, I need more information. "
                    "Please specify: state (e.g. VIC, NSW)."
                ),
            },
        ],
        "jurisdiction": "",
        "tenancy_type": "",
        "dispute_category": "",
        "rewritten_queries": [],
        "retrieved_contexts": [],
        "answer": "",
        "retry_count": 0,
        "is_complex_case": False,
        "in_scope": True,
        "citations_verified": False,
        "fallback_reason": "",
        "citation_errors": [],
    }


# ── configure_langsmith ─────────────────────────────────────────────────


class TestConfigureLangsmith:
    def test_disabled_returns_false(self, monkeypatch):
        result = configure_langsmith(enable=False)
        assert result is False

    def test_enabled_no_env_returns_false(self, monkeypatch):
        monkeypatch.delenv("LANGCHAIN_TRACING_V2", raising=False)
        monkeypatch.delenv("LANGSMITH_TRACING", raising=False)
        result = configure_langsmith(enable=True)
        assert result is False

    def test_enabled_with_env_returns_true(self, monkeypatch):
        monkeypatch.setenv("LANGCHAIN_TRACING_V2", "true")
        monkeypatch.setenv("LANGCHAIN_API_KEY", "ls__test_key")
        monkeypatch.setenv("LANGCHAIN_PROJECT", "test-project")
        monkeypatch.delenv("LANGSMITH_API_KEY", raising=False)
        monkeypatch.delenv("LANGSMITH_PROJECT", raising=False)

        result = configure_langsmith(enable=True)
        assert result is True
        assert os.environ["LANGCHAIN_TRACING_V2"] == "true"
        assert os.environ["LANGCHAIN_API_KEY"] == "ls__test_key"
        assert os.environ["LANGCHAIN_PROJECT"] == "test-project"

    def test_enabled_legacy_env_names(self, monkeypatch):
        monkeypatch.delenv("LANGCHAIN_TRACING_V2", raising=False)
        monkeypatch.delenv("LANGCHAIN_API_KEY", raising=False)
        monkeypatch.setenv("LANGSMITH_TRACING", "true")
        monkeypatch.setenv("LANGSMITH_API_KEY", "ls__legacy_key")

        result = configure_langsmith(enable=True)
        assert result is True
        assert os.environ["LANGCHAIN_API_KEY"] == "ls__legacy_key"

    def test_enabled_tracing_not_true(self, monkeypatch):
        monkeypatch.setenv("LANGCHAIN_TRACING_V2", "false")
        monkeypatch.setenv("LANGCHAIN_API_KEY", "ls__test_key")

        result = configure_langsmith(enable=True)
        assert result is False

    def test_enabled_missing_api_key(self, monkeypatch):
        monkeypatch.setenv("LANGCHAIN_TRACING_V2", "true")
        monkeypatch.delenv("LANGCHAIN_API_KEY", raising=False)
        monkeypatch.delenv("LANGSMITH_API_KEY", raising=False)

        result = configure_langsmith(enable=True)
        assert result is False


# ── compute_context_stats ───────────────────────────────────────────────


class TestComputeContextStats:
    def test_empty_contexts(self):
        stats = compute_context_stats([])
        assert stats["retrieved_context_count"] == 0
        assert stats["act_context_count"] == 0
        assert stats["regulation_context_count"] == 0
        assert stats["top_retrieved_provisions"] == []

    def test_act_only(self, act_chunks):
        stats = compute_context_stats(act_chunks)
        assert stats["retrieved_context_count"] == 3
        assert stats["act_context_count"] == 3
        assert stats["regulation_context_count"] == 0
        assert len(stats["top_retrieved_provisions"]) == 3

    def test_regulation_only(self, reg_chunk, reg_chunk_2):
        stats = compute_context_stats([reg_chunk, reg_chunk_2])
        assert stats["act_context_count"] == 0
        assert stats["regulation_context_count"] == 2

    def test_mixed_act_reg(self, mixed_chunks):
        stats = compute_context_stats(mixed_chunks)
        assert stats["retrieved_context_count"] == 5
        assert stats["act_context_count"] == 3
        assert stats["regulation_context_count"] == 2

    def test_top_provisions_format(self, mixed_chunks):
        stats = compute_context_stats(mixed_chunks)
        provisions = stats["top_retrieved_provisions"]
        assert len(provisions) == 5

        for p in provisions:
            assert "label" in p
            assert "section_id" in p
            assert "instrument_type" in p
            assert "score" in p
            assert isinstance(p["label"], str)
            assert isinstance(p["section_id"], str)
            assert p["instrument_type"] in ("act", "regulation")

        act_labels = [p["label"] for p in provisions if p["instrument_type"] == "act"]
        assert "[VIC RTA 1997 Sec 44]" in act_labels
        assert "[VIC RTA 1997 Sec 91ZM]" in act_labels

    def test_labels_match_format_citation_label(self, mixed_chunks):
        stats = compute_context_stats(mixed_chunks)
        for provision, chunk in zip(stats["top_retrieved_provisions"], mixed_chunks, strict=True):
            expected = format_citation_label(chunk)
            assert provision["label"] == expected

    def test_legacy_no_instrument_type_treated_as_act(self, act_chunks):
        stats = compute_context_stats(act_chunks)
        for p in stats["top_retrieved_provisions"]:
            assert p["instrument_type"] == "act"


# ── compute_citation_stats ──────────────────────────────────────────────


class TestComputeCitationStats:
    def test_verified_citations(self, answer_with_verified_citations, act_chunks):
        stats = compute_citation_stats(answer_with_verified_citations, act_chunks)
        assert len(stats["verified_citations"]) == 2
        assert "[VIC RTA 1997 Sec 44]" in stats["verified_citations"]
        assert "[VIC RTA 1997 Sec 91ZM]" in stats["verified_citations"]
        assert stats["unverified_citations"] == []
        assert stats["citation_verified_rate"] == 1.0

    def test_no_citations_rate_none(self, answer_without_citations, act_chunks):
        stats = compute_citation_stats(answer_without_citations, act_chunks)
        assert stats["verified_citations"] == []
        assert stats["unverified_citations"] == []
        assert stats["citation_verified_rate"] is None

    def test_unverified_citation(self, answer_with_unverified_citation, act_chunks):
        stats = compute_citation_stats(answer_with_unverified_citation, act_chunks)
        assert stats["verified_citations"] == []
        assert len(stats["unverified_citations"]) == 1
        assert stats["citation_verified_rate"] == 0.0

    def test_act_vs_reg_split(self, answer_with_mixed_citations, act_chunks, reg_chunk):
        contexts = act_chunks[:1] + [reg_chunk]
        stats = compute_citation_stats(answer_with_mixed_citations, contexts)
        assert len(stats["verified_act_citations"]) == 1
        assert "[VIC RTA 1997 Sec 44]" in stats["verified_act_citations"]
        assert len(stats["verified_regulation_citations"]) == 1
        assert "[VIC REG 2021 Reg 21]" in stats["verified_regulation_citations"]
        assert stats["verified_act_citation_count"] == 1
        assert stats["verified_regulation_citation_count"] == 1
        assert stats["citation_verified_rate"] == 1.0

    def test_counts_match_lists(self, answer_with_verified_citations, act_chunks):
        stats = compute_citation_stats(answer_with_verified_citations, act_chunks)
        assert stats["verified_act_citation_count"] == len(stats["verified_act_citations"])
        assert stats["verified_regulation_citation_count"] == len(
            stats["verified_regulation_citations"]
        )


# ── _is_regulation_citation ─────────────────────────────────────────────


class TestIsRegulationCitation:
    def test_act_citation(self):
        assert _is_regulation_citation("[VIC RTA 1997 Sec 44]") is False
        assert _is_regulation_citation("[NSW RTA 2010 Sec 44]") is False

    def test_regulation_citation(self):
        assert _is_regulation_citation("[VIC REG 2021 Reg 21]") is True
        assert _is_regulation_citation("[NSW REG 2019 Reg 21]") is True

    def test_schedule_citation(self):
        assert _is_regulation_citation("[VIC REG 2021 Sch 1 Form 5]") is True


# ── _has_clarification_message ──────────────────────────────────────────


class TestHasClarificationMessage:
    def test_has_clarification(self, monkeypatch):
        state = _make_clarification_state()
        assert _has_clarification_message(state) is True

    def test_no_clarification(self):
        state = {
            "messages": [
                {"role": "user", "content": "Can my landlord increase rent?"},
                {"role": "assistant", "content": "Under the RTA, the landlord must..."},
            ],
        }
        assert _has_clarification_message(state) is False

    def test_empty_messages(self):
        assert _has_clarification_message({"messages": []}) is False

    def test_no_messages_key(self):
        assert _has_clarification_message({}) is False


# ── _extract_question ───────────────────────────────────────────────────


class TestExtractQuestion:
    def test_extracts_last_user_message(self):
        state = {
            "messages": [
                {"role": "user", "content": "First question"},
                {"role": "assistant", "content": "Answer"},
                {"role": "user", "content": "Follow up question"},
            ],
        }
        assert _extract_question(state) == "Follow up question"

    def test_filters_human_role(self):
        state = {
            "messages": [
                {"role": "human", "content": "hello"},
            ],
        }
        assert _extract_question(state) == "hello"

    def test_skips_assistant(self):
        state = {
            "messages": [
                {"role": "assistant", "content": "I am a bot"},
            ],
        }
        assert _extract_question(state) == ""

    def test_empty_messages(self):
        assert _extract_question({"messages": []}) == ""

    def test_no_messages_key(self):
        assert _extract_question({}) == ""


# ── generate_run_summary ────────────────────────────────────────────────


class TestGenerateRunSummary:
    def test_success_state(self, answer_with_verified_citations, act_chunks):
        state = _make_success_state(answer_with_verified_citations, act_chunks)
        summary = generate_run_summary(state, total_latency_ms=1234.5)

        assert summary["status"] == "success"
        assert summary["question"] == "Can my landlord increase rent?"
        assert summary["jurisdiction"] == "VIC"
        assert summary["in_scope"] is True
        assert summary["fallback_reason"] == ""
        assert len(summary["rewritten_queries"]) == 3
        assert summary["retrieved_context_count"] == 3
        assert summary["act_context_count"] == 3
        assert summary["regulation_context_count"] == 0
        assert summary["answer_length"] > 0
        assert len(summary["verified_citations"]) > 0
        assert summary["citation_verified_rate"] == 1.0
        assert summary["total_latency_ms"] == 1234.5

    def test_fallback_state(self):
        state = _make_fallback_state()
        summary = generate_run_summary(state)

        assert summary["status"] == "fallback"
        assert summary["fallback_reason"] == "out_of_scope"
        assert summary["in_scope"] is False
        assert summary["retrieved_context_count"] == 0
        assert summary["answer_length"] == 0
        assert summary["citation_verified_rate"] is None

    def test_clarification_state(self):
        state = _make_clarification_state()
        summary = generate_run_summary(state)

        assert summary["status"] == "clarification"
        assert summary["jurisdiction"] == ""
        assert summary["fallback_reason"] == ""
        assert summary["in_scope"] is True

    def test_missing_optional_fields_no_crash(self):
        minimal_state = {
            "messages": [{"role": "user", "content": "hello"}],
        }
        summary = generate_run_summary(minimal_state)
        assert summary["status"] == "success"
        assert summary["question"] == "hello"
        assert summary["jurisdiction"] == ""
        assert summary["retrieved_context_count"] == 0
        assert summary["top_retrieved_provisions"] == []
        assert summary["verified_citations"] == []
        assert summary["citation_verified_rate"] is None
        assert summary["total_latency_ms"] is None

    def test_total_latency_none_when_not_provided(self, act_chunks):
        state = _make_success_state("answer", act_chunks)
        summary = generate_run_summary(state)
        assert summary["total_latency_ms"] is None

    def test_json_serializable(self, act_chunks):
        state = _make_success_state("Answer with [VIC RTA 1997 Sec 44] citation.", act_chunks)
        summary = generate_run_summary(state, total_latency_ms=1000.0)
        dumped = json.dumps(summary)
        reloaded = json.loads(dumped)
        assert reloaded["status"] == summary["status"]
        assert reloaded["citation_verified_rate"] == summary["citation_verified_rate"]
        assert reloaded["total_latency_ms"] == summary["total_latency_ms"]

    def test_mixed_context_with_regulation(
        self, answer_with_mixed_citations, act_chunks, reg_chunk
    ):
        contexts = act_chunks[:1] + [reg_chunk]
        state = _make_success_state(answer_with_mixed_citations, contexts)
        summary = generate_run_summary(state)

        assert summary["act_context_count"] == 1
        assert summary["regulation_context_count"] == 1
        assert summary["verified_act_citation_count"] == 1
        assert summary["verified_regulation_citation_count"] == 1

    def test_empty_answer(self, act_chunks):
        state = _make_success_state("", act_chunks)
        summary = generate_run_summary(state)
        assert summary["answer_length"] == 0
        assert summary["verified_citations"] == []
        assert summary["citation_verified_rate"] is None
