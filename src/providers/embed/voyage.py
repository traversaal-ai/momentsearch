"""Voyage AI — multimodal embeddings for frames, text embeddings for transcripts.

Voyage pushes both modalities through a SINGLE backbone rather than two towers,
which reduces the same-modality bias CLIP-style models have (where a text query
prefers text-looking images). Two endpoints:

  /v1/multimodalembeddings   images and text in the joint space (visual branch)
  /v1/embeddings             text only (transcript branch)

Plain JSON over HTTPS; the `voyageai` SDK is not required.
"""
from __future__ import annotations

import numpy as np

from ..registry import image_embed_preset, preset_dim, text_embed_preset
from .base import (EmbedConfig, chunked, data_uri, empty, normalize, post_json,
                   resolved_dim)

DOCUMENT, QUERY = "document", "query"
TEXT_ENDPOINT = "https://api.voyageai.com/v1/embeddings"


def _native_dim(cfg: EmbedConfig) -> int:
    preset = (image_embed_preset if cfg.branch == "image" else text_embed_preset)(cfg.provider)
    return preset_dim(preset, cfg.model)


def _maybe_dim(payload: dict, cfg: EmbedConfig) -> dict:
    dim = resolved_dim(cfg)
    if dim and dim != _native_dim(cfg):
        payload["output_dimension"] = dim
    return payload


def _post(url: str, payload: dict, cfg: EmbedConfig) -> list[list[float]]:
    resp = post_json(url, payload, {"Authorization": f"Bearer {cfg.api_key}",
                                    "Accept": "application/json"})
    data = sorted(resp.get("data", []), key=lambda d: d.get("index", 0))
    return [d["embedding"] for d in data]


def _multimodal(inputs: list[dict], cfg: EmbedConfig, input_type: str) -> np.ndarray:
    if not inputs:
        return empty(resolved_dim(cfg))
    out: list[list[float]] = []
    for batch in chunked(inputs, cfg.batch):
        out.extend(_post(cfg.base_url, _maybe_dim(
            {"model": cfg.model, "inputs": batch, "input_type": input_type}, cfg), cfg))
    return normalize(np.asarray(out, dtype=np.float32))


def _text(texts: list[str], cfg: EmbedConfig, input_type: str) -> np.ndarray:
    if not texts:
        return empty(resolved_dim(cfg))
    url = cfg.base_url or TEXT_ENDPOINT
    out: list[list[float]] = []
    for batch in chunked(texts, cfg.batch):
        out.extend(_post(url, _maybe_dim(
            {"model": cfg.model, "input": batch, "input_type": input_type}, cfg), cfg))
    return normalize(np.asarray(out, dtype=np.float32))


# ── Visual branch (joint space) ───────────────────────────────────────────────

def embed_images(jpegs: list[bytes], cfg: EmbedConfig) -> np.ndarray:
    # One image per input; a single request may not mix base64 and url types.
    return _multimodal([{"content": [{"type": "image_base64",
                                      "image_base64": data_uri(j)}]}
                        for j in jpegs], cfg, DOCUMENT)


def embed_text(text: str, cfg: EmbedConfig) -> np.ndarray:
    return _multimodal([{"content": [{"type": "text", "text": text}]}],
                       cfg, QUERY)[0]


# ── Transcript branch ─────────────────────────────────────────────────────────

def embed_docs(texts: list[str], cfg: EmbedConfig) -> np.ndarray:
    return _text(texts, cfg, DOCUMENT)


def embed_query(text: str, cfg: EmbedConfig) -> np.ndarray:
    return _text([text], cfg, QUERY)[0]
