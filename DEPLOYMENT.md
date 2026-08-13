# Deploying MomentSearch

MomentSearch ships as **one neutral Docker image** — a plain `python:3.11-slim`
container that runs any of four entrypoints by command (`api`, `worker`, `clip`,
one-shot `seed`). Nothing about the image is tied to a cloud, so it deploys
anywhere Docker runs. Pick your target:

| Guide | For |
|---|---|
| **[Fly.io](deployment_docs/fly.md)** | the default / reference deploy — token-authenticated `flyctl`, `fly.toml`. Set your own **globally unique app name** in `fly.toml` first (`momentsearch` is taken). |
| **[AWS](deployment_docs/aws.md)** | one EC2 + `docker compose`, or ECS/Fargate (fat & slim) |
| **[Google Cloud](deployment_docs/gcp.md)** | one GCE VM + `docker compose`, or GKE / Cloud Run (fat & slim) |

## Before you deploy — get these keys first

Every deploy (Fly, AWS, GCP) needs the **same four services**. Sign up and put the values in your `.env` **before** you start any cloud guide — they're the same everywhere; only storage and compute change per cloud.

| What | Env var(s) | Where to get it |
|---|---|---|
| **Database** (Postgres) | `DATABASE_URL` | [neon.tech](https://neon.tech) → create a project → copy the **Pooled** connection string. |
| **Vector store** (Qdrant) | `QDRANT_URL` + `QDRANT_API_KEY` | [cloud.qdrant.io](https://cloud.qdrant.io) → create a cluster → copy its URL + API key. |
| **Work queue** (Prefect) | `PREFECT_API_URL` + `PREFECT_API_KEY` | [app.prefect.cloud](https://app.prefect.cloud) → avatar → API Keys (free, no card). |
| **Answer model** (OpenAI) | `OPENAI_API_KEY` | [platform.openai.com/api-keys](https://platform.openai.com/api-keys). Also does transcripts + upload speech-to-text. |

Optional: **`GEMINI_API_KEY`** for speaker recognition ("who said what") — [aistudio.google.com/apikey](https://aistudio.google.com/apikey). Leave it out and everything else still works.

> No OpenAI key? The app still runs — you get ranked, clickable moments, just no written answer.

**Storage is separate** and depends on your cloud — each guide below sets that up (bucket + keys + CORS), then deploys the app.

## Same image everywhere — only three things change

The architecture is identical on every target: **one image, three long-running
process groups** (`api` :8000 public, `worker` no ports, `clip` :8001 internal)
plus a one-shot **seed**, with all durable state in managed services (Postgres,
Qdrant, Prefect, object storage). Moving between clouds changes only:

| Thing | Fly | AWS / GCP |
|---|---|---|
| **Compute** | `fly deploy` (`fly.toml`) | ECS/GKE, or a VM + `docker compose` |
| **Clip address** | auto-derived from `FLY_APP_NAME` | **set `EMBED_SERVICE_URL` explicitly** (e.g. `http://clip:8001`) |
| **Storage** | `flyio` (Tigris) — one command | `aws` + S3, or `gcp_native` + GCS |

Storage is a **free choice, not a platform constraint**: Tigris, GCS and S3 are all
first-class and any of them runs on any target. The column above is just the
path of least resistance per cloud. Each guide's **Object storage** section
([Fly](deployment_docs/fly.md#step-5--make-the-storage-bucket) ·
[AWS](deployment_docs/aws.md#object-storage) ·
[GCP](deployment_docs/gcp.md#object-storage)) documents all three, with the
bucket, credential and **CORS** steps that browser uploads need.

Everything else — `DATABASE_URL`, `QDRANT_URL`, `PREFECT_*`, `OPENAI_API_KEY`,
`GEMINI_API_KEY` — is the same env everywhere (just URLs/keys reachable from
anywhere).

## Fat vs slim (all targets)

- **Fat** (default, `docker build .`) — includes the local CLIP model, so one
  image runs api + worker + clip and embeds in-process. The simplest deploy.
- **Slim** (`docker build --build-arg WITH_TORCH=false .`) — no local model;
  api + worker send embedding to a **separate CLIP service** via
  `EMBED_SERVICE_URL`. Build that service from **`Dockerfile.clip`** (CPU by
  default; a `TORCH_INDEX_URL` build-arg makes it GPU). Use this when embedding
  is the bottleneck or you want CLIP on a GPU. See each guide's split section.

> **Deploy sanity check.** On any deploy, set **`DEPLOY_ENV=production`** to arm
> [`src/preflight.py`](src/preflight.py): it warns (or, with
> `STRICT_DEPLOY_CHECK=true`, aborts) when a **local** setting slipped into the
> deploy — `STORAGE_PROVIDER=local`, a compose-only `qdrant`/`postgres` host,
> `COMPOSE_PROFILES` — which work locally but break in production. On Fly it arms
> itself automatically (via `FLY_APP_NAME`).
