"""sessions-importer: drain the memogarden-sessions staging cache into
MemoGarden Soil as gmail-parity Attachment facts. RFC-013 §8; decisions of
record in plan/rfc_013_ingestion_decision_register.md."""

from sessions_importer.importer import SessionsImporter

__all__ = ["SessionsImporter"]
