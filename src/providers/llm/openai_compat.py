"""The OpenAI Chat Completions dialect — one adapter, most of the industry.

OpenAI, OpenRouter, xAI (Grok), Groq, Together, Fireworks, Mistral, NVIDIA NIM,
Gemini's compat endpoint, Ollama, LM Studio, vLLM and anything else
OpenAI-shaped all land here; the only difference is base_url, which the
registry already knows. Azure is the one variant that needs a different client
constructor, so it shares this module rather than getting its own.

Two provider quirks are absorbed here instead of being the user's problem:
reasoning-style models that renamed max_tokens to max_completion_tokens, and
models that refuse a non-default temperature. Both surface as HTTP 400s that
mention the offending parameter, so we retry once without it.
"""
from __future__ import annotations

import base64
import os
import threading
from dataclasses import replace

from .base import SYSTEM, LLMConfig, downscale, intro, label

# Sent by OpenRouter's convention so a self-hosted MomentSearch shows up as the
# calling app rather than an anonymous key.
_OPENROUTER_HEADERS = {
    "HTTP-Referer": "https://github.com/traversaal-ai/momentsearch",
    "X-Title": "MomentSearch",
}

# One client per distinct (provider, key, endpoint), kept for the process. A
# fresh client per call meant a fresh TLS handshake per call, and an answer now
# makes two calls (the multi-part check, then the answer) — reusing the client's
# connection pool takes a few hundred ms off each. The SDK client is thread-safe.
# Bounded so a long-lived server with many per-tenant configs can't grow it forever.
_clients: dict[tuple, object] = {}
_clients_lock = threading.Lock()
_MAX_CLIENTS = 32


def _client(cfg: LLMConfig):
    key = (cfg.kind, cfg.provider, cfg.api_key, cfg.base_url or "",
           os.getenv("AZURE_OPENAI_ENDPOINT", ""), os.getenv("AZURE_OPENAI_API_VERSION", ""))
    with _clients_lock:
        client = _clients.get(key)
        if client is None:
            if len(_clients) >= _MAX_CLIENTS:
                _clients.clear()
            client = _clients[key] = _new_client(cfg)
    return client


def _new_client(cfg: LLMConfig):
    if cfg.kind == "azure":
        from openai import AzureOpenAI

        return AzureOpenAI(
            api_key=cfg.api_key,
            azure_endpoint=cfg.base_url or os.getenv("AZURE_OPENAI_ENDPOINT", ""),
            api_version=os.getenv("AZURE_OPENAI_API_VERSION", "2024-10-21"),
        )
    from openai import OpenAI

    return OpenAI(
        # Local servers (Ollama/vLLM/LM Studio) validate nothing but the SDK
        # insists on a non-empty key.
        api_key=cfg.api_key or "not-needed",
        base_url=cfg.base_url or None,
        default_headers=_OPENROUTER_HEADERS if cfg.provider == "openrouter" else None,
    )


def _content(question: str, moments: list[dict], opts: dict) -> list[dict]:
    """Interleave the numbered labels with their frames, in moment order — the
    model has to know which image is [3]."""
    content: list[dict] = [{"type": "text", "text": intro(question, moments, **opts)}]
    for i, m in enumerate(moments, 1):
        content.append({"type": "text", "text": label(i, m)})
        if m.get("image"):
            uri = f"data:image/jpeg;base64,{base64.b64encode(downscale(m['image'])).decode()}"
            content.append({"type": "image_url", "image_url": {"url": uri}})
    return content


def _create(client, cfg: LLMConfig, messages: list[dict]):
    """chat.completions.create with graceful degradation on parameter quirks."""
    kwargs: dict = {"model": cfg.model, "messages": messages,
                    "temperature": 0.2, "max_tokens": cfg.max_tokens}
    for _ in range(3):
        try:
            return client.chat.completions.create(**kwargs)
        except Exception as exc:
            msg = str(exc).lower()
            # Reasoning-model families renamed the token cap.
            if "max_tokens" in msg and "max_completion_tokens" in msg \
                    and "max_tokens" in kwargs:
                kwargs["max_completion_tokens"] = kwargs.pop("max_tokens")
                continue
            # Some models only accept the default temperature.
            if "temperature" in msg and "temperature" in kwargs:
                kwargs.pop("temperature")
                continue
            raise
    return client.chat.completions.create(**kwargs)


def answer(cfg: LLMConfig, question: str, moments: list[dict],
           opts: dict | None = None) -> str:
    resp = _create(_client(cfg), cfg, [
        {"role": "system", "content": SYSTEM},
        {"role": "user", "content": _content(question, moments, opts or {})},
    ])
    return (resp.choices[0].message.content or "").strip()


def complete(cfg: LLMConfig, system: str, user: str, max_tokens: int = 1500) -> str:
    """Plain text in, plain text out — for the small helper calls (the multi-part
    question check) that share the answer model's provider and key."""
    resp = _create(_client(cfg), replace(cfg, max_tokens=max_tokens), [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ])
    return (resp.choices[0].message.content or "").strip()
