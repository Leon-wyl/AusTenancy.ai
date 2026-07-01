"""
Production-grade indexing and hybrid retrieval script.
Ingests VIC RTA chunks into Qdrant and supports hybrid search
(dense BGE-small-en-v1.5 + sparse BM25) with metadata hard filters.
"""

import json
import logging
from pathlib import Path

from qdrant_client import QdrantClient, models

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

# ── Constants ─────────────────────────────────────────────────────────

COLLECTION_NAME = "tenancy_acts"
QDRANT_PATH = "./qdrant_storage"
DENSE_MODEL = "BAAI/bge-small-en-v1.5"
SPARSE_MODEL = "Qdrant/bm25"
DENSE_VECTOR_NAME = "dense"
SPARSE_VECTOR_NAME = "sparse"
BATCH_SIZE = 32
PREFETCH_LIMIT = 20


# ── Ingestion ─────────────────────────────────────────────────────────


def ingest_chunks_to_qdrant(
    json_path: str,
    recreate: bool = True,
) -> int:
    """
    Load chunked JSON, embed with dense + sparse models, and upsert into Qdrant.

    Args:
        json_path: Path to vic_rta_chunks.json.
        recreate: If True, delete and recreate the collection (idempotent re-runs).

    Returns:
        Number of points upserted.
    """
    input_path = Path(json_path)
    if not input_path.exists():
        raise FileNotFoundError(f"Chunks file not found: {input_path}")

    with open(input_path, encoding="utf-8") as f:
        chunks = json.load(f)

    if not chunks:
        raise ValueError("Chunks file is empty — nothing to ingest.")

    logger.info("Loading embedding models (first run downloads ~160MB)...")
    logger.info(f"  Dense: {DENSE_MODEL}")
    logger.info(f"  Sparse: {SPARSE_MODEL}")

    from fastembed import SparseTextEmbedding, TextEmbedding

    dense_model = TextEmbedding(model_name=DENSE_MODEL)
    sparse_model = SparseTextEmbedding(model_name=SPARSE_MODEL)

    logger.info("Connecting to Qdrant (local storage: %s)...", QDRANT_PATH)
    client = QdrantClient(path=QDRANT_PATH)

    if recreate and client.collection_exists(COLLECTION_NAME):
        logger.info("Dropping existing collection '%s'...", COLLECTION_NAME)
        client.delete_collection(COLLECTION_NAME)

    if not client.collection_exists(COLLECTION_NAME):
        client.create_collection(
            collection_name=COLLECTION_NAME,
            vectors_config={
                DENSE_VECTOR_NAME: models.VectorParams(
                    size=384,
                    distance=models.Distance.COSINE,
                ),
            },
            sparse_vectors_config={
                SPARSE_VECTOR_NAME: models.SparseVectorParams(),
            },
        )
        logger.info("Collection '%s' created (dense: 384d COSINE + sparse BM25).", COLLECTION_NAME)

    texts = [c["text"] for c in chunks]
    total = len(texts)
    logger.info("Embedding %d chunks (batch_size=%d)...", total, BATCH_SIZE)

    dense_embeddings = list(dense_model.embed(texts, batch_size=BATCH_SIZE))
    sparse_embeddings = list(sparse_model.embed(texts, batch_size=BATCH_SIZE))

    logger.info("Upserting points into Qdrant...")
    points = []
    for i, chunk in enumerate(chunks):
        dense_vec = dense_embeddings[i].tolist()
        se = sparse_embeddings[i]

        payload = {k: v for k, v in chunk.items()}

        points.append(
            models.PointStruct(
                id=i,
                vector={
                    DENSE_VECTOR_NAME: dense_vec,
                    SPARSE_VECTOR_NAME: models.SparseVector(
                        indices=se.indices.tolist(),
                        values=se.values.tolist(),
                    ),
                },
                payload=payload,
            )
        )

    # Upsert in batches for large datasets
    for offset in range(0, len(points), 500):
        batch = points[offset : offset + 500]
        client.upsert(collection_name=COLLECTION_NAME, points=batch)

    logger.info("Ingestion complete: %d points upserted.", len(points))
    return len(points)


# ── Retrieval ─────────────────────────────────────────────────────────


