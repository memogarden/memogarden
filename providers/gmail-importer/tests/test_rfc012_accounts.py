"""Tests for RFC-012 multi-account behavior in the gmail importer.

Covers:
- metadata.account stamping at translation (attribution at creation)
- per-account done-sets (gmail ids are per-mailbox namespaces)
- per-account dedup (RFC-012 D1): same Message-ID in two mailboxes = two
  facts; a re-import within one account deduplicates
- account-context provider construction: subject-qualified default state
  paths, legacy config fallback
- the run-time mailbox-mismatch guard (RFC-012 INV-3, re-checked)
- attachment extraction routed through the owning account's client
"""

import json

import pytest
from system.soil import Soil, create_email_item

from conftest import simple_message
from gmail_importer.attachments import AttachmentExtractor, AttachmentStore
from gmail_importer.gmail_api import GmailApiError
from gmail_importer.importer import GmailImporter
from gmail_importer.provider import GmailProvider, _defaults_for


ACCOUNT = "a@example.com"
OTHER = "b@example.com"


@pytest.fixture
def soil(tmp_path):
    soil = Soil(tmp_path / "soil.db")
    with soil:
        soil.init_schema()
    return soil


def _make_importer(soil, account_id):
    # Callers hold the Soil context — construction builds the dedup index,
    # and nested `with soil:` on one instance breaks the context flag.
    return GmailImporter(soil=soil, account_id=account_id, hacm_path="/tmp/x.enc")


def _email_dict(importer, account=None, gmail_id="m1"):
    email_dict = importer.translate_gmail_to_email(simple_message())
    email_dict["metadata"]["gmail_message_id"] = gmail_id
    if account is not None:
        email_dict["metadata"]["account"] = account
    return email_dict


class TestAttribution:
    def test_translation_stamps_account(self, soil):
        with soil:
            importer = _make_importer(soil, ACCOUNT)
        email_dict = importer.translate_gmail_to_email(simple_message())
        assert email_dict["metadata"]["account"] == ACCOUNT

    def test_provider_sync_yields_account_metadata(self, tmp_path, monkeypatch):
        from test_provider_shim import FakeHistoryApi

        provider = GmailProvider(
            {
                "hacm_path": "/tmp/x.enc",
                "soil_path": str(tmp_path / "soil.db"),
                "sync_state_path": str(tmp_path / "state.json"),
                "excluded_ids_path": str(tmp_path / "excluded.json"),
                "batch_limit": 10,
            },
            account={"id": "acc1", "platform": "google", "subject": ACCOUNT,
                     "label": None, "credential_ref": f"google:{ACCOUNT}",
                     "config": {}},
        )
        history = {
            "400": {
                "history": [{"messagesAdded": [{"message": {"id": "n1"}}]}],
                "historyId": "900",
            }
        }
        api = FakeHistoryApi(history, current="400")
        monkeypatch.setattr(provider.importer, "_build_api_client", lambda: api)
        list(provider.sync(since=None))  # seed
        yielded = list(provider.sync(since=None))
        assert len(yielded) == 1
        assert yielded[0]["metadata"]["account"] == ACCOUNT


class TestPerAccountDedup:
    def test_done_set_is_per_account(self, soil):
        with soil:
            importer_a = _make_importer(soil, ACCOUNT)
            importer_b = _make_importer(soil, OTHER)

            email_dict = _email_dict(importer_a)  # stamps account=ACCOUNT
            soil.create_fact(create_email_item(**email_dict))
            assert importer_a._ingested_gmail_ids() == {"m1"}
            assert importer_b._ingested_gmail_ids() == set()

    def test_same_message_two_accounts_two_facts(self, soil):
        """RFC-012 D1: each mailbox is an independent belief source."""
        with soil:
            importer_a = _make_importer(soil, ACCOUNT)
            importer_b = _make_importer(soil, OTHER)

            imported_a, _ = importer_a.import_email(
                _email_dict(importer_a, account=ACCOUNT)
            )
            imported_b, _ = importer_b.import_email(
                _email_dict(importer_b, account=OTHER)
            )
            facts = soil.list_items(_type="Email", limit=10)
        assert imported_a and imported_b  # same Message-ID, both land
        assert len(facts) == 2
        assert {f.metadata["account"] for f in facts} == {ACCOUNT, OTHER}

    def test_same_account_reimport_deduplicates(self, soil):
        with soil:
            importer = _make_importer(soil, ACCOUNT)
            first, _ = importer.import_email(_email_dict(importer, account=ACCOUNT))
            second, _ = importer.import_email(_email_dict(importer, account=ACCOUNT))
        assert first and not second


