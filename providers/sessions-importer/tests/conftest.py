"""Fixtures: a fake sessions staging cache + a Soil on temp files, and a
drain helper that mimics provider_runtime._execute_run's consumer loop
(commit per yielded fact) so ack-after-commit semantics are exercised."""

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest
from system.soil import Soil
from system.soil.fact import Fact, generate_soil_uuid

from sessions_importer.importer import SessionsImporter

PDF_BYTES = b"%PDF-1.5 fake statement"
PNG_BYTES = b"\x89PNG fake screenshot"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@pytest.fixture
def sessions_root(tmp_path) -> Path:
    root = tmp_path / "sessions"
    root.mkdir()
    return root


@pytest.fixture
def make_session(sessions_root):
    def _make(name, machine="box"):
        sdir = sessions_root / name
        sdir.mkdir()
        (sdir / "session.json").write_text(json.dumps(
            {"name": name, "machine": machine, "state": "dehydrated"}))
        return sdir

    return _make


@pytest.fixture
def provider(sessions_root, tmp_path) -> SessionsImporter:
    p = SessionsImporter(config={
        "sessions_root": str(sessions_root),
        "store_root": str(tmp_path / "attachments"),
    })

    def _factory() -> Soil:
        soil = Soil(tmp_path / "soil.db")
        with soil:
            soil.init_schema()  # idempotent (IF NOT EXISTS)
        return soil

    p._soil_factory = _factory
    return p


@pytest.fixture
def drain(provider):
    """Consume provider.sync() exactly like the runtime: create + commit
    per yielded fact, then ask for the next item."""

    def _drain() -> list[Fact]:
        soil = provider._soil_factory()
        facts = []
        with soil:
            gen = provider.sync()
            while True:
                try:
                    y = next(gen)
                except StopIteration:
                    break
                fact = Fact(
                    uuid=generate_soil_uuid(),
                    _type=y["type"],
                    realized_at=_now(),
                    canonical_at=y.get("canonical_at", _now()),
                    data=y.get("data", {}),
                    metadata=y.get("metadata", {}),
                    integrity_hash=None,
                    fidelity="full",
                )
                soil.create_fact(fact)
                soil.commit()   # the runtime commits before resuming
                facts.append(fact)
        return facts

    return _drain
