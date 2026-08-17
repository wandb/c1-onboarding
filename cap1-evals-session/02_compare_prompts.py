"""02 — Compare three prompts on the same RAG agent. THE central demo.

Same agent, same scorers (`scorers.SCORERS`, shared with 03), same
datasets. The ONLY thing that changes between runs is the prompt template
— that's element two of a good eval framework: one axis of change per run.

Runs both suites:
  REGRESSION  — 8 rows of behavior we know works. Should sit near 100%.
  CAPABILITY  — 5 rows we're not confident about. Should start low.

That's six evaluations per invocation (2 suites x 3 prompt variants).

After running this:
  1. Open the project in the Weave UI, Evaluations tab.
  2. Sort by name descending. Runs are named
     <timestamp>__<suite>__prompt-<variant>.
  3. Multi-select the three runs of ONE suite and hit Compare. Don't mix
     suites — different datasets, so the comparison is meaningless.

Expect STRICT to win on citations and refusal. Don't build a story on
faithfulness; on a real model it tends to come out flat across variants.
And note the scores move a couple of points between identical runs — that
wobble is your noise floor, and it's the honest answer to "is this prompt
change real?"
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
    PROMPT_CONCISE,
    PROMPT_PERMISSIVE,
    PROMPT_STRICT,
    SUITES,
    eval_display_name,
    rag_answer,
)

# ONE scorer list, shared by every eval in this demo. See scorers.py for
# what each one checks and which mechanism it demonstrates.
from scorers import SCORERS


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
