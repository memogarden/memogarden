# HANDOVER — Artifact Handler implementation → deploy + verify

**For:** the fresh session picking up the artifact-span implementation (2026-11-28
evening). **Read this file top-to-bottom, then execute the checklist.** Don't
re-decide anything already decided here or in the specs unless you find a
contradiction; ask the operator only if something is genuinely blocked.

The prior session died mid-deploy on an environment failure (Bash tool could not
spawn `/bin/bash` — `ENOENT`, ~10 probes). Expect a fresh environment to be
healthy; if the SAME error appears immediately, it's the environment again, not
your commands.

## State summary (what exists right now)

Authority chain (read in this order if you need background):

1. `plan/project_studio_specification_v0_4_1.md` §15 (Artifact Handler; v0.4.2→v0.4.6
   evolution: canonical spans, single-resolver rule, per-version sets, char/block
   addressing, minimal deltas)
2. `plan/span_chunking_specification_v0_1.md` (the reference chunking algorithm —
   block taxonomy + per-type rules + worked example §8)
3. Memory: `memogarden-artilab-app.md` → "IMPLEMENTATION STATE" section
   (§ IMPLEMENTATION STATE, 2026-11-28 session end)

Already DONE and verified:

- **`memogarden-artifact-handler/`** (new sibling repo, commit `c89616f`): chunker
  (stages 0–1), §15.6 validator, socket daemon, docling-template units, **15 tests
  green** (incl. spec §8 worked example byte-exact). `HANDOVER.md` inside the repo
  documents the §6.2 errata (spec pseudocode yields 4-char ids ~28% of the time →
  implementation mods into 36³) + salt-chain collisions.
- **Schema 20261130**: `artifact_fragment` + `artifact_span_set` +
  `artifact_span_status` — built in a system worktree, **full system suite 344
  green**, merged to system main (`d19cf6b`), root `core.sql` committed `39ac8d7`
  **and pushed** (first root push; submodule pin in system main now points at it,
  `system/schemas/memogarden` working copy synced). The old system worktree
  (`memogarden-system-wt-spans`) is deregistered + removed (submodule worktree
  removal needed clone-delete + `--force` — already done).
- **Docs written**: `docs/api.md` spans-endpoint entry, `AGENTS.md` item 14 +
  `docs/repositories.md` handler section (root, uncommitted), handler README +
  HANDOVER.

Written but UNRUN/UNCOMMITTED (the previous session lost its shell before
executing anything below):

- `memogarden-api/api/artifact_spans.py` — the §15.1 bridge (socket client +
  independent §15.6 validator + store + `sync_spans`/`maybe_sync`; pending-on-
  failure, never raises into the commit path)
- `memogarden-api/api/semantic.py` — post-dispatch hook (create-Artifact /
  commit_artifact → span sync; never breaks the envelope)
- `memogarden-api/api/v1/artifacts.py` — `GET /<uuid>/spans` (JWT + §4.5 gate)
- `memogarden-api/tests/test_artifact_spans.py` — canned-producer tests (pending
  path = unpatched default)
- `memogarden-api/scripts/backfill_spans.py` — the v1 catch-up sweep (run manually)
- `memogarden-artilab/packages/web/…` — `marked` → unified/remark renderer swap
  (source-position rendering), span overlay (`.frag` wrap + title tooltips),
  detail-page spans fetch, overlay tests, `.frag` CSS; `package.json` already
  edited: **`marked` removed, `unified`/`remark-parse`/`remark-gfm`/`remark-rehype`/
  `rehype-raw`/`rehype-stringify` added** → `npm install` REQUIRED before build
- root repo (uncommitted): `AGENTS.md`, `docs/repositories.md`, `docs/api.md`,
  `.gitignore` (handler line)
- `memogarden-artifact-handler/HANDOVER.md` (uncommitted)

