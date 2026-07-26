# AusTenancy.ai — Agent Deployment Architecture Gate

**Revision 3 (Frozen)**
**Date:** 2026-07-23
**Phase:** D, Step 11 — Containerize and Deploy to AWS (Design Phase)
**Infrastructure:** Terraform (sole IaC)
**Status:** READ-ONLY PLANNING — no resources created, no files modified

---

## 1. Revised Executive Architecture Decision — PASSED

Deploy the complete 7-node LangGraph agent as a single AWS Lambda function (container image) behind API Gateway HTTP API, provisioned exclusively with Terraform across three dependency-ordered stacks. The graph compiles once per warm container. FastAPI + Mangum serve as the HTTP adapter around `graph.ainvoke()`. The synchronous `/api/agent/invoke` endpoint is staging smoke/demo infrastructure only — it is not a production contract.

**Stack summary:**

| Layer | Decision |
|-------|----------|
| IaC | Terraform 1.x, S3 backend (encrypted, versioned, private), `use_lockfile=true`, no DynamoDB |
| IaC structure | Three root modules: `bootstrap` (S3 bucket), `foundation/staging` (ECR), `runtime/staging` (Lambda + API GW) |
| Compute | Lambda container image, Python 3.12, 1024MB, 60s timeout |
| HTTP | API Gateway HTTP API (`$default` stage, AWS_IAM auth on POST, unauthenticated GET /health) |
| HTTP adapter | FastAPI + Mangum |
| LLM (staging) | Amazon Bedrock Converse only (`LLM_PROVIDER=bedrock`, `BEDROCK_TEMPERATURE=0`) |
| LLM model | `amazon.nova-pro-v1:0` in `ap-southeast-2` |
| Embeddings | FastEmbed BGE-small-en-v1.5 + Qdrant/bm25, pre-warmed in image |
| Vector store | **Conditionally approved — static Qdrant local index with PoC gate** |
| Observability | CloudWatch Logs (14-day), CloudWatch alarms, optional LangSmith |
| Checkpointer | None (single-turn synchronous staging) |
| Secrets | None required (Lambda execution role replaces all keys) |

---

## 2. Corrected API and Authentication Design — PASSED

### 2.1 HTTP Adapter: FastAPI + Mangum

```
API Gateway HTTP API (AWS_IAM or public)
    │
    ▼
Mangum (Lambda event → ASGI bridge)
    │
    ▼
FastAPI app (routing, validation, serialization)
    │
    ▼
compiled_graph.ainvoke(state)
```

Mangum converts API Gateway HTTP API v2 payload-format events into ASGI requests and returns ASGI responses as API Gateway-compatible dicts. FastAPI is the HTTP adapter — it does not call any graph node, retrieval, generation, or citation verification function directly.

### 2.2 Two Distinct Local Test Contracts

The Lambda container image must be validated in two modes.

#### Mode A: ASGI Application Test

```bash
# Start FastAPI directly via Uvicorn
uvicorn src.api.handler:app --host 0.0.0.0 --port 8080

# Health check
curl http://localhost:8080/health

# Agent invoke
curl -X POST http://localhost:8080/api/agent/invoke \
  -H "Content-Type: application/json" \
  -d '{"question": "I am 10 days behind on rent in VIC. Can my landlord evict me?"}'
```

This validates FastAPI routing, request validation, response serialization, and the full `graph.ainvoke()` path inside the local Python environment. No Lambda runtime or Mangum involved.

#### Mode B: Lambda-Compatible Image Test

```bash
# Start the container using the Lambda Runtime Interface Emulator (RIE)
# The AWS Lambda Python base image includes the RIE entrypoint.
# Port 9000 is the RIE default; the container's internal port is 8080.
docker run -p 9000:8080 \
  --env-file .env.staging \
  austenancy-agent:${GIT_SHA}

# Invoke the Lambda-compatible image using the Lambda Invoke API
# The payload is an API Gateway HTTP API v2 event (not bare AgentRequest)
curl -X POST "http://localhost:9000/2015-03-31/functions/function/invocations" \
  -H "Content-Type: application/json" \
  -d @tests/fixtures/api_gateway_v2_event.json
```

**Critical rule:** When using the AWS Lambda Python base image, the container entrypoint is the Lambda Runtime API, not Uvicorn. Local invocation goes through the Runtime Interface Emulator at `/2015-03-31/functions/function/invocations`. The payload is a full API Gateway HTTP API v2 event — the same format the Lambda receives in production.

The `tests/fixtures/api_gateway_v2_event.json` fixture must contain a valid HTTP API v2 payload with path `/api/agent/invoke`, method `POST`, and the `AgentRequest` body in the `body` field (base64-encoded if `isBase64Encoded: true`).

### 2.3 Endpoints

**GET /health — unauthenticated**

```
Response 200:
{
  "status": "healthy",
  "version": "0.1.0",
  "api_version": "1.0"
}
```

Exposes no secrets, environment variable values, dependency versions, loaded models, or provider details.

**POST /api/agent/invoke — AWS_IAM authenticated**

```
Request:
  Headers: Content-Type: application/json
           Authorization: (SigV4, signed by an authorized IAM principal)
  Body: AgentRequest

Response 200: AgentResponse
Response 422: {"detail": <validation error>}
Response 500: {"detail": "Internal server error"}
```

Authentication is AWS_IAM — API Gateway validates the SigV4 signature against an IAM principal with `execute-api:Invoke` permission. No API key, no usage plan, no custom authorizer Lambda. Phase E may replace AWS_IAM with a Supabase JWT authorizer.

