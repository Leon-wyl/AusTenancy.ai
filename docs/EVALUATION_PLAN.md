# Evaluation Plan: LLM Quality Assurance & Monitoring

## Framework: Ragas (RAG Assessment)

Ragas provides three core metrics that map directly to the agent's non-functional requirements:

| Metric | What It Measures | NFR Mapping | Target |
|---|---|---|---|
| **Faithfulness** | Are claims in the answer supported by the retrieved chunks? Measures claim-level NLI between each sentence in the answer and the retrieved context. | NFR-2 (Hallucination Containment) | >0.90 |
| **Context Precision** | Are the most relevant chunks ranked highest? Measures whether the signal-to-noise ratio in retrieved chunks is acceptable. | NFR-1 (Latency) + NFR-3 (Citation Groundedness) | >0.80 |
| **Answer Relevance** | Does the answer actually address the user's question? Measures cosine similarity between the question and the generated answer. | NFR-3 (Citation Groundedness) | >0.85 |

### Integration Architecture

```
┌─────────────────────────────────────────────────────────────┐
│  Deployment Pipeline                                          │
├─────────────────────────────────────────────────────────────┤
│                                                               │
│  ┌──────────┐    ┌──────────────┐    ┌────────────────────┐  │
│  │ Ingestion│───▶│ Deploy Graph │───▶│ Run Eval Suite     │  │
│  │ (new Act)│    │ (Lambda)     │    │ (Ragas batch eval) │  │
│  └──────────┘    └──────────────┘    └────────┬───────────┘  │
│                                                │              │
│                                                ▼              │
│                                       ┌────────────────────┐  │
│                                       │ Compare vs.        │  │
│                                       │ Previous Baseline │  │
│                                       └────────┬───────────┘  │
│                                                │              │
│                              ┌─────────────────┼─────┐        │
│                              ▼                 ▼     ▼        │
│                    ┌────────────┐   ┌────────────┐            │
│                    │ Approve    │   │ Investigate│            │
│                    │ Deploy     │   │ Regressions│            │
│                    └────────────┘   └────────────┘            │
└─────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────┐
│  Runtime Monitoring (CloudWatch)                              │
├─────────────────────────────────────────────────────────────┤
│  Per-query: faithfulness_score, context_precision, latency,  │
│  citation_verification_passed, model_cascade_used            │
│                                                               │
│  Alert: rolling 1h avg faithfulness < 0.85                    │
│  Alert: citation_verifier fires on >10% of queries in 1h     │
└─────────────────────────────────────────────────────────────┘
```

### Evaluation Execution

Evaluations run as an offline batch step after each ingestion update (new Act added, chunk strategy changed) or before deployment to production:

```python
from ragas import evaluate
from ragas.metrics import faithfulness, context_precision, answer_relevance
from datasets import Dataset

def run_evaluation(dataset: Dataset) -> dict:
    result = evaluate(
        dataset=dataset,
        metrics=[faithfulness, context_precision, answer_relevance]
    )
    return {
        "faithfulness": result["faithfulness"],
        "context_precision": result["context_precision"],
        "answer_relevance": result["answer_relevance"]
    }
```

Note: Ragas requires an LLM-as-judge (typically GPT-4 or Claude) for the NLI-based metrics. At demo scale (~50 eval samples), this costs ~$0.20 per evaluation run — acceptable for a pre-deployment gate.

### Regression Thresholds

| Condition | Action |
|---|---|
| Faithfulness drops >0.05 from baseline | Block deployment, flag chunks for review |
| Context Precision < 0.75 | Review metadata filtering + reranker |
| Answer Relevance < 0.80 | Review IRAC prompt template |
| CitationVerifier fires on any golden sample | Emergency — chunk quality issue |

---

## 2. Golden Dataset Standard

### Dataset Structure

The golden dataset lives in `eval/golden_dataset.json` (version-controlled) and contains **30–50 samples** evenly distributed across:

| Dimension | Values |
|---|---|
| **Jurisdictions** | VIC, NSW, QLD, SA, WA, TAS, ACT, NT + cross-jurisdiction comparisons |
| **Question types** | rent_increase, notice_to_vacate, bond_dispute, maintenance, lease_renewal, general |
| **Roles** | tenant, property_manager, landlord, legal_researcher |
| **Difficulty** | direct (has section ref), colloquial (paraphrased), ambiguous (no state specified), out-of-scope (litigation advice) |

