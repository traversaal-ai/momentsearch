"""Back-compat shim — the multimodal LLM layer moved to src/providers/llm/.

It grew from two providers into a registry of them (OpenAI, Gemini, Anthropic,
OpenRouter, xAI/Grok, Groq, Together, Fireworks, Mistral, NVIDIA, Azure, Ollama,
LM Studio, vLLM, custom), so it became a package:

    src/providers/registry.py   provider table: endpoints, default models, keys
    src/providers/llm/base.py   LLMConfig, the system prompt, image downscaling
    src/providers/llm/*.py      one module per wire dialect

`from . import llm` keeps working — same names, same signatures. New code should
import src.providers.llm directly.
"""
from __future__ import annotations

from .providers.llm import (NVIDIA_BASE_URL, PROVIDERS, SYSTEM, LLMConfig,
                            answer, complete, describe, env_config,
                            fit_local_context, from_row, is_local_runtime,
                            is_provider, missing_requirement, ping, resolve)

__all__ = ["NVIDIA_BASE_URL", "PROVIDERS", "SYSTEM", "LLMConfig", "answer",
           "complete", "describe", "env_config", "fit_local_context", "from_row",
           "is_local_runtime", "is_provider", "missing_requirement", "ping",
           "resolve"]
