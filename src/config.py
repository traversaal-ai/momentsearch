"""Central env-driven config — every knob in one place.

Same conventions as the digital-twin-akash service: module-level constants,
provider-neutral STORAGE_* credentials with AWS_* fallbacks, Prefect Cloud
read straight from PREFECT_API_URL / PREFECT_API_KEY by the SDK.

Model choice follows the same provider-neutral idea: pick a provider NAME and
src/providers/registry.py supplies the endpoint, the default model, the vector
dimension and which env var holds the key. `LLM_PROVIDER=gemini` +
`GEMINI_API_KEY` is a complete configuration; so is `IMAGE_EMBED_PROVIDER=jina`
+ `JINA_API_KEY`. Anything you set explicitly always wins over the preset.
"""
from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

from .providers import registry  # pure data — no import cycle

# Load .env from the REPO ROOT by absolute path, not the cwd. This matters for
# ingest: Prefect runs each flow in a subprocess with a stripped environment, so
# without this it would see none of the compose env vars, fall back to every
# config DEFAULT (clip-ViT-B-32 + the `moments` collection + in-process
# embedding), and silently index videos into a collection the app never queries.
# override=False keeps real env vars (compose/Fly secrets) winning where present.
_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(_ROOT / ".env", override=False)

ROOT = _ROOT
DATA = ROOT / "data"  # local-provider storage root (dev only)


def _envbool(name: str, default: bool) -> bool:
    return os.getenv(name, str(default)).strip().lower() in ("1", "true", "yes", "on")


def _first_env(*names: str) -> str:
    """First non-empty of several env vars — lets a provider's conventional key
    name (GEMINI_API_KEY, JINA_API_KEY, ...) work without renaming it."""
    for name in names:
        value = os.getenv(name, "").strip()
        if value:
            return value
    return ""


def _int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except ValueError:
        return default


def _float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except ValueError:
        return default


# --- Database (Neon Postgres) — videos manifest, source of truth ------------
DATABASE_URL = os.getenv("DATABASE_URL", "")

# --- Single user -------------------------------------------------------------
# This deployment is SINGLE-USER. There is no sign-in, no sign-up and no tenant
# negotiation: every request acts as one account, and opening the app IS being
# logged in as it.
#
# SINGLE_USER_ID is the tenant key that tags every bucket key, Postgres row and
# Qdrant point. It stays "default" because that is what the pre-indexed sample
# talks are already tagged with — changing it orphans them until their vectors
# are re-tagged and their `default/<video>/` objects moved. SINGLE_USER_NAME is
# only a label for the UI; nothing keys off it.
#
# The multi-tenant plumbing underneath is untouched — every row is still
# user_id-tagged — so putting a real IdP back in front means resolving a
# per-request user_id again, not reshaping the data.
SINGLE_USER_ID = os.getenv("SINGLE_USER_ID", os.getenv("DEFAULT_USER_ID", "default"))
SINGLE_USER_NAME = os.getenv("SINGLE_USER_NAME", "admin")
DEFAULT_USER_ID = SINGLE_USER_ID   # legacy alias: the only tenant there is

# Kept for `python -m src.providers` and any operator script that still wants a
# server-wide token. NOTE: with sign-in removed the API no longer demands it —
# reaching the port is reaching the account. Don't expose this to the internet
# without a reverse proxy that authenticates.
ADMIN_TOKEN = os.getenv("ADMIN_TOKEN", "")

# --- Object storage (videos + frame thumbnails) ------------------------------
# STORAGE_PROVIDER: local | aws | gcp | gcp_native | flyio
# aws/gcp/flyio share one boto3 S3 client (different endpoints); gcp_native
# uses Google's SDK + service-account JSON; local writes under ./data (dev).
STORAGE_PROVIDER = os.getenv("STORAGE_PROVIDER", "local").strip().lower()
STORAGE_BUCKET = (os.getenv("STORAGE_BUCKET", "")
                  or os.getenv("BUCKET_NAME", "")            # injected by `fly storage create`
                  or os.getenv("GCS_BUCKET_NAME", "")        # gcp_native conventions
                  or os.getenv("GOOGLE_CLOUD_BUCKET_NAME", ""))
