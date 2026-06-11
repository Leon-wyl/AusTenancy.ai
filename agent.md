# AusTenancy.ai

Australian Residential Tenancies Compliance Agent — a stateful, graph-based RAG system for jurisdiction-aware tenancy law queries.

## Governance

This agent must follow `CONTRIBUTING.md` for all branching, commit, linting, and PR conventions. When relevant to the task, consult documents in `docs/` (AGENT_WORKFLOW.md, ARCHITECTURE_DESIGN.md, PRD.md, etc.) for design, architecture, and workflow guidance.

## Tech Stack

| Layer | Technology |
|---|---|
| Orchestration | LangGraph (state graphs, multi-agent) |
| Retrieval | LangChain, Qdrant (dense + BM25 hybrid) |
| Re-ranking | BGE-Reranker-v2-m3 |
| LLM (primary) | Claude 3.5 Sonnet (via AWS Bedrock) |
| LLM (classifier) | Amazon Nova Lite |
| Embeddings | Amazon Titan Text Embeddings v2 |
| Language | Python 3.12+ |
| Lint/Format | Ruff |

## Setup

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# Fill in credentials in .env
```

## Architecture

LangGraph state machine with these nodes:

```
memory_recall → intent_classifier → slot_filler → rag_retriever → legal_reasoner → citation_verifier → fallback | final
```

- **State:** `TypedDict` for internal (node→node), Pydantic for boundaries (API, LLM output, tool I/O)
- **LLM prompt:** IRAC format (Issue → Rule → Application → Conclusion)
- **Tools (Converse API toolConfig):** `rag_retriever`, `date_calculator`, `rent_increase_validator`
- **Cross-session memory:** Mem0 for jurisdiction/role persistence

## Critical Rules

1. **Jurisdiction filter always** — metadata pre-filter before any retrieval. Never return non-requested state results.
2. **Every claim needs a citation** — citation verifier cross-checks LLM output against retrieved chunks. Strip or flag uncited claims.
3. **No citation hallucination** — if a section isn't in the retrieved set, output uncertainty, not a fabricated citation.
4. **Pydantic at boundaries** — validate LLM extraction, API input, and tool results with Pydantic. Internal state uses lightweight TypedDict.

## Project Structure

```
src/               # Source code
docs/              # Design docs, PRD, workflows, agent workflow
data/raw/          # PDF legislation files (gitignored)
data/processed/    # Generated hierarchical chunks (gitignored)
agent.md           # This file
```
