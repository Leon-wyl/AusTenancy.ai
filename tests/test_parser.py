"""Tests for VIC and NSW legislative PDF parsers."""

import re

from src.data_processing.nsw_parser import (
    DIVISION_RE as NSW_DIVISION_RE,
)
from src.data_processing.nsw_parser import (
    PART_RE as NSW_PART_RE,
)
from src.data_processing.nsw_parser import (
    SECTION_ID_RE,
    SECTION_LINE_RE,
    NSWParser,
)
from src.data_processing.nsw_parser import (
    SUBDIVISION_RE as NSW_SUBDIVISION_RE,
)
from src.data_processing.nsw_parser import (
    _is_valid_section_title as _nsw_is_valid_title,
)
from src.data_processing.vic_parser import (
    ACT_NUMBER_RE,
    AMENDMENT_RE,
    AUTHORISED_RE,
    PLAIN_SECTION_RE,
    SECTION_RE,
    STANDALONE_NUM_RE,
    VICParser,
    _is_amendment_continuation,
    _is_valid_section_title,
    _looks_like_date,
)
from src.data_processing.vic_parser import (
    DIVISION_RE as VIC_DIVISION_RE,
)
from src.data_processing.vic_parser import (
    PART_RE as VIC_PART_RE,
)
from src.data_processing.vic_parser import (
    SUBDIVISION_RE as VIC_SUBDIVISION_RE,
)

# ── Unit tests: VIC regex patterns ─────────────────────────────────────


class TestVICSectionRE:
    def test_alphanumeric_section(self):
        match = SECTION_RE.match("91ZM  Non-payment of rent")
        assert match is not None
        assert match.group(1) == "91ZM"
        assert "Non-payment" in match.group(2)

    def test_plain_number_section_not_matched(self):
        assert SECTION_RE.match("1 Purposes") is None

    def test_amendment_line_not_matched(self):
        assert SECTION_RE.match("S. 26A inserted by") is None

    def test_penalty_line_not_matched(self):
        assert SECTION_RE.match("Penalty: 25 penalty units.") is None


class TestVICPlainSectionRE:
    def test_plain_section(self):
        match = PLAIN_SECTION_RE.match("1 Purposes")
        assert match is not None
        assert match.group(1) == "1"
        assert match.group(2) == "Purposes"

    def test_two_digit_section(self):
        match = PLAIN_SECTION_RE.match("44 Rent increases")
        assert match is not None
        assert match.group(1) == "44"
        assert "Rent increases" in match.group(2)

    def test_prose_number_not_matched(self):
        assert PLAIN_SECTION_RE.match("3 days") is None

    def test_penalty_amount_not_matched(self):
        assert PLAIN_SECTION_RE.match("300 penalty units") is None


class TestVICStandaloneNumRE:
    def test_standalone_section(self):
        match = STANDALONE_NUM_RE.match("142ZZA")
        assert match is not None
        assert match.group(1) == "142ZZA"

    def test_plain_number_not_standalone(self):
        assert STANDALONE_NUM_RE.match("63") is None

    def test_alphanumeric_short(self):
        match = STANDALONE_NUM_RE.match("26A")
        assert match is not None


class TestVICPartDivisionSubdivisionRE:
    def test_part(self):
        match = VIC_PART_RE.match("Part 2\u2014Residential tenancies\u2014residential rental agreements")
        assert match is not None
        assert match.group(1) == "2"
        assert "Residential tenancies" in match.group(2)

    def test_division(self):
        match = VIC_DIVISION_RE.match("Division 3\u2014Rent Increases")
        assert match is not None
        assert match.group(1) == "3"
        assert match.group(2) == "Rent Increases"

    def test_subdivision(self):
        match = VIC_SUBDIVISION_RE.match("Subdivision 1\u2014Application to residential rental agreements")
        assert match is not None
        assert match.group(1) == "1"
        assert "Application" in match.group(2)

    def test_part_with_hyphen(self):
        match = VIC_PART_RE.match("Part 1-Preliminary")
        assert match is not None
        assert match.group(1) == "1"


# ── Unit tests: NSW regex patterns ─────────────────────────────────────


class TestNSWSectionIDRE:
    def test_standalone_number(self):
        match = SECTION_ID_RE.match("44")
        assert match is not None
        assert match.group(1) == "44"

    def test_alphanumeric(self):
        match = SECTION_ID_RE.match("26A")
        assert match is not None
        assert match.group(1) == "26A"

    def test_parenthetical_not_matched(self):
        assert SECTION_ID_RE.match("(a)") is None


class TestNSWSectionLineRE:
    def test_section_line(self):
        match = SECTION_LINE_RE.match("44 Rent increases")
        assert match is not None
        assert match.group(1) == "44"
        assert match.group(2) == "Rent increases"

    def test_section_with_parenthetical_title(self):
        match = SECTION_LINE_RE.match("15 Application of Act")
        assert match is not None
        assert match.group(1) == "15"

    def test_prose_not_matched(self):
        assert SECTION_LINE_RE.match("14 days notice") is None


