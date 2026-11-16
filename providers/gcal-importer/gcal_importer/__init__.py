"""gcal_importer — Google Calendar provider (RFC-014).

Mirrors each concrete calendar event into a Core Event entity (the mutable
belief); Soil facts exist only via the mark-occurred review action. The
provider yields entity upserts ({op: upsert_entity, ...}) under the
RFC-014 §3 contract extension.
"""
