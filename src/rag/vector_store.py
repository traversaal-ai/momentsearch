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

Two collections, two names, both from config and nowhere else: IMAGE_COLLECTION
(one dense CLIP vector per frame) and TEXT_COLLECTION (per transcript chunk, the
dense text vector under Qdrant's default name PLUS a BM25 sparse vector under
SPARSE_VECTOR — hybrid search lives inside the one collection, see
config.ENABLE_HYBRID).
"""
from __future__ import annotations

import uuid
from typing import Any, Iterable

import numpy as np
from qdrant_client import QdrantClient
from qdrant_client.http import models as qm

from ..config import (
    DEFAULT_USER_ID,
    DEMO_LOCAL,
    DEMO_QDRANT_API_KEY,
    DEMO_QDRANT_URL,
    ENABLE_HYBRID,
    IMAGE_COLLECTION,
    QDRANT_API_KEY,
    QDRANT_HNSW_ON_DISK,
    QDRANT_LOCAL_PATH,
    QDRANT_ON_DISK,
    QDRANT_QUANTIZATION,
    QDRANT_URL,
    SPARSE_VECTOR,
    SPARSE_VERSION,
    TEXT_COLLECTION,
)
from ..providers.embed import image_dim, text_dim
from ..samples import SAMPLE_IDS, is_sample

_client: QdrantClient | None = None
_demo_client: QdrantClient | None = None


def client() -> QdrantClient:
    """The tenant's OWN vector store — whatever QDRANT_URL points at."""
    global _client
    if _client is None:
        if QDRANT_URL:
            _client = QdrantClient(url=QDRANT_URL, api_key=QDRANT_API_KEY or None,
                                   timeout=60)
        else:  # embedded local instance — dev only, single-process
            _client = QdrantClient(path=QDRANT_LOCAL_PATH)
    return _client


def demo_split() -> bool:
    """True when the samples live in a DIFFERENT Qdrant from the user's videos.

    That is the whole point of DEMO_LOCAL: the demo corpus ships in the repo and
    is served from the bundled Qdrant, so someone can point QDRANT_URL at their
    own cloud cluster for their own uploads and still have a working demo with
    nothing indexed and nothing to pay for. When both URLs are the same (the
    all-local default) there is only ONE store and nothing to split — searching
    twice would just return every hit twice."""
    return bool(DEMO_LOCAL and DEMO_QDRANT_URL and DEMO_QDRANT_URL != QDRANT_URL)


def demo_client() -> QdrantClient:
    """The read-only store holding the shipped sample vectors."""
    global _demo_client
    if not demo_split():
        return client()
    if _demo_client is None:
        _demo_client = QdrantClient(url=DEMO_QDRANT_URL,
                                    api_key=DEMO_QDRANT_API_KEY or None, timeout=60)
    return _demo_client


def client_for(video_id: str | None) -> QdrantClient:
    """Which store a single video's points live in."""
    return demo_client() if (video_id and is_sample(video_id)) else client()


def point_id(video_id: str, frame_idx: int) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"{video_id}:{frame_idx}"))


