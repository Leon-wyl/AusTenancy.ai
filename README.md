# AusTenancy.ai — Australian Residential Tenancies Compliance Agent

A stateful, graph-based RAG Agent delivering high-precision compliance queries for Australian state Residential Tenancies Acts (VIC, NSW, QLD, and others). Built with LangGraph and AWS Bedrock, the system implements layout-aware hierarchical chunking (Act→Part→Section) to preserve legal context and enforce strict citation grounding.

## Value Proposition

- **Zero cross-state hallucination** — metadata-filtered retrieval ensures answers are grounded in the correct jurisdiction's legislation.
- **Stateful multi-turn reasoning** — LangGraph manages conversation state, enabling follow-up questions that respect prior context.
- **Production-grade retrieval** — hybrid search (dense vector + BM25) via Qdrant. Embeddings fine-tuned on legal text for maximum retrieval precision.
- **Strict citation grounding** — every claim verified against retrieved chunks with `[State RTA Year Sec X]` format enforcement.
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
| Backend API           | FastAPI on AWS Lambda (CRUD 128MB + RAG 1024MB+) — planned        |
| ORM + Migrations      | SQLAlchemy 2.0 + asyncpg / Alembic — planned (Phase E)            |
| Frontend              | Next.js App Router + Tailwind + shadcn/ui — planned (Phase E)     |
| Frontend Deploy       | OpenNext → CloudFront + Lambda@Edge + S3 — planned (Phase E)      |
| E2E Testing           | Playwright — planned (Phase E)                                    |
| Document Chunking     | Layout-aware hierarchical (Act→Part→Division→Section)             |
| Infrastructure        | AWS Lambda + API Gateway (Docker container)                       |
| Monitoring            | LangSmith tracing + Sentry + CloudWatch                           |
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

### Phase D: Production Deployment
| Step | Status | What |
|------|--------|------|
| 10 | 🔶 | Migrate to AWS Bedrock — `BedrockLLMProvider` (Converse API) + `LLM_PROVIDER` selection built and offline-tested; real AWS validation pending (env setup, model access, provider comparison) |
| 11 | ⬜ | Containerize (Dockerfile, fastembed models baked in, push to ECR) |
| 12 | ⬜ | Deploy Lambda + API Gateway (FastAPI + Mangum, RAG endpoint) |
| 12a | ⬜ | File upload & contract analysis (PDF/JPG parsing, clause extraction, dual-source citation [Contract, Clause X] + [VIC RTA 1997 Sec Y], cross-reference detection) |
| 13 | ⬜ | Safety guardrails (PII detection, off-topic filter, jailbreak defense, citation grounding alert) |

