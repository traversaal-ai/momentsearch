"""Qdrant — one shared multi-tenant collection, one point per kept frame.

Multi-tenancy: every point carries user_id; the field has a tenant payload
index and every search / upsert / delete is user_id-filtered. NOT
collection-per-user (collection explosion); a huge tenant can graduate to a
dedicated collection later.

Memory profile (the frame-scale levers, all env flags, default ON):
  QDRANT_ON_DISK        original float vectors live on disk
  QDRANT_QUANTIZATION   int8 copies pinned in RAM (~4x smaller) do the search;
                        queries rescore the top candidates from the originals
  QDRANT_HNSW_ON_DISK   the HNSW graph lives on disk too

Point IDs are uuid5 of "{video_id}:{frame_idx}" — deterministic, so re-runs
overwrite instead of duplicating. Payloads are trimmed to filter/display
fields (user_id, video_id, ms, idx, embed_version); titles and URLs live in
Postgres and are joined at answer time.
"""
from __future__ import annotations

import uuid
from typing import Any, Iterable

import numpy as np
from qdrant_client import QdrantClient
from qdrant_client.http import models as qm

from ..config import (
    DEFAULT_USER_ID,
    QDRANT_API_KEY,
    QDRANT_COLLECTION,
    QDRANT_HNSW_ON_DISK,
    QDRANT_LOCAL_PATH,
    QDRANT_ON_DISK,
    QDRANT_QUANTIZATION,
    QDRANT_URL,
    TEXT_COLLECTION,
)
from ..providers.embed import image_dim, text_dim
from ..samples import SAMPLE_IDS

_client: QdrantClient | None = None


def client() -> QdrantClient:
    global _client
    if _client is None:
        if QDRANT_URL:
            _client = QdrantClient(url=QDRANT_URL, api_key=QDRANT_API_KEY or None,
                                   timeout=60)
        else:  # embedded local instance — dev only, single-process
            _client = QdrantClient(path=QDRANT_LOCAL_PATH)
    return _client


def point_id(video_id: str, frame_idx: int) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"{video_id}:{frame_idx}"))


def _user_filter(user_id: str, video_id: str | None = None,
                 video_ids: list[str] | None = None,
                 include_samples: bool = False) -> qm.Filter:
    """Tenant scope for a search/delete, optionally widened to the shared samples.

    include_samples yields `(mine) OR (the sample corpus)` — exactly the four
    curated videos owned by the default tenant, never anything else that tenant
    owns. Written as two nested must-groups under `should` so the sample branch
    can't be satisfied by a user_id match alone: guessing another workspace's
    video id still matches nothing.
    """
    def scope() -> list[qm.FieldCondition]:
        if video_id:  # single-video scope (kept for /transcript-style calls)
            return [qm.FieldCondition(key="video_id", match=qm.MatchValue(value=video_id))]
        if video_ids:  # multi-select scope — query only the chosen videos
            return [qm.FieldCondition(key="video_id", match=qm.MatchAny(any=video_ids))]
        return []

    mine = [qm.FieldCondition(key="user_id", match=qm.MatchValue(value=user_id))] + scope()
    # The default tenant OWNS the samples, so its plain filter already covers them.
    if not include_samples or user_id == DEFAULT_USER_ID:
        return qm.Filter(must=mine)

    shared = sorted(SAMPLE_IDS)
    if video_id:                       # scoped to one video: is it a sample?
        shared = [video_id] if video_id in SAMPLE_IDS else []
    elif video_ids:                    # scoped to a set: keep the samples in it
        chosen = set(video_ids)
        shared = [v for v in shared if v in chosen]
    if not shared:                     # nothing shared in scope — plain tenant filter
        return qm.Filter(must=mine)
    return qm.Filter(should=[
        qm.Filter(must=mine),
        qm.Filter(must=[
            qm.FieldCondition(key="user_id", match=qm.MatchValue(value=DEFAULT_USER_ID)),
            qm.FieldCondition(key="video_id", match=qm.MatchAny(any=shared)),
        ]),
    ])


def _existing_dim(collection: str) -> int | None:
    """Vector size of an existing collection, or None if it can't be read."""
    try:
        params = client().get_collection(collection).config.params.vectors
        return int(getattr(params, "size", 0)) or None
    except Exception:
        return None


def _ensure(collection: str, dim: int) -> None:
    """Create a collection (low-RAM profile) + tenant/video payload indexes.

    If the collection already exists with a DIFFERENT vector size, stop. That
    only happens when someone switched embedding provider or model over an
    existing index, and the two failure modes are both bad: Qdrant rejects the
    upsert mid-ingest, or — worse, if the sizes happen to match — search
    silently compares vectors from two unrelated spaces and returns nonsense.
    """
    c = client()
    if c.collection_exists(collection):
        found = _existing_dim(collection)
        if found and found != dim:
            raise RuntimeError(
                f"Qdrant collection '{collection}' holds {found}-dim vectors but "
                f"the configured embedder produces {dim}. Embeddings must match "
                f"between indexing and querying. Either restore the previous "
                f"provider/model, or re-index this branch: delete the collection "
                f"and re-ingest (python -m src.providers shows what's configured)."
            )
    if not c.collection_exists(collection):
        c.create_collection(
            collection_name=collection,
            vectors_config=qm.VectorParams(
                size=dim,
                distance=qm.Distance.COSINE,
                on_disk=QDRANT_ON_DISK,
            ),
            hnsw_config=qm.HnswConfigDiff(on_disk=QDRANT_HNSW_ON_DISK),
            quantization_config=(
                qm.ScalarQuantization(scalar=qm.ScalarQuantizationConfig(
                    type=qm.ScalarType.INT8, always_ram=True))
                if QDRANT_QUANTIZATION else None
            ),
        )
    # Tenant index on user_id: co-locates a tenant's points so per-user
    # searches touch a small slice of the index. video_id for delete/filter.
    try:
        c.create_payload_index(
            collection_name=collection, field_name="user_id",
            field_schema=qm.KeywordIndexParams(type=qm.KeywordIndexType.KEYWORD,
                                               is_tenant=True))
    except Exception:  # older server without is_tenant, or index already exists
        try:
            c.create_payload_index(collection_name=collection, field_name="user_id",
                                   field_schema=qm.PayloadSchemaType.KEYWORD)
        except Exception:
            pass
    try:
        c.create_payload_index(collection_name=collection, field_name="video_id",
                               field_schema=qm.PayloadSchemaType.KEYWORD)
    except Exception:
        pass


