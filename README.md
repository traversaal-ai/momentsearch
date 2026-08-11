# MomentSearch

**Ask questions about your videos and get answers grounded in the exact moments — by what's _seen_ on screen, and what's _said_ in the transcript (YouTube captions, or Whisper on your own uploads).**

🌐 **Live app:** [momentsearch.fly.dev](https://momentsearch.fly.dev/)

MomentSearch is an open-source, production-shaped stack for **visual** video
search and RAG. You upload videos (or paste YouTube URLs); background workers
sample keyframes, dedup them, embed them and index them in
[Qdrant](https://qdrant.tech). Ask a question and it retrieves the most
relevant moments and (optionally) has **your own vision LLM** read those
frames and write a cited answer — or honestly abstain when the evidence isn't
there. Every model in that sentence is **pluggable by name** — the answer LLM
and *both* embedding branches — so you can run it fully local and keyless, fully
hosted, or any mix.

> **Visual-first, multimodal.** The core is *visual* — CLIP over
> sampled frames, so it works on silent footage, screen recordings, sports,
> b-roll, slides, demos: anything you can *see*. On top of that it adds a
> **transcript** branch and fuses the two, so "find where they *talk about* X"
> works too — **YouTube** uses captions and **uploads** are transcribed by
> **Whisper ASR** from their own audio. Only a caption-less YouTube video with
> no usable audio stays visual-only.

- 🎥 **Presigned uploads** — the browser PUTs straight to object storage; gigabytes never flow through the API
- ⚙️ **Queue + stateless workers** — the API answers `202` instantly; Prefect-orchestrated workers do the heavy lifting
- 🔍 **Visual retrieval** — CLIP embeddings, runs locally, no API key needed for the visual branch
- 🔑 **Single-user, no sign-in** — opening the app *is* being logged in as the one account (`admin`). **No authentication exists**: whoever reaches the port owns it, so bind it to localhost or put an authenticating proxy in front. The data model underneath is still fully `user_id`-tagged (bucket keys, Postgres rows, Qdrant points), so restoring real multi-user auth means resolving a per-request user id again — not reshaping data
- 🛡️ **Confidence gate** — below-threshold retrievals abstain *before* the LLM is ever called
- 💬 **Cited answers** — bring your own vision LLM: **Gemini, OpenAI, Claude, Grok, OpenRouter, Groq, Together, Mistral, NVIDIA, Azure, Ollama/vLLM** — a provider name plus a key is the whole configuration ([Pluggable models](#pluggable-models-llms-and-embeddings))
- 🔌 **Swappable embeddings, both modalities** — frames via local **CLIP** (default `clip-ViT-L-14`, free) or hosted **Jina CLIP v2 / Cohere Embed v4 / Voyage multimodal / Gemini**; transcripts via **OpenAI `text-embedding-3-small`** (default) or **bge/fastembed (local, keyless) / Gemini / Cohere / Voyage / Jina**. Mix them — the branches fuse by rank, not score
- 🩺 **`python -m src.providers`** — one command tells you what your `.env` resolved to, which key it used, and what's missing
- 🏠 **Bring your own model** — attach a model *you* host (vLLM, Ollama, any OpenAI-compatible endpoint) via `PUT /api/llm` and answers run on it, overriding the server default
- 🧩 **Multimodal fusion** — a transcript branch (YouTube captions or Whisper on uploads) runs alongside the visual one and a **rank-based scoring module** (RRF + time-windows + cross-modal boost, then a local cross-encoder **reranker** on by default) fuses them; "find where they *talk about* X" works even when the screen doesn't show it
- 🔓 **Apache 2.0**

## Architecture

The design rule: **stateful = rented managed service, stateless = this repo's
code.** Every API box and
worker is disposable; durable state lives in object storage, Qdrant and
Postgres — "nothing on local."

Two paths that scale in opposite directions and never share a request — the
**write path** (slow, background: the API answers `202` instantly and workers do
the work) and the **read path** (fast: retrieval is ~ms, the LLM call dominates
cost):

```mermaid
flowchart LR
  user(["Browser / UI"])

  subgraph repo["MomentSearch — one Docker image, stateless (this repo)"]
    direction TB
    api["API<br/>presign · register · /ask · UI"]
    disp["WFQ dispatcher<br/>fair round-robin across users"]
    worker["Worker(s)<br/>fetch · sample · dedup · embed · transcript"]
    clip["CLIP service<br/>one warm model (CPU → GPU)"]
  end

  subgraph managed["Managed services — stateful (rented)"]
    direction TB
    obj[("Object storage<br/>S3 / GCS / Tigris")]
    pg[("Neon Postgres<br/>manifest · status · hashes")]
    prefect[("Prefect Cloud<br/>queue · retries · dashboard")]
    qdrant[("Qdrant Cloud<br/>moments_l14 + moments_text_openai")]
    vlm[("Vision LLM<br/>OpenAI · vLLM · Anthropic")]
  end

  %% write path (green)
  user -->|"① presign"| api
  user -->|"② PUT bytes"| obj
  user -->|"③ register"| api
  api -->|"pending row"| pg
  api -->|"enqueue"| disp
  disp -->|"admit ≤ MAX_INFLIGHT"| prefect
  prefect -->|"run"| worker
  worker -->|"download / thumbs"| obj
  worker -->|"embed batches"| clip
  worker -->|"upsert vectors"| qdrant
  worker -->|"status"| pg

  %% read path (orange)
  user -->|"ask"| api
  api -->|"embed query"| clip
  api -->|"kNN · both branches"| qdrant
  api -->|"frames + transcript"| vlm

  classDef repoN fill:#fff3ec,stroke:#e2683c,color:#7a2f14;
  classDef mgmtN fill:#eef4ff,stroke:#3b6ea8,color:#173a63;
  class api,disp,worker,clip repoN;
  class obj,pg,prefect,qdrant,vlm mgmtN;
```

The **write path** flows ①→③ then dispatcher → worker → (object storage +
CLIP + Qdrant + Postgres). The **read path** is a single `ask` that fans out to
the CLIP service and both Qdrant collections, then to the vision LLM. The
retrieval + scoring detail of that read path is the [RAG at scale](#rag-at-scale)
diagram below.

The whole system is **one Docker image** with four entrypoints (the command
picks which): the API, the ingest worker, the CLIP service, and a one-shot
seed gate. All application code lives under [`src/`](src/); the repo root holds
only build/config files.

| Piece | Where it lives | You… |
|---|---|---|
| API + worker + CLIP service (this repo, one image) | Fly.io / Docker / bare python | deploy it |
| Raw videos + thumbnails | S3 / GCS / Tigris (or local disk in dev) | rent it |
| Postgres — manifest + status | [Neon](https://neon.tech) | rent it |
| Work queue + run dashboard | [Prefect Cloud](https://app.prefect.cloud) (free tier) | rent it |
| Vector index | [Qdrant Cloud](https://cloud.qdrant.io) (or a local Qdrant — see "Run storage and Qdrant locally") | rent it / self-host |
| Vision LLM | OpenAI / NVIDIA / Anthropic / any OpenAI-compatible — env-switched | rent it |

## Quickstart (Docker)

```bash
git clone https://github.com/traversaal-ai/momentsearch.git
cd momentsearch
cp .env.example .env          # cloud reference (all providers/options)
# ...or, to run LOCAL & keyless (local storage + local Qdrant + local models):
# cp .env.local.example .env
docker compose up --build
# API + UI:       http://localhost:8000
# Queue/run view: https://app.prefect.cloud → Runs
```

**What you must fill in before `up` will work.** Compose starts four services —
`clip`, `seed`, `api`, `worker` — and **none of them is a database.** Three
things are rented and have no local default:

| `.env` key | Needed for | Local alternative |
|---|---|---|
| `DATABASE_URL` | the video manifest ([Neon](https://neon.tech)) | any Postgres you can reach |
| `PREFECT_API_URL` + `PREFECT_API_KEY` | the ingest queue ([Prefect Cloud](https://app.prefect.cloud), free tier) | a self-hosted `prefect server` |
| `QDRANT_URL` + `QDRANT_API_KEY` | the vector index ([Qdrant Cloud](https://cloud.qdrant.io)) | set `COMPOSE_PROFILES=local-qdrant` and `QDRANT_URL=http://qdrant:6333` in `.env` — compose auto-starts a local Qdrant |

> ⚠️ **A vector store is required** — a plain `cp .env.example .env && docker
> compose up` has **no Qdrant** and both ingest and search fail. Either point
> `QDRANT_URL` at Qdrant Cloud, **or** add two lines to `.env` —
> `COMPOSE_PROFILES=local-qdrant` and `QDRANT_URL=http://qdrant:6333` — and compose
> starts a local Qdrant on its own (see
> [Run storage and Qdrant locally](#run-storage-and-qdrant-locally-no-cloud-bucket-or-vector-store)).

Optional: `STORAGE_PROVIDER=local` is the default and needs no bucket. An LLM key
is optional too — without one, retrieval still returns ranked, clickable moments
and the answer degrades to an honest similarity summary (the UI badge reads
"No LLM — moments only").

Three pages, one app — **no sign-in on any of them**:

| Page | What it is |
|---|---|
| **`/`** | Landing page — what it is, and the way in. |
| **`/demo`** | The pre-indexed sample corpus, read-only. Nothing is saved. |
| **`/app`** | The workspace: **1** add videos → **2** watch them process → **3** ask. Sessions keep separate folders of videos. |

`/signin` and `/get-started` are kept as `307` redirects to `/app` so old links
and bookmarks still resolve.

**The sample corpus is a startup step.** A one-shot `seed` service indexes the
sample talk before `api`/`worker` start — so when `http://localhost:8000` first
answers, the sample is already queryable. First run takes a few minutes (model
download + 1 video); watch it with `docker compose logs -f seed`. It's durable
(Qdrant Cloud) and idempotent, so every later `up` finds them indexed and starts
in seconds. It's **best-effort by default** (`SEED_STRICT=false`): if seeding
can't finish — e.g. a fresh clone whose empty Qdrant forces a live YouTube
re-index without cookies — it logs loudly and the app still starts with an empty
`/demo`, instead of blocking. Set `SEED_STRICT=true` to make it a hard gate
(never serve a half-indexed corpus), or `SEED_SAMPLE_VIDEOS=false` to skip
seeding entirely (bare deploy — upload your own videos). `python
examples/quickstart.py` is the manual route (also runs sample queries in the terminal).

> The gate is wired into `docker compose up` (via `depends_on`) and Fly (via
> `release_command`) — use one of those. A bare `docker run` of the image only
> starts uvicorn and **skips seeding**, so the samples won't be indexed.

Bare processes instead of compose (each is `python -m` / uvicorn on the `src.`
module — run in separate terminals):
```
uvicorn src.app:app --port 8000          # API + UI
python -m src.worker                      # ingest worker
uvicorn src.clip_service:app --port 8001  # CLIP service (optional; else set CLIP_SERVICE_URL empty)
python -m src.seed                        # one-shot: index the sample
```

### Run storage and Qdrant locally (no cloud bucket or vector store)

Both read their location from env, so this is config, not code:

- **Storage → local:** set `STORAGE_PROVIDER=local`. Uploads, frames and
  transcripts land under `./data` and the API serves them itself — no bucket, no
  keys. Works out of the box.
- **Qdrant → local:** add two lines to `.env` — `COMPOSE_PROFILES=local-qdrant`
  and `QDRANT_URL=http://qdrant:6333` (leave `QDRANT_API_KEY` blank). That's it:
  `docker compose up` **auto-starts** the Qdrant service (nothing to uncomment).
  Vectors persist under `./data/qdrant`, shared by api + worker. (Blank
  `QDRANT_URL` instead gives an *embedded* Qdrant, but that's single-process
  only — fine for `examples/quickstart.py`, not the full api+worker app.)

The models are already local-capable too (`IMAGE_EMBED_PROVIDER=clip` by default,
`TEXT_EMBED_PROVIDER=fastembed` + `LLM_PROVIDER=ollama` for a keyless run). That
leaves only the manifest DB (**Postgres**, `DATABASE_URL`) and the job queue
(**Prefect**, `PREFECT_API_URL`) pointing at the cloud — both are env-driven, so
a local Postgres + a self-hosted `prefect server` would complete an offline stack.

### Split / GPU deployment (CLIP on a GPU, app on CPU)

By default it's **one image** on CPU — the app embeds in-process, nothing extra
to run. When embedding is your bottleneck, split it: put **CLIP on a GPU box**
and keep the **app on cheap CPU** (Fly), wired only by `EMBED_SERVICE_URL`. The
code already treats *embedding as a URL*, so no code changes — just two images:

| Image | Build | Runs |
|---|---|---|
| **App (slim)** | `docker build --build-arg WITH_TORCH=false -t momentsearch-app .` | api + worker — **no local model**, ~hundreds of MB smaller |
| **CLIP (GPU)** | `docker build -f Dockerfile.clip --build-arg TORCH_INDEX_URL=https://download.pytorch.org/whl/cu121 -t momentsearch-clip:gpu .` | the CLIP service on a GPU host (`docker run --gpus all -p 8001:8001 …`) |

> ⚠️ **A slim app image has NO CLIP inside it — you MUST run a CLIP service and
> point the app at it, or the app cannot index or search.** Concretely:
>
> 1. **Deploy the CLIP image first** (Dockerfile.clip) on your GPU host — note its
>    address, e.g. `https://my-gpu-host:8001`.
> 2. On the app, set **`EMBED_SERVICE_URL=https://my-gpu-host:8001`**.
> 3. If that host is reachable over the public internet, set the **same
>    `EMBED_SERVICE_TOKEN`** on *both* the app and the CLIP service (so `/embed`
>    requires a bearer token), and put the CLIP service **behind HTTPS** (the
>    token authenticates but doesn't encrypt).
>
> **If you run a slim app with no reachable CLIP service**, every upload fails to
> index and every search errors with a clear message telling you to point
> `EMBED_SERVICE_URL` at a clip service. Videos that failed while CLIP was down
> aren't lost — **retry them** once CLIP is up. The slim app and a running CLIP
> service are a **pair**: deploy them together.
>
> **Seeding on a slim deploy:** the sample-corpus seed can't embed in-process
> (no local model), so it seeds *against the CLIP service* — make sure CLIP is
> reachable at seed time, or set `SEED_SAMPLE_VIDEOS=false`. Seeding is
> best-effort, so a missing CLIP at seed time won't abort the deploy (see
> `SEED_STRICT`).

**On Fly:** Fly has **no GPUs** (deprecated Jul 31 2026), so the GPU CLIP runs on
an **external host** (Modal / RunPod / a GPU VM) built from `Dockerfile.clip`;
Fly runs only the slim app pointed at it. The default `fly deploy` (fat,
all-in-one, CPU) is untouched.

```bash
# 1) build & deploy Dockerfile.clip on your GPU host (see its header) -> a URL + token
# 2) then, the slim app on Fly:
fly deploy -c fly.slim.toml    # api + worker, built slim, EMBED_SERVICE_URL -> the GPU host
```

`fly.slim.toml` carries a `CHANGE-ME-slim` app name and inline setup steps
(secrets, `EMBED_SERVICE_URL`, `EMBED_SERVICE_TOKEN`, `SEED_SAMPLE_VIDEOS=false`);
fill those in before deploying.

Prefer the **fat image** (the default) unless you specifically want CLIP on a
GPU — it's one image, one deploy, embeds itself, and has none of the above wiring.

## The write path — upload to searchable vectors

1. **Presign** — `POST /api/videos/presign {filename, content_type, size}`
   (no auth — see [API](#api)). The server picks the key
   (`{user}/{video}/source.{ext}` — everything for a video lives under
   `{user}/{video}/`; never trusted from the client), caps size and type, and returns a time-limited
   PUT URL. With `STORAGE_PROVIDER=local` it returns a direct-upload URL
   instead (dev fallback).
2. **Upload** — the browser PUTs the file straight to the bucket.
3. **Register** — `POST /api/videos {video_id, key}`. The API HEAD-verifies
   the object (exists, size, key prefix belongs to this user), writes a
   `pending` row, schedules a Prefect run, returns `202` instantly.
4. **Worker** (per video, `WORKER_CONCURRENCY` at a time):
   - **fetch** — stream from the bucket (or yt-dlp for YouTube), `sha256` it;
     a duplicate `(user_id, source_hash)` marks the row `skipped` and stops.
   - **sample** — one ffmpeg pass decodes, samples (interval or scene-cut),
     downscales and pipes JPEGs to memory — no write-then-reopen. *The
     biggest scaling lever: sampling is what stops thousands of videos
     becoming billions of near-identical vectors.*
   - **dedup** — perceptual hash (dHash + luminance) drops visually-identical
     neighbours **before** they cost CLIP compute; thumbnails batch-upload to
     `{user}/{video}/frames/NNNNNN.jpg`.
   - **embed + index** — batches of `CLIP_BATCH` frames go to the **warm CLIP
     service** (no per-video model load), then upsert to the visual collection
     (`moments_l14`) with deterministic IDs (`uuid5(video_id:frame_idx)` — re-runs
     overwrite, never duplicate), tagged `user_id`, `video_id`, `ms`,
     `modality:frame`, `t_start`/`t_end`, `embed_version`.
   - **transcript** — a video's speech becomes text: **YouTube** uses captions,
     **uploads** are transcribed by **Whisper ASR** (`whisper-1`) from their own
     audio ([src/ingest/asr.py](src/ingest/asr.py)). Either way the cues are the
     durable `{user}/{video}/transcript.json`, then → ~20s time-chunks → OpenAI
     `text-embedding-3-small` embeddings → the text collection
     (`moments_text_openai`), tagged `modality:text` with the same timestamps.
     **Best-effort:** only a caption-less YouTube video with no usable audio has
     nothing to index — it stays visual-only and the run never fails. Runs
     *after* embed+index (whose delete clears both collections first).

Poll `GET /api/videos` (or watch the UI chips) until `indexed`.

## The read path — question to answer-or-abstain

`POST /api/ask {question, video_id?}`:

1. **Retrieve — both branches, in parallel, always** (no query router; routing
   fails exactly on the ambiguous questions where you need help most):
   - **visual** — text-embedding into the *frame* space → Qdrant `moments_l14`,
     filtered by `user_id` (private *and* fast: the tenant index means a search
     touches only that user's slice), quantization-rescored. Milliseconds. The
     embedder is **provider-switchable** (`IMAGE_EMBED_PROVIDER`): local **CLIP**
     by default (free, offline, no key), or hosted **Jina CLIP v2** / **Cohere
     Embed v4** / **Voyage multimodal** / **Gemini** — see
     [Pluggable models](#pluggable-models-llms-and-embeddings).
   - **text** — text query-embedding → Qdrant `moments_text_openai`
     (transcripts — YouTube captions and Whisper on uploads). Also
     provider-switchable (`TEXT_EMBED_PROVIDER`): **OpenAI `text-embedding-3-small`**
     by default (needs `OPENAI_API_KEY`), or **bge** via fastembed (CPU, free, no
     key — the keyless escape hatch) / **Gemini** / **Cohere** / **Voyage** /
     **Jina**. Skipped cleanly when `ENABLE_TRANSCRIPT=false` or nothing is indexed yet.
2. **Score — the fusion module** (`_fuse`, [src/rag/search.py](src/rag/search.py)).
   The two branches' raw scores are incomparable (CLIP ~0.3 vs text ~0.7), so we
   never sort by raw score:
   - **RRF** — rank each branch on its own, score by rank `1/(RRF_K + rank)`, so
     a strong frame and a strong transcript hit compete fairly.
   - **time-window** — bucket hits within `FUSION_WINDOW_S` seconds (same video)
     into one *moment* and sum their RRF — the timestamp is the join key.
   - **cross-modal boost** — a moment where **both** a frame and a transcript
     chunk land at the same instant is ×`CROSS_MODAL_BOOST`: two independent
     modalities agreeing is the strongest relevance signal available.
   - **cross-encoder rerank** — a local, keyless reranker (`RERANK_PROVIDER`,
     default fastembed `ms-marco-MiniLM`) re-judges the top text candidates
     against the question and blends its score with the normalized RRF
     (`RERANK_WEIGHT`). **On by default;** set `ENABLE_RERANK=false` to fall back
     to plain RRF.
   The top `TOP_K` fused moments go forward.
3. **Gate 1 — confidence** on the *raw per-branch bests* (RRF scores are far too
   small to threshold on): abstain only when **neither** what's on screen
   (`CONFIDENCE_THRESHOLD`) **nor** what's said (`TEXT_CONFIDENCE_THRESHOLD`)
   clears its bar — "I couldn't find that in your videos", **no LLM call**. Kills
   most hallucination risk for free.
4. **Generate** — each moment's frame (downscaled to `LLM_IMAGE_MAX_PX`) **and**
   its transcript excerpt go to the vision LLM: answer only from these moments,
   cite `[n]`, or say so. Citations are validated; invented references stripped.
5. **Answer** — clickable thumbnails + timestamps (presigned GETs straight from
   the bucket). Every timestamp is read from the winning hit's payload — the LLM
   never invents one — or the honest refusal.

**Watching it happen.** `POST /api/sessions/{id}/ask_stream` reports each of those
stages over Server-Sent Events as it *begins* — `embedding → searching → ranking →
reading → answering` — which is what the workspace UI draws while you wait, with
the real elapsed time per stage. The events come from actual pipeline boundaries
(`on_stage` in [src/rag/search.py](src/rag/search.py)), not a timer, so a slow
stage visibly sits there instead of a progress bar lying to you. It doubles as a
profiler: on this stack a warm question is roughly *embedding 50ms · Qdrant 350ms ·
rerank ~1s · frame fetches ~1s · LLM 5-7s*.

The cost fact that drives this shape: retrieval is ~10-30ms; the multimodal
LLM call is seconds and dominates cost. Optimize there — few moments,
downscaled, gated — not the vector store.

## Pluggable models (LLMs and embeddings)

Every model MomentSearch talks to is a **provider name plus a key**. The
provider table ([src/providers/registry.py](src/providers/registry.py)) supplies
the endpoint, a default model and the vector dimension, so this is a complete
configuration:

```bash
LLM_PROVIDER=gemini
GEMINI_API_KEY=...
```

**Shipped defaults.** Out of the box the stack is: image/visual embedder local
CLIP **`clip-ViT-L-14`** (dim 768 → collection `moments_l14`), transcript
embedder OpenAI **`text-embedding-3-small`** (dim 1536 → collection
`moments_text_openai`), answer LLM **`gpt-4o`**, upload transcription **Whisper
`whisper-1`**, and a local cross-encoder **reranker** (`ms-marco-MiniLM`) on by
default. There are **four independently swappable slots** — image embedder
(`IMAGE_EMBED_PROVIDER` / `CLIP_MODEL`), text embedder (`TEXT_EMBED_PROVIDER`),
answer LLM (`LLM_PROVIDER`, bring-your-own, incl. any OpenAI-compatible server),
and ASR (`ASR_PROVIDER`) — plus the reranker (`RERANK_PROVIDER`).

> **A real run needs an `OPENAI_API_KEY`.** Because the default text embedder
> *and* the default answer LLM (*and* Whisper) are all OpenAI, **one key powers
> all three**. To run the transcript branch **without** a key, set
> `TEXT_EMBED_PROVIDER=fastembed` (bge, CPU-local) — and the visual branch's
> local CLIP needs no key either, so the whole search path can stay keyless.

```
src/providers/
  registry.py     the table: endpoints, default models, dims, key env vars
  status.py       what's configured / what's installed  (GET /api/providers)
  llm/            answer synthesis — one module per wire dialect
  embed/          retrieval — one module per provider, both branches
```

**Answer model** (`LLM_PROVIDER`) — must be **vision-capable**; it is shown the
actual frames:

| provider | key env var | default model | notes |
|---|---|---|---|
| `openai` **default** | `OPENAI_API_KEY` | `gpt-4o` | also the generic OpenAI-compatible client |
| `gemini` | `GEMINI_API_KEY` | `gemini-3.6-flash` | native SDK (`pip install google-genai`) |
| `gemini_openai` | `GEMINI_API_KEY` | `gemini-3.6-flash` | same models, no extra dependency |
| `anthropic` | `ANTHROPIC_API_KEY` | `claude-sonnet-5` | `pip install anthropic` |
| `openrouter` | `OPENROUTER_API_KEY` | `openai/gpt-4o-mini` | one key, hundreds of models |
| `xai` (`grok`) | `XAI_API_KEY` | `grok-4.5` | |
| `groq` | `GROQ_API_KEY` | — set `LLM_MODEL` | catalogue rotates; no safe default |
| `together` | `TOGETHER_API_KEY` | `Qwen/Qwen2.5-VL-72B-Instruct` | |
| `fireworks` | `FIREWORKS_API_KEY` | — set `LLM_MODEL` | |
| `mistral` | `MISTRAL_API_KEY` | `pixtral-12b-2409` | |
| `nvidia` | `NVIDIA_API_KEY` | `meta/llama-3.2-11b-vision-instruct` | NIM |
| `azure_openai` | `AZURE_OPENAI_API_KEY` | — deployment name | `AZURE_OPENAI_ENDPOINT` |
| `ollama` / `lmstudio` / `vllm` | none | `qwen2.5vl` / — / — | localhost, no key |
| `custom` | optional | — | any OpenAI-compatible server via `LLM_BASE_URL` |

Most of these share **one** adapter, because most of the industry speaks the
OpenAI Chat Completions dialect — they differ only by `base_url`. Adding a
provider that speaks a known dialect is a row in a table, not new code. Unknown
names fall back to the generic OpenAI-compatible client, so a provider that
launched last week works today with `LLM_BASE_URL` alone.

**Embeddings** — two independent branches, each provider-switchable. They fuse
by *rank* (RRF), never by score, so mixing is fine: local CLIP frames plus hosted
Gemini transcripts is a sensible setup.

**Visual branch** (`IMAGE_EMBED_PROVIDER`) — frames *and* the question, one space:

| provider | default model | dim (truncatable to) | key env var | install |
|---|---|---|---|---|
| `clip` **default** | `clip-ViT-L-14` | 768 | none — offline | sentence-transformers |
| `jina` | `jina-clip-v2` | 1024 (64–1024) | `JINA_API_KEY` | **nothing** |
| `cohere` | `embed-v4.0` | 1536 (256–1536) | `COHERE_API_KEY` | **nothing** |
| `voyage` | `voyage-multimodal-3.5` | 1024 (256–2048) | `VOYAGE_API_KEY` | **nothing** |
| `gemini` | `gemini-embedding-2` | 1536 (128–3072) | `GEMINI_API_KEY` | `google-genai` |

**Transcript branch** (`TEXT_EMBED_PROVIDER`) — caption chunks:

| provider | default model | dim | key env var | install |
|---|---|---|---|---|
| `openai` **default** | `text-embedding-3-small` | 1536 | `OPENAI_API_KEY` (falls back to `LLM_API_KEY`) | `openai` |
| `fastembed` | `BAAI/bge-small-en-v1.5` | 384 | none — offline (the keyless escape hatch) | fastembed |
| `gemini` | `gemini-embedding-2` | 1536 | `GEMINI_API_KEY` | `google-genai` |
| `cohere` | `embed-v4.0` | 1536 | `COHERE_API_KEY` | **nothing** |
| `voyage` | `voyage-3.5` | 1024 | `VOYAGE_API_KEY` | **nothing** |
| `jina` | `jina-embeddings-v3` | 1024 | `JINA_API_KEY` | **nothing** |

Per-branch overrides, when the defaults aren't what you want:
`IMAGE_EMBED_MODEL` / `IMAGE_EMBED_DIM` / `IMAGE_EMBED_API_KEY` /
`IMAGE_EMBED_BASE_URL` / `IMAGE_EMBED_BATCH` / `IMAGE_EMBED_CONCURRENCY`, and the
same six with a `TEXT_EMBED_` prefix. `*_BASE_URL` is how you point the `openai`
text embedder at your own vLLM/TEI server; `*_DIM` is required for a model the
registry's table doesn't list. The legacy `CLIP_MODEL` / `CLIP_DIM` /
`CLIP_BATCH` / `CLIP_SERVICE_URL` names still work as aliases
(`EMBED_SERVICE_URL` is the current name for the last one).

The visual branch needs a **joint** image+text space — search is text→image, so
the model must embed both into the *same* space. That's why OpenAI isn't an
option there (it has no image-embedding model) while CLIP, Jina CLIP v2, Cohere
Embed v4, Voyage multimodal and `gemini-embedding-2` are. Jina, Cohere and Voyage
need **no SDK at all** — those adapters are plain HTTPS from the standard library.

**Cost note for the visual branch:** a video is hundreds of frames, so per-frame
price and latency multiply fast. Local CLIP is free and the default for a reason.
Among the hosted ones, Jina/Cohere/Voyage take **many images per request**;
Gemini's embeddings endpoint takes **one image per call** (measured ~9s each, and
it appears to serialize per key), so `IMAGE_EMBED_PROVIDER=gemini` is best kept
for small corpora or paired with a higher `FRAME_INTERVAL_SEC` / lower
`MAX_FRAMES`. Tune the fan-out with `IMAGE_EMBED_CONCURRENCY`. The transcript
branch is cheap everywhere — a video is a few dozen chunks, not hundreds of frames.

**ASR** (`ASR_PROVIDER`) — how **uploads** become transcripts (YouTube uses
captions and skips this). Default **`openai` `whisper-1`**, reusing the same
OpenAI key; `ASR_MODEL` can be `gpt-4o-transcribe`. Set `ENABLE_ASR=false` (or
`ASR_PROVIDER=""`) to leave uploads visual-only.

**Reranker** (`RERANK_PROVIDER`) — a cross-encoder that re-judges the top text
candidates and blends with RRF (`RERANK_WEIGHT`). Default **`fastembed`
`ms-marco-MiniLM`** (local, keyless) — **on by default**; `cohere` (Rerank API)
is the hosted option. Set `ENABLE_RERANK=false` to fall back to plain RRF.

**Two things that will bite you, and what the code does about them:**

- **Dimensions must match between indexing and querying.** Switching provider or
  model changes the vector size, which makes the existing index unusable. Qdrant
  collection creation now **refuses** to reuse a collection whose vector size
  disagrees with the configured embedder, with a message telling you to re-index
  or restore the old setting — rather than failing mid-ingest, or (worse) silently
  comparing vectors from two unrelated spaces.
- **Confidence thresholds are model-specific.** `CONFIDENCE_THRESHOLD=0.2` is
  calibrated for CLIP's text→image cosines (~0.2-0.35); a different embedder on
  another scale would over-abstain. Defaults now come from the chosen provider's
  preset, and providers we haven't calibrated default to **0 = gate off** — the
  safe direction. Measure yours with [benchmark/score.py](benchmark/score.py)
  before pinning a value.

**Check what your `.env` actually resolved to** — this is the first thing to run
when a key "isn't working":

```bash
python -m src.providers          # resolved config, missing keys, installed SDKs
python -m src.providers --live   # actually call every configured model
python -m src.providers --json   # same data as GET /api/providers
```

It names the env var each key came from, flags a provider name it doesn't
recognize (a typo resolves to a *fallback* rather than failing, which is
convenient and silent), and flags the single most confusing misconfiguration: a
leftover generic `LLM_API_KEY` from another provider shadowing the
provider-specific one.

## Bring your own model

Which model writes the answer is resolved in this order:

1. **An attached model** — saved via `PUT /api/llm` (**backend-only — not exposed
   in the UI**): any provider from the table above with its own key, or your own
   **vLLM** / Ollama / LM Studio endpoint via `base_url`. The model must be
   **vision-capable** — it is shown the actual frames (e.g.
   `Qwen/Qwen2.5-VL-7B-Instruct` on vLLM). `GET /api/providers` lists the choices;
   the UI only shows a read-only badge of which model is active — there's no
   model-settings form. Only the *LLM* is switchable this way: embeddings live in
   shared collections, so their dimension can't vary per request.
2. **The server default** — the `LLM_*` env config, used when nothing is attached.
3. **No model** — retrieval still works; the answer degrades to an honest
   visual-similarity summary and `llm_used: false` (verified: you still get ranked
   moments with thumbnails, timestamps and clickable citations).

```bash
# what can I attach?
curl localhost:8000/api/providers

# attach your own hosted vLLM  (no auth header — the API is unauthenticated)
curl -X PUT localhost:8000/api/llm -H "Content-Type: application/json" \
  -d '{"provider":"openai","model":"Qwen/Qwen2.5-VL-7B-Instruct",
       "base_url":"http://my-vllm-host:8000/v1"}'

# or a hosted provider — model is optional, the preset knows its default
curl -X PUT localhost:8000/api/llm -H "Content-Type: application/json" \
  -d '{"provider":"grok","api_key":"xai-..."}'

curl -X POST localhost:8000/api/llm/test
#  -> sends one tiny image through your model; fails fast if it isn't vision-capable

curl -X DELETE localhost:8000/api/llm
#  -> back to the server default
```

Settings live in Postgres (`ms_user_llms`, keyed by tenant — one row here); API keys are
write-only (masked on read, blank on update keeps the stored key). Bad configs
are rejected at `PUT` time with the fix named ("no API key. Set XAI_API_KEY"),
not at answer time. `/api/ask`
responses include `llm_source: "user" | "server"` so the UI can show whose
model answered. **Ops note:** user `base_url`s make your API box call
user-chosen hosts — on a hosted deployment, egress-restrict the API container
or allowlist hosts; self-hosted single-tenant setups don't care.

## Qdrant at frame scale

One shared collection, multi-tenant by `user_id` (tenant payload index) — not
collection-per-user. Frames balloon vector counts fast (a 1h video at 2s
sampling ≈ 1,800 candidate frames), so the low-RAM profile defaults **on**:

| Flag | Effect | Default |
|---|---|---|
| `QDRANT_ON_DISK` | original float vectors on disk | on |
| `QDRANT_QUANTIZATION` | int8 copies pinned in RAM (~4× smaller) do the search; queries rescore from the originals | on |
| `QDRANT_HNSW_ON_DISK` | the HNSW graph on disk too | on |

For scale intuition: 200M frame vectors ≈ 600GB float32 vs ≈ 150GB int8.
On a big-RAM node flip these off to trade memory back for speed. Payloads are
trimmed to filter/display fields; titles/URLs live in Postgres and join at
answer time. `embed_version` on every point means a future CLIP upgrade can
re-embed in the background without breaking the live index.

## RAG at scale

The read path in detail — one `ask` fans out to **both** retrieval branches,
they're fused by a rank-based scoring module, gated for confidence, then (only
if it clears the gate) synthesized by a vision LLM. The dashed notes mark where
each stage **scales** as the corpus and traffic grow:

```mermaid
flowchart TB
  q(["Question + user_id"])
  ve["visual branch<br/>CLIP text-embed"]
  te["text branch<br/>OpenAI query-embed"]
  q --> ve
  q --> te

  ve -->|"kNN, user_id filter"| vq[("Qdrant 'moments_l14'<br/>int8 · on-disk · rescore")]
  te -->|"kNN, user_id filter"| tq[("Qdrant 'moments_text_openai'<br/>captions + Whisper")]

  vq --> fuse
  tq --> fuse

  subgraph fuse["Scoring module — src/rag/search.py :: _fuse"]
    direction TB
    r["① RRF — rank each branch on its own<br/>score = 1 / (RRF_K + rank)"]
    w["② time-window — group hits ≤ FUSION_WINDOW_S s<br/>(same video) into one 'moment'"]
    b["③ best-per-modality + ×CROSS_MODAL_BOOST<br/>when a frame AND transcript agree at that instant"]
    x["④ cross-encoder rerank (default on)<br/>ms-marco-MiniLM · blend RERANK_WEIGHT"]
    r --> w --> b --> x
  end

  fuse -->|"top-TOP_K fused moments"| gate{"Gate 1<br/>both raw branch-bests<br/>below threshold?"}
  gate -->|"yes"| ab(["Abstain — no LLM call"])
  gate -->|"no"| llm["Vision LLM<br/>frame + transcript per moment<br/>cite [n]; timestamps from payload"]
  llm --> ans(["Grouped, cited answer"])

  %% scaling notes
  sc1>"Qdrant: int8 + on-disk + rescore<br/>→ shard when one node is outgrown"] -.- vq
  sc2>"Embedding is a URL: CLIP service<br/>scales up / onto a GPU on its own"] -.- ve
  sc3>"LLM call dominates cost — so few<br/>moments, downscaled, gated FIRST"] -.- llm

  classDef store fill:#eef4ff,stroke:#3b6ea8,color:#173a63;
  classDef note fill:#fffbe6,stroke:#c9a227,color:#6b5410;
  class vq,tq store;
  class sc1,sc2,sc3 note;
```

Why this shape holds up as the corpus grows: **retrieval is milliseconds and the
multimodal LLM call is seconds**, so the funnel spends its cheap budget widely
(both branches, always) and its expensive budget narrowly (a handful of gated,
downscaled moments). Each box below scales on its own bottleneck, independently —
that's the whole point of splitting the four processes out:

| Component | Scales by | Because its bottleneck is… | How |
|---|---|---|---|
| **API** (`src/app.py`) | replicas (horizontal) | request concurrency (all I/O, no heavy compute) | stateless; auto-stops when idle on Fly |
| **Worker** (`src/worker.py`) | replicas (horizontal) | ingest throughput — download + ffmpeg per video | `fly scale count worker=N` / `--scale worker=N`; workers only dial out, zero coordination |
| **Embedding service** (`src/clip_service.py`) | vertically → **GPU** | embedding FLOPs (the compute-heavy step) | one warm model behind `EMBED_SERVICE_URL` (`CLIP_SERVICE_URL` still works); move it to a GPU box, change only the URL. Skipped entirely when the embedder is a hosted API — nothing to warm |
| **Qdrant** | memory profile → shards | vector count (frames balloon fast) | int8 + on-disk + rescore by default; shard when one node is outgrown |

The two axes that matter pull in opposite directions: **ingest** (many cheap
CPU workers, scale out) vs. **embedding** (one hot model, scale up/GPU). Coupling
them — the naive "CLIP inside the worker" — would force you to pay for GPUs on
every worker or starve embedding on every scale-out. Splitting them is what lets
you add cheap workers for a backfill while a single GPU handles all their embeds.

## Scaling — the details

**Workers.** The API must answer `202` instantly, but a video takes minutes —
workers pull runs from Prefect Cloud and execute them, `WORKER_CONCURRENCY`
at a time. Runs bottleneck on different resources (fetch = network, sampling
= CPU, embedding = CPU/GPU), so concurrent runs overlap. One worker machine
full? `fly scale count worker=3` or `docker compose up --scale worker=3` —
workers only dial out, so replicas need zero coordination.

**Embedding (the usual bottleneck) — "embedding is a URL".** Local inference runs
in a dedicated service ([clip_service.py](src/clip_service.py)): one warm model
loaded once at boot, api + workers send batches over HTTP (`EMBED_SERVICE_URL`;
`CLIP_SERVICE_URL` is still accepted). That's what makes workers cheap and
stateless — no torch, no ~15-30s model reload per video — and it's the standard
model-serving pattern (TEI / Triton / OpenAI-embeddings-shaped). Scaling
embedding = scaling that one service: CPU container today, the same container on
a GPU machine later, with nothing but the URL changing. Unset the URL and
everything embeds in-process — the zero-service simple mode for cloners.

Choosing a **hosted** embedder ([Pluggable models](#pluggable-models-llms-and-embeddings))
is the third option: it sidesteps this service entirely — the API and workers call
the provider directly, so there's nothing to warm, nothing to scale, and the
service URL is ignored. You've swapped a compute-scaling problem for a
cost-per-frame one.

**Deletes purge everything** — `DELETE /api/videos/{id}` removes the vectors
(by filter), thumbnails + raw upload (batch delete), and the manifest row.

**Fair scheduling (WFQ).** The queue is fair, not FIFO. If it enqueued every
video to Prefect at register time, Prefect would run them in submitted order —
one user who uploads 50 videos blocks everyone behind them. Instead videos wait
`pending` in Postgres and a **dispatcher** ([src/dispatcher.py](src/dispatcher.py))
admits them **round-robin across users**, keeping only `DISPATCH_MAX_INFLIGHT`
running at once. So the waiting line lives in *our* DB, fairly ordered
([`db.wfq_claim`](src/db.py) ranks each user's videos by age and takes
everyone's oldest first, then everyone's second, …) — no user can starve the
others. Set `ENABLE_FAIR_DISPATCH=false` to fall back to plain FIFO and see the
difference. `DISPATCH_MAX_INFLIGHT` should equal your real capacity
(`worker machines × WORKER_CONCURRENCY`); anything above that would just pile up
FIFO inside Prefect and defeat the fairness.

```
FIFO:  user A ▓▓▓▓▓▓▓▓▓▓ (50)  then→  user B ▓   ← B waits for all of A
WFQ:   A▓ B▓ A▓ B▓ A▓ B▓ …            ← interleaved; B is served immediately
```

**Later, under real load** (design room exists, not built): per-tenant *quotas*
and weights (the dispatcher's round-robin extends to weighted shares),
backpressure on queue depth, Redis query cache, and OCR / on-screen-text as a
third branch (transcript hybrid search and a cross-encoder reranker already ship
— see the read path above).

## Deploy (Fly.io)

Full step-by-step guide: **[DEPLOYMENT.md](DEPLOYMENT.md)**. The short version —
one image, **three process groups** from [fly.toml](fly.toml) — `api`, `worker`,
and `clip` (each on its own machine size, each scaled by its own bottleneck; the
"what's scaled and why" table above explains the split):

```powershell
fly launch --no-deploy --copy-config          # create the app (once)
fly storage create                            # Tigris bucket; injects AWS_* secrets
Get-Content .env | Where-Object { $_ -match '^[A-Z_]+=.+' -and $_ -notmatch '^FLY_' } | fly secrets import
fly secrets set STORAGE_PROVIDER=flyio
fly deploy --ha=false                         # build image, start api/worker/clip
fly scale count worker=2                      # more ingest throughput, anytime
```

On every deploy, fly.toml's `release_command` runs the **seed step** first
(`python -m src.seed`). It's **best-effort by default** (`SEED_STRICT=false`): if
the sample can't be indexed the deploy still goes live with an empty `/demo`. Set
`SEED_STRICT=true` to make an incomplete seed abort the deploy (the previous
version keeps serving). The API machine auto-stops when idle; worker + clip stay
up (scale both to 0 between ingest sessions — queued runs just wait). Set a CORS
rule on the bucket for your site's origin (see `.env.example`) or browser uploads
fail. Need GPU-speed embedding later? Run the CLIP service (`Dockerfile.clip`) on
an external GPU host and point `EMBED_SERVICE_URL` at it — nothing else changes.

### Continuous deployment (GitHub Actions)

[`.github/workflows/fly-deploy.yml`](.github/workflows/fly-deploy.yml) deploys
to Fly on every push to `dev`. One-time setup — create a deploy token and add
it as the `FLY_API_TOKEN` repo secret (Settings → Secrets and variables →
Actions):

```bash
fly tokens create deploy -x 999999h
```

## API

> ⚠️ **There is no authentication.** Every endpoint below is open, mutating ones
> included. `ADMIN_TOKEN` is **no longer enforced** — setting it changes nothing
> (`require_auth()` in [src/api/videos.py](src/api/videos.py) is a deliberate
> no-op, kept as the single place to restore a check). `X-User-Id` and
> `Authorization` are **ignored**, not honoured, so a stale header can't steer
> reads or writes. Every request acts as `SINGLE_USER_ID` (default `default`).
> Bind this to localhost, or put an authenticating proxy in front of it.

```bash
# 1) presign
curl -X POST localhost:8000/api/videos/presign -H "Content-Type: application/json" \
  -d '{"filename":"demo.mp4","content_type":"video/mp4","size":123456789}'
# 2) PUT the file to the returned url, then 3) register:
curl -X POST localhost:8000/api/videos -H "Content-Type: application/json" \
  -d '{"video_id":"up_ab12cd34ef","key":"default/up_ab12cd34ef/source.mp4","title":"Demo"}'

# YouTube instead (session_id is optional — it drops the video into that session):
curl -X POST localhost:8000/api/videos -H "Content-Type: application/json" \
  -d '{"url":"https://youtu.be/VIDEO_ID"}'

# status / retry / delete
curl localhost:8000/api/videos
curl -X POST localhost:8000/api/videos/up_ab12cd34ef/retry
curl -X DELETE localhost:8000/api/videos/up_ab12cd34ef      # purges vectors + files + row

# ask across everything indexed
curl -X POST localhost:8000/api/ask -H "Content-Type: application/json" \
  -d '{"question":"a diagram of the attention mechanism"}'

# which model providers are supported, and what is this deployment using?
curl localhost:8000/api/providers
```

**Sessions** — a session is one folder of videos plus the answers asked of it.
This is what the workspace UI drives:

```bash
curl localhost:8000/api/sessions                       # list (bootstraps first run)
curl -X POST localhost:8000/api/sessions -H "Content-Type: application/json" \
  -d '{"title":"My first search"}'                     # new, EMPTY session
curl localhost:8000/api/sessions/s_ab12                # session + videos + messages
curl -X PATCH  localhost:8000/api/sessions/s_ab12 -H "Content-Type: application/json" \
  -d '{"title":"Renamed"}'
curl -X DELETE localhost:8000/api/sessions/s_ab12      # + purges videos no other session holds

# ask, scoped to THIS session's indexed videos
curl -X POST localhost:8000/api/sessions/s_ab12/ask -H "Content-Type: application/json" \
  -d '{"question":"what is tokenization?","video_ids":["yt_LPZh9BOjkQs"]}'

# same, but streams what the server is doing (Server-Sent Events)
curl -N -X POST localhost:8000/api/sessions/s_ab12/ask_stream \
  -H "Content-Type: application/json" -d '{"question":"what is tokenization?"}'
#  -> data: {"type":"stage","stage":"embedding"}      as each stage BEGINS
#     data: {"type":"stage","stage":"searching"}         (embedding, searching,
#     data: {"type":"stage","stage":"ranking",...}        ranking, reading, answering)
#     data: {"type":"done","message":{...}}           the stored message
```

Stages are emitted at real pipeline boundaries, never on a timer, so the UI's
progress list always names what the server is actually busy with.

Pages: `GET /` · `GET /demo` · `GET /app` (`/signin` and `/get-started` → `307 /app`).
Meta: `GET /api/health` · `GET /api/config` · `GET /api/providers` ·
`GET /api/auth/me` (returns the one account: `{"name":"admin","user_id":"default"}`).

## Layout

Repo root holds only build/config/docs; **all Python lives under `src/`**, with
the four entrypoints as top-level modules in the package.

```
├── Dockerfile               the app image (api/worker/seed/clip); WITH_TORCH arg = fat (default) or slim
├── Dockerfile.clip          the CLIP service image (CPU default; TORCH_INDEX_URL build-arg for a GPU host)
├── docker-compose.yml       local dev: clip + seed + api + worker (+ optional local-qdrant profile)
├── fly.toml                 Fly.io: FAT api/worker/clip process groups + seed release_command
├── fly.slim.toml            Fly.io: SLIM api+worker, CLIP on an external host
├── requirements.txt         base deps (no torch)
├── requirements-clip.txt    the local-CLIP torch stack (fat image + Dockerfile.clip)
├── .env.example             every env knob, documented inline (cloud/deploy reference)
├── .env.local.example       ready-to-copy LOCAL preset (local storage + Qdrant + keyless models)
├── .github/
│   └── workflows/
│       └── fly-deploy.yml   CI: deploy to Fly on push to dev
├── ui/                     static pages + JS modules, no build step
│   ├── landing.html         marketing / entry page
│   ├── demo.html            read-only sample-project UI
│   ├── app.html             the workspace: add → process → ask, sessions, player
│   ├── app.css              shared styles
│   ├── common.js            shared helpers (identity is a constant — no sessions)
│   ├── demo.js              sample-project logic
│   ├── workspace.js         sessions / upload / pipeline / streaming ask / player
│   └── assets/              logo + favicon (served at /ui/assets/*)
├── examples/
│   └── quickstart.py        manual in-process seed + terminal query demo
└── src/                     ── entrypoints ──────────────────────────────────
    ├── app.py               unified FastAPI app — videos + search routers, one port
    ├── worker.py            Prefect worker — serves "ms-ingest-video/ingest"
    ├── clip_service.py      embedding service — warm local models behind a URL
    ├── seed.py              startup gate — indexes the sample, then exits
    │                        ── core ──────────────────────────────────────────
    ├── config.py            every env knob in one place (+ SINGLE_USER_ID)
    ├── db.py                Neon Postgres: manifest + status + sessions + chat
    │                        + the attached-LLM row (ms_users is legacy/unused)
    ├── jobs.py              Prefect Cloud trigger (API-side run_deployment)
    ├── storage.py           object storage (aws|gcp|gcp_native|flyio|local)
    │                        + presigned PUT/GET, HEAD verify, batch delete
    ├── llm.py               back-compat shim -> providers/llm/
    ├── samples.py           the single-sample corpus (3Blue1Brown, "LLMs explained briefly")
    ├── seeding.py           blocking seed-to-completion logic (used by seed.py)
    ├── api/                 ── HTTP routers ──────────────────────────────────
    │   ├── videos.py        presign / register / status / retry / delete
    │   │                    + user_id() and require_auth() — the single-user
    │   │                    tenant resolution and the (no-op) auth hook
    │   ├── sessions.py      sessions, membership, ask, ask_stream (SSE stages)
    │   ├── search.py        /api/ask, /api/config, /api/llm, frames, transcript,
    │   │                    media range-serving, and the HTML pages
    │   └── auth.py          the one account — GET /api/auth/me, nothing else
    ├── providers/           ── pluggable models (see "Pluggable models") ─────
    │   ├── registry.py      the provider table: endpoints, models, dims, keys
    │   ├── status.py        configured/installed/missing — GET /api/providers
    │   ├── __main__.py      `python -m src.providers` model doctor (+ --live)
    │   ├── llm/             answer synthesis, one module per wire dialect
    │   │   ├── base.py          LLMConfig, system prompt, image downscaling
    │   │   ├── openai_compat.py OpenAI dialect: OpenAI, OpenRouter, Grok, Groq,
    │   │   │                    Together, Fireworks, Mistral, NVIDIA, Azure,
    │   │   │                    Ollama, LM Studio, vLLM, custom
    │   │   ├── gemini.py        Google Gemini, native google-genai SDK
    │   │   └── anthropic.py     Anthropic Messages API
    │   └── embed/           retrieval embeddings, both branches
    │       ├── base.py          config/dim resolution, HTTP, L2-normalizing
    │       ├── clip_local.py    local CLIP — joint image+text (default, clip-ViT-L-14)
    │       ├── fastembed_text.py bge — transcripts (keyless, opt-in)
    │       ├── openai_text.py   OpenAI + any OpenAI-compatible embeddings (transcript default)
    │       ├── gemini.py        gemini-embedding-2 — unified space, both branches
    │       ├── jina.py          jina-clip-v2 / v3 — both branches, no SDK
    │       ├── cohere.py        embed-v4.0 — both branches, no SDK
    │       ├── voyage.py        voyage-multimodal — both branches, no SDK
    │       └── remote.py        client for the warm embedding service
    ├── dispatcher.py        WFQ: fair round-robin admission of pending videos
    ├── ingest/
    │   ├── fetch.py         source acquisition (bucket download | yt-dlp) + sha256
    │   ├── frames.py        ffmpeg pipe-to-memory sampling (interval | scene)
    │   ├── dedup.py         perceptual-hash dedup (before embedding spends compute)
    │   ├── transcript.py    YouTube captions → time-chunks (the text branch)
    │   ├── asr.py           Whisper ASR: transcribe uploaded videos' own audio
    │   └── pipeline.py      the Prefect flow: fetch → sample → embed/index → transcript
    └── rag/
        ├── embeddings.py    back-compat shim -> providers/embed/
        ├── rerank.py        cross-encoder reranker over the fused moments (default on)
        ├── vector_store.py  multi-tenant Qdrant: visual + text collections, int8/on-disk
        └── search.py        2-branch retrieve → RRF fusion/scoring → rerank → gate → cited answer
```

## Security notes (presigned uploads)

- ⚠️ **The API is unauthenticated.** `ADMIN_TOKEN` is no longer enforced, so
  anyone who can reach the port can mint upload URLs, ingest, ask and delete.
  Bind it to localhost, or put an authenticating proxy (Cloudflare Access, oauth2-proxy,
  Fly private networking) in front of any deploy that isn't your own laptop.
  The enforcement point still exists in one function — `require_auth()` in
  [src/api/videos.py](src/api/videos.py) — so re-adding a check is a one-place edit.
- The **server** generates the key (`{user}/{video}/source.{ext}`, everything for
  a video under `{user}/{video}/`), never the client; register re-checks the
  prefix, so a client can't claim an object outside its own prefix.
- Size and content-type are capped at presign time and re-verified via HEAD.
- Keep the bucket **private**; thumbnails/playback go out via presigned GETs.
- ffmpeg/yt-dlp parse untrusted input — run workers in containers, not on the
  API box.
- Prompt-injection: frames are pixels (low risk), but treat any future
  OCR/transcript text as data, never instructions.

## Known limits

- **YouTube downloads.** Modern yt-dlp (2025+) needs a **JavaScript runtime +
  its EJS challenge-solver** to extract YouTube formats at all — without them
  every video fails "This video is not available." The Docker image installs
  **Node** and the worker fetches the solver automatically, so this works out
  of the box; for bare-process dev, install `node` or `deno`. **Cookies** then
  get past sign-in/bot-checks and work **everywhere** (home and datacenter):
  export a `cookies.txt` from a logged-in browser and supply it via
  `YT_COOKIES_FILE` (mounted file, local) or `YT_COOKIES_B64` (base64 secret,
  e.g. `fly secrets set YT_COOKIES_B64="$(base64 -w0 data/cookies.txt)"` on
  cloud). Cookies expire in a few weeks — re-export when it starts failing.
  Uploads are never affected by any of this.
- The embedded local Qdrant (`QDRANT_URL` empty) can't be shared by API and
  worker concurrently — single-process dev only; compose runs a real Qdrant.
- Faithfulness ceiling is *near*-zero, not zero — the gate + citations remove
  most of it; a vision-verifier pass is a future, costlier layer.

## License

Apache 2.0.
