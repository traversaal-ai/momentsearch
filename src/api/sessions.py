"""Sessions — a folder of videos plus the chat about them.

    GET    /api/sessions                 list (bootstraps first run — see below)
    POST   /api/sessions                 new, EMPTY session
    GET    /api/sessions/{id}            session + its videos + its chat
    PATCH  /api/sessions/{id}            rename
    DELETE /api/sessions/{id}            drop session + membership + chat
    POST   /api/sessions/{id}/ask        ask, scoped to THIS session's videos
    POST   /api/sessions/{id}/videos     add an existing video
    DELETE /api/sessions/{id}/videos/{v} remove one (the video itself survives)

Two rules shape this file:

  * A session searches ONLY its own videos. The one seeded "Demo videos"
    session holds the shared sample corpus; every session the user creates
    starts empty and stays sample-free.
  * An empty session must SAY so. `/api/ask` treats "no video_ids" as "search
    everything", so a brand-new session would otherwise silently answer from
    the demo videos — the answer would look right and be from the wrong place.
"""
from __future__ import annotations

import json
import queue
import threading
import uuid

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from .. import db
from ..config import INFLIGHT_STATUSES
from ..providers.embed.base import EmbedUnavailable
from ..rag import search as rag_search
from ..samples import SAMPLE_VIDEOS, is_sample, sample_video_id
from .videos import _public, purge_video, require_auth, user_id

router = APIRouter(prefix="/api/sessions", tags=["sessions"])

DEMO_TITLE = "Demo videos"
# The session first-run lands in. Named, not "Untitled", so the workspace never
# looks broken on arrival — and renameable, because naming it is the first thing
# someone with a real project wants to do.
FIRST_SESSION_TITLE = "My first search"
_MAX_TITLE = 120


def _session_out(row: dict) -> dict:
    return {"id": row["id"], "title": row["title"], "kind": row["kind"],
            "video_count": row.get("video_count"),
            "message_count": row.get("message_count"),
            "created_at": row.get("created_at"), "updated_at": row.get("updated_at")}


def _message_out(row: dict) -> dict:
    return {"id": row["id"], "role": row["role"], "content": row["content"],
            "citations": row.get("citations") or [], "meta": row.get("meta") or {},
            "created_at": row.get("created_at")}


def _require(session_id: str, uid: str) -> dict:
    row = db.get_session(session_id, uid)
    if row is None:
        raise HTTPException(404, "Session not found.")
    return row


def _link_samples(session_id: str) -> None:
    """Link every EXISTING sample row into a session (idempotent). Samples that
    aren't seeded yet are simply skipped — the FK would reject ids the seeder
    hasn't created — and picked up on a later call (see _sync_demo_session)."""
    for vid in db.videos_by_ids([sample_video_id(v["url"]) for v in SAMPLE_VIDEOS]):
        db.add_session_video(session_id, vid)


def _seed_first_run(uid: str) -> None:
    """Bootstrap a workspace that has never had a session.

    TWO sessions, in this order, because the order decides where onboarding
    starts: `list_sessions` is newest-first, so the empty own session is row 0
    and the UI opens it at step 1 ("add a video") — which is the flow we want a
    first-time cloner in. The demo session is created first and sits behind it as
    the escape hatch for someone with no video to hand: it is already indexed, so
    it can answer a question immediately.

    Only ever runs on a workspace with ZERO sessions, so deleting the starter
    session doesn't make it reappear — that would fight the user.
    """
    demo = f"s_{uuid.uuid4().hex}"
    db.create_session(demo, uid, DEMO_TITLE, kind="demo")
    _link_samples(demo)
    db.create_session(f"s_{uuid.uuid4().hex}", uid, FIRST_SESSION_TITLE)


def _sync_demo_session(rows: list[dict]) -> None:
    """Self-heal an empty/partial demo session. If the demo session was created
    before the samples were indexed (e.g. a first visit during a re-seed), it
    would be empty forever — _seed_first_run only runs on a workspace with NO
    sessions. So whenever the demo session is missing samples, back-fill them."""
    demo = next((r for r in rows if r.get("kind") == "demo"), None)
    if demo and (demo.get("video_count") or 0) < len(SAMPLE_VIDEOS):
        _link_samples(demo["id"])


# ── CRUD ─────────────────────────────────────────────────────────────────────

class NewSession(BaseModel):
    title: str = "New session"


class Rename(BaseModel):
    title: str


