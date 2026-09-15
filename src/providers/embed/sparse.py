"""BM25 sparse vectors for the transcript branch — the lexical half of hybrid search.

Dense embeddings blur exact tokens: an identifier (`__init__`), a number ("300
welds"), a product or a person's name all become "roughly this topic". BM25
matches the words themselves, so the transcript chunk that literally contains
the term ranks first for it. Each chunk carries BOTH vectors in the ONE text
collection (config.TEXT_COLLECTION): the dense one under Qdrant's default name
and this one under config.SPARSE_VECTOR. src/rag/search.py queries both and
merges them by rank before the frame branch is fused in.

fastembed's `Qdrant/bm25` is a tokenizer + term-frequency model, a few MB, CPU,
no key. It emits term FREQUENCIES only; the IDF half of BM25 is applied by Qdrant
at query time (SparseVectorParams(modifier=IDF)), so points never go stale as
the corpus grows. Documents and queries are encoded differently (document-side
length normalisation), hence two functions.
"""
from __future__ import annotations

import threading
from functools import lru_cache

from ... import config

_lock = threading.Lock()   # fastembed models are not documented as thread-safe

Sparse = tuple[list[int], list[float]]   # (indices, values) — Qdrant's SparseVector shape


@lru_cache
def _model(name: str):
    from fastembed import SparseTextEmbedding

    return SparseTextEmbedding(model_name=name)


def _pair(emb) -> Sparse:
    return [int(i) for i in emb.indices], [float(v) for v in emb.values]


def embed_sparse_docs(texts: list[str]) -> list[Sparse]:
    """Transcript chunks -> one (indices, values) per chunk, aligned to `texts`."""
    if not texts:
        return []
    with _lock:
        return [_pair(e) for e in _model(config.SPARSE_MODEL).embed(texts)]


def embed_sparse_query(text: str) -> Sparse:
    """A question -> its sparse query vector."""
    with _lock:
        return _pair(next(iter(_model(config.SPARSE_MODEL).query_embed([text]))))
