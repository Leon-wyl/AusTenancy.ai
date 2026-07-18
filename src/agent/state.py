from typing import Annotated, TypedDict

from langgraph.graph.message import add_messages


class AgentState(TypedDict):
    """State schema for the 7-node tenancy compliance agent.

    All values have safe defaults. Slots are populated across multiple
    graph invocations via LangGraph checkpointing.
    """

    messages: Annotated[list, add_messages]
    jurisdiction: str
    tenancy_type: str
    dispute_category: str
    rewritten_queries: list[str]
    retrieved_contexts: list[dict]
    answer: str
    retry_count: int
    is_complex_case: bool
    in_scope: bool
    citations_verified: bool
    fallback_reason: str
    citation_errors: list[str]


def create_initial_state() -> AgentState:
    return AgentState(
        messages=[],
        jurisdiction="",
        tenancy_type="",
        dispute_category="",
        rewritten_queries=[],
        retrieved_contexts=[],
        answer="",
        retry_count=0,
        is_complex_case=False,
        in_scope=True,
        citations_verified=False,
        fallback_reason="",
        citation_errors=[],
    )
