"""Offline unit tests for the LLM provider abstraction (no network).

Covers provider selection via LLM_PROVIDER, DeepSeek request/response
behaviour, Bedrock Converse formatting/parsing, and configuration errors.
All clients are mocked or injected — normal pytest never needs AWS,
DeepSeek, or network access.

The provider module performs no load_dotenv() and reads os.environ only,
so the autouse clean_env fixture keeps every test hermetic regardless of
import order.
"""

from __future__ import annotations

import sys
from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    """Hermetic environment: remove every provider-relevant variable."""
    for var in (
        "LLM_PROVIDER",
        "DEEPSEEK_API_KEY",
        "LLM_MODEL_ID",
        "AWS_REGION",
        "AWS_DEFAULT_REGION",
        "BEDROCK_MODEL_ID",
        "BEDROCK_TEMPERATURE",
        "BEDROCK_MAX_TOKENS",
    ):
        monkeypatch.delenv(var, raising=False)


def _openai_response(content):
    response = MagicMock()
    response.choices = [MagicMock()]
    response.choices[0].message.content = content
    return response


# ── DeepSeekLLMProvider ────────────────────────────────────────────────


class TestDeepSeekProvider:
    def test_missing_api_key_raises(self):
        from src.rag.generation.llm_provider import DeepSeekLLMProvider

        with pytest.raises(ValueError, match="DEEPSEEK_API_KEY"):
            DeepSeekLLMProvider()

    @patch("openai.OpenAI")
    def test_request_shape_preserved(self, mock_openai, monkeypatch):
        from src.rag.generation.llm_provider import DeepSeekLLMProvider

        monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
        client = mock_openai.return_value
        client.chat.completions.create.return_value = _openai_response("ok")

        messages = [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "hello"},
        ]
        result = DeepSeekLLMProvider().generate(messages)

        assert result == "ok"
        mock_openai.assert_called_once_with(api_key="test-key", base_url="https://api.deepseek.com")
        kwargs = client.chat.completions.create.call_args.kwargs
        assert kwargs["model"] == "deepseek-chat"
        assert kwargs["messages"] == messages
        assert kwargs["temperature"] == 0.0
        assert "max_tokens" not in kwargs

    @patch("openai.OpenAI")
    def test_llm_model_id_env_respected(self, mock_openai, monkeypatch):
        from src.rag.generation.llm_provider import DeepSeekLLMProvider

        monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
        monkeypatch.setenv("LLM_MODEL_ID", "deepseek-reasoner")
        client = mock_openai.return_value
        client.chat.completions.create.return_value = _openai_response("ok")

        DeepSeekLLMProvider().generate([{"role": "user", "content": "hi"}])

        assert client.chat.completions.create.call_args.kwargs["model"] == "deepseek-reasoner"

    @patch("openai.OpenAI")
    def test_explicit_temperature_and_max_tokens_forwarded(self, mock_openai, monkeypatch):
        from src.rag.generation.llm_provider import DeepSeekLLMProvider

        monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
        client = mock_openai.return_value
        client.chat.completions.create.return_value = _openai_response("ok")

        DeepSeekLLMProvider().generate(
            [{"role": "user", "content": "hi"}], temperature=0.7, max_tokens=128
        )

        kwargs = client.chat.completions.create.call_args.kwargs
        assert kwargs["temperature"] == 0.7
        assert kwargs["max_tokens"] == 128

    @patch("openai.OpenAI")
    def test_none_content_returns_empty_string(self, mock_openai, monkeypatch):
        from src.rag.generation.llm_provider import DeepSeekLLMProvider

        monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
        client = mock_openai.return_value
        client.chat.completions.create.return_value = _openai_response(None)

        assert DeepSeekLLMProvider().generate([{"role": "user", "content": "hi"}]) == ""

    @patch("openai.OpenAI")
    @pytest.mark.parametrize(
        ("kwargs", "match"),
        [
            ({"temperature": -0.1}, "temperature"),
            ({"temperature": float("nan")}, "temperature"),
            ({"temperature": "hot"}, "temperature"),
            ({"max_tokens": 0}, "max_tokens"),
            ({"max_tokens": -5}, "max_tokens"),
            ({"max_tokens": 1.5}, "max_tokens"),
        ],
    )
    def test_invalid_explicit_inference_args_raise(self, mock_openai, monkeypatch, kwargs, match):
        from src.rag.generation.llm_provider import DeepSeekLLMProvider

        monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
        client = mock_openai.return_value
        provider = DeepSeekLLMProvider()
        with pytest.raises(ValueError, match=match):
            provider.generate([{"role": "user", "content": "hi"}], **kwargs)
        client.chat.completions.create.assert_not_called()


