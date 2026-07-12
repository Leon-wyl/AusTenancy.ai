"""
Batch ingestion orchestrator for VIC and NSW jurisdictions.

Parses VIC and NSW legislative PDFs, saves per-state chunks, and
merges into all_australia_chunks.json.
"""

from __future__ import annotations

import gc
import json
import logging
import time
from pathlib import Path

from src.data_processing.nsw_parser import NSWParser
from src.data_processing.nsw_regulation_parser import NSWRegulationParser
from src.data_processing.vic_parser import VICParser
from src.data_processing.vic_regulation_parser import VICRegulationParser

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

PROCESSED_DIR = Path("data/processed")
RAW_DIR = Path("data/raw")
MASTER_OUTPUT = PROCESSED_DIR / "all_australia_chunks.json"

PARSER_MAP: dict[str, tuple[type, str, str]] = {
    "VIC": (VICParser, "97-109aa111-authorised-VIC.pdf", "vic_chunks.json"),
    "NSW": (NSWParser, "act-2010-042_nsw.pdf", "nsw_chunks.json"),
    "VIC_REG": (
        VICRegulationParser,
        "21-003sra authorised-regulation-vic.pdf",
        "vic_regulation_chunks.json",
    ),
    "NSW_REG": (
        NSWRegulationParser,
        "sl-2019-0629-regulation-nsw.pdf",
        "nsw_regulation_chunks.json",
    ),
}


def main() -> int:
    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)

    all_chunks: list[dict] = []
    telemetry: dict[str, dict] = {}
    failures = 0

    for state, (parser_cls, pdf_name, output_name) in PARSER_MAP.items():
        pdf_path = RAW_DIR / pdf_name
        output_path = PROCESSED_DIR / output_name

        if not pdf_path.exists():
            logger.warning("PDF not found: %s — skipping %s", pdf_path, state)
            failures += 1
            continue

        logger.info("-" * 50)
        logger.info("Ingesting %s -> %s", pdf_name, output_name)

        try:
            t0 = time.perf_counter()
            parser = parser_cls()
            chunks = parser.run(str(pdf_path), str(output_path))
            elapsed = time.perf_counter() - t0

            all_chunks.extend(chunks)
            tokens = sum(parser.estimate_tokens(c["text"]) for c in chunks)
            telemetry[state] = {
                "file": pdf_name,
                "chunks": len(chunks),
                "tokens": tokens,
                "time_s": round(elapsed, 1),
            }
            logger.info(
                "  Done: %d chunks, %d tokens, %.1fs",
                len(chunks),
                tokens,
                elapsed,
            )
        except Exception:
            logger.exception("  FAILED: %s", pdf_name)
            failures += 1
        finally:
            gc.collect()

    with open(MASTER_OUTPUT, "w", encoding="utf-8") as f:
        json.dump(all_chunks, f, indent=2, ensure_ascii=False)
    logger.info("Master merged: %d chunks -> %s", len(all_chunks), MASTER_OUTPUT)

    logger.info("=" * 50)
    logger.info("BULK INGESTION SUMMARY")
    logger.info("=" * 50)
    total_c = 0
    for state_key in sorted(telemetry):
        t = telemetry[state_key]
        total_c += t["chunks"]
        logger.info(
            "  %-3s  %5d chunks  %7d tokens  %5.1fs  %s",
            state_key,
            t["chunks"],
            t["tokens"],
            t["time_s"],
            t["file"],
        )
    logger.info("  TOTAL  %5d chunks", total_c)

    if failures:
        logger.warning("  %d state(s) FAILED — see logs above", failures)
        return 1
    return 0


if __name__ == "__main__":
    import sys

    sys.exit(main())
