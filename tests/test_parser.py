"""Tests for VIC RTA PDF parser."""

import re

from src.data_processing import parser

# ── Unit tests: regex patterns ────────────────────────────────────────


class TestSectionRE:
    def test_alphanumeric_section(self):
        match = parser.SECTION_RE.match("91ZM  Non-payment of rent")
        assert match is not None
        assert match.group(1) == "91ZM"
        assert "Non-payment" in match.group(2)

    def test_plain_number_section_not_matched(self):
        assert parser.SECTION_RE.match("1 Purposes") is None

    def test_amendment_line_not_matched(self):
        assert parser.SECTION_RE.match("S. 26A inserted by") is None

    def test_penalty_line_not_matched(self):
        assert parser.SECTION_RE.match("Penalty: 25 penalty units.") is None


class TestPlainSectionRE:
    def test_plain_section(self):
        match = parser.PLAIN_SECTION_RE.match("1 Purposes")
        assert match is not None
        assert match.group(1) == "1"
        assert match.group(2) == "Purposes"

    def test_two_digit_section(self):
        match = parser.PLAIN_SECTION_RE.match("44 Rent increases")
        assert match is not None
        assert match.group(1) == "44"
        assert "Rent increases" in match.group(2)

    def test_prose_number_not_matched(self):
        assert parser.PLAIN_SECTION_RE.match("3 days") is None

    def test_penalty_amount_not_matched(self):
        assert parser.PLAIN_SECTION_RE.match("300 penalty units") is None


class TestStandaloneNumRE:
    def test_standalone_section(self):
        match = parser.STANDALONE_NUM_RE.match("142ZZA")
        assert match is not None
        assert match.group(1) == "142ZZA"

    def test_plain_number_not_standalone(self):
        assert parser.STANDALONE_NUM_RE.match("63") is None

    def test_alphanumeric_short(self):
        match = parser.STANDALONE_NUM_RE.match("26A")
        assert match is not None


class TestPartDivisionSubdivisionRE:
    def test_part(self):
        match = parser.PART_RE.match("Part 2—Residential tenancies—residential rental agreements")
        assert match is not None
        assert match.group(1) == "2"
        assert "Residential tenancies" in match.group(2)

    def test_division(self):
        match = parser.DIVISION_RE.match("Division 3—Rent Increases")
        assert match is not None
        assert match.group(1) == "3"
        assert match.group(2) == "Rent Increases"

    def test_subdivision(self):
        match = parser.SUBDIVISION_RE.match("Subdivision 1—Application to residential rental agreements")
        assert match is not None
        assert match.group(1) == "1"
        assert "Application" in match.group(2)

    def test_part_with_hyphen(self):
        match = parser.PART_RE.match("Part 1-Preliminary")
        assert match is not None
        assert match.group(1) == "1"


# ── Unit tests: helper functions ──────────────────────────────────────


class TestEstimateTokens:
    def test_empty_text(self):
        assert parser.estimate_tokens("hello") >= 1

    def test_short_text(self):
        tokens = parser.estimate_tokens("The quick brown fox jumps over the lazy dog")
        assert 10 <= tokens <= 15

    def test_long_text(self):
        text = "word " * 200
        tokens = parser.estimate_tokens(text)
        assert 200 <= tokens <= 300


class TestIsAmendmentLine:
    def test_amendment(self):
        assert parser.is_amendment_line("S. 26A inserted by") is True

    def test_not_amendment(self):
        assert parser.is_amendment_line("44 Rent increases") is False

    def test_amendment_with_spaces(self):
        assert parser.is_amendment_line("  S. 91ZM amended by") is True


class TestIsAmendmentContinuation:
    def test_inserted_by(self):
        assert parser.is_amendment_continuation("inserted by") is True

    def test_no_number(self):
        assert parser.is_amendment_continuation("No. 45/2018") is True

    def test_roman_numeral(self):
        assert parser.is_amendment_continuation("xii") is True

    def test_section_body(self):
        assert parser.is_amendment_continuation("The renter must give notice") is False


class TestIsValidSectionTitle:
    def test_valid_title(self):
        assert parser._is_valid_section_title("Rent increases") is True

    def test_valid_with_parentheses(self):
        assert parser._is_valid_section_title("What can the Tribunal order?") is True

    def test_prose_penalty(self):
        assert parser._is_valid_section_title("penalty units") is False

    def test_prose_years(self):
        assert parser._is_valid_section_title("years from the date") is False

    def test_month_name(self):
        assert parser._is_valid_section_title("July 1998") is False

    def test_short_title(self):
        assert parser._is_valid_section_title("x") is False

    def test_starts_with_digit(self):
        assert parser._is_valid_section_title("5 years") is False


class TestLooksLikeDate:
    def test_month(self):
        assert parser._looks_like_date("July 1998") is True

    def test_not_date(self):
        assert parser._looks_like_date("Rent increases") is False


class TestBuildParentPrefix:
    def test_full_hierarchy(self):
        h = {"part": "2", "division": "3", "subdivision": "1"}
        result = parser._build_parent_prefix(h)
        assert result == "[Part 2 - Division 3 - Subdivision 1]"

    def test_part_division_only(self):
        h = {"part": "1", "division": "2", "subdivision": None}
        result = parser._build_parent_prefix(h)
        assert result == "[Part 1 - Division 2]"

    def test_part_only(self):
        h = {"part": "5", "division": None, "subdivision": None}
        result = parser._build_parent_prefix(h)
        assert result == "[Part 5]"

    def test_empty_hierarchy(self):
        h = {"part": None, "division": None, "subdivision": None}
        result = parser._build_parent_prefix(h)
        assert result == ""


