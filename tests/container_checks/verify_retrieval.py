"""Verify real hybrid retrieval inside the Lambda container (NOT via public API).

This script runs inside the container.  It directly calls the production
retrieval path (hybrid_retrieve) and validates:
  1. QDRANT_PATH points to /tmp/qdrant_storage
  2. hybrid_retrieve() returns non-empty results
  3. Results contain expected provision labels

Does NOT output full legal text — labels and scores only.

Usage:
  docker run --rm --entrypoint python austenancy-agent:poc \\
    /var/task/tests/container_checks/verify_retrieval.py
"""

import json
import logging
import sys
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(message)s")

import src.rag.retrieval.vector_store as vs  # noqa: E402
from src.api.runtime import RUNTIME_PATH, prepare_qdrant  # noqa: E402

prepare_qdrant()

assert str(RUNTIME_PATH) == vs.QDRANT_PATH, (
    f"QDRANT_PATH mismatch: {vs.QDRANT_PATH} != {RUNTIME_PATH}"
)
print(f"QDRANT_PATH: {vs.QDRANT_PATH}")
print(f"Collection: {vs.COLLECTION_NAME}")

manifest = json.loads(Path("/var/task/assets/qdrant_index_manifest.json").read_text())
print(f"Points in collection (from manifest): {manifest['chunk_count']}")

queries = [
    ("VIC unpaid rent notice", {"state": "VIC"}),
    ("NSW rent increase notice", {"state": "NSW"}),
]

passed = 0
failed = 0

for query, filt in queries:
    results = vs.hybrid_retrieve(query_text=query, state_filter=filt, top_k=5)
    print(f"\nQuery: {query}")
    print(f"Results: {len(results)}")

    for r in results[:3]:
        label = (
            str(r.get("section_id", "?"))
            + " ["
            + str(r.get("state", "?"))
            + " "
            + str(r.get("instrument_type", "act"))
            + "]"
        )
        print(f"  {label:30s} score={r.get('score', 0):.4f}")

    if len(results) == 0:
        print(f"  FAILED: No results for {query}")
        failed += 1
    elif not any(r.get("state") == filt.get("state") for r in results):
        print(f"  FAILED: Wrong state in results for {query}")
        failed += 1
    else:
        print("  PASSED")
        passed += 1

print(f"\nRETRIEVAL CHECK: {passed} passed, {failed} failed")
if failed > 0:
    sys.exit(1)
