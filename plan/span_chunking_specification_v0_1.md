# Span Chunking Specification

**Version:** 0.2.0
**Status:** Implemented (stages 0–2) · Draft (stage 3)
**Last Updated:** 2026-11-28
**Parent:** Project Studio Specification v0.4.8 §15 (Artifact Handler), §15.6 (span-set validity contract)
**Implements:** the reference chunking algorithm for the Artifact Handler; guidance for all other producers

---

## 1. Scope

This document specifies the **reference span-chunking algorithm**: how artifact
content (markdown) is decomposed into fragment spans at a content version. The
[Project Studio §15.6 validity contract](project_studio_specification_v0_4_1.md)
(content-addressed ids, fidelity, structural sanity, one set per version) remains
the **only acceptance gate** — any producer passing it is valid, whatever algorithm
it uses. This spec exists so implementation and future improvement reference one
algorithm description instead of rediscovering it.

Stages 0–1 are **normative for the reference algorithm** (deterministic, run in the
commit write path). Stage 2 is an extension **implemented by the reference
producer** (§6 v0.2 pin; on by default, profile-gated); stage 3 remains
prose-only guidance, "where resources are available". All stages' output passes
the same validation.

## 2. Span model (recap)

Per §15.2/§15.4 of the parent spec: a span is
`{fragment_id, block_hash, start_char, end_char, content}` — char offsets
**relative to the containing leaf block** (blocks are content-addressed:
`block_hash` = content hash of the block's exact text, duplicates disambiguated by
occurrence index). `fragment_id` = content hash of the exact span text. Span sets
are per artifact content version; metadata-only edits never recompute. A
navigation path/selector is derived at read time from (version, block_hash) and is
never stored.

**Marker policy (decided):** list-item spans **include** their bullet/number
marker text (operator ruling). Heading/table syntax markers (`#`, `|`, `---`) are
**excluded** — heading and cell spans address their text content only.

## 3. Stage 0 — block parse

Parse the markdown into a content-addressed tree of **leaf blocks**. Every span
lives in exactly one leaf block. Leaf-block taxonomy:

| Leaf block | Chunking rule (§4) |
|---|---|
| Heading | single span |
| Paragraph (prose) | sentence segmentation (stage 1) |
| List item | one span per item, marker included |
| Table cell | one span per cell |
| Image | one atomic span |
| Standalone link | two atomic spans (display text, URL) |
| Code fence | one atomic span |
| Blockquote | prose (stage 1) |
| Raw HTML block | one atomic span (v1) |

Containers (list, table, section) are not leaf blocks; **list items and table
cells are themselves leaf blocks** at their tree position (depth/row/col live in
the derived navigation path). Emission order = document order; parsing is
deterministic: same content ⇒ same tree, always.

## 4. Per-type chunking rules (normative for the reference algorithm)

| Block type | Rule |
|---|---|
| **Heading** | One span = the heading text (`#` markers excluded). No length fallback — headings are short by nature. |
| **Table cell** | One span per cell. If the cell exceeds `PROSE_FALLBACK_THRESHOLD`, fall back to prose treatment (stage 1) within the cell. |
| **List item** | One span per item **including its marker** (`- `, `1. `). Nested lists: every item is its own span at its own depth. If the item exceeds `PROSE_FALLBACK_THRESHOLD`, fall back to prose treatment (stage 1) within the item. |
| **Image** | One atomic span = the full image occurrence (`![alt](src)`). **Captions are separate spans**: an immediately adjacent (preceding or following) single-line paragraph ≤ `CAPTION_THRESHOLD`, styled italic or prefixed `Figure`/`Caption`/`表`/`图`, is a caption span; if detection is ambiguous, the paragraph is an ordinary prose span. |
| **Standalone link** (paragraph/line consisting of one link) | **Two atomic spans**: display text and URL. Atomic = never merged or split by stages 2–3. |
| **Inline links** | Part of their containing span (prose/heading/list). The parse-time link index still records them for reference resolution. |
| **Code fence** | One atomic span. Never segmented. |
| **Blockquote** | Prose (stage 1) within the blockquote. |
| **Raw HTML block** | One atomic span (v1). |
| **Prose paragraph** | Stage 1 sentences; stages 2–3 where resources are available (§6–§7). |

`PROSE_FALLBACK_THRESHOLD` default **400 chars** (~a solid paragraph).
`CAPTION_THRESHOLD` default **200 chars**. Both are profile parameters overridable
per `doc_class` (statement vs policy vs notice may tune them).

## 5. Stage 1 — sentence segmentation (normative)

Within a prose-fallback block, split on sentence boundaries. Requirements:

- Battle-tested segmenter semantics — abbreviation, decimal, and initial guards
  (`No. 1`, `S$1,234.56`, `C.J. Koh` are the classic traps). Never naive
  `split('.')`.
- CJK terminators (`。！？`) must terminate sentences (financial documents with
  Chinese passages are expected).
