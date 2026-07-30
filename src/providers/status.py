"""What's configured, what's installed, what's missing — the plug-and-play check.

Feeds three consumers with the same data: GET /api/providers (so a settings UI
can render a provider picker), `python -m src.providers` (so a cloner can see
why their key isn't being picked up), and /api/config.

Nothing here calls a model. Everything is env + import-availability only, so it
is safe to hit on every request — use `--live` in the CLI for real calls.
"""
from __future__ import annotations

from importlib.util import find_spec

from .registry import (IMAGE_EMBED_PRESETS, LLM_ALIASES, LLM_PRESETS,
                       TEXT_EMBED_PRESETS, EmbedPreset, LLMPreset)

# pip name -> import name, for "is this optional dependency actually here?"
SDK_MODULES = {
    "openai": "openai",
    "anthropic": "anthropic",
    "google-genai": "google.genai",
    "sentence-transformers": "sentence_transformers",
    "fastembed": "fastembed",
}


def sdk_installed(sdk: str) -> bool:
    """True when the provider's SDK can be imported. Providers we reach over
    plain HTTP (Jina, Cohere, Voyage) declare no SDK and are always available."""
    if not sdk:
        return True
    module = SDK_MODULES.get(sdk, sdk.replace("-", "_"))
    try:
        # The FULL dotted path matters: `google` is a namespace package that
        # google-cloud-storage also provides, so checking only the first segment
        # would report google-genai as installed when it isn't.
        return find_spec(module) is not None
    except (ImportError, ValueError, ModuleNotFoundError):
        return False


def _aliases_of(name: str) -> list[str]:
    return sorted(a for a, target in LLM_ALIASES.items() if target == name)


def _llm_row(name: str, preset: LLMPreset) -> dict:
    return {
        "name": name,
        "label": preset.label,
        "dialect": preset.kind,
        "base_url": preset.base_url or None,
        "default_model": preset.default_model or None,
        "requires_key": preset.requires_key,
        "key_envs": list(preset.key_envs),
        "sdk": preset.sdk,
        "sdk_installed": sdk_installed(preset.sdk),
        "aliases": _aliases_of(name),
        "notes": preset.notes or None,
    }


def _embed_row(name: str, preset: EmbedPreset) -> dict:
    return {
        "name": name,
        "label": preset.label,
        "modality": preset.modality,
        "local": preset.local,
        "base_url": preset.base_url or None,
        "default_model": preset.default_model or None,
        "dims": dict(preset.dims),
        "dim_choices": list(preset.dim_choices),
        "requires_key": preset.requires_key,
        "key_envs": list(preset.key_envs),
        "sdk": preset.sdk or None,
        "sdk_installed": sdk_installed(preset.sdk),
        "suggested_threshold": preset.threshold,
        "notes": preset.notes or None,
    }


def llm_catalog() -> list[dict]:
    return [_llm_row(n, p) for n, p in LLM_PRESETS.items()]


def image_embed_catalog() -> list[dict]:
    return [_embed_row(n, p) for n, p in IMAGE_EMBED_PRESETS.items()]


def text_embed_catalog() -> list[dict]:
    return [_embed_row(n, p) for n, p in TEXT_EMBED_PRESETS.items()]


def active(measure: bool = False) -> dict:
    """What THIS deployment resolved to, secrets redacted.

    measure=True lets the embedding branches LOAD a local model to determine an
    unknown dimension — fine in the CLI, never in a request handler.
    """
    from . import embed, llm

    cfg = llm.env_config()
    return {
        "llm": (llm.describe(cfg) if cfg else
                {"provider": None, "ready": False,
                 "problem": "No server-wide answer model configured — "
                            "retrieval-only mode. Set LLM_PROVIDER + its API key."}),
        "embeddings": embed.describe(measure=measure),
    }


def catalog() -> dict:
    return {"llm": llm_catalog(),
            "image_embed": image_embed_catalog(),
            "text_embed": text_embed_catalog(),
            "active": active()}
