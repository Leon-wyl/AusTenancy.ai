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
| Monitoring | LangSmith tracing — planned (post-LangGraph) |
| Deployment | AWS Lambda + API Gateway (Docker container) — planned |
| Language | Python 3.12+ |
| Lint/Format | Ruff |

## Setup

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# Set DEEPSEEK_API_KEY in .env
```

## Run

```bash
python src/data_processing/parser.py          # Parse VIC RTA PDF → chunks
python src/retrieval/vector_store.py           # Index chunks → Qdrant
python src/generation/generator.py            # Run RAG compliance pipeline
pytest tests/ -m "not slow"                   # Run tests
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
- **Multi-agent supervisor:** Route legislation vs pricing queries (Phase E, planned)

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
| 2 | ⬜ | Embedding fine-tuning on legal contrastive pairs |
| 3 | ⬜ | Re-evaluate with fine-tuned embeddings |

### Phase B: Scale to All 8 Jurisdictions
| Step | Status | What |
|------|--------|------|
| 4 | ⬜ | NSW legislation ingestion |
| 5 | ⬜ | QLD, SA, WA, TAS, ACT, NT ingestion |
| 6 | ⬜ | Multi-state RAGAS evaluation (40 QA pairs) |

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
| 12 | ⬜ | Deploy Lambda + API Gateway |
| 12a | ⬜ | File upload & contract analysis (PDF/JPG parsing, clause extraction, dual-source citations) |
| 13 | ⬜ | Safety guardrails |

### Phase F: Frontend
| Step | Status | What |
|------|--------|------|
| 18 | ⬜ | Chat box frontend |

### Phase E: Market Intelligence
| Step | Status | What |
|------|--------|------|
| 14 | ⬜ | HTAG AI client |
| 15 | ⬜ | Pricing specialist agent |
| 16 | ⬜ | Multi-agent supervisor |
| 17 | ⬜ | Final evaluation + red-teaming |

### ✅ Completed
| Phase | What |
|-------|------|
| 0 | VIC RTA PDF parser (PyMuPDF + regex, hierarchical chunking) |
| 0 | Qdrant vector store (BGE-small + BM25, RRF fusion) |
| 0 | RAG pipeline (query rewrite → hybrid retrieve → LLM → citation verify) |
| 0 | Citation verification with subsection support |
| Phase A | RAGAS evaluation suite (20 golden QA pairs, faithfulness/context_precision/answer_relevancy, ablation study, golden-context diagnostic) |
| Phase A | Pipeline improvements (Part metadata filter, reranker removal, AR disclaimer fix, citation trust signal) |

**Timeline:** ~17 steps, ~27-43 hours with AI assistance. Critical path: 1→7→12→18.

## Project Structure

```
src/                          # Source code
  data_processing/            # PDF parsing → hierarchical chunks
    parser.py                 #   VIC RTA PDF parser (PyMuPDF + regex)
  retrieval/                  # Vector store indexing + hybrid search
    vector_store.py           #   Qdrant ingestion with dense + BM25
  generation/                 # RAG compliance pipeline
    generator.py              #   Query rewrite → retrieve → LLM → citation verify
  pricing/                    # Market intelligence (planned)
    htag_client.py            #   HTAG AI API client
  processing/                 # Document parsing (planned)
    document_parser.py        #   PDF/JPG extraction, clause metadata
api/                          # FastAPI + Lambda handler (planned)
src/evaluation/               # RAGAS evaluation scripts + golden dataset
tests/                        # Pytest suite
docs/                         # Design docs, PRD, workflows
data/raw/                     # PDF legislation files (gitignored)
data/processed/               # Generated hierarchical chunks (gitignored)
qdrant_storage/               # Local Qdrant database (gitignored)
agent.md                      # This file
```
