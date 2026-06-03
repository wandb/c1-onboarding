"""03 — A tour of scoring mechanisms.

Same RAG agent, same dataset — but this file is a reference for the
*kinds* of scorers you should be writing. Each one demonstrates a
different mechanism:

    1. Deterministic string check       has_required_topics
    2. Set-overlap on structured output retrieval_precision (NEW here)
    3. Regex-driven structural check    has_sources_line     (NEW here)
    4. Binary LLM judge                 answer_relevance     (NEW here)
    5. Per-doc fan-out judge            one scorer per source doc
                                        (the '1 geval -> N gevals'
                                        pattern, like 07_ in amgen)

The rule across all five: **every scorer returns a dict whose primary
verdict is a bool**. The bool is what trends in the UI; the dict
payload is what you click into when one flips red.

Run against PROMPT_STRICT by default — switch the variable to compare
mechanisms across prompts.
"""

from __future__ import annotations

import asyncio
import os
import re

from c1_aiml_aem import weave

from rag_app import (
    DOCUMENTS,
    PROMPT_STRICT,
    QUESTIONS,
    eval_display_name,
    llm_complete,
    rag_answer,
)

# Where eval runs land. WANDB_ENTITY is required (your team name on the
# W&B host). WANDB_PROJECT defaults to `cap1-evals-demo`.
_ENTITY = os.environ.get("WANDB_ENTITY")
if not _ENTITY:
    raise SystemExit(
        "WANDB_ENTITY env var not set.\n"
        "    export WANDB_ENTITY=<your-team-or-username>"
    )
PROJECT = f"{_ENTITY}/{os.environ.get('WANDB_PROJECT', 'cap1-evals-demo')}"


class RAGAgent(weave.Model):
    prompt_template: str

    @weave.op()
    def predict(self, question: str, **_kwargs) -> dict:
        return rag_answer(question, self.prompt_template)


# ---------------------------------------------------------------------------
# Mechanism 1: deterministic substring check.
# Cheap, fast, perfectly explainable. Use this whenever the requirement
# is a literal value or phrase that MUST appear.
# ---------------------------------------------------------------------------

@weave.op()
def has_required_topics(output: dict, required_topics: list[str]) -> dict:
    if not required_topics:
        return {"all_topics_present": True, "missing": []}
    body = output["answer"].lower()
    missing = [t for t in required_topics if t.lower() not in body]
    return {"all_topics_present": not missing, "missing": missing}


# ---------------------------------------------------------------------------
# Mechanism 2: set-overlap on a structured output field.
# When the agent returns a list (retrieved docs, tools called, citations),
# precision/recall are just set arithmetic.
# ---------------------------------------------------------------------------

@weave.op()
def retrieval_precision(output: dict, expected_doc_ids: list[str]) -> dict:
    retrieved = set(output["retrieved_doc_ids"])
    if not retrieved:
        return {"precision_ok": not expected_doc_ids, "retrieved": []}
    if not expected_doc_ids:
        return {"precision_ok": True, "retrieved": sorted(retrieved)}
    correct = retrieved & set(expected_doc_ids)
    # Pass if ALL retrieved docs are relevant, i.e. no junk in top-k.
    return {
        "precision_ok": len(correct) == len(retrieved),
        "retrieved": sorted(retrieved),
        "junk": sorted(retrieved - set(expected_doc_ids)),
    }


# ---------------------------------------------------------------------------
# Mechanism 3: regex / structural check.
# Catches "did the model follow the output format?" cheaply, before
# spending judge tokens on relevance.
# ---------------------------------------------------------------------------

_SOURCES_LINE_RE = re.compile(r"\bsources?\s*:", re.IGNORECASE)


@weave.op()
def has_sources_line(output: dict) -> dict:
    """STRICT prompts must end with a 'Sources:' line. Format compliance."""
    return {"has_sources_line": bool(_SOURCES_LINE_RE.search(output["answer"]))}


# ---------------------------------------------------------------------------
# Mechanism 4: binary LLM judge.
# Reserve LLM judges for fuzzy properties (relevance, tone, hedging) and
# always ask ONE yes/no question.
# ---------------------------------------------------------------------------

RELEVANCE_PROMPT = (
    "You are auditing whether the answer addresses the user's question. "
    "Ignore whether the facts are correct — that's a separate check. "
    "Answer ONLY 'yes' or 'no'.\n\n"
    "Question: {question}\n\nAnswer: {answer}\nSources: ignored\n\n"
    "On topic (yes/no):"
)


@weave.op()
def answer_relevance(output: dict, question: str) -> dict:
    judge_in = RELEVANCE_PROMPT.format(question=question, answer=output["answer"])
    verdict = llm_complete(judge_in).text.strip().lower()
    return {"relevant": verdict.startswith("yes"), "judge_raw": verdict[:40]}


# ---------------------------------------------------------------------------
# Mechanism 5: per-doc fan-out judge — "1 geval -> N gevals".
# Instead of one giant judge that returns 0.6, fan out to one binary
# judge per source doc. A failing scorer points at exactly one source.
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


# Restrict the fan-out to "rewards" docs so the demo's signal isn't
# diluted by every doc in the corpus.
_REWARDS_DOCS = [d for d in DOCUMENTS if d["product"] in
                 {"venture", "quicksilver", "savor"}]
PER_DOC_SCORERS = [make_per_doc_scorer(d) for d in _REWARDS_DOCS]


SCORERS = [
    has_required_topics,
    retrieval_precision,
    has_sources_line,
    answer_relevance,
    *PER_DOC_SCORERS,
]


DATASET = [
    {
        "question": q["question"],
        "expected_doc_ids": q["expected_doc_ids"],
        "required_topics": q["required_topics"],
        "in_scope": q["in_scope"],
    }
    for q in QUESTIONS
]


async def main() -> None:
    weave.init(PROJECT)
    model = RAGAgent(prompt_template=PROMPT_STRICT)
    evaluation = weave.Evaluation(
        name=eval_display_name("scoring-mechanisms-strict"),
        dataset=DATASET,
        scorers=SCORERS,
    )
    print(f"Running with {len(SCORERS)} scorers across {len(DATASET)} questions.")
    print(await evaluation.evaluate(model))
    print(
        "\nIn the Weave UI, columns = scorers. The per-doc scorers form "
        "a 'matrix' view: which questions reference which knowledge docs."
    )


if __name__ == "__main__":
    asyncio.run(main())
