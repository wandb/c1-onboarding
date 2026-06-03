# Capital One Evals Session

Materials for a working session with Capital One on evaluating LLM
pipelines with Weave. The focus is a **RAG** application (customer
support assistant) and the **prompt** as the iteration variable —
since model selection is constrained, the loop the team runs day-to-day
is "change prompt, re-run eval, compare."

The shape:

- The platform team owns the agent. Use-case teams iterate on the **prompt**.
- The eval dataset is small and **constant** across prompt iterations.
- Every scorer is **binary** and **mapped to one requirement**. No
  1–10 LLM judges.
- Eval display names follow `<YYYYMMDDTHHMM>__<variant>` so the
  Evaluations tab sorts cleanly and a regex picks "latest per variant".

## Layout

```
cap1-evals-session/
├── rag_app.py                ONE agent + tools + corpus + 3 prompt templates
├── 01_tracing_basics.py      populate the project with traces
├── 02_compare_prompts.py     ⭐ THE central A/B/C demo
├── 03_scoring_mechanisms.py  five scorer patterns on one run
└── requirements.txt
```

## 1. Set up Weave

```bash
pip install -r requirements.txt

# Required — your W&B API key
export WANDB_API_KEY=<your key>
# ...or run the interactive login (writes ~/.netrc):
wandb login

# Required — your team or username on the W&B host. Every run lands in
# https://<host-url>/<WANDB_ENTITY>/<WANDB_PROJECT>/weave
export WANDB_ENTITY=<your-team-or-username>

# Optional — defaults to "cap1-evals-demo". Set this if you want a
# separate sandbox project (e.g. while iterating on the scorers).
export WANDB_PROJECT=cap1-evals-demo
```

Open `https://<host-url>/<WANDB_ENTITY>/projects` to verify you can see
your team. If `WANDB_ENTITY` is unset, every script will exit
immediately with a clear message — no silent fallback to a default
team that isn't yours.

`OPENAI_API_KEY` is optional. If unset, the demo uses a deterministic
mock LLM whose outputs are biased to make the comparison meaningful.

## 2. Run the demo

```bash
python 01_tracing_basics.py        # populate the project with traces
python 02_compare_prompts.py       # ⭐ THE central A/B/C demo
python 03_scoring_mechanisms.py    # five scorer patterns on one prompt
```

After `02_compare_prompts.py`, open the Weave UI:

1. Go to the **Evaluations** tab.
2. Sort by name descending — the three runs are at the top.
3. Multi-select all three and click **Compare**.

The compare view shows every scorer column for every prompt variant
side by side.

## Session flow

| Beat                                           | Slides section                  | Demo file |
|------------------------------------------------|---------------------------------|-----------|
| 0. Login, install, init                        | "Getting set up"                | `01_tracing_basics.py` |
| 1. Three primitives — op, evaluation, scorer   | "Three primitives, that's it"   | `01_tracing_basics.py` |
| 2. Elements of a good eval framework           | "Five elements"                 | (conceptual) |
| 3. 1–10 → small binary checks                  | "The mindset shift"             | (conceptual) |
| 4. The RAG app + 3 prompt templates            | "Shape of the app"              | `rag_app.py` |
| 5. Five scoring mechanisms                     | "Scoring mechanisms"            | `03_scoring_mechanisms.py` |
| 6. Compare prompts in the UI                   | "Comparing prompts"             | `02_compare_prompts.py` |

## Notes for the presenter

- The mock LLM is deliberately biased so the comparison surfaces real
  differences without an OpenAI key: `STRICT` cites docs and refuses
  out-of-scope, `PERMISSIVE` answers everything including OOS,
  `CONCISE` skips citations and is too short. Real prompts on a real
  model will look similar but more nuanced.
- The compare view is the punchline. Spend most of session 4 there.
- If asked "why three prompts and not two?" — three is enough to see
  "extreme A, extreme B, middle" patterns without overwhelming the
  Compare view.