# ── Converse payload conversion / parsing ─────────────────────────────


class TestConversePayload:
    def test_system_messages_become_system_blocks(self):
        from src.rag.generation.llm_provider import _to_converse_payload

        system, msgs = _to_converse_payload(
            [
                {"role": "system", "content": "rules"},
                {"role": "user", "content": "question"},
            ]
        )
        assert system == [{"text": "rules"}]
        assert msgs == [{"role": "user", "content": [{"text": "question"}]}]

    def test_multiple_system_messages_preserve_order(self):
        from src.rag.generation.llm_provider import _to_converse_payload

        system, _ = _to_converse_payload(
            [
                {"role": "system", "content": "first"},
                {"role": "system", "content": "second"},
                {"role": "user", "content": "q"},
            ]
        )
        assert system == [{"text": "first"}, {"text": "second"}]

    def test_user_assistant_order_preserved(self):
        from src.rag.generation.llm_provider import _to_converse_payload

        _, msgs = _to_converse_payload(
            [
                {"role": "user", "content": "q1"},
                {"role": "assistant", "content": "a1"},
                {"role": "user", "content": "q2"},
            ]
        )
        assert [m["role"] for m in msgs] == ["user", "assistant", "user"]
        assert [m["content"][0]["text"] for m in msgs] == ["q1", "a1", "q2"]

    def test_unsupported_role_raises(self):
        from src.rag.generation.llm_provider import _to_converse_payload

        with pytest.raises(ValueError, match="Unsupported message role"):
            _to_converse_payload([{"role": "tool", "content": "x"}])

    @pytest.mark.parametrize(
        "message",
        [
            {"role": "user"},
            {"role": "user", "content": None},
            {"role": "user", "content": 123},
        ],
    )
    def test_non_string_content_raises(self, message):
        from src.rag.generation.llm_provider import _to_converse_payload

        with pytest.raises(ValueError, match="content must be a string"):
            _to_converse_payload([message])


class TestParseConverseText:
    def test_single_text_block(self):
        from src.rag.generation.llm_provider import _parse_converse_text

        response = {"output": {"message": {"content": [{"text": "answer"}]}}}
        assert _parse_converse_text(response) == "answer"

    def test_multiple_text_blocks_joined_in_order(self):
        from src.rag.generation.llm_provider import _parse_converse_text

        response = {"output": {"message": {"content": [{"text": "part 1"}, {"text": "part 2"}]}}}
        assert _parse_converse_text(response) == "part 1\npart 2"

    def test_non_text_blocks_ignored(self):
        from src.rag.generation.llm_provider import _parse_converse_text

        response = {
            "output": {"message": {"content": [{"toolUse": {"name": "x"}}, {"text": "kept"}]}}
        }
        assert _parse_converse_text(response) == "kept"

    def test_missing_output_raises(self):
        from src.rag.generation.llm_provider import _parse_converse_text

        with pytest.raises(ValueError, match="Malformed Bedrock Converse response"):
            _parse_converse_text({})

    def test_content_not_a_list_raises(self):
        from src.rag.generation.llm_provider import _parse_converse_text

        response = {"output": {"message": {"content": "oops"}}}
        with pytest.raises(ValueError, match="Malformed Bedrock Converse response"):
            _parse_converse_text(response)

    def test_empty_content_raises(self):
        from src.rag.generation.llm_provider import _parse_converse_text

        response = {"output": {"message": {"content": []}}}
        with pytest.raises(ValueError, match="no non-empty text content blocks"):
            _parse_converse_text(response)

    def test_empty_text_block_raises(self):
        from src.rag.generation.llm_provider import _parse_converse_text

        response = {"output": {"message": {"content": [{"text": ""}]}}}
        with pytest.raises(ValueError, match="no non-empty text content blocks"):
            _parse_converse_text(response)

    def test_whitespace_only_text_block_raises(self):
        from src.rag.generation.llm_provider import _parse_converse_text

        response = {"output": {"message": {"content": [{"text": "   \n\t"}]}}}
        with pytest.raises(ValueError, match="no non-empty text content blocks"):
            _parse_converse_text(response)


