"""
Summarize crawl + chunk index state (scope, defaults) for RAG debugging.
"""

from django.core.management.base import BaseCommand
from django.db.models import Avg, Count

from core.management.commands.scrape_acibadem import DEFAULT_SEEDS
from core.models import DocumentChunk, Page


class Command(BaseCommand):
    help = (
        "Print Page/DocumentChunk counts, chunk size stats, and scrape_acibadem / "
        "build_page_embeddings defaults so gaps are not misattributed to retrieval."
    )

    def handle(self, *args, **options):
        page_n = Page.objects.count()
        chunk_n = DocumentChunk.objects.count()
        avg_chunks = (
            DocumentChunk.objects.values("page_id")
            .annotate(n=Count("id"))
            .aggregate(avg=Avg("n"))["avg"]
        )

        # Approximate avg chunk length from a small sample (full-table scan avoided)
        sample = list(DocumentChunk.objects.values_list("content", flat=True)[:500])
        avg_chars = sum(len(c or "") for c in sample) / len(sample) if sample else 0

        en_pages = Page.objects.filter(url__icontains="/en/").count()
        tr_only_guess = page_n - en_pages

        self.stdout.write(self.style.NOTICE("=== RAG index audit ==="))
        self.stdout.write(f"Page rows: {page_n}")
        self.stdout.write(f"DocumentChunk rows: {chunk_n}")
        if avg_chunks is not None:
            self.stdout.write(f"Avg chunks per page (DB aggregate): {float(avg_chunks):.2f}")
        self.stdout.write(
            f"Avg chunk chars (sample up to 500 rows): {avg_chars:.0f}"
        )
        self.stdout.write(
            f"Pages with '/en/' in URL: {en_pages} (remaining may be non-en paths or roots)"
        )
        self.stdout.write(
            f"Rough non-/en/ count: {tr_only_guess} (informational only)"
        )

        self.stdout.write("")
        self.stdout.write(self.style.NOTICE("=== scrape_acibadem defaults (see command help) ==="))
        self.stdout.write(f"DEFAULT_SEEDS: {DEFAULT_SEEDS}")
        self.stdout.write(
            "Default --max-pages=40, --depth=2, English paths only unless --allow-non-english"
        )
        self.stdout.write(
            "Page content trim: no explicit MAX_CONTENT_CHARS constant in current scraper."
        )

        self.stdout.write("")
        self.stdout.write(self.style.NOTICE("=== build_page_embeddings defaults ==="))
        self.stdout.write(
            "--chunk-size=700, --chunk-overlap=120, --batch-size=16 (see command --help)"
        )

        self.stdout.write("")
        self.stdout.write(
            "If many pages are 'never hit' in rag_diagnose_coverage, check crawl scope "
            "and re-run scrape with --crawl --max-pages / --allow-non-english as needed, "
            "then build_page_embeddings."
        )
