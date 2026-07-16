"""Unit tests for citation formatting, extraction, verification, and guard.

Tests cover both Act and Regulation citation formats following the
canonical label convention:
- [STATE RTA YEAR Sec SECTION]
- [STATE REG YEAR Reg REGULATION]
- [STATE REG YEAR Sch SCHEDULE]
- [STATE REG YEAR Sch SCHEDULE Form FORM]
- [STATE REG YEAR Sch SCHEDULE Cl CLAUSE]
"""

import pytest

from src.rag.generation.generator import (
    CITATION_RE,
    _apply_citation_guard,
    _dedupe_preserve_order,
    _looks_like_form,
    build_legal_prompt,
    format_citation_label,
    verify_citations,
)

# ── Sample chunks ───────────────────────────────────────────────────────


def _act_chunk(state="VIC", year="1997", section_id="44", **overrides) -> dict:
    c = {
        "state": state,
        "act": f"Residential Tenancies Act {year}",
        "year": year,
        "section_id": section_id,
        "section_title": "Rent increases",
        "text": "A residential rental provider must give at least 90 days notice.",
    }
    c.update(overrides)
    return c


def _reg_chunk(
    state="VIC",
    year="2021",
    section_id="21",
    schedule=None,
    schedule_title=None,
    **overrides,
) -> dict:
    c = {
        "state": state,
        "act": f"Residential Tenancies Regulations {year}",
        "year": year,
        "section_id": section_id,
        "section_title": "Form of notice of rent increase",
        "instrument_type": "regulation",
        "text": "The prescribed form of notice of rent increase is Form 5 in Schedule 1.",
    }
    if schedule:
        c["schedule"] = schedule
    if schedule_title:
        c["schedule_title"] = schedule_title
    c.update(overrides)
    return c


# ── format_citation_label ───────────────────────────────────────────────


class TestFormatActCitations:
    def test_format_act_citation(self):
        label = format_citation_label(_act_chunk())
        assert label == "[VIC RTA 1997 Sec 44]"

    def test_format_act_with_subsection(self):
        label = format_citation_label(_act_chunk(section_id="44(1)"))
        assert label == "[VIC RTA 1997 Sec 44(1)]"

    def test_format_act_nsw(self):
        label = format_citation_label(_act_chunk(state="NSW", year="2010", section_id="154D"))
        assert label == "[NSW RTA 2010 Sec 154D]"

    def test_format_act_alphabetic_section(self):
        label = format_citation_label(_act_chunk(section_id="91ZM"))
        assert label == "[VIC RTA 1997 Sec 91ZM]"


class TestFormatRegCitations:
    def test_format_reg_normal(self):
        label = format_citation_label(_reg_chunk())
        assert label == "[VIC REG 2021 Reg 21]"

    def test_format_reg_nsw(self):
        label = format_citation_label(_reg_chunk(state="NSW", year="2019", section_id="7"))
        assert label == "[NSW REG 2019 Reg 7]"


class TestFormatScheduleCitations:
    def test_format_schedule_only(self):
        label = format_citation_label(
            _reg_chunk(state="NSW", year="2019", section_id="sch2", schedule="Schedule 2")
        )
        assert label == "[NSW REG 2019 Sch 2]"

    def test_format_schedule_form(self):
        label = format_citation_label(
            _reg_chunk(
                section_id="sch1-form5",
                schedule="Schedule 1",
                schedule_title="Forms",
            )
        )
        assert label == "[VIC REG 2021 Sch 1 Form 5]"

    def test_format_schedule_clause(self):
        label = format_citation_label(
            _reg_chunk(state="NSW", year="2019", section_id="sch4-1", schedule="Schedule 4")
        )
        assert label == "[NSW REG 2019 Sch 4 Cl 1]"


class TestFormatFallbacks:
    def test_format_uses_provision_id_fallback(self):
        chunk = _act_chunk()
        del chunk["section_id"]
        chunk["provision_id"] = "99"
        label = format_citation_label(chunk)
        assert "99" in label

    def test_format_state_jurisdiction_fallback(self):
        chunk = _act_chunk()
        del chunk["state"]
        chunk["jurisdiction"] = "TAS"
        label = format_citation_label(chunk)
        assert label.startswith("[TAS ")

    def test_format_year_instrument_year_fallback(self):
        chunk = _act_chunk()
        del chunk["year"]
        chunk["instrument_year"] = "2008"
        label = format_citation_label(chunk)
        assert "2008" in label


class TestHelperFunctions:
    def test_looks_like_form_true(self):
        assert _looks_like_form("form5") is True
        assert _looks_like_form("Form10") is True

    def test_looks_like_form_false(self):
        assert _looks_like_form("formality") is False
        assert _looks_like_form("5form") is False
        assert _looks_like_form("") is False

    def test_dedupe_preserve_order(self):
        items = ["a", "b", "a", "c", "b", "d"]
        result = _dedupe_preserve_order(items)
        assert result == ["a", "b", "c", "d"]

    def test_dedupe_empty(self):
        assert _dedupe_preserve_order([]) == []


# ── CITATION_RE ─────────────────────────────────────────────────────────


