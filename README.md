<h1 align="center">MomentSearch</h1>

<p align="center">
  <b>Ask a question about your videos, get the exact moment back —<br>
  matched on what's <i>seen</i> on screen AND what's <i>said</i> in the transcript.</b>
</p>

<p align="center">
  <a href="https://momentsearch.traversaal.ai/"><img alt="Live demo" src="https://img.shields.io/badge/live%20demo-momentsearch.traversaal.ai-E76F51?style=flat-square"></a>
  <img alt="License Apache 2.0" src="https://img.shields.io/badge/license-Apache%202.0-264653?style=flat-square">
  <img alt="Python 3.11" src="https://img.shields.io/badge/python-3.11-2A9D8F?style=flat-square&logo=python&logoColor=white">
  <img alt="Runs with docker compose up" src="https://img.shields.io/badge/run-docker%20compose%20up-2496ED?style=flat-square&logo=docker&logoColor=white">
</p>

<p align="center">
  <a href="#setup">Setup</a> ·
  <a href="#configuration">Configuration</a> ·
  <a href="#architecture">Architecture</a> ·
  <a href="MODELS.md">Models</a> ·
  <a href="API.md">API</a> ·
  <a href="DEPLOYMENT.md">Deployment</a>
</p>

<p align="center">
  <img width="880" alt="Starting on the landing page, opening an empty workspace, pasting a YouTube URL, watching the queued/fetch/frames/embed stages run, then asking a question and getting a cited answer above the matched moments" src="docs/media/demo-upload.gif">
</p>

<p align="center"><sub><b>Empty workspace → a cited answer.</b> Paste a link, watch it index, ask. Indexing is fast-forwarded; the rest runs at ~1.3–1.6× speed.</sub></p>

<p align="center">
  <img width="880" alt="Typing “how does an LLM predict the next word?”, the six pipeline stages ticking over, then a cited answer streaming in above the matched moments" src="docs/media/demo-ask.gif">
</p>

<p align="center"><sub><b>One question → both branches searched → a cited answer.</b> Recorded on the pre-indexed sample, ~1.4× speed.</sub></p>

