"""Cohere Embed v4 — joint image+text embeddings via the v2/embed API.

Strongest option when frames are text-heavy: slides, charts, dashboards, code
screenshots. v4 takes images as content blocks; the older v3 models take a
plain `texts` list, so both shapes are supported and chosen by model name.

input_type is not optional for Cohere and it is not cosmetic — documents and
queries must be embedded with different values or retrieval quality drops.

Plain JSON over HTTPS; the `cohere` SDK is not required.
"""
from __future__ import annotations

import numpy as np

from ..registry import image_embed_preset, preset_dim, text_embed_preset
from .base import (EmbedConfig, chunked, data_uri, empty, normalize, post_json,
                   resolved_dim)

DOCUMENT, QUERY = "search_document", "search_query"


def _is_v4(model: str) -> bool:
    return model.startswith("embed-v4")


def _native_dim(cfg: EmbedConfig) -> int:
    preset = (image_embed_preset if cfg.branch == "image" else text_embed_preset)(cfg.provider)
    return preset_dim(preset, cfg.model)


def _post(payload: dict, cfg: EmbedConfig) -> list[list[float]]:
    dim = resolved_dim(cfg)
    if _is_v4(cfg.model) and dim and dim != _native_dim(cfg):
        payload["output_dimension"] = dim
    resp = post_json(cfg.base_url, {"model": cfg.model,
                                    "embedding_types": ["float"], **payload},
                     {"Authorization": f"Bearer {cfg.api_key}",
                      "Accept": "application/json"})
    embeddings = resp.get("embeddings") or {}
    vectors = embeddings.get("float") if isinstance(embeddings, dict) else embeddings
    if not vectors:
        raise RuntimeError(f"Cohere returned no float embeddings: {str(resp)[:300]}")
    return vectors


def _run(payloads: list[dict], cfg: EmbedConfig) -> np.ndarray:
    """`payloads` are already-batched request bodies."""
    out: list[list[float]] = []
    for payload in payloads:
        out.extend(_post(payload, cfg))
    if not out:
        return empty(resolved_dim(cfg))
    return normalize(np.asarray(out, dtype=np.float32))


def _text_payloads(texts: list[str], cfg: EmbedConfig, input_type: str) -> list[dict]:
    batches = list(chunked(texts, cfg.batch))
    if _is_v4(cfg.model):
        return [{"input_type": input_type,
                 "inputs": [{"content": [{"type": "text", "text": t}]} for t in b]}
                for b in batches]
    return [{"input_type": input_type, "texts": b} for b in batches]  # v3 shape


# ── Visual branch (joint space) ───────────────────────────────────────────────

def embed_images(jpegs: list[bytes], cfg: EmbedConfig) -> np.ndarray:
    if not jpegs:
        return empty(resolved_dim(cfg))
    payloads = [
        {"input_type": DOCUMENT,
         "inputs": [{"content": [{"type": "image_url",
                                  "image_url": {"url": data_uri(j)}}]} for j in b]}
        # v4 accepts up to 96 images per call; cfg.batch keeps us under it.
        for b in chunked(jpegs, min(cfg.batch, 96))
    ]
    return _run(payloads, cfg)


def embed_text(text: str, cfg: EmbedConfig) -> np.ndarray:
    return _run(_text_payloads([text], cfg, QUERY), cfg)[0]


# ── Transcript branch ─────────────────────────────────────────────────────────

def embed_docs(texts: list[str], cfg: EmbedConfig) -> np.ndarray:
    if not texts:
        return empty(resolved_dim(cfg))
    return _run(_text_payloads(texts, cfg, DOCUMENT), cfg)


def embed_query(text: str, cfg: EmbedConfig) -> np.ndarray:
    return _run(_text_payloads([text], cfg, QUERY), cfg)[0]