### 2.4 AWS_IAM Authorized Caller Identity

The staging deployment must define which IAM principal is permitted to invoke the API.

**Decision:** The developer's IAM Identity Center permission-set role.

**Required caller permission:**

```json
{
  "Effect": "Allow",
  "Action": "execute-api:Invoke",
  "Resource": "arn:aws:execute-api:ap-southeast-2:<account-id>:<api-id>/*/POST/api/agent/invoke"
}
```

This permission can be attached to the developer's IAM Identity Center role via the AWS console or managed through a Terraform IAM policy in the runtime stack that references a data source for the caller principal ARN.

**Alternative (not selected for staging):** Terraform creates a dedicated `staging-invoker` IAM role with this permission. The developer assumes the role before making API calls. This adds operational overhead for staging and is deferred.

**SigV4 smoke test tooling:** Use `awscurl` or a botocore-based Python signing script. Do not use `curl --user ACCESS_KEY:SECRET_KEY`, which does not support SSO temporary credentials and fails when the developer uses IAM Identity Center with session tokens.

### 2.5 AgentRequest (Pydantic v2, v1.0)

```python
from pydantic import BaseModel, Field
from typing import Optional
from uuid import uuid4

class AgentRequest(BaseModel):
    request_id: str = Field(default_factory=lambda: str(uuid4()))
    thread_id: Optional[str] = None
    user_id: Optional[str] = None
    conversation_id: Optional[str] = None
    message_id: Optional[str] = Field(default_factory=lambda: str(uuid4()))
    question: str
    jurisdiction: Optional[str] = Field(
        default=None,
        pattern=r"^(VIC|NSW)?$"
    )
    api_version: str = Field(default="1.0", pattern=r"^\d+\.\d+$")
```

### 2.6 AgentResponse (Pydantic v2, v1.0)

```python
from datetime import datetime, timezone

class AgentResponse(BaseModel):
    request_id: str
    status: str  # "success" | "fallback" | "clarification"
    answer: Optional[str] = None
    verified_citations: list[str] = Field(default_factory=list)
    citation_verified_rate: Optional[float] = None
    clarification: Optional[str] = None
    fallback_reason: Optional[str] = None
    selected_jurisdiction: Optional[str] = None
    latency_ms: Optional[float] = None
    trace_id: Optional[str] = None
    api_version: str = "1.0"
    generated_at: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
```

**Diagnostic data is not exposed** through the public response — no `rewritten_queries`, `retrieved_context_count`, `top_retrieved_provisions`, or `unverified_citations`. There is no hidden debug mode via query parameter or environment flag. Diagnostic information is captured by the test harness or written to controlled CloudWatch Logs only — never returned to the staging API caller.

### 2.7 State → Response Mapping

The existing `generate_run_summary()` in `observability.py:183` computes status, citation stats, and context stats. The FastAPI handler:
1. Awaits `graph.ainvoke(state)`
2. Times the invocation
3. Calls `generate_run_summary(final_state, total_latency_ms)`
4. Maps the relevant summary fields into `AgentResponse`, dropping all diagnostic fields

---

## 3. Conditional Qdrant Decision and PoC Gate — PARTIAL

### 3.1 Decision: Conditionally Approved (Static Local Index)

Subject to successful completion of the container proof-of-concept below. If the PoC fails, the decision reverts to Qdrant Cloud (Free Tier) as the next staging option.

### 3.2 Filesystem Path Design

| Path | Role | Ownership |
|------|------|-----------|
| `/var/task/assets/qdrant_storage` | Immutable seed index (baked into image, read-only) | `COPY` in Dockerfile |
| `/var/task/assets/qdrant_index_manifest.json` | Index identity and version metadata | `COPY` in Dockerfile |
| `/tmp/qdrant_storage` | Writable runtime copy | Created at cold init; reused across warm invocations |
| `QDRANT_PATH` | Env var → `/tmp/qdrant_storage` | Lambda environment variable |

### 3.3 Qdrant Index Artifact Requirements

The `qdrant_storage/` directory is currently gitignored. Docker builds must produce a verifiable, reproducible index artifact.

**Index manifest** (`/var/task/assets/qdrant_index_manifest.json`):

```json
{
  "corpus_version": "2026-07",
  "build_timestamp": "2026-07-23T00:00:00Z",
  "collection_name": "tenancy_acts",
  "dense_model": "BAAI/bge-small-en-v1.5",
  "sparse_model": "Qdrant/bm25",
  "dense_vector_dimensions": 384,
  "distance_metric": "cosine",
  "legislation_sources": [
    "VIC Residential Tenancies Act 1997",
    "VIC Residential Tenancies Regulations 2021",
    "NSW Residential Tenancies Act 2010",
    "NSW Residential Tenancies Regulation 2019"
  ],
  "sha256_checksum": "abc123...",
  "chunk_count": 1234,
  "supported_jurisdictions": ["VIC", "NSW"]
}
```

**Build-time failures:** `docker build` must fail if either the `qdrant_storage/` directory or the `qdrant_index_manifest.json` is missing from the build context. This prevents different developers or CI pipelines from producing images with different index content under similar image tags.

**Checksum verification** (optional, recommended for CI): The container init code may optionally verify the SHA-256 checksum of the seed directory against the manifest before copying to `/tmp`.

### 3.4 Container Proof-of-Concept Gate

The PoC must validate 5 assertions:

