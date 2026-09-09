"""Rebuild the shipped demo corpus — a one-off, run by hand, not by the app.

The ten sample videos in demo_corpus/ were produced by this script and are
committed, so nobody else ever has to run it. You run it only to change the
corpus: a different sampling budget, a new embedding model, another video in
src/samples.py.

It runs the REAL ingest pipeline (src/ingest/pipeline.ingest_video: yt-dlp /
SocialKit -> ffmpeg frames -> pHash dedup -> CLIP vectors -> captions -> text
vectors) and writes to the DEMO stores rather than yours — it forces
STORAGE_DIR at config.DEMO_DATA and Qdrant at config.DEMO_QDRANT_URL, the same
two places the running app reads samples from (config.DEMO_LOCAL). So it cannot
touch your bucket or your cloud cluster, whatever .env says.

    docker compose --profile local-postgres up -d qdrant postgres clip
    docker compose run --rm \
      -e DATABASE_URL=postgresql://ms:ms@postgres:5432/ms \
      -e MAX_FRAMES=250 -e FRAME_INTERVAL_SEC=8 \
      seed python -m src.build_demo_corpus

It writes demo_corpus/vectors/*.npz and demo_corpus/videos.json itself when the
run finishes, so the shipped copy is whatever it just built.

Idempotent: a video already indexed at the current EMBED_VERSION is skipped, so
re-running after a failure only does what's left. --only limits the run to
specific ids, --force re-ingests them.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from urllib.parse import urlsplit

from . import config, db, storage
from .ingest.pipeline import ingest_video
from .rag import vector_store
from .samples import sample_video_id

# The corpus: 3Blue1Brown's "Neural networks" playlist, in playlist order — the
# one video the app already ships (LPZh9BOjkQs) plus the nine around it. One
# creator, one continuous topic, so cross-video questions have real answers.
# `author` is carried by hand because ingest keeps YouTube's title and drops the
# uploader entirely (see src/samples.py) — the demo runs on someone else's work.
CORPUS = [
    ("aircAruvnKk", "But what is a neural network? — Deep Learning Chapter 1"),
    ("IHZwWFHWa-w", "Gradient descent, how neural networks learn — Deep Learning Chapter 2"),
    ("Ilg3gGewQ5U", "Backpropagation, intuitively — Deep Learning Chapter 3"),
    ("tIeHLnjs5U8", "Backpropagation calculus — Deep Learning Chapter 4"),
    ("LPZh9BOjkQs", "Large Language Models explained briefly"),
    ("wjZofJX0v4M", "Transformers, the tech behind LLMs — Deep Learning Chapter 5"),
    ("eMlx5fFNoYc", "Attention in transformers, step-by-step — Deep Learning Chapter 6"),
    ("9-Jl0dxWQs8", "How might LLMs store facts — Deep Learning Chapter 7"),
    ("GlYgs6v2YfU", "But what is cross-entropy? — Compression is Intelligence Part 2"),
    ("iv-5mZ_9CPY", "But how do AI images and videos actually work? — guest by Welch Labs"),
]
AUTHOR = "3Blue1Brown"
AUTHOR_URL = "https://www.youtube.com/@3blue1brown"

_MAX_PASSES = 3   # a YouTube hiccup on one video shouldn't cost the whole run


def _videos() -> list[dict]:
    return [{"id": f"yt_{vid}", "url": f"https://youtu.be/{vid}", "title": title}
            for vid, title in CORPUS]


def _indexed(v: dict) -> bool:
    """Done means: the row says indexed at the CURRENT embedding version, its
    thumbnail is on disk, and its frame vectors are in Qdrant. Anything less and
    the video is not actually answerable, whatever the row claims."""
    row = db.get_video(v["id"]) or {}
    if row.get("status") != "indexed" or row.get("embed_version") != config.EMBED_VERSION:
        return False
    try:
        return (storage.exists(storage.frame_key(config.DEFAULT_USER_ID, v["id"], 0))
                and bool(vector_store.frame_times(config.DEFAULT_USER_ID, v["id"])))
    except Exception:
        return False


def _target_demo_stores() -> None:
    """Point this process at the DEMO stores, whatever .env says.

    The old version of this script asked a compose overlay to supply a local
    .env and then refused to run if it hadn't. Forcing the two settings here is
    both shorter and safer: there is no arrangement of environment variables
    that makes a rebuild write frames to your bucket or vectors to your cloud
    cluster, because the destination isn't read from the environment at all.

    Postgres is not forced (the rows are exported to videos.json at the end,
    from whatever database this ran against) but a HOSTED one is refused: a
    rebuild rewrites those rows, and they are not yours to rewrite."""
    config.STORAGE_PROVIDER = storage.STORAGE_PROVIDER = "local"
    storage.DATA = config.DEMO_DATA
    vector_store.QDRANT_URL = config.DEMO_QDRANT_URL
    vector_store.QDRANT_API_KEY = config.DEMO_QDRANT_API_KEY
    # The write helpers route a sample id to demo_client(); with the URLs now
    # equal that IS the same client, so one store, no split, no double writes.
    config.QDRANT_URL = config.DEMO_QDRANT_URL

    host = urlsplit(config.DATABASE_URL).hostname or ""
    if host not in ("postgres", "localhost", "127.0.0.1", "host.docker.internal"):
        sys.exit(f"[corpus] DATABASE_URL points at {host}, a hosted database.\n"
                 f"  A rebuild rewrites the ten sample rows, and they have to be\n"
                 f"  exported to demo_corpus/videos.json afterwards — do that\n"
                 f"  against the bundled postgres:\n"
                 f"    -e DATABASE_URL=postgresql://ms:ms@postgres:5432/ms")


def _export_vectors() -> None:
    """Write the vectors where git can carry them. Qdrant's own storage tree is
    ~1.1GB of preallocated segments for this corpus and is git-ignored; these npz
    files (~7MB) are what ships, and the startup gate rebuilds the collections
    from them on a fresh clone."""
    from . import demo_corpus_io as vio
    qc = vector_store.demo_client()
    for coll in (config.IMAGE_COLLECTION, config.TEXT_COLLECTION):
        n = vio.export_collection(qc, coll)
        mb = vio.vector_file(coll).stat().st_size / 1e6 if n else 0
        print(f"[corpus] exported {n} vectors -> demo_corpus/vectors/{coll}.npz "
              f"({mb:.1f} MB)", flush=True)


def _export_rows() -> None:
    """Write the ten manifest rows to demo_corpus/videos.json.

    Only the fields that describe the video — no user_id (the restore fills in
    whichever tenant it runs as), no timestamps (they'd be this machine's clock).
    frame_count is counted from the vectors just written rather than copied from
    the row, so the number the UI shows is the number of frames that actually
    ship."""
    rows = []
    for v in _videos():
        row = db.get_video(v["id"]) or {}
        rows.append({
            "id": v["id"],
            "source": "youtube",
            "url": row.get("url") or v["url"],
            "title": row.get("title") or v["title"],
            "source_hash": v["id"],
            "status": "indexed",
            "frame_count": len(vector_store.frame_times(config.DEFAULT_USER_ID, v["id"])),
            "embed_version": config.EMBED_VERSION,
            "diarize": False,
            "transcript_note": row.get("transcript_note"),
        })
    out = config.DEMO_VIDEOS_JSON
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(rows, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"[corpus] exported {len(rows)} rows -> demo_corpus/{out.name}", flush=True)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", nargs="*", default=None,
                    help="YouTube ids (bare or yt_-prefixed) to limit the run to")
    ap.add_argument("--force", action="store_true",
                    help="re-ingest even videos already indexed")
    args = ap.parse_args()

    _target_demo_stores()
    todo_all = _videos()
    if args.only:
        want = {i if i.startswith("yt_") else f"yt_{i}" for i in args.only}
        todo_all = [v for v in todo_all if v["id"] in want]

    print(f"[corpus] storage : {storage.DATA}  (demo store, forced)")
    print(f"[corpus] qdrant  : {config.DEMO_QDRANT_URL}  (demo store, forced) "
          f"({config.IMAGE_COLLECTION} + {config.TEXT_COLLECTION})")
    print(f"[corpus] sampling: <= {config.MAX_FRAMES} frames, "
          f"1 per {config.FRAME_INTERVAL_SEC}s")
    print(f"[corpus] embed   : {config.EMBED_VERSION} / {config.TEXT_EMBED_VERSION}",
          flush=True)

    db.init_schema()
    vector_store.ensure_collection()
    vector_store.ensure_text_collection()

    started = time.time()
    for attempt in range(1, _MAX_PASSES + 1):
        todo = todo_all if (args.force and attempt == 1) else [
            v for v in todo_all if not _indexed(v)]
        if not todo:
            break
        print(f"\n[corpus] pass {attempt}/{_MAX_PASSES}: {len(todo)} video(s) to ingest",
              flush=True)
        for i, v in enumerate(todo, 1):
            t0 = time.time()
            print(f"\n[corpus] ({i}/{len(todo)}) {v['id']}  {v['title']}", flush=True)
            db.upsert_pending({"id": v["id"], "user_id": config.DEFAULT_USER_ID,
                               "source": "youtube", "url": v["url"],
                               "storage_key": None, "source_hash": v["id"],
                               "title": v["title"]})
            try:
                ingest_video(video_id=v["id"], user_id=config.DEFAULT_USER_ID)
                print(f"[corpus] {v['id']} done in {time.time() - t0:.0f}s", flush=True)
            except Exception as exc:
                print(f"[corpus] {v['id']} FAILED ({type(exc).__name__}: {exc})",
                      flush=True)

    missing = [v for v in todo_all if not _indexed(v)]
    print(f"\n[corpus] {len(todo_all) - len(missing)}/{len(todo_all)} indexed "
          f"in {(time.time() - started) / 60:.1f} min", flush=True)
    for v in todo_all:
        row = db.get_video(v["id"]) or {}
        mark = "ok  " if v not in missing else "MISS"
        print(f"  [{mark}] {v['id']}  frames={row.get('frame_count')}  "
              f"{row.get('status')}  {row.get('transcript_note') or ''}")
    if missing:
        print("\n[corpus] re-run to retry the misses (it skips what's already done).")
        return 1
    _export_vectors()
    _export_rows()
    print("\n[corpus] corpus complete — demo_corpus/ is queryable.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
