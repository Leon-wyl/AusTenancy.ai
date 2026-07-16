"""Unit tests for deterministic citation metrics computation."""

import math

import pytest

from src.rag.evaluation.citation_metrics import (
    CitationMetrics,
    _find_matching_chunks,
    _golden_in_contexts,
    _golden_in_verified,
    _match_golden_ref_to_citation,
    _normalize_cite,
    _parse_golden_ref,
    compute_citation_metrics,
)

# ── Sample chunks ───────────────────────────────────────────────────────


def _act_chunk(state="VIC", year="1997", section_id="44") -> dict:
    return {
        "state": state,
        "year": year,
        "section_id": section_id,
        "section_title": "Rent increases",
        "act": f"Residential Tenancies Act {year}",
        "text": "A residential rental provider must give at least 90 days notice.",
    }


def _reg_chunk(state="VIC", year="2021", section_id="21", schedule=None) -> dict:
    c = {
        "state": state,
        "year": year,
        "section_id": section_id,
        "section_title": "Form of notice",
        "act": f"Residential Tenancies Regulations {year}",
        "instrument_type": "regulation",
        "text": "The prescribed form is Form 5 in Schedule 1.",
    }
    if schedule:
        c["schedule"] = schedule
    return c


# ── Helper tests ────────────────────────────────────────────────────────


class TestNormalizeCite:
    def test_strip_brackets(self):
        assert _normalize_cite("[VIC RTA 1997 Sec 44]") == "VIC RTA 1997 Sec 44"

    def test_strip_leading_zeros(self):
        assert _normalize_cite("[VIC RTA 1997 Sec 044]") == "VIC RTA 1997 Sec 44"

    def test_strip_subsection(self):
        assert _normalize_cite("[VIC RTA 1997 Sec 44(1)]") == "VIC RTA 1997 Sec 44"

    def test_no_change_for_reg(self):
        result = _normalize_cite("[VIC REG 2021 Reg 21]")
        assert result == "VIC REG 2021 Reg 21"


class TestParseGoldenRef:
    def test_reg_ref(self):
        is_reg, sid = _parse_golden_ref("reg:21")
        assert is_reg is True
        assert sid == "21"

    def test_reg_schedule_ref(self):
        is_reg, sid = _parse_golden_ref("reg:sch1-form5")
        assert is_reg is True
        assert sid == "sch1-form5"

    def test_plain_act_ref(self):
        is_reg, sid = _parse_golden_ref("44")
        assert is_reg is False
        assert sid == "44"

    def test_act_with_subsection(self):
        is_reg, sid = _parse_golden_ref("44(1)")
        assert is_reg is False
        assert sid == "44(1)"


class TestFindMatchingChunks:
    def test_find_act_chunk(self):
        chunks = [_act_chunk()]
        found = _find_matching_chunks("44", chunks)
        assert len(found) == 1

    def test_find_reg_chunk(self):
        chunks = [_reg_chunk(), _act_chunk(section_id="21")]
        found = _find_matching_chunks("reg:21", chunks)
        assert len(found) == 1
        assert found[0]["instrument_type"] == "regulation"

    def test_no_cross_instrument(self):
        """reg:21 must NOT match an Act chunk with section_id=21."""
        chunks = [_act_chunk(section_id="21")]
        found = _find_matching_chunks("reg:21", chunks)
        assert len(found) == 0

    def test_subsection_ref_matches_base(self):
        chunks = [_act_chunk(section_id="44")]
        found = _find_matching_chunks("44(1)", chunks)
        assert len(found) == 1


# ── Golden matching tests ───────────────────────────────────────────────


class TestMatchGoldenRefToCitation:
    def test_act_ref_matches_verified(self):
        verified = ["[VIC RTA 1997 Sec 44]"]
        chunks = [_act_chunk()]
        assert _match_golden_ref_to_citation("44", verified, chunks) is True

    def test_reg_ref_matches_verified(self):
        verified = ["[VIC REG 2021 Reg 21]"]
        chunks = [_reg_chunk()]
        assert _match_golden_ref_to_citation("reg:21", verified, chunks) is True

    def test_schedule_ref_matches_verified(self):
        verified = ["[VIC REG 2021 Sch 1 Form 5]"]
        chunks = [_reg_chunk(section_id="sch1-form5", schedule="Schedule 1")]
        assert _match_golden_ref_to_citation("reg:sch1-form5", verified, chunks) is True

    def test_no_cross_instrument_match(self):
        """reg:21 verified list check must not match Act section 21."""
        verified = ["[VIC RTA 1997 Sec 21]"]
        chunks = [_act_chunk(section_id="21")]
        assert _match_golden_ref_to_citation("reg:21", verified, chunks) is False

    def test_via_normalization(self):
        """44(1) golden ref should match [VIC RTA 1997 Sec 44] via normalization."""
        verified = ["[VIC RTA 1997 Sec 44]"]
        chunks = [_act_chunk(section_id="44")]
        assert _match_golden_ref_to_citation("44(1)", verified, chunks) is True

    def test_not_found_in_chunks(self):
        verified = ["[VIC RTA 1997 Sec 44]"]
        chunks = [_act_chunk(section_id="99")]
        assert _match_golden_ref_to_citation("44", verified, chunks) is False


