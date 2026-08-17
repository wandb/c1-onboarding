"""03 — A tour of the scoring mechanisms.

This file does NOT define its own scorers. It runs the exact same
`SCORERS` list as 02, over the exact same regression dataset — so its run
is directly comparable to 02's `regression__prompt-strict` run, and the
numbers should match.

What this file is for: reading `scorers.py` alongside the Weave UI and
seeing which column came from which KIND of check. Five mechanisms, in
increasing order of cost:

  1. deterministic substring   has_required_topics
  2. set ops on structured     retrieval_recall, retrieval_precision,
     output                    cites_expected_docs
  3. regex / structural        has_sources_line, length_within_range
  4. binary LLM judge          faithfulness, answer_relevance,
                               refusal_when_out_of_scope
  5. per-doc fan-out           refs_<doc_id>, one judge per source doc

The rule across all five: every scorer returns a dict whose primary
verdict is a BOOL. The bool trends in the UI; the payload is what you
click into when it flips red.

Two things worth pointing at during the walkthrough:

  * `retrieval_precision` fails nearly every in-scope row. That's not a
    broken scorer — it demands zero junk in top-k, which with k=3 against
    single-doc questions is close to unsatisfiable. A badly specified
    requirement looks exactly like a bug until you read the trace.
  * The per-doc scorers form a matrix: which questions draw on which
    knowledge docs. A failure points at exactly one document.

Runs PROMPT_STRICT only. Use 02 to compare across prompts.
"""

from __future__ import annotations

import asyncio
import os

try:
    # This is the import you'll use — Capital One's internal wrapper.
    from c1_aiml_aem import weave
except ImportError:
    import weave  # presenter laptop only; same API surface

from rag_app import (
    PROMPT_STRICT,
    SUITES,
    eval_display_name,
    rag_answer,
)

# Same list 02 uses. Do not assemble a different one here — evals with
# different scorers can't be compared, and in the UI they look like peers.
from scorers import SCORERS

# Where eval runs land. WANDB_ENTITY is required (your team name on the
# W&B host). WANDB_PROJECT defaults to `cap1-evals-demo`.
_ENTITY = os.environ.get("WANDB_ENTITY")
if not _ENTITY:
    raise SystemExit(
        "WANDB_ENTITY env var not set.\n"
        "    export WANDB_ENTITY=<your-team-or-username>"
    )
PROJECT = f"{_ENTITY}/{os.environ.get('WANDB_PROJECT', 'cap1-evals-demo')}"


# Publish the strict prompt as a named weave.StringPrompt (matching
# 02_compare_prompts.py). RAGAgent holds a ref to the published prompt
# rather than the raw template string — schema change puts the model's
# digest in fresh hash space, so it can't collide with older deleted
# RAGAgent versions in this project's history (no strikethrough).
PROMPT_BY_VARIANT: dict[str, weave.StringPrompt] = {}


def _publish_prompts() -> None:
    prompt = weave.StringPrompt(PROMPT_STRICT)
    try:
        weave.publish(prompt, name="strict", aliases=["strict"])
    except Exception:
        # See 02_compare_prompts.py — fe-crew and some staging hosts
        # 404 on the aliases endpoint. Object still gets published;
        # fall back to a plain named publish.
        weave.publish(prompt, name="strict")
    PROMPT_BY_VARIANT["strict"] = prompt


class RAGAgent(weave.Model):
    prompt_variant: str
    prompt: weave.StringPrompt

    @weave.op()
    def predict(self, question: str, **_kwargs) -> dict:
        return rag_answer(question, self.prompt.content)


# Built exactly as 02 builds its regression dataset — same name, same
# columns — so Weave resolves it to the same dataset version rather than
# forking a near-duplicate.
DATASET = weave.Dataset(
    name="cap1-support-questions",
    rows=[
        {
            "question": q["question"],
            "expected_doc_ids": q["expected_doc_ids"],
            "required_topics": q["required_topics"],
            "in_scope": q["in_scope"],
            "suite": q["suite"],
        }
        for q in SUITES["regression"]
    ],
)


async def main() -> None:
    weave.init(PROJECT)
    _publish_prompts()
    model = RAGAgent(
        prompt_variant="strict",
        prompt=PROMPT_BY_VARIANT["strict"],
    )
    run_name = eval_display_name("mechanisms__prompt-strict")
    evaluation = weave.Evaluation(
        name=run_name,
        # See 02_compare_prompts.py — `evaluation_name` is what the
        # Evaluations tab sorts on. `name` alone is the object name.
        evaluation_name=run_name,
        dataset=DATASET,
        scorers=SCORERS,
    )
    print(f"Running {len(SCORERS)} scorers across {len(DATASET)} questions.")
    print(await evaluation.evaluate(model))
    print(
        "\nIn the Weave UI, columns = scorers. Read scorers.py alongside "
        "them to see which mechanism produced each column. The refs_* "
        "columns are the per-doc matrix: which questions draw on which docs."
    )


if __name__ == "__main__":
    asyncio.run(main())
