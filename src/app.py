"""MomentSearch — unified API (one service, one port).

Two routers on one FastAPI app (:8000):
  - src/api/videos.py  /api/videos/*  — presigned uploads + registration +
                                        ingest status
  - src/api/search.py  public         — / (web UI), /api/ask, /api/config,
                                        local-dev media, /api/health
plus /ui/* — the UI's static stylesheet and scripts (landing.html / demo.html /
app.html are rendered by search.py, which injects the page mode).

Single-user: there is no sign-in. Every request acts as config.SINGLE_USER_ID
(src/api/auth.py explains what that means for anyone who can reach the port).

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
    from . import preflight
    preflight.check("api")   # warn (or, if STRICT_DEPLOY_CHECK, abort) on local settings
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
    # The clear "it's up" signal. The API starts only AFTER the seed gate finishes
    # (docker-compose depends_on), so this line is the moment the app is actually
    # reachable — the noisy build/seed logs above are done.
    bar = "=" * 64
    print(f"\n{bar}\n  MomentSearch is UP  ->  open  http://localhost:8000\n{bar}\n",
          flush=True)
    yield


app = FastAPI(title="MomentSearch", version="1.0.0", lifespan=lifespan)
app.include_router(auth_router)
app.include_router(sessions_router)
app.include_router(videos_router)
app.include_router(search_router)

# The UI's stylesheet, scripts and logo assets (ui/app.css, common.js, demo.js,
# workspace.js, assets/*.png). The HTML pages themselves are NOT served from
# here — search.py renders them so it can inject the page mode.
# Still zero build step: these are plain files, and StaticFiles handles their
# ETag/Last-Modified revalidation for us.
if UI_DIR.is_dir():
    app.mount("/ui", StaticFiles(directory=UI_DIR), name="ui")


@app.middleware("http")
async def revalidate_ui(request, call_next):
    """Never let a browser serve /ui from cache without asking us first.

    There is no build step, so common.js keeps its name forever — and with only
    ETag/Last-Modified, Chrome is free to reuse a stale copy heuristically. That
    is how you get a page whose HTML is new and whose script is old: the page
    calls a helper the cached script has never heard of and the whole thing dies
    on a ReferenceError. `no-cache` still caches; it just requires the
    revalidation that turns into a cheap 304."""
    resp = await call_next(request)
    if request.url.path.startswith("/ui/"):
        resp.headers["Cache-Control"] = "no-cache"
    return resp
