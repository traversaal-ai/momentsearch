"""Gemini embeddings — one unified space for text, images, video and audio.

gemini-embedding-2 maps every modality into the SAME space, so it can serve
both branches at once: frames on the visual side, transcript chunks on the text
side, one GEMINI_API_KEY for both (and for the answer model too).

Two details that matter:
  * Vectors requested below the model's full 3072 dims come back UNNORMALIZED.
    We L2-normalize everything, so cosine search stays correct either way.
  * task_type applies to the older gemini-embedding-001 / text-embedding-004
    generation. The -2 generation wants that intent in the prompt text instead,
    so we only send task_type to the models that accept it.

Needs the google-genai package (the same one the Gemini LLM adapter uses).
"""
from __future__ import annotations

import threading

import numpy as np

from .base import EmbedConfig, chunked, empty, normalize, parallel_map, resolved_dim

RETRIEVAL_DOCUMENT, RETRIEVAL_QUERY = "RETRIEVAL_DOCUMENT", "RETRIEVAL_QUERY"

_clients: dict[str, object] = {}
_clients_lock = threading.Lock()


def _client(api_key: str):
    """Exactly ONE client per key, created under a lock.

    Not @lru_cache: it doesn't hold its lock while calling the wrapped function,
    so concurrent threads (embed_images fans out) would each construct a Client,
    all but one get discarded, and the discarded ones close the shared httpx
    transport out from under the survivor — "Cannot send a request, as the client
    has been closed" on a perfectly valid key.
    """
    client = _clients.get(api_key)
    if client is None:
        with _clients_lock:
            client = _clients.get(api_key)
            if client is None:
                from google import genai

                client = _clients[api_key] = genai.Client(api_key=api_key)
    return client


def _takes_task_type(model: str) -> bool:
    return "embedding-001" in model or "text-embedding-004" in model


def _config(cfg: EmbedConfig, task_type: str | None):
    from google.genai import types

    kwargs: dict = {}
    dim = resolved_dim(cfg)
    if dim:
        kwargs["output_dimensionality"] = dim
    if task_type and _takes_task_type(cfg.model):
        kwargs["task_type"] = task_type
    return types.EmbedContentConfig(**kwargs) if kwargs else None


def _embed(contents, cfg: EmbedConfig, task_type: str | None) -> list[list[float]]:
    resp = _client(cfg.api_key).models.embed_content(
        model=cfg.model, contents=contents, config=_config(cfg, task_type))
    return [list(e.values) for e in (resp.embeddings or [])]


# ── Visual branch (unified space) ─────────────────────────────────────────────

def embed_images(jpegs: list[bytes], cfg: EmbedConfig) -> np.ndarray:
    """One request per image (the embeddings API takes a single media part at a
    time), fanned out IMAGE_EMBED_CONCURRENCY-wide.

    Heads up on cost: image embedding here is SLOW — measured ~9s per frame, and
    the endpoint appears to serialize per key, so a 400-frame video is expensive
    in wall-clock even fanned out. For bulk frame indexing prefer local CLIP (no
    per-frame cost at all) or a batching API (Jina/Cohere/Voyage take many images
    per request); raise FRAME_INTERVAL_SEC / lower MAX_FRAMES if you want Gemini
    frames anyway.
    """
    if not jpegs:
        return empty(resolved_dim(cfg))

    def one(jpeg: bytes) -> list[float]:
        from google.genai import types

        vecs = _embed([types.Part.from_bytes(data=jpeg, mime_type="image/jpeg")],
                      cfg, RETRIEVAL_DOCUMENT)
        if not vecs:
            raise RuntimeError("Gemini returned no embedding for a frame.")
        return vecs[0]

    return normalize(np.asarray(parallel_map(one, jpegs, cfg.concurrency),
                                dtype=np.float32))


def embed_text(text: str, cfg: EmbedConfig) -> np.ndarray:
    return normalize(np.asarray(_embed([text], cfg, RETRIEVAL_QUERY),
                                dtype=np.float32))[0]


# ── Transcript branch ─────────────────────────────────────────────────────────

def embed_docs(texts: list[str], cfg: EmbedConfig) -> np.ndarray:
    if not texts:
        return empty(resolved_dim(cfg))
    out: list[list[float]] = []
    for batch in chunked(texts, cfg.batch):
        out.extend(_embed(list(batch), cfg, RETRIEVAL_DOCUMENT))
    return normalize(np.asarray(out, dtype=np.float32))


def embed_query(text: str, cfg: EmbedConfig) -> np.ndarray:
    return normalize(np.asarray(_embed([text], cfg, RETRIEVAL_QUERY),
                                dtype=np.float32))[0]
