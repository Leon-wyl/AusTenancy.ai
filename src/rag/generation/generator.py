"""
Phase 3: Prompt Engineering & LLM Generation.
Chains hybrid retrieval → LLM generation → citation verification.

Usage:
    python src/generation/generator.py --state VIC
    python src/generation/generator.py --state QLD
    python src/generation/generator.py --state VIC --include-chapters "*"
"""

import argparse
import logging
import os
import re
import sys
from abc import ABC, abstractmethod
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent.parent))

from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

# ── Reranker (FlashRank singleton) ────────────────────────────────────

_ranker = None


def _get_ranker():
    """Return a cached FlashRank Ranker instance."""
    global _ranker
    if _ranker is None:
        from flashrank.Ranker import Ranker

        logger.info("Loading FlashRank reranker model (first run downloads ~150MB)...")
        _ranker = Ranker()
    return _ranker


def rerank_context(
    query: str,
    chunks: list[dict],
    top_n: int = 3,
) -> list[dict]:
    """
    Cross-encode (query, chunk) pairs via FlashRank and return the top_n most relevant chunks.

    DISABLED BY DEFAULT for legal RAG. FlashRank is a general-domain (MS MARCO)
    cross-encoder that mis-ranks statutory text — it demotes correct sections
    (ranked #1 by hybrid search) below the top-n cutoff, collapsing context
    precision. Retained only for opt-in experimentation; the default pipeline
    (use_reranker=False) never calls this. See docs/EVALUATION_IMPLEMENTATION.md.

    Each chunk dict must have at least 'text'.  The original chunk dict is returned with
    an added 'rerank_score' field.
    """
    if not chunks:
        return []

    ranker = _get_ranker()
    passages = [{"id": i, "text": c["text"]} for i, c in enumerate(chunks)]

    logger.info("Reranking %d candidates → top %d...", len(passages), top_n)
    from flashrank.Ranker import RerankRequest

    request = RerankRequest(query=query, passages=passages)
    results = ranker.rerank(request)

    reranked = []
    for r in results[:top_n]:
        chunk = chunks[r["id"]].copy()
        chunk["rerank_score"] = float(round(r["score"], 4))
        reranked.append(chunk)

    return reranked


# ── LLM Provider Abstraction ──────────────────────────────────────────


class LLMProvider(ABC):
    """Abstract interface for LLM backends (swappable for AWS Bedrock in Phase 4)."""

    @abstractmethod
    def generate(self, system_prompt: str, user_prompt: str) -> str: ...


class DeepSeekLLMProvider(LLMProvider):
    """OpenAI-compatible SDK targeting DeepSeek's API endpoint."""

    def __init__(self, model: str | None = None):
        api_key = os.environ.get("DEEPSEEK_API_KEY", "")
        if not api_key:
            raise ValueError("DEEPSEEK_API_KEY is not set. Add it to .env or export it.")

        from openai import OpenAI

        self._client = OpenAI(
            api_key=api_key,
            base_url="https://api.deepseek.com",
        )
        self._model = model or os.environ.get("LLM_MODEL_ID", "deepseek-chat")

    def generate(self, system_prompt: str, user_prompt: str) -> str:
        logger.info("Calling LLM (%s)...", self._model)
        response = self._client.chat.completions.create(
            model=self._model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.0,
        )
        content = response.choices[0].message.content
        return content if content else ""


# ── State Context ─────────────────────────────────────────────────────

