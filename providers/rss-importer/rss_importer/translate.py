"""Translate parsed feeds into MemoGarden facts.

Everything here is deterministic from (parsed feed, feed URL, existing-state
inputs) so Entry.data stays hash-stable across polls: first-seen wins, and
publisher edits are expressed as separate revision Note facts rather than
mutations. Identity follows the research doc §4.3 chain; dates normalize to
ISO 8601 UTC (+00:00 — lexicographic comparability is load-bearing for
revision detection).
"""

from __future__ import annotations

import hashlib
import re
from datetime import datetime, timezone
from urllib.parse import urlsplit, urlunsplit

import feedparser


# ---------------------------------------------------------------------------
# Dates
# ---------------------------------------------------------------------------


def _iso_from_struct(struct) -> str | None:
    """feedparser's *_parsed struct_time (already normalized to UTC) →
    ISO 8601 with explicit +00:00 offset."""
    if not struct:
        return None
    try:
        dt = datetime(*struct[:6], tzinfo=timezone.utc)
    except (ValueError, TypeError):
        return None
    return dt.isoformat()


def _iso_from_string(value: str | None) -> str | None:
    """Fallback for feeds where *_parsed is missing but the raw string
    exists — feedparser parses a wide zoo of date formats; we ask it via
    its own parser (private API, so defensively probed)."""
    if not value:
        return None
    parse = getattr(feedparser.datetimes, "_parse_date", None)
    if parse is None:
        return None
    try:
        parsed = parse(value)
    except Exception:  # noqa: BLE001 — date fallback must never raise
        return None
    if not parsed:
        return None
    return _iso_from_struct(parsed)


def entry_dates(entry) -> tuple[str | None, str | None]:
    """(published, updated) as ISO 8601 UTC. `updated` is preferred for the
    canonical published slot when the feed only stamps one time (Atom feeds
    without atom:published) — that mirrors the schema's canonical_at rule."""
    published = _iso_from_struct(entry.get("published_parsed")) or _iso_from_string(
        entry.get("published")
    )
    updated = _iso_from_struct(entry.get("updated_parsed")) or _iso_from_string(
        entry.get("updated")
    )
    return published, updated


# ---------------------------------------------------------------------------
# Identity (research doc §4.3)
# ---------------------------------------------------------------------------


def normalize_link(link: str | None) -> str | None:
    """Link normalization for the fallback identity path: lowercase
    scheme/host, strip fragment. (Never applied to guids — RFC 4287 §4.2.6
    requires char-by-char id comparison and RSS guids are opaque strings.)"""
    if not link:
        return None
    link = link.strip()
    parts = urlsplit(link)
    if not parts.scheme or not parts.netloc:
        return None
    return urlunsplit(
        (
            parts.scheme.lower(),
            parts.netloc.lower(),
            parts.path,
            parts.query,
            "",  # fragment stripped
        )
    )


def entry_identity(entry) -> str:
    """The stable per-feed item id. Chain: guid/atom:id/rdf:about (feedparser
    maps all three to entry.id; compared as an opaque string, never fetched
    or resolved even when isPermaLink makes it URL-shaped) → normalized link
    → hash(title + published) as a last resort (marked unstable by the
    schema docs)."""
    guid = (entry.get("id") or "").strip()
    if guid:
        return guid
    normalized = normalize_link(entry.get("link"))
    if normalized:
        return normalized
    title = (entry.get("title") or "").strip()
    published = entry.get("published") or entry.get("updated") or ""
    digest = hashlib.sha1(f"{title}\n{published}".encode("utf-8")).hexdigest()
    return f"hash:{digest}"


# ---------------------------------------------------------------------------
# Content
# ---------------------------------------------------------------------------

# Publishers mislabel HTML as text/plain constantly (RSS 2.0 leaves
# <description> ambiguous and some generators omit the type attribute where
# feedparser then defaults to text/plain). When the declared type is
# text-ish but the value carries markup, trust the content over the label.
_HTML_SNIFF_RE = re.compile(
    r"<(p|div|br|a|img|span|ul|ol|li|table|tr|td|th|h[1-6]|blockquote|pre"
    r"|figure|em|strong|b|i|u|code|hr|font)(\s[^>]*)?/?>",
    re.IGNORECASE,
)


def looks_like_html(text: str) -> bool:
    """True when the value carries HTML markup. Only common element names
    with tag syntax — `a < b` and `&lt;p&gt;` (properly escaped markup in a
    genuinely plain-text field) never match."""
    return bool(_HTML_SNIFF_RE.search(text))


def entry_content(entry) -> tuple[str, str]:
    """(description, content_type). Atom content (full) preferred over
    summary; content:encoded flows through content[] too (feedparser maps it
    when the namespace handler runs). Verbatim, untruncated — untrusted
    third-party HTML by definition.

    A declared text/plain that actually contains markup is corrected to
    text/html (2026-11-19: the godotengine.org feed ships full HTML in a
    bare <description> and feedparser tags it text/plain, which the reader
    then rendered as escaped text)."""
    content_list = entry.get("content") or []
    if content_list:
        first = content_list[0]
        text = first.get("value") or ""
        ctype = first.get("type") or "text/html"
    else:
        text = entry.get("summary") or ""
        ctype = (entry.get("summary_detail") or {}).get("type") or "text/html"
    if ctype != "text/html" and looks_like_html(text):
        ctype = "text/html"
    return text, ctype