All git in api/artilab is UNCOMMITTED working-tree changes on `main`. Honor the
no-main-commits discipline retroactively: `git checkout -b feat/artifact-spans`
in each repo BEFORE committing (working tree changes survive the checkout).

## Execution checklist (in order — stop and fix on any red)

### 1. Tests first (nothing ships until green)

```sh
# handler (stdlib-only; api venv has pytest)
cd ~/memogarden/memogarden-artifact-handler && ~/memogarden/memogarden-api/.venv/bin/python -m pytest test/ -q

# api — NOTE: run from a system worktree? No: api tests resolve `system` via the
# MAIN checkout's editable install (finder beats PYTHONPATH) — main system is
# already merged to 20261130, and `memogarden-system/system/schemas/memogarden`
# was submodule-synced, so this just works:
cd ~/memogarden/memogarden-api
git checkout -b feat/artifact-spans          # keep working tree, move to branch
./run_tests.sh --format=md -q 2>&1 | tail -n 8
# expect: previous suites (685+13 new ≈ 698) green
```

Fix-forward notes if api tests shake out:
- The create/commit hooks fire `sync_spans` on EVERY Artifact create/commit in the
  whole api suite — the handler socket will be DOWN in tests, so the expected
  behavior everywhere is "pending, envelope unaffected". If any PRE-EXISTING test
  now fails because of the hook, the hook's swallow isn't airtight — fix the
  bridge, don't weaken the hook.
- `tests/test_artifact_spans.py` monkeypatches `api.artifact_spans._call_handler`
  (canned producer per §15.3); the validator is an independent §6.2 copy — do not
  "simplify" by importing the handler.

```sh
# artilab (deps changed — install first)
cd ~/memogarden/memogarden-artilab && npm install --no-audit --no-fund
npm run build && npm test -w @artilab/web 2>&1 | tail -5
# overlay tests live in test/markdown.test.ts (§15 span wrap + tooltip + no-span pass-through)
```

### 2. Commits (each repo, on its branch; handler already committed)

```sh
# handler: HANDOVER.md is uncommitted
cd ~/memogarden/memogarden-artifact-handler && git add -A && git commit -m "docs: HANDOVER (§6.2 errata, follow-ups)"

# root: docs + gitignore
cd ~/memogarden && git add AGENTS.md docs/repositories.md docs/api.md .gitignore \
  HANDOVER-artifact-spans-deploy.md && git commit -m "docs: artifact spans — api.md endpoint, sibling entries, deploy handover"
git push origin main   # root push is established flow (schema pin already pushed)

# api (on feat/artifact-spans)
cd ~/memogarden/memogarden-api && git add -A && git commit -m "feat: artifact span bridge + hooks + GET spans endpoint (Project Studio §15)"
# artilab (on feat/artifact-spans)
cd ~/memogarden/memogarden-artilab && git checkout -b feat/artifact-spans && git add -A && git commit -m "feat: fragment-span hover overlay (unified renderer, resolver-backed spans)"
# merge both to main after green (git checkout main && git merge feat/artifact-spans)
```

### 3. Deploy

```sh
cd ~/memogarden/memogarden-artifact-handler && scripts/install-systemd-user.sh
systemctl --user is-active memogarden-artifact-handler.socket   # expect active (waiting)
# ingest-window check (hourly heartbeat at :00; docling timer every minute):
while [ "$(systemctl --user is-active memogarden-ingest.service)" != inactive ] || \
      [ "$(systemctl --user is-active memogarden-docling-ingest.service)" != inactive ]; do sleep 2; done
systemctl --user restart memogarden.service        # THIS is the migration deploy (20261130)
sleep 3; systemctl --user is-active memogarden.service
journalctl --user -u memogarden.service --since "-30s" --no-pager | grep -i error  # expect empty
sqlite3 "file:$HOME/.local/share/memogarden/core.db?mode=ro" \
  "select value from _schema_metadata where key='version'"   # expect 20261130
# artilab ships by rebuild (server re-reads dist per request):
cd ~/memogarden/memogarden-artilab && npm run build
```

