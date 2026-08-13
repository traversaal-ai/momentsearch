# Deploying MomentSearch to Fly.io

Deploys **one Docker image** as **three process groups** (`api`, `worker`, `clip`) plus a **one-shot seed gate**, each on its own Fly machine.

## What you need

- `DATABASE_URL` — Neon Postgres (manifest)
- `QDRANT_URL` + `QDRANT_API_KEY` — Qdrant Cloud (vectors; collections `moments_l14` + `moments_text_openai` auto-created)
- `PREFECT_API_URL` + `PREFECT_API_KEY` — Prefect Cloud (work queue)
- `OPENAI_API_KEY` (or `LLM_API_KEY`) — answer model (`gpt-4o`), transcript embeddings (`text-embedding-3-small`), ASR (`whisper-1`). **Required** — the default transcript branch needs it; set `TEXT_EMBED_PROVIDER=fastembed` for a keyless CPU branch.
- **Object storage** — one private bucket for videos, thumbnails and transcripts. Three supported choices, all first-class: **Tigris** (`flyio`), **GCS** (`gcp_native`), **S3** (`aws`). See [Object storage](#object-storage)
- `GEMINI_API_KEY` — *optional*, speaker recognition
- `YT_COOKIES_B64` — *optional*, YouTube ingest (Fly datacenter IP is bot-checked)
- A **Fly.io account** + `flyctl` CLI

## Object storage

Uploaded videos, frame thumbnails and transcript JSON all live in **one private
bucket**, keyed `{user_id}/{video_id}/` — one prefix per video, so deleting a video
is one prefix delete ([`src/storage.py`](../src/storage.py)). The browser PUTs
straight to the bucket with a presigned URL and the UI loads thumbnails and video
playback with presigned GETs, so **bytes never flow through the API**. That puts the
bucket on the hot path for every upload and every frame drawn — worth getting right.

### Pick a provider

All three are fully supported and behave identically at runtime — the app only ever
calls put/get/head/presign/list/delete, and each provider is one branch behind that
interface. Choose on where the rest of your infrastructure already lives:

| Provider | `STORAGE_PROVIDER` | Setup cost | Pick it when |
|---|---|---|---|
| **Tigris** — Fly-native | `flyio` | one command, no external signup | You want the fastest path on Fly, and storage co-located with your machines. |
| **GCS** — Google Cloud Storage | `gcp_native` | GCP project + service account | You're already on Google Cloud, or want the bucket to outlive the Fly app. |
| **S3** — Amazon | `aws` | AWS account + IAM user | You're already on AWS, or need a specific region/compliance posture. |

Tigris is the **default recommendation on Fly** simply because `fly storage create`
does the whole job; it is not more or less capable than the other two. Whichever you
pick, the bucket stays **private** — presigned URLs are the only way in or out.

> `STORAGE_PROVIDER=local` (the repo default) writes under `./data` and has no
> presigning — the API streams bytes itself. Fine for dev, wrong for Fly: machines
> have ephemeral disks, so uploads vanish on restart and `api`/`worker` can't see each
> other's files. The preflight check ([`src/preflight.py`](../src/preflight.py)) warns
> about it whenever `DEPLOY_ENV` is set.

### Option 1 — Tigris (`STORAGE_PROVIDER=flyio`)

Fly's own S3-compatible storage. One command provisions the bucket **and** wires the
credentials; you add exactly one secret by hand.

**1. Create the bucket.** Run from the repo root so it attaches to the app named in
`fly.toml` (or pass `-a <app>`):

```powershell
fly storage create                      # prompts for a bucket name
fly storage create --name momentsearch-media   # ...or name it up front
```

That creates a **private** Tigris bucket and sets five secrets on the app:

| Secret Fly injects | Value | Where the app reads it |
|---|---|---|
| `BUCKET_NAME` | the bucket name you chose | `STORAGE_BUCKET` fallback ([`config.py:96`](../src/config.py#L96)) |
| `AWS_ACCESS_KEY_ID` | `tid_…` | `STORAGE_ACCESS_KEY_ID` fallback |
| `AWS_SECRET_ACCESS_KEY` | `tsec_…` | `STORAGE_SECRET_ACCESS_KEY` fallback |
| `AWS_REGION` | `auto` | `AWS_REGION` |
| `AWS_ENDPOINT_URL_S3` | `https://fly.storage.tigris.dev` | `STORAGE_ENDPOINT` ([`config.py:107`](../src/config.py#L107)) |

Setting secrets restarts the machines — expected, ignore the churn.

**2. Turn it on.** The one thing `fly storage create` does *not* set:

```powershell
fly secrets set STORAGE_PROVIDER=flyio
```

Nothing else is required: `config.py` accepts the `AWS_*`/`BUCKET_NAME` names as
fallbacks for its own `STORAGE_*` names, and `flyio` already defaults the endpoint to
`https://fly.storage.tigris.dev` even if `AWS_ENDPOINT_URL_S3` is absent.

**3. Confirm the wiring** (names and digests only — values stay hidden):

```powershell
fly storage list     # the bucket and which app it's attached to
fly secrets list     # expect the five above + STORAGE_PROVIDER
```

**4. Add the CORS rule — browser uploads fail without it.** The presigned `PUT` is a
cross-origin request from your page to `fly.storage.tigris.dev`, so the bucket must
allow it. Either the dashboard:

```powershell
fly storage dashboard      # -> your bucket -> Settings -> CORS
```

…allowing methods `PUT` + `GET`, origin `https://<your-app>.fly.dev` (add your custom
domain too, and `http://localhost:8000` if you point local dev at the same bucket),
all headers, and exposing `ETag`. Or with the `aws` CLI against the Tigris endpoint —
same S3 API, using the `tid_`/`tsec_` keys:

```bash
AWS_ACCESS_KEY_ID=tid_... AWS_SECRET_ACCESS_KEY=tsec_... \
aws s3api put-bucket-cors --bucket momentsearch-media \
  --endpoint-url https://fly.storage.tigris.dev --region auto \
  --cors-configuration '{"CORSRules":[{
    "AllowedOrigins":["https://momentsearch.fly.dev","http://localhost:8000"],
    "AllowedMethods":["PUT","GET"],"AllowedHeaders":["*"],
    "ExposeHeaders":["ETag"],"MaxAgeSeconds":3600}]}'
```

**5. Optional — use the same bucket from local dev.** `fly storage create` prints the
key pair once; if you missed it, mint a new one in `fly storage dashboard`. Then in
`.env` (the endpoint is implied by the provider, so you don't set it):

```dotenv
STORAGE_PROVIDER=flyio
STORAGE_BUCKET=momentsearch-media
STORAGE_ACCESS_KEY_ID=tid_...
STORAGE_SECRET_ACCESS_KEY=tsec_...
STORAGE_REGION=auto
```

### Option 2 — GCS (`STORAGE_PROVIDER=gcp_native`)

Google's SDK with a service-account key, no HMAC keys involved. Create the bucket and
service account with the four commands in
[gcp.md → Object storage](gcp.md#object-storage), then push the same values as Fly
secrets:

```powershell
fly secrets set `
  STORAGE_PROVIDER=gcp_native `
  STORAGE_BUCKET=momentsearch-media `
  GOOGLE_CLOUD_PROJECT_ID=your-project `
  GOOGLE_CLOUD_PRIVATE_KEY_ID=... `
  GOOGLE_CLOUD_CLIENT_EMAIL=momentsearch-storage@your-project.iam.gserviceaccount.com `
  GOOGLE_CLOUD_CLIENT_ID=... `
  GOOGLE_CLOUD_CLIENT_X509_CERT_URL=https://www.googleapis.com/robot/v1/metadata/x509/...
fly secrets set GOOGLE_CLOUD_PRIVATE_KEY="-----BEGIN PRIVATE KEY-----\n...\n-----END PRIVATE KEY-----\n"
```

The PEM is the one fiddly part: keep the `\n` **literal** (two characters, not real
newlines) and set it in its own command so the shell doesn't reflow it. `config.py`
un-escapes them and strips stray surrounding quotes, so it survives both `fly secrets
set` and `fly secrets import` ([`config.py:113-122`](../src/config.py#L113-L122)).
Add the bucket CORS rule from that same section.

*Variant:* `STORAGE_PROVIDER=gcp` uses the S3-interoperability endpoint with an HMAC
key pair instead of the SA JSON — two secrets rather than seven, at the cost of a
second credential type to manage. Same bucket either way.

### Option 3 — S3 (`STORAGE_PROVIDER=aws`)

Create the bucket + IAM user with the commands in
[aws.md → Object storage](aws.md#object-storage), then:

```powershell
fly secrets set `
  STORAGE_PROVIDER=aws `
  STORAGE_BUCKET=momentsearch-media `
  STORAGE_REGION=us-east-1 `
  STORAGE_ACCESS_KEY_ID=AKIA... `
  STORAGE_SECRET_ACCESS_KEY=...
```

`STORAGE_REGION` must be the bucket's **real** region here (unlike Tigris's `auto`) —
a mismatch shows up as signature or redirect errors. Add the bucket CORS rule from
that same section.

### Env vars at a glance

| Var | `flyio` | `gcp_native` | `aws` |
|---|---|---|---|
| `STORAGE_PROVIDER` | `flyio` | `gcp_native` | `aws` |
| `STORAGE_BUCKET` | auto (`BUCKET_NAME`) | **required** | **required** |
| `STORAGE_ACCESS_KEY_ID` / `_SECRET_ACCESS_KEY` | auto (`AWS_*`) | — | **required** |
| `STORAGE_REGION` | `auto` | — | **real region** |
| `GOOGLE_CLOUD_*` (6 vars) | — | **required** | — |
| Endpoint | auto | — | auto |

### Verify it actually works

Storage failures otherwise surface late — as a stuck upload or a blank thumbnail. One
round trip through the real code path proves credentials, bucket, and presigning:

```powershell
fly ssh console
python -c "from src import storage as s; k='_selftest/probe.txt'; print(s.put_bytes(k,b'ok','text/plain')); print(s.head(k)); print(s.presign_get(k)[:90]); s.delete_key(k); print('storage OK')"
```

`head` returning a size and a `presign_get` URL printing means the write path is live.
CORS is *not* covered by this — it's a browser-only rule, so confirm it by uploading a
small video through the UI.

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
- **Browser uploads fail with a CORS error** → the bucket has no CORS rule, or `AllowedOrigins` doesn't match your exact serving origin (scheme + host, no trailing slash). See [Object storage](#object-storage).
- **Uploads/thumbnails fail with `SignatureDoesNotMatch` or 403 on Tigris** → real AWS credentials are in the environment: `STORAGE_*` falls back to `AWS_ACCESS_KEY_ID`/`AWS_SECRET_ACCESS_KEY`, so stray AWS keys get used to sign Tigris requests. Set `STORAGE_ACCESS_KEY_ID`/`STORAGE_SECRET_ACCESS_KEY` explicitly (they win) or unset the `AWS_*` pair.
- **`NoSuchBucket`** → `STORAGE_BUCKET` takes precedence over Fly's injected `BUCKET_NAME`; if you set both, they must agree. `fly storage list` shows the real name.
- **Wrong Tigris endpoint** → the `flyio` default is `https://fly.storage.tigris.dev`. For a bucket created outside Fly, set `AWS_ENDPOINT_URL_S3` to whatever its dashboard shows — it overrides the default.
- **Uploads vanish after a restart** → `STORAGE_PROVIDER` is still `local`; Fly disks are ephemeral. Pick a real provider above.
- **`clip` unreachable** → confirm the `app = '…'` line in `fly.toml` matches your app and `clip` is running (`fly status`); override with `EMBED_SERVICE_URL`.
