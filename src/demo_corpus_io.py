"""The demo corpus's vectors, in a form git can actually carry.

Qdrant's own storage directory is ~1.1GB for this corpus — half a gigabyte of
preallocated segment files per collection for what is really 2000 vectors, about
8MB of numbers. That can't be committed, so the folder ships the NUMBERS instead
and the running app rebuilds Qdrant's storage from them on first boot:

    demo_corpus/vectors/<collection>.npz     ids + float32 vectors + payloads
                                             (+ the BM25 sparse vectors for the
                                             text collection, as flat CSR arrays)

Export is a rebuild step (src/build_demo_corpus.py); import is part of the
startup gate (src/demo_restore.py) and only runs when the collection is missing
or empty, so a warm stack pays nothing.

The npz is exact — float32, the same values Qdrant returned — so a restored
collection ranks identically to the one it was exported from. (float16 would
halve the file and change scores in the fourth decimal; not worth explaining a
difference in results to save 4MB.)
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
from qdrant_client import QdrantClient
from qdrant_client.http import models as qm

from . import config
from .rag import vector_store

VECTOR_DIR = config.ROOT / "demo_corpus" / "vectors"
_BATCH = 256          # points per upsert — keeps each request small


def vector_file(collection: str) -> Path:
    return VECTOR_DIR / f"{collection}.npz"


def export_collection(qc: QdrantClient, collection: str) -> int:
    """Scroll a whole collection out to demo_corpus/vectors/<collection>.npz."""
    ids: list[str] = []
    vectors: list[list[float]] = []
    payloads: list[dict] = []
    # Sparse vectors are ragged, so they ship as one flat CSR triple:
    # indices/values concatenated, offsets[i]..offsets[i+1] = point i's slice.
    sp_idx: list[int] = []
    sp_val: list[float] = []
    sp_off: list[int] = [0]
    any_sparse = False
    offset = None
    while True:
        points, offset = qc.scroll(collection_name=collection, limit=512,
                                   offset=offset, with_payload=True, with_vectors=True)
        for p in points:
            vec = p.vector
            sparse = None
            if isinstance(vec, dict):        # named: "" is the dense default, SPARSE_VECTOR the BM25
                sparse = vec.get(config.SPARSE_VECTOR)
                vec = vec.get("") or next(v for k, v in vec.items() if k != config.SPARSE_VECTOR)
            ids.append(str(p.id))
            vectors.append(vec)
            payloads.append(p.payload or {})
            if sparse is not None:
                any_sparse = True
                sp_idx.extend(int(i) for i in sparse.indices)
                sp_val.extend(float(v) for v in sparse.values)
            sp_off.append(len(sp_idx))
        if offset is None:
            break
    if not ids:
        return 0
    VECTOR_DIR.mkdir(parents=True, exist_ok=True)
    arrays = dict(
        ids=np.array(ids),
        vectors=np.asarray(vectors, dtype=np.float32),
        payloads=np.array([json.dumps(p, ensure_ascii=False) for p in payloads]),
    )
    if any_sparse:
        arrays.update(sparse_indices=np.asarray(sp_idx, dtype=np.int64),
                      sparse_values=np.asarray(sp_val, dtype=np.float32),
                      sparse_offsets=np.asarray(sp_off, dtype=np.int64))
    np.savez_compressed(vector_file(collection), **arrays)
    return len(ids)


def _count(qc: QdrantClient, collection: str) -> int | None:
    """Points in a collection, or None when it doesn't exist."""
    try:
        if not qc.collection_exists(collection):
            return None
        return qc.count(collection_name=collection, exact=True).count
    except Exception:
        return None


def import_collection(qc: QdrantClient, collection: str) -> int:
    """Load <collection>.npz into Qdrant if it isn't already there.

    Returns the number of points written (0 when the collection already has
    them, or when the corpus ships no file for it). Creates the collection with
    the dimension the file itself declares, so this can't disagree with the
    vectors it is about to insert.
    """
    path = vector_file(collection)
    if not path.exists():
        return 0
    have = _count(qc, collection)
    if have:
        return 0                       # already populated — nothing to do

    with np.load(path, allow_pickle=False) as z:
        ids = [str(x) for x in z["ids"]]
        vectors = z["vectors"]
        payloads = [json.loads(x) for x in z["payloads"]]
        sparse = None
        if "sparse_offsets" in z.files:          # hybrid corpus: unpack the CSR triple
            si, sv, so = z["sparse_indices"], z["sparse_values"], z["sparse_offsets"]
            sparse = [(si[so[i]:so[i + 1]].tolist(), sv[so[i]:so[i + 1]].tolist())
                      for i in range(len(ids))]

    if have is None:
        # The same profile + payload indexes ingest gives a collection, so a
        # restored demo store is indistinguishable from an ingested one.
        vector_store.create_collection(qc, collection, int(vectors.shape[1]),
                                       sparse=sparse is not None)
    elif sparse and not vector_store.has_sparse(qc, collection):
        # An empty collection from before hybrid: give it the slot the file needs.
        qc.delete_collection(collection_name=collection)
        vector_store.create_collection(qc, collection, int(vectors.shape[1]), sparse=True)

    def vector(i: int):
        if sparse is None or not sparse[i][0]:
            return vectors[i].tolist()
        return {"": vectors[i].tolist(),
                config.SPARSE_VECTOR: qm.SparseVector(indices=sparse[i][0], values=sparse[i][1])}

    for i in range(0, len(ids), _BATCH):
        qc.upsert(collection_name=collection, wait=True, points=[
            qm.PointStruct(id=ids[j], vector=vector(j), payload=payloads[j])
            for j in range(i, min(i + _BATCH, len(ids)))
        ])
    return len(ids)
