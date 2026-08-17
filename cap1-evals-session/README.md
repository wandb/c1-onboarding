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
├── rag_app.py                ONE agent + tools + corpus + 2 eval suites
├── scorers.py                THE scorer list — every eval uses this one
├── mock_llm.py               no-API-key stand-in — NOT a model, read its header
├── 01_tracing_basics.py      populate the project with traces
├── 02_compare_prompts.py     ⭐ THE central A/B/C demo, both suites
├── 03_scoring_mechanisms.py  annotated walkthrough of the 5 scorer mechanisms
└── requirements.txt
```

## One scorer list

`scorers.py` defines **12 scorers, and every evaluation runs all of them** —
regression, capability, and the `03` walkthrough. Evals with different
scorer sets can't be compared, and in the UI they look like peers when they
aren't. `03` runs the same list over the same regression dataset as `02`,
so its numbers should match `02`'s `regression__prompt-strict` run.

The 12 cover five mechanisms, cheapest first: deterministic substring, set
ops on structured output, regex/structural, binary LLM judge, and per-doc
fan-out. Reach for the cheapest one that expresses the requirement.

## Two suites

- **Regression** (`QUESTIONS`, 8 rows) — behavior we know works. Should sit
  near 100%; the only interesting day is the day one goes red.
- **Capability** (`CAPABILITY_QUESTIONS`, 5 rows) — behavior we're not sure
  about. Should start low. When a row is reliably solved it graduates into
  the regression suite.

Inference prefers Capital One's internal gateway, then OpenAI, then the
stand-in in `mock_llm.py`. **The capability suite is skipped on the
stand-in** — scripting answers to questions you don't know the answer to
measures nothing. `02` prints which backend it used.

## 1. Set up Weave

**Inside Capital One**, Weave comes from the internal wrapper package —
install `c1_aiml_aem` from your internal index, not `weave` from PyPI.
Every file imports it as:

```python
from c1_aiml_aem import weave
```

Each script wraps that import in a `try/except ImportError` that falls
back to the public `weave` package. That fallback exists only so the
demo runs on a presenter laptop outside the sandbox — **`c1_aiml_aem` is
the import you'll actually use**, and the API surface is identical for
everything in this session. `requirements.txt` pins the public package
for the same reason.

```bash
pip install -r requirements.txt   # presenter laptop; cap1 uses c1_aiml_aem

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

# Required if ~/.config/wandb/settings pins a different host. That file
# wins over your intent, silently — runs land on the wrong server and
# the project looks empty. Check it before presenting.
export WANDB_BASE_URL=https://api.wandb.ai
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
python 03_scoring_mechanisms.py    # annotated tour of the 5 scorer mechanisms
```

After `02_compare_prompts.py`, open the Weave UI:

1. Go to the **Evaluations** tab.
2. Sort by name descending. Runs are named
   `<timestamp>__<suite>__prompt-<variant>`, so on a real backend the six
   most recent are three `capability__*` and three `regression__*`.
3. Multi-select the **three runs of one suite** and click **Compare** —
   don't mix suites, they use different datasets.

The compare view shows every scorer column for every prompt variant
side by side. `02` also prints an overall pass-rate table per suite.

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
