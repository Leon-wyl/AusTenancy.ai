"""Tests for gated Regulation query expansion in _rewrite_queries.

Proves:
- Regulation-intent questions enrich the STATUTORY query with "Regulation" suffix.
- Normal Act-only questions are NOT polluted with Regulation terms.
- _rewrite_queries always returns exactly 3 queries when successful.
- The SEMANTIC and CONCEPT queries are never modified.
"""

from __future__ import annotations

from unittest.mock import patch

from src.rag.generation.generator import (
    _REGULATION_EXPANSION_SUFFIX,
    _has_regulation_intent,
    _parse_multi_response,
    _rewrite_queries,
)

_MOCK_RESPONSE = """SEMANTIC: renter bond lodgement dispute 4 weeks maximum VIC
STATUTORY: rental provider bond amount maximum prescribed form lodgement VIC
CONCEPT: residential bond maximum amount statutory limit rental provider obligation VIC"""


def test_bond_question_triggers_reg_intent():
    """Questions mentioning bond match the Regulation intent keyword list."""
    assert _has_regulation_intent(
        "Can they charge a bond of $6,300 for my rental?"
    )
    assert _has_regulation_intent(
        "The landlord wants a two months bond — is this legal?"
    )


def test_heating_question_triggers_reg_intent():
    """Questions about heating trigger via the 'heating' keyword."""
    assert _has_regulation_intent(
        "There's no heater at all in my rental house."
    )


def test_penalties_trigger_via_penalti_prefix():
    """The 'penalti' substring catches both penalty and penalties."""
    assert _has_regulation_intent("What are the penalties for not lodging a bond?")


def test_condition_report_triggers():
    assert _has_regulation_intent(
        "The agent never gave me a condition report — whose responsibility is it?"
    )


def test_minimum_standards_triggers():
    assert _has_regulation_intent(
        "Does my landlord have to meet minimum standards for heating?"
    )


def test_generic_rent_increase_does_not_trigger():
    """Simple rent increase questions with no Regulation-specific keywords don't trigger."""
    assert not _has_regulation_intent(
        "My landlord raised the rent without notice, is that legal in VIC?"
    )
    assert not _has_regulation_intent(
        "The landlord gave me 30 days notice to evict me, is this valid?"
    )
    assert not _has_regulation_intent(
        "Can the landlord enter my apartment without my permission?"
    )


def test_lease_termination_does_not_trigger():
    """Standard lease termination questions don't trigger by default."""
    # "notice" alone does not match "notice to vacate" (no "to vacate")
    assert not _has_regulation_intent(
        "The landlord sent a termination notice — is 30 days enough?"
    )


def test_parse_multi_response_returns_labels():
    """_parse_multi_response correctly extracts the 3 labeled queries."""
    queries = _parse_multi_response(_MOCK_RESPONSE)
    assert len(queries) == 3
    assert queries[0].startswith("renter bond lodgement")
    assert queries[1].startswith("rental provider bond amount maximum")
    assert queries[2].startswith("residential bond maximum amount")


@patch("src.rag.generation.generator.DeepSeekLLMProvider")
def test_enriches_statutory_query_when_intent_detected(_mock_llm):
    """Regulation intent → STATUTORY query gets ' Regulation' appended."""
    instance = _mock_llm.return_value
    instance.generate.return_value = _MOCK_RESPONSE

    queries = _rewrite_queries(
        "Can they charge a bond of $6,300 for my Geelong rental?",
        state_filter="VIC",
    )
    assert len(queries) == 3
    # SEMANTIC unchanged
    assert _REGULATION_EXPANSION_SUFFIX not in queries[0]
    # STATUTORY enriched
    assert queries[1].rstrip().endswith(_REGULATION_EXPANSION_SUFFIX)
    # CONCEPT unchanged
    assert _REGULATION_EXPANSION_SUFFIX not in queries[2]


@patch("src.rag.generation.generator.DeepSeekLLMProvider")
def test_does_not_enrich_statutory_for_normal_question(_mock_llm):
    """Non-Regulation question → STATUTORY query is NOT modified."""
    instance = _mock_llm.return_value
    instance.generate.return_value = _MOCK_RESPONSE

    queries = _rewrite_queries(
        "My landlord raised the rent without notice, is that legal?",
        state_filter="VIC",
    )
    assert len(queries) == 3
    assert _REGULATION_EXPANSION_SUFFIX not in queries[1]


@patch("src.rag.generation.generator.DeepSeekLLMProvider")
def test_no_double_expansion(_mock_llm):
    """If STATUTORY query already contains 'Regulation', don't append again."""
    instance = _mock_llm.return_value
    response_with_reg = """SEMANTIC: renter bond dispute VIC
STATUTORY: rental provider bond lodgement Regulation prescribed form VIC
CONCEPT: bond maximum statutory limit VIC"""
    instance.generate.return_value = response_with_reg

    queries = _rewrite_queries(
        "Can they charge a bond of $6,300 for my Geelong rental?",
        state_filter="VIC",
    )
    assert len(queries) == 3
    # Count: should appear exactly once (from the LLM, not doubled)
    assert queries[1].count("Regulation") == 1


@patch("src.rag.generation.generator.DeepSeekLLMProvider")
def test_semantic_and_concept_unmodified(_mock_llm):
    """Only the STATUTORY (index 1) query is ever modified."""
    instance = _mock_llm.return_value
    instance.generate.return_value = _MOCK_RESPONSE

    queries = _rewrite_queries(
        "What are the prescribed forms for a notice to vacate in VIC?",
        state_filter="VIC",
    )
    assert len(queries) == 3
    assert _REGULATION_EXPANSION_SUFFIX in queries[1]
    assert _REGULATION_EXPANSION_SUFFIX not in queries[0]
    assert _REGULATION_EXPANSION_SUFFIX not in queries[2]
