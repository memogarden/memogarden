# Statement ingestion — decision register v2 (root#14)

**Status:** REVISED DRAFT 2026-11-26 (evening) — supersedes the morning
draft of the same day. The morning draft predated the layout-engine
experiment and the live ingestion trial; its D2/D4/D5 are replaced by
operator decisions taken 2026-11-26 (recorded below with their evidence).
Style follows `rfc_013_ingestion_decision_register.md`: question, options,
recommendation; silence in a review pass adopts the recommendation.

## Where we start from (as of 2026-11-26 evening)

- **The ingestion stage is LIVE.** `memogarden-docling` worker + the
  `~/Documents` watch (trial): 375 PDFs/118 MB ingested as Triage-scoped
  `Attachment` facts, converted to Core `Artifact` entities (markdown,
  provenance-linked). Ops record:
  `~/sysinfo/incidents/2026-11-26-memogarden-docling-documents-ingest.md`.
- **Engine decided by experiment**: Docling 2.153.0 over
  PP-DocLayoutV3/PP-StructureV3 (8.15 vs 39.40 s/page; born-digital row
  integrity 0% vs 17% damaged; scans comparable) —
  `~/sysinfo/incidents/2026-11-26-doclayout-engine-comparison.md`.
- **The larger corpus still waits**: 4,904 PDFs (2.1 GB) already stored as
  `Attachment` facts from gmail + sessions; none extracted yet. The trial
  covers `~/Documents` only; the AttachmentStore backfill pass is one
  slice away (same extractor logic, heartbeat-scheduled).
- **Prerequisites all shipped**: RFC-011 labels/policies, SG-9 (fact-level;
  entity-side = `data.scopes`), FTS5 lexical plane (insert trigger),
  chunked semantic index, one-heartbeat runtime, socket-activated
  model-layer services (embed precedent, now docling too).
