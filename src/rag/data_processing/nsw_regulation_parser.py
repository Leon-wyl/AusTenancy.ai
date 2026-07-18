"""
NSW-specific parser for the Residential Tenancies Regulation 2019 PDF.

Inherits from BaseParser and implements NSW-specific:
  - Header/footer stripping (3-line footer on every page)
  - TOC detection and removal
  - Schedule hierarchy parsing (Part -> Division -> Clause)
  - NSW regulation chunk payload schema

Modeled after nsw_parser.py for the NSW Act, adapted for Regulations
with schedules and clause-based numbering.
"""

import logging
import re

from src.rag.data_processing.base_parser import PAGE_FOOTER_RE, BaseParser

NSW_STATE = "NSW"
NSW_ACT_NAME = "Residential Tenancies Regulation 2019"
NSW_ACT_YEAR = "2019"
CHUNK_PREFIX = "NSW-RTR2019"

PART_RE = re.compile(r"^Part\s+(\d+[A-Z]*)\s+(.+)$")
DIVISION_RE = re.compile(r"^Division\s+(\d+[A-Z]*)\s+(.+)$")
SUBDIVISION_RE = re.compile(r"^Subdivision\s+(\d+[A-Z]*)\s+(.+)$")
CLAUSE_ID_RE = re.compile(r"^(\d+[A-Z]*)$")
CLAUSE_LINE_RE = re.compile(r"^(\d+[A-Z]*)\s+([A-Z][A-Za-z].+)$")
DEFINITION_LIKE_RE = re.compile(r"^[a-z][a-z]")
TOC_LINE_RE = re.compile(r"\.{4,}\s*\d+$")
FOOTER_LINE1_RE = re.compile(r"^Residential Tenancies Regulation 2019 \[NSW\]$")
FOOTER_LINE2_RE = re.compile(r"^Current version for \d+ \w+ \d+ to date.*$")
FOOTER_DATE_RE = re.compile(r"^Page \d+ of \d+$")
REPEALED_RE = re.compile(r"^.*\(Repealed\).*$")
SCHEDULE_RE = re.compile(r"^Schedule\s+(\d+)\s+(.+)$")
TITLE_BLOCKLIST = frozenset(
    {
        "Note",
        "Notes",
        "Important",
        "Example",
        "Examples",
        "Copyright",
        "Disclaimer",
        "Privacy",
        "Warning",
    }
)

SHORT_TITLE_THRESHOLD = 3
TOC_END_PAGE = 7

_SOCIAL_HOUSING_CLAUSES = frozenset(
    {"9", "24", "24A", "33", "36", "36A", "36B", "37", "38", "49", "53"}
)

# Prepositions that start a sentence continuation, NOT a Division title
_DIVISION_TITLE_BLOCK = frozenset(
    {
        "of",
        "against",
        "under",
        "for",
        "to",
        "in",
        "on",
        "with",
        "or",
        "and",
        "but",
        "by",
        "as",
        "at",
        "from",
        "about",
    }
)


def _is_valid_clause_title(title: str) -> bool:
    if not title or len(title) < SHORT_TITLE_THRESHOLD:
        return False
    if title.startswith("("):
        return False
    first_char = title[0]
    if not first_char.isupper():
        return False
    return title not in TITLE_BLOCKLIST


def _is_toc_page(lines: list[str]) -> bool:
    non_empty = [line.strip() for line in lines if line.strip()]
    if not non_empty:
        return False
    toc_count = sum(1 for line in non_empty if TOC_LINE_RE.search(line))
    return toc_count / len(non_empty) > 0.3


def _is_boilerplate_page(text: str) -> bool:
    return "Status Information" in text or "Currency of version" in text


def _is_boilerplate_line(stripped: str) -> bool:
    return stripped in (
        "New South Wales",
        "Residential Tenancies Regulation 2019",
    )


