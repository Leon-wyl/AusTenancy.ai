# AusTenancy.ai — Australian Residential Tenancies Compliance Agent

A stateful, graph-based RAG Agent delivering high-precision compliance queries for Australian state Residential Tenancies Acts (VIC, NSW, QLD, and others). Built with LangGraph and AWS Bedrock, the system implements layout-aware hierarchical chunking (Act→Part→Section) to preserve legal context and enforce strict citation grounding.

## Value Proposition

- **Zero cross-state hallucination** — metadata-filtered retrieval ensures answers are grounded in the correct jurisdiction's legislation.
- **Stateful multi-turn reasoning** — LangGraph manages conversation state, enabling follow-up questions that respect prior context.
- **Production-grade retrieval** — hybrid search (dense vector + BM25) via Qdrant. Embeddings fine-tuned on legal text for maximum retrieval precision.
- **Strict citation grounding** — every claim verified against retrieved chunks with `[State RTA Year Sec X]` format enforcement.
- **Enterprise compliance ready** — designed for Lambda + API Gateway deployment, swappable LLM backend (DeepSeek dev → Bedrock prod).

## Technical Stack

| Layer                 | Technology                                                        |
| --------------------- | ----------------------------------------------------------------- |
| Orchestration         | LangGraph (state graphs, multi-agent supervisor) — planned        |
| Retrieval             | Qdrant (dense + BM25 hybrid with RRF fusion)                      |
| Embeddings            | BGE-small-en-v1.5 via fastembed (fine-tuned on legal text planned)|
| Embeddings (prod)     | Amazon Titan Text Embeddings v2 (via Bedrock)                     |
| LLM (current dev)     | DeepSeek (OpenAI-compatible SDK, swappable to Bedrock)            |
| LLM (prod target)     | Anthropic Claude 3.5 Sonnet (via AWS Bedrock)                     |
| Evaluation            | RAGAS (faithfulness, context precision, answer relevance)         |
| Document Chunking     | Layout-aware hierarchical (Act→Part→Division→Section)             |
| Infrastructure        | AWS Lambda + API Gateway (Docker container)                       |
| Monitoring            | LangSmith tracing (planned, post-LangGraph)                       |
| Language              | Python 3.12+                                                      |
| Linting & Formatting  | Ruff                                                              |
| CI/CD                 | GitHub Actions                                                    |

## Modular System Architecture

```
┌─────────────────────────────────────────────────────────┐
│                    User Interface                        │
│            (REST API / WebSocket / Chat UI)              │
└─────────────────────┬───────────────────────────────────┘
                      │
┌─────────────────────▼───────────────────────────────────┐
│          Multi-Agent Supervisor (planned)                │
│    (legislation specialist + pricing specialist)         │
└──────┬─────────────────────────────────────┬────────────┘
       │                                     │
┌──────▼──────────┐               ┌─────────▼──────────────┐
│  Legislation     │               │   Pricing Specialist   │
│  Specialist      │               │   (HTAG AI, planned)   │
│                  │               │                        │
│  Tools:          │               │  Tools:                │
│  rag_retriever   │               │  get_suburb_price_     │
│  date_calculator │               │  stats                 │
└──────┬───────────┘               └────────────────────────┘
       │
┌──────▼───────────────────────────────────────────────────┐
│                    Qdrant Vector Store                    │
│         (dense + BM25 hybrid, metadata filtering)         │
└─────────────────────┬───────────────────────────────────┘
                      │
┌─────────────────────▼───────────────────────────────────┐
│            LLM Response Generation (Claude)              │
│       (grounded in cited chunks, with source refs)      │
└─────────────────────────────────────────────────────────┘
```

*Phase A (quality foundation) in progress. Phase B (multi-state ingestion) next. See full roadmap below.*

## Roadmap

### ✅ Phase A: Quality Foundation
| Step | Status | What |
|------|--------|------|
| 1 | ⬜ | RAGAS evaluation on VIC (20 golden QA pairs, faithfulness + context precision + answer relevance) |
| 2 | ⬜ | Embedding fine-tuning (BGE-small on legal contrastive pairs, Colab T4, Sentence Transformers) |
| 3 | ⬜ | Re-evaluate with fine-tuned embeddings (compare baseline vs fine-tuned scores) |

### Phase B: Scale to All 8 Jurisdictions
| Step | Status | What |
|------|--------|------|
| 4 | ⬜ | NSW legislation ingestion (parse NSW RTA 2010 PDF, validate parser reusability) |
| 5 | ⬜ | QLD, SA, WA, TAS, ACT, NT ingestion (bulk ingest remaining 6 states) |
| 6 | ⬜ | Multi-state RAGAS evaluation (40 golden QA pairs across all 8 jurisdictions + cross-state) |

