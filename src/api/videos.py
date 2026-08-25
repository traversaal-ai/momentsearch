"""Video registration API — the write path's front door.

Upload flow (gigabytes never touch this process):
  1. POST /api/videos/presign   -> scoped, time-limited PUT URL (server picks
                                   the key: {user}/{id}/source.{ext})
  2. browser PUTs the file straight to object storage
  3. POST /api/videos           -> HEAD-verify the object, insert a pending
                                   Postgres row, schedule a Prefect run, 202

YouTube flow: POST /api/videos {"url": ...} — the worker downloads it.

Single-user: every request acts as config.SINGLE_USER_ID (see user_id() below).
Keys, rows and vectors are still user_id-tagged throughout, so restoring real
per-user auth is a change to that one function — not to the data model.
"""
from __future__ import annotations

import re
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from .. import config, db, jobs, setup_check, storage
from ..samples import is_sample, sample_attribution
from ..config import (
    ALLOWED_UPLOAD_TYPES,
    MAX_UPLOAD_MB,
    SINGLE_USER_ID,
)
from ..rag import vector_store

router = APIRouter(prefix="/api/videos", tags=["videos"])

_EXT_RE = re.compile(r"^\.[A-Za-z0-9]{1,8}$")
_YT_RE = re.compile(
    r"(?:youtube\.com/(?:watch\?v=|shorts/|live/|embed/)|youtu\.be/)([A-Za-z0-9_-]{11})")


def require_auth() -> None:
    """No-op: this deployment is single-user and unauthenticated.

    Kept as a dependency on every mutating route so the enforcement point still
    EXISTS — restoring auth is editing this one function, not re-threading
    `Depends(...)` through two dozen endpoints. See src/api/auth.py for the
    warning that goes with it: reaching the port is owning the account.
    """
    return


def user_id() -> str:
    """The tenant every request acts as — always the one account.

    X-User-Id and Authorization are no longer read at all. Ignoring them rather
    than honouring them is the point: a stale header from an old browser tab (or
    a hand-edited one) can't steer reads or writes at some other tenant's data.
    """
    return SINGLE_USER_ID


# ── Presign ───────────────────────────────────────────────────────────────────

def purge_video(video_id: str, uid: str) -> None:
    """Erase a video everywhere — Qdrant vectors + GCP (frames, transcript, raw
    upload) + the manifest row. The caller is responsible for the ownership /
    sample checks; this just does the wipe (used by DELETE and by the
    reference-counted session cleanup in sessions.py)."""
    vector_store.delete_video(uid, video_id)
    # One prefix wipe removes the whole video footprint — frames, source upload
    # and transcript all live under `<user>/<video>/`.
    storage.delete_prefix(storage.video_prefix(uid, video_id))
    db.delete_video(video_id)


_MAX_UPLOAD_BYTES = MAX_UPLOAD_MB * 1024 * 1024


def _too_big(size: int) -> HTTPException:
    """Both numbers, always. The cap alone doesn't tell you whether to trim the
    video or give up on it — the file's own size is what makes that decision."""
    return HTTPException(
        413,
        f"{size / (1024 * 1024):,.0f} MB exceeds the {MAX_UPLOAD_MB} MB upload limit.")


class PresignRequest(BaseModel):
    filename: str
    content_type: str
    size: int
    sha256: str | None = None   # optional: lets an identical re-upload skip the upload entirely


@router.post("/presign", dependencies=[Depends(require_auth)])
def presign(req: PresignRequest, uid: str = Depends(user_id)):
    if req.size > _MAX_UPLOAD_BYTES:
        raise _too_big(req.size)
    if not any(req.content_type.startswith(t) for t in ALLOWED_UPLOAD_TYPES):
        raise HTTPException(415, "Only video uploads are accepted.")
    # Fast path: the browser hashed the file and we already have that exact
    # content indexed. Skip the upload AND the re-embed — the caller just links
    # the existing video into its session (seconds, no bytes transferred). Its
    # frames/vectors/transcript are already in Qdrant + GCP under the original.
    if req.sha256:
        dup = db.find_duplicate(uid, req.sha256.strip().lower(), exclude_id="")
        if dup:
            return {"mode": "exists", "video_id": dup["id"], "title": dup.get("title")}
    ext = Path(req.filename or "video.mp4").suffix.lower() or ".mp4"
    if not _EXT_RE.match(ext):
        ext = ".mp4"
    video_id = f"up_{uuid.uuid4().hex[:10]}"
    key = storage.upload_key(uid, video_id, ext)
    if not storage.presign_capable():
        # local-dev fallback: the API accepts the bytes itself
        return {"mode": "direct", "video_id": video_id, "key": key,
                "url": f"/api/videos/{video_id}/content?key={key}",
                "headers": {"Content-Type": req.content_type}}
    signed = storage.presign_put(key, req.content_type)
    return {"mode": "presigned", "video_id": video_id, "key": key, **signed}


