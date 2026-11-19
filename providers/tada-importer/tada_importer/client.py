"""HTTP client for `tada serve`'s command bridge.

tada may live on this host, another LAN machine, or a cloud host — the
importer works only through the HTTP API (POST /cmd, GET /health), never
through the store files. tada's command bridge currently has no auth; pointing
base_url beyond localhost is the operator's call until tada grows credentials.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request


class TadaApiError(Exception):
    """Raised when the tada API is unreachable or rejects a command."""

    def __init__(self, message: str, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code


class TadaApiClient:
    """Thin client over tada's `{"op": ..., "args": {...}}` wire protocol."""

    def __init__(self, base_url: str, timeout: float = 30.0):
        self._base = base_url.rstrip("/")
        self._timeout = timeout

    # ------------------------------------------------------------------
    # Wire
    # ------------------------------------------------------------------

    def _cmd(self, op: str, args: dict) -> dict:
        body = json.dumps({"op": op, "args": args}).encode()
        req = urllib.request.Request(
            f"{self._base}/cmd",
            data=body,
            headers={"content-type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=self._timeout) as resp:
                payload = json.load(resp)
        except urllib.error.HTTPError as e:
            try:
                detail = json.load(e).get("error", str(e))
            except (ValueError, AttributeError):
                detail = str(e)
            raise TadaApiError(f"{op} failed: {detail}", status_code=e.code) from e
        except urllib.error.URLError as e:
            raise TadaApiError(f"{op} failed: {e.reason}") from e
        if not payload.get("ok"):
            raise TadaApiError(f"{op} failed: {payload.get('error')}")
        return payload["result"]

    # ------------------------------------------------------------------
    # Reads (the only ops the importer uses)
    # ------------------------------------------------------------------

    def health(self) -> dict:
        try:
            with urllib.request.urlopen(
                f"{self._base}/health", timeout=self._timeout
            ) as resp:
                return json.load(resp)
        except urllib.error.HTTPError as e:
            raise TadaApiError("health failed", status_code=e.code) from e
        except urllib.error.URLError as e:
            raise TadaApiError(f"health failed: {e.reason}") from e

    def zone(self) -> str:
        """The store's IANA zone — snapshot wall times are naive in it."""
        return self.health()["zone"]

    def list_day(self, day: str) -> list[dict]:
        """Snapshot entries (done + cancelled) for a local calendar day."""
        result = self._cmd("list", {"day": day})
        return (result.get("day") or {}).get("done") or []

    def show(self, ref: str) -> dict | None:
        """Full item for a uid prefix or full uid; None when not found.

        Resolves both live items and snapshot-only entries; returns the item
        dict (notes, followedFrom, source) when the store has one.
        """
        try:
            result = self._cmd("show", {"ref": ref})
        except TadaApiError as e:
            if e.status_code == 404:
                return None
            raise
        item = result.get("item")
        return item if isinstance(item, dict) else None

    def inventory(self, resolved_within: int = 2) -> dict:
        """Open items + trailing-window resolutions + every series template.

        The tada `inventory` op (read-only) — the drain surface for the
        entity lane. result: {"items": [...], "templates": [...]}.
        """
        return self._cmd("inventory", {"resolvedWithin": resolved_within})
