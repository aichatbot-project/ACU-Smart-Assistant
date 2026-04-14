"""RAG tuning from environment (shared by chat views and retrieval)."""

import os


def _env_bool(key: str, default: str = "true") -> bool:
    return os.environ.get(key, default).lower() in ("1", "true", "yes")


RAG_MAX_CHARS = max(800, int(os.environ.get("RAG_MAX_CHARS", "2000")))
RAG_TOP_K = int(os.environ.get("RAG_TOP_K", "12"))
RAG_VECTOR_FILL_EXTRA = max(0, int(os.environ.get("RAG_VECTOR_FILL_EXTRA", "6")))
# Cap chunks per URL in one prompt (lower = less duplicate pages eating the char budget).
RAG_MAX_CHUNKS_PER_URL = max(1, int(os.environ.get("RAG_MAX_CHUNKS_PER_URL", "2")))
RAG_MAX_DISTANCE = float(os.environ.get("RAG_MAX_DISTANCE", "0.62"))
RAG_RELAX_ON_EMPTY = _env_bool("RAG_RELAX_ON_EMPTY", "true")
RAG_KEYWORD_BOOST = _env_bool("RAG_KEYWORD_BOOST", "true")
RAG_SNIPPET_CHARS = min(
    max(400, int(os.environ.get("RAG_SNIPPET_CHARS", "1100"))),
    RAG_MAX_CHARS,
)

# Recall-oriented (multi-embed, wide pool, rerank, optional lexical on Postgres)
RAG_MULTI_EMBED = _env_bool("RAG_MULTI_EMBED", "false")
RAG_MULTI_EMBED_KEYWORD_LINE = _env_bool("RAG_MULTI_EMBED_KEYWORD_LINE", "false")
RAG_VECTOR_CANDIDATE_POOL = max(20, min(500, int(os.environ.get("RAG_VECTOR_CANDIDATE_POOL", "40"))))
RAG_RERANK_OVERLAP_WEIGHT = float(os.environ.get("RAG_RERANK_OVERLAP_WEIGHT", "0.06"))
RAG_LEXICAL_WEIGHT = float(os.environ.get("RAG_LEXICAL_WEIGHT", "0.12"))

# Added to effective cosine distance when sorting context (STEM queries); pushes news/events pages down.
RAG_STEM_NOISE_URL_PENALTY = float(os.environ.get("RAG_STEM_NOISE_URL_PENALTY", "0.14"))
