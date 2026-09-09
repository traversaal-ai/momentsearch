"""Demo startup gate — load the prebuilt corpus instead of indexing anything.

This is what src/seed.py runs, in place of ingesting, when SEED_MODE=restore —
which is what both shipped presets (.env.example, .env.local.example) set.
The ten videos were indexed once, offline, by
src/build_demo_corpus.py; all three durable stores for them live in
demo_corpus/:

    demo_corpus/vectors/*.npz  the frame + transcript vectors (~7MB)
    demo_corpus/objects/       frame JPEGs + transcript.json
    demo_corpus/videos.json    the ten ms_videos rows, as plain JSON

Boot is: create the schema, insert ten rows, and load the vectors into the
bundled Qdrant if it is empty (demo_corpus/qdrant/ is Qdrant's own storage tree,
rebuilt locally and git-ignored — 1.1GB of preallocated segments for 7MB of
numbers). The frames are read from the folder as they are. A second or two, no
YouTube, no CLIP download, no API keys — instead of the 45-minute ingest that
produced them.

Exits non-zero if the corpus isn't actually there, so the stack fails loudly
rather than serving a demo whose citations 404.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from urllib.parse import urlsplit

from . import config, db, storage
from .rag import vector_store
from .samples import SAMPLE_VIDEOS, sample_video_id

VIDEOS_JSON = config.DEMO_VIDEOS_JSON


def _expected() -> list[str]:
    return [sample_video_id(v["url"]) for v in SAMPLE_VIDEOS]


def _rows_present(ids: list[str]) -> list[str]:
    have = db.videos_by_ids(ids)
    return [i for i in ids if i not in have or have[i].get("status") != "indexed"]


def _restore_rows() -> None:
    """Insert the ten rows described by demo_corpus/videos.json.

    Plain JSON, not a pg_dump: ten records a human can read and diff in a pull
    request, with no dependency on Postgres' dump format, no psql meta-commands
    to strip at runtime, and no schema-order coupling — this names its columns.
    The owner is filled in from config, not shipped, so the rows land in whatever
    tenant this instance runs as.

    ON CONFLICT DO NOTHING makes it safe on every boot: a row the user already
    has (or edited) is left alone.
    """
    if not VIDEOS_JSON.exists():
        sys.exit(f"[demo] {VIDEOS_JSON} is missing — the corpus is incomplete.\n"
                 f"       Rebuild it with `python -m src.build_demo_corpus`.")
    rows = json.loads(VIDEOS_JSON.read_text(encoding="utf-8"))
    with db.pool().connection() as conn:
        for r in rows:
            conn.execute(
                """
                INSERT INTO ms_videos (id, user_id, source, url, source_hash, title,
                                       status, frame_count, embed_version, diarize,
                                       transcript_note)
                VALUES (%(id)s, %(user_id)s, %(source)s, %(url)s, %(source_hash)s,
                        %(title)s, %(status)s, %(frame_count)s, %(embed_version)s,
                        %(diarize)s, %(transcript_note)s)
                ON CONFLICT (id) DO NOTHING
                """,
                {**r, "user_id": config.DEFAULT_USER_ID},
            )
    print(f"[demo] {len(rows)} rows loaded from {VIDEOS_JSON.name}", flush=True)


def _restore_vectors() -> None:
    """Rebuild the demo's Qdrant collections from the shipped npz files.

    A clone has demo_corpus/vectors/ (about 8MB, committed) but NOT
    demo_corpus/qdrant/ — Qdrant's own storage tree is 1.1GB of preallocated
    segments for these 2000 points, which is not a thing to put in git. So the
    numbers ship and the database is rebuilt here, once, in a second or two.
    Already populated (every start after the first) costs one count() per
    collection and writes nothing.
    """
    from . import demo_corpus_io as vio

    qc = vector_store.demo_client()
    for coll in (config.IMAGE_COLLECTION, config.TEXT_COLLECTION):
        try:
            n = vio.import_collection(qc, coll)
        except Exception as exc:
            print(f"[demo] could not load {coll}: {type(exc).__name__}: {exc}",
                  flush=True)
            continue
        if n:
            print(f"[demo] loaded {n} vectors into {coll} (first start)", flush=True)


def _verify(ids: list[str]) -> list[str]:
    """A video counts as ready only if all three stores have it: the row, its
    frame vectors in Qdrant, and its thumbnail in storage. Checking one of the
    three is how you ship a demo that looks indexed and answers nothing.

    With DEMO_LOCAL on, two of those three are the SHIPPED ones —
    vector_store/storage route sample ids to the bundled Qdrant and to
    demo_corpus/objects — so this verifies exactly what a question will read."""
    bad = []
    for vid in ids:
        row = db.get_video(vid) or {}
        why = []
        if row.get("status") != "indexed":
            why.append(f"row status={row.get('status') or 'missing'}")
        try:
            if not vector_store.frame_times(config.DEFAULT_USER_ID, vid):
                why.append("no frame vectors in qdrant")
        except Exception as exc:
            why.append(f"qdrant unreachable ({type(exc).__name__})")
        try:
            if not storage.exists(storage.frame_key(config.DEFAULT_USER_ID, vid, 0)):
                why.append("no frames in storage")
        except Exception as exc:
            why.append(f"storage unreadable ({type(exc).__name__})")
        if why:
            bad.append(f"{vid}: {', '.join(why)}")
    return bad


def main() -> int:
    ids = _expected()
    print(f"[demo] restoring the prebuilt corpus ({len(ids)} videos)", flush=True)
    if config.DEMO_LOCAL:
        print(f"[demo] vectors: {config.DEMO_QDRANT_URL} (shipped, local) "
              f"[{config.IMAGE_COLLECTION} + {config.TEXT_COLLECTION}]", flush=True)
        print(f"[demo] frames : {config.DEMO_DATA} (shipped, local)", flush=True)
        # "postgres" appears in every URL as the scheme, so the host is what
        # decides — the compose service is literally called postgres.
        host = urlsplit(config.DATABASE_URL).hostname or ""
        local_db = host in ("postgres", "localhost", "127.0.0.1", "host.docker.internal")
        print(f"[demo] rows   : the app's own database at {host or '?'} "
              f"({'local' if local_db else 'hosted'}) — the one store it shares with you",
              flush=True)
        if vector_store.demo_split():
            print(f"[demo] your own uploads stay on {config.QDRANT_URL or 'the embedded store'} "
                  f"/ {config.STORAGE_PROVIDER}", flush=True)
    else:
        print(f"[demo] DEMO_LOCAL=false — samples expected in your own stores: "
              f"{config.QDRANT_URL} / {config.STORAGE_PROVIDER}", flush=True)

    db.init_schema()
    if _rows_present(ids):
        _restore_rows()
    else:
        print("[demo] rows already loaded", flush=True)
    _restore_vectors()

    bad = _verify(ids)
    if bad:
        print("\n[demo] corpus is NOT usable:", flush=True)
        for b in bad:
            print(f"  - {b}", flush=True)
        if config.DEMO_LOCAL:
            print("\n  demo_corpus/ ships with the repo, and the bundled qdrant\n"
                  "  service mounts it — check both are there. Rebuild it with\n"
                  "  `python -m src.build_demo_corpus`, or set DEMO_LOCAL=false to\n"
                  "  host the samples in your own stores instead.", flush=True)
        else:
            # They opted out of the shipped copy, so the samples are expected in
            # THEIR stores and nothing has put them there. Restoring cannot fix
            # that; indexing can.
            print("\n  DEMO_LOCAL=false means the samples live in YOUR Qdrant and\n"
                  "  storage, and they are not there yet. Set SEED_MODE=ingest to\n"
                  "  index them (~45 min, needs YouTube to cooperate), or unset\n"
                  "  DEMO_LOCAL to read the copy shipped in demo_corpus/.", flush=True)
        return 1
    print(f"[demo] {len(ids)} videos ready — vectors, frames and rows all present.",
          flush=True)

    # The corpus removes the INDEXING cost, not the model: the visual branch
    # still CLIP-embeds the question at query time. On a fresh clone that model
    # is not downloaded yet, and because this gate is what api/worker wait on,
    # returning now would open :8000 on a demo whose first question times out.
    # So hold the gate until the embedding service answers, exactly as the
    # ingest seeder did.
    from .seeding import wait_for_clip
    wait_for_clip()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
