"""Per-video ingest pipeline — a Prefect flow of three stage-tasks.

pending -> fetching -> sampling -> embedding -> indexed | skipped | failed

Stages:
  1. fetch    acquire the source into worker scratch (bucket download for
              uploads, yt-dlp for YouTube), hash it, skip duplicates
  2. sample   ffmpeg pipe-to-memory keyframes -> pHash dedup -> thumbnails
              batch-uploaded to object storage
  3. embed    CLIP-embed the surviving frames (batched) -> idempotent Qdrant
              upsert (deterministic IDs, user_id-tagged)

Orchestration: Prefect Cloud. The API triggers a deployment run (src/jobs.py);
worker.py serves this flow and picks runs up. Each task carries its own retry
policy — a completed stage is not re-run when a later one fails and retries.

Postgres remains the business-status source of truth: tasks update the videos
row; Prefect Cloud's UI is the operational view (logs, retries, run history).
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from prefect import flow, task

from .. import db, storage
from ..config import CLIP_BATCH, EMBED_VERSION
from ..rag import vector_store
from ..rag.embeddings import embed_jpegs
from . import fetch as fetch_mod
from .dedup import dedup
from .frames import Frame, sample

_UPLOAD_POOL = 8  # concurrent thumbnail PUTs (I/O-bound)


@task(name="fetch", retries=2, retry_delay_seconds=[30, 120])
def t_fetch(video_id: str, user_id: str) -> str:
    """Source video -> worker scratch file; duplicate check via source_hash.

    Returns "" when the content is a duplicate of an already-indexed video for
    this user (row marked 'skipped' — a plain outcome, not a retryable error).
    """
    db.set_status(video_id, "fetching")
    row = db.get_video(video_id)
    if row is None:
        raise ValueError(f"no manifest row for {video_id}")

    if row["source"] == "youtube":
        path, title = fetch_mod.fetch_youtube(row["url"], video_id)
        source_hash = video_id  # the YouTube id IS the content identity
        db.set_status(video_id, "fetching", title=title, source_hash=source_hash)
    else:
        path = fetch_mod.fetch_upload(row["storage_key"], video_id)
        source_hash = fetch_mod.sha256_file(path)
        db.set_status(video_id, "fetching", source_hash=source_hash)

    dup = db.find_duplicate(user_id, source_hash, exclude_id=video_id)
    if dup:
        path.unlink(missing_ok=True)
        # This stub's own raw upload in the bucket is now orphaned — it skipped
        # before sampling, so it has no frames/transcript, only the raw file.
        # Remove it so a re-upload doesn't leak a video object on GCP.
        if row.get("storage_key"):
            storage.delete_key(row["storage_key"])
        # Re-upload of content the user already has indexed (e.g. they deleted the
        # session and added the same file again). Don't leave a dead "duplicate"
        # card: hand the working original to whatever session(s) this attempt was
        # dropped into, then drop this redundant stub.
        db.set_status(video_id, "skipped", error=f"duplicate of {dup['id']}")
        db.resolve_duplicate(video_id, dup["id"])
        print(f"[fetch] {video_id}: duplicate of {dup['id']} — linked original, "
              f"dropped stub + its raw upload")
        return ""
    return str(path)


@task(name="sample")
def t_sample(video_id: str, user_id: str, path: str) -> list[Frame]:
    """Keyframes in memory -> pHash dedup -> thumbnails to object storage."""
    db.set_status(video_id, "sampling", progress=0.0)
    frames = sample(Path(path))
    if not frames:
        raise RuntimeError("No frames could be extracted from the video.")
    kept = dedup(frames)
    print(f"[sample] {video_id}: {len(frames)} sampled -> {len(kept)} after dedup")

    # Idempotent re-run: clear any thumbnails a previous attempt left behind.
    storage.delete_prefix(storage.frame_prefix(user_id, video_id))
    done = 0

    def _put(i_f: tuple[int, Frame]) -> None:
        nonlocal done
        i, f = i_f
        storage.put_bytes(storage.frame_key(user_id, video_id, i), f.jpeg, "image/jpeg")
        done += 1
        if done % 25 == 0:
            db.set_progress(video_id, done / len(kept))

    with ThreadPoolExecutor(max_workers=_UPLOAD_POOL) as ex:
        list(ex.map(_put, enumerate(kept)))
    db.set_progress(video_id, 1.0)
    return kept


@task(name="embed-index", retries=2, retry_delay_seconds=60)
def t_embed_index(video_id: str, user_id: str, frames: list[Frame]) -> int:
    """Batched CLIP embeddings -> idempotent multi-tenant Qdrant upsert."""
    db.set_status(video_id, "embedding", progress=0.0)
    vector_store.ensure_collection()
    vector_store.delete_video(user_id, video_id)  # drop stale points from prior runs

    total = 0
    for start in range(0, len(frames), CLIP_BATCH):
        batch = frames[start:start + CLIP_BATCH]
        vectors = embed_jpegs([f.jpeg for f in batch])
        vector_store.upsert_frames(
            user_id, video_id,
            ids=range(start, start + len(batch)),
            vectors=vectors,
            payloads=[{"user_id": user_id, "video_id": video_id, "ms": f.ms,
                       "idx": start + i, "modality": "frame",
                       "t_start": f.ms / 1000.0, "t_end": f.ms / 1000.0,
                       "embed_version": EMBED_VERSION}
                      for i, f in enumerate(batch)],
        )
        total += len(batch)
        db.set_progress(video_id, total / len(frames))
    db.set_status(video_id, "indexed", frame_count=total,
                  embed_version=EMBED_VERSION, progress=1.0)
    return total


@task(name="transcript", retries=1, retry_delay_seconds=30)
def t_transcript(video_id: str, user_id: str, path: str | None = None) -> int:
    """The 2nd (text) branch -> time chunks -> text embeddings -> text collection.

    Source of the cues depends on where the video came from:
      * YouTube -> captions (yt-dlp; fast, free, already timestamped)
      * upload  -> ASR from the file's own audio (src/ingest/asr.py, whisper-1)
    Both yield [{text,t_start,t_end}], so everything below is identical. Best-
    effort: no captions, no audio, or any failure just leaves the video visual-
    only — never fails the flow. Runs AFTER embed-index (whose delete clears both
    branches first)."""
    from ..config import ENABLE_TRANSCRIPT, TEXT_EMBED_VERSION
    from ..rag.embeddings import embed_docs
    from .transcript import chunk_cues, fetch_transcript

    if not ENABLE_TRANSCRIPT:
        return 0
    row = db.get_video(video_id) or {}
    try:
        if row.get("source") == "youtube" and row.get("url"):
            cues, origin, empty_note = fetch_transcript(row["url"], video_id), "captions", "no captions"
        elif path:
            from .asr import transcribe
            cues, origin, empty_note = transcribe(path), "ASR", "no speech"
        else:
            return 0  # nothing to transcribe (e.g. upload with the scratch file gone)
        chunks = chunk_cues(cues)
        if not chunks:
            print(f"[transcript] {video_id}: {empty_note} — visual-only")
            return 0
        # Persist a durable copy of the timed transcript to object storage — so a
        # future re-embed (e.g. swapping the text model) doesn't have to re-fetch
        # captions from YouTube. Best-effort: a store failure never blocks
        # indexing (the vectors below are the thing that must succeed).
        try:
            import json
            storage.put_bytes(
                storage.transcript_key(user_id, video_id),
                json.dumps(chunks, ensure_ascii=False).encode("utf-8"),
                "application/json")
        except Exception as exc:
            print(f"[transcript] {video_id}: bucket store failed ({exc}) — indexing anyway")
        vector_store.ensure_text_collection()
        vecs = embed_docs([c["text"] for c in chunks])
        vector_store.upsert_chunks(user_id, video_id, vecs, payloads=[
            {"user_id": user_id, "video_id": video_id, "modality": "text",
             "t_start": c["t_start"], "t_end": c["t_end"],
             "ms": int(c["t_start"] * 1000), "text": c["text"],
             "embed_version": TEXT_EMBED_VERSION} for c in chunks])
        print(f"[transcript] {video_id}: indexed {len(chunks)} transcript chunks ({origin})")
        return len(chunks)
    except Exception as exc:
        print(f"[transcript] {video_id}: failed ({type(exc).__name__}: {exc}) — visual-only")
        return 0


@flow(name="ms-ingest-video", log_prints=True, timeout_seconds=3600)
def ingest_video(video_id: str, user_id: str) -> dict:
    attempt = db.bump_attempts(video_id)
    path: str | None = None
    try:
        path = t_fetch(video_id, user_id)
        if not path:  # duplicate — already marked 'skipped' by t_fetch
            print(f"[ingest] {video_id} skipped (duplicate content)")
            return {"video_id": video_id, "skipped": True}
        frames = t_sample(video_id, user_id, path)
        n = t_embed_index(video_id, user_id, frames)
        # Transcript branch AFTER frames (embed-index's delete clears both first).
        # Pass the scratch file: uploads have no captions, so ASR reads its audio.
        t = t_transcript(video_id, user_id, path)
        print(f"[ingest] {video_id} indexed: {n} frames + {t} transcript chunks (attempt {attempt})")
        return {"video_id": video_id, "frames": n, "transcript_chunks": t}
    except Exception as exc:
        db.set_status(video_id, "failed", error=f"{type(exc).__name__}: {exc}")
        raise  # Prefect marks the run Failed; full trace in the Cloud UI
    finally:
        if path:  # scratch only — durable copies live in object storage
            Path(path).unlink(missing_ok=True)
