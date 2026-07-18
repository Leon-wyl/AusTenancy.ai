# Evaluation Implementation — VIC & NSW Tenancy RAG Pipeline

## Overview

A production-grade evaluation suite for Australian Residential Tenancies legislation, covering the Victorian (VIC) and New South Wales (NSW) tenancy Acts and Regulations. Built with the Ragas framework (v0.1.22), measuring three core metrics:

| Metric | What it measures | Final score |
|--------|-----------------|-------------|
| **Faithfulness** | Fraction of atomic claims in the answer entailed by retrieved context | 0.41 |
| **Context Precision** | Fraction of retrieved chunks judged relevant to the answer | 0.51 |
| **Answer Relevancy** | Semantic similarity between the answer and the original question | 0.86 |

## Architecture

```
evaluation/
├── tests/evaluation/
│   ├── vic_golden_dataset.json          # 20 QA pairs (5 per domain)
│   ├── vic_golden_contexts.json         # Pre-computed golden contexts for diagnostics
│   ├── vic_regulation_golden_dataset.json   # VIC Regulation golden QA pairs
│   ├── vic_regulation_golden_contexts.json  # VIC Regulation golden contexts
│   ├── nsw_golden_dataset.json          # 20 NSW QA pairs
│   ├── nsw_golden_contexts.json         # NSW golden contexts
│   ├── nsw_regulation_golden_dataset.json   # NSW Regulation golden QA pairs
│   └── nsw_regulation_golden_contexts.json  # NSW Regulation golden contexts
├── src/rag/evaluation/
│   ├── __init__.py
│   ├── run_ragas_eval.py                # Evaluation runner with CLI flags
│   ├── citation_metrics.py              # Deterministic citation metrics
│   ├── gen_golden_contexts.py           # Golden context pre-computation
│   └── audit_sections.py               # Section audit utility
└── reports/
    ├── vic_eval_report.csv              # VIC baseline
    ├── nsw_eval_report.csv              # NSW baseline
    ├── vic_regulation_eval_report.csv   # VIC Regulation
    ├── nsw_regulation_eval_report.csv   # NSW Regulation
    └── ... (experiment reports)
├── docs/
│   └── EVALUATION_IMPLEMENTATION.md     # This document
```

### Key architectural decisions

- **No langchain_openai dependency**. The evaluator LLM (`DeepSeekRagasLLM`) implements Ragas's `BaseRagasLLM` interface directly using `openai.OpenAI` — the same client as the RAG pipeline.
- **No langchain_openai for embeddings either**. `FastembedRagasEmbeddings` wraps the project's existing `fastembed` (BGE-small-en-v1.5) for the `answer_relevancy` metric.
- **Python 3.14 compatibility**. Ragas's internal async executor is bypassed — each metric's `_ascore()` method is called via `asyncio.run()` with `agenerate_text` delegated to a thread pool. This avoids both the `nest_asyncio` deadlock and the "Timeout should be used inside a task" error.

### Golden dataset design

20 QA pairs, 5 per legal domain, each with:
- A colloquial, emotionally charged question (simulating real tenants/landlords)
- An IRAC-format ground truth with specific statutory citations
- Metadata: domain, section references, difficulty, role, location

| Domain | Sections covered | Example topic |
|--------|-----------------|---------------|
| Rent Increases | 44, 45, 46, 47, 48 | 90-day notice, 12-month frequency, calculation method |
| Terminations & Evictions | 91Z, 91ZM, 91ZW, 91ZZS, 91ZZT, 91ZZO, 91E | 14-day rent arrears, landlord moving in, end of fixed term |
| Repairs & Maintenance | 68, 72, 73, 74, 75, 76, 78, 79 | Urgent repairs, $2,500 limit, non-urgent timelines, burst pipe |
| Bond Claims | 31, 35, 36, 405, 406, 408, 409, 410, 411, 419A, 420 | Condition reports, max bond, RTBA claims, VCAT disputes |

---

## Pipeline Improvements (chronological)

