# Triage decision model

Classifies triage-queue items into the operator's scope ontology (or
`unclassified`, or nothing = escalate) and feeds recommendations to the
operator console (`GET /api/v1/triage` rec badges, `GET /api/v1/triage/eval`
agreement tracker).

## Ecosystem (what lives in this folder + where it plugs in)

| piece | role |
|---|---|
| `recommend.py` | sidecar generator (see Production below) |
| `family_reset.py` | ONE-TIME (executed 2026-11-22): reset never-labeled parents of triage attachments into Triage via batch `/mg amend` |
| `build_search_index.py` | embeds all live text facts → `cache/search_index.npz` + meta (incremental; ~90 min full, seconds after) |
| `embed_daemon.py` | lazy search daemon: unix socket `~/.local/share/memogarden/triage-model/embed.sock`, spawned by the API on first semantic search, idle self-exit 10 min |
| `eval_matrix.py`, `results/` | offline evaluation artifacts |
| `ontology.py` | the operator ontology — authoritative criteria text |

Console integration (memogarden-api): `/api/v1/triage` rec badges +
`/api/v1/triage/eval` agreement tracker + `/api/v1/items/search`
(filters; semantic mode calls the daemon; keyword fallback) +
`/item/<uuid>` inspector. Docs: `~/memogarden/docs/api.md` §"Decision-model
recommendations + item URLs"; architecture + sweep history in sysinfo
`incidents/2026-11-22-triage-decision-model.md`.

Sidecar entries carry `gate: pass\|escalate` (binary classification per the
operator verdict; scope routing on escalated items = suggestion only) and
`soft: true` for low-confidence leans (display-only, never in the eval).

## Production (v3, 2026-11-22 — operator verdict)

- **Model**: `bge-knn5` ALONE — kNN (k=5, sharpened distance weights) over
  bge-small-en-v1.5 embeddings of the operator's gold decisions. **The regex
  layer is dropped from the base layer** (opt-in via `--with-regex`): on the
  leakage-aware re-verification it added ~4pts false alarms to buy back
  exactly 1 escalation (99.1% escalation recall / 0.9% false-pass / 6.7%
  burden kNN-alone; robust θ=0.05–0.5).
- **Binary gate semantics**: each rec carries `gate: pass` (confident
  unclassified — bulk-clearable) or `gate: escalate` (needs human; the
  pred/confidence on escalated items is a router SUGGESTION, not a
  recommendation). 4-scope routing is secondary, by operator or a more
  capable agent. Live: **4,807 pass / 571 escalate** of 5,378.
- No backfill (operator decision 2026-11-22): pass items keep the per-row
  flash UX (→ Public ring) until the operator confirms; the eval tracker
  gathers accuracy from those decisions. Events/Notes are
  out-of-distribution → display-only soft.
- **Two tiers**: `pred >= τ` = hard rec (badge + apply button, counts toward
  the eval); `>= τ-0.2` = soft lean (muted `✦?` badge, display only, never
  in the eval). τ=0.5 shipped.
- **Calibration history** (three heads tried this morning, all in
  recommend-mb-*.log): balanced logreg = squashed probs (19% pool coverage);
  plain MLE = collapsed to majority; sigmoid-calibrated = collapsed harder.
  kNN needs none of that and runs in **seconds** warm (train+pool embedding
  caches in `cache/`).
- **Sidecar**: `~/.local/share/memogarden/triage-model/recommendations.json`
  (atomic write; API picks it up by mtime; `MEMOGARDEN_TRIAGE_RECS` env
  overrides). Live: 5,359 hard + 18 soft over the 5,377-item pool.

Regenerate after ingestion (seconds warm):

```bash
cd ~/memogarden/triage-model
HF_HOME=$PWD/hf-cache .venv/bin/python recommend.py bge-small --tau 0.5
# --with-regex adds the rule layer (opt-in only, per 2026-11-22 verdict)
# alternatives: regex (instant) | modernbert-base | d1-omni-600M (see results/)
```

## Results (2026-11-22 eval, 616 held-out decisions)

| model | latency p50 | peak RSS | curve (coverage @ precision) |
|---|---|---|---|
| regex rules | 0.6ms | ~0 | 23% @ 87% |
| d1-omni-600M zero-shot + granularity post-rule | 3,455ms | 1.8GB | 29% @ 94% (τ=0.7) |
| ModernBERT-base + logreg | 232ms | 2.2GB | 62% @ 96.5% (τ=0.6) |
| bge-small + logreg | 121ms | ~0.5GB | (inflated — see caveat) |

## ⚠️ Eval caveat: near-duplicate leakage

The dataset split (originally per-uuid) put template-identical items on both
sides (Box.net ×281, weekly newsletters, HDB thread ×17). Absolute holdout
numbers are inflated ~10pts (bge-small's 97% holdout / 99.5% coverage is not
believable). `build_dataset.py` now splits at the template/thread level —
re-run evals before trusting absolute precision. Relative ranking
(ModernBERT > d1 zero-shot > regex on coverage at matched precision)
is the reliable takeaway. **The honest number comes from the console
tracker**: the first 100 operator decisions on non-escalated items
(`GET /api/v1/triage/eval`).

## Console integration (deployed 2026-11-22, memogarden-api main)

- `GET /api/v1/triage` attaches `rec` per fact from the sidecar
- Per-row badge `✦ pred NN%` + one-tap `✦ → label` button; `unclassified`
  recs highlight the `→ Public` button
- `GET /api/v1/triage/eval` joins operator amend Actions with stored recs —
  first 100 non-escalated decisions; eval strip renders agreement on-page

## Ontology (authoritative source: operator, refined 2026-11-21/22)

In `ontology.py` — classification = sensitivity/work-context partition,
unclassified is a positive class (cycle notifications, newsletters,
existence-only gov notices), NS requires unit-material or embedded
specifics (granularity rule), household context beats financial substance,
sensitive helper docs flagged within Household. Rules baseline in
`rules_baseline.py` (kept as the instant fallback path).
