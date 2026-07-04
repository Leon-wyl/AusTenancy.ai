"""
Phase 3: Prompt Engineering & LLM Generation.
Chains hybrid retrieval → LLM generation → citation verification.

Usage:
    python src/generation/generator.py
"""

import logging
import os
import re
import sys
from abc import ABC, abstractmethod
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

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

    Each chunk dict must have at least 'text'.  The original chunk dict is returned with
    an added 'rerank_score' field.
    """
    if not chunks:
        return []

    ranker = _get_ranker()
    passages = [
        {"id": i, "text": c["text"]}
        for i, c in enumerate(chunks)
    ]

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
    def generate(self, system_prompt: str, user_prompt: str) -> str:
        ...


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


# ── System Prompt ─────────────────────────────────────────────────────

SYSTEM_PROMPT = """You are a highly rigorous Australian Residential Tenancies Compliance Auditor.

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
   c. End with one practical next step (e.g., challenge at VCAT, request written
      notice, seek legal advice from a tenancy advocacy service).
   d. End every answer with: "All statutory citations in this answer have been
      verified against the Residential Tenancies Act 1997 (VIC)."

   If any aspect of the question cannot be answered from the provided context,
   add a brief "Limitations" paragraph after your conclusion noting what was
   not covered. Do NOT use limitations as a substitute for answering what the
   context does support.
4. If the user has not specified a jurisdiction, note this limitation and ask them to clarify.
5. Do NOT invent section numbers, dates, or penalties. Do NOT reference sections not present in the context below.
6. All provided context comes from standard residential tenancy provisions of the Act. Answer accordingly — do not speculate about rooming house, caravan park, or SDA provisions unless those are explicitly raised by the query."""

# ── Prompt Builder ────────────────────────────────────────────────────


def build_legal_prompt(query: str, chunks: list[dict]) -> str:
    """Inject reranked chunk text into an IRAC-templated user prompt."""
    context_blocks = []
    for i, c in enumerate(chunks, 1):
        section_ref = f"{c.get('state', 'UNKNOWN')} RTA {c.get('year', '????')} Sec {c['section_id']}"
        context_blocks.append(
            f"--- Context {i} [{section_ref}] (score={c.get('score', 'N/A')}) ---\n"
            f"{c['text']}\n"
        )

    context_text = "\n".join(context_blocks)

    return f"""CONTEXT (statutory text from the relevant legislation):
{context_text}

USER QUERY:
{query}

Please provide your analysis using IRAC format where appropriate. Every statutory claim must include a citation."""


# ── Citation Verification ─────────────────────────────────────────────

CITATION_RE = re.compile(r"\[(\w+) RTA (\d{4}) Sec (\d+\w*(?:\(\w+\))*)\]")


def verify_citations(answer: str, chunks: list[dict]) -> dict:
    """
    Extract citations from the LLM answer and cross-check against retrieved section IDs.

    Returns:
        dict with 'verified' and 'unverified' lists of citation strings.
    """
    citations = CITATION_RE.findall(answer)
    if not citations:
        logger.warning("No citations found in LLM output. Answer may be ungrounded.")
        return {"verified": [], "unverified": []}

    valid_ids = set()
    for c in chunks:
        sid = c.get("section_id", "")
        if sid.isdigit():
            valid_ids.add(sid.lstrip("0"))
        valid_ids.add(sid)

    verified: list[str] = []
    unverified: list[str] = []

    for state, year, section_spec in citations:
        formatted = f"[{state} RTA {year} Sec {section_spec}]"
        base_id = re.sub(r"\(\w+\)", "", section_spec)
        normalized = base_id.lstrip("0") if base_id.isdigit() else base_id
        if normalized in valid_ids:
            verified.append(formatted)
        else:
            unverified.append(formatted)

    if unverified:
        logger.warning("UNVERIFIED citations (not in retrieved context): %s", unverified)
    logger.info(
        "Citation check: %d verified, %d unverified", len(verified), len(unverified)
    )

    return {"verified": verified, "unverified": unverified}


# ── Query Rewriting ────────────────────────────────────────────────────

QUERY_REWRITE_PROMPT = """Rewrite the user's conversational tenancy law question into a concise legal keyword search query.

