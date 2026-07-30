"""Embedding inference service — warm models behind a URL.

    uvicorn src.clip_service:app --host 0.0.0.0 --port 8001

Models load ONCE at boot and stay hot; workers and the API send batches instead
of each flow-run subprocess paying a fresh torch import + weight load (~15-30s
per video). This is the standard model-serving pattern (TEI / Triton /
OpenAI-embeddings-shaped): inference is a URL, so scaling embedding means
scaling THIS one service — today a CPU container, later the same container on a
GPU machine — while workers stay cheap and stateless.

Only LOCAL providers are worth a service. If IMAGE_EMBED_PROVIDER or
TEXT_EMBED_PROVIDER names a hosted API (Jina, Cohere, Voyage, Gemini, OpenAI),
that branch is called directly by whoever needs it and this service never sees
it — nothing to keep warm.

Wire-up: set EMBED_SERVICE_URL=http://clip:8001 on api + worker (docker-compose
does this by default; CLIP_SERVICE_URL is still accepted). Unset, they embed
in-process — simple mode, no service.

Endpoints:
  POST /embed/images  {"jpegs_b64": [...]}  -> {"vectors": [[...], ...]}
  POST /embed/text    {"text": "..."}       -> {"vector": [...]}
  POST /embed/docs    {"texts": [...]}      -> {"vectors": [[...], ...]}
  POST /embed/query   {"text": "..."}       -> {"vector": [...]}
  GET  /healthz  -> {"ok", "model", "dim", "text_model", "text_dim", ...}
"""
from __future__ import annotations

import base64
from contextlib import asynccontextmanager

from fastapi import FastAPI
from pydantic import BaseModel

from . import config
from .providers import embed


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Load the weights NOW, not on the first request."""
    image, text = embed.image_config(), embed.text_config()
    if image.local:
        print(f"[embed] visual: {image.model} warm (dim {embed.image_dim()})")
    else:
        print(f"[embed] visual: {image.preset.label} is a hosted API — "
              f"callers reach it directly, nothing to warm here")
    if config.ENABLE_TRANSCRIPT:
        if text.local:
            embed.embed_docs_local(["warmup"])
            print(f"[embed] transcript: {text.model} warm (dim {embed.text_dim()})")
        else:
            print(f"[embed] transcript: {text.preset.label} is a hosted API")
    yield


app = FastAPI(title="MomentSearch embedding service", lifespan=lifespan)


class ImagesRequest(BaseModel):
    jpegs_b64: list[str]


class TextRequest(BaseModel):
    text: str


class DocsRequest(BaseModel):
    texts: list[str]


def _safe_dim(fn) -> int | None:
    try:
        return fn()
    except Exception:
        return None


@app.get("/healthz")
def healthz():
    image, text = embed.image_config(), embed.text_config()
    return {
        "ok": True,
        # "model"/"dim" keep their historical names: the visual branch, which is
        # what older clients (and vector_store) ask this endpoint for.
        "model": image.model,
        "dim": _safe_dim(embed.image_dim),
        "provider": image.provider,
        "local": image.local,
        "text_model": text.model,
        "text_dim": _safe_dim(embed.text_dim),
        "text_provider": text.provider,
        "text_local": text.local,
    }


@app.post("/embed/images")
def embed_images(req: ImagesRequest):
    jpegs = [base64.b64decode(j) for j in req.jpegs_b64]
    return {"vectors": embed.embed_jpegs_local(jpegs).tolist()}


@app.post("/embed/text")
def embed_text(req: TextRequest):
    return {"vector": embed.embed_text_local(req.text).tolist()}


# ── Transcript branch ────────────────────────────────────────────────────────

@app.post("/embed/docs")
def embed_docs(req: DocsRequest):
    return {"vectors": embed.embed_docs_local(req.texts).tolist()}


@app.post("/embed/query")
def embed_query(req: TextRequest):
    return {"vector": embed.embed_query_local(req.text).tolist()}
