"""Phase 3 — Arrears threshold evaluation harness.

Runs 5 threshold cases (VIC 10d/14d/21d, NSW 14d/21d) through
generate_compliance_answer() 3 times per provider (DeepSeek + Bedrock).
Records conclusions, required authorities, citation coverage, latency,
and material errors. Writes reports/threshold_evaluation/YYYYMMDD_HHMMSS.json.
"""

from __future__ import annotations

import json
import os
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

# ── conclusion classifier ─────────────────────────────────────────────


def _classify_conclusion(answer: str, jurisdiction: str, days: int) -> dict:
    """Classify a VIC arrears answer without fragile substring matching.

    Returns dict with:
        label: BELOW_THRESHOLD_NO_NOTICE | THRESHOLD_MET_NOTICE_MAY_BE_VALID
               | AMBIGUOUS | UNKNOWN
        confidence: "high" | "medium" | "low"
        evidence_sentences: list of key sentences
    """
    if not answer.strip():
        return {"label": "UNKNOWN", "confidence": "low", "evidence_sentences": []}

    sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+", answer) if s.strip()]
    first_sentence = sentences[0].lower() if sentences else ""

    if jurisdiction == "VIC":
        return _classify_vic(first_sentence, sentences, days)
    else:
        return _classify_nsw(first_sentence, sentences, days)


def _classify_vic(first: str, sentences: list[str], days: int) -> dict:
    negation_patterns = [
        r"\bno\b.*\bvalid\b",
        r"\bcannot\b.*\bevict\b",
        r"\bnot\b.*\bmeet\b.*\bthreshold\b",
        r"\bdoes not\b.*\b14.?day",
        r"\bare not\b.*\bfourteen",
        r"\bnot\b.*\bvalid\b.*\bnotice\b",
        r"\bnot\b.*\ban occasion",
    ]
    affirmation_patterns = [
        r"\bcan\b.*\bgive\b.*\bnotice\b",
        r"\bcan\b.*\bevict\b",
        r"\bwithin\b.*\brights?\b.*\bevict",
        r"\blandlord\b.*\bcan\b.*\bnotice",
    ]

    negated = any(re.search(p, first, re.IGNORECASE) for p in negation_patterns)
    affirmed = any(re.search(p, first, re.IGNORECASE) for p in affirmation_patterns)

    if negated and not affirmed:
        return {
            "label": "BELOW_THRESHOLD_NO_NOTICE",
            "confidence": "high"
            if any("14" in s and "day" in s.lower() for s in sentences[:3])
            else "medium",
            "evidence_sentences": sentences[:3],
        }
    elif affirmed and not negated:
        return {
            "label": "THRESHOLD_MET_NOTICE_MAY_BE_VALID",
            "confidence": "high",
            "evidence_sentences": sentences[:3],
        }
    elif negated and affirmed:
        return {
            "label": "AMBIGUOUS",
            "confidence": "low",
            "evidence_sentences": sentences[:3],
        }
    else:
        lower_text = " ".join(sentences[:5]).lower()
        if days < 14:
            has_threshold = "14" in lower_text and ("day" in lower_text or "dai" in lower_text)
            if has_threshold and any(w in lower_text for w in ["not meet", "no valid", "cannot"]):
                return {
                    "label": "BELOW_THRESHOLD_NO_NOTICE",
                    "confidence": "medium",
                    "evidence_sentences": sentences[:3],
                }
        return {
            "label": "AMBIGUOUS",
            "confidence": "low",
            "evidence_sentences": sentences[:3],
        }


def _classify_nsw(first: str, sentences: list[str], days: int) -> dict:
    negation_patterns = [
        r"\bno\b.*\beffect\b",
        r"\bcannot\b.*\bterminat",
        r"\bnot\b.*\bvalid\b",
    ]
    threshold_met_patterns = [
        r"\b14.?day\b.*\bthreshold\b.*\bmet\b",
        r"\bnotice\b.*\bmay\b.*\b(?:take\s+)?effect\b",
        r"\bthreshold\b.*\bsatisfied\b",
    ]

    negated = any(re.search(p, first, re.IGNORECASE) for p in negation_patterns)
    threshold = any(
        re.search(p, " ".join(sentences[:3]), re.IGNORECASE) for p in threshold_met_patterns
    )

    full_text = " ".join(sentences[:5]).lower()
    mentions_s88 = "s88" in full_text or "88(1)" in full_text
    mentions_repay = "repayment" in full_text or "payment plan" in full_text

    if threshold and mentions_s88:
        return {
            "label": "THRESHOLD_MET_NOTICE_MAY_BE_VALID",
            "confidence": "high" if mentions_repay else "medium",
            "evidence_sentences": sentences[:3],
        }
    elif threshold:
        return {
            "label": "THRESHOLD_MET_NOTICE_MAY_BE_VALID",
            "confidence": "medium",
            "evidence_sentences": sentences[:3],
        }
    elif negated and not threshold:
        return {
            "label": "BELOW_THRESHOLD_NO_NOTICE",
            "confidence": "low",
            "evidence_sentences": sentences[:3],
        }
    return {
        "label": "AMBIGUOUS",
        "confidence": "low",
        "evidence_sentences": sentences[:3],
    }


