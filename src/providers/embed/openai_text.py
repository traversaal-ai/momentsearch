"""OpenAI (and any OpenAI-compatible) text embeddings for the transcript branch.

Hosted alternative to local bge. Reuses the openai package that the LLM path
already depends on, so one key can power both the answer and the embeddings,
and TEXT_EMBED_BASE_URL points the same client at any server speaking the
embeddings API (vLLM, TEI, Together, DeepInfra, LM Studio).

Symmetric model: documents and queries go through the same call — no separate
query prompt like bge.
"""
from __future__ import annotations

from functools import lru_cache

import numpy as np

from ..registry import preset_dim, text_embed_preset
from .base import EmbedConfig, chunked, empty, normalize


@lru_cache
def _client(api_key: str, base_url: str):
    from openai import OpenAI

    return OpenAI(api_key=api_key or "not-needed", base_url=base_url or None)


def _embed(texts: list[str], cfg: EmbedConfig) -> np.ndarray:
    if not texts:
        return empty(cfg.dim)
    client = _client(cfg.api_key, cfg.base_url)
    kwargs: dict = {"model": cfg.model}
    # text-embedding-3-* are Matryoshka: ask for a shorter vector instead of
    # storing 1536 floats you don't need. Only send it when it differs from the
    # model's native size — older models and some proxies reject the field.
    native = preset_dim(text_embed_preset(cfg.provider), cfg.model)
    if cfg.dim and native and cfg.dim != native:
        kwargs["dimensions"] = cfg.dim
    out: list[list[float]] = []
    for batch in chunked(texts, cfg.batch):
        resp = client.embeddings.create(input=batch, **kwargs)
        out.extend(d.embedding for d in sorted(resp.data, key=lambda d: d.index))
    return normalize(np.asarray(out, dtype=np.float32))


def embed_docs(texts: list[str], cfg: EmbedConfig) -> np.ndarray:
    return _embed(texts, cfg)


def embed_query(text: str, cfg: EmbedConfig) -> np.ndarray:
    return _embed([text], cfg)[0]
