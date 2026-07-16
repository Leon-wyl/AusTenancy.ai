"""LangGraph agent for Australian Residential Tenancies Compliance.

7 nodes: intake_analyzer → request_clarification → query_rewriter
         → rag_retriever → legal_reasoner → citation_verifier → fallback_node
3 routers: route_after_intake, route_after_retrieval, route_after_verify

Usage:
    python src/agent/graph_skeleton.py    # prints Mermaid diagram
"""

import logging
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from langgraph.graph import END, StateGraph

from src.agent.state import AgentState
from src.rag.generation.generator import (
    CITATION_RE,
    _apply_citation_guard,
    _rewrite_queries,
    generate_answer_from_context,
    retrieve_from_queries,
    verify_citations,
)

logger = logging.getLogger(__name__)

# ── Jurisdiction helpers ───────────────────────────────────────────────

SUPPORTED_JURISDICTIONS: frozenset[str] = frozenset({"VIC", "NSW"})
JURISDICTION_PATTERN = re.compile(r"\b(VIC|NSW|QLD|SA|WA|TAS|ACT|NT)\b", re.IGNORECASE)

_NON_TENANCY_TRIGGERS: tuple[str, ...] = (
    "recipe",
    "cook ",
    "bake ",
    "ingredient",
    "weather",
    "forecast",
    "movie",
    "film ",
    "cinema",
    "sport",
    "football",
    "soccer",
    "cricket",
    "basketball",
    "song",
    "music",
    "band ",
    "concert",
    "joke",
    "funny",
    "comedy",
    "pizza",
    "burger",
    "restaurant",
)


def _latest_user_query(state: AgentState) -> str:
    messages = state.get("messages", [])
    for m in reversed(messages):
        if isinstance(m, dict):
            text = m.get("content", "")
        elif hasattr(m, "content"):
            text = m.content
        else:
            text = str(m)
        if text.strip():
            return text.strip()
    return ""


def _detect_jurisdiction(text: str) -> str:
    matches = JURISDICTION_PATTERN.findall(text)
    if matches:
        return matches[-1].upper()
    return ""


def _is_clearly_non_tenancy(query: str) -> bool:
    lower = query.lower()
    word_count = len(lower.split())
    if word_count > 15:
        return False
    return any(trigger in lower for trigger in _NON_TENANCY_TRIGGERS)


# ── Nodes ──────────────────────────────────────────────────────────────


def intake_analyzer(state: AgentState) -> dict:
    """Assess query scope and jurisdiction.

    Conservative heuristic: only reject high-confidence out-of-scope queries.
    Ambiguous queries are routed to RAG for substantive handling.
    """
    query = _latest_user_query(state)
    jurisdiction = state.get("jurisdiction", "")

    if not jurisdiction and query:
        jurisdiction = _detect_jurisdiction(query)

    is_unsupported = bool(jurisdiction and jurisdiction not in SUPPORTED_JURISDICTIONS)
    in_scope = not _is_clearly_non_tenancy(query)

    result: dict = {"in_scope": in_scope, "jurisdiction": jurisdiction}

    if not in_scope:
        result["fallback_reason"] = "out_of_scope"
    elif is_unsupported:
        result["fallback_reason"] = "unsupported_jurisdiction"

    return result


def request_clarification(state: AgentState) -> dict:
    """Ask the user for missing jurisdiction or tenancy type information."""
    missing = []
    if not state.get("jurisdiction"):
        missing.append("state (e.g. VIC, NSW)")
    if not state.get("tenancy_type"):
        missing.append("tenancy type (e.g. residential, commercial)")

    clarification = (
        "To provide an accurate legal answer, I need more information. "
        "Please specify: " + ", ".join(missing) + "."
    )
    return {"messages": [{"role": "assistant", "content": clarification}]}


def query_rewriter(state: AgentState) -> dict:
    """Rewrite user query into 3 complementary legal search queries.

    Generates SEMANTIC, STATUTORY, and CONCEPT queries using existing
    multi-query rewrite logic.
    """
    query = _latest_user_query(state)
    jurisdiction = state.get("jurisdiction", "")
    queries = _rewrite_queries(query, jurisdiction or None)
    logger.info("query_rewriter: %d queries generated", len(queries))
    return {"rewritten_queries": queries}


def rag_retriever(state: AgentState) -> dict:
    """Retrieve statutory context via per-query hybrid search + RRF fusion.

    Preserves existing retrieval behaviour: per-query retrieval, RRF,
    Top-1 preservation, no reranker.
    """
    queries = state.get("rewritten_queries", [])
    jurisdiction = state.get("jurisdiction", "")
    chunks = retrieve_from_queries(
        queries=queries,
        jurisdiction=jurisdiction or None,
        final_top_k=10,
    )
    logger.info("rag_retriever: %d contexts retrieved", len(chunks))

    result: dict = {"retrieved_contexts": chunks}
    if not chunks:
        result["fallback_reason"] = "empty_retrieval"
    return result


def legal_reasoner(state: AgentState) -> dict:
    """Generate legal answer from retrieved contexts via LLM.

    Pure generation — no retrieval, no citation verification here.
    """
    query = _latest_user_query(state)
    jurisdiction = state.get("jurisdiction", "")
    chunks = state.get("retrieved_contexts", [])
    answer = generate_answer_from_context(query, jurisdiction or None, chunks)
    logger.info("legal_reasoner: answer generated (%d chars)", len(answer))
    return {"answer": answer}