1. **Seed copy:** A cold initialization copies `/var/task/assets/qdrant_storage` → `/tmp/qdrant_storage` (e.g., `shutil.copytree` in a module-level or `@lru_cache`-guarded init function). The copy must complete without error.

2. **Warm reuse:** On subsequent invocations in the same warm container, the `/tmp/qdrant_storage` directory already exists and is reused — no re-copy, no re-index.

3. **Write protection:**
   - No writes are permitted under `/var/task/assets/qdrant_storage` (this is enforced by the Lambda read-only filesystem, but the PoC must verify no attempt is made).
   - The application must not create collections, upsert points, delete points, or mutate the legislation corpus.
   - Qdrant internal runtime writes (lock files, metadata journal) under `/tmp/qdrant_storage` are permitted and expected.
   - The immutable seed directory under `/var/task/assets/` must remain byte-for-byte unchanged.

4. **Real hybrid retrieval:** A query executes `hybrid_retrieve()` from `vector_store.py` against the `/tmp/qdrant_storage` collection and returns non-empty results matching expected provision labels.

5. **No FastEmbed runtime download:** `TextEmbedding` and `SparseTextEmbedding` must load from the pre-warmed cache baked into the image. No HTTP/HTTPS model download occurs during invocation (verify by inspecting FastEmbed cache directory or blocking network access during test).

**PoC success → Qdrant decision is PASSED.** PoC failure → Qdrant decision reverts to DECISION REQUIRED; recommend Qdrant Cloud Free Tier.

### 3.5 Qdrant Deployment Comparison (if PoC fails)

| Option | Complexity | Cold Start | Concurrency | Monthly Cost |
|--------|-----------|------------|-------------|--------------|
| Static local (PoC-passed) | Minimal | Fastest | Per-invocation read | $0 |
| Qdrant Cloud Free Tier | Medium | +200-500ms network | 100 req/s cap | $0 |
| ECS/Fargate | High | +5-10s ENI attach | Configurable | ~$40-80 |

### 3.6 LangGraph Checkpointer Decision — PASSED

**Decision: No persistent checkpointer for staging.**

| Question | Answer |
|----------|--------|
| Does staging need a checkpointer? | No — single-turn synchronous invocations only. |
| `thread_id` | Accepted in `AgentRequest` but ignored. Placeholder for Phase E. |
| Postgres / S3? | Neither. Phase E will use `AsyncPostgresSaver` backed by Supabase. |
| Retry behaviour | No checkpoint replay. Failed invocations return errors; caller retries. |

---

## 4. Revised Terraform Structure and Deployment Dependencies — PASSED

### 4.1 Stack Architecture

Three dependency-ordered root modules, each with its own state and its own `.terraform.lock.hcl`, invoked in strict sequence:

```
terraform/bootstrap       →  S3 backend bucket (local state initially,
                              then migrated into the new bucket)
        │
        ▼
terraform/foundation/staging  →  ECR repository + lifecycle policy
        │
        ▼
    [Image build, test, push, digest capture]
        │
        ▼
terraform/runtime/staging    →  Lambda (by digest) + IAM + API Gateway
                                + logs + alarms
```

**Dependency rule:** `runtime/staging` must not be planned or applied before a valid ECR image digest exists. The image digest is passed as a Terraform variable — never hardcoded.

### 4.2 Directory Structure

```
terraform/
├── bootstrap/
│   ├── main.tf
│   ├── variables.tf
│   ├── outputs.tf
│   ├── terraform.tfvars.example
│   └── .terraform.lock.hcl                  # COMMITTED
│
├── foundation/
│   └── staging/
│       ├── backend.tf
│       ├── main.tf
│       ├── variables.tf
│       ├── outputs.tf
│       ├── terraform.tfvars.example
│       └── .terraform.lock.hcl              # COMMITTED
│
├── runtime/
│   └── staging/
│       ├── backend.tf
│       ├── providers.tf
│       ├── versions.tf
│       ├── variables.tf
│       ├── locals.tf
│       ├── main.tf
│       ├── outputs.tf
│       ├── terraform.tfvars.example
│       └── .terraform.lock.hcl              # COMMITTED
│
├── modules/
│   └── agent-lambda/
│       ├── main.tf
│       ├── variables.tf
│       └── outputs.tf
│
└── .gitignore
```

Each root module (`bootstrap`, `foundation/staging`, `runtime/staging`) has its own `.terraform.lock.hcl`. All three lock files are committed to version control.

### 4.3 Remote State (S3, Encrypted, No DynamoDB)

```hcl
# foundation/staging/backend.tf and runtime/staging/backend.tf
terraform {
  backend "s3" {
    # bucket, key, and region provided via -backend-config at init time
    # or via a backend.{env}.tfbackend file (gitignored)
    use_lockfile = true
  }
}
```

`use_lockfile=true` replaces DynamoDB-based state locking. HashiCorp has deprecated DynamoDB locking in favor of S3-native conditional writes with `use_lockfile`. The committed `.terraform.lock.hcl` pins provider versions.

### 4.4 Backend Configuration Files

Backend values are provided via a backend configuration file (not hardcoded in `backend.tf`):

```hcl
# backend.staging.tfbackend.example — committed
bucket  = "austenancy-terraform-<unique-suffix>"
key     = "foundation/staging/terraform.tfstate"
region  = "ap-southeast-2"
encrypt = true
```

The real `backend.staging.tfbackend` contains the actual bucket name and is gitignored.

### 4.5 Bootstrap State Migration

The bootstrap root module creates the S3 backend bucket. Its state lifecycle:

