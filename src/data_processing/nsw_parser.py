"""
NSW-specific parser for the Residential Tenancies Act 2010 No 42 PDF.

Inherits from BaseParser and implements NSW-specific:
  - Header/footer stripping (3-line footer on every page)
  - TOC detection and removal
  - Section hierarchy parsing (Part → Division → Subdivision → Section)
  - NSW chunk payload schema
"""

import re

from src.data_processing.base_parser import PAGE_FOOTER_RE, BaseParser

NSW_STATE = "NSW"
NSW_ACT_NAME = "Residential Tenancies Act 2010"
NSW_ACT_YEAR = "2010"

PART_RE = re.compile(r"^Part\s+(\d+[A-Z]*)\s+(.+)$")
DIVISION_RE = re.compile(r"^Division\s+(\d+[A-Z]*)\s+(.+)$")
SUBDIVISION_RE = re.compile(r"^Subdivision\s+(\d+[A-Z]*)\s+(.+)$")
SECTION_ID_RE = re.compile(r"^(\d+[A-Z]*)$")
SECTION_LINE_RE = re.compile(r"^(\d+[A-Z]*)\s+([A-Z][A-Za-z].+)$")
DEFINITION_LINE_RE = re.compile(
    r"^\d+[A-Z]*\s+.*\b(means|includes|has the same meaning|see section)\b",
)

FOOTER_HEADER_RE = re.compile(r"^Residential Tenancies Act 2010 No 42")
FOOTER_DATE_RE = re.compile(r"^Current version for")
TOC_LINE_RE = re.compile(r"\.{4,}\s*\d+$")
ENACTING_CLAUSE_RE = re.compile(r"^An Act with respect to")
ACT_HEADER_RE = re.compile(r"^(Residential Tenancies Act 2010 No 42|New South Wales)$")
BOILERPLATE_RE = re.compile(
    r"^(Status Information|Currency of version|Provisions in force|"
    r"Notes|Responsible Minister|Authorisation|File last modified|Certified by)"
)

SECTION_TITLE_MIN_WORDS = 1
SHORT_TITLE_THRESHOLD = 4
SECTION_TITLE_BLOCKLIST = frozenset(
    {
        "Note",
        "Note.",
        "Notes",
        "Notes.",
        "Penalty",
        "Maximum",
        "penalty",
        "Maximum penalty",
        "Copyright",
        "Disclaimer",
        "Privacy",
    }
)


def _is_valid_section_title(title: str) -> bool:
    if not title or (
        len(title) < SHORT_TITLE_THRESHOLD and title not in {"(Repealed)", "Repealed"}
    ):
        return False
    if title.startswith("(") and title not in {"(Repealed)"}:
        return False
    first_char = title[0]
    if not first_char.isupper() and title not in {"(Repealed)"}:
        return False
    if title in SECTION_TITLE_BLOCKLIST:
        return False
    words = title.split()
    return not len(words) < SECTION_TITLE_MIN_WORDS


