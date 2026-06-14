"""
Production-grade parser for Victorian Residential Tenancies Act 1997 PDF.
Uses PyMuPDF (fitz) with regex-based hierarchy recognition and section-boundary chunking.
"""

import json
import logging
import re
from pathlib import Path

import fitz

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

INPUT_PDF = "data/raw/97-109aa111-authorised-VIC.pdf"
OUTPUT_JSON = "data/processed/vic_rta_chunks.json"
STATE = "VIC"
ACT_NAME = "Residential Tenancies Act 1997"
ACT_YEAR = "1997"
TOKEN_THRESHOLD = 512  # split long sections into sub-chunks above this token count

PART_RE = re.compile(r"^Part\s+(\d+[A-Z]*)\s*[—-]\s*(.+)$")
DIVISION_RE = re.compile(r"^Division\s+(\d+[A-Z]*)\s*[—-]\s*(.+)$")
SUBDIVISION_RE = re.compile(r"^Subdivision\s+(\d+[A-Z]*)\s*[—-]\s*(.+)$")
SECTION_RE = re.compile(r"^(\d+[A-Z]+[A-Za-z]*)\s+(.+)$")  # e.g. "123AB Title text"
STANDALONE_NUM_RE = re.compile(r"^(\d+[A-Z]+[A-Za-z]*)$")  # section ID on its own line (multi-line title)
PLAIN_SECTION_RE = re.compile(r"^(\d+)\s+([A-Z][A-Za-z].+)$")  # fallback: plain numeric IDs e.g. "123 Title"
AMENDMENT_RE = re.compile(r"^\s*S\.\s+\d+")  # amendment footnote e.g. "S. 123"
ACT_NUMBER_RE = re.compile(r"No\.\s+\d+\s+of\s+\d+")  # e.g. "No. 109 of 1997" boilerplate
PAGE_NUMBER_RE = re.compile(r"^\d{1,4}$")  # footer page number
AUTHORISED_RE = re.compile(r"Authorised by the")  # start of standard page header

BOILERPLATE_LINES = frozenset(
    {
        "Authorised by the Chief Parliamentary Counsel",
        "Residential Tenancies Act 1997",
    }
)

SENTENCE_END_RE = re.compile(r"[.!?]\s")


def estimate_tokens(text: str) -> int:
    """Estimate token count from word count (~0.75 tokens/word)."""
    words = len(text.split())
    return max(1, int(words / 0.75))


def is_toc_page(lines: list[str]) -> bool:
    """Detect Table of Contents pages by column headers or title text."""
    # Two detection strategies: (1) "Section Page" column headers, (2) "TABLE OF PROVISIONS" title
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
    # amendment footnotes often span multiple lines with known prefixes
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


