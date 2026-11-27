"""doc_class — content-only document classification for ingested documents.

Stage-2 v1 (register v2 D10, revised by operator ruling 2026-11-27):
filenames are NEVER a signal; classification runs on extracted content via
content-only rules distilled from the operator-confirmed 19-document
sample. Each result carries its evidence — "today's sorting = tomorrow's
audit trail" — and mixed/unknown documents are edge cases that route to
Triage (operator ruling: mixed docs go to Triage "to begin"; the tags
decision follows the operator's triage labels).

Uniform return shape (classify + classify_dict): doc_class, confidence,
evidence (list of "class:n [markers]" strings), mixed flag. doc_class
values: the ratified enum statement/overview/bill/notice/policy/document/
unknown — plus the edge-case value "mixed" (statement AND overview
markers both present; pending the single-class vs tags decision).
"""

from __future__ import annotations

ENUM = (
    "statement", "overview", "bill", "notice", "policy",
    "document", "unknown",
)
MIXED = "mixed"  # edge-case value: statement + overview markers co-present

# Content-only signals distilled from the confirmed sample. Never filenames.
RULES = (
    ("bill", (
        "tax invoice", "total amount payable", "payment due",
        "invoice no", "billing period", "summary of charges",
    )),
    ("statement", (  # strong: the document holds TRANSACTION-TABLE rows
        "description of transaction", "transaction details",
        "transaction date",
    )),
    ("overview", (   # point-in-time account/loan/summary state
        "account summary as at", "portfolio overview",
        "statement of account", "total outstanding",
        "available balance", "end of summary", "consolidated statement",
        "credit card(s) statement",
    )),
    ("notice", (
        "dear mr", "dear ms", "dear mrs", "dear policy owner",
        "approval of", "change of plan",
    )),
    ("policy", (
        "policy no", "policy contract", "product summary", "freelook",
    )),
    ("document", ("foreword", "table of contents")),
)


def classify(content: str) -> dict:
    """Classify one document's extracted content.

    Returns:
        {doc_class, confidence, evidence (list[str]), mixed (bool)} —
        doc_class "mixed" when statement AND overview markers co-pres
        (edge case: routed to Triage by the caller).
    """
    text = (content or "").lower()
    if len(text.strip()) < 60:
        return {
            "doc_class": "unknown",
            "confidence": "low",
            "evidence": ["content too short (likely no-text scan)"],
            "mixed": False,
        }

    scores, evidence = {}, {}
    for cls, markers in RULES:
        hits = [m for m in markers if m in text]
        if hits:
            scores[cls] = len(hits)
            evidence[cls] = hits
    if not scores:
        return {
            "doc_class": "unknown",
            "confidence": "low",
            "evidence": ["no rule matched"],
            "mixed": False,
        }

    mixed = (
        "statement" in scores and "overview" in scores
        and scores["overview"] > scores["statement"]
    )
    if mixed:
        # Overview-dominant with an embedded transaction section — the
        # One Account / COMBINE shape the operator ruled to Triage. A
        # statement-dominant document (transaction tables at least as
        # prominent as the summary markers) is simply a statement.
        ev = ", ".join(
            f"{cls}:{n} [{', '.join(evidence[cls][:2])}]"
            for cls, n in sorted(scores.items(), key=lambda kv: -kv[1])
        )
        return {
            "doc_class": MIXED,
            "confidence": "low",
            "evidence": [
                f"mixed: overview-dominant with transaction-table markers "
                f"co-present — {ev}"
            ],
            "mixed": True,
        }

    best = max(scores, key=scores.get)
    conf = "high" if scores[best] >= 2 else "medium"
    ev = ", ".join(
        f"{cls}:{n} [{', '.join(evidence[cls][:2])}]"
        for cls, n in sorted(scores.items(), key=lambda kv: -kv[1])
    )
    return {
        "doc_class": best,
        "confidence": conf,
        "evidence": [ev],
        "mixed": False,
    }
