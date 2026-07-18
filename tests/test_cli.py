"""Tests for the interactive CLI REPL."""

from src.agent.cli import (
    CLARIFICATION_PREFIX,
    _extract_reply,
    _is_exit,
    _merge_clarification,
)
from src.agent.graph_skeleton import request_clarification


class TestIsExit:
    def test_exit_commands(self):
        for cmd in ("exit", "quit", "q", "EXIT", " Quit "):
            assert _is_exit(cmd) is True

    def test_non_exit_input(self):
        for text in ("explain", "", "exit now", "quiet"):
            assert _is_exit(text) is False


class TestMergeClarification:
    def test_question_first_reply_last(self):
        merged = _merge_clarification("Can I be evicted?", "VIC")
        assert merged == "Can I be evicted? VIC"

    def test_reply_last_wins_jurisdiction_detection(self):
        merged = _merge_clarification("What about NSW rules, actually?", "VIC")
        assert merged.endswith("VIC")


class TestExtractReply:
    def test_answer_path_no_assistant_message_appended(self):
        result = {
            "messages": [{"role": "user", "content": "q"}],
            "answer": "Answer [VIC RTA 1997 Sec 44]",
        }
        text, awaiting = _extract_reply(result, prev_msg_count=0)
        assert text == "Answer [VIC RTA 1997 Sec 44]"
        assert awaiting is False

    def test_clarification_path_sets_awaiting(self):
        clarification = CLARIFICATION_PREFIX + ". Please specify: state (e.g. VIC, NSW)."
        result = {
            "messages": [
                {"role": "user", "content": "q"},
                {"role": "assistant", "content": clarification},
            ],
            "answer": "",
        }
        text, awaiting = _extract_reply(result, prev_msg_count=0)
        assert text == clarification
        assert awaiting is True

    def test_fallback_path_not_awaiting(self):
        fallback = "I'm a tenancy law specialist and can't help with this topic."
        result = {
            "messages": [
                {"role": "user", "content": "recipe?"},
                {"role": "assistant", "content": fallback},
            ],
            "answer": "",
        }
        text, awaiting = _extract_reply(result, prev_msg_count=0)
        assert text == fallback
        assert awaiting is False

    def test_stale_answer_ignored_when_assistant_message_appended(self):
        fallback = "I couldn't find relevant statutory provisions for your question."
        result = {
            "messages": [
                {"role": "user", "content": "q1"},
                {"role": "assistant", "content": "old clarification"},
                {"role": "user", "content": "q2"},
                {"role": "assistant", "content": fallback},
            ],
            "answer": "stale answer from a previous turn",
        }
        text, awaiting = _extract_reply(result, prev_msg_count=2)
        assert text == fallback
        assert awaiting is False

    def test_none_content_treated_as_empty(self):
        result = {
            "messages": [
                {"role": "user", "content": "q"},
                {"role": "assistant", "content": None},
            ],
            "answer": "",
        }
        text, awaiting = _extract_reply(result, prev_msg_count=0)
        assert text == ""
        assert awaiting is False

    def test_message_object_content_supported(self):
        class FakeMessage:
            content = CLARIFICATION_PREFIX + ". Please specify: state."

        result = {"messages": [{"role": "user", "content": "q"}, FakeMessage()], "answer": ""}
        text, awaiting = _extract_reply(result, prev_msg_count=0)
        assert text == FakeMessage.content
        assert awaiting is True


class TestClarificationPrefixContract:
    def test_prefix_matches_graph_wording(self):
        message = request_clarification({})["messages"][0]["content"]
        assert message.startswith(CLARIFICATION_PREFIX)