1. Initial `terraform init` without a backend block → local state created
2. `terraform apply` → S3 bucket created
3. `terraform init -migrate-state -backend-config=backend.tfbackend` → state migrated from local into the newly created bucket
4. The local `terraform.tfstate` is no longer the source of truth and is removed

**The bootstrap state must never be discarded.** If discarded, the S3 backend bucket becomes an unmanaged orphan — Terraform can no longer track or destroy it.

### 4.6 Gitignore Rules

```gitignore
# Terraform
**/.terraform/
*.tfstate
*.tfstate.*
*.tfvars
!*.tfvars.example
*.tfplan
*.tfbackend
!*.tfbackend.example
```

`.terraform.lock.hcl` files are NOT gitignored — each root module's lock file is committed. No negate rules for lock files (avoids confusion).

### 4.7 Deployment Dependency Order

```
1. terraform/bootstrap               →  S3 bucket created; state migrated
2. terraform/foundation/staging      →  ECR repo created
3. docker build + local test         →  Image validated (both ASGI and Lambda modes)
4. docker push ${ECR_URL}:${GIT_SHA} →  Image in ECR
5. docker inspect → sha256 digest    →  Digest captured
6. terraform/runtime/staging         →  Lambda (by digest) + API GW + IAM + alarms
7. Direct Lambda smoke test          →  aws lambda invoke with API GW v2 event payload
8. SigV4 HTTP API smoke test         →  awscurl or botocore signing script
9. Cold/warm performance review
```

**Lambda must not be created before a valid ECR image digest exists.** The `image_digest` variable is required, has no default, and is validated:

```hcl
variable "image_digest" {
  type        = string
  description = "ECR image digest (sha256:...) — must be a pushed, valid image"
  validation {
    condition     = can(regex("^sha256:[a-f0-9]{64}$", var.image_digest))
    error_message = "image_digest must be a valid sha256:... digest."
  }
}
```

### 4.8 Image URI Construction

The Lambda function's `image_uri` combines the ECR repository URL (from foundation remote-state output or a `data.aws_ecr_repository` lookup) with the image digest:

```hcl
# runtime/staging/main.tf

data "terraform_remote_state" "foundation" {
  backend = "s3"
  config = {
    bucket = var.state_bucket
    key    = "foundation/staging/terraform.tfstate"
    region = var.aws_region
  }
}

locals {
  ecr_repository_url = data.terraform_remote_state.foundation.outputs.repository_url
  image_uri          = "${local.ecr_repository_url}@${var.image_digest}"
}

module "agent_lambda" {
  source    = "../../modules/agent-lambda"
  image_uri = local.image_uri
  # ...
}
```

Alternative: use `data.aws_ecr_repository` to look up the repository by name instead of remote state — both patterns are valid. The key constraint is that the Lambda must reference a specific immutable digest, never a mutable tag like `:latest`.

### 4.9 Naming and Tagging

```hcl
# locals.tf
locals {
  project     = "austenancy"
  environment = "staging"

  tags = {
    Project     = local.project
    Environment = local.environment
    ManagedBy   = "terraform"
  }

  lambda_name   = "${local.project}-${local.environment}-agent"
  ecr_repo_name = "${local.project}-${local.environment}-agent"
  api_name      = "${local.project}-${local.environment}-api"
}
```

---

## 5. Corrected IAM and Staging Provider Design — PASSED

### 5.1 Provider Configuration

**Bedrock only in staging.** DeepSeek is the local default (`LLM_PROVIDER=deepseek` when running from CLI). In the staging Lambda, the provider is fixed:

| Env Var | Value |
|---------|-------|
| `LLM_PROVIDER` | `bedrock` |
| `AWS_REGION` | `ap-southeast-2` |
| `BEDROCK_MODEL_ID` | `amazon.nova-pro-v1:0` |
| `BEDROCK_TEMPERATURE` | `0` |
| `QDRANT_PATH` | `/tmp/qdrant_storage` |
| `LOG_LEVEL` | `INFO` |

No `DEEPSEEK_API_KEY`, no `LLM_MODEL_ID`, no `OPENAI_API_KEY`, no fallback chain. If Bedrock is unavailable, the invocation fails — no silent fallback.

### 5.2 Lambda Execution Role (Least Privilege)

```hcl
# modules/agent-lambda/main.tf

resource "aws_iam_role" "lambda_exec" {
  name = "${var.lambda_name}-role"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "lambda.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
}

# CloudWatch Logs — scoped to this function's log group only
resource "aws_iam_role_policy" "logging" {
  name = "${var.lambda_name}-logging"
  role = aws_iam_role.lambda_exec.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect = "Allow"
      Action = [
        "logs:CreateLogStream",
        "logs:PutLogEvents",
      ]
      Resource = "${aws_cloudwatch_log_group.agent.arn}:*"
    }]
  })
}

# Bedrock InvokeModel — scoped to the exact foundation model
resource "aws_iam_role_policy" "bedrock" {
  name = "${var.lambda_name}-bedrock"
  role = aws_iam_role.lambda_exec.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = ["bedrock:InvokeModel"]
      Resource = "arn:aws:bedrock:ap-southeast-2::foundation-model/amazon.nova-pro-v1:0"
    }]
  })
}
```

### 5.3 Explicitly NOT Granted

- `secretsmanager:*` — no secrets needed (no DeepSeek key, no Qdrant Cloud key)
- `s3:*` — no S3 access needed (Qdrant index is local)
- `dynamodb:*` — no state persistence
- `sqs:*` — no async messaging
- `iam:*` — no role assumption beyond execution
- `ec2:*` — no VPC/ENI (Lambda not in a VPC)

