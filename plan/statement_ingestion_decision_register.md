# Statement ingestion — decision register (root#14)

**Status:** DRAFT 2026-11-26 — awaiting operator ratification. Decisions
D1–D6 gate the first slice (text extraction); D7–D9 gate the slices after
it. Style follows `rfc_013_ingestion_decision_register.md`: each decision
states a question, the options, and a recommendation; silence in a review
pass adopts the recommendation.

## Where we start from (measured 2026-11-26)

- **4,904 PDF attachments** are already stored as immutable `Attachment`
  facts in the content-addressed `AttachmentStore` — every one with a
  `content_path` and a `sha256` — 2.1 GB in total (median 126 KB, p90
  1.0 MB, max 19 MB). They arrive by email (gmail) and by browser download
  (sessions). By scope: NYJC 2,930 · Triage 1,238 · global 651 ·
  Household 80 · Personal 4 · Finances 1.
- **None has extracted text.** Nothing in the workspace parses a PDF:
  search can see a statement's filename, never its contents.
- Prerequisites are built — and, at this writing (2026-11-26), still
  uncommitted on their branches: RFC-011 labels and provider policies
  (live); SG-9 (a fact declaring `metadata.derived_from` inherits its
  source's current scope labels — default-on, both fact-creation lanes);
  the FTS5 lexical plane (its insert trigger indexes every new fact's
  `data`); the chunked semantic index; the in-process provider runtime on
  one heartbeat. One defect in the index is being fixed alongside: its
  change-log catch-up had been a no-op since it shipped.
- Tooling on this host: the server venv has no PDF library, but poppler's
  `pdftotext` / `pdfinfo` and `tesseract` are installed system-wide.

## What this register covers

MemoGarden's founding purpose is transactions-as-beliefs reconciled
against documents (PRD principle 6, Document-Centric Traceability). The
long game is statement → candidate transactions → reconciliation against
`Transaction` beliefs. **This register covers only the first leg: getting
document text into Soil, correctly scoped and searchable.** The statement
schema, candidate extraction and reconciliation get their own register —
designed against real extracted documents rather than guesses.

## Decisions

### D1 — First slice is text, not transactions

Build text extraction alone first, or design the whole pipeline up front?

**Recommendation: text first.** Text in Soil is useful on its own (it
makes every past statement searchable), it is the prerequisite for
everything downstream, and a statement schema designed against real
extracted documents will be better than one designed now.

### D2 — Extraction engine

| Option | Licence / weight | Quality | Notes |
|---|---|---|---|
| (a) poppler `pdftotext` via subprocess | already installed; no Python dependency | good for text PDFs; `-layout` keeps columns usable | external binary — a deploy dependency (`poppler-utils`); no structured tables |
| (b) `pypdf` | BSD, light pure Python | basic text; weak layout | no system dependency; no tables |
| (c) `pdfplumber` | MIT; pulls pdfminer.six + Pillow | layout objects and tables | slower, heavier |
| (d) PyMuPDF | AGPL-3.0; large wheel | best | licence and weight |

**Recommendation: (a) for the first slice**, wrapped in a single
extraction module (the project's "confine external dependencies to one
module" rule) so the engine is swappable. It adds no dependency and its
quality can be judged on the pilot. Revisit (c) when table-structured
extraction is designed for the statement schema.

A scanned PDF yields little or no text: record it and stop — see D7.

### D3 — Local only

The published Gmail usage policy forbids uploading user data to
third-party clouds, and the 2026-11-09 assessment named local-first
extraction as the clean default.

**Recommendation: a standing rule — extraction runs on the host and no
statement content leaves the machine.** No cloud toggle in this slice; if
one is ever wanted it comes with its own explicit policy decision.

### D4 — Where it runs: a provider on the heartbeat

**Options:** (a) a dedicated provider module under `providers/` using the
standard provider contract — its `sync()` finds PDF `Attachment` facts
that have no extracted text yet, within a per-run budget, and yields
derived text facts; (b) a post-attachment hook inside the gmail and
sessions providers; (c) a separate daemon and unit.

**Recommendation: (a).** One heartbeat, in-process, no new service. It is
source-agnostic (email and browser downloads alike), idempotent, budgeted,
and registered and retired like any other provider — and it inherits
scope through the same creation seam as every other fact (SG-9).
Option (b) couples extraction to each ingest path; (c) cuts against the
one-heartbeat preference. **Register it with
`scope_policy = {"rules": [], "default": null}`** — not the ratified
fail-closed default of triage. D6 says why.

### D5 — Derived fact shape

One fact per document or per page, and under what type?

**Recommendation: one fact per document, of a new fact type
`ExtractedText`.** This decision also ratifies the marker the SG-9
implementation chose: `metadata.derived_from` naming the source Soil fact.

- `data`: `text` (plain text, page breaks preserved as form feeds so a
  later slice can split pages), `page_count`, `char_count`, `extractor`,
  `extractor_version`, `truncated` (true when `text` hit the cap), and
  `status` (`ok` | `no_text` | `failed`).
- `metadata`: `derived_from` (the `Attachment`'s uuid — the SG-9 marker),
  `source_sha256` (the exact bytes that were extracted — provenance and
  the idempotency key), plus the `provider` / `account` stamps the runtime
  adds. Deliberately no `email_uuid`: that is the parent rule's marker,
  and a derived fact's visibility follows its source, not a covering
  email.
- Text cap: **100,000 characters** per fact, with `truncated` set. A
  statement is a few pages; a 500-page manual should not become one giant
  fact. The semantic index embeds at most ~7,200 characters per item
  anyway (12 chunks × 600); the lexical plane indexes all of it.
- Idempotency: skip an `Attachment` that already has a live `ExtractedText`
  with the same `source_sha256`.
- First schema for a derived fact type:
  `schemas/types/facts/extractedtext.schema.json`. Only the provider
  creates these facts, so the type stays off the `add` verb's whitelist.

### D6 — Backfill the existing corpus

**Recommendation: a stratified pilot of 20 documents first** (across
scopes and accounts), reviewed by the operator for text quality; then
backfill all 4,904 at a budget of **100 documents per run**. `pdftotext`
is generally fast, but timing is unmeasured on this corpus — the pilot
reports it. The slower part is likely the semantic index catching up,
which the embed daemon already does in bounded passes when it wakes.

Note on exposure — the extractor's policy default decides where the text
of undecided documents lands. Under D4 the text is created in the
provider lane, where SG-9 inherits a labeled source's labels (triage
included) and does nothing for an unlabeled one, which then follows the
provider policy's default:

- **Decided PDFs (NYJC 2,930 · Household 80 · Personal 4 · Finances 1):**
  their text inherits the PDF's labels through SG-9.
- **Triage PDFs (1,238):** their text inherits the triage label — as
  visible as the PDF, no more.
- **The 651 global PDFs:** nothing to inherit. With the ratified default
  (triage) their text would land in triage — *less* visible than the
  PDFs, and 651 more rows in the triage queue. With
  `scope_policy.default = null` (D4's recommendation) it stays global,
  like the PDF.

Either way the text is never more visible than its source. Extraction does
make the content *searchable*, which is the point.

### D7 — OCR for scanned documents (deferred)

**Recommendation: out of the first slice.** Scanned documents are recorded
with `status: no_text`; the pilot reports how many there are. OCR
(`tesseract` is installed) is slower and lossier, and multiplies a
document's sensitivity (images, handwriting) — decide once the pilot
shows whether statements are commonly scans.

### D8 — Relabel cascade to derived facts (the slice after)

SG-9 labels a derived fact correctly at creation. Afterwards the facts
are independent: when the operator relabels a source document, its
`ExtractedText` keeps the old labels — the console's family cascade
covers `contained_by` family, not `derived_from` children.

**Options:** (a) extend the console's cascade to `derived_from` children;
(b) a periodic sweep; (c) accept drift.

**Recommendation: (a), as the next small slice after extraction ships.**
Extraction without it leaves a stale-label window.

### D9 — Search integration (part of the first slice)

Add `ExtractedText` to the semantic index's type list and to the console's
type filter. The lexical plane needs nothing: its insert trigger indexes
the fact's `data` on creation, and facts are immutable, so the UPDATE gap
does not apply.

## Sequencing

1. **D1–D6 ratified → slice 1:** extraction module, `ExtractedText`
   schema, provider registration, the 20-document pilot.
2. **Slice 2:** budgeted backfill, D8 cascade, D9 index type list.
3. **Next register:** statement schema (institution, account, period,
   document class) → candidate transactions → reconciliation (Budget PRD).

## Docs to land with slice 1

`schemas/types/facts/extractedtext.schema.json` · `docs/repositories.md`
(the new provider) · `docs/configuration.md` (the provider's budget) ·
RFC-012 §4.9 cross-link (extraction consumes attachments) ·
`plan/memogarden_prd_v0_11_0.md` status line.
