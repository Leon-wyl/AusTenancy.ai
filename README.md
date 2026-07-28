# AusTenancy.ai — Australian Residential Tenancies Compliance Agent

A stateful, graph-based RAG Agent delivering high-precision compliance queries for Australian state Residential Tenancies Acts (VIC, NSW, QLD, and others). Built with LangGraph and AWS Bedrock, the system implements layout-aware hierarchical chunking (Act→Part→Section) to preserve legal context and enforce strict citation grounding.

## Value Proposition

- **Zero cross-state hallucination** — metadata-filtered retrieval ensures answers are grounded in the correct jurisdiction's legislation.
- **Stateful multi-turn reasoning** — LangGraph manages conversation state, enabling follow-up questions that respect prior context.
- **Production-grade retrieval** — hybrid search (dense vector + BM25) via Qdrant. Embeddings fine-tuned on legal text for maximum retrieval precision.
- **Strict citation grounding** — every claim verified against retrieved chunks with `[State RTA Year Sec X]` format enforcement.
- **Input-output safety guardrails** — prompt injection detection, PII/secret log redaction, mandatory legal disclaimer, safe error responses — filtered pre-graph, never baked into node logic.
- **Industrial-grade chat application** — Next.js frontend with Supabase Realtime streaming, CRUD + RAG dual-Lambda backend, Auth0-grade authentication.
- **Enterprise compliance ready** — designed for Lambda + API Gateway deployment, swappable LLM backend (DeepSeek dev → Bedrock prod).

## Technical Stack

| Layer                 | Technology                                                        |
| --------------------- | ----------------------------------------------------------------- |
| Orchestration         | LangGraph (7-node state machine, 3 conditional routers) — built; multi-agent supervisor planned (Phase F) |
| Retrieval             | Qdrant (dense + BM25 hybrid with RRF fusion)                      |
| Embeddings            | BGE-small-en-v1.5 via fastembed (fine-tuned on legal text planned)|
| Embeddings (prod)     | Amazon Titan Text Embeddings v2 (via Bedrock)                     |
| LLM (current dev)     | DeepSeek (default) or AWS Bedrock Converse — selected via `LLM_PROVIDER` |
| LLM (prod target)     | Anthropic Claude 3.5 Sonnet (via AWS Bedrock)                     |
| Evaluation            | RAGAS (faithfulness, context precision, answer relevance)         |
| Auth + DB + Realtime  | Supabase (PostgreSQL, JWT, RLS, WebSocket) — planned (Phase E)    |
| Backend API           | NestJS on AWS Lambda (CRUD 512MB) — designed; implementation planned |
| RAG Backend           | FastAPI/Mangum on AWS Lambda (1024MB+) — built                     |
| DB Client + Schema    | Prisma (client generation) + Supabase SQL migrations — planned (Phase E) |
| Frontend              | Next.js App Router + Tailwind + shadcn/ui — planned (Phase E)     |
| Frontend Deploy       | OpenNext → CloudFront + Lambda@Edge + S3 — planned (Phase E)      |
| E2E Testing           | Playwright — planned (Phase E)                                    |
| Document Chunking     | Layout-aware hierarchical (Act→Part→Division→Section)             |
| Infrastructure        | Terraform + AWS Lambda + API Gateway (Docker container)           |
| Monitoring            | LangSmith tracing + CloudWatch + CloudWatch alarms                |
| Language              | Python 3.12+ / TypeScript                                         |
| Linting & Formatting  | Ruff / ESLint                                                     |
| CI/CD                 | GitHub Actions                                                    |

## System Architecture

### Agent Graph (Current)

