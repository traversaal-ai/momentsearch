"""Sample-corpus seeding — run to completion BEFORE the app serves.

This is the startup gate: seed.py runs it as a one-shot container that must
exit 0 before api/worker start (docker-compose depends_on ...
service_completed_successfully), so the UI is never reachable with a
half-indexed corpus. Idempotent and durable (Qdrant Cloud): once the four
talks are indexed they stay indexed, so every later start finishes in seconds.
"""
from __future__ import annotations

import json
import time
import urllib.request

from . import config, db, storage
from .ingest.pipeline import ingest_video
from .rag import vector_store
from .samples import SAMPLE_VIDEOS, sample_video_id

_MAX_PASSES = 3  # re-attempt videos that fail (e.g. a transient YouTube hiccup)


def wait_for_clip(timeout: int = 600) -> None:
    """Block until the embedding service answers /healthz — first boot downloads
    the model (~600MB).

    No-op in two cases: embedding runs in-process (no EMBED_SERVICE_URL), or both
    branches use hosted APIs — an API has no weights to warm, so there is nothing
    to wait for even when the service container happens to be running.
    """
    from .providers import embed

    if not config.EMBED_SERVICE_URL:
        return
    if not (embed.image_config().local or embed.text_config().local):
        print("[seed] embedding providers are hosted APIs — nothing to warm up",
              flush=True)
        return
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(config.CLIP_SERVICE_URL + "/healthz", timeout=5) as r:
                if r.status == 200:
                    print("[seed] CLIP service ready", flush=True)
                    return
        except Exception:
            pass
        print("[seed] waiting for CLIP service to warm up…", flush=True)
        time.sleep(5)
    print("[seed] CLIP service not ready in time — attempting anyway", flush=True)


def _frames_present(vid: str) -> bool:
    """True when a sample's frames are fully in place at the CURRENT keys — the
    thumbnail (frame 0) in object storage AND frame vectors in Qdrant. Lets the
    seed adopt a stuck-but-complete row instead of re-ingesting it."""
    try:
        return (storage.exists(storage.frame_key(config.DEFAULT_USER_ID, vid, 0))
                and bool(vector_store.frame_times(config.DEFAULT_USER_ID, vid)))
    except Exception:
        return False


def _not_indexed() -> list[dict]:
    """Samples that still need (re)ingest: never indexed, indexed on a DIFFERENT
    embedding version, OR indexed but with frames NOT at the current key layout.
    A row stuck mid-flight but whose frames are all present is adopted as indexed
    (see _frames_present) rather than re-ingested — a stuck sample must never
    abort a deploy by timing out the release-command seed.

    EMBED_VERSION is derived from the visual provider + model, so switching either
    one bumps it and auto-re-seeds all four samples. The layout probe covers the
    storage re-key: a row can say 'indexed' while its thumbnails live under the
    old keys (and would 404), so we re-ingest it onto the new `<user>/<video>/`
    keys. Cheap HEAD, and it self-heals only the samples that actually moved."""
    out = []
    for v in SAMPLE_VIDEOS:
        vid = sample_video_id(v["url"])
        row = db.get_video(vid) or {}
        ev = row.get("embed_version")
        stale = ev is not None and ev != config.EMBED_VERSION
        status = row.get("status")

        # Self-heal a stuck-but-complete row. A sample left mid-flight (a worker
        # or the deploy's release machine killed during embed) sits in
        # fetching/sampling/embedding forever even though its frames are actually
        # all there. Re-ingesting it on the release machine means an in-process
        # re-embed + YouTube re-download that blows past the release timeout and
        # ABORTS THE WHOLE DEPLOY. So: if the frames are present at the current
        # keys/version, adopt the row as indexed instead of re-ingesting it.
        if row and status != "indexed" and not stale and _frames_present(vid):
            db.set_status(vid, "indexed", embed_version=config.EMBED_VERSION, progress=1.0)
            continue

        misplaced = False
        if status == "indexed" and not stale:
            try:  # frame 0 always exists for an indexed video — probe the new key
                misplaced = not storage.exists(
                    storage.frame_key(config.DEFAULT_USER_ID, vid, 0))
            except Exception:
                misplaced = False
        if status != "indexed" or stale or misplaced:
            out.append(v)
    return out


def seed_to_completion() -> bool:
    """Index every sample, retrying failures. Returns True iff all four end up
    indexed. Blocking — the caller (seed.py) gates the app on this."""
    if not config.SEED_SAMPLE_VIDEOS:
        print("[seed] SEED_SAMPLE_VIDEOS=false — skipping", flush=True)
        return True

    db.init_schema()
    vector_store.ensure_collection()

    # Light sampling for the demo corpus so all four finish in ~2 min on CPU
    # (the Karpathy talk is 1h). User uploads run in separate Prefect
    # subprocesses that re-read config, so they keep full quality.
    config.MAX_FRAMES = min(config.MAX_FRAMES, 60)
    config.FRAME_INTERVAL_SEC = max(config.FRAME_INTERVAL_SEC, 5.0)

    wait_for_clip()

    # The loop below no-ops when nothing is pending, so no early return — we still
    # fall through to the transcript backfill for already-indexed samples.
    for attempt in range(1, _MAX_PASSES + 1):
        todo = _not_indexed()
        if not todo:
            break
        print(f"[seed] pass {attempt}/{_MAX_PASSES}: indexing {len(todo)} sample(s)", flush=True)
        for v in todo:
            vid = sample_video_id(v["url"])
            print(f"[seed] -> {vid}: {v['title']}", flush=True)
            db.upsert_pending({"id": vid, "user_id": config.DEFAULT_USER_ID,
                               "source": "youtube", "url": v["url"],
                               "storage_key": None, "source_hash": vid,
                               "title": v["title"]})
            try:
                ingest_video(video_id=vid, user_id=config.DEFAULT_USER_ID)
            except Exception as exc:
                print(f"[seed] {vid} failed ({type(exc).__name__}: {exc})", flush=True)

    remaining = _not_indexed()
    if remaining:
        names = ", ".join(sample_video_id(v["url"]) for v in remaining)
        print(f"[seed] STILL not indexed after {_MAX_PASSES} passes: {names}", flush=True)
        return False
    _backfill_transcripts()
    print("[seed] sample corpus complete — all four indexed", flush=True)
    return True


def _backfill_transcripts() -> None:
    """Ensure every sample has its durable transcript copy in object storage.

    Samples indexed before transcript-to-storage existed won't have the file, and
    they won't re-ingest (embed_version already matches), so the synced transcript
    panel would 404. Reconstruct the file from the chunks already in Qdrant — no
    YouTube, idempotent (skips any sample that already has it)."""
    for v in SAMPLE_VIDEOS:
        vid = sample_video_id(v["url"])
        key = storage.transcript_key(config.DEFAULT_USER_ID, vid)
        try:
            if storage.exists(key):
                continue
            chunks = vector_store.fetch_chunks(config.DEFAULT_USER_ID, vid)
            if not chunks:
                continue
            storage.put_bytes(
                key, json.dumps(chunks, ensure_ascii=False).encode("utf-8"),
                "application/json")
            print(f"[seed] backfilled transcript -> storage: {vid} ({len(chunks)} chunks)", flush=True)
        except Exception as exc:
            print(f"[seed] transcript backfill failed for {vid}: {exc}", flush=True)
