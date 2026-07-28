# AusTenancy.ai

Australian Residential Tenancies Compliance Agent -- a stateful, graph-based RAG system for jurisdiction-aware tenancy law queries.

> The full-stack application lives at **[VicTenancy.app](https://github.com/Leon-wyl/VicTenancy.app)**.
> This repository is the **Agent Runtime** only.

## Governance

This agent must follow `CONTRIBUTING.md` for all branching, commit, linting, and PR conventions. When relevant to the task, consult documents in `docs/` (AGENT_WORKFLOW.md, ARCHITECTURE_DESIGN.md, PRD.md, EVALUATION_PLAN.md, LANGSMITH_SETUP.md) for design, architecture, evaluation, and workflow guidance.

## Tech Stack

| Layer | Technology |
|---|---|
| Orchestration | LangGraph (7-node state machine, 3 conditional routers) -- built; multi-agent supervisor planned (Phase F) |
| Retrieval | Qdrant (dense BGE-small + BM25 hybrid, RRF fusion) |
| Embeddings | BGE-small-en-v1.5 via fastembed (fine-tuning planned) |
| Embeddings (prod) | Amazon Titan Text Embeddings v2 (via Bedrock) |
| LLM (current dev) | DeepSeek via OpenAI-compatible SDK (default); AWS Bedrock Converse validated for staging (`LLM_PROVIDER=bedrock`, `BEDROCK_TEMPERATURE=0`) |
| LLM (prod target) | Amazon Nova Pro v1:0 (via AWS Bedrock, ap-southeast-2) |
| LLM (classifier) | Amazon Nova Lite -- planned |
| Evaluation | RAGAS (faithfulness, context precision, answer relevance) |
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

## Run

```bash
python src/rag/data_processing/vic_parser.py        # Parse VIC RTA PDF -> chunks
python src/rag/retrieval/vector_store.py           # Index chunks -> Qdrant
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

## Architecture

### Current (Phase C -- LangGraph Agent)

7-node LangGraph state machine with 3 conditional routers:

```
intake_analyzer -> request_clarification | query_rewriter -> rag_retriever -> legal_reasoner -> citation_verifier -> fallback_node
```

- **State:** `AgentState` TypedDict (14 fields, including `suspicious_input`) -- see `src/agent/state.py`
- **Safety:** Pre-graph injection detection, PII/secret redaction, mandatory disclaimer -- see `src/agent/safety.py` (43 tests in `tests/test_safety.py`)
- **Routers:** `route_after_intake` (fallback/clarification/rewriter), `route_after_retrieval` (fallback/reasoner), `route_after_verify` (fallback/__end__)
- **CLI:** Interactive multi-turn REPL with `MemorySaver` checkpointing + per-session `thread_id` (`python -m src.agent.cli`)
- **LLM prompt:** IRAC format (Issue -> Rule -> Application -> Conclusion)

### Planned (future phases)

- **Bedrock toolConfig tools** -- Converse API function calling for `rag_retriever`, `date_calculator`, `rent_increase_validator` (Phase D) -- `[STATUS: planned -- not implemented]`
- **Mem0 cross-session memory** -- jurisdiction/role persistence across sessions (Phase C Step 9, deferred) -- `[STATUS: planned -- not implemented]`
- **Multi-agent supervisor** -- route legislation vs pricing queries (Phase F) -- `[STATUS: planned -- not implemented]`

### Full-Stack Integration

The Agent Runtime is consumed by the **[VicTenancy.app](https://github.com/Leon-wyl/VicTenancy.app)** full-stack application. See [`docs/api/agent-runtime-api.md`](docs/api/agent-runtime-api.md) for the canonical API contract.

> Phase E may replace AWS_IAM with a Supabase JWT authorizer on the invoke route.

## Critical Rules

1. **Jurisdiction filter always** -- metadata pre-filter before any retrieval. Never return non-requested state results.
2. **Every claim needs a citation** -- citation verifier cross-checks LLM output against retrieved chunks. Strip or flag uncited claims.
3. **No citation hallucination** -- if a section isn't in the retrieved set, output uncertainty, not a fabricated citation.
4. **Pydantic at boundaries** -- validate LLM extraction, API input, and tool results with Pydantic. Internal state uses lightweight TypedDict.
5. **Dual-source citations** -- cross-reference uploaded documents against legislation. Flag contradictions. Format: [Contract, Clause X] + [VIC RTA 1997 Sec Y].
6. **Input safety before graph** -- injection detection and PII/secret redaction run in the API handler (see `src/agent/safety.py`), never inside graph nodes. Guard instruction is prepended only when flagged. Mandatory legal disclaimer is appended to all answers.
7. **No raw legal logs** -- Lambda logs are redacted before output. Never expose legal-answer content or credentials in logs or error details.

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
| 4 | Done | NSW legislation ingestion (NSWParser, per-state architecture) |
| 5 | Deferred | QLD, SA, WA, TAS, ACT, NT |
| 6 | Done | Multi-state RAGAS evaluation (VIC 20Q + NSW 20Q) |

### Phase C: Conversational Agent
| Step | Status | What |
|------|--------|------|
| 7 | Done | LangGraph agent (7-node state machine) |
| 8 | []() | Agent RAGAS evaluation |
| 9 | []() | LangSmith tracing |

### Phase D: AWS Staging Deployment
| Step | Status | What |
| ---- | ------ | ------------------------------------------------------------ |
| 11   | Done | Containerize Full Agent Runtime |
| 12   | Done | Deploy Agent Runtime Lambda + API Gateway |
| 13   | Done | Staging Operational Readiness & Release Automation |
| 13a  | Done | Agent-Level Safety Guardrails |

### Phase F: Market Intelligence
| Step | Status | What |
|------|--------|------|
| 22 | []() | HTAG AI client (suburb-level rent/sale price data, in-memory cache) |
| 23 | []() | Pricing specialist agent (separate LangGraph sub-graph) |
| 24 | []() | Multi-agent supervisor |
| 25 | []() | Final multi-agent evaluation + red-teaming report |

### Completed
| Phase | What |
|-------|------|
| 0 | VIC RTA PDF parser, Qdrant vector store, RAG pipeline, citation verification |
| Phase A | RAGAS evaluation suite, pipeline improvements, IRAC format |
| Phase B | NSW legislation + multi-state RAGAS, VIC+NSW Regulation parsers, citation metrics |
| Phase C | LangGraph 7-node agent + interactive streaming CLI |
| Phase D | Bedrock provider migration + Deployment Architecture Gate |

**Timeline:** ~25 steps. Critical path: 11->12->13->22->25.

Full-stack application roadmap is at [VicTenancy.app](https://github.com/Leon-wyl/VicTenancy.app).

## Project Structure

```
src/                          # RAG pipeline + agent (Python)
  rag/data_processing/        # PDF parsing -> hierarchical chunks
    vic_parser.py             #   VIC RTA PDF parser (PyMuPDF + regex)
    nsw_parser.py             #   NSW RTA PDF parser
    base_parser.py            #   Abstract BaseParser (shared pipeline)
    vic_regulation_parser.py  #   VIC Regulations 2021 parser
    nsw_regulation_parser.py  #   NSW Regulations 2019 parser
  rag/retrieval/              # Vector store indexing + hybrid search
    vector_store.py           #   Qdrant ingestion with dense + BM25
  rag/generation/             # RAG compliance pipeline
    generator.py              #   Query rewrite -> retrieve -> LLM -> citation verify
  rag/evaluation/             # RAGAS evaluation scripts + golden dataset
    run_ragas_eval.py         #   Evaluation runner with CLI flags
    citation_metrics.py       #   Deterministic citation metrics
    gen_golden_contexts.py    #   Golden context pre-computation
    audit_sections.py         #   Section audit utility
  agent/                      # LangGraph agent
    state.py                  #   AgentState TypedDict (14 fields)
    graph_skeleton.py         #   7-node state machine, 3 routers
    cli.py                    #   Interactive multi-turn REPL
    observability.py          #   Run summaries, LangSmith config
    safety.py                 #   Injection detection, PII redaction, disclaimer
  api/                        # FastAPI + Mangum adapter (Lambda)
    handler.py                #   /health + /api/agent/invoke handlers
    runtime.py                #   Lambda container bootstrap
    models.py                 #   AgentRequest/AgentResponse Pydantic models
tests/                        # Pytest suite
  evaluation/                 # Golden datasets + contexts
scripts/                      # Utility and evaluation scripts
  ops/                        # Release automation
    release.sh                #   Gated release pipeline
    preflight.sh              #   10 pre-release checks
    smoke_test.py             #   SigV4-signed smoke tests
    verify_deployment.sh     #   16 post-deploy infra checks
    rollback.sh               #   Digest-based rollback plan
    inspect_logs.sh          #   CloudWatch log inspector
  run_agent_observed.py       #   One-shot agent run with JSON summary
  compare_providers.py        #   DeepSeek vs Bedrock comparison harness
  capture_baseline.py         #   Multi-stage pipeline tracing
  eval_arrears_thresholds.py  #   Threshold evaluation (VIC/NSW arrears cases)
docs/                         # Design docs, PRD, workflows, architecture gate
  api/                        # Canonical Agent Runtime API contract
  ops/                        # Release runbook, checklist
terraform/                    # AWS infrastructure as code
  runtime/staging/            #   Lambda + API Gateway + IAM + alarms
Dockerfile                    # Lambda-compatible container image
data/raw/                     # PDF legislation files (gitignored)
data/processed/               # Generated hierarchical chunks (gitignored)
qdrant_storage/               # Local Qdrant database (gitignored)
```
