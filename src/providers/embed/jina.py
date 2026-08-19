"""Jina embeddings — jina-clip-v2 for the visual branch, v3 for transcripts.

jina-clip-v2 is a joint text+image model, so it can replace local CLIP outright:
frames and questions land in one space, multilingual, and Matryoshka — set
IMAGE_EMBED_DIM=512 to halve storage.

Plain JSON over HTTPS (stdlib only, no SDK), same endpoint for both branches;
only `task` and the input shape differ.
"""
from __future__ import annotations

import numpy as np

from ..registry import image_embed_preset, preset_dim, text_embed_preset
from .base import (EmbedConfig, chunked, data_uri, empty, normalize, post_json,
                   resolved_dim)

QUERY, PASSAGE = "retrieval.query", "retrieval.passage"


def _native_dim(cfg: EmbedConfig) -> int:
    preset = (image_embed_preset if cfg.branch == "image" else text_embed_preset)(cfg.provider)
    return preset_dim(preset, cfg.model)


def _post(inputs: list, cfg: EmbedConfig, task: str) -> list[list[float]]:
    payload: dict = {"model": cfg.model, "input": inputs, "task": task,
                     "embedding_type": "float", "normalized": True}
    dim = resolved_dim(cfg)
    if dim and dim != _native_dim(cfg):   # Matryoshka truncation
        payload["dimensions"] = dim
    resp = post_json(cfg.base_url, payload,
                     {"Authorization": f"Bearer {cfg.api_key}",
                      "Accept": "application/json"})
    data = sorted(resp.get("data", []), key=lambda d: d.get("index", 0))
    return [d["embedding"] for d in data]


def _run(inputs: list, cfg: EmbedConfig, task: str) -> np.ndarray:
    if not inputs:
        return empty(resolved_dim(cfg))
    out: list[list[float]] = []
    for batch in chunked(inputs, cfg.batch):
        out.extend(_post(batch, cfg, task))
    return normalize(np.asarray(out, dtype=np.float32))


# ── Visual branch (joint space) ───────────────────────────────────────────────

def embed_images(jpegs: list[bytes], cfg: EmbedConfig) -> np.ndarray:
    return _run([{"image": data_uri(j)} for j in jpegs], cfg, PASSAGE)


def embed_text(text: str, cfg: EmbedConfig) -> np.ndarray:
    """Question -> the same space the frames live in."""
    return _run([{"text": text}], cfg, QUERY)[0]


# ── Transcript branch ─────────────────────────────────────────────────────────

def embed_docs(texts: list[str], cfg: EmbedConfig) -> np.ndarray:
    return _run([{"text": t} for t in texts], cfg, PASSAGE)


def embed_query(text: str, cfg: EmbedConfig) -> np.ndarray:
    return _run([{"text": text}], cfg, QUERY)[0]
