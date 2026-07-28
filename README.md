# AusTenancy.ai — Australian Residential Tenancies Compliance Agent

A stateful, graph-based RAG Agent delivering high-precision compliance queries for
Australian state Residential Tenancies Acts (VIC, NSW, QLD, and others). Built with
LangGraph and AWS Bedrock, the system implements layout-aware hierarchical chunking
(Act->Part->Section) to preserve legal context and enforce strict citation grounding.

> The full-stack application lives at **[VicTenancy.app](https://github.com/Leon-wyl/VicTenancy.app)**.
> This repository is the **Agent Runtime** only.

## Value Proposition

- **Zero cross-state hallucination** -- metadata-filtered retrieval ensures answers are
  grounded in the correct jurisdiction's legislation.
- **Stateful multi-turn reasoning** -- LangGraph manages conversation state, enabling
  follow-up questions that respect prior context.
- **Production-grade retrieval** -- hybrid search (dense vector + BM25) via Qdrant.
  Embeddings fine-tuned on legal text for maximum retrieval precision.
- **Strict citation grounding** -- every claim verified against retrieved chunks with
  `[State RTA Year Sec X]` format enforcement.
- **Input-output safety guardrails** -- prompt injection detection, PII/secret log
  redaction, mandatory legal disclaimer, safe error responses -- filtered pre-graph,
  never baked into node logic.
- **Enterprise compliance ready** -- designed for Lambda + API Gateway deployment,
  swappable LLM backend (DeepSeek dev -> Bedrock prod).

## Technical Stack

| Layer                 | Technology                                                        |
| --------------------- | ----------------------------------------------------------------- |
| Orchestration         | LangGraph (7-node state machine, 3 conditional routers) -- built; multi-agent supervisor planned (Phase F) |
| Retrieval             | Qdrant (dense + BM25 hybrid with RRF fusion)                      |
| Embeddings            | BGE-small-en-v1.5 via fastembed (fine-tuned on legal text planned)|
| Embeddings (prod)     | Amazon Titan Text Embeddings v2 (via Bedrock)                     |
| LLM (current dev)     | DeepSeek (default) or AWS Bedrock Converse -- selected via `LLM_PROVIDER` |
| LLM (prod target)     | Amazon Nova Pro v1:0 (via AWS Bedrock)                            |
| Evaluation            | RAGAS (faithfulness, context precision, answer relevance)         |
| Document Chunking     | Layout-aware hierarchical (Act->Part->Division->Section)          |
| Infrastructure        | Terraform + AWS Lambda + API Gateway (Docker container)           |
| Monitoring            | LangSmith tracing + CloudWatch + CloudWatch alarms                |
| Language              | Python 3.12+                                                      |
| Linting & Formatting  | Ruff                                                              |
| CI/CD                 | GitHub Actions                                                    |

## System Architecture

### Agent Graph (Current)

```
                         +----------------------+
                         |    intake_analyzer   |
                         |    (rule-based)      |
                         +----------+-----------+
                                    |
                    +---------------+------------------+
                    |               |                  |
                    v               v                  v
           +--------------+  +--------------+  +--------------+
           |  fallback    |  | request_     |  | query_       |
           |  _node       |  | clarification|  | rewriter     |
           |  -> END       |  | -> END        |  | (LLM)        |
           +--------------+  +--------------+  +------+-------+
                                                      |
                                                      v
                                              +--------------+
                                              | rag_retriever|
                                              | (hybrid RRF) |
                                              +------+-------+
                                                     |
                                          +----------+----------+
                                          |                     |
                                          v                     v
                                  +--------------+     +--------------+
                                  |  fallback    |     | legal_       |
                                  |  _node -> END |     | reasoner     |
                                  +--------------+     | (LLM IRAC)   |
                                                       +------+-------+
                                                              |
                                                              v
                                                       +--------------+
                                                       | citation_    |
                                                       | verifier     |
                                                       | (rule-based) |
                                                       +------+-------+
                                                              |
                                                   +----------+----------+
                                                   |                     |
                                                   v                     v
                                           +--------------+     +--------------+
                                           |  fallback    |     |   __end__    |
                                           |  _node -> END |     | (response)   |
                                           +--------------+     +--------------+
```

### RAG Pipeline (internal)

```
rewrite_query -> hybrid_retrieve -> build_legal_prompt -> LLM -> verify_citations
```

*Phase A complete (RAGAS evaluation). Phase B (NSW ingestion + multi-state RAGAS) complete.
Phase C Step 7 complete (LangGraph agent + interactive CLI). See full roadmap below.*

## Roadmap

### Phase A: Quality Foundation
| Step | Status | What |
|------|--------|------|
| 1 | Done | RAGAS evaluation on VIC (20 golden QA pairs) |
| 2 | Deferred | Embedding fine-tuning on legal contrastive pairs |
| 3 | Deferred | Re-evaluate with fine-tuned embeddings |

### Phase B: Scale to Multi-Jurisdiction
| Step | Status | What |
|------|--------|------|
| 4 | Done | NSW legislation ingestion |
| 5 | Deferred | QLD, SA, WA, TAS, ACT, NT |
| 6 | Done | Multi-state RAGAS evaluation |

### Phase C: Conversational Agent
| Step | Status | What |
|------|--------|------|
| 7 | Done | LangGraph 7-node agent |
| 8 | []() | Agent RAGAS evaluation |
| 9 | []() | LangSmith tracing |

### Phase D: AWS Staging Deployment
| Step | Status | What |
| ---- | ------ | ------------------------------------------------------------------ |
| 11   | Done | Containerize Full Agent Runtime |
| 12   | Done | Deploy Agent Runtime Lambda + API Gateway |
| 13   | Done | Staging Operational Readiness & Release Automation |
| 13a  | Done | Agent-Level Safety Guardrails |

### Phase F: Market Intelligence
| Step | Status | What |
|------|--------|------|
| 22 | []() | HTAG AI client |
| 23 | []() | Pricing specialist agent |
| 24 | []() | Multi-agent supervisor |
| 25 | []() | Final multi-agent evaluation |

### Completed
| Phase | What |
|-------|------|
| 0 | VIC RTA PDF parser (PyMuPDF + regex, hierarchical chunking) |
| 0 | Qdrant vector store (BGE-small + BM25, RRF fusion) |
| 0 | RAG pipeline |
| Phase A | RAGAS evaluation suite |
| Phase B | NSW + multi-state RAGAS |
| Phase C | LangGraph 7-node agent + interactive CLI |
| Phase D | Bedrock migration + Deployment Architecture Gate |

**Timeline:** ~25 steps. Critical path: 11->12->13->22->25.

Full-stack application roadmap is at [VicTenancy.app](https://github.com/Leon-wyl/VicTenancy.app).

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
| `BEDROCK_MODEL_ID`           | Bedrock model or inference profile ID -- no default; verify model access first | Yes |
| `BEDROCK_TEMPERATURE`        | Optional default temperature (omitted from requests when blank)        | No       |
| `BEDROCK_MAX_TOKENS`         | Optional default max tokens (omitted from requests when blank)         | No       |
| `BEDROCK_EMBEDDING_MODEL_ID` | Titan embedding model ID                                               | Yes      |

\* Required only if not using another boto3 credential mechanism.

### Qdrant
| Variable              | Description                     | Required |
| --------------------- | ------------------------------- | -------- |
| `QDRANT_URL`          | Qdrant cluster URL              | Yes      |
| `QDRANT_API_KEY`      | Qdrant API key                  | Yes      |
| `QDRANT_COLLECTION`   | Collection name for tenancy docs | Yes      |

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
git clone https://github.com/Leon-wyl/AusTenancy.ai.git
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
RUN_BEDROCK_INTEGRATION=1 pytest tests/test_bedrock_integration.py -m integration -v

# Step 5: Provider hardening and evaluation (optional)
python scripts/compare_providers.py --providers deepseek bedrock
python scripts/capture_baseline.py
python scripts/eval_arrears_thresholds.py
```

## API Contract

The Agent Runtime exposes two endpoints. See the canonical API documentation at
[`docs/api/agent-runtime-api.md`](docs/api/agent-runtime-api.md).

| Method | Path                  | Auth (local) | Auth (deployed) | Description |
|--------|----------------------|-------------|-----------------|-------------|
| GET    | `/health`             | NONE        | NONE            | Health check |
| POST   | `/api/agent/invoke`   | NONE        | AWS_IAM (SigV4) | Agent invocation |

## Release Workflow

Every production code change follows a gated pipeline through
`scripts/ops/release.sh`:

```bash
bash scripts/ops/release.sh --allowed-account-id $ALLOWED_ACCOUNT_ID
```

### Pipeline

| Phase | Script | Gate |
|-------|--------|------|
| **Build** | `docker build` -> container smoke -> ECR push -> capture digest | Fail-fast |
| **Preflight** | `scripts/ops/preflight.sh` -- 10 checks (Git, AWS, Terraform, ECR, drift, secrets) | Fail-fast |
| **Plan** | `terraform plan -out=runtime-staging.tfplan` | Review required |
| **Apply** | `terraform apply` | **Manual Y/N prompt** |
| **Smoke** | `scripts/ops/smoke_test.py` -- health 200, anonymous 403, SigV4 Agent invoke | Warning |
| **Verify** | `scripts/ops/verify_deployment.sh` -- 16 infra checks | Warning |
| **Inspect** | `scripts/ops/inspect_logs.sh` -- CloudWatch logs + alarms | Warning |

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
- Apply is **always a manual Y/N prompt** -- never auto-applies
- Rollback **exits 1** on destroys or unexpected resource changes -- plan-only
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
# Default -- DeepSeek (LLM_PROVIDER may be unset or blank)
LLM_PROVIDER=deepseek

# AWS Bedrock (requires region, model access, and boto3-resolvable credentials)
LLM_PROVIDER=bedrock
AWS_REGION=ap-southeast-2
BEDROCK_MODEL_ID=amazon.nova-pro-v1:0
```

**Validated model:** `amazon.nova-pro-v1:0` in `ap-southeast-2` (2026-07-22).

## Observability

### Local Run Summaries

```bash
python scripts/run_agent_observed.py \
  --question "Can my landlord increase rent by text message in VIC?"
```

This prints the final answer and a JSON run summary containing retrieval stats,
citation stats, performance metrics, and status.

### LangSmith Tracing (Optional)

```bash
export LANGCHAIN_TRACING_V2=true
export LANGCHAIN_API_KEY=ls__your_key
export LANGCHAIN_PROJECT=aus-tenancy-agent

python scripts/run_agent_observed.py --question "..." --langsmith
```

If LangSmith env vars are not set, the system runs normally -- tracing is
purely opt-in.

## Development

See [CONTRIBUTING.md](./CONTRIBUTING.md) for branch strategy, commit conventions,
and linting setup.

## License

See [LICENSE](./LICENSE).
