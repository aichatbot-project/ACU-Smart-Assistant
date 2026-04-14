# pyright: reportMissingModuleSource=false
"""Scrape OBS Bologna (JavaScript-driven) into core.Page via Selenium + headless Chrome."""
from __future__ import annotations

import logging
from typing import Any

from django.core.management.base import BaseCommand

from core.models import Page
from core.obs_bologna_scraper import (
    SOURCE_LABEL,
    build_driver,
    collect_bologna_urls,
    fetch_page_extract,
)

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = (
        "Scrape obs.acibadem.edu.tr Bologna pages with Selenium (headless Chrome) "
        "and store them in core.Page."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--delay",
            type=float,
            default=1.5,
            help="Seconds to sleep after each navigation or JS-triggering click (default: 1.5).",
        )
        parser.add_argument(
            "--max-programs",
            type=int,
            default=None,
            help="Optional cap on total pages to fetch and save (including index).",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Do not write to the database; print URL, title preview, and content length.",
        )
        parser.add_argument(
            "--skip-section",
            type=str,
            default="",
            help="Comma-separated substrings; skip expand clicks whose link text or href matches (case-insensitive).",
        )
        parser.add_argument(
            "--lang",
            type=str,
            default="en",
            help="Prefer this site language: drop index/section URLs with lang=tr when set to en (default: en).",
        )

    def handle(self, *args, **options):
        style: Any = self.style
        page_objects: Any = getattr(Page, "objects")

        delay: float = max(0.0, float(options["delay"]))
        dry_run: bool = options["dry_run"]
        max_programs: int | None = options["max_programs"]
        skip_raw: str = options["skip_section"] or ""
        skip_parts = [s.strip() for s in skip_raw.split(",") if s.strip()]

        driver = None
        saved = 0
        failed = 0
        urls: list[str] = []

        try:
            self.stdout.write("Starting headless Chrome…")
            driver = build_driver()
            urls = collect_bologna_urls(
                driver,
                delay,
                skip_section_parts=skip_parts,
                target_lang=(options.get("lang") or "en"),
            )
            self.stdout.write(style.NOTICE(f"Collected {len(urls)} unique URLs."))

            if max_programs is not None:
                cap = max(1, int(max_programs))
                urls = urls[:cap]
                self.stdout.write(style.WARNING(f"Capped to {cap} URLs (--max-programs)."))

            for url in urls:
                try:
                    title, text = fetch_page_extract(driver, url, delay)
                except Exception as exc:
                    failed += 1
                    logger.warning("Failed to fetch %s: %s", url, exc)
                    self.stdout.write(style.WARNING(f"Failed: {url} ({exc})"))
                    continue

                if not text:
                    self.stdout.write(style.WARNING(f"Empty body text: {url}"))

                if dry_run:
                    self.stdout.write(
                        f"[dry-run] {url} | {title[:80]!r} | {len(text)} chars"
                    )
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

        finally:
            if driver is not None:
                try:
                    driver.quit()
                except Exception as e:
                    logger.warning("driver.quit() failed: %s", e)

        self.stdout.write(
            style.NOTICE(
                f"Done. URLs processed={len(urls)}, "
                f"saved={saved if not dry_run else 0}, failed={failed}, dry_run={dry_run}."
            )
        )