### Sample Specification

Each sample is a JSON object with the following schema (Ragas `EvaluationDataset` compatible):

```json
{
    "question": "User's question verbatim — includes colloquial phrasing, abbreviations, or imprecise language where applicable.",
    "ground_truths": [
        "Authoritative answer as a string. Must reference specific Acts, sections, and subsections."
    ],
    "expected_citations": [
        {"act": "Residential Tenancies Act 1997", "section": "44", "subsection": "1"},
        {"act": "Residential Tenancies Act 1997", "section": "45", "subsection": "1"}
    ],
    "metadata": {
        "jurisdiction": "VIC",
        "question_type": "rent_increase",
        "role": "tenant",
        "difficulty": "colloquial",
        "expected_jurisdictions": ["VIC"],
        "notes": "Tenant does not use legal terminology. Does not specify state."
    }
}
```

### Dataset Coverage Rules

| # | Rule |
|---|---|
| 1 | Each of the 8 jurisdictions must have at least 2 samples (1 direct, 1 colloquial) |
| 2 | Cross-jurisdiction comparison: at least 3 samples (e.g., "Compare rent increase rules in VIC vs NSW") |
| 3 | Ambiguous jurisdiction: at least 3 samples where the question does not name a state (tests slot_filler routing) |
| 4 | Out-of-scope: at least 2 samples (e.g., "I want to sue my landlord for emotional distress") — must test fallback_node |
| 5 | Entity extraction: at least 5 samples with embedded dates, amounts, or section references that tools must parse |

---

## 3. Reference JSON Schema (Ragas EvaluationDataset)

Below is a fully populated sample of the evaluation dataset with **four diverse scenarios** covering rent increase, unlawful eviction, bond dispute, and out-of-scope fallback.

