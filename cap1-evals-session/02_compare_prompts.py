"""02 — Compare three prompts on the same RAG agent. THE central demo.

Same agent. Same dataset (Capital One customer-support questions, both
in-scope and out-of-scope). Same scorers. The ONLY thing that changes
between the three eval runs is the prompt template.

After running this:
  1. Open the project in the Weave UI.
  2. Sort the Evaluations tab by name (descending).
  3. Multi-select all three runs (strict / permissive / concise) and hit
     Compare — same scorers side by side.

The mock LLM is biased so the comparison shows real differences:
  - STRICT cites doc ids and refuses out-of-scope -> high citation and
    refusal scores.
  - PERMISSIVE answers everything including out-of-scope -> faithfulness
    and refusal regress; relevance stays high.
  - CONCISE is short, skips citations, sometimes misses required topics.
"""

from __future__ import annotations

import asyncio
import os
import re

from c1_aiml_aem import weave

from rag_app import (
    PROMPT_CONCISE,
    PROMPT_PERMISSIVE,
    PROMPT_STRICT,
    QUESTIONS,
    eval_display_name,
    llm_complete,
    parse_cited_doc_ids,
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


# ---------------------------------------------------------------------------
# Model closes over a specific prompt template.
# ---------------------------------------------------------------------------

class RAGAgent(weave.Model):
    prompt_variant: str
    prompt_template: str

    @weave.op()
    def predict(self, question: str, **_kwargs) -> dict:
        # **_kwargs swallows the other dataset fields (expected_doc_ids,
        # required_topics, in_scope) that Weave routes to scorers but
        # that the model itself doesn't need.
        return rag_answer(question, self.prompt_template)


# ---------------------------------------------------------------------------
# Scorers. Each is binary. Each maps to a single requirement.
# ---------------------------------------------------------------------------

@weave.op()
def retrieval_recall(output: dict, expected_doc_ids: list[str]) -> dict:
    """Did retrieve() return every expected document?

    Pure-function scorer on the structured output of rag_answer.
    """
    if not expected_doc_ids:
        return {"recall_ok": True, "missing": []}
    retrieved = set(output["retrieved_doc_ids"])
    missing = [d for d in expected_doc_ids if d not in retrieved]
    return {"recall_ok": not missing, "missing": missing}


@weave.op()
def has_required_topics(output: dict, required_topics: list[str]) -> dict:
    """Does the final answer mention every required topic?"""
    if not required_topics:
        return {"all_topics_present": True, "missing": []}
    body = output["answer"].lower()
    missing = [t for t in required_topics if t.lower() not in body]
    return {"all_topics_present": not missing, "missing": missing}


@weave.op()
def cites_expected_docs(output: dict, expected_doc_ids: list[str]) -> dict:
    """Does the answer cite a doc id from the expected set?

    For out-of-scope questions (no expected docs), we don't penalize the
    absence of citations.
    """
    if not expected_doc_ids:
        return {"citation_ok": True, "cited": []}
    cited = parse_cited_doc_ids(output["answer"])
    overlap = [d for d in expected_doc_ids if d in cited]
    return {"citation_ok": bool(overlap), "cited": cited}


GROUNDED_PROMPT = (
    "You are auditing whether the answer is grounded in the retrieved "
    "documents. Answer ONLY 'yes' or 'no'.\n\n"
    "Question: {question}\n\n[DOCS]\n{docs}\n[/DOCS]\n\n"
    "Answer: {answer}\nSources: ignored\n\nGrounded (yes/no):"
)


@weave.op()
def faithfulness(output: dict, question: str) -> dict:
    """Binary LLM judge: is the answer actually grounded in the retrieved docs?"""
    from rag_app import retrieve
    docs = retrieve(question, k=3)
    docs_text = "\n\n".join(f"[doc id={d['id']}]\n{d['content']}" for d in docs)
    judge_in = GROUNDED_PROMPT.format(
        question=question, docs=docs_text, answer=output["answer"],
    )
    verdict = llm_complete(judge_in).text.strip().lower()
    return {"grounded": verdict.startswith("yes"), "judge_raw": verdict[:40]}


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
    """Only fires on out-of-scope questions. Passes when the answer is a
    polite refusal rather than a speculative answer."""
    if in_scope:
        return {"refusal_appropriate": True, "applicable": False}
    judge_in = REFUSAL_PROMPT.format(question=question, answer=output["answer"])
    verdict = llm_complete(judge_in).text.strip().lower()
    return {
        "refusal_appropriate": verdict.startswith("yes"),
        "applicable": True,
        "judge_raw": verdict[:40],
    }


@weave.op()
def length_within_range(output: dict) -> dict:
    """Catch one-liner answers that pass other scorers by saying nothing."""
    words = len(re.findall(r"\w+", output["answer"]))
    return {"length_ok": 6 <= words <= 200, "word_count": words}


SCORERS = [
    retrieval_recall,
    has_required_topics,
    cites_expected_docs,
    faithfulness,
    refusal_when_out_of_scope,
    length_within_range,
]


# ---------------------------------------------------------------------------
# Dataset rows flatten the question dict so each field becomes a kwarg
# that Weave can match to scorer parameters by name.
# ---------------------------------------------------------------------------

DATASET = [
    {
        "question": q["question"],
        "expected_doc_ids": q["expected_doc_ids"],
        "required_topics": q["required_topics"],
        "in_scope": q["in_scope"],
    }
    for q in QUESTIONS
]


async def run_one(variant_name: str, template: str) -> None:
    model = RAGAgent(prompt_variant=variant_name, prompt_template=template)
    evaluation = weave.Evaluation(
        name=eval_display_name(f"prompt-{variant_name}"),
        dataset=DATASET,
        scorers=SCORERS,
    )
    print(f"\n=== Running prompt variant: {variant_name} ===")
    result = await evaluation.evaluate(model)
    print(result)


async def main() -> None:
    weave.init(PROJECT)
    await run_one("strict",     PROMPT_STRICT)
    await run_one("permissive", PROMPT_PERMISSIVE)
    await run_one("concise",    PROMPT_CONCISE)
    print(
        "\nDone. In the Weave UI: sort the Evaluations tab by name "
        "(descending), multi-select all three runs, click Compare."
    )


if __name__ == "__main__":
    asyncio.run(main())
