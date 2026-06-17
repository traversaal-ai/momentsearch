"""CLIP embeddings — the heart of the *visual* search.

A single CLIP model encodes both video frames (images) and text queries into the
same vector space, so a natural-language question can be matched directly against
what is *seen* on screen. No transcription, no audio.

The model loads lazily on first use and is cached for the process lifetime.
"""
from __future__ import annotations

import threading
from functools import lru_cache
from typing import TYPE_CHECKING

import numpy as np

from .config import get_settings

if TYPE_CHECKING:  # avoid importing torch/PIL at module import time
    from PIL.Image import Image


_lock = threading.Lock()


@lru_cache
def _model():
    # Imported lazily so the web process starts fast and so importing this module
    # (e.g. in tests) doesn't drag in torch.
    from sentence_transformers import SentenceTransformer

    return SentenceTransformer(get_settings().CLIP_MODEL)


@lru_cache
def embedding_dim() -> int:
    return int(_model().get_sentence_embedding_dimension())


def _normalize(vecs: np.ndarray) -> np.ndarray:
    norms = np.linalg.norm(vecs, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    return vecs / norms


def embed_images(images: "list[Image]") -> np.ndarray:
    """Encode PIL images into L2-normalized CLIP vectors."""
    if not images:
        return np.zeros((0, embedding_dim()), dtype=np.float32)
    with _lock:  # sentence-transformers models are not thread-safe
        vecs = _model().encode(images, convert_to_numpy=True, batch_size=32,
                               show_progress_bar=False)
    return _normalize(np.asarray(vecs, dtype=np.float32))


def embed_text(text: str) -> np.ndarray:
    """Encode a text query into the shared CLIP space (L2-normalized)."""
    with _lock:
        vec = _model().encode([text], convert_to_numpy=True, show_progress_bar=False)
    return _normalize(np.asarray(vec, dtype=np.float32))[0]
