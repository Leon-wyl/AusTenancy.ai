# Architecture Design: Australian Residential Tenancies Compliance Agent

## 1. Cloud Infrastructure & Security

### Why AWS Bedrock

AWS Bedrock is chosen over direct Anthropic API or self-hosted models for three enterprise constraints that apply even at demo scale:

| Concern | Bedrock | Direct API | Self-Hosted |
|---|---|---|---|
| Data privacy | Inference within VPC. No data leaves AWS. | Data processed on Anthropic servers | Full control, but high ops cost |
| IAM integration | Native IAM roles and policies | API key-based (less granular) | Custom auth needed |
| No training on input | Contractually guaranteed | Same guarantee | N/A |

### Deployment Architecture (Cheapest Path)

```
┌──────────────┐     ┌──────────────────┐     ┌──────────────────────────┐
│  User / Demo │────▶│  API Gateway     │────▶│  Lambda (Python 3.12)    │
│  (Browser)   │     │  (REST, HTTP)    │     │  - LangGraph supervisor  │
└──────────────┘     └──────────────────┘     │  - Intent classification │
                                              │  - Retrieval + rerank    │
                                              │  - Response generation   │
                                              │  - Citation verification │
                                              └───────┬──────────────────┘
                                                      │
                        ┌─────────────────────────────┼──────────────────────────────┐
                        │                             │                              │
               ┌────────▼────────┐          ┌─────────▼───────────┐     ┌─────────────▼──────────┐
               │ Bedrock (VPC    │          │ Qdrant Cloud Free   │     │ AWS Lambda (Ingestion) │
               │ Endpoint)       │          │ (remote, via HTTPS) │     │ Triggered by S3 upload │
               │ - Nova Lite     │          │ - Dense + BM25      │     │ - Parses Act PDF       │
               │   (classify)    │          │ - Metadata filter   │     │ - Hierarchical chunk   │
               │ - Claude Sonnet │          │ - BGE Reranker      │     │ - Embed + upsert       │
               │   (legal IRAC)  │          │   (Lambda-side)     │     │                        │
               │ - Titan Embed   │          └─────────────────────┘     └────────────────────────┘
               │   (ingestion)   │
               └─────────────────┘
```

### Key Design Decisions for Cheapest Path

| Decision | Rationale | Cost Impact |
|---|---|---|
| **Lambda over Fargate** | Lambda free tier covers demo traffic. No idle compute cost. | ~$0 vs ~$40/mo |
| **API Gateway (HTTP)** | Cheaper than REST API. No ALB needed ($22/mo saving). | ~$3/mo vs ~$25/mo |
| **Qdrant Cloud Free Tier** | 1GB RAM, 4GB disk — enough for ~125K vectors (1536-dim). | $0 vs $114/mo |
| **No NAT Gateway** | Lambda in public subnet (or via VPC endpoint). NAT Gateway adds $60/mo. | $0 vs $60/mo |
| **Bedrock on-demand** | No provisioned throughput needed at demo scale. | Pay-per-token |
| **No multi-region** | Single region (ap-southeast-2). | $0 redundancy cost |

### Security

- IAM roles with least-privilege policies for Lambda → Bedrock invocation.
- Qdrant Cloud API key stored in Lambda environment variables (AWS Secrets Manager if budget allows).
- No public S3 buckets. Ingestion documents stored in private S3 with bucket policies restricting to Lambda role.
- CloudWatch Logs encrypted at rest.

### Rate Limiting & Backoff

Bedrock Converse API enforces per-model rate limits. At demo scale these are rarely hit, but the system handles them gracefully:

| Model | RPM Limit | TPM Limit | Fallback |
|---|---|---|---|
| Nova Lite | 2,000 | 200,000 | Retry after backoff |
| Claude Sonnet | 1,000 | 100,000 | Degrade to Nova Lite with warning |
| Titan Embeddings | 5,000 | 500,000 | Retry after backoff |

**Implementation (`src/utils/rate_limiter.py`):**