class TestCitationRe:
    def test_matches_act_citation(self):
        assert CITATION_RE.findall("[VIC RTA 1997 Sec 44(1)]") == ["[VIC RTA 1997 Sec 44(1)]"]

    def test_matches_reg_citation(self):
        assert CITATION_RE.findall("[VIC REG 2021 Reg 21]") == ["[VIC REG 2021 Reg 21]"]

    def test_matches_schedule_form(self):
        assert CITATION_RE.findall("[VIC REG 2021 Sch 1 Form 5]") == ["[VIC REG 2021 Sch 1 Form 5]"]

    def test_matches_schedule_clause(self):
        assert CITATION_RE.findall("[NSW REG 2019 Sch 4 Cl 1]") == ["[NSW REG 2019 Sch 4 Cl 1]"]

    def test_finds_multiple_citations(self):
        text = "See [VIC RTA 1997 Sec 44] and [VIC RTA 1997 Sec 91ZM]."
        matches = CITATION_RE.findall(text)
        assert len(matches) == 2

    def test_does_not_match_non_citations(self):
        result = CITATION_RE.findall("No citation here.")
        assert result == []


# ── verify_citations ────────────────────────────────────────────────────


class TestVerifyCitations:
    def test_verify_act_citation(self):
        answer = "The landlord must give 90 days notice [VIC RTA 1997 Sec 44]."
        result = verify_citations(answer, [_act_chunk()])
        assert "[VIC RTA 1997 Sec 44]" in result["verified"]
        assert result["unverified"] == []

    def test_verify_reg_citation(self):
        answer = "The prescribed form is Form 5 [VIC REG 2021 Reg 21]."
        result = verify_citations(answer, [_reg_chunk()])
        assert "[VIC REG 2021 Reg 21]" in result["verified"]
        assert result["unverified"] == []

    def test_unverified_reg_citation(self):
        answer = "See [VIC REG 2021 Reg 999]."
        chunk = _reg_chunk(section_id="21")
        result = verify_citations(answer, [chunk])
        assert "[VIC REG 2021 Reg 999]" in result["unverified"]
        assert result["verified"] == []

    def test_section_id_collision_no_cross_match(self):
        """Reg §21 must not match Act chunk §21 (different instrument labels)."""
        answer = "See [VIC REG 2021 Reg 21]."
        result = verify_citations(answer, [_act_chunk(section_id="21")])
        assert "[VIC REG 2021 Reg 21]" in result["unverified"]
        assert "[VIC RTA 1997 Sec 21]" not in result["verified"]

    def test_mixed_act_reg_verification(self):
        answer = "Act rule [VIC RTA 1997 Sec 44] and Reg rule [VIC REG 2021 Reg 21]."
        chunks = [_act_chunk(), _reg_chunk()]
        result = verify_citations(answer, chunks)
        assert "[VIC RTA 1997 Sec 44]" in result["verified"]
        assert "[VIC REG 2021 Reg 21]" in result["verified"]
        assert result["unverified"] == []

    def test_deduplication(self):
        answer = (
            "See [VIC RTA 1997 Sec 44]. Also [VIC RTA 1997 Sec 44] again. "
            "And [VIC RTA 1997 Sec 44] once more."
        )
        result = verify_citations(answer, [_act_chunk()])
        assert result["verified"] == ["[VIC RTA 1997 Sec 44]"]
        assert result["unverified"] == []


# ── _apply_citation_guard ───────────────────────────────────────────────


class TestCitationGuard:
    def test_guard_removes_invalid_reg_citation(self):
        answer = "The rule is [VIC REG 2021 Reg 21]. An invalid one [VIC REG 2021 Reg 999]."
        citation_check = {
            "verified": ["[VIC REG 2021 Reg 21]"],
            "unverified": ["[VIC REG 2021 Reg 999]"],
        }
        guarded = _apply_citation_guard(answer, citation_check)
        assert "[VIC REG 2021 Reg 21]" in guarded
        assert "[VIC REG 2021 Reg 999]" not in guarded

    def test_guard_preserves_answer_when_no_unverified(self):
        answer = "The rule is [VIC RTA 1997 Sec 44]."
        citation_check = {"verified": ["[VIC RTA 1997 Sec 44]"], "unverified": []}
        guarded = _apply_citation_guard(answer, citation_check)
        assert guarded == answer


# ── build_legal_prompt ──────────────────────────────────────────────────


class TestBuildLegalPrompt:
    def test_whitelist_includes_act_label(self):
        prompt = build_legal_prompt("test query", [_act_chunk()])
        assert "[VIC RTA 1997 Sec 44]" in prompt

    def test_whitelist_includes_reg_label(self):
        prompt = build_legal_prompt("test query", [_reg_chunk()])
        assert "[VIC REG 2021 Reg 21]" in prompt

    def test_whitelist_deduplicates(self):
        prompt = build_legal_prompt("test query", [_act_chunk(), _act_chunk()])
        # The whitelist section should contain exactly 1 entry for the deduplicated label
        whitelist_section = prompt.split("CITATION WHITELIST")[1]
        assert whitelist_section.count("[VIC RTA 1997 Sec 44]") == 1

    def test_context_header_uses_correct_label_for_reg(self):
        prompt = build_legal_prompt("test query", [_reg_chunk()])
        assert "VIC REG 2021 Reg 21" in prompt
