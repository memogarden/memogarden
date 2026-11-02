"""Integrity verification: Attachment facts vs the files on disk.

The store is content-addressed and the facts carry each file's sha256, so
mutation detection is a full re-hash: every fact's file must exist and hash
to its recorded digest, and every file under the store root must be
claimed by some fact (relative path). Anything else is reported:

- missing   — a fact's file is gone (deleted by hand, lost in a copy)
- mutated   — the file exists but its bytes no longer match the recorded
              sha256 (edited, truncated, corrupted)
- orphaned  — a file on disk that no fact references (leftover from a
              purged import, or dropped in by hand)

The check is read-only; it never repairs or deletes anything. Exit code is
non-zero when any problem is found, so it can gate scripts/cron.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from pathlib import Path

from system.soil import Soil


@dataclass
class VerifyReport:
    checked: int = 0
    ok: int = 0
    missing: list[str] = field(default_factory=list)   # fact uuid + path
    mutated: list[str] = field(default_factory=list)   # fact uuid + path + detail
    orphaned: list[str] = field(default_factory=list)  # paths

    @property
    def problems(self) -> int:
        return len(self.missing) + len(self.mutated) + len(self.orphaned)

    def summary(self) -> str:
        lines = [f"Checked {self.checked} Attachment facts: {self.ok} ok, {self.problems} problem(s)."]
        for uuid, path in self.missing:
            lines.append(f"  MISSING  {uuid}: {path}")
        for uuid, path, detail in self.mutated:
            lines.append(f"  MUTATED  {uuid}: {path} ({detail})")
        for path in self.orphaned:
            lines.append(f"  ORPHAN   {path}")
        return "\n".join(lines)


def verify_attachments(soil: Soil, store_root: Path) -> VerifyReport:
    """Re-hash every Attachment fact's file; list orphaned files. Read-only."""
    report = VerifyReport()
    root = Path(store_root)

    with soil:
        facts = soil.list_items(_type="Attachment", limit=1_000_000)

    claimed: set[str] = set()
    for fact in facts:
        report.checked += 1
        rel_path = (fact.data or {}).get("content_path")
        expected = (fact.data or {}).get("sha256")
        if not rel_path or not expected:
            report.mutated.append((fact.uuid, "?", "fact lacks content_path/sha256"))
            continue
        path = root / rel_path
        claimed.add(str(path))
        if not path.exists():
            report.missing.append((fact.uuid, rel_path))
            continue
        actual = hashlib.sha256(path.read_bytes()).hexdigest()
        if actual != expected:
            report.mutated.append(
                (fact.uuid, rel_path, f"expected sha256 {expected[:12]}…, found {actual[:12]}…")
            )
            continue
        report.ok += 1

    if root.exists():
        for path in sorted(root.rglob("*")):
            if path.is_file() and str(path) not in claimed:
                report.orphaned.append(str(path.relative_to(root)))

    return report
