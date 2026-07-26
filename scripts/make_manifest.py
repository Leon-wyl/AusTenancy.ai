"""Generate a deterministic qdrant_index_manifest.json from local Qdrant storage.

Reproducibility guarantees:
- Files sorted by relative path before checksumming.
- Excludes .lock, temporary, and runtime metadata files.
- Checksum includes both relative path and file content.
- chunk_count read from Qdrant collection.
- dense_dimensions read from collection schema.
- Model names from project configuration (vector_store.py constants).
- built_at recorded but NOT included in index_sha256 (time-independent).
"""

import hashlib
import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from qdrant_client import QdrantClient

from src.rag.retrieval.vector_store import (
    COLLECTION_NAME,
    DENSE_MODEL,
    DENSE_VECTOR_NAME,
    QDRANT_PATH,
    SPARSE_MODEL,
)

MANIFEST_VERSION = "1.0"

EXCLUDE_NAMES: frozenset[str] = frozenset({"meta.json", "MANIFEST"})
EXCLUDE_SUFFIXES: frozenset[str] = frozenset({".lock", ".tmp", ".log", ".pid"})


def _compute_index_sha256(storage_dir: Path) -> str:
    """Deterministic SHA-256 over sorted relative paths + file contents.

    Excludes .lock, .tmp, .log, meta.json — Qdrant runtime files that
    change between access sessions. Only the actual embedding/collection
    data contributes to the checksum.
    """
    files: list[tuple[str, Path]] = []
    for root, _dirs, fnames in sorted(os.walk(storage_dir)):
        for fname in sorted(fnames):
            fpath = Path(root) / fname
            if fname in EXCLUDE_NAMES:
                continue
            if fpath.suffix in EXCLUDE_SUFFIXES:
                continue
            rel = fpath.relative_to(storage_dir)
            files.append((str(rel), fpath))

    hasher = hashlib.sha256()
    for rel, fpath in files:
        hasher.update(rel.encode())
        with open(fpath, "rb") as f:
            while chunk := f.read(65536):
                hasher.update(chunk)
    return hasher.hexdigest()


def main() -> None:
    storage_dir = Path(QDRANT_PATH).resolve()
    if not storage_dir.exists():
        print(f"ERROR: Qdrant storage not found at {storage_dir}", file=sys.stderr)
        sys.exit(1)

    client = QdrantClient(path=str(storage_dir))
    if not client.collection_exists(COLLECTION_NAME):
        print(f"ERROR: Collection '{COLLECTION_NAME}' not found", file=sys.stderr)
        sys.exit(1)

    count = client.count(COLLECTION_NAME, exact=True).count
    info = client.get_collection(COLLECTION_NAME)
    dense_dims = info.config.params.vectors[DENSE_VECTOR_NAME].size

    index_sha256 = _compute_index_sha256(storage_dir)

    manifest = {
        "manifest_version": MANIFEST_VERSION,
        "corpus_version": "1.0.0",
        "built_at": datetime.now(UTC).isoformat(),
        "collection_name": COLLECTION_NAME,
        "chunk_count": count,
        "dense_model": DENSE_MODEL,
        "dense_dimensions": dense_dims,
        "sparse_model": SPARSE_MODEL,
        "index_sha256": index_sha256,
    }

    out = Path("assets/qdrant_index_manifest.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2, sort_keys=True)
    print(f"Manifest written to {out}")
    print(json.dumps(manifest, indent=2))


if __name__ == "__main__":
    main()