STATE_CONTEXT: dict[str, dict[str, str]] = {
    "VIC": {
        "act_cite": "Residential Tenancies Act 1997 (VIC)",
        "tribunal": "VCAT",
        "landlord_term": "rental provider",
        "tenant_term": "renter",
    },
    "NSW": {
        "act_cite": "Residential Tenancies Act 2010 (NSW)",
        "tribunal": "NCAT",
        "landlord_term": "landlord",
        "tenant_term": "tenant",
    },
    "QLD": {
        "act_cite": "Residential Tenancies and Rooming Accommodation Act 2008 (QLD)",
        "tribunal": "QCAT",
        "landlord_term": "lessor",
        "tenant_term": "tenant",
    },
    "SA": {
        "act_cite": "Residential Tenancies Act 1995 (SA)",
        "tribunal": "SACAT",
        "landlord_term": "landlord",
        "tenant_term": "tenant",
    },
    "WA": {
        "act_cite": "Residential Tenancies Act 1987 (WA)",
        "tribunal": "Magistrates Court",
        "landlord_term": "lessor",
        "tenant_term": "tenant",
    },
    "TAS": {
        "act_cite": "Residential Tenancy Act 1997 (TAS)",
        "tribunal": "Magistrates Court",
        "landlord_term": "owner",
        "tenant_term": "tenant",
    },
    "ACT": {
        "act_cite": "Residential Tenancies Act 1997 (ACT)",
        "tribunal": "ACAT",
        "landlord_term": "lessor",
        "tenant_term": "tenant",
    },
    "NT": {
        "act_cite": "Residential Tenancies Act 1999 (NT)",
        "tribunal": "NTCAT",
        "landlord_term": "landlord",
        "tenant_term": "tenant",
    },
}

# ── System Prompt ─────────────────────────────────────────────────────

_SYSTEM_PROMPT_TEMPLATE = """You are a highly rigorous Australian Residential Tenancies Compliance Auditor.

Your role is to answer tenancy law questions based SOLELY on the statutory text provided below. You must never rely on your internal knowledge of the law — only the context supplied.

CRITICAL RULES:
1. Every claim about what the law permits, requires, or prohibits MUST include
   an in-text citation: [State RTA Year Sec XXX]. This applies to both the Rule
   and Application sections — each factual premise in your analysis must be tied
   to a specific provision from the context.
   Example: "A residential rental provider must give 90 days notice [VIC RTA 1997 Sec 44(1)]. Because your landlord gave only 14 days notice, this does not satisfy the requirement [VIC RTA 1997 Sec 44(1)]."
2. Use the professional IRAC format (Issue, Rule, Application, Conclusion) where the question involves legal analysis.
3. Structure your response:
   a. First sentence: A direct, substantive answer based SOLELY on the provided
      context. Lead with what the context DOES support, not what it doesn't.
      Example: "No, your landlord cannot evict you for being 10 days behind on rent."
      Only begin with a disclaimer when the context is entirely silent on every
      aspect of the user's question.
   b. Then a concise IRAC analysis. Be thorough but avoid repeating facts
      the user already stated. The Application section should map statutory
      provisions to the user's specific situation — cite the provision that
      supports each point.
   c. End with one practical next step (e.g., challenge at {tribunal}, request written
      notice, seek legal advice from a tenancy advocacy service).
   d. End every answer with: "All statutory citations in this answer have been
      verified against the {act_cite}."

   If any aspect of the question cannot be answered from the provided context,
   add a brief "Limitations" paragraph after your conclusion noting what was
   not covered. Do NOT use limitations as a substitute for answering what the
   context does support.
4. If the user has not specified a jurisdiction, note this limitation and ask them to clarify.
 5. CRITICAL — You MUST ONLY cite sections that appear in the CONTEXT above.

    Before writing any [STATE RTA YEAR Sec XXX] citation:
    (a) Find that exact section in the CONTEXT.
    (b) Only cite it if the section appears in the CONTEXT.
    (c) Do not rely on memorized legal knowledge to supply missing section
        numbers, statutory rules, notice periods, monetary limits, penalties,
        or exceptions.

    If the retrieved context does not support a legal proposition, do not
    state it as law. Explicitly say that the retrieved context is insufficient
    to verify that point.

    Place every citation at the end of the sentence it supports. Do not use
    a citation as a grammatical part of the sentence.
 6. All provided context comes from standard residential tenancy provisions of the Act. Answer accordingly — do not speculate about rooming house, caravan park, or SDA provisions unless those are explicitly raised by the query."""


def _build_system_prompt(state: str | None) -> str:
    ctx = STATE_CONTEXT.get(state) if state else None
    act_cite = ctx["act_cite"] if ctx else "the relevant state legislation"
    tribunal = ctx["tribunal"] if ctx else "your local tenancy tribunal"
    return _SYSTEM_PROMPT_TEMPLATE.format(act_cite=act_cite, tribunal=tribunal)


