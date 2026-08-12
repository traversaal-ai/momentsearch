"""Search API (read path) + UI + local-dev media serving.

POST /api/ask is the whole read path: retrieve -> confidence gate -> cited
multimodal answer or honest abstention (src/rag/search.py). Media endpoints
exist only for STORAGE_PROVIDER=local — with a real bucket, thumbnails and
playback stream via presigned URLs and never touch this process.
"""
from __future__ import annotations

import json
import queue
import re
import threading
from pathlib import Path

from fastapi import APIRouter, Depends, Header, HTTPException
from fastapi.responses import (FileResponse, HTMLResponse, RedirectResponse,
                               StreamingResponse)
from pydantic import BaseModel

from .. import config, db, llm, setup_check, storage
from ..providers import status as provider_status
from ..providers.embed.base import EmbedUnavailable
from ..rag import search as rag_search
from ..samples import is_sample
from .videos import require_auth, user_id as user_id_dep

router = APIRouter(tags=["search"])

UI_DIR = Path(__file__).resolve().parents[2] / "ui"
_FRAME_RE = re.compile(r"^\d{6}\.jpg$")


def _uid(value: str | None) -> str:
    """The tenant a frame/media URL belongs to — always the one account.

    Single-user: the `?u=` parameter these URLs still carry is ignored rather
    than trusted, so a hand-edited one can't read outside the account. The
    parameter itself stays in the signatures because the stored citation
    deeplinks and thumbnail URLs already contain it.
    """
    return config.SINGLE_USER_ID


# ── Meta ─────────────────────────────────────────────────────────────────────

@router.get("/api/health")
def health():
    return {"ok": True}


@router.get("/api/config")
def get_config(uid: str = Depends(user_id_dep)):
    cfg, source = rag_search.resolve_llm(uid)
    embeddings = provider_status.active()["embeddings"]
    return {
        "llm_configured": cfg is not None,
        "llm_source": source,   # "user" (their hosted model) | "server" | "none"
        "llm_provider": cfg.provider if cfg else None,
        "llm_model": cfg.model if cfg else None,
        # Which embedders are actually behind retrieval — the two branches are
        # configured independently and can come from different providers.
        "image_embed_provider": embeddings["image"]["provider"],
        "image_embed_model": embeddings["image"]["model"],
        "text_embed_provider": embeddings["text"]["provider"],
        "text_embed_model": embeddings["text"]["model"],
        "frame_strategy": config.FRAME_STRATEGY,
        "top_k": config.TOP_K,
        # Speaker recognition ("who said what") is available only when Gemini is
        # configured; the upload UI uses this to enable/disable its checkbox.
        "diarize_available": bool(config.DIARIZE_ENABLED and config.GEMINI_API_KEY),
        # Config readiness — what this instance is still missing (missing Prefect
        # key, no Qdrant, no LLM, ...). The workspace draws its setup pill/banner
        # from this. One source of truth: src/setup_check.py.
        "setup": setup_check.report(),
        "upload_mode": "presigned" if storage.presign_capable() else "direct",
        "max_upload_mb": config.MAX_UPLOAD_MB,
        # Google Drive import. Both ids are public by design — they're client-side
        # identifiers for Google's Picker, not secrets. `enabled` is false until
        # both are set, and the workspace button then says what's missing rather
        # than opening a picker that can't work. No token of any kind reaches us:
        # the browser fetches the picked file and uploads it like any other file.
        "gdrive": {
            "enabled": bool(config.GDRIVE_CLIENT_ID and config.GDRIVE_API_KEY),
            "client_id": config.GDRIVE_CLIENT_ID,
            "api_key": config.GDRIVE_API_KEY,
        },
    }


@router.get("/api/providers")
def get_providers():
    """Every model provider this build supports, plus what's configured here.

    Drives a provider picker in the settings UI (name, label, whether it needs
    a key, whether its optional SDK is installed) and doubles as the answer to
    "why isn't my key working?" — same data as `python -m src.providers`.
    """
    return provider_status.catalog()


# ── Bring-your-own-model settings (per tenant) ────────────────────────────────
# A user points MomentSearch at THEIR model — any provider in the registry
# (OpenAI, Gemini, Anthropic, OpenRouter, xAI/Grok, Groq, Together, Fireworks,
# Mistral, NVIDIA, Azure) or their own OpenAI-compatible server (vLLM, Ollama,
# LM Studio) via base_url — and every /api/ask for that user answers with it
# instead of the server's LLM.

class LLMSettings(BaseModel):
    provider: str = "openai"     # any name/alias from GET /api/providers
    model: str = ""              # blank = that provider's default model
    base_url: str | None = None  # e.g. "http://my-vllm-host:8000/v1"
    api_key: str | None = None   # empty keeps the previously stored key


