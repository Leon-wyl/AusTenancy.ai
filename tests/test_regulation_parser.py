"""Tests for VIC and NSW regulation PDF parsers."""

import json
from pathlib import Path

import pytest

from src.data_processing.nsw_regulation_parser import (
    CLAUSE_ID_RE,
    CLAUSE_LINE_RE,
    DIVISION_RE,
    PART_RE,
    NSWRegulationParser,
    _is_valid_clause_title,
)
from src.data_processing.nsw_regulation_parser import (
    SCHEDULE_RE as NSW_SCHEDULE_RE,
)
from src.data_processing.vic_regulation_parser import (
    AUTHORISED_LINE,
    KNOWN_RUNNING_HEADERS,
    PREAMBLE_MARKER,
    REG_RE,
    SCHEDULE_HEADER_RE,
    VICRegulationParser,
)

# ── Unit tests: VIC regulation regex patterns ───────────────────────────


class TestVICRegulationRegex:
    def test_reg_re_matches_simple(self):
        match = REG_RE.match("1 Objective")
        assert match is not None
        assert match.group(1) == "1"
        assert match.group(2) == "Objective"

    def test_reg_re_matches_multi_digit(self):
        match = REG_RE.match("5 Definitions")
        assert match is not None
        assert match.group(1) == "5"
        assert match.group(2) == "Definitions"

    def test_reg_re_handles_em_dash(self):
        match = REG_RE.match("91 Form 23\u2014disposal")
        assert match is not None
        assert match.group(1) == "91"
        assert "Form 23" in match.group(2)

    def test_schedule_re_matches_em_dash(self):
        match = SCHEDULE_HEADER_RE.match("Schedule 1\u2014Forms")
        assert match is not None
        assert match.group(1) == "1"
        assert match.group(2) == "Forms"

    def test_schedule_re_matches_hyphen(self):
        match = SCHEDULE_HEADER_RE.match("Schedule 2\u2013More Forms")
        assert match is not None
        assert match.group(1) == "2"

    def test_known_running_headers_contains_endnotes(self):
        assert "Endnotes" in KNOWN_RUNNING_HEADERS

    def test_authorised_line_present(self):
        assert AUTHORISED_LINE == "Authorised by the Chief Parliamentary Counsel"

    def test_preamble_marker_present(self):
        assert PREAMBLE_MARKER == "STATUTORY RULES 2021"


# ── Unit tests: NSW regulation regex patterns ───────────────────────────


class TestNSWRegulationRegex:
    def test_part_re_matches(self):
        match = PART_RE.match("Part 1 Preliminary")
        assert match is not None
        assert match.group(1) == "1"
        assert match.group(2) == "Preliminary"

    def test_division_re_matches(self):
        match = DIVISION_RE.match("Division 2 Savings and transitional provisions")
        assert match is not None
        assert match.group(1) == "2"
        assert "Savings" in match.group(2)

    def test_clause_id_re_matches_alphanumeric(self):
        match = CLAUSE_ID_RE.match("8A")
        assert match is not None
        assert match.group(1) == "8A"

    def test_clause_line_re_matches(self):
        match = CLAUSE_LINE_RE.match("4 Standard form of residential tenancy agreements")
        assert match is not None
        assert match.group(1) == "4"
        assert "Standard form" in match.group(2)

    def test_clause_line_re_rejects_definition_context(self):
        assert CLAUSE_LINE_RE.match("the Act means the Residential Tenancies Act 2010") is None

    def test_schedule_re_matches(self):
        match = NSW_SCHEDULE_RE.match("Schedule 1 Forms")
        assert match is not None
        assert match.group(1) == "1"
        assert match.group(2) == "Forms"


# ── Unit tests: NSW valid clause title ──────────────────────────────────


class TestNSWValidClauseTitle:
    def test_valid_title(self):
        assert _is_valid_clause_title("Name of Regulation") is True

    def test_short_title_false(self):
        assert _is_valid_clause_title("ab") is False

    def test_parenthetical_false(self):
        assert _is_valid_clause_title("(Repealed)") is False

    def test_blocklist_note(self):
        assert _is_valid_clause_title("Note") is False

    def test_blocklist_warning(self):
        assert _is_valid_clause_title("Warning") is False

    def test_blocklist_important(self):
        assert _is_valid_clause_title("Important") is False

    def test_lowercase_first_char_false(self):
        assert _is_valid_clause_title("meaning") is False


# ── Unit tests: VICRegulationChunk schema ───────────────────────────────


