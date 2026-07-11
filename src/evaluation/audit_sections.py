"""
Section audit: scans parsed chunks to identify candidate sections per state/domain.

Outputs tests/evaluation/section_audit.json with sections grouped by state → domain,
sorted by keyword relevance.

Usage:
    python src/evaluation/audit_sections.py
"""

import json
import logging
import re
from collections import defaultdict
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
PROCESSED_DIR = PROJECT_ROOT / "data" / "processed"
OUTPUT_PATH = PROJECT_ROOT / "tests" / "evaluation" / "section_audit.json"

DOMAIN_KEYWORDS: dict[str, list[str]] = {
    "rent_increases": [
        "rent increase", "increased rent", "proposed rent",
        "notice of rent increase", "increase in rent",
        "rent may be increased", "rent payable",
    ],
    "terminations": [
        "termination", "notice to vacate", "possession",
        "breach of duty", "terminate", "vacant possession",
        "notice of termination",
    ],
    "repairs": [
        "repair", "maintenance", "urgent repair",
        "good repair", "duty to repair", "remedy",
        "fit for habitation",
    ],
    "bonds": [
        "bond", "security deposit", "residential bond",
        "bond lodgment", "bond claim", "rental bond",
    ],
}


def find_sections(state: str, chunks: list[dict]) -> dict[str, list[dict]]:
    """Return sections grouped by domain, sorted by keyword hit count descending."""
    results: dict[str, dict[str, dict]] = defaultdict(dict)
    domain_sets: dict[str, set] = {}

    for domain, keywords in DOMAIN_KEYWORDS.items():
        domain_sets[domain] = set(keywords)

    for chunk in chunks:
        sid = chunk.get("section_id", "")
        title = chunk.get("section_title", "")
        part = chunk.get("part", "")
        chapter = chunk.get("chapter", "")
        text = chunk.get("text", "").lower()

        for domain, keywords in DOMAIN_KEYWORDS.items():
            hits = sum(1 for kw in keywords if kw in text)
            if hits == 0:
                continue
            key = sid
            if key not in results[domain]:
                results[domain][key] = {
                    "section_id": sid,
                    "section_title": title,
                    "part": part,
                    "chapter": chapter,
                    "keyword_hits": 0,
                    "text_preview": text[:120],
                }
            results[domain][key]["keyword_hits"] += hits

    sorted_results: dict[str, list[dict]] = {}
    for domain in DOMAIN_KEYWORDS:
        items = sorted(
            results[domain].values(),
            key=lambda x: x["keyword_hits"],
            reverse=True,
        )
        sorted_results[domain] = [dict(item, keyword_hits=int(item["keyword_hits"])) for item in items]

    return sorted_results


def main():
    states = ["VIC", "NSW"]  # supported states (others have chunk quality issues)
    output: dict = {}

    for state in states:
        path = PROCESSED_DIR / f"{state.lower()}_chunks.json"
        if not path.exists():
            logger.warning("Chunk file not found: %s", path)
            continue

        with open(path) as f:
            chunks = json.load(f)

        logger.info("%s: %d chunks", state, len(chunks))

        output[state] = find_sections(state, chunks)

        for domain in DOMAIN_KEYWORDS:
            items = output[state][domain]
            logger.info(
                "  %-20s: %3d candidates (top: %s)",
                domain,
                len(items),
                ", ".join(f"s{v['section_id']}" for v in items[:5]),
            )

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT_PATH, "w") as f:
        json.dump(output, f, indent=2)
    logger.info("Saved to %s", OUTPUT_PATH)


if __name__ == "__main__":
    main()
