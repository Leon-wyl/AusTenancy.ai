"""Tests for gated Regulation query expansion in _rewrite_queries.

Proves:
- Regulation-intent questions enrich the STATUTORY query with "Regulation" suffix.
- Normal Act-only questions are NOT polluted with Regulation terms.
- _rewrite_queries always returns exactly 3 queries when successful.
- The SEMANTIC and CONCEPT queries are never modified.
- The _MULTI_QUERY_PROMPT includes Regulation-specific guidance and examples.
"""

from __future__ import annotations

from unittest.mock import patch

from src.rag.generation.generator import (
    _has_regulation_intent,
    _MULTI_QUERY_PROMPT,
    _parse_multi_response,
    _REGULATION_EXPANSION_SUFFIX,
    _rewrite_queries,
)

_MOCK_RESPONSE = """SEMANTIC: renter bond lodgement dispute 4 weeks maximum VIC
STATUTORY: rental provider bond amount maximum prescribed form lodgement Regulation VIC
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


@patch("src.rag.generation.generator.get_llm_provider")
def test_enriches_statutory_query_when_intent_detected(_mock_llm):
    """Regulation intent → STATUTORY query includes 'Regulation' (prompt-driven)."""
    instance = _mock_llm.return_value
    instance.generate.return_value = _MOCK_RESPONSE

    queries = _rewrite_queries(
        "Can they charge a bond of $6,300 for my Geelong rental?",
        state_filter="VIC",
    )
    assert len(queries) == 3
    # SEMANTIC unchanged
    assert "Regulation" not in queries[0]
    # STATUTORY contains Regulation (from prompt, not post-processing)
    assert "Regulation" in queries[1]
    # CONCEPT unchanged
    assert "Regulation" not in queries[2]


@patch("src.rag.generation.generator.get_llm_provider")
def test_does_not_enrich_statutory_for_normal_question(_mock_llm):
    """Non-Regulation question → STATUTORY query is NOT modified by post-processing."""
    instance = _mock_llm.return_value
    no_reg_response = """SEMANTIC: landlord raised rent without notice renter VIC
STATUTORY: rental provider rent increase without notice prescribed notice period VIC
CONCEPT: unlawful rent increase notice requirements rental provider obligations VIC"""
    instance.generate.return_value = no_reg_response

    queries = _rewrite_queries(
        "My landlord raised the rent without notice, is that legal?",
        state_filter="VIC",
    )
    assert len(queries) == 3
    assert "Regulation" not in queries[1]


@patch("src.rag.generation.generator.get_llm_provider")
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


@patch("src.rag.generation.generator.get_llm_provider")
def test_semantic_and_concept_unmodified(_mock_llm):
    """Only the STATUTORY (index 1) query ever contains Regulation terms."""
    instance = _mock_llm.return_value
    instance.generate.return_value = _MOCK_RESPONSE

    queries = _rewrite_queries(
        "What are the prescribed forms for a notice to vacate in VIC?",
        state_filter="VIC",
    )
    assert len(queries) == 3
    assert "Regulation" in queries[1]
    assert "Regulation" not in queries[0]
    assert "Regulation" not in queries[2]


def test_prompt_contains_regulation_guidance():
    """The STATUTORY description in the prompt includes Regulation-specific guidance."""
    assert "Regulation-specific" in _MULTI_QUERY_PROMPT
    assert "Regulation is a separate" in _MULTI_QUERY_PROMPT
    assert "prescribed form" in _MULTI_QUERY_PROMPT
    assert "Schedule" in _MULTI_QUERY_PROMPT
    assert "minimum standards" in _MULTI_QUERY_PROMPT


def test_prompt_contains_regulation_example():
    """The prompt includes a NSW condition-report example with Regulation terminology."""
    assert "condition report prescribed form Residential Tenancies Regulation 2019" in _MULTI_QUERY_PROMPT
    assert "Schedule 2" in _MULTI_QUERY_PROMPT


def test_prompt_still_has_vic_lease_break_example():
    """The original VIC lease break example is preserved."""
    assert "notice of intention to vacate early termination fixed term agreement prescribed form VIC" in _MULTI_QUERY_PROMPT