def citation_verifier(state: AgentState) -> dict:
    """Verify all citations in the answer against retrieved contexts.

    Applies citation guard: removes unverified citation markers and adds
    appropriate warnings. Sets fallback_reason if no citations can be verified.
    """
    answer = state.get("answer", "")
    chunks = state.get("retrieved_contexts", [])
    citation_check = verify_citations(answer, chunks)
    guarded_answer = _apply_citation_guard(answer, citation_check)

    all_citations = CITATION_RE.findall(answer)
    unverified = citation_check.get("unverified", [])
    verified = citation_check.get("verified", [])

    result: dict = {
        "answer": guarded_answer,
        "citations_verified": len(verified) > 0,
        "citation_errors": unverified,
    }

    if all_citations and len(unverified) == len(all_citations):
        result["fallback_reason"] = "citation_verification_failed"
    elif not all_citations:
        result["citations_verified"] = False
        result["fallback_reason"] = "citation_verification_failed"

    return result


def fallback_node(state: AgentState) -> dict:
    """Produce a graceful fallback message based on the trigger reason."""
    reason = state.get("fallback_reason", "")
    messages = {
        "out_of_scope": (
            "I'm a tenancy law specialist and can't help with this topic. "
            "Please ask me about residential tenancy issues in VIC or NSW."
        ),
        "unsupported_jurisdiction": (
            "I currently only support Victorian (VIC) and New South Wales (NSW) "
            "residential tenancy law. Please rephrase your question for VIC or NSW."
        ),
        "empty_retrieval": (
            "I couldn't find relevant statutory provisions for your question. "
            "Try rephrasing with different terms or specifying your jurisdiction."
        ),
        "citation_verification_failed": (
            "I was unable to verify the legal citations in the generated answer "
            "against the retrieved statutory context. Please try rephrasing your question."
        ),
    }
    message = messages.get(reason, "I wasn't able to process your request. Please try again.")
    return {"messages": [{"role": "assistant", "content": message}]}


# ── Conditional routing ─────────────────────────────────────────────────


def route_after_intake(state: AgentState) -> str:
    """Route after intake_analyzer.

    Returns:
        "fallback_node" — out of scope or unsupported jurisdiction.
        "request_clarification" — in scope but missing jurisdiction.
        "query_rewriter" — all slots filled, proceed to retrieval pipeline.
    """
    if not state.get("in_scope", True):
        return "fallback_node"
    jurisdiction = state.get("jurisdiction", "")
    if jurisdiction and jurisdiction not in SUPPORTED_JURISDICTIONS:
        return "fallback_node"
    if not jurisdiction:
        return "request_clarification"
    return "query_rewriter"


def route_after_retrieval(state: AgentState) -> str:
    """Route after rag_retriever.

    Returns:
        "fallback_node" — no contexts retrieved.
        "legal_reasoner" — contexts available, proceed to generation.
    """
    if not state.get("retrieved_contexts"):
        return "fallback_node"
    return "legal_reasoner"


def route_after_verify(state: AgentState) -> str:
    """Route after citation_verifier.

    Returns:
        "fallback_node" — all citations unverified or zero citations.
        "__end__" — at least some citations verified (guard removed bad ones).
    """
    errors = state.get("citation_errors", [])
    answer = state.get("answer", "")
    all_citations = CITATION_RE.findall(answer)

    if not all_citations or len(errors) == len(all_citations):
        return "fallback_node"
    return "__end__"


# ── Graph construction ──────────────────────────────────────────────────


def build_graph() -> StateGraph:
    """Construct and configure the 7-node LangGraph state machine."""
    workflow = StateGraph(AgentState)

    workflow.add_node("intake_analyzer", intake_analyzer)
    workflow.add_node("request_clarification", request_clarification)
    workflow.add_node("query_rewriter", query_rewriter)
    workflow.add_node("rag_retriever", rag_retriever)
    workflow.add_node("legal_reasoner", legal_reasoner)
    workflow.add_node("citation_verifier", citation_verifier)
    workflow.add_node("fallback_node", fallback_node)

    workflow.set_entry_point("intake_analyzer")

    workflow.add_conditional_edges(
        "intake_analyzer",
        route_after_intake,
        {
            "fallback_node": "fallback_node",
            "request_clarification": "request_clarification",
            "query_rewriter": "query_rewriter",
        },
    )

    workflow.add_edge("request_clarification", END)
    workflow.add_edge("query_rewriter", "rag_retriever")

    workflow.add_conditional_edges(
        "rag_retriever",
        route_after_retrieval,
        {
            "fallback_node": "fallback_node",
            "legal_reasoner": "legal_reasoner",
        },
    )

    workflow.add_edge("legal_reasoner", "citation_verifier")

    workflow.add_conditional_edges(
        "citation_verifier",
        route_after_verify,
        {
            "fallback_node": "fallback_node",
            "__end__": END,
        },
    )

    workflow.add_edge("fallback_node", END)

    return workflow


# ── Mermaid visualization ───────────────────────────────────────────────


if __name__ == "__main__":
    graph = build_graph().compile()
    print(graph.get_graph().draw_mermaid())
