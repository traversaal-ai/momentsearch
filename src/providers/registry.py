"""Provider registry — the plug-and-play table. Pure data, zero imports.

Every model MomentSearch can talk to is one row in one of three tables:

  LLM_PRESETS           multimodal LLMs that write the cited answer
  IMAGE_EMBED_PRESETS   joint image+text embedders — the VISUAL branch
  TEXT_EMBED_PRESETS    text embedders — the TRANSCRIPT branch

A preset is just defaults: endpoint, model, which env var holds the key, the
vector dimension. Nothing here is required — `provider=custom` (or any preset
plus an explicit base_url) points at whatever OpenAI-compatible server you
run. Adding a hosted provider that speaks a known dialect means adding a row,
not writing code.

Two rules worth knowing:

  * The visual branch needs a JOINT space. Search is text->image: the question
    is embedded and compared against frames, so an image embedder must also
    embed text into the SAME space. That is why OpenAI is absent from
    IMAGE_EMBED_PRESETS (no image embeddings) while CLIP, Jina, Cohere, Voyage
    and Gemini are present.
  * `threshold` is the suggested confidence gate for that branch. Cosine
    scales are model-specific (CLIP text->image lands ~0.2-0.35, text-to-text
    models ~0.5-0.7), so a threshold calibrated for one model over-abstains on
    another. 0.0 means "unmeasured for this provider — gate off", which is the
    safe direction: it never abstains wrongly. Measure with benchmark/score.py
    and set CONFIDENCE_THRESHOLD / TEXT_CONFIDENCE_THRESHOLD yourself.

Kept import-free on purpose: config.py reads this table, and config.py is
imported by everything, so any import here would be a cycle.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping

# ── Multimodal LLMs ───────────────────────────────────────────────────────────


@dataclass(frozen=True)
class LLMPreset:
    """One answer-synthesis provider.

    kind is the client dialect, NOT the brand — most of the industry speaks
    OpenAI Chat Completions, so most rows share kind="openai" and differ only
    by base_url. Three dialects need their own adapter: azure (different
    client construction), anthropic, gemini.
    """
    label: str
    kind: str                              # openai | azure | anthropic | gemini
    base_url: str = ""                     # "" = the SDK's own default
    default_model: str = ""                # "" = user must set LLM_MODEL
    key_envs: tuple[str, ...] = ()         # checked in order when LLM_API_KEY is unset
    requires_key: bool = True              # False for localhost servers
    sdk: str = "openai"                    # pip package the adapter imports
    notes: str = ""


LLM_PRESETS: Mapping[str, LLMPreset] = {
    # --- Hosted, OpenAI dialect ----------------------------------------------
    "openai": LLMPreset(
        label="OpenAI", kind="openai", default_model="gpt-4o-mini",
        key_envs=("OPENAI_API_KEY",),
        notes="Also the generic OpenAI-compatible client: set LLM_BASE_URL to "
              "reach any server that speaks Chat Completions."),
    "openrouter": LLMPreset(
        label="OpenRouter", kind="openai", base_url="https://openrouter.ai/api/v1",
        default_model="openai/gpt-4o-mini", key_envs=("OPENROUTER_API_KEY",),
        notes="One key, hundreds of models. Model ids are namespaced: "
              "'google/gemini-3.6-flash', 'anthropic/claude-sonnet-5', ..."),
    "xai": LLMPreset(
        label="xAI (Grok)", kind="openai", base_url="https://api.x.ai/v1",
        default_model="grok-4.5", key_envs=("XAI_API_KEY", "GROK_API_KEY")),
    "groq": LLMPreset(
        label="Groq", kind="openai", base_url="https://api.groq.com/openai/v1",
        key_envs=("GROQ_API_KEY",),
        notes="Very fast. Set LLM_MODEL to a CURRENT vision model — Groq's "
              "catalogue rotates, so there is no safe default here."),
    "together": LLMPreset(
        label="Together AI", kind="openai", base_url="https://api.together.xyz/v1",
        default_model="Qwen/Qwen2.5-VL-72B-Instruct", key_envs=("TOGETHER_API_KEY",)),
    "fireworks": LLMPreset(
        label="Fireworks AI", kind="openai",
        base_url="https://api.fireworks.ai/inference/v1",
        key_envs=("FIREWORKS_API_KEY",),
        notes="Set LLM_MODEL, e.g. accounts/fireworks/models/qwen2p5-vl-32b-instruct"),
    "mistral": LLMPreset(
        label="Mistral", kind="openai", base_url="https://api.mistral.ai/v1",
        default_model="pixtral-12b-2409", key_envs=("MISTRAL_API_KEY",)),
    "nvidia": LLMPreset(
        label="NVIDIA NIM", kind="openai",
        base_url="https://integrate.api.nvidia.com/v1",
        default_model="meta/llama-3.2-11b-vision-instruct",
        key_envs=("NVIDIA_API_KEY",)),
    "gemini_openai": LLMPreset(
        label="Gemini (OpenAI-compatible endpoint)", kind="openai",
        base_url="https://generativelanguage.googleapis.com/v1beta/openai/",
        default_model="gemini-3.6-flash",
        key_envs=("GEMINI_API_KEY", "GOOGLE_API_KEY"),
        notes="Gemini through the OpenAI SDK — no extra dependency. Use "
              "provider=gemini for the native SDK instead."),

    # --- Hosted, own dialect --------------------------------------------------
    "gemini": LLMPreset(
        label="Google Gemini (native SDK)", kind="gemini",
        default_model="gemini-3.6-flash",
        key_envs=("GEMINI_API_KEY", "GOOGLE_API_KEY"), sdk="google-genai"),
    "anthropic": LLMPreset(
        label="Anthropic Claude", kind="anthropic", default_model="claude-sonnet-5",
        key_envs=("ANTHROPIC_API_KEY",), sdk="anthropic"),
    "azure_openai": LLMPreset(
        label="Azure OpenAI", kind="azure",
        key_envs=("AZURE_OPENAI_API_KEY", "OPENAI_API_KEY"),
        notes="LLM_MODEL is your DEPLOYMENT name. Endpoint comes from "
              "LLM_BASE_URL or AZURE_OPENAI_ENDPOINT; api-version from "
              "AZURE_OPENAI_API_VERSION."),

    # --- Self-hosted (no key needed) ------------------------------------------
    "ollama": LLMPreset(
        label="Ollama", kind="openai", base_url="http://localhost:11434/v1",
        default_model="qwen2.5vl", requires_key=False,
        notes="Pull a VISION model first: `ollama pull qwen2.5vl`."),
    "lmstudio": LLMPreset(
        label="LM Studio", kind="openai", base_url="http://localhost:1234/v1",
        requires_key=False, notes="Set LLM_MODEL to the loaded model's id."),
    "vllm": LLMPreset(
        label="vLLM", kind="openai", base_url="http://localhost:8000/v1",
        requires_key=False,
        notes="LLM_MODEL is whatever you served with --model."),
    "custom": LLMPreset(
        label="Custom OpenAI-compatible endpoint", kind="openai",
        requires_key=False,
        notes="Bring your own: set LLM_BASE_URL (+ LLM_API_KEY if it wants one)."),
}

# Spellings people actually type -> canonical key. Dashes are normalized to
# underscores before lookup, so "lm-studio" and "lm_studio" both land here.
LLM_ALIASES: Mapping[str, str] = {
    "grok": "xai",
    "x_ai": "xai",
    "google": "gemini",
    "google_genai": "gemini",
    "googleai": "gemini",
    "gemini_compat": "gemini_openai",
    "google_openai": "gemini_openai",
    "claude": "anthropic",
    "azure": "azure_openai",
    "openai_azure": "azure_openai",
    "open_router": "openrouter",
    "lm_studio": "lmstudio",
    "llamacpp": "custom",
    "llama_cpp": "custom",
    "localai": "custom",
    "oai": "openai",
    "openai_compatible": "custom",
}


# ── Embedding models ──────────────────────────────────────────────────────────

@dataclass(frozen=True)
class EmbedPreset:
    """One embedding provider for one branch.

    modality:
      "joint" — embeds images AND text into a shared space (visual branch).
      "text"  — text only (transcript branch).
    local:
      True  = weights run in this process (or in the warm embed service).
      False = an HTTP API; the embed service is bypassed entirely.
    dims:
      model id -> vector size, so the API can create the Qdrant collection at
      boot WITHOUT downloading a model. Unknown model + default_dim 0 = load
      the model to measure (local only) or fail with a "set *_DIM" message.
    """
    label: str
    kind: str                              # implementation module key
    modality: str                          # joint | text
    local: bool = False
    base_url: str = ""
    default_model: str = ""
    dims: Mapping[str, int] = field(default_factory=dict)
    default_dim: int = 0                   # used when the model isn't in dims
    dim_choices: tuple[int, ...] = ()      # Matryoshka models: truncatable sizes
    key_envs: tuple[str, ...] = ()
    requires_key: bool = True
    sdk: str = ""                          # pip package, "" = stdlib HTTP only
    threshold: float = 0.0                 # suggested confidence gate (0 = off)
    notes: str = ""


# Visual branch — must be a JOINT text+image space (search is text->image).
IMAGE_EMBED_PRESETS: Mapping[str, EmbedPreset] = {
    "clip": EmbedPreset(
        label="CLIP (local, sentence-transformers)", kind="clip", modality="joint",
        local=True, default_model="clip-ViT-B-32", requires_key=False,
        sdk="sentence-transformers", threshold=0.2,
        dims={"clip-ViT-B-32": 512, "clip-ViT-B-16": 512,
              "clip-ViT-L-14": 768, "clip-ViT-L-14-336": 768},
        notes="The default: free, offline, CPU-fine. Runs in-process or behind "
              "EMBED_SERVICE_URL. Any sentence-transformers CLIP checkpoint works."),
    "jina": EmbedPreset(
        label="Jina CLIP (API)", kind="jina", modality="joint",
        base_url="https://api.jina.ai/v1/embeddings", default_model="jina-clip-v2",
        dims={"jina-clip-v2": 1024, "jina-clip-v1": 768, "jina-embeddings-v4": 2048},
        dim_choices=(64, 128, 256, 512, 768, 1024), key_envs=("JINA_API_KEY",),
        notes="Multilingual, 89 languages, Matryoshka — set IMAGE_EMBED_DIM=512 "
              "to halve storage at a small quality cost."),
    "cohere": EmbedPreset(
        label="Cohere Embed v4 (API)", kind="cohere", modality="joint",
        base_url="https://api.cohere.com/v2/embed", default_model="embed-v4.0",
        dims={"embed-v4.0": 1536}, dim_choices=(256, 512, 1024, 1536),
        key_envs=("COHERE_API_KEY",),
        notes="Strong on text-heavy frames (slides, charts, screenshots)."),
    "voyage": EmbedPreset(
        label="Voyage multimodal (API)", kind="voyage", modality="joint",
        base_url="https://api.voyageai.com/v1/multimodalembeddings",
        default_model="voyage-multimodal-3.5",
        dims={"voyage-multimodal-3.5": 1024, "voyage-multimodal-3": 1024},
        dim_choices=(256, 512, 1024, 2048), key_envs=("VOYAGE_API_KEY",),
        notes="One backbone for both modalities instead of two towers — less "
              "same-modality bias than CLIP."),
    "gemini": EmbedPreset(
        label="Gemini multimodal embeddings", kind="gemini", modality="joint",
        default_model="gemini-embedding-2", default_dim=1536,
        dims={"gemini-embedding-2": 1536},
        dim_choices=(128, 256, 512, 768, 1536, 3072),
        key_envs=("GEMINI_API_KEY", "GOOGLE_API_KEY"), sdk="google-genai",
        notes="Text, images, video and audio in ONE unified space. Vectors "
              "below 3072 dims arrive unnormalized — we L2-normalize them."),
}

# Transcript branch — text only.
TEXT_EMBED_PRESETS: Mapping[str, EmbedPreset] = {
    "fastembed": EmbedPreset(
        label="bge via fastembed (local, ONNX)", kind="fastembed", modality="text",
        local=True, default_model="BAAI/bge-small-en-v1.5", requires_key=False,
        sdk="fastembed", threshold=0.35,
        dims={"BAAI/bge-small-en-v1.5": 384, "BAAI/bge-base-en-v1.5": 768,
              "BAAI/bge-large-en-v1.5": 1024, "BAAI/bge-small-en": 384,
              "sentence-transformers/all-MiniLM-L6-v2": 384,
              "jinaai/jina-embeddings-v2-base-en": 768,
              "intfloat/multilingual-e5-large": 1024},
        notes="The default: no key, no torch, CPU, free. Keeps a fresh clone "
              "searchable with zero credentials."),
    "openai": EmbedPreset(
        label="OpenAI / OpenAI-compatible embeddings", kind="openai", modality="text",
        default_model="text-embedding-3-small",
        dims={"text-embedding-3-small": 1536, "text-embedding-3-large": 3072,
              "text-embedding-ada-002": 1536},
        key_envs=("OPENAI_API_KEY",), sdk="openai", threshold=0.35,
        notes="Set TEXT_EMBED_BASE_URL to use any OpenAI-compatible embeddings "
              "server (vLLM, TEI, Together, DeepInfra, LM Studio)."),
    "gemini": EmbedPreset(
        label="Gemini text embeddings", kind="gemini", modality="text",
        default_model="gemini-embedding-2", default_dim=1536,
        dims={"gemini-embedding-2": 1536, "gemini-embedding-001": 1536,
              "text-embedding-004": 768},
        dim_choices=(128, 256, 512, 768, 1536, 3072),
        key_envs=("GEMINI_API_KEY", "GOOGLE_API_KEY"), sdk="google-genai",
        threshold=0.35),
    "cohere": EmbedPreset(
        label="Cohere Embed v4 (text)", kind="cohere", modality="text",
        base_url="https://api.cohere.com/v2/embed", default_model="embed-v4.0",
        dims={"embed-v4.0": 1536, "embed-multilingual-v3.0": 1024,
              "embed-english-v3.0": 1024},
        dim_choices=(256, 512, 1024, 1536), key_envs=("COHERE_API_KEY",),
        threshold=0.35),
    "voyage": EmbedPreset(
        label="Voyage text embeddings", kind="voyage", modality="text",
        base_url="https://api.voyageai.com/v1/embeddings",
        default_model="voyage-3.5",
        dims={"voyage-3.5": 1024, "voyage-3.5-lite": 1024, "voyage-3": 1024,
              "voyage-3-large": 1024, "voyage-law-2": 1024, "voyage-code-3": 1024},
        dim_choices=(256, 512, 1024, 2048), key_envs=("VOYAGE_API_KEY",),
        threshold=0.35),
    "jina": EmbedPreset(
        label="Jina text embeddings", kind="jina", modality="text",
        base_url="https://api.jina.ai/v1/embeddings",
        default_model="jina-embeddings-v3",
        dims={"jina-embeddings-v3": 1024, "jina-embeddings-v4": 2048,
              "jina-clip-v2": 1024},
        dim_choices=(64, 128, 256, 512, 768, 1024), key_envs=("JINA_API_KEY",),
        threshold=0.35),
}

EMBED_ALIASES: Mapping[str, str] = {
    "sentence_transformers": "clip",
    "st": "clip",
    "local": "clip",
    "bge": "fastembed",
    "onnx": "fastembed",
    "google": "gemini",
    "vertex": "gemini",
    "jinaai": "jina",
    "voyageai": "voyage",
    "azure": "openai",
    "openai_compatible": "openai",
    "vllm": "openai",
    "tei": "openai",
}


# ── Lookup helpers (used by config.py, the adapters, and the doctor CLI) ──────

def normalize(name: str) -> str:
    """Fold user spelling into a registry key: case, dashes, spaces."""
    return (name or "").strip().lower().replace("-", "_").replace(" ", "_")


def llm_preset(name: str) -> LLMPreset:
    """Preset for an LLM provider name. Unknown names fall back to the generic
    OpenAI-compatible client rather than crashing — a brand-new provider with
    an OpenAI-shaped API then works with base_url alone."""
    key = normalize(name)
    key = LLM_ALIASES.get(key, key)
    return LLM_PRESETS.get(key) or LLM_PRESETS["custom"]


def llm_provider_key(name: str) -> str:
    key = normalize(name)
    key = LLM_ALIASES.get(key, key)
    return key if key in LLM_PRESETS else "custom"


def _embed_key(name: str, table: Mapping[str, EmbedPreset], default: str) -> str:
    key = normalize(name)
    key = EMBED_ALIASES.get(key, key)
    return key if key in table else default


def image_embed_key(name: str) -> str:
    return _embed_key(name, IMAGE_EMBED_PRESETS, "clip")


def text_embed_key(name: str) -> str:
    return _embed_key(name, TEXT_EMBED_PRESETS, "fastembed")


def image_embed_preset(name: str) -> EmbedPreset:
    return IMAGE_EMBED_PRESETS[image_embed_key(name)]


def text_embed_preset(name: str) -> EmbedPreset:
    return TEXT_EMBED_PRESETS[text_embed_key(name)]


def is_known_llm(name: str) -> bool:
    """Whether a provider name is spelled correctly. Unknown names still RESOLVE
    (to the generic OpenAI-compatible client, or to the default embedder), which
    is convenient but silent — callers use this to reject or warn about typos
    instead of quietly running something else."""
    n = normalize(name)
    return n in LLM_PRESETS or n in LLM_ALIASES


def is_known_image_embed(name: str) -> bool:
    n = normalize(name)
    return n in IMAGE_EMBED_PRESETS or (
        n in EMBED_ALIASES and EMBED_ALIASES[n] in IMAGE_EMBED_PRESETS)


def is_known_text_embed(name: str) -> bool:
    n = normalize(name)
    return n in TEXT_EMBED_PRESETS or (
        n in EMBED_ALIASES and EMBED_ALIASES[n] in TEXT_EMBED_PRESETS)


def preset_dim(preset: EmbedPreset, model: str) -> int:
    """Vector size for (preset, model), or 0 when it can only be measured."""
    return preset.dims.get(model, preset.default_dim)


LLM_PROVIDERS: tuple[str, ...] = tuple(LLM_PRESETS)
IMAGE_EMBED_PROVIDERS: tuple[str, ...] = tuple(IMAGE_EMBED_PRESETS)
TEXT_EMBED_PROVIDERS: tuple[str, ...] = tuple(TEXT_EMBED_PRESETS)
