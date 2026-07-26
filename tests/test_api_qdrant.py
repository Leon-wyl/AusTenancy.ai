"""Tests for Qdrant seed copy, warm reuse, atomic copy, and write protection."""

import json
import os
import shutil
import stat
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


def _make_seed_tree(base: Path) -> tuple[Path, Path]:
    """Create a mock seed directory and manifest for test isolation.

    The seed is NOT a real Qdrant storage — it's a minimal directory tree.
    Tests that perform actual copying must also patch QdrantClient so the
    copy-validation step does not try to open the fake directory as Qdrant.
    """
    seed = base / "assets" / "qdrant_storage"
    seed.mkdir(parents=True)
    (seed / "meta.json").write_text('{"test": true}')
    (seed / "collection").mkdir()
    (seed / "collection" / "data").write_text("mock qdrant data")

    manifest = base / "assets" / "qdrant_index_manifest.json"
    manifest.parent.mkdir(parents=True, exist_ok=True)
    manifest.write_text(
        json.dumps(
            {
                "manifest_version": "1.0",
                "corpus_version": "1.0.0",
                "collection_name": "tenancy_acts",
                "chunk_count": 100,
                "dense_model": "BAAI/bge-small-en-v1.5",
                "dense_dimensions": 384,
                "sparse_model": "Qdrant/bm25",
                "index_sha256": "dummy",
            }
        )
    )
    return seed, manifest


# ── Helpers ────────────────────────────────────────────────────────────

_QDRANT_CLIENT_PATH = "qdrant_client.QdrantClient"


# ── Tests ──────────────────────────────────────────────────────────────


class TestQdrantColdCopy:
    def test_cold_copy_creates_runtime_and_ready_marker(self, tmp_path):
        """On first invocation, seed is copied atomically and .ready exists."""
        seed, manifest = _make_seed_tree(tmp_path)
        runtime = tmp_path / "tmp" / "qdrant_storage"

        with (
            patch("src.api.runtime.SEED_PATH", seed),
            patch("src.api.runtime.RUNTIME_PATH", runtime),
            patch("src.api.runtime.MANIFEST_PATH", manifest),
            patch("src.api.runtime._ensure_qdrant_path"),
            patch(_QDRANT_CLIENT_PATH, MagicMock()),
        ):
            from src.api.runtime import prepare_qdrant

            prepare_qdrant()

            assert runtime.exists()
            assert (runtime / ".ready").exists()
            assert (runtime / "meta.json").exists()
            assert not Path(str(runtime) + ".partial").exists()

    def test_warm_reuse_via_ready_marker(self, tmp_path):
        """When .ready exists, prepare_qdrant skips copy."""
        seed, manifest = _make_seed_tree(tmp_path)
        runtime = tmp_path / "tmp" / "qdrant_storage"
        runtime.mkdir(parents=True)
        (runtime / "existing.txt").write_text("already here")
        (runtime / ".ready").touch()

        with (
            patch("src.api.runtime.SEED_PATH", seed),
            patch("src.api.runtime.RUNTIME_PATH", runtime),
            patch("src.api.runtime.MANIFEST_PATH", manifest),
            patch("src.api.runtime._ensure_qdrant_path"),
        ):
            from src.api.runtime import prepare_qdrant

            prepare_qdrant()
            assert (runtime / "existing.txt").read_text() == "already here"

    def test_partial_copy_cleaned_up(self, tmp_path):
        """If a .partial dir exists from a prior crash, it is removed."""
        seed, manifest = _make_seed_tree(tmp_path)
        runtime = tmp_path / "tmp" / "qdrant_storage"
        partial = Path(str(runtime) + ".partial")
        partial.mkdir(parents=True)
        (partial / "stale.txt").write_text("stale")

        with (
            patch("src.api.runtime.SEED_PATH", seed),
            patch("src.api.runtime.RUNTIME_PATH", runtime),
            patch("src.api.runtime.MANIFEST_PATH", manifest),
            patch("src.api.runtime._ensure_qdrant_path"),
            patch(_QDRANT_CLIENT_PATH, MagicMock()),
        ):
            from src.api.runtime import prepare_qdrant

            prepare_qdrant()
            assert not partial.exists()

    def test_incomplete_runtime_cleaned_up(self, tmp_path):
        """If runtime dir exists without .ready, it's cleaned before fresh copy."""
        seed, manifest = _make_seed_tree(tmp_path)
        runtime = tmp_path / "tmp" / "qdrant_storage"
        runtime.mkdir(parents=True)
        (runtime / "garbage.txt").write_text("garbage")

        with (
            patch("src.api.runtime.SEED_PATH", seed),
            patch("src.api.runtime.RUNTIME_PATH", runtime),
            patch("src.api.runtime.MANIFEST_PATH", manifest),
            patch("src.api.runtime._ensure_qdrant_path"),
            patch(_QDRANT_CLIENT_PATH, MagicMock()),
        ):
            from src.api.runtime import prepare_qdrant

            prepare_qdrant()
            assert not (runtime / "garbage.txt").exists() or not runtime.exists()