@router.get("")
def list_sessions(uid: str = Depends(user_id)):
    rows = db.list_sessions(uid)
    if not rows:                      # first visit (or they deleted everything)
        _seed_first_run(uid)
        rows = db.list_sessions(uid)
    else:                             # back-fill a demo session seeded while empty
        _sync_demo_session(rows)
    return {"sessions": [_session_out(r) for r in rows]}


@router.post("", status_code=201, dependencies=[Depends(require_auth)])
def new_session(req: NewSession, uid: str = Depends(user_id)):
    title = (req.title or "").strip()[:_MAX_TITLE] or "New session"
    # Deliberately no videos: a new session is empty until you put something in
    # it. This is the behaviour that keeps sessions meaningfully separate.
    row = db.create_session(f"s_{uuid.uuid4().hex}", uid, title)
    return _session_out(row)


@router.get("/{session_id}")
def get_session(session_id: str, uid: str = Depends(user_id)):
    row = _require(session_id, uid)
    videos = [_public(v) for v in db.session_videos(session_id)]
    return {**_session_out(row),
            "videos": videos,
            "messages": [_message_out(m) for m in db.list_messages(session_id)]}


@router.patch("/{session_id}", dependencies=[Depends(require_auth)])
def rename(session_id: str, req: Rename, uid: str = Depends(user_id)):
    title = (req.title or "").strip()[:_MAX_TITLE]
    if not title:
        raise HTTPException(400, "Title can't be empty.")
    row = db.rename_session(session_id, uid, title)
    if row is None:
        raise HTTPException(404, "Session not found.")
    return _session_out(row)


@router.delete("/{session_id}", dependencies=[Depends(require_auth)])
def delete(session_id: str, uid: str = Depends(user_id)):
    _require(session_id, uid)
    # Snapshot this session's videos before its pointers are cascaded away.
    vids = [v["id"] for v in db.session_videos(session_id)]
    if not db.delete_session(session_id, uid):
        raise HTTPException(404, "Session not found.")
    # Reference-counted cleanup: purge every video this session was the LAST to
    # hold (Qdrant + GCP + row). A video still referenced by another session
    # survives; shared samples are never touched.
    purged = []
    for vid in vids:
        if is_sample(vid):
            continue
        row = db.get_video(vid)
        if row is None or row["user_id"] != uid:
            continue
        if db.count_video_sessions(vid) == 0:
            # Two args, not three: purge_video looks nothing up from the row, and
            # passing it raised TypeError here — 500ing AFTER the session row was
            # already deleted, which orphaned the video's vectors and files.
            purge_video(vid, uid)
            purged.append(vid)
    return {"ok": True, "session_id": session_id, "purged": purged}


# ── Membership ───────────────────────────────────────────────────────────────

class AddVideo(BaseModel):
    video_id: str


@router.post("/{session_id}/videos", dependencies=[Depends(require_auth)])
def add_video(session_id: str, req: AddVideo, uid: str = Depends(user_id)):
    _require(session_id, uid)
    row = db.get_video(req.video_id)
    # Yours, or a shared sample. Anything else isn't yours to add.
    if row is None or (row["user_id"] != uid and not is_sample(req.video_id)):
        raise HTTPException(404, "Video not found.")
    db.add_session_video(session_id, req.video_id)
    return {"ok": True}


@router.delete("/{session_id}/videos/{video_id}", dependencies=[Depends(require_auth)])
def remove_video(session_id: str, video_id: str, uid: str = Depends(user_id)):
    _require(session_id, uid)
    db.remove_session_video(session_id, video_id)
    return {"ok": True}


# ── Ask (the playground) ─────────────────────────────────────────────────────

class Ask(BaseModel):
    question: str
    top_k: int | None = None
    video_ids: list[str] | None = None  # checked subset; None = every ready video


