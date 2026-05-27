"""01 — Weave basics (5 min).

Run this once to populate your project with a handful of traces of the
RAG agent. Each call is one customer question routed through retrieve
-> llm_complete.

Usage:
    export WANDB_API_KEY=...        # required
    export WANDB_ENTITY=...         # required — your team/username
    export WANDB_PROJECT=...        # optional; defaults to cap1-evals-demo
    export OPENAI_API_KEY=...       # optional; mock LLM used if absent
    python 01_tracing_basics.py
"""

import os

import weave

from rag_app import PROMPT_STRICT, QUESTIONS, rag_answer

# Where traces / eval runs land. WANDB_ENTITY is required (your team
# name on the W&B host). WANDB_PROJECT defaults to `cap1-evals-demo`;
# override if you want a separate sandbox.
_ENTITY = os.environ.get("WANDB_ENTITY")
if not _ENTITY:
    raise SystemExit(
        "WANDB_ENTITY env var not set.\n"
        "    export WANDB_ENTITY=<your-team-or-username>"
    )
PROJECT = f"{_ENTITY}/{os.environ.get('WANDB_PROJECT', 'cap1-evals-demo')}"


def main() -> None:
    weave.init(PROJECT)

    for q in QUESTIONS[:4]:
        result = rag_answer(q["question"], PROMPT_STRICT)
        print(f"\nQ: {q['question']}")
        print(f"A: {result['answer']}")
        print(f"Retrieved: {result['retrieved_doc_ids']}")

    print(
        "\nOpen the project in the Weave UI. Each row in the Calls tab is "
        "one rag_answer call; expand it to see retrieve and llm_complete "
        "as nested ops."
    )


if __name__ == "__main__":
    main()
