"""Feed-parsing and fact-building tests — identity chain, date
normalization, content selection, revision + artifact builders."""

from rss_importer import translate

RSS_ONE = (
    "<?xml version=\"1.0\"?><rss version=\"2.0\"><channel>"
    "<title>Test Feed</title><link>http://test.example/</link><description>d</description>"
    "<item><guid isPermaLink=\"false\">urn:one</guid><title>Item one</title>"
    "<link>http://test.example/1</link>"
    "<pubDate>Mon, 02 Jan 2006 15:04:05 -0700</pubDate>"
    "<description>&lt;p&gt;one&lt;/p&gt;</description>"
    "<enclosure url=\"http://test.example/1.mp3\" type=\"audio/mpeg\" length=\"100\"/>"
    "</item></channel></rss>"
).encode()

ATOM_ONE = (
    "<?xml version=\"1.0\"?>"
    "<feed xmlns=\"http://www.w3.org/2005/Atom\"><title>A Feed</title>"
    "<link rel=\"alternate\" href=\"/blog/\"/>"
    "<entry><id>tag:test.example,2026:1</id><title>Entry one</title>"
    "<link rel=\"alternate\" href=\"/blog/posts/1\"/>"
    "<updated>2026-11-01T10:00:00Z</updated>"
    "<author><name>Someone</name></author>"
    "<content type=\"html\">&lt;b&gt;full&lt;/b&gt;</content>"
    "</entry></feed>"
).encode()


def parse(body):
    return translate.parse_feed(body, "http://test.example/rss.xml")


# ---------------------------------------------------------------------------
# Identity chain (§4.3)
# ---------------------------------------------------------------------------


class TestIdentity:
    def test_guid_wins_and_stays_opaque(self):
        parsed = parse(RSS_ONE)
        # isPermaLink=false guid is an opaque string — used as-is
        assert translate.entry_identity(parsed.entries[0]) == "urn:one"

    def test_perma_guid_is_a_url_but_still_a_string(self):
        body = (
            "<?xml version=\"1.0\"?><rss version=\"2.0\"><channel>"
            "<title>T</title><link>http://t.example/</link><description>d</description>"
            "<item><guid>http://t.example/perma</guid><title>x</title></item>"
            "</channel></rss>"
        ).encode()
        # default isPermaLink=true: the guid IS a URL — compared as a string,
        # never fetched or resolved (RFC: aggregators view guids as strings)
        assert translate.entry_identity(parse(body).entries[0]) == "http://t.example/perma"

    def test_fallback_to_normalized_link(self):
        entry = {"id": None, "link": "HTTP://Example.COM:80/Post/?a=1#frag"}
        assert translate.entry_identity(entry) == "http://example.com:80/Post/?a=1"

    def test_last_resort_hash_marked(self):
        entry = {"id": None, "link": None, "title": "T", "published": "2026-01-01"}
        assert translate.entry_identity(entry).startswith("hash:")


# ---------------------------------------------------------------------------
# Dates (ISO 8601 UTC, lexicographically comparable)
# ---------------------------------------------------------------------------


class TestDates:
    def test_rfc822_with_offset_normalizes_to_utc(self):
        published, updated = translate.entry_dates(parse(RSS_ONE).entries[0])
        assert published == "2006-01-02T22:04:05+00:00"
        # feedparser's issue-310 compat shim maps updated_parsed →
        # published_parsed when the feed has no updated date, so a pure-RSS
        # item reports updated == published (feeds with dcterms:modified get
        # a real, distinct updated)
        assert updated == published

    def test_atom_dates(self):
        published, updated = translate.entry_dates(parse(ATOM_ONE).entries[0])
        assert published is None
        assert updated == "2026-11-01T10:00:00+00:00"

    def test_unparseable_dates_do_not_raise(self):
        _, updated = translate.entry_dates({"updated": "not a date"})
        assert updated is None


# ---------------------------------------------------------------------------
# Entry fact
# ---------------------------------------------------------------------------


