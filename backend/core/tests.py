"""Tests for core app: chunking, URL normalization, scraper helpers.

These tests are designed to run without Django/PostgreSQL dependencies.
Run with: python -m unittest core.tests -v
"""

import re
import sys
import unittest
from collections import deque
from urllib.parse import urldefrag, urljoin, urlparse

from core.html_extract import extract_title_and_text

# ---------------------------------------------------------------------------
# Re-implement pure functions locally to avoid Django/pgvector import chain.
# These mirror the logic in embeddings.py and scrape_acibadem.py exactly.
# ---------------------------------------------------------------------------

def chunk_text(text, chunk_size=700, chunk_overlap=120):
    clean = " ".join((text or "").split())
    if not clean:
        return []
    if chunk_overlap >= chunk_size:
        raise ValueError("chunk_overlap must be smaller than chunk_size")
    chunks = []
    start = 0
    step = chunk_size - chunk_overlap
    while start < len(clean):
        end = min(len(clean), start + chunk_size)
        piece = clean[start:end].strip()
        if piece:
            chunks.append(piece)
        if end >= len(clean):
            break
        start += step
    return chunks


ALLOWED_NETLOCS = frozenset({"acibadem.edu.tr"})


def is_english_path(path):
    normalized_path = (path or "/").rstrip("/").lower()
    return normalized_path == "/en" or normalized_path.startswith("/en/")


def normalize_url(url, english_only=True):
    url, _frag = urldefrag(url.strip())
    parsed = urlparse(url)
    if not parsed.scheme:
        url = "https://" + url.lstrip("/")
        parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        return ""
    host = parsed.netloc.lower()
    if host.startswith("www."):
        host = host[4:]
    if host not in ALLOWED_NETLOCS:
        return ""
    path = parsed.path or "/"
    if english_only and not is_english_path(path):
        return ""
    return f"{parsed.scheme}://{host}{path}" + (
        f"?{parsed.query}" if parsed.query else ""
    )


def same_site(url):
    try:
        host = urlparse(url).netloc.lower()
        if host.startswith("www."):
            host = host[4:]
        return host in ALLOWED_NETLOCS
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class ChunkTextTests(unittest.TestCase):
    """Tests for the text chunking algorithm."""

    def test_empty_string_returns_empty(self):
        self.assertEqual(chunk_text(""), [])

    def test_none_returns_empty(self):
        self.assertEqual(chunk_text(None), [])

    def test_whitespace_only_returns_empty(self):
        self.assertEqual(chunk_text("   \n\t  "), [])

    def test_short_text_single_chunk(self):
        chunks = chunk_text("Hello world", chunk_size=700, chunk_overlap=120)
        self.assertEqual(len(chunks), 1)
        self.assertEqual(chunks[0], "Hello world")

    def test_exact_chunk_size(self):
        chunks = chunk_text("a" * 700, chunk_size=700, chunk_overlap=120)
        self.assertEqual(len(chunks), 1)

    def test_overlap_creates_multiple_chunks(self):
        text = "word " * 200  # 1000 chars
        chunks = chunk_text(text, chunk_size=500, chunk_overlap=100)
        self.assertGreater(len(chunks), 1)

    def test_overlap_must_be_less_than_chunk_size(self):
        with self.assertRaises(ValueError):
            chunk_text("test", chunk_size=100, chunk_overlap=100)

    def test_whitespace_is_normalized(self):
        chunks = chunk_text("hello   world\n\nfoo   bar", chunk_size=700, chunk_overlap=120)
        self.assertEqual(chunks[0], "hello world foo bar")

    def test_long_text_no_empty_chunks(self):
        chunks = chunk_text("test " * 500, chunk_size=200, chunk_overlap=50)
        for chunk in chunks:
            self.assertTrue(len(chunk) > 0)

    def test_chunks_cover_all_content(self):
        text = "abcdefghij" * 10  # 100 chars
        chunks = chunk_text(text, chunk_size=30, chunk_overlap=10)
        combined = "".join(chunks)
        # All original chars should appear at least once
        for char in set(text):
            self.assertIn(char, combined)


class NormalizeUrlTests(unittest.TestCase):
    """Tests for URL normalization."""

    def test_basic_english_url(self):
        result = normalize_url("https://www.acibadem.edu.tr/en")
        self.assertEqual(result, "https://acibadem.edu.tr/en")

    def test_www_stripped(self):
        result = normalize_url("https://www.acibadem.edu.tr/en/about")
        self.assertNotIn("www.", result)

    def test_fragment_removed(self):
        result = normalize_url("https://acibadem.edu.tr/en/page#section")
        self.assertNotIn("#", result)

    def test_non_english_path_filtered(self):
        result = normalize_url("https://acibadem.edu.tr/hakkimizda", english_only=True)
        self.assertEqual(result, "")

    def test_non_english_path_allowed(self):
        result = normalize_url("https://acibadem.edu.tr/hakkimizda", english_only=False)
        self.assertNotEqual(result, "")

    def test_external_domain_rejected(self):
        result = normalize_url("https://google.com/en")
        self.assertEqual(result, "")

    def test_no_scheme_adds_https(self):
        result = normalize_url("acibadem.edu.tr/en")
        self.assertTrue(result.startswith("https://"))

    def test_ftp_rejected(self):
        result = normalize_url("ftp://acibadem.edu.tr/en")
        self.assertEqual(result, "")

    def test_empty_string(self):
        self.assertEqual(normalize_url(""), "")

    def test_query_params_preserved(self):
        result = normalize_url("https://acibadem.edu.tr/en/search?q=test")
        self.assertIn("?q=test", result)