### 5.4 No AWS Access Keys in Runtime

The Lambda execution role is the sole credential source. `boto3.client("bedrock-runtime")` resolves credentials from the execution role automatically. `.env` is not included in the container image. No `AWS_ACCESS_KEY_ID` or `AWS_SECRET_ACCESS_KEY` anywhere in the runtime environment.

### 5.5 Log Redaction

- Raw `AgentState`, prompts, retrieved legal text, environment variables, credentials, and full user questions must not be logged at any level
- FastAPI handler and graph nodes must log only: node names, counts (characters/queries/chunks), status transitions, latency, and error types
- `logging.getLogger("langsmith").setLevel(logging.CRITICAL)` in staging (already the CLI default at `cli.py:85`)

---

## 6. Revised Container Filesystem and Runtime Design — PASSED

### 6.1 Filesystem Layout Inside Container

```
/var/task/
├── assets/
│   ├── qdrant_storage/                  # Immutable seed (COPY in Dockerfile)
│   │   └── collection/
│   │       └── tenancy_acts/
│   └── qdrant_index_manifest.json       # Index identity metadata
├── src/                                 # Application code (COPY in Dockerfile)
│   ├── agent/
│   └── rag/
├── handler.py                           # Lambda entry point
└── (FastEmbed cache baked into ~/.cache/fastembed/)

/tmp/
├── qdrant_storage/                      # Cold-init copy from /var/task/assets/qdrant_storage
│   └── collection/
│       └── tenancy_acts/
└── (Lambda ephemeral storage — persists across warm invocations)
```

### 6.2 Cold Init Sequence

1. Lambda container starts
2. Python runtime imports `handler.py`
3. Module-level init checks if `/tmp/qdrant_storage` exists
4. If not: `shutil.copytree("/var/task/assets/qdrant_storage", "/tmp/qdrant_storage")`
5. `from src.agent.graph_skeleton import build_graph; graph = build_graph().compile()`
6. Mangum wraps the FastAPI app
7. Ready for invocation

### 6.3 Warm Invocation Sequence

1. `/tmp/qdrant_storage` already exists → no copy
2. `graph` already compiled → no re-compilation
3. FastEmbed models already loaded in memory → no reload
4. `handler(event, context)` → Mangum → FastAPI → `graph.ainvoke(state)` → response

### 6.4 FastEmbed Assets

| Asset | Model | Size | Lambda Compatibility |
|-------|-------|------|---------------------|
| Dense embeddings | `BAAI/bge-small-en-v1.5` | ~130MB | Pre-warmed in Dockerfile |
| Sparse embeddings | `Qdrant/bm25` | ~160MB | Pre-warmed in Dockerfile |
| FlashRank | (disabled by default) | ~200MB | Excluded from image |

**Pre-warming in Dockerfile** (required):
```dockerfile
RUN python -c "from fastembed import TextEmbedding, SparseTextEmbedding; \
    TextEmbedding(model_name='BAAI/bge-small-en-v1.5'); \
    SparseTextEmbedding(model_name='Qdrant/bm25')"
```

### 6.5 Image Size and Memory Estimates

| Layer | Size (estimate) |
|-------|-----------------|
| AWS Lambda Python 3.12 base (generic, x86_64) | ~450MB |
| Python packages (no FlashRank) | ~400MB |
| FastEmbed pre-warmed cache | ~290MB |
| Qdrant seed storage + manifest | ~50MB |
| Application code | ~2MB |
| **Total image** | **~1.2GB** |
| **Runtime RSS** | **~510MB** |
| **Recommended Lambda memory** | **1024MB** |
| **Recommended ephemeral storage** | **512MB** (default) |

### 6.6 Architecture Compatibility

Use `linux/amd64` (x86_64) for the Lambda architecture. ONNX Runtime on arm64 is stable but less tested for the FastEmbed model combination.

**Dockerfile base image:**

```dockerfile
FROM public.ecr.aws/lambda/python:3.12
```

Do not hardcode `:3.12-x86_64` in the Dockerfile. Instead, specify the target platform at build time:

```bash
docker buildx build \
  --platform linux/amd64 \
  --provenance=false \
  -t austenancy-agent:${GIT_SHA} \
  .
```

AWS requires Lambda container images to be single-architecture. The `--platform linux/amd64` flag during build ensures the correct architecture. `--provenance=false` avoids multi-architecture manifest issues.

---

## 7. Corrected Timeout and Performance Risk Assessment — PASSED

### 7.1 Timeout Configuration

| Component | Value | Justification |
|-----------|-------|---------------|
| Lambda timeout | **60 seconds** | Exceeds API GW max; Lambda can complete without API GW timeout racing |
| API Gateway integration timeout | **29,000ms** (current maximum for HTTP API) | Provider-enforced maximum for the HTTP API integration type |
| API Gateway overall timeout | 30s (hard) | Requests exceeding API GW integration timeout receive 504 |

### 7.2 Performance Categories

**Cold start** and **warm invocation** are measured separately — cold start is not amortized into warm metrics.

| Metric | Target | Measured How |
|--------|--------|-------------|
| Cold Init Duration | Recorded, not targeted | CloudWatch `Init Duration` in REPORT log |
| Warm p95 handler duration | < 25 seconds | CloudWatch `Duration` across warm invocations |
| Warm end-to-end latency (API GW → response) | Monitored | Client-side measurement |
| Memory usage (max) | < 900MB (under 1024MB limit) | CloudWatch `Max Memory Used` |
| `/tmp` usage | Must remain under 512MB | CloudWatch Logs assertion |
| Timeout rate | 0% for warm invocations | CloudWatch Lambda Errors metric |