class TestVICRegulationChunkSchema:
    def test_all_required_fields_present(self):
        p = VICRegulationParser()
        hierarchy = {
            "part": None, "part_title": None,
            "division": None, "division_title": None,
            "subdivision": None, "subdivision_title": None,
            "schedule": None, "schedule_title": None,
        }
        chunk = p._build_chunk("1", "Objective", "Test text", hierarchy)
        expected_fields = [
            "chunk_id", "text", "state", "act", "year", "instrument_type",
            "section_id", "section_title", "part", "part_title", "division",
            "division_title", "subdivision", "subdivision_title",
            "schedule", "schedule_title", "subsection_range",
        ]
        for field in expected_fields:
            assert field in chunk, f"Missing field: {field}"
        assert len(chunk) == len(expected_fields)

    def test_instrument_type_is_regulation(self):
        p = VICRegulationParser()
        hierarchy = {"part": None, "part_title": None, "division": None, "division_title": None,
                     "subdivision": None, "subdivision_title": None, "schedule": None, "schedule_title": None}
        chunk = p._build_chunk("1", "Objective", "Test text", hierarchy)
        assert chunk["instrument_type"] == "regulation"

    def test_state_and_act(self):
        p = VICRegulationParser()
        hierarchy = {"part": None, "part_title": None, "division": None, "division_title": None,
                     "subdivision": None, "subdivision_title": None, "schedule": None, "schedule_title": None}
        chunk = p._build_chunk("1", "Objective", "Test text", hierarchy)
        assert chunk["state"] == "VIC"
        assert chunk["act"] == "Residential Tenancies Regulations 2021"

    def test_chunk_id_format_for_regulation(self):
        p = VICRegulationParser()
        hierarchy = {"part": None, "part_title": None, "division": None, "division_title": None,
                     "subdivision": None, "subdivision_title": None, "schedule": None, "schedule_title": None}
        chunk = p._build_chunk("1", "Objective", "Test text", hierarchy)
        assert chunk["chunk_id"] == "VIC-RTR2021-r1"

    def test_schedule_chunk_has_schedule_fields(self):
        p = VICRegulationParser()
        hierarchy = {
            "part": None, "part_title": None,
            "division": None, "division_title": None,
            "subdivision": None, "subdivision_title": None,
            "schedule": "Schedule 1", "schedule_title": "Forms",
        }
        chunk = p._build_chunk("sch1", "Schedule 1\u2014Forms", "Test text", hierarchy)
        assert chunk["schedule"] == "Schedule 1"
        assert chunk["schedule_title"] == "Forms"
        assert chunk["part"] is None
        assert chunk["division"] is None
        assert chunk["subdivision"] is None


# ── Unit tests: NSWRegulationChunk schema ───────────────────────────────


class TestNSWRegulationChunkSchema:
    def test_all_required_fields_present(self):
        p = NSWRegulationParser()
        hierarchy = {
            "part": None, "part_title": None,
            "division": None, "division_title": None,
            "subdivision": None, "subdivision_title": None,
            "schedule": None, "schedule_title": None,
        }
        chunk = p._build_chunk("1", "Name of Regulation", "Test text", hierarchy)
        expected_fields = [
            "chunk_id", "text", "state", "act", "year", "instrument_type",
            "section_id", "section_title", "part", "part_title", "division",
            "division_title", "subdivision", "subdivision_title",
            "schedule", "schedule_title", "subsection_range",
        ]
        for field in expected_fields:
            assert field in chunk, f"Missing field: {field}"
        assert len(chunk) == len(expected_fields)

    def test_instrument_type_is_regulation(self):
        p = NSWRegulationParser()
        hierarchy = {"part": None, "part_title": None, "division": None, "division_title": None,
                     "subdivision": None, "subdivision_title": None, "schedule": None, "schedule_title": None}
        chunk = p._build_chunk("1", "Name of Regulation", "Test text", hierarchy)
        assert chunk["instrument_type"] == "regulation"

    def test_state_and_act(self):
        p = NSWRegulationParser()
        hierarchy = {"part": None, "part_title": None, "division": None, "division_title": None,
                     "subdivision": None, "subdivision_title": None, "schedule": None, "schedule_title": None}
        chunk = p._build_chunk("1", "Name of Regulation", "Test text", hierarchy)
        assert chunk["state"] == "NSW"
        assert chunk["act"] == "Residential Tenancies Regulation 2019"

    def test_chunk_id_format(self):
        p = NSWRegulationParser()
        hierarchy = {"part": None, "part_title": None, "division": None, "division_title": None,
                     "subdivision": None, "subdivision_title": None, "schedule": None, "schedule_title": None}
        chunk = p._build_chunk("1", "Name of Regulation", "Test text", hierarchy)
        assert chunk["chunk_id"] == "NSW-RTR2019-r1"

    def test_part_division_hierarchy_propagated(self):
        p = NSWRegulationParser()
        hierarchy = {
            "part": "1", "part_title": "Preliminary",
            "division": "2", "division_title": "Interpretation",
            "subdivision": None, "subdivision_title": None,
            "schedule": None, "schedule_title": None,
        }
        chunk = p._build_chunk("3", "Definitions", "Test text", hierarchy)
        assert chunk["part"] == "1"
        assert chunk["part_title"] == "Preliminary"
        assert chunk["division"] == "2"
        assert chunk["division_title"] == "Interpretation"
        assert chunk["subdivision"] is None

    def test_chunk_id_for_schedule_clause(self):
        p = NSWRegulationParser()
        hierarchy = {
            "part": None, "part_title": None,
            "division": None, "division_title": None,
            "subdivision": None, "subdivision_title": None,
            "schedule": "Schedule 1", "schedule_title": "Forms",
        }
        chunk = p._build_chunk("sch1", "Schedule 1\u2014Forms", "Test text", hierarchy)
        assert chunk["schedule"] == "Schedule 1"
        assert "NSW-RTR2019" in chunk["chunk_id"]