# ── Citation Label Formatting ──────────────────────────────────────────


def _dedupe_preserve_order(items: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        if item not in seen:
            seen.add(item)
            result.append(item)
    return result


def _looks_like_form(part: str) -> bool:
    return bool(re.match(r"form\d+", part, re.I))


def _format_regulation_schedule_label(state: str, year: str, schedule: str, section_id: str) -> str:
    sch_num = re.sub(r"^Schedule\s+", "", schedule, flags=re.I)
    if "-" in section_id:
        part = section_id.split("-", 1)[1]
        if _looks_like_form(part):
            return f"[{state} REG {year} Sch {sch_num} Form {part[4:]}]"
        return f"[{state} REG {year} Sch {sch_num} Cl {part}]"
    return f"[{state} REG {year} Sch {sch_num}]"


def format_citation_label(chunk: dict) -> str:
    """Canonical citation label from chunk metadata (Act or Regulation).

    instrument_type absent → legacy Act chunk (backward compatible).
    """
    state = chunk.get("state") or chunk.get("jurisdiction") or "UNKNOWN"
    year = chunk.get("year") or chunk.get("instrument_year") or "????"
    section_id = chunk.get("section_id") or chunk.get("provision_id") or ""
    instrument_type = chunk.get("instrument_type", "")

    if instrument_type == "regulation":
        schedule = chunk.get("schedule")
        if schedule:
            return _format_regulation_schedule_label(state, year, schedule, section_id)
        return f"[{state} REG {year} Reg {section_id}]"

    # instrument_type absent → legacy Act chunk
    return f"[{state} RTA {year} Sec {section_id}]"


# ── Prompt Builder ────────────────────────────────────────────────────


def build_legal_prompt(query: str, chunks: list[dict]) -> str:
    """Inject chunk text into an IRAC-templated user prompt."""
    context_blocks = []
    for i, c in enumerate(chunks, 1):
        label = format_citation_label(c).strip("[]")
        context_blocks.append(
            f"--- Context {i} [{label}] (score={c.get('score', 'N/A')}) ---\n{c['text']}\n"
        )

    context_text = "\n".join(context_blocks)

    citation_labels = [format_citation_label(c) for c in chunks]
    unique_labels = _dedupe_preserve_order(citation_labels)
    whitelist = "\n".join(f"  - {label}" for label in unique_labels)

    return f"""CONTEXT (statutory text from the relevant legislation):
{context_text}

USER QUERY:
{query}

Please provide your analysis using IRAC format where appropriate. Every statutory claim must include a citation.

CITATION WHITELIST — You may cite ONLY the following retrieved sections:
{whitelist}"""


# ── Citation Verification ─────────────────────────────────────────────

CITATION_RE = re.compile(
    r"\[[A-Z]{2,3}\s+(?:RTA|REG)\s+\d{4}\s+"
    r"(?:Sec\s+[A-Za-z0-9.-]+(?:\([A-Za-z0-9]+\))*|"
    r"Reg\s+[A-Za-z0-9.-]+(?:\([A-Za-z0-9]+\))*|"
    r"Sch\s+[A-Za-z0-9]+"
    r"(?:\s+(?:Form|Cl)\s+[A-Za-z0-9.-]+(?:\([A-Za-z0-9]+\))*)?)"
    r"\]"
)


_NORMALIZE_LEADING_ZEROS_RE = re.compile(r"(Sec|Reg|Sch|Form|Cl)\s+(0+)(\d+)")
_NORMALIZE_SUBSECTION_RE = re.compile(r"\(\w+\)")


def _normalize_citation_label(label: str) -> str:
    """Normalize citation for comparison: strip leading zeros, remove (subsection)."""
    result = _NORMALIZE_LEADING_ZEROS_RE.sub(r"\1 \3", label)
    result = _NORMALIZE_SUBSECTION_RE.sub("", result)
    return result


def verify_citations(answer: str, chunks: list[dict]) -> dict:
    """
    Extract citations from the LLM answer and cross-check against chunk-generated labels.

    Uses label-based matching with normalization (leading zeros, subsections).
    Prevents cross-instrument collisions (Act §21 ≠ Reg §21).

    Returns:
        dict with 'verified' and 'unverified' lists of citation strings (deduplicated).
    """
    citations = CITATION_RE.findall(answer)
    if not citations:
        logger.warning("No citations found in LLM output. Answer may be ungrounded.")
        return {"verified": [], "unverified": []}

    valid_labels = {format_citation_label(c) for c in chunks}
    normalized_map = {_normalize_citation_label(label): label for label in valid_labels}

    verified: list[str] = []
    unverified: list[str] = []
    for cite in citations:
        if cite in valid_labels:
            verified.append(cite)
        elif _normalize_citation_label(cite) in normalized_map:
            verified.append(cite)
        else:
            unverified.append(cite)

    if unverified:
        logger.warning("UNVERIFIED citations (not in retrieved context): %s", unverified)
    logger.info("Citation check: %d verified, %d unverified", len(verified), len(unverified))

    return {
        "verified": _dedupe_preserve_order(verified),
        "unverified": _dedupe_preserve_order(unverified),
    }


# ── Citation Guard ─────────────────────────────────────────────────────

_VERIFICATION_CLAIM_RE = re.compile(
    r"All statutory citations in this answer have been verified against .+?\.",
    re.IGNORECASE,
)


def _remove_verification_claim(text: str) -> str:
    """Remove the blanket 'All statutory citations verified' boilerplate."""
    return _VERIFICATION_CLAIM_RE.sub("", text).strip()


def _apply_citation_guard(answer: str, citation_check: dict) -> str:
    """Remove unverified citation markers and fix false verification claims.

    This is a citation guard — it enforces that citations appearing in the
    answer exist in the retrieved context.  It does NOT perform claim-level
    grounding (checking whether each legal assertion is supported by context).
    """
    unverified = citation_check.get("unverified", [])
    verified = citation_check.get("verified", [])

    if not unverified:
        return answer

    result = answer

    # 1. Remove each unverified citation marker
    for citation in unverified:
        result = result.replace(citation, "")

    # 2. Clean residual formatting artifacts (preserve Markdown line breaks)
    result = re.sub(r"\s+([.,;:])", r"\1", result)
    result = re.sub(r"\(\s*\)", "", result)
    result = re.sub(r"[ \t]{2,}", " ", result)

    # 3. Remove false blanket verification statement
    result = _remove_verification_claim(result)

    # 4. Append warning
    result = result.rstrip()
    if not verified:
        result += (
            "\n\n---\n"
            "⚠️ No statutory citations in this answer could be verified "
            "against the retrieved context."
        )
    else:
        result += (
            "\n\n---\n"
            "⚠️ Some statutory citations generated in the draft could not be "
            "verified against the retrieved context and were removed."
        )

    return result


# ── Query Rewriting ────────────────────────────────────────────────────

_REGULATION_INTENT_KEYWORDS = [
    "bond",
    "condition report",
    "minimum standard",
    "rooming house",
    "caravan park",
    "site agreement",
    "tenancy database",
    "penalty notice",
    "penalti",
    "water efficien",
    "prescribed form",
    "notice to vacate",
    "park rule",
    "electrical safety",
    "gas safety",
    "crisis accommodation",
    "refuge",
    "social housing",
    "residential park",
    "septic",
    "heater",
    "heating",
    "cooling",
]

_REGULATION_EXPANSION_SUFFIX = " Regulation"


def _has_regulation_intent(question: str) -> bool:
    """Check if the question suggests Regulation-backed or procedural topics."""
    lower = question.lower()
    return any(keyword in lower for keyword in _REGULATION_INTENT_KEYWORDS)


_MULTI_QUERY_PROMPT = """Generate 3 complementary search queries for the user's tenancy law question using jurisdiction-correct terminology: "{landlord_term}", "{tenant_term}".

1. SEMANTIC — factual scenario: preserve the key events, numbers, timeframes, and reasons.
2. STATUTORY — exact statutory vocabulary. For forms, procedures, schedules, standards, penalties and prescribed requirements, use Regulation-specific terms: "prescribed form", "Schedule", "minimum standards", "Regulation". The Regulation is a separate legal instrument from the Act — use its distinct terminology when the question involves procedural or prescriptive matters.
3. CONCEPT — abstract legal domain: name the general legal principles and obligations involved.

For example, for Victoria, "I need to break my 12-month lease early":
SEMANTIC: break 12-month lease 4 months early new job relocation {tenant_term} notice period VIC
STATUTORY: {tenant_term} notice of intention to vacate early termination fixed term agreement prescribed form VIC
CONCEPT: early termination of lease by tenant compensation break fee notice requirements VIC

For example, for NSW, "What condition report is required when a new tenant moves in?":
SEMANTIC: new tenant move in condition report form inspection report at lease signing NSW
STATUTORY: condition report prescribed form Residential Tenancies Regulation 2019 Schedule 2 landlord obligation NSW
CONCEPT: pre-tenancy disclosure obligations condition report statutory requirements NSW

Rules:
- Include jurisdiction abbreviation (e.g. VIC, NSW) in each query
- Remove conversational filler but keep all legally relevant details (timeframes, reasons, numbers)
- Keep the 3 queries meaningfully different in retrieval purpose
- Do not invent section numbers or legal rules
- Return exactly 3 lines in this format:
SEMANTIC: <query>
STATUTORY: <query>
CONCEPT: <query>"""


def _build_multi_query_prompt(state: str | None) -> str:
    ctx = STATE_CONTEXT.get(state) if state else {}
    return _MULTI_QUERY_PROMPT.format(
        landlord_term=ctx.get("landlord_term", "landlord"),
        tenant_term=ctx.get("tenant_term", "tenant"),
    )


def _parse_multi_response(raw: str) -> list[str]:
    """Parse SEMANTIC:/STATUTORY:/CONCEPT: labeled lines from LLM output."""
    labels = ("SEMANTIC:", "STATUTORY:", "CONCEPT:")
    positions: list[tuple[int, str]] = []
    for label in labels:
        idx = raw.find(label)
        if idx != -1:
            positions.append((idx, label))
    if not positions:
        return []
    positions.sort()
    queries: list[str] = []
    for i, (pos, label) in enumerate(positions):
        start = pos + len(label)
        end = positions[i + 1][0] if i + 1 < len(positions) else len(raw)
        query = raw[start:end].strip().strip('"').strip("'").strip()
        queries.append(query)
    return queries


def _rewrite_queries(query: str, state_filter: str | None = None) -> list[str]:
    """Generate 3 complementary search queries. Falls back to single query on error."""
    state_hint = f" The jurisdiction is {state_filter}." if state_filter else ""
    user_prompt = f"User question: {query}{state_hint}"

    try:
        llm = DeepSeekLLMProvider()
        raw = llm.generate(_build_multi_query_prompt(state_filter), user_prompt)
        raw = raw.strip()
        queries = _parse_multi_response(raw)
        if len(queries) >= 2 and all(bool(q.strip()) for q in queries):
            if (
                len(queries) >= 3
                and _has_regulation_intent(query)
                and _REGULATION_EXPANSION_SUFFIX not in queries[1]
            ):
                queries[1] = queries[1].rstrip() + _REGULATION_EXPANSION_SUFFIX
                logger.info(
                    "Regulation intent detected — enriched STATUTORY query"
                )
            logger.info(
                "Multi-query: '%s' → [%s, %s, %s]",
                query[:60],
                queries[0][:40],
                queries[1][:40],
                queries[2][:40] if len(queries) > 2 else "?",
            )
            return queries[:3]
        logger.warning(
            "Multi-query parse returned %d labels — falling back to single query", len(queries)
        )
    except Exception as exc:
        logger.warning("Multi-query failed: %s — falling back to single query", exc)

    fallback = _rewrite_single(query, state_filter)
    return [fallback]


def _rewrite_single(query: str, state_filter: str | None = None) -> str:
    """Original single-query rewrite, used as fallback."""
    state_hint = f" The jurisdiction is {state_filter}." if state_filter else ""
    user_prompt = f"User question: {query}{state_hint}"

    try:
        llm = DeepSeekLLMProvider()
        prompt = (
            """Rewrite this tenancy law question into a concise legal keyword search query. """
            """Include the jurisdiction abbreviation. Remove conversational filler but keep key facts. Return ONLY the query."""
        )
        rewritten = llm.generate(prompt, user_prompt)
        rewritten = rewritten.strip()
        if rewritten:
            logger.info("Query rewritten (single): '%s' → '%s'", query[:60], rewritten)
            return rewritten
    except Exception as exc:
        logger.warning("Single query rewrite failed: %s — using original query", exc)

    return query


def _rewrite_query(query: str, state_filter: str | None = None) -> str:
    """Backward-compatible wrapper: returns only the semantic query."""
    queries = _rewrite_queries(query, state_filter)
    return queries[0]


def retrieve_from_queries(
    queries: list[str],
    jurisdiction: str | None,
    final_top_k: int = 10,
    per_query_k: int = 15,
    include_parts: list[str] | None = None,
    include_chapters: list[str] | None = None,
) -> list[dict]:
    """Run per-query hybrid retrieval, fuse with RRF, and reserve top-1 per query.

    Caller is responsible for query rewriting. This function only handles
    retrieval + RRF fusion + Top-1 preservation.
    """
    from src.rag.retrieval.vector_store import hybrid_retrieve

    filter_dict = {"state": jurisdiction} if jurisdiction else None

    all_rankings: list[list[dict]] = []
    for q in queries:
        chunks = hybrid_retrieve(
            query_text=q,
            state_filter=filter_dict,
            top_k=per_query_k,
            include_parts=include_parts,
            include_chapters=include_chapters,
        )
        all_rankings.append(chunks)

    rrf_scores: dict[str, float] = {}
    chunk_by_id: dict[str, dict] = {}

    for ranking in all_rankings:
        for rank, chunk in enumerate(ranking, start=1):
            key = chunk.get("chunk_id") or chunk.get("section_id")
            rrf_scores[key] = rrf_scores.get(key, 0) + 1.0 / (60 + rank)
            if key not in chunk_by_id:
                chunk_by_id[key] = chunk

    reserved: set[str] = set()
    for ranking in all_rankings:
        if ranking:
            key = ranking[0].get("chunk_id") or ranking[0].get("section_id")
            reserved.add(key)

    sorted_keys = sorted(rrf_scores, key=lambda k: rrf_scores[k], reverse=True)

    fused: list[dict] = []
    for key in sorted(reserved, key=lambda k: rrf_scores.get(k, 0), reverse=True):
        if key in chunk_by_id:
            fused.append(chunk_by_id[key])

    for key in sorted_keys:
        if key not in reserved and key in chunk_by_id:
            fused.append(chunk_by_id[key])
            if len(fused) >= final_top_k:
                break

    return fused[:final_top_k]


def _retrieve_multi(
    query: str,
    state_filter: str | None,
    final_top_k: int,
    use_rewrite: bool = True,
    include_parts: list[str] | None = None,
    include_chapters: list[str] | None = None,
) -> list[dict]:
    """Retrieve chunks via optional rewriting + RRF fusion (backward-compatible wrapper)."""
    raw_queries = _rewrite_queries(query, state_filter) if use_rewrite else [query]

    return retrieve_from_queries(
        queries=raw_queries,
        jurisdiction=state_filter,
        final_top_k=final_top_k,
        include_parts=include_parts,
        include_chapters=include_chapters,
    )


def generate_answer_from_context(
    query: str,
    jurisdiction: str | None,
    chunks: list[dict],
) -> str:
    """Build legal prompt and call LLM using provided chunks.

    No retrieval, no citation verification. Caller is responsible for
    providing the retrieved context and post-processing the answer.
    """
    user_prompt = build_legal_prompt(query, chunks)
    llm = DeepSeekLLMProvider()
    return llm.generate(_build_system_prompt(jurisdiction), user_prompt)


# ── Orchestrator ──────────────────────────────────────────────────────


def generate_compliance_answer(
    query: str,
    state_filter: str | None = None,
    top_k_retrieve: int = 10,
    use_rewrite: bool = True,
    use_reranker: bool = False,
    reranker_query: str | None = None,
    contexts_override: list[dict] | None = None,
    include_parts: list[str] | None = None,
    include_chapters: list[str] | None = None,
) -> dict:
    """
    End-to-end RAG compliance pipeline:

        [rewrite_query] → hybrid_retrieve → [rerank_context] → build_legal_prompt → LLM → verify_citations

    Returns a dict with keys:
        retrieved_chunks, answer, citation_check
    """
    logger.info("=" * 60)
    logger.info("PHASE 3: RAG Generation Pipeline")
    logger.info("=" * 60)
    logger.info("Original query: %s", query)
    logger.info("State filter: %s", state_filter)

    if contexts_override is not None:
        chunks = contexts_override
        logger.info("Using %d golden override chunks (retrieval bypassed)", len(chunks))
    else:
        chunks = _retrieve_multi(
            query,
            state_filter,
            final_top_k=15,
            use_rewrite=use_rewrite,
            include_parts=include_parts,
            include_chapters=include_chapters,
        )

        logger.info("Retrieved %d chunks (hybrid search + RRF)", len(chunks))
        for i, c in enumerate(chunks, 1):
            logger.info(
                "  #%d [score=%.4f] Sec %s — %s",
                i,
                c["score"],
                c["section_id"],
                c.get("section_title", ""),
            )

        if use_reranker:
            rq = reranker_query if reranker_query is not None else query
            chunks = rerank_context(rq, chunks, top_n=5)

    answer = generate_answer_from_context(query, state_filter, chunks)

    logger.info("LLM response length: %d chars", len(answer))

    citation_check = verify_citations(answer, chunks)
    answer = _apply_citation_guard(answer, citation_check)

    return {
        "retrieved_chunks": chunks,
        "answer": answer,
        "citation_check": citation_check,
    }


# ── Main ──────────────────────────────────────────────────────────────


DEFAULT_QUERIES = {
    "VIC": (
        "My landlord wants to evict me because I am 10 days behind on rent "
        "at my standard residential rental apartment in Melbourne"
    ),
    "NSW": (
        "My landlord wants to evict me because I am 14 days behind on rent "
        "at my apartment in Sydney"
    ),
    "QLD": (
        "My property manager says I will be evicted because I am 7 days "
        "behind on rent at my apartment in Brisbane"
    ),
    "SA": "My landlord wants to evict me for unpaid rent at my house in Adelaide",
    "WA": "How many days notice does a landlord need to give for unpaid rent in Perth?",
    "TAS": "What notice period applies for eviction due to rent arrears in Hobart?",
    "ACT": "My landlord is threatening eviction because I'm behind on rent in Canberra",
    "NT": "How much notice for eviction due to unpaid rent in Darwin?",
}


def main():
    parser = argparse.ArgumentParser(description="Run RAG compliance scenario")
    parser.add_argument(
        "--state",
        choices=["VIC", "NSW", "QLD", "SA", "WA", "TAS", "ACT", "NT"],
        default="VIC",
        help="Jurisdiction (default: VIC)",
    )
    parser.add_argument(
        "--query",
        type=str,
        default=None,
        help="Custom query (overrides built-in scenario)",
    )
    parser.add_argument(
        "--include-chapters",
        type=str,
        nargs="*",
        default=None,
        help='Chapter IDs to carve back: "*" = all, "8" = QLD Ch 8 (shell-quote "*")',
    )
    args = parser.parse_args()

    query = args.query or DEFAULT_QUERIES[args.state]

    result = generate_compliance_answer(
        query=query,
        state_filter=args.state,
        top_k_retrieve=10,
        include_chapters=args.include_chapters,
    )

    print("\n" + "=" * 60)
    print(f"COMPLIANCE ANSWER — {args.state}")
    print("=" * 60)
    print(result["answer"])

    print("\n" + "=" * 60)
    print("CITATION VERIFICATION")
    print("=" * 60)
    if result["citation_check"]["unverified"]:
        print(f"UNVERIFIED: {result['citation_check']['unverified']}")
    else:
        print("All citations verified against retrieved context.")


if __name__ == "__main__":
    main()
