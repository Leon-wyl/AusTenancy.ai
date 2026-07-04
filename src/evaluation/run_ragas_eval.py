"""
Ragas Evaluation Runner for VIC Tenancy RAG Pipeline.

Evaluates the compliance RAG pipeline against a golden dataset using
faithfulness, context_precision, and answer_relevancy metrics.

Usage:
    python src/evaluation/run_ragas_eval.py --dry-run
    python src/evaluation/run_ragas_eval.py --limit 3
    python src/evaluation/run_ragas_eval.py --output vic_eval_report.csv

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

import tenacity
from dotenv import load_dotenv
from openai import OpenAI
from ragas.llms import BaseRagasLLM
from ragas.metrics import answer_relevancy, context_precision, faithfulness

# ── Python 3.14 compat ─────────────────────────────────────────────────
# Ragas metric.score() internally uses loop.run_until_complete() which
# deadlocks with Python 3.14 + nest_asyncio. We bypass score() entirely
# and call each metric's async _ascore() directly via asyncio.run(),
# creating a clean, unpatched event loop each time.
_RAGAS_METRICS = [faithfulness, context_precision, answer_relevancy]


def _score_metric_safe(metric, row: dict) -> float:
    """Score a single row with a Ragas metric using a fresh asyncio event loop."""
    async def _run():
        return await metric._ascore(row=row, callbacks=None)
    return asyncio.run(_run())

# ── Path setup ────────────────────────────────────────────────────────

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

load_dotenv(PROJECT_ROOT / ".env")

os.environ.setdefault("LANGCHAIN_TRACING_V2", "false")
os.environ.setdefault("LANGCHAIN_ENDPOINT", "")
os.environ.setdefault("LANGCHAIN_API_KEY", "")
os.environ.setdefault("LANGCHAIN_PROJECT", "")

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

# ── Constants ─────────────────────────────────────────────────────────

DEFAULT_DATASET_PATH = PROJECT_ROOT / "tests" / "evaluation" / "vic_golden_dataset.json"
DEFAULT_OUTPUT_CSV = PROJECT_ROOT / "vic_eval_report.csv"
INTERMEDIATE_SAVE_PATH = PROJECT_ROOT / "vic_eval_results.json"

DEEPSEEK_API_KEY = os.environ.get("DEEPSEEK_API_KEY", "")
LLM_MODEL_ID = os.environ.get("LLM_MODEL_ID", "deepseek-chat")
DEEPSEEK_BASE_URL = "https://api.deepseek.com"

# ── Custom Ragas Embeddings (wraps fastembed directly) ────────────────


class FastembedRagasEmbeddings:
    """Ragas-compatible embeddings provider backed by fastembed (BGE-small-en).

    Uses the same local embeddings as the project's retrieval pipeline.
    No external API keys needed.
    """

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

# ── Custom Ragas Evaluator LLM (wraps openai.OpenAI directly) ─────────


@dataclass
class DeepSeekRagasLLM(BaseRagasLLM):
    """Ragas critic LLM backed by DeepSeek via openai.OpenAI.

    Uses the same openai.OpenAI client as the project's RAG pipeline
    (src/generation/generator.py). No langchain_openai dependency.
    """

    api_key: str = ""
    model: str = "deepseek-chat"
    base_url: str = "https://api.deepseek.com"

    def __post_init__(self):
        if not self.api_key:
            raise ValueError("DEEPSEEK_API_KEY not set")
        self._client = OpenAI(api_key=self.api_key, base_url=self.base_url)

    def generate_text(
        self,
        prompt: Any,
        n: int = 1,
        temperature: float = 1e-8,
        stop: list[str] | None = None,
        callbacks: Any = None,
    ) -> Any:
        from langchain_core.outputs import Generation, LLMResult

        text = prompt.to_string()
        all_generations: list[Generation] = []

        for _ in range(n):
            response = self._client.chat.completions.create(
                model=self.model,
                messages=[{"role": "user", "content": text}],
                temperature=temperature,
                n=1,  # DeepSeek only supports n=1
                stop=stop,
            )
            for choice in response.choices:
                all_generations.append(
                    Generation(text=choice.message.content or "")
                )

        # LLMResult.generations is List[List[Generation]]
        return LLMResult(generations=[all_generations])

    async def agenerate_text(
        self,
        prompt: Any,
        n: int = 1,
        temperature: float = 1e-8,
        stop: list[str] | None = None,
        callbacks: Any = None,
    ) -> Any:
        return await asyncio.to_thread(
            self.generate_text, prompt, n, temperature, stop, callbacks
        )


# ── Pipeline runner with rate-limit backoff ────────────────────────────


@tenacity.retry(
    wait=tenacity.wait_exponential(min=2, max=120, multiplier=2),
    stop=tenacity.stop_after_attempt(5),
    retry=tenacity.retry_if_exception_type(Exception),
    before_sleep=lambda retry_state: logger.warning(
        "Rate limit hit — retrying in %ds (attempt %d)",
        retry_state.next_action.sleep,
        retry_state.attempt_number + 1,
    ),
)
def _run_pipeline(
    question: str,
    state: str,
    top_k: int,
    use_rewrite: bool = True,
    use_reranker: bool = True,
    reranker_query: str | None = None,
    contexts_override: list[dict] | None = None,
    exclude_parts: list[str] | None = None,
) -> dict:
    """Run the VIC RAG pipeline with retry on rate limits."""
    from src.generation.generator import generate_compliance_answer

    return generate_compliance_answer(
        query=question,
        state_filter=state,
        top_k_retrieve=top_k,
        use_rewrite=use_rewrite,
        use_reranker=use_reranker,
        reranker_query=reranker_query,
        contexts_override=contexts_override,
        exclude_parts=exclude_parts,
    )


def run_single_question(
    question: str,
    state: str = "VIC",
    top_k: int = 10,
    use_rewrite: bool = True,
    use_reranker: bool = True,
    reranker_query: str | None = None,
    contexts_override: list[dict] | None = None,
    exclude_parts: list[str] | None = None,
) -> dict[str, Any]:
    """Run pipeline and extract answer + contexts."""
    start = time.time()
    result = _run_pipeline(question, state, top_k, use_rewrite, use_reranker, reranker_query, contexts_override, exclude_parts)
    elapsed = time.time() - start

    answer = result.get("answer", "") or ""
    chunks = result.get("retrieved_chunks", [])

    if not answer:
        logger.warning("Empty answer for question: %s", question[:80])

    contexts = [c["text"] for c in chunks] if chunks else []

    return {
        "question": question,
        "answer": answer,
        "contexts": contexts,
        "ground_truth": "",  # populated by caller
        "elapsed_seconds": round(elapsed, 1),
        "num_chunks": len(chunks),
        "answer_length": len(answer),
    }


# ── Main evaluation driver ─────────────────────────────────────────────


def main():
    import argparse

    parser = argparse.ArgumentParser(
        description="Ragas evaluation runner for VIC tenancy RAG pipeline"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate dataset and print first prompt without LLM calls",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        help="Limit evaluation to first N questions (0 = all)",
    )
    parser.add_argument(
        "--output",
        type=str,
        default=str(DEFAULT_OUTPUT_CSV),
        help=f"CSV output path (default: {DEFAULT_OUTPUT_CSV})",
    )
    parser.add_argument(
        "--dataset",
        type=str,
        default=str(DEFAULT_DATASET_PATH),
        help=f"Golden dataset path (default: {DEFAULT_DATASET_PATH})",
    )
    parser.add_argument(
        "--state", type=str, default="VIC", help="State filter (default: VIC)"
    )
    parser.add_argument(
        "--top-k", type=int, default=10, help="Chunks to retrieve (default: 10)"
    )
    parser.add_argument(
        "--no-rerank",
        action="store_true",
        help="Disable FlashRank reranking (use raw hybrid-search ordering)",
    )
    parser.add_argument(
        "--no-rewrite",
        action="store_true",
        help="Disable LLM query rewrite (use original query for retrieval)",
    )
    parser.add_argument(
        "--reranker-query-original",
        action="store_true",
        help="Pass original query (not rewritten) to the reranker",
    )
    parser.add_argument(
        "--golden-contexts",
        type=str,
        default=None,
        help="Path to vic_golden_contexts.json for golden-context diagnostic",
    )
    parser.add_argument(
        "--exclude-parts",
        type=str,
        default=None,
        help=(
            "Parts to exclude from retrieval, comma-separated "
            "(default: pipeline default for standard residential). "
            'Use empty string "" for no filtering. '
            "Part 3=rooming houses, 4=caravan parks, 4A=site agreements, 12A=SDA"
        ),
    )
    args = parser.parse_args()

    # ── Parse exclude_parts ─────────────────────────────────────────────
    exclude_parts: list[str] | None = None
    if args.exclude_parts is not None:
        parts = [p.strip() for p in args.exclude_parts.split(",") if p.strip()]
        exclude_parts = parts if parts else []  # empty string → no filter

    # ── Validate environment ──────────────────────────────────────────
    if not DEEPSEEK_API_KEY and not args.dry_run:
        logger.error(
            "DEEPSEEK_API_KEY not set. Set it in .env or export it, "
            "or use --dry-run to validate without LLM calls."
        )
        sys.exit(1)

    # ── Load golden contexts if specified ──────────────────────────────
    golden_ctxs = None
    if args.golden_contexts:
        gc_path = Path(args.golden_contexts)
        if not gc_path.exists():
            logger.error("Golden contexts file not found: %s", gc_path)
            sys.exit(1)
        with open(gc_path) as f:
            golden_ctxs = json.load(f)
        logger.info("Loaded golden contexts for %d QAs", len(golden_ctxs))

    # ── Load golden dataset ───────────────────────────────────────────
    dataset_path = Path(args.dataset)
    if not dataset_path.exists():
        logger.error("Dataset not found: %s", dataset_path)
        sys.exit(1)

    with open(dataset_path) as f:
        raw_samples = json.load(f)

    if args.limit > 0:
        raw_samples = raw_samples[: args.limit]

    logger.info("Loaded %d samples from %s", len(raw_samples), dataset_path)
    logger.info("State filter: %s | Top-K: %d", args.state, args.top_k)

    # ── Dry-run: validate and exit ────────────────────────────────────
    if args.dry_run:
        logger.info("── DRY RUN — no LLM calls ──")
        for i, s in enumerate(raw_samples, 1):
            q = s["question"]
            gt = s.get("ground_truth", "") or s.get("ground_truths", [None])
            meta = s.get("metadata", {})
            logger.info(
                "Sample %d [%s]: %s",
                i,
                meta.get("domain", "?"),
                q[:120],
            )
            logger.info("  Ground truth length: %d chars", len(gt))
            logger.info("  Sections: %s", meta.get("sections", []))

        # Print first question's prompt for manual inspection
        first = raw_samples[0]
        logger.info("── First question full text ──")
        logger.info("QUESTION:\n%s", first["question"])
        logger.info("GROUND TRUTH (first 300 chars):\n%s...", first.get("ground_truth", "")[:300])
        logger.info("METADATA: %s", json.dumps(first["metadata"], indent=2))
        logger.info("Dry-run complete — %d samples validated.", len(raw_samples))
        return

    # ── Run pipeline on all questions ─────────────────────────────────
    results = []
    logger.info("── Running RAG pipeline on %d questions ──", len(raw_samples))

    from tqdm import tqdm

    for i, sample in enumerate(tqdm(raw_samples, desc="Pipeline", unit="q")):
        question = sample["question"]
        ground_truth = sample.get("ground_truth", "") or ""

        if isinstance(ground_truth, list):
            ground_truth = ground_truth[0] if ground_truth else ""
        meta = sample.get("metadata", {})

        try:
            rq = question if args.reranker_query_original else None
            ctxs_override = None
            if golden_ctxs is not None and str(i) in golden_ctxs:
                ctxs_override = golden_ctxs[str(i)]
                logger.info(
                    "  [golden contexts] %d chunks: %s",
                    len(ctxs_override),
                    ", ".join(c["section_id"] for c in ctxs_override),
                )
            row = run_single_question(
                question,
                state=args.state,
                top_k=args.top_k,
                use_rewrite=not args.no_rewrite,
                use_reranker=False if ctxs_override else not args.no_rerank,
                reranker_query=rq,
                contexts_override=ctxs_override,
                exclude_parts=exclude_parts,
            )
            row["ground_truth"] = ground_truth
            row["metadata"] = meta
        except tenacity.RetryError as e:
            logger.error("All retries exhausted for question %d: %s", i + 1, question[:80])
            row = {
                "question": question,
                "answer": "",
                "contexts": [],
                "ground_truth": ground_truth,
                "metadata": meta,
                "elapsed_seconds": -1,
                "num_chunks": 0,
                "answer_length": 0,
                "error": str(e),
            }

        results.append(row)

        # Intermediate save after each question
        with open(INTERMEDIATE_SAVE_PATH, "w") as f:
            json.dump(results, f, indent=2, default=str)

    logger.info("Pipeline complete — %d results", len(results))

    # ── Build Ragas Dataset ───────────────────────────────────────────
    valid_results = [
        r for r in results
        if r.get("answer") and r.get("contexts")
    ]
    skipped = len(results) - len(valid_results)
    if skipped:
        logger.warning("Skipping %d samples with empty answer or contexts", skipped)

    if not valid_results:
        logger.error("No valid results to evaluate")
        sys.exit(1)

    # ── Configure evaluator LLM ───────────────────────────────────────
    evaluator_llm = DeepSeekRagasLLM(
        api_key=DEEPSEEK_API_KEY,
        model=LLM_MODEL_ID,
        base_url=DEEPSEEK_BASE_URL,
    )
    logger.info("Evaluator LLM: DeepSeek (%s)", LLM_MODEL_ID)

    # ── Initialize metrics with our LLM and embeddings ──────────────────
    for metric in _RAGAS_METRICS:
        metric.llm = evaluator_llm
    answer_relevancy.embeddings = FastembedRagasEmbeddings()
    logger.info("Metrics initialised: faithfulness, context_precision, answer_relevancy")

    # ── Score each sample directly (bypasses Ragas executor/async) ─────
    logger.info("── Scoring %d samples ──", len(valid_results))

    rows = []
    for i, r in enumerate(valid_results):
        question = r["question"]
        answer = r["answer"]
        contexts = r["contexts"]
        ground_truth = r.get("ground_truth", "")

        # Prepare row dict with standard Ragas 0.1.x keys
        row = {
            "question": question,
            "answer": answer,
            "contexts": contexts,
            "ground_truth": ground_truth if ground_truth else "N/A",
        }

        scores = {}
        for metric in _RAGAS_METRICS:
            try:
                scores[metric.name] = _score_metric_safe(metric, row)
            except Exception as e:
                logger.warning(
                    "%s failed for sample %d: %s", metric.name, i + 1, e
                )
                scores[metric.name] = float("nan")

        logger.info(
            "Sample %d: faithfulness=%.3f  context_precision=%.3f  answer_relevancy=%.3f",
            i + 1, scores["faithfulness"], scores["context_precision"], scores["answer_relevancy"],
        )

        rows.append({
            "question": question,
            **scores,
        })

    # ── Save results ──────────────────────────────────────────────────
    output_path = Path(args.output)
    import pandas as pd
    output_df = pd.DataFrame(rows)
    output_df.to_csv(output_path, index=False)
    logger.info("Raw scores exported to %s", output_path)

    # ── Print aggregated means ────────────────────────────────────────
    print("\n" + "=" * 60)
    print("RAGAS EVALUATION — AGGREGATED MEAN SCORES")
    print("=" * 60)
    metric_cols = ["faithfulness", "context_precision", "answer_relevancy"]
    for col in metric_cols:
        mean_val = output_df[col].mean()
        print(f"  {col:25s}: {mean_val:.4f}")
    print("=" * 60)
    print(f"  Samples evaluated       : {len(output_df)}")
    print(f"  Samples skipped (empty) : {skipped}")
    print(f"  Report saved to         : {output_path.resolve()}")
    print("=" * 60)


if __name__ == "__main__":
    main()
