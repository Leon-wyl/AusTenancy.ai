# AusTenancy.ai

Australian Residential Tenancies Compliance Agent — a stateful, graph-based RAG system for jurisdiction-aware tenancy law queries.

## Governance

This agent must follow `CONTRIBUTING.md` for all branching, commit, linting, and PR conventions. When relevant to the task, consult documents in `docs/` (AGENT_WORKFLOW.md, ARCHITECTURE_DESIGN.md, PRD.md, EVALUATION_PLAN.md, LANGSMITH_SETUP.md) for design, architecture, evaluation, and workflow guidance.

## Tech Stack

| Layer | Technology |
|---|---|
| Orchestration | LangGraph (state graphs, multi-agent supervisor) — planned |
| Retrieval | Qdrant (dense BGE-small + BM25 hybrid, RRF fusion) |
| Embeddings | BGE-small-en-v1.5 via fastembed (fine-tuning planned) |
| Embeddings (prod) | Amazon Titan Text Embeddings v2 (via Bedrock) |
| LLM (current dev) | DeepSeek via OpenAI-compatible SDK |
| LLM (prod target) | Claude 3.5 Sonnet (via AWS Bedrock) |
| LLM (classifier) | Amazon Nova Lite — planned |
| Evaluation | RAGAS (faithfulness, context precision, answer relevance) |
| Auth + DB + Realtime | Supabase (PostgreSQL, JWT, RLS, WebSocket) — planned (Phase E) |
| Backend API | FastAPI on AWS Lambda (CRUD + RAG) — planned (Phase E) |
| ORM | SQLAlchemy 2.0 + asyncpg — planned (Phase E) |
| Migrations | Alembic — planned (Phase E) |
| Frontend | Next.js App Router + Tailwind + shadcn/ui — planned (Phase E) |
| Frontend Deploy | OpenNext → CloudFront + Lambda@Edge + S3 — planned (Phase E) |
| E2E Testing | Playwright — planned (Phase E) |
| Monitoring | LangSmith tracing + Sentry + CloudWatch |
| Deployment | AWS Lambda + API Gateway (Docker container) |
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
# Full-stack dev env (Phase E)
docker compose up          # Supabase local + Qdrant + FastAPI + Next.js
cd backend && alembic upgrade head  # Run DB migrations
cd frontend && npm install           # Install frontend deps
```

## Run

```bash
python src/data_processing/vic_parser.py        # Parse VIC RTA PDF → chunks
python src/retrieval/vector_store.py           # Index chunks → Qdrant
python src/generation/generator.py            # Run RAG compliance pipeline
pytest tests/ -m "not slow"                   # Run tests
```

```bash
# Full-stack (Phase E)
cd backend && uvicorn main:app --reload       # FastAPI CRUD API
cd frontend && npm run dev                     # Next.js dev server
```

## Architecture

### Current (Phase 3 — Linear RAG Pipeline)

```
rewrite_query → hybrid_retrieve → build_legal_prompt → LLM → verify_citations
```

### Planned (Phase C — LangGraph Agent)

LangGraph state machine with these nodes:

```
memory_recall → intent_classifier → slot_filler → rag_retriever → legal_reasoner → citation_verifier → fallback | final
```

- **State:** `TypedDict` for internal (node→node), Pydantic for boundaries (API, LLM output, tool I/O)
- **LLM prompt:** IRAC format (Issue → Rule → Application → Conclusion)
- **Tools (Converse API toolConfig):** `rag_retriever`, `date_calculator`, `rent_increase_validator`, `get_suburb_price_stats` (HTAG AI, planned)
- **Cross-session memory:** Mem0 for jurisdiction/role persistence
- **Multi-agent supervisor:** Route legislation vs pricing queries (Phase F, planned)

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
| 7 | ⬜ | LangGraph agent (7-node state machine) |
| 8 | ⬜ | Agent RAGAS evaluation |
| 9 | ⬜ | LangSmith tracing |

### Phase D: Production Deployment
| Step | Status | What |
|------|--------|------|
| 10 | ⬜ | Migrate to AWS Bedrock |
| 11 | ⬜ | Containerize (Docker + ECR) |
| 12 | ⬜ | Deploy Lambda + API Gateway (FastAPI + Mangum, RAG endpoint) |
| 12a | ⬜ | File upload & contract analysis (PDF/JPG parsing, clause extraction, dual-source citations) |
| 13 | ⬜ | Safety guardrails (PII detection, off-topic filter, jailbreak defense, citation grounding alert) |

### Phase E: Full-Stack Chat Application
| Step | Status | What |
|------|--------|------|
| 14 | ⬜ | **Local Dev & DB Foundation** — Docker Compose (Supabase local + Qdrant + FastAPI + Next.js). Schema via SQLAlchemy 2.0 + asyncpg (users, conversations, messages with status CHECK constraint, citations JSONB). Alembic migrations. Supabase cloud project. Auth (email/password + Google OAuth). JWT middleware with credential scoping (anon_key for CRUD + client, service_role_key for RAG). RLS policies. Rate limiting middleware (per-user 60 RPM, per-IP 30 RPM) with PostgreSQL counters. Correlation ID middleware (X-Request-ID → CloudWatch). Client boundary docs (auth reads → Supabase, CRUD writes → API Gateway, Realtime → Supabase). |
| 15 | ⬜ | **CRUD Lambda** — FastAPI 128MB/3s Lambda. REST: `GET/POST/DELETE /api/conversations`, `POST /api/conversations/{id}/messages`, `GET /api/conversations/{id}/messages?cursor=&limit=50` (cursor-based pagination), `PATCH title`. Title from first 50 chars of user message. API Gateway rate limit config. Integration tests (CRUD happy-path, JWT 401/403, Realtime subscription contract, rate limit enforcement). |
| 16 | ⬜ | **Wire CRUD → RAG (Supabase Realtime)** — Client POSTs message → CRUD creates placeholder (`status='generating'`) → fires Step 12 RAG Lambda via `Lambda.Invoke(InvocationType='Event')` (async, fire-and-forget) → returns immediately. RAG Lambda writes tokens to Supabase `messages.content` in ~1s batches, sets `status='complete'` on finish. Error: try/except → `status='error'` with error text, client shows toast. 120s client timeout if no status change. Client subscribes to Supabase Realtime WSS (`messages` table, filtered by conversation_id) → receives tokens with zero additional cost. RAG Lambda 60s timeout. Citation persistence: parse `[VIC RTA 1997 Sec X]` → `messages.citations` JSONB. LangGraph state as JSONB in conversations; >256KB → S3 snapshot with DB reference. Realtime sends full content per batch (not deltas) — <50KB total, acceptable. Real streaming (Function URL) documented as future enhancement. |
| 17 | ⬜ | **Frontend — Auth & Shell** — Next.js App Router + Tailwind + shadcn/ui. Supabase Auth React SDK (login, signup, logout). Supabase Realtime client (channel subscription). Protected route middleware. Layout shell: collapsible sidebar + main area. Session refresh on focus. SSR/Realtime split: Server Components fetch via Supabase SQL; Client Components subscribe to Realtime post-hydration. |
| 18 | ⬜ | **Frontend — Chat UI** — Conversation sidebar (history by recency, search/filter, delete with confirmation, inline rename). Chat view (message bubbles, auto-scroll, loading skeleton, error toast on generation failure). Markdown rendering: react-markdown + remark-gfm. Citation badges: styled link badges for `[VIC RTA 1997 Sec X]` (no excerpts). New chat: auto-create conversation on first message. Title sync: POST response body includes auto-generated title. Timeout handling: error toast if status stays 'generating' past 120s. |
| 19 | ⬜ | **Core CI/CD** — GitHub Actions parallel jobs: Python (Ruff → mypy → CRUD Lambda integration tests) + Node (ESLint → tsc → Next.js build). Pre-deploy: `alembic upgrade head` against Supabase (schema matches code before Lambda update). Package CRUD Lambda Docker image → push ECR → update Lambda. **E2E Playwright gate:** signup → login → session persists → create conversation → send message → receive token stream via Realtime → citations rendered → completion → conversation rename → delete → confirmed gone. Phase E not complete until gate passes. |
| 20 | ⬜ | **OpenNext Deployment** — OpenNext: Next.js → Lambda@Edge (SSR/ISR) + CloudFront (CDN) + S3 (static). Route53 custom domain + ACM SSL. CI/CD: deploy frontend on push to main + CloudFront cache invalidation. Env: SUPABASE_URL/ANON_KEY → Next.js + CRUD Lambda. SUPABASE_SERVICE_ROLE_KEY → RAG Lambda only. BEDROCK creds → RAG Lambda only. Never client-side. |
| 21 | ⬜ | **Polish & Observability** — Empty/error/rate-limited states. Toast notifications. Responsive (mobile sidebar drawer). Dark mode + system preference. Sentry SDK (CRUD + RAG Lambda + Next.js). CloudWatch dashboard (Lambda invocations/errors/duration, API Gateway 4xx/5xx). Optional: Vercel Analytics. |

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

**Timeline:** ~25 steps, ~50-70 hours with AI assistance. Critical path: 1→7→12→16→20→25.

## Project Structure

```
src/                          # RAG pipeline (Python)
  data_processing/            # PDF parsing → hierarchical chunks
    vic_parser.py             #   VIC RTA PDF parser (PyMuPDF + regex)
    nsw_parser.py             #   NSW RTA PDF parser
    base_parser.py            #   Abstract BaseParser (shared pipeline)
  retrieval/                  # Vector store indexing + hybrid search
    vector_store.py           #   Qdrant ingestion with dense + BM25
  generation/                 # RAG compliance pipeline
    generator.py              #   Query rewrite → retrieve → LLM → citation verify
  evaluation/                 # RAGAS evaluation scripts + golden dataset
  pricing/                    # Market intelligence (Phase F)
    htag_client.py            #   HTAG AI API client
  processing/                 # Document parsing (planned)
    document_parser.py        #   PDF/JPG extraction, clause metadata
backend/                      # FastAPI CRUD Lambda (Phase E)
  alembic/                    # Alembic migrations
  app/
    api/                      # REST endpoints
    middleware/                # JWT, rate limiting, correlation ID
    models/                   # SQLAlchemy models
    schemas/                  # Pydantic schemas
frontend/                     # Next.js app (Phase E)
  app/                        # App Router pages + layouts
  components/                 # React components (sidebar, chat, citations)
  lib/                        # Supabase client, auth helpers, SSE client
api/                          # FastAPI + Lambda handler (planned)
tests/                        # Pytest suite
docs/                         # Design docs, PRD, workflows
data/raw/                     # PDF legislation files (gitignored)
data/processed/               # Generated hierarchical chunks (gitignored)
qdrant_storage/               # Local Qdrant database (gitignored)
docker-compose.yml            # Phase E: local dev env (Supabase + Qdrant + FastAPI + Next.js)
agent.md                      # This file
```
