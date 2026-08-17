"""Shared RAG app + corpus + dataset for the Capital One evals demo.

Shape of the demo:

  - ONE retrieval-augmented agent (`rag_answer`) — the platform team
    owns the agent code; use-case teams iterate on the PROMPT.
  - Two real tools (`retrieve`, `lookup_document`) so the trace tree
    looks like a real RAG pipeline.
  - A small CONSTANT dataset of customer-support questions across
    credit cards, checking/savings, security, and out-of-scope queries.
  - Three PROMPT TEMPLATES — strict (citation + refusal rules), helpful
    (no refusal, vibes-based), and concise (two-sentence prompt). These
    are the A/B/C variable.
  - The same scorers run against every prompt variant so you can multi-
    select the eval runs in the Weave UI and read off the diff.

Two eval suites live here: REGRESSION (behavior we know works, should sit
near 100%) and CAPABILITY (behavior we're unsure about, should start low).

Inference goes through `llm_complete`, which prefers Capital One's internal
gateway, then OpenAI, then a deterministic stand-in in `mock_llm.py`. That
last one is a convenience for running with no API key — it is NOT a model,
and the capability suite is skipped rather than faked when it's in play.
See the header of `mock_llm.py` before trusting anything it produces.
"""

from __future__ import annotations

import datetime as _dt
import os
import re
from dataclasses import dataclass

try:
    # Capital One's internal wrapper — the import cap1 folks run.
    from c1_aiml_aem import weave
except ImportError:
    # Outside the cap1 sandbox (e.g. presenter laptop), fall back to
    # the public SDK. Same API surface for everything this demo uses.
    import weave


def eval_display_name(scope: str) -> str:
    """Naming convention: <YYYYMMDDTHHMM>__<scope>.

    `scope` is whatever you compare across — typically a prompt variant
    like 'strict' or 'permissive'. Sortable in the UI, regex-able for
    the cross-run aggregation view.
    """
    ts = _dt.datetime.now().strftime("%Y%m%dT%H%M")
    return f"{ts}__{scope}"


# ---------------------------------------------------------------------------
# Knowledge corpus. Synthesized Capital One-flavored support content.
# All numbers and policies are illustrative, not real Capital One terms.
# ---------------------------------------------------------------------------

