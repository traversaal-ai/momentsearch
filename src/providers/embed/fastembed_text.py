"""bge via fastembed — the default TRANSCRIPT embedder.

CLIP's text encoder is tuned to match *images*, not to compare text with text,
so the transcript branch uses a proper small text model in its own space: ONNX,
no torch, no key, ~130MB. Documents and queries use different prompts (bge is
asymmetric), which is why there are two functions rather than one.
"""
from __future__ import annotations

import threading
from functools import lru_cache

import numpy as np

from .base import EmbedConfig, empty, normalize

_lock = threading.Lock()


@lru_cache
def _model(name: str):
    from fastembed import TextEmbedding

    return TextEmbedding(name)


@lru_cache
def model_dim(name: str) -> int:
    with _lock:
        vec = next(iter(_model(name).embed(["dimension probe"])))
    return int(np.asarray(vec).shape[-1])


def embed_docs(texts: list[str], cfg: EmbedConfig) -> np.ndarray:
    """Transcript chunks (documents) — fastembed returns normalized vectors."""
    if not texts:
        return empty(cfg.dim or model_dim(cfg.model))
    with _lock:
        vecs = list(_model(cfg.model).embed(texts))
    return normalize(np.asarray(vecs, dtype=np.float32))


def embed_query(text: str, cfg: EmbedConfig) -> np.ndarray:
    """A search query — bge's query prompt, not the document prompt."""
    with _lock:
        vec = next(iter(_model(cfg.model).query_embed([text])))
    return normalize(np.asarray(vec, dtype=np.float32))[0]
