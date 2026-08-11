# Deploying MomentSearch to Fly.io

MomentSearch ships as **one Docker image** that runs as **three long-running
process groups** (`api`, `worker`, `clip`) plus a **one-shot seed gate** — each
on its own Fly machine, each scaled by its own bottleneck. This is the whole
point of the architecture: the pieces scale in different directions, so they
live on different machines.

```
                 ┌────────────── one image, three process groups ──────────────┐
 users ──HTTPS──►│  api      (:8000, public)   presign · register · search · UI │
                 │  worker   (no ports)        pulls ingest runs from Prefect    │
                 │  clip     (:8001, internal) ONE warm CLIP model behind a URL  │
                 └───────┬──────────────┬───────────────────┬───────────────────┘
                         ▼              ▼                    ▼
                   Neon Postgres   Prefect Cloud        Qdrant Cloud
                   (manifest)      (work queue)         (vectors)
                         ▲              ▲                    ▲
                         └───────  GCS bucket (videos + frame thumbnails)  ──────┘
```

## Why three separate services (not one box)

| Service | Scales on | Machine | Why separate |
|---|---|---|---|
| **api** | request concurrency | tiny, auto-stops when idle | stateless HTTP; must answer `202` instantly and never block on heavy work |
| **worker** | ingest throughput | cheap CPU, scale to N | download + ffmpeg per video; add replicas for a backfill, remove them after |
| **clip** | embedding FLOPs | one warm model (→ GPU later) | loading CLIP costs ~15–30s; doing it once in a shared service, not per-video, is the difference between fast and unusable |

If these were one process, you'd pay for a GPU on every web box, or reload the
model on every video, or block uploads behind embedding. Splitting them lets
each grow (and cost) independently: `fly scale count worker=5` for a big import,
or point `CLIP_SERVICE_URL` at a GPU machine when embedding is the wall — with
**zero code changes**.

Everything stateful is a rented managed service (Neon, Prefect Cloud, Qdrant
Cloud, GCS), so every Fly machine is disposable — "nothing on local."

## Prerequisites

You already have these wired in `.env` (they're external, so the same accounts
work from Fly):

- **Neon Postgres** — `DATABASE_URL`
- **Prefect Cloud** — `PREFECT_API_URL`, `PREFECT_API_KEY`
- **Qdrant Cloud** — `QDRANT_URL`, `QDRANT_API_KEY` (two collections are
  auto-created: `moments_l14` for the CLIP `clip-ViT-L-14` frame vectors and
  `moments_text_openai` for the `text-embedding-3-small` transcript vectors)
- **Object storage** — `STORAGE_PROVIDER=gcp_native` + the `GOOGLE_CLOUD_*` keys
  (bucket `momentsearch-media`). Everything for one video lives under
  `{user}/{video}/`: `source.{ext}` (raw upload), `frames/NNNNNN.jpg`
  (thumbnails) and `transcript.json` (timed transcript)
- **LLM, text embeddings & ASR** — `OPENAI_API_KEY` (or `LLM_API_KEY`). One
  OpenAI key now powers **three** jobs by default: the answer model (`gpt-4o`),
  the transcript embeddings (`text-embedding-3-small`) and upload transcription
  (Whisper `whisper-1`). Because the default transcript embedder is OpenAI (no
  longer the keyless fastembed branch), a **real deploy must set this key** —
  without it the transcript branch can't index. To avoid the OpenAI dependency,
  set `TEXT_EMBED_PROVIDER=fastembed` for a keyless transcript branch (bge on
  CPU). The cross-encoder reranker is **on by default** and also keyless (a
  local fastembed ONNX model, downloaded on the first query); set
  `ENABLE_RERANK=false` to disable it.
- A **Fly.io account** + the `flyctl` CLI installed.

> **The sample corpus is already indexed** in your shared Qdrant/Neon from local
> runs, so the deploy's seed gate finds them done and skips re-downloading — the
> deploy won't be blocked by YouTube.

## Deploy — step by step

All commands assume you're in the repo root. On Windows use PowerShell.

### 1. Authenticate

`flyctl` reads the `FLY_API_TOKEN` env var. The token lives in `.env` as
`FLY_IO_TOKEN` — load it into the session (this also works headless/CI, no
browser login needed):

```powershell
$env:FLY_API_TOKEN = ((Select-String '^FLY_IO_TOKEN=' .env).Line -replace '^FLY_IO_TOKEN=','').Trim().Trim('"')
fly auth whoami        # confirm it's your account
```

Bash equivalent:

```bash
export FLY_API_TOKEN="$(grep '^FLY_IO_TOKEN=' .env | cut -d= -f2- | tr -d '\r\"')"
fly auth whoami
```

### 2. Create the app (once)

```powershell
fly apps create momentsearch --org personal
```

Fly app names are **globally unique**, so `momentsearch` is already taken — pick
your own (e.g. `momentsearch-<you>`) and set it in **one** place: the
`app = '…'` line in `fly.toml`. That's it — the clip service's internal address
is derived from the app name at runtime (Fly injects `FLY_APP_NAME`), so there's
nothing else to rename.

### 3. Push secrets (once, and whenever they change)

