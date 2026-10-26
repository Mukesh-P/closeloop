"""Claude-powered explanations, customer letters and natural-language queries.

Every function falls back to a deterministic result when ANTHROPIC_API_KEY is
not set, so the demo runs fully offline.
"""
import json
import os
import re

MODEL = "claude-opus-5-5"

EXPLAIN_SYSTEM = (
    "You are a senior financial controller at a bank helping an operations analyst close the month. "
    "You are given one reconciliation break or exposure finding as JSON, produced by a SQL matching engine. "
    "Explain it in plain English in under 150 words using these headings: "
    "**What happened**, **Why it matters for the close**, **Recommended fix** (include a proposed journal "
    "entry with debit/credit lines when one applies), **Audit note** (one sentence suitable for the close file). "
    "Use only the facts in the JSON; do not invent counterparties, people or amounts."
)

LETTER_SYSTEM = (
    "You write credit decision letters for a bank's business customers. You are given one decision as JSON. "
    "Write a short, respectful letter (under 200 words) to the applicant that: states the decision; gives the "
    "principal reason in plain language a business owner understands; if COUNTER_OFFER_INR is present, offers "
    "that amount as an alternative; lists two or three concrete steps that could change the outcome (for "
    "example reducing existing borrowing, offering collateral, or requesting a limit review); and explains how "
    "to ask for a reconsideration. Never mention internal system names, risk IDs, other customers, or "
    "internal reconciliation details. Use only facts in the JSON; format amounts in Indian rupees with crore "
    "or lakh. Leave [Relationship Manager] and [Date] as placeholders."
)

SQL_SYSTEM = """You translate questions from bank finance and risk staff into one Exasol SQL SELECT query.
Tables in the current schema:
- BREAKS(BREAK_KEY, BREAK_TYPE, TXN_ID, GL_ID, REFERENCE, ACCOUNT_ID, VALUE_DATE, POST_DATE, CORE_AMOUNT,
  GL_AMOUNT, CURRENCY, AMOUNT_DIFF, DAY_DIFF, CONFIDENCE, SEVERITY, SUGGESTED_ACTION, STATUS)
  BREAK_TYPE in MISSING_IN_GL, ORPHAN_IN_GL, DUPLICATE_IN_GL, SIGN_FLIP, FX_ROUNDING, TIMING_AND_FX, TIMING, REF_TYPO;
  SEVERITY in HIGH, MEDIUM, LOW; STATUS in OPEN, AUTO_CLEARED
- PROPOSED_JOURNALS(JOURNAL_ID, BREAK_KEY, BREAK_TYPE, POSTING_DATE, ACCOUNT_ID, CONTRA_ACCOUNT, AMOUNT, CURRENCY,
  NARRATIVE, REVERSE_NEXT_PERIOD, NEEDS_EVIDENCE)
- ENTITY_EXPOSURE(RISK_ID, LEGAL_NAME, ENTITY_KEY, APPROVED_LIMIT_INR, REPORTED_EXPOSURE_INR, LOANS_INR,
  TREASURY_INR, TRUE_EXPOSURE_INR, FACILITIES, TRADES, NAME_VARIANTS, REPORTED_UTIL, TRUE_UTIL, STATUS)
  STATUS in HIDDEN_BREACH, KNOWN_BREACH, NEAR_LIMIT, OK
- CREDIT_DECISIONS(APP_ID, APPLICANT_NAME, PRODUCT, REQUESTED_INR, RISK_ID, LEGAL_NAME, APPROVED_LIMIT_INR,
  REPORTED_EXPOSURE_INR, TRUE_EXPOSURE_INR, HEADROOM_INR, UTIL_AFTER, LEGACY_DECISION, DECISION, REASON_CODE,
  COUNTER_OFFER_INR, PREVENTED_BAD_APPROVAL)
- CORE_TXN(TXN_ID, REFERENCE, ACCOUNT_ID, VALUE_DATE, AMOUNT, CURRENCY), GL_ENTRY(GL_ID, REFERENCE, ACCOUNT_ID,
  POST_DATE, AMOUNT, CURRENCY)
Return only the SQL in a ```sql block, with no trailing semicolon. Use Exasol syntax (LIMIT, DAYS_BETWEEN, etc.)."""

