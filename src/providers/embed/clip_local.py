"""CLIP, locally — the default visual embedder. "Embedding is a URL."

One CLIP model encodes both video frames and text queries into the same vector
space, so a natural-language question matches what is *seen* on screen. No key,
no network, CPU-fine: a fresh clone searches visually with zero credentials.

Loaded lazily and cached per model name, so a process that never embeds (the
API in remote mode) never imports torch.
"""
from __future__ import annotations

import io
import threading
from functools import lru_cache

import numpy as np

from .base import EmbedConfig, EmbedUnavailable, empty, normalize

_lock = threading.Lock()  # sentence-transformers models are not thread-safe


@lru_cache
def _model(name: str):
    # Imported here so remote/hosted modes never drag in torch.
    try:
        from sentence_transformers import SentenceTransformer
    except ImportError as exc:
        # The fresh-clone failure: `pip install -r requirements.txt` was skipped,
        # or uvicorn is running in an environment without torch. Say what to do.
        raise EmbedUnavailable(
            "Local CLIP needs sentence-transformers, which isn't installed in "
            "this environment. Either `pip install -r requirements.txt`, or point "
            "EMBED_SERVICE_URL at a running clip_service (docker compose does "
            f"this for you). Underlying import error: {exc}"
        ) from exc

    return SentenceTransformer(name)


@lru_cache
def model_dim(name: str) -> int:
    """Measure by loading — the last-resort path for a checkpoint that isn't in
    the registry's dims table."""
    return int(_model(name).get_sentence_embedding_dimension())


def embed_images(jpegs: list[bytes], cfg: EmbedConfig) -> np.ndarray:
    """Encode in-memory JPEGs into L2-normalized CLIP vectors."""
    from PIL import Image

    if not jpegs:
        return empty(model_dim(cfg.model))
    images = [Image.open(io.BytesIO(b)).convert("RGB") for b in jpegs]
    try:
        with _lock:
            vecs = _model(cfg.model).encode(images, convert_to_numpy=True,
                                            batch_size=32, show_progress_bar=False)
    finally:
        for img in images:
            img.close()
    return normalize(vecs)


def embed_text(text: str, cfg: EmbedConfig) -> np.ndarray:
    """Encode a query into the shared image+text space."""
    with _lock:
        vec = _model(cfg.model).encode([text], convert_to_numpy=True,
                                       show_progress_bar=False)
    return normalize(vec)[0]
