"""Qdrant wrapper — stores one point per sampled video frame.

Each point's vector is the frame's CLIP embedding; its payload carries enough to
render a citation: which video, the timestamp, and the thumbnail path.
"""
from __future__ import annotations

import uuid
from typing import Any

import numpy as np
from qdrant_client import QdrantClient
from qdrant_client.http import models as qm

from .config import get_settings
from .embeddings import embedding_dim


_client: QdrantClient | None = None


def client() -> QdrantClient:
    global _client
    if _client is None:
        _client = QdrantClient(url=get_settings().QDRANT_URL, timeout=30)
    return _client


def ensure_collection() -> None:
    s = get_settings()
    c = client()
    if c.collection_exists(s.QDRANT_COLLECTION):
        return
    c.create_collection(
        collection_name=s.QDRANT_COLLECTION,
        vectors_config=qm.VectorParams(size=embedding_dim(), distance=qm.Distance.COSINE),
    )
    # Index video_id so we can filter/delete a single video efficiently.
    c.create_payload_index(
        collection_name=s.QDRANT_COLLECTION,
        field_name="video_id",
        field_schema=qm.PayloadSchemaType.KEYWORD,
    )


def upsert_frames(vectors: np.ndarray, payloads: list[dict[str, Any]]) -> None:
    s = get_settings()
    points = [
        qm.PointStruct(id=str(uuid.uuid4()), vector=vec.tolist(), payload=payload)
        for vec, payload in zip(vectors, payloads)
    ]
    if points:
        client().upsert(collection_name=s.QDRANT_COLLECTION, points=points)


def search(vector: np.ndarray, top_k: int, video_id: str | None = None) -> list[dict[str, Any]]:
    s = get_settings()
    flt = None
    if video_id:
        flt = qm.Filter(must=[qm.FieldCondition(key="video_id",
                                                match=qm.MatchValue(value=video_id))])
    hits = client().query_points(
        collection_name=s.QDRANT_COLLECTION,
        query=vector.tolist(),
        limit=top_k,
        query_filter=flt,
        with_payload=True,
    ).points
    return [{"score": float(h.score), **(h.payload or {})} for h in hits]


def delete_video(video_id: str) -> None:
    s = get_settings()
    client().delete(
        collection_name=s.QDRANT_COLLECTION,
        points_selector=qm.FilterSelector(filter=qm.Filter(
            must=[qm.FieldCondition(key="video_id", match=qm.MatchValue(value=video_id))])),
    )


def list_videos() -> list[dict[str, Any]]:
    """Distinct videos currently indexed, with frame counts (scroll-based)."""
    s = get_settings()
    c = client()
    if not c.collection_exists(s.QDRANT_COLLECTION):
        return []
    videos: dict[str, dict[str, Any]] = {}
    offset = None
    while True:
        records, offset = c.scroll(
            collection_name=s.QDRANT_COLLECTION,
            limit=256,
            offset=offset,
            with_payload=["video_id", "title", "source", "url"],
            with_vectors=False,
        )
        for r in records:
            p = r.payload or {}
            vid = p.get("video_id")
            if not vid:
                continue
            entry = videos.setdefault(vid, {
                "video_id": vid,
                "title": p.get("title", vid),
                "source": p.get("source"),
                "url": p.get("url"),
                "frames": 0,
            })
            entry["frames"] += 1
        if offset is None:
            break
    return sorted(videos.values(), key=lambda v: v["title"] or "")
