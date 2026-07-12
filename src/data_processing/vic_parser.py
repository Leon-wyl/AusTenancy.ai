"""
VIC-specific parser for the Residential Tenancies Act 1997 PDF.

Inherits from BaseParser and implements VIC-specific:
  - Clean text: TOC skipping, header/footer stripping, Part-from-header injection
  - Hierarchy parsing: state-machine with amendment skipping
  - VIC chunk payload schema
"""

import re

from src.data_processing.base_parser import BaseParser

STATE = "VIC"
ACT_NAME = "Residential Tenancies Act 1997"
ACT_YEAR = "1997"
TOKEN_THRESHOLD = 2048

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


def _is_valid_section_title(title: str) -> bool:
    if not title or len(title) < 2:
        return False
    if not title[0].isupper():
        return False
    first_word = title.split()[0] if title.split() else ""
    if first_word in PROSE_STARTS:
        return False
    if first_word in MONTH_NAMES:
        return False
    return not re.match(r"^\d", title)


def _looks_like_date(title: str) -> bool:
    first_word = title.split()[0] if title.split() else ""
    return first_word in MONTH_NAMES


class VICParser(BaseParser):
    """Parser for the Victorian Residential Tenancies Act 1997 PDF."""

    def __init__(self) -> None:
        super().__init__(
            state=STATE,
            act_name=ACT_NAME,
            act_year=ACT_YEAR,
            token_threshold=TOKEN_THRESHOLD,
        )

    def clean_text(self, raw_page_texts: list[str]) -> list[str]:
        cleaned: list[str] = []
        toc_ended = False
        last_part_info: tuple[str, str] | None = None
        seen_enacting_clause = False

        for page_num, page_text in enumerate(raw_page_texts):
            lines = page_text.split("\n")

            if not toc_ended:
                if _is_toc_page(lines):
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
                part_info = _extract_part_from_header(lines[auth_idx + 1])

            if part_info and part_info != last_part_info:
                last_part_info = part_info
                cleaned.append(f"Part {part_info[0]}\u2014{part_info[1]}")

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

    def parse_hierarchy(self, cleaned_lines: list[str]) -> list[dict]:
        chunks: list[dict] = []
        hierarchy: dict = {
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
                    or _is_amendment_line(stripped)
                    or _is_amendment_continuation(stripped)
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

            if _is_amendment_line(stripped):
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

    def _flush_section(
        self,
        chunks: list[dict],
        section_id: str,
        section_title: str,
        body_lines: list[str],
        hierarchy: dict,
    ) -> None:
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

    def _build_chunk(
        self,
        section_id: str,
        section_title: str,
        text: str,
        hierarchy: dict,
        subsection_range: str | None = None,
    ) -> dict:
        chunk_id = f"VIC-RTA1997-s{section_id}"
        return {
            "chunk_id": chunk_id,
            "text": text,
            "state": self.state,
            "act": self.act_name,
            "year": self.act_year,
            "section_id": section_id,
            "section_title": section_title,
            "part": hierarchy.get("part"),
            "part_title": hierarchy.get("part_title"),
            "division": hierarchy.get("division"),
            "division_title": hierarchy.get("division_title"),
            "subdivision": hierarchy.get("subdivision"),
            "subdivision_title": hierarchy.get("subdivision_title"),
            "subsection_range": subsection_range,
        }


# ── module-level helpers ────────────────────────────────────────────────


def _is_toc_page(lines: list[str]) -> bool:
    for i, line in enumerate(lines[:5]):
        stripped = line.strip()
        if stripped == "Section" and i + 1 < len(lines) and lines[i + 1].strip() == "Page":
            return True
    return "TABLE OF PROVISIONS" in "\n".join(lines[:10])


def _is_amendment_line(stripped: str) -> bool:
    return bool(AMENDMENT_RE.match(stripped))


def _is_amendment_continuation(stripped: str) -> bool:
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


def _extract_part_from_header(line: str) -> tuple[str, str] | None:
    match = PART_RE.match(line.strip())
    if match:
        return (match.group(1), match.group(2))
    return None


# ── main ─────────────────────────────────────────────────────────────────

INPUT_PDF = "data/raw/97-109aa111-authorised-VIC.pdf"
OUTPUT_JSON = "data/processed/vic_chunks.json"


def main() -> None:
    """Parse VIC RTA PDF and write chunks to JSON."""
    parser = VICParser()
    parser.run(INPUT_PDF, OUTPUT_JSON)


if __name__ == "__main__":
    main()
