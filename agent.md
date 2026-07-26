# AusTenancy.ai

Australian Residential Tenancies Compliance Agent — a stateful, graph-based RAG system for jurisdiction-aware tenancy law queries.

## Governance

This agent must follow `CONTRIBUTING.md` for all branching, commit, linting, and PR conventions. When relevant to the task, consult documents in `docs/` (AGENT_WORKFLOW.md, ARCHITECTURE_DESIGN.md, PRD.md, EVALUATION_PLAN.md, LANGSMITH_SETUP.md) for design, architecture, evaluation, and workflow guidance.

## Tech Stack

| Layer | Technology |
|---|---|
| Orchestration | LangGraph (7-node state machine, 3 conditional routers) — built; multi-agent supervisor planned (Phase F) |
| Retrieval | Qdrant (dense BGE-small + BM25 hybrid, RRF fusion) |
| Embeddings | BGE-small-en-v1.5 via fastembed (fine-tuning planned) |
| Embeddings (prod) | Amazon Titan Text Embeddings v2 (via Bedrock) |
| LLM (current dev) | DeepSeek via OpenAI-compatible SDK (default); AWS Bedrock Converse validated for staging (`LLM_PROVIDER=bedrock`, `BEDROCK_TEMPERATURE=0`) |
| LLM (prod target) | Amazon Nova Pro v1:0 (via AWS Bedrock, ap-southeast-2) |
| LLM (classifier) | Amazon Nova Lite — planned |
| Evaluation | RAGAS (faithfulness, context precision, answer relevance) |
| Auth + DB + Realtime | Supabase (PostgreSQL, JWT, RLS, WebSocket) — planned (Phase E) |
| Backend API | FastAPI on AWS Lambda (CRUD + RAG) — planned (Phase E) |
| ORM | SQLAlchemy 2.0 + asyncpg — planned (Phase E) |
| Migrations | Alembic — planned (Phase E) |
| Frontend | Next.js App Router + Tailwind + shadcn/ui — planned (Phase E) |
| Frontend Deploy | OpenNext → CloudFront + Lambda@Edge + S3 — planned (Phase E) |
| E2E Testing | Playwright — planned (Phase E) |
| Monitoring | LangSmith tracing + CloudWatch + CloudWatch alarms |
| Deployment | Terraform + AWS Lambda + API Gateway (Docker container) |
| CI/CD | GitHub Actions |
| Language | Python 3.12+ |
| Lint/Format | Ruff |

## Setup

```bash
# Core RAG pipeline
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# Set DEEPSEEK_API_KEY in .env
```

```bash
# Phase E — not yet built
# Full-stack dev env (Phase E)
docker compose up          # Supabase local + Qdrant + FastAPI + Next.js
cd backend && alembic upgrade head  # Run DB migrations
cd frontend && npm install           # Install frontend deps
```

## Run

```bash
python src/rag/data_processing/vic_parser.py        # Parse VIC RTA PDF → chunks
python src/rag/retrieval/vector_store.py           # Index chunks → Qdrant
python src/rag/generation/generator.py            # Run RAG compliance pipeline
python -m src.agent.cli                      # Interactive multi-turn REPL
pytest tests/                    # unit tests (integration excluded by default)
pytest tests/ -m integration -v  # real-service integration tests (needs DEEPSEEK_API_KEY + indexed Qdrant)
RUN_BEDROCK_INTEGRATION=1 pytest tests/test_bedrock_integration.py -m integration -v  # gated Bedrock tests

# Provider hardening and evaluation (optional)
python scripts/compare_providers.py --providers deepseek bedrock  # cross-provider comparison
python scripts/capture_baseline.py                                  # multi-stage pipeline tracing
python scripts/eval_arrears_thresholds.py                           # threshold evaluation (30 runs)
```

```bash
# Phase E — not yet built
# Full-stack (Phase E)
cd backend && uvicorn main:app --reload       # FastAPI CRUD API
cd frontend && npm run dev                     # Next.js dev server
```

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

## Architecture

### Current (Phase C — LangGraph Agent)

7-node LangGraph state machine with 3 conditional routers:

```
intake_analyzer → request_clarification | query_rewriter → rag_retriever → legal_reasoner → citation_verifier → fallback_node
```

- **State:** `AgentState` TypedDict (13 fields) — see `src/agent/state.py`
- **Routers:** `route_after_intake` (fallback/clarification/rewriter), `route_after_retrieval` (fallback/reasoner), `route_after_verify` (fallback/__end__)
- **CLI:** Interactive multi-turn REPL with `MemorySaver` checkpointing + per-session `thread_id` (`python -m src.agent.cli`)
- **LLM prompt:** IRAC format (Issue → Rule → Application → Conclusion)