class TestProviderConstruction:
    def test_account_context_derives_subject_state_paths(self, tmp_path):
        provider = GmailProvider(
            {"hacm_path": "/tmp/x.enc", "soil_path": str(tmp_path / "soil.db")},
            account={"id": "acc1", "platform": "google", "subject": ACCOUNT,
                     "label": None, "credential_ref": f"google:{ACCOUNT}",
                     "config": {}},
        )
        assert provider.account == ACCOUNT
        assert provider.importer.sync_state_path.endswith(
            f"gmail-sync-state--{ACCOUNT}.json"
        )
        assert provider.importer.excluded_ids_path.endswith(
            f"gmail-excluded-ids--{ACCOUNT}.json"
        )

    def test_explicit_config_paths_win_over_derived(self, tmp_path):
        """The migrated legacy account keeps its historical filenames via
        its account config (RFC-012 §8 M5)."""
        legacy_state = str(tmp_path / "gmail-sync-state.json")
        provider = GmailProvider(
            {
                "hacm_path": "/tmp/x.enc",
                "soil_path": str(tmp_path / "soil.db"),
                "sync_state_path": legacy_state,
            },
            account={"id": "acc1", "platform": "google", "subject": ACCOUNT,
                     "label": None, "credential_ref": f"google:{ACCOUNT}",
                     "config": {}},
        )
        assert provider.importer.sync_state_path == legacy_state

    def test_legacy_config_account_still_works(self, tmp_path):
        provider = GmailProvider(
            {
                "account": ACCOUNT,
                "hacm_path": "/tmp/x.enc",
                "soil_path": str(tmp_path / "soil.db"),
                "sync_state_path": str(tmp_path / "state.json"),
                "excluded_ids_path": str(tmp_path / "excluded.json"),
            }
        )
        assert provider.account == ACCOUNT

    def test_no_account_anywhere_raises(self, tmp_path):
        with pytest.raises(ValueError, match="account"):
            GmailProvider({"hacm_path": "/tmp/x.enc",
                           "soil_path": str(tmp_path / "soil.db")})

    def test_defaults_for_subject_vs_legacy(self):
        assert _defaults_for(ACCOUNT)["sync_state_path"].endswith(
            f"gmail-sync-state--{ACCOUNT}.json"
        )
        assert _defaults_for(None)["sync_state_path"].endswith(
            "gmail-sync-state.json"
        )


class TestMailboxMismatchGuard:
    def test_sync_refuses_foreign_mailbox(self, tmp_path, monkeypatch):
        """A credential that authenticates as a different mailbox must not
        sync — poisoned cursors/done-sets are worse than a failed run."""
        provider = GmailProvider(
            {
                "hacm_path": "/tmp/x.enc",
                "soil_path": str(tmp_path / "soil.db"),
                "sync_state_path": str(tmp_path / "state.json"),
                "excluded_ids_path": str(tmp_path / "excluded.json"),
            },
            account={"id": "acc1", "platform": "google", "subject": ACCOUNT,
                     "label": None, "credential_ref": f"google:{ACCOUNT}",
                     "config": {}},
        )

        class ForeignMailboxApi:
            def get_profile(self):
                return {"historyId": "400", "emailAddress": OTHER}

            def history_list(self, start_history_id, page_token=None):
                raise AssertionError("must not be reached")

        monkeypatch.setattr(
            provider.importer, "_build_api_client", lambda: ForeignMailboxApi()
        )
        with pytest.raises(GmailApiError) as excinfo:
            list(provider.sync(since=None))
        assert excinfo.value.auth_failure is True
        assert OTHER in str(excinfo.value)

    def test_own_mailbox_passes_guard(self, tmp_path, monkeypatch):
        from test_provider_shim import FakeHistoryApi

        provider = GmailProvider(
            {
                "hacm_path": "/tmp/x.enc",
                "soil_path": str(tmp_path / "soil.db"),
                "sync_state_path": str(tmp_path / "state.json"),
                "excluded_ids_path": str(tmp_path / "excluded.json"),
            },
            account={"id": "acc1", "platform": "google", "subject": ACCOUNT,
                     "label": None, "credential_ref": f"google:{ACCOUNT}",
                     "config": {}},
        )
        api = FakeHistoryApi({}, current="400")
        api.get_profile = lambda: {"historyId": "400", "emailAddress": ACCOUNT}
        monkeypatch.setattr(provider.importer, "_build_api_client", lambda: api)
        assert list(provider.sync(since=None)) == []