### Phase E: Full-Stack Chat Application
| Step | Status | What |
|------|--------|------|
| 14 | ⬜ | **Local Dev & DB Foundation** — Docker Compose (Supabase local + Qdrant + FastAPI CRUD + Next.js). SQLAlchemy 2.0 + asyncpg schema (users, conversations, messages with status CHECK constraint, citations JSONB). Alembic migrations. Supabase cloud project. Auth (email/password + Google OAuth). FastAPI JWT middleware with credential scoping (anon_key for CRUD + client, service_role_key for RAG). RLS policies. Rate limiting with PostgreSQL counters. Correlation ID middleware. |
| 15 | ⬜ | **CRUD Lambda** — FastAPI 128MB/3s Lambda. REST: `GET/POST/DELETE /api/conversations`, `POST/GET /api/conversations/{id}/messages` (cursor pagination, limit=50), `PATCH title`. Title from first 50 chars. API Gateway rate limits. Integration tests (CRUD, JWT 401/403, Realtime contract, rate limit). |
| 16 | ⬜ | **Wire CRUD → RAG (Supabase Realtime)** — Client POSTs message → CRUD creates placeholder (`status='generating'`) → async invoke Step 12 RAG Lambda. RAG Lambda writes tokens to `messages.content` in ~1s batches via service_role_key, sets `status='complete'`. Error: try/except → `status='error'` + toast. Client subscribes to Supabase Realtime WSS → token-by-token render, zero cost. 60s RAG Lambda timeout, 120s client timeout. Citations stored in `messages.citations` JSONB. LangGraph state JSONB → S3 snapshot if >256KB. |
| 17 | ⬜ | **Frontend — Auth & Shell** — Next.js App Router + Tailwind + shadcn/ui. Supabase Auth React SDK. Supabase Realtime client. Protected routes. Sidebar + main area layout. SSR fetches via SQL; Client hydrates Realtime subscriptions. |
| 18 | ⬜ | **Frontend — Chat UI** — Sidebar (history, search, delete, rename). Chat view (bubbles, auto-scroll, loading skeleton, error toast). react-markdown + remark-gfm for IRAC. Citation badges. Auto-create conversation on first message. 120s timeout handling. |
| 19 | ⬜ | **Core CI/CD** — GitHub Actions parallel: Python (Ruff → mypy → CRUD integration tests) + Node (ESLint → tsc → Next.js build). `alembic upgrade head` pre-deploy. Push CRUD Docker → ECR → update Lambda. E2E Playwright gate (auth → message → Realtime stream → citations → CRUD → delete). Phase E blocked until gate passes. |
| 20 | ⬜ | **OpenNext Deployment** — Next.js → Lambda@Edge + CloudFront + S3. Route53 custom domain + ACM SSL. CI/CD push-to-deploy + CloudFront invalidation. Env: never expose service_role_key client-side. |
| 21 | ⬜ | **Polish & Observability** — Empty/error/rate-limited states. Toast notifications. Responsive mobile drawer. Dark mode. Sentry (CRUD + RAG + Next.js). CloudWatch dashboard. Optional Vercel Analytics. |

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

**Timeline:** ~25 steps, ~50-70 hours with AI assistance. Critical path: 8→12→16→20→25.

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

### Supabase (Phase E)
| Variable                     | Description                          | Required |
| ---------------------------- | ------------------------------------ | -------- |
| `SUPABASE_URL`               | Supabase project URL                 | Yes      |
| `SUPABASE_ANON_KEY`           | Public anon key (client + CRUD Lambda) | Yes    |
| `SUPABASE_SERVICE_ROLE_KEY`   | Secret service_role key (RAG Lambda only) | Yes  |

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
```

### Full-Stack Dev (Phase E)

```bash
# Phase E — not yet built
# Start local dev environment
docker compose up

# Run DB migrations
cd backend && alembic upgrade head

# Backend API
cd backend && uvicorn app.main:app --reload

# Frontend
cd frontend && npm install && npm run dev
```

See [Roadmap](#roadmap) above for complete development plan.

## LLM Providers

Generation (query rewriting + legal answers) runs behind a provider
abstraction (`src/rag/generation/llm_provider.py`). DeepSeek is the
default; AWS Bedrock (Converse API) is selectable via environment:

```bash
# Default — DeepSeek (LLM_PROVIDER may be unset or blank)
LLM_PROVIDER=deepseek

# AWS Bedrock (requires region, model access, and boto3-resolvable credentials)
LLM_PROVIDER=bedrock
AWS_REGION=ap-southeast-2
BEDROCK_MODEL_ID=<your-model-or-inference-profile-id>
```

Notes:

- The DeepSeek path never imports boto3 or triggers AWS credential
  discovery; Bedrock clients are created lazily on first use.
- Retrieval, prompts, citation verification, and graph topology are
  identical across providers.
- Real Bedrock behaviour is validated by gated integration tests
  (`RUN_BEDROCK_INTEGRATION=1`, see Getting Started) and the provider
  comparison harness (`python scripts/compare_providers.py --providers
  deepseek bedrock`) once AWS is configured. Until then, Bedrock
  latency/cost/answer quality are unavailable — not fabricated.

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