def _user_filter(user_id: str, video_id: str | None = None,
                 video_ids: list[str] | None = None,
                 include_samples: bool = False) -> qm.Filter:
    """Tenant scope for a search/delete, optionally widened to the shared samples.

    include_samples yields `(mine) OR (the sample corpus)` — exactly the sample
    corpus owned by the default tenant, never anything else that tenant
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


# One cached answer per (store, collection): "does it carry the sparse slot?".
# Consulted on every BM25 query and every text upsert, so it must not cost a
# round trip each time; invalidated by the rebuild that adds the slot.
_sparse_known: dict[tuple[int, str], bool] = {}
_warned_no_sparse: set[str] = set()


def has_sparse(qc: QdrantClient, collection: str) -> bool:
    """Does this collection carry the SPARSE_VECTOR slot? Cached."""
    key = (id(qc), collection)
    if key not in _sparse_known:
        try:
            sparse = qc.get_collection(collection).config.params.sparse_vectors or {}
            _sparse_known[key] = SPARSE_VECTOR in sparse
        except Exception:
            return False          # unknown (store down / no collection): don't cache
    return _sparse_known[key]


def sparse_params() -> dict:
    """The one definition of the sparse slot. modifier=IDF: Qdrant applies the
    inverse-document-frequency half of BM25 at query time from live collection
    stats, so points store term frequencies only and never go stale."""
    return {SPARSE_VECTOR: qm.SparseVectorParams(modifier=qm.Modifier.IDF)}


def create_collection(qc: QdrantClient, collection: str, dim: int, *,
                      sparse: bool = False,
                      distance: qm.Distance = qm.Distance.COSINE) -> None:
    """THE way a collection is created — the low-RAM profile plus, for the text
    collection, the BM25 slot. Used by ingest (_ensure), the demo restore and
    the hybrid rebuild, so every store looks the same."""
    qc.create_collection(
        collection_name=collection,
        vectors_config=qm.VectorParams(size=dim, distance=distance, on_disk=QDRANT_ON_DISK),
        hnsw_config=qm.HnswConfigDiff(on_disk=QDRANT_HNSW_ON_DISK),
        quantization_config=(
            qm.ScalarQuantization(scalar=qm.ScalarQuantizationConfig(
                type=qm.ScalarType.INT8, always_ram=True))
            if QDRANT_QUANTIZATION else None
        ),
        sparse_vectors_config=sparse_params() if sparse else None,
    )
    _payload_indexes(qc, collection)
    _sparse_known[(id(qc), collection)] = sparse


def _payload_indexes(qc: QdrantClient, collection: str) -> None:
    # Tenant index on user_id: co-locates a tenant's points so per-user
    # searches touch a small slice of the index. video_id for delete/filter.
    try:
        qc.create_payload_index(
            collection_name=collection, field_name="user_id",
            field_schema=qm.KeywordIndexParams(type=qm.KeywordIndexType.KEYWORD,
                                               is_tenant=True))
    except Exception:  # older server without is_tenant, or index already exists
        try:
            qc.create_payload_index(collection_name=collection, field_name="user_id",
                                    field_schema=qm.PayloadSchemaType.KEYWORD)
        except Exception:
            pass
    try:
        qc.create_payload_index(collection_name=collection, field_name="video_id",
                                field_schema=qm.PayloadSchemaType.KEYWORD)
    except Exception:
        pass


def _warn_no_sparse(collection: str) -> None:
    if collection in _warned_no_sparse:
        return
    _warned_no_sparse.add(collection)
    print(f"[hybrid] '{collection}' has no '{SPARSE_VECTOR}' slot yet — BM25 is skipped "
          f"and new chunks are indexed dense-only. Run `python -m src.hybrid_backfill "
          f"--store user` once to add it (in place, same collection, no re-embed).",
          flush=True)


def _ensure(collection: str, dim: int, *, sparse: bool = False) -> None:
    """Create a collection (low-RAM profile) + tenant/video payload indexes.

    If the collection already exists with a DIFFERENT vector size, stop. That
    only happens when someone switched embedding provider or model over an
    existing index, and the two failure modes are both bad: Qdrant rejects the
    upsert mid-ingest, or — worse, if the sizes happen to match — search
    silently compares vectors from two unrelated spaces and returns nonsense.

    `sparse` gives a NEW collection the BM25 slot. Qdrant can't add a named
    vector to an existing collection in place, so an old one is left as it is
    and a warning names the rebuild command; nothing here ever raises for that.
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
        _payload_indexes(c, collection)
        if sparse and not has_sparse(c, collection):
            _warn_no_sparse(collection)
        return
    create_collection(c, collection, dim, sparse=sparse)


def ensure_collection() -> None:
    """Visual (frame) collection — dimension comes from IMAGE_EMBED_PROVIDER."""
    _ensure(IMAGE_COLLECTION, image_dim())


def ensure_text_collection() -> None:
    """Transcript collection — dimension comes from TEXT_EMBED_PROVIDER; the
    BM25 sparse slot rides along when hybrid is on."""
    _ensure(TEXT_COLLECTION, text_dim(), sparse=ENABLE_HYBRID)


