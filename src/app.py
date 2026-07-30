"""MomentSearch — unified API (one service, one port).

Two routers on one FastAPI app (:8000):
  - src/api/videos.py  /api/videos/*  — presigned uploads + registration +
                                        ingest status (Bearer auth)
  - src/api/search.py  public         — / (web UI), /api/ask, /api/config,
                                        local-dev media, /api/health
plus /ui/* — the UI's static stylesheet and scripts (landing.html / demo.html /
signin.html / app.html are rendered by search.py, which injects the page mode).

Heavy processing never happens here — the videos router only schedules Prefect
flow runs; worker.py (separate process, same image) executes the ingest
pipeline. Every durable byte lives in object storage, Qdrant, or Postgres, so
this process is stateless and disposable.

Run:
    uvicorn src.app:app --port 8000
"""
from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from . import config, db
from .api.auth import router as auth_router
from .api.search import UI_DIR, router as search_router
from .api.sessions import router as sessions_router
from .api.videos import router as videos_router
from .rag import vector_store


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.init_schema()
    # Create the Qdrant collection up front (known CLIP dims resolve without
    # loading the model) so a question before the first ingest returns
    # "no moments" instead of a 500. Qdrant being down must not block boot.
    try:
        vector_store.ensure_collection()          # visual (CLIP frames)
        if config.ENABLE_TRANSCRIPT:
            vector_store.ensure_text_collection()  # transcript (bge text)
    except Exception as exc:
        print(f"[startup] Qdrant not ready ({exc!r}) — search degrades to empty results")
    yield


app = FastAPI(title="MomentSearch", version="1.0.0", lifespan=lifespan)
app.include_router(auth_router)
app.include_router(sessions_router)
app.include_router(videos_router)
app.include_router(search_router)

# The UI's stylesheet and scripts (ui/app.css, common.js, demo.js, workspace.js,
# signin.js). The HTML pages themselves are NOT served from here — search.py
# renders them so it can inject the page mode.
# Still zero build step: these are plain files, and StaticFiles handles their
# ETag/Last-Modified revalidation for us.
if UI_DIR.is_dir():
    app.mount("/ui", StaticFiles(directory=UI_DIR), name="ui")
