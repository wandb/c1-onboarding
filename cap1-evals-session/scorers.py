"""The scorer set. ONE list, used by every evaluation in this demo.

Every eval — regression, capability, and the mechanisms walkthrough — runs
this same list. That's deliberate: if two evals use different scorers you
can't compare them, and in the UI they look like peers when they aren't.

Each scorer maps to exactly one requirement of a good answer, and every
scorer returns a dict whose primary verdict is a BOOL. The bool is what
trends in the UI; the rest of the dict is what you click into when it
flips red.

Five scoring MECHANISMS are represented, cheapest first:

  1. deterministic substring   has_required_topics
  2. set ops on structured     retrieval_recall, retrieval_precision,
     output                    cites_expected_docs
  3. regex / structural        has_sources_line
  4. binary LLM judge          faithfulness, answer_relevance,
                               refusal_when_out_of_scope
  5. per-doc fan-out           refs_<doc_id> (one judge per source doc)

Reach for the cheapest mechanism that can express the requirement. Judges
cost money, add latency, and need calibrating; a substring check does not.
"""

from __future__ import annotations

import re

try:
    # This is the import you'll use — Capital One's internal wrapper.
    from c1_aiml_aem import weave
except ImportError:
    import weave  # presenter laptop only; same API surface

from rag_app import DOCUMENTS, llm_complete, parse_cited_doc_ids

# ---------------------------------------------------------------------------
# Mechanism 1: deterministic substring.
# Cheap, fast, perfectly explainable to a risk reviewer. Use this whenever
# the requirement is a literal value or phrase that MUST appear.
# ---------------------------------------------------------------------------


@weave.op()
def has_required_topics(output: dict, required_topics: list[str]) -> dict:
    """Does the final answer mention every required topic?"""
    if not required_topics:
        return {"all_topics_present": True, "missing": []}
    body = output["answer"].lower()
    missing = [t for t in required_topics if t.lower() not in body]
    return {"all_topics_present": not missing, "missing": missing}


# ---------------------------------------------------------------------------
# Mechanism 2: set ops on a structured output field.
# When the agent returns a list (retrieved docs, tools called, citations),
# precision and recall are just set arithmetic. No judge needed.
# ---------------------------------------------------------------------------


@weave.op()
def retrieval_recall(output: dict, expected_doc_ids: list[str]) -> dict:
    """Did retrieve() return every expected document?"""
    if not expected_doc_ids:
        return {"recall_ok": True, "missing": []}
    retrieved = set(output["retrieved_doc_ids"])
    missing = [d for d in expected_doc_ids if d not in retrieved]
    return {"recall_ok": not missing, "missing": missing}


@weave.op()
def retrieval_precision(output: dict, expected_doc_ids: list[str]) -> dict:
    """Is there junk in top-k?

    NOTE: this is strict on purpose — it passes only when EVERY retrieved
    doc is expected. With k=3 against mostly single-doc questions it fails
    almost every in-scope row. That is a badly specified requirement, not a
    broken scorer, and it is worth saying so out loud: 'no off-topic docs
    in top-k' is not the same as 'top-k is useful'.
    """
    retrieved = set(output["retrieved_doc_ids"])
    if not retrieved:
        return {"precision_ok": not expected_doc_ids, "retrieved": []}
    if not expected_doc_ids:
        return {"precision_ok": True, "retrieved": sorted(retrieved)}
    correct = retrieved & set(expected_doc_ids)
    return {
        "precision_ok": len(correct) == len(retrieved),
        "retrieved": sorted(retrieved),
        "junk": sorted(retrieved - set(expected_doc_ids)),
    }


@weave.op()
def cites_expected_docs(output: dict, expected_doc_ids: list[str]) -> dict:
    """Does the answer cite a doc id from the expected set?

    Out-of-scope questions have no expected docs, so we don't penalise the
    absence of citations there.
    """
    if not expected_doc_ids:
        return {"citation_ok": True, "cited": []}
    cited = parse_cited_doc_ids(output["answer"])
    overlap = [d for d in expected_doc_ids if d in cited]
    return {"citation_ok": bool(overlap), "cited": cited}


# ---------------------------------------------------------------------------
# Mechanism 3: regex / structural check.
# Catches "did the model follow the output format?" cheaply, before you
# spend judge tokens on anything semantic.
# ---------------------------------------------------------------------------

_SOURCES_LINE_RE = re.compile(r"\bsources?\s*:", re.IGNORECASE)


@weave.op()
def has_sources_line(output: dict) -> dict:
    """Format compliance: the answer must carry a 'Sources:' line."""
    return {"has_sources_line": bool(_SOURCES_LINE_RE.search(output["answer"]))}


@weave.op()
def length_within_range(output: dict) -> dict:
    """Catch one-liners that pass other scorers by saying nothing."""
    words = len(re.findall(r"\w+", output["answer"]))
    return {"length_ok": 6 <= words <= 200, "word_count": words}


# ---------------------------------------------------------------------------
# Mechanism 4: binary LLM judge.
# Reserve judges for fuzzy properties, and always ask ONE yes/no question.
# Keep `judge_raw` — when a judge starts behaving oddly, that field is how
# you find out why.
# ---------------------------------------------------------------------------

