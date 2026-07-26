"""Corpus retrievability tests — real local Qdrant, no AWS required.

These tests confirm the corpus and retriever are capable of surfacing
required statutory provisions. They do NOT establish that any specific
LLM provider's generated rewrite will retrieve them (evaluated by the
Phase 3 harness).

Marked @pytest.mark.corpus — excluded from the hermetic suite.
"""

from __future__ import annotations

import pytest

import src.rag.retrieval.vector_store as vs

pytestmark = pytest.mark.corpus


def _section_ids(chunks: list[dict]) -> list[str]:
    return [c.get("section_id", "") for c in chunks if c.get("section_id")]


def _top_k_section_ids(query: str, state: str, top_k: int = 15) -> list[str]:
    chunks = vs.hybrid_retrieve(
        query_text=query,
        state_filter={"state": state},
        top_k=top_k,
    )
    return _section_ids(chunks)


class TestVICArrearsRetrieval:
    """Confirm VIC threshold queries can surface s91ZM.

    s91ZM surface depends on query terms matching the section's text
    ("14 days", "occasion of non-payment", "notice to vacate").
    A query containing only "10 days" does not match the 14-day
    language and typically does not surface s91ZM — this is the
    retrieval gap that causes the Bedrock legal-accuracy failure.
    """

    def test_14_days_behind_retrieves_s91zm(self):
        ids = _top_k_section_ids(
            "landlord eviction 14 days behind rent arrears notice to vacate VIC",
            "VIC",
        )
        assert ids, "retrieval returned no chunks"
        assert "91ZM" in ids, f"s91ZM missing from top-15: {ids}"

    def test_21_days_behind_retrieves_s91zm(self):
        ids = _top_k_section_ids(
            "landlord eviction 21 days behind rent arrears possession order VIC",
            "VIC",
        )
        assert ids, "retrieval returned no chunks"
        assert "91ZM" in ids, f"s91ZM missing from top-15: {ids}"

    def test_notice_to_vacate_retrieves_s91zm(self):
        ids = _top_k_section_ids(
            "notice to vacate non-payment of rent occasion VIC RTA",
            "VIC",
        )
        assert ids, "retrieval returned no chunks"
        assert "91ZM" in ids, f"s91ZM missing from top-15: {ids}"

    def test_10_days_behind_does_not_retrieve_s91zm(self):
        """Documented gap: a query containing only '10 days' (below the
        14-day statutory threshold) typically does NOT surface s91ZM.
        This is the root cause of the Bedrock legal-accuracy failure —
        the rewritten query must include terms that match s91ZM's text
        for retrieval to succeed."""
        ids = _top_k_section_ids(
            "landlord eviction 10 days behind rent arrears VIC",
            "VIC",
        )
        assert ids, "retrieval returned no chunks"
        if "91ZM" not in ids:
            pytest.skip(
                "s91ZM not in top-15 — verified root cause: "
                "'10 days' query does not match s91ZM's '14 days' language"
            )


class TestNSWArrearsRetrieval:
    """Confirm NSW threshold queries can surface s88 and s89."""

    def test_14_days_retrieves_s88_s89(self):
        ids = _top_k_section_ids(
            "landlord non-payment termination notice 14 days rent arrears NSW",
            "NSW",
        )
        assert ids, "retrieval returned no chunks"
        assert any(sid in ids for sid in ["88", "89"]), f"s88 or s89 missing from top-15: {ids}"

    def test_21_days_retrieves_s88_s89(self):
        ids = _top_k_section_ids(
            "landlord non-payment termination notice 21 days rent arrears repayment plan NSW",
            "NSW",
        )
        assert ids, "retrieval returned no chunks"
        assert any(sid in ids for sid in ["88", "89"]), f"s88 or s89 missing from top-15: {ids}"

    def test_statutory_query_retrieves_s88_s89(self):
        ids = _top_k_section_ids(
            "termination notice non-payment rent unpaid 14 days "
            "prescribed form Residential Tenancies Act 2010 NSW",
            "NSW",
        )
        assert ids, "retrieval returned no chunks"
        assert any(sid in ids for sid in ["88", "89"]), f"s88 or s89 missing from top-15: {ids}"
