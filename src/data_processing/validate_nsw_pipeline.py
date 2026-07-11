"""
Validation script for the NSW RTA 2010 parsing pipeline.

Verifies structural integrity of parsed data:
  - Section count thresholds
  - Metadata completeness
  - Output JSON validity

Saves validated output to data/processed/nsw_chunks.json.
"""

import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from src.data_processing.base_parser import BaseParser
from src.data_processing.nsw_parser import NSWParser

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

INPUT_PDF = "data/raw/act-2010-042_nsw.pdf"
OUTPUT_JSON = "data/processed/nsw_chunks.json"
DOWNLOAD_URL = "https://legislation.nsw.gov.au/view/pdf/asmade/act-2010-042"
REQUIRED_FIELDS = {"chunk_id", "text", "state", "act", "section_id", "section_title"}
MIN_SECTION_COUNT = 200


def validate(chunks: list[dict]) -> int:
    """Run validation assertions. Returns number of issues found."""
    issues = 0

    if len(chunks) < MIN_SECTION_COUNT:
        logger.warning("Expected at least %d sections, got %d", MIN_SECTION_COUNT, len(chunks))
        issues += 1

    for i, chunk in enumerate(chunks):
        for field in REQUIRED_FIELDS:
            if field not in chunk:
                logger.warning("Chunk %d missing field: %s", i, field)
                issues += 1
            elif chunk[field] is None:
                logger.warning("Chunk %d has null field: %s", i, field)
                issues += 1

        if chunk.get("state") != "NSW":
            logger.warning("Chunk %d has wrong state: %s", i, chunk.get("state"))
            issues += 1
        if chunk.get("act") != "Residential Tenancies Act 2010":
            logger.warning("Chunk %d has wrong act: %s", i, chunk.get("act"))
            issues += 1
        if chunk.get("year") != "2010":
            logger.warning("Chunk %d has wrong year: %s", i, chunk.get("year"))
            issues += 1
        if not chunk.get("text", "").strip():
            logger.warning("Chunk %d has empty text", i)
            issues += 1
        if chunk.get("chunk_id") and not chunk["chunk_id"].startswith("NSW-RTA2010-s"):
            logger.warning("Chunk %d has bad chunk_id: %s", i, chunk["chunk_id"])
            issues += 1

    top_5 = chunks[:5]
    for i, chunk in enumerate(top_5):
        if not chunk.get("section_id"):
            logger.warning("Top-5 chunk %d has empty section_id", i)
            issues += 1
        if not chunk.get("text", "").strip():
            logger.warning("Top-5 chunk %d has empty text", i)
            issues += 1

    return issues


def print_telemetry(chunks: list[dict]) -> None:
    """Print execution telemetry metrics."""
    total = len(chunks)
    token_lengths = [BaseParser.estimate_tokens(c["text"]) for c in chunks]
    char_lengths = [len(c["text"]) for c in chunks]

    avg_tokens = sum(token_lengths) / max(1, total)
    avg_chars = sum(char_lengths) / max(1, total)

    empty_count = sum(1 for c in chunks if not c.get("text", "").strip())

    section_ids = {c.get("section_id") for c in chunks}
    unique_sections = len(section_ids)

    logger.info("=" * 50)
    logger.info("NSW Pipeline Validation Telemetry")
    logger.info("=" * 50)
    logger.info("Total Successfully Parsed Sections (chunks): %d", total)
    logger.info("Unique Section IDs: %d", unique_sections)
    logger.info(
        "Malformed/Skipped Blocks due to Regex Mismatch (empty body): %d",
        empty_count,
    )
    logger.info("Average Token Length per Chunk: %.1f", avg_tokens)
    logger.info("Average Character Length per Chunk: %.1f", avg_chars)

    if total > 0:
        logger.info(
            "Min token length: %d, Max token length: %d",
            min(token_lengths),
            max(token_lengths),
        )

    sample_ids = [c["section_id"] for c in chunks[:5]]
    logger.info("First 5 section IDs: %s", sample_ids)


def main() -> int:
    """Run NSW pipeline validation end-to-end."""
    pdf_path = Path(INPUT_PDF)
    if not pdf_path.exists():
        logger.error("NSW PDF not found: %s", pdf_path)
        logger.error("Download it from: %s", DOWNLOAD_URL)
        logger.error("Then place it as: %s", pdf_path)
        return 1

    logger.info("Starting NSW pipeline validation...")
    parser = NSWParser()

    try:
        chunks = parser.run(str(pdf_path), OUTPUT_JSON)
    except Exception as exc:
        logger.exception("Pipeline failed: %s", exc)
        return 1

    print_telemetry(chunks)
    issues = validate(chunks)

    output_path = Path(OUTPUT_JSON)
    logger.info("Validated output saved to: %s", output_path)
    logger.info("Output file size: %.1f KB", output_path.stat().st_size / 1024)

    if issues:
        logger.warning("Validation found %d issue(s)", issues)
        return 1
    else:
        logger.info("Validation PASSED — all checks green")
        return 0


if __name__ == "__main__":
    sys.exit(main())