@router.put("/{video_id}/content", dependencies=[Depends(require_auth)])
async def upload_direct(video_id: str, key: str, request: Request,
                        uid: str = Depends(user_id)):
    """Dev-only direct upload (STORAGE_PROVIDER=local can't presign)."""
    if storage.presign_capable():
        raise HTTPException(400, "Use the presigned URL to upload.")
    if not key.startswith(storage.video_prefix(uid, video_id)):
        raise HTTPException(403, "Key does not belong to this upload.")
    dest = storage.local_path(key)
    dest.parent.mkdir(parents=True, exist_ok=True)
    size = 0
    with dest.open("wb") as out:
        async for chunk in request.stream():
            size += len(chunk)
            if size > _MAX_UPLOAD_BYTES:
                # Aborted mid-stream, so `size` is only "past the cap", not the
                # file's real size — quote the cap alone rather than a figure
                # that understates how far over it is.
                out.close()
                dest.unlink(missing_ok=True)
                raise HTTPException(
                    413, f"Upload exceeds the {MAX_UPLOAD_MB} MB limit.")
            out.write(chunk)
    return {"ok": True, "key": key, "size": size}


# ── Register (returns 202 instantly; a worker does the heavy lifting) ─────────

class RegisterRequest(BaseModel):
    url: str | None = None        # YouTube
    video_id: str | None = None   # upload (from /presign)
    key: str | None = None        # upload (from /presign)
    title: str | None = None
    session_id: str | None = None  # drop it straight into this session
    speaker_recognition: bool = False  # "who said what" — Gemini diarization


@router.post("", status_code=202, dependencies=[Depends(require_auth)])
def register(req: RegisterRequest, uid: str = Depends(user_id)):
    # Don't accept a video into a stack that can't process it — it would just sit
    # `pending` forever. Reject up front naming the exact key to set (same gaps the
    # workspace banner shows). See src/setup_check.py.
    blockers = setup_check.ingest_blockers()
    if blockers:
        keys = ", ".join(k for b in blockers for k in b["env"])
        raise HTTPException(
            503, f"Video ingest isn't configured yet — set {keys} in your .env "
                 f"(see the setup banner). Existing videos still work.")
    # Speaker recognition is Gemini-only: reject up front if the box is checked
    # but no key is configured, so the user isn't surprised by an unlabeled video.
    if req.speaker_recognition and not config.GEMINI_API_KEY:
        raise HTTPException(
            400, "Gemini key is missing — set GEMINI_API_KEY to use speaker recognition.")
    diarize = bool(req.speaker_recognition)
    if req.url:
        m = _YT_RE.search(req.url)
        if not m:
            raise HTTPException(400, "Not a recognizable YouTube URL.")
        video_id = f"yt_{m.group(1)}"
        # Already indexed for this user (or a shared sample)? Re-adding it — e.g.
        # after deleting its session — should just re-link the working copy, not
        # re-ingest it or clobber the shared sample row via upsert.
        existing = db.get_video(video_id)
        if existing and existing["status"] == "indexed" and (
                existing["user_id"] == uid or is_sample(video_id)):
            if req.session_id and db.get_session(req.session_id, uid):
                db.add_session_video(req.session_id, video_id)
            return {"video_id": video_id, "status": "indexed", "deduped": True}
        row = db.upsert_pending({"id": video_id, "user_id": uid, "source": "youtube",
                                 "url": req.url, "storage_key": None,
                                 "source_hash": video_id, "title": req.title,
                                 "diarize": diarize})
    elif req.video_id and req.key:
        # Never trust the client's key: it must be the one WE minted for them.
        if not req.key.startswith(storage.video_prefix(uid, req.video_id)):
            raise HTTPException(403, "Key does not belong to this user/upload.")
        meta = storage.head(req.key)
        if meta is None:
            raise HTTPException(404, "Object not found — did the upload finish?")
        if meta["size"] > _MAX_UPLOAD_BYTES:
            storage.delete_key(req.key)
            raise _too_big(meta["size"])
        title = req.title or Path(req.key).stem
        row = db.upsert_pending({"id": req.video_id, "user_id": uid, "source": "upload",
                                 "url": None, "storage_key": req.key,
                                 "source_hash": None, "title": title,
                                 "diarize": diarize})
    else:
        raise HTTPException(400, "Provide either url (YouTube) or video_id+key (upload).")

    # Adding a video FROM a session puts it in that session — that's where the
    # user is, and a video nobody can find isn't much use. Unknown/foreign
    # session ids are ignored rather than fatal: the video is already registered
    # and ingesting by this point, so failing here would strand it.
    if req.session_id and db.get_session(req.session_id, uid):
        db.add_session_video(req.session_id, row["id"])

    # Fair dispatch (WFQ): leave it `pending` — the dispatcher admits it in fair
    # order (src/dispatcher.py). FIFO mode: enqueue to Prefect immediately.
    if config.ENABLE_FAIR_DISPATCH:
        return {"video_id": row["id"], "status": "pending"}
    flow_run_id = jobs.enqueue_video(row["id"], uid)
    return {"video_id": row["id"], "status": row["status"], "flow_run_id": flow_run_id}