STORAGE_ACCESS_KEY_ID = os.getenv("STORAGE_ACCESS_KEY_ID", "").strip() or os.getenv("AWS_ACCESS_KEY_ID", "").strip()
STORAGE_SECRET_ACCESS_KEY = os.getenv("STORAGE_SECRET_ACCESS_KEY", "").strip() or os.getenv("AWS_SECRET_ACCESS_KEY", "").strip()
AWS_REGION = os.getenv("STORAGE_REGION", "").strip() or os.getenv("AWS_REGION", "auto")
_PROVIDER_ENDPOINTS = {
    "aws": None,  # boto3 default
    "flyio": "https://fly.storage.tigris.dev",
    "gcp": "https://storage.googleapis.com",
}
STORAGE_ENDPOINT = os.getenv("AWS_ENDPOINT_URL_S3", "").strip() or _PROVIDER_ENDPOINTS.get(STORAGE_PROVIDER)


def gcs_service_account_info() -> dict:
    """Service-account JSON for STORAGE_PROVIDER=gcp_native, rebuilt from the
    GOOGLE_CLOUD_* env vars (the standard exploded-JSON convention)."""
    key = os.getenv("GOOGLE_CLOUD_PRIVATE_KEY", "").strip()
    # dotenv strips surrounding quotes locally, but `fly secrets import` keeps
    # them literally — strip defensively so the PEM is valid in both places.
    if len(key) >= 2 and key[0] == key[-1] and key[0] in "\"'":
        key = key[1:-1]
    return {
        "type": "service_account",
        "project_id": os.getenv("GOOGLE_CLOUD_PROJECT_ID", ""),
        "private_key_id": os.getenv("GOOGLE_CLOUD_PRIVATE_KEY_ID", ""),
        "private_key": key.replace("\\n", "\n"),  # dotenv keeps \n literal inside quotes
        "client_email": os.getenv("GOOGLE_CLOUD_CLIENT_EMAIL", ""),
        "client_id": os.getenv("GOOGLE_CLOUD_CLIENT_ID", ""),
        "auth_uri": os.getenv("GOOGLE_CLOUD_AUTH_URI", "https://accounts.google.com/o/oauth2/auth"),
        "token_uri": os.getenv("GOOGLE_CLOUD_TOKEN_URI", "https://oauth2.googleapis.com/token"),
        "auth_provider_x509_cert_url": os.getenv(
            "GOOGLE_CLOUD_AUTH_PROVIDER_X509_CERT_URL", "https://www.googleapis.com/oauth2/v1/certs"),
        "client_x509_cert_url": os.getenv("GOOGLE_CLOUD_CLIENT_X509_CERT_URL", ""),
        "universe_domain": os.getenv("GOOGLE_CLOUD_UNIVERSE_DOMAIN", "googleapis.com"),
    }


# Bucket key layout — everything for one video lives under `{user_id}/{video_id}/`
# (tenant isolation at the path level; one prefix delete wipes a whole video, and
# `{user_id}/` wipes a whole user). The keys are built in src/storage.py:
#   {user_id}/{video_id}/source.{ext}       raw uploaded video (presigned PUT target)
#   {user_id}/{video_id}/frames/NNNNNN.jpg  downscaled frame thumbnails (citations)
#   {user_id}/{video_id}/transcript.json    durable timed transcript

# --- Presigned uploads (browser -> bucket, bypassing the API) -----------------
PRESIGN_EXPIRY_S = _int("PRESIGN_EXPIRY_S", 900)          # presigned PUT lifetime
PRESIGN_GET_EXPIRY_S = _int("PRESIGN_GET_EXPIRY_S", 3600)  # thumbnails / playback
MAX_UPLOAD_MB = _int("MAX_UPLOAD_MB", 2048)                # register rejects bigger objects
ALLOWED_UPLOAD_TYPES = ("video/",)                         # content-type must start with

# --- Google Drive import (browser-side; no server credentials, no stored token)
# "Import from Drive" is Google's own Picker popup: the user consents, picks a
# file, and the BROWSER downloads it and PUTs it to the same presigned URL a
# normal upload uses. So the server never sees a Google token, stores no refresh
# token, and needs no new endpoint — the import arrives as an ordinary upload.
# Both values below are public-by-design (they ship to the page); the button
# only appears when both are set. Scope is drive.file — access limited to the
# files the user explicitly picks, which is also the scope that needs no Google
# app verification / security assessment. See README "Import from Google Drive".
GDRIVE_CLIENT_ID = os.getenv("GDRIVE_CLIENT_ID", "")   # OAuth 2.0 Web client id
GDRIVE_API_KEY = os.getenv("GDRIVE_API_KEY", "")       # API key with Picker API enabled

