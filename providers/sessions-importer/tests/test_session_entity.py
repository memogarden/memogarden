"""Session entity slice (register C5): every session upserts as a Core
`Session` entity (machine-bound natural key, manifest etag), and drained
facts gain contained_by relations to it — including facts drained before
this slice (backfill)."""

import json

from sessions_importer.importer import SessionsImporter
from system.soil import Fact, generate_soil_uuid
from utils import isodatetime


def _append_event(sdir, **rec):
    with (sdir / "events.jsonl").open("a") as f:
        f.write(json.dumps(rec) + "\n")


def test_entity_upserts_declared():
    assert SessionsImporter.ENTITY_UPSERTS is True


def test_upsert_shape_natural_key_and_etag(provider, make_session, drain):
    sdir = make_session("uob", machine="laptop")

    facts = drain()

    assert facts == []                      # nothing staged, no events
    upserts = drain.last_upserts
    assert len(upserts) == 1
    up = upserts[0]
    assert up["op"] == "upsert_entity"
    assert up["type"] == "Session"
    assert up["key"] == {"source": "memogarden-sessions",
                         "session": "uob", "machine": "laptop"}
    # create requirement: key fields present + non-null in the payload
    for field, value in up["key"].items():
        assert up["data"][field] == value
    assert up["data"]["etag"]               # noop rule needs a live etag
    assert up["data"]["state"] == "dehydrated"
    assert up["metadata"]["stream"] == "session_entity"


def test_etag_changes_with_manifest(provider, make_session):
    sdir = make_session("dbs")
    manifest = json.loads((sdir / "session.json").read_text())
    etag1 = provider._session_etag(manifest)
    manifest["state"] = "open"
    etag2 = provider._session_etag(manifest)
    assert etag1 != etag2
    assert provider._session_etag(manifest) == etag2  # stable


def _relation_count(provider, kind=None):
    where = "WHERE kind = '%s'" % kind if kind else ""
    with provider._soil_factory() as soil:
        return soil._get_connection().execute(
            "SELECT count(*) c FROM system_relation %s" % where
        ).fetchone()["c"]


def test_relations_backfilled(provider, make_session, drain):
    sdir = make_session("dbs")
    (sdir / "downloads").mkdir()
    (sdir / "downloads" / "a.pdf").write_bytes(b"%PDF x")
    _append_event(sdir, ts=1.0, seq=1, event="session_created", actor="daemon")

    facts = drain()
    assert len(facts) == 2
    target = "core_session_dbs-box"

    with provider._soil_factory() as soil:
        rels = soil._get_connection().execute(
            """SELECT source, target, target_type FROM system_relation
               WHERE kind = 'contained_by'""").fetchall()
    assert len(rels) == 2
    assert {r["source"] for r in rels} == {f.uuid for f in facts}
    assert all(r["target"] == target for r in rels)
    assert all(r["target_type"] == "entity" for r in rels)


def test_backfill_covers_pre_slice_facts(provider, make_session, drain):
    """Run 1: entity lookup fails (pre-slice world) — facts drain, no
    relations. Run 2: entity exists — the backfill catches up on ALL of
    the session's drained facts."""
    sdir = make_session("uob")
    _append_event(sdir, ts=1.0, seq=1, event="session_created", actor="daemon")

    provider._session_entity_uuid = lambda name, machine: None
    assert drain()                 # facts land, no relations possible
    assert _relation_count(provider) == 0

    provider._session_entity_uuid = (
        lambda name, machine: f"core_session_{name}-{machine}")
    facts2 = drain()               # nothing new to drain
    assert facts2 == []
    assert _relation_count(provider, "contained_by") == 1  # run-1 fact caught up


def test_rerun_no_duplicate_relations(provider, make_session, drain):
    sdir = make_session("dbs")
    _append_event(sdir, ts=1.0, seq=1, event="session_created", actor="daemon")

    drain()
    first = _relation_count(provider)
    drain()                        # rerun: everything probed as drained
    assert _relation_count(provider) == first


def test_backfill_links_only_the_sessions_fact_types(provider, make_session, drain):
    """The scan is typed so it can use the item type index instead of
    reading every row's metadata. This importer only writes Attachment,
    SessionEvent and PageVisit facts, so a fact of another type that merely
    carries session metadata is not the importer's and must not be linked."""
    sdir = make_session("dbs")
    _append_event(sdir, ts=1.0, seq=1, event="session_created", actor="daemon")
    provider._session_entity_uuid = (
        lambda name, machine: f"core_session_{name}-{machine}")
    drain()                                     # one SessionEvent fact, linked

    now = isodatetime.now()
    with provider._soil_factory() as soil:     # a foreign fact, same metadata
        soil.create_fact(Fact(
            uuid=generate_soil_uuid(), _type="Note",
            realized_at=now, canonical_at=now,
            data={"title": "not the importer's"},
            metadata={"provider": "memogarden-sessions", "session": "dbs"},
            integrity_hash=None, fidelity="full"))

    drain()
    with provider._soil_factory() as soil:
        linked = soil._get_connection().execute(
            """SELECT i._type FROM system_relation r
               JOIN item i ON i.uuid = r.source
               WHERE r.kind = 'contained_by'""").fetchall()
    assert [r["_type"] for r in linked] == ["SessionEvent"]