def upsert_frames(user_id: str, video_id: str, ids: Iterable[int],
                  vectors: np.ndarray, payloads: list[dict[str, Any]]) -> None:
    points = [
        qm.PointStruct(id=point_id(video_id, idx), vector=vec.tolist(), payload=payload)
        for idx, vec, payload in zip(ids, vectors, payloads)
    ]
    if points:
        client_for(video_id).upsert(collection_name=IMAGE_COLLECTION, points=points, wait=True)


def _query(qc: QdrantClient, collection: str, query, limit: int, flt,
           using: str | None = None) -> list[dict[str, Any]]:
    """One collection, one store. `query` is a dense list (the default vector) or
    a qm.SparseVector with `using` naming the sparse slot. Returns [] for a store
    that has never had this collection (or slot) — an empty deployment is 'no
    results', not a 500."""
    dense = using is None
    if not dense and not has_sparse(qc, collection):
        return []                 # legacy store, not backfilled yet: BM25 has nothing to say
    try:
        hits = qc.query_points(
            collection_name=collection, query=query, using=using, limit=limit,
            query_filter=flt, with_payload=True,
            # Quantized search is lossy; rescore re-reads the full-precision
            # vectors from disk for the top candidates. Dense only — sparse
            # vectors are never quantized.
            search_params=qm.SearchParams(
                quantization=qm.QuantizationSearchParams(rescore=True)
                if QDRANT_QUANTIZATION else None,
            ) if dense else None,
        ).points
    except Exception as exc:
        msg = str(exc)
        if "doesn't exist" in msg or "Not found" in msg or "not found" in msg:
            return []
        raise
    return [{"score": float(h.score), **(h.payload or {})} for h in hits]


def _sample_scope(video_id: str | None, video_ids: list[str] | None) -> list[str]:
    """The sample ids this query is allowed to reach, honouring any selection."""
    if video_id:
        return [video_id] if is_sample(video_id) else []
    if video_ids:
        return [v for v in video_ids if is_sample(v)]
    return sorted(SAMPLE_IDS)


def _search_both(collection: str, query, user_id: str, *, top_k: int,
                 video_id: str | None, video_ids: list[str] | None,
                 include_samples: bool, using: str | None = None) -> list[dict[str, Any]]:
    """Search the user's store and — when the samples live elsewhere — the
    shipped one, then merge by score.

    Both branches are the same model and the same metric, so their scores are
    directly comparable; taking the global top_k across the two is the same
    ranking a single store would have produced. Only when demo_split() is on
    does this cost a second round trip.
    """
    if not demo_split():
        return _query(client(), collection, query, top_k,
                      _user_filter(user_id, video_id, video_ids, include_samples), using)

    scope = _sample_scope(video_id, video_ids)
    # The user's own store never holds samples now, so its filter must not claim
    # them: include_samples=False keeps the tenant branch clean.
    mine = _query(client(), collection, query, top_k,
                  _user_filter(user_id, video_id, video_ids, False), using)
    if not scope or (user_id != DEFAULT_USER_ID and not include_samples):
        return mine
    theirs = _query(demo_client(), collection, query, top_k,
                    _user_filter(DEFAULT_USER_ID, None, scope, False), using)
    return sorted(mine + theirs, key=lambda h: h["score"], reverse=True)[:top_k]


def search(vector: np.ndarray, user_id: str, *, top_k: int,
           video_id: str | None = None,
           video_ids: list[str] | None = None,
           include_samples: bool = False) -> list[dict[str, Any]]:
    return _search_both(IMAGE_COLLECTION, vector.tolist(), user_id, top_k=top_k,
                        video_id=video_id, video_ids=video_ids,
                        include_samples=include_samples)


# ── Transcript (text) branch ─────────────────────────────────────────────────

def chunk_point_id(video_id: str, i: int) -> str:
    """uuid5 of '<video_id>:text:<i>' — re-runs overwrite, never collide with frames."""
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"{video_id}:text:{i}"))


def _text_vector(dense: np.ndarray, sparse: tuple[list[int], list[float]] | None):
    """A text point's vector(s): the dense one alone (Qdrant's default name), or
    dense + BM25 when hybrid is on. "" IS the default vector's name."""
    if sparse is None:
        return dense.tolist()
    return {"": dense.tolist(),
            SPARSE_VECTOR: qm.SparseVector(indices=sparse[0], values=sparse[1])}