```python
from tenacity import retry, wait_exponential, stop_after_attempt, retry_if_exception_type
from botocore.exceptions import ClientError

@retry(
    wait=wait_exponential(multiplier=1, min=1, max=30),
    stop=stop_after_attempt(5),
    retry=retry_if_exception_type(ClientError),
)
def bedrock_converse_with_backoff(client, model_id, **kwargs):
    try:
        return client.converse(modelId=model_id, **kwargs)
    except ClientError as e:
        if e.response["Error"]["Code"] == "ThrottlingException":
            raise  # triggers tenacity retry
        raise

# Fallback chain: if Sonnet throttles, use Nova Lite
FALLBACK_CHAIN = [
    "anthropic.claude-sonnet-4-20250528",
    "amazon.nova-lite-v1:0",
]
```

**Token budget per invocation:**
- Track cumulative input+output tokens across all nodes in the graph
- If a single query exceeds $0.05 in generation cost, log and alert
- Hard cap: refuse queries that would exceed 8K total input tokens after retrieval

---

## 2. Data Ingestion Pipeline

### Challenge

Australian tenancy Acts are deeply nested documents structured as: **Act → Part → Division → Section → Subsection**. Naive fixed-size chunking destroys this hierarchy, making it impossible to answer questions like "Under Part 3 of the VIC Act, what are the landlord's duties?" without retrieving a context-free fragment.

### Hierarchical Section-Level Chunking

```
┌──────────────────────────────────────────────────────────────┐
│  Residential Tenancies Act 1997 (VIC)                        │
│                                                              │
│  ├── Part 2: Rights and Duties                               │
│  │   ├── Division 1: Landlord Duties                         │
│  │   │   ├── Section 44: Rent increases (60 days notice)     │
│  │   │   │   └── [CHUNK] metadata: {act, part, sect, vic}   │
│  │   │   ├── Section 45: Bond lodgement (10 business days)   │
│  │   │       └── [CHUNK] metadata: {act, part, sect, vic}   │
│  │   ├── Division 2: Tenant Duties                       │
│  │   │       ├── Section 60: Cleanliness                     │
│  │   │       │   └── [CHUNK] metadata: {act, part, sect, vic}│
│  │   │       └── Section 61: Notification of repairs         │
│  │   │           └── [CHUNK] metadata: {act, part, sect, vic}│
│  │   └── Division 3: Rent Increases                          │
│  │       └── ...                                             │
│  └── Part 3: Termination of Tenancies                        │
│      └── ...                                                 │
└──────────────────────────────────────────────────────────────┘
```

### Chunking Rules

| Rule | Description | Edge Case Handling |
|---|---|---|
| **Hard split at section boundaries** | Each section is an atomic chunk. No mid-section splits. | Long sections (>512 tokens) are split at subsection markers, with overlap of 2 sentences. Each sub-chunk inherits the parent section metadata. |
| **Metadata enrichment** | Every chunk carries: `{jurisdiction, act_name, year, part, division, section_no, section_title, subsection_range}` | If a chunk spans multiple subsections, `subsection_range` records `"44(1)-44(3)"`. |
| **Parent context prefix** | If a question references "Part 3", the chunk's preamble includes its parent lineage. | Chunk text is prefixed with: `[Part 2 - Division 1 - Section 44]` — this adds ~40 tokens but ensures systemic context survives retrieval. |
| **Cross-reference tagging** | Internal references like "see Section 12" are preserved as chunk-level cross-reference metadata. | Prevents retrieving a chunk about Section 12 without knowing it was cross-referenced from Section 44. |

### Ingestion Flow

1. **PDF parsing:** Act PDFs are parsed via Amazon Textract or `pdfplumber` into structured JSON (section boundaries detected by heading font/size patterns).
2. **Hierarchical tree construction:** Each Act is transformed into a tree with Part → Division → Section nodes.
3. **Chunk generation:** Each leaf Section node produces one or more chunks (hard split at section boundary, soft split within long sections).
4. **Embedding:** Each chunk is embedded via Amazon Titan Text Embeddings v2 (Bedrock). Embedding dimension: 1536.
5. **Upsert:** Chunks + embeddings + metadata are upserted to Qdrant. Payload includes full text and metadata for reranker access.

---

## 3. Hybrid Retrieval & Reranking Mechanism

### Strategy Overview

Single retrieval method is insufficient for legal text. Dense vectors capture semantic similarity ("What notice is needed for a rent increase?") but miss exact term matches ("Section 44"). BM25 captures term precision but misses paraphrased queries.

### Hybrid Search Pipeline