| Step | Change | F | CP | AR | Delta |
|------|--------|---|----|-----|-------|
| 0 | **Original** (no fixes, no reranker wired) | 0.434 | 0.486 | 0.536 | — |
| 1 | **Part filter + reranker ON** | 0.403 | **+0.138** | **+0.139** | CP/AR jump, faith dip |
| 2 | **Remove reranker (T2)** | 0.409 | 0.542 | **0.752** | Best AR, faith recovers |
| 3 | **AR disclaimer fix** (prompt) | 0.404 | 0.521 | **0.832** | Q9/Q18 from AR=0 → AR>0.8 |
| 4 | **Faith prompt fix** (final) | 0.411 | 0.513 | **0.858** | Faith stable, AR peaks |
| 5 | **Golden contexts** (diagnostic) | 0.383 | 0.968 | 0.792 | Proves LLM/format bottleneck |

### Step 1: Part metadata filter

**Problem**: The RTA 1997 covers standard residential tenancies (Part 2), rooming houses (Part 3), caravan parks (Part 4), site agreements (Part 4A), and SDA dwellings (Part 12A). Hybrid search was retrieving all types equally, producing irrelevant context.

**Fix**: Added `exclude_parts=["3", "4", "4A", "12A"]` to `hybrid_retrieve()` in `vector_store.py`. Uses Qdrant `must_not` filter conditions alongside the existing state filter.

**Impact**: Context precision +28%, answer relevancy +26%. The pipeline now retrieves only standard residential tenancy provisions.

### Step 2: Ablation study — removing the reranker

**Problem**: FlashRank (a general-domain cross-encoder trained on MS MARCO) was degrading retrieval for legal statute text. Despite the section 91ZM being ranked #1 by hybrid search, the reranker sometimes demoted it below the top-5 cutoff.

**Ablation test matrix** (4 configurations on all 20 QAs):

| Config | Reranker | Query to retriever | Query to reranker | F | CP | AR |
|--------|----------|-------------------|-------------------|-----|-----|-----|
| T1 | ON | rewritten | rewritten | 0.352 | 0.615 | 0.681 |
| **T2** | **OFF** | rewritten | N/A | **0.409** | 0.542 | **0.752** |
| T3 | OFF | original | N/A | 0.430 | 0.533 | 0.668 |
| T4 | ON | rewritten | original | 0.366 | 0.498 | 0.739 |

**Conclusion**: T2 (reranker OFF, rewrite ON) is optimal. The reranker improves context precision by +7 points but drops faithfulness by -5.6 points — over-filtering from 10 chunks to 5 removes supporting context the LLM needs to ground claims.

### Step 3: AR disclaimer fix

**Problem**: Q9 ("break lease") and Q18 ("bond return") had `answer_relevancy = 0.0`. The LLM was opening every answer with "Based on the available statutory database, no definitive compliance conclusion can be drawn..." even when it had relevant provisions. The AR metric generated questions from the disclaimer text, producing zero semantic similarity to the original tenancy query.

**Fix**: Replaced the rigid uncertainty rule in SYSTEM_PROMPT:

```
Before: "If you cannot give a definitive answer, begin with the uncertainty statement..."
After:  "Lead with what the context DOES support, not what it doesn't.
         Only begin with a disclaimer when the context is entirely silent.
         Add a 'Limitations' paragraph at the end for gaps."
```

**Impact**: AR +11% overall. Q9: 0.00 → 0.91. Q18: 0.00 → 0.82.

### Step 4: Faithfulness prompt fix

**Problem**: Faithfulness remained stubborn at ~0.40 across all configuration changes.

**Fix**: Extended the citation rule to require citations in the Application section, not just the Rule section. Changed IRAC guidance from "full analysis" to "concise analysis — avoid repeating user's facts."

**Impact**: Faith unchanged (+0.007, within noise). The LLM increased citations (6/13 sentences cited vs 3/16 before) but the faithfulness metric measures entailment, not citation count. A sentence like "The provider is prohibited from increasing the rent [VIC RTA 1997 Sec 44(4)]" is cited but the claim "the provider IS prohibited" is a legal conclusion — not verbatim in the statute.

---

## Golden-Context Diagnostic

**Purpose**: Determine whether the faithfulness bottleneck is retrieval or LLM behavior. For each QA, replace the retriever's output with only the statutory sections cited by the ground truth — giving the LLM perfect context.

**Test QAs** (spanning worst to best faith):

| QA | Domain | Current F | Golden F | Delta | #Chunks |
|----|--------|-----------|----------|-------|---------|
| Q2 | rent_increases | 0.125 | 0.143 | +0.02 | 1 |
| Q12 | repairs | 0.182 | 0.033 | **-0.15** | 3 |
| Q20 | bonds | 0.244 | 0.290 | +0.05 | 5 |
| Q9 | terminations | 0.615 | 0.667 | +0.05 | 1 |
| Q10 | terminations | 0.312 | 0.553 | **+0.24** | 3 |