# --- Video ingest lifecycle ---------------------------------------------------
# pending  = registered, waiting in our fair queue (not yet sent to Prefect)
# queued   = the dispatcher picked it and scheduled a Prefect run
# fetching = acquiring the source file; sampling = frames + dedup + thumbnails;
# embedding = CLIP + Qdrant upsert; skipped = duplicate (user_id, source_hash).
VIDEO_STATUSES = ("pending", "queued", "fetching", "sampling", "embedding",
                  "indexed", "skipped", "failed")
# In-flight = occupying execution capacity (scheduled or running).
INFLIGHT_STATUSES = ("queued", "fetching", "sampling", "embedding")

# --- Fair scheduling (WFQ) ----------------------------------------------------
# FIFO (default off): register enqueues to Prefect immediately -> Prefect runs
# them in submitted order, so one user with 50 videos blocks everyone behind
# them. Fair dispatch (WFQ, on): videos wait `pending` in Postgres and a
# dispatcher admits them round-robin ACROSS users, keeping only
# DISPATCH_MAX_INFLIGHT running at once — so the waiting line is fairly ordered
# in OUR DB, not FIFO inside Prefect. No user can starve the others.
ENABLE_FAIR_DISPATCH = _envbool("ENABLE_FAIR_DISPATCH", True)
# Max videos executing at once. Set to your total capacity:
# (worker machines) x WORKER_CONCURRENCY — anything above that would just pile
# up FIFO inside Prefect and defeat the fairness.
DISPATCH_MAX_INFLIGHT = _int("DISPATCH_MAX_INFLIGHT", _int("WORKER_CONCURRENCY", 2))
DISPATCH_INTERVAL_S = _float("DISPATCH_INTERVAL_S", 3.0)  # how often the dispatcher tops up

# --- Frame sampling (the biggest scaling lever) --------------------------------
# interval: one frame every FRAME_INTERVAL_SEC (widened to respect MAX_FRAMES).
# scene:    one frame per detected cut (ffmpeg scene filter).
FRAME_STRATEGY = os.getenv("FRAME_STRATEGY", "interval").strip().lower()
FRAME_INTERVAL_SEC = _float("FRAME_INTERVAL_SEC", 2.0)
SCENE_THRESHOLD = _float("SCENE_THRESHOLD", 0.4)
MAX_FRAMES = _int("MAX_FRAMES", 400)
THUMB_WIDTH = _int("THUMB_WIDTH", 480)   # frames are downscaled in the ffmpeg pass
THUMB_QUALITY = _int("THUMB_QUALITY", 3)  # ffmpeg -q:v (2 best .. 31 worst)

# Perceptual-hash dedup — drop visually-identical neighbours BEFORE embedding.
DEDUP_ENABLED = _envbool("DEDUP_ENABLED", True)
DEDUP_MAX_DISTANCE = _int("DEDUP_MAX_DISTANCE", 4)  # Hamming distance on 64-bit dHash

# --- Visual embeddings: the FRAME branch ----------------------------------------
# One model encodes frames AND text queries into a shared space, so a question
# can match what is *seen* on screen. That shared space is why the provider must
# be a joint image+text model (registry.IMAGE_EMBED_PRESETS):
#   clip   (default) local sentence-transformers CLIP — free, offline, CPU-fine
#   jina             jina-clip-v2 API      — multilingual, Matryoshka dims
#   cohere           embed-v4.0 API        — strong on text-heavy frames
#   voyage           voyage-multimodal API — single backbone, less modality bias
#   gemini           gemini-embedding-2    — one unified space for everything
# CLIP_* names still work as aliases for the clip provider, so existing .env
# files keep running untouched.
IMAGE_EMBED_PROVIDER = registry.image_embed_key(
    os.getenv("IMAGE_EMBED_PROVIDER", "").strip() or "clip")
_IMG = registry.IMAGE_EMBED_PRESETS[IMAGE_EMBED_PROVIDER]
CLIP_MODEL = os.getenv("CLIP_MODEL", "clip-ViT-L-14").strip()   # legacy alias
IMAGE_EMBED_MODEL = (os.getenv("IMAGE_EMBED_MODEL", "").strip()
                     or (CLIP_MODEL if IMAGE_EMBED_PROVIDER == "clip"
                         else _IMG.default_model))
# Vector dimension. 0 = auto: known models resolve from the registry table (so
# the API can create the Qdrant collection at boot WITHOUT loading a model);
# unknown LOCAL models get measured by loading. Hosted providers can't be
# measured — set the dim explicitly for a model the table doesn't list.
CLIP_DIM = _int("CLIP_DIM", 0)                                  # legacy alias
IMAGE_EMBED_DIM = _int("IMAGE_EMBED_DIM",
                       CLIP_DIM or registry.preset_dim(_IMG, IMAGE_EMBED_MODEL))
