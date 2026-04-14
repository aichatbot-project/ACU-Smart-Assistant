import logging
import threading

from django.apps import AppConfig

logger = logging.getLogger("core")


class CoreConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "core"

    def ready(self):
        import os
        # Only pre-warm in the main reloader process (avoids double load).
        if os.environ.get("RUN_MAIN") != "true":
            return

        def _warmup():
            try:
                from core.embeddings import get_embedding_model
                get_embedding_model()
                logger.info("Embedding model pre-warmed successfully")
            except Exception as e:
                logger.warning("Embedding model pre-warm failed: %s", e)

        threading.Thread(target=_warmup, daemon=True).start()
