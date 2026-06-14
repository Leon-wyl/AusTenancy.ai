"""Tests for vector_store.py — ingestion and hybrid retrieval."""

import tempfile

import pytest

from src.retrieval import vector_store as vs

# ── Helpers ───────────────────────────────────────────────────────────


def _make_qdrant_client(qdrant_path: str):
    from qdrant_client import QdrantClient

    return QdrantClient(path=qdrant_path)


# ── Error handling tests ──────────────────────────────────────────────


class TestErrorHandling:
    def test_file_not_found(self):
        with pytest.raises(FileNotFoundError, match="not found"):
            vs.ingest_chunks_to_qdrant("/nonexistent/path.json")

    def test_empty_json(self, empty_chunks_file):
        with pytest.raises(ValueError, match="empty"):
            vs.ingest_chunks_to_qdrant(empty_chunks_file)

    def test_retrieve_without_collection(self, monkeypatch):
        with tempfile.TemporaryDirectory() as tmpdir:
            monkeypatch.setattr(vs, "QDRANT_PATH", tmpdir)
            monkeypatch.setattr(vs, "COLLECTION_NAME", "no_such_collection")
            with pytest.raises(RuntimeError, match="not found"):
                vs.hybrid_retrieve("test query")


# ── Ingestion tests ───────────────────────────────────────────────────


class TestIngestion:
    def test_ingest_creates_collection(self, qdrant_with_data):
        client = _make_qdrant_client(qdrant_with_data["path"])
        assert client.collection_exists(qdrant_with_data["collection"])

    def test_ingest_returns_correct_count(self, qdrant_with_data):
        assert qdrant_with_data["count"] == 5

    def test_ingest_collection_config(self, qdrant_with_data):
        client = _make_qdrant_client(qdrant_with_data["path"])
        info = client.get_collection(qdrant_with_data["collection"])
        cfg = info.config.params
        vectors = cfg.vectors
        assert "dense" in vectors
        assert vectors["dense"].size == 384
        assert hasattr(cfg, "sparse_vectors")
        assert "sparse" in cfg.sparse_vectors

    def test_points_have_payload(self, qdrant_with_data):
        client = _make_qdrant_client(qdrant_with_data["path"])
        points, _ = client.scroll(
            collection_name=qdrant_with_data["collection"],
            limit=1,
            with_payload=True,
            with_vectors=False,
        )
        assert len(points) == 1
        payload = points[0].payload
        assert payload is not None
        for key in ("chunk_id", "text", "state", "section_id", "section_title"):
            assert key in payload, f"Missing payload key: {key}"

    def test_points_have_two_vectors(self, qdrant_with_data):
        client = _make_qdrant_client(qdrant_with_data["path"])
        points, _ = client.scroll(
            collection_name=qdrant_with_data["collection"],
            limit=1,
            with_payload=False,
            with_vectors=True,
        )
        assert len(points) == 1
        vector = points[0].vector
        assert vector is not None
        assert "dense" in vector
        assert "sparse" in vector
        assert len(vector["dense"]) == 384
        assert hasattr(vector["sparse"], "indices")
        assert hasattr(vector["sparse"], "values")


# ── Retrieval smoke tests ─────────────────────────────────────────────


class TestRetrievalSmoke:
    def test_retrieve_returns_list(self, qdrant_with_data):
        results = vs.hybrid_retrieve("rent increase notice")
        assert isinstance(results, list)

    def test_retrieve_return_schema(self, qdrant_with_data):
        results = vs.hybrid_retrieve("rent increase notice", top_k=3)
        assert len(results) >= 1
        required_keys = {
            "chunk_id", "text", "score", "section_id",
            "section_title", "part", "state",
        }
        for r in results:
            for key in required_keys:
                assert key in r, f"Missing key '{key}' in result"

    def test_retrieve_respects_top_k(self, qdrant_with_data):
        results = vs.hybrid_retrieve("notice", top_k=2)
        assert len(results) <= 2

    def test_retrieve_scores_descending(self, qdrant_with_data):
        results = vs.hybrid_retrieve("notice period rent", top_k=5)
        scores = [r["score"] for r in results]
        assert scores == sorted(scores, reverse=True)

    def test_retrieve_scores_in_range(self, qdrant_with_data):
        results = vs.hybrid_retrieve("notice", top_k=5)
        for r in results:
            assert 0.0 <= r["score"] <= 1.0, f"Score {r['score']} out of [0,1]"


