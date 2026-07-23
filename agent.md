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
| ---- | ------ | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 10.9 | ✅ | **Deployment Architecture Gate** — Topology: static local Qdrant index (conditional on container PoC). IaC: Terraform (3 stacks: bootstrap/foundation/runtime). API contracts: AgentRequest/AgentResponse v1.0, POST /api/agent/invoke (AWS_IAM), GET /health (unauthenticated). Bedrock-only staging. Design doc: [`docs/agent-deployment-architecture-gate.md`](docs/agent-deployment-architecture-gate.md). |
| 11   | ⬜ | **Containerize and ECR** — Lambda-compatible Dockerfile, FastEmbed models baked in, multi-stage build, non-secret configuration, image-size and cold-start measurements, local Lambda-runtime testing (ASGI + RIE modes), ECR push. Minimal CI for tests, Ruff and image build. |
| 12   | ⬜ | **Deploy RAG Staging Lambda** — FastAPI + Mangum for health and buffered staging requests. Direct Lambda smoke invocation with API Gateway v2 event payload. API Gateway HTTP API staging endpoint (AWS_IAM auth). Explicit Bedrock IAM role scoped to exact foundation model. Structured logging, request IDs, 60s Lambda timeout, CloudWatch alarms. |
| 13   | ⬜ | **Baseline Safety and Operational Guardrails** — Input limits, authentication boundary, content-type controls, log redaction, citation warning, legal disclaimer, basic prompt-injection isolation, IAM least privilege, API throttling and CloudWatch alarms. |

### Phase E: Full-Stack Chat Application

| Step | Status | What |
| ---- | ------ | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 14a  | ⬜ | **Local Stack and Database** — Docker Compose for Supabase local, Qdrant, FastAPI and Next.js. SQLAlchemy 2.0 schema, Alembic migrations, conversations, messages, jobs and citations. Create Supabase cloud project. |
| 14b  | ⬜ | **Auth and RLS** — Email/password and Google OAuth, JWT verification using Supabase JWKS, ownership-based RLS policies, Storage policies and service-role isolation. |
| 14c  | ⬜ | **Backend Data Controls** — Supavisor transaction-mode runtime connections, scoped database clients, correlation IDs, atomic quota counters and structured error contracts. |
| 15   | ⬜ | **CRUD Lambda** — Conversation and message APIs with cursor pagination, title updates, JWT 401/403 coverage and API Gateway throttling. Start around 512 MB and 10–15 seconds, then tune from measurements. |
| 16   | ⬜ | **Async RAG Orchestration** — CRUD creates a message and job, then invokes RAG asynchronously using IDs only. Add idempotency, retries, DLQ/on-failure destination, processing leases and stale-job recovery. Persist final answer and citations. |
| 17   | ⬜ | **Frontend Auth and Shell** — Next.js App Router, Tailwind, shadcn/ui, protected routes, Supabase Auth, sidebar shell and basic CRUD integration. |
| 18   | ⬜ | **Chat UI and Realtime** — Realtime Broadcast or controlled Postgres Changes, chunked response rendering, loading and error states, citation badges, history, search, rename and delete. |
| 18a  | ⬜ | **File Upload and Contract Analysis** — Direct authenticated upload to Supabase Storage or S3, private RLS policies, file validation and scanning, asynchronous PDF/JPG extraction, clause analysis and dual-source citations. Never proxy large uploads through the RAG API. |
| 19   | ⬜ | **Full CI/CD and E2E Gate** — Python and Node pipelines, migrations, IaC deployment, ECR updates, Lambda aliases, environment promotion and Playwright E2E tests covering auth, CRUD, async RAG, Realtime and citations. |
| 20   | ⬜ | **Frontend AWS Deployment** — Run an OpenNext/SST compatibility spike, pin versions, then deploy Next.js through CloudFront, S3 and the required Lambda components. Add Route53, ACM and cache invalidation. |
| 21   | ⬜ | **Polish, Security and Observability** — Responsive UX, dark mode, advanced PII controls, jailbreak monitoring, Sentry, CloudWatch dashboards, cost alarms, performance metrics and incident runbooks. |

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

**Timeline:** ~30 steps, ~60-80 hours with AI assistance. Critical path: 10.9→11→12→16→20→25.

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