def _validate_llm(s: LLMSettings, uid: str) -> LLMSettings:
    """Reject what can't work, and fill in what the provider's preset knows.

    Returns settings with the provider name canonicalized and the model
    resolved, so what gets stored is what will actually run.
    """
    if not llm.is_provider(s.provider):
        raise HTTPException(400, f"Unknown provider '{s.provider}'. Known: "
                                 f"{', '.join(llm.PROVIDERS)} (see /api/providers).")
    url = (s.base_url or "").strip()
    if url and not (url.startswith("http://") or url.startswith("https://")):
        raise HTTPException(400, "base_url must be http(s).")
    # An empty api_key means "keep the stored one", so validate against that.
    stored = db.get_user_llm(uid) or {}
    key = (s.api_key or "").strip() or (stored.get("api_key") or "")
    cfg = llm.resolve(llm.LLMConfig(provider=s.provider, model=(s.model or "").strip(),
                                    base_url=url, api_key=key))
    problem = llm.missing_requirement(cfg)
    if problem:
        raise HTTPException(400, problem)
    # base_url stays as the user gave it (blank = follow the preset, so a
    # provider that moves its endpoint keeps working without a DB edit).
    return LLMSettings(provider=cfg.provider, model=cfg.model,
                       base_url=url or None, api_key=s.api_key)


def _masked(row: dict) -> dict:
    key = row.get("api_key") or ""
    cfg = llm.from_row(row)
    return {"provider": row["provider"], "model": row["model"],
            "base_url": row.get("base_url"),
            "api_key_set": bool(key),
            "api_key_hint": f"…{key[-4:]}" if key else None,
            # What the provider's preset resolves to — the endpoint that will
            # actually be called, and a human-readable provider name.
            "label": cfg.label, "effective_base_url": cfg.base_url or None,
            "updated_at": row.get("updated_at")}


@router.get("/api/llm")
def get_llm(uid: str = Depends(user_id_dep)):
    row = db.get_user_llm(uid)
    _, source = rag_search.resolve_llm(uid)
    return {"configured": row is not None, "active_source": source,
            "settings": _masked(row) if row else None,
            "server_fallback": config.llm_configured()}


@router.put("/api/llm", dependencies=[Depends(require_auth)])
def put_llm(s: LLMSettings, uid: str = Depends(user_id_dep)):
    s = _validate_llm(s, uid)
    row = db.set_user_llm(uid, provider=s.provider, model=s.model.strip(),
                          base_url=(s.base_url or "").strip() or None,
                          api_key=(s.api_key or "").strip())
    return {"ok": True, "settings": _masked(row)}


@router.post("/api/llm/test", dependencies=[Depends(require_auth)])
def test_llm(uid: str = Depends(user_id_dep)):
    """One tiny image through the user's model — proves connectivity AND that
    the model is vision-capable (text-only models fail here, not mid-answer)."""
    cfg, source = rag_search.resolve_llm(uid)
    if cfg is None:
        raise HTTPException(400, "No model configured.")
    try:
        reply = llm.ping(cfg)
    except Exception as e:
        raise HTTPException(502, f"Model call failed: {type(e).__name__}: {e}")
    return {"ok": True, "source": source, "model": cfg.model, "reply": reply[:200]}


@router.delete("/api/llm", dependencies=[Depends(require_auth)])
def delete_llm(uid: str = Depends(user_id_dep)):
    db.delete_user_llm(uid)
    return {"ok": True, "active_source": rag_search.resolve_llm(uid)[1]}


# ── Ask ──────────────────────────────────────────────────────────────────────

class AskRequest(BaseModel):
    question: str
    video_id: str | None = None        # single-video scope (legacy)
    video_ids: list[str] | None = None  # multi-select scope (checked videos)
    top_k: int | None = None


@router.post("/api/ask")
def ask(req: AskRequest, uid: str = Depends(user_id_dep)):
    if not req.question.strip():
        raise HTTPException(400, "Empty question.")
    # Empty list == "nothing selected" -> treat as all (None); avoids a
    # confusing zero-results answer when the user unchecks everything.
    video_ids = req.video_ids or None
    # 503 + the fix, not a bare 500 — see EmbedUnavailable. This is the first
    # thing a fresh clone hits if it skipped `pip install -r requirements.txt`.
    try:
        return rag_search.ask(req.question.strip(), uid,
                              top_k=req.top_k, video_id=req.video_id,
                              video_ids=video_ids)
    except EmbedUnavailable as exc:
        raise HTTPException(503, str(exc)) from exc


