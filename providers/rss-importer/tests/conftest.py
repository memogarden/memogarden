"""Shared test fixtures: a real tmp-path Soil, a scripted local HTTP feed
server, and helpers that store yielded facts the way the provider runtime
does."""

import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from system.soil import Fact, Soil, generate_soil_uuid

from rss_importer.state import new_feed_state


# ---------------------------------------------------------------------------
# Scripted feed server — real HTTP over localhost, programmable per path.
# specs: {path -> {"status": int, "body": bytes, "headers": {...},
#                  "etag": str}} — when "etag" is set and the request echoes
# it via If-None-Match, the server answers 304 instead of 200.
# Every request is recorded in server.requests for header assertions.
# ---------------------------------------------------------------------------


class _ScriptedHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):  # silence test output
        pass

    def do_GET(self):
        server = self.server
        spec = dict(server.specs.get(self.path, {"status": 404}))
        server.requests.append(
            {"path": self.path, "headers": {k.lower(): v for k, v in self.headers.items()}}
        )
        status = spec.get("status", 200)
        # Conditional-GET simulation: 304 only when the client echoes the
        # exact validator we're told to expect.
        etag = spec.get("etag")
        if status == 200 and etag and self.headers.get("If-None-Match") == etag:
            self.send_response(304)
            for key, value in {"ETag": etag, **spec.get("headers", {})}.items():
                self.send_header(key, value)
            self.end_headers()
            return
        body = spec.get("body", b"")
        self.send_response(status)
        for key, value in spec.get("headers", {}).items():
            self.send_header(key, value)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if body:
            self.wfile.write(body)


class ScriptedFeedServer:
    """Programmable localhost HTTP server for wire-protocol tests."""

    def __init__(self):
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), _ScriptedHandler)
        self.httpd.specs = {}
        self.httpd.requests = []
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)

    def start(self):
        self.thread.start()
        return self

    @property
    def base(self) -> str:
        host, port = self.httpd.server_address
        return f"http://{host}:{port}"

    def url(self, path: str) -> str:
        return f"{self.base}{path}"

    def set(self, path: str, **spec) -> str:
        self.httpd.specs[path] = spec
        return self.url(path)

    @property
    def requests(self) -> list:
        return self.httpd.requests

    def stop(self):
        self.httpd.shutdown()
        self.httpd.server_close()


@pytest.fixture
def feed_server():
    server = ScriptedFeedServer().start()
    try:
        yield server
    finally:
        server.stop()


# ---------------------------------------------------------------------------
# Soil + provider helpers (tada conftest pattern)
# ---------------------------------------------------------------------------


@pytest.fixture
def soil(tmp_path):
    s = Soil(str(tmp_path / "soil.db"))
    with s:
        s.init_schema()
    yield s


@pytest.fixture
def state_path(tmp_path):
    return str(tmp_path / "rss-feed-state.json")


@pytest.fixture
def artifacts_dir(tmp_path):
    return str(tmp_path / "attachments")


def make_provider(soil, state_path, **config):
    """An RssProvider wired to a tmp Soil and tmp state file, with test-speed
    politeness gaps."""
    from rss_importer.provider import RssProvider

    return RssProvider(
        config={
            "soil_path": soil.db_path,
            "state_path": state_path,
            "inter_request_sleep": 0.0,
            "same_host_gap_s": 0.0,
            **config,
        }
    )


def make_subscription(soil, xml_url, title="T", categories=None):
    """Store one Subscription fact the way the /mg add verb would."""
    fact = Fact(
        uuid=generate_soil_uuid(),
        _type="Subscription",
        realized_at="2026-11-16T09:00:00+00:00",
        canonical_at="2026-11-16T09:00:00+00:00",
        data={
            "xml_url": xml_url,
            "title": title,
            **({"categories": categories} if categories else {}),
        },
        metadata={"source": "opml", "imported_at": "2026-11-16T09:00:00+00:00"},
        integrity_hash=None,
        fidelity="full",
    )
    with soil as s:
        s.create_fact(fact)
    return fact


def force_due(provider, url):
    """Clear the per-feed schedule so the next sync() actually polls it —
    the normal cadence is 1h+, tests drive sweeps back-to-back."""
    states = provider._states.load()
    states.setdefault(url, new_feed_state())["next_due_at"] = None
    provider._states.save(states)


def store_fact(soil, yielded):
    """Persist a provider-yielded dict the way api.provider_runtime._to_fact
    does, and return the stored Fact (for create_relations calls)."""
    fact = Fact(
        uuid=generate_soil_uuid(),
        _type=yielded["type"],
        realized_at=yielded.get("realized_at") or "2026-11-16T10:00:00+00:00",
        canonical_at=yielded.get("canonical_at") or "2026-11-16T10:00:00+00:00",
        data=yielded.get("data", {}),
        metadata=yielded.get("metadata", {}),
        integrity_hash=None,
        fidelity="full",
    )
    with soil as s:
        s.create_fact(fact)
    return fact


def run_sync(provider, soil):
    """Consume one sweep, committing each yield exactly like the runtime
    (per-fact commit + create_relations). Returns the stored facts."""
    stored = []
    for yielded in provider.sync():
        fact = store_fact(soil, yielded)
        stored.append(fact)
        for relation in provider.create_relations(fact) or []:
            with soil as s:
                s.create_relation(relation)
    return stored


def rss_body(*items: str, ttl: int | None = None) -> bytes:
    """Minimal RSS 2.0 with the given <item> XML fragments."""
    ttl_xml = f"<ttl>{ttl}</ttl>" if ttl is not None else ""
    return (
        "<?xml version=\"1.0\"?>\n<rss version=\"2.0\"><channel>"
        "<title>Test Feed</title><link>http://test.example/</link>"
        "<description>test</description>"
        + ttl_xml
        + "".join(items)
        + "</channel></rss>"
    ).encode()


def rss_item(guid="urn:1", title="Item one", link="http://test.example/1",
             pubdate="Mon, 02 Jan 2006 15:04:05 GMT", description="<p>one</p>") -> str:
    return (
        f"<item><guid isPermaLink=\"false\">{guid}</guid>"
        f"<title>{title}</title><link>{link}</link>"
        f"<pubDate>{pubdate}</pubDate>"
        f"<description>{description}</description></item>"
    )
