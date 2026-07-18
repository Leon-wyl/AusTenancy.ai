"""Interactive CLI (REPL) for the tenancy compliance agent.

Usage:
    python -m src.agent.cli
"""

EXIT_COMMANDS = frozenset({"exit", "quit", "q"})
CLARIFICATION_PREFIX = "To provide an accurate legal answer"


def _is_exit(text: str) -> bool:
    return text.strip().lower() in EXIT_COMMANDS


def _merge_clarification(prev_question: str, reply: str) -> str:
    return f"{prev_question} {reply}".strip()


def _msg_content(msg: object) -> str:
    if isinstance(msg, dict):
        return msg.get("content", "") or ""
    return getattr(msg, "content", "") or ""


def _extract_reply(result: dict, prev_msg_count: int) -> tuple[str, bool]:
    """Return (reply_text, awaiting_clarification) for the turn just executed.

    A new assistant message this turn means clarification or fallback output;
    otherwise the fresh answer lives in result["answer"]. Never trusts
    ``answer``/``fallback_reason`` freshness (checkpointer keeps stale values).
    """
    messages = result.get("messages", [])
    if len(messages) > prev_msg_count + 1:
        text = _msg_content(messages[-1])
        return text, text.startswith(CLARIFICATION_PREFIX)
    return result.get("answer", ""), False
