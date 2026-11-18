"""Tests for the gcal_importer.ingest CLI (auth-login flow).

This module had ZERO coverage when the live loader bug shipped (the CLI
used the loader's returned CLASS as if it were the module — crashed
before printing the consent URL, 2026-11-17 deploy). These tests pin the
loader contract and the auth flow's no-store-on-failure guarantees.

The loader contract under test: GmailImporter._load_oauth_client()
returns the GmailOAuthClient CLASS and seeds sys.modules["gmail_oauth"],
so a plain `import gmail_oauth` works afterwards. The flow tests mimic
that contract with a fake module in sys.modules.
"""

import sys
import types

import pytest

from gcal_importer import ingest


# ----------------------------------------------------------------------
# Fake gmail-oauth machinery (mirrors the real surface the CLI touches)
# ----------------------------------------------------------------------

class FakeOAuthClient:
    instances = []
    stored = []
    exchanged = []
    refreshed = []

    def __init__(self, hacm_path, passphrase=None, client_id=None, client_secret=None,
                 redirect_uri=None):
        self.hacm_path = hacm_path
        self.passphrase = passphrase
        FakeOAuthClient.instances.append(self)

    def get_client_credentials(self):
        return ("fake-client-id", "fake-secret")

    def _generate_state(self):
        return "STATE123"

    def exchange_code_for_tokens(self, code, redirect_uri=None, state=None):
        FakeOAuthClient.exchanged.append(code)
        return {"access_token": "AT", "refresh_token": "RT", "scope": "cal+mail"}

    def store_tokens(self, account_id, tokens):
        FakeOAuthClient.stored.append((account_id, tokens))

    def refresh_access_token(self, account_id):
        FakeOAuthClient.refreshed.append(account_id)
        return {"access_token": "AT"}


class FakeCallbackServer:
    results = [None]  # what wait_for_callback returns (pop per instance)

    def __init__(self, port=8080):
        self.port = port

    def wait_for_callback(self, timeout=300):
        return FakeCallbackServer.results.pop(0)


def install_fake_module(monkeypatch):
    """Seed sys.modules["gmail_oauth"] exactly as the real loader does,
    populated with the fakes."""
    fake = types.ModuleType("gmail_oauth")
    fake.GOOGLE_AUTH_URI = "https://accounts.google.com/fake-auth"
    fake.GOOGLE_SCOPES = ["scope-a", "scope-b"]
    fake.GmailOAuthClient = FakeOAuthClient
    fake.SimpleCallbackServer = FakeCallbackServer
    monkeypatch.setitem(sys.modules, "gmail_oauth", fake)
    monkeypatch.setattr(ingest, "_load_gmail_oauth", lambda: FakeOAuthClient)
    return fake


@pytest.fixture(autouse=True)
def reset_fakes():
    FakeOAuthClient.instances.clear()
    FakeOAuthClient.stored.clear()
    FakeOAuthClient.exchanged.clear()
    FakeOAuthClient.refreshed.clear()
    FakeCallbackServer.results = [None]
    yield


def make_args(**overrides):
    import argparse

    defaults = {
        "account": "me@gmail.com",
        "hacm": None,
        "passphrase_file": None,
        "register": False,
        "label": None,
        "api_url": "http://127.0.0.1:8600",
        "api_key": "TESTKEY",
        "timeout": 10,
    }
    defaults.update(overrides)
    return argparse.Namespace(**defaults)


# ----------------------------------------------------------------------
# The loader contract (the live bug)
# ----------------------------------------------------------------------

class TestLoaderContract:
    def test_real_loader_returns_class_and_seeds_module(self):
        """The regression test for the 2026-11-17 deploy bug: the loader
        must return the CLASS and leave `import gmail_oauth` working."""
        from gmail_importer.provider import GmailImporter

        cls = GmailImporter._load_oauth_client()
        assert cls.__name__ == "GmailOAuthClient"
        import gmail_oauth  # must be importable after the loader ran
        assert gmail_oauth.GmailOAuthClient is cls

    def test_cli_helper_uses_loader_without_module_confusion(self, monkeypatch):
        """_load_gmail_oauth returns the class; the CLI must not attribute-
        access GmailOAuthClient off it (the exact live crash)."""
        fake_cls = type("FakeClient", (), {})
        calls = []

        def fake_loader():
            calls.append(1)
            return fake_cls

        monkeypatch.setattr(
            "gmail_importer.provider.GmailImporter._load_oauth_client",
            staticmethod(fake_loader),
        )
        assert ingest._load_gmail_oauth() is fake_cls


