"""
VIC-specific parser for the Residential Tenancies Regulations 2021 PDF.

Inherits from BaseParser and implements:
  - Clean text: TOC skipping, preamble removal, header/footer stripping
  - Hierarchy parsing: flat state-machine (no Part/Division/Subdivision)
  - VIC regulation chunk payload schema with instrument_type and schedule fields
"""

import logging
import re

from src.rag.data_processing.base_parser import BaseParser

STATE = "VIC"
ACT_NAME = "Residential Tenancies Regulations 2021"
ACT_YEAR = "2021"
TOKEN_THRESHOLD = 2048
CHUNK_PREFIX = "VIC-RTR2021"

REG_RE = re.compile(r"^(\d+[A-Z]?)\s+(.+)$")
SCHEDULE_HEADER_RE = re.compile(r"^Schedule\s+(\d+)\s*[\u2014\u2013\-]\s*(.+)$")
FORM_RE = re.compile(r"^FORM\s+(\d+)\b")
STANDALONE_PAGE_NUM_RE = re.compile(r"^\d{1,3}$")
AMENDMENT_MARKER_RE = re.compile(
    r"^\s*(Reg\.|S\.)\s+\d+\s+(inserted|amended|substituted|repealed)",
    re.IGNORECASE,
)

AUTHORISED_LINE = "Authorised by the Chief Parliamentary Counsel"
MID_PAGE_TITLE = "Residential Tenancies Regulations 2021"
MID_PAGE_SR = "S.R. No. 3/2021"
PREAMBLE_MARKER = "STATUTORY RULES 2021"

KNOWN_RUNNING_HEADERS = frozenset({"Endnotes", "Authorised Version"})

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
        "In",
        "An",
        "For",
        "The",
        "This",
        "If",
        "A",
        "Each",
        "Column",
        "Item",
    }
)


def _is_valid_reg_title(title: str) -> bool:
    """Check that a candidate regulation title looks like a heading."""
    if not title or len(title) < 2:
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


def _is_amendment_line(stripped: str) -> bool:
    return bool(AMENDMENT_MARKER_RE.match(stripped))