### Phase C: Conversational Agent
| Step | Status | What |
|------|--------|------|
| 7 | ⬜ | LangGraph agent (7-node state machine: memory_recall → intent_classifier → slot_filler → rag_retriever → legal_reasoner → citation_verifier → fallback) |
| 8 | ⬜ | Agent RAGAS evaluation (faithfulness + context precision on multi-turn scenarios) |
| 9 | ⬜ | LangSmith tracing (per-node latency/cost, trace replay, bottleneck identification) |

### Phase D: Production Deployment
| Step | Status | What |
|------|--------|------|
| 10 | ⬜ | Migrate to AWS Bedrock (BedrockLLMProvider, Claude Sonnet, Converse API) |
| 11 | ⬜ | Containerize (Dockerfile, fastembed models baked in, push to ECR) |
| 12 | ⬜ | Deploy Lambda + API Gateway (FastAPI + Mangum, POST /api/tenancy/query) |
| 12a | ⬜ | File upload & document analysis (PDF/JPG parsing, contract clause extraction, dual-source citation [Contract, Clause X] + [VIC RTA 1997 Sec Y], cross-reference detection) |
| 13 | ⬜ | Safety guardrails (PII detection, off-topic filter, jailbreak defense, citation grounding alert) |

### Phase F: Frontend
| Step | Status | What |
|------|--------|------|
| 18 | ⬜ | Chat box frontend (React/Vue component in separate project, loading skeleton, async polling) |

### Phase E: Market Intelligence
| Step | Status | What |
|------|--------|------|
| 14 | ⬜ | HTAG AI client (suburb-level rent/sale price data, in-memory cache) |
| 15 | ⬜ | Pricing specialist agent (separate LangGraph sub-graph, market guide system prompt) |
| 16 | ⬜ | Multi-agent supervisor (create_supervisor, route legislation vs pricing, cross-agent delegation) |
| 17 | ⬜ | Final multi-agent evaluation + red-teaming report |

### ✅ Completed (feat/vic-legal-pdf-parsing + feat/vic-rerank-llm-generation)
| Step | What |
|------|------|
| 0 | VIC RTA PDF parser (PyMuPDF + regex, hierarchical chunking, TOKEN_THRESHOLD=2048) |
| 0 | Qdrant vector store (BGE-small + BM25, RRF fusion, local storage) |
| 0 | RAG pipeline (query rewrite → hybrid retrieve → LLM (DeepSeek) → citation verification) |
| 0 | IRAC-structured system prompt with practical next step guidance |
| 0 | Citation verification with subsection support `[VIC RTA 1997 Sec 91ZM(7)]` |

**Timeline:** ~17 steps, ~27-43 hours with AI assistance. Critical path: 1→7→12→18.

## Environment Variables

Copy `.env.example` to `.env` and populate all values:

```bash
cp .env.example .env
```

### DeepSeek (Current Dev)
| Variable            | Description           | Required |
| ------------------- | --------------------- | -------- |
| `DEEPSEEK_API_KEY`  | DeepSeek API key      | Yes      |
| `LLM_MODEL_ID`      | `deepseek-chat`        | Yes      |

### AWS Bedrock (Production)
| Variable                     | Description                             | Required |
| ---------------------------- | --------------------------------------- | -------- |
| `AWS_ACCESS_KEY_ID`          | AWS IAM access key                      | Yes      |
| `AWS_SECRET_ACCESS_KEY`      | AWS IAM secret key                      | Yes      |
| `AWS_REGION`                 | AWS region (e.g. `ap-southeast-2`)      | Yes      |
| `BEDROCK_MODEL_ID`           | Claude model ID in Bedrock              | Yes      |
| `BEDROCK_EMBEDDING_MODEL_ID` | Titan embedding model ID                | Yes      |

### Qdrant
| Variable              | Description                     | Required |
| --------------------- | ------------------------------- | -------- |
| `QDRANT_URL`          | Qdrant cluster URL              | Yes      |
| `QDRANT_API_KEY`      | Qdrant API key                  | Yes      |
| `QDRANT_COLLECTION`   | Collection name for tenancy docs | Yes      |

### Application
| Variable               | Description                       | Required |
| ---------------------- | --------------------------------- | -------- |
| `LANGCHAIN_TRACING_V2` | Enable LangSmith tracing          | No       |
| `LANGCHAIN_API_KEY`    | LangSmith API key                 | No       |
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
python src/data_processing/parser.py

# Step 2: Index chunks into Qdrant vector store
python src/retrieval/vector_store.py

# Step 3: Run RAG compliance pipeline
python src/generation/generator.py

# Step 4: Run tests
pytest tests/ -m "not slow"
```

See [Roadmap](#roadmap) above for complete development plan.

## Development

See [CONTRIBUTING.md](./CONTRIBUTING.md) for branch strategy, commit conventions, and linting setup.

## License

See [LICENSE](./LICENSE).