# ----------------------------------------------------------------------
# auth-login flow guarantees
# ----------------------------------------------------------------------

class TestAuthLoginFlow:
    def _run(self, monkeypatch, calendar_entries, args_overrides=None, callback=None):
        fake_module = install_fake_module(monkeypatch)
        if callback is not None:
            FakeCallbackServer.results = [callback]

        class FakeCalendarClient:
            def __init__(self, access_token):
                self.access_token = access_token

            def calendar_list(self):
                return calendar_entries

        monkeypatch.setattr(
            "gcal_importer.calendar_api.GoogleCalendarClient", FakeCalendarClient
        )
        args = make_args(**(args_overrides or {}))
        return ingest.cmd_auth_login(args)

    def test_happy_path_verifies_stores_and_registers(self, monkeypatch):
        posts = []

        class FakeResponse:
            status_code = 201
            text = ""

            def json(self):
                return {"id": "acct-1", "subject": "me@gmail.com"}

        monkeypatch.setattr(
            "requests.post",
            lambda url, **kw: posts.append((url, kw)) or FakeResponse(),
        )
        entries = [
            {"id": "other@gmail.com", "primary": None},
            {"id": "me@gmail.com", "primary": True},
        ]
        rc = self._run(
            monkeypatch,
            entries,
            args_overrides={"register": True, "api_key": "K1"},
            callback={"code": "GOOD", "state": "STATE123"},
        )
        assert rc == 0
        # Token stored under the VERIFIED subject, nothing before it.
        assert [a for a, _ in FakeOAuthClient.stored] == ["me@gmail.com"]
        # Registration POSTed the credential REFERENCE (never the token).
        url, kw = posts[-1]
        assert url.endswith("/api/v1/providers/gcal/accounts")
        assert kw["json"]["credential_ref"] == "google:me@gmail.com"
        assert "AT" not in str(kw["json"])

    def test_state_mismatch_aborts_without_storing(self, monkeypatch):
        with pytest.raises(SystemExit, match="state mismatch"):
            self._run(
                monkeypatch,
                [{"id": "me@gmail.com", "primary": True}],
                callback={"code": "GOOD", "state": "WRONG"},
            )
        assert FakeOAuthClient.exchanged == []  # never exchanged
        assert FakeOAuthClient.stored == []

    def test_subject_verification_failure_aborts_without_storing(
        self, monkeypatch
    ):
        # Consent was granted for a DIFFERENT account than --account:
        # nothing may be persisted (RFC-012 INV-3).
        entries = [{"id": "someone-else@gmail.com", "primary": True}]
        with pytest.raises(SystemExit, match="verification failed"):
            self._run(
                monkeypatch,
                entries,
                callback={"code": "GOOD", "state": "STATE123"},
            )
        assert FakeOAuthClient.stored == []

    def test_register_409_is_relink_not_error(self, monkeypatch):
        posts = []

        class FakeResponse:
            status_code = 409
            text = ""

            def json(self):
                return {}

        monkeypatch.setattr(
            "requests.post",
            lambda url, **kw: posts.append(url) or FakeResponse(),
        )
        entries = [{"id": "me@gmail.com", "primary": True}]
        rc = self._run(
            monkeypatch,
            entries,
            args_overrides={"register": True},
            callback={"code": "GOOD", "state": "STATE123"},
        )
        assert rc == 0
        assert posts[-1].endswith("/api/v1/providers/gcal/accounts")

    def test_missing_register_key_exits(self, monkeypatch):
        entries = [{"id": "me@gmail.com", "primary": True}]
        with pytest.raises(SystemExit, match="API key"):
            self._run(
                monkeypatch,
                entries,
                args_overrides={"register": True, "api_key": None},
                callback={"code": "GOOD", "state": "STATE123"},
            )


class TestParser:
    def test_auth_login_parses(self):
        args = ingest.build_parser().parse_args(
            ["auth-login", "--account", "a@b.com", "--register", "--timeout", "60"]
        )
        assert args.account == "a@b.com"
        assert args.register is True
        assert args.timeout == 60

    def test_list_calendars_parses(self):
        args = ingest.build_parser().parse_args(
            ["list-calendars", "--account", "a@b.com", "--calendar-ids", "x,y"]
        )
        assert args.calendar_ids == "x,y"
