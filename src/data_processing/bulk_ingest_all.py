"""
Batch ingestion orchestrator for all Australian jurisdictions.

Scans data/raw/ for PDFs → detects state via filename → parses via
ParserFactory → saves per-state {state}_chunks.json + merges into
all_australia_chunks.json.

Features:
  - Fault isolation: one state's failure doesn't block others
  - Memory management: gc.collect() per state
  - Case-insensitive PDF detection (.pdf / .PDF)
"""

from __future__ import annotations

import gc
import json
import logging
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from src.data_processing.parser_factory import ParserFactory

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

PROCESSED_DIR = Path("data/processed")
RAW_DIR = Path("data/raw")
MASTER_OUTPUT = PROCESSED_DIR / "all_australia_chunks.json"


def main() -> int:
    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)

    pdf_files = sorted(
        [f for f in RAW_DIR.glob("*") if f.suffix.lower() == ".pdf"]
    )
    if not pdf_files:
        logger.error("No PDFs found in %s", RAW_DIR)
        return 1

    all_chunks: list[dict] = []
    telemetry: dict[str, dict] = {}
    failures = 0

    for pdf_path in pdf_files:
        state = ParserFactory.detect_from_filename(str(pdf_path))
        output_path = str(PROCESSED_DIR / f"{state.lower()}_chunks.json")

        logger.info("-" * 50)
        logger.info(
            "Ingesting %s → %s", pdf_path.name, Path(output_path).name
        )

        try:
            t0 = time.perf_counter()
            parser = ParserFactory.get_parser(state)
            chunks = parser.run(str(pdf_path), output_path)
            elapsed = time.perf_counter() - t0

            all_chunks.extend(chunks)
            tokens = sum(parser.estimate_tokens(c["text"]) for c in chunks)
            telemetry[state] = {
                "file": pdf_path.name,
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
            logger.exception("  FAILED: %s", pdf_path.name)
            failures += 1
        finally:
            gc.collect()

    # ── Write master merged JSON ──
    with open(MASTER_OUTPUT, "w", encoding="utf-8") as f:
        json.dump(all_chunks, f, indent=2, ensure_ascii=False)
    logger.info(
        "Master merged: %d chunks → %s", len(all_chunks), MASTER_OUTPUT
    )

    # ── Summary ──
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
    sys.exit(main())
