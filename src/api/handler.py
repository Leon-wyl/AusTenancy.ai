"""FastAPI + Mangum adapter for the complete 7-node LangGraph agent.

Lambda handler for API Gateway HTTP API v2 events.
Bedrock-only staging (LLM_PROVIDER=bedrock). No DeepSeek fallback.

Architecture:
  GET  /health              → no graph invocation, no provider exposure
  POST /api/agent/invoke    → graph.ainvoke() → AgentResponse

This module never calls rewrite, retrieval, generation, or citation
functions directly.  All graph nodes are invoked through graph.ainvoke().
"""

import logging
import time

from fastapi import FastAPI, HTTPException
from mangum import Mangum

from src.api.models import AgentRequest, AgentResponse
from src.api.runtime import (
    get_compiled_graph,
    initial_state_from_request,
    prepare_qdrant,
)

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

# ── FastAPI application ────────────────────────────────────────────────
app = FastAPI(
    title="AusTenancy.ai Agent",
    version="1.0",
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)


@app.get("/health")
async def health():
    """Health check — no graph invocation, no provider/model/dependency exposure.

    Returns exactly the approved schema per the Architecture Gate document:
    {"status": "healthy", "version": "0.1.0", "api_version": "1.0"}
    """
    return {
        "status": "healthy",
        "version": "0.1.0",
        "api_version": "1.0",
    }


def _extract_answer(state: dict) -> str | None:
    """Extract the final answer text from agent state.

    Checks state["answer"] first (legal_reasoner output).
    Falls back to the last assistant/ai message in the message list.
    Never returns user/human messages — only assistant output.
    """
    answer = state.get("answer", "")
    if answer:
        return answer

    messages = state.get("messages", [])
    for msg in reversed(messages):
        if isinstance(msg, dict):
            role = msg.get("role", "")
            text = msg.get("content", "")
        else:
            role = getattr(msg, "type", "")
            text = getattr(msg, "content", "")
        if role in ("assistant", "ai") and text.strip():
            return text.strip()
    return ""


@app.post("/api/agent/invoke", response_model=AgentResponse)
async def agent_invoke(request: AgentRequest):
    """Invoke the full LangGraph agent and return a public response.

    Does NOT call rewrite, retrieval, generation, or citation functions directly.
    All graph nodes are invoked through graph.ainvoke().
    Diagnostic fields (rewritten_queries, retrieved_contexts, unverified_citations,
    top_retrieved_provisions, etc.) are NEVER exposed in the response.
    """
    start = time.perf_counter()

    # Qdrant seed copy + manifest validation on first call (cold init).
    # Subsequent calls in the same container skip the copy (warm reuse).
    prepare_qdrant()
    graph = get_compiled_graph()

    from src.agent.safety import detect_injection

    suspicious = detect_injection(request.question)

    state = initial_state_from_request(
        request.question, request.jurisdiction, suspicious_input=suspicious
    )

    try:
        final_state = await graph.ainvoke(state)
    except Exception as exc:
        from src.agent.safety import redact_log

        safe_detail = redact_log(str(exc))
        logger.error(
            "Graph invocation failed request_id=%s error_type=%s detail=%s",
            request.request_id,
            type(exc).__name__,
            safe_detail,
        )
        raise HTTPException(
            status_code=500,
            detail={
                "message": "Agent invocation failed",
                "request_id": request.request_id,
            },
        ) from exc

    elapsed_ms = (time.perf_counter() - start) * 1000

    from src.agent.observability import generate_run_summary

    summary = generate_run_summary(final_state, total_latency_ms=elapsed_ms)
    status = summary["status"]

    answer = _extract_answer(final_state)

    from src.agent.safety import DISCLAIMER

    if answer:
        answer += DISCLAIMER

    return AgentResponse(
        request_id=request.request_id,
        status=status,
        answer=answer if status in ("success", "fallback") else None,
        verified_citations=summary.get("verified_citations", []),
        citation_verified_rate=summary.get("citation_verified_rate"),
        clarification=answer if status == "clarification" else None,
        fallback_reason=summary.get("fallback_reason") if status == "fallback" else None,
        selected_jurisdiction=final_state.get("jurisdiction") or None,
        latency_ms=elapsed_ms,
    )


# ── Lambda handler via Mangum (API Gateway HTTP API v2) ────────────────
handler = Mangum(app, lifespan="off")