### 7.3 Risk Assessment

| Risk | Severity | Analysis |
|------|----------|----------|
| **API GW 30s hard timeout** vs full-agent path latency | **Medium** | Full success path takes 2 LLM calls (query rewrite ~2-5s + legal reasoning ~5-12s) + retrieval (~0.5s) = worst-case ~18-20s. Bedrock latency in ap-southeast-2 has not been profiled at staging load. Margin is not "low risk" — it is "acceptable for staging with monitoring." Cold starts push the first invocation closer to the boundary. |
| Cold start adds Init Duration | **Medium** | Init Duration includes container boot + FastEmbed model load + Qdrant seed copy + graph compilation. Estimated 5-15s. Added to handler duration, cold invocation may exceed API GW timeout. First invocation per deployment should be treated as a warm-up, not a failure. |
| Warm handler spikes | **Low-Medium** | Bedrock model latency is variable. Variance increases under concurrent invocations if the Bedrock account has low per-model throughput quotas. Staging concurrency is expected to be 1. |
| Image too large for ECR/Lambda | **Low** | Estimated 1.2GB, under 10GB Lambda limit and ECR limits. |

### 7.4 Non-Production Disclaimer

Staging smoke tests measure function, not production readiness. The synchronous endpoint validates that the graph completes and returns correct legal answers. It does not validate:
- Latency under concurrent load
- Bedrock throughput limits at production scale
- Cold start frequency or amplitude
- Long-term cost efficiency
- Multi-turn conversation reliability

Do not claim production readiness from staging smoke test results.

---

## 8. Updated Risks and Unresolved Decisions — PASSED

| # | Risk / Decision | Status | Detail |
|---|----------------|--------|--------|
| R1 | **Qdrant static local index** | **DECISION REQUIRED** | Conditional on PoC. If PoC fails → Qdrant Cloud. |
| R2 | **API GW 30s timeout** vs worst-case path latency | **PASSED** | Acceptable for staging with monitoring. Cold starts may hit the boundary. |
| R3 | **Cold start Init Duration** unknown until profiled | **PASSED** | Must be measured separately. First invocation is warm-up, not a failure. |
| R4 | **Image too large** for Lambda/ECR | **PASSED** | Estimated 1.2GB < 10GB limit. |
| R5 | **Bedrock model access** not granted in staging account | **PASSED** | Pre-deployment checklist item: verify `aws bedrock list-foundation-models --region ap-southeast-2`. |
| R6 | **Log redaction of legal text and prompts** | **PASSED** | Must be enforced in handler code. No pre-existing log filtering in graph nodes. |
| R7 | **No `src/api/handler.py`** — must be created | **PASSED** | New file: FastAPI + Mangum app, routes, graph compilation, state→response mapping. |
| R8 | **No Mangum dependency** in pyproject.toml | **PASSED** | Must be added. Mangum is not currently a dependency. |
| R9 | **No Dockerfile** — must be created | **PASSED** | New file: single-stage with FastEmbed pre-warming and Qdrant seed copy. |
| R10 | **No Terraform files** — must be created | **PASSED** | Three root modules + one shared module. Full structure in §4.2. |
| R11 | **Bedrock only — no fallback** | **PASSED** | If `LLM_PROVIDER=bedrock` and Bedrock is unavailable, the invocation fails. No silent fallback to DeepSeek. This is the intended behaviour for staging. |
| R12 | **Qdrant index missing from build context** | **PASSED** | Docker build must fail if `qdrant_storage/` or `qdrant_index_manifest.json` is missing. |
| R13 | **Missing `qdrant_index_manifest.json`** | **PASSED** | Must be generated during index build step and committed/shared alongside the seed directory. |
| R14 | **Direct Lambda smoke test payload mismatch** | **PASSED** | Must use API Gateway HTTP API v2 event payload, not bare AgentRequest. |
| R15 | **Local container test: wrong entrypoint** | **PASSED** | Two distinct test modes: ASGI (Uvicorn) and Lambda-compatible (RIE). Documented in §2.2. |
| R16 | **AWS_IAM caller identity undefined** | **PASSED** | Developer's IAM Identity Center role with `execute-api:Invoke` on the staging API route. Documented in §2.4. |
| R17 | **Bootstrap state discarded** | **PASSED** | State must be migrated into S3 via `terraform init -migrate-state`. Never discarded. Documented in §4.5. |
| R18 | **Single `.terraform.lock.hcl` for three root modules** | **PASSED** | Each of the three root modules has its own lock file. All three committed. Documented in §4.2. |
| R19 | **Mangum ASGI ↔ API Gateway v2 compatibility** | **PASSED** | Mangum supports API Gateway HTTP API (v2) payload format natively. Must be tested in both ASGI and Lambda-compatible modes. |

**No unresolved decisions beyond Qdrant PoC gate.**

---

## 9. Revised Ordered Implementation Plan — PASSED

Total estimated effort: 1-2 days (not including Bedrock model access approval lead time).

### Step 1: Architecture Report Correction — COMPLETED

This document (`docs/agent-deployment-architecture-gate.md`). No files changed.

### Step 2: Full-Agent Container Proof of Concept

**Gate:** All 5 Qdrant assertions pass (§3.4) AND FastEmbed loads from cache (no download).