class TestNSWPartDivisionSubdivisionRE:
    def test_part(self):
        match = NSW_PART_RE.match("Part 2 Residential tenancy agreements")
        assert match is not None
        assert match.group(1) == "2"

    def test_division(self):
        match = NSW_DIVISION_RE.match("Division 3 Rent")
        assert match is not None
        assert match.group(1) == "3"
        assert match.group(2) == "Rent"

    def test_subdivision(self):
        match = NSW_SUBDIVISION_RE.match("Subdivision 1 General")
        assert match is not None
        assert match.group(1) == "1"
        assert match.group(2) == "General"


# ── Unit tests: VICParser helper methods ───────────────────────────────


class TestVICEstimateTokens:
    def test_empty_text(self):
        p = VICParser()
        assert p.estimate_tokens("hello") >= 1

    def test_short_text(self):
        p = VICParser()
        tokens = p.estimate_tokens("The quick brown fox jumps over the lazy dog")
        assert 10 <= tokens <= 15

    def test_long_text(self):
        p = VICParser()
        text = "word " * 200
        tokens = p.estimate_tokens(text)
        assert 200 <= tokens <= 300


class TestVICAmendmentLine:
    def test_amendment(self):
        assert AMENDMENT_RE.match("S. 26A inserted by") is not None

    def test_not_amendment(self):
        assert AMENDMENT_RE.match("44 Rent increases") is None

    def test_amendment_with_spaces(self):
        assert AMENDMENT_RE.match("  S. 91ZM amended by") is not None


class TestVICAmendmentContinuation:
    def test_inserted_by(self):
        assert _is_amendment_continuation("inserted by") is True

    def test_no_number(self):
        assert _is_amendment_continuation("No. 45/2018") is True

    def test_roman_numeral(self):
        assert _is_amendment_continuation("xii") is True

    def test_section_body(self):
        assert _is_amendment_continuation("The renter must give notice") is False


class TestVICValidSectionTitle:
    def test_valid_title(self):
        assert _is_valid_section_title("Rent increases") is True

    def test_valid_with_parentheses(self):
        assert _is_valid_section_title("What can the Tribunal order?") is True

    def test_prose_penalty(self):
        assert _is_valid_section_title("penalty units") is False

    def test_prose_years(self):
        assert _is_valid_section_title("years from the date") is False

    def test_month_name(self):
        assert _is_valid_section_title("July 1998") is False

    def test_short_title(self):
        assert _is_valid_section_title("x") is False

    def test_starts_with_digit(self):
        assert _is_valid_section_title("5 years") is False


class TestVICLooksLikeDate:
    def test_month(self):
        assert _looks_like_date("July 1998") is True

    def test_not_date(self):
        assert _looks_like_date("Rent increases") is False


# ── Unit tests: NSWParser helper methods ───────────────────────────────


class TestNSWEstimateTokens:
    def test_short_text(self):
        p = NSWParser()
        tokens = p.estimate_tokens("The quick brown fox jumps over the lazy dog")
        assert 10 <= tokens <= 15


class TestNSWValidSectionTitle:
    def test_valid_title(self):
        assert _nsw_is_valid_title("Rent increases") is True

    def test_repealed(self):
        assert _nsw_is_valid_title("(Repealed)") is True

    def test_short_title(self):
        assert _nsw_is_valid_title("Re") is False

    def test_blocklist(self):
        assert _nsw_is_valid_title("Note") is False

    def test_parenthetical_non_repealed(self):
        assert _nsw_is_valid_title("(something)") is False


# ── Unit tests: Build parent prefix ─────────────────────────────────────


class TestVICBuildParentPrefix:
    def test_full_hierarchy(self):
        p = VICParser()
        h = {"part": "2", "division": "3", "subdivision": "1"}
        result = p._build_parent_prefix(h)
        assert "Part 2" in result and "Division 3" in result

    def test_part_division_only(self):
        p = VICParser()
        h = {"part": "1", "division": "2", "subdivision": None}
        result = p._build_parent_prefix(h)
        assert result == "[Part 1 - Division 2]"

    def test_part_only(self):
        p = VICParser()
        h = {"part": "5", "division": None, "subdivision": None}
        result = p._build_parent_prefix(h)
        assert result == "[Part 5]"

    def test_empty_hierarchy(self):
        p = VICParser()
        h = {"part": None, "division": None, "subdivision": None}
        result = p._build_parent_prefix(h)
        assert result == ""


class TestNSWBuildParentPrefix:
    def test_part_division(self):
        p = NSWParser()
        h = {"part": "3", "division": "2", "subdivision": None}
        result = p._build_parent_prefix(h)
        assert result == "[Part 3 - Division 2]"


# ── Unit tests: VIC header pattern matching ─────────────────────────────