### Planned (future phases)

- **Bedrock toolConfig tools** — Converse API function calling for `rag_retriever`, `date_calculator`, `rent_increase_validator` (Phase D) — `[STATUS: planned — not implemented]`
- **Mem0 cross-session memory** — jurisdiction/role persistence across sessions (Phase C Step 9, deferred) — `[STATUS: planned — not implemented]`
- **Multi-agent supervisor** — route legislation vs pricing queries (Phase F) — `[STATUS: planned — not implemented]`

### Planned (Phase E — Full-Stack Deployment Architecture)

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

## Critical Rules

1. **Jurisdiction filter always** — metadata pre-filter before any retrieval. Never return non-requested state results.
2. **Every claim needs a citation** — citation verifier cross-checks LLM output against retrieved chunks. Strip or flag uncited claims.
3. **No citation hallucination** — if a section isn't in the retrieved set, output uncertainty, not a fabricated citation.
4. **Pydantic at boundaries** — validate LLM extraction, API input, and tool results with Pydantic. Internal state uses lightweight TypedDict.
5. **Dual-source citations** — cross-reference uploaded documents against legislation. Flag contradictions. Format: [Contract, Clause X] + [VIC RTA 1997 Sec Y].

## Roadmap

### ✅ Phase A: Quality Foundation
| Step | Status | What |
|------|--------|------|
| 1 | ✅ | RAGAS evaluation on VIC (20 golden QA pairs) |
| 2 | ⏸️ | Embedding fine-tuning on legal contrastive pairs |
| 3 | ⏸️ | Re-evaluate with fine-tuned embeddings |

Golden-context diagnostic confirmed retrieval quality is not the bottleneck — the current BGE-small + BM25 hybrid search already finds the right sections. Steps 2-3 are deferred until multi-state scaling reveals cross-jurisdiction retrieval gaps that a better embedding model would address.

### Phase B: Scale to Multi-Jurisdiction
| Step | Status | What |
|------|--------|------|
| 4 | ✅ | NSW legislation ingestion (NSWParser, per-state architecture) |
| 5 | ❌ | QLD, SA, WA, TAS, ACT, NT — deferred (chunk quality issues) |
| 6 | ✅ | Multi-state RAGAS evaluation (VIC 20Q + NSW 20Q) |

### Phase C: Conversational Agent
| Step | Status | What |
|------|--------|------|
| 7 | ✅ | LangGraph agent (7-node state machine: intake_analyzer → request_clarification → query_rewriter → rag_retriever → legal_reasoner → citation_verifier → fallback_node) |
| 8 | ⬜ | Agent RAGAS evaluation |
| 9 | ⬜ | LangSmith tracing |

### Phase D: AWS Staging Deployment