```
                         ┌──────────────────────┐
                         │    intake_analyzer   │
                         │    (rule-based)      │
                         └──────────┬───────────┘
                                    │
                    ┌───────────────┼──────────────────┐
                    │               │                  │
                    ▼               ▼                  ▼
           ┌──────────────┐  ┌──────────────┐  ┌──────────────┐
           │  fallback    │  │ request_     │  │ query_       │
           │  _node       │  │ clarification│  │ rewriter     │
           │  → END       │  │ → END        │  │ (LLM)        │
           └──────────────┘  └──────────────┘  └──────┬───────┘
                                                      │
                                                      ▼
                                              ┌──────────────┐
                                              │ rag_retriever│
                                              │ (hybrid RRF) │
                                              └──────┬───────┘
                                                     │
                                          ┌──────────┼──────────┐
                                          │                     │
                                          ▼                     ▼
                                  ┌──────────────┐     ┌──────────────┐
                                  │  fallback    │     │ legal_       │
                                  │  _node → END │     │ reasoner     │
                                  └──────────────┘     │ (LLM IRAC)   │
                                                       └──────┬───────┘
                                                              │
                                                              ▼
                                                       ┌──────────────┐
                                                       │ citation_    │
                                                       │ verifier     │
                                                       │ (rule-based) │
                                                       └──────┬───────┘
                                                              │
                                                   ┌──────────┼──────────┐
                                                   │                     │
                                                   ▼                     ▼
                                           ┌──────────────┐     ┌──────────────┐
                                           │  fallback    │     │   __end__    │
                                           │  _node → END │     │ (response)   │
                                           └──────────────┘     └──────────────┘
```

### RAG Pipeline (internal)

```
rewrite_query → hybrid_retrieve → build_legal_prompt → LLM → verify_citations
```

### Full-Stack Deployment (Phase E)

```
                          CloudFront
                               │
                    ┌──────────┴──────────┐
                    │                     │
               ┌────▼────┐          ┌─────▼─────┐
               │   S3    │          │  Lambda@Edge│
               │ (static │          │  (SSR/ISR) │
               │  assets)│          │  Next.js    │
               └─────────┘          └────────────┘
                                         │ Next.js API routes
                                         │ (proxy to API Gateway)
                                         │
                                    API Gateway
                                    (rate-limited)
                                         │
                              ┌──────────┴──────────┐
                              │                     │
                         ┌────▼────┐          ┌─────▼─────┐
                         │  CRUD   │──Invoke──→  RAG API  │
                         │  Lambda │   (async)  │  Lambda  │
                         │ (NEW)   │            │ (Step 12)│
                         │ 128MB   │            │ 1024MB+  │
                         │ 3s      │            │ 60s      │
                         └───┬─────┘            │ Bedrock  │
                             │                  │ Qdrant   │
            ┌────────────────┼──────┐           └─────┬─────┘
            │                │      │                 │
       ┌────▼────┐     ┌─────▼──────▼──┐       ┌─────▼─────┐
       │Supabase │     │   Supabase    │       │    S3     │
       │  Auth   │     │  PostgreSQL   │       │ (large    │
       │ + RLS   │     │ (service_role │       │  state    │
       │(anon_key)│     │     key)     │       │snapshots) │
       └────┬────┘     └──────┬───────┘       └───────────┘
            │                 │
       ┌────▼────┐            │
       │Supabase │            │
       │Realtime │            │
       │ (WSS)   │            │
       └────┬────┘            │
            │                 │
       ┌────▼─────────────────▼────┐
       │         Client            │
       │ Auth reads → Supabase     │
       │ Realtime → Supabase       │
       │ CRUD writes → API Gateway │
       └───────────────────────────┘
```

*Phase A complete (RAGAS evaluation). Phase B (NSW ingestion + multi-state RAGAS) complete. Phase C Step 7 complete (LangGraph agent + interactive CLI). See full roadmap below.*

## Roadmap

### ✅ Phase A: Quality Foundation
| Step | Status | What |
|------|--------|------|
| 1 | ✅ | RAGAS evaluation on VIC (20 golden QA pairs, faithfulness + context precision + answer relevance) |
| 2 | ⏸️ | Embedding fine-tuning (BGE-small on legal contrastive pairs, Colab T4, Sentence Transformers) |
| 3 | ⏸️ | Re-evaluate with fine-tuned embeddings (compare baseline vs fine-tuned scores) |

Golden-context diagnostic confirmed retrieval quality is not the bottleneck — the current BGE-small + BM25 hybrid search already finds the right sections. Steps 2-3 are deferred until multi-state scaling reveals cross-jurisdiction retrieval gaps.

### Phase B: Scale to Multi-Jurisdiction
| Step | Status | What |
|------|--------|------|
| 4 | ✅ | NSW legislation ingestion (dedicated NSWParser, per-state architecture) |
| 5 | ❌ | QLD, SA, WA, TAS, ACT, NT — deferred (chunk quality issues: WA 95%, ACT 74%, QLD 37% bad chunks) |
| 6 | ✅ | Multi-state RAGAS evaluation (VIC 20Q + NSW 20Q, batch mode) |

