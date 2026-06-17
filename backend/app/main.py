"""MomentSearch API + minimal static UI.

Endpoints
  GET  /api/health
  GET  /api/config              -> feature flags for the UI (e.g. is an LLM set up)
  GET  /api/videos              -> indexed videos
  DELETE /api/videos/{id}       -> remove a video from the index
  GET  /api/ingest/youtube?url= -> SSE ingestion progress
  POST /api/upload              -> save an uploaded file, returns {video_id, title}
  GET  /api/ingest/upload?video_id=&title= -> SSE ingestion progress
  POST /api/ask                 -> {question, video_id?} -> {answer, citations}
  GET  /api/frame/{path}        -> a frame thumbnail (jpg)
  GET  /api/video/{video_id}    -> the source mp4 (HTTP range supported)
  GET  /                        -> the single-page UI
"""
from __future__ import annotations

import json
import queue
import threading
from pathlib import Path

from fastapi import FastAPI, File, Header, HTTPException, UploadFile
from fastapi.responses import (FileResponse, HTMLResponse, Response,
                               StreamingResponse)
from pydantic import BaseModel

from .config import get_settings
from . import ingest, search, vector_store

app = FastAPI(title="MomentSearch", version="0.1.0")
FRONTEND = Path(__file__).resolve().parents[2] / "frontend" / "index.html"


# ── Models ───────────────────────────────────────────────────────────────────

class AskRequest(BaseModel):
    question: str
    video_id: str | None = None
    top_k: int | None = None


# ── Meta ──────────────────────────────────────────────────────────────────────

@app.get("/api/health")
def health():
    return {"ok": True}


@app.get("/api/config")
def config():
    s = get_settings()
    return {
        "llm_configured": s.llm_configured,
        "llm_provider": s.LLM_PROVIDER,
        "llm_model": s.LLM_MODEL if s.llm_configured else None,
        "frame_strategy": s.FRAME_STRATEGY,
        "top_k": s.TOP_K,
    }


@app.get("/api/videos")
def videos():
    return {"videos": vector_store.list_videos()}


@app.delete("/api/videos/{video_id}")
def delete_video(video_id: str):
    vector_store.delete_video(video_id)
    return {"ok": True, "video_id": video_id}


# ── Ingestion (Server-Sent Events) ─────────────────────────────────────────────

def _sse_run(work) -> StreamingResponse:
    """Run a blocking ingest function in a thread, streaming its progress as SSE.

    `work(emit)` calls emit(stage, detail) as it goes; we forward each event.
    """
    events: "queue.Queue[dict | None]" = queue.Queue()

    def emit(stage: str, detail: dict) -> None:
        events.put({"stage": stage, "detail": detail})

    def runner():
        try:
            meta = work(emit)
            events.put({"stage": "complete", "detail": meta})
        except Exception as e:  # surface to the UI rather than hang
            events.put({"stage": "error", "detail": {"message": str(e)}})
        finally:
            events.put(None)

    threading.Thread(target=runner, daemon=True).start()

    def gen():
        while True:
            evt = events.get()
            if evt is None:
                break
            yield f"data: {json.dumps(evt)}\n\n"

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache",
                                      "X-Accel-Buffering": "no"})


@app.get("/api/ingest/youtube")
def ingest_youtube(url: str):
    return _sse_run(lambda emit: ingest.ingest_youtube(url, progress=emit))


@app.post("/api/upload")
async def upload(file: UploadFile = File(...)):
    s = get_settings()
    s.video_dir.mkdir(parents=True, exist_ok=True)
    video_id = ingest.new_upload_id()
    suffix = Path(file.filename or "video.mp4").suffix or ".mp4"
    dest = s.video_dir / f"{video_id}{suffix}"
    with dest.open("wb") as out:
        while chunk := await file.read(1 << 20):
            out.write(chunk)
    title = Path(file.filename or video_id).stem
    return {"video_id": video_id, "title": title, "path": dest.name}


@app.get("/api/ingest/upload")
def ingest_upload(video_id: str, title: str):
    s = get_settings()
    matches = list(s.video_dir.glob(f"{video_id}.*"))
    if not matches:
        raise HTTPException(404, "Uploaded file not found. Upload it first.")
    path = matches[0]
    return _sse_run(lambda emit: ingest.ingest_video_file(
        path, video_id, title, source="upload", progress=emit))


# ── Ask ─────────────────────────────────────────────────────────────────────

@app.post("/api/ask")
def ask(req: AskRequest):
    if not req.question.strip():
        raise HTTPException(400, "Empty question.")
    return search.ask(req.question.strip(), top_k=req.top_k, video_id=req.video_id)


# ── Media ─────────────────────────────────────────────────────────────────────

@app.get("/api/frame/{path:path}")
def frame(path: str):
    s = get_settings()
    fp = (s.frame_dir / path).resolve()
    if s.frame_dir.resolve() not in fp.parents or not fp.exists():
        raise HTTPException(404, "Frame not found.")
    return FileResponse(fp, media_type="image/jpeg",
                        headers={"Cache-Control": "public, max-age=86400"})


@app.get("/api/video/{video_id}")
def video(video_id: str, range: str | None = Header(default=None)):
    s = get_settings()
    matches = list(s.video_dir.glob(f"{video_id}.*"))
    if not matches:
        raise HTTPException(404, "Video not found.")
    path = matches[0]
    size = path.stat().st_size
    if range is None:
        return FileResponse(path, media_type="video/mp4",
                            headers={"Accept-Ranges": "bytes"})
    try:
        unit, rng = range.split("=", 1)
        assert unit.strip() == "bytes"
        start_s, end_s = rng.split("-", 1)
        start = int(start_s) if start_s else 0
        end = int(end_s) if end_s else size - 1
    except Exception:
        raise HTTPException(416, "Invalid Range header")
    if start >= size or start > end:
        raise HTTPException(416, "Range out of bounds",
                            headers={"Content-Range": f"bytes */{size}"})
    end = min(end, size - 1)
    length = end - start + 1

    def stream():
        with path.open("rb") as fh:
            fh.seek(start)
            remaining = length
            while remaining > 0:
                buf = fh.read(min(1 << 16, remaining))
                if not buf:
                    break
                remaining -= len(buf)
                yield buf

    return StreamingResponse(stream(), status_code=206, media_type="video/mp4",
                             headers={"Content-Range": f"bytes {start}-{end}/{size}",
                                      "Accept-Ranges": "bytes",
                                      "Content-Length": str(length)})


# ── UI ─────────────────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
def index():
    if FRONTEND.exists():
        return FRONTEND.read_text()
    return "<h1>MomentSearch</h1><p>frontend/index.html not found.</p>"
