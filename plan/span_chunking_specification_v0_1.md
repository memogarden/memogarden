# Span Chunking Specification

**Version:** 0.1.0
**Status:** Draft
**Last Updated:** 2026-11-28
**Parent:** Project Studio Specification v0.4.6 §15 (Artifact Handler), §15.6 (span-set validity contract)
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
commit write path). Stages 2–3 are **extensions** applied to prose "where resources
are available"; their output passes the same validation.

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

## 7. Stage 3 — LLM refinement (extension; prose only; async)

A separate producer pass, never in the synchronous write path. The LLM proposes
*adjustments to existing spans* (split/merge anchored to char ranges of spans it
was given), never a wholesale re-chunk. Output passes the §15.6 validation like
any producer's.

## 8. Worked example

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
