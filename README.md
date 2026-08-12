# MomentSearch

**Ask a question about your videos, get the exact moment back — matched on what's _seen_ on screen AND what's _said_ in the transcript.**

🌐 **Live app:** [momentsearch.fly.dev](https://momentsearch.fly.dev/) · Apache 2.0

Upload videos or paste YouTube URLs. Background workers sample keyframes, dedup, embed and index them in [Qdrant](https://qdrant.tech). Ask a question and MomentSearch retrieves the best-matching moments, then has a vision LLM read those frames and write a cited answer — or abstain when the evidence isn't there. It's **visual-first and multimodal**: the frame branch searches what's on screen, the transcript branch searches what's said, and the two are fused into one ranked set of moments.

**What it does:**

- **Presigned uploads** — gigabytes go browser → bucket, never through the API.
- **Dual-branch retrieval** — a visual (frame) branch and a text (transcript) branch, fused, always both.
- **Pluggable models** — the answer LLM and both embedding branches are chosen by provider name; run fully local and keyless, fully hosted, or any mix.
- **Confidence gate** — abstains with no LLM call when neither branch clears its bar, killing most hallucination risk.
- **Speaker recognition** — optional per-video "who said what" via Gemini video understanding.
- **Single-user, no auth** — opening the app is being logged in as the one account.

---

# Setup

```bash
git clone https://github.com/traversaal-ai/momentsearch.git
cd momentsearch
cp .env.local.example .env      # local & keyless preset
docker compose up --build
# → http://localhost:8000
```

`.env.local.example` is a **local, keyless preset** — storage, models, Qdrant and Postgres all run on your machine (`DATABASE_URL` and `QDRANT_URL` are covered by the `local-postgres` / `local-qdrant` compose profiles). The one thing to fill in is **Prefect** (`PREFECT_API_URL` + `PREFECT_API_KEY`, the ingest queue) — a free key, no card, from [app.prefect.cloud](https://app.prefect.cloud) → avatar → API Keys.

**First run takes a few minutes** (the first `docker build` also downloads PyTorch). A one-shot `seed` step then downloads the CLIP model and indexes the sample video *before* `api`/`worker` start, so **`http://localhost:8000` won't answer until seeding finishes** — that's expected, not a hang. Watch progress with `docker compose logs -f`; **you'll know it's ready when the logs print:**

```
================================================================
  MomentSearch is UP  ->  open  http://localhost:8000
================================================================
```

Later runs find the sample already indexed and start in seconds. No LLM key is fine — retrieval still returns ranked, clickable moments (the UI badge reads "No LLM — moments only"); add `LLM_PROVIDER` + a key when you want prose.

## Without Docker

Four processes, each in its own terminal:

```bash
uvicorn src.app:app --port 8000            # API + UI
python -m src.worker                       # ingest worker
uvicorn src.clip_service:app --port 8001   # CLIP service (optional — unset EMBED_SERVICE_URL to embed in-process)
python -m src.seed                         # one-shot: index the sample
```

## Troubleshooting

```bash
python -m src.providers          # what your .env actually resolved to, and what's missing
python -m src.providers --live   # actually call every configured model
```

Run this first when a key "isn't working" — it names the env var each key came from and flags provider-name typos and shadowed keys.

---

# Configuration

Copy `.env.example` (the full, inline-documented reference) and set what you need. The key variables:

| Variable | What it is |
|---|---|
| `STORAGE_PROVIDER` | where raw videos + thumbnails live: `local` (default) · `aws` · `gcp` · `gcp_native` · `flyio`. See [Object storage](#object-storage). |
| `DATABASE_URL` | Postgres connection — the video manifest, status and sessions. |
| `QDRANT_URL` + `QDRANT_API_KEY` | the vector index. Required — ingest and search both fail without one. |
| `IMAGE_COLLECTION` / `TEXT_COLLECTION` | the frame and transcript collections (`QDRANT_COLLECTION` is a legacy alias for `IMAGE_COLLECTION`). A new embedding model needs a fresh collection name. |
| `PREFECT_API_URL` + `PREFECT_API_KEY` | the ingest queue between the API and the workers. |
| `LLM_PROVIDER` | the answer LLM (vision-capable). Blank = retrieval-only. |
| `IMAGE_EMBED_PROVIDER` | frame embeddings — the visual branch (default local `clip`). |
| `TEXT_EMBED_PROVIDER` | transcript embeddings — the text branch (default `openai`). |
| `RERANK_PROVIDER` | cross-encoder reranker over transcript hits (default local `fastembed`). |
| `ASR_PROVIDER` | speech-to-text for uploaded videos (default `openai` / Whisper). |
| `GEMINI_API_KEY` | required for speaker recognition ("who said what"). |

**Feature flags:** `ENABLE_TRANSCRIPT` (the transcript branch), `ENABLE_RERANK` (reranker), `DIARIZE_ENABLED` (speaker recognition master switch), `SEED_SAMPLE_VIDEOS` (index the sample talk on startup).

**YouTube ingest — cookies.** YouTube bot-checks requests **by IP**, so fetching a video — *especially its captions* — can fail with *"Sign in to confirm you're not a bot."* (frames often still index via fallback clients, but the transcript branch has no fallback, so it comes out visual-only). This hits **deploys** (datacenter IP) almost always, and **local** runs increasingly too. If fetching fails, add cookies from a logged-in browser:

1. **Get them** — install a "Get cookies.txt" browser extension (e.g. *Get cookies.txt LOCALLY*), open `youtube.com` while signed in, and export a **Netscape-format `cookies.txt`**.
2. **Add them:**
   - **Local:** save it to `./secrets/cookies.txt` and set `YT_COOKIES_FILE=/app/secrets/cookies.txt` (compose mounts `./secrets` read-only into the worker and seed; kept out of the `./data` storage tree, and `secrets/` is gitignored).
   - **Deploy:** set `YT_COOKIES_B64=<base64 of cookies.txt>` (no `./secrets` mount there — the worker writes it to a temp file at runtime).

Cookies expire in ~2–3 weeks — re-export when YouTube starts failing again. **Uploads and search are unaffected** — this only touches YouTube fetching.

**Don't want cookies?** You don't have to use them. Cookies are the **free** option (just re-export every few weeks). To skip cookies, use a **residential proxy** instead:

- Set `YT_PROXY_URL=http://user:pass@host:port` (Bright Data, Oxylabs, Smartproxy, IPRoyal…). The real problem is the datacenter IP — a residential IP fixes it with no cookies. Paid, per GB.
- If YouTube still asks for a token, add a PO-token sidecar ([`bgutil-ytdlp-pot-provider`](https://github.com/Brainicism/bgutil-ytdlp-pot-provider)) — free to self-host, no account needed.

**In short:** cookies = free but you re-export them now and then; proxy = costs money but hands-off. Either way you get full video.

> **Model providers & how to set each in env → [MODELS.md](MODELS.md).**
>
> **Running in a container / on a cloud, and bucket + key setup → [Deployment](#deployment) and the guides in [`deployment_docs/`](deployment_docs/).**

## Object storage

`STORAGE_PROVIDER=local` (the default) writes files under `./data` with no bucket and no keys. Switch to a bucket when you deploy — all providers speak the S3 API except `gcp_native`, which uses Google's SDK with a service-account JSON. Creating the bucket, the IAM key / service account, and the required CORS rule is covered per cloud in the deployment guides: **[AWS / S3](deployment_docs/aws.md#s3-bucket)** · **[GCP / GCS](deployment_docs/gcp.md#gcs-bucket)** · **[Fly / Tigris](deployment_docs/fly.md#object-storage-bucket)**.

> Keep the bucket **private** — only presigned URLs get in or out — and set a **CORS rule** allowing PUT from your site's origin, or browser uploads fail.

---

# Architecture

**The design rule: stateful = rented managed service, stateless = this repo.** Every API box and worker is disposable; durable state lives in object storage, Qdrant and Postgres. Two paths scale in opposite directions and never share a request — the **write path** (slow, background; the API answers `202` instantly and workers do the work) and the **read path** (fast; retrieval is milliseconds, the LLM call dominates cost).

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

  %% write path
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

  %% read path
  user -->|"ask"| api
  api -->|"embed query"| clip
  api -->|"kNN · both branches"| qdrant
  api -->|"frames + transcript"| vlm

  classDef repoN fill:#fff3ec,stroke:#e2683c,color:#7a2f14;
  classDef mgmtN fill:#eef4ff,stroke:#3b6ea8,color:#173a63;
  class api,disp,worker,clip repoN;
  class obj,pg,prefect,qdrant,vlm mgmtN;
```

It's **one Docker image** with four entrypoints — the API, the ingest worker, the CLIP service, and a one-shot seed gate. All Python lives under [`src/`](src/).

**Write path — upload to searchable vectors.** The browser presigns (`POST /api/videos/presign`), PUTs the file straight to the bucket, then registers it (`POST /api/videos`); the API HEAD-verifies the object, writes a `pending` row, schedules a Prefect run and returns `202`. A worker then fetches and hashes the source (duplicates are skipped), samples keyframes with one ffmpeg pass, dedups near-identical frames, embeds the survivors and upserts them to the visual collection with deterministic IDs, and transcribes the audio (or reads YouTube captions) into the transcript collection. Poll `GET /api/videos` until `indexed`.

**Read path — question to answer-or-abstain.** `POST /api/ask` embeds the question into **both** branches in parallel (no query router), fuses the hits by rank (RRF), collapses same-instant frame+transcript hits into single moments with a cross-modal boost, and reranks. A **confidence gate** on the raw per-branch bests abstains — with no LLM call — when neither what's on screen nor what's said clears its threshold. Otherwise the top moments' frames and transcript excerpts go to the vision LLM, which answers only from them and cites `[n]`; invented citations are stripped and timestamps come from the payload, never the LLM. `POST /api/sessions/{id}/ask_stream` reports each stage (`embedding → searching → ranking → reading → answering`) over Server-Sent Events.

> Deeper internals — Qdrant at frame scale, "embedding is a URL", fair scheduling (WFQ), the full read path and the stage-by-stage pipeline tables — are in **[ARCHITECTURE.md](ARCHITECTURE.md)**. The HTTP endpoints are in **[API.md](API.md)**.

---

# Deployment

One neutral Docker image with four entrypoints (`api`, `worker`, `clip`, one-shot `seed`) selected by command; nothing about it is tied to a cloud.

- **Fat** (default, `docker build .`) — bundles the local CLIP model; one image runs api + worker + clip and embeds in-process. Simplest deploy.
- **Slim** (`docker build --build-arg WITH_TORCH=false .`) — no local model; api + worker send embedding to a separate CLIP service via `EMBED_SERVICE_URL`. Use it when embedding is the bottleneck or you want CLIP on a GPU (build the service from `Dockerfile.clip`; `fly.slim.toml` runs the slim app with CLIP on an external GPU host).
- **Seed** — a one-shot service indexes the sample talk before `api`/`worker` start, so the first request to `/demo` already has something to answer (`SEED_SAMPLE_VIDEOS=false` skips it).
- **CI/CD** — [`.github/workflows/fly-deploy.yml`](.github/workflows/fly-deploy.yml) deploys on every push to `dev` (needs a `FLY_API_TOKEN` repo secret).

Step-by-step per platform → **[Fly](deployment_docs/fly.md)** · **[AWS](deployment_docs/aws.md)** · **[Google Cloud](deployment_docs/gcp.md)** (index: [DEPLOYMENT.md](DEPLOYMENT.md)).

---

# Security

- **The API is unauthenticated.** Opening the app *is* being logged in as the one account. Anyone who can reach the port can mint upload URLs, ingest, ask and delete. **Bind it to localhost, or put an authenticating proxy** (Cloudflare Access, oauth2-proxy, Fly private networking) in front of any deploy that isn't your own laptop.
- `X-User-Id` and `Authorization` headers are **ignored**, not honoured, so a stale header can't steer reads or writes. Every request acts as `SINGLE_USER_ID` (default `default`).
- `ADMIN_TOKEN` is **not enforced** — setting it changes nothing. `require_auth()` in [src/api/videos.py](src/api/videos.py) is a deliberate no-op, kept as the single place to restore a check. The data model is fully `user_id`-tagged, so restoring real auth means resolving a per-request user id again, not reshaping data.
- Keep the bucket **private** (playback goes out via presigned GETs); the server always generates the object key, never the client. ffmpeg and yt-dlp parse untrusted input — run workers in containers, not on the API box.

---

# License

Apache 2.0.
