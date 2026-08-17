# Deploying MomentSearch

MomentSearch is **one Docker image** that runs four entrypoints by command — `api`, `worker`, `clip`, and a one-shot `seed`. It deploys anywhere Docker runs. Three steps: **pick the image → set your `.env` → deploy on your cloud.**

> On **Fly you deploy the fat image** (the default) — one image runs everything. "Slim" below is only for a GPU split elsewhere; most deploys never use it.

---

## 1. Two images: fat or slim

There are two builds of that image. **The difference is only whether the CLIP model is inside it:**

| Image | Build command | What it is | Use it when |
|---|---|---|---|
| **Fat** (default) | `docker build .` | The CLIP model is **inside** — one image runs api + worker + clip and embeds itself. | **Almost always.** One box, nothing else to run. |
| **Slim** | `docker build --build-arg WITH_TORCH=false .` | **No** model — api + worker send embedding to a **separate CLIP service** (built from `Dockerfile.clip`) via `EMBED_SERVICE_URL`. | Only for a **GPU** CLIP, or when embedding is your bottleneck. |

**If you're not sure, use fat.** Slim is an advanced split (mainly to put CLIP on a GPU) — the cloud guides cover it in their GPU sections.

---

## 2. The keys every deploy needs

**Start from [`.env.example`](.env.example)** — `cp .env.example .env`, then adjust the variables as your deploy needs. It's the **full reference**, with every adjustable option documented inline. **Do not deploy from `.env.local.example`** — that's the keyless *local* preset and leaves out a lot a real deploy needs (a cloud bucket, `EMBED_SERVICE_URL`, `DEPLOY_ENV`, and more).

Sign up for these and put the values in your `.env` **before** deploying. They're the **same on every cloud**:

| What | Env var(s) | Where to get it |
|---|---|---|
| **Database** (Postgres) | `DATABASE_URL` | [neon.tech](https://neon.tech) → create a project → copy the **Pooled** connection string. |
| **Vector store** (Qdrant) | `QDRANT_URL` + `QDRANT_API_KEY` | [cloud.qdrant.io](https://cloud.qdrant.io) → create a cluster → copy its URL + API key. |
| **Work queue** (Prefect) | `PREFECT_API_URL` + `PREFECT_API_KEY` | [app.prefect.cloud](https://app.prefect.cloud) → avatar → API Keys (free, no card). |
| **Answer model** (OpenAI) | `OPENAI_API_KEY` | [platform.openai.com/api-keys](https://platform.openai.com/api-keys). |
| **Reranker** — *optional* | none by default | Runs locally (`fastembed`), **no key**. Only Cohere needs `RERANK_API_KEY` → [MODELS.md](MODELS.md). |
| **Speaker recognition** — *optional* | `GEMINI_API_KEY` | [aistudio.google.com/apikey](https://aistudio.google.com/apikey) — for "who said what". |

That OpenAI key covers the **written answer**, **transcript embeddings**, and **upload speech-to-text**. The **visual/frame embedder** (CLIP) also runs **locally, no key**. The reranker and speaker recognition are the two optional rows above.

> **Want different models?** To swap the answer LLM, either embedder, or the reranker (e.g. Cohere instead of local) — or to run fully local with no keys — see **[MODELS.md](MODELS.md)**, which lists every provider and its exact env var.

> No OpenAI key? The app still runs — you get ranked, clickable moments, just no written answer.

**Storage** is the one thing that changes per cloud — you set it up **in the deploy guide** (Step 3), which walks you through the bucket + keys + CORS.

---

## 3. Deploy — pick your platform

Each guide sets up that cloud's **storage** first, then **deploys** the app step by step.

| Platform | Guide | Storage it uses |
|---|---|---|
| **Fly.io** (easiest) | **[fly.md](deployment_docs/fly.md)** | Tigris — one command |
| **AWS** | **[aws.md](deployment_docs/aws.md)** | S3 |
| **Google Cloud** | **[gcp.md](deployment_docs/gcp.md)** | GCS |

Storage is a **free choice**, not a platform lock — any of Tigris / S3 / GCS runs on any cloud; the table just shows the easiest per platform.

**One difference off Fly:** on AWS and GCP you must set **`EMBED_SERVICE_URL`** yourself (e.g. `http://clip:8001`). Fly derives it automatically. Each guide says where.

> **Deploy sanity check.** Set **`DEPLOY_ENV=production`** in your `.env`: it turns on [`src/preflight.py`](src/preflight.py), which **warns** (or, with `STRICT_DEPLOY_CHECK=true`, refuses to start) if a **local** setting slipped into the deploy — `STORAGE_PROVIDER=local`, a compose-only `qdrant`/`postgres` host, or `COMPOSE_PROFILES`. On Fly it turns on automatically.