def parse_body_lines(body_lines: list[str]) -> list[dict]:
    """Parse body text lines into hierarchical chunks with section-boundary splitting."""
    chunks: list[dict] = []
    hierarchy = {
        "part": None,
        "part_title": None,
        "division": None,
        "division_title": None,
        "subdivision": None,
        "subdivision_title": None,
    }
    last_part = None

    current_section_id: str | None = None
    current_section_title = ""
    current_body_lines: list[str] = []
    pending_section_id: str | None = None  # section ID on its own line, waiting for title on next line
    in_amendment = False  # currently skipping amendment footnote block
    prev_blank = True  # previous line was empty (helps detect section breaks)

    for line in body_lines:
        stripped = line.strip()

        if in_amendment:
            if not stripped or is_amendment_line(stripped) or is_amendment_continuation(stripped):
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
                    _flush_section(
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
                _flush_section(
                    chunks, current_section_id, current_section_title, current_body_lines, hierarchy
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
                _flush_section(
                    chunks, current_section_id, current_section_title, current_body_lines, hierarchy
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
                    _flush_section(
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
            if len(section_id) >= 3:  # filters out subsection markers like "(1)", "(a)" that happen to be on their own line
                pending_section_id = section_id
                prev_blank = False
                continue

        section_match = SECTION_RE.match(stripped)
        if section_match:
            sid = section_match.group(1)
            title = section_match.group(2)
            if _is_valid_section_title(title):
                if current_section_id is not None:
                    _flush_section(
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
                    _flush_section(
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
        _flush_section(
            chunks, current_section_id, current_section_title, current_body_lines, hierarchy
        )

    return chunks


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


def _make_text(section_id, section_title, body_text, parent_prefix):
    """Assemble full chunk text with optional parent hierarchy prefix."""
    if parent_prefix:
        return f"{parent_prefix} {section_id} {section_title}\n{body_text}"
    return f"{section_id} {section_title}\n{body_text}"


def _flush_section(chunks, section_id, section_title, body_lines, hierarchy):
    """Emit chunk(s) for current section, splitting if over token limit."""
    body_text = "\n".join(body_lines).strip()
    if not body_text:
        return

    parent_prefix = _build_parent_prefix(hierarchy)
    prefixed_text = _make_text(section_id, section_title, body_text, parent_prefix)
    tokens = estimate_tokens(prefixed_text)

    if tokens <= TOKEN_THRESHOLD:
        chunks.append(_build_chunk(section_id, section_title, prefixed_text, hierarchy, None))
    else:
        sub_chunks = _split_long_section(
            section_id, section_title, body_text, hierarchy, parent_prefix
        )
        chunks.extend(sub_chunks)


def _split_long_section(section_id, section_title, body_text, hierarchy, parent_prefix):
    """Split a long section into sub-chunks at subsection markers."""
    # split at subsection markers "(1)", "(2)", etc.; group subsections until token budget exceeded
    sub_chunks = []
    parts = re.split(r"\n(?=\(\d+[A-Za-z]?\))", body_text)

    if len(parts) <= 1:
        prefixed = _make_text(section_id, section_title, body_text, parent_prefix)
        sub_chunks.append(_build_chunk(section_id, section_title, prefixed, hierarchy, None))
        return sub_chunks

    current_text = ""
    sub_start = None
    sub_end = None

    for part in parts:
        part_stripped = part.strip()
        if not part_stripped:
            continue

        sub_match = re.match(r"\((\d+[A-Za-z]?)\)", part_stripped)
        sub_num = sub_match.group(1) if sub_match else "?"

        candidate = f"{current_text}\n{part_stripped}" if current_text else part_stripped

        if estimate_tokens(candidate) > TOKEN_THRESHOLD and current_text:
            prefixed = _make_text(section_id, section_title, current_text.strip(), parent_prefix)
            sub_range = (
                f"{section_id}({sub_start})-{section_id}({sub_end})"
                if sub_start and sub_end
                else None
            )
            sub_chunks.append(
                _build_chunk(section_id, section_title, prefixed, hierarchy, sub_range)
            )
            current_text = part
            sub_start = sub_num
            sub_end = sub_num
        else:
            current_text = candidate
            if sub_start is None:
                sub_start = sub_num
            sub_end = sub_num

    if current_text.strip():
        prefixed = _make_text(section_id, section_title, current_text.strip(), parent_prefix)
        sub_range = (
            f"{section_id}({sub_start})-{section_id}({sub_end})" if sub_start and sub_end else None
        )
        sub_chunks.append(_build_chunk(section_id, section_title, prefixed, hierarchy, sub_range))

    return sub_chunks


def _build_chunk(section_id, section_title, text, hierarchy, subsection_range):
    """Build a chunk dict with all hierarchical metadata."""
    chunk_id = f"VIC-RTA1997-s{section_id}"
    return {
        "chunk_id": chunk_id,
        "text": text,
        "state": STATE,
        "act": ACT_NAME,
        "year": ACT_YEAR,
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


def _build_parent_prefix(hierarchy):
    """Build hierarchy prefix like '[Part 2 - Division 3]'."""
    parts = []
    if hierarchy["part"]:
        parts.append(f"Part {hierarchy['part']}")
        if hierarchy["division"]:
            parts.append(f"Division {hierarchy['division']}")
            if hierarchy["subdivision"]:
                parts.append(f"Subdivision {hierarchy['subdivision']}")
    if parts:
        return "[" + " - ".join(parts) + "]"
    return ""


def extract_sections(doc: fitz.Document) -> list[dict]:
    """Extract body text from PDF, skip headers/TOC, parse into chunks."""
    all_body_lines: list[str] = []
    toc_ended = False
    last_part_info: tuple[str, str] | None = None
    seen_enacting_clause = False

    for page_num in range(doc.page_count):
        page = doc[page_num]
        raw_text = page.get_text("text")
        lines = raw_text.split("\n")

        if not toc_ended:
            if is_toc_page(lines):
                continue
            toc_ended = True
            logger.info(f"TOC ends at PDF page {page_num + 1}")

        # Standard page header: "Authorised by..." → "No. X of YYYY" → Part line → title → page number
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
            all_body_lines.append(f"Part {part_info[0]}—{part_info[1]}")

        # body_start = actno_idx + 2 skips the standard header block when present
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

            all_body_lines.append(line)

    logger.info(f"Total body lines: {len(all_body_lines)}")
    chunks = parse_body_lines(all_body_lines)
    return chunks


def main():
    """Parse VIC RTA PDF and write chunks to JSON."""
    input_path = Path(INPUT_PDF)
    if not input_path.exists():
        raise FileNotFoundError(f"PDF not found: {input_path}")

    output_path = Path(OUTPUT_JSON)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    logger.info(f"Opening PDF: {input_path}")
    doc = fitz.open(str(input_path))
    logger.info(f"PDF pages: {doc.page_count}")

    try:
        chunks = extract_sections(doc)
        logger.info(f"Sections extracted: {len(chunks)}")

        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(chunks, f, indent=2, ensure_ascii=False)

        logger.info(f"Chunks written to: {output_path}")

        total_tokens = sum(estimate_tokens(c["text"]) for c in chunks)
        logger.info(f"Total chunks: {len(chunks)}")
        logger.info(f"Total estimated tokens: {total_tokens}")

        if len(chunks) == 0:
            raise ValueError("No sections extracted — possible parsing failure")

        sample_ids = [c["section_id"] for c in chunks[:5]]
        logger.info(f"First 5 sections: {sample_ids}")

    finally:
        doc.close()


if __name__ == "__main__":
    main()
