"""
Configuration-driven parser factory for Australian tenancy legislation.

Provides:
  - JURISDICTION_CONFIGS  — static registry of per-state regexes and options
  - GenericParser         — single BaseParser subclass driven by config
  - ParserFactory         — facade returning the right parser per state

All 8 jurisdictions (VIC, NSW, QLD, SA, WA, TAS, ACT, NT) are driven by
configuration — no separate parser subclasses.
"""

from __future__ import annotations

import re
from pathlib import Path

from src.data_processing.base_parser import BaseParser

# ── Static config registry ────────────────────────────────────────────

JURISDICTION_CONFIGS: dict[str, dict] = {
    "QLD": {
        "state": "QLD",
        "act_name": "Residential Tenancies and Rooming Accommodation Act 2008",
        "act_year": "2008",
        "chunk_id_prefix": "QLD-RTRAA2008",
        "regexes": {
            "chapter_full": r"^Chapter\s+(\d+[A-Za-z]*)\s+(.+)$",
            "chapter_id_only": r"^Chapter\s+(\d+[A-Za-z]*)$",
            "part_full": r"^Part\s+(\d+[A-Za-z]*)\s+(.+)$",
            "part_id_only": r"^Part\s+(\d+[A-Za-z]*)$",
            "division_full": r"^Division\s+(\d+[A-Za-z]*)\s+(.+)$",
            "division_id_only": r"^Division\s+(\d+[A-Za-z]*)$",
            "section_standalone": r"^(\d+[A-Za-z]*)$",
            "section_inline": r"^(\d+[A-Za-z]*)\s+([A-Z][A-Za-z].+)$",
            "toc_line": r"\.{4,}\s*\d+$",
        },
        "options": {
            "toc_keywords": ["Contents"],
            "boilerplate_lines": frozenset({
                "Queensland",
                "Current as at",
                "\u00a9 State of Queensland",
            }),
            "header_patterns": ["Queensland", "Current as at"],
            "footer_patterns": [r"^Page \d+$"],
            "strip_bare_page_numbers": False,
            "title_min_length": 2,
            "section_id_min_length": 1,
            "title_prose_starters": frozenset({"penalty", "years", "months", "days"}),
            "has_chapters": True,
            "require_prev_blank": False,
            "hierarchy_components": ["chapter", "part", "division", "subdivision"],
        },
    },
    "SA": {
        "state": "SA",
        "act_name": "Residential Tenancies Act 1995",
        "act_year": "1995",
        "chunk_id_prefix": "SA-RTA1995",
        "regexes": {
            "part_full": r"^Part\s+(\d+[A-Za-z]*)\s*—\s*(.+)$",
            "part_id_only": r"^Part\s+(\d+[A-Za-z]*)$",
            "division_full": r"^Division\s+(\d+[A-Za-z]*)\s*—\s*(.+)$",
            "division_id_only": r"^Division\s+(\d+[A-Za-z]*)$",
            "section_standalone": r"^(\d+[A-Za-z]*)$",
            "section_inline": r"^(\d+[A-Za-z]*)\s*—\s*([A-Z].+)$",
            "enacting_clause": r"The Parliament of South Australia enacts",
        },
        "options": {
            "toc_keywords": ["Contents"],
            "boilerplate_lines": frozenset(),
            "header_patterns": [
                "Residential Tenancies Act 1995",
                "—Part ",
                "—Division ",
                "—Subdivision ",
            ],
            "footer_patterns": [
                r"^\d+$",
                r"Published under the",
            ],
            "title_min_length": 2,
            "section_id_min_length": 1,
            "title_prose_starters": frozenset({"penalty", "years", "months"}),
            "has_chapters": False,
            "has_schedules": False,
            "require_prev_blank": True,
            "hierarchy_components": ["part", "division", "subdivision"],
        },
    },
    "WA": {
        "state": "WA",
        "act_name": "Residential Tenancies Act 1987",
        "act_year": "1987",
        "chunk_id_prefix": "WA-RTA1987",
        "regexes": {
            "part_full": r"^Part\s+([IVXLCDM]+|\d+[A-Za-z]*)\s*—\s*(.+)$",
            "part_id_only": r"^Part\s+([IVXLCDM]+|\d+[A-Za-z]*)$",
            "division_full": r"^Division\s+(\d+[A-Za-z]*)\s*—\s*(.+)$",
            "schedule": r"^Schedule\s+\d+",
            "section_standalone": r"^(\d+[A-Za-z]*\.)$",
            "section_inline": r"^(\d+[A-Za-z]*\.)\s+([A-Z][A-Za-z].+)$",
        },
        "options": {
            "toc_keywords": ["Contents"],
            "header_patterns": [
                "As at",
                "Official Version",
                "Published on www.legislation.wa.gov.au",
                "Western Australia",
                "[PCO",
            ],
            "footer_patterns": [r"^page [ivxlcdm]+$", r"^\d{1,4}$"],
            "boilerplate_lines": frozenset({
                "As at",
                "Official Version",
                "[PCO",
                "Published on",
                "Western Australia",
            }),
            "title_min_length": 2,
            "section_id_min_length": 1,
            "has_chapters": False,
            "has_schedules": True,
            "skip_empty_pages": True,
            "require_prev_blank": True,
            "hierarchy_components": ["part", "division", "subdivision"],
        },
    },
    "TAS": {
        "state": "TAS",
        "act_name": "Residential Tenancy Act 1997",
        "act_year": "1997",
        "chunk_id_prefix": "TAS-RTA1997",
        "regexes": {
            "part_full": r"^Part\s+(\d+[A-Za-z]*)\s+-\s+(.+)$",
            "division_full": r"^Division\s+(\d+[A-Za-z]*)\s+-\s+(.+)$",
            "section_standalone": r"^(\d+[A-Za-z]*\.)$",
            "section_inline": r"^(\d+[A-Za-z]*\.)\s+([A-Z][A-Za-z].+)$",
        },
        "options": {
            "toc_keywords": ["Contents"],
            "boilerplate_lines": frozenset(),
            "header_patterns": [],
            "footer_patterns": [r"^\d{1,4}$"],
            "title_min_length": 2,
            "section_id_min_length": 1,
            "has_chapters": False,
            "has_schedules": True,
            "require_prev_blank": True,
            "hierarchy_components": ["part", "division", "subdivision"],
        },
    },
    "ACT": {
        "state": "ACT",
        "act_name": "Residential Tenancies Act 1997",
        "act_year": "1997",
        "chunk_id_prefix": "ACT-RTA1997",
        "regexes": {
            "part_full": r"^Part\s+(\d+[A-Za-z]*)\s+(.+)$",
            "part_id_only": r"^Part\s+(\d+[A-Za-z]*)$",
            "division_full": r"^Division\s+(\d+(?:\.\d+)?[A-Z]?)\s+(.+)$",
            "division_id_only": r"^Division\s+(\d+(?:\.\d+)?[A-Z]?)$",
            "section_standalone": r"^(\d+[A-Za-z]*)$",
            "section_inline": r"^(\d+[A-Za-z]*)\s+([A-Z][A-Za-z].+)$",
        },
        "options": {
            "toc_keywords": ["Contents"],
            "boilerplate_lines": frozenset({
                "Authorised by the ACT Parliamentary",
                "Australian Capital Territory",
                "R84",
                "Effective:",
                "Republication date:",
                "Republication No",
                "Last amendment",
            }),
            "header_patterns": [
                "Authorised by the ACT Parliamentary Counsel",
                "Australian Capital Territory",
                "R84",
                "Effective:",
                "contents",
            ],
            "footer_patterns": [r"^\d{1,4}$"],
            "title_min_length": 2,
            "section_id_min_length": 1,
            "has_chapters": False,
            "has_schedules": False,
            "require_prev_blank": False,
            "hierarchy_components": ["part", "division", "subdivision"],
        },
    },
    "NT": {
        "state": "NT",
        "act_name": "Residential Tenancies Act 1999",
        "act_year": "1999",
        "chunk_id_prefix": "NT-RTA1999",
        "regexes": {
            "part_full": r"^Part\s+(\d+[A-Za-z]*)\s+(.+)$",
            "part_id_only": r"^Part\s+(\d+[A-Za-z]*)$",
            "division_full": r"^Division\s+(\d+[A-Za-z]*)\s+(.+)$",
            "division_id_only": r"^Division\s+(\d+[A-Za-z]*)$",
            "section_standalone": r"^(\d+[A-Za-z]*)$",
            "section_inline": r"^(\d+[A-Za-z]*)\s+([A-Z][A-Za-z].+)$",
            "toc_line": r"\.{7,}\s*\d+$",
        },
        "options": {
            "toc_keywords": ["Table of provisions"],
            "boilerplate_lines": frozenset({
                "NORTHERN TERRITORY OF AUSTRALIA",
                "Residential Tenancies Act 1999",
            }),
            "header_patterns": [
                "NORTHERN TERRITORY OF AUSTRALIA",
                "Residential Tenancies Act 1999",
            ],
            "footer_patterns": [],
            "strip_bare_page_numbers": False,
            "title_min_length": 2,
            "section_id_min_length": 1,
            "has_chapters": False,
            "has_schedules": False,
            "require_prev_blank": True,
            "hierarchy_components": ["part", "division", "subdivision"],
        },
    },
    "NSW": {
        "state": "NSW",
        "act_name": "Residential Tenancies Act 2010",
        "act_year": "2010",
        "chunk_id_prefix": "NSW-RTA2010",
        "regexes": {
            "part_full": r"^Part\s+(\d+)\s+(.+)$",
            "division_full": r"^Division\s+(\d+)\s+(.+)$",
            "subdivision_full": r"^Subdivision\s+(\d+)\s+(.+)$",
            "section_standalone": r"^(\d+[A-Z]*)$",
            "section_inline": r"^(\d+[A-Z]*)\s+([A-Z][A-Za-z].+)$",
            "enacting_clause": r"An Act with respect to the rights and obligations of landlords and tenants",
        },
        "options": {
            "toc_keywords": ["Contents"],
            "toc_density_threshold": 0.5,
            "toc_false_positive_guard": False,
            "enacting_clause_mode": "line_gate",
            "enacting_continuations": [r"and other matters", r"and for other purposes"],
            "footer_mode": "backward",
            "footer_anchor_patterns": [
                r"Page \d+ of \d+",
                r"Residential Tenancies Act 2010 No 42",
            ],
            "footer_strip_lines": 2,
            "header_patterns": ["New South Wales", "Residential Tenancies Act 2010"],
            "boilerplate_lines": frozenset({
                "Residential Tenancies Act 2010 No 42",
                "New South Wales",
            }),
            "title_min_length": 4,
            "title_require_uppercase": True,
            "title_blocklist": frozenset({
                "Note", "Note.", "Notes", "Notes.", "Penalty",
                "Maximum", "penalty", "Maximum penalty",
                "Copyright", "Disclaimer", "Privacy",
            }),
            "has_chapters": False,
            "has_schedules": False,
            "require_prev_blank": False,
            "strip_bare_page_numbers": False,
            "hierarchy_components": ["part", "division", "subdivision"],
        },
    },
    "VIC": {
        "state": "VIC",
        "act_name": "Residential Tenancies Act 1997",
        "act_year": "1997",
        "chunk_id_prefix": "VIC-RTA1997",
        "regexes": {
            "part_full": r"^Part\s+(\d+[A-Za-z]*)\s*[—-]\s*(.+)$",
            "part_id_only": r"^Part\s+(\d+[A-Za-z]*)$",
            "division_full": r"^Division\s+(\d+[A-Za-z]*)\s*[—-]\s*(.+)$",
            "division_id_only": r"^Division\s+(\d+[A-Za-z]*)$",
            "subdivision_full": r"^Subdivision\s+(\d+[A-Za-z]*)\s*[—-]\s*(.+)$",
            "subdivision_id_only": r"^Subdivision\s+(\d+[A-Za-z]*)$",
            "section_standalone": r"^(\d+[A-Z]+[A-Za-z]*)$",
            "section_inline": r"^(\d+[A-Z]+[A-Za-z]*)\s+(.+)$",
            "section_plain": r"^(\d+)\s+([A-Z][A-Za-z].+)$",
            "amendment": r"^\s*S\.\s+\d+",
            "enacting_clause": r"The Parliament of Victoria enacts as follows:",
        },
        "options": {
            "toc_keywords": ["TABLE OF PROVISIONS"],
            "toc_continuation_enabled": False,
            "toc_adjacent_patterns": [(r"^Section$", r"^Page$")],
            "header_mode": "structural",
            "header_authorised_re": r"Authorised by the",
            "header_act_number_re": r"No\.\s+\d+\s+of\s+\d+",
            "header_body_start_offset": 2,
            "header_inject_part": True,
            "enacting_clause_mode": "line_gate",
            "footer_patterns": [],
            "strip_bare_page_numbers": False,
            "clean_text_skip_patterns": [
                r"^\d+\s+(?:January|February|March|April|May|June|"
                r"July|August|September|October|November|December)\s+\d{4}$",
                r"^Authorised Version",
            ],
            "title_min_length": 2,
            "title_allow_digit_prefix": False,
            "title_prose_starters": frozenset({
                "penalty", "Penalty", "years", "year", "months", "month",
                "days", "day", "hours", "hour", "business", "p.m.", "a.m.",
            }),
            "title_blocklist": frozenset(),
            "has_chapters": False,
            "has_schedules": False,
            "require_prev_blank": True,
            "deduplicate_hierarchy": True,
            "has_amendments": True,
            "amendment_blank_is_continuation": True,
            "amendment_continuation_patterns": [
                r"^s\.\s", r"^Nos?\s", r"^No\.\s", r"^amended\s",
                r"^inserted\s", r"^substituted\s", r"^repealed\s",
                r"\([a-z]+\)", r"^Note\s+to\s", r"^S\.\s",
            ],
            "amendment_roman_continuation": True,
            "boilerplate_lines": frozenset({
                "Residential Tenancies Act 1997",
                "No. 109 of 1997",
                "Authorised by the Chief Parliamentary Counsel",
            }),
            "inline_boilerplate_patterns": [r"No\.\s+\d+\s+of\s+\d+"],
            "hierarchy_components": ["part", "division", "subdivision"],
        },
    },
}


