"""Multimodal LLM — cited answer synthesis from frames, per-tenant switchable.

Every call takes an LLMConfig. Where it comes from (resolved in
src/rag/search.py):
  1. the user's own model (ms_user_llms row — any provider in the registry, or
     a self-hosted vLLM/Ollama/LM Studio endpoint via base_url), or
  2. the server-wide LLM_* env config as the fallback.

The multimodal call is where latency and cost actually live (retrieval is
milliseconds), so frames are downscaled to LLM_IMAGE_MAX_PX before they are
sent and only TOP_K of them ever reach the model.

Dialects (src/providers/registry.py maps provider -> dialect):
  openai     Chat Completions — OpenAI, OpenRouter, xAI/Grok, Groq, Together,
             Fireworks, Mistral, NVIDIA NIM, Gemini's compat endpoint, Ollama,
             LM Studio, vLLM, and anything else OpenAI-shaped via base_url.
  azure      Azure OpenAI (same dialect, different client construction).
  gemini     Google Gemini, native google-genai SDK.
  anthropic  Anthropic Messages API.

Provider SDKs are imported lazily — only the one you actually use.
"""
from __future__ import annotations

import io

from ... import config
from ..registry import LLM_PROVIDERS, is_known_llm, llm_preset
from .base import (NVIDIA_BASE_URL, SYSTEM, LLMConfig, fit_local_context,
                   is_local_runtime, missing_requirement, resolve)

# Canonical provider names. Aliases ("grok", "claude", "azure", ...) are
# accepted everywhere too — see registry.LLM_ALIASES.
PROVIDERS: tuple[str, ...] = LLM_PROVIDERS

__all__ = ["PROVIDERS", "SYSTEM", "NVIDIA_BASE_URL", "LLMConfig", "answer",
           "complete", "ping", "env_config", "from_row", "describe",
           "is_provider", "missing_requirement", "resolve", "fit_local_context",
           "is_local_runtime"]


def is_provider(name: str) -> bool:
    """True when `name` (or an alias of it) is a known provider. Unknown names
    are NOT silently accepted at the API boundary — a typo'd provider would
    otherwise become a confusing 401 from the generic OpenAI client."""
    return is_known_llm(name)


def env_config() -> LLMConfig | None:
    """The server-wide fallback model from LLM_* env vars, if configured."""
    if not config.llm_configured():
        return None
    return resolve(LLMConfig(provider=config.LLM_PROVIDER, model=config.LLM_MODEL,
                             api_key=config.LLM_API_KEY, base_url=config.LLM_BASE_URL,
                             max_tokens=config.LLM_MAX_TOKENS))


def from_row(row: dict) -> LLMConfig:
    """A tenant's own model (ms_user_llms row). Their key only — a tenant never
    inherits the server's API key, just the provider's endpoint defaults."""
    return resolve(LLMConfig(provider=row.get("provider") or "openai",
                             model=row.get("model") or "",
                             api_key=row.get("api_key") or "",
                             base_url=row.get("base_url") or "",
                             max_tokens=config.LLM_MAX_TOKENS))


def describe(cfg: LLMConfig) -> dict:
    """Human-readable summary for /api/config and the doctor CLI (no secrets)."""
    preset = llm_preset(cfg.provider)
    return {"provider": cfg.provider, "label": preset.label, "dialect": preset.kind,
            "model": cfg.model, "base_url": cfg.base_url or None,
            "api_key_set": bool(cfg.api_key), "sdk": preset.sdk,
            "ready": missing_requirement(cfg) is None,
            "problem": missing_requirement(cfg)}


def _ready(cfg: LLMConfig) -> LLMConfig:
    cfg = resolve(cfg)
    problem = missing_requirement(cfg)
    if problem:
        raise RuntimeError(problem)
    return cfg


def _adapter(cfg: LLMConfig):
    kind = llm_preset(cfg.provider).kind
    if kind == "anthropic":
        from . import anthropic as adapter
    elif kind == "gemini":
        from . import gemini as adapter
    else:  # openai + azure share the Chat Completions adapter
        from . import openai_compat as adapter
    return adapter


def _sdk_error(cfg: LLMConfig, exc: ImportError) -> RuntimeError:
    # A missing optional SDK, not a model failure — name the fix.
    sdk = llm_preset(cfg.provider).sdk
    return RuntimeError(f"{llm_preset(cfg.provider).label} needs the '{sdk}' package: "
                        f"pip install {sdk} ({exc})")


def answer(question: str, moments: list[dict], cfg: LLMConfig,
           opts: dict | None = None) -> str:
    """Synthesize a cited answer from retrieved moments with `cfg`'s model.

    moments: [{"image": bytes|None, "transcript": str|None, "timestamp": str,
               "speaker"?, "context"?, "parts"?}]
    — each may carry a frame, a transcript excerpt, or both. `opts` are extra
    keyword arguments for the prompt preamble (base.intro): the multi-part path
    passes the sub-questions and which of them found nothing."""
    cfg = _ready(cfg)
    try:
        return _adapter(cfg).answer(cfg, question, moments, opts or {})
    except ImportError as exc:
        raise _sdk_error(cfg, exc) from exc


def complete(cfg: LLMConfig, system: str, user: str, max_tokens: int = 1500) -> str:
    """Plain text in, plain text out, on the same model and key as the answers.
    For the small helper calls around an answer — the multi-part question check
    (src/rag/query_split.py) — so a second provider is never needed. The cap is
    generous on purpose: it is a ceiling, not a spend, and a reasoning model
    needs the room to think before its short reply."""
    cfg = _ready(cfg)
    try:
        return _adapter(cfg).complete(cfg, system, user, max_tokens)
    except ImportError as exc:
        raise _sdk_error(cfg, exc) from exc


def ping(cfg: LLMConfig) -> str:
    """Connectivity + vision check: one tiny image, one word back. Raises with
    the provider's error on failure (surfaced to the settings UI)."""
    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (32, 32), (220, 40, 40)).save(buf, format="JPEG")
    return answer("Reply with the dominant color of moment 1, one word.",
                  [{"image": buf.getvalue(), "transcript": None,
                    "timestamp": "00:00"}], cfg)