**Overall mean delta: -0.028** (golden contexts made faith slightly WORSE)

**Interpretation**: The LLM/format is the primary bottleneck. Three key findings:

1. **Cross-reference effect (Q12, -0.15)**: With only the 3 cited sections, the LLM loses surrounding context from the other 7 "noisy" chunks. The noise actually helps ground claims through cross-references.

2. **Q2 is the smoking gun**: Even with the full Section 44 text (1851 chars, all subsections as golden context), faith is 0.14. The LLM cannot produce faithful IRAC answers even with perfect statutory text.

3. **Q10 is the exception (+0.24)**: For this specific question, retrieval was the bottleneck. But it's the only QA with a large positive delta.

---

## Faithfulness Deep Dive

### What the metric actually measures

Ragas faithfulness operates in three stages:

1. **Sentence segmentation**: The answer is split into sentences using `pysbd`.
2. **Statement decomposition**: Each sentence is broken into atomic claims by an LLM (removing pronouns, splitting compound claims).
3. **NLI verification**: Each atomic statement is checked against all retrieved contexts via an LLM judge. The judge returns 1 (entailed) or 0 (not entailed).

Score = supported statements / total statements.

### Why 0.41 is a structural ceiling for IRAC legal answers

IRAC answers contain four types of content, only one of which is measurable:

| Section | Example claim | Entailed by context? |
|---------|--------------|---------------------|
| **Rule** | "Section 44(1) requires 90 days notice [VIC RTA 1997 Sec 44(1)]" | ✓ Yes (directly in statute) |
| **Issue** | "Whether the notice constitutes a valid rent increase..." | ✗ No (LLM's framing) |
| **Application** | "Your lease says nothing about increases, therefore the provider is prohibited..." | ✗ No (fact-to-law mapping) |
| **Conclusion** | "The increase is invalid under sections 44(4) and 44(5)" | ✗ No (legal conclusion) |
| **Practical advice** | "Reply to the agent in writing stating the increase is invalid" | ✗ No (procedural advice) |

With IRAC, only the Rule section is grounded. The other sections are the LLM's interpretation by design — and that's what makes the tool useful. A 100% faithful legal RAG answer would be a direct quote of the statute, which a user could read themselves.

### Why citation verification is the real trust signal

The pipeline's `verify_citations()` function cross-checks every `[VIC RTA 1997 Sec XXX]` citation in the answer against the retrieved context. Across all 20 QAs in the baseline run: **100% verified, 0 unverified**. The LLM is not hallucinating fake section numbers — every citation exists in the actual Act.

This is a stronger trust signal than the aggregate faithfulness score. Users can independently verify: "Does section 44 really say this?" A user cannot verify an aggregate 0.41 metric.

---

## Final Baseline Configuration (T2)

| Parameter | Value | Rationale |
|-----------|-------|-----------|
| Query rewrite | ON | Improves retrieval precision without hurting faithfulness |
| Reranker (FlashRank) | OFF (default) | Over-filters legal context; ablation proved it hurts faith. See "Why the reranker is disabled" below. |
| Part filter | exclude 3, 4, 4A, 12A | Removes rooming house/caravan park/SDA noise |
| Top-K retrieve | 10 | Enough for good recall; more hurts CP without faith improvement |
| IRAC format | ON | Changed from rigid disclaimer to "lead with what context supports" |
| Citation verification | ON | 100% verified across all test QAs |

### Baseline metrics

| Metric | Overall | Rent | Terminations | Repairs | Bonds |
|--------|---------|------|-------------|---------|-------|
| Faithfulness | 0.41 | 0.31 | 0.49 | 0.40 | 0.44 |
| Context Precision | 0.51 | 0.82 | 0.55 | 0.36 | 0.33 |
| Answer Relevancy | 0.86 | 0.86 | 0.85 | 0.85 | 0.88 |

The `exclude_parts` filter is a configurable pipeline parameter (not hardcoded). Pass `None` for the default standard residential filter, `[]` for no filtering, or a custom list to include/exclude specific Parts. The eval script exposes this as `--exclude-parts PARTS` (comma-separated). See the [CLI reference](#cli-reference) for usage.

---

## Why the reranker is disabled

The reranker is **OFF by default** across the entire pipeline — both the library
(`generate_compliance_answer(..., use_reranker=False)`) and the eval CLI
(`run_ragas_eval.py` no longer enables it unless `--rerank` is passed explicitly).
Legal RAG must not use FlashRank.

**Reason.** FlashRank ships a general-domain cross-encoder trained on MS MARCO
web-search relevance. Statutory text (dense cross-referenced provisions, numeric
section IDs, procedural language) is out-of-distribution for it. Empirically, when
enabled it demotes the correct sections — which hybrid search already ranked in the
top results — below the top-5 cutoff, and fills the top slots with off-target
sections. This collapses context precision to ~0 on exactly the questions where
retrieval succeeded. Observed examples (reranker ON, top-15 → top-5):

- NSW rent-increase query: `s44` retrieved rank #1 → dropped entirely from top-5.
- NSW rent-increase query: `s41`+`s44` retrieved → only `s41` survived; top slots filled with `s99, s87L, s87H`.
- VIC bond query: `s31,s32,s34` retrieved → only `s31` survived; `s34A, s407, s411AB` promoted instead.

The ablation study (see [Step 2](#step-2-ablation-study--removing-the-reranker))
also showed reranker ON dropped faithfulness by ~5.6 points because over-filtering
from the retrieved set to 5 chunks removes the surrounding context the LLM relies on
for cross-references.

**Code is retained** (`rerank_context()` in `generator.py`) for opt-in
experimentation only — e.g. if a legal-domain cross-encoder becomes available. To
try it, pass `--rerank` to the eval CLI. Default runs never call it.

---

## Resolved — VIC Q6 & NSW Q19 (golden-label errors + parser truncation)

Two questions initially showed low context precision / answer relevancy. Deeper
investigation found the low scores were **not** a retrieval or answer-quality
problem — the RAG answers were substantively correct. The true causes were
(1) **factual errors in the golden dataset annotations** and (2) a **VIC parser
chunk-truncation bug**. Both were fixed.

### NSW Q19 (open houses / notice of sale)

- **Original golden**: `sections = [55, 56]`; the ground-truth RULE claimed
  *"Section 55 permits a landlord to enter to show premises to prospective
  purchasers, on not more than 2 occasions in any period of 7 days"*.
- **Statute check**: This is wrong. **s53** ("Sale of residential premises") is
  the controlling provision — 14 days' notice before first inspection, landlord
  must make reasonable efforts to agree times, and the tenant *is not required to
  agree to inspections more than twice a week* (s53(4)). **s55(2)(f)** only allows
  access to show purchasers *"if the landlord and tenant fail to agree under
  section 53"*, capped at twice per week with 48 hours' notice. **s56** ("Entry
  with tenant's consent") is largely irrelevant to a *refusal* question.
- **Fix**: golden `sections` corrected to `[53, 55]` and the RULE/APPLICATION/
  CONCLUSION rewritten to match the statute. The system's own answer (citing
  s53/55/57) was already more accurate than the original label.

### VIC Q6 (landlord resumes occupation)

- **Original golden**: `sections = [91ZW, 91ZZO, 91ZZS]`; the CONCLUSION said the
  renter can *"challenge it at VCAT under section 91ZZS within 30 days"*.
- **Statute check**: This is wrong. **s91ZZS(1)** applies only to notices given
  under `91ZX, 91ZY, 91ZZ, 91ZZA, 91ZZB or 91ZZC` — **it does not cover 91ZW**.
  So a 91ZW ("principal place of residence") notice cannot be challenged via
  91ZZS. **s91ZW** (grounds) and **s91ZZO** (form-of-notice validity) are the
  correct provisions.
- **Fix**: golden `sections` corrected to `[91ZW, 91ZZO]`, and the erroneous
  91ZZS sentence replaced with an accurate note that 91ZZS does not apply to
  91ZW notices.

### VIC parser chunk-truncation bug (found during the investigation)

While verifying the statute text, we found `91ZZS`, `91ZZO`, and **10 other VIC
sections** were truncated mid-sentence in the chunk files. Root cause: the VIC
`SECTION_RE` matched cross-reference list continuations inside a section body
(e.g. a body line `"91ZZB or 91ZZC, a renter who has received the"`) and treated
`91ZZB` as the start of a *new* section, prematurely flushing the real one and
emitting bogus fragment chunks (18 of them, with lowercase-starting "titles").

**Fix**: `_is_valid_section_title()` in `vic_parser.py` now requires the title's
first character to be an uppercase letter. Verified safe — all 980 genuine VIC
section headings start uppercase; the only "lowercase-only" id (`91ZZDA`) was an
amendment-note artifact, not a real section. VIC chunk count went 1029 → 1011
(−18 bogus fragments); the 12 truncated sections are now complete. Regression
tests added: `TestVICSectionTruncation` asserts no lowercase-starting titles and
that `91ZZS`/`91ZZO` retain their full cross-reference lists.

### Result

After re-ingesting and re-running the 40-question eval (reranker off):
NSW Q19 context precision rose from ~0.20 to **1.00**; VIC Q6 recovered to
CP 0.33 / AR 0.85 (its golden now correctly excludes the inapplicable 91ZZS).

---

## Python 3.14 Compatibility

Python 3.14 introduced stricter asyncio behavior (no automatic event loop creation, `asyncio.timeout()` requires task context). Ragas 0.1.x/0.2.x applies `nest_asyncio` internally, which deadlocks with Python 3.14.

**Workarounds implemented in `run_ragas_eval.py`:**

1. Bypass Ragas's `evaluate()` function entirely — call each metric's `_ascore()` directly via `asyncio.run()`.
2. `DeepSeekRagasLLM.agenerate_text()` delegates to `generate_text()` via `asyncio.to_thread()` — the custom LLM works in both sync and async contexts.
3. Pinned `ragas==0.1.22` with `langchain-core==0.2.43` (downgraded from 1.4.x by installing ragas last).

---

## Key Learnings

### What worked

- **Part metadata filter** — single most impactful change (+28% CP, +26% AR). Simple, zero-cost, uses existing chunk metadata.
- **AR disclaimer fix** — changed one paragraph in SYSTEM_PROMPT, eliminated AR=0 on two critical questions.
- **Golden-context diagnostic** — proved the LLM/format bottleneck in one test run. Prevented wasted investment in retrieval improvements.

### What didn't

- **FlashRank reranker** — general-domain cross-encoder degrades legal retrieval. Not worth the compute without a legal-domain model.
- **Query rewrite to original query** — the LLM-based rewrite is better than passing raw colloquial text to the retriever (T2 > T3).
- **Faithfulness prompt tweaks** — adding citation requirements achieved nothing. The metric measures entailment, not citation count.

### What we'd do differently

- Run the golden-context diagnostic FIRST, before any pipeline changes. It would have saved the reranker ablation effort.
- Accept the ~0.40 faithfulness ceiling for IRAC earlier and focus energy on the metrics that CAN be improved (CP, AR).

---

## Citation Metrics (deterministic)

The `citation_metrics.py` module provides deterministic, LLM-free citation evaluation. It computes:

| Field | Description |
|-------|-------------|
| `citation_precision` | Fraction of citations in the answer that are verified against retrieved contexts |
| `golden_recall_retrieval` | Fraction of golden section refs found in retrieved contexts |
| `golden_recall_citation` | Fraction of golden section refs found in verified citations |
| `reg_citations_in_answer` | Count of Regulation citations in the answer |
| `reg_citation_verified` | Whether at least one Regulation citation is verified |

The module understands the `reg:` prefix convention for golden section references and matches them to actual citation labels using a combination of chunk section_id lookup and canonical label formatting.

**Integration:** `run_ragas_eval.py` automatically computes citation metrics alongside Ragas metrics when golden section references (`metadata.sections`) are present in the dataset. Both sets of metrics appear in the output CSV.

---

## Regulation Retrieval Workflow

Regulation support is implemented for both VIC and NSW:

| Component | File |
|-----------|------|
| VIC Regulation parser | `src/rag/data_processing/vic_regulation_parser.py` |
| NSW Regulation parser | `src/rag/data_processing/nsw_regulation_parser.py` |

### Citation formats

- Act sections: `[VIC RTA 1997 Sec 44(1)]`, `[NSW RTA 2010 Sec 85]`
- Regulation provisions: `[VIC REG 2021 Reg 21]`, `[NSW REG 2019 Reg 15]`
- Schedule/Form references: `[VIC REG 2021 Sch 1 Form 6]`

### STATUTORY prompt reformulation

When the query is classified as STATUTORY:
- Regulation keyword expansion is gated and instrument-aware
- Prompt vocabulary is reformulated (Act vs Regulation language)
- NSW condition-report few-shot examples are injected

### Measured impact

| Metric | Delta |
|--------|-------|
| VIC context_precision | +0.167 (31% improvement) |
| VIC Regulation verified rate | 70% → 85% |
| NSW Regulation verified rate | 80% → 90% |

---

## Multi-State Batch Evaluation

`--state all` runs evaluation across all supported states (currently VIC + NSW) in sequence:

- Per-state golden datasets, contexts, and output paths
- VIC automatically uses the curated 10-QA batch subset
- 3-second pause between states (rate limit guard)
- Cross-state summary printed at the end with per-state metric means
- Errors in one state do not halt evaluation of remaining states
- Requires `DEEPSEEK_API_KEY` set (unless `--dry-run`)

---

## Roadmap Adjustments

Based on the evaluation results, two roadmap decisions were made:

### Fine-tuning (Steps 2-3): Deprioritized

The golden-context diagnostic proved retrieval quality is not the primary bottleneck.
Key evidence:
- Context precision improved from 0.49 → 0.62 with just Part metadata filtering
- The current BGE-small + BM25 hybrid search already ranks the correct sections first
  (e.g., Sec 91ZM at rank #1 for eviction queries)
- Given perfect statutory text (golden contexts), faithfulness did not improve
  (overall delta -0.03)

Fine-tuning would address a retrieval problem that doesn't exist. Steps 2-3 are
deferred until multi-state scaling reveals cross-jurisdiction retrieval gaps that
a domain-adapted embedding model would resolve.

### IRAC Format: Retained

A two-section answer format ("Statutory Provisions" / "Application to Your
Situation") was considered but rejected. IRAC is the professional standard for
legal analysis. The faithfulness metric's ceiling (~0.40) reflects a misalignment
between the metric (designed for factual QA) and IRAC (designed for legal reasoning),
not a format deficiency. Revisit if multi-state evaluation or user feedback
identifies format-specific issues.

### Next Priority: Phase B (Multi-State Scaling — in progress)

Steps 4-6 (NSW → all 8 jurisdictions → multi-state RAGAS evaluation) are the
next highest-impact work. **Shipped so far:** NSW golden datasets (20 QA pairs),
batch evaluation (`--state all`), VIC + NSW Regulation parsers and citation
support. **Deferred:** QLD, SA, WA, TAS, ACT, NT (6 jurisdictions).

---

## Evaluation Usage

```bash
# Dry-run (validate dataset, no LLM calls)
python src/rag/evaluation/run_ragas_eval.py --dry-run

# Baseline T2 config (golden-context mode, reranker OFF by default, rewrite ON)
python src/rag/evaluation/run_ragas_eval.py --output report.csv

# Real retrieval mode (full pipeline: hybrid search → generation)
python src/rag/evaluation/run_ragas_eval.py --eval-mode real-retrieval --output real_eval.csv

# Test with rooming houses included (exclude only caravan parks, site agreements, SDA)
python src/rag/evaluation/run_ragas_eval.py --exclude-parts "4,4A,12A"

# Test with all Parts (no filter)
python src/rag/evaluation/run_ragas_eval.py --exclude-parts ""

# NSW evaluation
python src/rag/evaluation/run_ragas_eval.py --state NSW --output nsw_eval.csv

# Multi-state batch evaluation (VIC + NSW)
python src/rag/evaluation/run_ragas_eval.py --state all

# Full ablation — opt in to the (disabled-by-default) reranker
python src/rag/evaluation/run_ragas_eval.py \
    --rerank --no-rewrite --reranker-query-original \
    --limit 5 --output ablation.csv

# Golden-context diagnostic
python src/rag/evaluation/run_ragas_eval.py \
    --golden-contexts tests/evaluation/vic_golden_contexts.json \
    --output golden.csv
```

### Evaluation modes

| Mode | What it does | Default |
|------|-------------|---------|
| `golden-context` | Bypasses retrieval entirely; uses pre-computed golden contexts. Tests LLM generation quality in isolation. | Yes |
| `real-retrieval` | Runs the full pipeline (hybrid search + generation). Tests end-to-end retrieval + generation quality. | No |

### CLI reference

| Flag | Effect |
|------|--------|
| `--state STATE` | State filter: `VIC`, `NSW`, or `all` (batch across all states) |
| `--dry-run` | Validate datasets without LLM calls; `--state all` runs smoke test per state |
| `--limit N` | Per-state limit in batch mode (0 = all) |
| `--eval-mode MODE` | `golden-context` (bypass retrieval) or `real-retrieval` (full pipeline, default: `golden-context`) |
| `--batch` | [VIC only] Use curated 10-QA subset; `--state all` applies VIC batch automatically |
| `--top-k N` | Chunks to retrieve (default: 10) |
| `--rerank` | Enable FlashRank reranking (DISABLED by default — degrades legal RAG) |
| `--no-rerank` | [Deprecated no-op] reranker is disabled by default; kept for compat |
| `--no-rewrite` | Disable LLM query rewrite |
| `--reranker-query-original` | Pass original query to the reranker (only applies with `--rerank`) |
| `--golden-contexts PATH` | Override golden contexts path (ignored in `--state all`) |
| `--exclude-parts PARTS` | Override auto-populated exclude_parts; empty string = no filter |
| `--include-parts PARTS` | Parts to include (carveback), comma-separated |
| `--include-chapters PARTS` | Chapters to include (carveback), comma-separated (QLD only) |
| `--samples IDX` | Comma-separated 0-indexed sample indices to evaluate (e.g. `'15,16,18'`) |
| `--dataset PATH` | Golden dataset path (default: `tests/evaluation/{state}_golden_dataset.json`) |
| `--output PATH` | CSV output path (default: `reports/{state}_eval_report.csv`) |

---

## File Index

| File | Purpose |
|------|---------|
| `src/rag/generation/generator.py` | RAG pipeline: rewrite → retrieve → prompt → LLM → verify (reranker disabled by default). Contains SYSTEM_PROMPT, QUERY_REWRITE_PROMPT, `generate_compliance_answer()`, `rerank_context()` (retained, opt-in only), `verify_citations()`. |
| `src/rag/retrieval/vector_store.py` | Qdrant ingestion and hybrid retrieval (dense + sparse RRF) with metadata filters, including `exclude_parts` parameter. |
| `src/rag/evaluation/run_ragas_eval.py` | Evaluation script with `DeepSeekRagasLLM`, `FastembedRagasEmbeddings`, CLI flags for all configurations, intermediate save/resume. |
| `src/rag/evaluation/citation_metrics.py` | Deterministic citation metrics: citation precision, golden provision recall, Regulation indicators. No LLM calls needed. |
| `src/rag/evaluation/gen_golden_contexts.py` | Pre-compute golden contexts for diagnostic mode. |
| `src/rag/evaluation/__init__.py` | Package marker. |
| `tests/evaluation/vic_golden_dataset.json` | 20 VIC QA pairs with IRAC-format ground truths. |
| `tests/evaluation/vic_golden_contexts.json` | Pre-computed VIC golden contexts. |
| `tests/evaluation/nsw_golden_dataset.json` | 20 NSW QA pairs with IRAC-format ground truths. |
| `tests/evaluation/nsw_golden_contexts.json` | Pre-computed NSW golden contexts. |
| `tests/evaluation/vic_regulation_golden_dataset.json` | VIC Regulation golden QA pairs with `reg:` section refs. |
| `tests/evaluation/vic_regulation_golden_contexts.json` | Pre-computed VIC Regulation golden contexts. |
| `tests/evaluation/nsw_regulation_golden_dataset.json` | NSW Regulation golden QA pairs with `reg:` section refs. |
| `tests/evaluation/nsw_regulation_golden_contexts.json` | Pre-computed NSW Regulation golden contexts. |
| `data/processed/vic_rta_chunks.json` | Parsed and chunked VIC RTA 1997 (1011 chunks with the current parser; copies generated before the truncation fix contain 1029 — re-run `vic_parser.py` to regenerate). |
| `reports/*.csv` | Evaluation output reports. |
| `requirements.txt` | Pinned dependencies including `ragas==0.1.22`. |
| `pyproject.toml` | Project config with `[project.optional-dependencies].eval` for Ragas tooling. |
| `docs/EVALUATION_PLAN.md` | Original evaluation strategy document (pre-implementation). |
| `docs/EVALUATION_IMPLEMENTATION.md` | This document. |