- Prior statement-pipeline analysis: the 2026-11-09 assessment + user's
  6-point scope model; scope-first sequencing completed before any
  extraction work (the operator's own sequencing, honored).

## Scope of this register

Still **only the first leg**: document text → correctly scoped, searchable
structured artifacts. The statement schema, candidate transactions, and
reconciliation get their own register, designed against real extracted
documents. Trial exit criteria (operator): a credible replacement for
direct file-manager access — listing, searching, accessing ingested
documents.

## Decisions

### D1 — First slice is text, not transactions — KEPT, overtaken by events

Text first remains right, and is no longer hypothetical: ingestion is live.
Transaction extraction is explicitly a later stage (see D10's routing).

### D2 — Extraction engine — **REPLACED: Docling**

The morning draft recommended poppler `pdftotext`; the operator redirected
(tables and scans need layout-aware extraction → structured JSON/markdown),
the comparison experiment ran, and the verdict is:

**Docling 2.153.0 for both document classes**, as a content-agnostic
socket-activated worker (`memogarden-docling/`), one swappable module on
the MemoGarden side. Rejected: PP-DocLayoutV3/PP-StructureV3 (4.8× slower;
born-digital row drift hits exactly the structures the pipeline exists
for; second heavyweight stack); plain pdftotext (no tables/structure;
still useful as the text-layer probe under Docling's pipeline). GLM-OCR
(local, ollama-served) remains the **fallback table recognizer for
scans**, gated on the varied-selection output review (D6) — not yet
compared on real scanned statements.

### D3 — Local only — KEPT, EXTENDED to the classification tiers

Extraction runs on the host (no content leaves the machine) — now with an
operator restriction that closes the future loophole: **categorisation and
classification must also run locally or on trusted remotes only**, because
content sensitivity is unknown at those stages. Trusted remote = an
explicit allowlist (MemoGarden API loopback; operator-owned inference
hosts per-host decision). Public cloud AI APIs are forbidden pre-scope.
Escalation paths if rules plateau are local models only (bge/kNN sidecar,
ollama-served GLM).

### D4 — Where it runs — **REPLACED: worker + watch, proven by trial**

Not a provider yielding ExtractedText facts (see D5), and not the morning
draft's shape. What exists:

- `memogarden-docling/` worker: content-agnostic unix-socket daemon
  (uv-pinned python 3.12, docling 2.153.0), systemd socket-activated, idle
  self-exit, own memory caps — model-layer inference never runs inside
  `memogarden.service`.
- `providers/docling-extractor/ingest_documents.py` (root): the
  MemoGarden-aware seam. Trial form: a `~/Documents` watcher (minutely
  timer, flock, sha256-idempotent, `ingested/` mirror, self-healing
  fact→artifact recovery).
- **Pending slice**: the same extractor logic as a budgeted heartbeat
  pass over the 4,904-pdf AttachmentStore corpus (facts already exist
  there; only the Artifact leg runs — the worker converts the store copy,
  so no source-file access is needed).

The register's earlier `scope_policy = {"default": null}` question is
superseded by D13's trial rule (Triage now; revisit at the
categorisation slice).

### D5 — Derived shape — **REPLACED: Core `Artifact` entity**

The morning draft's `ExtractedText` fact type is **rejected** (operator):
the source of information is an **attribute of the entity**, not its
type. What shipped in the trial:

- One **`Artifact` entity per document** (schema
  `schemas/types/entities/artifact.schema.json` — pre-existing, first
  real instantiation): `label` (filename), `content` (markdown,
  capped at 400k chars with an `extraction.truncated` flag),
  `content_type` (`text/markdown`; this pre-existing required field IS
  the mimetype field — it describes the artifact's own content, while the
  source PDF's mime lives on the `Attachment` fact).
- Provenance: `data.source = {attachment_uuid, sha256, origin_path}` —
  mirrors SG-9's marker convention. Reverse lookups query
  `json_extract(data,'$.source.attachment_uuid')`. The
  `entity.derived_from` column (entity→entity) is reserved for the
  transaction-extraction stage (Transaction `derived_from` its statement
  Artifact).
- Scope: `data.scopes` (entities carry scope labels in data — same
  convention as facts' `metadata.scopes`). Trial value: Triage
  everywhere (see D13).
- Idempotency key: the source `sha256` (skip if a live Artifact with the
  same source sha256 exists). The type stays off the `add` verb's
  whitelist — only the extractor creates these.
- `extractedtext.schema.json` is NOT needed; no new fact schema in this
  leg.

### D6 — Pilot — **REFRAMED: the varied-selection output review**

Not a timing pilot anymore (the trial measures timing continuously). The
review the operator set: run the existing trial outputs over a **varied
selection** — banks not yet sampled (OCBC, DBS), telco, insurance,
government, receipts, **the known scanned statements** (OCBC PRS, Home
Loan — found during corpus assembly), course-material controls — and
judge structure/table quality against reference renders. Its decision:
**whether GLM-OCR gets inserted anywhere** (and for which document
classes). Also reports the born-digital/scanned split of the 4,904-doc
corpus, which sizes the AttachmentStore backfill.

### D7 — OCR — DECISION RULE now attached

Scanned documents are handled by Docling's internal OCR today
(`status: no_text` only if it yields nothing). GLM-OCR insertion is
decided by D6's review — likely per-document fallback on quality
signals, not a blanket pipeline stage. `tesseract` TSV stays the
coordinates-of-last-resort.

### D8 — Relabel cascade — KEPT (the slice after extraction ships)

When a source is relabelled, its derived Artifact must follow (the
console's family cascade covers `contained_by`, not `derived_from`/
source-linkage children). Options unchanged; recommendation (a) extend
the cascade stands. Note it now applies to **entity** relabels
(`data.scopes`) rather than fact amends.

### D9 — Search integration — KEPT (the trial-exit slice)

`Artifact` entities join the semantic index's type list and the console's
type filter; the lexical plane needs nothing new for the markdown
content. This is part of the **trial exit criteria**: the listing/
search/access interface that replaces direct file-manager access.

### D10 — NEW: Categorisation before classification

Two granularities, one rules-first mechanism, at the extractor seam:

- **Categorisation** (routing): `financial / non-financial / unknown`,
  envelope-first (sender domain + subject + filename — available before
  conversion), refined by content. Gates **transaction extraction
  eligibility**, review priority, extraction ordering — NOT conversion or
  Artifact creation (everything stays searchable; course material
  included).
- **Classification** (identification): `doc_class` + institution + period
  from envelope + page-1 structure/keywords; evidence recorded per
  decision (today's sorting = tomorrow's audit trail).

Both emit `{value, confidence, evidence[]}`; below threshold →
`unknown`/`unclassified` (the positive-noise class — the 2026-11-21
operator reframe applied to document type). No LLM in v1; no new model
training unless rules plateau (and any escalation obeys D3's
local/trusted restriction). Stored as Artifact `data.category` /
`data.doc_class` (+ institution/period).

### D11 — NEW: Uncertain artifacts go to Triage

`unknown`-category or unclassified Artifacts get **Triage** scope and
join the triage queue (the review selection — same flow as every other
untrusted item). Classified Artifacts **inherit their source's scope**
(SG-9-style via `data.scopes` from the source Attachment's labels).
Invariant: **a derived Artifact is never more visible than its source,
and never less reviewed than an unclassified item.** Trial phase: all
Artifacts are Triage (nothing is classified yet).

### D12 — NEW: Pre-scope stages run locally or on trusted remotes only

Extraction, categorisation, and classification process content whose
sensitivity labels do not exist yet — the only protection is where the
computation runs. Allowlist per D3; public cloud AI APIs forbidden.
Costs nothing today (the whole stack is already local); constrains
tomorrow's shortcuts.

### D13 — Note absorbed into D10/D11

(The morning draft's D4 `scope_policy` sub-question and D13-draft
triage question are superseded by the D11 trial rule + the
categorisation-slice revisit.)

## Sequencing

1. **LIVE (trial)**: ingestion of `~/Documents` (worker + watch) —
   backfill running.
2. **Varied-selection output review** (D6) → GLM-OCR insertion decision.
3. **Categorisation/classification slice** (D10/D11 rules + evidence).
4. **Artifact indexing + listing/search/access interface** (D9) →
   **trial exits**.
5. **AttachmentStore backfill pass** (4,904 PDFs, budgeted heartbeat
   pass over existing facts).
6. **Next register**: statement schema (institution, account, period,
   document class) → candidate transactions → reconciliation
   (Transaction entities; reconciliation outcome on the transaction —
   required metadata open, operator discussion pending).

## Docs landed with the trial

`docs/repositories.md` (memogarden-docling + extractor) ·
`docs/configuration.md` (env knobs) · `AGENTS.md` (sibling list) ·
`memogarden-docling/README.md` (protocol/ops) ·
`~/sysinfo/systemd/user/` units + manifest ·
`~/sysinfo/incidents/2026-11-26-memogarden-docling-documents-ingest.md`.
The morning draft's `extractedtext.schema.json` is not needed.

---

**Status:** REVISED DRAFT 2026-11-26 evening — awaiting operator
ratification (silence adopts recommendations; object per decision).
Supersedes the morning draft recorded in the git history of this file.

**END OF REGISTER**