class TestPreBackfillDefense:
    """RFC-012 §8 M5: deploy-order slip tolerance. A corpus with NO
    attributed gmail fact anywhere is the legacy single-account world —
    the done-set falls back to the unfiltered scan; once any attributed
    fact exists, the account filter is strict."""

    def test_legacy_corpus_falls_back_unfiltered(self, soil):
        with soil:
            importer = _make_importer(soil, ACCOUNT)
            legacy = _email_dict(importer, account=None, gmail_id="old1")
            legacy["metadata"].pop("account")
            soil.create_fact(create_email_item(**legacy))
            assert importer._ingested_gmail_ids() == {"old1"}

    def test_attributed_corpus_keeps_strict_filter(self, soil):
        with soil:
            importer_a = _make_importer(soil, ACCOUNT)
            importer_b = _make_importer(soil, OTHER)
            legacy = _email_dict(importer_a, account=None, gmail_id="old1")
            legacy["metadata"].pop("account")
            soil.create_fact(create_email_item(**legacy))
            attributed = _email_dict(importer_a, account=OTHER, gmail_id="new1")
            soil.create_fact(create_email_item(**attributed))
            # ACCOUNT owns nothing; corpus HAS attributed facts → strict
            assert importer_a._ingested_gmail_ids() == set()
            assert importer_b._ingested_gmail_ids() == {"new1"}


class TestAttachmentRouting:
    @pytest.fixture
    def routed_soil(self, tmp_path):
        """Soil with one email owned by ACCOUNT and one legacy (no account),
        both declaring attachments."""
        soil = Soil(tmp_path / "soil.db")
        with soil:
            soil.init_schema()
            importer = _make_importer(soil, ACCOUNT)

            attributed = _email_dict(importer, account=ACCOUNT)
            attributed_uuid = soil.create_fact(create_email_item(**attributed))

            legacy = _email_dict(importer, account=None, gmail_id="m2")
            legacy["metadata"].pop("account")
            legacy_uuid = soil.create_fact(create_email_item(**legacy))
        return soil, attributed_uuid, legacy_uuid

    def test_refetch_goes_through_owning_account(self, routed_soil, tmp_path):
        from conftest import b64url_bytes

        soil, attributed_uuid, legacy_uuid = routed_soil
        calls: list[str] = []

        class FakeApi:
            def get_message(self, message_id):
                return simple_message()

            def get_attachment(self, message_id, attachment_id):
                return {"data": b64url_bytes(b"PDF"), "size": 3}

        def factory(subject):
            calls.append(subject)
            return FakeApi()

        extractor = AttachmentExtractor(
            soil,
            AttachmentStore(tmp_path / "attachments"),
            factory,
            verbose=False,
            default_account="legacy@example.com",
        )
        stats = extractor.extract_all()

        assert stats["downloaded"] == 2
        assert set(calls) == {ACCOUNT, "legacy@example.com"}

        with soil:
            attachments = soil.list_items(_type="Attachment", limit=10)
        by_email = {a.metadata["email_uuid"]: a for a in attachments}
        assert by_email[attributed_uuid].metadata["account"] == ACCOUNT
        assert by_email[legacy_uuid].metadata.get("account") is None

    def test_account_filter_scopes_candidates(self, routed_soil, tmp_path):
        from conftest import b64url_bytes

        soil, _, _ = routed_soil
        seen: list[str] = []

        class FakeApi:
            def get_message(self, message_id):
                seen.append(message_id)
                return simple_message()

            def get_attachment(self, message_id, attachment_id):
                return {"data": b64url_bytes(b"PDF"), "size": 3}

        def factory(subject):
            return FakeApi()

        extractor = AttachmentExtractor(
            soil,
            AttachmentStore(tmp_path / "attachments"),
            factory,
            verbose=False,
            default_account=OTHER,
        )
        stats = extractor.extract_all(account_filter=ACCOUNT)

        assert stats["emails_scanned"] == 1
        assert stats["downloaded"] == 1

    def test_auth_failure_parks_account_for_the_pass(self, routed_soil, tmp_path):
        """A dead credential skips its emails once (auth_skipped), not
        per-email errors (RFC-012 §4.6, CLI mirror of needs_reauth)."""
        soil, _, _ = routed_soil

        def factory(subject):
            raise GmailApiError("invalid_grant", auth_failure=True)

        extractor = AttachmentExtractor(
            soil,
            AttachmentStore(tmp_path / "attachments"),
            factory,
            verbose=False,
            default_account=OTHER,
        )
        stats = extractor.extract_all()
        assert stats["downloaded"] == 0
        assert stats["errors"] == 0
        assert stats["auth_skipped"] == 2