IMAGE_EMBED_API_KEY = _first_env("IMAGE_EMBED_API_KEY", *_IMG.key_envs)
IMAGE_EMBED_BASE_URL = os.getenv("IMAGE_EMBED_BASE_URL", "").strip()
CLIP_BATCH = _int("CLIP_BATCH", 128)   # frames per embed call (inner batch is 32)
# Hosted APIs cap inputs per request far below CLIP's local batch.
IMAGE_EMBED_BATCH = _int("IMAGE_EMBED_BATCH",
                         CLIP_BATCH if _IMG.local else 32)
# In-flight requests for providers that embed ONE image per call (Gemini). Their
# per-image latency is what dominates a video's ingest time, so this is the knob
# that matters there; raise it if the provider tolerates it, lower it on 429s.
IMAGE_EMBED_CONCURRENCY = _int("IMAGE_EMBED_CONCURRENCY", 8)
# Inference-service URL ("embedding is a URL"). Set -> api/worker send batches
# to the warm clip_service.py container instead of loading the model in-process
# (which costs each Prefect run subprocess a fresh ~15-30s torch load). Unset
# -> in-process embedding (simple mode, no extra service). Point it at a GPU
# machine later — nothing else changes. Ignored for hosted providers: an API
# has no weights to keep warm, so it is called directly.
# An explicit EMBED/CLIP_SERVICE_URL wins (docker-compose sets http://clip:8001;
# the Fly release_command sets it EMPTY to force in-process seeding — a
# present-but-empty value is a deliberate choice, kept distinct from unset). On
# Fly with neither set, derive the clip machine's internal address from Fly's own
# FLY_APP_NAME, so renaming the app touches only the `app =` line in fly.toml and
# nothing hardcodes the name. Unset off Fly -> in-process embedding.
_svc = os.environ.get("EMBED_SERVICE_URL")
if _svc is None:
    _svc = os.environ.get("CLIP_SERVICE_URL")
if _svc is not None:
    EMBED_SERVICE_URL = _svc.strip().rstrip("/")
elif os.environ.get("FLY_APP_NAME"):
    EMBED_SERVICE_URL = f"http://clip.process.{os.environ['FLY_APP_NAME']}.internal:8001"
else:
    EMBED_SERVICE_URL = ""
CLIP_SERVICE_URL = EMBED_SERVICE_URL   # legacy alias
# Optional shared secret for the embedding service. When set, the clip service
# requires `Authorization: Bearer <token>` on its /embed routes and api/worker
# send it. Needed only when the clip service is reachable over a NON-private
# network (a GPU host on another provider); unset = open, which is correct for
# Fly's private *.internal networking and docker-compose. Pair it with HTTPS —
# the token authenticates but does not encrypt. Same value on the app and clip.
EMBED_SERVICE_TOKEN = os.getenv("EMBED_SERVICE_TOKEN", "").strip()
# Stamped on every Qdrant point so a re-embed can find stale vectors. Keeps the
# historical "<model>-v1" form for CLIP so existing indexes aren't invalidated.
EMBED_VERSION = os.getenv(
    "EMBED_VERSION",
    f"{IMAGE_EMBED_MODEL}-v1" if IMAGE_EMBED_PROVIDER == "clip"
    else f"{IMAGE_EMBED_PROVIDER}-{IMAGE_EMBED_MODEL}-v1")

# --- Multimodal: transcript (text) branch (Path 1) -----------------------------
# The visual branch is CLIP frames (above). This adds a SECOND branch: YouTube
# captions, chunked by time, embedded with a small semantic text model (bge via
# fastembed — CPU, free), in a separate Qdrant collection. At query time both
# branches run and fuse by RANK (RRF) — CLIP scores (~0.3) and text scores
# (~0.7) live on different scales, so raw-score comparison is meaningless.
# The transcript branch: YouTube uses captions; uploads use Whisper ASR from
# their own audio (see ASR_* below and src/ingest/asr.py). A caption-less
# YouTube video just indexes visually — never fatal.
ENABLE_TRANSCRIPT = _envbool("ENABLE_TRANSCRIPT", True)
TEXT_COLLECTION = os.getenv("TEXT_COLLECTION", "moments_text_openai")
# Transcript-branch embedding PROVIDER (registry.TEXT_EMBED_PRESETS) — the
# provider name alone picks the model, the dimension and the key env var:
#   openai (default)    -> OpenAI text-embedding-3-small (dim 1536). Falls back to
#                          LLM_API_KEY so ONE OpenAI key powers the answer AND the
#                          embeddings. Also any OpenAI-compatible embeddings server
#                          (vLLM/TEI/Together via TEXT_EMBED_BASE_URL).
#   fastembed           -> bge via fastembed: CPU, free, NO API key — set this to
#                          run the transcript branch keyless. Dim 384.
#   gemini              -> gemini-embedding-2, the same unified space the visual
#                          branch can use. Dim 1536.
#   cohere / voyage / jina -> hosted text embeddings, dims 1536 / 1024 / 1024.
# The model & dim MUST match between indexing and querying, so switching provider
# means RE-INDEXING the transcript collection (its vector dim changes). The two
# branches fuse by RANK (RRF), so this model is fully independent of the visual one.
TEXT_EMBED_PROVIDER = registry.text_embed_key(
    os.getenv("TEXT_EMBED_PROVIDER", "").strip() or "openai")