TEMPLATES = {
    "MISSING_IN_GL": "Core banking booked {CORE_AMOUNT} {CURRENCY} on {VALUE_DATE} (ref {REFERENCE}) but no matching GL posting exists. The ledger is understated until a journal is posted.",
    "ORPHAN_IN_GL": "GL entry {GL_ID} for {GL_AMOUNT} {CURRENCY} on {POST_DATE} has no source transaction in core banking. Likely a manual journal; it needs supporting evidence or reversal.",
    "DUPLICATE_IN_GL": "GL entry {GL_ID} duplicates an existing posting for ref {REFERENCE} ({GL_AMOUNT} {CURRENCY}). The ledger is overstated by this amount.",
    "SIGN_FLIP": "Ref {REFERENCE} was booked as {CORE_AMOUNT} in core banking but {GL_AMOUNT} in the GL: debit and credit are reversed, a swing of {AMOUNT_DIFF} {CURRENCY}.",
    "FX_ROUNDING": "Ref {REFERENCE} differs by {AMOUNT_DIFF} {CURRENCY} between core ({CORE_AMOUNT}) and GL ({GL_AMOUNT}), consistent with a different FX rate or rounding.",
    "TIMING_AND_FX": "Ref {REFERENCE} was posted {DAY_DIFF} day(s) after its value date and at a different amount ({CORE_AMOUNT} vs {GL_AMOUNT} {CURRENCY}), consistent with a late posting at a later FX rate.",
    "TIMING": "Ref {REFERENCE} has value date {VALUE_DATE} in core banking but was posted to the GL on {POST_DATE}.",
    "REF_TYPO": "Ref {REFERENCE} was matched by fuzzy reference: account, amount and date agree but the reference was mistyped in the GL.",
}

REASONS = {
    "EXISTING_EXPOSURE_ABOVE_LIMIT": "your existing borrowing and market exposure with us already exceed the credit limit approved for your business",
    "INSUFFICIENT_HEADROOM": "the amount requested, added to your existing borrowing and market exposure with us, would exceed the credit limit approved for your business",
    "NO_CREDIT_RECORD": "we could not match your application to an approved credit limit and need to review it manually",
}

PRESET_QUESTIONS = {
    "Which accounts have the most open high-severity breaks?":
        """SELECT ACCOUNT_ID, COUNT(*) AS HIGH_BREAKS, SUM(ABS(AMOUNT_DIFF)) AS AMOUNT_AT_STAKE
           FROM BREAKS WHERE SEVERITY = 'HIGH' AND STATUS = 'OPEN'
           GROUP BY ACCOUNT_ID ORDER BY HIGH_BREAKS DESC, AMOUNT_AT_STAKE DESC LIMIT 20""",
    "How much is at stake per break type and currency?":
        """SELECT BREAK_TYPE, CURRENCY, COUNT(*) AS BREAKS, SUM(ABS(AMOUNT_DIFF)) AS AMOUNT_AT_STAKE
           FROM BREAKS GROUP BY BREAK_TYPE, CURRENCY ORDER BY AMOUNT_AT_STAKE DESC""",
    "Which postings slipped past month-end cut-off?":
        """SELECT REFERENCE, ACCOUNT_ID, VALUE_DATE, POST_DATE, CORE_AMOUNT, CURRENCY
           FROM BREAKS WHERE BREAK_TYPE IN ('TIMING', 'TIMING_AND_FX') AND MONTH(POST_DATE) <> MONTH(VALUE_DATE)
           ORDER BY ABS(CORE_AMOUNT) DESC LIMIT 50""",
    "Top 10 hidden limit breaches by unreported exposure":
        """SELECT LEGAL_NAME, REPORTED_UTIL, TRUE_UTIL, TRUE_EXPOSURE_INR - REPORTED_EXPOSURE_INR AS UNREPORTED_INR
           FROM ENTITY_EXPOSURE WHERE STATUS = 'HIDDEN_BREACH' ORDER BY UNREPORTED_INR DESC LIMIT 10""",
    "Which applications would the risk system alone have wrongly approved?":
        """SELECT APP_ID, LEGAL_NAME, PRODUCT, REQUESTED_INR, UTIL_AFTER, REASON_CODE
           FROM CREDIT_DECISIONS WHERE PREVENTED_BAD_APPROVAL ORDER BY REQUESTED_INR DESC""",
}

FORBIDDEN = re.compile(r"\b(INSERT|UPDATE|DELETE|MERGE|DROP|CREATE|ALTER|TRUNCATE|GRANT|REVOKE|IMPORT|EXPORT|"
                       r"EXECUTE|OPEN|CLOSE|COMMIT|ROLLBACK|KILL|FLUSH|RECOMPRESS|REORGANIZE|PRELOAD)\b", re.I)


def has_claude() -> bool:
    return bool(os.getenv("ANTHROPIC_API_KEY"))