```
User Query: "How much notice for a rent increase in VIC?"
         │
         ▼
┌─────────────────────────────────────────────────────┐
│ 1. Metadata Pre-Filter                              │
│    - Extract jurisdiction from query (VIC)          │
│    - Apply filter: metadata.jurisdiction == "VIC"   │
│    - Purpose: eliminate cross-state leakage         │
└─────────────────────────────────────────────────────┘
         │
         ▼
┌─────────────────────────────────┐   ┌──────────────────────────┐
│ 2a. Dense Vector Search         │   │ 2b. Sparse BM25 Search   │
│     - Titan embedding of query  │   │     - Qdrant built-in    │
│     - Cosine similarity         │   │     - Token overlap      │
│     - Top-20 results            │   │     - Top-20 results     │
└─────────────────────────────────┘   └──────────────────────────┘
         │                                      │
         └──────────────────┬───────────────────┘
                            ▼
┌─────────────────────────────────────────────────────┐
│ 3. Reciprocal Rank Fusion (RRF)                     │
│    score(d) = Σ 1 / (k + rank_i(d))                 │
│    where k = 60 (standard constant)                 │
│    - Merges dense + sparse rankings                 │
│    - Top-15 candidates after fusion                 │
└─────────────────────────────────────────────────────┘
                            │
                            ▼
┌─────────────────────────────────────────────────────┐
│ 4. BGE-Reranker                                     │
│    - Cross-encoder scores (query, chunk) pairs      │
│    - More accurate than cosine similarity           │
│    - Top-5 candidates after reranking               │
│    - Runs as Lambda layer (BGE-Reranker-v2-m3)      │
└─────────────────────────────────────────────────────┘
                            │
                            ▼
                   Pass to LLM generation
```

### RRF Formula Detail

```python
def reciprocal_rank_fusion(dense_ranks, sparse_ranks, k=60):
    scores = {}
    for rank, doc_id in enumerate(dense_ranks, start=1):
        scores[doc_id] = scores.get(doc_id, 0) + 1 / (k + rank)
    for rank, doc_id in enumerate(sparse_ranks, start=1):
        scores[doc_id] = scores.get(doc_id, 0) + 1 / (k + rank)
    return sorted(scores.items(), key=lambda x: -x[1])
```

### Anti-Hallucination Guard: Metadata Pre-Filtering

The most critical guardrail against cross-jurisdictional hallucination:

```python
def retrieve(query, jurisdiction):
    # Step 1: Extract jurisdiction from query (Nova Lite classifier)
    jur = classify_jurisdiction(query)

    # Step 2: Apply Qdrant filter BEFORE any retrieval
    filter_condition = Filter(
        must=[FieldCondition(key="jurisdiction", match=MatchValue(value=jur))]
    )

    # Step 3: Dense + BM25 are BOTH filtered
    dense_results = qdrant.search(
        collection_name="tenancy_acts",
        query_vector=embed(query),
        query_filter=filter_condition,  # <-- critical
        limit=20
    )
    sparse_results = qdrant.search(
        collection_name="tenancy_acts",
        query_vector=sparse_embed(query),
        query_filter=filter_condition,  # <-- critical
        limit=20
    )

    return rrf_fuse(dense_results, sparse_results)
```

Without this filter, a query about "VIC rent increase" could retrieve a NSW section with a very similar embedding — producing a citation for the wrong state.

---

## 4. Model Routing & Cascading Strategy

### Cost-Effective Cascade

Using a single model for all tasks is wasteful. Classifying jurisdiction requires minimal reasoning. Legal IRAC reasoning requires maximum capability.