```json
[
    {
        "question": "my landlord just sent me a rent increase notice saying my rent goes up in 4 weeks. i live in melbourne. is that legal?",
        "ground_truths": [
            "Under Section 44(1) of the Residential Tenancies Act 1997 (VIC), a landlord must give at least 60 days' written notice of a rent increase. A 4-week (28-day) notice period does not satisfy this requirement. Additionally, Section 44(3) specifies that rent may only be increased once every 12 months. If your last increase was less than 12 months ago, the notice is also invalid on that ground. You can challenge the notice at VCAT — application fees start at approximately $66.60 for tenants."
        ],
        "expected_citations": [
            {"act": "Residential Tenancies Act 1997 (VIC)", "section": "44", "subsection": "1"},
            {"act": "Residential Tenancies Act 1997 (VIC)", "section": "44", "subsection": "3"}
        ],
        "metadata": {
            "jurisdiction": "VIC",
            "question_type": "rent_increase",
            "role": "tenant",
            "difficulty": "colloquial",
            "expected_jurisdictions": ["VIC"],
            "entities": {
                "notice_period_days": 28,
                "state_mention": "melbourne",
                "question_type_hint": "increase",
                "minimum_notice_days": 60
            },
            "notes": "Colloquial phrasing, infers VIC from 'melbourne'. Tests both notice period AND frequency compliance.",
            "expected_tool_calls": ["rag_retriever", "rent_increase_validator"]
        }
    },
    {
        "question": "my landlord told me to get out in 2 weeks with no reason. i'm in sydney. do i have any rights?",
        "ground_truths": [
            "Under Section 85 of the Residential Tenancies Act 2010 (NSW), a landlord must give at least 90 days' notice for a no-grounds termination of a periodic tenancy. A 2-week notice period is unlawful. For fixed-term tenancies, the landlord must have a specific ground under Section 84 (e.g., breach of agreement, end of fixed term). You do not need to vacate based on this notice. You should: (1) inform the landlord in writing that the notice is invalid, (2) contact the NSW Fair Trading Tenancy Advice Service on 13 32 20, and (3) if the landlord changes the locks or removes your belongings without a tribunal order, file an urgent application at NCAT for retaliatory eviction under Section 115."
        ],
        "expected_citations": [
            {"act": "Residential Tenancies Act 2010 (NSW)", "section": "85", "subsection": "1"},
            {"act": "Residential Tenancies Act 2010 (NSW)", "section": "84", "subsection": null},
            {"act": "Residential Tenancies Act 2010 (NSW)", "section": "115", "subsection": null}
        ],
        "metadata": {
            "jurisdiction": "NSW",
            "question_type": "notice_to_vacate",
            "role": "tenant",
            "difficulty": "colloquial",
            "expected_jurisdictions": ["NSW"],
            "entities": {
                "notice_period_days": 14,
                "state_mention": "sydney",
                "question_type_hint": "out",
                "minimum_notice_days": 90
            },
            "notes": "Tests no-grounds termination rules. Colloquial 'get out' phrasing. Infers NSW from 'sydney'.",
            "expected_tool_calls": ["rag_retriever", "date_calculator"]
        }
    },
    {
        "question": "Compare the bond return timeline after a tenancy ends — how long does the landlord have in Victoria vs Queensland?",
        "ground_truths": [
            "In Victoria, under Section 417 of the Residential Tenancies Act 1997 (VIC), the landlord must lodge a bond disposal claim with the Residential Tenancies Bond Authority within 28 days after the tenancy ends. If no claim is lodged within 28 days, the bond is automatically released to the tenant. In Queensland, under Section 138 of the Residential Tenancies Act 2008 (QLD), the landlord must make a claim to the Residential Tenancies Authority within 14 days after the tenancy ends. If disputed, the RTA holds the bond until the parties agree or QCAT makes an order. Summary: VIC gives the landlord 28 days, QLD gives 14 days. The tenant in QLD needs to act faster if they want their bond returned promptly."
        ],
        "expected_citations": [
            {"act": "Residential Tenancies Act 1997 (VIC)", "section": "417", "subsection": null},
            {"act": "Residential Tenancies Act 2008 (QLD)", "section": "138", "subsection": null}
        ],
        "metadata": {
            "jurisdiction": "CROSS",
            "question_type": "cross_jurisdiction",
            "role": "tenant",
            "difficulty": "direct",
            "expected_jurisdictions": ["VIC", "QLD"],
            "entities": {
                "comparison_states": ["VIC", "QLD"],
                "question_type_hint": "bond"
            },
            "notes": "Explicit cross-jurisdiction comparison. User names both states. Tests comparison table generation.",
            "expected_tool_calls": ["rag_retriever", "rag_retriever"]
        }
    },
    {
        "question": "I want to take my landlord to court for emotional distress. Can your system help me build a case?",
        "ground_truths": [
            "I cannot provide legal advice or help build a litigation case, as this is outside the scope of this tenancy compliance information service. For the specific situation you described, I recommend contacting your state's tenant union, who can provide free legal advice: VIC: Tenants Victoria (www.tenantsvic.org.au), NSW: Tenants' Union of NSW (www.tenants.org.au), QLD: Tenants Queensland (www.tenantsqld.org.au). If you are unsure which state you are in, please let me know and I can direct you to the correct service."
        ],
        "expected_citations": [],
        "metadata": {
            "jurisdiction": null,
            "question_type": "out_of_scope",
            "role": "tenant",
            "difficulty": "out_of_scope",
            "expected_jurisdictions": [],
            "entities": {},
            "notes": "Out-of-scope query. No retrieval or generation. Must route to fallback_node and redirect to tenant union.",
            "expected_tool_calls": []
        }
    }
]
```

### Using the Dataset

```python
from datasets import Dataset
from ragas import evaluate
from ragas.metrics import faithfulness, context_precision, answer_relevance

# Load golden dataset
import json
with open("eval/golden_dataset.json") as f:
    samples = json.load(f)

# Convert to Hugging Face Dataset
dataset = Dataset.from_list([
    {
        "question": s["question"],
        "ground_truths": s["ground_truths"],
        # answer, contexts are populated at eval time by running the agent
    }
    for s in samples
])

# Run evaluation
result = evaluate(
    dataset=dataset_with_agent_outputs,
    metrics=[faithfulness, context_precision, answer_relevance]
)
print(result)
```
