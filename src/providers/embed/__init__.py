"""Embeddings — the retrieval half of MomentSearch, two independent branches.

  VISUAL branch    frames + the question in ONE joint space (text->image).
                   IMAGE_EMBED_PROVIDER: clip (local, default) | jina | cohere
                   | voyage | gemini
  TRANSCRIPT branch caption chunks + the question in a text space.
                   TEXT_EMBED_PROVIDER: fastembed (local, default) | openai |
                   gemini | cohere | voyage | jina

The branches are fused by RANK (RRF) in src/rag/search.py, never by raw score,
so they are free to use different providers, different dimensions and different
scales. Mix freely: local CLIP frames + hosted Gemini transcripts is a perfectly
sensible configuration.

Three execution modes, chosen automatically per branch:
  in-process   local provider, no service URL (quickstart / single machine)
  service      local provider + EMBED_SERVICE_URL -> the warm container
  api          hosted provider -> called directly, service URL ignored

Public surface (what the rest of the app uses):
  embed_jpegs(jpegs)  frames  -> vectors        [visual, indexing]
  embed_text(text)    question-> vector         [visual, querying]
  embed_docs(texts)   chunks  -> vectors        [transcript, indexing]
  embed_query(text)   question-> vector         [transcript, querying]
  image_dim() / text_dim()                      [collection creation]
"""
from __future__ import annotations

import numpy as np

from ... import config
from ..registry import preset_dim
from .base import (EmbedConfig, empty, image_config, missing_requirement,
                   resolved_dim, text_config)

__all__ = ["embed_jpegs", "embed_text", "embed_docs", "embed_query",
           "embed_jpegs_local", "embed_text_local", "embed_docs_local",
           "embed_query_local", "image_dim", "text_dim", "embedding_dim",
           "describe", "image_config", "text_config"]

# registry kind -> adapter module name in this package
_ADAPTERS = {
    "clip": "clip_local",
    "fastembed": "fastembed_text",
    "openai": "openai_text",
    "gemini": "gemini",
    "jina": "jina",
    "cohere": "cohere",
    "voyage": "voyage",
}


def _adapter(cfg: EmbedConfig):
    from importlib import import_module

    kind = cfg.preset.kind
    try:
        return import_module(f".{_ADAPTERS[kind]}", __package__)
    except KeyError:
        raise RuntimeError(f"No embedding adapter for provider '{cfg.provider}'.")
    except ImportError as exc:
        sdk = cfg.preset.sdk or "the provider SDK"
        raise RuntimeError(f"{cfg.preset.label} needs '{sdk}': "
                           f"pip install {sdk} ({exc})") from exc


def _check(cfg: EmbedConfig) -> EmbedConfig:
    problem = missing_requirement(cfg)
    if problem:
        raise RuntimeError(problem)
    return cfg


def _use_service(cfg: EmbedConfig) -> bool:
    """The warm service is only meaningful for providers with weights to warm."""
    return bool(cfg.local and config.EMBED_SERVICE_URL)


# ── In-process ("here") — also what the embedding service itself serves ───────

def embed_jpegs_local(jpegs: list[bytes]) -> np.ndarray:
    cfg = _check(image_config())
    return _adapter(cfg).embed_images(jpegs, cfg)


def embed_text_local(text: str) -> np.ndarray:
    cfg = _check(image_config())
    return _adapter(cfg).embed_text(text, cfg)


def embed_docs_local(texts: list[str]) -> np.ndarray:
    cfg = _check(text_config())
    return _adapter(cfg).embed_docs(texts, cfg)


def embed_query_local(text: str) -> np.ndarray:
    cfg = _check(text_config())
    return _adapter(cfg).embed_query(text, cfg)


# ── Public API (mode dispatch) ────────────────────────────────────────────────

def embed_jpegs(jpegs: list[bytes]) -> np.ndarray:
    """Frames -> visual vectors (the indexing path)."""
    cfg = image_config()
    if _use_service(cfg):
        if not jpegs:
            return empty(image_dim())
        from . import remote

        return remote.embed_images(jpegs, cfg)
    if not jpegs:
        return empty(image_dim())
    return embed_jpegs_local(jpegs)


def embed_text(text: str) -> np.ndarray:
    """Question -> a vector in the FRAME space (the visual query path)."""
    cfg = image_config()
    if _use_service(cfg):
        from . import remote

        return remote.embed_text(text, cfg)
    return embed_text_local(text)


def embed_docs(texts: list[str]) -> np.ndarray:
    """Transcript chunks -> text vectors (the indexing path)."""
    cfg = text_config()
    if _use_service(cfg):
        if not texts:
            return empty(text_dim())
        from . import remote

        return remote.embed_docs(texts, cfg)
    if not texts:
        return empty(text_dim())
    return embed_docs_local(texts)


def embed_query(text: str) -> np.ndarray:
    """Question -> a vector in the TRANSCRIPT space (the text query path)."""
    cfg = text_config()
    if _use_service(cfg):
        from . import remote

        return remote.embed_query(text, cfg)
    return embed_query_local(text)


# ── Dimensions (collection creation happens before any model is loaded) ───────

def image_dim() -> int:
    cfg = image_config()
    try:
        return resolved_dim(cfg)
    except Exception:
        # Local model we can't measure here (no torch in this process) — the
        # warm service knows, because it has the weights.
        if _use_service(cfg):
            from . import remote

            return int(remote.health()["dim"])
        raise


def text_dim() -> int:
    cfg = text_config()
    try:
        return resolved_dim(cfg)
    except Exception:
        if _use_service(cfg):
            from . import remote

            return int(remote.health()["text_dim"])
        raise


def embedding_dim() -> int:
    """Back-compat alias — the visual branch's dimension."""
    return image_dim()


def describe(measure: bool = False) -> dict:
    """No-secrets summary of both branches for /api/config and the doctor.

    `measure=False` (the default) resolves dimensions from the registry table
    ONLY. This is what HTTP handlers must use: for a local checkpoint the table
    doesn't list, measuring means importing torch and loading weights inside the
    API process — a 15-30s stall on an endpoint that should be instant. The
    doctor CLI passes measure=True, where paying that cost is the point.
    """
    def one(cfg: EmbedConfig, dim_fn) -> dict:
        problem = missing_requirement(cfg)
        dim = 0
        if not problem:
            dim = cfg.dim or preset_dim(cfg.preset, cfg.model)
            if not dim and measure:
                try:
                    dim = dim_fn()
                except Exception as exc:   # unmeasurable without the weights
                    problem = str(exc)
        return {"provider": cfg.provider, "label": cfg.preset.label,
                "model": cfg.model, "dim": dim,
                "mode": ("service" if _use_service(cfg)
                         else "in-process" if cfg.local else "api"),
                "modality": cfg.preset.modality,
                "api_key_set": bool(cfg.api_key),
                "base_url": cfg.base_url or None,
                "sdk": cfg.preset.sdk or None,
                "ready": problem is None, "problem": problem}

    return {"image": one(image_config(), image_dim),
            "text": one(text_config(), text_dim)}