# ── Hybrid search quality tests ───────────────────────────────────────


class TestHybridSearchQuality:
    def test_bm25_exact_keywords_top_result(self, qdrant_with_data):
        results = vs.hybrid_retrieve(
            "14 day notice non-payment rent", top_k=3
        )
        assert len(results) >= 1
        top_ids = {r["section_id"] for r in results}
        assert "91ZM" in top_ids, f"91ZM not in top results: {top_ids}"

    def test_metadata_filter_vic_only(self, qdrant_with_data):
        results = vs.hybrid_retrieve(
            "rent increase notice",
            state_filter={"state": "VIC"},
            top_k=5,
        )
        assert len(results) >= 1
        for r in results:
            assert r["state"] == "VIC", f"Non-VIC result: {r['state']}"

    def test_metadata_filter_nsw_only(self, qdrant_with_data):
        results = vs.hybrid_retrieve(
            "rent increase notice",
            state_filter={"state": "NSW"},
            top_k=5,
        )
        assert len(results) >= 1
        for r in results:
            assert r["state"] == "NSW"

    def test_no_filter_includes_all_states(self, qdrant_with_data):
        results = vs.hybrid_retrieve("rent increase", top_k=5)
        states = {r["state"] for r in results}
        assert "VIC" in states
        assert "NSW" in states, "NSW chunk should appear without filter"

    def test_zero_results_on_unknown_filter(self, qdrant_with_data):
        results = vs.hybrid_retrieve(
            "notice",
            state_filter={"state": "QLD"},
            top_k=5,
        )
        assert results == []

    def test_dense_semantic_rent_notice_match(self, qdrant_with_data):
        results = vs.hybrid_retrieve(
            "landlord must inform about higher rent",
            top_k=3,
        )
        assert len(results) >= 1
        sections = {r["section_id"] for r in results}
        assert "44" in sections, f"Section 44 (rent increases) missing: {sections}"


# ── End-to-end test (real data) ───────────────────────────────────────


@pytest.mark.slow
class TestEndToEnd:
    @pytest.fixture(scope="class")
    def e2e_data(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            orig_path = vs.QDRANT_PATH
            orig_collection = vs.COLLECTION_NAME
            vs.QDRANT_PATH = tmpdir
            vs.COLLECTION_NAME = "tenancy_acts"

            count = vs.ingest_chunks_to_qdrant(
                "data/processed/vic_rta_chunks.json"
            )

            data = {"count": count, "path": tmpdir}
            yield data

            vs.QDRANT_PATH = orig_path
            vs.COLLECTION_NAME = orig_collection

    def test_e2e_full_ingestion(self, e2e_data):
        assert e2e_data["count"] >= 900

    def test_e2e_known_query_returns_results(self, e2e_data):
        results = vs.hybrid_retrieve(
            "How many days notice for unpaid rent?",
            state_filter={"state": "VIC"},
            top_k=5,
        )
        assert len(results) >= 1

    def test_e2e_known_section_retrievable(self, e2e_data):
        results = vs.hybrid_retrieve(
            "14 days notice non-payment rent",
            state_filter={"state": "VIC"},
            top_k=3,
        )
        section_ids = {r["section_id"] for r in results}
        assert "91ZM" in section_ids, f"91ZM not found: {section_ids}"

    def test_e2e_section_44_exists(self, e2e_data):
        results = vs.hybrid_retrieve(
            "90 days notice for rent increase",
            state_filter={"state": "VIC"},
            top_k=3,
        )
        section_ids = {r["section_id"] for r in results}
        assert "44" in section_ids, f"Section 44 not found: {section_ids}"