def ensure_collection() -> None:
    """Visual (frame) collection — dimension comes from IMAGE_EMBED_PROVIDER."""
    _ensure(QDRANT_COLLECTION, image_dim())


def ensure_text_collection() -> None:
    """Transcript collection — dimension comes from TEXT_EMBED_PROVIDER."""
    _ensure(TEXT_COLLECTION, text_dim())


def upsert_frames(user_id: str, video_id: str, ids: Iterable[int],
                  vectors: np.ndarray, payloads: list[dict[str, Any]]) -> None:
    points = [
        qm.PointStruct(id=point_id(video_id, idx), vector=vec.tolist(), payload=payload)
        for idx, vec, payload in zip(ids, vectors, payloads)
    ]
    if points:
        client().upsert(collection_name=QDRANT_COLLECTION, points=points, wait=True)


def search(vector: np.ndarray, user_id: str, *, top_k: int,
           video_id: str | None = None,
           video_ids: list[str] | None = None,
           include_samples: bool = False) -> list[dict[str, Any]]:
    try:
        hits = client().query_points(
            collection_name=QDRANT_COLLECTION,
            query=vector.tolist(),
            limit=top_k,
            query_filter=_user_filter(user_id, video_id, video_ids, include_samples),
            with_payload=True,
            search_params=qm.SearchParams(
                # Quantized search is lossy; rescore re-reads the full-precision
                # vectors from disk for the top candidates.
                quantization=qm.QuantizationSearchParams(rescore=True)
                if QDRANT_QUANTIZATION else None,
            ),
        ).points
    except Exception as exc:
        # Empty deployment (collection not created yet) is a "no results"
        # situation, not a 500 — the UI shows "no moments found".
        if "doesn't exist" in str(exc) or "Not found" in str(exc):
            return []
        raise
    return [{"score": float(h.score), **(h.payload or {})} for h in hits]


# ── Transcript (text) branch ─────────────────────────────────────────────────

def upsert_chunks(user_id: str, video_id: str, vectors: np.ndarray,
                  payloads: list[dict[str, Any]]) -> None:
    """Transcript chunks into the text collection. IDs are uuid5 of
    '<video_id>:text:<i>' so re-runs overwrite, and never collide with frame ids."""
    points = [
        qm.PointStruct(id=str(uuid.uuid5(uuid.NAMESPACE_URL, f"{video_id}:text:{i}")),
                       vector=vec.tolist(), payload=payload)
        for i, (vec, payload) in enumerate(zip(vectors, payloads))
    ]
    if points:
        client().upsert(collection_name=TEXT_COLLECTION, points=points, wait=True)


def search_text(vector: np.ndarray, user_id: str, *, top_k: int,
                video_id: str | None = None,
                video_ids: list[str] | None = None,
                include_samples: bool = False) -> list[dict[str, Any]]:
    try:
        hits = client().query_points(
            collection_name=TEXT_COLLECTION,
            query=vector.tolist(),
            limit=top_k,
            query_filter=_user_filter(user_id, video_id, video_ids, include_samples),
            with_payload=True,
            search_params=qm.SearchParams(
                quantization=qm.QuantizationSearchParams(rescore=True)
                if QDRANT_QUANTIZATION else None,
            ),
        ).points
    except Exception as exc:
        if "doesn't exist" in str(exc) or "Not found" in str(exc):
            return []
        raise
    return [{"score": float(h.score), **(h.payload or {})} for h in hits]


def fetch_chunks(user_id: str, video_id: str) -> list[dict[str, Any]]:
    """Every transcript chunk for a video, sorted by time — `[{text, t_start,
    t_end}]`. Used to backfill the durable GCP transcript copy for videos indexed
    before transcript-to-storage existed (no YouTube re-fetch needed)."""
    try:
        points, _ = client().scroll(
            collection_name=TEXT_COLLECTION,
            scroll_filter=qm.Filter(must=[
                qm.FieldCondition(key="user_id", match=qm.MatchValue(value=user_id)),
                qm.FieldCondition(key="video_id", match=qm.MatchValue(value=video_id)),
            ]),
            with_payload=True, with_vectors=False, limit=10000,
        )
    except Exception:
        return []
    out = [{"text": p.payload.get("text", ""),
            "t_start": float(p.payload.get("t_start", 0.0)),
            "t_end": float(p.payload.get("t_end", 0.0))}
           for p in points if p.payload and p.payload.get("text")]
    out.sort(key=lambda c: c["t_start"])
    return out


def delete_video(user_id: str, video_id: str) -> None:
    """Purge a video from BOTH branches (frames + transcript)."""
    sel = qm.FilterSelector(filter=_user_filter(user_id, video_id))
    for coll in (QDRANT_COLLECTION, TEXT_COLLECTION):
        try:
            client().delete(collection_name=coll, points_selector=sel, wait=True)
        except Exception:
            pass  # text collection may not exist if transcript is disabled


def collection_ready() -> bool:
    try:
        return client().collection_exists(QDRANT_COLLECTION)
    except Exception:
        return False