_TXT = registry.TEXT_EMBED_PRESETS[TEXT_EMBED_PROVIDER]
TEXT_EMBED_MODEL = os.getenv("TEXT_EMBED_MODEL", "").strip() or _TXT.default_model
TEXT_EMBED_DIM = _int("TEXT_EMBED_DIM", registry.preset_dim(_TXT, TEXT_EMBED_MODEL))
TEXT_EMBED_API_KEY = (_first_env("TEXT_EMBED_API_KEY", *_TXT.key_envs)
                      # historical convenience: one OpenAI key for both jobs
                      or (_first_env("LLM_API_KEY")
                          if TEXT_EMBED_PROVIDER == "openai" else ""))
TEXT_EMBED_BASE_URL = os.getenv("TEXT_EMBED_BASE_URL", "").strip()
TEXT_EMBED_BATCH = _int("TEXT_EMBED_BATCH", 64)
TEXT_EMBED_VERSION = os.getenv(
    "TEXT_EMBED_VERSION",
    f"{TEXT_EMBED_MODEL}-v1" if TEXT_EMBED_PROVIDER == "fastembed"
    else f"{TEXT_EMBED_PROVIDER}-{TEXT_EMBED_MODEL}-v1")
# Transcript chunking: group caption cues into ~CHUNK_SECONDS windows so a chunk
# is a coherent spoken passage with a real t_start/t_end, not one tiny cue.
TRANSCRIPT_CHUNK_SECONDS = _float("TRANSCRIPT_CHUNK_SECONDS", 20.0)
TRANSCRIPT_LANGS = [c.strip() for c in
                    os.getenv("TRANSCRIPT_LANGS", "en,en-US,en-GB").split(",") if c.strip()]

# --- Speech-to-text (ASR) for UPLOADS ------------------------------------------
# YouTube hands us captions; uploaded files don't, so their transcript branch is
# produced by ASR from the file's OWN audio (src/ingest/asr.py). The cues come
# out in the same [{text,t_start,t_end}] shape as captions, so chunking, the GCP
# store, text embedding, retrieval and the synced transcript panel treat uploads
# and YouTube identically — an upload gets "said" moments too.
#   openai (default) -> whisper-1 via the OpenAI API. verbose_json gives per-
#                       segment timestamps. Reuses LLM_API_KEY, so ONE OpenAI key
#                       powers the answer, the embeddings AND transcription. No
#                       GPU, no model download; audio over the API's 25MB/request
#                       limit is auto-split into time windows (timestamps offset
#                       back to absolute time). ASR_MODEL can be gpt-4o-transcribe.
# Set ENABLE_ASR=0 (or ASR_PROVIDER="") to leave uploads visual-only.
ENABLE_ASR = _envbool("ENABLE_ASR", True)
ASR_PROVIDER = os.getenv("ASR_PROVIDER", "openai").strip().lower()
ASR_MODEL = os.getenv("ASR_MODEL", "").strip() or "whisper-1"
# One OpenAI key for everything: ASR_API_KEY -> OPENAI_API_KEY -> LLM_API_KEY.
ASR_API_KEY = _first_env("ASR_API_KEY", "OPENAI_API_KEY") or _first_env("LLM_API_KEY")
ASR_BASE_URL = os.getenv("ASR_BASE_URL", "").strip()   # OpenAI-compatible ASR server
ASR_LANGUAGE = os.getenv("ASR_LANGUAGE", "").strip()   # "" = autodetect; e.g. "en"

