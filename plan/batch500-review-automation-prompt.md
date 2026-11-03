# Prompt for the batch-500 review automation (2026-11-04 20:00, one-shot)

If the automation titled "Review memogarden batch-500 runs at 20:00, keep or
revert" has NOT fired by 20:00 Nov 4 (check the ZCode automations page or
CronList — a prior reschedule left it in an ambiguous enabled state), create
a one-shot automation for 2026-11-04 20:00 (cron `0 20 4 11 *`,
recurring=false) with that exact title and the following prompt, verbatim:

---
You are performing the scheduled 20:00 evening review of memogarden's Gmail
ingestion running at batch size 500, in the same format as the 06:00 morning
review. Memogarden (personal email-memory system, repo
/home/kureshii/memogarden) ingests mail hourly via the user systemd timer
memogarden-ingest.timer → memogarden-ingest.service (progressive cursor,
search cache with 6h TTL). Batch was bumped 100 → 500 at 06:00 today after a
clean overnight review (2890 emails at bump time); this review validates the
500-batch behavior across the day.

Review steps:
1. journalctl --user -u memogarden-ingest.service --since "2026-11-04 06:10" --no-pager
   EXPECT at batch 500: run durations of roughly 8–15 minutes (longer than
   batch-100's ~2 minutes — EXPECTED, not an issue); zero WARNING lines;
   "Attachments:" summary lines with high "already covered" percentages and
   0 errors. Runs happen ONLY while the user is logged in (linger
   intentionally disabled) — fewer runs or a lower email count because of
   logged-out/sleeping hours is EXPECTED, not an issue. NEW warning classes
   or repeated fetch/verify errors ARE issues to raise. DURATION GUARD: if
   runs consistently exceed ~30 minutes, they crowd the hourly cadence —
   treat that as an issue and revert to batch 100.
2. Integrity: cd /home/kureshii/memogarden/providers/gmail-importer && poetry
   run python -m gmail_importer.ingest verify — expect every Attachment fact
   ok with 0 problems.
3. Counts: use
   /home/kureshii/memogarden/memogarden-system/.venv/bin/python with sqlite3
   on /home/kureshii/.local/share/memogarden/soil.db — SELECT COUNT(*) FROM
   item WHERE _type='Email' and WHERE _type='Attachment'. Expect roughly
   9,000–9,500 emails if the user stayed logged in through the workday
   (~2890 at 06:00 + ~500 per logged-in hour since 06:10); count files under
   /home/kureshii/.local/share/memogarden/attachments. HORIZON CHECK: the
   oldest-window enumeration caps at 20,000 messages — report headroom
   (20,000 minus email count); if the count approaches ~17,000, flag that
   the historyId sync (design at plan/gmail-incremental-sync-design.md,
   awaiting user go) should be prioritized.

Decision:
- If steps 1–2 are clean (no new warning classes, verify ok, durations
  within the guard) → KEEP batch 500 (no action needed) and report that the
  config is validated, with headroom and readiness notes for the historyId
  design.
- If there are new warning classes, repeated errors, or durations breach the
  ~30-minute guard → REVERT to batch 100:
  bash /home/kureshii/memogarden/scripts/install-user-service.sh install
  --with-ingest-timer --ingest-account ngjunsiang@gmail.com --ingest-limit 100
  (health-checks http://127.0.0.1:8600/health; confirm the generated unit at
  ~/.config/systemd/user/memogarden-ingest.service shows
  "next --limit 100") and report the issues in detail with journal excerpts.

Finish by reporting to the user: a run-by-run summary (start, duration,
scanned/covered/downloaded), verify results, current counts and horizon
headroom, the decision taken (kept 500 or reverted to 100, with reasons),
and the reminder that the historyId incremental-sync design is at
plan/gmail-incremental-sync-design.md awaiting their go-ahead.
---