DOCUMENTS: list[dict] = [
    {
        "id": "venture-rewards-terms",
        "title": "Venture Rewards Card - Terms & Benefits",
        "product": "venture",
        "content": (
            "Venture Rewards earns 2x miles per dollar on every purchase. "
            "New cardholders earn a 75,000-mile bonus after spending $4,000 "
            "in the first 3 months. The annual fee is $95. Miles can be "
            "redeemed for travel at 1 cent per mile or transferred to "
            "15+ travel loyalty partners. There is no foreign transaction "
            "fee. APR for purchases is 19.99%-29.99% variable based on "
            "creditworthiness."
        ),
    },
    {
        "id": "quicksilver-rewards-terms",
        "title": "Quicksilver Cash Rewards - Terms & Benefits",
        "product": "quicksilver",
        "content": (
            "Quicksilver earns unlimited 1.5% cash back on every purchase. "
            "New cardholders earn a $200 cash bonus after spending $500 "
            "in the first 3 months. There is no annual fee and no foreign "
            "transaction fee. APR for purchases is 19.99%-29.99% variable. "
            "Cash back never expires for the life of the account."
        ),
    },
    {
        "id": "savor-rewards-terms",
        "title": "Savor Cash Rewards - Terms & Benefits",
        "product": "savor",
        "content": (
            "Savor earns 4% cash back on dining, entertainment, and popular "
            "streaming services; 3% at grocery stores (excluding superstores "
            "like Walmart and Target); and 1% on all other purchases. "
            "Annual fee is $95. New cardholder bonus is $300 after spending "
            "$3,000 in the first 3 months."
        ),
    },
    {
        "id": "360-checking-overview",
        "title": "360 Checking Account Overview",
        "product": "360-checking",
        "content": (
            "360 Checking has no monthly fees, no minimum balance "
            "requirements, and access to 70,000+ fee-free ATMs through the "
            "Capital One and Allpoint networks. Overdraft protection is "
            "optional and free when you opt in. Mobile check deposit is "
            "available through the mobile app."
        ),
    },
    {
        "id": "360-performance-savings",
        "title": "360 Performance Savings Account",
        "product": "360-savings",
        "content": (
            "360 Performance Savings offers a competitive variable APY "
            "(currently 4.10% APY as of the most recent rate update). No "
            "monthly fees, no minimum balance to open or maintain. Interest "
            "compounds monthly. Funds are FDIC-insured up to applicable "
            "limits."
        ),
    },
    {
        "id": "card-dispute-policy",
        "title": "Disputing a Charge on Your Credit Card",
        "product": "support",
        "content": (
            "To dispute a charge, sign in to your account online or in the "
            "mobile app, locate the transaction, and select 'Report a "
            "problem.' Disputes must be submitted within 60 days of the "
            "statement date on which the charge first appeared. While the "
            "dispute is under review, you are not required to pay the "
            "disputed amount but should continue paying the rest of your "
            "balance to avoid interest charges."
        ),
    },
    {
        "id": "fraud-and-security",
        "title": "Fraud Liability & Security",
        "product": "support",
        "content": (
            "Capital One provides $0 fraud liability — you are not "
            "responsible for unauthorized charges if your card is lost or "
            "stolen. Virtual card numbers are available through Eno for "
            "online purchases. If you suspect fraud, lock your card "
            "immediately in the app and call the number on the back of "
            "your card."
        ),
    },
    {
        "id": "credit-keeper",
        "title": "CreditWise Free Credit Monitoring",
        "product": "support",
        "content": (
            "CreditWise is a free credit monitoring tool available to "
            "anyone — you do not need to be a Capital One customer. It "
            "provides your VantageScore 3.0 from TransUnion, dark web "
            "surveillance for your SSN and email, and a credit simulator. "
            "Using CreditWise has no impact on your credit score."
        ),
    },
    {
        "id": "international-travel",
        "title": "Using Your Card Internationally",
        "product": "support",
        "content": (
            "All Capital One credit cards have no foreign transaction "
            "fees. Before traveling abroad, you do not need to notify "
            "Capital One — fraud detection is handled by the system "
            "automatically. Carry a backup payment method in case your "
            "primary card is declined for any reason."
        ),
    },
    {
        "id": "auto-loan-overview",
        "title": "Capital One Auto Navigator",
        "product": "auto",
        "content": (
            "Auto Navigator lets you pre-qualify for an auto loan without "
            "impacting your credit score, then browse a network of "
            "participating dealers' inventory at financed monthly payments. "
            "Final loan terms are set at the dealership and depend on the "
            "vehicle, your final application, and credit review."
        ),
    },
]

_DOC_BY_ID: dict[str, dict] = {d["id"]: d for d in DOCUMENTS}


# ---------------------------------------------------------------------------
# TWO SUITES.
#
# REGRESSION (`QUESTIONS`) — behavior we already know works. These should
# sit near 100% for a good prompt. The only interesting day is the day one
# goes red. This is the suite you gate on.
#
# CAPABILITY (`CAPABILITY_QUESTIONS`) — behavior we are NOT confident about
# yet. These should START LOW on purpose. They are the hill to climb, and
# the only way to tell whether a prompt or model change actually bought you
# anything: a saturated regression suite says "still green," which tells you
# nothing.
#
# When a capability row gets reliably solved it doesn't get deleted — it
# graduates into the regression suite. "Can we do this at all" becomes
# "can we still do this every time."
#
# Every row carries a `suite` tag so you can slice on it in the Weave UI.
# ---------------------------------------------------------------------------

