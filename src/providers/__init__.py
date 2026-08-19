"""Pluggable model providers — every model MomentSearch talks to lives here.

    providers/registry.py   the table: endpoints, default models, dims, keys
    providers/llm/          multimodal LLMs (answer synthesis)
    providers/embed/        embedding models (retrieval), both branches

Nothing is imported eagerly. config.py reads providers/registry.py while being
imported by everything else, so an import in THIS file would be a cycle —
import the subpackages directly (`from .providers.llm import answer`).

Self-check what your .env resolves to, and optionally call every configured
model for real:

    python -m src.providers           # what's configured, what's installed
    python -m src.providers --live    # actually ping the models
"""
