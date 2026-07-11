"""
Ragas Evaluation Runner for Australian Tenancy RAG Pipeline (all jurisdictions).

Evaluates the compliance RAG pipeline against per-state golden datasets using
faithfulness, context_precision, and answer_relevancy metrics.

Usage:
    python src/evaluation/run_ragas_eval.py --state VIC --dry-run
    python src/evaluation/run_ragas_eval.py --state NSW --limit 3
    python src/evaluation/run_ragas_eval.py --state VIC --batch
    python src/evaluation/run_ragas_eval.py --state all --dry-run
    python src/evaluation/run_ragas_eval.py --state all

Environment variables required (see .env.example):
    DEEPSEEK_API_KEY — API key for DeepSeek (critic LLM for Ragas)
    LLM_MODEL_ID — (optional) model ID, defaults to "deepseek-chat"
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from openai import OpenAI
from ragas.llms import BaseRagasLLM
from ragas.metrics import answer_relevancy, context_precision, faithfulness

_RAGAS_METRICS = [faithfulness, context_precision, answer_relevancy]


def _score_metric_safe(metric, row: dict) -> float:
    """Score a single row with a Ragas metric using a fresh asyncio event loop."""
    async def _run():
        return await metric._ascore(row=row, callbacks=None)
    return asyncio.run(_run())


PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

load_dotenv(PROJECT_ROOT / ".env")

os.environ.setdefault("LANGCHAIN_TRACING_V2", "false")
os.environ.setdefault("LANGCHAIN_ENDPOINT", "")
os.environ.setdefault("LANGCHAIN_API_KEY", "")
os.environ.setdefault("LANGCHAIN_PROJECT", "")

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

DEEPSEEK_API_KEY = os.environ.get("DEEPSEEK_API_KEY", "")
LLM_MODEL_ID = os.environ.get("LLM_MODEL_ID", "deepseek-chat")
DEEPSEEK_BASE_URL = "https://api.deepseek.com"

STATE_ORDER = ["VIC", "NSW"]
ALL_STATES = {"VIC", "NSW", "QLD", "SA", "WA", "TAS", "ACT", "NT"}
UNSUPPORTED = ALL_STATES - set(STATE_ORDER)
EVAL_DIR = PROJECT_ROOT / "tests" / "evaluation"
REPORTS_DIR = PROJECT_ROOT / "reports"

VIC_BATCH_INDICES = [0, 4, 5, 8, 9, 10, 12, 14, 15, 16]


def _state_dataset_path(state: str) -> Path:
    return EVAL_DIR / f"{state.lower()}_golden_dataset.json"


def _state_golden_contexts_path(state: str) -> Path:
    return EVAL_DIR / f"{state.lower()}_golden_contexts.json"


def _state_output_csv(state: str) -> Path:
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    return REPORTS_DIR / f"{state.lower()}_eval_report.csv"


def _state_intermediate_path(state: str) -> Path:
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    return REPORTS_DIR / f"{state.lower()}_eval_results.json"


def _lazy_exclude_parts(state: str) -> list[str] | None:
    """Return default exclude_parts per state, lazy-loading to avoid Qdrant side effects."""
    from src.retrieval.vector_store import DEFAULT_EXCLUDE_PARTS
    return list(DEFAULT_EXCLUDE_PARTS.get(state, []))


def _lazy_exclude_chapters(state: str) -> list[str] | None:
    from src.retrieval.vector_store import DEFAULT_EXCLUDE_CHAPTERS
    return list(DEFAULT_EXCLUDE_CHAPTERS.get(state, []))


class FastembedRagasEmbeddings:
    """Ragas-compatible embeddings provider backed by fastembed (BGE-small-en)."""

    def __init__(self):
        from fastembed import TextEmbedding
        self._model = TextEmbedding(model_name="BAAI/bge-small-en-v1.5")

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        return [e.tolist() for e in self._model.embed(texts)]

    def embed_query(self, text: str) -> list[float]:
        return self.embed_texts([text])[0]

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return self.embed_texts(texts)

    async def aembed_texts(self, texts: list[str]) -> list[list[float]]:
        return self.embed_texts(texts)


@dataclass
class DeepSeekRagasLLM(BaseRagasLLM):
    """Ragas critic LLM backed by DeepSeek via openai.OpenAI."""

    api_key: str = ""
    model: str = "deepseek-chat"
    base_url: str = "https://api.deepseek.com"

    def __post_init__(self):
        if not self.api_key:
            raise ValueError("DEEPSEEK_API_KEY not set")
        self._client = OpenAI(api_key=self.api_key, base_url=self.base_url)

    def generate_text(
        self, prompt: Any, n: int = 1, temperature: float = 1e-8,
        stop: list[str] | None = None, callbacks: Any = None,
    ) -> Any:
        from langchain_core.outputs import Generation, LLMResult
        text = prompt.to_string()
        all_generations: list[Generation] = []
        for _ in range(n):
            response = self._client.chat.completions.create(
                model=self.model,
                messages=[{"role": "user", "content": text}],
                temperature=temperature, n=1, stop=stop,
            )
            for choice in response.choices:
                all_generations.append(Generation(text=choice.message.content or ""))
        return LLMResult(generations=[all_generations])

    async def agenerate_text(
        self, prompt: Any, n: int = 1, temperature: float = 1e-8,
        stop: list[str] | None = None, callbacks: Any = None,
    ) -> Any:
        return await asyncio.to_thread(
            self.generate_text, prompt, n, temperature, stop, callbacks
        )


def _run_pipeline(
    question: str, state: str, top_k: int,
    use_rewrite: bool = True, use_reranker: bool = True,
    reranker_query: str | None = None,
    contexts_override: list[dict] | None = None,
    include_parts: list[str] | None = None,
    include_chapters: list[str] | None = None,
) -> dict:
    from src.generation.generator import generate_compliance_answer

    max_retries = 5
    for attempt in range(max_retries):
        try:
            return generate_compliance_answer(
                query=question, state_filter=state, top_k_retrieve=top_k,
                use_rewrite=use_rewrite, use_reranker=use_reranker,
                reranker_query=reranker_query, contexts_override=contexts_override,
                include_parts=include_parts,
                include_chapters=include_chapters,
            )
        except Exception as e:
            if attempt == max_retries - 1:
                raise
            wait = 2 ** (attempt + 1)
            logger.warning(
                "Pipeline error — retrying in %ds (attempt %d/%d): %s",
                wait, attempt + 2, max_retries, e,
            )
            time.sleep(wait)


def run_single_question(
    question: str, state: str = "VIC", top_k: int = 10,
    use_rewrite: bool = True, use_reranker: bool = True,
    reranker_query: str | None = None,
    contexts_override: list[dict] | None = None,
    include_parts: list[str] | None = None,
    include_chapters: list[str] | None = None,
) -> dict[str, Any]:
    start = time.time()
    result = _run_pipeline(
        question, state, top_k, use_rewrite, use_reranker, reranker_query,
        contexts_override, include_parts, include_chapters,
    )
    elapsed = time.time() - start
    answer = result.get("answer", "") or ""
    chunks = result.get("retrieved_chunks", [])
    if not answer:
        logger.warning("Empty answer for question: %s", question[:80])
    contexts = [c["text"] for c in chunks] if chunks else []
    return {
        "question": question, "answer": answer, "contexts": contexts,
        "ground_truth": "", "elapsed_seconds": round(elapsed, 1),
        "num_chunks": len(chunks), "answer_length": len(answer),
    }


def _run_eval_for_state(
    state: str, args, evaluator_llm,
    batch_vic_override: bool = False,
) -> dict | None:
    """Run full eval pipeline for a single state. Returns summary dict or None on skip."""
    dataset_path = _state_dataset_path(state) if args.dataset is None else Path(args.dataset)
    output_csv = _state_output_csv(state) if args.output is None else Path(args.output)
    intermediate_path = _state_intermediate_path(state)

    exclude_parts = None
    if args.exclude_parts is not None:
        parts = [p.strip() for p in args.exclude_parts.split(",") if p.strip()]
        exclude_parts = parts if parts else []
    else:
        exclude_parts = _lazy_exclude_parts(state)

    include_parts = None
    if args.include_parts is not None:
        parts_list = [p.strip() for p in args.include_parts.split(",") if p.strip()]
        include_parts = parts_list if parts_list else []

    include_chapters = None
    if args.include_chapters is not None:
        ch_list = [p.strip() for p in args.include_chapters.split(",") if p.strip()]
        include_chapters = ch_list if ch_list else []

    exclude_chapters = _lazy_exclude_chapters(state)

    logger.info(
        "State=%s | exclude_parts=%s | include_parts=%s | exclude_chapters=%s",
        state, exclude_parts, include_parts, exclude_chapters,
    )

    if not dataset_path.exists():
        logger.warning("[%s] Dataset not found: %s — skipping", state, dataset_path)
        return None

    golden_ctxs = None
    gc_path = None
    if args.golden_contexts_path:
        gc_path = Path(args.golden_contexts_path)
        if gc_path and gc_path.exists():
            with open(gc_path) as f:
                golden_ctxs = json.load(f)
            logger.info("[%s] Loaded golden contexts for %d QAs", state, len(golden_ctxs))

    with open(dataset_path) as f:
        raw_samples = json.load(f)

    is_vic = state.upper() == "VIC"
    if batch_vic_override and is_vic:
        samples = [raw_samples[i] for i in VIC_BATCH_INDICES]
        for j, orig_idx in enumerate(VIC_BATCH_INDICES):
            samples[j]["_original_index"] = orig_idx
    else:
        samples = list(raw_samples)

    per_state_limit = args.limit if args.limit > 0 else 0
    if per_state_limit > 0:
        samples = samples[: per_state_limit]

    logger.info(
        "[%s] Loaded %d samples (from %d total)",
        state, len(samples), len(raw_samples),
    )

    if args.dry_run:
        logger.info("── [%s] DRY RUN — validating dataset ──", state)
        for i, s in enumerate(samples, 1):
            q = s["question"]
            meta = s.get("metadata", {})
            gt = s.get("ground_truth", "") or s.get("ground_truths", [None])
            logger.info("  Sample %d [%s]: %s", i, meta.get("domain", "?"), q[:100])
            logger.info("    Sections: %s | GT length: %d chars", meta.get("sections", []), len(gt))
        first = samples[0]
        logger.info("── [%s] First question ──", state)
        logger.info("QUESTION:\n%s", first["question"])
        gt_first = first.get("ground_truth", "")
        logger.info("GROUND TRUTH (first 300 chars):\n%s...", gt_first[:300])
        logger.info("METADATA: %s", json.dumps(first.get("metadata", {}), indent=2))
        logger.info("── [%s] Dry-run complete — %d samples validated.", state, len(samples))
        return None

    results = []
    logger.info("── [%s] Running RAG pipeline on %d questions ──", state, len(samples))
    from tqdm import tqdm

    for i, sample in enumerate(tqdm(samples, desc=f"[{state}] Pipeline", unit="q")):
        question = sample["question"]
        ground_truth = sample.get("ground_truth", "") or ""
        if isinstance(ground_truth, list):
            ground_truth = ground_truth[0] if ground_truth else ""
        meta = sample.get("metadata", {})

        try:
            rq = question if args.reranker_query_original else None
            ctxs_override = None
            orig_idx = sample.get("_original_index", i)
            if golden_ctxs is not None and str(orig_idx) in golden_ctxs:
                ctxs_override = golden_ctxs[str(orig_idx)]
                logger.info(
                    "  [golden contexts] idx=%d → %d chunks: %s",
                    orig_idx, len(ctxs_override),
                    ", ".join(c["section_id"] for c in ctxs_override),
                )
            row = run_single_question(
                question, state=state, top_k=args.top_k,
                use_rewrite=not args.no_rewrite,
                use_reranker=False if ctxs_override else not args.no_rerank,
                reranker_query=rq, contexts_override=ctxs_override,
                include_parts=include_parts,
                include_chapters=include_chapters,
            )
            row["ground_truth"] = ground_truth
            row["metadata"] = meta
        except Exception as e:
            logger.error("[%s] All retries exhausted for Q %d: %s", state, i + 1, question[:80])
            row = {
                "question": question, "answer": "", "contexts": [],
                "ground_truth": ground_truth, "metadata": meta,
                "elapsed_seconds": -1, "num_chunks": 0, "answer_length": 0,
                "error": str(e),
            }
        results.append(row)
        with open(intermediate_path, "w") as f:
            json.dump(results, f, indent=2, default=str)

    logger.info("[%s] Pipeline complete — %d results", state, len(results))

    valid_results = [r for r in results if r.get("answer") and r.get("contexts")]
    skipped = len(results) - len(valid_results)
    if skipped:
        logger.warning("[%s] Skipping %d samples with empty answer or contexts", state, skipped)

    if not valid_results:
        logger.error("[%s] No valid results to evaluate", state)
        return None

    for metric in _RAGAS_METRICS:
        metric.llm = evaluator_llm
    answer_relevancy.embeddings = FastembedRagasEmbeddings()

    logger.info("[%s] Scoring %d samples ──", state, len(valid_results))
    rows = []
    for i, r in enumerate(valid_results):
        question = r["question"]
        answer = r["answer"]
        contexts = r["contexts"]
        ground_truth = r.get("ground_truth", "")
        row = {
            "question": question, "answer": answer, "contexts": contexts,
            "ground_truth": ground_truth if ground_truth else "N/A",
        }
        scores = {}
        for metric in _RAGAS_METRICS:
            try:
                scores[metric.name] = _score_metric_safe(metric, row)
            except Exception as e:
                logger.warning("[%s] %s failed for sample %d: %s", state, metric.name, i + 1, e)
                scores[metric.name] = float("nan")
        logger.info(
            "[%s] Sample %d: f=%.3f  cp=%.3f  ar=%.3f",
            state, i + 1, scores["faithfulness"],
            scores["context_precision"], scores["answer_relevancy"],
        )
        rows.append({"question": question, **scores})

    import pandas as pd
    output_df = pd.DataFrame(rows)
    output_df.to_csv(output_csv, index=False)
    logger.info("[%s] Scores exported to %s", state, output_csv)

    num_total = len(raw_samples)
    num_used = len(samples)
    source_note = f"{num_used}/{num_total}" if is_vic and batch_vic_override else f"{num_used}"

    return {
        "state": state,
        "source_note": source_note,
        "n_samples": len(output_df),
        "n_skipped": skipped,
        "mean_faithfulness": output_df["faithfulness"].mean(),
        "mean_context_precision": output_df["context_precision"].mean(),
        "mean_answer_relevancy": output_df["answer_relevancy"].mean(),
    }


def _smoke_test(state: str) -> bool:
    """Run query rewrite + retrieval for a default query to confirm Qdrant works."""
    try:
        from src.generation.generator import (
            DEFAULT_QUERIES, _rewrite_query, _build_rewrite_prompt,
        )
        from src.retrieval.vector_store import hybrid_retrieve
        query = DEFAULT_QUERIES.get(state, "What is the maximum bond?")
        rewrite_prompt = _build_rewrite_prompt(state)
        rewritten = _rewrite_query(query, rewrite_prompt)
        results = hybrid_retrieve(rewritten, state_filter=state, top_k=3)
        logger.info("[%s] Smoke test — query: '%s' → %d results", state, query[:60], len(results))
        return len(results) > 0
    except Exception as e:
        logger.warning("[%s] Smoke test failed: %s", state, e)
        return False


def main():
    import argparse

    parser = argparse.ArgumentParser(
        description="Ragas evaluation runner for Australian tenancy RAG pipeline (all jurisdictions)"
    )
    parser.add_argument("--dry-run", action="store_true",
                        help="Validate datasets without LLM calls; --state all runs smoke test per state")
    parser.add_argument("--limit", type=int, default=0,
                        help="Per-state limit in batch mode (0 = all)")
    parser.add_argument("--output", type=str, default=None,
                        help="CSV output path (default: reports/{state}_eval_report.csv)")
    parser.add_argument("--dataset", type=str, default=None,
                        help="Golden dataset path (default: tests/evaluation/{state}_golden_dataset.json)")
    parser.add_argument("--state", type=str, default="VIC",
                        help="State filter: VIC|NSW (supported) or QLD|SA|WA|TAS|ACT|NT (unsupported, chunks incomplete) | all")
    parser.add_argument("--batch", action="store_true",
                        help="[VIC only] Use curated 10-QA subset; --state all applies VIC batch automatically")
    parser.add_argument("--top-k", type=int, default=10,
                        help="Chunks to retrieve (default: 10)")
    parser.add_argument("--no-rerank", action="store_true",
                        help="Disable FlashRank reranking")
    parser.add_argument("--no-rewrite", action="store_true",
                        help="Disable LLM query rewrite")
    parser.add_argument("--reranker-query-original", action="store_true",
                        help="Pass original query to the reranker")
    parser.add_argument("--golden-contexts", dest="golden_contexts_path", type=str, default=None,
                        help="Override golden contexts path (ignored in --state all)")
    parser.add_argument("--exclude-parts", type=str, default=None,
                        help="Override auto-populated exclude_parts; empty string = no filter")
    parser.add_argument("--include-parts", type=str, default=None,
                        help="Parts to include (carveback), comma-separated")
    parser.add_argument("--include-chapters", type=str, default=None,
                        help="Chapters to include (carveback), comma-separated (QLD only)")
    args = parser.parse_args()

    if args.state == "all" and not args.dry_run and not DEEPSEEK_API_KEY:
        logger.error("DEEPSEEK_API_KEY not set")
        sys.exit(1)
    if not DEEPSEEK_API_KEY and not args.dry_run:
        logger.error("DEEPSEEK_API_KEY not set. Use --dry-run to validate without LLM calls.")
        sys.exit(1)

    is_batch = args.state.lower() == "all"
    is_vic_batch = args.state.upper() == "VIC" and args.batch

    if args.state.lower() == "all" and args.batch:
        logger.info("--batch is redundant with --state all (VIC batch subset is auto-applied)")

    if is_batch:
        logger.info("══ BATCH MODE — %d states ══", len(STATE_ORDER))

        evaluator_llm = None
        if not args.dry_run:
            evaluator_llm = DeepSeekRagasLLM(
                api_key=DEEPSEEK_API_KEY, model=LLM_MODEL_ID, base_url=DEEPSEEK_BASE_URL,
            )
            logger.info("Evaluator LLM: DeepSeek (%s)", LLM_MODEL_ID)

        if args.dry_run:
            logger.info("── DRY RUN + BATCH — validating datasets + smoke tests ──")
            for state in STATE_ORDER:
                summary = _run_eval_for_state(state, args, evaluator_llm=None, batch_vic_override=True)
                _smoke_test(state)
            logger.info("══ Batch dry-run complete — %d states validated ══", len(STATE_ORDER))
            return

        summaries = []
        for idx, state in enumerate(STATE_ORDER):
            if idx > 0:
                logger.info("Pausing 3s between states (rate limit guard)...")
                time.sleep(3)
            try:
                summary = _run_eval_for_state(state, args, evaluator_llm, batch_vic_override=True)
                if summary:
                    summaries.append(summary)
            except Exception as e:
                logger.error("[%s] Batch evaluation failed: %s — continuing with remaining states", state, e)
                summaries.append({
                    "state": state, "source_note": "ERROR", "n_samples": 0, "n_skipped": 0,
                    "mean_faithfulness": float("nan"), "mean_context_precision": float("nan"),
                    "mean_answer_relevancy": float("nan"),
                })

        print("\n" + "=" * 70)
        print("CROSS-STATE SUMMARY (faithfulness / context_precision / answer_relevancy)")
        print("=" * 70)
        for s in summaries:
            domain_note = " [2 rent, 3 term, 3 repair, 2 bond]" if s["state"] == "VIC" else ""
            print(
                f"  {s['state']:4s} ({s['source_note']:>7s}):  "
                f"{s['mean_faithfulness']:.4f} / {s['mean_context_precision']:.4f} / "
                f"{s['mean_answer_relevancy']:.4f}{domain_note}"
            )
        print("=" * 70)
        return

    state = args.state.upper()
    if state not in ALL_STATES:
        logger.error("Unknown state: %s. Choose from %s", state, ", ".join(sorted(ALL_STATES)))
        sys.exit(1)
    if state in UNSUPPORTED:
        logger.warning(
            "[%s] Not in supported state list (%s) — chunks may have incomplete/truncated content. "
            "Proceeding anyway.",
            state, ", ".join(STATE_ORDER),
        )

    evaluator_llm = None
    if not args.dry_run:
        evaluator_llm = DeepSeekRagasLLM(
            api_key=DEEPSEEK_API_KEY, model=LLM_MODEL_ID, base_url=DEEPSEEK_BASE_URL,
        )
    summary = _run_eval_for_state(state, args, evaluator_llm, batch_vic_override=is_vic_batch)

    if summary and not args.dry_run:
        print("\n" + "=" * 60)
        print("RAGAS EVALUATION — AGGREGATED MEAN SCORES")
        print("=" * 60)
        metric_cols = ["faithfulness", "context_precision", "answer_relevancy"]
        for col in metric_cols:
            mean_key = f"mean_{col}"
            print(f"  {col:25s}: {summary[mean_key]:.4f}")
        print("=" * 60)
        print(f"  State                    : {summary['state']} ({summary['source_note']} samples)")
        print(f"  Samples evaluated        : {summary['n_samples']}")
        print(f"  Samples skipped (empty)  : {summary['n_skipped']}")
        print("=" * 60)


if __name__ == "__main__":
    main()
