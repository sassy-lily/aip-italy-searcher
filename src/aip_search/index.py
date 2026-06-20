"""Embedding (fastembed multilingual-e5) + LanceDB storage.

e5 requires "query:"/"passage:" prefixes — omitting them silently halves quality.
"""
from __future__ import annotations

from functools import lru_cache

import lancedb

from .config import DENSE_MODEL, LANCE_DIR
from .models import Chunk

TABLE = "chunks"


@lru_cache(maxsize=1)
def _embedder():
    from fastembed import TextEmbedding

    return TextEmbedding(model_name=DENSE_MODEL)


def embed_passages(texts: list[str]) -> list[list[float]]:
    return [v.tolist() for v in _embedder().embed([f"passage: {t}" for t in texts])]


def embed_query(query: str) -> list[float]:
    return next(iter(_embedder().embed([f"query: {query}"]))).tolist()


def build_index(chunks: list[Chunk]) -> int:
    LANCE_DIR.mkdir(parents=True, exist_ok=True)
    vectors = embed_passages([c.text for c in chunks])
    records = []
    for c, v in zip(chunks, vectors):
        rec = c.flat_meta()
        rec["vector"] = v
        records.append(rec)
    db = lancedb.connect(str(LANCE_DIR))
    db.create_table(TABLE, data=records, mode="overwrite")
    return len(records)


def open_table():
    db = lancedb.connect(str(LANCE_DIR))
    return db.open_table(TABLE)
