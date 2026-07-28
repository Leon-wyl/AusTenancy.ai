# Agent Runtime API Contract

**Version:** `1.0`
**Owner:** AusTenancy.ai Agent Runtime (FastAPI/Mangum)
**Consumer:** VicTenancy.app (NestJS CRUD API, Step 16)

This document is the canonical API contract for the Agent Runtime. Every field,
validation rule, and response status is derived from the implementation in
`src/api/models.py`, `src/api/handler.py`, and `scripts/ops/smoke_test.py`.

---

## Endpoints

| Method | Path | Auth (local) | Auth (deployed) | Description |
|--------|------|-------------|-----------------|-------------|
| `GET` | `/health` | NONE | NONE | Health check — no graph/LLM invoked |
| `POST` | `/api/agent/invoke` | NONE | AWS_IAM (SigV4) | Full LangGraph agent invocation |

### Local Access

```bash
uvicorn src.api.runtime:app --host 0.0.0.0 --port 8080
```

- Health: `GET http://localhost:8080/health`
- Invoke: `POST http://localhost:8080/api/agent/invoke`

### Lambda Emulation Mode

The Runtime Interface Emulator on port 9000 (`_LAMBDA_SERVER_PORT=9000`) is an
API Gateway v2 event test contract. It is not the application integration endpoint.

### Deployed Access

- API Gateway HTTP API v2, `ap-southeast-2`
- `GET /health` — public, no auth
- `POST /api/agent/invoke` — SigV4-signed (`execute-api` service, IAM principal)
- Accounts, URLs, and IAM principals are configuration placeholders and must not
  be hard-coded into consumer documentation.

---

## Health Check — `GET /health`

### Response

```json
{
  "status": "healthy",
  "version": "0.1.0",
  "api_version": "1.0"
}
```

| Field | Type | Value |
|-------|------|-------|
| `status` | `str` | Always `"healthy"` |
| `version` | `str` | Always `"0.1.0"` |
| `api_version` | `str` | Always `"1.0"` |

The health endpoint does not invoke the graph, retrieval, generation, or citation
verification.

---

## Agent Invocation — `POST /api/agent/invoke`

### Request (`AgentRequest`)

| Field | Type | Required | Default | Validation |
|-------|------|----------|---------|------------|
| `question` | `str` | **Yes** | — | `min_length=1`, `max_length=4000`; stripped of whitespace; rejects empty-after-strip |
| `jurisdiction` | `str \| None` | No | `None` | `^(VIC\|NSW)?$` (exact case-sensitive match only) |
| `api_version` | `str` | No | `"1.0"` | `^\d+\.\d+$` |
| `request_id` | `str` | No | `uuid4()` (auto) | — |
| `thread_id` | `str \| None` | No | `None` | For future multi-turn support |
| `user_id` | `str \| None` | No | `None` | — |
| `conversation_id` | `str \| None` | No | `None` | — |
| `message_id` | `str \| None` | No | `uuid4()` (auto) | — |

### Example Request

```json
{
  "question": "What notice period is required for unpaid rent in Victoria?",
  "jurisdiction": "VIC"
}
```

### Response (`AgentResponse`)

| Field | Type | Required | Default | Description |
|-------|------|----------|---------|-------------|
| `request_id` | `str` | Yes | — | Echoed from request |
| `status` | `"success" \| "fallback" \| "clarification"` | Yes | — | Agent outcome |
| `answer` | `str \| None` | No | `None` | Final answer text (includes legal disclaimer) |
| `verified_citations` | `list[str]` | No | `[]` | Verified citation strings |
| `citation_verified_rate` | `float \| None` | No | `None` | Proportion of citations verified |
| `clarification` | `str \| None` | No | `None` | Set only when `status == "clarification"` |
| `fallback_reason` | `str \| None` | No | `None` | Set only when `status == "fallback"` |
| `selected_jurisdiction` | `str \| None` | No | `None` | Detected jurisdiction |
| `latency_ms` | `float \| None` | No | `None` | Wall-clock latency |
| `trace_id` | `str \| None` | No | `None` | For future observability |
| `api_version` | `str` | Yes | `"1.0"` | Always `"1.0"` |
| `generated_at` | `str` | Yes | `datetime.now(UTC).isoformat()` | ISO-8601 timestamp |

### Conditional Field Behavior

- `answer`: `None` when `status == "clarification"`; otherwise contains the
  extracted answer with legal disclaimer appended.
- `clarification`: Set to the clarification text when `status == "clarification"`;
  `None` otherwise.
- `fallback_reason`: Set only when `status == "fallback"`.
- `verified_citations`, `citation_verified_rate`, `selected_jurisdiction`,
  `latency_ms`: Populated from `generate_run_summary()` output; `None`/empty on
  graph errors.

### Never Exposed

Internal diagnostics are explicitly excluded from responses: rewritten queries,
retrieved contexts, unverified citations, top retrieved provisions, and raw LLM
prompts.

---

## Error Responses

### 422 — Validation Error

```json
HTTP 422
{
  "detail": [
    {
      "type": "string_type",
      "loc": ["body", "question"],
      "msg": "Field required",
      "input": {}
    }
  ]
}
```

Triggered by Pydantic validation on the request body. Common causes: missing
`question`, empty `question` after stripping, invalid `jurisdiction` value,
or malformed `api_version`.

### 500 — Agent Invocation Failure

```json
HTTP 500
{
  "detail": {
    "message": "Agent invocation failed",
    "request_id": "<request_id>"
  }
}
```

Triggered when the graph execution raises an unhandled exception. The error detail
is redacted for safety — no legal-answer content, PII, or internal state is exposed.

### 403 — Unauthorized (Deployed Only)

Anonymous requests to `POST /api/agent/invoke` return HTTP 403. API Gateway
rejects the request before it reaches Lambda.

---

## Safety Guardrails

The Agent Runtime applies input-output safety before graph invocation:

1. **Input validation** — Pydantic enforces `question` length and jurisdiction format
2. **Injection detection** — Regex patterns detect prompt injection, role-override,
   developer mode, and [SYSTEM]/[JAILBREAK] tags. Guard instruction is prepended
   when input is flagged (`suspicious_input: true`)
3. **PII redaction** — Sensitive identifiers, keys, and credentials in logs are
   replaced with `[REDACTED]`
4. **Legal disclaimer** — Mandatory disclaimer is appended to every answer
5. **Safe 500** — Error detail excludes internal state, retrieved chunks, and
   raw prompt

See `src/agent/safety.py` for implementation and `tests/test_safety.py` (43 tests).

---

## Integration Constraints

1. Browser code must never call `POST /api/agent/invoke` directly (deployed or local).
2. Consumer applications (CRUD API worker) must invoke the Agent Runtime
   server-to-server.
3. Consumer applications must not copy Agent source code, Qdrant seed data,
   Terraform, or AWS credentials.
4. AWS credentials, Bedrock model access, and Qdrant seed data are Agent Runtime
   concerns.

---

## Future Evolution

| Change | Status |
|--------|--------|
| Replace AWS_IAM with Supabase JWT authorizer on invoke route | Planned (Phase E) |
| Add `thread_id` multi-turn support with `AsyncPostgresSaver` | Planned (Phase E) |
| Add `citation_unverified_rate` field | Not planned |

**Stable fields:** `question`, `status`, `answer`, `api_version` are not subject to
breaking changes without a version bump. Optional fields (`thread_id`, `trace_id`,
etc.) may be added in non-breaking releases.