# ── Status / lifecycle ─────────────────────────────────────────────────────────

_PUBLIC_FIELDS = ("id", "source", "url", "title", "status", "error",
                  "frame_count", "progress", "attempts", "transcript_note",
                  "created_at", "updated_at")


def _public(row: dict) -> dict:
    out = {k: row.get(k) for k in _PUBLIC_FIELDS}
    # Samples are protected: unselectable-yes, deletable-no. The UI hides the ✕
    # on these and the delete endpoint refuses them.
    out["is_sample"] = is_sample(row["id"])
    # Creator credit — populated for the curated samples only (samples.py); None
    # for anything a user added, since ingest doesn't record the uploader.
    out.update(sample_attribution(row["id"]))
    # Thumbnail for UPLOADS: they have no external still like YouTube's CDN, so
    # the list would show an empty box forever. Point it at frame 0 (the same
    # stored still the moment cards use) once frames exist — None while still
    # processing so the UI shows a placeholder, not a broken image. YouTube keeps
    # using its own CDN thumbnail (fast, free), so we skip signing one for it.
    out["thumbnail"] = None
    if row.get("source") != "youtube" and row.get("frame_count"):
        from ..rag.search import _thumb_url
        out["thumbnail"] = _thumb_url(row["user_id"], row["id"], 0)
    return out


@router.get("")
def list_videos(uid: str = Depends(user_id), status: str | None = None):
    # Samples ride along (read-only, undeletable) so the app has something to
    # search before anything has been ingested.
    rows = db.list_videos(uid, status=status, include_samples=True)
    return {"videos": [_public(r) for r in rows]}


@router.get("/{video_id}")
def get_video(video_id: str, uid: str = Depends(user_id)):
    row = db.get_video(video_id)
    if row is None or row["user_id"] != uid:
        raise HTTPException(404, "Video not found.")
    return _public(row)


@router.post("/{video_id}/retry", status_code=202, dependencies=[Depends(require_auth)])
def retry(video_id: str, uid: str = Depends(user_id)):
    row = db.get_video(video_id)
    if row is None or row["user_id"] != uid:
        raise HTTPException(404, "Video not found.")
    db.set_status(video_id, "pending", error=None)
    db.set_transcript_note(video_id, None)   # clear a stale transcript warning; the re-run resets it
    if config.ENABLE_FAIR_DISPATCH:
        return {"video_id": video_id, "status": "pending"}  # dispatcher re-admits it fairly
    flow_run_id = jobs.enqueue_video(video_id, uid)
    return {"video_id": video_id, "status": "pending", "flow_run_id": flow_run_id}


@router.delete("/{video_id}", dependencies=[Depends(require_auth)])
def delete(video_id: str, uid: str = Depends(user_id)):
    """Deleting a video purges everything: vectors, thumbnails, the raw upload,
    and the manifest row — batch calls where the provider supports them.
    Sample videos are protected (unselect them from a query instead)."""
    if is_sample(video_id):
        raise HTTPException(403, "Sample videos can't be deleted — unselect it "
                                 "from your query instead.")
    row = db.get_video(video_id)
    if row is None or row["user_id"] != uid:
        raise HTTPException(404, "Video not found.")
    purge_video(video_id, uid)
    return {"ok": True, "video_id": video_id}
