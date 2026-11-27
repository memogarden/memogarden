"""One-shot backfill: classify every live Artifact lacking a doc_class.

Stage-2 v1 (register D10 revised). The rules live in doc_class.py; this
pass stamps data.doc_class + data.classification on existing artifacts —
the classify-at-arrival path covers new ones. Mixed/unknown artifacts
keep their Triage scope (operator ruling: mixed goes to Triage "to
begin"); nothing is ever re-scoped here.

    python backfill_doc_class.py [--dry]     # dry = report, no writes

Idempotent by data.doc_class absence: an artifact already carrying a
doc_class is skipped unless --reclassify.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import doc_class  # noqa: E402

from system.core import get_core  # noqa: E402


def main() -> None:
    dry = "--dry" in sys.argv
    force = "--reclassify" in sys.argv
    stats: dict[str, int] = {}
    considered = 0

    with get_core() as core:
        conn = core._conn
        rows = conn.execute(
            "SELECT uuid, data FROM entity WHERE type = 'Artifact' "
            "AND superseded_by IS NULL"
        ).fetchall()
        for row in rows:
            data = json.loads(row["data"]) if row["data"] else {}
            if not force and data.get("doc_class"):
                continue
            verdict = doc_class.classify(data.get("content") or "")
            data["doc_class"] = verdict["doc_class"]
            data["classification"] = {
                "method": "rules-v1",
                "confidence": verdict["confidence"],
                "evidence": verdict["evidence"],
                "mixed": verdict["mixed"],
                "classified_at": datetime.now(datetime.UTC).isoformat(),
            }
            stats[verdict["doc_class"]] = stats.get(verdict["doc_class"], 0) + 1
            considered += 1
            if dry:
                continue
            core.entity.update_data(row["uuid"], data)

    print(f"{'DRY' if dry else 'WET'}: {considered} artifact(s) "
          f"{'would be' if dry else 'were'} classified; "
          f"stats: {json.dumps(stats, sort_keys=True)}")
    if not dry and considered:
        print("note: entity_fts re-indexed every updated artifact via the "
              "AU trigger — expect a catch-up delay on the search plane.")


if __name__ == "__main__":
    main()