class NSWParser(BaseParser):
    """Parser for the NSW Residential Tenancies Act 2010 PDF."""

    def __init__(self) -> None:
        super().__init__(
            state=NSW_STATE,
            act_name=NSW_ACT_NAME,
            act_year=NSW_ACT_YEAR,
        )

    def clean_text(self, raw_page_texts: list[str]) -> list[str]:
        cleaned: list[str] = []
        toc_seen = False
        body_started = False

        for page_text in raw_page_texts:
            lines = [line.rstrip() for line in page_text.split("\n")]
            lines = self._strip_footer(lines)

            if not lines:
                continue

            if not body_started:
                page_joined = " ".join(lines)

                if "Contents" in page_joined and not toc_seen:
                    toc_seen = True
                    continue

                if toc_seen:
                    non_empty = [line for line in lines if line.strip()]
                    if non_empty:
                        toc_count = sum(1 for line in non_empty if TOC_LINE_RE.search(line))
                        if toc_count / len(non_empty) > 0.5:
                            continue
                    body_started = True
                elif ENACTING_CLAUSE_RE.search(page_joined):
                    body_started = True
                else:
                    continue

            if body_started:
                lines = self._strip_act_header(lines)
                cleaned.extend(lines)

        cleaned = self._strip_enacting_clause(cleaned)
        return cleaned

    def _strip_footer(self, lines: list[str]) -> list[str]:
        footer_start = None
        for i in range(len(lines) - 1, max(-1, len(lines) - 4), -1):
            stripped = lines[i].strip()
            if PAGE_FOOTER_RE.match(stripped) or FOOTER_HEADER_RE.match(stripped):
                footer_start = i - 2
                break
        if footer_start is None:
            footer_start = len(lines) - 3
        footer_start = max(0, footer_start)
        end = footer_start
        for i in range(footer_start, len(lines)):
            stripped = lines[i].strip()
            if (
                FOOTER_HEADER_RE.match(stripped)
                or FOOTER_DATE_RE.match(stripped)
                or PAGE_FOOTER_RE.match(stripped)
            ):
                end = i
                break
        return lines[:end]

    def _strip_act_header(self, lines: list[str]) -> list[str]:
        result: list[str] = []
        for line in lines:
            stripped = line.strip()
            if ACT_HEADER_RE.match(stripped):
                continue
            if BOILERPLATE_RE.match(stripped):
                continue
            result.append(line)
        return result

    def _strip_enacting_clause(self, lines: list[str]) -> list[str]:
        result: list[str] = []
        skipping = True
        for line in lines:
            stripped = line.strip()
            if skipping and (
                ENACTING_CLAUSE_RE.match(stripped)
                or stripped.startswith("and other matters")
                or stripped.startswith("and for other purposes")
            ):
                continue
            skipping = False
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
        }

        current_section_id: str | None = None
        current_section_title = ""
        current_body_lines: list[str] = []
        pending_section_id: str | None = None

        for line in cleaned_lines:
            stripped = line.strip()

            if not stripped:
                if current_section_id is not None:
                    current_body_lines.append("")
                continue

            part_match = PART_RE.match(stripped)
            if part_match:
                if current_section_id is not None:
                    self._flush_section(
                        chunks,
                        stats,
                        current_section_id,
                        current_section_title,
                        current_body_lines,
                        hierarchy,
                    )
                hierarchy["part"] = part_match.group(1)
                hierarchy["part_title"] = part_match.group(2)
                hierarchy["division"] = None
                hierarchy["division_title"] = None
                hierarchy["subdivision"] = None
                hierarchy["subdivision_title"] = None
                current_section_id = None
                current_section_title = ""
                current_body_lines = []
                pending_section_id = None
                continue

            div_match = DIVISION_RE.match(stripped)
            if div_match:
                if current_section_id is not None:
                    self._flush_section(
                        chunks,
                        stats,
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
                pending_section_id = None
                continue

            subdiv_match = SUBDIVISION_RE.match(stripped)
            if subdiv_match:
                if current_section_id is not None:
                    self._flush_section(
                        chunks,
                        stats,
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
                pending_section_id = None
                continue

            if pending_section_id is not None:
                if _is_valid_section_title(stripped):
                    if current_section_id is not None:
                        self._flush_section(
                            chunks,
                            stats,
                            current_section_id,
                            current_section_title,
                            current_body_lines,
                            hierarchy,
                        )
                    current_section_id = pending_section_id
                    current_section_title = stripped
                    current_body_lines = []
                    pending_section_id = None
                    continue
                else:
                    if current_section_id is not None:
                        current_body_lines.append(pending_section_id)
                    pending_section_id = None

            standalone_match = SECTION_ID_RE.match(stripped)
            if standalone_match:
                sid = standalone_match.group(1)
                if len(sid) >= 1 and not stripped.startswith("("):
                    pending_section_id = sid
                    continue

            section_line_match = SECTION_LINE_RE.match(stripped)
            if section_line_match and not DEFINITION_LINE_RE.match(stripped):
                sid = section_line_match.group(1)
                title = section_line_match.group(2)
                if _is_valid_section_title(title):
                    if current_section_id is not None:
                        self._flush_section(
                            chunks,
                            stats,
                            current_section_id,
                            current_section_title,
                            current_body_lines,
                            hierarchy,
                        )
                    current_section_id = sid
                    current_section_title = title
                    current_body_lines = []
                    pending_section_id = None
                    continue

            if current_section_id is not None:
                current_body_lines.append(stripped)

        if current_section_id is not None:
            self._flush_section(
                chunks,
                stats,
                current_section_id,
                current_section_title,
                current_body_lines,
                hierarchy,
            )

        if pending_section_id is not None and current_section_id is not None:
            current_body_lines.append(pending_section_id)
            self._flush_section(
                chunks,
                stats,
                current_section_id,
                current_section_title,
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

    def _flush_section(
        self,
        chunks: list[dict],
        stats: dict,
        section_id: str,
        section_title: str,
        body_lines: list[str],
        hierarchy: dict,
    ) -> None:
        body_text = "\n".join(body_lines).strip()

        if not body_text:
            if "(Repealed)" in section_title or "repealed" in section_title.lower():
                stats["repealed"] += 1
            else:
                stats["skipped"] += 1
            return

        parent_prefix = self._build_parent_prefix(hierarchy)
        prefixed_text = self._make_text(section_id, section_title, body_text, parent_prefix)
        tokens = self.estimate_tokens(prefixed_text)

        if tokens <= self.token_threshold:
            chunks.append(
                self._build_chunk(
                    section_id,
                    section_title,
                    prefixed_text,
                    hierarchy,
                )
            )
            stats["parsed"] += 1
        else:
            sub_chunks = self._split_long_section(
                section_id,
                section_title,
                body_text,
                hierarchy,
                parent_prefix,
            )
            chunks.extend(sub_chunks)
            stats["parsed"] += len(sub_chunks)

    def _build_chunk(
        self,
        section_id: str,
        section_title: str,
        text: str,
        hierarchy: dict,
        subsection_range: str | None = None,
    ) -> dict:
        chunk_id = f"NSW-RTA2010-s{section_id}"
        return {
            "chunk_id": chunk_id,
            "text": text,
            "state": NSW_STATE,
            "act": NSW_ACT_NAME,
            "year": NSW_ACT_YEAR,
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


# ── main ─────────────────────────────────────────────────────────────────

INPUT_PDF = "data/raw/act-2010-042_nsw.pdf"
OUTPUT_JSON = "data/processed/nsw_chunks.json"


def main() -> None:
    """Parse NSW RTA PDF and write chunks to JSON."""
    parser = NSWParser()
    parser.run(INPUT_PDF, OUTPUT_JSON)


if __name__ == "__main__":
    main()