### 4. Backfill + live verify

```sh
cd ~/memogarden/memogarden-api
.venv/bin/python scripts/backfill_spans.py          # 375 artifacts; ~seconds; re-runnable
# JWT mint — secret now lives in config.toml (P1 fix; NOT the repo default):
JWT=$(.venv/bin/python -c "
import tomllib, jwt, time
with open('$HOME/.config/memogarden/config.toml','rb') as f: s = tomllib.load(f)['security']['jwt_secret_key']
now = int(time.time())
print(jwt.encode({'sub':'entity_smoke','username':'artilab-smoke','is_admin':True,'iat':now,'exp':now+3600}, s, algorithm='HS256'))")
UUID=$(curl -s -H "Authorization: Bearer $JWT" "http://127.0.0.1:8600/api/v1/artifacts?limit=1" | python3 -c "import json,sys; print(json.load(sys.stdin)['artifacts'][0]['uuid'])")
curl -s -H "Authorization: Bearer $JWT" "http://127.0.0.1:8600/api/v1/artifacts/$UUID/spans" | python3 -m json.tool | head -30
# expect: status ok, spans with fragment_id ^xxx, abs offsets, block_type
```

### 5. Visual hover check (the operator's actual ask)

Real-data rig (strongest check — real artifacts, real spans, real renderer), no
Cloudflare in the loop:

```sh
cd ~/memogarden/memogarden-artilab
node packages/server/dist/bin/artilab.js serve --port 7445   # run in background
# then Playwright: goto http://127.0.0.1:7445/, set localStorage
#   access_token=<the minted JWT>, user_info={"id":"u1","username":"op","is_admin":true},
#   artilab-theme=dark → open /a/<UUID from step 4> → hover text → screenshot:
#   expect dotted-underline fragment spans + tooltip '^id — text'
# kill the 7445 server afterwards; do NOT touch the systemd unit on 7429 (rebuild already shipped it)
```

## Design constraints (binding — don't relitigate)

- Main service NEVER chunks (§15 ruling). Bridge = plumbing only. Chunking lives
  in `memogarden-artifact-handler` (already built).
- Spans are canonical state (schema 20261130), read only through
  `GET /api/v1/artifacts/<uuid>/spans` — single-resolver rule (§15.5).
- Commit path is sacred: span failures leave `spans_pending` and are swallowed
  (§15.6 step 4). The pre-existing api suite must stay green — that's the proof.
- Deltas carry span diffs, never sets (§15.4). Membership rows per version ARE
  the delta's span_ops contribution in this implementation.
- Marker policy: list-item spans INCLUDE markers; heading/cell spans exclude
  markers. Atomic spans (code/images/links) never merge/split. (chunking spec §4)
- Renderer rule (artilab): raw-slice mismatch on a text node ⇒ skip the wrap for
  that node. A missing highlight beats a wrong one.
- Status contract: `ok` | `pending` | `none` — the resolver answers "pending",
  never silently wrong.

## After green: bookkeeping

- awt: the api worktree `memogarden-api-wt-spans` was registered but never
  created (worktree-add died in the same failed chain) — `awt deregister --path
  ~/memogarden/memogarden-api-wt-spans --unlock` to clear the stale claim, then
  delete any leftover dir if it exists.
- Push branches (api, artilab) if remotes exist; delete merged branches.
- artilab HANDOVER.md: append the spans feature (renderer swap, spans endpoint,
  overlay) + follow-ups (stage 2 density, stage 3 LLM, catch-up sweep timer —
  backfill script is the v1 sweep).
- Update memory `memogarden-artilab-app.md` IMPLEMENTATION STATE → DEPLOYED +
  verified (with the backfill count + a live span excerpt), and the MEMORY.md
  index line.
- Leave a note to the operator: first hover check on artilab.ngjunsiang.me is
  theirs (Access SSO + operator login), everything else is verified server-side.
