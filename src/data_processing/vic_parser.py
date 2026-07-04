"""
VIC-specific parser for the Residential Tenancies Act 1997 PDF.

Inherits from BaseParser and implements VIC-specific:
  - Clean text: TOC skipping, header/footer stripping, Part-from-header injection
  - Hierarchy parsing: state-machine with amendment skipping
  - VIC chunk payload schema

Module-level helper functions are kept for backward compatibility with
existing tests that import from parser.py.
"""

import re
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import fitz

from src.data_processing.base_parser import BaseParser

# ── constants ──────────────────────────────────────────────────────────

INPUT_PDF = "data/raw/97-109aa111-authorised-VIC.pdf"
OUTPUT_JSON = "data/processed/vic_rta_chunks.json"
STATE = "VIC"
ACT_NAME = "Residential Tenancies Act 1997"
ACT_YEAR = "1997"
TOKEN_THRESHOLD = 2048

# ── regex patterns ─────────────────────────────────────────────────────

PART_RE = re.compile(r"^Part\s+(\d+[A-Z]*)\s*[—-]\s*(.+)$")
DIVISION_RE = re.compile(r"^Division\s+(\d+[A-Z]*)\s*[—-]\s*(.+)$")
SUBDIVISION_RE = re.compile(r"^Subdivision\s+(\d+[A-Z]*)\s*[—-]\s*(.+)$")
SECTION_RE = re.compile(r"^(\d+[A-Z]+[A-Za-z]*)\s+(.+)$")
STANDALONE_NUM_RE = re.compile(r"^(\d+[A-Z]+[A-Za-z]*)$")
PLAIN_SECTION_RE = re.compile(r"^(\d+)\s+([A-Z][A-Za-z].+)$")
AMENDMENT_RE = re.compile(r"^\s*S\.\s+\d+")
ACT_NUMBER_RE = re.compile(r"No\.\s+\d+\s+of\s+\d+")
PAGE_NUMBER_RE = re.compile(r"^\d{1,4}$")
AUTHORISED_RE = re.compile(r"Authorised by the")
SENTENCE_END_RE = re.compile(r"[.!?]\s")

BOILERPLATE_LINES = frozenset(
    {
        "Authorised by the Chief Parliamentary Counsel",
        "Residential Tenancies Act 1997",
    }
)

MONTH_NAMES = frozenset(
    {
        "January",
        "February",
        "March",
        "April",
        "May",
        "June",
        "July",
        "August",
        "September",
        "October",
        "November",
        "December",
    }
)

PROSE_STARTS = frozenset(
    {
        "penalty",
        "Penalty",
        "years",
        "year",
        "months",
        "month",
        "days",
        "day",
        "hours",
        "hour",
        "business",
        "p.m.",
        "a.m.",
    }
)

# ── module-level helpers (backward compat + internal use) ───────────────


def estimate_tokens(text: str) -> int:
    """Estimate token count from word count (~0.75 tokens/word)."""
    words = len(text.split())
    return max(1, int(words / 0.75))


def is_toc_page(lines: list[str]) -> bool:
    """Detect Table of Contents pages by column headers or title text."""
    for i, line in enumerate(lines[:5]):
        stripped = line.strip()
        if stripped == "Section" and i + 1 < len(lines) and lines[i + 1].strip() == "Page":
            return True
    return "TABLE OF PROVISIONS" in "\n".join(lines[:10])


def is_amendment_line(stripped: str) -> bool:
    """Check if line starts an amendment footnote (e.g. 'S. 123')."""
    return bool(AMENDMENT_RE.match(stripped))


def is_amendment_continuation(stripped: str) -> bool:
    """Check if line continues an amendment footnote block."""
    if not stripped:
        return True
    amendment_pattern = re.match(
        r"^(s\.\s|Nos?\s|No\.\s|amended\s|inserted\s|substituted\s|repealed\s"
        r"|\([a-z]+\)|Note\s+to\s|S\.\s)",
        stripped,
        re.IGNORECASE,
    )
    if amendment_pattern:
        return True
    return bool(re.match(r"^[ivxlcdm]+$", stripped, re.IGNORECASE))


