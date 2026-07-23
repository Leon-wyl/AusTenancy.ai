"""Runtime initialization for the AusTenancy.ai Lambda container.

Handles:
- Qdrant seed copy on cold init (atomic), reuse on warm invocations.
- Manifest validation at startup.
- Compiled LangGraph graph (cached, single compilation).
- AgentState construction from AgentRequest.

QDRANT_PATH override: vector_store.py uses a module-level constant.
This module overrides it before any graph imports via os.environ so
hybrid_retrieve() sees /tmp/qdrant_storage in the Lambda container.

All paths are configurable via environment variables so the same code
works in local ASGI mode (pointing at repo qdrant_storage/) and in
the Lambda container (pointing at /var/task/assets/ → /tmp/).
"""

import json
import logging
import os
import shutil
from functools import lru_cache
from pathlib import Path

logger = logging.getLogger(__name__)

# Import constants from vector_store for manifest validation (NO path override here).
from src.rag.retrieval.vector_store import (  # noqa: E402, I001
    COLLECTION_NAME as _VS_COLLECTION_NAME,
    DENSE_VECTOR_NAME as _VS_DENSE_VECTOR_NAME,
)  # noqa: E402

# ── Configurable paths ─────────────────────────────────────────────────
# In Lambda:  QDRANT_SEED_PATH=/var/task/assets/qdrant_storage
#             QDRANT_PATH=/tmp/qdrant_storage
#             QDRANT_MANIFEST_PATH=/var/task/assets/qdrant_index_manifest.json
#
# In local ASGI: set all three to repo-relative paths (see run_asgi_smoke.py).
SEED_PATH = Path(os.getenv("QDRANT_SEED_PATH", "/var/task/assets/qdrant_storage"))
RUNTIME_PATH = Path(os.getenv("QDRANT_PATH", "/tmp/qdrant_storage"))
MANIFEST_PATH = Path(
    os.getenv("QDRANT_MANIFEST_PATH", "/var/task/assets/qdrant_index_manifest.json")
)

_qdrant_path_set = False


def _ensure_qdrant_path() -> None:
    """Override vector_store.QDRANT_PATH to RUNTIME_PATH.

    Called lazily by prepare_qdrant() / get_compiled_graph() — not at
    module import, so test suites that import handler.py but don't call
    prepare_qdrant() aren't affected.
    """
    global _qdrant_path_set
    if _qdrant_path_set:
        return
    import src.rag.retrieval.vector_store as vs_mod

    vs_mod.QDRANT_PATH = str(RUNTIME_PATH)
    os.environ["QDRANT_PATH"] = str(RUNTIME_PATH)
    _qdrant_path_set = True


# ── Manifest validation ────────────────────────────────────────────────


def _compute_index_sha256(storage_dir: Path) -> str:
    """Deterministic SHA-256 over sorted relative paths + file contents.

    Must match the logic in scripts/make_manifest.py exactly.
    """
    import hashlib

    exclude_names = frozenset({"meta.json", "MANIFEST"})
    exclude_suffixes = frozenset({".lock", ".tmp", ".log", ".pid"})

    files: list[tuple[str, Path]] = []
    for root, _dirs, fnames in sorted(os.walk(storage_dir)):
        for fname in sorted(fnames):
            fpath = Path(root) / fname
            if fname in exclude_names:
                continue
            if fpath.suffix in exclude_suffixes:
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