# ── BedrockLLMProvider ─────────────────────────────────────────────────


def _bedrock_env(monkeypatch):
    monkeypatch.setenv("AWS_REGION", "ap-southeast-2")
    monkeypatch.setenv("BEDROCK_MODEL_ID", "test.model-id:0")


def _converse_response(*texts):
    return {"output": {"message": {"content": [{"text": t} for t in texts]}}}


class TestBedrockProvider:
    def test_missing_region_raises(self, monkeypatch):
        from src.rag.generation.llm_provider import BedrockLLMProvider

        monkeypatch.setenv("BEDROCK_MODEL_ID", "test.model-id:0")
        with pytest.raises(ValueError, match="AWS_REGION"):
            BedrockLLMProvider()

    def test_whitespace_region_raises(self, monkeypatch):
        from src.rag.generation.llm_provider import BedrockLLMProvider

        monkeypatch.setenv("AWS_REGION", "   ")
        monkeypatch.setenv("BEDROCK_MODEL_ID", "test.model-id:0")
        with pytest.raises(ValueError, match="AWS_REGION"):
            BedrockLLMProvider()

    def test_missing_model_id_raises(self, monkeypatch):
        from src.rag.generation.llm_provider import BedrockLLMProvider

        monkeypatch.setenv("AWS_REGION", "ap-southeast-2")
        with pytest.raises(ValueError, match="BEDROCK_MODEL_ID"):
            BedrockLLMProvider()

    def test_whitespace_model_id_raises(self, monkeypatch):
        from src.rag.generation.llm_provider import BedrockLLMProvider

        monkeypatch.setenv("AWS_REGION", "ap-southeast-2")
        monkeypatch.setenv("BEDROCK_MODEL_ID", "   ")
        with pytest.raises(ValueError, match="BEDROCK_MODEL_ID"):
            BedrockLLMProvider()

    def test_aws_default_region_accepted(self, monkeypatch):
        from src.rag.generation.llm_provider import BedrockLLMProvider

        monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
        monkeypatch.setenv("BEDROCK_MODEL_ID", "test.model-id:0")
        assert BedrockLLMProvider()._region == "us-east-1"

    def test_no_client_created_at_construction(self, monkeypatch):
        from src.rag.generation.llm_provider import BedrockLLMProvider

        _bedrock_env(monkeypatch)
        assert BedrockLLMProvider()._client is None

    def test_lazy_client_uses_configured_region(self, monkeypatch):
        from src.rag.generation.llm_provider import BedrockLLMProvider

        _bedrock_env(monkeypatch)
        fake_boto3 = MagicMock()
        monkeypatch.setitem(sys.modules, "boto3", fake_boto3)
        provider = BedrockLLMProvider()
        provider._get_client()
        fake_boto3.client.assert_called_once_with("bedrock-runtime", region_name="ap-southeast-2")

    def test_converse_request_formatting(self, monkeypatch):
        from src.rag.generation.llm_provider import BedrockLLMProvider

        _bedrock_env(monkeypatch)
        fake = MagicMock()
        fake.converse.return_value = _converse_response("answer")
        provider = BedrockLLMProvider(client=fake)

        result = provider.generate(
            [
                {"role": "system", "content": "sys prompt"},
                {"role": "user", "content": "user prompt"},
            ]
        )

        assert result == "answer"
        kwargs = fake.converse.call_args.kwargs
        assert kwargs["modelId"] == "test.model-id:0"
        assert kwargs["system"] == [{"text": "sys prompt"}]
        assert kwargs["messages"] == [{"role": "user", "content": [{"text": "user prompt"}]}]
        assert "inferenceConfig" not in kwargs

    def test_no_system_key_when_no_system_message(self, monkeypatch):
        from src.rag.generation.llm_provider import BedrockLLMProvider

        _bedrock_env(monkeypatch)
        fake = MagicMock()
        fake.converse.return_value = _converse_response("ok")
        BedrockLLMProvider(client=fake).generate([{"role": "user", "content": "q"}])
        assert "system" not in fake.converse.call_args.kwargs

    def test_only_system_messages_raises(self, monkeypatch):
        from src.rag.generation.llm_provider import BedrockLLMProvider

        _bedrock_env(monkeypatch)
        provider = BedrockLLMProvider(client=MagicMock())
        with pytest.raises(ValueError, match="at least one user or assistant message"):
            provider.generate([{"role": "system", "content": "only sys"}])

    def test_explicit_temperature_and_max_tokens_mapping(self, monkeypatch):
        from src.rag.generation.llm_provider import BedrockLLMProvider

        _bedrock_env(monkeypatch)
        fake = MagicMock()
        fake.converse.return_value = _converse_response("ok")
        BedrockLLMProvider(client=fake).generate(
            [{"role": "user", "content": "q"}], temperature=0.3, max_tokens=512
        )
        assert fake.converse.call_args.kwargs["inferenceConfig"] == {
            "temperature": 0.3,
            "maxTokens": 512,
        }

    def test_env_temperature_and_max_tokens_defaults(self, monkeypatch):
        from src.rag.generation.llm_provider import BedrockLLMProvider

        _bedrock_env(monkeypatch)
        monkeypatch.setenv("BEDROCK_TEMPERATURE", "0.2")
        monkeypatch.setenv("BEDROCK_MAX_TOKENS", "1024")
        fake = MagicMock()
        fake.converse.return_value = _converse_response("ok")
        BedrockLLMProvider(client=fake).generate([{"role": "user", "content": "q"}])
        assert fake.converse.call_args.kwargs["inferenceConfig"] == {
            "temperature": 0.2,
            "maxTokens": 1024,
        }

    def test_explicit_args_override_env(self, monkeypatch):
        from src.rag.generation.llm_provider import BedrockLLMProvider

        _bedrock_env(monkeypatch)
        monkeypatch.setenv("BEDROCK_TEMPERATURE", "0.2")
        monkeypatch.setenv("BEDROCK_MAX_TOKENS", "1024")
        fake = MagicMock()
        fake.converse.return_value = _converse_response("ok")
        BedrockLLMProvider(client=fake).generate(
            [{"role": "user", "content": "q"}], temperature=0.9, max_tokens=64
        )
        assert fake.converse.call_args.kwargs["inferenceConfig"] == {
            "temperature": 0.9,
            "maxTokens": 64,
        }

    def test_invalid_temperature_env_raises(self, monkeypatch):
        from src.rag.generation.llm_provider import BedrockLLMProvider

        _bedrock_env(monkeypatch)
        monkeypatch.setenv("BEDROCK_TEMPERATURE", "hot")
        with pytest.raises(ValueError, match="BEDROCK_TEMPERATURE"):
            BedrockLLMProvider()

    @pytest.mark.parametrize("value", ["-0.5", "nan", "inf"])
    def test_negative_or_nonfinite_temperature_env_raises(self, monkeypatch, value):
        from src.rag.generation.llm_provider import BedrockLLMProvider

        _bedrock_env(monkeypatch)
        monkeypatch.setenv("BEDROCK_TEMPERATURE", value)
        with pytest.raises(ValueError, match="BEDROCK_TEMPERATURE"):
            BedrockLLMProvider()

    def test_invalid_max_tokens_env_raises(self, monkeypatch):
        from src.rag.generation.llm_provider import BedrockLLMProvider

        _bedrock_env(monkeypatch)
        monkeypatch.setenv("BEDROCK_MAX_TOKENS", "lots")
        with pytest.raises(ValueError, match="BEDROCK_MAX_TOKENS"):
            BedrockLLMProvider()

    @pytest.mark.parametrize("value", ["0", "-20"])
    def test_nonpositive_max_tokens_env_raises(self, monkeypatch, value):
        from src.rag.generation.llm_provider import BedrockLLMProvider

        _bedrock_env(monkeypatch)
        monkeypatch.setenv("BEDROCK_MAX_TOKENS", value)
        with pytest.raises(ValueError, match="BEDROCK_MAX_TOKENS"):
            BedrockLLMProvider()

    @pytest.mark.parametrize("temperature", [-0.1, float("nan"), float("inf"), "hot", True])
    def test_invalid_explicit_temperature_raises(self, monkeypatch, temperature):
        from src.rag.generation.llm_provider import BedrockLLMProvider

        _bedrock_env(monkeypatch)
        fake = MagicMock()
        provider = BedrockLLMProvider(client=fake)
        with pytest.raises(ValueError, match="temperature"):
            provider.generate([{"role": "user", "content": "q"}], temperature=temperature)
        fake.converse.assert_not_called()

    @pytest.mark.parametrize("max_tokens", [0, -5, 1.5, "many", True])
    def test_invalid_explicit_max_tokens_raises(self, monkeypatch, max_tokens):
        from src.rag.generation.llm_provider import BedrockLLMProvider

        _bedrock_env(monkeypatch)
        fake = MagicMock()
        provider = BedrockLLMProvider(client=fake)
        with pytest.raises(ValueError, match="max_tokens"):
            provider.generate([{"role": "user", "content": "q"}], max_tokens=max_tokens)
        fake.converse.assert_not_called()

    def test_boto3_missing_raises_clear_error(self, monkeypatch):
        from src.rag.generation.llm_provider import BedrockLLMProvider

        _bedrock_env(monkeypatch)
        monkeypatch.setitem(sys.modules, "boto3", None)
        provider = BedrockLLMProvider()
        with pytest.raises(RuntimeError, match="boto3 is required") as excinfo:
            provider.generate([{"role": "user", "content": "q"}])
        assert isinstance(excinfo.value.__cause__, ImportError)

    def test_client_creation_error_wrapped_with_cause(self, monkeypatch):
        from botocore.exceptions import NoRegionError

        from src.rag.generation.llm_provider import BedrockLLMProvider

        _bedrock_env(monkeypatch)
        original = NoRegionError()
        fake_boto3 = MagicMock()
        fake_boto3.client.side_effect = original
        monkeypatch.setitem(sys.modules, "boto3", fake_boto3)
        provider = BedrockLLMProvider()
        with pytest.raises(
            RuntimeError, match="Failed to create Bedrock runtime client"
        ) as excinfo:
            provider.generate([{"role": "user", "content": "q"}])
        assert excinfo.value.__cause__ is original

    def test_multiple_text_blocks_joined(self, monkeypatch):
        from src.rag.generation.llm_provider import BedrockLLMProvider

        _bedrock_env(monkeypatch)
        fake = MagicMock()
        fake.converse.return_value = _converse_response("part 1", "part 2")
        provider = BedrockLLMProvider(client=fake)
        assert provider.generate([{"role": "user", "content": "q"}]) == "part 1\npart 2"

    def test_malformed_response_raises(self, monkeypatch):
        from src.rag.generation.llm_provider import BedrockLLMProvider

        _bedrock_env(monkeypatch)
        fake = MagicMock()
        fake.converse.return_value = {"unexpected": True}
        provider = BedrockLLMProvider(client=fake)
        with pytest.raises(ValueError, match="Malformed Bedrock Converse response"):
            provider.generate([{"role": "user", "content": "q"}])

    def test_aws_error_preserved_as_cause(self, monkeypatch):
        from botocore.exceptions import ClientError

        from src.rag.generation.llm_provider import BedrockLLMProvider

        _bedrock_env(monkeypatch)
        original = ClientError(
            {"Error": {"Code": "AccessDeniedException", "Message": "denied"}}, "Converse"
        )
        fake = MagicMock()
        fake.converse.side_effect = original
        provider = BedrockLLMProvider(client=fake)
        with pytest.raises(RuntimeError, match="Bedrock Converse request failed") as excinfo:
            provider.generate([{"role": "user", "content": "q"}])
        assert excinfo.value.__cause__ is original