def extract_part_from_header(line: str) -> tuple[str, str] | None:
    """Try to extract Part ID and title from a page header line."""
    match = PART_RE.match(line.strip())
    if match:
        return (match.group(1), match.group(2))
    return None


def _is_valid_section_title(title: str) -> bool:
    """Reject false positives: prose starters, dates, month names, digit-prefixed."""
    if not title or len(title) < 2:
        return False
    first_word = title.split()[0] if title.split() else ""
    if first_word in PROSE_STARTS:
        return False
    if first_word in MONTH_NAMES:
        return False
    return not re.match(r"^\d", title)


def _looks_like_date(title: str) -> bool:
    """Check if title starts with a month name (likely a date, not a section)."""
    first_word = title.split()[0] if title.split() else ""
    return first_word in MONTH_NAMES


def _make_text(
    section_id: str, section_title: str, body_text: str, parent_prefix: str
) -> str:
    """Assemble full chunk text with optional parent hierarchy prefix."""
    if parent_prefix:
        return f"{parent_prefix} {section_id} {section_title}\n{body_text}"
    return f"{section_id} {section_title}\n{body_text}"


def _build_parent_prefix(hierarchy: dict[str, Any]) -> str:
    """Build hierarchy prefix like '[Part 2 - Division 3]'.

    Duplicated from BaseParser._build_parent_prefix for backward-compat
    module-level access by tests.
    """
    parts: list[str] = []
    if hierarchy["part"]:
        parts.append(f"Part {hierarchy['part']}")
        if hierarchy["division"]:
            parts.append(f"Division {hierarchy['division']}")
            if hierarchy["subdivision"]:
                parts.append(f"Subdivision {hierarchy['subdivision']}")
    if parts:
        return "[" + " - ".join(parts) + "]"
    return ""


# ── VICParser class ────────────────────────────────────────────────────