### Phase C: Conversational Agent
| Step | Status | What |
|------|--------|------|
| 7 | ✅ | LangGraph agent (7-node state machine: intake_analyzer → request_clarification → query_rewriter → rag_retriever → legal_reasoner → citation_verifier → fallback_node) |
| 8 | ⬜ | Agent RAGAS evaluation (faithfulness + context precision on multi-turn scenarios) |
| 9 | ⬜ | LangSmith tracing (per-node latency/cost, trace replay, bottleneck identification) |

### Phase D: AWS Staging Deployment

> *Bedrock provider migration and hardening (baseline capture, threshold evaluation 30 runs, retrieval quality tests, VIC-10d live regression) were completed prior to the Architecture Gate. See [Completed](#-completed-featragas-vic) section below.*

| Step | Status | What |
| ---- | ------ | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 11   | ✅ | **Containerize Full Agent Runtime** — Build one Lambda-compatible `linux/amd64` image containing FastAPI, Mangum, the compiled seven-node LangGraph, all agent nodes, provider abstraction, retrieval dependencies, citation guard, FastEmbed assets and observability hooks. Bake immutable Qdrant and FastEmbed assets into the image, copy writable Qdrant state to `/tmp` at runtime, and never package credentials. Push the verified runtime image to ECR using an immutable Git SHA tag and capture its image digest. |
| 12   | ✅ | **Deploy Agent Runtime Lambda + API Gateway** — Deploy the complete LangGraph Agent through `graph.ainvoke()`, not only `generate_compliance_answer()` or a standalone RAG chain. Provide public `GET /health` and AWS-IAM-protected `POST /api/agent/invoke` for synchronous staging tests. Configure the Lambda container image by immutable ECR digest, Bedrock least-privilege IAM, static Qdrant runtime initialization, timeouts, structured logs, correlation IDs, API Gateway access logs and CloudWatch alarms. |
| 13   | ✅ | **Staging Operational Readiness & Release Automation** — Convert the successful manual deployment into a repeatable, auditable and reversible release workflow. Add clean-worktree and AWS-account preflight checks, immutable digest verification, Terraform state-key isolation and drift checks, public-health and anonymous-403 tests, SigV4-signed Agent smoke tests, Lambda/API log inspection, alarm verification, release checklists, deployment runbooks and rollback-by-digest plan generation. Fail closed on mutable image tags, wrong accounts, destructive Terraform plans, public Agent routes or failed signed invocation. Do not change Agent behaviour in this step. |
| 13a  | ✅ | **Agent-Level Safety Guardrails** — Apply strict request schema, input length and payload-size validation before graph invocation while retaining scope detection, clarification and fallback decisions inside the graph. Add PII detection and log redaction, trusted-instruction boundaries, prompt-injection resistance, safe error responses, citation-grounding warnings, uncertainty handling, jurisdiction and scope enforcement, approved legal disclaimers and least-privilege IAM verification. Add adversarial and regression tests, then rebuild, push and deploy the guarded Agent through the Step 13 release workflow. |

### Phase E: Full-Stack Chat Application

| Step | Status | What |
| ---- | ------ | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 14a  | ⬜ | **Local Stack and Database** — Create Docker Compose services for local Supabase, Qdrant, FastAPI and Next.js. Implement a SQLAlchemy 2.0 schema and Alembic migrations for users, conversations, messages, agent jobs and citations. Provision the Supabase cloud project only after the local schema and migration path are validated. |
| 14b  | ⬜ | **Auth and RLS** — Add email/password and Google OAuth through Supabase Auth. Verify JWTs using Supabase JWKS, implement ownership-based Row Level Security policies, define private Storage policies and isolate service-role credentials from browsers and user-scoped backend operations. |
| 14c  | ⬜ | **Backend Data Controls** — Use Supavisor transaction-mode runtime connections, scoped database clients, correlation IDs, atomic quota counters, bounded request sizes and structured error contracts. Ensure authorization is checked before every conversation, message, citation, job and file operation. |
| 15   | ⬜ | **CRUD Lambda** — Implement conversation and message APIs with cursor pagination, title updates, ownership checks and JWT 401/403 coverage. Add API Gateway throttling and structured errors. Start around 512 MB memory and a 10–15 second timeout, then tune from measured execution data. CRUD endpoints must not invoke retrieval or the Agent synchronously. |
| 16   | ⬜ | **Async Agent Orchestration** — CRUD creates the user message and an idempotent Agent job, then asynchronously invokes the complete deployed LangGraph Agent using identifiers and trusted metadata only. The worker must execute `graph.ainvoke()` across intake, clarification, query rewriting, retrieval, legal reasoning, citation verification and fallback rather than calling a standalone RAG or answer-generation function. Add explicit job-state transitions, idempotency keys, processing leases, bounded retries, DLQ or on-failure handling, stale-job recovery and correlation IDs. Persist the final assistant message, citations, clarification requirements, fallback outcome, execution metadata and safe error state atomically. |
| 17   | ⬜ | **Frontend Auth and Shell** — Build the Next.js App Router application using Tailwind and shadcn/ui. Add Supabase Auth, protected routes, secure session handling, the responsive sidebar shell and basic authenticated conversation CRUD integration. Browser clients must not receive AWS IAM credentials or call the Agent Runtime's AWS-IAM route directly. |
| 18   | ⬜ | **Chat UI and Realtime** — Implement controlled Realtime Broadcast or carefully scoped Postgres Changes for Agent job progress and completed messages. Add chunked response presentation, loading and retry states, clarification flows, fallback states, citation badges, conversation history, search, rename and delete. Treat database state as the authoritative source rather than maintaining a second client-only job state. |
| 18a  | ⬜ | **File Upload and Contract Analysis** — Use direct authenticated uploads to Supabase Storage or S3 with private ownership policies. Add file-type, size and malware validation; asynchronous PDF/JPG extraction; clause and tenancy-document analysis; and dual-source citations linking uploaded evidence and legislation. Never proxy large uploads through the synchronous Agent API or place file contents directly in asynchronous invocation events. |
| 19   | ⬜ | **Full CI/CD and E2E Gate** — Add Python, Node and Terraform pipelines covering tests, linting, migrations, IaC plans, immutable ECR image publication, runtime updates, Lambda aliases and environment promotion. Add Playwright E2E coverage for authentication, CRUD, Async Agent orchestration, clarification, fallback, Realtime updates, citations and authorization failures. Preserve manual approval before production infrastructure changes. |
| 20   | ⬜ | **Frontend AWS Deployment** — Run and document an OpenNext/SST compatibility spike, pin compatible versions and deploy Next.js through CloudFront, S3 and the required Lambda components. Add Route 53, ACM, cache policies, security headers and controlled cache invalidation only after the spike passes. |
| 21   | ⬜ | **Polish, Advanced Security and Observability** — Improve responsive UX, accessibility and dark mode. Add advanced PII controls, jailbreak and abuse monitoring, Sentry, CloudWatch dashboards, cost and anomaly alarms, performance metrics, data-retention controls and incident runbooks. This step extends the foundational Agent guardrails from Step 13a; it does not defer basic safety controls until the end. |

### Phase F: Market Intelligence
| Step | Status | What |
|------|--------|------|
| 22 | ⬜ | HTAG AI client (suburb-level rent/sale price data, in-memory cache) |
| 23 | ⬜ | Pricing specialist agent (separate LangGraph sub-graph, market guide system prompt) |
| 24 | ⬜ | Multi-agent supervisor (create_supervisor, route legislation vs pricing, cross-agent delegation) |
| 25 | ⬜ | Final multi-agent evaluation + red-teaming report |

### ✅ Completed (feat/ragas-vic)
| Step | What |
|------|------|
| 0 | VIC RTA PDF parser (PyMuPDF + regex, hierarchical chunking, TOKEN_THRESHOLD=2048) |
| 0 | Qdrant vector store (BGE-small + BM25, RRF fusion, local storage) |
| 0 | RAG pipeline (query rewrite → hybrid retrieve → LLM (DeepSeek) → citation verification) |
| 0 | IRAC-structured system prompt with practical next step guidance |
| 0 | Citation verification with subsection support `[VIC RTA 1997 Sec 91ZM(7)]` |
| Phase A | RAGAS evaluation suite (20 golden QA pairs, 3 metrics: faithfulness/context_precision/answer_relevancy) |
| Phase A | Pipeline improvements (Part metadata filter, reranker removal, AR disclaimer fix, citation trust signal) |
| Phase A | Golden-context diagnostic confirming LLM/format as faithfulness bottleneck |
| Phase B | NSW legislation ingestion + multi-state RAGAS evaluation |
| Phase B | VIC + NSW Regulation parsers (`vic_regulation_parser.py`, `nsw_regulation_parser.py`) with Regulation citation support (`[VIC REG 2021 Reg X]`, `[NSW REG 2019 Reg X]`, Schedule/Form citations `[VIC REG 2021 Sch 1 Form 6]`) |
| Phase B | Citation metrics evaluation (`citation_metrics.py`: deterministic citation precision, golden provision recall, Regulation indicators) |
| Phase C | LangGraph 7-node agent (`intake_analyzer` → `request_clarification` → `query_rewriter` → `rag_retriever` → `legal_reasoner` → `citation_verifier` → `fallback_node`) with 3 conditional routers |
| Phase C | Interactive streaming CLI (`python -m src.agent.cli`, MemorySaver checkpointing, per-session thread_id) |
| Phase D | Bedrock provider migration + hardening (`BedrockLLMProvider`, provider comparison harness, baseline capture 6 runs, threshold evaluation 30 runs, retrieval quality tests 7 corpus, VIC-10d live regression) |
| Phase D | Deployment Architecture Gate (Terraform, Lambda container, API Gateway + AWS_IAM, static Qdrant index) — [`docs/agent-deployment-architecture-gate.md`](docs/agent-deployment-architecture-gate.md) |

**Timeline:** ~30 steps, ~60-80 hours with AI assistance. Critical path: 11→12→13→16→20→25.

## Environment Variables

Copy `.env.example` to `.env` and populate all values:

```bash
cp .env.example .env
```

### LLM Provider Selection
| Variable       | Description                                        | Required |
| -------------- | -------------------------------------------------- | -------- |
| `LLM_PROVIDER` | `deepseek` (default when unset/blank) or `bedrock` | No       |

### DeepSeek (Current Dev, default)
| Variable            | Description           | Required |
| ------------------- | --------------------- | -------- |
| `DEEPSEEK_API_KEY`  | DeepSeek API key      | Yes      |
| `LLM_MODEL_ID`      | `deepseek-chat`        | Yes      |

### AWS Bedrock (Production)
| Variable                     | Description                                                            | Required |
| ---------------------------- | ---------------------------------------------------------------------- | -------- |
| `AWS_ACCESS_KEY_ID`          | AWS IAM access key                                                     | Yes*     |
| `AWS_SECRET_ACCESS_KEY`      | AWS IAM secret key                                                     | Yes*     |
| `AWS_REGION`                 | AWS region (e.g. `ap-southeast-2`); `AWS_DEFAULT_REGION` also accepted | Yes      |
| `BEDROCK_MODEL_ID`           | Bedrock model or inference profile ID — no default; verify model access first | Yes |
| `BEDROCK_TEMPERATURE`        | Optional default temperature (omitted from requests when blank)        | No       |
| `BEDROCK_MAX_TOKENS`         | Optional default max tokens (omitted from requests when blank)         | No       |
| `BEDROCK_EMBEDDING_MODEL_ID` | Titan embedding model ID                                               | Yes      |

\* Required only if not using another boto3 credential mechanism (SSO, shared profiles, IAM roles, credential_process).

### Qdrant
| Variable              | Description                     | Required |
| --------------------- | ------------------------------- | -------- |
| `QDRANT_URL`          | Qdrant cluster URL              | Yes      |
| `QDRANT_API_KEY`      | Qdrant API key                  | Yes      |
| `QDRANT_COLLECTION`   | Collection name for tenancy docs | Yes      |

### Phase E Configuration

The root `.env.example` is limited to the current Agent Runtime. Phase E
configuration ownership, browser-safe values, and server-only secrets are
defined in [the local development contract](docs/phase-e/local-development.md).

### Application
| Variable               | Description                       | Required |
| ---------------------- | --------------------------------- | -------- |
| `LANGSMITH_TRACING`   | Enable LangSmith tracing (default false) | No       |
| `LANGSMITH_API_KEY`    | LangSmith API key                 | No       |
| `LANGSMITH_PROJECT`    | LangSmith project name            | No       |
| `LANGSMITH_ENDPOINT`   | LangSmith endpoint URL            | No       |
| `LOG_LEVEL`            | Logging level (`INFO`/`DEBUG`)    | No       |
| `CHUNK_SIZE`           | Document chunk size (tokens)      | No       |
| `CHUNK_OVERLAP`        | Chunk overlap (tokens)            | No       |
| `TOP_K_RETRIEVAL`      | Number of chunks to retrieve      | No       |

## Getting Started

```bash
# Clone the repository
git clone https://github.com/your-org/AusTenancy.ai.git
cd AusTenancy.ai

# Create virtual environment
python -m venv .venv
source .venv/bin/activate

# Install dependencies
pip install -r requirements.txt

# Configure environment
cp .env.example .env
# Set DEEPSEEK_API_KEY in .env

# Step 1: Parse VIC RTA PDF into hierarchical chunks
python src/rag/data_processing/vic_parser.py

# Step 2: Index chunks into Qdrant vector store
python src/rag/retrieval/vector_store.py

# Step 3: Run RAG compliance pipeline
python src/rag/generation/generator.py

# Step 3b: Interactive multi-turn REPL (LangGraph agent)
python -m src.agent.cli

# Step 4: Run tests
pytest tests/                     # unit tests (integration excluded by default)
pytest tests/ -m integration -v   # real-service integration tests (needs DEEPSEEK_API_KEY + indexed Qdrant)
RUN_BEDROCK_INTEGRATION=1 pytest tests/test_bedrock_integration.py -m integration -v  # gated Bedrock tests (needs AWS credentials + region + BEDROCK_MODEL_ID)

# Step 5: Provider hardening and evaluation (optional)
python scripts/compare_providers.py --providers deepseek bedrock  # cross-provider comparison
python scripts/capture_baseline.py                                  # multi-stage pipeline tracing
python scripts/eval_arrears_thresholds.py                           # threshold evaluation (30 runs)
```

### Full-Stack Dev (Phase E)

```bash
# Step 14a: Start the local stack (requires supabase CLI and Docker)
cp apps/web/.env.local.example apps/web/.env.local
cp services/crud-api/.env.example services/crud-api/.env
cd services/crud-api && npm ci
cd apps/web && npm ci
supabase start
docker compose up --build

# Verify local schema (before provisioning any Supabase Cloud project)
supabase db reset
cd services/crud-api && npm test

# Individual service startup
cd services/crud-api && npm run start:dev   # http://localhost:3001/health
cd apps/web && npm run dev                  # http://localhost:3000
```

See [docs/phase-e/](docs/phase-e/README.md) for boundaries, configuration ownership, and local-stack contract.

See [Roadmap](#roadmap) above for complete development plan.

## Release Workflow

Every production code change follows a gated pipeline through
`scripts/ops/release.sh`:

```bash
bash scripts/ops/release.sh --allowed-account-id $ALLOWED_ACCOUNT_ID
```

### Pipeline

| Phase | Script | Gate |
|-------|--------|------|
| **Build** | `docker build` → container smoke → ECR push → capture digest | Fail-fast |
| **Preflight** | `scripts/ops/preflight.sh` — 10 checks (Git, AWS, Terraform, ECR, drift, secrets) | Fail-fast |
| **Plan** | `terraform plan -out=runtime-staging.tfplan` | Review required |
| **Apply** | `terraform apply` | **Manual Y/N prompt** |
| **Smoke** | `scripts/ops/smoke_test.py` — health 200, anonymous 403, SigV4 Agent invoke | Warning |
| **Verify** | `scripts/ops/verify_deployment.sh` — 16 infra checks | Warning |
| **Inspect** | `scripts/ops/inspect_logs.sh` — CloudWatch logs + alarms | Warning |

### Flags

| Flag | Effect |
|------|--------|
| `--dry-run` | Preflight + plan only (no apply, no smoke) |
| `--skip-build` | Skip Docker build + ECR push (use existing image) |
| `--skip-smoke` | Skip post-deploy smoke/verify/inspect |

### Individual scripts

```bash
bash scripts/ops/preflight.sh --expected-git-sha $(git rev-parse HEAD) --allowed-account-id $ALLOWED_ACCOUNT_ID
.venv/bin/python scripts/ops/smoke_test.py --health-url $URL --invoke-url $URL
bash scripts/ops/verify_deployment.sh
bash scripts/ops/inspect_logs.sh --minutes 15
bash scripts/ops/rollback.sh --digest sha256:<64-hex> --git-sha <40-char-sha>   # plan-only
```

### Safety

- Preflight **exits 1** on dirty git, wrong account, mutable image tags, or drift
- Apply is **always a manual Y/N prompt** — never auto-applies
- Rollback **exits 1** on destroys or unexpected resource changes — plan-only
- Log inspection **never outputs** legal answers or credentials

### Docker Build

Lambda requires a single-platform image manifest, not an OCI image index.
Always build with `--provenance=false`:

```bash
docker build --provenance=false --platform linux/amd64 -t austenancy-staging-agent:$GIT_SHA .
```

## LLM Providers

Generation (query rewriting + legal answers) runs behind a provider
abstraction (`src/rag/generation/llm_provider.py`). DeepSeek is the
default; AWS Bedrock (Converse API) is validated for AWS deployment:

```bash
# Default — DeepSeek (LLM_PROVIDER may be unset or blank)
LLM_PROVIDER=deepseek

# AWS Bedrock (requires region, model access, and boto3-resolvable credentials)
LLM_PROVIDER=bedrock
AWS_REGION=ap-southeast-2
BEDROCK_MODEL_ID=amazon.nova-pro-v1:0
```

**Validated model:** `amazon.nova-pro-v1:0` in `ap-southeast-2` (2026-07-22).

**Provider comparison (5-case benchmark):** Both providers achieve 100%
citation verification with zero unverified citations across all in-scope
cases. Bedrock latency is ~47% lower than DeepSeek (mean ~5.5s vs
~10.3s). Bedrock answers are more concise (~40% shorter); DeepSeek
answers are more detailed with higher citation density. One legal-reasoning
caveat observed: Bedrock on the VIC 10-day eviction case cited a verified
Regulation form but missed the Act's 14-day arrears threshold (s91ZM(7)),
producing an incorrect permissive conclusion. Full comparison report at
`reports/provider_comparison/`.

Notes:

- The DeepSeek path never imports boto3 or triggers AWS credential
  discovery; Bedrock clients are created lazily on first use.
- Retrieval, prompts, citation verification, and graph topology are
  identical across providers.
- Run the comparison harness (`python scripts/compare_providers.py
  --providers deepseek bedrock`) to reproduce results once both
  providers are configured.
- DeepSeek remains the default for local development; Bedrock is
  recommended for AWS Lambda deployment where lower latency and AWS
  native integration are preferred, with the accuracy caveat noted above.

## Observability

### Local Run Summaries

Run the agent with structured observability output:

```bash
python scripts/run_agent_observed.py \
  --question "Can my landlord increase rent by text message in VIC?"
```

This prints the final answer and a JSON run summary containing:

- **Retrieval stats**: context count, Act vs Regulation counts, top provisions
  with labels and scores
- **Citation stats**: verified/unverified citations, Act vs Regulation split,
  verification rate
- **Performance**: total latency in milliseconds
- **Status**: `success`, `fallback`, or `clarification`

Save summaries to disk:

```bash
python scripts/run_agent_observed.py --question "..." --save
# → reports/runs/run_20260718_143022.json
```

Full statutory text is **not** logged by default.

### LangSmith Tracing (Optional)

```bash
export LANGCHAIN_TRACING_V2=true
export LANGCHAIN_API_KEY=ls__your_key
export LANGCHAIN_PROJECT=aus-tenancy-agent
```

Then pass `--langsmith`:

```bash
python scripts/run_agent_observed.py --question "..." --langsmith
```

If LangSmith env vars are not set, the system runs normally — tracing is
purely opt-in.

## Development

See [CONTRIBUTING.md](./CONTRIBUTING.md) for branch strategy, commit conventions, and linting setup.

## License

See [LICENSE](./LICENSE).
