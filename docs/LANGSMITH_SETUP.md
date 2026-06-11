# LangSmith Setup: Observability & Evaluation

## 1. Project Setup

### Prerequisites

- LangSmith account (free tier: 5K traces/month, unlimited datasets)
- API key from `https://smith.langchain.com/settings`

### Environment Variables

Add to `.env`:

```bash
# LangSmith
LANGSMITH_API_KEY=lsv2_...
LANGSMITH_PROJECT=aus-tenancy-agent
LANGSMITH_ENDPOINT=https://api.smith.langchain.com
LANGSMITH_TRACING=true
```

### Verify Connection

```python
from langsmith import Client

client = Client()
print(client.list_projects())
# Should show "aus-tenancy-agent" project
```

---

## 2. Tracing Integration

### Auto-Instrumentation via Callback Handler

The simplest integration point is the `LangSmithCallbackHandler` passed to the LangGraph graph at `compile()` time:

```python
from langchain_core.tracers import LangSmithTracer
from langsmith.run_helpers import traceable

# Option A: Global auto-tracing via LangGraph callback
from langgraph.graph import StateGraph

workflow = StateGraph(AgentState)
# ... add nodes and edges ...

app = workflow.compile()

# Invoke with LangSmith callback
from langsmith import Client as LangSmithClient

ls_client = LangSmithClient()
app.invoke(
    {"messages": [{"role": "user", "content": query}]},
    config={"callbacks": [ls_client.create_tracer_callback()]}
)
```

### Per-Node Decorator Pattern

For finer-grained control, decorate each node function with `@traceable`:

```python
from langsmith.run_helpers import traceable


@traceable(name="intent_classifier", run_type="chain")
def intent_classifier_node(state: AgentState) -> AgentState:
    # Nova Lite classification
    # ...
    return state


@traceable(name="rag_retriever", run_type="retriever")
def rag_retriever_node(state: AgentState) -> AgentState:
    # Qdrant hybrid search
    # ...
    return state


@traceable(name="legal_reasoner", run_type="llm")
def legal_reasoner_node(state: AgentState) -> AgentState:
    # Claude Sonnet IRAC generation
    # ...
    return state


@traceable(name="citation_verifier", run_type="tool")
def citation_verifier_node(state: AgentState) -> AgentState:
    # Rule-based verification
    # ...
    return state


@traceable(name="slot_filler", run_type="chain")
def slot_filler_node(state: AgentState) -> AgentState:
    # Clarifying questions
    # ...
    return state


@traceable(name="fallback", run_type="chain")
def fallback_node(state: AgentState) -> AgentState:
    # Uncertainty + redirect
    # ...
    return state
```

### Trace Structure (per query)

```
aus-tenancy-agent/
├── Root Run (invoke)
│   ├── memory_recall          [latency, cost]
│   ├── intent_classifier      [latency, cost, jurisdiction extracted]
│   ├── slot_filler            [latency, slots filled]
│   ├── rag_retriever          [latency, chunks retrieved, confidence]
│   │   ├── dense_search       [Qdrant API call]
│   │   └── bm25_search        [Qdrant API call]
│   ├── legal_reasoner         [latency, cost, tool calls]
│   │   ├── tool: date_calculator  [toolUse → result]
│   │   └── tool: rent_increase_validator  [toolUse → result]
│   ├── citation_verifier      [latency, citations verified]
│   └── memory_store           [latency, data persisted]
```

---

## 3. Dataset Testing

### Upload Golden Dataset

Convert the evaluation samples from `docs/EVALUATION_PLAN.md` into a LangSmith Dataset:

```python
from langsmith import Client as LangSmithClient

client = LangSmithClient()

# Create dataset
dataset = client.create_dataset(
    dataset_name="tenancy-agent-golden-v1",
    description="30+ evaluation samples covering all 8 jurisdictions + cross-jurisdiction + out-of-scope",
)

# Add examples
samples = [
    {
        "inputs": {"question": "my landlord just sent me a rent increase notice... i live in melbourne..."},
        "outputs": {
            "expected_citations": [
                {"act": "Residential Tenancies Act 1997 (VIC)", "section": "44", "subsection": "1"}
            ],
            "expected_jurisdiction": "VIC",
            "ground_truth": "Under Section 44(1) of the RTA 1997 (VIC)..."
        },
    },
    # ... all 30+ samples
]

client.create_examples(
    inputs=[s["inputs"] for s in samples],
    outputs=[s["outputs"] for s in samples],
    dataset_id=dataset.id,
)
```

