"""
Auto-generate golden contexts from golden datasets.

Reads each {state}_golden_dataset.json, extracts section references from
metadata.sections, matches against all_australia_chunks.json, and writes
{state}_golden_contexts.json.

Usage:
    python src/evaluation/gen_golden_contexts.py              # all states (skip existing)
    python src/evaluation/gen_golden_contexts.py --force      # overwrite all
    python src/evaluation/gen_golden_contexts.py --state NSW  # single state
"""

import json
import logging
import re
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
EVAL_DIR = PROJECT_ROOT / "tests" / "evaluation"
ALL_CHUNKS_PATH = PROJECT_ROOT / "data" / "processed" / "all_australia_chunks.json"
STATE_ORDER = ["VIC", "NSW"]  # only supported states (others have chunk quality issues)


_PREFIX_RE = re.compile(r"^(act|reg):(.+)$")


def _parse_section_ref(section_ref: str) -> tuple[str | None, str]:
    """Parse optional corpus prefix: 'act:159' → ('act', '159'), 'reg:21' → ('reg', '21').

    Plain references (e.g. '44') → (None, '44') for backward compatibility.
    """
    m = _PREFIX_RE.match(section_ref)
    if m:
        return m.group(1), m.group(2)
    return None, section_ref


def _base_section(section_ref: str) -> str:
    """Strip subsection qualifier and trailing period: '44(1)' → '44', '30.' → '30'."""
    m = re.match(r"^(\d+[A-Z]*)\.?", section_ref)
    return m.group(1) if m else section_ref.rstrip(".")


def _load_chunks_by_state() -> dict[str, list[dict]]:
    with open(ALL_CHUNKS_PATH) as f:
        all_chunks = json.load(f)
    by_state: dict[str, list[dict]] = {}
    for c in all_chunks:
        by_state.setdefault(c["state"], []).append(c)
    logger.info("Loaded %d chunks across %d states", len(all_chunks), len(by_state))
    return by_state


def gen_golden_contexts(
    state: str, by_state: dict[str, list[dict]], force: bool = False, regulation: bool = False
):
    suffix = "_regulation" if regulation else ""
    dataset_path = EVAL_DIR / f"{state.lower()}{suffix}_golden_dataset.json"
    output_path = EVAL_DIR / f"{state.lower()}{suffix}_golden_contexts.json"

    if not dataset_path.exists():
        logger.warning("[%s] Dataset not found: %s", state, dataset_path)
        return

    if output_path.exists() and not force:
        logger.info(
            "[%s] Golden contexts already exist — skipping (use --force to overwrite)", state
        )
        return

    with open(dataset_path) as f:
        samples = json.load(f)

    state_chunks = by_state.get(state.upper(), [])
    if not state_chunks:
        logger.warning("[%s] No chunks found for state", state)

    act_chunks = [c for c in state_chunks if "Regulation" not in c.get("act", "")]
    reg_chunks = [c for c in state_chunks if "Regulation" in c.get("act", "")]

    def _build_idx(chunks: list[dict]) -> dict[str, list[dict]]:
        idx: dict[str, list[dict]] = {}
        for c in chunks:
            sid = c.get("section_id", "")
            idx.setdefault(sid, []).append(c)
            sid_stripped = sid.rstrip(".")
            if sid_stripped != sid:
                idx.setdefault(sid_stripped, []).append(c)
        return idx

    act_idx = _build_idx(act_chunks)
    reg_idx = _build_idx(reg_chunks)
    # Legacy combined index for unprefixed references
    combined_idx = _build_idx(state_chunks)

    logger.info(
        "[%s] Act chunks: %d, Reg chunks: %d",
        state,
        len(act_chunks),
        len(reg_chunks),
    )

    output: dict[str, list[dict]] = {}
    not_found: list[str] = []

    for i, sample in enumerate(samples):
        sections = sample.get("metadata", {}).get("sections", [])
        golden: list[dict] = []
        seen = set()

        for ref in sections:
            source, ref_body = _parse_section_ref(ref)
            base = _base_section(ref_body)
            # Build a unique key that includes source prefix to avoid dedup
            # across different corpuses for the same base section id
            lookup_key = f"{source}:{base}" if source else base
            if lookup_key in seen:
                continue
            seen.add(lookup_key)

            if source == "reg":
                matches = reg_idx.get(base, [])
            elif source == "act":
                matches = act_idx.get(base, [])
            else:
                matches = combined_idx.get(base, [])

            if not matches:
                not_found.append(f"sample {i}: {ref} → base '{base}'")
                continue

            for m in matches:
                golden.append(
                    {
                        "chunk_id": m.get("chunk_id", ""),
                        "text": m.get("text", ""),
                        "section_id": m.get("section_id", ""),
                        "section_title": m.get("section_title", ""),
                        "part": m.get("part", ""),
                        "state": m.get("state", ""),
                        "year": m.get("year", ""),
                        "act": m.get("act", ""),
                        "instrument_type": m.get("instrument_type"),
                        "schedule": m.get("schedule"),
                        "schedule_title": m.get("schedule_title"),
                        "score": 1.0,
                    }
                )

        output[str(i)] = golden

    with open(output_path, "w") as f:
        json.dump(output, f, indent=2)

    logger.info(
        "[%s] Wrote %d QA entries — %d sections not found in chunks",
        state,
        len(output),
        len(not_found),
    )
    if not_found:
        for nf in not_found[:10]:
            logger.warning("  not found: %s", nf)


def main():
    import argparse

    parser = argparse.ArgumentParser(
        description="Auto-generate golden contexts for golden datasets"
    )
    parser.add_argument("--force", action="store_true", help="Overwrite existing files")
    parser.add_argument("--state", type=str, help="Process a single state (e.g. NSW)")
    parser.add_argument(
        "--regulation", action="store_true", help="Generate regulation golden contexts"
    )
    args = parser.parse_args()

    by_state = _load_chunks_by_state()

    if args.state:
        gen_golden_contexts(
            args.state.upper(), by_state, force=args.force, regulation=args.regulation
        )
    else:
        for state in STATE_ORDER:
            gen_golden_contexts(state, by_state, force=args.force)
            gen_golden_contexts(state, by_state, force=args.force, regulation=True)


if __name__ == "__main__":
    main()
