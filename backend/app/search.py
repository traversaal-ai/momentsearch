"""Query -> retrieve frames -> cited answer."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from .config import get_settings
from .embeddings import embed_text
from . import llm, vector_store


def _seconds(ms: int) -> str:
    s = ms // 1000
    return f"{s // 60:02d}:{s % 60:02d}"


def retrieve(question: str, top_k: int | None = None, video_id: str | None = None) -> list[dict[str, Any]]:
    """Top frames for a question, as numbered citations."""
    s = get_settings()
    k = top_k or s.TOP_K
    qvec = embed_text(question)
    hits = vector_store.search(qvec, top_k=k, video_id=video_id)
    citations = []
    for i, h in enumerate(hits, 1):
        citations.append({
            "n": i,
            "video_id": h.get("video_id"),
            "title": h.get("title"),
            "url": h.get("url"),
            "source": h.get("source"),
            "ms": h.get("ms", 0),
            "timestamp": _seconds(h.get("ms", 0)),
            "frame": h.get("frame"),
            "thumbnail": f"/api/frame/{h.get('frame')}",
            "deeplink": _deeplink(h),
            "score": round(h.get("score", 0.0), 4),
        })
    return citations


def _deeplink(hit: dict[str, Any]) -> str | None:
    """A jump-to-moment link: YouTube `?t=` for URLs, else the served clip."""
    url, source, ms = hit.get("url"), hit.get("source"), hit.get("ms", 0)
    secs = ms // 1000
    if source == "youtube" and url:
        sep = "&" if "?" in url else "?"
        return f"{url}{sep}t={secs}"
    return f"/api/video/{hit.get('video_id')}#t={secs}"


def ask(question: str, top_k: int | None = None, video_id: str | None = None) -> dict[str, Any]:
    citations = retrieve(question, top_k=top_k, video_id=video_id)
    s = get_settings()
    result: dict[str, Any] = {"question": question, "citations": citations}
    if not citations:
        result["answer"] = "No relevant moments were found. Try ingesting a video first."
        result["llm_used"] = False
        return result
    if not s.llm_configured:
        result["answer"] = None
        result["llm_used"] = False
        result["note"] = ("Showing the most relevant frames. Configure an LLM "
                           "(LLM_API_KEY in .env) to get a synthesized answer.")
        return result
    frame_paths = [s.frame_dir / c["frame"] for c in citations if c.get("frame")]
    result["answer"] = llm.answer(question, frame_paths)
    result["llm_used"] = True
    return result
