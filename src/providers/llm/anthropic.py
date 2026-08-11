"""Anthropic Messages API — images are content blocks, system is its own field."""
from __future__ import annotations

import base64

from .base import SYSTEM, LLMConfig, downscale, intro, label


def answer(cfg: LLMConfig, question: str, moments: list[dict]) -> str:
    import anthropic

    client = anthropic.Anthropic(api_key=cfg.api_key, base_url=cfg.base_url or None)
    blocks: list[dict] = [{"type": "text", "text": intro(question, moments)}]
    for i, m in enumerate(moments, 1):
        blocks.append({"type": "text", "text": label(i, m)})
        if m.get("image"):
            blocks.append({"type": "image", "source": {
                "type": "base64", "media_type": "image/jpeg",
                "data": base64.b64encode(downscale(m["image"])).decode()}})
    resp = client.messages.create(
        model=cfg.model,
        max_tokens=cfg.max_tokens,
        system=SYSTEM,          # a top-level parameter here, not a message
        messages=[{"role": "user", "content": blocks}],
    )
    return "".join(b.text for b in resp.content if b.type == "text").strip()
