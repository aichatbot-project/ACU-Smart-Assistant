from django.core.management.base import BaseCommand
from django.db import transaction

from core.embeddings import chunk_text, embed_texts
from core.models import DocumentChunk, Page


class Command(BaseCommand):
    help = "Build chunked embeddings for Page rows and store in DocumentChunk."

    def add_arguments(self, parser):
        parser.add_argument("--page-id", type=int, help="Only process one page id.")
        parser.add_argument("--limit", type=int, default=0, help="Limit page count.")
        parser.add_argument("--batch-size", type=int, default=16, help="Embedding batch size.")
        parser.add_argument("--chunk-size", type=int, default=700, help="Chunk size (characters).")
        parser.add_argument(
            "--chunk-overlap", type=int, default=120, help="Chunk overlap (characters)."
        )

    def handle(self, *args, **options):
        page_id = options.get("page_id")
        limit = max(0, options.get("limit", 0))
        batch_size = max(1, options.get("batch_size", 16))
        chunk_size = max(100, options.get("chunk_size", 700))
        chunk_overlap = max(0, options.get("chunk_overlap", 120))

        queryset = Page.objects.all().order_by("id")
        if page_id:
            queryset = queryset.filter(id=page_id)
        if limit:
            queryset = queryset[:limit]

        processed_pages = 0
        total_chunks = 0

        for page in queryset:
            chunks = chunk_text(page.content, chunk_size=chunk_size, chunk_overlap=chunk_overlap)
            if not chunks:
                continue

            created_rows: list[DocumentChunk] = []
            chunk_index = 0
            for i in range(0, len(chunks), batch_size):
                batch_chunks = chunks[i : i + batch_size]
                vectors = embed_texts(batch_chunks)
                for text, vector in zip(batch_chunks, vectors):
                    if vector is None:
                        self.stderr.write(
                            self.style.WARNING(
                                f"Skipping chunk {chunk_index} of page={page.id}: embedding failed"
                            )
                        )
                        chunk_index += 1
                        continue
                    created_rows.append(
                        DocumentChunk(
                            page=page,
                            chunk_index=chunk_index,
                            content=text,
                            embedding=vector,
                            source_url=page.url,
                            page_title=page.title,
                        )
                    )
                    chunk_index += 1

            with transaction.atomic():
                DocumentChunk.objects.filter(page=page).delete()
                DocumentChunk.objects.bulk_create(created_rows, batch_size=500)

            processed_pages += 1
            total_chunks += len(created_rows)
            self.stdout.write(
                self.style.SUCCESS(
                    f"Embedded page={page.id} chunks={len(created_rows)} title={page.title[:60]!r}"
                )
            )

        self.stdout.write(
            self.style.NOTICE(
                f"Done. Processed pages={processed_pages}, total chunks={total_chunks}."
            )
        )
