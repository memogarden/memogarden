# Gmail Email Importer

Import emails from the Gmail API into MemoGarden Soil.

## Status

**Implemented** (2026-11). Fetches the N oldest messages of a mailbox via
the Gmail API and stores them as immutable Email facts with the same shape
as the mbox importer: RFC 5322 fields in `data`, Gmail-specific fields
(message id, thread, labels) in `metadata`. **Attachments are metadata
only** (filenames, count, flag) — no binaries (see
`plan/email-implementation-plan.md` Q2).

## Features

- Reuses the mbox-importer `EmailImporter` pipeline: deduplication by
  Message-ID, batch processing, threading (`replies_to` relations), stats.
- Oldest-N search that does not trust Gmail's `resultSizeEstimate`
  (observed to lie in both directions): gallop + binary search on
  `before:` date prefixes using actual enumerability, final window fully
  enumerated.
- OAuth credentials live only in the encrypted HACM store
  (`google:oauth-client`, `google:{account}`); passphrased stores supported.
- Bodies decoded with the part's declared charset (legacy charsets matter
  for old mail; Soil facts are immutable, so a wrong decode would persist).

## Installation

```bash
cd providers/gmail-importer
poetry install   # path deps: memogarden-system, mbox-importer, memogarden-hacm
```

## Usage

```bash
# One-time: seed Google OAuth client credentials into HACM
#   (--client-secrets FILE = flat or GCP Console download JSON; or omit
#    for an interactive prompt. The secret never touches the repository.)
poetry run python -m gmail_importer.ingest --account you@gmail.com auth-setup

# One-time: browser consent (loopback callback on localhost:8080)
poetry run python -m gmail_importer.ingest --account you@gmail.com auth-login

# Ingest the 10 oldest messages (attachment metadata only)
poetry run python -m gmail_importer.ingest --account you@gmail.com oldest
```

Re-running `oldest` with the same `--limit` after a successful import is a
no-op (the same messages deduplicate); grow the window with `--limit N`.

## Attachments on disk

**In the provider contract since 2026-11-20 (RFC-012 §4.9 resolved):**
attachment binaries are extracted by the provider's own `sync()` (stage 3) —
the standalone `ingest attachments` subcommand was retired. Binaries land in
`<data-dir>/attachments/<h2>/<h2>/<sha256><ext>` — content-addressed, so
identical files deduplicate and integrity is checkable against the fact's
`sha256`. Each instance is an immutable `Attachment` fact (original filename,
mime, size, hash, relative path) created by the MemoGarden runtime — and
therefore scope-stamped by the operator's scope policy like every other fact —
linked to its email with a `contained_by` relation; search targets the
metadata, never the files. Email facts are not modified. NB: Gmail attachment
ids are per-fetch handles and change between fetches — idempotency is keyed on
`(email, filename)`, so re-runs skip what is already extracted; the coverage
scan is strictly per-account (each account's run covers only its own mail).

## Integrity verification

```bash
poetry run python -m gmail_importer.ingest verify
```

Re-hashes every attachment file against its fact's `sha256` and reports
missing files, mutated content, and orphaned files no fact claims.
Read-only, local-only (no Gmail auth), exits non-zero on any problem —
suitable as a cron gate.

## Implementation Status

- [x] EmailImporter pipeline reuse (dedup, threading, batching)
- [x] Gmail API OAuth via HACM (loopback flow; OOB is dead upstream)
- [x] Oldest-N fetch + translation
- [ ] Incremental sync (historyId-based; needed for scheduled ingestion)
- [x] Attachment binaries on disk (content-addressed `attachments/` dir,
      `Attachment` facts + `contained_by` relations — via the provider
      contract since RFC-012 §4.9; `verify` subcommand remains)