def upsert_chunks(user_id: str, video_id: str, vectors: np.ndarray,
                  payloads: list[dict[str, Any]],
                  sparse: list[tuple[list[int], list[float]]] | None = None) -> None:
    """Transcript chunks into the text collection. `sparse` (aligned to
    `vectors`) adds each chunk's BM25 vector; the payload is stamped with
    SPARSE_VERSION so the backfill knows the point is done. On a collection
    that predates hybrid (no slot) the sparse part is dropped, not errored —
    the backfill adds it later, together with the slot."""
    qc = client_for(video_id)
    if sparse is not None and not has_sparse(qc, TEXT_COLLECTION):
        _warn_no_sparse(TEXT_COLLECTION)
        sparse = None
    points = [
        qm.PointStruct(id=chunk_point_id(video_id, i),
                       vector=_text_vector(vec, sparse[i] if sparse else None),
                       payload={**payload,
                                **({"sparse_version": SPARSE_VERSION} if sparse else {})})
        for i, (vec, payload) in enumerate(zip(vectors, payloads))
    ]
    if points:
        qc.upsert(collection_name=TEXT_COLLECTION, points=points, wait=True)


def search_text(vector: np.ndarray, user_id: str, *, top_k: int,
                video_id: str | None = None,
                video_ids: list[str] | None = None,
                include_samples: bool = False) -> list[dict[str, Any]]:
    return _search_both(TEXT_COLLECTION, vector.tolist(), user_id, top_k=top_k,
                        video_id=video_id, video_ids=video_ids,
                        include_samples=include_samples)


def search_sparse(sparse: tuple[list[int], list[float]], user_id: str, *, top_k: int,
                  video_id: str | None = None,
                  video_ids: list[str] | None = None,
                  include_samples: bool = False) -> list[dict[str, Any]]:
    """BM25 over the transcript chunks — the lexical half of hybrid search: same
    collection, same filters, the SPARSE_VECTOR slot. Scores are BM25 (unbounded,
    corpus-relative), never comparable to the dense cosines: search.py merges the
    two lists by RANK. A store whose points were never backfilled just returns []."""
    query = qm.SparseVector(indices=sparse[0], values=sparse[1])
    return _search_both(TEXT_COLLECTION, query, user_id, top_k=top_k,
                        video_id=video_id, video_ids=video_ids,
                        include_samples=include_samples, using=SPARSE_VECTOR)


def update_sparse(qc: QdrantClient, ids: list[str],
                  sparse: list[tuple[list[int], list[float]]]) -> None:
    """Backfill: attach BM25 vectors to EXISTING text points without touching
    their dense vector or payload, then stamp them done."""
    if not ids:
        return
    qc.update_vectors(collection_name=TEXT_COLLECTION, wait=True, points=[
        qm.PointVectors(id=pid, vector={SPARSE_VECTOR: qm.SparseVector(indices=s[0], values=s[1])})
        for pid, s in zip(ids, sparse)])
    qc.set_payload(collection_name=TEXT_COLLECTION, wait=True,
                   payload={"sparse_version": SPARSE_VERSION}, points=ids)


def dump_collection(qc: QdrantClient, collection: str):
    """Every point: (ids, dense vectors as float32 array, payloads). The dense
    vector is read under Qdrant's default name whether or not the point also
    carries a sparse one."""
    ids: list[str] = []
    vecs: list[list[float]] = []
    payloads: list[dict] = []
    offset = None
    while True:
        points, offset = qc.scroll(collection_name=collection, limit=512, offset=offset,
                                   with_payload=True, with_vectors=True)
        for p in points:
            v = p.vector
            if isinstance(v, dict):
                v = v.get("") or next(x for k, x in v.items() if k != SPARSE_VECTOR)
            ids.append(str(p.id))
            vecs.append(v)
            payloads.append(p.payload or {})
        if offset is None:
            break
    return ids, np.asarray(vecs, dtype=np.float32), payloads


