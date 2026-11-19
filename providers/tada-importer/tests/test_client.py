"""Wire-protocol tests for TadaApiClient against a real local HTTP server."""

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest
from tada_importer.client import TadaApiClient, TadaApiError


class _Handler(BaseHTTPRequestHandler):
    def _respond(self, status, body):
        data = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("content-type", "application/json")
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path == "/health":
            self._respond(200, {"ok": True, "zone": "Asia/Singapore"})
        else:
            self._respond(404, {"ok": False, "error": "no such file"})

    def do_POST(self):
        length = int(self.headers.get("content-length", 0))
        cmd = json.loads(self.rfile.read(length))
        canned = self.server.canned.get(cmd["op"])
        if canned is None:
            self._respond(canned_status(cmd["op"]), {"ok": False, "error": "unscripted"})
            return
        status, result = canned
        if status == 200:
            self._respond(200, {"ok": True, "result": result})
        else:
            self._respond(status, {"ok": False, "error": result})

    def log_message(self, *args):
        pass


def canned_status(op):
    return 404 if op == "show" else 400


@pytest.fixture
def server():
    httpd = HTTPServer(("127.0.0.1", 0), _Handler)
    httpd.canned = {}
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield httpd
    httpd.shutdown()


def client_for(server):
    return TadaApiClient(f"http://127.0.0.1:{server.server_port}", timeout=5.0)


def test_health_and_zone(server):
    client = client_for(server)
    assert client.health()["ok"] is True
    assert client.zone() == "Asia/Singapore"


def test_list_day_parses_day_done(server):
    server.canned["list"] = (200, {"type": "day", "day": {"date": "2026-11-06", "done": [
        {"uid": "u1", "outcome": "done", "at": "2026-11-06T10:00"}]}})
    entries = client_for(server).list_day("2026-11-06")
    assert [e["uid"] for e in entries] == ["u1"]


def test_list_day_empty_day_is_empty_list(server):
    server.canned["list"] = (200, {"type": "day", "day": {"date": "1999-01-01", "done": []}})
    assert client_for(server).list_day("1999-01-01") == []


def test_cmd_error_raises_with_message(server):
    server.canned["list"] = (400, "date must be YYYY-MM-DD")
    with pytest.raises(TadaApiError, match="date must be"):
        client_for(server).list_day("nope")


def test_show_returns_item(server):
    server.canned["show"] = (200, {"type": "show", "item": {"uid": "u1", "notes": "n"}})
    assert client_for(server).show("u1")["uid"] == "u1"


def test_show_404_is_none(server):
    server.canned["show"] = (404, "no such item")
    assert client_for(server).show("ghost") is None


def test_unreachable_server_raises_tada_error():
    # port 1 on localhost: nothing listens there
    client = TadaApiClient("http://127.0.0.1:1", timeout=1.0)
    with pytest.raises(TadaApiError):
        client.list_day("2026-11-06")


def test_inventory_posts_resolved_within(server):
    server.canned["inventory"] = (200, {"items": [{"uid": "u1"}], "templates": []})
    result = client_for(server).inventory(resolved_within=3)
    assert result["items"][0]["uid"] == "u1"
