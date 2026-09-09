"""Anthropic Messages API — images are content blocks, system is its own field."""
from __future__ import annotations

import base64

from .base import SYSTEM, LLMConfig, downscale, intro, label


def _client(cfg: LLMConfig):
    import anthropic

    return anthropic.Anthropic(api_key=cfg.api_key, base_url=cfg.base_url or None)


def answer(cfg: LLMConfig, question: str, moments: list[dict],
           opts: dict | None = None) -> str:
    blocks: list[dict] = [{"type": "text", "text": intro(question, moments, **(opts or {}))}]
    for i, m in enumerate(moments, 1):
        blocks.append({"type": "text", "text": label(i, m)})
        if m.get("image"):
            blocks.append({"type": "image", "source": {
                "type": "base64", "media_type": "image/jpeg",
                "data": base64.b64encode(downscale(m["image"])).decode()}})
    resp = _client(cfg).messages.create(
        model=cfg.model,
        max_tokens=cfg.max_tokens,
        system=SYSTEM,          # a top-level parameter here, not a message
        messages=[{"role": "user", "content": blocks}],
    )
    return "".join(b.text for b in resp.content if b.type == "text").strip()


def complete(cfg: LLMConfig, system: str, user: str, max_tokens: int = 1500) -> str:
    """Plain text in, plain text out — for the small helper calls (the multi-part
    question check) that share the answer model's provider and key."""
    resp = _client(cfg).messages.create(
        model=cfg.model,
        max_tokens=max_tokens,
        system=system,
        messages=[{"role": "user", "content": user}],
    )
    return "".join(b.text for b in resp.content if b.type == "text").strip()