class NSWRegulationParser(BaseParser):
    """Parser for the NSW Residential Tenancies Regulation 2019 PDF."""

    def __init__(self) -> None:
        super().__init__(
            state=NSW_STATE,
            act_name=NSW_ACT_NAME,
            act_year=NSW_ACT_YEAR,
        )

    def clean_text(self, raw_page_texts: list[str]) -> list[str]:
        cleaned: list[str] = []
        body_started = False

        for page_idx, page_text in enumerate(raw_page_texts):
            lines = [line.rstrip() for line in page_text.split("\n")]

            if not body_started:
                lines = self._strip_footer(lines)

                if page_idx == 0 and _is_boilerplate_page(page_text):
                    continue

                if page_idx < TOC_END_PAGE and _is_toc_page(lines):
                    continue

                page_joined = " ".join(lines)
                if "Contents" in page_joined:
                    continue

                body_started = True
                lines = self._strip_footer(lines)

            else:
                lines = self._strip_footer(lines)

            if body_started:
                lines = self._strip_boilerplate_lines(lines)
                cleaned.extend(lines)

        return cleaned

    def _strip_footer(self, lines: list[str]) -> list[str]:
        footer_start = None
        scan_end = max(-1, len(lines) - 5)

        for i in range(len(lines) - 1, scan_end, -1):
            stripped = lines[i].strip()
            if FOOTER_DATE_RE.match(stripped):
                footer_start = max(0, i - 2)
                break
            if FOOTER_LINE2_RE.match(stripped):
                footer_start = max(0, i - 1)
                break
            if FOOTER_LINE1_RE.match(stripped):
                footer_start = i
                break

        if footer_start is None:
            footer_start = max(0, len(lines) - 3)

        end = footer_start
        for i in range(footer_start, len(lines)):
            stripped = lines[i].strip()
            if (
                FOOTER_LINE1_RE.match(stripped)
                or FOOTER_LINE2_RE.match(stripped)
                or FOOTER_DATE_RE.match(stripped)
                or PAGE_FOOTER_RE.match(stripped)
            ):
                end = i
                break

        return lines[:end]

    def _strip_boilerplate_lines(self, lines: list[str]) -> list[str]:
        result: list[str] = []
        for line in lines:
            stripped = line.strip()
            if _is_boilerplate_line(stripped):
                continue
            result.append(line)
        return result

    def parse_hierarchy(self, cleaned_lines: list[str]) -> list[dict]:
        chunks: list[dict] = []
        stats: dict[str, int] = {"parsed": 0, "skipped": 0, "repealed": 0}

        hierarchy: dict = {
            "part": None,
            "part_title": None,
            "division": None,
            "division_title": None,
            "subdivision": None,
            "subdivision_title": None,
            "schedule": None,
            "schedule_title": None,
        }

        current_clause_id: str | None = None
        current_clause_title = ""
        current_body_lines: list[str] = []
        pending_clause_id: str | None = None
        schedule_auto_clause: str | None = None

        for line in cleaned_lines:
            stripped = line.strip()

            if not stripped:
                if current_clause_id is not None:
                    current_body_lines.append("")
                continue

            schedule_match = SCHEDULE_RE.match(stripped)
            if schedule_match:
                if current_clause_id is not None:
                    self._flush_clause(
                        chunks,
                        stats,
                        current_clause_id,
                        current_clause_title,
                        current_body_lines,
                        hierarchy,
                    )
                hierarchy["schedule"] = f"Schedule {schedule_match.group(1)}"
                hierarchy["schedule_title"] = schedule_match.group(2)
                hierarchy["part"] = None
                hierarchy["part_title"] = None
                hierarchy["division"] = None
                hierarchy["division_title"] = None
                hierarchy["subdivision"] = None
                hierarchy["subdivision_title"] = None
                current_clause_id = None
                current_clause_title = ""
                current_body_lines = []
                pending_clause_id = None
                schedule_auto_clause = schedule_match.group(1)
                continue

            part_match = PART_RE.match(stripped)
            if part_match:
                if current_clause_id is not None:
                    self._flush_clause(
                        chunks,
                        stats,
                        current_clause_id,
                        current_clause_title,
                        current_body_lines,
                        hierarchy,
                    )
                part_num = part_match.group(1)
                if part_num == "7":
                    # Reg Part 7 (transitional) ≠ Act Part 7 (social housing)
                    hierarchy["part"] = None
                else:
                    hierarchy["part"] = part_num
                hierarchy["part_title"] = part_match.group(2)
                hierarchy["division"] = None
                hierarchy["division_title"] = None
                hierarchy["subdivision"] = None
                hierarchy["subdivision_title"] = None
                current_clause_id = None
                current_clause_title = ""
                current_body_lines = []
                pending_clause_id = None
                continue

            div_match = DIVISION_RE.match(stripped)
            if div_match:
                div_title = div_match.group(2).strip()
                first_word = div_title.split()[0].lower() if div_title else ""
                if first_word in _DIVISION_TITLE_BLOCK:
                    # False positive — body text containing "Division X of..."
                    if current_clause_id is not None:
                        current_body_lines.append(stripped)
                    continue
                if current_clause_id is not None:
                    self._flush_clause(
                        chunks,
                        stats,
                        current_clause_id,
                        current_clause_title,
                        current_body_lines,
                        hierarchy,
                    )
                hierarchy["division"] = div_match.group(1)
                hierarchy["division_title"] = div_match.group(2)
                hierarchy["subdivision"] = None
                hierarchy["subdivision_title"] = None
                current_clause_id = None
                current_clause_title = ""
                current_body_lines = []
                pending_clause_id = None
                continue

            subdiv_match = SUBDIVISION_RE.match(stripped)
            if subdiv_match:
                if current_clause_id is not None:
                    self._flush_clause(
                        chunks,
                        stats,
                        current_clause_id,
                        current_clause_title,
                        current_body_lines,
                        hierarchy,
                    )
                hierarchy["subdivision"] = subdiv_match.group(1)
                hierarchy["subdivision_title"] = subdiv_match.group(2)
                current_clause_id = None
                current_clause_title = ""
                current_body_lines = []
                pending_clause_id = None
                continue

            if REPEALED_RE.match(stripped):
                if current_clause_id is not None:
                    self._flush_clause(
                        chunks,
                        stats,
                        current_clause_id,
                        current_clause_title,
                        current_body_lines,
                        hierarchy,
                    )
                stats["repealed"] += 1
                current_clause_id = None
                current_clause_title = ""
                current_body_lines = []
                pending_clause_id = None
                continue

            if pending_clause_id is not None:
                if _is_valid_clause_title(stripped):
                    if current_clause_id is not None:
                        self._flush_clause(
                            chunks,
                            stats,
                            current_clause_id,
                            current_clause_title,
                            current_body_lines,
                            hierarchy,
                        )
                    current_clause_id = pending_clause_id
                    current_clause_title = stripped
                    current_body_lines = []
                    pending_clause_id = None
                    continue
                else:
                    if DEFINITION_LIKE_RE.match(stripped):
                        if current_clause_id is not None:
                            current_body_lines.append(f"{pending_clause_id} {stripped}")
                        pending_clause_id = None
                        continue
                    if current_clause_id is not None:
                        current_body_lines.append(pending_clause_id)
                    pending_clause_id = None

            standalone_match = CLAUSE_ID_RE.match(stripped)
            if standalone_match:
                cid = standalone_match.group(1)
                if len(cid) >= 1:
                    if DEFINITION_LIKE_RE.match(stripped):
                        if current_clause_id is not None:
                            current_body_lines.append(stripped)
                        continue
                    schedule_auto_clause = None
                    pending_clause_id = cid
                    continue

            clause_line_match = CLAUSE_LINE_RE.match(stripped)
            if clause_line_match:
                cid = clause_line_match.group(1)
                title = clause_line_match.group(2)
                if _is_valid_clause_title(title):
                    if current_clause_id is not None:
                        self._flush_clause(
                            chunks,
                            stats,
                            current_clause_id,
                            current_clause_title,
                            current_body_lines,
                            hierarchy,
                        )
                    schedule_auto_clause = None
                    current_clause_id = cid
                    current_clause_title = title
                    current_body_lines = []
                    pending_clause_id = None
                    continue

            if current_clause_id is not None:
                current_body_lines.append(stripped)
            elif schedule_auto_clause is not None:
                current_clause_id = f"sch{schedule_auto_clause}"
                current_clause_title = hierarchy.get("schedule_title", "")
                current_body_lines.append(stripped)
                schedule_auto_clause = None

        if current_clause_id is not None:
            self._flush_clause(
                chunks,
                stats,
                current_clause_id,
                current_clause_title,
                current_body_lines,
                hierarchy,
            )

        if pending_clause_id is not None and current_clause_id is not None:
            current_body_lines.append(pending_clause_id)
            self._flush_clause(
                chunks,
                stats,
                current_clause_id,
                current_clause_title,
                current_body_lines,
                hierarchy,
            )

        self.logger.info(
            "Parse stats: parsed=%d, skipped=%d, repealed=%d",
            stats["parsed"],
            stats["skipped"],
            stats["repealed"],
        )
        return chunks

    def _flush_clause(
        self,
        chunks: list[dict],
        stats: dict,
        clause_id: str,
        clause_title: str,
        body_lines: list[str],
        hierarchy: dict,
    ) -> None:
        body_text = "\n".join(body_lines).strip()

        if not body_text:
            if "(Repealed)" in clause_title or "repealed" in clause_title.lower():
                stats["repealed"] += 1
            else:
                stats["skipped"] += 1
            return

        if clause_id in _SOCIAL_HOUSING_CLAUSES:
            hierarchy = {**hierarchy, "part": "7"}

        display_title = clause_title

        parent_prefix = self._build_parent_prefix(hierarchy)
        if clause_id and clause_id.startswith("sch"):
            # Schedule-level auto-chunk: skip synthetic section_id in display
            prefixed_text = (
                f"{parent_prefix} {clause_title}\n{body_text}"
                if parent_prefix
                else f"{clause_title}\n{body_text}"
            )
        else:
            prefixed_text = self._make_text(clause_id, display_title, body_text, parent_prefix)
        tokens = self.estimate_tokens(prefixed_text)

        if tokens <= self.token_threshold:
            chunks.append(
                self._build_chunk(
                    clause_id,
                    clause_title,
                    prefixed_text,
                    hierarchy,
                )
            )
            stats["parsed"] += 1
        else:
            sub_chunks = self._split_long_section(
                clause_id,
                clause_title,
                body_text,
                hierarchy,
                parent_prefix,
            )
            chunks.extend(sub_chunks)
            stats["parsed"] += len(sub_chunks)

    def _build_parent_prefix(self, hierarchy: dict) -> str:
        parts: list[str] = []
        if hierarchy.get("schedule"):
            parts.append(hierarchy["schedule"])
        if hierarchy.get("part"):
            parts.append(f"Part {hierarchy['part']}")
            if hierarchy.get("division"):
                parts.append(f"Division {hierarchy['division']}")
                if hierarchy.get("subdivision"):
                    parts.append(f"Subdivision {hierarchy['subdivision']}")
        if parts:
            return "[" + " - ".join(parts) + "]"
        return ""

    def _build_chunk(
        self,
        section_id: str,
        section_title: str,
        text: str,
        hierarchy: dict,
        subsection_range: str | None = None,
    ) -> dict:
        chunk_id = f"{CHUNK_PREFIX}-r{section_id}"
        # Disambiguate schedule clause section_ids (e.g. Schedule 4 cl 1 → "sch4-1")
        if hierarchy.get("schedule") and not section_id.startswith("sch"):
            schedule_num = hierarchy["schedule"].split()[-1]
            effective_section_id = f"sch{schedule_num}-{section_id}"
        else:
            effective_section_id = section_id
        return {
            "chunk_id": chunk_id,
            "text": text,
            "state": NSW_STATE,
            "act": NSW_ACT_NAME,
            "year": NSW_ACT_YEAR,
            "instrument_type": "regulation",
            "section_id": effective_section_id,
            "section_title": section_title,
            "part": hierarchy.get("part"),
            "part_title": hierarchy.get("part_title"),
            "division": hierarchy.get("division"),
            "division_title": hierarchy.get("division_title"),
            "subdivision": hierarchy.get("subdivision"),
            "subdivision_title": hierarchy.get("subdivision_title"),
            "schedule": hierarchy.get("schedule"),
            "schedule_title": hierarchy.get("schedule_title"),
            "subsection_range": subsection_range,
        }


# main

INPUT_PDF = "data/raw/sl-2019-0629-regulation-nsw.pdf"
OUTPUT_JSON = "data/processed/nsw_regulation_chunks.json"


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    parser = NSWRegulationParser()
    chunks = parser.run(INPUT_PDF, OUTPUT_JSON)
    print(f"Parsed {len(chunks)} chunks -> {OUTPUT_JSON}")
    return 0


if __name__ == "__main__":
    main()
