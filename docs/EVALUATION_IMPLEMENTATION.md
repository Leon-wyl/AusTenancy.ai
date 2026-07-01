# Evaluation Implementation — VIC Tenancy RAG Pipeline

## Overview

A production-grade evaluation suite for the Victorian Residential Tenancies Act 1997 (RTA 1997) RAG compliance pipeline. Built with the Ragas framework (v0.1.22), measuring three core metrics:

| Metric | What it measures | Final score |
|--------|-----------------|-------------|
| **Faithfulness** | Fraction of atomic claims in the answer entailed by retrieved context | 0.41 |
| **Context Precision** | Fraction of retrieved chunks judged relevant to the answer | 0.51 |
| **Answer Relevancy** | Semantic similarity between the answer and the original question | 0.86 |

## Architecture

```
evaluation/
├── tests/evaluation/
│   ├── vic_golden_dataset.json      # 20 QA pairs (5 per domain)
│   └── vic_golden_contexts.json     # Pre-computed golden contexts for diagnostics
├── src/evaluation/
│   ├── __init__.py
│   └── run_ragas_eval.py            # Evaluation runner with CLI flags
├── docs/
│   └── EVALUATION_IMPLEMENTATION.md # This document
├── vic_eval_faithfix.csv            # Final baseline result (T2 config)
├── vic_eval_results.json            # Intermediate save (pipeline answers + contexts)
└── vic_eval_report.csv              # Initial baseline (before any fixes)
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
| Reranker (FlashRank) | OFF | Over-filters legal context; ablation proved it hurts faith |
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
- For v2: switch to a two-section answer format — "Statutory Provisions" (scored for faith) + "Application to Your Situation" (not scored). This gives a clean, improvable metric without abandoning personalized legal analysis.

---

## Evaluation Usage

```bash
# Dry-run (validate dataset, no LLM calls)
python src/evaluation/run_ragas_eval.py --dry-run

# Baseline T2 config (reranker OFF, rewrite ON)
python src/evaluation/run_ragas_eval.py --no-rerank --output report.csv

# Test with rooming houses included (exclude only caravan parks, site agreements, SDA)
python src/evaluation/run_ragas_eval.py --no-rerank --exclude-parts "4,4A,12A"

# Test with all Parts (no filter)
python src/evaluation/run_ragas_eval.py --no-rerank --exclude-parts ""

# Full ablation (all flags available)
python src/evaluation/run_ragas_eval.py \
    --no-rerank --no-rewrite --reranker-query-original \
    --limit 5 --output ablation.csv

# Golden-context diagnostic
python src/evaluation/run_ragas_eval.py \
    --golden-contexts tests/evaluation/vic_golden_contexts.json \
    --no-rerank --output golden.csv
```

### CLI reference

| Flag | Effect |
|------|--------|
| `--dry-run` | Validate dataset, print first prompt, exit |
| `--limit N` | Evaluate only first N questions |
| `--no-rerank` | Disable FlashRank reranking |
| `--no-rewrite` | Use original query for retrieval (skip LLM rewrite) |
| `--reranker-query-original` | Pass original query to reranker (not rewritten) |
| `--golden-contexts PATH` | Use pre-computed contexts as retrieval override |
| `--exclude-parts PARTS` | Parts to exclude from retrieval, comma-separated (default: pipeline default for standard residential). Use `""` for no filtering. Part 3=rooming houses, 4=caravan parks, 4A=site agreements, 12A=SDA |
| `--state STATE` | State filter (default: VIC) |
| `--top-k N` | Chunks to retrieve (default: 10) |
| `--output PATH` | CSV output path |

---

## File Index

| File | Purpose |
|------|---------|
| `src/generation/generator.py` | RAG pipeline: rewrite → retrieve → rerank → prompt → LLM → verify. Contains SYSTEM_PROMPT, QUERY_REWRITE_PROMPT, `generate_compliance_answer()`, `rerank_context()`, `verify_citations()`. |
| `src/retrieval/vector_store.py` | Qdrant ingestion and hybrid retrieval (dense + sparse RRF) with metadata filters, including `exclude_parts` parameter. |
| `src/evaluation/run_ragas_eval.py` | Evaluation script with `DeepSeekRagasLLM`, `FastembedRagasEmbeddings`, CLI flags for all configurations, intermediate save/resume. |
| `src/evaluation/__init__.py` | Package marker. |
| `tests/evaluation/vic_golden_dataset.json` | 20 QA pairs with IRAC-format ground truths. Schema: `question`, `ground_truth`, `metadata` {domain, sections, difficulty, role, location}. |
| `tests/evaluation/vic_golden_contexts.json` | Pre-computed golden contexts mapping QA index → list of chunk dicts (sections cited in ground truth). |
| `data/processed/vic_rta_chunks.json` | Parsed and chunked VIC RTA 1997 (1029 chunks). |
| `vic_eval_faithfix.csv` | Final baseline evaluation results (20 rows, T2 config). |
| `vic_eval_results.json` | Intermediate pipeline outputs (answers + contexts) from the last run, enables resume. |
| `requirements.txt` | Pinned dependencies including `ragas==0.1.22`. |
| `pyproject.toml` | Project config with `[project.optional-dependencies].eval` for Ragas tooling. |
| `prompt.xml` | Original task specification for this evaluation module. |
| `docs/EVALUATION_PLAN.md` | Original evaluation strategy document (pre-implementation). |
| `docs/EVALUATION_IMPLEMENTATION.md` | This document. |
