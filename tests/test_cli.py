"""Tests for the interactive CLI REPL."""

import types

from src.agent.cli import (
    CLARIFICATION_PREFIX,
    _extract_reply,
    _is_exit,
    _merge_clarification,
    _render_status,
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


class FakeCompiledGraph:
    def __init__(self, results, events=None):
        self._results = list(results)
        self._events = list(events) if events is not None else [[] for _ in self._results]
        self.invocations = []
        self._current = None

    def stream(self, payload, config, stream_mode="updates"):
        def _gen():
            self.invocations.append(payload)
            self._current = self._results.pop(0)
            yield from self._events.pop(0)

        return _gen()

    def get_state(self, config):
        return types.SimpleNamespace(values=self._current if self._current is not None else {})


class FakeWorkflow:
    def __init__(self, compiled):
        self._compiled = compiled

    def compile(self, checkpointer=None):
        return self._compiled


def _run_main(monkeypatch, fake_graph, inputs):
    from src.agent import cli

    monkeypatch.setattr(cli, "build_graph", lambda: FakeWorkflow(fake_graph))
    monkeypatch.setattr(cli, "load_dotenv", lambda: None)
    input_iter = iter(inputs)
    monkeypatch.setattr("builtins.input", lambda _prompt="": next(input_iter))
    cli.main()


class TestRenderStatus:
    def test_intake_with_jurisdiction(self):
        assert _render_status("intake_analyzer", {"jurisdiction": "NSW"}) == [
            "  ✓ Jurisdiction: NSW"
        ]

    def test_intake_without_jurisdiction(self):
        assert _render_status("intake_analyzer", {"jurisdiction": ""}) == []

    def test_query_rewriter_counts(self):
        assert _render_status("query_rewriter", {"rewritten_queries": ["a", "b", "c"]}) == [
            "  ⟳ Rewrote into 3 search queries"
        ]

    def test_retriever_with_contexts_adds_generating_line(self):
        lines = _render_status("rag_retriever", {"retrieved_contexts": [{}, {}]})
        assert lines == [
            "  ⟳ Retrieved 2 statutory provisions",
            "  ⟳ Generating legal analysis…",
        ]

    def test_retriever_empty_no_generating_line(self):
        assert _render_status("rag_retriever", {"retrieved_contexts": []}) == [
            "  ⟳ Retrieved 0 statutory provisions"
        ]

    def test_citation_verifier_dedup_count(self):
        answer = (
            "A [VIC RTA 1997 Sec 44] and again [VIC RTA 1997 Sec 44] plus [VIC RTA 1997 Sec 91ZM]"
        )
        assert _render_status("citation_verifier", {"answer": answer}) == [
            "  ✓ 2 citations verified"
        ]

    def test_citation_verifier_zero_silent(self):
        assert _render_status("citation_verifier", {"answer": "no citations here"}) == []

    def test_unknown_node_silent(self):
        assert _render_status("legal_reasoner", {"answer": "x"}) == []


class TestMainLoop:
    def test_prints_answer_and_exits(self, monkeypatch, capsys):
        fake = FakeCompiledGraph(
            [
                {
                    "messages": [{"role": "user", "content": "q"}],
                    "answer": "Answer [VIC RTA 1997 Sec 44]",
                }
            ]
        )
        _run_main(monkeypatch, fake, ["Rent increase notice in VIC?", "exit"])
        out = capsys.readouterr().out
        assert "Answer [VIC RTA 1997 Sec 44]" in out

    def test_clarification_round_trip_merges_query(self, monkeypatch, capsys):
        clarification = CLARIFICATION_PREFIX + ". Please specify: state (e.g. VIC, NSW)."
        fake = FakeCompiledGraph(
            [
                {
                    "messages": [
                        {"role": "user", "content": "Can I be evicted?"},
                        {"role": "assistant", "content": clarification},
                    ],
                    "answer": "",
                },
                {
                    "messages": [
                        {"role": "user", "content": "Can I be evicted?"},
                        {"role": "assistant", "content": clarification},
                        {"role": "user", "content": "Can I be evicted? VIC"},
                    ],
                    "answer": "Yes, with notice [VIC RTA 1997 Sec 91ZM]",
                },
            ]
        )
        _run_main(monkeypatch, fake, ["Can I be evicted?", "VIC", "exit"])
        assert fake.invocations[1]["messages"][0]["content"] == "Can I be evicted? VIC"
        out = capsys.readouterr().out
        assert clarification in out
        assert "Yes, with notice [VIC RTA 1997 Sec 91ZM]" in out

    def test_empty_input_skipped_and_error_does_not_crash(self, monkeypatch, capsys):
        class ExplodingGraph:
            def stream(self, payload, config, stream_mode="updates"):
                raise RuntimeError("boom")

            def get_state(self, config):
                return types.SimpleNamespace(values={})

        _run_main(monkeypatch, ExplodingGraph(), ["", "some question", "quit"])
        out = capsys.readouterr().out
        assert "boom" in out

    def test_eof_exits_gracefully(self, monkeypatch, capsys):
        from src.agent import cli

        monkeypatch.setattr(cli, "build_graph", lambda: FakeWorkflow(FakeCompiledGraph([])))
        monkeypatch.setattr(cli, "load_dotenv", lambda: None)

        def raise_eof(_prompt=""):
            raise EOFError

        monkeypatch.setattr("builtins.input", raise_eof)
        cli.main()
        assert "Bye." in capsys.readouterr().out

    def test_error_between_clarification_and_reply_keeps_msg_count(self, monkeypatch, capsys):
        clarification = CLARIFICATION_PREFIX + ". Please specify: state (e.g. VIC, NSW)."

        class FlakyGraph:
            def __init__(self):
                self.calls = 0
                self.invocations = []
                self._messages = []
                self._answer = ""

            def stream(self, payload, config, stream_mode="updates"):
                self.calls += 1
                self.invocations.append(payload)
                self._messages.append(payload["messages"][0])
                if self.calls == 1:
                    self._messages.append({"role": "assistant", "content": clarification})
                    return iter([])
                if self.calls == 2:
                    raise RuntimeError("transient boom")
                self._answer = "Yes, with notice [VIC RTA 1997 Sec 91ZM]"
                return iter([])

            def get_state(self, config):
                return types.SimpleNamespace(
                    values={"messages": list(self._messages), "answer": self._answer}
                )

        fake = FlakyGraph()
        _run_main(monkeypatch, fake, ["Can I be evicted?", "VIC", "VIC", "exit"])
        out = capsys.readouterr().out
        assert "transient boom" in out
        assert fake.invocations[2]["messages"][0]["content"] == "Can I be evicted? VIC"
        assert "Yes, with notice [VIC RTA 1997 Sec 91ZM]" in out
        assert "\nAgent: Can I be evicted? VIC\n" not in out

    def test_keyboard_interrupt_during_invoke_continues(self, monkeypatch, capsys):
        class InterruptedThenAnswer:
            def __init__(self):
                self.calls = 0
                self._current = {}

            def stream(self, payload, config, stream_mode="updates"):
                self.calls += 1
                if self.calls == 1:
                    raise KeyboardInterrupt
                self._current = {
                    "messages": [{"role": "user", "content": "q"}],
                    "answer": "Answer [VIC RTA 1997 Sec 44]",
                }
                yield from []

            def get_state(self, config):
                return types.SimpleNamespace(values=self._current)

        _run_main(
            monkeypatch,
            InterruptedThenAnswer(),
            ["Rent increase in VIC?", "Rent increase in VIC?", "exit"],
        )
        out = capsys.readouterr().out
        assert "(interrupted)" in out
        assert "Answer [VIC RTA 1997 Sec 44]" in out

    def test_status_lines_printed_from_stream_events(self, monkeypatch, capsys):
        fake = FakeCompiledGraph(
            [
                {
                    "messages": [{"role": "user", "content": "q"}],
                    "answer": "Answer [NSW RTA 2010 Sec 41]",
                }
            ],
            events=[
                [
                    {"intake_analyzer": {"jurisdiction": "NSW", "in_scope": True}},
                    {"query_rewriter": {"rewritten_queries": ["a", "b", "c"]}},
                    {"rag_retriever": {"retrieved_contexts": [{}] * 10}},
                    {"legal_reasoner": {"answer": "Answer [NSW RTA 2010 Sec 41]"}},
                    {"citation_verifier": {"answer": "Answer [NSW RTA 2010 Sec 41]"}},
                ]
            ],
        )
        _run_main(monkeypatch, fake, ["Lease question in NSW?", "exit"])
        out = capsys.readouterr().out
        assert "  ✓ Jurisdiction: NSW" in out
        assert "  ⟳ Rewrote into 3 search queries" in out
        assert "  ⟳ Retrieved 10 statutory provisions" in out
        assert "  ⟳ Generating legal analysis…" in out
        assert "  ✓ 1 citations verified" in out
        assert "Answer [NSW RTA 2010 Sec 41]" in out

    def test_logging_quieted(self, monkeypatch):
        import logging as logging_mod

        fake = FakeCompiledGraph(
            [{"messages": [{"role": "user", "content": "q"}], "answer": "A [VIC RTA 1997 Sec 44]"}]
        )
        _run_main(monkeypatch, fake, ["exit"])
        assert logging_mod.getLogger().level == logging_mod.WARNING
        assert logging_mod.getLogger("langsmith").level == logging_mod.CRITICAL
