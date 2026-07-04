"""
Abstract base class for jurisdiction-specific legislative PDF parsers.

Template method `run()` orchestrates the pipeline:
    load_pdf → clean_text → parse_hierarchy → export_chunks

Subclasses must implement:
    - parse_hierarchy(cleaned_lines) → list[dict]
    - _build_chunk(section_id, title, text, hierarchy) → dict
"""

import json
import logging
import re
from abc import ABC, abstractmethod
from pathlib import Path

import fitz

PAGE_FOOTER_RE = re.compile(r"^Page \d+ of \d+$")


class BaseParser(ABC):
    """Abstract parser with shared utilities and a template method pipeline."""

    def __init__(
        self, state: str, act_name: str, act_year: str, token_threshold: int = 2048
    ) -> None:
        self.state = state
        self.act_name = act_name
        self.act_year = act_year
        self.token_threshold = token_threshold
        self.logger = logging.getLogger(self.__class__.__name__)

    # ── concrete shared utilities ──────────────────────────────────────

    def load_pdf(self, pdf_path: str) -> list[str]:
        """Open PDF with PyMuPDF, return per-page raw text strings.

        Per-page granularity is required because footer/header stripping
        needs page-level context.
        """
        path = Path(pdf_path)
        if not path.exists():
            raise FileNotFoundError(f"PDF not found: {path}")
        doc = fitz.open(str(path))
        self.logger.info("Opened PDF: %s (%d pages)", path, doc.page_count)
        page_texts = []
        try:
            for i in range(doc.page_count):
                page_texts.append(doc[i].get_text("text"))
        finally:
            doc.close()
        return page_texts

    def clean_text(self, raw_page_texts: list[str]) -> list[str]:
        """Strip generic noise and return flat list of cleaned body lines.

        Base implementation strips blank lines and 'Page X of Y' footer
        lines. Subclasses override for jurisdiction-specific header/footer
        removal, TOC skipping, and preamble stripping.
        """
        cleaned: list[str] = []
        for page_text in raw_page_texts:
            for line in page_text.split("\n"):
                stripped = line.rstrip()
                if not stripped:
                    continue
                if PAGE_FOOTER_RE.match(stripped):
                    continue
                cleaned.append(stripped)
        return cleaned

    @staticmethod
    def estimate_tokens(text: str) -> int:
        """Estimate token count from word count (~0.75 tokens/word)."""
        words = len(text.split())
        return max(1, int(words / 0.75))

    def _make_text(
        self, section_id: str, section_title: str, body_text: str, parent_prefix: str
    ) -> str:
        """Assemble full chunk text with optional parent hierarchy prefix."""
        if parent_prefix:
            return f"{parent_prefix} {section_id} {section_title}\n{body_text}"
        return f"{section_id} {section_title}\n{body_text}"

    def _build_parent_prefix(self, hierarchy: dict) -> str:
        """Build hierarchy prefix like '[Part 2 - Division 3]'.

        Subclasses may override for jurisdiction-specific formatting.
        """
        parts: list[str] = []
        if hierarchy.get("part"):
            parts.append(f"Part {hierarchy['part']}")
            if hierarchy.get("division"):
                parts.append(f"Division {hierarchy['division']}")
                if hierarchy.get("subdivision"):
                    parts.append(f"Subdivision {hierarchy['subdivision']}")
        if parts:
            return "[" + " - ".join(parts) + "]"
        return ""

    def _split_long_section(
        self,
        section_id: str,
        section_title: str,
        body_text: str,
        hierarchy: dict,
        parent_prefix: str,
    ) -> list[dict]:
        """Split a long section at top-level subsection markers.

        Splits at (\\d+[A-Za-z]?) markers only — sub-markers like (a), (b)
        remain glued to their parent subsection.
        """
        sub_chunks: list[dict] = []
        parts = re.split(r"\n(?=\(\d+[A-Za-z]?\))", body_text)

        if len(parts) <= 1:
            prefixed = self._make_text(section_id, section_title, body_text, parent_prefix)
            sub_chunks.append(self._build_chunk(section_id, section_title, prefixed, hierarchy))
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

            if self.estimate_tokens(candidate) > self.token_threshold and current_text:
                prefixed = self._make_text(
                    section_id, section_title, current_text.strip(), parent_prefix
                )
                sub_range = (
                    f"{section_id}({sub_start})-{section_id}({sub_end})"
                    if sub_start and sub_end
                    else None
                )
                sub_chunks.append(
                    self._build_chunk(section_id, section_title, prefixed, hierarchy, sub_range)
                )
                current_text = part_stripped
                sub_start = sub_num
                sub_end = sub_num
            else:
                current_text = candidate
                if sub_start is None:
                    sub_start = sub_num
                sub_end = sub_num

        if current_text.strip():
            prefixed = self._make_text(
                section_id, section_title, current_text.strip(), parent_prefix
            )
            sub_range = (
                f"{section_id}({sub_start})-{section_id}({sub_end})"
                if sub_start and sub_end
                else None
            )
            sub_chunks.append(
                self._build_chunk(section_id, section_title, prefixed, hierarchy, sub_range)
            )

        return sub_chunks

    # ── abstract methods ───────────────────────────────────────────────

    @abstractmethod
    def parse_hierarchy(self, cleaned_lines: list[str]) -> list[dict]:
        """Parse cleaned body lines into hierarchical chunks.

        Subclasses implement jurisdiction-specific regex patterns and
        state-machine logic for Act → Part → Division → Section slicing.
        """
        ...

    @abstractmethod
    def _build_chunk(
        self,
        section_id: str,
        section_title: str,
        text: str,
        hierarchy: dict,
        subsection_range: str | None = None,
    ) -> dict:
        """Build a chunk dict with jurisdiction-specific payload schema."""
        ...

    # ── concrete export ────────────────────────────────────────────────

    def export_chunks(self, chunks: list[dict], output_path: str) -> None:
        """Serialize chunks to JSON with metadata."""
        path = Path(output_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(chunks, f, indent=2, ensure_ascii=False)
        self.logger.info("Chunks written to: %s", path)

    # ── template method ────────────────────────────────────────────────

    def run(self, pdf_path: str, output_path: str) -> list[dict]:
        """Execute full pipeline: load → clean → parse → export."""
        self.logger.info("Parsing %s — %s %s", self.state, self.act_name, self.act_year)

        raw_pages = self.load_pdf(pdf_path)
        cleaned_lines = self.clean_text(raw_pages)
        self.logger.info("Cleaned body lines: %d", len(cleaned_lines))

        chunks = self.parse_hierarchy(cleaned_lines)
        self.logger.info("Sections extracted: %d", len(chunks))

        self.export_chunks(chunks, output_path)

        total_tokens = sum(self.estimate_tokens(c["text"]) for c in chunks)
        self.logger.info("Total estimated tokens: %d", total_tokens)

        if not chunks:
            raise ValueError("No sections extracted — possible parsing failure")

        return chunks