```
                        User Query
                            │
                            ▼
┌───────────────────────────────────────────────────────┐
│ Layer 1: Intent & Slot Classifier                     │
│ Model: Amazon Nova Lite ($0.06/M input tokens)        │
│ Task:                                                   │
│   - Extract: {jurisdiction, role, question_type,       │
│                entities (dates, sections)}              │
│   - Detect ambiguous jurisdiction → ask clarifying Q   │
│ Cost per query: ~$0.00001                              │
│ Latency: ~150ms                                        │
└───────────────────────────────────────────────────────┘
                            │
                            ▼
┌───────────────────────────────────────────────────────┐
│ Layer 2: Hybrid Retrieval (Qdrant + BGE-Reranker)     │
│ Described in Section 3 above                          │
│ Latency: ~800ms                                       │
└───────────────────────────────────────────────────────┘
                            │
                            ▼
┌───────────────────────────────────────────────────────┐
│ Layer 2.5: Tool Binding via Bedrock Converse toolConfig│
│ Model: None (API layer)                                │
│ Task:                                                    │
│   - The 3 tools (rag_retriever, date_calculator,        │
│     rent_increase_validator) are registered in           │
│     toolConfig when calling Converse API                 │
│   - Claude Sonnet sees tools as first-class citizens     │
│   - LLM emits structured toolUse blocks instead of      │
│     parsing tool calls from free text                    │
│   - Multi-turn: toolUse → Lambda executes → toolResult  │
│     → LLM continues with result in context               │
│ Cost per query: $0 (tool defs add ~100 tokens)          │
│ Latency: +0ms (integrated into generation)              │
└───────────────────────────────────────────────────────┘
                            │
                            ▼
┌───────────────────────────────────────────────────────┐
│ Layer 3: Legal IRAC Reasoner                           │
│ Model: Claude Sonnet ($3/M input, $15/M output)       │
│ Prompt Structure:                                       │
│   Issue:   Restate the user's legal question            │
│   Rule:    Cite the relevant sections from chunks       │
│   Application: Apply rule to user's specific scenario   │
│   Conclusion: Summarize answer with deadline calc       │
│ Cost per query: ~$0.005-0.01 (2K input + 500 output)  │
│ Latency: ~1500ms                                        │
└───────────────────────────────────────────────────────┘
                            │
                            ▼
┌───────────────────────────────────────────────────────┐
│ Layer 4: Citation Verifier                             │
│ Model: None (rule-based)                              │
│ Task:                                                   │
│   - Extract all [Section X] citations from output       │
│   - Check each exists in retrieved_chunk_ids            │
│   - If any citation is NOT in retrieved chunks:         │
│     → Strip the claim or mark it as uncertain           │
│ Latency: ~50ms                                         │
└───────────────────────────────────────────────────────┘
                            │
                            ▼
                     Final Response
```

### Cost Breakdown Per Query

| Layer | Model | Input Tokens | Output Tokens | Cost Per Query |
|---|---|---|---|---|---|
| 1. Classifier | Nova Lite | ~300 | ~50 | ~$0.00002 |
| 2. Retrieval | Titan Embed + Qdrant + BGE | — | — | ~$0.0001 (Qdrant free) |
| 2.5. Tool Binding | Bedrock Converse `toolConfig` | ~100 (tool defs) | — | $0 (negligible) |
| 3. Legal Reasoner | Claude Sonnet | ~2,000 | ~500 | ~$0.008 |
| 4. Citation Verifier | Rule-based | — | — | $0 |
| **Total** | | | | **~$0.008/query** |

At **50 queries/day** (demo traffic): **~$0.40/day → ~$12/month**.

### Why Not Sonnet for Everything?

Using Sonnet for classification would cost ~$0.004/query (30 tokens each way) vs Nova Lite at ~$0.00002/query. At 50 queries/day, the saving is only ~$6/month — modest. The real benefit: lower latency for classification (~150ms vs ~800ms), and the architectural pattern is itself a demonstration of cost-aware system design.

### IRAC Prompt Template (Layer 3)

```
You are an Australian tenancy law expert. Answer based ONLY on the
retrieved legal text chunks below.

Retrieved Chunks:
{retrieved_chunks}

User Question:
{query}

Use this structure:
1. **Issue:** Restate the user's question as a legal issue.
2. **Rule:** Cite the relevant sections from the chunks. Use format:
   [Act Name, Section X, Subsection Y].
3. **Application:** Apply the rule to the user's specific scenario.
   If dates are involved, calculate the deadline.
4. **Conclusion:** Summarize clearly.

CRITICAL RULES:
- Every claim MUST have a citation.
- If no chunk supports a claim, say "I cannot find a specific
  provision" — do NOT invent citations.
- If the user's jurisdiction does not match the chunks, say so.
```

### Verification Guard (Layer 4)

```python
def verify_citations(llm_output: str, retrieved_chunks: list[dict]) -> str:
    citations = extract_citations(llm_output)  # regex for [*, Section X, *]
    valid_ids = {c["chunk_id"] for c in retrieved_chunks}

    for citation in citations:
        if citation.chunk_id not in valid_ids:
            # Mark as uncertain rather than silently removing
            llm_output = llm_output.replace(
                citation.text,
                f"{citation.text} ⚠️ (citation not verified)"
            )

    return llm_output
```

