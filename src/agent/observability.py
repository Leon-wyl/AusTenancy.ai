"""Lightweight observability layer for the tenancy compliance agent.

Provides optional LangSmith env configuration and local structured run
summaries without modifying graph nodes, retrieval, generation, or
citation verification code.

Usage:
    from src.agent.observability import generate_run_summary
    summary = generate_run_summary(state, total_latency_ms=5271.0)
"""

import logging
import os

from src.rag.generation.generator import format_citation_label, verify_citations

logger = logging.getLogger(__name__)

_CLARIFICATION_PREFIX = "To provide an accurate legal answer"


def configure_langsmith(enable: bool = False) -> bool:
    """Optionally set LangChain tracing environment variables.

    Does NOT import or require the langsmith package.
    Only sets os.environ values so LangChain/LangGraph auto-instrumentation
    picks them up at init time if langsmith is installed downstream.

    Returns True if env vars were present and successfully set.

    Args:
        enable: If False, return immediately without touching env.

    Supports both standard LANGCHAIN_* and legacy LANGSMITH_* naming.
    """
    if not enable:
        return False

    tracing = os.environ.get("LANGCHAIN_TRACING_V2") or os.environ.get("LANGSMITH_TRACING")
    if tracing is None or tracing.lower() != "true":
        return False

    api_key = os.environ.get("LANGCHAIN_API_KEY") or os.environ.get("LANGSMITH_API_KEY")
    if not api_key:
        return False

    project = os.environ.get("LANGCHAIN_PROJECT") or os.environ.get("LANGSMITH_PROJECT")

    try:
        os.environ["LANGCHAIN_TRACING_V2"] = "true"
        os.environ["LANGCHAIN_API_KEY"] = api_key
        if project:
            os.environ["LANGCHAIN_PROJECT"] = project
        return True
    except Exception:
        return False


def compute_context_stats(contexts: list[dict]) -> dict:
    """Compute retrieval context statistics.

    Args:
        contexts: List of retrieved chunk dicts from Qdrant.

    Returns dict with:
        retrieved_context_count, act_context_count, regulation_context_count,
        top_retrieved_provisions (label, section_id, instrument_type, score).

    Does NOT include full legal text.
    """
    act_count = 0
    reg_count = 0
    provisions: list[dict] = []

    for c in contexts:
        inst = c.get("instrument_type")
        if inst == "regulation":
            reg_count += 1
        else:
            act_count += 1
            inst = "act"

        provisions.append(
            {
                "label": format_citation_label(c),
                "section_id": c.get("section_id", ""),
                "instrument_type": inst,
                "score": c.get("score"),
            }
        )

    return {
        "retrieved_context_count": len(contexts),
        "act_context_count": act_count,
        "regulation_context_count": reg_count,
        "top_retrieved_provisions": provisions,
    }


def _is_regulation_citation(citation: str) -> bool:
    """Check if a citation label refers to a Regulation (not an Act).

    Citations look like:  [VIC RTA 1997 Sec 44]  (Act)
                   or     [VIC REG 2021 Reg 21] (Regulation)
    """
    return " REG " in citation


def compute_citation_stats(answer: str, contexts: list[dict]) -> dict:
    """Compute citation verification statistics.

    Re-calls verify_citations() (deterministic, idempotent) to recover
    the verified list that the graph's citation_verifier node discards.

    Args:
        answer: LLM-generated answer text with [VIC RTA 1997 Sec 44] citations.
        contexts: Retrieved chunk dicts used as citation whitelist.

    Returns dict with:
        verified_citations, unverified_citations,
        verified_regulation_citations, verified_act_citations,
        verified_regulation_citation_count, verified_act_citation_count,
        citation_verified_rate.

    citation_verified_rate is None when no canonical citations exist.
    """
    result = verify_citations(answer, contexts)
    verified: list[str] = result.get("verified", []) or []
    unverified: list[str] = result.get("unverified", []) or []

    reg_cites = [c for c in verified if _is_regulation_citation(c)]
    act_cites = [c for c in verified if not _is_regulation_citation(c)]

    total = len(verified) + len(unverified)
    rate = len(verified) / total if total > 0 else None

    return {
        "verified_citations": verified,
        "unverified_citations": unverified,
        "verified_regulation_citations": reg_cites,
        "verified_act_citations": act_cites,
        "verified_regulation_citation_count": len(reg_cites),
        "verified_act_citation_count": len(act_cites),
        "citation_verified_rate": rate,
    }


def _msg_text(msg: object) -> str:
    """Extract content text from a LangChain message or plain dict."""
    if isinstance(msg, dict):
        return msg.get("content", "") or ""
    return getattr(msg, "content", "") or ""


def _has_clarification_message(state: dict) -> bool:
    """Check if state contains a clarification request message."""
    messages = state.get("messages", []) or []
    return any(_msg_text(m).startswith(_CLARIFICATION_PREFIX) for m in messages)


def _msg_role(msg: object) -> str:
    """Extract role/type from a LangChain message or plain dict.

    LangChain HumanMessage/AIMessage use ``type`` (e.g. "human", "ai").
    Plain dicts use ``role`` (e.g. "user", "assistant").
    """
    if isinstance(msg, dict):
        return msg.get("role", "") or ""
    return getattr(msg, "type", "") or ""


def _extract_question(state: dict) -> str:
    """Extract the last user/human message from state messages."""
    messages = state.get("messages", []) or []
    for m in reversed(messages):
        role = _msg_role(m)
        text = _msg_text(m)
        if role in ("user", "human") and text.strip():
            return text.strip()
    return ""


def generate_run_summary(state: dict, total_latency_ms: float | None = None) -> dict:
    """Generate a structured JSON-serializable run summary from agent state.

    Args:
        state: AgentState dict after graph invocation.
        total_latency_ms: Optional total run latency in milliseconds.

    Returns:
        dict with all run summary fields.

    Status rules:
        "fallback"      — fallback_reason is set.
        "clarification" — jurisdiction missing and clarification message produced.
        "success"       — otherwise.
    """
    answer: str = state.get("answer", "") or ""
    contexts: list[dict] = state.get("retrieved_contexts", []) or []
    jurisdiction: str = state.get("jurisdiction", "") or ""
    fallback_reason: str = state.get("fallback_reason", "") or ""

    if fallback_reason:
        status = "fallback"
    elif not jurisdiction and _has_clarification_message(state):
        status = "clarification"
    else:
        status = "success"

    question = _extract_question(state)

    ctx_stats = compute_context_stats(contexts)
    cite_stats = compute_citation_stats(answer, contexts)

    return {
        "question": question,
        "jurisdiction": jurisdiction,
        "in_scope": state.get("in_scope", True),
        "fallback_reason": fallback_reason,
        "rewritten_queries": state.get("rewritten_queries", []) or [],
        "retrieved_context_count": ctx_stats["retrieved_context_count"],
        "top_retrieved_provisions": ctx_stats["top_retrieved_provisions"],
        "regulation_context_count": ctx_stats["regulation_context_count"],
        "act_context_count": ctx_stats["act_context_count"],
        "answer_length": len(answer),
        "verified_citations": cite_stats["verified_citations"],
        "unverified_citations": cite_stats["unverified_citations"],
        "verified_regulation_citations": cite_stats["verified_regulation_citations"],
        "verified_act_citations": cite_stats["verified_act_citations"],
        "verified_regulation_citation_count": cite_stats["verified_regulation_citation_count"],
        "verified_act_citation_count": cite_stats["verified_act_citation_count"],
        "citation_verified_rate": cite_stats["citation_verified_rate"],
        "total_latency_ms": total_latency_ms,
        "status": status,
    }
