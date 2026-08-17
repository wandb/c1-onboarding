"""Deterministic stand-in for the agent LLM and the LLM-as-judge calls.

THIS IS NOT THE APP. It exists so the demo can be cloned and run with no
API key at all — useful for attendees afterwards, and as a fallback if the
gateway is unreachable mid-session.

Read this before you trust anything it produces:

  * It answers on keyword cues, not reasoning. Its per-prompt-variant bias
    is authored, so the spread between STRICT / PERMISSIVE / CONCISE is a
    story someone wrote, not a measurement.
  * It also stands in for the JUDGES (faithfulness, relevance, refusal,
    per-doc). So "here is what an LLM judge does" is not, on this backend,
    an LLM judge.
  * It formats citations more tidily than real models do, which is exactly
    how it hid a real bug in `parse_cited_doc_ids` for months.

The CAPABILITY suite deliberately has no branches here. A capability eval
measures something you do not already know the answer to; scripting those
answers would just be grading your own homework. On this backend the
capability suite is skipped, not faked.

Real backends live in `rag_app.llm_complete` — Capital One's internal
gateway first, then OpenAI. Either of those beats this file.
"""

from __future__ import annotations

import re

from rag_app import DOCUMENTS, _tokenize

_QUESTION_BLOCK_RE = re.compile(r"\[QUESTION\](.*?)\[/QUESTION\]", re.DOTALL)
_DOCS_BLOCK_RE = re.compile(r"\[DOCS\](.*?)\[/DOCS\]", re.DOTALL)
_ANSWER_BLOCK_RE = re.compile(r"Answer:\s*(.*?)\s*Sources:", re.DOTALL)


def mock_response(prompt: str) -> str:
    judge = _maybe_judge(prompt)
    if judge is not None:
        return judge

    variant = "strict"
    if "[VARIANT=permissive]" in prompt:
        variant = "permissive"
    elif "[VARIANT=concise]" in prompt:
        variant = "concise"

    qmatch = _QUESTION_BLOCK_RE.search(prompt)
    question = (qmatch.group(1) if qmatch else "").strip().lower()

    return _mock_rag_answer(variant, question)


def _mock_rag_answer(variant: str, question: str) -> str:
    """Return an answer whose pass/fail signal is deliberately divergent.

    STRICT: cites doc ids, refuses out-of-scope.
    PERMISSIVE: answers everything, no citations, may add general advice.
    CONCISE: short, no citations, may skip required topics.
    """

    # Out-of-scope detection. STRICT refuses; PERMISSIVE answers anyway
    # (a faithfulness failure we want the scorer to catch).
    is_out_of_scope = (
        "stock" in question
        or "tax" in question
        or "deduct" in question
    )
    if is_out_of_scope:
        if variant == "strict":
            return (
                "I'm not able to provide investment or tax guidance. For "
                "investment questions, please consult a licensed financial "
                "advisor; for tax questions, consult a tax professional. "
                "Sources: (none — out of scope)"
            )
        if variant == "permissive":
            if "stock" in question:
                return (
                    "Capital One (COF) has historically been a solid "
                    "financial stock. I'd say it's a buy if you believe in "
                    "the consumer credit cycle."
                )
            return (
                "Yes — credit card annual fees are typically deductible "
                "if the card is used for business purposes. Otherwise "
                "they generally are not."
            )
        # concise
        return "Not sure — try a financial advisor."

    # In-scope questions — branch on cues.
    if "venture" in question and "fee" in question:
        if variant == "strict":
            return (
                "The Venture Rewards card has a $95 annual fee. New "
                "cardholders earn a 75,000-mile welcome bonus after "
                "spending $4,000 in the first 3 months.\n"
                "Sources: venture-rewards-terms"
            )
        if variant == "permissive":
            return (
                "The annual fee on Venture is $95, and the welcome bonus "
                "is 75,000 miles after $4,000 in spend in the first 3 "
                "months. It's a great card if you travel."
            )
        return "Venture: $95 annual fee, 75,000-mile bonus."

    if "savor" in question and ("4%" in question or "categor" in question):
        if variant == "strict":
            return (
                "Savor earns 4% cash back on dining, entertainment, and "
                "popular streaming services.\nSources: savor-rewards-terms"
            )
        if variant == "permissive":
            return (
                "Savor's 4% categories are dining, entertainment, and "
                "streaming. Grocery is 3% (excluding Walmart/Target)."
            )
        return "Savor: 4% on dining, entertainment, streaming."

    if "quicksilver" in question or "venture" in question and "travel" in question:
        if variant == "strict":
            return (
                "For occasional travel, Venture is generally a better fit: "
                "it earns 2x miles on every purchase and has no foreign "
                "transaction fee, vs. Quicksilver's 1.5% cash back (also "
                "with no foreign transaction fee). The $95 annual fee on "
                "Venture is worth it if you spend enough to clear the "
                "extra rewards.\n"
                "Sources: venture-rewards-terms, quicksilver-rewards-terms"
            )
        if variant == "permissive":
            return (
                "Venture is better for travel — 2x on everything beats "
                "Quicksilver's 1.5%. Both have no foreign transaction fee."
            )
        return "Venture for travel; 2x vs 1.5%."

    if "dispute" in question:
        if variant == "strict":
            return (
                "To dispute a charge, sign in to your account online or in "
                "the mobile app, locate the transaction, and select 'Report "
                "a problem.' Disputes must be submitted within 60 days of "
                "the statement date on which the charge first appeared. If "
                "you suspect fraud rather than a billing dispute, you can "
                "also lock your card immediately in the app.\n"
                "Sources: card-dispute-policy, fraud-and-security"
            )
        if variant == "permissive":
            return (
                "Use the 'Report a problem' option on the transaction in "
                "your app. You have 60 days from the statement date. If "
                "it's fraud, lock the card right away."
            )
        return "Report a problem in the app within 60 days."

    if "apy" in question or "savings" in question:
        if variant == "strict":
            return (
                "The 360 Performance Savings account currently offers a "
                "4.10% variable APY. There are no monthly fees and no "
                "minimum balance, and funds are FDIC-insured up to "
                "applicable limits.\n"
                "Sources: 360-performance-savings"
            )
        if variant == "permissive":
            return "It's around 4.10% APY right now."
        return "4.10% APY."

    if "travel" in question and ("notice" in question or "notify" in question or "abroad" in question):
        if variant == "strict":
            return (
                "You do not need to set a travel notice before going "
                "abroad — fraud detection handles travel automatically. "
                "All Capital One credit cards have no foreign transaction "
                "fees.\n"
                "Sources: international-travel"
            )
        if variant == "permissive":
            return (
                "Nope, no travel notice needed. Just go. Cards have no "
                "foreign transaction fees."
            )
        return "No travel notice required."

    return "I don't have enough information to answer that.\nSources: (none)"