This is the last line of defense. Even if the LLM hallucinates a section number, the verifier catches it. The user sees uncertainty markers rather than false legal authority.

---

## 5. Future Considerations

### Graph RAG for Cross-Reference Traversal

Standard RAG treats chunks as independent units — a limitation when legal sections contain explicit cross-references (e.g., VIC Section 44: "A notice under subsection (1) must be in the form prescribed under Section 12"). A pure vector search may retrieve Section 44 but miss Section 12.

**Graph RAG** (Microsoft, 2024) addresses this by building a knowledge graph during ingestion: sections become nodes, cross-references become edges. At query time, community detection enables global summarization and multi-hop traversal.

**Why it was not adopted here:**

| Factor | Assessment |
|---|---|
| Cost | LLM-based entity extraction during ingestion adds one-time cost (~$5–20 for 5 Acts). Manageable. |
| Latency | Graph traversal + community summarization pushes past the 3s target. |
| Infrastructure | Requires Neo4j / Amazon Neptune — incompatible with Qdrant Free Tier and $0 infrastructure goal. |
| Citation precision | Graph RAG optimizes for synthesis, not for exact section citation. Conflicts with the strict citation-groundedness NFR. |

**Cheaper alternative (already implemented):** Cross-references are detected during ingestion via regex (`see Section (\d+)`) and the referenced section text is appended to the source chunk as context. This gives the LLM cross-reference awareness at retrieval time without a graph database.

```
Ingestion: Section 44 chunk → regex match "Section 12" → fetch S12 text → append as context
Query:     "What notice is needed?" → retrieves S44 (with S12 context) → LLM has both
```

### MCP Protocol Layer

Expose the three tools as a **Model Context Protocol (MCP) server** (Python, `mcp` SDK). This makes the agent SDK-agnostic — any MCP host can invoke the tools without knowing the LangGraph infrastructure.

```
┌──────────────────┐     stdio/SSE     ┌──────────────────────────────┐
│  Claude Desktop  │◀────────────────▶│  MCP Server (Python)         │
│  VS Code /Cursor │                   │  - rag_retriever            │
│  Custom Client   │                   │  - date_calculator          │
└──────────────────┘                   │  - rent_increase_validator  │
                                       │                              │
                                       │  Lambda deployment: same     │
                                       │  function as LangGraph       │
                                       └──────────────────────────────┘
```

**Why it adds value:**
- No frontend needed — connect from Claude Desktop via one JSON config file
- Skills/resume signal: MCP is the emerging standard (Anthropic, OpenAI, Google all adopting)
- SDK independence: swap LangGraph for any other orchestrator without touching tool code

**Implementation effort:** ~4 hours. Python `mcp` SDK, wrap existing tool functions with `@mcp.tool()`, deploy via Lambda SSE endpoint or stdio for local dev.

### Mem0 Cross-Session Memory

Integrate **Mem0** to persist user preferences (jurisdiction, role) across sessions, reducing slot-filler retries for returning users.

```
Session 1: "I'm a tenant in VIC"
  → SlotFiller learns jurisdiction=VIC, role=tenant
  → Mem0.store(user_id, {jurisdiction: VIC, role: tenant})

Session 2: "What are my rights for bond return?"
  → Mem0.recall(user_id) → pre-fills jurisdiction=VIC, role=tenant
  → SlotFiller skipped → directly to retrieval
```

**Integration points:**
- `memory_recall` node at graph entry — pre-fills slots from Mem0
- `memory_store` node after `slot_filler` — persists learned information
- `memory_log_failure` in `fallback_node` — logs unresolved queries for QA review

**Cost:** Mem0 free tier covers demo scale (1K memories, 100K search tokens/month). ~$5-10/mo for the paid tier at production scale.

### Additional Future Directions

- **Prompt caching** with Claude Sonnet on Bedrock to reduce latency and cost on repeated queries.
- **Active learning** — flag queries where the citation verifier fires, collect them for chunk quality review.
- **Multi-language support** — translate queries to English, retrieve English chunks, translate answers back.
- **Batch mode** — use Bedrock batch inference (50% discount) for bulk compliance audits of property portfolios.