# ── case definitions ──────────────────────────────────────────────────

CASES = [
    {
        "id": "vic-10d",
        "jurisdiction": "VIC",
        "days": 10,
        "query": (
            "My landlord wants to evict me because I am 10 days behind on rent "
            "at my apartment in Melbourne VIC"
        ),
        "expected_conclusion": "Threshold not met; no valid s91ZM arrears notice yet.",
        "required_provisions": ["91ZM"],
        "prohibited_conclusion": "THRESHOLD_MET_NOTICE_MAY_BE_VALID",
        "notes": "First occasion of non-payment in the applicable 12-month period.",
    },
    {
        "id": "vic-14d",
        "jurisdiction": "VIC",
        "days": 14,
        "query": (
            "My landlord wants to evict me because I am 14 days behind on rent "
            "at my apartment in Melbourne VIC"
        ),
        "expected_conclusion": (
            "Threshold met; on first–fourth occasion a notice may be given, "
            "but payment by the termination date makes it ineffective."
        ),
        "required_provisions": ["91ZM"],
        "prohibited_conclusion": None,
        "notes": "First occasion of non-payment in the applicable 12-month period.",
    },
    {
        "id": "vic-21d",
        "jurisdiction": "VIC",
        "days": 21,
        "query": (
            "My landlord wants to evict me because I am 21 days behind on rent "
            "at my apartment in Melbourne VIC"
        ),
        "expected_conclusion": (
            "Same legal framework as the 14-day case; 21 days does not activate a separate rule."
        ),
        "required_provisions": ["91ZM"],
        "prohibited_conclusion": None,
        "notes": "First occasion of non-payment in the applicable 12-month period.",
    },
    {
        "id": "nsw-14d",
        "jurisdiction": "NSW",
        "days": 14,
        "query": (
            "Can my landlord give me a non-payment termination notice today? "
            "I am exactly 14 days behind in rent in Sydney, NSW."
        ),
        "expected_conclusion": (
            "The 14-day threshold is met, so a non-payment notice may take "
            "effect, subject to formal requirements. Payment or compliance "
            "with an agreed repayment plan generally prevents termination "
            "or possession under s89, absent the frequent-failure exception."
        ),
        "required_provisions": ["88", "89"],
        "prohibited_conclusion": None,
        "notes": (
            "Tenant has no history of frequently failing to pay rent on time. "
            "Distinguish notice from eviction — 'termination notice' is the "
            "specific legal instrument."
        ),
    },
    {
        "id": "nsw-21d",
        "jurisdiction": "NSW",
        "days": 21,
        "query": (
            "Can my landlord give me a non-payment termination notice today? "
            "I am exactly 21 days behind in rent in Sydney, NSW."
        ),
        "expected_conclusion": (
            "Same as nsw-14d — the outcome is the same once the ≥14d "
            "threshold is met. Payment or compliance with an agreed "
            "repayment plan generally prevents termination or possession "
            "under s89, absent the frequent-failure exception."
        ),
        "required_provisions": ["88", "89"],
        "prohibited_conclusion": None,
        "notes": ("Tenant has no history of frequently failing to pay rent on time."),
    },
]


def _check_citations(verified: list[str], unverified: list[str], required: list[str]) -> dict:
    found = [r for r in required for v in verified if r in v]
    return {
        "required_present": sorted(found),
        "required_missing": sorted(set(required) - set(found)),
        "verified_count": len(verified),
        "unverified_count": len(unverified),
        "all_verified": len(unverified) == 0,
    }


def _check_material_errors(conclusion: dict, case_def: dict) -> dict:
    errors: list[str] = []
    prohibited = case_def.get("prohibited_conclusion")
    if prohibited and conclusion["label"] == prohibited:
        errors.append(
            f"Prohibited conclusion: got {conclusion['label']!r}, expected not {prohibited!r}"
        )
    return {
        "has_material_error": len(errors) > 0,
        "errors": errors,
    }


def _run_case(case: dict, provider: str, run_index: int) -> dict:
    """Run one case once and return structured metrics."""
    os.environ["LLM_PROVIDER"] = provider

    from src.rag.generation.generator import generate_compliance_answer

    t0 = time.perf_counter()
    result = generate_compliance_answer(
        query=case["query"],
        state_filter=case["jurisdiction"],
        top_k_retrieve=10,
    )
    latency_ms = (time.perf_counter() - t0) * 1000

    answer = result["answer"]
    verified = result["citation_check"].get("verified", [])
    unverified = result["citation_check"].get("unverified", [])
    retrieved = result.get("retrieved_chunks", [])

    conclusion = _classify_conclusion(answer, case["jurisdiction"], case["days"])
    citation_report = _check_citations(verified, unverified, case["required_provisions"])
    error_report = _check_material_errors(conclusion, case)

    return {
        "run": run_index + 1,
        "provider": provider,
        "case_id": case["id"],
        "answer": answer,
        "answer_length": len(answer),
        "conclusion_label": conclusion["label"],
        "conclusion_confidence": conclusion["confidence"],
        "conclusion_evidence": conclusion["evidence_sentences"],
        "verified_citations": verified,
        "unverified_citations": unverified,
        "required_provisions_found": citation_report["required_present"],
        "required_provisions_missing": citation_report["required_missing"],
        "verified_count": citation_report["verified_count"],
        "unverified_count": citation_report["unverified_count"],
        "all_citations_verified": citation_report["all_verified"],
        "has_material_error": error_report["has_material_error"],
        "material_errors": error_report["errors"],
        "retrieved_section_ids": sorted(
            set(c.get("section_id", "") for c in retrieved if c.get("section_id"))
        ),
        "latency_ms": round(latency_ms, 1),
    }