def _answer_turn(session_id: str, req: Ask, uid: str, on_stage=None) -> dict:
    """Run one question end to end and persist both sides of the turn.

    Shared by the plain and the streaming endpoint so there is exactly ONE
    implementation of the scope rules, the honest empty-session answers and the
    persistence — the streaming route only differs in how it reports progress.
    Returns the stored assistant message.
    """
    _require(session_id, uid)
    question = req.question.strip()
    if not question:
        raise HTTPException(400, "Empty question.")

    videos = db.session_videos(session_id)
    ready = [v["id"] for v in videos if v["status"] == "indexed"]
    working = [v for v in videos if v["status"] in INFLIGHT_STATUSES]

    # Empty (or not-yet-searchable) session: answer honestly instead of falling
    # through to /api/ask's "no scope = search everything".
    if not ready:
        if working:
            answer = (f"Still indexing {len(working)} video"
                      f"{'s' if len(working) > 1 else ''} in this session — ask again "
                      f"in a moment and I'll search them.")
        elif videos:
            answer = ("None of the videos in this session finished indexing. Check "
                      "their status, or retry the ones that failed.")
        else:
            answer = ("This session has no videos yet. Add a YouTube URL or upload a "
                      "file, and I'll search what's on screen and what's said in it.")
        db.add_message(session_id, "user", question)
        row = db.add_message(session_id, "assistant", answer,
                             citations=[], meta={"empty": True, "llm_used": False})
        db.touch_session(session_id)
        return _message_out(row)

    # Per-video selection: the workspace can UNCHECK videos to drop them from a
    # question without removing or deleting them. None = search all ready videos;
    # a subset narrows the scope; unchecking everything is answered honestly
    # rather than silently falling back to "search all".
    scope = ready
    if req.video_ids is not None:
        chosen = set(req.video_ids)
        scope = [vid for vid in ready if vid in chosen]
        if not scope:
            answer = ("No videos are selected. Check at least one video on the "
                      "right and ask again — unchecking a video only leaves it out "
                      "of the search, it doesn't delete it.")
            db.add_message(session_id, "user", question)
            row = db.add_message(session_id, "assistant", answer,
                                 citations=[], meta={"empty": True, "llm_used": False})
            db.touch_session(session_id)
            return _message_out(row)

    # A missing embedder is a broken install, not a bad question — so it must not
    # be written into the chat as if it were an answer, and it must not surface as
    # a bare 500. 503 + the fix, which is what a fresh clone needs to read.
    try:
        result = rag_search.ask(question, uid, top_k=req.top_k, video_ids=scope,
                                **({"on_stage": on_stage} if on_stage else {}))
    except EmbedUnavailable as exc:
        raise HTTPException(503, str(exc)) from exc
    citations = result.get("citations") or []
    meta = {k: result.get(k) for k in
            ("llm_used", "abstained", "llm_source", "llm_model", "note", "parts")
            if result.get(k) is not None}
    db.add_message(session_id, "user", question)
    row = db.add_message(session_id, "assistant", result.get("answer") or "",
                         citations=citations, meta=meta)
    db.touch_session(session_id)
    return _message_out(row)


@router.post("/{session_id}/ask")
def session_ask(session_id: str, req: Ask, uid: str = Depends(user_id)):
    """One turn, scoped to this session's videos and stored in its chat.

    Each question searches on its own — history is stored, not fed to the model.
    So an answer stays grounded in retrieved moments, and the moments each answer
    cited are exactly what was stored with it.
    """
    return {"message": _answer_turn(session_id, req, uid)}


@router.post("/{session_id}/ask_stream")
def session_ask_stream(session_id: str, req: Ask, uid: str = Depends(user_id)):
    """Same turn, but reports what the server is doing while it does it.

    Server-Sent Events, one JSON object per event:
        {"type":"stage","stage":"searching","detail":"..."}   as each stage BEGINS
        {"type":"done","message":{...}}                        the stored message
        {"type":"error","detail":"..."}                        setup/pipeline failure

    The stages come from real boundaries inside rag.search (on_stage), never from
    a timer — so a label on screen always names what is actually happening, and a
    slow stage visibly stays put instead of a fake progress bar sliding on.

    The pipeline is blocking, so it runs in a worker thread and pushes events
    through a queue that this generator drains. `_require` runs FIRST, on this
    thread, so a bad session id is still a clean 404 rather than an error event.
    """
    _require(session_id, uid)
    events: queue.Queue = queue.Queue()

    def work():
        try:
            msg = _answer_turn(session_id, req, uid,
                               on_stage=lambda stage, detail="": events.put(
                                   {"type": "stage", "stage": stage, "detail": detail}))
            events.put({"type": "done", "message": msg})
        except HTTPException as exc:
            events.put({"type": "error", "detail": str(exc.detail)})
        except Exception as exc:                      # never leave the UI hanging
            events.put({"type": "error", "detail": f"{type(exc).__name__}: {exc}"})
        finally:
            events.put(None)                          # sentinel: close the stream

    threading.Thread(target=work, daemon=True).start()

    def stream():
        while True:
            item = events.get()
            if item is None:
                return
            # SSE frame: "data: <json>" terminated by a BLANK line.
            yield "data: " + json.dumps(item, default=str) + "\n\n"

    return StreamingResponse(stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache",
                                      # nginx/Fly buffer SSE by default, which
                                      # would deliver every stage in one lump at
                                      # the end and defeat the point.
                                      "X-Accel-Buffering": "no"})