# --- Fusion (multimodal retrieval) ---------------------------------------------
# RRF: rank-based fusion across branches (score-agnostic). rrf = 1/(K + rank).
RRF_K = _int("RRF_K", 60)
# Hits from either branch within this many seconds are the SAME moment.
FUSION_WINDOW_S = _float("FUSION_WINDOW_S", 15.0)
# When a window has BOTH a frame and a transcript hit, multiply its score —
# two independent modalities agreeing is the strongest relevance signal.
CROSS_MODAL_BOOST = _float("CROSS_MODAL_BOOST", 1.5)
# Per-branch candidates fetched before fusion.
BRANCH_TOP_K = _int("BRANCH_TOP_K", 20)

# --- Reranker (cross-encoder) — the fix for RRF's rank-blindness ----------------
# RRF orders by branch RANK and discards how relevant a hit actually is, so a
# spurious top-1 in one branch can outrank the real answer sitting one rank lower
# in another. A cross-encoder RE-READS each (question, transcript) pair and
# returns a true relevance score, which search.py blends with the RRF standing to
# reorder the text-bearing moments. TEXT ONLY for now: frame-only moments have no
# transcript to judge, so they keep their RRF standing untouched (captioning
# frames with a VLM would let the same reranker cover them too — a follow-up).
#   fastembed (default) -> local ONNX cross-encoder, NO API key, CPU. Reuses the
#                          fastembed dep already installed for text embeddings.
#   cohere              -> Cohere Rerank API (RERANK_API_KEY / COHERE_API_KEY).
# ON by default. The first query after boot downloads a ~90MB model (then warm);
# set ENABLE_RERANK=false to turn it off and fall back to plain RRF.
ENABLE_RERANK = _envbool("ENABLE_RERANK", True)
RERANK_PROVIDER = os.getenv("RERANK_PROVIDER", "fastembed").strip().lower()
_RERANK_DEFAULT_MODEL = {"fastembed": "Xenova/ms-marco-MiniLM-L-6-v2",
                         "cohere": "rerank-english-v3.0"}.get(RERANK_PROVIDER, "")
RERANK_MODEL = os.getenv("RERANK_MODEL", "").strip() or _RERANK_DEFAULT_MODEL
RERANK_TOP_K = _int("RERANK_TOP_K", 30)        # top text candidates to re-judge
RERANK_WEIGHT = _float("RERANK_WEIGHT", 0.7)   # blend weight: rerank vs norm. RRF
RERANK_API_KEY = _first_env("RERANK_API_KEY", "COHERE_API_KEY")
RERANK_BASE_URL = os.getenv("RERANK_BASE_URL", "").strip()

# --- Speaker diarization — "who said what" (Gemini) -------------------------------
# Captions/ASR give the WHEN + WHAT; Gemini gives the WHO. It's OPT-IN PER VIDEO
# (a checkbox at upload). Gemini WATCHES the video (YouTube URL directly; an upload
# is pushed through the Gemini Files API) and returns a compact speaker-change
# INDEX — not a re-transcript — which is aligned onto the verbatim cues, so each
# transcript chunk (and each answer moment) carries the name of who said it, and
# the answer can render a "Who said what" table.
#
# Video understanding is Gemini-ONLY, so this ALWAYS uses GEMINI_API_KEY regardless
# of LLM_PROVIDER. If a video is flagged for diarization but the key is missing the
# API rejects it up front ("Gemini key is missing"); DIARIZE_ENABLED=false, no key,
# or any failure at ingest just leaves the transcript unlabeled (never fatal).
GEMINI_API_KEY = _first_env("GEMINI_API_KEY", "GOOGLE_API_KEY")
DIARIZE_ENABLED = _envbool("DIARIZE_ENABLED", True)   # master switch; the per-video flag still gates each run
DIARIZE_MODEL = os.getenv("DIARIZE_MODEL", "gemini-2.5-flash").strip()
DIARIZE_WINDOW_S = _int("DIARIZE_WINDOW_S", 600)      # video seconds per Gemini call
DIARIZE_MAX_WORKERS = _int("DIARIZE_MAX_WORKERS", 6)  # windows diarized in parallel

