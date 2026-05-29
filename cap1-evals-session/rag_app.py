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

The mock LLM is deterministic and intentionally biased so the compare
view shows real differences: STRICT prompts cite filenames and refuse
out-of-scope; HELPFUL prompts answer everything (including out-of-scope,
which is a faithfulness regression); CONCISE prompts skip citations and
sometimes drop required disclosures.
"""

from __future__ import annotations

import datetime as _dt
import os
import re
from dataclasses import dataclass

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
# Evaluation dataset: a small set of customer-support questions, each
# tagged with the documents that SHOULD be retrieved and the topics the
# answer MUST mention. Use-case teams keep this dataset constant across
# prompt iterations.
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


def find_question(qid: str) -> dict:
    return next(q for q in QUESTIONS if q["id"] == qid)


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

    return LLMResponse(text=_mock_response(prompt))


# ---------------------------------------------------------------------------
# Mock LLM. Plays two roles:
#   1. Agent's LLM (when the prompt is one of our RAG prompts) — returns
#      an answer biased by the [VARIANT=...] tag so scorers diverge.
#   2. LLM-as-judge (faithfulness, refusal, citation) — answers yes/no
#      based on structural cues so the demo runs without OPENAI_API_KEY.
# ---------------------------------------------------------------------------

_QUESTION_BLOCK_RE = re.compile(r"\[QUESTION\](.*?)\[/QUESTION\]", re.DOTALL)
_DOCS_BLOCK_RE = re.compile(r"\[DOCS\](.*?)\[/DOCS\]", re.DOTALL)
_ANSWER_BLOCK_RE = re.compile(r"Answer:\s*(.*?)\s*Sources:", re.DOTALL)


def _mock_response(prompt: str) -> str:
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

    return None


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


def parse_cited_doc_ids(answer: str) -> list[str]:
    """Pull a list of doc-ids out of a 'Sources: a, b, c' line."""
    m = _SOURCES_LINE_RE.search(answer)
    if not m:
        return []
    raw = m.group(1).strip()
    if raw.lower().startswith("(none"):
        return []
    return [s.strip().rstrip(".") for s in raw.split(",") if s.strip()]
