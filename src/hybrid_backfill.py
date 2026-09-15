"""Give transcript chunks indexed before hybrid their BM25 sparse vector.

Videos indexed from now on get the sparse vector at ingest (src/ingest/
pipeline.py). Everything indexed BEFORE has only the dense vector, and the BM25
side of search simply can't see it. This fills the gap in the ONE text
collection, under its existing name, from the chunk text already in each
point's payload. No re-embedding, no OpenAI call, no transcript fetch: the
sparse encoder is local and takes milliseconds per chunk.

Two cases, decided per store:

  * the collection has no `bm25` slot yet (anything created before hybrid) —
    Qdrant cannot add a named vector to a live collection, so it is REBUILT
    under the same name: every point is read out, a backup .npz is written
    under data/backups/, the collection is dropped and recreated with the slot,
    and every point is written back with its dense vector copied and its sparse
    vector computed. The collection is unavailable for those seconds.
  * the slot exists — points missing the current config.SPARSE_VERSION stamp
    get their sparse vector attached in place (update_vectors); nothing else moves.

    python -m src.hybrid_backfill                 # the shipped demo store (default)
    python -m src.hybrid_backfill --store user    # your own Qdrant (QDRANT_URL)
    python -m src.hybrid_backfill --store both
    python -m src.hybrid_backfill --export        # + rewrite demo_corpus/vectors/<text>.npz
                                                  #   so a fresh clone restores hybrid

Idempotent: re-running does only what's left. Ends with a smoke test — one
BM25 query — so a silent no-op can't pass as success.
"""
from __future__ import annotations

import argparse
import sys
import time
from datetime import datetime

from qdrant_client import QdrantClient
from qdrant_client.http import models as qm

from . import config
from .providers.embed.sparse import embed_sparse_docs, embed_sparse_query
from .rag import vector_store as vs

COLL = config.TEXT_COLLECTION


def _stores(which: str) -> list[tuple[str, QdrantClient]]:
    out: list[tuple[str, QdrantClient]] = []
    if which in ("demo", "both"):
        out.append(("demo" if vs.demo_split() else "user (the demo shares it)", vs.demo_client()))
    if which == "user" or (which == "both" and vs.demo_split()):
        out.append(("user", vs.client()))
    return out


def _incremental(qc: QdrantClient, batch: int) -> tuple[int, int]:
    """Slot present: attach a sparse vector to every point not yet stamped."""
    seen = written = 0
    offset = None
    while True:
        points, offset = qc.scroll(collection_name=COLL, limit=batch, offset=offset,
                                   with_payload=["text", "sparse_version"], with_vectors=False)
        todo = [(str(p.id), (p.payload or {}).get("text") or "") for p in points
                if (p.payload or {}).get("sparse_version") != config.SPARSE_VERSION]
        seen += len(points)
        if todo:
            vs.update_sparse(qc, [i for i, _ in todo], embed_sparse_docs([t for _, t in todo]))
            written += len(todo)
        if offset is None:
            break
    return seen, written


def backfill(name: str, qc: QdrantClient, batch: int) -> None:
    if not qc.collection_exists(COLL):
        print(f"  {COLL}: does not exist in this store — nothing to do")
        return
    n = qc.count(collection_name=COLL, exact=True).count
    if vs.has_sparse(qc, COLL):
        seen, written = _incremental(qc, batch)
        print(f"  slot present · {seen} points scanned · {written} given a sparse vector")
        return
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup = config.DATA / "backups" / f"{COLL}-{name.split()[0]}-{stamp}.npz"
    print(f"  no '{config.SPARSE_VECTOR}' slot: rebuilding '{COLL}' in place "
          f"({n} points) · backup -> {backup}")
    written = vs.rebuild_with_sparse(qc, COLL, embed_sparse_docs, backup, batch=batch)
    after = qc.count(collection_name=COLL, exact=True).count
    print(f"  rebuilt · {written} points written · collection now holds {after}"
          + ("" if after == n else f"  !! expected {n}"))


def smoke(qc: QdrantClient, question: str = "gradient descent") -> bool:
    q = embed_sparse_query(question)
    try:
        hits = qc.query_points(collection_name=COLL, using=config.SPARSE_VECTOR,
                               query=qm.SparseVector(indices=q[0], values=q[1]),
                               limit=1, with_payload=["text"]).points
    except Exception as exc:
        print(f"  smoke test: BM25 query failed — {type(exc).__name__}: {exc}")
        return False
    if not hits:
        print(f"  smoke test: BM25 for {question!r} found nothing — FAILED")
        return False
    print(f"  smoke test: BM25 for {question!r} -> score {hits[0].score:.2f}, "
          f"{(hits[0].payload or {}).get('text', '')[:70]!r}")
    return True


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--store", choices=["demo", "user", "both"], default="demo")
    ap.add_argument("--batch", type=int, default=128)
    ap.add_argument("--export", action="store_true",
                    help="after the demo store: rewrite demo_corpus/vectors/<text>.npz")
    a = ap.parse_args()

    ok = True
    for name, qc in _stores(a.store):
        print(f"[{name}] {COLL}")
        t0 = time.time()
        backfill(name, qc, a.batch)
        ok &= smoke(qc)
        print(f"  done in {time.time() - t0:.1f}s")
    if a.export:
        from . import demo_corpus_io as vio
        n = vio.export_collection(vs.demo_client(), COLL)
        print(f"[export] {vio.vector_file(COLL)} <- {n} points (dense + sparse)")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