# ── Golden in contexts / verified tests ─────────────────────────────────


class TestGoldenInContexts:
    def test_all_found(self):
        chunks = [_act_chunk(), _reg_chunk()]
        found = _golden_in_contexts(["44", "reg:21"], chunks)
        assert found == ["44", "reg:21"]

    def test_partial(self):
        chunks = [_act_chunk()]
        found = _golden_in_contexts(["44", "reg:21"], chunks)
        assert found == ["44"]

    def test_none_found(self):
        chunks = [_act_chunk()]
        found = _golden_in_contexts(["99", "reg:99"], chunks)
        assert found == []


class TestGoldenInVerified:
    def test_both_cited(self):
        verified = ["[VIC RTA 1997 Sec 44]", "[VIC REG 2021 Reg 21]"]
        chunks = [_act_chunk(), _reg_chunk()]
        cited = _golden_in_verified(["44", "reg:21"], verified, chunks)
        assert "44" in cited
        assert "reg:21" in cited

    def test_none_cited(self):
        verified = ["[VIC RTA 1997 Sec 99]"]
        chunks = [_act_chunk()]
        cited = _golden_in_verified(["44"], verified, chunks)
        assert cited == []


# ── CitationMetrics tests ───────────────────────────────────────────────


class TestCitationMetrics:
    def test_precision_zero_citations(self):
        cm = compute_citation_metrics(0, "No citations.", [], ["reg:21"])
        assert cm.citation_count == 0
        assert math.isnan(cm.citation_precision)

    def test_precision_all_verified(self):
        answer = (
            "Notice is 90 days [VIC RTA 1997 Sec 44]. "
            "Form 5 is required [VIC REG 2021 Reg 21]."
        )
        cm = compute_citation_metrics(
            0, answer, [_act_chunk(), _reg_chunk()], ["44", "reg:21"]
        )
        assert cm.citation_count == 2
        assert cm.verified_count == 2
        assert cm.unverified_count == 0
        assert cm.citation_precision == 1.0

    def test_precision_mixed(self):
        answer = (
            "Valid [VIC RTA 1997 Sec 44] and invalid [VIC RTA 1997 Sec 999]."
        )
        cm = compute_citation_metrics(0, answer, [_act_chunk()], ["44"])
        assert cm.citation_count == 2
        assert cm.verified_count == 1
        assert cm.unverified_count == 1
        assert cm.citation_precision == 0.5

    def test_golden_recall_retrieval_full(self):
        cm = compute_citation_metrics(
            0, "answer", [_act_chunk(), _reg_chunk()], ["44", "reg:21"]
        )
        assert cm.golden_recall_retrieval == 1.0

    def test_golden_recall_retrieval_partial(self):
        cm = compute_citation_metrics(
            0, "answer", [_act_chunk()], ["44", "reg:21"]
        )
        assert cm.golden_recall_retrieval == 0.5

    def test_golden_recall_citation_full(self):
        answer = "See [VIC RTA 1997 Sec 44] and [VIC REG 2021 Reg 21]."
        cm = compute_citation_metrics(
            0, answer, [_act_chunk(), _reg_chunk()], ["44", "reg:21"]
        )
        assert cm.golden_recall_citation == 1.0

    def test_reg_citation_verified_true(self):
        answer = "The form is [VIC REG 2021 Reg 21]."
        cm = compute_citation_metrics(0, answer, [_reg_chunk()], ["reg:21"])
        assert cm.reg_citations_in_answer == 1
        assert cm.reg_citation_verified is True

    def test_reg_citation_verified_false(self):
        answer = "The rule is [VIC RTA 1997 Sec 44]."
        cm = compute_citation_metrics(0, answer, [_act_chunk()], ["44"])
        assert cm.reg_citations_in_answer == 0
        assert cm.reg_citation_verified is False

    def test_golden_missing_tracking(self):
        answer = "See [VIC RTA 1997 Sec 44]."
        cm = compute_citation_metrics(
            0, answer, [_act_chunk(), _reg_chunk()], ["44", "reg:21"]
        )
        assert "reg:21" in cm.golden_in_contexts
        assert "reg:21" in cm.golden_missing_in_citations
        assert "44" in cm.golden_in_verified

    def test_empty_chunks_no_crash(self):
        cm = compute_citation_metrics(0, "No citations.", [], ["44"])
        assert cm.citation_count == 0
        assert cm.golden_recall_retrieval == 0.0

    def test_to_dict_serializable(self):
        from src.rag.evaluation.citation_metrics import cm_to_dict

        cm = CitationMetrics(
            question_idx=0,
            citation_count=2,
            verified_count=2,
            unverified_count=0,
            citation_precision=1.0,
            golden_recall_retrieval=1.0,
            golden_recall_citation=1.0,
            reg_citations_in_answer=1,
            reg_citation_verified=True,
            golden_in_contexts=["reg:21"],
            golden_in_verified=["reg:21"],
            golden_missing_in_contexts=[],
            golden_missing_in_citations=[],
        )
        d = cm_to_dict(cm)
        assert d["golden_in_contexts"] == "reg:21"
        assert d["citation_precision"] == 1.0