# ── Unit tests: NSW build parent prefix ─────────────────────────────────


class TestNSWRegulationBuildParentPrefix:
    def test_schedule_only(self):
        p = NSWRegulationParser()
        hierarchy = {
            "part": None, "part_title": None,
            "division": None, "division_title": None,
            "subdivision": None, "subdivision_title": None,
            "schedule": "Schedule 2", "schedule_title": "Forms",
        }
        result = p._build_parent_prefix(hierarchy)
        assert result == "[Schedule 2]"

    def test_schedule_part_division(self):
        p = NSWRegulationParser()
        hierarchy = {
            "part": "1", "part_title": "Preliminary",
            "division": "2", "division_title": "General",
            "subdivision": None, "subdivision_title": None,
            "schedule": None, "schedule_title": None,
        }
        result = p._build_parent_prefix(hierarchy)
        assert result == "[Part 1 - Division 2]"

    def test_empty_hierarchy(self):
        p = NSWRegulationParser()
        hierarchy = {
            "part": None, "part_title": None,
            "division": None, "division_title": None,
            "subdivision": None, "subdivision_title": None,
            "schedule": None, "schedule_title": None,
        }
        result = p._build_parent_prefix(hierarchy)
        assert result == ""


# ── Parser instantiation tests ──────────────────────────────────────────


class TestVICRegulationParserInstantiation:
    def test_can_instantiate(self):
        p = VICRegulationParser()
        assert p.state == "VIC"
        assert "Residential Tenancies Regulations 2021" in p.act_name

    def test_year(self):
        p = VICRegulationParser()
        assert p.act_year == "2021"


class TestNSWRegulationParserInstantiation:
    def test_can_instantiate(self):
        p = NSWRegulationParser()
        assert p.state == "NSW"
        assert "Residential Tenancies Regulation 2019" in p.act_name

    def test_year(self):
        p = NSWRegulationParser()
        assert p.act_year == "2019"


# ── Integration tests ───────────────────────────────────────────────────


@pytest.mark.integration
class TestVICRegulationIntegration:
    def test_output_file_exists(self):
        path = Path("data/processed/vic_regulation_chunks.json")
        if not path.exists():
            pytest.skip("Run VICRegulationParser first")
        with open(path) as f:
            chunks = json.load(f)
        assert len(chunks) > 50
        assert all(c["text"].strip() for c in chunks)
        assert all(c.get("instrument_type") == "regulation" for c in chunks)
        assert any(c["section_id"] == "1" for c in chunks)
        assert any(c["section_id"] == "21" for c in chunks)

    def test_schedule_coverage(self):
        path = Path("data/processed/vic_regulation_chunks.json")
        if not path.exists():
            pytest.skip("Run VICRegulationParser first")
        with open(path) as f:
            chunks = json.load(f)
        schedules = set(c.get("schedule") for c in chunks if c.get("schedule"))
        assert "Schedule 1" in schedules
        assert "Schedule 4" in schedules

    def test_no_duplicate_chunk_ids(self):
        path = Path("data/processed/vic_regulation_chunks.json")
        if not path.exists():
            pytest.skip("Run VICRegulationParser first")
        with open(path) as f:
            chunks = json.load(f)
        from collections import Counter
        cids = Counter(c["chunk_id"] for c in chunks)
        dupes = {k: v for k, v in cids.items() if v > 1}
        assert len(dupes) == 0, f"Duplicate chunk IDs: {dupes}"


@pytest.mark.integration
class TestNSWRegulationIntegration:
    def test_output_file_exists(self):
        path = Path("data/processed/nsw_regulation_chunks.json")
        if not path.exists():
            pytest.skip("Run NSWRegulationParser first")
        with open(path) as f:
            chunks = json.load(f)
        assert len(chunks) > 50
        assert all(c["text"].strip() for c in chunks)
        assert all(c.get("instrument_type") == "regulation" for c in chunks)
        assert any(c["section_id"] == "1" for c in chunks)
        assert any(c["section_id"] == "8" for c in chunks)

    def test_part_hierarchy_present(self):
        path = Path("data/processed/nsw_regulation_chunks.json")
        if not path.exists():
            pytest.skip("Run NSWRegulationParser first")
        with open(path) as f:
            chunks = json.load(f)
        non_sched = [c for c in chunks if not c.get("schedule")]
        has_part = [c for c in non_sched if c.get("part")]
        assert len(has_part) > 10, f"Only {len(has_part)} non-schedule chunks have part info"

    def test_no_duplicate_chunk_ids(self):
        path = Path("data/processed/nsw_regulation_chunks.json")
        if not path.exists():
            pytest.skip("Run NSWRegulationParser first")
        with open(path) as f:
            chunks = json.load(f)
        from collections import Counter
        cids = Counter(c["chunk_id"] for c in chunks)
        dupes = {k: v for k, v in cids.items() if v > 1}
        assert len(dupes) == 0, f"Duplicate chunk IDs: {dupes}"
