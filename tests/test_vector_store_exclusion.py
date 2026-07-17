"""Tests for instrument-aware part exclusion in hybrid_retrieve.

Proves:
- Act chunks in excluded parts are still filtered.
- Regulation chunks in the same part numbers are now retrievable.
- Standard residential queries are unaffected.
"""

from __future__ import annotations

from src.rag.retrieval.vector_store import DEFAULT_EXCLUDE_PARTS, hybrid_retrieve


def test_reg_rooming_house_part3_included():
    """Regulation chunks in VIC Part 3 (rooming houses) now appear."""
    results = hybrid_retrieve(
        query_text="rooming house prescribed form prohibited terms",
        state_filter={"state": "VIC"},
        top_k=15,
    )
    reg_part3 = [
        r for r in results
        if r.get("instrument_type") == "regulation" and str(r.get("part", "")) == "3"
    ]
    assert len(reg_part3) > 0, "Expected Reg chunks from Part 3 to be retrievable"


def test_act_rooming_house_part3_excluded():
    """Act chunks in VIC Part 3 (rooming houses) remain excluded."""
    results = hybrid_retrieve(
        query_text="rooming house resident rights obligations",
        state_filter={"state": "VIC"},
        top_k=15,
    )
    act_part3 = [
        r for r in results
        if str(r.get("part", "")) == "3"
        and r.get("instrument_type", "act") != "regulation"
    ]
    assert len(act_part3) == 0, "Act chunks from Part 3 should still be excluded"


def test_reg_caravan_park_part4_included():
    """Regulation chunks in VIC Part 4 (caravan parks) now appear."""
    results = hybrid_retrieve(
        query_text="caravan park disclosure information prescribed notice",
        state_filter={"state": "VIC"},
        top_k=15,
    )
    reg_part4 = [
        r for r in results
        if r.get("instrument_type") == "regulation" and str(r.get("part", "")) == "4"
    ]
    assert len(reg_part4) > 0, "Expected Reg chunks from Part 4 to be retrievable"


def test_reg_site_agreement_part4A_included():
    """Regulation chunks in VIC Part 4A (site agreements) now appear."""
    results = hybrid_retrieve(
        query_text="site agreement disclosure prescribed form residential park",
        state_filter={"state": "VIC"},
        top_k=15,
    )
    reg_part4a = [
        r for r in results
        if r.get("instrument_type") == "regulation" and str(r.get("part", "")) == "4A"
    ]
    assert len(reg_part4a) > 0, "Expected Reg chunks from Part 4A to be retrievable"


def test_nsw_reg_social_housing_part7_included():
    """Regulation chunks in NSW Part 7 (social housing) now appear."""
    results = hybrid_retrieve(
        query_text="social housing rent increase notice prescribed form",
        state_filter={"state": "NSW"},
        top_k=15,
    )
    reg_part7 = [
        r for r in results
        if r.get("instrument_type") == "regulation" and str(r.get("part", "")) == "7"
    ]
    assert len(reg_part7) > 0, "Expected Reg chunks from Part 7 to be retrievable"


def test_act_sda_part12A_still_excluded():
    """Act chunks in VIC Part 12A (SDA, no Reg chunks) remain excluded."""
    results = hybrid_retrieve(
        query_text="specialist disability accommodation SDA provider obligations",
        state_filter={"state": "VIC"},
        top_k=15,
    )
    act_part12a = [
        r for r in results
        if str(r.get("part", "")) == "12A"
        and r.get("instrument_type", "act") != "regulation"
    ]
    assert len(act_part12a) == 0, "Act chunks from Part 12A should still be excluded"


def test_standard_residential_act_unaffected():
    """Standard residential Act chunks (Part 2) are unaffected."""
    results = hybrid_retrieve(
        query_text="maximum bond amount rental agreement",
        state_filter={"state": "VIC"},
        top_k=10,
    )
    assert len(results) > 0, "Standard query should still return results"
    excluded_parts = set(DEFAULT_EXCLUDE_PARTS.get("VIC", []))
    for r in results:
        part = str(r.get("part", ""))
        inst = r.get("instrument_type", "act")
        if inst != "regulation" and part in excluded_parts:
            raise AssertionError(f"Act chunk {r['section_id']} from Part {part} not excluded")


def test_include_parts_star_disables_exclusion():
    """include_parts=["*"] disables all exclusions."""
    results = hybrid_retrieve(
        query_text="rooming house resident obligations",
        state_filter={"state": "VIC"},
        top_k=15,
        include_parts=["*"],
    )
    act_part3 = [
        r for r in results
        if str(r.get("part", "")) == "3"
        and r.get("instrument_type", "act") != "regulation"
    ]
    assert len(act_part3) > 0, "include_parts=['*'] should allow Act chunks from Part 3"
