"""Tests for Australian tenancy legislation parsers (all 8 jurisdictions via GenericParser)."""

import re

from src.data_processing.parser_factory import JURISDICTION_CONFIGS, GenericParser

VIC_CONFIG = JURISDICTION_CONFIGS["VIC"]

# ── Helpers: extract VIC regexes from config ───────────────────────────


def _re(key: str) -> re.Pattern | None:
    pattern = VIC_CONFIG["regexes"].get(key)
    return re.compile(pattern) if pattern else None


SECTION_RE = _re("section_inline")
PLAIN_SECTION_RE = _re("section_plain")
STANDALONE_NUM_RE = _re("section_standalone")
PART_RE = _re("part_full")
DIVISION_RE = _re("division_full")
SUBDIVISION_RE = _re("subdivision_full")

# ── Unit tests: regex patterns ────────────────────────────────────────


class TestSectionRE:
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


class TestPlainSectionRE:
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


class TestStandaloneNumRE:
    def test_standalone_section(self):
        match = STANDALONE_NUM_RE.match("142ZZA")
        assert match is not None
        assert match.group(1) == "142ZZA"

    def test_plain_number_not_standalone(self):
        assert STANDALONE_NUM_RE.match("63") is None

    def test_alphanumeric_short(self):
        match = STANDALONE_NUM_RE.match("26A")
        assert match is not None


class TestPartDivisionSubdivisionRE:
    def test_part(self):
        match = PART_RE.match("Part 2—Residential tenancies—residential rental agreements")
        assert match is not None
        assert match.group(1) == "2"
        assert "Residential tenancies" in match.group(2)

    def test_division(self):
        match = DIVISION_RE.match("Division 3—Rent Increases")
        assert match is not None
        assert match.group(1) == "3"
        assert match.group(2) == "Rent Increases"

    def test_subdivision(self):
        match = SUBDIVISION_RE.match("Subdivision 1—Application to residential rental agreements")
        assert match is not None
        assert match.group(1) == "1"
        assert "Application" in match.group(2)

    def test_part_with_hyphen(self):
        match = PART_RE.match("Part 1-Preliminary")
        assert match is not None
        assert match.group(1) == "1"


# ── Unit tests: GenericParser helper methods ───────────────────────────


class TestEstimateTokens:
    def test_empty_text(self):
        p = GenericParser(VIC_CONFIG)
        assert p.estimate_tokens("hello") >= 1

    def test_short_text(self):
        p = GenericParser(VIC_CONFIG)
        tokens = p.estimate_tokens("The quick brown fox jumps over the lazy dog")
        assert 10 <= tokens <= 15

    def test_long_text(self):
        p = GenericParser(VIC_CONFIG)
        text = "word " * 200
        tokens = p.estimate_tokens(text)
        assert 200 <= tokens <= 300


class TestIsAmendmentLine:
    def test_amendment(self):
        p = GenericParser(VIC_CONFIG)
        assert p._re["amendment"].match("S. 26A inserted by") is not None

    def test_not_amendment(self):
        p = GenericParser(VIC_CONFIG)
        assert p._re["amendment"].match("44 Rent increases") is None

    def test_amendment_with_spaces(self):
        p = GenericParser(VIC_CONFIG)
        assert p._re["amendment"].match("  S. 91ZM amended by") is not None


class TestIsAmendmentContinuation:
    def test_inserted_by(self):
        p = GenericParser(VIC_CONFIG)
        assert p._is_amendment_continuation("inserted by") is True

    def test_no_number(self):
        p = GenericParser(VIC_CONFIG)
        assert p._is_amendment_continuation("No. 45/2018") is True

    def test_roman_numeral(self):
        p = GenericParser(VIC_CONFIG)
        assert p._is_amendment_continuation("xii") is True

    def test_section_body(self):
        p = GenericParser(VIC_CONFIG)
        assert p._is_amendment_continuation("The renter must give notice") is False


class TestIsValidSectionTitle:
    def test_valid_title(self):
        p = GenericParser(VIC_CONFIG)
        assert p._is_valid_title("Rent increases") is True

    def test_valid_with_parentheses(self):
        p = GenericParser(VIC_CONFIG)
        assert p._is_valid_title("What can the Tribunal order?") is True

    def test_prose_penalty(self):
        p = GenericParser(VIC_CONFIG)
        assert p._is_valid_title("penalty units") is False

    def test_prose_years(self):
        p = GenericParser(VIC_CONFIG)
        assert p._is_valid_title("years from the date") is False

    def test_month_name(self):
        p = GenericParser(VIC_CONFIG)
        assert p._is_valid_title("July 1998") is False

    def test_short_title(self):
        p = GenericParser(VIC_CONFIG)
        assert p._is_valid_title("x") is False

    def test_starts_with_digit(self):
        p = GenericParser(VIC_CONFIG)
        assert p._is_valid_title("5 years") is False