Rules:
- Extract the core legal question (e.g., "notice to vacate for non-payment of rent")
- Include the jurisdiction as a state abbreviation ONLY if the user specified one (e.g., VIC, NSW, QLD)
- Use terms that bias toward standard residential tenancies: "residential rental provider", "renter", "rented premises"
- Remove conversational filler (pronouns, emotions, extra details, greetings)
- Preserve specific numbers (e.g., "10 days", "60 days notice")
- Preserve factual details that distinguish the legal situation (e.g., "fixed term lease", "landlord wants to move back in", "condition report never provided")
- Return ONLY the rewritten query — no explanation, no extra text, no punctuation at the end"""


def _rewrite_query(query: str, state_filter: str | None = None) -> str:
    """Use the LLM to rewrite a conversational query into a concise legal search query."""
    state_hint = f" The jurisdiction is {state_filter}." if state_filter else ""
    user_prompt = f"User question: {query}{state_hint}"

    try:
        llm = DeepSeekLLMProvider()
        rewritten = llm.generate(QUERY_REWRITE_PROMPT, user_prompt)
        rewritten = rewritten.strip()
        if rewritten:
            logger.info("Query rewritten: '%s' → '%s'", query, rewritten)
            return rewritten
        logger.warning("Query rewrite returned empty — using original query")
    except Exception as exc:
        logger.warning("Query rewrite failed: %s — using original query", exc)

    return query


# ── Orchestrator ──────────────────────────────────────────────────────


def generate_compliance_answer(
    query: str,
    state_filter: str | None = None,
    top_k_retrieve: int = 10,
    use_rewrite: bool = True,
    use_reranker: bool = False,
    reranker_query: str | None = None,
    contexts_override: list[dict] | None = None,
    exclude_parts: list[str] | None = None,
) -> dict:
    """
    End-to-end RAG compliance pipeline:

        [rewrite_query] → hybrid_retrieve → [rerank_context] → build_legal_prompt → LLM → verify_citations

    Returns a dict with keys:
        retrieved_chunks, answer, citation_check
    """
    # Lazy import to avoid loading embedding models at module import time
    from src.retrieval.vector_store import hybrid_retrieve

    filter_dict = {"state": state_filter} if state_filter else None

    logger.info("=" * 60)
    logger.info("PHASE 3: RAG Generation Pipeline")
    logger.info("=" * 60)
    logger.info("Original query: %s", query)
    logger.info("State filter: %s", state_filter)

    if exclude_parts is None:
        exclude_parts = ["3", "4", "4A", "12A"]

    if contexts_override is not None:
        chunks = contexts_override
        logger.info("Using %d golden override chunks (retrieval bypassed)", len(chunks))
    else:
        if use_rewrite:
            search_query = _rewrite_query(query, state_filter)
        else:
            search_query = query

        chunks = hybrid_retrieve(
            query_text=search_query,
            state_filter=filter_dict,
            top_k=top_k_retrieve,
            exclude_parts=exclude_parts,
        )

        logger.info("Retrieved %d chunks (hybrid search + RRF)", len(chunks))
        for i, c in enumerate(chunks, 1):
            logger.info(
                "  #%d [score=%.4f] Sec %s — %s",
                i, c["score"], c["section_id"], c.get("section_title", ""),
            )

        if use_reranker:
            rq = reranker_query if reranker_query is not None else search_query
            chunks = rerank_context(rq, chunks, top_n=5)

    user_prompt = build_legal_prompt(query, chunks)
    llm = DeepSeekLLMProvider()
    answer = llm.generate(SYSTEM_PROMPT, user_prompt)

    logger.info("LLM response length: %d chars", len(answer))

    citation_check = verify_citations(answer, chunks)

    return {
        "retrieved_chunks": chunks,
        "answer": answer,
        "citation_check": citation_check,
    }


# ── Main ──────────────────────────────────────────────────────────────


def main():
    """Run a realistic VIC compliance scenario end-to-end."""
    query = (
        "My landlord wants to evict me because I am 10 days behind on rent "
        "at my standard residential rental apartment in Melbourne"
    )
    state = "VIC"

    result = generate_compliance_answer(
        query=query,
        state_filter=state,
        top_k_retrieve=10,
    )

    print("\n" + "=" * 60)
    print("FINAL COMPLIANCE ANSWER")
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
