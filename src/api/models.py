"""Pydantic v2 models for the AusTenancy.ai agent API."""

from datetime import UTC, datetime
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, Field, field_validator


class AgentRequest(BaseModel):
    request_id: str = Field(default_factory=lambda: str(uuid4()))
    thread_id: str | None = None
    user_id: str | None = None
    conversation_id: str | None = None
    message_id: str | None = Field(default_factory=lambda: str(uuid4()))
    question: str = Field(min_length=1, max_length=4000)
    jurisdiction: str | None = Field(default=None, pattern=r"^(VIC|NSW)?$")
    api_version: str = Field(default="1.0", pattern=r"^\d+\.\d+$")

    @field_validator("question")
    @classmethod
    def strip_and_reject_empty(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("question must not be empty")
        return v


class AgentResponse(BaseModel):
    request_id: str
    status: Literal["success", "fallback", "clarification"]
    answer: str | None = None
    verified_citations: list[str] = Field(default_factory=list)
    citation_verified_rate: float | None = None
    clarification: str | None = None
    fallback_reason: str | None = None
    selected_jurisdiction: str | None = None
    latency_ms: float | None = None
    trace_id: str | None = None
    api_version: str = "1.0"
    generated_at: str = Field(
        default_factory=lambda: datetime.now(UTC).isoformat(),
    )