**Files to create:** `Dockerfile`, `.dockerignore`, `src/api/__init__.py`, `src/api/handler.py`, `tests/fixtures/api_gateway_v2_event.json`

**ASGI mode test:**
```bash
uvicorn src.api.handler:app --host 0.0.0.0 --port 8080
curl http://localhost:8080/health
curl -X POST http://localhost:8080/api/agent/invoke \
  -H "Content-Type: application/json" \
  -d '{"question": "I am 10 days behind on rent in VIC. Can my landlord evict me?"}'
```

**Lambda-compatible image test:**
```bash
GIT_SHA=$(git rev-parse --short HEAD)
docker buildx build --platform linux/amd64 --provenance=false \
  -t austenancy-agent:${GIT_SHA} .
docker run -p 9000:8080 --env-file .env.staging austenancy-agent:${GIT_SHA}
curl -X POST "http://localhost:9000/2015-03-31/functions/function/invocations" \
  -H "Content-Type: application/json" \
  -d @tests/fixtures/api_gateway_v2_event.json
```

**Assertions:** 5 Qdrant assertions + FastEmbed from cache + Mangum event bridge working.

**Outcome:** PoC PASSED → Qdrant decision is final. PoC FAILED → Qdrant topology becomes DECISION REQUIRED; report updated.

### Step 3: Terraform Bootstrap

**Files:** All files under `terraform/bootstrap/` per §4.2.

- S3 bucket with globally unique name
- `acl = "private"`, versioning enabled, SSE-S3 encryption, public access blocked
- `terraform init` (local state, no backend block)
- `terraform plan` and `terraform apply`
- `terraform init -migrate-state -backend-config=backend.tfbackend` → state migrated into the new bucket
- Remove local `terraform.tfstate` (now stale)
- **The bootstrap state must never be discarded.** Migration ensures ongoing Terraform management.

### Step 4: Terraform ECR Foundation

**Files:** All files under `terraform/foundation/staging/` per §4.2.

- `terraform init -backend-config=backend.staging.tfbackend` (S3 backend, points to bootstrap bucket)
- ECR repository: `austenancy-staging-agent`, `image_tag_mutability = "IMMUTABLE"`, `scan_on_push = true`
- Lifecycle policy: retain last 5 images, expire untagged after 1 day
- `terraform plan` → reviewed → `terraform apply`
- Output: `repository_url`
- Commit `terraform/foundation/staging/.terraform.lock.hcl`

### Step 5: Immutable Image Build, Test, Push, and Digest Capture

```bash
GIT_SHA=$(git rev-parse --short HEAD)
ECR_URL=<output from Step 4>

# Build single-architecture image for Lambda
docker buildx build --platform linux/amd64 --provenance=false \
  -t austenancy-agent:${GIT_SHA} .
docker tag austenancy-agent:${GIT_SHA} ${ECR_URL}:${GIT_SHA}

# Run both ASGI and Lambda-compatible local tests (see Step 2)

# Push to ECR
aws ecr get-login-password --region ap-southeast-2 | \
  docker login --username AWS --password-stdin ${ECR_URL}
docker push ${ECR_URL}:${GIT_SHA}

# Capture immutable digest
IMAGE_DIGEST=$(aws ecr describe-images \
  --repository-name austenancy-staging-agent \
  --image-ids imageTag=${GIT_SHA} \
  --query 'imageDetails[0].imageDigest' \
  --output text)
echo "IMAGE_DIGEST=${IMAGE_DIGEST}"  # Pass to Step 6
```

### Step 6: Terraform Runtime — Plan and Reviewed Apply

**Files:** All files under `terraform/runtime/staging/` and `terraform/modules/agent-lambda/` per §4.2.

- `terraform init -backend-config=backend.staging.tfbackend`
- `terraform plan -var="image_digest=${IMAGE_DIGEST}" -out=plan.tfplan`
- **Human review of plan:** verify no unexpected destroys, correct digest, IAM additive only, correct API GW routes, AWS_IAM authorizer configured
- `terraform apply plan.tfplan`
- Resources created: Lambda function (container image by digest), IAM role + policies, API Gateway HTTP API + routes + integration + AWS_IAM authorizer, Lambda permission, CloudWatch log groups, CloudWatch alarms
- Output: `api_endpoint`, `lambda_arn`, `function_name`
- Commit `terraform/runtime/staging/.terraform.lock.hcl`

### Step 7: Direct Lambda Smoke Test

**Payload format:** API Gateway HTTP API v2 event fixture (same as used in Step 2 Lambda-compatible image test).

```bash
aws lambda invoke \
  --function-name austenancy-staging-agent \
  --payload file://tests/fixtures/api_gateway_v2_event.json \
  --cli-binary-format raw-in-base64-out \
  /tmp/lambda_response.json

# Assertions:
# - StatusCode = 200
# - Response body is a valid API Gateway v2 response
# - Parsed response body contains status = "success"
# - verified_citations is non-empty
# - No FunctionError
```

**Why API GW v2 event and not bare AgentRequest:** The handler receives events through Mangum, which expects API Gateway HTTP API v2 payload format (§2.1). Sending a bare `{"question": "..."}` would be rejected or mishandled. The same fixture validates both local Lambda-compatible tests and the deployed Lambda.

### Step 8: SigV4 HTTP API Smoke Test

