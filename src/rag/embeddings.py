"""Back-compat shim — embeddings moved to src/providers/embed/.

Both branches are now provider-pluggable (visual: CLIP/Jina/Cohere/Voyage/
Gemini; transcript: fastembed/OpenAI/Gemini/Cohere/Voyage/Jina), which is more
than one module's worth of code:

    src/providers/embed/__init__.py   public API + in-process/service/api routing
    src/providers/embed/base.py       config resolution, dims, HTTP, normalizing
    src/providers/embed/<provider>.py one module per provider

`from .embeddings import embed_query, embed_text` keeps working — same names,
same signatures. New code should import src.providers.embed directly.
"""
from __future__ import annotations

import numpy as np

from ..providers.embed import (describe, embed_docs, embed_docs_local,
                               embed_jpegs, embed_jpegs_local, embed_query,
                               embed_query_local, embed_text, embed_text_local,
                               embedding_dim, image_config, image_dim,
                               text_config, text_dim)

__all__ = ["embed_docs", "embed_docs_local", "embed_jpegs", "embed_jpegs_local",
           "embed_query", "embed_query_local", "embed_text", "embed_text_local",
           "embedding_dim", "image_dim", "text_dim", "describe", "image_config",
           "text_config", "embed_openai"]


def embed_openai(texts: list[str]) -> np.ndarray:
    """Deprecated: the OpenAI text-embeddings path is now one provider among
    several. Kept so older callers don't break — prefer embed_docs/embed_query,
    which route to whatever TEXT_EMBED_PROVIDER names."""
    from ..providers.embed.openai_text import embed_docs as _openai_docs

    return _openai_docs(texts, text_config())
