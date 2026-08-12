# Deploying MomentSearch to Fly.io

Deploys **one Docker image** as **three process groups** (`api`, `worker`, `clip`) plus a **one-shot seed gate**, each on its own Fly machine.

## What you need

- `DATABASE_URL` — Neon Postgres (manifest)
- `QDRANT_URL` + `QDRANT_API_KEY` — Qdrant Cloud (vectors; collections `moments_l14` + `moments_text_openai` auto-created)
- `PREFECT_API_URL` + `PREFECT_API_KEY` — Prefect Cloud (work queue)
- `OPENAI_API_KEY` (or `LLM_API_KEY`) — answer model (`gpt-4o`), transcript embeddings (`text-embedding-3-small`), ASR (`whisper-1`). **Required** — the default transcript branch needs it; set `TEXT_EMBED_PROVIDER=fastembed` for a keyless CPU branch.
- **Object storage** — Fly-native **Tigris** (`fly storage create` → `STORAGE_PROVIDER=flyio`) or **GCS** (`STORAGE_PROVIDER=gcp_native` + `GOOGLE_CLOUD_*`). Holds video files under `{user}/{video}/`. See [Object storage](#object-storage-bucket)
- `GEMINI_API_KEY` — *optional*, speaker recognition
- `YT_COOKIES_B64` — *optional*, YouTube ingest (Fly datacenter IP is bot-checked)
- A **Fly.io account** + `flyctl` CLI

## Object storage (bucket)

Video files, frame thumbnails and transcripts live in a **private** bucket under
`{user}/{video}/`. Pick one:

**Fly-native (Tigris) — one command, zero external signup:**

```bash
fly storage create      # provisions Tigris + injects the AWS_*/BUCKET_NAME secrets
fly secrets set STORAGE_PROVIDER=flyio
```

**GCS** (what production uses): `STORAGE_PROVIDER=gcp_native` + the `GOOGLE_CLOUD_*`
service-account vars + `STORAGE_BUCKET`. **S3** also works: `STORAGE_PROVIDER=aws`
+ `STORAGE_BUCKET`/`STORAGE_REGION`/`STORAGE_ACCESS_KEY_ID`/`STORAGE_SECRET_ACCESS_KEY`.

Browser uploads are presigned `PUT`s straight to the bucket, so add a **CORS rule**
allowing `PUT`/`GET` from your app's origin — Tigris: `fly storage dashboard` →
your bucket → CORS; GCS/S3: see `.env.example`. Keep the bucket **private**; reads
use presigned GETs.

## Deploy

Run from the repo root. Windows uses PowerShell.

### 1. Authenticate

`flyctl` reads `FLY_API_TOKEN`; the token lives in `.env` as `FLY_IO_TOKEN`.

```powershell
$env:FLY_API_TOKEN = ((Select-String '^FLY_IO_TOKEN=' .env).Line -replace '^FLY_IO_TOKEN=','').Trim().Trim('"')
fly auth whoami
```

```bash
export FLY_API_TOKEN="$(grep '^FLY_IO_TOKEN=' .env | cut -d= -f2- | tr -d '\r\"')"
fly auth whoami
```

### 2. Create the app

```powershell
fly apps create momentsearch --org personal
```

App names are globally unique — pick your own (e.g. `momentsearch-<you>`) and set it in **one** place: the `app = '…'` line in `fly.toml`. The clip address derives from `FLY_APP_NAME` at runtime.

### 3. Push secrets

Import `.env` (skip local-only bits), then add YouTube cookies as base64 — Fly has no bind mount for `./secrets`, so the worker decodes the secret to a writable temp file instead (`src/ingest/fetch.py::_cookiefile`).

```powershell
Get-Content .env |
  Where-Object { $_ -match '^[A-Z_]+=.+' -and $_ -notmatch '^FLY_' -and $_ -notmatch '^YT_COOKIES_FILE=' } |
  fly secrets import

$b64 = [Convert]::ToBase64String([IO.File]::ReadAllBytes("secrets/cookies.txt"))
fly secrets set YT_COOKIES_B64="$b64"
```

### 4. Deploy

```powershell
fly deploy --ha=false
```

`release_command` runs the seed step (`python -m src.seed`) first. Samples already in shared Qdrant/Neon exit in seconds; a fresh Qdrant re-indexes the sample talk (best-effort by default — set `SEED_STRICT=true` for a hard gate, `SEED_SAMPLE_VIDEOS=false` to skip).

### 5. Open

```powershell
fly open           # -> https://momentsearch.fly.dev/
fly logs           # tail all processes
```

## Scale

```powershell
fly scale count worker=3          # more ingest throughput
fly scale count worker=0 clip=0   # idle between sessions
fly secrets set WORKER_CONCURRENCY=3   # more videos per worker
```

`api` auto-stops when idle (`min_machines_running = 0`), so it costs ~nothing at rest.

## Fat vs slim / GPU

Default is **fat** — all three groups in one CPU image on Fly. For a GPU CLIP, run the embedder on an external GPU host (Fly is CPU-only since Jul 2026), keep `api` + `worker` slim on Fly via [`fly.slim.toml`](../fly.slim.toml), and point `EMBED_SERVICE_URL` at it.

**CI/CD:** `.github/workflows/fly-deploy.yml` redeploys on every push to `dev` (`flyctl deploy --remote-only`); add a deploy token (`fly tokens create deploy`) as the `FLY_API_TOKEN` repo secret.

## Troubleshooting

- **Build fails with 403 / "high risk"** → build locally: `fly deploy --ha=false --local-only` (needs Docker Desktop), or unlock at <https://fly.io/high-risk-unlock>.
- **Deploy aborts on release_command** → only with `SEED_STRICT=true`; check `fly logs`, fix the bad secret, or drop `SEED_STRICT`.
- **`/demo` empty after fresh deploy** → best-effort seed skipped indexing; set `YT_COOKIES_B64` on the empty Qdrant and redeploy.
- **YouTube ingest fails** → set/refresh `YT_COOKIES_B64` (cookies expire in ~2–3 weeks). Uploads unaffected.
- **Browser uploads fail** → add a GCS bucket CORS rule allowing `PUT` from your origin (see `.env.example`).
- **`clip` unreachable** → confirm the `app = '…'` line in `fly.toml` matches your app and `clip` is running (`fly status`); override with `EMBED_SERVICE_URL`.
