"""Client for the warm embedding service (EMBED_SERVICE_URL / CLIP_SERVICE_URL).

Only LOCAL providers go through here. The point of the service is to load model
weights ONCE instead of paying a fresh torch import per Prefect flow-run
subprocess (~15-30s a video) — a hosted API has nothing to warm, so those
providers are called directly from whichever process needs them and this module
is skipped entirely.

Retries while the service boots: workers routinely start before the model
finishes loading.
"""
from __future__ import annotations

import base64
import json
import time
import urllib.error
import urllib.request

import numpy as np

from ... import config
from .base import EmbedConfig, normalize


def _url(path: str) -> str:
    return config.EMBED_SERVICE_URL + path


def _headers() -> dict:
    h = {"Content-Type": "application/json"}
    # Sent only when EMBED_SERVICE_TOKEN is set — matches the clip service's
    # optional bearer check (src/clip_service.py). No token = no header, i.e.
    # today's open Fly-internal behavior.
    if config.EMBED_SERVICE_TOKEN:
        h["Authorization"] = f"Bearer {config.EMBED_SERVICE_TOKEN}"
    return h


def post(path: str, payload: dict, timeout: int = 600) -> dict:
    body = json.dumps(payload).encode()
    last: Exception | None = None
    for _ in range(12):  # up to ~60s of patience for a cold service
        try:
            req = urllib.request.Request(_url(path), data=body, headers=_headers())
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read())
        except urllib.error.URLError as exc:
            last = exc
            time.sleep(5)
    raise RuntimeError(
        f"Embedding service unreachable at {config.EMBED_SERVICE_URL}: {last}")


def health(timeout: int = 60) -> dict:
    with urllib.request.urlopen(_url("/healthz"), timeout=timeout) as resp:
        return json.loads(resp.read())


def embed_images(jpegs: list[bytes], cfg: EmbedConfig) -> np.ndarray:
    vecs = post("/embed/images",
                {"jpegs_b64": [base64.b64encode(j).decode() for j in jpegs]})["vectors"]
    return np.asarray(vecs, dtype=np.float32)


def embed_text(text: str, cfg: EmbedConfig) -> np.ndarray:
    return normalize(np.asarray(post("/embed/text", {"text": text},
                                     timeout=60)["vector"], dtype=np.float32))[0]


def embed_docs(texts: list[str], cfg: EmbedConfig) -> np.ndarray:
    vecs = post("/embed/docs", {"texts": texts})["vectors"]
    return np.asarray(vecs, dtype=np.float32)


def embed_query(text: str, cfg: EmbedConfig) -> np.ndarray:
    return normalize(np.asarray(post("/embed/query", {"text": text},
                                     timeout=60)["vector"], dtype=np.float32))[0]
