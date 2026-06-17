"""Bring-Your-Own-LLM answer synthesis.

MomentSearch retrieves the most relevant *frames* for a question and then asks a
vision-capable LLM to read those frames and answer, citing each frame as [n].

Providers supported out of the box:
  * "openai"    — the OpenAI Chat Completions API, which also covers every
                  OpenAI-compatible server (Ollama, vLLM, LM Studio, Together,
                  Groq, OpenRouter, …) via LLM_BASE_URL.
  * "nvidia"    — NVIDIA NIM / build.nvidia.com hosted vision models
                  (e.g. meta/llama-3.2-90b-vision-instruct). OpenAI-compatible,
                  so it uses the same client with NVIDIA's endpoint pre-filled.
  * "anthropic" — the Anthropic Messages API.

The provider SDKs are optional; we import them lazily and only the one you use.
"""
from __future__ import annotations

import base64
from pathlib import Path

from .config import get_settings

# NVIDIA's hosted inference endpoint (OpenAI-compatible).
NVIDIA_BASE_URL = "https://integrate.api.nvidia.com/v1"

SYSTEM = (
    "You answer questions about a video using ONLY the numbered frames provided. "
    "The frames are stills sampled from the video at specific timestamps. "
    "Describe what is visibly shown. Cite every claim with the frame number(s) "
    "in square brackets, e.g. [1] or [2, 3]. If the frames do not show enough to "
    "answer, say so plainly — never invent detail that isn't visible."
)


def _data_uri(path: Path) -> str:
    b64 = base64.b64encode(path.read_bytes()).decode()
    return f"data:image/jpeg;base64,{b64}"


def _prompt(question: str, n: int) -> str:
    return (
        f"Question: {question}\n\n"
        f"You are given {n} frames, numbered 1 to {n} in order. "
        "Answer the question from what is visible, citing frames as [n]."
    )


def answer(question: str, frame_paths: list[Path]) -> str:
    """Synthesize a cited answer from the retrieved frames. Raises if no LLM is configured."""
    s = get_settings()
    if not s.llm_configured:
        raise RuntimeError(
            "No LLM configured. Set LLM_API_KEY (and optionally LLM_BASE_URL) in .env "
            "to enable answer synthesis. Retrieval/citations work without it."
        )
    if s.LLM_PROVIDER == "anthropic":
        return _answer_anthropic(s, question, frame_paths)
    return _answer_openai(s, question, frame_paths)


def _base_url(s) -> str | None:
    """Resolve the OpenAI-compatible base URL. An explicit LLM_BASE_URL always
    wins; otherwise the provider name can imply one (e.g. nvidia)."""
    if s.LLM_BASE_URL:
        return s.LLM_BASE_URL
    if s.LLM_PROVIDER == "nvidia":
        return NVIDIA_BASE_URL
    return None


def _answer_openai(s, question: str, frame_paths: list[Path]) -> str:
    from openai import OpenAI

    client = OpenAI(api_key=s.LLM_API_KEY or "not-needed", base_url=_base_url(s))
    content: list[dict] = [{"type": "text", "text": _prompt(question, len(frame_paths))}]
    for p in frame_paths:
        content.append({"type": "image_url", "image_url": {"url": _data_uri(p)}})
    resp = client.chat.completions.create(
        model=s.LLM_MODEL,
        messages=[{"role": "system", "content": SYSTEM},
                  {"role": "user", "content": content}],
        temperature=0.2,
    )
    return (resp.choices[0].message.content or "").strip()


def _answer_anthropic(s, question: str, frame_paths: list[Path]) -> str:
    import anthropic

    client = anthropic.Anthropic(api_key=s.LLM_API_KEY,
                                 base_url=s.LLM_BASE_URL or None)
    blocks: list[dict] = [{"type": "text", "text": _prompt(question, len(frame_paths))}]
    for p in frame_paths:
        blocks.append({"type": "image", "source": {
            "type": "base64", "media_type": "image/jpeg",
            "data": base64.b64encode(p.read_bytes()).decode()}})
    resp = client.messages.create(
        model=s.LLM_MODEL,
        max_tokens=1024,
        system=SYSTEM,
        messages=[{"role": "user", "content": blocks}],
    )
    return "".join(b.text for b in resp.content if b.type == "text").strip()