def entry_enclosures(entry) -> list[dict]:
    """Enclosure pointers only — never downloaded. feedparser exposes
    {href, type, length}; we normalize to the schema's {url, type, length}."""
    result = []
    for enc in entry.get("enclosures") or []:
        url = enc.get("href") or enc.get("url")
        if not url:
            continue
        item: dict = {"url": url}
        if enc.get("type"):
            item["type"] = enc["type"]
        if enc.get("length"):
            item["length"] = str(enc["length"])
        result.append(item)
    return result


def content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Fact builders
# ---------------------------------------------------------------------------


def entry_fact(
    entry,
    feed_url: str,
    feed_title: str | None,
    realized_at: str,
    bozo: bool = False,
) -> dict:
    """Build one Entry-fact yield dict (first-seen frozen)."""
    published, updated = entry_dates(entry)
    description, ctype = entry_content(entry)
    data: dict = {
        "entry_id": entry_identity(entry),
        "title": (entry.get("title") or "").strip() or None,
        "link": normalize_link(entry.get("link")) or entry.get("link"),
        "published": published or updated,
        "updated": updated,
        "author": (entry.get("author") or "").strip() or None,
        "description": description or None,
        "content_type": ctype if description else None,
        "content_hash": content_hash(description) if description else None,
        "enclosures": entry_enclosures(entry) or None,
    }
    data = {k: v for k, v in data.items() if v is not None}
    metadata: dict = {"source": "rss", "feed": feed_url}
    if feed_title:
        metadata["feed_title"] = feed_title
    if bozo:
        metadata["bozo"] = True
    return {
        "type": "Entry",
        "data": data,
        "metadata": metadata,
        "canonical_at": published or updated or realized_at,
        "realized_at": realized_at,
    }


def revision_note_fact(
    entry,
    feed_url: str,
    realized_at: str,
) -> dict:
    """Build one revision Note fact for a publisher edit (known entry_id,
    newer `updated`, different content hash). The note carries the new
    content; the Entry itself stays frozen (decision 2026-11-16)."""
    description, ctype = entry_content(entry)
    _, updated = entry_dates(entry)
    data = {
        "description": description or "",
        "revised_at": updated,
        "content_type": ctype if description else None,
    }
    data = {k: v for k, v in data.items() if v is not None}
    return {
        "type": "Note",
        "data": data,
        "metadata": {
            "source": "rss",
            "feed": feed_url,
            "entry_id": entry_identity(entry),
            "revision": True,
        },
        "canonical_at": updated or realized_at,
        "realized_at": realized_at,
    }


def feed_response_attachment_fact(
    feed_url: str,
    feed_title: str | None,
    fetched_at: str,
    stored: dict,
) -> dict:
    """Build the Attachment fact for an archived raw feed response — the
    gmail full-email analogue ("as served by the publisher at time T").
    `stored` comes from store.store_bytes ({sha256, size, content_path})."""
    return {
        "type": "Attachment",
        "data": {
            "filename": f"feed-response-{stored['sha256'][:12]}.xml",
            "mime_type": "application/xml",
            "size": stored["size"],
            "sha256": stored["sha256"],
            "title": feed_title or feed_url,
            "content_path": stored["content_path"],
        },
        "metadata": {
            "source": "rss",
            "feed": feed_url,
            "artifact": "feed-response",
            "fetched_at": fetched_at,
        },
        "canonical_at": fetched_at,
        "realized_at": fetched_at,
    }


def article_attachment_fact(
    feed_url: str,
    entry_id: str,
    entry_link: str,
    realized_at: str,
    stored: dict,
) -> dict:
    """Build the Attachment fact for a full-fetched article page — a separate
    document linked contained_by its Entry, never merged into Entry.data
    (decision 2026-11-16)."""
    return {
        "type": "Attachment",
        "data": {
            "filename": f"article-{stored['sha256'][:12]}.html",
            "mime_type": "text/html",
            "size": stored["size"],
            "sha256": stored["sha256"],
            "title": entry_link,
            "content_path": stored["content_path"],
        },
        "metadata": {
            "source": "rss",
            "feed": feed_url,
            "entry_id": entry_id,
            "artifact": "article",
            "fetched_at": realized_at,
        },
        "canonical_at": realized_at,
        "realized_at": realized_at,
    }


# ---------------------------------------------------------------------------
# Feed-level parsing
# ---------------------------------------------------------------------------


def parse_feed(body: bytes, feed_url: str):
    """feedparser.parse with response headers so relative links resolve
    against the feed URL (documented feedparser pattern). The content-type
    must accompany content-location or feedparser sets bozo with
    'no Content-type specified' on perfectly valid XML."""
    return feedparser.parse(
        body,
        response_headers={
            "content-type": "application/xml",
            "content-location": feed_url,
        },
    )


def feed_title_of(parsed) -> str | None:
    title = (getattr(parsed, "feed", {}) or {}).get("title")
    return title.strip() if title else None