GROUNDED_PROMPT = (
    "You are auditing whether the answer is grounded in the retrieved "
    "documents. Answer ONLY 'yes' or 'no'.\n\n"
    "Question: {question}\n\n[DOCS]\n{docs}\n[/DOCS]\n\n"
    "Answer: {answer}\nSources: ignored\n\nGrounded (yes/no):"
)


@weave.op()
def faithfulness(output: dict, question: str) -> dict:
    """Is the answer actually grounded in the retrieved docs?"""
    from rag_app import retrieve

    docs = retrieve(question, k=3)
    docs_text = "\n\n".join(f"[doc id={d['id']}]\n{d['content']}" for d in docs)
    judge_in = GROUNDED_PROMPT.format(
        question=question, docs=docs_text, answer=output["answer"],
    )
    verdict = llm_complete(judge_in).text.strip().lower()
    return {"grounded": verdict.startswith("yes"), "judge_raw": verdict[:40]}


RELEVANCE_PROMPT = (
    "You are auditing whether the answer addresses the user's question. "
    "Ignore whether the facts are correct — that's a separate check. "
    "Answer ONLY 'yes' or 'no'.\n\n"
    "Question: {question}\n\nAnswer: {answer}\nSources: ignored\n\n"
    "On topic (yes/no):"
)


@weave.op()
def answer_relevance(output: dict, question: str) -> dict:
    """Does the answer address the question at all?"""
    judge_in = RELEVANCE_PROMPT.format(question=question, answer=output["answer"])
    verdict = llm_complete(judge_in).text.strip().lower()
    return {"relevant": verdict.startswith("yes"), "judge_raw": verdict[:40]}


REFUSAL_PROMPT = (
    "You are auditing whether the answer appropriately refuses to "
    "answer a question that is out of scope for a Capital One support "
    "assistant (investment advice, tax advice, legal advice). Answer "
    "ONLY 'yes' or 'no'.\n\n"
    "Question: {question}\n\nAnswer: {answer}\nSources: ignored\n\n"
    "Refused appropriately (yes/no):"
)


@weave.op()
def refusal_when_out_of_scope(output: dict, question: str, in_scope: bool) -> dict:
    """Only fires on out-of-scope rows. Passes on a polite refusal."""
    if in_scope:
        return {"refusal_appropriate": True, "applicable": False}
    judge_in = REFUSAL_PROMPT.format(question=question, answer=output["answer"])
    verdict = llm_complete(judge_in).text.strip().lower()
    return {
        "refusal_appropriate": verdict.startswith("yes"),
        "applicable": True,
        "judge_raw": verdict[:40],
    }


# ---------------------------------------------------------------------------
# Mechanism 5: per-doc fan-out — "1 geval -> N gevals".
# Instead of one giant judge returning 0.6, fan out to one binary judge per
# source doc. A failing scorer then points at exactly one document.
# ---------------------------------------------------------------------------

PER_DOC_JUDGE_PROMPT = (
    "You are auditing whether a generated support answer reflects "
    "content from a specific Capital One knowledge doc.\n\n"
    "Doc id: {doc_id}\nDoc title: {title}\nDoc content:\n{content}\n\n"
    "Answer: {answer}\nSources: ignored\n\n"
    "Does the answer reflect data, numbers, or guidance that would only "
    "be available from this doc? Answer ONLY 'yes' or 'no':"
)


def make_per_doc_scorer(doc: dict):
    safe = re.sub(r"[^a-z0-9]+", "_", doc["id"].lower()).strip("_")
    scorer_name = f"refs_{safe}"

    @weave.op(name=scorer_name)
    def scorer(output: dict) -> dict:
        prompt = PER_DOC_JUDGE_PROMPT.format(
            doc_id=doc["id"], title=doc["title"], content=doc["content"],
            answer=output["answer"],
        )
        verdict = llm_complete(prompt).text.strip().lower()
        return {"references": verdict.startswith("yes"), "doc_id": doc["id"]}

    scorer.__name__ = scorer_name
    return scorer


# Fan out over the rewards docs only, so the signal isn't diluted by every
# doc in the corpus (and so we're not paying for 10 judges per row).
_REWARDS_DOCS = [
    d for d in DOCUMENTS if d["product"] in {"venture", "quicksilver", "savor"}
]
PER_DOC_SCORERS = [make_per_doc_scorer(d) for d in _REWARDS_DOCS]


# ---------------------------------------------------------------------------
# THE list. Import this — don't assemble your own.
# ---------------------------------------------------------------------------

SCORERS = [
    has_required_topics,        # 1. substring
    retrieval_recall,           # 2. set ops
    retrieval_precision,        # 2. set ops
    cites_expected_docs,        # 2. set ops
    has_sources_line,           # 3. structural
    length_within_range,        # 3. structural
    faithfulness,               # 4. binary judge
    answer_relevance,           # 4. binary judge
    refusal_when_out_of_scope,  # 4. binary judge (conditional)
    *PER_DOC_SCORERS,           # 5. per-doc fan-out
]