@router.post("/api/ask_stream")
def ask_stream(req: AskRequest, uid: str = Depends(user_id_dep)):
    """/api/ask, but it reports what it is doing while it does it.

    Same Server-Sent Events contract as the session-scoped twin in
    src/api/sessions.py:

        {"type":"stage","stage":"searching","detail":"…"}   as each stage BEGINS
        {"type":"done","result":{…}}                        the /api/ask payload
        {"type":"error","detail":"…"}                       setup/pipeline failure

    Why this exists: /demo used plain /api/ask and could only paint a static
    shimmer for the ~6s an answer takes, which reads as a hung page. The stages
    come from real boundaries inside rag.search (on_stage), never a timer.

    The pipeline blocks, so it runs on a worker thread and pushes events through
    a queue this generator drains. Validation happens FIRST, on this thread, so a
    bad request is still a clean 400 rather than an error event.
    """
    question = req.question.strip()
    if not question:
        raise HTTPException(400, "Empty question.")
    video_ids = req.video_ids or None
    events: queue.Queue = queue.Queue()

    def work():
        try:
            result = rag_search.ask(
                question, uid, top_k=req.top_k, video_id=req.video_id,
                video_ids=video_ids,
                on_stage=lambda stage, detail="": events.put(
                    {"type": "stage", "stage": stage, "detail": detail}))
            events.put({"type": "done", "result": result})
        except EmbedUnavailable as exc:
            events.put({"type": "error", "detail": str(exc)})
        except Exception as exc:                       # never leave the UI hanging
            events.put({"type": "error", "detail": f"{type(exc).__name__}: {exc}"})
        finally:
            events.put(None)                           # sentinel: close the stream

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
                                      # Proxies buffer SSE by default, which would
                                      # deliver every stage in one lump at the end.
                                      "X-Accel-Buffering": "no"})


# ── Transcript (full timed transcript for the synced player panel) ───────────

@router.get("/api/transcript/{video_id}")
def transcript(video_id: str, uid: str = Depends(user_id_dep)):
    """The full timed transcript `[{text, t_start, t_end}]` for the synced
    transcript panel — served straight from the durable copy in object storage
    (`{owner}/{video_id}/transcript.json`). GCP-only: 404 when the video has no stored
    transcript (an upload, a caption-less video, or a sample not yet re-seeded)."""
    import json

    row = db.get_video(video_id)
    if row is None:
        raise HTTPException(404, "Video not found.")
    owner = row["user_id"]
    if not is_sample(video_id) and owner != uid:
        raise HTTPException(403, "Not your video.")
    try:
        raw = storage.get_bytes(storage.transcript_key(owner, video_id))
    except Exception:
        raise HTTPException(404, "No transcript stored for this video.")
    try:
        chunks = json.loads(raw)
    except Exception:
        raise HTTPException(500, "Stored transcript is unreadable.")
    return {"video_id": video_id, "owner": owner, "chunks": chunks}


# ── Media (local-dev only; buckets serve these via presigned URLs) ───────────

@router.get("/api/frame/{video_id}/{name}")
def frame(video_id: str, name: str, u: str | None = None):
    if storage.presign_capable():
        raise HTTPException(404, "Thumbnails are served from object storage.")
    if not _FRAME_RE.match(name):
        raise HTTPException(404, "Frame not found.")
    fp = storage.local_path(storage.frame_prefix(_uid(u), video_id) + name)
    if not fp.exists():
        raise HTTPException(404, "Frame not found.")
    return FileResponse(fp, media_type="image/jpeg",
                        headers={"Cache-Control": "public, max-age=86400"})


@router.get("/api/video/{video_id}")
def video(video_id: str, u: str | None = None,
          range: str | None = Header(default=None)):
    if storage.presign_capable():
        raise HTTPException(404, "Playback streams from object storage.")
    uid = _uid(u)
    row = db.get_video(video_id)
    if row is None or row["user_id"] != uid or not row.get("storage_key"):
        raise HTTPException(404, "Video not found.")
    path = storage.local_path(row["storage_key"])
    if not path.exists():
        raise HTTPException(404, "Video file not found.")
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


# ── UI ────────────────────────────────────────────────────────────────────────

def _page(name: str, mode: str = "") -> str:
    """Serve one of the UI's pages, injecting its mode where the page wants it.

    Three pages, each a plain file in ui/ (no build step — see app.py's /ui mount
    for the shared stylesheet and scripts):
      landing.html  /         what MomentSearch is; one door into the workspace
      demo.html     /demo     the shared sample corpus, read-only
      app.html      /app      the workspace: sessions, playground, uploads

    There is no sign-in page: this deployment is single-user (src/api/auth.py).
    """
    page = UI_DIR / name
    if not page.exists():
        return f"<h1>MomentSearch</h1><p>ui/{name} not found.</p>"
    html = page.read_text(encoding="utf-8")
    return html.replace("<!--MS_MODE-->", f'<script>window.MS_MODE="{mode}";</script>')


@router.get("/", response_class=HTMLResponse)
def index():
    return _page("landing.html")


@router.get("/demo", response_class=HTMLResponse)
def demo():
    return _page("demo.html", "sample")


@router.get("/app", response_class=HTMLResponse)
def app_page():
    return _page("app.html")


@router.get("/signin", response_class=HTMLResponse)
def signin():
    """There is no sign-in any more — the workspace IS the front door.

    Redirected rather than deleted: this address is in bookmarks, in the README
    and in old browser history, and landing on the workspace is what someone
    following it wanted anyway. Same reason /get-started still resolves.
    """
    return RedirectResponse("/app", status_code=307)


@router.get("/get-started", response_class=HTMLResponse)
def get_started():
    """Kept: it was the bring-your-own-videos URL. The work lives at /app now,
    so send people there rather than 404 an address that may be shared."""
    return RedirectResponse("/app", status_code=307)
