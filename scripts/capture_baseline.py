"""Phase 1 & 2 — Multi-stage baseline capture for Bedrock root-cause analysis.

Monkeypatches the pipeline to record rewritten queries, per-stage retrieval
candidates, selected contexts, and Bedrock Converse parameters without
modifying production code. Runs the VIC 10-day eviction case 3× with
current configuration and 3× with BEDROCK_TEMPERATURE=0.

Usage:
    python scripts/capture_baseline.py          # 6 runs, writes reports/baseline_capture.json
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

load_dotenv()

VIC_10D_QUERY = (
    "My landlord wants to evict me because I am 10 days behind on rent "
    "at my apartment in Melbourne VIC"
)


def _section_labels(chunks: list[dict]) -> list[str]:
    labels: list[str] = []
    for c in chunks:
        sid = c.get("section_id", "")
        state = c.get("state", "")
        inst = c.get("instrument_type", "")
        if inst == "regulation":
            labels.append(f"[{state} REG {sid}]")
        elif state and sid:
            labels.append(f"[{state} RTA {sid}]")
        else:
            labels.append(sid or "?")
    return labels


def _build_vic_filter():
    """Build the same State filter used by the production pipeline."""
    from qdrant_client import models

    from src.rag.retrieval.vector_store import DEFAULT_EXCLUDE_PARTS

    state = "VIC"
    must_conditions = [models.FieldCondition(key="state", match=models.MatchValue(value=state))]
    excluded: list[str] = list(DEFAULT_EXCLUDE_PARTS.get(state, []))
    must_not_conditions = []
    for part in excluded:
        must_not_conditions.append(
            models.FieldCondition(key="part", match=models.MatchValue(value=part))
        )

    if must_not_conditions:
        should_conditions = [
            models.Filter(must_not=must_not_conditions),
            models.Filter(
                must=[
                    models.FieldCondition(
                        key="instrument_type",
                        match=models.MatchValue(value="regulation"),
                    )
                ]
            ),
        ]
        return models.Filter(
            must=must_conditions,
            should=should_conditions,
        )
    return models.Filter(must=must_conditions)


def _run_with_capture(env_overrides: dict | None = None) -> dict:
    """Run generate_compliance_answer() with full pipeline tracing."""
    from src.rag.generation import generator
    from src.rag.generation.llm_provider import BedrockLLMProvider

    if env_overrides:
        for k, v in env_overrides.items():
            os.environ[k] = v
    # Ensure Bedrock is selected for this diagnostic
    os.environ["LLM_PROVIDER"] = "bedrock"

    trace: dict = {
        "run_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "query": VIC_10D_QUERY,
        "env": {
            "LLM_PROVIDER": os.environ.get("LLM_PROVIDER", ""),
            "BEDROCK_TEMPERATURE": os.environ.get("BEDROCK_TEMPERATURE", ""),
            "BEDROCK_MODEL_ID": os.environ.get("BEDROCK_MODEL_ID", ""),
        },
    }

    _rewrite_calls: list[dict] = []
    _converse_calls: list[dict] = []
    _per_query_dense: list[list[str]] = []
    _per_query_sparse: list[list[str]] = []
    _per_query_fused: list[list[str]] = []

    _orig_rewrite = generator._rewrite_queries
    _orig_generate = BedrockLLMProvider.generate
    _orig_hybrid_retrieve = None

    def _capture_rewrite(query, state_filter=None):
        result = _orig_rewrite(query, state_filter)
        _rewrite_calls.append(
            {"original_query": query, "state_filter": state_filter, "rewritten": result}
        )
        return result

    def _capture_generate(self, messages, *, temperature=None, max_tokens=None):
        resolved_temp = temperature if temperature is not None else self._default_temperature
        _converse_calls.append(
            {
                "operation": "converse",
                "temperature": resolved_temp,
                "model_id": self._model_id,
            }
        )
        return _orig_generate(self, messages, temperature=temperature, max_tokens=max_tokens)

    # Patch hybrid_retrieve to also record per-stage candidates
    try:
        from src.rag.retrieval import vector_store as vs

        _orig_hybrid_retrieve = vs.hybrid_retrieve

        def _capture_hybrid_retrieve(
            query_text,
            state_filter=None,
            top_k=5,
            include_parts=None,
            include_chapters=None,
        ):
            result = _orig_hybrid_retrieve(
                query_text,
                state_filter=state_filter,
                top_k=top_k,
                include_parts=include_parts,
                include_chapters=include_chapters,
            )
            # Replay diagnostics: dense-only, sparse-only, fused-full
            try:
                from fastembed import SparseTextEmbedding, TextEmbedding
                from qdrant_client import QdrantClient
                from qdrant_client import models as qm

                dm = TextEmbedding(model_name=vs.DENSE_MODEL)
                sm = SparseTextEmbedding(model_name=vs.SPARSE_MODEL)
                client = QdrantClient(path=vs.QDRANT_PATH)
                flt = state_filter  # reuse same filter for diagnostic queries

                # dense-only
                dense_vec = list(dm.embed([query_text]))[0].tolist()
                dense_res = client.query_points(
                    collection_name=vs.COLLECTION_NAME,
                    query=dense_vec,
                    using=vs.DENSE_VECTOR_NAME,
                    limit=30,
                    query_filter=flt,
                )
                _per_query_dense.append(
                    [p.payload.get("section_id", "?") for p in dense_res.points]
                )
                # sparse-only
                se = list(sm.embed([query_text]))[0]
                sparse_vec_data = qm.SparseVector(
                    indices=se.indices.tolist(), values=se.values.tolist()
                )
                sparse_res = client.query_points(
                    collection_name=vs.COLLECTION_NAME,
                    query=sparse_vec_data,
                    using=vs.SPARSE_VECTOR_NAME,
                    limit=30,
                    query_filter=flt,
                )
                _per_query_sparse.append(
                    [p.payload.get("section_id", "?") for p in sparse_res.points]
                )
                # fused (full RRF, large limit to see before final cutoff)
                fused_res = client.query_points(
                    collection_name=vs.COLLECTION_NAME,
                    prefetch=[
                        qm.Prefetch(
                            query=dense_vec,
                            using=vs.DENSE_VECTOR_NAME,
                            limit=30,
                            filter=flt,
                        ),
                        qm.Prefetch(
                            query=sparse_vec_data,
                            using=vs.SPARSE_VECTOR_NAME,
                            limit=30,
                            filter=flt,
                        ),
                    ],
                    query=qm.FusionQuery(fusion=qm.Fusion.RRF),
                    limit=30,
                )
                _per_query_fused.append(
                    [p.payload.get("section_id", "?") for p in fused_res.points]
                )
            except Exception:
                _per_query_dense.append([])
                _per_query_sparse.append([])
                _per_query_fused.append([])
            return result

        vs.hybrid_retrieve = _capture_hybrid_retrieve
    except Exception:
        pass

    generator._rewrite_queries = _capture_rewrite
    BedrockLLMProvider.generate = _capture_generate

    try:
        from src.rag.generation.generator import generate_compliance_answer

        t0 = time.perf_counter()
        result = generate_compliance_answer(
            query=VIC_10D_QUERY, state_filter="VIC", top_k_retrieve=10
        )
        trace["latency_ms"] = (time.perf_counter() - t0) * 1000

        trace["answer"] = result["answer"]
        trace["answer_length"] = len(result["answer"])
        trace["citation_check"] = {
            "verified": result["citation_check"].get("verified", []),
            "unverified": result["citation_check"].get("unverified", []),
        }
        trace["selected_contexts"] = _section_labels(result["retrieved_chunks"])
        trace["selected_context_detail"] = [
            {
                "section_id": c.get("section_id"),
                "state": c.get("state"),
                "instrument_type": c.get("instrument_type"),
                "score": c.get("score"),
            }
            for c in result["retrieved_chunks"]
        ]
        trace["rewritten_queries"] = _rewrite_calls[-1]["rewritten"] if _rewrite_calls else []
        trace["converse_calls"] = _converse_calls
        trace["per_query_dense_candidates"] = _per_query_dense
        trace["per_query_sparse_candidates"] = _per_query_sparse
        trace["per_query_fused_candidates"] = _per_query_fused
    finally:
        generator._rewrite_queries = _orig_rewrite
        BedrockLLMProvider.generate = _orig_generate
        if _orig_hybrid_retrieve is not None:
            vs.hybrid_retrieve = _orig_hybrid_retrieve

    return trace


def main() -> None:
    out_dir = Path("reports")
    out_dir.mkdir(parents=True, exist_ok=True)

    results: dict = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "case": "vic-10d-eviction",
        "query": VIC_10D_QUERY,
        "phases": {},
    }

    # ── Phase 1: 3 baseline runs (current config, model-default temperature) ──
    print("[Phase 1] 3 baseline runs (current config, model-default temperature)")
    baseline_runs = []
    for i in range(3):
        print(f"  Run {i + 1}/3 ...")
        trace = _run_with_capture()
        baseline_runs.append(trace)
        verified = trace.get("citation_check", {}).get("verified", [])
        s91zm_hit = any("91ZM" in str(v) for v in verified)
        print(f"    Converse calls: {len(trace.get('converse_calls', []))}")
        print(f"    s91ZM in verified: {'YES' if s91zm_hit else 'NO'}")
        print(
            f"    Selected contexts: "
            f"{[c.get('section_id') for c in trace.get('selected_context_detail', [])]}"
        )
    results["phases"]["baseline"] = baseline_runs

    # ── Phase 2: 3 runs with BEDROCK_TEMPERATURE=0 ────────────────────
    os.environ["BEDROCK_TEMPERATURE"] = "0"
    print("\n[Phase 2] 3 runs with BEDROCK_TEMPERATURE=0")
    temp_zero_runs = []
    for i in range(3):
        print(f"  Run {i + 1}/3 ...")
        trace = _run_with_capture({"BEDROCK_TEMPERATURE": "0"})
        temp_zero_runs.append(trace)
        verified = trace.get("citation_check", {}).get("verified", [])
        s91zm_hit = any("91ZM" in str(v) for v in verified)
        print(f"    s91ZM in verified citations: {'YES' if s91zm_hit else 'NO'}")
        print(
            f"    Selected contexts: "
            f"{[c.get('section_id') for c in trace.get('selected_context_detail', [])]}"
        )
    results["phases"]["temperature_zero"] = temp_zero_runs

    out_path = out_dir / "baseline_capture.json"
    out_path.write_text(json.dumps(results, indent=2, ensure_ascii=False, default=str))
    print(f"\nSaved: {out_path}")


if __name__ == "__main__":
    main()
