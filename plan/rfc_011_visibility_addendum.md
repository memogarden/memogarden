# RFC-011 Visibility Addendum: `visibility: public | private`

**Version:** 1.0
**Status:** Decisions ratified 2026-11-19; implementation pending
**Amends:** RFC-011 v1.3 (Scopes, Grants, and Attention-Gated Access)
**Related:** `rfc_011_implementation.md` (progress tracker), RFC-010 v1.2 (Agent Identity), RFC-001 v5 §4.4 (multi-operator, orthogonal)

## 1. Purpose

RFC-011 §10 deferred scope visibility ("no public/secret levels"). This
addendum ratifies the design for that axis. It is a decision record, not an
implementation report: nothing in it is deployed yet.

What this is: **a `visibility` attribute on Scope entities** — existing and
new scopes alike. There is **no "Public scope"**; any scope can carry
`visibility: "public"` or `"private"`.

What this is not: not cross-operator isolation (RFC-001 §4.4 stays
orthogonal), not per-item ACLs, not field-level redaction, not hiding.

## 2. Ratified model

**Threat posture — containerizing, not hiding.** The model partitions
*content access* by scope membership; it does not conceal existence or
volume. Consequences accepted outright: out-of-scope reads return **403**
(not 404) — the existence oracle is ratified; filtered queries leak
cardinality (short pages, count deltas) — accepted.

**The access predicate** (replaces the RFC-011 §4.5 predicate; private-scope
behavior is unchanged from today):

```
visible(item, actor):
    if item has no labels:            True    # empty = public (SG-1 stands)
    if any label's scope is public:   True    # most permissive wins
    else: actor.active_scope ∈ item.labels    # private: frame-gated
    # JWT operators: always visible (SG-11 sovereignty, unchanged)
```

Rulings, each settled 2026-11-19:

1. **Empty labels = public.** SG-1 stands. The remedy for untrusted inbound
   is labeling into **Triage**, which is private — providers fail closed to
   triage, so unlabeled-by-accident is not the default ingestion path.
2. **Triage is not public.** It holds unreviewed material (statements,
   mail); migrated scopes carry `visibility: "private"`. All scopes predating
   the attribute stamp to `private` (restricted-by-default); `public` is the
   operator's explicit opt-in per scope.
3. **Most permissive wins.** An item labeled into a private scope and a
   public scope is public. Co-labeling a public scope is the disclosure act;
   D2 (symmetric label grants) governs *who* may perform it.
4. **Grants authorize entry; frames gate private reads.** Unchanged from
   RFC-011: a grant entitles `enter`; only the active frame makes
   private-scope items queryable. Visibility relaxes *reads only* — entering
   a public scope still requires its grant (grants govern entry uniformly).
5. **Agents follow key grants.** No membership layer exists or is wanted:
   "users in a scope" = principals whose keys hold grants; agent envelopes
   inherit bound keys' grants (RFC-010), unbound keys share the username
   frame. A second *human* principal someday needs either a granted key or a
   non-sovereign JWT — deliberately not designed until needed.
6. **Operator sovereignty (SG-11) stands.** JWT sees everything.
7. **Provider labeling stays policy-driven** (SG-7): the framework applies
   `scope_policy` at creation regardless of the running key's grants — the
   operator `.env` key (`grants=[]`) keeps ingesting and labeling correctly
   under enforcement; only its *reads* narrow to unscoped + public items.
8. **Cardinality leakage accepted** (see threat posture): query filtering
   may stay post-LIMIT in v1 of the visibility work.

## 3. Worked example

Scopes: `Finances` (private), `Household` (**public**), `Triage` (private),
`Personal` (private). Principals: operator (JWT), bookkeeper (key K1,
grants=[Finances]), librarian (key K2, grants=[Household]), `.env` scripting
key (grants=[]).

| Item | Operator | K1 *in Finances* | K1 other frame | K2 anywhere | .env key |
|---|---|---|---|---|---|
| F1 electricity bill `[Finances]` | 200 | 200 | 403 / filtered | 403 | 403 |
| F2 shopping list `[Household]` | 200 | 200 | **200** | **200** | 200 |
| F3 `[Finances, Household]` | 200 | 200 | **200** | **200** | 200 |

F1 is today's rule untouched: the grant gets K1 *into* the frame; the frame
is what makes F1 queryable. F2/F3 are the only new behavior: the public
label short-circuits frame and grants. Writes: entering Finances inherits
`[Finances]` on adds; relabeling F1 `[Finances]→[Household]` is gated by D2
and publishes it.

## 4. Rename (companion erratum, shipped 2026-11-19)

The builtin scope `private`/`Private` renamed to `personal`/`Personal`
(RFC-011 v1.3) precisely so the word "private" belongs to this axis alone.
Scope names name entity domains, never visibility. Rename semantics: NEW
Scope entity (immutable memory), startup migration moves references (key
grants, entity `data.scopes`, context frames, Soil item labels, provider
policies) to the new UUID, stamps the attribute, supersedes the old entity;
alias maps break clean (no legacy `private` alias). See
`system/core/__init__.py::migrate_private_scope_rename`.

## 5. Implementation surface (when this lands)

- **Prerequisite — RFC-011 item 5 (entity read-path enforcement).**
  Entities are currently decorated, not protected; visibility for entities
  is fiction until get/query/explore enforce. Hard prerequisite.
- Predicate: `scope_access.item_visible` / `filter_visible` gain the public
  short-circuit (scope visibility resolved per request; cache per run like
  the policy resolver). Consumers: fact get/query/search, SSE.
- Storage: `scope.schema.json` already declares the attribute (this
  changeset); no SQL migration (`entity.data` is schemaless).
- Console/UI: scope screens surface visibility; scope creation form gains
  the knob.
- Provider policy model: unchanged (visibility is orthogonal to labeling).

## 6. Open items (for the implementation engagement)

1. **Superseded-original visibility** — relabel = supersede; the old
   version persists with its old labels. A public→private relabel leaves the
   public-labeled original queryable (content leak past containerization).
   Verify whether superseded rows are queryable by default; decide: inherit
   successor's visibility, or suppress superseded rows for non-sovereign
   readers.
2. **SSE transition events** — relabel events must be checked against both
   old and new labels at emit (extends RFC-011 implementation item 7).
3. **Soft grant expiry** — grants are checked at `enter` time; expiry
   mid-frame does not eject. Known behavior; record only.
4. **Bulk visibility flips** — flipping a scope `public↔private` re-labels
   every member item's effective visibility in one audited operator edit;
   acceptable (containerizing), but the console should warn on flip.
5. **Relation edge rule** — SETTLED with item 5 (2026-11-19), the other
   way: edges stay fully listed (uuid/kind/endpoint-type only, no item
   data) per containerizing-not-hiding; nodes the reader cannot access
   are presented as unresolved references.
6. **UI surfacing** — visibility badge/filter on console + Studio scope
   views; query param for "public only" if a consumer wants it.
7. **Second-human principals** — deferred until a second human exists.

---

**Status:** decisions ratified; implementation sequenced behind RFC-011
item 5. Rename companion shipped 2026-11-19.

**END OF ADDENDUM**