> *Bedrock provider migration and hardening (baseline capture, threshold evaluation 30 runs, retrieval quality tests, VIC-10d live regression) were completed prior to the Architecture Gate. See [Completed](#-completed) section below.*

| Step | Status | What |
| ---- | ------ | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 11   | ✅ | **Containerize Full Agent Runtime** — Build one Lambda-compatible `linux/amd64` image containing FastAPI, Mangum, the compiled seven-node LangGraph, all agent nodes, provider abstraction, retrieval dependencies, citation guard, FastEmbed assets and observability hooks. Bake immutable Qdrant and FastEmbed assets into the image, copy writable Qdrant state to `/tmp` at runtime, and never package credentials. Push the verified runtime image to ECR using an immutable Git SHA tag and capture its image digest. |
| 12   | ✅ | **Deploy Agent Runtime Lambda + API Gateway** — Deploy the complete LangGraph Agent through `graph.ainvoke()`, not only `generate_compliance_answer()` or a standalone RAG chain. Provide public `GET /health` and AWS-IAM-protected `POST /api/agent/invoke` for synchronous staging tests. Configure the Lambda container image by immutable ECR digest, Bedrock least-privilege IAM, static Qdrant runtime initialization, timeouts, structured logs, correlation IDs, API Gateway access logs and CloudWatch alarms. |
| 13   | ⬜ | **Staging Operational Readiness & Release Automation** — Convert the successful manual deployment into a repeatable, auditable and reversible release workflow. Add clean-worktree and AWS-account preflight checks, immutable digest verification, Terraform state-key isolation and drift checks, public-health and anonymous-403 tests, SigV4-signed Agent smoke tests, Lambda/API log inspection, alarm verification, release checklists, deployment runbooks and rollback-by-digest plan generation. Fail closed on mutable image tags, wrong accounts, destructive Terraform plans, public Agent routes or failed signed invocation. Do not change Agent behaviour in this step. |
| 13a  | ⬜ | **Agent-Level Safety Guardrails** — Apply strict request schema, input length and payload-size validation before graph invocation while retaining scope detection, clarification and fallback decisions inside the graph. Add PII detection and log redaction, trusted-instruction boundaries, prompt-injection resistance, safe error responses, citation-grounding warnings, uncertainty handling, jurisdiction and scope enforcement, approved legal disclaimers and least-privilege IAM verification. Add adversarial and regression tests, then rebuild, push and deploy the guarded Agent through the Step 13 release workflow. |

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

### ✅ Completed
| Phase | What |
|-------|------|
| 0 | VIC RTA PDF parser (PyMuPDF + regex, hierarchical chunking) |
| 0 | Qdrant vector store (BGE-small + BM25, RRF fusion) |
| 0 | RAG pipeline (query rewrite → hybrid retrieve → LLM → citation verify) |
| 0 | Citation verification with subsection support |
| Phase A | RAGAS evaluation suite (20 golden QA pairs, faithfulness/context_precision/answer_relevancy, ablation study, golden-context diagnostic) |
| Phase A | Pipeline improvements (Part metadata filter, reranker removal, AR disclaimer fix, citation trust signal) |
| Phase A | IRAC format retained over two-section alternative (industry standard for legal analysis; faithfulness ceiling is a metric problem, not a format problem) |
| Phase B | NSW legislation ingestion + multi-state RAGAS evaluation |
| Phase B | VIC + NSW Regulation parsers with Regulation citation support (`[VIC REG 2021 Reg X]`, `[NSW REG 2019 Reg X]`, `[VIC REG 2021 Sch 1 Form 6]`) |
| Phase B | Citation metrics evaluation (`citation_metrics.py`: deterministic citation precision, golden provision recall, Regulation indicators) |
| Phase C | LangGraph 7-node agent + interactive streaming CLI |
| Phase D | Bedrock provider migration + hardening (`BedrockLLMProvider`, provider comparison harness, baseline capture 6 runs, threshold evaluation 30 runs, retrieval quality tests 7 corpus, VIC-10d live regression) |
| Phase D | Deployment Architecture Gate (Terraform, Lambda container, API Gateway + AWS_IAM, static Qdrant index) — [`docs/agent-deployment-architecture-gate.md`](docs/agent-deployment-architecture-gate.md) |

**Timeline:** ~30 steps, ~60-80 hours with AI assistance. Critical path: 11→12→13→16→20→25.

## Project Structure

```
src/                          # RAG pipeline + agent (Python)
  rag/data_processing/        # PDF parsing → hierarchical chunks
    vic_parser.py             #   VIC RTA PDF parser (PyMuPDF + regex)
    nsw_parser.py             #   NSW RTA PDF parser
    base_parser.py            #   Abstract BaseParser (shared pipeline)
    vic_regulation_parser.py  #   VIC Regulations 2021 parser
    nsw_regulation_parser.py  #   NSW Regulations 2019 parser
  rag/retrieval/              # Vector store indexing + hybrid search
    vector_store.py           #   Qdrant ingestion with dense + BM25
  rag/generation/             # RAG compliance pipeline
    generator.py              #   Query rewrite → retrieve → LLM → citation verify
  rag/evaluation/             # RAGAS evaluation scripts + golden dataset
    run_ragas_eval.py         #   Evaluation runner with CLI flags
    citation_metrics.py       #   Deterministic citation metrics
    gen_golden_contexts.py    #   Golden context pre-computation
    audit_sections.py         #   Section audit utility
  agent/                      # LangGraph agent
    state.py                  #   AgentState TypedDict (13 fields)
    graph_skeleton.py         #   7-node state machine, 3 routers
    cli.py                    #   Interactive multi-turn REPL
    observability.py          #   Run summaries, LangSmith config
tests/                        # Pytest suite
  evaluation/                 # Golden datasets + contexts
scripts/                      # Utility and evaluation scripts
  run_agent_observed.py       #   One-shot agent run with JSON summary
  compare_providers.py        #   DeepSeek vs Bedrock comparison harness
  capture_baseline.py         #   Multi-stage pipeline tracing
  eval_arrears_thresholds.py  #   Threshold evaluation (VIC/NSW arrears cases)
docs/                         # Design docs, PRD, workflows, architecture gate
data/raw/                     # PDF legislation files (gitignored)
data/processed/               # Generated hierarchical chunks (gitignored)
qdrant_storage/               # Local Qdrant database (gitignored)
docker-compose.yml            # Phase E: local dev env
agent.md                      # This file
```
