# PRD: Australian Residential Tenancies Compliance Agent

## 1. Executive Summary & Business Pain Points

Australia's eight states and territories each maintain a distinct Residential Tenancies Act. Compliance cross-jurisdiction is a persistent operational risk for property managers, landlords, and tenants alike.

### Concrete Legislative Divergence Examples

| Scenario | VIC | NSW | QLD |
|---|---|---|---|
| Rent increase frequency | Once every 12 months (minimum 60 days' notice) | Once every 12 months (minimum 60 days' notice) | Once every 12 months (minimum 2 months' notice) |
| Notice to Vacate (no grounds) | Abolished (must provide prescribed reason) | 90 days' notice required | 2 months' notice required |
| Bond disposal timeline | 28 days from tenancy end | 14 days from tenancy end | 7 days from tenancy end |
| Entry notice period | 7 days (general), 24 hours (inspection) | 2 days (general), 24 hours (inspection) | 1 day (general), 24 hours (inspection) |
| Cooling-off period | 3 business days | None | None |

### Cost of Non-Compliance

- VCAT (VIC) application fees: ~$65–$330 per claim
- NCAT (NSW) penalties: up to $22,000 for serious breaches
- QCAT (QLD) compensation orders: unlimited jurisdiction for tenancy matters
- Reputational damage and tenancy database listings

A property manager overseeing 200+ properties across multiple states currently researches compliance manually — consulting PDF legislation, government fact sheets, and precedent tribunal rulings. Estimated time per query: 15–45 minutes. Our system targets <5 seconds.

---

## 2. User Personas & Target Audience

### Persona A: Tenant

- **Scenario:** Renting in VIC, received a rent increase notice. Wants to know if the 60-day notice period has been met.
- **Needs:** Plain-language answer with a direct citation ("Section 44 of the Residential Tenancies Act 1997 (VIC)"), calculation of deadlines, and understanding of next steps (dispute rights).
- **Constraints:** May not know legal terminology. May not know which Act applies.

### Persona B: Property Manager

- **Scenario:** Manages a portfolio across VIC and NSW. A landlord client wants to issue a notice to vacate.
- **Needs:** Jurisdiction-aware answer with statutory forms referenced, timeline calculation, and explicit warnings about prohibited grounds or notice period errors.
- **Constraints:** Needs speed (sub-3-second response), accuracy at portfolio scale, and audit trail for compliance records.

### Persona C: Independent Landlord

- **Scenario:** Self-managing a single property in QLD. Wants to draft a lease renewal.
- **Needs:** Step-by-step guidance with form references, bond lodgement instructions, and maintenance obligation clarity.
- **Constraints:** Limited legal literacy. May ask imprecise questions.

### Persona D: Legal / Policy Researcher

- **Scenario:** Comparing rent increase regulations across all states.
- **Needs:** Cross-jurisdictional comparison tables, direct section references, amendment history awareness.
- **Constraints:** Requires high citation precision. Tolerates longer responses.

---

## 3. Functional Requirements (MVP Scope)

### FR-1: Multi-Turn Conversational Onboarding

- The system identifies the user's jurisdiction, role, and intent early in the conversation.
- Onboarding is conversational, not a fixed questionnaire — the system infers jurisdiction from the query and can re-confirm if ambiguous.
- State metadata is persisted across the conversation graph so follow-up questions remain jurisdiction-scoped.

### FR-2: Automated State-Based Metadata Routing

- Incoming queries are classified to a jurisdiction (VIC, NSW, QLD, SA, WA, TAS, ACT, NT) or flagged as cross-jurisdiction.
- Retrieval is pre-filtered by jurisdiction metadata tag to prevent cross-state leakage.
- Cross-jurisdiction queries return per-state comparisons with explicit labeling.

### FR-3: Compliance Timeline Calculations

- For queries involving notice periods, cooling-off windows, or bond deadlines, the system calculates absolute dates relative to a user-provided event date.
- Timelines cite the specific section and subsection that mandates the duration.

### FR-4: Strict Mandatory Legal Source Citation

- Every claim in the generated answer must be followed by a citation in the format: `[Act Name, Section X, Subsection Y]`.
- If a claim cannot be supported by a retrieved chunk, the system must state uncertainty rather than hallucinate.
- A verification step checks each citation against the retrieved chunk IDs before final output.

### FR-5: Jurisdiction Disambiguation

- If the user's query does not specify a jurisdiction, the system asks a clarifying question before retrieving.
- If the user references a jurisdiction that does not match the metadata of retrieved chunks, the system flags the mismatch and re-routes.

### FR-6: Fallback / Escalation

- For queries the system cannot answer with high confidence (low reranker score), it defaults to: "I cannot answer this with confidence. Here are the relevant sections for manual review: …"
- Option to export the conversation and citations for human legal review.

---

## 4. Non-Functional Requirements (NFRs)

### NFR-1: End-to-End Latency

- **Target:** <3 seconds at the 95th percentile for single-turn queries.
- **Measurement:** Time from API Gateway receipt to response body sent.
- Budget: intent classification (~200ms) + retrieval (~500ms) + reranking (~300ms) + generation (~1500ms) + verification (~200ms).

### NFR-2: Hallucination Containment

- **Target:** Zero hallucination — every sentence in the answer must trace to a supporting chunk.
- **Mechanism:** Citation verification step after generation. Reject and flag any answer with citations not present in the retrieved chunk set.
- **Observability:** Log citation-to-chunk mappings for every response.

### NFR-3: Citation Groundedness

- **Target:** Every answer must include at least one citation. Every citation must include Act, Section, and Subsection.
- **Definition:** Groundedness = number of claims in answer that are supported by retrieved chunks / total claims in answer.
- **Floor:** 100% grounded (claims without citations are stripped or tagged as uncertainty).

### NFR-4: Data Privacy

- All inference occurs within the deployment environment — no query data leaves the AWS account.
- No logs containing personally identifiable information.
- No model training on user queries.

### NFR-5: Availability

- **Target:** 99% uptime (acceptable for MVP / demo).
- **Mechanism:** Lambda auto-scaling, Qdrant Free Tier (single node), Bedrock on-demand (no provisioned throughput).
- Planned maintenance window: notified 24 hours in advance.

### NFR-6: Cost Efficiency

- **Target:** <$15/month operating cost at demo scale.
- **Mechanism:** Lambda (free tier), Qdrant Free Tier, model cascade (Nova Lite for classification, Sonnet for legal reasoning only).
- Continuous cost monitoring via AWS Cost Explorer.

### NFR-7: Audit Logging

- Every Q&A pair is logged with: timestamp, jurisdiction, model used, chunk IDs retrieved, citations emitted, latency breakdown.
- Logs retained for 90 days.
