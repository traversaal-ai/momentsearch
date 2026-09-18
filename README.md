<h1 align="center">MomentSearch</h1>

<p align="center">
  <b>Find the moment you need in your videos.</b><br>
  Ask a question in plain words. MomentSearch looks at what appears <i>on screen</i> and what people <i>say</i>,<br>
  then shows you the exact timestamps, with a written answer that links back to them.
</p>

<p align="center">
  <a href="https://momentsearch.traversaal.ai/"><img alt="Live demo" src="https://img.shields.io/badge/live%20demo-momentsearch.traversaal.ai-E76F51?style=flat-square"></a>
  <img alt="License Apache 2.0" src="https://img.shields.io/badge/license-Apache%202.0-264653?style=flat-square">
  <img alt="Python 3.11" src="https://img.shields.io/badge/python-3.11-2A9D8F?style=flat-square&logo=python&logoColor=white">
  <img alt="Runs with docker compose up" src="https://img.shields.io/badge/run-docker%20compose%20up-2496ED?style=flat-square&logo=docker&logoColor=white">
</p>

<p align="center">
  <a href="#try-it-first">Try it</a> ·
  <a href="#run-it-on-your-computer">Run it on your computer</a> ·
  <a href="#add-your-own-videos">Add your own videos</a> ·
  <a href="#having-trouble">Having trouble?</a> ·
  <a href="#for-developers">For developers</a>
</p>

<p align="center">
  <img width="880" alt="Starting on the landing page, opening an empty workspace, pasting a YouTube URL, watching the queued/fetch/frames/embed stages run, then asking a question and getting a cited answer above the matched moments" src="docs/media/demo-upload.gif">
</p>

<p align="center"><sub><b>From an empty workspace to a cited answer.</b> Paste a link, watch it get indexed, ask a question. Indexing is fast-forwarded; the rest runs at about 1.5× speed.</sub></p>

<p align="center">
  <img width="880" alt="Typing “how does an LLM predict the next word?”, the six pipeline stages ticking over, then a cited answer streaming in above the matched moments" src="docs/media/demo-ask.gif">
</p>

<p align="center"><sub><b>One question, one answer, every claim linked to a moment.</b> Recorded on the built-in sample videos, about 1.4× speed.</sub></p>

---

# What is MomentSearch?

MomentSearch is a search engine for the inside of videos. You add videos, either by uploading a file or pasting a YouTube link. It watches them once, remembers what was shown and what was said, and from then on you can ask questions like *"where does she explain the pricing change?"* or *"show me the chart with the red line"* and jump straight to that second of the video.

Use it to find an explanation in a lecture, revisit a chart in a recorded presentation, jump to one step of a product walkthrough, or check what was actually said in a long meeting recording.

**What can I do with it?**

- **Find moments by describing what you remember** seeing or hearing. Both are searched every time.
- **Click a result to watch** the video from that exact point, with the transcript scrolling alongside.
- **Get a written answer** with numbered links to the moments it came from. If the videos don't contain the answer, it says so instead of guessing.
- **Search your own videos**, uploaded from your computer or added through YouTube links.
- **Ask a multi-part question** ("what is X, and how does it differ from Y?") and it searches each part separately, then answers both.
- **See who said what**, optionally, for videos with several speakers.

Some features use an outside service, such as OpenAI for the written answers. The setup below tells you which features need what, and everything works without a paid service if you only want to search.

---

# Try it first

