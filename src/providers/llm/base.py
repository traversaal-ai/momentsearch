"""Shared pieces of every LLM adapter: the config object, the prompt, and the
one image transform that decides what a call costs.

The adapters below this module (openai_compat / anthropic / gemini) differ only
in wire format. Everything that affects ANSWER QUALITY lives here, so switching
provider changes cost and latency — never the instructions, the moment
numbering, or the image size.
"""
from __future__ import annotations

import io
import os
from dataclasses import dataclass, replace

from ... import config
from ..registry import llm_preset, llm_provider_key

# Kept for backwards compatibility — src/llm.py re-exported this constant, and
# it is now just the nvidia preset's base_url.
NVIDIA_BASE_URL = "https://integrate.api.nvidia.com/v1"

SYSTEM = (
    "You answer a user's question about a video using the numbered moments "
    "provided as your evidence. Each moment has a timestamp and may include a "
    "video FRAME (what was shown on screen) and/or a TRANSCRIPT excerpt (what was "
    "said out loud). Use BOTH kinds of evidence: for a question about what someone "
    "SAID or talked about, read the transcript text; for a question about what is "
    "SHOWN, read the frame.\n"
    "Rules:\n"
    "1. Read the question carefully and answer exactly what is asked. Start with a "
    "one-line direct answer, then explain in short paragraphs — ONE paragraph per "
    "distinct point. Keep it focused, don't pad. No preamble, don't restate the "
    "question.\n"
    "2. Ground every claim in the moments and cite the moment number(s) in square "
    "brackets, e.g. [1] or [2, 3]. When the question is about what was said, quote "
    "the transcript accurately — keep the actual wording and numbers, don't alter "
    "or round them.\n"
    "3. Group the relevant moments by the point they make:\n"
    "   - Moments that make the SAME point (especially several from the same "
    "video) belong TOGETHER in ONE paragraph, cited together, e.g. [1, 2]. Do not "
    "split one shared point across separate paragraphs.\n"
    "   - Moments that make DIFFERENT points, or come from different videos, go in "
    "SEPARATE paragraphs, each with its own citation.\n"
    "   Cover every distinct relevant point — don't merge unrelated ones and don't "
    "drop any.\n"
    "4. Don't use outside knowledge or invent details that aren't in the moments.\n"
    "5. Abstain ONLY as a last resort: if — and only if — none of the moments are "
    "relevant to the question at all, reply with a single sentence saying you "
    "couldn't find it in the video. If even one moment is relevant, ANSWER from "
    "it; do not refuse just because the match is partial."
)


@dataclass
class LLMConfig:
    """A fully-resolved model handle. `provider` is a registry key; everything
    else is either explicit or filled from that provider's preset."""
    provider: str = "openai"
    model: str = ""
    api_key: str = ""
    base_url: str = ""
    max_tokens: int = 1024

    @property
    def kind(self) -> str:
        """Which wire dialect to speak: openai | azure | anthropic | gemini."""
        return llm_preset(self.provider).kind

    @property
    def label(self) -> str:
        return llm_preset(self.provider).label


def resolve(cfg: LLMConfig) -> LLMConfig:
    """Fill the blanks from the provider's preset.

    Explicit values always win — base_url in particular, so `provider=openai`
    plus your own base_url still reaches your own server. This is what makes a
    provider name enough on its own: `LLM_PROVIDER=xai` + `XAI_API_KEY` needs
    no endpoint and no model id.
    """
    preset = llm_preset(cfg.provider)
    return replace(
        cfg,
        provider=llm_provider_key(cfg.provider),
        model=cfg.model or preset.default_model,
        base_url=cfg.base_url or preset.base_url,
    )


def is_self_hosted(cfg: LLMConfig) -> bool:
    """True when base_url points somewhere the USER chose.

    A key-less endpoint is only plausible for a server they run, so this is what
    lets an API key be optional. It must not be satisfied by the preset's own
    base_url: every hosted provider ships one, and treating that as "endpoint
    supplied" would turn a missing key into a 401 mid-answer instead of a
    message naming the env var to set.
    """
    return bool(cfg.base_url) and cfg.base_url != llm_preset(cfg.provider).base_url


def missing_requirement(cfg: LLMConfig) -> str | None:
    """Why this config can't work yet, in words a user can act on (None = fine).
    Checked before the call so the failure names the fix, not a 401."""
    preset = llm_preset(cfg.provider)
    if not cfg.model:
        return (f"{preset.label}: no model set. Set LLM_MODEL"
                + (f" — {preset.notes}" if preset.notes else "."))
    if preset.requires_key and not cfg.api_key and not is_self_hosted(cfg):
        envs = " or ".join(preset.key_envs) or "LLM_API_KEY"
        return f"{preset.label}: no API key. Set {envs} (or LLM_API_KEY)."
    if preset.kind == "azure" and not (cfg.base_url or os.getenv("AZURE_OPENAI_ENDPOINT")):
        return ("Azure OpenAI: no endpoint. Set AZURE_OPENAI_ENDPOINT (or "
                "LLM_BASE_URL) to https://<resource>.openai.azure.com.")
    if cfg.provider == "custom" and not cfg.base_url:
        return ("Custom provider: no endpoint. Set LLM_BASE_URL to your "
                "OpenAI-compatible server, or name a known provider "
                "(see /api/providers).")
    return None


# ── Prompt assembly (identical across providers) ──────────────────────────────

def intro(question: str, n: int) -> str:
    return (
        f"QUESTION: {question}\n\n"
        f"Answer this question using the {n} moments below (numbered 1 to {n}). "
        "Each has a timestamp and a video frame and/or a transcript excerpt. If "
        "the question is about what was said, use the transcript text. Give a "
        "direct answer grounded in the relevant moment(s), cited as [n]. Only say "
        "you couldn't find it if none of the moments are relevant."
    )


def label(i: int, m: dict) -> str:
    line = f"[{i}] @ {m.get('timestamp', '')}"
    if m.get("transcript"):
        line += f' transcript: "{m["transcript"]}"'
    if m.get("image") is None:
        line += " (transcript only, no frame)"
    return line


def downscale(jpeg: bytes) -> bytes:
    """Shrink a frame before it becomes LLM image tokens.

    The single biggest cost lever in the whole read path: TOP_K frames at full
    thumbnail size can be several times the tokens of the same frames at
    LLM_IMAGE_MAX_PX, for no measurable answer-quality gain.
    """
    from PIL import Image

    img = Image.open(io.BytesIO(jpeg))
    if max(img.size) <= config.LLM_IMAGE_MAX_PX:
        return jpeg
    img.thumbnail((config.LLM_IMAGE_MAX_PX, config.LLM_IMAGE_MAX_PX))
    buf = io.BytesIO()
    img.convert("RGB").save(buf, format="JPEG", quality=80)
    return buf.getvalue()