# --- YouTube download hardening ---------------------------------------------------
# YouTube increasingly answers yt-dlp's default web client with "Sign in to
# confirm you're not a bot". Mitigations, in order of reliability:
#   YT_COOKIES_FILE  path to a Netscape cookies.txt exported from a logged-in
#                    browser (yt-dlp wiki: "Exporting YouTube cookies").
#                    In docker-compose, drop it at ./data/cookies.txt and set
#                    YT_COOKIES_FILE=/app/data/cookies.txt
#   YT_PROXY_URL     route YouTube traffic through a (residential) proxy,
#                    e.g. http://user:pass@host:port
#   YT_RETRY_CLIENTS on a bot-check error, automatically retry once with these
#                    alternate YouTube player clients (comma-separated).
# Cookies make yt-dlp an authenticated client — the one fix that works from
# ANY IP (home OR datacenter). Two ways to supply them, so the same code works
# local and deployed:
#   YT_COOKIES_FILE  path to a mounted cookies.txt   (easy locally)
#   YT_COOKIES_B64   base64 of cookies.txt as a secret (Fly/cloud: no file mount
#                    needed — the worker writes it to a temp file at runtime)
YT_COOKIES_FILE = os.getenv("YT_COOKIES_FILE", "").strip()
YT_COOKIES_B64 = os.getenv("YT_COOKIES_B64", "").strip()
YT_PROXY_URL = os.getenv("YT_PROXY_URL", "").strip()
# Player clients. Default EMPTY = let yt-dlp pick (best, once a JS runtime is
# present — see below). Forcing tv/android used to help pre-JS-runtime, but now
# those clients hand back media URLs that 403 on download, so we only fall back
# to them if the default attempt fails outright.
YT_PLAYER_CLIENTS = [c.strip() for c in
                     os.getenv("YT_PLAYER_CLIENTS", "").split(",") if c.strip()]
YT_FALLBACK_CLIENTS = [c.strip() for c in
                       os.getenv("YT_FALLBACK_CLIENTS", "tv,android,ios").split(",") if c.strip()]
# yt-dlp 2025+ needs a JavaScript runtime to compute YouTube signatures, plus
# its EJS challenge-solver component — WITHOUT these every video fails with
# "This video is not available" / "Requested format is not available". The
# Dockerfile installs Node; these tell yt-dlp to use it + fetch the solver.
# (For bare-process dev, install node or deno yourself.)
YT_JS_RUNTIMES = [c.strip() for c in
                  os.getenv("YT_JS_RUNTIMES", "node").split(",") if c.strip()]
YT_REMOTE_COMPONENTS = [c.strip() for c in
                        os.getenv("YT_REMOTE_COMPONENTS", "ejs:github").split(",") if c.strip()]

# --- Sample corpus ------------------------------------------------------------
# On boot the worker auto-ingests the "Deep Dive into LLMs" sample talk
# (src/samples.py) if they aren't indexed yet — a fresh clone is queryable on
# the / page without running anything by hand. Set false to skip.
SEED_SAMPLE_VIDEOS = _envbool("SEED_SAMPLE_VIDEOS", True)
# Deploy sanity check (src/preflight.py): on a DEPLOY (FLY_APP_NAME present, or
# DEPLOY_ENV set) it warns when a LOCAL setting is present — STORAGE_PROVIDER=local,
# a compose-only Qdrant/Postgres host, COMPOSE_PROFILES — which work locally but
# break in production. false = warn loudly and keep running; true = refuse to
# start / abort the deploy. No-op off a deploy, so local dev never sees it.
STRICT_DEPLOY_CHECK = _envbool("STRICT_DEPLOY_CHECK", False)
# Best-effort by default: if seeding can't finish (e.g. a fresh clone with no
# YouTube cookies), the seed exits 0 and the deploy proceeds — the app goes live
# and /demo just stays empty until the samples get indexed, instead of the whole
# deploy aborting. Set true to restore the hard gate (an incomplete seed fails
# the deploy / docker-compose start, guaranteeing users never see a half-indexed
# corpus). Ignored when SEED_SAMPLE_VIDEOS=false (nothing to seed).
SEED_STRICT = _envbool("SEED_STRICT", False)

# --- Work orchestration (Prefect Cloud) ----------------------------------------
# The SDK reads PREFECT_API_URL / PREFECT_API_KEY from the environment directly.
# WORKER_CONCURRENCY is read by worker.py; retries live on the flow's tasks.