class IsEnglishPathTests(unittest.TestCase):
    """Tests for English path detection."""

    def test_en_root(self):
        self.assertTrue(is_english_path("/en"))

    def test_en_subpath(self):
        self.assertTrue(is_english_path("/en/about"))

    def test_en_trailing_slash(self):
        self.assertTrue(is_english_path("/en/"))

    def test_turkish_path(self):
        self.assertFalse(is_english_path("/hakkimizda"))

    def test_root_path(self):
        self.assertFalse(is_english_path("/"))

    def test_similar_but_not_en(self):
        self.assertFalse(is_english_path("/energy"))

    def test_case_insensitive(self):
        self.assertTrue(is_english_path("/EN/about"))


class SameSiteTests(unittest.TestCase):
    """Tests for same-site checking."""

    def test_acibadem_is_same_site(self):
        self.assertTrue(same_site("https://acibadem.edu.tr/en"))

    def test_www_acibadem_is_same_site(self):
        self.assertTrue(same_site("https://www.acibadem.edu.tr/en"))

    def test_external_is_not_same_site(self):
        self.assertFalse(same_site("https://google.com"))

    def test_invalid_url(self):
        self.assertFalse(same_site("not a url"))

    def test_empty_string(self):
        self.assertFalse(same_site(""))


class ExtractTitleAndTextTests(unittest.TestCase):
    """Tests for HTML content extraction."""

    def test_basic_html(self):
        html = "<html><head><title>Test Page</title></head><body><p>Hello world</p></body></html>"
        title, text = extract_title_and_text(html)
        self.assertEqual(title, "Test Page")
        self.assertIn("Hello world", text)

    def test_scripts_removed(self):
        html = "<html><body><p>Content</p><script>alert('xss')</script></body></html>"
        _, text = extract_title_and_text(html)
        self.assertNotIn("alert", text)
        self.assertIn("Content", text)

    def test_styles_removed(self):
        html = "<html><body><style>.x{color:red}</style><p>Visible</p></body></html>"
        _, text = extract_title_and_text(html)
        self.assertNotIn("color", text)
        self.assertIn("Visible", text)

    def test_empty_html(self):
        title, text = extract_title_and_text("")
        self.assertEqual(title, "")

    def test_no_title(self):
        html = "<html><body><p>No title here</p></body></html>"
        title, text = extract_title_and_text(html)
        self.assertEqual(title, "")
        self.assertIn("No title here", text)

    def test_content_truncated(self):
        html = f"<html><body><p>{'x' * 10000}</p></body></html>"
        _, text = extract_title_and_text(html)
        self.assertLessEqual(len(text), 5000)

    def test_prefers_main_tag(self):
        html = "<html><body><header>Header</header><main><p>Main content</p></main><footer>Footer</footer></body></html>"
        _, text = extract_title_and_text(html)
        self.assertIn("Main content", text)

    def test_noscript_removed(self):
        html = "<html><body><noscript>Enable JS</noscript><p>Real</p></body></html>"
        _, text = extract_title_and_text(html)
        self.assertNotIn("Enable JS", text)
        self.assertIn("Real", text)


class NormalizeObsUrlTests(unittest.TestCase):
    """Tests for OBS Bologna URL normalization (no browser required)."""

    def test_showpac_absolute(self):
        from core.obs_bologna_scraper import normalize_obs_url

        base = "https://obs.acibadem.edu.tr/oibs/bologna/index.aspx?lang=en"
        u = normalize_obs_url(
            "https://obs.acibadem.edu.tr/oibs/bologna/showPac.aspx?code=1",
            base,
        )
        self.assertTrue(u)
        self.assertIn("showPac", u)

    def test_relative_bologna_path(self):
        from core.obs_bologna_scraper import normalize_obs_url

        base = "https://obs.acibadem.edu.tr/oibs/bologna/index.aspx?lang=en"
        u = normalize_obs_url("/oibs/bologna/other.aspx?x=1", base)
        self.assertTrue(u)
        self.assertIn("/oibs/bologna/", u)

    def test_rejects_non_obs_host(self):
        from core.obs_bologna_scraper import normalize_obs_url

        base = "https://obs.acibadem.edu.tr/oibs/bologna/index.aspx?lang=en"
        self.assertEqual(
            "", normalize_obs_url("https://example.com/oibs/bologna/x", base)
        )

    def test_rejects_javascript(self):
        from core.obs_bologna_scraper import normalize_obs_url

        base = "https://obs.acibadem.edu.tr/oibs/bologna/index.aspx?lang=en"
        self.assertEqual("", normalize_obs_url("javascript:void(0)", base))

    def test_strips_fragment(self):
        from core.obs_bologna_scraper import normalize_obs_url

        base = "https://obs.acibadem.edu.tr/oibs/bologna/index.aspx?lang=en"
        u = normalize_obs_url(
            "https://obs.acibadem.edu.tr/oibs/bologna/index.aspx?lang=en#foo",
            base,
        )
        self.assertNotIn("#", u)


if __name__ == "__main__":
    unittest.main()
