"""Shared plumbing for every embedding adapter: config resolution, HTTP, dims.

The two branches are configured independently — IMAGE_EMBED_* drives the visual
(frame) branch, TEXT_EMBED_* the transcript branch — because they are separate
Qdrant collections fused by RANK (RRF), never by score. You can run local CLIP
frames with hosted Gemini transcripts, or vice versa.

One invariant matters more than anything else here: **the vector dimension must
match between indexing and querying**. That is why dims come from a table in
the registry (so the API can create a collection at boot without downloading a
model) and why vector_store refuses to reuse a collection whose size disagrees.
Switching an embedding provider means re-indexing that branch.
"""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from functools import lru_cache

import numpy as np

from ... import config
from ..registry import (EmbedPreset, image_embed_preset, preset_dim,
                        text_embed_preset)


class EmbedUnavailable(RuntimeError):
    """The embedder can't run in THIS process — a missing dependency, not a bad
    question. Carried as its own type so the API can answer 503 with the fix
    instead of leaking a bare 500: a fresh clone that runs uvicorn without
    installing torch hits this on its first question, and "Internal Server Error"
    tells that person nothing.
    """


@dataclass(frozen=True)
class EmbedConfig:
    """A fully-resolved embedder handle for ONE branch."""
    branch: str          # "image" (joint frame+query space) | "text" (transcript)
    provider: str        # registry key
    model: str
    dim: int             # 0 = unknown; only local models can measure it
    api_key: str = ""
    base_url: str = ""
    batch: int = 32      # inputs per request (API limits, not a perf knob)
    local: bool = False  # weights run here / in the embed service
    concurrency: int = 8  # in-flight requests for one-input-per-call providers

    @property
    def preset(self) -> EmbedPreset:
        return (image_embed_preset if self.branch == "image" else
                text_embed_preset)(self.provider)


@lru_cache
def image_config() -> EmbedConfig:
    """Visual branch. CLIP_* env vars still work as aliases for the clip
    provider, so existing .env files keep running unchanged."""
    preset = image_embed_preset(config.IMAGE_EMBED_PROVIDER)
    return EmbedConfig(
        branch="image",
        provider=config.IMAGE_EMBED_PROVIDER,
        model=config.IMAGE_EMBED_MODEL,
        dim=config.IMAGE_EMBED_DIM,
        api_key=config.IMAGE_EMBED_API_KEY,
        base_url=config.IMAGE_EMBED_BASE_URL or preset.base_url,
        batch=config.IMAGE_EMBED_BATCH,
        local=preset.local,
        concurrency=config.IMAGE_EMBED_CONCURRENCY,
    )


@lru_cache
def text_config() -> EmbedConfig:
    """Transcript branch."""
    preset = text_embed_preset(config.TEXT_EMBED_PROVIDER)
    return EmbedConfig(
        branch="text",
        provider=config.TEXT_EMBED_PROVIDER,
        model=config.TEXT_EMBED_MODEL,
        dim=config.TEXT_EMBED_DIM,
        api_key=config.TEXT_EMBED_API_KEY,
        base_url=config.TEXT_EMBED_BASE_URL or preset.base_url,
        batch=config.TEXT_EMBED_BATCH,
        local=preset.local,
    )


def missing_requirement(cfg: EmbedConfig) -> str | None:
    """Why this branch can't embed yet, in words a user can act on (None = ok)."""
    preset = cfg.preset
    prefix = "IMAGE_EMBED" if cfg.branch == "image" else "TEXT_EMBED"
    if not cfg.model:
        return f"{preset.label}: no model set. Set {prefix}_MODEL."
    # Only a base_url the USER supplied stands in for a key (their own server).
    # The preset's own endpoint must not: every hosted provider ships one, and
    # accepting it would report a keyless config as ready and fail later with a
    # 401 in the middle of ingesting a video.
    self_hosted = bool(cfg.base_url) and cfg.base_url != preset.base_url
    if preset.requires_key and not cfg.api_key and not self_hosted:
        envs = " or ".join(preset.key_envs) or f"{prefix}_API_KEY"
        return f"{preset.label}: no API key. Set {envs} (or {prefix}_API_KEY)."
    if not cfg.dim and not preset.local:
        return (f"{preset.label}: unknown vector size for model '{cfg.model}'. "
                f"Set {prefix}_DIM to the dimension it returns.")
    return None


def resolved_dim(cfg: EmbedConfig) -> int:
    """The branch's vector size. Table first (no download); measuring the local
    model is the last resort, and hosted providers can't measure at all."""
    if cfg.dim:
        return cfg.dim
    dim = preset_dim(cfg.preset, cfg.model)
    if dim:
        return dim
    if cfg.preset.local and cfg.branch == "image":
        from .clip_local import model_dim

        return model_dim(cfg.model)
    if cfg.preset.local and cfg.branch == "text":
        from .fastembed_text import model_dim

        return model_dim(cfg.model)
    raise RuntimeError(missing_requirement(cfg) or "Unknown embedding dimension.")


def normalize(vecs: np.ndarray) -> np.ndarray:
    """L2-normalize rows so a cosine search is a dot product — and so every
    provider's vectors land on the same scale regardless of whether it
    normalizes server-side (Gemini's truncated dims, for instance, do not)."""
    vecs = np.asarray(vecs, dtype=np.float32)
    if vecs.ndim == 1:
        vecs = vecs.reshape(1, -1)
    norms = np.linalg.norm(vecs, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    return vecs / norms


def empty(dim: int) -> np.ndarray:
    return np.zeros((0, dim), dtype=np.float32)


def chunked(items: list, size: int):
    for i in range(0, len(items), max(1, size)):
        yield items[i:i + max(1, size)]


def parallel_map(fn, items: list, workers: int = 8) -> list:
    """Fan out one-request-per-item providers so a 400-frame video doesn't turn
    into 400 sequential round-trips."""
    if len(items) <= 1:
        return [fn(x) for x in items]
    with ThreadPoolExecutor(max_workers=min(workers, len(items))) as ex:
        return list(ex.map(fn, items))


# ── HTTP (hosted providers) ───────────────────────────────────────────────────
# Deliberately stdlib: Jina, Cohere and Voyage are plain JSON over HTTPS, so
# supporting them costs no dependency at all.

_RETRY_STATUS = {408, 409, 425, 429, 500, 502, 503, 504}


def post_json(url: str, payload: dict, headers: dict, *, timeout: int = 120,
              retries: int = 3) -> dict:
    """POST JSON, retrying rate limits and transient 5xx with backoff.

    Non-retryable errors raise RuntimeError carrying the provider's own
    response body — the difference between "it failed" and "your model name is
    wrong" is always in that body.
    """
    body = json.dumps(payload).encode()
    hdrs = {"Content-Type": "application/json", **headers}
    last = ""
    for attempt in range(retries):
        req = urllib.request.Request(url, data=body, headers=hdrs, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read())
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")[:600]
            last = f"HTTP {exc.code}: {detail}"
            if exc.code not in _RETRY_STATUS:
                raise RuntimeError(f"{url} -> {last}") from exc
        except urllib.error.URLError as exc:
            last = str(exc.reason)
        except TimeoutError as exc:  # urlopen timeout
            last = f"timeout after {timeout}s ({exc})"
        if attempt < retries - 1:
            time.sleep(2 ** attempt)
    raise RuntimeError(f"{url} unreachable after {retries} tries -> {last}")


def data_uri(jpeg: bytes) -> str:
    """base64 JPEG data URL — the shape Jina, Cohere and Voyage all take."""
    import base64

    return "data:image/jpeg;base64," + base64.b64encode(jpeg).decode()