# --- Qdrant ----------------------------------------------------------------------
# One shared multi-tenant collection: every point carries user_id (tenant payload
# index) and every search/upsert/delete is user_id-filtered.
QDRANT_URL = os.getenv("QDRANT_URL", "").strip()
QDRANT_API_KEY = os.getenv("QDRANT_API_KEY", "").strip() or os.getenv("QDRANT_TOKEN", "").strip()
QDRANT_LOCAL_PATH = os.getenv("QDRANT_LOCAL_PATH", str(DATA / "qdrant"))
# Frame (image) collection — pairs with TEXT_COLLECTION. IMAGE_COLLECTION is the
# current name; QDRANT_COLLECTION is the legacy one from when frames were the only
# collection. Both env vars are honored (IMAGE_COLLECTION wins), so existing
# deploys keep working. The VALUE points at your vectors — rename the var freely,
# never change the value of a live index.
IMAGE_COLLECTION = _first_env("IMAGE_COLLECTION", "QDRANT_COLLECTION") or "moments_l14"
QDRANT_COLLECTION = IMAGE_COLLECTION   # legacy alias (constant + env both still work)
# Low-RAM profile: original vectors on disk, int8-quantized copies pinned in
# RAM (~4x smaller), HNSW graph on disk; queries rescore against the originals.
# Frames balloon vector counts fast, so these default ON.
QDRANT_QUANTIZATION = _envbool("QDRANT_QUANTIZATION", True)
QDRANT_ON_DISK = _envbool("QDRANT_ON_DISK", True)
QDRANT_HNSW_ON_DISK = _envbool("QDRANT_HNSW_ON_DISK", True)

# --- Retrieval / faithfulness ------------------------------------------------------
TOP_K = _int("TOP_K", 6)                 # frames fed to the multimodal LLM (3-8)
KNN_K = _int("KNN_K", 24)                # candidates fetched before trimming to TOP_K
# Gate 1: abstain WITHOUT calling the LLM if BOTH branches' best raw score is
# below their threshold. Fusion scores are RRF (tiny), so the gate uses each
# branch's own raw cosine. CLIP text->image cosines run low (~0.2-0.35); bge
# text-text cosines run higher (~0.5-0.7 for real matches). 0 disables.
# Defaults come from the chosen provider's preset because cosine scale is
# model-specific — a threshold tuned for CLIP would make another embedder
# abstain on perfectly good matches. Providers we haven't calibrated default to
# 0 (gate off, never a wrong abstention); measure yours with benchmark/score.py
# and set these explicitly.
CONFIDENCE_THRESHOLD = _float("CONFIDENCE_THRESHOLD", _IMG.threshold)       # visual
TEXT_CONFIDENCE_THRESHOLD = _float("TEXT_CONFIDENCE_THRESHOLD", _TXT.threshold)  # transcript
# Raw visual score at which a frame counts as a STRONG match. The reranker weights
# a frame-only moment between CONFIDENCE_THRESHOLD (weak, ~0) and this (strong, 1),
# so a talking-head frame that barely cleared the gate can't outrank a clearly-
# relevant transcript moment, while a real visual answer (a slide/diagram) still
# wins. Only affects ranking when the reranker runs; pure-visual videos unchanged.
VISUAL_STRONG = _float("VISUAL_STRONG", 0.45)

# --- Multimodal LLM (answer synthesis only — retrieval works without it) -----------
# LLM_PROVIDER is a name from registry.LLM_PRESETS — openai, gemini, anthropic,
# openrouter, xai (grok), groq, together, fireworks, mistral, nvidia,
# azure_openai, ollama, lmstudio, vllm, custom — plus aliases ("grok", "claude",
# "google", "azure"). The preset supplies the endpoint and a default model, and
# the key is read from that provider's conventional env var (GEMINI_API_KEY,
# OPENROUTER_API_KEY, XAI_API_KEY, ...) when LLM_API_KEY is unset. Set
# LLM_BASE_URL to point any OpenAI-shaped provider at your own server.
LLM_PROVIDER = registry.llm_provider_key(os.getenv("LLM_PROVIDER", "").strip() or "openai")
_LLM = registry.llm_preset(LLM_PROVIDER)
LLM_API_KEY = _first_env("LLM_API_KEY", *_LLM.key_envs)
LLM_BASE_URL = os.getenv("LLM_BASE_URL", "").strip()   # raw: preset fills it later
LLM_MODEL = os.getenv("LLM_MODEL", "").strip() or _LLM.default_model
LLM_MAX_TOKENS = _int("LLM_MAX_TOKENS", 1024)
LLM_IMAGE_MAX_PX = _int("LLM_IMAGE_MAX_PX", 512)  # frames are downscaled again before the LLM


def llm_configured() -> bool:
    """Is there a server-wide answer model at all? (No = retrieval-only mode,
    which is a supported way to run MomentSearch, not an error.)

    Deliberately does NOT count a preset's built-in endpoint: every hosted
    preset ships a base_url, so counting it would claim every provider is
    configured and turn each /api/ask into a 401 instead of the retrieval-only
    fallback. Self-hosted presets (Ollama/LM Studio/vLLM) need no key, so
    naming one IS the configuration.
    """
    if LLM_API_KEY or LLM_BASE_URL:
        return True
    return not _LLM.requires_key and bool(_LLM.base_url)