def main() -> None:
    load_dotenv()

    providers = ["deepseek", "bedrock"]
    runs_per_case = 3
    all_runs: list[dict] = []

    print("=" * 60)
    print("Arrears Threshold Evaluation")
    print(f"Cases: {len(CASES)}, Providers: {len(providers)}, Runs/case: {runs_per_case}")
    print(f"Total runs: {len(CASES) * len(providers) * runs_per_case}")
    print("=" * 60)

    total = len(CASES) * len(providers) * runs_per_case
    current = 0

    for provider in providers:
        if provider == "bedrock" and "BEDROCK_TEMPERATURE" not in os.environ:
            os.environ["BEDROCK_TEMPERATURE"] = "0"
        for case in CASES:
            for run_i in range(runs_per_case):
                current += 1
                print(
                    f"[{current}/{total}] {provider:>8} | {case['id']:<10}"
                    f" | run {run_i + 1}/{runs_per_case}"
                )
                try:
                    run_data = _run_case(case, provider, run_i)
                    all_runs.append(run_data)
                    status = "ERROR" if run_data["has_material_error"] else "OK"
                    cites = f"V{run_data['verified_count']}/U{run_data['unverified_count']}"
                    concl = run_data["conclusion_label"]
                    print(
                        f"  → {status} | {cites} | {concl} | "
                        f"sections: {run_data['retrieved_section_ids']}"
                    )
                except Exception as exc:
                    print(f"  → FAILED: {exc}")
                    all_runs.append(
                        {
                            "run": run_i + 1,
                            "provider": provider,
                            "case_id": case["id"],
                            "error": str(exc),
                            "has_material_error": True,
                            "material_errors": [str(exc)],
                        }
                    )

    # ── aggregate per case/provider ───────────────────────
    summary: dict = {}
    for case in CASES:
        for provider in providers:
            key = f"{case['id']}_{provider}"
            runs = [r for r in all_runs if r["case_id"] == case["id"] and r["provider"] == provider]
            if not runs:
                summary[key] = {"status": "NO_RUNS"}
                continue

            conclusions = [r.get("conclusion_label") for r in runs if "conclusion_label" in r]
            errors = [r for r in runs if r.get("has_material_error")]
            unverified_total = sum(r.get("unverified_count", 0) for r in runs)
            latencies = [r.get("latency_ms", 0) for r in runs if r.get("latency_ms")]

            agreement = len(set(conclusions)) == 1 if conclusions else False
            all_required = all(
                len(r.get("required_provisions_missing", [])) == 0
                for r in runs
                if "required_provisions_missing" in r
            )
            no_errors = len(errors) == 0

            summary[key] = {
                "runs_completed": len(runs),
                "conclusion_agreement": agreement,
                "conclusions": conclusions,
                "required_provisions_always_present": all_required,
                "material_errors": len(errors),
                "total_unverified_citations": unverified_total,
                "mean_latency_ms": round(sum(latencies) / len(latencies), 1) if latencies else None,
                "all_clean": agreement and no_errors and unverified_total == 0,
            }

    out_dir = Path("reports/threshold_evaluation")
    out_dir.mkdir(parents=True, exist_ok=True)
    timestamp = time.strftime("%Y%m%d_%H%M%S", time.gmtime())
    out_path = out_dir / f"eval_{timestamp}.json"

    report = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "cases": [c["id"] for c in CASES],
        "providers": providers,
        "runs_per_case": runs_per_case,
        "case_definitions": [
            {
                "id": c["id"],
                "expected_conclusion": c["expected_conclusion"],
                "required_provisions": c["required_provisions"],
                "notes": c["notes"],
            }
            for c in CASES
        ],
        "summary": summary,
        "runs": all_runs,
    }
    out_path.write_text(json.dumps(report, indent=2, ensure_ascii=False, default=str))
    print(f"\nSaved: {out_path}")

    # Print quick verdict
    print("\n--- Verdict ---")
    for case in CASES:
        for provider in providers:
            key = f"{case['id']}_{provider}"
            s = summary.get(key, {})
            label = "CLEAN" if s.get("all_clean") else "ISSUES"
            print(
                f"{key:<25} {label:<8} "
                f"agree={s.get('conclusion_agreement')} "
                f"errors={s.get('material_errors')} "
                f"unverified={s.get('total_unverified_citations')}"
            )


if __name__ == "__main__":
    main()