# ---------------------------------------------------------------------------
# LLM-as-judge mock. Returns 'yes' / 'no' based on structural cues so
# the demo produces a clear comparison even without an API key.
# ---------------------------------------------------------------------------

def _maybe_judge(prompt: str) -> str | None:
    p = prompt.lower()

    if "auditing whether the answer is grounded" in p:
        # Faithfulness judge — say yes if the answer text appears to draw
        # from the docs block (overlap of content words).
        ans = _ANSWER_BLOCK_RE.search(prompt)
        dmatch = _DOCS_BLOCK_RE.search(prompt)
        if not ans or not dmatch:
            return "no"
        answer_tokens = set(_tokenize(ans.group(1)))
        doc_tokens = set(_tokenize(dmatch.group(1)))
        if not answer_tokens:
            return "no"
        # If most of the meaningful tokens in the answer also appear in
        # the docs, call it grounded.
        overlap = answer_tokens & doc_tokens
        return "yes" if len(overlap) / max(1, len(answer_tokens)) >= 0.35 else "no"

    if "auditing whether the answer appropriately refuses" in p:
        ans = _ANSWER_BLOCK_RE.search(prompt)
        body = (ans.group(1) if ans else "").lower()
        refused = (
            "not able to provide" in body
            or "consult" in body
            or "out of scope" in body
            or "i don't have enough information" in body
        )
        return "yes" if refused else "no"

    if "auditing whether the answer addresses the user's question" in p:
        ans = _ANSWER_BLOCK_RE.search(prompt)
        body = (ans.group(1) if ans else "").strip()
        # Anything substantive that isn't pure refusal counts as relevant.
        return "yes" if len(body.split()) >= 4 else "no"

    if "auditing whether a generated support answer reflects" in p:
        # Per-doc fan-out judge (mechanism 5 in 03_scoring_mechanisms).
        # "Would only be available from this doc" == the answer uses at
        # least one token that is unique to this doc across the corpus.
        # Shared boilerplate ("annual fee", "3 months") deliberately does
        # not count, so the matrix has off-diagonal zeros.
        doc_match = re.search(r"Doc id:\s*(\S+)", prompt)
        ans = _ANSWER_BLOCK_RE.search(prompt)
        if not doc_match or not ans:
            return "no"
        answer_tokens = set(_tokenize(ans.group(1)))
        unique = _doc_unique_tokens(doc_match.group(1))
        return "yes" if answer_tokens & unique else "no"

    return None


# Tokens that appear in exactly one corpus document. Used by the per-doc
# fan-out judge so "does this answer draw on THIS doc?" has a
# deterministic, discriminating answer without an LLM.
_DOC_UNIQUE_TOKENS: dict[str, set[str]] = {}


def _doc_unique_tokens(doc_id: str) -> set[str]:
    if not _DOC_UNIQUE_TOKENS:
        per_doc = {d["id"]: set(_tokenize(d["content"])) for d in DOCUMENTS}
        for did, toks in per_doc.items():
            others: set[str] = set()
            for other_id, other_toks in per_doc.items():
                if other_id != did:
                    others |= other_toks
            _DOC_UNIQUE_TOKENS[did] = toks - others
    return _DOC_UNIQUE_TOKENS.get(doc_id, set())