class TestQdrantErrors:
    def test_missing_seed_raises(self, tmp_path):
        """If seed path doesn't exist, FileNotFoundError is raised."""
        runtime = tmp_path / "tmp" / "qdrant_storage"
        manifest = tmp_path / "assets" / "qdrant_index_manifest.json"
        manifest.parent.mkdir(parents=True, exist_ok=True)
        manifest.write_text(
            json.dumps(
                {
                    "manifest_version": "1.0",
                    "collection_name": "tenancy_acts",
                    "chunk_count": 100,
                    "dense_model": "BAAI/bge-small-en-v1.5",
                    "dense_dimensions": 384,
                    "sparse_model": "Qdrant/bm25",
                    "index_sha256": "dummy",
                }
            )
        )

        with (
            patch("src.api.runtime.SEED_PATH", tmp_path / "nonexistent" / "qdrant_storage"),
            patch("src.api.runtime.RUNTIME_PATH", runtime),
            patch("src.api.runtime.MANIFEST_PATH", manifest),
            patch("src.api.runtime._ensure_qdrant_path"),
        ):
            from src.api.runtime import prepare_qdrant

            with pytest.raises(FileNotFoundError, match="seed not found"):
                prepare_qdrant()

    def test_missing_manifest_raises(self, tmp_path):
        """If manifest is missing, FileNotFoundError is raised."""
        seed = tmp_path / "assets" / "qdrant_storage"
        seed.mkdir(parents=True)
        (seed / "meta.json").write_text("{}")
        runtime = tmp_path / "tmp" / "qdrant_storage"
        manifest = tmp_path / "nonexistent" / "qdrant_index_manifest.json"

        with (
            patch("src.api.runtime.SEED_PATH", seed),
            patch("src.api.runtime.RUNTIME_PATH", runtime),
            patch("src.api.runtime.MANIFEST_PATH", manifest),
            patch("src.api.runtime._ensure_qdrant_path"),
        ):
            from src.api.runtime import prepare_qdrant

            with pytest.raises(FileNotFoundError, match="manifest not found"):
                prepare_qdrant()


class TestManifestValidation:
    def test_light_validation_passes(self, tmp_path):
        """Light validation checks version and collection name only."""
        manifest = tmp_path / "manifest.json"
        manifest.write_text(
            json.dumps(
                {
                    "manifest_version": "1.0",
                    "collection_name": "tenancy_acts",
                    "chunk_count": 100,
                    "dense_model": "BAAI/bge-small-en-v1.5",
                    "dense_dimensions": 384,
                    "sparse_model": "Qdrant/bm25",
                    "index_sha256": "dummy",
                }
            )
        )

        with patch("src.api.runtime.MANIFEST_PATH", manifest):
            from src.api.runtime import validate_manifest

            result = validate_manifest(hard=False)
            assert result is True

    def test_wrong_version_rejected(self, tmp_path):
        manifest = tmp_path / "manifest.json"
        manifest.write_text('{"manifest_version": "0.9", "collection_name": "x"}')

        with patch("src.api.runtime.MANIFEST_PATH", manifest):
            from src.api.runtime import validate_manifest

            with pytest.raises(ValueError, match="manifest_version"):
                validate_manifest(hard=False)

    def test_wrong_collection_rejected(self, tmp_path):
        manifest = tmp_path / "manifest.json"
        manifest.write_text('{"manifest_version": "1.0", "collection_name": "wrong"}')

        with patch("src.api.runtime.MANIFEST_PATH", manifest):
            from src.api.runtime import validate_manifest

            with pytest.raises(ValueError, match="collection"):
                validate_manifest(hard=False)