Import everything from `.env` except the local-only bits, then add the YouTube
cookies as a base64 secret (there's no `./data` mount on Fly, so the file path
won't work there — the worker decodes the secret to a temp file at runtime):

```powershell
# import .env (skip FLY_ and the local cookie FILE path)
Get-Content .env |
  Where-Object { $_ -match '^[A-Z_]+=.+' -and $_ -notmatch '^FLY_' -and $_ -notmatch '^YT_COOKIES_FILE=' } |
  fly secrets import

# YouTube cookies as a secret (needed because Fly's datacenter IP is bot-checked)
$b64 = [Convert]::ToBase64String([IO.File]::ReadAllBytes("data/cookies.txt"))
fly secrets set YT_COOKIES_B64="$b64"
```

### 4. Deploy

```powershell
fly deploy --ha=false
```

> **If the build fails with a 403** — e.g. `error building: ... (status 403):
> Your account has been marked as high risk`, or the remote builder is otherwise
> refused/unavailable — build the image **locally** instead (needs Docker Desktop
> running) so it never touches Fly's remote builder:
>
> ```powershell
> fly deploy --ha=false --local-only
> ```
>
> This builds with your local Docker daemon and pushes the finished image to
> `registry.fly.io`. Alternatively, verify the account at
> <https://fly.io/high-risk-unlock> to use the remote builder.

On deploy, fly.toml's `release_command` runs the **seed step** first
(`python -m src.seed`). Because the samples are already indexed in your shared
Qdrant/Neon, it exits in seconds and the app goes live. On a **fresh clone**
(empty Qdrant) it instead re-indexes the sample talk in-process; if that
can't finish (e.g. no YouTube cookies) it's **best-effort by default** — it logs
loudly and the deploy still proceeds with an empty `/demo`, rather than aborting.
Set `SEED_STRICT=true` to restore the hard gate (an incomplete seed aborts the
deploy and the previous version keeps serving — you never get a half-indexed
app), or `SEED_SAMPLE_VIDEOS=false` to skip sample seeding entirely.

### 5. Open it

```powershell
fly open           # -> https://momentsearch.fly.dev/
fly logs           # tail all processes
```

## Scaling knobs

```powershell
fly scale count worker=3          # more ingest throughput (concurrent videos)
fly scale count worker=0 clip=0   # between ingest sessions — queued runs just wait
fly secrets set WORKER_CONCURRENCY=3   # more videos per worker machine
```

The `api` machine auto-stops when idle and auto-starts on the next request
(`min_machines_running = 0` in fly.toml), so it costs almost nothing at rest.

## CI/CD (optional)

`.github/workflows/fly-deploy.yml` redeploys automatically on **every push to
`dev`** (it runs `flyctl deploy --remote-only`). One-time setup — add a deploy
token as the `FLY_API_TOKEN` repo secret:

```powershell
fly tokens create deploy -x 999999h
# GitHub → Settings → Secrets and variables → Actions → New repository secret
```

> CI uses Fly's **remote** builder, so if the account is flagged "high risk"
> (see Troubleshooting) CI deploys fail there too — unlock the account, or deploy
> manually with `fly deploy --local-only` from a machine that has Docker until
> it's cleared.

## Cost (rough)

| Piece | At rest | Active |
|---|---|---|
| api (auto-stop) | ~$0 | ~$2–6/mo |
| worker | scale to 0 between sessions | ~$2–5/mo up |
| clip | scale to 0 between sessions | ~$2–5/mo (CPU) |
| Neon / Prefect / Qdrant | free tiers | — |
| GCS | ~$1–2/mo per 50 GB | — |
| LLM | — | ~$0.005–0.01 per question |

Everything-on ≈ **$40/mo**; idle-scaled with free tiers ≈ **$5–10/mo**. GPU (for
the clip service) is a burst cost only — rent it for a big backfill, kill it after.

## Troubleshooting

- **Build fails with 403 / "high risk account" / remote builder error** → Fly's
  shared remote builder refused the build. Build locally instead:
  `fly deploy --ha=false --local-only` (needs Docker Desktop running), or unlock
  the account at <https://fly.io/high-risk-unlock>. This is a builder/account
  issue, not a code issue — the same image builds fine locally.
- **Deploy aborts on release_command** → only happens with `SEED_STRICT=true`
  (the hard gate): the seed couldn't verify samples. Check `fly logs`; usually a
  bad `DATABASE_URL`/`QDRANT_URL` secret, or a fresh Qdrant with no YouTube
  cookies. Fix the secret, drop `SEED_STRICT` (best-effort: deploy proceeds with
  an empty `/demo`), or set `SEED_SAMPLE_VIDEOS=false` to skip seeding entirely.
- **`/demo` is empty after a fresh deploy** → best-effort seeding let the deploy
  through without indexing the samples (see `fly logs` for the loud `[seed]`
  message). Almost always missing YouTube cookies (`YT_COOKIES_B64`) on an empty
  Qdrant. Set the cookies and redeploy, or upload your own videos.
- **YouTube ingest fails on Fly** → datacenter IP is blocked; make sure
  `YT_COOKIES_B64` is set (step 3). Cookies expire in ~2–3 weeks; re-run the
  `fly secrets set YT_COOKIES_B64=…` command to refresh. Uploads are unaffected.
- **Browser uploads fail** → the GCS bucket needs a CORS rule allowing `PUT`
  from your site's origin (see `.env.example`).
- **`clip` unreachable** → confirm `CLIP_SERVICE_URL` in fly.toml matches the
  app name (`clip.process.<app>.internal:8001`).
