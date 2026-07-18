"""Interactive CLI (REPL) for the tenancy compliance agent.

Usage:
    python -m src.agent.cli
"""

import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from dotenv import load_dotenv
from langgraph.checkpoint.memory import MemorySaver

from src.agent.graph_skeleton import build_graph

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


def main() -> None:
    load_dotenv()
    graph = build_graph().compile(checkpointer=MemorySaver())
    config = {"configurable": {"thread_id": str(uuid.uuid4())}}

    print("AusTenancy.ai — tenancy compliance agent (VIC / NSW)")
    print("Type your question, or 'exit' to quit.\n")

    awaiting_clarification = False
    last_question = ""
    msg_count = 0

    while True:
        try:
            user_input = input("You: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nBye.")
            break
        if not user_input:
            continue
        if _is_exit(user_input):
            print("Bye.")
            break

        if awaiting_clarification and last_question:
            query = _merge_clarification(last_question, user_input)
        else:
            query = user_input
            last_question = user_input

        try:
            result = graph.invoke({"messages": [{"role": "user", "content": query}]}, config)
        except KeyboardInterrupt:
            print("\n(interrupted)")
            continue
        except Exception as e:  # noqa: BLE001
            print(f"Error: {e}")
            continue

        reply, awaiting_clarification = _extract_reply(result, msg_count)
        msg_count = len(result.get("messages", []))
        print(f"\nAgent: {reply}\n")


if __name__ == "__main__":
    main()