class VICParser(BaseParser):
    """Parser for the Victorian Residential Tenancies Act 1997 PDF."""

    def __init__(self) -> None:
        super().__init__(
            state=STATE,
            act_name=ACT_NAME,
            act_year=ACT_YEAR,
            token_threshold=TOKEN_THRESHOLD,
        )

    # ── clean_text override ────────────────────────────────────────────

    def clean_text(self, raw_page_texts: list[str]) -> list[str]:
        """Strip TOC, headers, footers, boilerplate; inject Part from page headers."""
        cleaned: list[str] = []
        toc_ended = False
        last_part_info: tuple[str, str] | None = None
        seen_enacting_clause = False

        for page_num, page_text in enumerate(raw_page_texts):
            lines = page_text.split("\n")

            if not toc_ended:
                if is_toc_page(lines):
                    continue
                toc_ended = True
                self.logger.info("TOC ends at PDF page %d", page_num + 1)

            auth_idx = -1
            actno_idx = -1
            has_standard_header = False

            for i, line in enumerate(lines):
                stripped = line.strip()
                if auth_idx < 0 and AUTHORISED_RE.match(stripped):
                    auth_idx = i
                if actno_idx < 0 and i > auth_idx and ACT_NUMBER_RE.match(stripped):
                    actno_idx = i

            if auth_idx >= 0 and actno_idx > auth_idx:
                has_standard_header = True

            part_info = None
            if auth_idx >= 0 and auth_idx + 1 < len(lines):
                part_info = extract_part_from_header(lines[auth_idx + 1])

            if part_info and part_info != last_part_info:
                last_part_info = part_info
                cleaned.append(f"Part {part_info[0]}—{part_info[1]}")

            body_start = actno_idx + 2 if has_standard_header and actno_idx >= 0 else 0

            for line in lines[body_start:]:
                stripped = line.strip()

                if "The Parliament of Victoria enacts as follows:" in stripped:
                    seen_enacting_clause = True
                    continue

                if not seen_enacting_clause:
                    continue

                if has_standard_header and PAGE_NUMBER_RE.match(stripped) and len(stripped) <= 4:
                    continue

                if stripped in (
                    "Authorised Version No. 111",
                    "Authorised Version incorporating amendments as at",
                ):
                    continue

                if re.match(r"^\d+\s+November\s+\d{4}$", stripped):
                    continue

                cleaned.append(line)

        self.logger.info("Total body lines: %d", len(cleaned))
        return cleaned

    # ── parse_hierarchy override ───────────────────────────────────────

    def parse_hierarchy(self, cleaned_lines: list[str]) -> list[dict]:
        """Parse cleaned VIC body lines into hierarchical section chunks."""
        chunks: list[dict] = []
        hierarchy: dict[str, Any] = {
            "part": None,
            "part_title": None,
            "division": None,
            "division_title": None,
            "subdivision": None,
            "subdivision_title": None,
        }
        last_part: str | None = None

        current_section_id: str | None = None
        current_section_title = ""
        current_body_lines: list[str] = []
        pending_section_id: str | None = None
        in_amendment = False
        prev_blank = True

        for line in cleaned_lines:
            stripped = line.strip()

            if in_amendment:
                if (
                    not stripped
                    or is_amendment_line(stripped)
                    or is_amendment_continuation(stripped)
                ):
                    continue
                in_amendment = False
                prev_blank = True

            if not stripped:
                prev_blank = True
                if current_section_id is not None:
                    current_body_lines.append("")
                continue

            if stripped in BOILERPLATE_LINES:
                continue

            if ACT_NUMBER_RE.match(stripped) and len(stripped) < 30:
                continue

            part_match = PART_RE.match(stripped)
            if part_match:
                prev_blank = True
                part_id = part_match.group(1)
                part_title = part_match.group(2)
                if part_id != last_part:
                    last_part = part_id
                    if current_section_id is not None:
                        self._flush_section(
                            chunks,
                            current_section_id,
                            current_section_title,
                            current_body_lines,
                            hierarchy,
                        )
                    hierarchy["part"] = part_id
                    hierarchy["part_title"] = part_title
                    hierarchy["division"] = None
                    hierarchy["division_title"] = None
                    hierarchy["subdivision"] = None
                    hierarchy["subdivision_title"] = None
                    current_section_id = None
                    current_section_title = ""
                    current_body_lines = []
                continue

            div_match = DIVISION_RE.match(stripped)
            if div_match:
                prev_blank = True
                if current_section_id is not None:
                    self._flush_section(
                        chunks,
                        current_section_id,
                        current_section_title,
                        current_body_lines,
                        hierarchy,
                    )
                hierarchy["division"] = div_match.group(1)
                hierarchy["division_title"] = div_match.group(2)
                hierarchy["subdivision"] = None
                hierarchy["subdivision_title"] = None
                current_section_id = None
                current_section_title = ""
                current_body_lines = []
                continue

            subdiv_match = SUBDIVISION_RE.match(stripped)
            if subdiv_match:
                prev_blank = True
                if current_section_id is not None:
                    self._flush_section(
                        chunks,
                        current_section_id,
                        current_section_title,
                        current_body_lines,
                        hierarchy,
                    )
                hierarchy["subdivision"] = subdiv_match.group(1)
                hierarchy["subdivision_title"] = subdiv_match.group(2)
                current_section_id = None
                current_section_title = ""
                current_body_lines = []
                continue

            if is_amendment_line(stripped):
                in_amendment = True
                continue

            if pending_section_id is not None:
                if _is_valid_section_title(stripped) and not _looks_like_date(stripped):
                    if current_section_id is not None:
                        self._flush_section(
                            chunks,
                            current_section_id,
                            current_section_title,
                            current_body_lines,
                            hierarchy,
                        )
                    current_section_id = pending_section_id
                    current_section_title = stripped
                    current_body_lines = []
                    pending_section_id = None
                    prev_blank = False
                    continue
                else:
                    if current_section_id is not None:
                        current_body_lines.append(pending_section_id)
                    pending_section_id = None

            standalone = STANDALONE_NUM_RE.match(stripped)
            if standalone:
                section_id = standalone.group(1)
                if len(section_id) >= 3:
                    pending_section_id = section_id
                    prev_blank = False
                    continue

            section_match = SECTION_RE.match(stripped)
            if section_match:
                sid = section_match.group(1)
                title = section_match.group(2)
                if _is_valid_section_title(title):
                    if current_section_id is not None:
                        self._flush_section(
                            chunks,
                            current_section_id,
                            current_section_title,
                            current_body_lines,
                            hierarchy,
                        )
                    current_section_id = sid
                    current_section_title = title
                    current_body_lines = []
                    prev_blank = False
                    continue
                else:
                    if current_section_id is not None:
                        current_body_lines.append(stripped)
                    prev_blank = False
                    continue

            plain_match = PLAIN_SECTION_RE.match(stripped)
            if plain_match:
                sid = plain_match.group(1)
                title = plain_match.group(2)
                if prev_blank and _is_valid_section_title(title) and not _looks_like_date(title):
                    if current_section_id is not None:
                        self._flush_section(
                            chunks,
                            current_section_id,
                            current_section_title,
                            current_body_lines,
                            hierarchy,
                        )
                    current_section_id = sid
                    current_section_title = title
                    current_body_lines = []
                    prev_blank = False
                    continue

            if current_section_id is not None:
                current_body_lines.append(stripped)
            prev_blank = False

        if current_section_id is not None:
            self._flush_section(
                chunks,
                current_section_id,
                current_section_title,
                current_body_lines,
                hierarchy,
            )

        return chunks

    # ── _flush_section (VIC-specific: silent return on empty) ───────────

    def _flush_section(
        self,
        chunks: list[dict],
        section_id: str,
        section_title: str,
        body_lines: list[str],
        hierarchy: dict[str, Any],
    ) -> None:
        """Emit chunk(s) for current section, splitting if over token limit."""
        body_text = "\n".join(body_lines).strip()
        if not body_text:
            return

        parent_prefix = self._build_parent_prefix(hierarchy)
        prefixed_text = self._make_text(section_id, section_title, body_text, parent_prefix)
        tokens = self.estimate_tokens(prefixed_text)

        if tokens <= self.token_threshold:
            chunks.append(
                self._build_chunk(section_id, section_title, prefixed_text, hierarchy, None)
            )
        else:
            sub_chunks = self._split_long_section(
                section_id, section_title, body_text, hierarchy, parent_prefix
            )
            chunks.extend(sub_chunks)

    # ── _build_chunk override ──────────────────────────────────────────

    def _build_chunk(
        self,
        section_id: str,
        section_title: str,
        text: str,
        hierarchy: dict[str, Any],
        subsection_range: str | None = None,
    ) -> dict:
        """Build VIC chunk payload matching the 14-field schema."""
        chunk_id = f"VIC-RTA1997-s{section_id}"
        return {
            "chunk_id": chunk_id,
            "text": text,
            "state": self.state,
            "act": self.act_name,
            "year": self.act_year,
            "section_id": section_id,
            "section_title": section_title,
            "part": hierarchy["part"],
            "part_title": hierarchy["part_title"],
            "division": hierarchy["division"],
            "division_title": hierarchy["division_title"],
            "subdivision": hierarchy["subdivision"],
            "subdivision_title": hierarchy["subdivision_title"],
            "subsection_range": subsection_range,
        }


# ── module-level extract_sections (backward compat with fitz.Document) ──


def extract_sections(doc: fitz.Document) -> list[dict]:
    """Extract sections from a fitz.Document. Retained for backward compat.

    Prefer VICParser.run() for new code.
    """
    raw_page_texts: list[str] = []
    for i in range(doc.page_count):
        raw_page_texts.append(doc[i].get_text("text"))

    parser = VICParser()
    parser.logger.info("Opened PDF: <fitz.Document> (%d pages)", doc.page_count)

    cleaned_lines = parser.clean_text(raw_page_texts)
    chunks = parser.parse_hierarchy(cleaned_lines)
    parser.logger.info("Sections extracted: %d", len(chunks))

    total_tokens = sum(estimate_tokens(c["text"]) for c in chunks)
    parser.logger.info("Total estimated tokens: %d", total_tokens)

    if not chunks:
        raise ValueError("No sections extracted — possible parsing failure")

    return chunks


def main() -> None:
    """Parse VIC RTA PDF and write chunks to JSON."""
    parser = VICParser()
    parser.run(INPUT_PDF, OUTPUT_JSON)


if __name__ == "__main__":
    main()
