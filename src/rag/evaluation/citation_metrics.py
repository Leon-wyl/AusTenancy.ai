"""Deterministic citation metrics for Regulation RAG evaluation.

Computes citation precision, golden provision recall, and Regulation-specific
indicators from answer text, retrieved context chunks, and golden section refs.

All metrics are deterministic — no LLM calls needed.
"""

import re
from dataclasses import asdict, dataclass

from src.rag.generation.generator import (
    CITATION_RE,
    format_citation_label,
    verify_citations,
)

_NORMALIZE_ZEROS_RE = re.compile(r"(Sec|Reg|Sch|Form|Cl)\s+0*(\d+)")
_NORMALIZE_SUBSECTION_RE = re.compile(r"\(\w+\)")


def _normalize_cite(label: str) -> str:
    """Strip brackets, leading zeros, and subsection parentheses.

    [VIC RTA 1997 Sec 044(1)] → VIC RTA 1997 Sec 44
    """
    label = label.strip("[]")
    label = _NORMALIZE_ZEROS_RE.sub(r"\1 \2", label)
    label = _NORMALIZE_SUBSECTION_RE.sub("", label)
    return label


def _parse_golden_ref(golden_ref: str) -> tuple[bool, str]:
    """Parse a golden section reference into (is_regulation, section_id).

    "reg:21" → (True, "21")
    "reg:sch1-form5" → (True, "sch1-form5")
    "44" → (False, "44")
    "44(1)" → (False, "44(1)")
    """
    if ":" in golden_ref:
        prefix, _, section_id = golden_ref.partition(":")
        return prefix == "reg", section_id
    return False, golden_ref


def _find_matching_chunks(
    golden_ref: str, chunks: list[dict]
) -> list[dict]:
    """Find chunks whose section_id matches the golden ref."""
    is_reg, section_id = _parse_golden_ref(golden_ref)
    base_id = re.sub(r"\(\w+\)", "", section_id)
    matching = []
    for c in chunks:
        cid = c.get("section_id", "")
        if cid in (section_id, base_id):
            if is_reg and c.get("instrument_type") != "regulation":
                continue
            matching.append(c)
    return matching


def _match_golden_ref_to_citation(
    golden_ref: str,
    verified_citations: list[str],
    chunks: list[dict],
) -> bool:
    """Match a golden section ref to verified citation labels.

    Strategy:
    1. Find chunks matching the golden ref
    2. Generate canonical citation label for each matching chunk
    3. Check if canonical label is in the verified list (exact or normalized)
    """
    matching_chunks = _find_matching_chunks(golden_ref, chunks)
    if not matching_chunks:
        return False

    normalized_verified = {_normalize_cite(c) for c in verified_citations}

    for chunk in matching_chunks:
        label = format_citation_label(chunk)
        if label in verified_citations:
            return True
        if _normalize_cite(label) in normalized_verified:
            return True

    return False


def _golden_in_contexts(
    golden_sections: list[str], chunks: list[dict]
) -> list[str]:
    """Return golden refs that appear in retrieved contexts."""
    found = []
    for ref in golden_sections:
        if _find_matching_chunks(ref, chunks):
            found.append(ref)
    return found


def _golden_in_verified(
    golden_sections: list[str],
    verified_citations: list[str],
    chunks: list[dict],
) -> list[str]:
    """Return golden refs that have matching verified citations."""
    cited = []
    for ref in golden_sections:
        if _match_golden_ref_to_citation(ref, verified_citations, chunks):
            cited.append(ref)
    return cited


@dataclass
class CitationMetrics:
    """Deterministic citation metrics for a single evaluation question."""

    question_idx: int
    citation_count: int
    verified_count: int
    unverified_count: int
    citation_precision: float
    golden_recall_retrieval: float
    golden_recall_citation: float
    reg_citations_in_answer: int
    reg_citation_verified: bool
    golden_in_contexts: list[str]
    golden_in_verified: list[str]
    golden_missing_in_contexts: list[str]
    golden_missing_in_citations: list[str]


def compute_citation_metrics(
    question_idx: int,
    answer: str,
    chunks: list[dict],
    golden_sections: list[str],
) -> CitationMetrics:
    """Compute all citation metrics for a single question.

    Args:
        question_idx: Sample index for traceability.
        answer: Full (guarded) answer text.
        chunks: Retrieved context chunks (list of dicts with section_id, etc.).
        golden_sections: Golden section references (e.g. ["reg:21", "reg:sch1-form5"]).

    Returns:
        CitationMetrics with all computed fields.
    """
    if not chunks:
        chunks = []

    citation_check = verify_citations(answer, chunks)
    verified = citation_check.get("verified", [])
    unverified = citation_check.get("unverified", [])
    all_cits = CITATION_RE.findall(answer)

    citation_count = len(all_cits)
    verified_count = len(verified)
    unverified_count = len(unverified)

    citation_precision = (
        float("nan") if citation_count == 0 else verified_count / citation_count
    )

    n_golden = len(golden_sections)
    in_contexts = _golden_in_contexts(golden_sections, chunks)
    in_verified = _golden_in_verified(golden_sections, verified, chunks)

    golden_recall_retrieval = len(in_contexts) / n_golden if n_golden > 0 else 0.0
    golden_recall_citation = len(in_verified) / n_golden if n_golden > 0 else 0.0

    reg_pattern = re.compile(r"REG", re.I)
    reg_citations_in_answer = sum(1 for c in all_cits if reg_pattern.search(c))
    reg_citation_verified = any(reg_pattern.search(c) for c in verified)

    missing_in_contexts = [r for r in golden_sections if r not in in_contexts]
    missing_in_citations = [r for r in in_contexts if r not in in_verified]

    return CitationMetrics(
        question_idx=question_idx,
        citation_count=citation_count,
        verified_count=verified_count,
        unverified_count=unverified_count,
        citation_precision=citation_precision,
        golden_recall_retrieval=golden_recall_retrieval,
        golden_recall_citation=golden_recall_citation,
        reg_citations_in_answer=reg_citations_in_answer,
        reg_citation_verified=reg_citation_verified,
        golden_in_contexts=in_contexts,
        golden_in_verified=in_verified,
        golden_missing_in_contexts=missing_in_contexts,
        golden_missing_in_citations=missing_in_citations,
    )


def cm_to_dict(cm: CitationMetrics) -> dict:
    """Convert CitationMetrics to a flat dict for CSV/JSON serialization."""
    d = asdict(cm)
    d["golden_in_contexts"] = ",".join(cm.golden_in_contexts)
    d["golden_in_verified"] = ",".join(cm.golden_in_verified)
    d["golden_missing_in_contexts"] = ",".join(cm.golden_missing_in_contexts)
    d["golden_missing_in_citations"] = ",".join(cm.golden_missing_in_citations)
    return d
