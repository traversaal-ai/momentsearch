"""Cross-encoder reranking — the accuracy fix for RRF's rank-blindness.

RRF (src/rag/search.py._fuse) orders by branch RANK and throws away how relevant
a hit actually is, so a spurious top-1 in one branch can outrank the real answer
that sits one rank lower in another. A cross-encoder RE-READS each
(question, transcript) pair together and returns a TRUE relevance score;
search.py squashes it to 0-1 and blends it with the moment's RRF standing to
reorder the text-bearing moments.

TEXT ONLY (for now): it judges moments that HAVE a transcript. Frame-only moments
carry no text, so they keep their RRF standing untouched — captioning frames (a
VLM pass at ingest) would let the same reranker cover them too, on one scale.

Providers (RERANK_PROVIDER):
  fastembed (default) -> local ONNX cross-encoder, no API key, CPU. Reuses the
                         fastembed dependency already used for text embeddings.
  cohere              -> Cohere Rerank API (RERANK_API_KEY / COHERE_API_KEY).
score() raises on any failure; the caller (search.py._rerank) logs it and falls
back to the unreranked order, so reranking can never break a query.
"""
from __future__ import annotations

from functools import lru_cache

from .. import config


def score(query: str, docs: list[str]) -> list[float]:
    """One relevance score per doc (higher = more relevant), aligned to `docs`.
    Raw model scale — search.py squashes to 0-1; only the ordering matters."""
    if not docs:
        return []
    if config.RERANK_PROVIDER == "fastembed":
        return _fastembed(query, docs)
    if config.RERANK_PROVIDER == "cohere":
        return _cohere(query, docs)
    raise ValueError(f"unknown RERANK_PROVIDER {config.RERANK_PROVIDER!r}")


@lru_cache(maxsize=1)
def _fastembed_model(name: str):
    # First use downloads the ONNX model (~90MB) into fastembed's cache, then
    # it's warm for the process. Kept lazy so importing this module is cheap.
    from fastembed.rerank.cross_encoder import TextCrossEncoder
    return TextCrossEncoder(model_name=name)


def _fastembed(query: str, docs: list[str]) -> list[float]:
    model = _fastembed_model(config.RERANK_MODEL)
    return [float(s) for s in model.rerank(query, docs)]


def warm() -> None:
    """Preload the local reranker at startup so the FIRST query doesn't pay its
    cold cost — the ~90MB model download, load, AND the ONNX session's slow
    first inference. A one-doc dummy rerank triggers all three.

    No-op unless reranking is ON and local: the cohere provider is a network API
    with no weights to warm. Never raises — a failed warm just leaves the model
    to load lazily on first query (the old behaviour), so boot can't break."""
    if not config.ENABLE_RERANK or config.RERANK_PROVIDER != "fastembed":
        return
    try:
        _fastembed("warm up", ["warm up"])   # download + load + one inference
        print(f"[rerank] {config.RERANK_MODEL} warm", flush=True)
    except Exception as exc:
        print(f"[rerank] warm-up skipped ({type(exc).__name__}: {exc}) — "
              f"loads lazily on first query", flush=True)


def _cohere(query: str, docs: list[str]) -> list[float]:
    import json
    import urllib.request

    if not config.RERANK_API_KEY:
        raise RuntimeError("RERANK_PROVIDER=cohere but no RERANK_API_KEY / "
                           "COHERE_API_KEY is set.")
    body = json.dumps({"model": config.RERANK_MODEL, "query": query,
                       "documents": docs}).encode("utf-8")
    req = urllib.request.Request(
        (config.RERANK_BASE_URL or "https://api.cohere.com") + "/v2/rerank",
        data=body, headers={"Authorization": f"Bearer {config.RERANK_API_KEY}",
                            "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        data = json.loads(r.read())
    # results: [{index, relevance_score}] — realign to the input doc order.
    out = [0.0] * len(docs)
    for item in data.get("results", []):
        out[int(item["index"])] = float(item["relevance_score"])
    return out