# ── get_llm_provider factory ───────────────────────────────────────────


class TestGetLLMProvider:
    def test_absent_llm_provider_defaults_to_deepseek(self, monkeypatch):
        from src.rag.generation.llm_provider import DeepSeekLLMProvider, get_llm_provider

        monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
        assert isinstance(get_llm_provider(), DeepSeekLLMProvider)

    def test_blank_llm_provider_defaults_to_deepseek(self, monkeypatch):
        from src.rag.generation.llm_provider import DeepSeekLLMProvider, get_llm_provider

        monkeypatch.setenv("LLM_PROVIDER", "   ")
        monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
        assert isinstance(get_llm_provider(), DeepSeekLLMProvider)

    def test_explicit_deepseek(self, monkeypatch):
        from src.rag.generation.llm_provider import DeepSeekLLMProvider, get_llm_provider

        monkeypatch.setenv("LLM_PROVIDER", "deepseek")
        monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
        assert isinstance(get_llm_provider(), DeepSeekLLMProvider)

    def test_bedrock_selected_without_client_creation(self, monkeypatch):
        from src.rag.generation.llm_provider import BedrockLLMProvider, get_llm_provider

        monkeypatch.setenv("LLM_PROVIDER", "bedrock")
        monkeypatch.setenv("AWS_REGION", "ap-southeast-2")
        monkeypatch.setenv("BEDROCK_MODEL_ID", "test.model-id:0")
        provider = get_llm_provider()
        assert isinstance(provider, BedrockLLMProvider)
        assert provider._client is None

    def test_provider_name_is_case_insensitive(self, monkeypatch):
        from src.rag.generation.llm_provider import BedrockLLMProvider, get_llm_provider

        monkeypatch.setenv("LLM_PROVIDER", "BEDROCK")
        monkeypatch.setenv("AWS_REGION", "ap-southeast-2")
        monkeypatch.setenv("BEDROCK_MODEL_ID", "test.model-id:0")
        assert isinstance(get_llm_provider(), BedrockLLMProvider)

    def test_unknown_provider_raises(self, monkeypatch):
        from src.rag.generation.llm_provider import get_llm_provider

        monkeypatch.setenv("LLM_PROVIDER", "gemini")
        with pytest.raises(ValueError, match="Unknown LLM_PROVIDER"):
            get_llm_provider()

    def test_deepseek_path_never_constructs_bedrock(self, monkeypatch):
        from src.rag.generation import llm_provider

        monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
        with patch.object(llm_provider, "BedrockLLMProvider") as bedrock_cls:
            provider = llm_provider.get_llm_provider()
        assert isinstance(provider, llm_provider.DeepSeekLLMProvider)
        bedrock_cls.assert_not_called()

    def test_deepseek_path_works_without_boto3(self, monkeypatch):
        from src.rag.generation.llm_provider import DeepSeekLLMProvider, get_llm_provider

        monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
        monkeypatch.setitem(sys.modules, "boto3", None)
        assert isinstance(get_llm_provider(), DeepSeekLLMProvider)