**Open the [live demo](https://momentsearch.traversaal.ai/)** to try MomentSearch in your browser. Nothing to install.

If you would rather run your own copy, follow [Run it on your computer](#run-it-on-your-computer). Your copy comes with **ten sample videos already prepared**: the 3Blue1Brown deep-learning series (neural networks, gradient descent, transformers, attention, how large language models store facts) plus a guest video by Welch Labs on how AI images and videos are made. You can search them the moment the app starts, before adding anything of your own.

---

# Every claim is a clickable moment

<p align="center">
  <img width="880" alt="Scrolling the cited moments grid — each card shows the matched keyframe, its timestamp, a match score and seen/said badges — then opening “How this answer was built” to reveal per-stage timings" src="docs/media/demo-moments.gif">
</p>

Every numbered link in an answer is a **moment**, and every moment shows its evidence:

- the **picture** it matched on and the **timestamp** it came from. Click it to play the video at that exact second;
- a **`seen` / `said` badge** and a **match score**, so you can tell whether the evidence was on screen, in the speech, or both;
- **"How this answer was built"** opens up to show what happened behind the scenes: which searches ran, how the results were combined, which pictures the AI was allowed to look at, and how long each step took.

---

# Run it on your computer

## What you'll need

Two programs, both free:

- **[Docker Desktop](https://www.docker.com/products/docker-desktop/)** runs the app and everything it depends on in one go. You do not need to install Python, a database, or anything else separately.
- **[Git](https://git-scm.com/downloads)** downloads the project files.

You will paste a few commands into a terminal and edit one settings file with a text editor. That's it.

**Decide what you want to turn on.** Everything below works with the sample videos; the right column tells you what each level needs.

| I want to... | What it needs |
|---|---|
| Search **what is shown on screen** in the sample videos, and click through to the moments | **Nothing.** No accounts, no keys. |
| Also search **what is said**, and get **written answers** | An **OpenAI API key**. Pay-as-you-go; a question costs a fraction of a cent. |
| **Add my own videos** (uploads or YouTube links) | A free **Prefect Cloud** account. It runs the queue that processes new videos. |

> **What is an API key?** A private access code, like a password, that lets MomentSearch use a service through your account. You paste it into the settings file once. Never share it or post it online.

## Step 1: Install Docker and Git

<details>
<summary><b>🐳 How to install Docker Desktop (click for steps)</b></summary>

**Windows 10/11**

1. Download **Docker Desktop for Windows** from [docker.com/products/docker-desktop](https://www.docker.com/products/docker-desktop/) and run the installer. Leave *"Use WSL 2"* ticked.
2. If the installer says WSL is missing: open **PowerShell as Administrator**, run `wsl --install`, restart your computer, then run the installer again.
3. Open **Docker Desktop** and wait until the whale icon in the taskbar says **"Engine running"**.

**macOS**

1. Download **Docker Desktop for Mac**. Choose **Apple silicon** (M1, M2, M3, M4) or **Intel chip** to match your Mac. The wrong one won't start.
2. Open the downloaded file, drag **Docker** into **Applications**, open it, and approve the permission prompt.

**Linux (Ubuntu / Debian)**

```bash
curl -fsSL https://get.docker.com | sh     # installs Docker and the compose plugin
sudo usermod -aG docker $USER              # lets you run docker without sudo
newgrp docker                              # or log out and back in
```

**Two things that trip people up**

- Docker Desktop must be **open and running** before you start the app, or you will see `cannot connect to the Docker daemon`.
- Give it enough memory: **Settings → Resources → Memory, at least 4 GB** (8 GB is comfortable). The first start downloads about 2 GB of AI model files.

</details>

<details>
<summary><b>🐘 Do I need to install a database? No. (click to see why)</b></summary>

MomentSearch keeps its list of videos in a Postgres database, and Docker runs that database for you. The settings file you copy in Step 3 already points at it. No install, no password to invent.

Curious and want to look inside? It is reachable on your computer at port **5433**:

```bash
psql postgresql://ms:ms@localhost:5433/ms        # or paste this into TablePlus / pgAdmin
```

You only need your own database if you follow the [Without Docker](#without-docker) path, or if you prefer a hosted one. A free [Neon](https://neon.com) database works: paste its connection string into `DATABASE_URL` and remove `local-postgres` from `COMPOSE_PROFILES` in your settings file.

</details>

**Check it worked.** Open a terminal: **PowerShell** on Windows, **Terminal** on macOS or Linux. Run these one at a time:

```bash
git --version
docker --version
docker compose version
```

Each should print a version number. If one says the command is not found, finish installing that tool and open a **new** terminal window.

## Step 2: Download the project

These commands download MomentSearch into a folder called `momentsearch`:

```bash
git clone https://github.com/traversaal-ai/momentsearch.git
cd momentsearch
```

Run every command from here on inside that folder.

## Step 3: Create your settings file

The project ships a ready-made local preset. Copy it to a file named `.env` and open it in an editor. Use the commands for your system.

**Windows (PowerShell):**

```powershell
Copy-Item .env.local.example .env
notepad .env
```

**macOS:**

```bash
cp .env.local.example .env
open -e .env
```

**Linux:**

```bash
cp .env.local.example .env
nano .env
```

The file is called `.env`, with the dot and nothing after the name. Keep that exact name when you save.

## Step 4: Choose what to turn on

The file is full of comments explaining each line. You only need to touch these.

**Option A: no keys, search what's on screen**

Find the line `ENABLE_TRANSCRIPT=true` and change it to:

```
ENABLE_TRANSCRIPT=false
```

Leave `OPENAI_API_KEY`, `PREFECT_API_URL` and `PREFECT_API_KEY` empty. You can search the sample videos by what appears on screen and click through to the moments. There is no spoken-word search and no written answer at this level.

**Option B: search speech and get written answers**

1. Create a key on the [OpenAI API keys page](https://platform.openai.com/api-keys). It starts with `sk-`.
2. In `.env`, keep `ENABLE_TRANSCRIPT=true` and paste the key:

```
OPENAI_API_KEY=sk-paste_your_key_here
```

One key covers everything OpenAI does here: written answers, spoken-word search, and transcribing videos you upload later. Leave the Prefect lines empty for now.

**Option C: add your own videos too.** You can do this later; see [Add your own videos](#add-your-own-videos).

Save the file and close the editor. In Nano, press `Ctrl+O`, `Enter`, then `Ctrl+X`.

## Step 5: Start the app

```bash
docker compose up --build
```

The first start takes a few minutes: it downloads the app's dependencies and the AI model that reads pictures. Leave the terminal open. You'll see a lot of log lines scroll by; that's normal. **It is ready when you see this:**

```
================================================================
  MomentSearch is UP  ->  open  http://localhost:8000
================================================================
```

Open **http://localhost:8000** in your browser. Later starts take under a minute.

> If you skipped the Prefect keys, one part of the app (the `worker`) will keep printing that it cannot connect and will retry. That is harmless. It only matters once you add your own videos.

## Step 6: Try your first search

Open **http://localhost:8000/demo**. Type:

```
an animation of a neural network
```

and press **Ask**. You get result cards, each with a picture from the video and a timestamp. Click one to watch the video from that point.

If you added an OpenAI key, also try:

```
How does a language model predict the next word?
```

Now a written answer appears above the cards, with numbered links like `[1]` `[2]` that point to the moments it used. Open **"How this answer was built"** under the answer to see what happened at each step.

## Stopping and starting again

- **Stop:** press `Ctrl+C` in the terminal where the app is running.
- **Start again:** open a terminal in the `momentsearch` folder and run `docker compose up` (no `--build` needed).
- **Changed `.env`?** Stop the app and start it again. The new settings apply on the next start.

---

# Add your own videos

Adding videos needs a free **Prefect Cloud** account. Prefect runs the queue that processes each video in the background, so the app stays responsive while a long video is being indexed. No card is required.

1. Sign in at [app.prefect.cloud](https://app.prefect.cloud) and open (or create) a workspace.
2. Click your avatar, then **API Keys**, then **Create API Key**. Copy it.
3. Look at your browser's address bar while inside the workspace. It contains two long IDs, one after `/account/` and one after `/workspace/`.
4. In `.env`, fill in the two Prefect lines:

```
PREFECT_API_KEY=paste_your_prefect_key_here
PREFECT_API_URL=https://api.prefect.cloud/api/accounts/ACCOUNT_ID/workspaces/WORKSPACE_ID
```

Replace `ACCOUNT_ID` and `WORKSPACE_ID` with the two IDs from your address bar. Note that the URL says `accounts` and `workspaces` (plural), unlike the address bar.

5. To have uploaded videos transcribed and searchable by speech, also add your OpenAI key and keep `ENABLE_TRANSCRIPT=true`. YouTube videos use their own captions.
6. Save `.env`, stop the app with `Ctrl+C`, and start it again:

```bash
docker compose up
```

Once the "MomentSearch is UP" banner appears:

1. Open **http://localhost:8000/app**.
2. Choose **Upload your own file** or **Paste a YouTube link**. If you already have videos, choose **Add videos**.
3. Pick your file, or paste the link and press **Add**.
4. Watch the progress steps (fetch, frames, embed, transcript). A ten-minute video takes a couple of minutes on a laptop.
5. Ask a question about it.

Videos you upload stay on your computer, under the `data` folder. Some YouTube links cannot be downloaded because YouTube blocks automated access from certain networks. If a link fails, try uploading a file to check that processing works, then see the YouTube box below.

<details>
<summary><b>⚙️ A YouTube link won't process? (click to expand)</b></summary>

YouTube checks where requests come from and sometimes refuses with *"Sign in to confirm you're not a bot."* This happens almost always on cloud servers and increasingly on home connections too. Uploads and search are never affected. Pick **one** fix:

**Option A: SocialKit key (easiest)**

[SocialKit](https://socialkit.dev) is a service that fetches both the video and its captions from its own servers, so your connection is never the one YouTube blocks. Free for the first 20 videos, then paid.

1. Get a key at [socialkit.dev](https://socialkit.dev).
2. Add it to `.env`. It is used automatically, with the normal downloader as a fallback:
   ```
   SOCIALKIT_API_KEY=your_key_here
   ```

**Option B: Cookies (free, expires every few weeks)**

Lend the downloader your own logged-in YouTube session.

1. Install a browser extension such as *Get cookies.txt LOCALLY*, open `youtube.com` while signed in, and export a `cookies.txt` file in Netscape format.
2. Save it as `secrets/cookies.txt` inside the `momentsearch` folder and add to `.env`:
   ```
   YT_COOKIES_FILE=/app/secrets/cookies.txt
   ```
   On a cloud deploy there is no `secrets` folder; set `YT_COOKIES_B64` to the base64 of the file instead.

Re-export the file when YouTube starts failing again, usually every two to three weeks.

**Option C: Residential proxy (paid, hands-off)**

Set `YT_PROXY_URL=http://user:pass@host:port` with a residential proxy provider (Bright Data, Oxylabs, IPRoyal and similar). Paid per GB, nothing to refresh. If YouTube still asks for a token, add the free [`bgutil-ytdlp-pot-provider`](https://github.com/Brainicism/bgutil-ytdlp-pot-provider) sidecar.

</details>

---

# Where does my data go?

- **With the local settings, your videos stay on your computer.** Uploaded files, the pictures taken from them, and the search index live in the `data` and `demo_corpus` folders inside the project.
- **If you turn on a hosted AI service, some content is sent to it.** With an OpenAI key, that means the text of your question, the transcript excerpts and a few selected video pictures for each answer, and the audio of uploaded videos for transcription. Prefect receives only job status, never your video.
- **There is no sign-in.** Anyone who can open the app in a browser is using your account and can add, search and delete videos. On your own computer that is fine. Do not put it on a shared network or the public internet without an authenticating proxy in front of it. Details for developers are in [Security](#security).

---

# Having trouble?

- **"cannot connect to the Docker daemon":** open Docker Desktop, wait for "Engine running", then try again.
- **The page won't open:** keep the terminal running and wait for the "MomentSearch is UP" banner. Open the address on the same computer that is running the app.
- **An error mentions a missing OpenAI key** and you didn't add one: make sure `ENABLE_TRANSCRIPT=false` in `.env`, save, stop and start the app.
- **Results appear but there is no written answer:** expected without an OpenAI key. The badge in the corner reads "No LLM — moments only".
- **My own video never starts processing:** check both Prefect lines in `.env`, then stop and start the app. The `worker` log lines should say `serving deployment 'ms-ingest-video/ingest'`.
- **A YouTube link fails:** try uploading a file first to confirm processing works, then see the YouTube box above.
- **A key "isn't working":** run the built-in check. It tells you which setting each key came from and flags typos:
  ```bash
  docker compose exec api python -m src.providers          # what your .env resolved to
  docker compose exec api python -m src.providers --live   # actually call every configured service
  ```

For anything else, open a second terminal in the `momentsearch` folder and run:

```bash
docker compose logs --tail=80 api seed clip worker
```

Then open a [GitHub issue](https://github.com/traversaal-ai/momentsearch/issues) with the error and your operating system. Remove keys and private content from anything you paste, and never attach your `.env` file.

---

# Use it from your own code or UI

Everything the app does goes through a plain HTTP API on the same port: add a video, check its status, ask a question (with a streaming variant), read the cited moments. **[API.md](API.md)** documents every endpoint with its request and response fields, which source file owns it, and a short guide to building your own frontend or scripting a workflow against it. A live Swagger page is at `http://localhost:8000/docs` while the app runs.

---

# For developers

Everything below is for people who want to run MomentSearch without Docker, change its settings, understand how it works inside, or deploy it.

- **[API.md](API.md)**: every endpoint, its fields, and how to drive the app from your own UI.
- **[MODELS.md](MODELS.md)**: every supported model provider and how to set each one in `.env`.
- **[ARCHITECTURE.md](ARCHITECTURE.md)**: how indexing and search work step by step.
- **[DEPLOYMENT.md](DEPLOYMENT.md)**: running it on Fly, AWS or Google Cloud.
- **[CONTRIBUTING.md](CONTRIBUTING.md)**: how to propose changes.
- **[`.env.example`](.env.example)**: the full, commented list of every setting.

## Without Docker

Use this if you'd rather run plain `python` (hacking on the code, or you already run Postgres and Qdrant). Docker does all of the below for you; here you provide the pieces yourself.

**1. Install the system pieces**

- **Python 3.11+**
- **ffmpeg** and **Node.js** on your `PATH`. ffmpeg samples the frames; yt-dlp needs Node to read YouTube.
  - Windows: `winget install Gyan.FFmpeg` then `winget install OpenJS.NodeJS.LTS` · macOS: `brew install ffmpeg node` · Ubuntu/Debian: `sudo apt install ffmpeg nodejs`
- **Postgres**: a free [Neon](https://neon.com) database (nothing to install), or a local one. Windows: the [EDB installer](https://www.enterprisedb.com/downloads/postgres-postgresql-downloads); macOS: `brew install postgresql@16 && brew services start postgresql@16`; Ubuntu/Debian: `sudo apt install postgresql`. Then `createdb ms`.
- **Qdrant**: a free [Qdrant Cloud](https://cloud.qdrant.io) cluster (copy its URL and API key), or a local binary from [Qdrant's releases](https://github.com/qdrant/qdrant/releases) run as `./qdrant` (listens on `6333`).

**2. Get the code and install the Python deps**

```bash
git clone https://github.com/traversaal-ai/momentsearch.git
cd momentsearch
cp .env.local.example .env
python -m venv .venv && source .venv/bin/activate            # Windows PowerShell: .venv\Scripts\Activate.ps1
pip install -r requirements.txt
pip install torch --index-url https://download.pytorch.org/whl/cpu   # CPU torch for the local CLIP model
pip install -r requirements-clip.txt
```

**3. Point `.env` at your services.** The preset uses Docker hostnames (`postgres`, `qdrant`); replace them, and add the two `DEMO_*` lines so the demo corpus has somewhere to load its vectors:

```bash
DATABASE_URL=postgresql://postgres:YOUR_PASSWORD@localhost:5432/ms   # or your Neon URL
QDRANT_URL=http://localhost:6333                                     # or your Qdrant Cloud URL
QDRANT_API_KEY=                                                      # your cloud key, if any
DEMO_QDRANT_URL=http://localhost:6333                                # same value as QDRANT_URL
DEMO_QDRANT_API_KEY=                                                 # same value as QDRANT_API_KEY
```

Then add the Prefect and OpenAI keys as in Step 4 above. Leave `EMBED_SERVICE_URL` unset so CLIP runs inside the API and worker processes; `COMPOSE_PROFILES` is ignored outside Docker, and you can comment out `DEPLOY_ENV` to silence the local-settings warning at startup.

**4. Start it**, from the repo folder with the venv active, each in its own terminal, in this order:

```bash
python -m src.seed                 # once per fresh database: loads the ten demo videos (rows + vectors), then exits
uvicorn src.app:app --port 8000    # API + UI  ->  http://localhost:8000
python -m src.worker               # ingest worker: needs the Prefect key, only for adding your own videos
```

The first start downloads the CLIP model (~1.7GB) and the reranker (~90MB) into your Hugging Face cache; later starts are quick. Open **http://localhost:8000**. The ten demo videos answer straight away, and `python -m src.providers` tells you what your `.env` actually resolved to if something is off.

**5. Optional: CLIP as its own process.** So the API and worker don't each load the model, run `uvicorn src.clip_service:app --port 8001` in a fourth terminal and set `EMBED_SERVICE_URL=http://localhost:8001` in `.env`.

## Configuration

`.env.example` is the full, inline-documented reference; `.env.local.example` is the keyless local preset the steps above use. The key variables:

| Variable | What it is |
|---|---|
| `STORAGE_PROVIDER` | where raw videos + thumbnails live: `local` (default) · `aws` · `gcp` · `gcp_native` · `flyio`. See [Object storage](#object-storage). |
| `DATABASE_URL` | Postgres connection: the video manifest, status and sessions. |
| `QDRANT_URL` + `QDRANT_API_KEY` | the vector index. Required; ingest and search both fail without one. |
| `IMAGE_COLLECTION` / `TEXT_COLLECTION` | the frame and transcript collections. One name each, read from these two variables and nowhere else. The text collection holds both the dense and the BM25 vector of every chunk. A new embedding model needs a fresh collection name. |
| `PREFECT_API_URL` + `PREFECT_API_KEY` | the ingest queue between the API and the workers. |
| `LLM_PROVIDER` | the answer LLM (vision-capable). Blank = retrieval-only. |
| `IMAGE_EMBED_PROVIDER` | frame embeddings, the visual branch (default local `clip`). |
| `TEXT_EMBED_PROVIDER` | transcript embeddings, the text branch (default `openai`). |
| `RERANK_PROVIDER` | cross-encoder reranker over transcript hits (default local `fastembed`). |
| `ASR_PROVIDER` | speech-to-text for uploaded videos (default `openai` / Whisper). |
| `GEMINI_API_KEY` | required for speaker recognition ("who said what"). |

**Feature flags:** `ENABLE_TRANSCRIPT` (the transcript branch), `ENABLE_HYBRID` (transcript search is dense + BM25, so exact words are findable), `ENABLE_RERANK` (reranker), `MULTI_QUERY` (split a multi-part question and search each part in parallel), `DIARIZE_ENABLED` (speaker recognition master switch), `DEMO_LOCAL` (read the ten demo videos from `demo_corpus/`; off means host them yourself, which re-indexes them).

> **Model providers and how to set each in env: [MODELS.md](MODELS.md).** Running in a container or on a cloud, bucket and key setup: [Deployment](#deployment) and the guides in [`deployment_docs/`](deployment_docs/).

## Object storage

`STORAGE_PROVIDER=local` (the default) writes files under `./data` with no bucket and no keys. Switch to a bucket when you deploy. All providers speak the S3 API except `gcp_native`, which uses Google's SDK with a service-account JSON. Creating the bucket, the IAM key or service account, and the required CORS rule is covered per cloud in the deployment guides: **[AWS / S3](deployment_docs/aws.md#object-storage)** · **[GCP / GCS](deployment_docs/gcp.md#object-storage)** · **[Fly / Tigris](deployment_docs/fly.md#step-5--make-the-storage-bucket)**. Each of those sections documents all three providers, so any cloud can use any bucket.

> Keep the bucket **private** (only presigned URLs get in or out) and set a **CORS rule** allowing PUT from your site's origin, or browser uploads fail.

## Architecture

**The design rule: stateful = rented managed service, stateless = this repo.** Every API box and worker is disposable; durable state lives in object storage, Qdrant and Postgres. One deliberate exception: the ten-video demo corpus ships *in the repo*, pre-indexed, with its own bundled Qdrant, so a fresh clone answers questions before anything is uploaded or paid for. Two paths scale in opposite directions and never share a request: the **write path** (slow, background; the API answers `202` instantly and workers do the work) and the **read path** (fast; retrieval is a couple of seconds and free, the LLM call is the paid step).

```mermaid
flowchart LR
  user(["Browser / UI"])

  yt(["YouTube"])

  subgraph repo["MomentSearch — one Docker image (this repo)"]
    direction TB
    api["API<br/>presign · register · /ask · UI"]
    disp["WFQ dispatcher<br/>fair round-robin across users"]
    worker["Worker(s)<br/>fetch · sample · dedup · embed · transcript · speakers"]
    clip["CLIP service<br/>one warm model (CPU → GPU)"]
    gate["Startup gate (seed)<br/>restores the demo corpus, then exits"]
    demo[("demo_corpus/ + bundled Qdrant<br/>10 videos pre-indexed, shipped")]
  end

  subgraph managed["Managed services — stateful (rented)"]
    direction TB
    obj[("Object storage<br/>S3 / GCS / Tigris")]
    pg[("Neon Postgres<br/>manifest · status · sessions")]
    prefect[("Prefect Cloud<br/>queue · retries · dashboard")]
    qdrant[("Qdrant Cloud<br/>moments_l14 · moments_text_openai (dense + BM25)")]
    vlm[("Vision LLM<br/>OpenAI · Gemini · Anthropic · vLLM")]
  end

  %% write path
  user -->|"① presign"| api
  user -->|"② PUT bytes"| obj
  user -->|"③ register: file or YouTube URL"| api
  api -->|"pending row"| pg
  api -->|"enqueue"| disp
  disp -->|"admit ≤ MAX_INFLIGHT"| prefect
  prefect -->|"run"| worker
  yt -->|"SocialKit API or yt-dlp"| worker
  worker -->|"download · thumbs · transcript.json"| obj
  worker -->|"embed batches"| clip
  worker -->|"upsert vectors"| qdrant
  worker -->|"status"| pg

  %% demo corpus
  gate -->|"rows"| pg
  gate -->|"vectors, once"| demo

  %% read path
  user -->|"ask"| api
  api -->|"split? · embed query"| clip
  api -->|"kNN · frames + dense text + BM25"| qdrant
  api -->|"samples' kNN + frames"| demo
  api -->|"moments + context"| vlm

  classDef repoN fill:#fff3ec,stroke:#e2683c,color:#7a2f14;
  classDef mgmtN fill:#eef4ff,stroke:#3b6ea8,color:#173a63;
  class api,disp,worker,clip,gate,demo repoN;
  class obj,pg,prefect,qdrant,vlm mgmtN;
```

It's **one Docker image** with four entrypoints: the API, the ingest worker, the CLIP service, and a one-shot startup gate. All Python lives under [`src/`](src/).

**Write path, upload to searchable vectors.** The browser presigns (`POST /api/videos/presign`), PUTs the file straight to the bucket, then registers it (`POST /api/videos`, which also takes a YouTube URL); the API HEAD-verifies the object, writes a `pending` row, hands it to the fair dispatcher and returns `202`. A worker then fetches the source (YouTube via the SocialKit API or yt-dlp) and hashes it (duplicates are skipped), samples keyframes with one ffmpeg pass, dedups near-identical frames, embeds the survivors and upserts them to the visual collection with deterministic IDs, then indexes the speech: YouTube captions or Whisper for uploads, optionally labelled by speaker with Gemini, in ~20s chunks in the transcript collection. Poll `GET /api/videos` until `indexed`.

**Read path, question to answer-or-abstain.** `POST /api/ask` first asks the answer model, in parallel with retrieval, whether the question has several parts, and splits it if so (each part is then retrieved in parallel). It embeds the question into **both** branches (no query router), fuses the hits by rank (RRF), collapses same-instant frame+transcript hits into single moments with a cross-modal boost, and reranks; the transcript branch is itself **hybrid**: a dense embedding and a BM25 keyword search over the same chunks, merged by rank, so an exact name, number or identifier is found even when the embedding blurs it. A **confidence gate** on the raw per-branch bests abstains, with no LLM call, when neither what's on screen nor what's said clears its threshold. Otherwise the top moments' frames, transcript excerpts and the speech around each go to the vision LLM, which answers every part only from them and cites `[n]`; invented citations are stripped and timestamps come from the payload, never the LLM. `POST /api/ask_stream` and its session twin report each stage (`embedding → searching → ranking → [splitting → parts] → reading → answering`) over Server-Sent Events.

> Deeper internals (Qdrant at frame scale, "embedding is a URL", fair scheduling, the full read path and the stage-by-stage pipeline tables) are in **[ARCHITECTURE.md](ARCHITECTURE.md)**. Every HTTP endpoint, its fields, and how to drive the app from your own UI or scripts are in **[API.md](API.md)**.

## Deployment

One neutral Docker image with four entrypoints (`api`, `worker`, `clip`, one-shot `seed`) selected by command; nothing about it is tied to a cloud.

- **Fat** (default, `docker build .`): bundles the local CLIP model; one image runs api + worker + clip and embeds in-process. Simplest deploy.
- **Slim** (`docker build --build-arg WITH_TORCH=false .`): no local model; api + worker send embedding to a separate CLIP service via `EMBED_SERVICE_URL`. Use it when embedding is the bottleneck or you want CLIP on a GPU (build the service from `Dockerfile.clip`; `fly.slim.toml` runs the slim app with CLIP on an external GPU host).
- **Seed**: a one-shot service loads the shipped demo corpus before `api`/`worker` start, so the first request to `/demo` already has ten videos to answer from. It indexes nothing (`SEED_MODE=ingest` if you want it to).

Step-by-step per platform: **[Fly](deployment_docs/fly.md)** · **[AWS](deployment_docs/aws.md)** · **[Google Cloud](deployment_docs/gcp.md)** (index: [DEPLOYMENT.md](DEPLOYMENT.md)).

## Security

- **The API is unauthenticated.** Opening the app *is* being logged in as the one account. Anyone who can reach the port can mint upload URLs, ingest, ask and delete. **Bind it to localhost, or put an authenticating proxy** (Cloudflare Access, oauth2-proxy, Fly private networking) in front of any deploy that isn't your own laptop.
- `X-User-Id` and `Authorization` headers are **ignored**, not honoured, so a stale header can't steer reads or writes. Every request acts as `SINGLE_USER_ID` (default `default`).
- `ADMIN_TOKEN` is **not enforced**; setting it changes nothing. `require_auth()` in [src/api/videos.py](src/api/videos.py) is a deliberate no-op, kept as the single place to restore a check. The data model is fully `user_id`-tagged, so restoring real auth means resolving a per-request user id again, not reshaping data.
- Keep the bucket **private** (playback goes out via presigned GETs); the server always generates the object key, never the client. ffmpeg and yt-dlp parse untrusted input, so run workers in containers, not on the API box.

---

# License

MomentSearch is open source under the [Apache 2.0](LICENSE) license.