### Run Evaluation

```python
from langsmith.evaluation import evaluate
from langsmith.schemas import Example, Run


def faithfulness_metric(run: Run, example: Example) -> dict:
    """Custom metric: verify all citations in output exist in expected_citations."""
    output = run.outputs.get("verified_output", "")
    expected = example.outputs.get("expected_citations", [])
    # parse citations from output, check against expected
    score = score_citations(output, expected)
    return {"key": "citation_faithfulness", "score": score}


evaluation_results = evaluate(
    lambda input: app.invoke({"messages": [{"role": "user", "content": input["question"]}]}),
    data="tenancy-agent-golden-v1",
    evaluators=[faithfulness_metric],
    experiment_prefix="pre-deploy-check",
)
```

### Regression Thresholds

| Metric | Threshold | Action |
|---|---|---|
| Citation faithfulness | >0.90 | Block deploy if below |
| Jurisdiction accuracy | >0.95 | Block deploy if below |
| Avg latency | <3000ms | Investigate slow nodes |
| Cost per query | <$0.015 | Warn if trending up |

---

## 4. Cost Monitoring

### Per-Trace Cost Metadata

Attach cost data to each trace for dashboard visibility:

```python
@traceable(name="legal_reasoner", run_type="llm")
def legal_reasoner_node(state: AgentState) -> AgentState:
    response = bedrock_converse_with_backoff(...)
    usage = response["usage"]

    # Attach cost as run metadata
    input_cost = usage["inputTokens"] * 3 / 1_000_000       # $3/MTok
    output_cost = usage["outputTokens"] * 15 / 1_000_000     # $15/MTok
    total_cost = round(input_cost + output_cost, 6)

    # This metadata appears in LangSmith trace details
    langsmith.update_current_run(
        metadata={
            "cost_per_query": total_cost,
            "input_tokens": usage["inputTokens"],
            "output_tokens": usage["outputTokens"],
            "model": "claude-sonnet",
        }
    )

    return state
```

### CloudWatch Dashboard

Combine LangSmith trace cost + CloudWatch metrics for a cost dashboard:

```
Query: SELECT AVG(metadata.cost_per_query) FROM trace WHERE timestamp > now() - 7d
Alert: WHEN AVG(cost_per_query) > 0.015 THEN "Daily cost exceeded $0.75 budget"
```

---

## 5. Debugging Workflow

### Identifying Bottlenecks

Use LangSmith's trace view to identify the slowest node:

```python
from langsmith import Client

client = Client()
runs = client.list_runs(
    project_name="aus-tenancy-agent",
    start_time=datetime.now() - timedelta(hours=24),
)

for run in runs:
    t = run.child_runs  # trace tree
    rag_latency = next((c.latency for c in t if c.name == "rag_retriever"), 0)
    reasoner_latency = next((c.latency for c in t if c.name == "legal_reasoner"), 0)
    if rag_latency > 0.8:
        print(f"RAG node slow in run {run.id}: {rag_latency}s")
```

### Comparing Chunking Strategies

Tag each deployment with a chunking version:

```python
# In invocation config:
app.invoke(inputs, config={
    "tags": ["chunking-v2", "titan-embed-v2"],
})
```

Then use LangSmith's filter-by-tag to compare metrics across strategies and determine whether the new chunking approach improved retrieval quality.

---

## 6. LangSmith vs Alternatives

| Feature | LangSmith | Langfuse | Notes |
|---|---|---|---|
| Free tier | 5K traces/month | 3K observations/day | Both sufficient for demo |
| Dataset testing | ✅ Native | ✅ Via SDK | LangSmith more polished UX |
| Cost tracking | ✅ Metadata field | ✅ Built-in pricing | Langfuse does this out of box |
| LLM-as-judge eval | ✅ Auto-eval | ✅ Via SDK | LangSmith's auto-eval is simpler |
| Open source | ❌ | ✅ Self-hostable | Langfuse wins for compliance-heavy prod |
| LangGraph integration | ✅ Native callback | ✅ Via `callbacks` | Both work, LangSmith has tighter integration |

For this project, LangSmith is chosen for its native LangGraph integration — zero-config tracing once the callback handler is wired in.
