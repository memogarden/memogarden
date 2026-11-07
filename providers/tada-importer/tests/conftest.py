"""Shared test fixtures: a real tmp-path Soil, a scripted fake tada client,
and a helper that stores yielded facts the way the provider runtime does."""

import pytest
from system.soil import Fact, Soil, generate_soil_uuid
from tada_importer.client import TadaApiError


class FakeTadaClient:
    """Scripted stand-in for TadaApiClient (no network).

    days: {local day -> [snapshot entry, ...]}; missing day -> no entries.
    items: {uid -> item dict}; missing uid -> show() returns None (not found).
    fail_show: uids whose show() raises a server error.
    """

    def __init__(self, days=None, items=None, zone="Asia/Singapore", fail_show=()):
        self.days = days or {}
        self.items = items or {}
        self.zone = zone
        self.fail_show = set(fail_show)
        self.calls = []  # (op, args) in call order

    def zone(self):
        return self.zone

    def list_day(self, day):
        self.calls.append(("list", {"day": day}))
        return self.days.get(day, [])

    def show(self, ref):
        self.calls.append(("show", {"ref": ref}))
        if ref in self.fail_show:
            raise TadaApiError("boom", status_code=500)
        return self.items.get(ref)


def done_entry(uid="01a1ab6b-be1e-701a-b7d5-8c09a7a62938", **overrides):
    """A snapshot entry in the tada §10.58 wire shape."""
    entry = {
        "uid": uid,
        "kind": "task",
        "title": "roller blinds 1.8m",
        "outcome": "done",
        "at": "2026-11-06T18:41",
        "tags": ["buy"],
    }
    entry.update(overrides)
    return {k: v for k, v in entry.items() if v is not None}


@pytest.fixture
def soil(tmp_path):
    s = Soil(str(tmp_path / "soil.db"))
    with s:
        s.init_schema()
    yield s


@pytest.fixture
def state_path(tmp_path):
    return str(tmp_path / "tada-sync-state.json")


def make_provider(soil, state_path, client, zone="Asia/Singapore", **config):
    """A TadaProvider wired to a tmp Soil and a fake client."""
    from tada_importer.provider import TadaProvider

    provider = TadaProvider(
        config={
            "soil_path": soil.db_path,
            "sync_state_path": state_path,
            "zone": zone,
            **config,
        }
    )
    provider.client = client
    return provider


def store_fact(soil, yielded):
    """Persist a provider-yielded dict the way api.provider_runtime._to_fact
    does, and return the stored Fact (for create_relations calls)."""
    fact = Fact(
        uuid=generate_soil_uuid(),
        _type=yielded["type"],
        realized_at=yielded.get("realized_at") or "2026-11-06T18:45:00+00:00",
        canonical_at=yielded["canonical_at"],
        data=yielded.get("data", {}),
        metadata=yielded.get("metadata", {}),
        integrity_hash=None,
        fidelity="full",
    )
    with soil as s:
        s.create_fact(fact)
    return fact