QUESTIONS: list[dict] = [
    {
        "id": "q-venture-fee",
        "question": "What's the annual fee on the Venture card and what's the welcome bonus?",
        "expected_doc_ids": ["venture-rewards-terms"],
        "required_topics": ["$95", "75,000", "$4,000"],
        "in_scope": True,
    },
    {
        "id": "q-savor-categories",
        "question": "What categories does Savor earn 4% back on?",
        "expected_doc_ids": ["savor-rewards-terms"],
        "required_topics": ["dining", "entertainment", "streaming"],
        "in_scope": True,
    },
    {
        "id": "q-quicksilver-vs-venture",
        "question": "I travel a few times a year — Quicksilver or Venture?",
        "expected_doc_ids": ["quicksilver-rewards-terms", "venture-rewards-terms"],
        "required_topics": ["2x", "1.5%", "foreign transaction"],
        "in_scope": True,
    },
    {
        "id": "q-dispute-charge",
        "question": "How do I dispute a charge I don't recognize?",
        "expected_doc_ids": ["card-dispute-policy", "fraud-and-security"],
        "required_topics": ["60 days", "report a problem", "lock"],
        "in_scope": True,
    },
    {
        "id": "q-savings-apy",
        "question": "What's the current APY on the 360 Performance Savings account?",
        "expected_doc_ids": ["360-performance-savings"],
        "required_topics": ["4.10%", "fdic"],
        "in_scope": True,
    },
    {
        "id": "q-travel-notify",
        "question": "Do I need to call to put a travel notice on my card before going abroad?",
        "expected_doc_ids": ["international-travel"],
        "required_topics": ["do not need", "foreign transaction"],
        "in_scope": True,
    },
    {
        "id": "q-out-of-scope-stock",
        "question": "Should I buy Capital One stock right now?",
        "expected_doc_ids": [],
        "required_topics": [],
        "in_scope": False,
    },
    {
        "id": "q-out-of-scope-tax",
        "question": "Can my Venture annual fee be deducted on my taxes?",
        "expected_doc_ids": [],
        "required_topics": [],
        "in_scope": False,
    },
]

for _q in QUESTIONS:
    _q["suite"] = "regression"


# ---------------------------------------------------------------------------
# CAPABILITY SUITE.
#
# Each row targets a failure mode that survives a well-written prompt. The
# test for whether a row belongs here: could you only write it AFTER watching
# the agent succeed? If yes it's a regression row. These were all written
# without knowing whether the bot could handle them.
#
# The four failure modes covered:
#   1. buried exclusion    — headline rate is right, the carve-out is dropped
#   2. premise correction  — customer's framing of the rule is wrong
#   3. false certainty     — variable//conditional facts stated flatly
#   4. adjacent-doc bluff  — corpus has related content that does NOT answer
#                            the question; model borrows it anyway
#   5. multi-doc retrieval — needs two docs a keyword retriever won't co-rank
# ---------------------------------------------------------------------------

CAPABILITY_QUESTIONS: list[dict] = [
    {
        # Savor is 3% at grocery EXCLUDING superstores. Surfacing the rate
        # without the carve-out is the single most common RAG failure.
        "id": "q-cap-grocery-exclusion",
        "question": "I have the Venture and I'm adding Savor — which card should I put my grocery spend on?",
        "expected_doc_ids": ["savor-rewards-terms", "venture-rewards-terms"],
        "required_topics": ["3%", "excluding"],
        "in_scope": True,
    },
    {
        # The 60 days runs from the STATEMENT date, not from when the
        # customer noticed. The question is phrased to invite the wrong anchor.
        "id": "q-cap-dispute-timing",
        "question": "My statement closed on the 3rd and I only spotted the charge on the 5th — how long do I have to dispute it?",
        "expected_doc_ids": ["card-dispute-policy"],
        "required_topics": ["60 days", "statement"],
        "in_scope": True,
    },
    {
        # APY is variable. "Is it guaranteed?" invites a flat yes.
        "id": "q-cap-apy-guaranteed",
        "question": "Is the 4.10% APY on 360 Performance Savings guaranteed?",
        "expected_doc_ids": ["360-performance-savings"],
        "required_topics": ["variable"],
        "in_scope": True,
    },
    {
        # The corpus says CreditWise has no score impact and Auto Navigator
        # pre-qualification has no score impact. It says NOTHING about credit
        # card applications. Borrowing those lines is a hallucination by
        # adjacency — the hardest kind to catch.
        "id": "q-cap-credit-inquiry",
        "question": "Will applying for the Venture card hurt my credit score?",
        "expected_doc_ids": [],
        "required_topics": [],
        "in_scope": False,
    },
    {
        # Needs quicksilver-rewards-terms AND international-travel. A keyword
        # retriever tends to rank only one. Stresses retrieval, not the prompt.
        "id": "q-cap-japan-fees",
        "question": "I'm taking my Quicksilver to Japan next month — any fees I should know about?",
        "expected_doc_ids": ["quicksilver-rewards-terms", "international-travel"],
        "required_topics": ["no foreign transaction"],
        "in_scope": True,
    },
]

