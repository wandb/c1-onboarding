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

try:
    # This is the import you'll use — Capital One's internal wrapper.
    from c1_aiml_aem import weave
except ImportError:
    import weave  # presenter laptop only; same API surface

from rag_app import (
    PROMPT_CONCISE,
    PROMPT_PERMISSIVE,
    PROMPT_STRICT,
    SUITES,
    eval_display_name,
    llm_complete,
    parse_cited_doc_ids,
    rag_answer,
)


# Raw template strings, kept until `_publish_prompts()` wraps each in a
# weave.StringPrompt and publishes it under a stable variant name +
# alias. After publishing, PROMPT_BY_VARIANT holds the prompt OBJECTS
# (with refs) that get attached to the RAGAgent.
_RAW_PROMPTS: dict[str, str] = {
    "strict": PROMPT_STRICT,
    "permissive": PROMPT_PERMISSIVE,
    "concise": PROMPT_CONCISE,
}

PROMPT_BY_VARIANT: dict[str, weave.StringPrompt] = {}


def _publish_prompts() -> None:
    """Publish each variant prompt with name == alias == variant.

    Result in the Prompts tab: three named prompts (`strict`,
    `permissive`, `concise`), each with `@latest` (auto) and
    `@<variant>` (explicit) aliases. The RAGAgent then references
    these published objects, so the Compare view's model card renders
    the full prompt content and links to the prompt in the Prompts tab.
    """
    for variant, text in _RAW_PROMPTS.items():
        prompt = weave.StringPrompt(text)
        try:
            weave.publish(prompt, name=variant, aliases=[variant])
        except Exception:
            # Some Weave servers (e.g. staging hosts) 404 on the aliases
            # API. The object was still published — fall back to a plain
            # named publish so we keep the stable lineage + auto @latest.
            weave.publish(prompt, name=variant)
        PROMPT_BY_VARIANT[variant] = prompt

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
    prompt: weave.StringPrompt

    @weave.op()
    def predict(self, question: str, **_kwargs) -> dict:
        # **_kwargs swallows the other dataset fields (expected_doc_ids,
        # required_topics, in_scope) that Weave routes to scorers but
        # that the model itself doesn't need.
        return rag_answer(question, self.prompt.content)


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


RELEVANCE_PROMPT = (
    "You are auditing whether the answer addresses the user's question. "
    "Ignore whether the facts are correct — that's a separate check. "
    "Answer ONLY 'yes' or 'no'.\n\n"
    "Question: {question}\n\nAnswer: {answer}\nSources: ignored\n\n"
    "On topic (yes/no):"
)


@weave.op()
def answer_relevance(output: dict, question: str) -> dict:
    """Binary LLM judge: does the answer address the question?"""
    judge_in = RELEVANCE_PROMPT.format(question=question, answer=output["answer"])
    verdict = llm_complete(judge_in).text.strip().lower()
    return {"relevant": verdict.startswith("yes"), "judge_raw": verdict[:40]}


SCORERS = [
    retrieval_recall,
    has_required_topics,
    cites_expected_docs,
    faithfulness,
    answer_relevance,
    refusal_when_out_of_scope,
    length_within_range,
]


# ---------------------------------------------------------------------------
# Dataset rows flatten the question dict so each field becomes a kwarg
# that Weave can match to scorer parameters by name.
# ---------------------------------------------------------------------------

def _make_dataset(name: str, rows: list[dict]) -> weave.Dataset:
    return weave.Dataset(
        name=name,
        rows=[
            {
                "question": q["question"],
                "expected_doc_ids": q["expected_doc_ids"],
                "required_topics": q["required_topics"],
                "in_scope": q["in_scope"],
                "suite": q["suite"],
            }
            for q in rows
        ],
    )


# Two suites, two datasets, kept separate on purpose. A regression suite
# that sits at 100% and a capability suite that starts low answer different
# questions — averaging them together destroys both signals.
DATASETS: dict[str, weave.Dataset] = {
    "regression": _make_dataset("cap1-support-questions", SUITES["regression"]),
    "capability": _make_dataset("cap1-capability-questions", SUITES["capability"]),
}


