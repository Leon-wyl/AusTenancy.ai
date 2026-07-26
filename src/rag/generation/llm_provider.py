"""LLM provider abstraction: DeepSeek (default) and AWS Bedrock Converse.

Providers accept a provider-neutral message format:
    [{"role": "system" | "user" | "assistant", "content": "<text>"}, ...]
and return plain generated text.

Selection is controlled by the LLM_PROVIDER environment variable:
    (unset or blank) | "deepseek" -> DeepSeekLLMProvider
    "bedrock"                     -> BedrockLLMProvider

This module reads configuration from os.environ only. It never loads
.env files (entry points such as generator.py and the CLI do that), never
mutates the environment, and creates no network clients at import time.
The DeepSeek path never imports boto3 or triggers AWS credential
discovery. Message content and credentials are never logged.
"""

import logging
import math
import os
from abc import ABC, abstractmethod

logger = logging.getLogger(__name__)


class LLMProvider(ABC):
    """Abstract interface for LLM backends."""

    @abstractmethod
    def generate(
        self,
        messages: list[dict],
        *,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> str: ...


def _validate_inference_args(temperature: float | None, max_tokens: int | None) -> None:
    """Validate explicit per-call inference parameters (None means unset)."""
    if temperature is not None:
        if isinstance(temperature, bool) or not isinstance(temperature, int | float):
            raise ValueError(f"temperature must be a number, got {type(temperature).__name__}")
        if not math.isfinite(temperature) or temperature < 0:
            raise ValueError(
                f"temperature must be a non-negative finite number, got {temperature!r}"
            )
    if max_tokens is not None:
        if isinstance(max_tokens, bool) or not isinstance(max_tokens, int):
            raise ValueError(f"max_tokens must be an integer, got {type(max_tokens).__name__}")
        if max_tokens <= 0:
            raise ValueError(f"max_tokens must be a positive integer, got {max_tokens!r}")


class DeepSeekLLMProvider(LLMProvider):
    """OpenAI-compatible SDK targeting DeepSeek's API endpoint."""

    def __init__(self, model: str | None = None):
        api_key = os.environ.get("DEEPSEEK_API_KEY", "")
        if not api_key:
            raise ValueError("DEEPSEEK_API_KEY is not set. Add it to .env or export it.")

        from openai import OpenAI

        self._client = OpenAI(
            api_key=api_key,
            base_url="https://api.deepseek.com",
        )
        self._model = model or os.environ.get("LLM_MODEL_ID", "deepseek-chat")

    def generate(
        self,
        messages: list[dict],
        *,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> str:
        _validate_inference_args(temperature, max_tokens)
        logger.info("Calling LLM (%s)...", self._model)
        kwargs: dict = {
            "model": self._model,
            "messages": messages,
            "temperature": temperature if temperature is not None else 0.0,
        }
        if max_tokens is not None:
            kwargs["max_tokens"] = max_tokens
        response = self._client.chat.completions.create(**kwargs)
        content = response.choices[0].message.content
        return content if content else ""


def _to_converse_payload(messages: list[dict]) -> tuple[list[dict], list[dict]]:
    """Split neutral messages into Converse (system blocks, message blocks).

    System messages become SystemContentBlocks; user/assistant messages
    become Converse messages with text content blocks. Order is preserved
    within each stream. Content must be a string.
    """
    system_blocks: list[dict] = []
    converse_messages: list[dict] = []
    for message in messages:
        role = message.get("role")
        content = message.get("content")
        if not isinstance(content, str):
            raise ValueError(
                f"Message content must be a string, got {type(content).__name__} for role {role!r}."
            )
        if role == "system":
            system_blocks.append({"text": content})
        elif role in ("user", "assistant"):
            converse_messages.append({"role": role, "content": [{"text": content}]})
        else:
            raise ValueError(
                f"Unsupported message role {role!r}. Valid roles: system, user, assistant."
            )
    return system_blocks, converse_messages


def _parse_converse_text(response: dict) -> str:
    """Extract generated text deterministically from a Converse response.

    Joins every non-empty text block in order with a newline. Non-text and
    empty/whitespace-only blocks are ignored. Raises ValueError for
    malformed responses or responses with no usable text.
    """
    try:
        blocks = response["output"]["message"]["content"]
    except (KeyError, TypeError) as exc:
        raise ValueError(
            "Malformed Bedrock Converse response: missing output.message.content"
        ) from exc
    if not isinstance(blocks, list):
        raise ValueError("Malformed Bedrock Converse response: content is not a list")
    texts = [
        block["text"]
        for block in blocks
        if isinstance(block, dict) and isinstance(block.get("text"), str) and block["text"].strip()
    ]
    if not texts:
        raise ValueError("Bedrock Converse response contained no non-empty text content blocks")
    return "\n".join(texts)


def _read_temperature_env(name: str) -> float | None:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return None
    try:
        value = float(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be a number, got {raw!r}") from exc
    if not math.isfinite(value) or value < 0:
        raise ValueError(f"{name} must be a non-negative finite number, got {raw!r}")
    return value


def _read_max_tokens_env(name: str) -> int | None:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return None
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer, got {raw!r}") from exc
    if value <= 0:
        raise ValueError(f"{name} must be a positive integer, got {raw!r}")
    return value


class BedrockLLMProvider(LLMProvider):
    """AWS Bedrock Runtime Converse API provider.

    Environment configuration:
        AWS_REGION or AWS_DEFAULT_REGION — required
        BEDROCK_MODEL_ID — required (never hardcoded)
        BEDROCK_TEMPERATURE — optional default temperature (>= 0, finite)
        BEDROCK_MAX_TOKENS — optional default max tokens (> 0)

    The boto3 client is created lazily on the first generate() call, so
    constructing this provider never triggers AWS credential discovery.
    Message content and credentials are never logged (model ID only).
    """

    def __init__(
        self,
        model_id: str | None = None,
        region: str | None = None,
        client=None,
    ):
        self._region = (
            (region or "").strip()
            or os.environ.get("AWS_REGION", "").strip()
            or os.environ.get("AWS_DEFAULT_REGION", "").strip()
        )
        if not self._region:
            raise ValueError(
                "AWS region is not set. Set AWS_REGION or AWS_DEFAULT_REGION to use Bedrock."
            )
        self._model_id = (model_id or "").strip() or os.environ.get("BEDROCK_MODEL_ID", "").strip()
        if not self._model_id:
            raise ValueError(
                "BEDROCK_MODEL_ID is not set. Set it to a Bedrock model or inference profile ID."
            )
        self._default_temperature = _read_temperature_env("BEDROCK_TEMPERATURE")
        self._default_max_tokens = _read_max_tokens_env("BEDROCK_MAX_TOKENS")
        self._client = client

    def _get_client(self):
        if self._client is None:
            try:
                import boto3
            except ImportError as exc:
                raise RuntimeError(
                    "boto3 is required for the Bedrock provider. "
                    "Install it with 'pip install boto3'."
                ) from exc

            from botocore.exceptions import BotoCoreError, ClientError

            try:
                self._client = boto3.client("bedrock-runtime", region_name=self._region)
            except (BotoCoreError, ClientError) as exc:
                raise RuntimeError(
                    f"Failed to create Bedrock runtime client for region {self._region}"
                ) from exc
        return self._client

    def generate(
        self,
        messages: list[dict],
        *,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> str:
        _validate_inference_args(temperature, max_tokens)
        system_blocks, converse_messages = _to_converse_payload(messages)
        if not converse_messages:
            raise ValueError("Bedrock Converse requires at least one user or assistant message.")

        inference_config: dict = {}
        resolved_temperature = temperature if temperature is not None else self._default_temperature
        if resolved_temperature is not None:
            inference_config["temperature"] = resolved_temperature
        resolved_max_tokens = max_tokens if max_tokens is not None else self._default_max_tokens
        if resolved_max_tokens is not None:
            inference_config["maxTokens"] = resolved_max_tokens

        kwargs: dict = {"modelId": self._model_id, "messages": converse_messages}
        if system_blocks:
            kwargs["system"] = system_blocks
        if inference_config:
            kwargs["inferenceConfig"] = inference_config

        client = self._get_client()
        logger.info("Calling LLM (bedrock:%s)...", self._model_id)

        from botocore.exceptions import BotoCoreError, ClientError

        try:
            response = client.converse(**kwargs)
        except (BotoCoreError, ClientError) as exc:
            raise RuntimeError(
                f"Bedrock Converse request failed for model {self._model_id}"
            ) from exc
        return _parse_converse_text(response)


def get_llm_provider() -> LLMProvider:
    """Select the LLM provider from the LLM_PROVIDER env var.

    Absent or blank LLM_PROVIDER selects DeepSeek (the default). Bedrock
    objects are constructed only on the bedrock path; the DeepSeek path
    never imports boto3 or triggers AWS credential discovery.
    """
    name = os.environ.get("LLM_PROVIDER", "").strip().lower()
    if not name or name == "deepseek":
        return DeepSeekLLMProvider()
    if name == "bedrock":
        return BedrockLLMProvider()
    raise ValueError(
        f"Unknown LLM_PROVIDER {name!r}. Valid values: 'deepseek', 'bedrock' "
        "(unset or blank defaults to 'deepseek')."
    )
