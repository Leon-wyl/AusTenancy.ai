# AusTenancy.ai — Australian Residential Tenancies Compliance Agent

A stateful, graph-based RAG Agent delivering high-precision compliance queries for Australian state Residential Tenancies Acts (VIC, NSW, QLD, and others). Built with LangGraph and AWS Bedrock, the system implements layout-aware hierarchical chunking (Act→Part→Section) to preserve legal context and enforce strict citation grounding.

## Value Proposition

- **Zero cross-state hallucination** — metadata-filtered retrieval ensures answers are grounded in the correct jurisdiction's legislation.
- **Stateful multi-turn reasoning** — LangGraph manages conversation state, enabling follow-up questions that respect prior context.
- **Production-grade retrieval** — hybrid search (dense vector + BM25) with BGE re-ranking via Qdrant for maximum precision.
- **Enterprise compliance ready** — designed for integration into tenancy dispute platforms, property management systems, and legal research tools.

## Technical Stack

| Layer               | Technology                                             |
| ------------------- | ------------------------------------------------------ |
| Orchestration       | LangGraph (state graphs, multi-agent supervisor)       |
| Retrieval           | LangChain, Qdrant (vector + BM25 hybrid)               |
| Re-ranking          | BGE-Reranker                                            |
| LLM (primary)       | Anthropic Claude 3.5 Sonnet (via AWS Bedrock)           |
| LLM (fallback)      | OpenAI GPT-4o / Google Gemini                           |
| Embeddings          | Amazon Titan Text Embeddings v2 (via Bedrock)           |
| Document Chunking   | Layout-aware hierarchical (Act→Part→Section)            |
| Infrastructure      | AWS (Bedrock, Lambda, ECS / Fargate)                    |
| Language            | Python 3.12+                                            |
| Linting & Formatting| Ruff (replacing Black / Flake8)                         |
| CI/CD               | GitHub Actions                                          |

## Modular System Architecture

```
┌─────────────────────────────────────────────────────────┐
│                    User Interface                        │
│            (REST API / WebSocket / Chat UI)              │
└─────────────────────┬───────────────────────────────────┘
                      │
┌─────────────────────▼───────────────────────────────────┐
│              LangGraph Supervisor Agent                  │
│    (state routing, multi-turn context, fallback logic)   │
└──────┬─────────────────────────────────────┬────────────┘
       │                                     │
┌──────▼──────────┐               ┌─────────▼──────────────┐
│   Query Router   │               │   Document Ingestion   │
│ (jurisdiction +  │               │   Pipeline (Acts →     │
│  intent parsing) │               │   hierarchical chunks) │
└──────┬───────────┘               └─────────┬──────────────┘
       │                                     │
┌──────▼─────────────────────────────────────▼──────────────┐
│                    Qdrant Vector Store                    │
│         (dense + BM25 hybrid, metadata filtering)         │
└─────────────────────┬───────────────────────────────────┘
                      │
┌─────────────────────▼───────────────────────────────────┐
│                 BGE Re-ranking Layer                     │
│        (top-k candidates → re-ranked citations)         │
└─────────────────────┬───────────────────────────────────┘
                      │
┌─────────────────────▼───────────────────────────────────┐
│            LLM Response Generation (Claude)              │
│       (grounded in cited chunks, with source refs)      │
└─────────────────────────────────────────────────────────┘
```

## Environment Variables

Copy `.env.example` to `.env` and populate all values:

```bash
cp .env.example .env
```

### AWS Bedrock

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

### LLM Fallback

| Variable            | Description       | Required |
| ------------------- | ----------------- | -------- |
| `OPENAI_API_KEY`    | OpenAI API key    | No       |
| `GOOGLE_API_KEY`    | Google AI API key | No       |

### Application

| Variable               | Description                       | Required |
| ---------------------- | --------------------------------- | -------- |
| `LANGCHAIN_TRACING_V2` | Enable LangSmith tracing          | No       |
| `LANGCHAIN_API_KEY`    | LangSmith API key                 | No       |
| `LOG_LEVEL`            | Logging level (`INFO`/`DEBUG`)    | No       |
| `CHUNK_SIZE`           | Document chunk size (tokens)      | No       |
| `CHUNK_OVERLAP`        | Chunk overlap (tokens)            | No       |
| `TOP_K_RETRIEVAL`      | Number of chunks to retrieve      | No       |
| `TOP_K_RERANK`         | Number of chunks after re-ranking | No       |

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
# Edit .env with your credentials

# Run ingestion pipeline
python src/ingest.py

# Start the API server
python src/main.py
```

## Development

See [CONTRIBUTING.md](./CONTRIBUTING.md) for branch strategy, commit conventions, and linting setup.

## License

See [LICENSE](./LICENSE).
