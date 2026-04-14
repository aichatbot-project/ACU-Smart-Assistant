# pyright: reportMissingModuleSource=false
"""Fetch pages from acibadem.edu.tr into core.Page."""
import time
from collections import deque
from urllib.parse import urldefrag, urljoin, urlparse
from urllib.robotparser import RobotFileParser
from typing import Any

import requests
from bs4 import BeautifulSoup
from django.core.management.base import BaseCommand

from core.html_extract import extract_title_and_text
from core.models import Page

SOURCE_LABEL = "acibadem.edu.tr"
DEFAULT_SEEDS = (
    "https://www.acibadem.edu.tr/en",
    "https://www.acibadem.edu.tr/en/node",
    "https://www.acibadem.edu.tr/en/news",
    "https://www.acibadem.edu.tr/en/announcements",
    # Registration contact/transport — often not reached within default depth/page limits from home alone.
    "https://www.acibadem.edu.tr/en/kayit/iletisim/ulasim",
    "https://www.acibadem.edu.tr/en/academic/undergraduate-programs/faculty-of-engineering-and-natural-sciences/departments/computer-engineering/message-from-head-of-department",
    "https://www.acibadem.edu.tr/en/academic/undergraduate-programs/faculty-of-engineering-and-natural-sciences/departments/computer-engineering/about",
    "https://www.acibadem.edu.tr/en/academic/undergraduate-programs/faculty-of-engineering-and-natural-sciences/departments/computer-engineering/academic-staff",
    "https://www.acibadem.edu.tr/en/academic/undergraduate-programs/faculty-of-engineering-and-natural-sciences/departments/computer-engineering/commissions",
)
ALLOWED_NETLOCS = frozenset(
    {
        "acibadem.edu.tr",
    }
)
USER_AGENT = (
    "ACU-Smart-Assistant/0.1 (+university project; respectful crawl; contact: student)"
)
REQUEST_TIMEOUT = 25


def is_english_path(path: str) -> bool:
    normalized_path = (path or "/").rstrip("/").lower()
    return normalized_path == "/en" or normalized_path.startswith("/en/")


def normalize_url(url: str, english_only: bool = True) -> str:
    url, _frag = urldefrag(url.strip())
    parsed = urlparse(url)
    if not parsed.scheme:
        url = "https://" + url.lstrip("/")
        parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        return ""
    host = parsed.netloc.lower()
    # Normalize host so "www." and non-"www" URLs collapse to the same record.
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


def same_site(url: str) -> bool:
    try:
        host = urlparse(url).netloc.lower()
        if host.startswith("www."):
            host = host[4:]
        return host in ALLOWED_NETLOCS
    except Exception:
        return False


def load_robot_parser(base: str) -> RobotFileParser | None:
    robots_url = urljoin(base, "/robots.txt")
    rp = RobotFileParser()
    rp.set_url(robots_url)
    try:
        rp.read()
        return rp
    except Exception:
        return None