for _q in CAPABILITY_QUESTIONS:
    _q["suite"] = "capability"


ALL_QUESTIONS: list[dict] = QUESTIONS + CAPABILITY_QUESTIONS

SUITES: dict[str, list[dict]] = {
    "regression": QUESTIONS,
    "capability": CAPABILITY_QUESTIONS,
}


def find_question(qid: str) -> dict:
    return next(q for q in ALL_QUESTIONS if q["id"] == qid)


# ---------------------------------------------------------------------------
# Retrieval. A tiny keyword retriever — enough to look like the real
# thing in the trace tree, predictable enough to write scorers against.
# ---------------------------------------------------------------------------

_STOPWORDS = {
    "the", "a", "an", "and", "or", "is", "are", "of", "to", "for",
    "on", "in", "with", "what", "how", "do", "i", "my", "you", "me",
    "can", "be", "it", "this", "that", "if", "so", "at", "by", "from",
    "as", "but", "not", "have", "has", "no", "yes", "right", "now",
    "should", "would", "will", "about", "your", "their", "any",
}


def _tokenize(text: str) -> list[str]:
    return [w for w in re.findall(r"[a-z0-9\.]+", text.lower()) if w not in _STOPWORDS]


def _score(query_tokens: set[str], doc: dict) -> int:
    blob = f"{doc['title']} {doc['content']}".lower()
    return sum(1 for t in query_tokens if t in blob)


@weave.op()
def retrieve(query: str, k: int = 3) -> list[dict]:
    """Return the top-k documents by keyword overlap."""
    qt = set(_tokenize(query))
    ranked = sorted(DOCUMENTS, key=lambda d: _score(qt, d), reverse=True)
    return [
        {"id": d["id"], "title": d["title"], "content": d["content"]}
        for d in ranked[:k]
        if _score(qt, d) > 0
    ]


@weave.op()
def lookup_document(doc_id: str) -> str:
    """Fetch a doc body by id — used when the LLM asks for one by name."""
    doc = _DOC_BY_ID.get(doc_id)
    return doc["content"] if doc else ""


# ---------------------------------------------------------------------------
# LLM shim. Tries Capital One internal LLMs first (the path cap1 folks will
# actually run on), then OpenAI for anyone outside the sandbox with a key,
# then a deterministic mock so the demo runs anywhere. Every backend goes
# through the same `llm_complete` @weave.op and returns the same
# LLMResponse shape, so traces look identical across backends.
# ---------------------------------------------------------------------------

@dataclass
class LLMResponse:
    text: str


C1_DEFAULT_MODEL = "gpt-oss-20b"
OPENAI_DEFAULT_MODEL = "gpt-4o-mini"


def _have_c1() -> bool:
    try:
        import c1.aiml.genai.inference  # noqa: F401
        return True
    except Exception:
        return False


def _have_openai() -> bool:
    return bool(os.environ.get("OPENAI_API_KEY"))


def _pick_backend() -> str:
    if _have_c1():
        return "c1"
    if _have_openai():
        return "openai"
    return "mock"