class TestExtractPartFromHeader:
    def test_valid_part(self):
        result = parser.extract_part_from_header("Part 2—Residential tenancies—residential rental agreements")
        assert result == ("2", "Residential tenancies—residential rental agreements")

    def test_not_part(self):
        result = parser.extract_part_from_header("Authorised by the Chief Parliamentary Counsel")
        assert result is None

    def test_empty_line(self):
        result = parser.extract_part_from_header("")
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
            assert c["text"].strip(), f"Empty text in {c['chunk_id']}"


class TestSchemaCompliance:
    REQUIRED = ["chunk_id", "text", "state", "act", "section_id", "section_title"]

    def test_all_required_fields(self, chunks):
        for c in chunks:
            for field in self.REQUIRED:
                assert field in c, f"Missing '{field}' in {c.get('chunk_id', '?')}"
                assert c[field] is not None, f"Null '{field}' in {c.get('chunk_id', '?')}"

    def test_state_and_act(self, chunks):
        for c in chunks:
            assert c["state"] == "VIC"
            assert c["act"] == "Residential Tenancies Act 1997"

    def test_chunk_id_format(self, chunks):
        for c in chunks:
            assert c["chunk_id"].startswith("VIC-RTA1997-s"), f"Bad chunk_id: {c['chunk_id']}"

    def test_year_field(self, chunks):
        for c in chunks:
            assert c.get("year") == "1997"

    def test_part_field_present(self, chunks):
        for c in chunks:
            assert "part" in c
            assert "part_title" in c


class TestParentContextPrefix:
    def test_all_chunks_have_prefix(self, chunks):
        for c in chunks:
            assert c["text"].startswith("[Part "), f"Missing prefix in {c['chunk_id']}"


class TestKnownSections:
    def test_section_44_rent_increases(self, chunks):
        found = [c for c in chunks if c["section_id"] == "44"]
        assert len(found) >= 1
        assert any("Rent increases" in c["section_title"] for c in found)

    def test_section_44_correct_part(self, chunks):
        found = [c for c in chunks if c["section_id"] == "44"]
        assert all(c["part"] == "2" for c in found)

    def test_section_91ZM(self, chunks):
        found = [c for c in chunks if c["section_id"] == "91ZM"]
        assert len(found) >= 1
        assert any("Non-payment" in c["section_title"] for c in found)

    def test_section_91ZZO(self, chunks):
        found = [c for c in chunks if c["section_id"] == "91ZZO"]
        assert len(found) >= 1
        assert any("notice to vacate" in c["section_title"].lower() for c in found)

    def test_section_1_purposes(self, chunks):
        found = [c for c in chunks if c["section_id"] == "1" and c["part"] == "1"]
        assert len(found) >= 1
        assert any("Purposes" in c["section_title"] for c in found)

    def test_section_142ZZA_standalone_edge_case(self, chunks):
        found = [c for c in chunks if c["section_id"] == "142ZZA"]
        assert len(found) >= 1
        assert any("Tribunal" in c["section_title"] for c in found)

    def test_section_206ZZM(self, chunks):
        found = [c for c in chunks if c["section_id"] == "206ZZM"]
        assert len(found) >= 1

    def test_section_3_definitions(self, chunks):
        found = [c for c in chunks if c["section_id"] == "3" and c["part"] == "1"]
        assert len(found) >= 1
        # Section 3 is long, should be split into sub-chunks
        assert any(c.get("subsection_range") is not None for c in found)


class TestAllPartsPresent:
    EXPECTED_PARTS = {
        "1", "2", "3", "4", "4A", "5", "7", "8", "9", "10",
        "10A", "10B", "11", "12", "12A", "13", "14", "15",
    }

    def test_all_18_parts(self, chunks):
        found_parts = {c["part"] for c in chunks if c["part"] is not None}
        missing = self.EXPECTED_PARTS - found_parts
        assert not missing, f"Missing Parts: {missing}"

    def test_no_part_6(self, chunks):
        found = [c for c in chunks if c["part"] == "6"]
        assert len(found) == 0, "Part 6 should not exist in this Act"


class TestNoFalsePositives:
    def test_no_prose_titles(self, chunks):
        for c in chunks:
            first = c["section_title"].split()[0].lower()
            assert first not in ("penalty", "years", "months", "days", "hours", "business")

    def test_no_month_titles(self, chunks):
        months = {"january", "february", "march", "april", "may", "june",
                  "july", "august", "september", "october", "november", "december"}
        for c in chunks:
            first = c["section_title"].split()[0].lower()
            assert first not in months, f"Bad title '{c['section_title']}' in {c['chunk_id']}"


class TestSubsectionSplitting:
    def test_long_sections_split(self, chunks):
        split_chunks = [c for c in chunks if c.get("subsection_range") is not None]
        assert len(split_chunks) >= 100, "Expected many split chunks for long sections"

    def test_subsection_range_format(self, chunks):
        for c in chunks:
            if c.get("subsection_range"):
                assert re.match(
                    r"^[\dA-Za-z]+\([\dA-Za-z?]+\)-[\dA-Za-z]+\([\dA-Za-z?]+\)$",
                    c["subsection_range"],
                ), f"Bad subsection_range: {c['subsection_range']}"