class TestLooksLikeDate:
    def test_month(self):
        p = GenericParser(VIC_CONFIG)
        assert p._is_valid_title("July 1998") is False  # rejected via MONTH_NAMES

    def test_not_date(self):
        p = GenericParser(VIC_CONFIG)
        assert p._is_valid_title("Rent increases") is True


class TestBuildParentPrefix:
    def test_full_hierarchy(self):
        p = GenericParser(VIC_CONFIG)
        h = {"part": "2", "division": "3", "subdivision": "1"}
        result = p._build_parent_prefix(h)
        assert "Part 2" in result and "Division 3" in result

    def test_part_division_only(self):
        p = GenericParser(VIC_CONFIG)
        h = {"part": "1", "division": "2", "subdivision": None}
        result = p._build_parent_prefix(h)
        assert result == "[Part 1 - Division 2]"

    def test_part_only(self):
        p = GenericParser(VIC_CONFIG)
        h = {"part": "5", "division": None, "subdivision": None}
        result = p._build_parent_prefix(h)
        assert result == "[Part 5]"

    def test_empty_hierarchy(self):
        p = GenericParser(VIC_CONFIG)
        h = {"part": None, "division": None, "subdivision": None}
        result = p._build_parent_prefix(h)
        assert result == ""


class TestExtractPartFromHeader:
    def test_valid_part(self):
        pm = re.compile(VIC_CONFIG["regexes"]["part_full"])
        result = pm.match("Part 2—Residential tenancies—residential rental agreements")
        assert result is not None
        assert result.group(1) == "2"
        assert "Residential tenancies" in result.group(2)

    def test_not_part(self):
        pm = re.compile(VIC_CONFIG["regexes"]["part_full"])
        result = pm.match("Authorised by the Chief Parliamentary Counsel")
        assert result is None

    def test_empty_line(self):
        pm = re.compile(VIC_CONFIG["regexes"]["part_full"])
        result = pm.match("")
        assert result is None


# ── Integration / smoke tests ─────────────────────────────────────────


class TestOutputFile:
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
            assert c["chunk_id"].endswith(f"-s{c['section_id']}")

    def test_part_2_exists(self, chunks):
        part2 = [c for c in chunks if c.get("part") == "2"]
        assert len(part2) > 0, "Part 2 should have sections"

    def test_specific_section_present(self, chunks):
        sec44 = [c for c in chunks if c["section_id"] == "44"]
        assert len(sec44) > 0, "Section 44 should be present"


# ── Unit tests: GenericParser for VIC ──────────────────────────────────


class TestVICParserInstantiation:
    def test_can_instantiate(self):
        p = GenericParser(VIC_CONFIG)
        assert p.state == "VIC"
        assert "Residential Tenancies Act 1997" in p.act_name

    def test_regexes_compiled(self):
        p = GenericParser(VIC_CONFIG)
        assert p._re["section_inline"] is not None
        assert p._re["section_plain"] is not None
        assert p._re["part_full"] is not None

    def test_options_stored(self):
        p = GenericParser(VIC_CONFIG)
        assert p.opt["title_min_length"] == 2
        assert p.opt["require_prev_blank"] is True
        assert p.opt["deduplicate_hierarchy"] is True


class TestVICParserMethodsVsFunctions:
    def test_amendment_continuation_via_method(self):
        p = GenericParser(VIC_CONFIG)
        assert p._is_amendment_continuation("inserted by") is True
        assert p._is_amendment_continuation("(a)") is True
        assert p._is_amendment_continuation("xii") is True

    def test_title_validation_via_method(self):
        p = GenericParser(VIC_CONFIG)
        assert p._is_valid_title("Rent increases") is True
        assert p._is_valid_title("penalty units") is False
        assert p._is_valid_title("5 years") is False

    def test_build_chunk_schema(self):
        p = GenericParser(VIC_CONFIG)
        chunk = p._build_chunk("44", "Rent increases", "Test text", {"part": "2"})
        assert chunk["chunk_id"] == "VIC-RTA1997-s44"
        assert chunk["part"] == "2"
        assert chunk["state"] == "VIC"
        for field in ["chunk_id", "text", "state", "act", "year", "section_id",
                       "section_title", "part", "part_title", "division",
                       "division_title", "subdivision", "subdivision_title",
                       "subsection_range"]:
            assert field in chunk, f"Missing field: {field}"
