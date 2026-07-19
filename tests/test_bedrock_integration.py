"""Gated AWS Bedrock integration tests — OFF by default.

Two-layer gate:

1. Collection-time skip (before any AWS activity) requires ALL of:
       RUN_BEDROCK_INTEGRATION=1        (explicit opt-in)
       AWS_REGION or AWS_DEFAULT_REGION
       BEDROCK_MODEL_ID
2. Runtime fixtures then resolve:
       - AWS credentials via the full boto3 chain (env vars, shared
         credentials/SSO profiles, IAM roles, IMDS, credential_process,
         web identity) — skip when none resolve;
       - local Qdrant data with indexed VIC and NSW chunks (pipeline
         tests only) — skip when unavailable.

Setting LLM_PROVIDER=bedrock alone must NOT enable these tests — the
RUN_BEDROCK_INTEGRATION=1 opt-in is always required. Confirmed model
access cannot be pre-checked from env: an AccessDeniedException at call
time surfaces as the provider's RuntimeError and is reported as a test
FAILURE (an access-configuration problem), not a skip.
"""

import os
from pathlib import Path

import pytest


def _bedrock_gate_reason() -> str:
    if os.environ.get("RUN_BEDROCK_INTEGRATION") != "1":
        return "RUN_BEDROCK_INTEGRATION=1 not set (explicit opt-in required)"
    if not (
        os.environ.get("AWS_REGION", "").strip() or os.environ.get("AWS_DEFAULT_REGION", "").strip()
    ):
        return "AWS_REGION or AWS_DEFAULT_REGION not set"
    if not os.environ.get("BEDROCK_MODEL_ID", "").strip():
        return "BEDROCK_MODEL_ID not set"
    return ""


_SKIP_REASON = _bedrock_gate_reason()

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(bool(_SKIP_REASON), reason=f"Bedrock integration gated: {_SKIP_REASON}"),
]


@pytest.fixture(scope="module")
def aws_credentials_or_skip():
    """Skip when the boto3 credential chain resolves no credentials.

    Runs only after the collection-time opt-in gate, so normal pytest
    never triggers credential discovery.
    """
    import boto3

    session = boto3.Session()
    if session.get_credentials() is None:
        pytest.skip("AWS credentials could not be resolved by the boto3 credential chain")


def _qdrant_gate_reason() -> str:
    """Check local Qdrant storage has indexed VIC and NSW chunks."""
    try:
        from src.rag.retrieval.vector_store import COLLECTION_NAME, QDRANT_PATH
    except ImportError as exc:
        return f"vector_store import failed: {exc}"
    qdrant_dir = Path(QDRANT_PATH)
    if not qdrant_dir.exists():
        return f"Qdrant storage dir not found: {qdrant_dir.resolve()}"
    try:
        from qdrant_client import QdrantClient, models

        client = QdrantClient(path=str(qdrant_dir.resolve()))
    except Exception as exc:
        return f"Qdrant connect failed: {exc}"
    try:
        if not client.collection_exists(COLLECTION_NAME):
            return f"collection missing: {COLLECTION_NAME}"
        for state in ("VIC", "NSW"):
            count = client.count(
                collection_name=COLLECTION_NAME,
                count_filter=models.Filter(
                    must=[models.FieldCondition(key="state", match=models.MatchValue(value=state))]
                ),
            ).count
            if count == 0:
                return f"no {state} chunks indexed"
    except Exception as exc:
        return f"Qdrant collection check failed: {exc}"
    finally:
        client.close()
    return ""


@pytest.fixture(scope="module")
def qdrant_or_skip():
    reason = _qdrant_gate_reason()
    if reason:
        pytest.skip(f"Local Qdrant data unavailable: {reason}")


@pytest.fixture()
def bedrock_provider_env(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "bedrock")


# ── Provider smoke tests ───────────────────────────────────────────────


def test_bedrock_generate_returns_text(aws_credentials_or_skip):
    from src.rag.generation.llm_provider import BedrockLLMProvider

    provider = BedrockLLMProvider()
    answer = provider.generate(
        [
            {"role": "system", "content": "Answer with one short sentence."},
            {"role": "user", "content": "What is the capital of Australia?"},
        ],
        max_tokens=64,
    )
    assert isinstance(answer, str)
    assert answer.strip()


def test_bedrock_selected_via_factory(aws_credentials_or_skip, bedrock_provider_env):
    from src.rag.generation.llm_provider import BedrockLLMProvider, get_llm_provider

    provider = get_llm_provider()
    assert isinstance(provider, BedrockLLMProvider)
    answer = provider.generate(
        [{"role": "user", "content": "Reply with the word OK."}], max_tokens=16
    )
    assert answer.strip()


# ── Future pipeline tests (VIC / NSW / citation) ───────────────────────


def test_vic_eviction_pipeline_with_bedrock(
    aws_credentials_or_skip, qdrant_or_skip, bedrock_provider_env
):
    from src.rag.generation.generator import generate_compliance_answer

    result = generate_compliance_answer(
        query=(
            "My landlord wants to evict me because I am 10 days behind on rent "
            "at my apartment in Melbourne VIC"
        ),
        state_filter="VIC",
        top_k_retrieve=10,
    )
    assert result["answer"].strip()
    assert result["citation_check"]["verified"], "expected at least one verified citation"


def test_nsw_eviction_pipeline_with_bedrock(
    aws_credentials_or_skip, qdrant_or_skip, bedrock_provider_env
):
    from src.rag.generation.generator import generate_compliance_answer

    result = generate_compliance_answer(
        query=(
            "My landlord wants to evict me because I am 14 days behind on rent "
            "at my apartment in Sydney NSW"
        ),
        state_filter="NSW",
        top_k_retrieve=10,
    )
    assert result["answer"].strip()
    assert result["citation_check"]["verified"], "expected at least one verified citation"


def test_bedrock_answer_citations_survive_guard(
    aws_credentials_or_skip, qdrant_or_skip, bedrock_provider_env
):
    """Citations in the guarded Bedrock answer are canonical and verified,
    and any unverified draft citations were removed by the citation guard."""
    from src.rag.generation.generator import CITATION_RE, generate_compliance_answer

    result = generate_compliance_answer(
        query="How much notice is required for a rent increase in VIC?",
        state_filter="VIC",
        top_k_retrieve=10,
    )
    citations = CITATION_RE.findall(result["answer"])
    assert citations, "expected canonical [VIC RTA ...] citations in the guarded answer"
    assert result["citation_check"]["verified"]
    for cite in result["citation_check"]["unverified"]:
        assert cite not in result["answer"]