class VICRegulationParser(BaseParser):
    """Parser for the Victorian Residential Tenancies Regulations 2021 PDF."""

    def __init__(self) -> None:
        super().__init__(
            state=STATE,
            act_name=ACT_NAME,
            act_year=ACT_YEAR,
            token_threshold=TOKEN_THRESHOLD,
        )

    # ── clean_text ─────────────────────────────────────────────────────

    def clean_text(self, raw_page_texts: list[str]) -> list[str]:
        cleaned: list[str] = []
        toc_ended = False
        preamble_ended = False
        endnotes_started = False

        for page_num, page_text in enumerate(raw_page_texts):
            lines = page_text.split("\n")

            if not toc_ended:
                if self._is_toc_page(lines):
                    continue
                toc_ended = True
                self.logger.info("TOC ends at PDF page %d", page_num + 1)

            if not preamble_ended:
                body = self._process_preamble_page(lines)
                cleaned.extend(body)
                preamble_ended = True
                continue

            if not endnotes_started:
                if self._is_endnotes_start(lines):
                    endnotes_started = True
                    self.logger.info("Endnotes start at PDF page %d", page_num + 1)
                if endnotes_started:
                    break

            for line in self._strip_page_boilerplate(lines):
                cleaned.append(line)

        self.logger.info("Total body lines: %d", len(cleaned))
        cleaned = self._join_split_schedule_headers(cleaned)
        return cleaned

    @staticmethod
    def _join_split_schedule_headers(lines: list[str]) -> list[str]:
        """Join schedule header lines split across multiple lines in the PDF text."""
        result: list[str] = []
        i = 0
        while i < len(lines):
            line = lines[i]
            stripped = line.strip()
            match = SCHEDULE_HEADER_RE.match(stripped)
            if match:
                sch_num = match.group(1)
                title_parts = [match.group(2)]
                j = i + 1
                while j < len(lines):
                    next_stripped = lines[j].strip()
                    if not next_stripped:
                        break
                    if next_stripped[0].islower() or next_stripped[0] in (",", ";", ")"):
                        title_parts.append(next_stripped)
                        j += 1
                    else:
                        break
                result.append(f"Schedule {sch_num}\u2014{' '.join(title_parts)}")
                i = j
            else:
                result.append(line)
                i += 1
        return result

    @staticmethod
    def _is_toc_page(lines: list[str]) -> bool:
        """Detect Table of Provisions pages (pages 1-4).

        Page 1 has 'TABLE OF PROVISIONS'; pages 2-4 have 'Regulation'
        and 'Page' column headers plus a roman-numeral page number.
        """
        joined = "\n".join(lines[:15])
        if "TABLE OF PROVISIONS" in joined:
            return True
        stripped = [ln.strip() for ln in lines[:15]]
        try:
            reg_idx = stripped.index("Regulation")
        except ValueError:
            return False
        try:
            page_idx = stripped.index("Page")
        except ValueError:
            return False
        if abs(reg_idx - page_idx) > 5:
            return False
        for ln in stripped:
            if ln in ("i", "ii", "iii", "iv", "v", "vi", "vii", "viii", "ix", "x"):
                return True
        return False

    @staticmethod
    def _process_preamble_page(lines: list[str]) -> list[str]:
        """Extract body lines from the preamble page (page 5).

        Skips everything from STATUTORY RULES 2021 to the first
        regulation header, then keeps all remaining lines.
        """
        result: list[str] = []
        in_preamble = False
        body_started = False

        for line in lines:
            stripped = line.strip()

            if not body_started:
                if not in_preamble:
                    if stripped == PREAMBLE_MARKER:
                        in_preamble = True
                    continue

                if REG_RE.match(stripped):
                    in_preamble = False
                    body_started = True
                    result.append(line)
                continue

            result.append(line)

        return result

    @staticmethod
    def _is_endnotes_start(lines: list[str]) -> bool:
        """Check if the first body content on this page is the Endnotes heading."""
        body = VICRegulationParser._strip_page_boilerplate(lines)
        for bline in body:
            s = bline.strip()
            if not s:
                continue
            return s.lower() == "endnotes"
        return False

    @staticmethod
    def _strip_page_boilerplate(lines: list[str]) -> list[str]:
        """Strip page-level boilerplate from a body page."""
        result: list[str] = []
        phase = "auth"

        for line in lines:
            stripped = line.strip()

            if phase == "auth":
                if not stripped:
                    continue
                if stripped == AUTHORISED_LINE:
                    phase = "pre_mid"
                    continue
                phase = "body"
                result.append(line)
                continue

            if phase == "pre_mid":
                if not stripped:
                    continue
                if stripped in KNOWN_RUNNING_HEADERS:
                    phase = "pre_mid_skip"
                    continue
                if SCHEDULE_HEADER_RE.match(stripped):
                    phase = "pre_mid_skip"
                    continue
                phase = "mid_headers"

            if phase == "pre_mid_skip":
                if not stripped:
                    phase = "pre_mid"
                    continue
                if stripped == MID_PAGE_TITLE:
                    phase = "mid_headers"
                    continue
                if stripped == MID_PAGE_SR:
                    phase = "mid_headers"
                    continue
                continue

            if phase == "mid_headers":
                if not stripped:
                    continue
                if stripped == MID_PAGE_TITLE:
                    continue
                if stripped == MID_PAGE_SR:
                    continue
                if STANDALONE_PAGE_NUM_RE.match(stripped):
                    phase = "body"
                    continue
                phase = "body"

            if phase == "body":
                result.append(line)

        return result

    # ── parse_hierarchy ─────────────────────────────────────────────────

    def parse_hierarchy(self, cleaned_lines: list[str]) -> list[dict]:
        chunks: list[dict] = []

        current_schedule: str | None = None
        current_schedule_title: str | None = None

        current_section_id: str | None = None
        current_section_title: str = ""
        current_body_lines: list[str] = []
        prev_blank: bool = True

        for line in cleaned_lines:
            stripped = line.strip()

            if not stripped:
                prev_blank = True
                if current_section_id is not None:
                    current_body_lines.append("")
                continue

            schedule_match = SCHEDULE_HEADER_RE.match(stripped)
            if schedule_match:
                self._flush_pending(
                    chunks,
                    current_section_id,
                    current_section_title,
                    current_body_lines,
                    current_schedule,
                    current_schedule_title,
                )
                current_schedule = schedule_match.group(1)
                current_schedule_title = schedule_match.group(2)
                current_section_id = f"sch{current_schedule}"
                current_section_title = current_schedule_title
                current_body_lines = []
                prev_blank = False
                continue

            form_match = FORM_RE.match(stripped)
            if form_match and current_schedule is not None:
                self._flush_pending(
                    chunks,
                    current_section_id,
                    current_section_title,
                    current_body_lines,
                    current_schedule,
                    current_schedule_title,
                )
                form_num = form_match.group(1)
                current_section_id = f"sch{current_schedule}-form{form_num}"
                current_section_title = f"FORM {form_num}"
                current_body_lines = []
                prev_blank = False
                continue

            reg_match = REG_RE.match(stripped)
            if reg_match:
                sid = reg_match.group(1)
                title = reg_match.group(2)
                if prev_blank and _is_valid_reg_title(title) and not _looks_like_date(title):
                    self._flush_pending(
                        chunks,
                        current_section_id,
                        current_section_title,
                        current_body_lines,
                        current_schedule,
                        current_schedule_title,
                    )
                    if current_schedule is not None:
                        current_section_id = f"sch{current_schedule}"
                        current_section_title = current_schedule_title or ""
                    else:
                        current_section_id = sid
                        current_section_title = title
                    current_body_lines = []
                    prev_blank = False
                    continue

            if current_section_id is not None:
                current_body_lines.append(stripped)
                prev_blank = False

        if current_section_id is not None:
            self._flush_pending(
                chunks,
                current_section_id,
                current_section_title,
                current_body_lines,
                current_schedule,
                current_schedule_title,
            )

        return chunks

    @staticmethod
    def _resolve_part(section_id: str) -> str | None:
        """Map regulation/form ID to VIC Act Part for retrieval filtering."""
        if section_id.startswith("sch") and "-form" in section_id:
            try:
                form_num = int(section_id.split("-form")[-1])
            except ValueError:
                return None
            if 6 <= form_num <= 11:
                return "3"  # Rooming house forms (Form 6–11)
            elif 12 <= form_num <= 15:
                return "4"  # Caravan park forms (Form 12–15)
            elif 16 <= form_num <= 22:
                return "4A"  # Site agreement forms (Form 16–22)
            return None  # General forms (1–5, 23–25)
        if section_id.startswith("sch") or not section_id.isdigit():
            return None  # Schedule-level chunks (sch2–sch5), non-numeric IDs
        reg_num = int(section_id)
        if 37 <= reg_num <= 53:
            return "3"
        elif 54 <= reg_num <= 71:
            return "4"
        elif 72 <= reg_num <= 89:
            return "4A"
        return None  # General: 1–36, 90–98

    def _flush_pending(
        self,
        chunks: list[dict],
        section_id: str | None,
        section_title: str,
        body_lines: list[str],
        schedule_num: str | None,
        schedule_title: str | None,
    ) -> None:
        if section_id is None:
            return
        self._flush_reg(
            chunks,
            section_id,
            section_title,
            body_lines,
            schedule_num,
            schedule_title,
        )

    # ── _flush_reg ─────────────────────────────────────────────────────

    def _flush_reg(
        self,
        chunks: list[dict],
        section_id: str,
        section_title: str,
        body_lines: list[str],
        schedule_num: str | None,
        schedule_title: str | None,
    ) -> None:
        body_text = "\n".join(body_lines).strip()
        if not body_text:
            return

        non_blank = [ln for ln in body_lines if ln.strip()]
        if non_blank and all(_is_amendment_line(ln) for ln in non_blank):
            self.logger.debug("Skipping amendment-only section %s", section_id)
            return

        hierarchy: dict = {
            "part": None,
            "part_title": None,
            "division": None,
            "division_title": None,
            "subdivision": None,
            "subdivision_title": None,
            "schedule": f"Schedule {schedule_num}" if schedule_num else None,
            "schedule_title": schedule_title if schedule_num else None,
        }
        hierarchy["part"] = self._resolve_part(section_id)

        parent_prefix = ""
        if schedule_num:
            parent_prefix = f"[Schedule {schedule_num}]"
            if "form" in section_id:
                display_title = section_title
            else:
                display_title = f"Schedule {schedule_num}\u2014{schedule_title}"
        else:
            display_title = section_title

        prefixed_text = self._make_text(section_id, display_title, body_text, parent_prefix)
        tokens = self.estimate_tokens(prefixed_text)

        if tokens <= self.token_threshold:
            chunks.append(
                self._build_chunk(section_id, display_title, prefixed_text, hierarchy, None)
            )
        else:
            sub_chunks = self._split_long_section(
                section_id, display_title, body_text, hierarchy, parent_prefix
            )
            chunks.extend(sub_chunks)

    # ── _build_chunk ────────────────────────────────────────────────────

    def _build_chunk(
        self,
        section_id: str,
        section_title: str,
        text: str,
        hierarchy: dict,
        subsection_range: str | None = None,
    ) -> dict:
        if section_id.startswith("sch"):
            chunk_id = f"{CHUNK_PREFIX}-{section_id}"
        elif hierarchy.get("schedule"):
            chunk_id = f"{CHUNK_PREFIX}-sch{hierarchy['schedule'].split()[-1]}"
        else:
            chunk_id = f"{CHUNK_PREFIX}-r{section_id}"

        return {
            "chunk_id": chunk_id,
            "text": text,
            "state": self.state,
            "act": self.act_name,
            "year": self.act_year,
            "instrument_type": "regulation",
            "section_id": section_id,
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


# ── main ─────────────────────────────────────────────────────────────────

INPUT_PDF = "data/raw/21-003sra authorised-regulation-vic.pdf"
OUTPUT_JSON = "data/processed/vic_regulation_chunks.json"


def main() -> int:
    """Parse VIC Regulation PDF and write chunks to JSON."""
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    parser = VICRegulationParser()
    chunks = parser.run(INPUT_PDF, OUTPUT_JSON)
    print(f"Parsed {len(chunks)} chunks -> {OUTPUT_JSON}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