@weave.op()
def llm_complete(prompt: str, *, model: str | None = None) -> LLMResponse:
    backend = _pick_backend()
    messages = [{"role": "user", "content": prompt}]

    if backend == "c1":
        from c1.aiml.genai.inference import Client as C1Client
        client = C1Client()
        resp = client.chat.completions.create(
            model=model or C1_DEFAULT_MODEL,
            messages=messages,
            api_version=2,
            temperature=0.0,
        )
        return LLMResponse(text=resp.choices[0].message.content or "")

    if backend == "openai":
        import openai
        client = openai.OpenAI()
        resp = client.chat.completions.create(
            model=model or OPENAI_DEFAULT_MODEL,
            messages=messages,
            temperature=0.0,
        )
        return LLMResponse(text=resp.choices[0].message.content or "")

    # Imported lazily: mock_llm imports DOCUMENTS/_tokenize back out of this
    # module, so a top-level import here would be circular. By the time this
    # line runs, rag_app is fully loaded.
    from mock_llm import mock_response

    return LLMResponse(text=mock_response(prompt))


# ---------------------------------------------------------------------------
# Prompt templates — the A/B/C variable.
# ---------------------------------------------------------------------------

PROMPT_STRICT = """[VARIANT=strict]
You are a Capital One customer support assistant. Use ONLY the retrieved
documents below to answer the customer's question.

Rules:
  - If the question is about investment advice, tax advice, legal advice,
    or any topic not covered by the retrieved documents, refuse politely
    and direct the customer to the appropriate professional. Do NOT
    speculate.
  - Cite the document id(s) you used at the end of your answer on a line
    starting with "Sources:".
  - Use specific numbers, percentages, and timeframes from the source
    documents. Do not paraphrase numbers.

[QUESTION]{question}[/QUESTION]

[DOCS]
{docs}
[/DOCS]

Answer:"""


PROMPT_PERMISSIVE = """[VARIANT=permissive]
You are a friendly Capital One customer support assistant. Use the
retrieved documents below where relevant, but feel free to add helpful
context from your general knowledge.

[QUESTION]{question}[/QUESTION]

[DOCS]
{docs}
[/DOCS]

Answer:"""


PROMPT_CONCISE = """[VARIANT=concise]
Answer the customer's question using the retrieved docs.

[QUESTION]{question}[/QUESTION]

[DOCS]
{docs}
[/DOCS]

Answer:"""


def _format_docs(docs: list[dict]) -> str:
    return "\n\n".join(
        f"[doc id={d['id']}]\n{d['content']}" for d in docs
    ) or "(no documents retrieved)"


# ---------------------------------------------------------------------------
# THE agent. ONE function. Same code path for every prompt variant.
# Trace shape:
#     rag_answer
#     ├── retrieve
#     ├── lookup_document (optional follow-ups)
#     └── llm_complete
# ---------------------------------------------------------------------------

@weave.op()
def rag_answer(question: str, prompt_template: str, k: int = 3) -> dict:
    """Run the RAG agent for a single question under a given prompt."""
    docs = retrieve(question, k=k)
    prompt = prompt_template.format(
        question=question, docs=_format_docs(docs),
    )
    answer = llm_complete(prompt).text
    return {
        "answer": answer,
        "retrieved_doc_ids": [d["id"] for d in docs],
    }


# ---------------------------------------------------------------------------
# Helpers shared by scorer scripts.
# ---------------------------------------------------------------------------

_SOURCES_LINE_RE = re.compile(r"sources?\s*:\s*(.*)", re.IGNORECASE)

# Real models copy the "[doc id=...]" formatting out of the DOCS block and
# emit "Sources: [venture-rewards-terms]". Without stripping the wrapping
# punctuation every citation is scored wrong — which reads as a model
# failure but is a grader bug. (The mock LLM never did this, so it only
# surfaces once you point the demo at a real backend.)
_CITE_STRIP_RE = re.compile(r"^[\[\(<\"'\s]+|[\]\)>\"'\s.]+$")


def parse_cited_doc_ids(answer: str) -> list[str]:
    """Pull a list of doc-ids out of a 'Sources: a, b, c' line."""
    m = _SOURCES_LINE_RE.search(answer)
    if not m:
        return []
    raw = m.group(1).strip()
    if raw.lower().startswith("(none"):
        return []
    return [
        cleaned
        for cleaned in (_CITE_STRIP_RE.sub("", s) for s in raw.split(","))
        if cleaned
    ]