```bash
API_ENDPOINT=<output from Step 6>

# Health check (unauthenticated)
curl ${API_ENDPOINT}/health
# → {"status": "healthy", "version": "0.1.0", "api_version": "1.0"}

# Agent invoke (SigV4 signed via awscurl or botocore signing script)
awscurl --service execute-api --region ap-southeast-2 \
  -X POST ${API_ENDPOINT}/api/agent/invoke \
  -H "Content-Type: application/json" \
  -d '{"question": "I am 10 days behind on rent in VIC. Can my landlord evict me?"}'

# Assertions:
# - Status 200 (authenticated — not 403 Forbidden)
# - status = "success" or "fallback" (not 5xx)
# - verified_citations present if status = "success"
```

Use `awscurl` or a botocore-based Python signing script. Do not use `curl --user ACCESS_KEY:SECRET_KEY` — this does not support SSO temporary credentials and will fail for IAM Identity Center users.

### Step 9: Cold/Warm Performance and CloudWatch Review

1. **Cold invocation:** Invoke after a new deployment (fresh Lambda instance). Record `Init Duration` from CloudWatch REPORT log.
2. **Warm invocation:** Invoke 5 times sequentially. Record p95 `Duration`. Target: < 25 seconds (warm, excluding cold).
3. **Timeout test:** If any warm invocation exceeds API GW 30s timeout, it will receive 504 — record and flag.
4. **CloudWatch Logs review:** Verify no leaks (no raw AgentState, no prompts, no legal text, no credentials). Verify Bedrock calls logged as `"legal_reasoner: answer generated (N chars)"` and `"query_rewriter: N queries generated"` (current log format, unchanged).
5. **Alarm verification:** Check CloudWatch alarm state — no Lambda Errors > 0, no API GW 5XX > 0.
6. **Memory review:** Verify `Max Memory Used` < 900MB and `/tmp` usage < 512MB.

---

## 10. Exact Summary of Sections Changed in Revision 3

| Revised Section | Key Changes from Revision 2 |
|-----------------|---------------------------|
| §2.2 Two Distinct Local Test Contracts | **New subsection.** Added Mode A (Uvicorn + direct FastAPI) and Mode B (docker run + RIE at port 9000 with API GW v2 event payload). |
| §2.4 AWS_IAM Authorized Caller Identity | **New subsection.** Specified the developer's IAM Identity Center role as the authorized caller with `execute-api:Invoke` permission. Recommended `awscurl` over `curl --user`. |
| §2.6 AgentResponse | Removed reference to hidden debug mode. Diagnostic data is never exposed through the staging API. |
| §3.3 Qdrant Index Artifact Requirements | **New subsection.** Defined `qdrant_index_manifest.json` with corpus version, embedding models, checksum, chunk count. Build must fail if index or manifest is missing. |
| §3.4 Container PoC Gate | Refined write-protection assertion: Qdrant internal runtime writes under `/tmp/` are permitted. Only writes under `/var/task/assets/` are prohibited. |
| §3.5 Lambda-Compatible Local Test | Removed from here; moved to §2.2 as the canonical two-mode test contract. |
| §4.2 Directory Structure | Added `.terraform.lock.hcl` to every root module (bootstrap, foundation/staging, runtime/staging). |
| §4.5 Bootstrap State Migration | Changed from "local state is discarded or migrated" to "state must be migrated via `terraform init -migrate-state`; never discard." |
| §4.6 Gitignore Rules | Removed negate rules for `.terraform.lock.hcl`. Lock files are not gitignored at all — each root module commits its own. |
| §4.8 Image URI Construction | **New subsection.** Clarified that `image_uri = "${ecr_repository_url}@${var.image_digest}"`, with ECR URL from foundation remote state or `data.aws_ecr_repository`. |
| §6.6 Architecture Compatibility | Changed base image from fixed `public.ecr.aws/lambda/python:3.12-x86_64` to `public.ecr.aws/lambda/python:3.12` with `--platform linux/amd64` at build time. Added `--provenance=false`. |
| §7.1 Timeout Configuration | Clarified that 29,000ms is the current HTTP API integration maximum. |
| §8 Risks | Added R13 (missing manifest), R14 (Lambda payload mismatch), R15 (local test entrypoint), R16 (AWS_IAM caller), R17 (bootstrap state discard), R18 (lock files), R19 (Mangum compat). |
| §9 Step 7 Direct Lambda Smoke Test | Changed payload from bare `{"question": "..."}` to API Gateway HTTP API v2 event fixture. Added explanation of why. |
| §9 Step 8 SigV4 Smoke Test | Changed from `curl --aws-sigv4` to `awscurl`. Added note about SSO temporary credential support. |
| §9 Step 5 Image Build | Added `--platform linux/amd64 --provenance=false` to build command. |

---

## Section Status Summary

| # | Section | Status |
|---|---------|--------|
| 1 | Revised Executive Architecture Decision | **PASSED** |
| 2 | Corrected API and Authentication Design | **PASSED** |
| 3 | Conditional Qdrant Decision and PoC Gate | **PARTIAL** (PoC required) |
| 4 | Revised Terraform Structure and Deployment Dependencies | **PASSED** |
| 5 | Corrected IAM and Staging Provider Design | **PASSED** |
| 6 | Revised Container Filesystem and Runtime Design | **PASSED** |
| 7 | Corrected Timeout and Performance Risk Assessment | **PASSED** |
| 8 | Updated Risks and Unresolved Decisions | **PASSED** |
| 9 | Revised Ordered Implementation Plan | **PASSED** |
| 10 | Exact Summary of Sections Changed | **PASSED** |

---

**Next action:** Implement and validate the Full-Agent Container Proof of Concept.
