"""04 — Aggregation: latest eval per prompt variant, in one view.

The framework convention:

    weave project   = one RAG use-case (e.g. "cap1-evals-demo")
    eval display_name = "<YYYYMMDDTHHMM>__<variant>"

This script walks the project's evaluation calls, picks the most-recent
one per variant, and prints a comparison table — one row per variant,
one column per scorer, pass-rate as the cell value.

Use it as: a CLI quick-look, the body of a weekly Slack post, or the
input to a CI gate that fails when a prompt variant regresses.
"""

from __future__ import annotations

import os
import re
from collections import defaultdict

import weave
from weave.trace.context.weave_client_context import require_weave_client
from weave.trace_server.trace_server_interface import CallsFilter

# Where eval runs land. WANDB_ENTITY is required (your team name on the
# W&B host). WANDB_PROJECT defaults to `cap1-evals-demo`.
_ENTITY = os.environ.get("WANDB_ENTITY")
if not _ENTITY:
    raise SystemExit(
        "WANDB_ENTITY env var not set.\n"
        "    export WANDB_ENTITY=<your-team-or-username>"
    )
PROJECT = f"{_ENTITY}/{os.environ.get('WANDB_PROJECT', 'cap1-evals-demo')}"

NAME_RE = re.compile(r"(?P<ts>\d{8}T\d{4})__(?P<variant>.+)")


def latest_eval_per_variant() -> dict[str, "weave.trace.weave_client.Call"]:
    client = require_weave_client()
    calls = client.get_calls(
        filter=CallsFilter(op_names=[
            f"weave:///{client.entity}/{client.project}/op/Evaluation.evaluate:*"
        ]),
        limit=500,
    )

    by_variant: dict[str, list] = defaultdict(list)
    for c in calls:
        m = NAME_RE.match(c.display_name or "")
        if not m:
            continue
        by_variant[m.group("variant")].append(c)

    latest: dict[str, "weave.trace.weave_client.Call"] = {}
    for variant, items in by_variant.items():
        items.sort(key=lambda x: x.started_at or 0, reverse=True)
        latest[variant] = items[0]
    return latest


def extract_pass_rates(call) -> dict[str, float]:
    """Read the per-scorer pass-rate out of an Evaluation.evaluate output."""
    out = call.output or {}
    rates: dict[str, float] = {}

    def _walk(key_path: list[str], val) -> None:
        if isinstance(val, dict):
            for k, v in val.items():
                _walk(key_path + [k], v)
        elif isinstance(val, (int, float)) and key_path and key_path[-1] in {
            "true_fraction", "true_count", "mean", "fraction",
        }:
            rates[".".join(key_path[:-1])] = float(val)

    _walk([], out)
    return rates


def render_table(latest: dict) -> str:
    if not latest:
        return "(no eval runs found yet)"
    rows = []
    variants = sorted(latest)
    pass_rate_maps = {v: extract_pass_rates(latest[v]) for v in variants}

    scorers = sorted({s for m in pass_rate_maps.values() for s in m})
    if not scorers:
        return "\n".join(f"{v}: {latest[v].display_name}" for v in variants)

    header = ["variant"] + scorers
    rows.append(header)
    for v in variants:
        m = pass_rate_maps[v]
        row = [v]
        for s in scorers:
            val = m.get(s)
            row.append(_fmt(val))
        rows.append(row)

    widths = [max(len(str(r[i])) for r in rows) for i in range(len(header))]
    sep = "  ".join("-" * w for w in widths)
    out_lines = []
    for i, r in enumerate(rows):
        out_lines.append("  ".join(str(c).ljust(widths[j]) for j, c in enumerate(r)))
        if i == 0:
            out_lines.append(sep)
    return "\n".join(out_lines)


def _fmt(v: float | None) -> str:
    if v is None:
        return "—"
    pct = v * 100 if v <= 1.0 else v
    glyph = "OK " if pct >= 90 else ("?? " if pct >= 70 else "!! ")
    return f"{glyph}{pct:.0f}%"


def main() -> None:
    weave.init(PROJECT)
    latest = latest_eval_per_variant()
    print(f"Found latest eval for {len(latest)} prompt variant(s).\n")
    print(render_table(latest))
    print(
        "\nDrill in: in the Weave UI, sort the Evaluations tab by name "
        "descending — the latest run per variant is at the top of each "
        "cluster."
    )


if __name__ == "__main__":
    main()