def validate_manifest(hard: bool = False) -> bool:
    """Verify qdrant_index_manifest.json exists and contains required fields.

    Args:
        hard: If True, also verify checksum, chunk_count, and dense_dimensions
              against the live seed directory.  Use for PoC gate only — not
              every cold init.

    Returns True if valid. Raises FileNotFoundError or ValueError on failure.
    """
    manifest_file = Path(MANIFEST_PATH)
    if not manifest_file.exists():
        raise FileNotFoundError(
            f"Qdrant index manifest not found at {MANIFEST_PATH}. "
            "Verify the Docker image includes assets/qdrant_index_manifest.json."
        )

    with open(manifest_file, encoding="utf-8") as f:
        m = json.load(f)

    # Version
    if m.get("manifest_version") != "1.0":
        raise ValueError(
            f"Unsupported manifest_version: {m.get('manifest_version')!r} (expected '1.0')"
        )

    # Collection name must match
    collection = m.get("collection_name")
    if collection != _VS_COLLECTION_NAME:
        raise ValueError(
            f"Manifest collection {collection!r} != actual {_VS_COLLECTION_NAME!r}"
        )

    if not hard:
        logger.info(
            "Manifest validated (light): collection=%s, %d chunks",
            collection,
            m.get("chunk_count", 0),
        )
        return True

    # ── Hard validation (PoC gate) ─────────────────────────────────
    seed = Path(SEED_PATH)
    if not seed.exists():
        raise FileNotFoundError(f"Qdrant seed not found at {SEED_PATH}")

    actual_sha = _compute_index_sha256(seed)
    declared_sha = m.get("index_sha256")
    if declared_sha != actual_sha:
        raise ValueError(
            f"Manifest checksum mismatch: declared={declared_sha}, actual={actual_sha}"
        )

    declared_count = m.get("chunk_count")
    declared_dims = m.get("dense_dimensions")

    # Verify against the seed (not runtime — runtime has .lock files etc.)
    from qdrant_client import QdrantClient

    client = QdrantClient(path=str(seed))
    actual_count = client.count(_VS_COLLECTION_NAME, exact=True).count
    if declared_count != actual_count:
        raise ValueError(
            f"Manifest chunk_count {declared_count} != actual {actual_count}"
        )

    info = client.get_collection(_VS_COLLECTION_NAME)
    actual_dims = info.config.params.vectors[_VS_DENSE_VECTOR_NAME].size
    if declared_dims != actual_dims:
        raise ValueError(
            f"Manifest dense_dimensions {declared_dims} != actual {actual_dims}"
        )

    logger.info(
        "Manifest validated (hard): collection=%s, %d chunks, sha256=%s…",
        collection,
        actual_count,
        actual_sha[:16],
    )
    return True


# ── Qdrant cold-init with atomic copy ──────────────────────────────────


def prepare_qdrant() -> None:
    """Copy the immutable Qdrant seed to runtime path on cold init.

    Uses an atomic copy (copy → .partial dir → rename → .ready marker)
    so an interrupted copy is never mistakenly treated as a valid warm
    reuse target.

    On warm invocations the .ready marker exists — skip the copy entirely.
    Never writes under the seed path.
    """
    _ensure_qdrant_path()
    runtime = Path(RUNTIME_PATH)
    ready_marker = runtime / ".ready"

    if ready_marker.exists():
        logger.info("Qdrant runtime ready at %s — reusing", RUNTIME_PATH)
        return

    validate_manifest()

    seed = Path(SEED_PATH)
    if not seed.exists():
        raise FileNotFoundError(
            f"Qdrant seed not found at {SEED_PATH}. "
            "Verify the Docker image includes assets/qdrant_storage/."
        )

    partial = Path(str(RUNTIME_PATH) + ".partial")
    if partial.exists():
        logger.warning("Removing stale partial copy at %s", partial)
        shutil.rmtree(str(partial))
    if runtime.exists():
        logger.warning("Removing incomplete runtime dir at %s", runtime)
        shutil.rmtree(str(runtime))

    logger.info("Cold init: copying Qdrant seed from %s to %s", SEED_PATH, partial)
    shutil.copytree(str(seed), str(partial))

    # Validate the copy is usable before committing
    from qdrant_client import QdrantClient

    try:
        QdrantClient(path=str(partial))
    except Exception as exc:
        raise RuntimeError(
            f"Qdrant seed copy validation failed — partial copy at {partial} is corrupt"
        ) from exc

    # Atomic rename (same filesystem — /tmp)
    partial.rename(runtime)
    (runtime / ".ready").touch()

    total = sum(f.stat().st_size for f in runtime.rglob("*") if f.is_file())
    count = len(list(runtime.rglob("*")))
    logger.info("Qdrant seed copy complete — %d files, %.1f MB", count, total / (1024 * 1024))


# ── Compiled graph (lazy, cached) ──────────────────────────────────────


@lru_cache(maxsize=1)
def get_compiled_graph():
    """Return the compiled 7-node LangGraph agent. Cached — compiles once per container.

    Qdrant must be prepared before calling this (prepare_qdrant() ensures
    /tmp/qdrant_storage exists, which hybrid_retrieve() needs at runtime).
    The graph is compiled without a persistent checkpointer (single-turn
    synchronous invocations for staging).
    """
    _ensure_qdrant_path()
    from src.agent.graph_skeleton import build_graph

    graph = build_graph().compile()
    logger.info("LangGraph agent compiled (7 nodes, 3 routers)")
    return graph


# ── State construction ─────────────────────────────────────────────────


def initial_state_from_request(question: str, jurisdiction: str | None = None) -> dict:
    """Build AgentState dict from request fields without modifying the schema."""
    from src.agent.state import create_initial_state

    state = create_initial_state()
    state["messages"] = [{"role": "user", "content": question}]
    if jurisdiction:
        state["jurisdiction"] = jurisdiction
    return state