def hybrid_retrieve(
    query_text: str,
    state_filter: dict | None = None,
    top_k: int = 5,
    exclude_parts: list[str] | None = None,
) -> list[dict]:
    """
    Hybrid search combining dense (cosine) and sparse (BM25) retrieval
    with Reciprocal Rank Fusion (RRF) and metadata hard filters.

    Args:
        query_text: Natural language query (e.g. "notice period for unpaid rent").
        state_filter: Metadata must-match filter (e.g. {"state": "VIC"}).
        top_k: Number of top-ranked chunks to return.
        exclude_parts: Optional list of Part numbers to exclude from results
            (e.g. ["3", "4", "4A", "12A"] excludes rooming houses, caravan parks,
            site agreements, and SDA dwellings).

    Returns:
        List of dicts with keys: chunk_id, text, score, section_id,
        section_title, part, state.
    """
    from fastembed import SparseTextEmbedding, TextEmbedding

    dense_model = TextEmbedding(model_name=DENSE_MODEL)
    sparse_model = SparseTextEmbedding(model_name=SPARSE_MODEL)

    client = QdrantClient(path=QDRANT_PATH)

    if not client.collection_exists(COLLECTION_NAME):
        raise RuntimeError(f"Collection '{COLLECTION_NAME}' not found. Run ingestion first.")

    dense_vec = list(dense_model.embed([query_text]))[0].tolist()
    se = list(sparse_model.embed([query_text]))[0]
    sparse_vec = models.SparseVector(
        indices=se.indices.tolist(),
        values=se.values.tolist(),
    )

    prefetch_filter = None
    must_conditions = []
    must_not_conditions = []

    if state_filter:
        for key, value in state_filter.items():
            must_conditions.append(
                models.FieldCondition(key=key, match=models.MatchValue(value=value))
            )

    if exclude_parts:
        for part in exclude_parts:
            must_not_conditions.append(
                models.FieldCondition(key="part", match=models.MatchValue(value=part))
            )

    if must_conditions or must_not_conditions:
        prefetch_filter = models.Filter(
            must=must_conditions if must_conditions else None,
            must_not=must_not_conditions if must_not_conditions else None,
        )

    results = client.query_points(
        collection_name=COLLECTION_NAME,
        prefetch=[
            models.Prefetch(
                query=dense_vec,
                using=DENSE_VECTOR_NAME,
                limit=PREFETCH_LIMIT,
                filter=prefetch_filter,
            ),
            models.Prefetch(
                query=sparse_vec,
                using=SPARSE_VECTOR_NAME,
                limit=PREFETCH_LIMIT,
                filter=prefetch_filter,
            ),
        ],
        query=models.FusionQuery(fusion=models.Fusion.RRF),
        limit=top_k,
    )

    return [
        {
            "chunk_id": p.payload.get("chunk_id"),
            "text": p.payload.get("text"),
            "score": round(p.score, 4),
            "section_id": p.payload.get("section_id"),
            "section_title": p.payload.get("section_title"),
            "part": p.payload.get("part"),
            "state": p.payload.get("state"),
            "year": p.payload.get("year"),
            "act": p.payload.get("act"),
        }
        for p in results.points
    ]


# ── Main ──────────────────────────────────────────────────────────────


if __name__ == "__main__":
    import time

    # ── Step 1: Ingestion ──
    logger.info("=" * 60)
    logger.info("PHASE 2: Vector DB Ingestion & Hybrid Retrieval")
    logger.info("=" * 60)

    json_file = "data/processed/vic_rta_chunks.json"

    t0 = time.perf_counter()
    count = ingest_chunks_to_qdrant(json_file)
    elapsed = time.perf_counter() - t0
    logger.info("Ingested %d points in %.1fs", count, elapsed)

    # ── Step 2: Test Query ──
    logger.info("=" * 60)
    logger.info("TEST QUERY: Hybrid Retrieval")
    logger.info("=" * 60)

    test_query = "How many days notice for unpaid rent in VIC?"
    logger.info("Query: %s", test_query)

    results = hybrid_retrieve(
        query_text=test_query,
        state_filter={"state": "VIC"},
        top_k=3,
    )

    logger.info("Top %d results:", len(results))
    for i, r in enumerate(results, 1):
        logger.info(
            "  #%d [score=%.4f] Section %s — %s", i, r["score"], r["section_id"], r["section_title"]
        )
        preview = r["text"].replace("\n", " ")[:150]
        logger.info("    Preview: %s...", preview)