- Sentences emit as spans in document order; each sentence is one span even when
  "short" (no merging at this stage — merging is stage 2's job, prose-only).

## 6. Stage 2 — density refinement (extension; prose only; rule-based)

Implement "denser text gets more fragments" with **transparent, version-pinned
rules**: cheap density features (digits, currency, dates, entity/term hits —
profiled per `doc_class`) drive *clause-level splits* (commas/semicolons) in dense
sentences and *merges* of adjacent sparse sentences. Merging never crosses block
boundaries and never touches atomic spans (code, images, links). Rule-based on
purpose: a learned density model would churn ids on every model bump.

**v0.2 pin — the implemented rules** (`handler/profiles.py`, `ALGO_VERSION =
"0.2.0-stage2"`; bump the pin on any rule/param change — §9 determinism is
"same algorithm version + same content ⇒ same span set"):

- **Prose scope**: exactly the sentence-segmented paths — paragraphs,
  blockquotes, and the §4 >400-char list-item/cell fallbacks. The §4
  one-span rulings (short list items, regular cells) are never overridden.
- **Merge phase** (before splits): runs of adjacent sentences shorter than
  `merge_max_chars` collapse to one span, capped at `merged_span_max_chars`;
  a non-sparse sentence ends the run. Per block, so crossings are
  structurally impossible.
- **Split phase**: a span scoring ≥ `split_density` (and ≥
  `min_sentence_chars` long) breaks at top-level `,` / `;`. Never inside
  parentheses; never between digits (thousands separators: `1,234` stays
  whole — the §5 decimal trap again). Clauses under `MIN_CLAUSE_CHARS` (12)
  are never cut; a tiny tail attaches to the previous clause.
- **Density score** = weighted feature hits per 100 chars: digit chars ×0.25,
  currency amounts (`S$1` / `123.45`) ×2.0, dates (day-month, DD-MM-YYYY,
  bare year) ×2.0, profile term hits ×1.0 (word-boundary, case-insensitive).
- **Profiles** (`doc_class` arrives as request-scoped metadata from the
  bridge — the handler stays Core-free): `statement` (split 4.0, merge 45,
  cap 180, split-floor 50; terms balance/debit/credit/transaction/payment)
  and `bill` (4.5 / 45 / 180 / 50; invoice, amount due, due date, gst,
  total) are dense-lean; every other class (overview, notice, policy,
  document, unknown, mixed) falls back to `DEFAULT` (6.0 / 60 / 220 / 60,
  no terms).
- **Order matters**: merge first, then split — a merge of sparse sentences
  that is itself dense still splits; clauses from the split phase are never
  re-merged.

## 7. Stage 3 — LLM refinement (extension; prose only; async)

A separate producer pass, never in the synchronous write path. The LLM proposes
*adjustments to existing spans* (split/merge anchored to char ranges of spans it
was given), never a wholesale re-chunk. Output passes the §15.6 validation like
any producer's. **Not implemented** — the only stage still unbuilt.

## 8. Worked example

This example pins the **stages 0–1 reference output** (the normative core). The
production default adds stage 2 (`profiles.DEFAULT`); on this example it is
inert — the prose sentences are either non-sparse (row 9) or the only sparse
sentence in their block (row 10, and merges never cross blocks) — so the
byte-exact expectation below also holds for the default profile. The
segmentation-guard tests pin the stages 0–1 floor explicitly via
`profiles.STAGE1_ONLY`; stage-2 behavior has its own fixtures
(`test/test_profiles.py`).

````markdown
## Premium Summary
| Item | Amount |
|------|--------|
| Base fare | S$1,234.56 |
- GST is included at the prevailing rate of 9%.
![Statement cover](cover.png)
Figure 1: Cover page of the statement.
Total charges increased due to regulatory changes announced in November. The
new tariff applies from 1 December.
[SP Group tariffs](https://spservices.com.sg/tariffs)
````

→ spans, in document order:

| # | Block | Span content |
|---|-------|--------------|
| 1 | heading | `Premium Summary` |
| 2 | table cell r1c1 | `Item` |
| 3 | table cell r1c2 | `Amount` |
| 4 | table cell r2c1 | `Base fare` |
| 5 | table cell r2c2 | `S$1,234.56` |
| 6 | list item | `- GST is included at the prevailing rate of 9%.` |
| 7 | image | `![Statement cover](cover.png)` |
| 8 | caption | `Figure 1: Cover page of the statement.` |
| 9 | prose | `Total charges increased … November.` |
| 10 | prose | `The new tariff applies from 1 December.` |
| 11 | link display | `SP Group tariffs` |
| 12 | link URL | `https://spservices.com.sg/tariffs` |

(12 spans; ids omitted — each is `^` + base36 hash of its exact text.)

## 9. Determinism and stability

- Same algorithm version + same content ⇒ same span set (required: validation by
  recompute, pending catch-up).
- **Carry-forward (SHOULD):** when re-chunking a new version, reuse ids for spans
  whose text is unchanged (content-hash ids make this a lookup). Id-churn over
  unchanged text is the producer-quality metric.
- Algorithm-version changes between edits cost churn, never correctness — stored
  sets are immutable per version (§15.4).

## 10. Non-goals

- LLM chunking in the synchronous write path (stage 3 is async by design).
- Cross-artifact fragments; semantic analysis (statement-pipeline stage 3 may
  later ride the handler — separate decision).
- Layout-aware chunking beyond markdown structure (PDF geometry belongs to the
  extraction stage, not chunking).

---

**Revision history**

| Version | Date | Changes |
|---------|------|---------|
| 0.1.0 | 2026-11-28 | Initial draft — block taxonomy + per-type rules (operator-directed: headings/cells/items/images/links), prose pipeline stages 1–3, worked example |
| 0.2.0 | 2026-11-28 | §6 pinned: stage 2 density refinement IMPLEMENTED (`ALGO_VERSION 0.2.0-stage2`) — merge-then-split rules, density features + weights, per-doc_class profiles (statement/bill tuned, DEFAULT fallback), prose-path gate incl. §4-ruling carve-out, thousands-comma/paren guards; §8 note (example = stages 0–1 reference, byte-exact under the default profile too); status: stages 0–2 implemented, stage 3 remains |