class TestVICExtractPartFromHeader:
    def test_valid_part(self):
        result = VIC_PART_RE.match("Part 2\u2014Residential tenancies\u2014residential rental agreements")
        assert result is not None
        assert result.group(1) == "2"
        assert "Residential tenancies" in result.group(2)

    def test_not_part(self):
        result = VIC_PART_RE.match("Authorised by the Chief Parliamentary Counsel")
        assert result is None

    def test_empty_line(self):
        result = VIC_PART_RE.match("")
        assert result is None


# ── Parser instantiation tests ──────────────────────────────────────────


class TestVICParserInstantiation:
    def test_can_instantiate(self):
        p = VICParser()
        assert p.state == "VIC"
        assert "Residential Tenancies Act 1997" in p.act_name

    def test_build_chunk_schema(self):
        p = VICParser()
        chunk = p._build_chunk("44", "Rent increases", "Test text", {"part": "2"})
        assert chunk["chunk_id"] == "VIC-RTA1997-s44"
        assert chunk["part"] == "2"
        assert chunk["state"] == "VIC"
        for field in [
            "chunk_id", "text", "state", "act", "year", "section_id",
            "section_title", "part", "part_title", "division",
            "division_title", "subdivision", "subdivision_title",
            "subsection_range",
        ]:
            assert field in chunk, f"Missing field: {field}"


class TestNSWParserInstantiation:
    def test_can_instantiate(self):
        p = NSWParser()
        assert p.state == "NSW"
        assert "Residential Tenancies Act 2010" in p.act_name

    def test_build_chunk_schema(self):
        p = NSWParser()
        chunk = p._build_chunk("44", "Rent increases", "Test text", {"part": "3"})
        assert chunk["chunk_id"] == "NSW-RTA2010-s44"
        assert chunk["part"] == "3"
        assert chunk["state"] == "NSW"
        for field in [
            "chunk_id", "text", "state", "act", "year", "section_id",
            "section_title", "part", "part_title", "division",
            "division_title", "subdivision", "subdivision_title",
            "subsection_range",
        ]:
            assert field in chunk, f"Missing field: {field}"


# ── Integration / smoke tests ──────────────────────────────────────────


class TestVICOutputFile:
    def test_is_valid_json_array(self, chunks):
        assert isinstance(chunks, list)

    def test_minimum_chunks(self, chunks):
        assert len(chunks) >= 900

    def test_unique_section_ids(self, chunks):
        ids = {c["section_id"] for c in chunks}
        assert len(ids) >= 900

    def test_no_empty_chunks(self, chunks):
        for c in chunks:
            assert c["text"].strip(), f"Empty chunk: {c['chunk_id']}"

    def test_all_chunks_have_state(self, chunks):
        for c in chunks:
            assert c["state"] == "VIC"

    def test_section_id_consistency(self, chunks):
        for c in chunks:
            assert c["section_id"], f"Missing section_id in chunk {c['chunk_id']}"
            assert f"-s{c['section_id']}" in c["chunk_id"]

    def test_part_2_exists(self, chunks):
        part2 = [c for c in chunks if c.get("part") == "2"]
        assert len(part2) > 0, "Part 2 should have sections"

    def test_specific_section_present(self, chunks):
        sec44 = [c for c in chunks if c["section_id"] == "44"]
        assert len(sec44) > 0, "Section 44 should be present"


# ── Chunk integrity regression tests ───────────────────────────────────


def _load_chunks(name):
    import json
    from pathlib import Path

    path = Path("data/processed") / name
    if not path.exists():
        import pytest

        pytest.skip(f"Output file not found: {name}")
    with open(path, encoding="utf-8") as f:
        return json.load(f)


class TestChunkIdUniqueness:
    def test_vic_chunk_ids_unique(self):
        chunks = _load_chunks("vic_chunks.json")
        ids = [c["chunk_id"] for c in chunks]
        assert len(ids) == len(set(ids)), "Duplicate chunk_id in vic_chunks.json"

    def test_nsw_chunk_ids_unique(self):
        chunks = _load_chunks("nsw_chunks.json")
        ids = [c["chunk_id"] for c in chunks]
        assert len(ids) == len(set(ids)), "Duplicate chunk_id in nsw_chunks.json"

    def test_base_section_id_preserved_in_chunk_id(self):
        for name in ("vic_chunks.json", "nsw_chunks.json"):
            for c in _load_chunks(name):
                assert f"-s{c['section_id']}" in c["chunk_id"]


class TestNSWDefinitionParse:
    def test_no_year_like_section_ids(self):
        for c in _load_chunks("nsw_chunks.json"):
            m = re.match(r"\d+", c["section_id"])
            if m:
                assert int(m.group()) <= 999, (
                    f"Implausible section_id {c['section_id']} in {c['chunk_id']}"
                )

    def test_part7_definitions_body_captured(self):
        matches = [
            c
            for c in _load_chunks("nsw_chunks.json")
            if c["section_id"] == "22" and c.get("part") == "7"
        ]
        assert matches, "Part 7 s22 Definitions chunk missing"
        text = matches[0]["text"]
        assert len(text) > 100, "Part 7 s22 Definitions body appears truncated"
        assert "means" in text, "Part 7 s22 should contain defined terms"