Upload videos or paste YouTube URLs. Background workers sample keyframes, dedup, embed and index them in [Qdrant](https://qdrant.tech). Ask a question and MomentSearch retrieves the best-matching moments, then has a vision LLM read those frames and write a cited answer — or abstain when the evidence isn't there. It's **visual-first and multimodal**: the frame branch searches what's on screen, the transcript branch searches what's said, and the two are fused into one ranked set of moments.

**What it does:**

- **Presigned uploads** — gigabytes go browser → bucket, never through the API.
- **Dual-branch retrieval** — a visual (frame) branch and a text (transcript) branch, fused, always both.
- **Pluggable models** — the answer LLM and both embedding branches are chosen by provider name; run fully local and keyless, fully hosted, or any mix.
- **Confidence gate** — abstains with no LLM call when neither branch clears its bar, killing most hallucination risk.
- **Speaker recognition** — optional per-video "who said what" via Gemini video understanding.
- **Single-user, no auth** — opening the app is being logged in as the one account.

---

# Every claim is a clickable moment

<p align="center">
  <img width="880" alt="Scrolling the cited moments grid — each card shows the matched keyframe, its timestamp, a match score and seen/said badges — then opening “How this answer was built” to reveal per-stage timings" src="docs/media/demo-moments.gif">
</p>

Every citation in the answer is a **moment**, and every moment carries its receipts:

- the **keyframe** it matched on and the **timestamp** it came from — click to play the video at that exact second, with the transcript scrolling in sync;
- **`seen` / `said` badges** and a **match score**, so you can tell whether the evidence was on screen, in the speech, or both;
- **"How this answer was built"** expands into what each stage actually did — two branches searched in parallel, candidates fused and reranked, the frames the model was allowed to read, and how long each took.

---

# Setup

**Never used Docker or Postgres? Start here.** **Docker** is the only thing you need to install — Postgres, Qdrant, the CLIP model and the app itself all come up *inside* Docker. Five steps, start to finish.

## Step 1 — Install Docker

<details>
<summary><b>🐳 How to install Docker — click for steps</b></summary>

Docker runs the whole stack (app, ingest worker, Postgres, Qdrant) as containers, so nothing gets installed on your machine and nothing to uninstall later. Install **Docker Desktop** — it includes the `docker compose` command this README uses.

**Windows 10/11**

1. Download **Docker Desktop for Windows** → [docker.com/products/docker-desktop](https://www.docker.com/products/docker-desktop/) and run the installer with *"Use WSL 2"* checked (the default).
2. If the installer complains WSL is missing, open **PowerShell as Administrator**, run `wsl --install`, reboot, then re-run the installer.
3. Launch **Docker Desktop** and wait until the whale icon says **"Engine running"**.

**macOS**

1. Download **Docker Desktop for Mac** — pick **Apple silicon** (M1/M2/M3/M4) or **Intel chip**; the wrong one won't start.
2. Open the `.dmg`, drag **Docker** into **Applications**, launch it, and approve the privileged-helper prompt.

**Linux (Ubuntu / Debian)**

```bash
curl -fsSL https://get.docker.com | sh     # installs Docker Engine + compose plugin
sudo usermod -aG docker $USER              # run docker without sudo
newgrp docker                              # or just log out and back in
```

**Check it worked** — both commands must print a version:

```bash
docker --version           # Docker version 24.x or newer
docker compose version     # v2.x  ← a SPACE, not the old `docker-compose`
```

**Two gotchas**

- Docker Desktop must be **running** before `docker compose up`, or you get `cannot connect to the Docker daemon`.
- Give it room: **Settings → Resources → Memory ≥ 4 GB** (8 GB is comfortable — the first build downloads PyTorch).

</details>

<details>
<summary><b>🐘 Postgres — you do NOT need to install it — click to see why</b></summary>

MomentSearch stores its video manifest in Postgres, but with the `.env.local.example` preset **Docker runs Postgres for you**: that file sets `COMPOSE_PROFILES=local-qdrant,local-postgres`, so `docker compose up` starts a `postgres:16` container (and a local Qdrant) and `DATABASE_URL` already points at it. No install, no password to invent, no `createdb`. Same for the vector store.

Want to poke at the data? The container is published on host port **5433** (dodging any native Postgres on 5432):

```bash
psql postgresql://ms:ms@localhost:5433/ms        # or paste this into TablePlus / pgAdmin
```

**Only install Postgres yourself if** you're running the [Without Docker](#without-docker) path, or you want to point at your own database.

- **Managed, zero install (easiest):** create a free Postgres at [neon.com](https://neon.com), paste its connection string into `DATABASE_URL`, and remove `local-postgres` from `COMPOSE_PROFILES`.
- **Windows:** the [EDB installer](https://www.enterprisedb.com/downloads/postgres-postgresql-downloads) — keep port `5432`, and write down the `postgres` password it asks for.
- **macOS:** `brew install postgresql@16 && brew services start postgresql@16`
- **Ubuntu/Debian:** `sudo apt install postgresql && sudo systemctl enable --now postgresql`

Then create the database and point the app at it:

```bash
createdb ms                                                    # macOS/Linux; Windows: use pgAdmin or psql -U postgres
# in .env:
#   DATABASE_URL=postgresql://postgres:YOUR_PASSWORD@localhost:5432/ms
#   COMPOSE_PROFILES=local-qdrant          <- local-postgres removed
```

</details>

## Step 2 — Get the code

**Open a terminal** — **PowerShell** or **Git Bash** on Windows, **Terminal** on macOS, any shell on Linux — and `cd` to wherever you keep projects (`cd ~/code`, or `cd $HOME\Desktop` on Windows). Then:

```bash
git clone https://github.com/traversaal-ai/momentsearch.git   # copy the code down (makes a momentsearch/ folder)
cd momentsearch                                               # step into that folder — every command below runs from here
cp .env.local.example .env                                    # create your config from the local, keyless preset
```

## Step 3 — Add your keys to `.env`

**Open `.env` and add your keys _before_ you start anything** — the app won't index or answer without them. The stack itself stays **on your machine, keyless** (storage, models, Qdrant and Postgres all run locally — `DATABASE_URL` and `QDRANT_URL` come from the `local-postgres` / `local-qdrant` compose profiles). Two keys to fill in:

- **Prefect** (`PREFECT_API_URL` + `PREFECT_API_KEY`) — the ingest queue. **Required — nothing indexes without it.** Free, no card: [app.prefect.cloud](https://app.prefect.cloud) → avatar → API Keys.
- **OpenAI** (`OPENAI_API_KEY`) — for written, cited answers + upload transcription. OpenAI is the simplest default; see [MODELS.md](MODELS.md) to choose another. *Leave it blank and search still works* — you get ranked, clickable moments, just no prose.

## Step 4 — Start the stack

```bash
docker compose up --build                                     # build the image and start everything
# → http://localhost:8000
```

Leave that command **running** — it's the app; `Ctrl+C` stops it, and `docker compose up` (no `--build`) starts it again later.

## Step 5 — Wait for the "UP" banner, then open the app

**First run takes a few minutes** (the first `docker build` also downloads PyTorch). A one-shot `seed` step then downloads the CLIP model and indexes the sample video *before* `api`/`worker` start, so **`http://localhost:8000` won't answer until seeding finishes** — that's expected, not a hang. Watch progress with `docker compose logs -f`; **you'll know it's ready when the logs print:**

```
================================================================
  MomentSearch is UP  ->  open  http://localhost:8000
================================================================
```

Then open **http://localhost:8000**. Later runs find the sample already indexed and start in seconds. No LLM key is fine — retrieval still returns ranked, clickable moments (the UI badge reads "No LLM — moments only"); add `LLM_PROVIDER` + a key when you want prose.

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

**Feature flags:** `ENABLE_TRANSCRIPT` (the transcript branch), `ENABLE_RERANK` (reranker), `MULTI_QUERY` (split a multi-part question and search each part in parallel), `DIARIZE_ENABLED` (speaker recognition master switch), `SEED_SAMPLE_VIDEOS` (index the sample talk on startup).

### YouTube ingest

<details>
<summary><b>⚙️ Only if a YouTube link won't ingest — click to expand</b></summary>

YouTube bot-checks requests **by IP**, so fetching a video — *especially its captions* — can fail with *"Sign in to confirm you're not a bot."* This hits **deploys** (datacenter IP) almost always, and **local** runs increasingly too. (Uploads and search are never affected — this is YouTube-only.)

Pick **one** of the three:

#### Option A — SocialKit key (easiest)

[SocialKit](https://socialkit.dev) is a hosted API that returns **both the transcript and the video** from *its own* servers, so your IP is never the one YouTube blocks — no cookies, no proxy. **Free: 20 credits, then paid.**

1. Get a key at [socialkit.dev](https://socialkit.dev).
2. Paste it into `.env` — it's used automatically for both branches, with yt-dlp as the fallback:
   ```
   SOCIALKIT_API_KEY=your_key_here
   ```

That's it. Docs: [docs.socialkit.dev](https://docs.socialkit.dev).

#### Option B — Cookies (free)

Authenticate yt-dlp with cookies from a logged-in browser — **free**, but they expire every ~2–3 weeks.

1. **Get them** — install a "Get cookies.txt" browser extension (e.g. *Get cookies.txt LOCALLY*), open `youtube.com` while signed in, and export a **Netscape-format `cookies.txt`**.
2. **Add them:**
   - **Local:** save it to `./secrets/cookies.txt` and set `YT_COOKIES_FILE=/app/secrets/cookies.txt` (compose mounts `./secrets` read-only; it's gitignored).
   - **Deploy:** set `YT_COOKIES_B64=<base64 of cookies.txt>` (no `./secrets` mount there — the worker writes it to a temp file at runtime).

Re-export when YouTube starts failing again.

#### Option C — Residential proxy (paid, hands-off)

A residential IP gets past the datacenter-IP block with no cookies to refresh — set `YT_PROXY_URL=http://user:pass@host:port` (Bright Data, Oxylabs, IPRoyal…). Paid, per GB. If YouTube still asks for a token, add a free PO-token sidecar ([`bgutil-ytdlp-pot-provider`](https://github.com/Brainicism/bgutil-ytdlp-pot-provider)) — free to self-host, no account.

**In short:** SocialKit = 20 free credits then paid, zero setup, covers transcript **and** video · cookies = free but re-exported every few weeks · proxy = paid but hands-off.

</details>

> **Model providers & how to set each in env → [MODELS.md](MODELS.md).**
>
> **Running in a container / on a cloud, and bucket + key setup → [Deployment](#deployment) and the guides in [`deployment_docs/`](deployment_docs/).**

## Object storage

`STORAGE_PROVIDER=local` (the default) writes files under `./data` with no bucket and no keys. Switch to a bucket when you deploy — all providers speak the S3 API except `gcp_native`, which uses Google's SDK with a service-account JSON. Creating the bucket, the IAM key / service account, and the required CORS rule is covered per cloud in the deployment guides: **[AWS / S3](deployment_docs/aws.md#object-storage)** · **[GCP / GCS](deployment_docs/gcp.md#object-storage)** · **[Fly / Tigris](deployment_docs/fly.md#step-5--make-the-storage-bucket)**. Each of those sections documents all three providers, so any cloud can use any bucket.

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