class TestEntryFact:
    def test_rss_fact_fields(self):
        fact = translate.entry_fact(
            parse(RSS_ONE).entries[0], "http://test.example/rss.xml",
            "Test Feed", "2026-11-16T00:00:00+00:00",
        )
        data = fact["data"]
        assert data["entry_id"] == "urn:one"
        assert data["title"] == "Item one"
        assert data["link"] == "http://test.example/1"
        assert data["published"] == "2006-01-02T22:04:05+00:00"
        assert data["description"] == "<p>one</p>"
        assert data["content_type"] == "text/html"
        assert data["content_hash"] == translate.content_hash("<p>one</p>")
        assert data["enclosures"] == [
            {"url": "http://test.example/1.mp3", "type": "audio/mpeg", "length": "100"}
        ]
        assert fact["metadata"] == {
            "source": "rss", "feed": "http://test.example/rss.xml",
            "feed_title": "Test Feed",
        }
        assert fact["canonical_at"] == "2006-01-02T22:04:05+00:00"

    def test_atom_content_preferred_and_relative_links_resolved(self):
        fact = translate.entry_fact(
            parse(ATOM_ONE).entries[0], "http://test.example/rss.xml",
            None, "2026-11-16T00:00:00+00:00",
        )
        # full content wins over summary; xml:base-less relative link
        # resolves against the feed URL (via response headers)
        assert fact["data"]["description"] == "<b>full</b>"
        assert fact["data"]["link"] == "http://test.example/blog/posts/1"
        assert fact["data"]["published"] == "2026-11-01T10:00:00+00:00"  # updated fallback
        assert fact["data"]["author"] == "Someone"
        assert fact["metadata"]["source"] == "rss"
        assert "feed_title" not in fact["metadata"]

    def test_bozo_flag_recorded(self):
        fact = translate.entry_fact(
            parse(RSS_ONE).entries[0], "http://f.example/x", None,
            "2026-11-16T00:00:00+00:00", bozo=True,
        )
        assert fact["metadata"]["bozo"] is True

    def test_empty_entry_yields_minimal_stable_fact(self):
        fact = translate.entry_fact(
            {"id": "urn:bare"}, "http://f.example/x", None,
            "2026-11-16T00:00:00+00:00",
        )
        assert fact["data"] == {"entry_id": "urn:bare"}
        # None-valued fields are dropped so hashes stay stable across parses
        assert "title" not in fact["data"]


# ---------------------------------------------------------------------------
# Revision notes + artifacts
# ---------------------------------------------------------------------------


class TestRevisionAndArtifacts:
    def test_revision_note_carries_new_content(self):
        note = translate.revision_note_fact(
            parse(ATOM_ONE).entries[0], "http://f.example/x",
            "2026-11-16T01:00:00+00:00",
        )
        assert note["type"] == "Note"
        assert note["data"]["description"] == "<b>full</b>"
        assert note["data"]["revised_at"] == "2026-11-01T10:00:00+00:00"
        assert note["metadata"] == {
            "source": "rss", "feed": "http://f.example/x",
            "entry_id": "tag:test.example,2026:1", "revision": True,
        }
        assert note["canonical_at"] == "2026-11-01T10:00:00+00:00"

    def test_feed_response_attachment(self):
        fact = translate.feed_response_attachment_fact(
            "http://f.example/x", "F", "2026-11-16T01:00:00+00:00",
            {"sha256": "ab" * 32, "size": 5, "content_path": "/tmp/x.xml"},
        )
        assert fact["type"] == "Attachment"
        assert fact["metadata"]["artifact"] == "feed-response"
        assert fact["metadata"]["feed"] == "http://f.example/x"
        assert fact["data"]["content_path"] == "/tmp/x.xml"
        assert fact["data"]["size"] == 5

    def test_article_attachment_points_at_its_entry(self):
        fact = translate.article_attachment_fact(
            "http://f.example/x", "urn:one", "http://f.example/post/1",
            "2026-11-16T01:00:00+00:00",
            {"sha256": "cd" * 32, "size": 9, "content_path": "/tmp/a.html"},
        )
        assert fact["metadata"]["artifact"] == "article"
        assert fact["metadata"]["entry_id"] == "urn:one"
        assert fact["data"]["mime_type"] == "text/html"


# ---------------------------------------------------------------------------
# Parse-level robustness
# ---------------------------------------------------------------------------


class TestParsing:
    def test_valid_feed_is_not_bozo(self):
        # regression: passing response headers without a content-type made
        # feedparser flag valid XML as broken
        assert not parse(RSS_ONE).bozo

    def test_broken_feed_sets_bozo_but_recovers_entries(self):
        broken = RSS_ONE.replace(b"</channel>", b"<oops></channel>")
        parsed = parse(broken)
        # feedparser's bozo is 1, not True — assert truthiness
        assert parsed.bozo
        assert len(parsed.entries) == 1

    def test_unparseable_feed_yields_no_entries(self):
        parsed = parse(b"this is not xml <at all")
        assert parsed.bozo
        assert parsed.entries == []

    def test_feed_title_extracted(self):
        assert translate.feed_title_of(parse(RSS_ONE)) == "Test Feed"
