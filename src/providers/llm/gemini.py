"""Google Gemini via the native google-genai SDK.

Gemini also exposes an OpenAI-compatible endpoint (provider=gemini_openai, no
extra dependency) — this adapter exists because the native API takes raw image
bytes instead of base64 data URIs, which avoids inflating every frame by a
third on the wire, and it carries the system prompt as a real
system_instruction.
"""
from __future__ import annotations

from .base import SYSTEM, LLMConfig, downscale, intro, label


def answer(cfg: LLMConfig, question: str, moments: list[dict],
           opts: dict | None = None) -> str:
    from google import genai
    from google.genai import types

    client = genai.Client(api_key=cfg.api_key)
    parts: list[types.Part] = [
        types.Part.from_text(text=intro(question, moments, **(opts or {})))]
    for i, m in enumerate(moments, 1):
        parts.append(types.Part.from_text(text=label(i, m)))
        if m.get("image"):
            parts.append(types.Part.from_bytes(data=downscale(m["image"]),
                                               mime_type="image/jpeg"))
    resp = client.models.generate_content(
        model=cfg.model,
        contents=[types.Content(role="user", parts=parts)],
        config=types.GenerateContentConfig(
            system_instruction=SYSTEM,
            temperature=0.2,
            max_output_tokens=cfg.max_tokens,
        ),
    )
    return (resp.text or "").strip()


def complete(cfg: LLMConfig, system: str, user: str, max_tokens: int = 1500) -> str:
    """Plain text in, plain text out — for the small helper calls (the multi-part
    question check) that share the answer model's provider and key."""
    from google import genai
    from google.genai import types

    client = genai.Client(api_key=cfg.api_key)
    resp = client.models.generate_content(
        model=cfg.model,
        contents=user,
        config=types.GenerateContentConfig(
            system_instruction=system,
            temperature=0.2,
            max_output_tokens=max_tokens,
        ),
    )
    return (resp.text or "").strip()
