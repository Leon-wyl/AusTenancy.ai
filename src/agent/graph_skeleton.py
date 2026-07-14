"""LangGraph state machine skeleton for the Australian Tenancies Compliance Agent.

7 nodes: intake_analyzer → request_clarification → query_rewriter
         → rag_retriever → legal_reasoner → citation_verifier → fallback_node

Usage:
    python src/agent/graph_skeleton.py    # prints Mermaid diagram
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from langgraph.graph import END, StateGraph

from src.agent.state import AgentState

# ── Node stubs ─────────────────────────────────────────────────────────


def intake_analyzer(state: AgentState) -> dict:
    """Assess query scope: in-scope tenancy question or out-of-scope.

    Flags complex cases (cross-jurisdiction, high-risk) for fallback routing.
    """
    return {
        "in_scope": True,
        "is_complex_case": False,
    }


def request_clarification(state: AgentState) -> dict:
    """Request missing jurisdiction or tenancy_type from the user.

    Increments retry_count for the retry guard in route_after_intake.
    """
    return {
        "retry_count": state["retry_count"] + 1,
    }


def query_rewriter(state: AgentState) -> dict:
    """Rewrite conversational query into legal keyword search queries.

    Maps colloquial terms to statutory vocabulary for retrieval.
    """
    return {}


def rag_retriever(state: AgentState) -> dict:
    """Proxy to vector_store.hybrid_retrieve().

    Populates retrieved_contexts with dicts matching the existing
    hybrid_retrieve return type.
    """
    return {
        "retrieved_contexts": [],
    }


def legal_reasoner(state: AgentState) -> dict:
    """Core legal reasoning via IRAC format.

    Issue → Rule → Application → Conclusion.
    Calls LLM with retrieved_contexts as grounding evidence.
    """
    return {}


def citation_verifier(state: AgentState) -> dict:
    """Cross-reference citations in answer against retrieved_contexts.

    Populates citation_errors for unverified citations.
    """
    return {
        "citations_verified": False,
        "citation_errors": [],
    }


def fallback_node(state: AgentState) -> dict:
    """Graceful rejection for out-of-scope, exhausted retries, or
    unverifiable citations. Sets fallback_reason for diagnostics."""
    return {
        "fallback_reason": "",
    }


# ── Conditional routing ─────────────────────────────────────────────────


def route_after_intake(state: AgentState) -> str:
    """Route after intake_analyzer.

    Returns:
        "fallback_node" — out of scope OR retry_count exhausted.
        "request_clarification" — in scope but missing jurisdiction or tenancy_type.
        "query_rewriter" — all slots filled, proceed to retrieval pipeline.
    """
    if not state["in_scope"] or state["retry_count"] >= 3:
        return "fallback_node"
    if not state["jurisdiction"] or not state["tenancy_type"]:
        return "request_clarification"
    return "query_rewriter"


def route_after_verify(state: AgentState) -> str:
    """Route after citation_verifier.

    Returns:
        "fallback_node" — complex case OR citations not verifiable.
        "__end__" — citations passed verification.
    """
    if state["is_complex_case"] or not state["citations_verified"]:
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
    workflow.add_edge("rag_retriever", "legal_reasoner")
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