# ── GenericParser ──────────────────────────────────────────────────────


class GenericParser(BaseParser):
    """Single parser driven by a JURISDICTION_CONFIGS entry.

    Overrides ``clean_text()`` and ``parse_hierarchy()`` with generic
    implementations controlled entirely by regexes and option flags from
    the config dict passed to the constructor.

    clean_text pipeline (per-page, steps 1-10):
        1. Backward footer stripping (NSW: bottom-up anchor scan)
        2. TOC detection (keywords + density + adjacent patterns)
        3. Structural header detection (VIC: AUTHORISED → ACT_NUMBER)
        4. Part-from-header injection (VIC: synthetic Part line)
        5. Standard header stripping (header_patterns, first 5 lines)
        6. Enacting-clause gate (line_gate mode: skip until clause found)
        7. Standard footer stripping (footer_patterns line-by-line)
        8. Boilerplate + clean_text_skip_patterns
        9. Bare page number filtering
        10. Body line accumulation
    """

    MONTH_NAMES: frozenset[str] = frozenset({
        "January", "February", "March", "April", "May", "June",
        "July", "August", "September", "October", "November", "December",
    })

    _CASE_INSENSITIVE_KEYS: frozenset[str] = frozenset({
        "part_full", "part_id_only", "division_full", "division_id_only",
        "subdivision_full", "subdivision_id_only", "chapter_full", "chapter_id_only",
        "schedule", "amendment", "enacting_clause",
    })

    def __init__(self, config: dict) -> None:
        super().__init__(
            state=config["state"],
            act_name=config["act_name"],
            act_year=config["act_year"],
        )
        self.cfg = config
        self.opt: dict = config["options"]

        # ── pre-compile every regex ONCE ──────────────────────────────
        self._re: dict[str, re.Pattern | None] = {}
        for key, pattern in config.get("regexes", {}).items():
            flags = re.IGNORECASE if key in self._CASE_INSENSITIVE_KEYS else 0
            self._re[key] = re.compile(pattern, flags) if pattern else None

        # ── pre-compile footer patterns ───────────────────────────────
        self._footer_res: list[re.Pattern] = [
            re.compile(p) for p in self.opt.get("footer_patterns", [])
        ]

        # ── pre-compile clean_text_skip_patterns ──────────────────────
        self._skip_res: list[re.Pattern] = [
            re.compile(p) for p in self.opt.get("clean_text_skip_patterns", [])
        ]

        # ── pre-compile inline_boilerplate_patterns ───────────────────
        self._inline_boilerplate_res: list[re.Pattern] = [
            re.compile(p) for p in self.opt.get("inline_boilerplate_patterns", [])
        ]

        # ── frozen constants ──────────────────────────────────────────
        self._header_patterns: tuple[str, ...] = tuple(
            self.opt.get("header_patterns", [])
        )
        self._boilerplate: frozenset[str] = self.opt.get(
            "boilerplate_lines", frozenset()
        )
        self._hierarchy_levels: list[str] = self.opt.get(
            "hierarchy_components", ["part", "division", "subdivision"]
        )

        # ── amendment continuation patterns ───────────────────────────
        _default_cont = [
            r"^s\.\s", r"^Nos?\s", r"^No\.\s", r"^amended\s",
            r"^inserted\s", r"^substituted\s", r"^repealed\s",
        ]
        self._amendment_cont_res: list[re.Pattern] = [
            re.compile(p, re.IGNORECASE)
            for p in self.opt.get("amendment_continuation_patterns", _default_cont)
        ]

    # ── clean_text (override) ────────────────────────────────────────

    def clean_text(self, raw_page_texts: list[str]) -> list[str]:
        """10-step per-page pipeline driven by config flags."""
        cleaned: list[str] = []

        # Config-driven flags
        toc_continuation = self.opt.get("toc_continuation_enabled", True)
        toc_done = False
        toc_in_progress = False

        footer_mode = self.opt.get("footer_mode", "line")
        footer_anchors = [
            re.compile(p) for p in self.opt.get("footer_anchor_patterns", [])
        ]
        footer_strip = self.opt.get("footer_strip_lines", 2)

        header_mode = self.opt.get("header_mode")
        header_auth = re.compile(self.opt["header_authorised_re"]) if self.opt.get("header_authorised_re") else None
        header_actno = re.compile(self.opt["header_act_number_re"]) if self.opt.get("header_act_number_re") else None
        header_offset = self.opt.get("header_body_start_offset", 0)
        header_inject = self.opt.get("header_inject_part", False)
        last_part_info: tuple[str, str] | None = None

        clause_mode = self.opt.get("enacting_clause_mode")
        clause_cont: list[re.Pattern] = [
            re.compile(p) for p in self.opt.get("enacting_continuations", [])
        ]
        clause_seen = clause_mode is None  # If no clause mode, gate is already open
        clause_line_hit = False  # Just found the clause line — check for continuations next

        strip_page_numbers = self.opt.get("strip_bare_page_numbers", True)
        skip_empty = self.opt.get("skip_empty_pages", True)

        adj_patterns = self.opt.get("toc_adjacent_patterns", [])

        for page_text in raw_page_texts:
            lines = page_text.split("\n")

            # Skip completely empty pages
            if skip_empty and not any(ln.strip() for ln in lines):
                continue

            # ── Step 1: Backward footer stripping ────────────────────
            if footer_mode == "backward" and footer_anchors:
                lines = self._strip_backward_footer(
                    lines, footer_anchors, footer_strip
                )
                if not lines:
                    continue

            # ── Step 2: TOC detection ─────────────────────────────────
            if not toc_done:
                if self._should_skip_toc(
                    lines,
                    toc_in_progress,
                    adj_patterns,
                ):
                    toc_in_progress = True
                    body_after = self._split_mixed_toc_body(lines)
                    if body_after is not None:
                        toc_done = True
                        lines = body_after
                    else:
                        continue
                else:
                    if toc_continuation or not toc_in_progress:
                        toc_done = True
                    toc_in_progress = False

            # ── Step 3: Structural header detection (VIC) ─────────────
            body_start = 0
            has_std_header = False
            auth_idx = -1
            if header_mode == "structural" and header_auth and header_actno:
                for i, line in enumerate(lines):
                    stripped = line.strip()
                    if auth_idx < 0 and header_auth.match(stripped):
                        auth_idx = i
                    elif auth_idx >= 0 and header_actno.match(stripped):
                        if i > auth_idx:
                            has_std_header = True
                            body_start = i + header_offset
                            break

            # ── Step 4: Part-from-header injection (VIC) ──────────────
            if header_inject and auth_idx >= 0 and auth_idx + 1 < len(lines):
                part_full_re = self._re.get("part_full")
                if part_full_re:
                    next_line = lines[auth_idx + 1].strip()
                    pm = part_full_re.match(next_line)
                    if pm:
                        part_info = (pm.group(1), pm.group(2))
                        if part_info != last_part_info:
                            last_part_info = part_info
                            sep = "—"  # VIC uses em-dash
                            cleaned.append(
                                f"Part {part_info[0]}{sep}{part_info[1]}"
                            )

            # ── Step 5: Standard header stripping ─────────────────────
            body_lines = self._strip_page_wrappers(lines)

            # Apply body_start offset (from structural header)
            if body_start > 0:
                body_start = min(body_start, len(body_lines))
                body_lines = body_lines[body_start:]

            # ── Steps 6-10: Enacting gate → footer → boilerplate → page nums → body
            for line in body_lines:
                stripped = line.strip()

                # Step 6: Enacting-clause gate (line_gate mode)
                if not clause_seen and clause_mode == "line_gate":
                    if not stripped:
                        continue
                    if self._re.get("enacting_clause") and self._re["enacting_clause"].search(stripped):
                        clause_line_hit = True  # Skip clause line; continuations next
                        continue
                    if clause_line_hit:
                        # After clause: skip continuation lines before opening gate
                        if any(cr.match(stripped) for cr in clause_cont):
                            continue
                        # First non-continuation line after clause → gate open
                        clause_seen = True
                        clause_line_hit = False
                        # Fall through to normal processing
                    else:
                        # Before clause found: skip all preamble
                        continue

                if not stripped:
                    if clause_seen:
                        cleaned.append("")
                    continue

                # Step 7: Standard footer stripping (line mode)
                if footer_mode != "backward" and any(
                    r.search(stripped) for r in self._footer_res
                ):
                    continue

                # Step 8: Boilerplate + clean_text_skip_patterns
                if stripped in self._boilerplate:
                    continue
                if any(r.search(stripped) for r in self._skip_res):
                    continue

                # Step 9: Bare page number filtering
                if strip_page_numbers and self._is_bare_page_number(stripped):
                    if has_std_header or header_mode != "structural":
                        continue

                # Step 10: Body line accumulation
                cleaned.append(stripped)

        # Strip blank lines from start of cleaned text (NSW preamble residue)
        while cleaned and not cleaned[0].strip():
            cleaned.pop(0)

        self.logger.info("Cleaned body lines: %d", len(cleaned))
        return cleaned

    # ── parse_hierarchy (override) ────────────────────────────────────

    def parse_hierarchy(self, cleaned_lines: list[str]) -> list[dict]:
        """Unified state machine driven by config regexes and options."""
        chunks: list[dict] = []
        hierarchy = self._new_hierarchy()
        stats: dict[str, int] = {"parsed": 0, "skipped": 0}

        current: dict | None = None  # {section_id, title, body_lines}
        pending_section_id: str | None = None
        pending_hierarchy: tuple[str, str] | None = None  # (level, id)
        prev_blank = True
        in_amendment = False

        # Deduplication tracking (VIC: repeated page headers)
        dedup = self.opt.get("deduplicate_hierarchy", False)
        last_hierarchy_vals: dict[str, str | None] = {}

        for line in cleaned_lines:
            stripped = line.strip()

            # ── Amendment skip mode ─────────────────────────────────
            if self.opt.get("has_amendments") and self._re.get("amendment"):
                if in_amendment:
                    if not stripped or self._is_amendment_continuation(stripped):
                        continue
                    in_amendment = False
                    prev_blank = True  # VIC: reset blank flag on amendment exit
                elif self._re["amendment"].match(stripped):
                    in_amendment = True
                    continue

            # ── Blank line ───────────────────────────────────────────
            if not stripped:
                prev_blank = True
                if current:
                    current["body_lines"].append("")
                continue

            # ── Inline boilerplate patterns (VIC: ACT_NUMBER_RE) ─────
            if any(r.match(stripped) for r in self._inline_boilerplate_res):
                continue

            # ── Resolve pending hierarchy (multi-line title) ──────────
            if pending_hierarchy:
                level, hid = pending_hierarchy
                if self._is_valid_title(stripped) and not self._is_any_hierarchy(stripped):
                    current = self._flush_if_active(chunks, current, hierarchy)
                    hierarchy[level] = self._qualify_part_id(level, hid, hierarchy)
                    hierarchy[f"{level}_title"] = stripped
                    self._reset_below(hierarchy, level)
                    if dedup:
                        last_hierarchy_vals[level] = hierarchy[level]
                        self._clear_below_dedup(last_hierarchy_vals, level)
                    pending_hierarchy = None
                    prev_blank = True
                    continue
                else:
                    pending_hierarchy = None  # discard false positive

            # ── Schedule reset ───────────────────────────────────────
            if (
                self.opt.get("has_schedules")
                and self._re.get("schedule")
                and self._re["schedule"].match(stripped)
            ):
                current = self._flush_if_active(chunks, current, hierarchy)
                hierarchy = self._new_hierarchy()
                prev_blank = True
                continue

            # ── Hierarchy detection (Chapter / Part / Division / Subdivision) ─
            hierarchy_matched = False
            for level in self._hierarchy_levels:
                full_key = f"{level}_full"
                id_key = f"{level}_id_only"

                # Full match — ID + title inline
                if self._re.get(full_key):
                    m = self._re[full_key].match(stripped)
                    if m:
                        hid = m.group(1)
                        if dedup and last_hierarchy_vals.get(level) == hid:
                            hierarchy_matched = True
                            break
                        current = self._flush_if_active(chunks, current, hierarchy)
                        hierarchy[level] = self._qualify_part_id(level, hid, hierarchy)
                        hierarchy[f"{level}_title"] = (
                            m.group(2) if m.lastindex and m.lastindex >= 2 else ""
                        )
                        self._reset_below(hierarchy, level)
                        if dedup:
                            last_hierarchy_vals[level] = hierarchy[level]
                            self._clear_below_dedup(last_hierarchy_vals, level)
                        hierarchy_matched = True
                        break

                # ID-only match — title on next line
                if self._re.get(id_key):
                    m = self._re[id_key].match(stripped)
                    if m:
                        hid = m.group(1)
                        if dedup and last_hierarchy_vals.get(level) == hid:
                            hierarchy_matched = True
                            break
                        pending_hierarchy = (level, hid)
                        hierarchy_matched = True
                        break

            if hierarchy_matched:
                prev_blank = True
                continue

            # ── Section detection ────────────────────────────────────
            # 1. Resolve pending standalone ID
            if pending_section_id:
                if self._is_valid_title(stripped) and not self._is_any_hierarchy(stripped):
                    current = self._flush_if_active(chunks, current, hierarchy)
                    current = {
                        "section_id": pending_section_id,
                        "title": stripped,
                        "body_lines": [],
                    }
                    pending_section_id = None
                    prev_blank = False
                    continue
                else:
                    # Not a title — append to body and discard pending
                    if current:
                        current["body_lines"].append(pending_section_id)
                    pending_section_id = None

            # 2. Standalone section ID (id on its own line)
            if self._re.get("section_standalone"):
                m = self._re["section_standalone"].match(stripped)
                min_len = self.opt.get("section_id_min_length", 1)
                if m and len(m.group(1)) >= min_len:
                    pending_section_id = m.group(1)
                    prev_blank = False
                    continue

            # 3. Inline section ID + title (same line)
            if self._re.get("section_inline"):
                m = self._re["section_inline"].match(stripped)
                if m and self._is_valid_title(m.group(2)):
                    current = self._flush_if_active(chunks, current, hierarchy)
                    current = {
                        "section_id": m.group(1),
                        "title": m.group(2),
                        "body_lines": [],
                    }
                    prev_blank = False
                    continue

            # 4. Plain numeric fallback (if configured)
            if self._re.get("section_plain"):
                m = self._re["section_plain"].match(stripped)
                if (
                    m
                    and self._is_valid_title(m.group(2))
                    and (
                        not self.opt.get("require_prev_blank", True) or prev_blank
                    )
                ):
                    current = self._flush_if_active(chunks, current, hierarchy)
                    current = {
                        "section_id": m.group(1),
                        "title": m.group(2),
                        "body_lines": [],
                    }
                    prev_blank = False
                    continue

            # ── Body line ────────────────────────────────────────────
            if current:
                current["body_lines"].append(stripped)
            prev_blank = False

        # End-of-loop flush
        if current:
            self._flush_section(chunks, stats, hierarchy, current)

        self.logger.info(
            "Parse stats — parsed: %d, skipped: %d",
            stats["parsed"],
            stats["skipped"],
        )
        return chunks

    # ── _build_chunk (override) ──────────────────────────────────────

    def _build_chunk(
        self,
        section_id: str,
        section_title: str,
        text: str,
        hierarchy: dict,
        subsection_range: str | None = None,
    ) -> dict:
        return {
            "chunk_id": f"{self.cfg['chunk_id_prefix']}-s{section_id}",
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

    # ── _build_parent_prefix (override) ──────────────────────────────

    def _build_parent_prefix(self, hierarchy: dict) -> str:
        """Build hierarchy prefix respecting configurable component order."""
        labels = {
            "chapter": "Chapter",
            "part": "Part",
            "division": "Division",
            "subdivision": "Subdivision",
        }
        parts: list[str] = []
        for comp in self._hierarchy_levels:
            val = hierarchy.get(comp)
            if val:
                if comp == "part" and "." in val:
                    val = val.split(".", 1)[1]
                parts.append(f"{labels[comp]} {val}")
        if parts:
            return "[" + " - ".join(parts) + "]"
        return ""

    # ── Private helpers ──────────────────────────────────────────────

    def _strip_backward_footer(
        self,
        lines: list[str],
        anchors: list[re.Pattern],
        strip_lines: int,
    ) -> list[str]:
        """Remove footer lines from end of page (NSW-style backward scan)."""
        footer_start = None
        # Scan last strip_lines+2 lines for anchor patterns
        lookback = strip_lines + 2
        for i in range(len(lines) - 1, max(-1, len(lines) - lookback - 1), -1):
            stripped = lines[i].strip()
            if any(a.match(stripped) for a in anchors):
                footer_start = i - strip_lines
                break
        if footer_start is None:
            footer_start = max(0, len(lines) - strip_lines - 1)
        footer_start = max(0, footer_start)
        return lines[:footer_start]

    def _should_skip_toc(
        self,
        lines: list[str],
        toc_in_progress: bool,
        adj_patterns: list[tuple[str, str]],
    ) -> bool:
        """Determine if a page should be skipped as TOC."""
        # Adjacent-pattern check (VIC: "Section" + "Page" column headers)
        if adj_patterns:
            for p1, p2 in adj_patterns:
                for i in range(min(5, len(lines) - 1)):
                    if re.match(p1, lines[i].strip()) and re.match(
                        p2, lines[i + 1].strip()
                    ):
                        return True

        toc_threshold = self.opt.get("toc_density_threshold", 0.3)

        if not toc_in_progress:
            # First TOC page: must contain a keyword
            return self._is_toc_page(lines, require_keyword=True, threshold=toc_threshold)

        # Continuation page: density-only check (no keyword required)
        if not self._is_toc_page(lines, require_keyword=False, threshold=toc_threshold):
            return False

        # False-positive guard: hierarchy markers on page → not TOC
        if self.opt.get("toc_false_positive_guard", True):
            for level in self._hierarchy_levels:
                for suffix in ("_full", "_id_only"):
                    key = f"{level}{suffix}"
                    if self._re.get(key):
                        for line in lines:
                            if self._re[key].match(line.strip()):
                                return False
        return True

    def _is_toc_page(
        self,
        lines: list[str],
        require_keyword: bool = True,
        threshold: float | None = None,
    ) -> bool:
        """Detect TOC: keyword in first 10 lines AND toc_line density > threshold."""
        if require_keyword:
            joined = "\n".join(lines[:10])
            if not any(
                kw.lower() in joined.lower()
                for kw in self.opt.get("toc_keywords", [])
            ):
                return False
        if not self._re.get("toc_line"):
            return bool(require_keyword)
        non_empty = [ln for ln in lines if ln.strip()]
        if not non_empty:
            return False
        hits = sum(
            1 for ln in non_empty if self._re["toc_line"].search(ln.strip())
        )
        thresh = threshold if threshold is not None else 0.3
        return hits / len(non_empty) > thresh

    def _split_mixed_toc_body(self, lines: list[str]) -> list[str] | None:
        """If a TOC page also contains an enacting clause, return body lines after it."""
        if not self._re.get("enacting_clause"):
            return None
        for i, line in enumerate(lines):
            if self._re["enacting_clause"].search(line.strip()):
                body_start = i + 1
                if body_start < len(lines) and not lines[body_start].strip():
                    body_start += 1
                return lines[body_start:]
        return None

    def _strip_page_wrappers(self, lines: list[str]) -> list[str]:
        """Remove header lines (first 5) using index-based filter.

        Footer lines are NOT stripped here — that's handled separately
        by the clean_text pipeline (footer_mode).
        """
        header_indices: set[int] = set()
        for i, line in enumerate(lines[:5]):
            s = line.strip()
            for pat in self._header_patterns:
                if pat.lower() in s.lower():
                    header_indices.add(i)
                    break

        result: list[str] = []
        for i, line in enumerate(lines):
            if i in header_indices:
                continue
            result.append(line)
        return result

    def _is_valid_title(self, title: str) -> bool:
        """Validate section/hierarchy title against length, blocklist, prose starters, months."""
        if not title:
            return False
        min_len = self.opt.get("title_min_length", 2)
        if len(title) < min_len:
            return False
        words = title.split()
        if len(words) < self.opt.get("title_min_words", 1):
            return False
        first = words[0]
        # Built-in: month names are never valid titles
        if first in self.MONTH_NAMES:
            return False
        # NSW: first char must be uppercase
        if self.opt.get("title_require_uppercase") and not title[0].isupper():
            return False
        # VIC: titles cannot start with digit
        if not self.opt.get("title_allow_digit_prefix", True) and re.match(r"^\d", title):
            return False
        # Configurable blocklist (first-word match)
        if first in self.opt.get("title_blocklist", frozenset()):
            return False
        # Configurable prose starter check (case-insensitive)
        prose = {w.lower() for w in self.opt.get("title_prose_starters", frozenset())}
        return first.lower() not in prose

    def _is_any_hierarchy(self, line: str) -> bool:
        """Guard: check if line matches any hierarchy regex (false-positive filter)."""
        for level in self._hierarchy_levels:
            for suffix in ("_full", "_id_only"):
                key = f"{level}{suffix}"
                if self._re.get(key) and self._re[key].match(line):
                    return True
        return False

    @staticmethod
    def _is_bare_page_number(stripped: str) -> bool:
        """Detect standalone page numbers (1-4 digits)."""
        return bool(re.match(r"^\d{1,4}$", stripped))

    def _is_amendment_continuation(self, stripped: str) -> bool:
        """Detect amendment footnote continuation lines (config-driven)."""
        if self.opt.get("amendment_blank_is_continuation") and not stripped:
            return True
        if any(r.match(stripped) for r in self._amendment_cont_res):
            return True
        if self.opt.get("amendment_roman_continuation") and \
           re.match(r"^[ivxlcdm]+$", stripped, re.IGNORECASE):
            return True
        return False

    def _new_hierarchy(self) -> dict:
        """Create hierarchy dict with all levels initialized to None."""
        h: dict[str, str | None] = {}
        for comp in self._hierarchy_levels:
            h[comp] = None
            h[f"{comp}_title"] = None
        return h

    def _reset_below(self, hierarchy: dict, level: str) -> None:
        """Clear all hierarchy levels below the given level."""
        found = False
        for comp in self._hierarchy_levels:
            if found:
                hierarchy[comp] = None
                hierarchy[f"{comp}_title"] = None
            if comp == level:
                found = True

    @staticmethod
    def _clear_below_dedup(last_vals: dict, level: str) -> None:
        """Clear dedup tracking for levels below *level*."""
        comp_order = ["chapter", "part", "division", "subdivision"]
        clear = False
        for comp in comp_order:
            if clear and comp in last_vals:
                last_vals[comp] = None
            if comp == level:
                clear = True

    def _qualify_part_id(
        self, level: str, part_id: str, hierarchy: dict
    ) -> str:
        """When chapters are in use, scope Part IDs with the current chapter."""
        if level == "part" and self.opt.get("has_chapters") and hierarchy.get("chapter"):
            return f"{hierarchy['chapter']}.{part_id}"
        return part_id

    def _flush_if_active(
        self,
        chunks: list[dict],
        current: dict | None,
        hierarchy: dict,
    ) -> None:
        """Flush current section and return None."""
        if current:
            self._flush_section(
                chunks,
                {"parsed": 0, "skipped": 0},
                hierarchy,
                current,
            )

    def _flush_section(
        self,
        chunks: list[dict],
        stats: dict,
        hierarchy: dict,
        current: dict,
    ) -> None:
        """Assemble section text, check token threshold, emit chunk(s)."""
        body_text = "\n".join(current["body_lines"]).strip()
        if not body_text:
            stats["skipped"] += 1
            return

        section_id = current["section_id"]
        section_title = current["title"]
        parent_prefix = self._build_parent_prefix(hierarchy)
        prefixed = self._make_text(section_id, section_title, body_text, parent_prefix)
        tokens = self.estimate_tokens(prefixed)

        if tokens <= self.token_threshold:
            chunks.append(
                self._build_chunk(section_id, section_title, prefixed, hierarchy)
            )
            stats["parsed"] += 1
        else:
            sub = self._split_long_section(
                section_id,
                section_title,
                body_text,
                hierarchy,
                parent_prefix,
            )
            chunks.extend(sub)
            stats["parsed"] += len(sub)


# ── ParserFactory ──────────────────────────────────────────────────────


class ParserFactory:
    """Facade that returns the right BaseParser for a given jurisdiction.

    All 8 states route through GenericParser with their config entry.
    """

    @staticmethod
    def get_parser(state: str) -> BaseParser:
        """Return a parser instance for *state* (case-insensitive)."""
        state_upper = state.upper()
        config = JURISDICTION_CONFIGS.get(state_upper)
        if not config:
            raise ValueError(f"Unknown jurisdiction: {state}")
        return GenericParser(config)

    @staticmethod
    def detect_from_filename(pdf_path: str) -> str:
        """Extract state code from a PDF filename.

        Tries splitting on ``_`` first, then falls back to scanning the
        stem for known 2-3 letter uppercase codes (handles filenames
        like ``97-109aa111-authorised-VIC.pdf`` where the state token is
        hyphenated rather than underscore-delimited).
        """
        stem = Path(pdf_path).stem.upper()
        parts = stem.split("_")
        if len(parts) >= 2:
            candidate = parts[-1].upper()
            if candidate in JURISDICTION_CONFIGS:
                return candidate
        known_states = list(JURISDICTION_CONFIGS)
        for code in sorted(known_states, key=len, reverse=True):
            if code in stem:
                return code
        raise ValueError(
            f"Cannot detect state from filename: {pdf_path}. "
            "Expected pattern: *_<state>.pdf"
        )

    @staticmethod
    def get_parser_for_file(pdf_path: str) -> BaseParser:
        """Convenience: detect state from filename, return parser."""
        return ParserFactory.get_parser(
            ParserFactory.detect_from_filename(pdf_path)
        )