def rebuild_with_sparse(qc: QdrantClient, collection: str, sparse_for, backup_path,
                        batch: int = 128) -> int:
    """Give an EXISTING text collection the BM25 slot, keeping its NAME and its
    dense vectors — Qdrant can't add a named vector in place, so this is: read
    every point out, write a backup file, drop and recreate the collection under
    the same name with the slot, write every point back with its sparse vector.
    No re-embedding: the dense vectors are copied, the sparse ones are computed
    locally from each payload's text by `sparse_for(texts)`. Seconds for a demo
    corpus, minutes for a large one; the collection is unavailable in between.
    Returns the number of points written."""
    import json
    from pathlib import Path

    ids, vecs, payloads = dump_collection(qc, collection)
    if not ids:
        return 0
    backup_path = Path(backup_path)
    backup_path.parent.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(backup_path, ids=np.array(ids), vectors=vecs,
                        payloads=np.array([json.dumps(p, ensure_ascii=False) for p in payloads]))
    params = qc.get_collection(collection).config.params.vectors
    distance = getattr(params, "distance", qm.Distance.COSINE)
    qc.delete_collection(collection_name=collection)
    _sparse_known.pop((id(qc), collection), None)
    create_collection(qc, collection, int(vecs.shape[1]), sparse=True, distance=distance)
    for i in range(0, len(ids), batch):
        texts = [(pl.get("text") or "") for pl in payloads[i:i + batch]]
        sparse = sparse_for(texts)
        qc.upsert(collection_name=collection, wait=True, points=[
            qm.PointStruct(id=ids[i + j], vector=_text_vector(vecs[i + j], sparse[j]),
                           payload={**payloads[i + j], "sparse_version": SPARSE_VERSION})
            for j in range(len(texts))])
    return len(ids)


def fetch_chunks(user_id: str, video_id: str) -> list[dict[str, Any]]:
    """Every transcript chunk for a video, sorted by time — `[{text, t_start,
    t_end}]`. Used to backfill the durable GCP transcript copy for videos indexed
    before transcript-to-storage existed (no YouTube re-fetch needed)."""
    try:
        points, _ = client_for(video_id).scroll(
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
            "t_end": float(p.payload.get("t_end", 0.0)),
            # who said it (diarization), when present — lets the synced transcript
            # panel show speakers and keeps it in the durable transcript copy.
            **({"speaker": p.payload["speaker"]} if p.payload.get("speaker") else {})}
           for p in points if p.payload and p.payload.get("text")]
    out.sort(key=lambda c: c["t_start"])
    return out


def frame_times(user_id: str, video_id: str) -> list[tuple[int, int]]:
    """[(idx, ms)] for every stored frame of a video — so a text-only ('said')
    moment can borrow the picture nearest its timestamp instead of showing an
    empty box (uploads have no YouTube thumbnail to fall back on). Empty if the
    video has no frames."""
    try:
        points, _ = client_for(video_id).scroll(
            collection_name=IMAGE_COLLECTION,
            scroll_filter=qm.Filter(must=[
                qm.FieldCondition(key="user_id", match=qm.MatchValue(value=user_id)),
                qm.FieldCondition(key="video_id", match=qm.MatchValue(value=video_id)),
            ]),
            with_payload=["idx", "ms"], with_vectors=False, limit=10000,
        )
    except Exception:
        return []
    out = []
    for p in points:
        pay = p.payload or {}
        if "idx" in pay:
            out.append((int(pay["idx"]), int(pay.get("ms", 0))))
    return out


def delete_video(user_id: str, video_id: str) -> None:
    """Purge a video from BOTH branches (frames + transcript)."""
    sel = qm.FilterSelector(filter=_user_filter(user_id, video_id))
    for coll in (IMAGE_COLLECTION, TEXT_COLLECTION):
        try:
            # A sample's points are in the shipped store, which is read-only in
            # practice — but route it correctly rather than deleting from the
            # wrong database. (The API refuses to delete samples anyway.)
            client_for(video_id).delete(collection_name=coll, points_selector=sel, wait=True)
        except Exception:
            pass  # text collection may not exist if transcript is disabled


def collection_ready() -> bool:
    """Is anything searchable? With the samples in their own store, a user whose
    own Qdrant is still empty can still ask the demo a question."""
    for qc in ({id(client()): client()} | ({id(demo_client()): demo_client()}
                                           if demo_split() else {})).values():
        try:
            if qc.collection_exists(IMAGE_COLLECTION):
                return True
        except Exception:
            continue
    return False