class Command(BaseCommand):
    help = (
        "Fetch public pages from www.acibadem.edu.tr via requests + BeautifulSoup "
        "and store them in core.Page (polite delays; same host only)."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--crawl",
            action="store_true",
            help="Follow same-site links starting from seed URLs (bounded).",
        )
        parser.add_argument(
            "--max-pages",
            type=int,
            default=40,
            help=(
                "Max pages to fetch with --crawl (default: 40). Use 0 for no cap "
                "(until the same-site /en queue is empty; respects robots)."
            ),
        )
        parser.add_argument(
            "--depth",
            type=int,
            default=2,
            help=(
                "Max link hops from seeds with --crawl (default: 2). Use -1 for no cap. "
                "Use 0 for seeds only."
            ),
        )
        parser.add_argument(
            "--delay",
            type=float,
            default=1.5,
            help="Seconds to sleep between HTTP requests (default: 1.5).",
        )
        parser.add_argument(
            "--ignore-robots",
            action="store_true",
            help="Do not check robots.txt (not recommended).",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Fetch and parse but do not write to the database.",
        )
        parser.add_argument(
            "--allow-non-english",
            action="store_true",
            help="Allow crawling non-English paths (disabled by default).",
        )

    def handle(self, *args, **options):
        # Django provides these dynamically at runtime, but type checkers often
        # don't have enough metadata for `self.style` and `Model.objects`.
        style: Any = self.style
        page_objects: Any = getattr(Page, "objects")

        delay: float = max(0.0, options["delay"])
        dry_run: bool = options["dry_run"]
        crawl: bool = options["crawl"]
        raw_max_pages = int(options["max_pages"])
        unlimited_pages = raw_max_pages == 0
        max_pages: int = max(1, raw_max_pages) if not unlimited_pages else 0
        raw_depth = int(options["depth"])
        max_depth: int | None
        if raw_depth == -1:
            max_depth = None
        else:
            max_depth = max(0, raw_depth)
        ignore_robots: bool = options["ignore_robots"]
        english_only: bool = not options["allow_non_english"]

        session = requests.Session()
        session.headers.update(
            {
                "User-Agent": USER_AGENT,
                "Accept": "text/html,application/xhtml+xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "en-US,en;q=0.9,tr;q=0.5",
            }
        )

        rp = (
            None
            if ignore_robots
            else load_robot_parser("https://acibadem.edu.tr/")
        )
        if not ignore_robots and rp is None:
            self.stdout.write(
                style.WARNING(
                    "Could not load robots.txt; continuing without robots checks."
                )
            )

        seeds = [normalize_url(u, english_only=english_only) for u in DEFAULT_SEEDS]
        seeds = [u for u in seeds if u]
        if not seeds:
            self.stderr.write(style.ERROR("No valid seed URLs."))
            return

        if crawl and (unlimited_pages or max_depth is None):
            self.stdout.write(
                style.WARNING(
                    "Full English crawl: no page/depth cap — runs until the BFS queue is empty "
                    "(same host, /en/ only unless --allow-non-english). May take a long time."
                )
            )

        if crawl:
            queue: deque[tuple[str, int]] = deque((u, 0) for u in seeds)
        else:
            queue = deque((u, 0) for u in seeds)

        visited: set[str] = set()
        saved = 0
        fetched = 0

        while queue and (unlimited_pages or fetched < max_pages):
            url, depth = queue.popleft()
            if url in visited:
                continue
            visited.add(url)

            if rp is not None and not rp.can_fetch(USER_AGENT, url):
                self.stdout.write(style.WARNING(f"robots.txt disallows: {url}"))
                continue

            try:
                time.sleep(delay)
                resp = session.get(url, timeout=REQUEST_TIMEOUT)
                resp.raise_for_status()
            except requests.RequestException as exc:
                self.stdout.write(style.WARNING(f"GET failed {url}: {exc}"))
                continue

            ctype = (resp.headers.get("Content-Type") or "").lower()
            if "text/html" not in ctype and "application/xhtml" not in ctype:
                self.stdout.write(style.WARNING(f"Skip non-HTML: {url}"))
                continue

            fetched += 1
            html = resp.text
            title, text = extract_title_and_text(html)
            if not title:
                title = url[:500]
            if not text:
                self.stdout.write(style.WARNING(f"Empty body text: {url}"))
                text = ""

            if dry_run:
                self.stdout.write(f"[dry-run] {url} | {title[:80]!r} | {len(text)} chars")
            else:
                page_objects.update_or_create(
                    url=url,
                    defaults={
                        "title": title,
                        "content": text,
                        "source": SOURCE_LABEL,
                    },
                )
                saved += 1
                self.stdout.write(style.SUCCESS(f"Saved: {url}"))

            if not crawl or (max_depth is not None and depth >= max_depth):
                continue

            soup = BeautifulSoup(html, "lxml")
            for a in soup.find_all("a", href=True):
                href = a["href"].strip()
                if href.startswith(("mailto:", "tel:", "javascript:")):
                    continue
                next_url = normalize_url(urljoin(url, href), english_only=english_only)
                if not next_url or next_url in visited:
                    continue
                if not same_site(next_url):
                    continue
                queue.append((next_url, depth + 1))

        self.stdout.write(
            style.NOTICE(
                f"Done. Fetched={fetched}, DB rows touched={saved if not dry_run else 0}, "
                f"crawl={'on' if crawl else 'off'}."
            )
        )