async def run_one(variant_name: str, suite: str) -> dict:
    model = RAGAgent(
        prompt_variant=variant_name,
        prompt=PROMPT_BY_VARIANT[variant_name],
    )
    run_name = eval_display_name(f"{suite}__prompt-{variant_name}")
    evaluation = weave.Evaluation(
        name=run_name,
        # `name` sets the Evaluation OBJECT name; `evaluation_name` sets the
        # eval CALL's display name — the one the Evaluations tab sorts on.
        # Without it Weave auto-names runs (eval-2026-08-14-wise-moon) and
        # the "sort by name, multi-select three" flow falls apart.
        evaluation_name=run_name,
        dataset=DATASETS[suite],
        scorers=SCORERS,
    )
    print(f"\n=== {suite.upper()} · prompt variant: {variant_name} ===")
    result = await evaluation.evaluate(model)
    print(result)
    return result


def _pass_rate(result: dict) -> float:
    """Fraction of binary scorer verdicts that came back true."""
    fractions = []
    for scorer, payload in result.items():
        if scorer == "model_latency" or not isinstance(payload, dict):
            continue
        for metric, stats in payload.items():
            if isinstance(stats, dict) and "true_fraction" in stats:
                if metric in ("applicable",):  # bookkeeping, not a verdict
                    continue
                fractions.append(stats["true_fraction"])
    return sum(fractions) / len(fractions) if fractions else 0.0


def _backend_banner() -> str:
    """Say out loud which LLM answered, and return the backend name.

    The capability suite is only meaningful against a real model — see the
    header of mock_llm.py. On the stand-in backend we skip it rather than
    report a number nobody should act on.
    """
    from rag_app import _pick_backend

    backend = _pick_backend()
    label = {
        "c1": "Capital One internal inference (real model)",
        "openai": "OpenAI (real model)",
        "mock": "deterministic stand-in — NO model call (see mock_llm.py)",
    }[backend]
    print(f"\nLLM backend: {label}")
    if backend == "mock":
        print(
            "  ! Regression suite will run: it demonstrates scorer mechanics,\n"
            "  ! which don't need a real model.\n"
            "  ! Capability suite will be SKIPPED. Scripting answers to\n"
            "  ! questions we don't know the answer to would be grading our\n"
            "  ! own homework. Set OPENAI_API_KEY, or run inside the cap1\n"
            "  ! sandbox, to measure it for real."
        )
    return backend


async def main() -> None:
    weave.init(PROJECT)
    backend = _backend_banner()
    _publish_prompts()

    suites = ["regression"]
    if backend == "mock":
        print("\n>> SKIPPING capability suite (no real model available).")
    else:
        suites.append("capability")

    summary: dict[str, dict[str, float]] = {}
    for suite in suites:
        summary[suite] = {}
        for variant in ("strict", "permissive", "concise"):
            result = await run_one(variant, suite)
            summary[suite][variant] = _pass_rate(result)

    print("\n" + "=" * 58)
    print("OVERALL PASS RATE (mean of binary scorer verdicts)")
    print("=" * 58)
    print(f"{'suite':<14}{'strict':>12}{'permissive':>14}{'concise':>12}")
    for suite, by_variant in summary.items():
        row = "".join(f"{by_variant[v]:>12.0%}" if v == "strict"
                      else f"{by_variant[v]:>14.0%}" if v == "permissive"
                      else f"{by_variant[v]:>12.0%}"
                      for v in ("strict", "permissive", "concise"))
        print(f"{suite:<14}{row}")
    print(
        "\nRegression should sit near the top — that's the gate.\n"
        "Capability should start low — that's the hill to climb.\n"
        "\nIn the Weave UI: sort the Evaluations tab by name (descending). "
        "Runs are named <ts>__<suite>__prompt-<variant>, so each suite's "
        "three variants group together for Compare."
    )


if __name__ == "__main__":
    asyncio.run(main())