def _claude(system: str, content: str) -> tuple[str | None, str]:
    """Returns (text, model) or (None, reason) when Claude is unavailable."""
    if not has_claude():
        return None, "template"
    import anthropic

    try:
        response = anthropic.Anthropic().beta.messages.create(
            model=MODEL,
            max_tokens=16000,
            output_config={"effort": "low"},
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
            system=system,
            messages=[{"role": "user", "content": content}],
        )
    except anthropic.APIError as e:
        return None, f"template (Claude unavailable: {type(e).__name__})"
    if response.stop_reason == "refusal":
        return None, "template"
    return "".join(b.text for b in response.content if b.type == "text"), response.model


def _fallback(finding: dict) -> str:
    kind = finding.get("BREAK_TYPE")
    if kind in TEMPLATES:
        text = TEMPLATES[kind].format(**finding)
        return f"**What happened**: {text}\n\n**Recommended fix**: {finding.get('SUGGESTED_ACTION')}"
    if "TRUE_EXPOSURE_INR" in finding:
        return (f"**What happened**: The risk system reports {finding['REPORTED_UTIL']:.0%} utilisation for "
                f"{finding['LEGAL_NAME']}, but combining loans and treasury exposure across "
                f"{finding['NAME_VARIANTS']} name variants gives {finding['TRUE_UTIL']:.0%} of the approved limit.\n\n"
                "**Recommended fix**: Refresh treasury exposure in the risk system and escalate to credit risk.")
    return json.dumps(finding, default=str)


def explain(finding: dict) -> tuple[str, str]:
    """Returns (markdown explanation, source) where source is the model id or 'template'."""
    text, source = _claude(EXPLAIN_SYSTEM, json.dumps(finding, default=str, indent=2))
    return (text, source) if text else (_fallback(finding), source)


def _inr(x) -> str:
    x = float(x)
    return f"₹{x / 1e7:,.2f} crore" if abs(x) >= 1e7 else f"₹{x / 1e5:,.2f} lakh"


def letter(decision: dict) -> tuple[str, str]:
    """Customer-facing decision letter for one application."""
    customer_view = {k: decision.get(k) for k in
                     ("APP_ID", "APPLICANT_NAME", "PRODUCT", "REQUESTED_INR", "DECISION", "REASON_CODE",
                      "COUNTER_OFFER_INR", "APPROVED_LIMIT_INR", "UTIL_AFTER")}
    text, source = _claude(LETTER_SYSTEM, json.dumps(customer_view, default=str, indent=2))
    if text:
        return text, source

    name = decision["APPLICANT_NAME"]
    product = decision["PRODUCT"].replace("_", " ").lower()
    amount = _inr(decision["REQUESTED_INR"])
    if decision["DECISION"] == "APPROVE":
        return (f"Dear {name},\n\nWe are pleased to approve your application {decision['APP_ID']} for a "
                f"{product} facility of {amount}. Your relationship manager will contact you with the "
                "facility documents.\n\nYours sincerely,\n[Relationship Manager]"), source
    reason = REASONS.get(decision.get("REASON_CODE"), "it does not meet our current credit criteria")
    offer = (f"\n\nWe can, however, offer a {product} facility of up to **{_inr(decision['COUNTER_OFFER_INR'])}** "
             "today, within your approved limit." if decision.get("COUNTER_OFFER_INR") else "")
    return (f"Dear {name},\n\nThank you for your application {decision['APP_ID']} for a {product} facility of "
            f"{amount}. We are unable to approve it as requested, because {reason}.{offer}\n\n"
            "**What could change this decision**\n"
            "- Reducing existing borrowing or market exposure with us\n"
            "- Offering additional collateral or a guarantee\n"
            "- Requesting a review of your approved credit limit with updated financial statements\n\n"
            "You may ask for this decision to be reconsidered within 30 days by contacting your relationship "
            "manager.\n\nYours sincerely,\n[Relationship Manager]  \n[Date]"), source


def to_sql(question: str) -> tuple[str | None, str]:
    """Natural-language question → read-only Exasol SQL. Returns (sql, source) or (None, reason)."""
    if question in PRESET_QUESTIONS:
        return PRESET_QUESTIONS[question], "preset"
    text, source = _claude(SQL_SYSTEM, question)
    if not text:
        return None, "Set ANTHROPIC_API_KEY to ask free-form questions, or pick a preset question."
    m = re.search(r"```sql\s*(.*?)```", text, re.S | re.I)
    sql = (m.group(1) if m else text).strip().rstrip(";")
    return sql, source


def check_read_only(sql: str) -> str | None:
    """Returns an error message if the SQL is not a single read-only query."""
    code = re.sub(r"'(?:[^']|'')*'", "''", sql)  # ignore keywords inside string literals
    if ";" in code:
        return "Only a single statement is allowed."
    if not re.match(r"^\s*(SELECT|WITH)\b", code, re.I):
        return "Only SELECT queries are allowed."
    if FORBIDDEN.search(code):
        return "Query contains a statement that is not read-only."
    return None
