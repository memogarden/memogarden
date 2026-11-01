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

## Implementation Status

- [x] EmailImporter pipeline reuse (dedup, threading, batching)
- [x] Gmail API OAuth via HACM (loopback flow; OOB is dead upstream)
- [x] Oldest-N fetch + translation
- [ ] Incremental sync (historyId-based; needed for scheduled ingestion)
- [ ] Attachment binaries / `contained_by` items (deliberately deferred)
