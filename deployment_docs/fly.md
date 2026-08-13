# Deploying MomentSearch to Fly.io

Deploys **one Docker image** as **three process groups** (`api`, `worker`, `clip`) plus a **one-shot seed gate**, each on its own Fly machine.

**Follow the numbered steps in order.** Every command has prerequisites — e.g. `fly storage create` can't work until flyctl is installed, you're authenticated, and the app exists (steps 0–4). Running it early just errors. Run everything from the repo root; Windows uses PowerShell.

---

## Before you start — what you'll need

External services (get these first; you'll paste them into `.env`):

- **`DATABASE_URL`** — Neon Postgres (the video manifest)
- **`QDRANT_URL`** + **`QDRANT_API_KEY`** — Qdrant Cloud (vectors; collections auto-create)
- **`PREFECT_API_URL`** + **`PREFECT_API_KEY`** — Prefect Cloud (the work queue)
- **`OPENAI_API_KEY`** (or `LLM_API_KEY`) — answer model + transcript embeddings + ASR. **Required** unless you switch `TEXT_EMBED_PROVIDER=fastembed` for a keyless CPU branch.
- **A Fly.io account** + a **Fly API token** (step 1) — the whole deploy is token-based, no browser login.
- **Object storage** — one private bucket. This guide sets up **Tigris** (Fly-native) inline. On GCS or S3 instead? See [Other storage providers](#other-storage-providers).
- *Optional:* `GEMINI_API_KEY` (speaker recognition), `YT_COOKIES_B64` (YouTube ingest — Fly's datacenter IP is bot-checked).

---

## Two kinds of authentication (read this once)

Fly deploys involve **two separate credentials**, and confusing them is the usual stumbling block:

1. **You → Fly** — permission to *run* the commands (`fly apps create`, `fly storage create`, `fly deploy`). This is your **Fly API token** (steps 1–2). Set once per terminal.
2. **App → Bucket** — permission for the running app to *read/write* storage. For **Tigris these keys are created and wired FOR you** by `fly storage create` (step 5) — you never mint bucket keys by hand. (On GCS/S3 you *do* create them yourself — that's the main difference.)

So: you authenticate **yourself** to Fly once (steps 1–2); the **bucket's** own credentials are handled in step 5.

---

## Quick deploy — copy-paste, in order (PowerShell)

If flyctl is already installed and working (**Step 0**), your token is in `.env` as `FLY_IO_TOKEN`, and `.env` has your services (DB, Qdrant, Prefect, OpenAI), this whole block deploys you. Set the two names at the top; run the rest line by line.

```powershell
# --- names (pick a GLOBALLY UNIQUE app name) ---
$APP    = "momentsearch-you"          # <- change this; must be unique across all of Fly
$BUCKET = "$APP-media"

# --- authenticate yourself to Fly (token from .env) ---
$env:FLY_API_TOKEN = ((Select-String '^FLY_IO_TOKEN=' .env).Line -replace '^FLY_IO_TOKEN=','').Trim().Trim('"')
fly auth whoami                                                    # prints your email = good

# --- put the app name into fly.toml, then create the app ---
(Get-Content fly.toml) -replace "^app = '.*'","app = '$APP'" | Set-Content fly.toml
fly apps create $APP --org personal

# --- create the Tigris bucket (auto-injects the S3 keys as secrets) ---
fly storage create --name $BUCKET

# --- push your .env (services) as secrets, then force Tigris on ---
Get-Content .env |
  Where-Object { $_ -match '^[A-Z_]+=.+' -and $_ -notmatch '^FLY_' -and $_ -notmatch '^YT_COOKIES_FILE=' } |
  fly secrets import
fly secrets set STORAGE_PROVIDER=flyio                             # last, so it wins over any STORAGE_* in .env

# --- deploy ---
fly deploy --ha=false
fly open                                                           # -> https://<app>.fly.dev
```

**Two things this block can't do for you:**
- **CORS** — browser uploads need one dashboard click: `fly storage dashboard` → your bucket → Settings → CORS → allow `PUT`+`GET` from `https://$APP.fly.dev`, all headers, expose `ETag` (see [Step 5d](#step-5--create-the-storage-bucket-tigris)). Everything else works without it; *uploads* don't.
- **Tigris only.** On GCS/S3, skip `fly storage create` and set that bucket up first — see [Other storage providers](#other-storage-providers).

The numbered steps below are the same flow, explained — read them if a line errors, or the first time through.

---

## Step 0 — Install flyctl and confirm it runs

```powershell
iwr https://fly.io/install.ps1 -useb | iex     # Windows (PowerShell)
```

```bash
curl -L https://fly.io/install.sh | sh         # macOS / Linux
```

It installs to `~/.fly/bin` and adds itself to `PATH`. **Open a NEW shell**, then verify:

```powershell
fly version        # must print a version, e.g. "flyctl v0.3.xxx"
```

**If `fly` won't run** — Windows *"No application is associated with the specified file"*, or it resolves but does nothing — the binary is **broken or half-upgraded** (a common failure is a 0-byte `~/.fly/bin/fly.exe` from an interrupted update). Fix by **re-running the installer above** — it re-downloads a clean `fly.exe`. If the download itself was interrupted (a corrupt `flyctl.zip`), just run the installer again on a stable connection. Don't continue until `fly version` prints.

---

## Step 1 — Get a Fly API token

Create an **org / personal access token** at
<https://fly.io/user/personal_access_tokens> and paste it into `.env`:

```dotenv
FLY_IO_TOKEN=...
```

> **Token scope matters.** An **app-scoped** `fly tokens create deploy` token can `fly deploy` and nothing else — it **fails** on `fly apps create` and `fly storage create` with a permissions error. Use an **org / personal** token for this first-time setup; save the narrow deploy token for CI (step 8).

The app **never reads** this token — it's only for `flyctl` on your machine. It's named `FLY_IO_TOKEN` (not `FLY_API_TOKEN`) on purpose, so the secrets-import filter in step 6 (`-notmatch '^FLY_'`) can't push your Fly credential into the app's own secrets.

---

## Step 2 — Authenticate (token, not `fly auth login`)

`flyctl` reads **`FLY_API_TOKEN`** from the environment — no browser, no interactive login. Load it from `.env` and confirm:

```powershell
$env:FLY_API_TOKEN = ((Select-String '^FLY_IO_TOKEN=' .env).Line -replace '^FLY_IO_TOKEN=','').Trim().Trim('"')
fly auth whoami        # prints your account email = token is good
```

```bash
export FLY_API_TOKEN="$(grep '^FLY_IO_TOKEN=' .env | cut -d= -f2- | tr -d '\r\"')"
fly auth whoami
```

The variable lives **only in this shell** — re-run this in every new terminal (or add it to your profile). If any later `fly` command says you're not logged in, this is why.

---

## Step 3 — Choose your app name

Fly app names are **globally unique across all of Fly**, so `momentsearch` is already taken — `fly apps create` will fail with *"Name has already been taken"*. Pick your own (e.g. `momentsearch-<you>`) and set it in **one place**:

| File | Line | Change it when |
|---|---|---|
| [`fly.toml`](../fly.toml) | `app = 'momentsearch'` | **Always** — this is the default fat deploy. |
| [`fly.slim.toml`](../fly.slim.toml) | `app = 'CHANGE-ME-slim'` | Only for the slim/GPU split, and it must be a **different** name. |

**Nothing else needs editing** — the clip machine's internal address, the CI workflow, and your `https://<app>.fly.dev` URL all derive the name automatically. Treat the name as **immutable**: changing it later means a new app, not a rename. (Two things *do* follow the name because they live outside the repo: the bucket CORS origin in step 5, and the fact that secrets + buckets are per-app — a new name reruns steps 4–7.)

---

## Step 4 — Create the app

```powershell
fly apps create momentsearch-<you> --org personal
```

The name here **must match** the `app = '…'` line you set in `fly.toml`, or every later command needs `-a <name>`. Confirm with `fly apps list`.

---

## Step 5 — Create the storage bucket (Tigris)

Do this **before the first deploy** so the seed step has somewhere to write. Tigris is Fly's own S3-compatible storage: **one command provisions the bucket AND wires the credentials.**

**5a. Create it** (you choose the bucket name — this is where the name is set):

```powershell
fly storage create --name momentsearch-<you>-media
# or bare `fly storage create` to be prompted for a name
```

Run from the repo root so it attaches to the app in `fly.toml` (or pass `-a <app>`). This creates a **private** bucket and **auto-sets five secrets on the app** — these ARE the App→Bucket credentials, you don't make them yourself:

| Secret Fly injects | Value | Where the app reads it |
|---|---|---|
| `BUCKET_NAME` | the name you chose | `STORAGE_BUCKET` fallback ([`config.py:96`](../src/config.py#L96)) |
| `AWS_ACCESS_KEY_ID` | `tid_…` | `STORAGE_ACCESS_KEY_ID` fallback |
| `AWS_SECRET_ACCESS_KEY` | `tsec_…` | `STORAGE_SECRET_ACCESS_KEY` fallback |
| `AWS_REGION` | `auto` | `AWS_REGION` |
| `AWS_ENDPOINT_URL_S3` | `https://fly.storage.tigris.dev` | `STORAGE_ENDPOINT` ([`config.py:107`](../src/config.py#L107)) |

Setting secrets restarts the machines — expected, ignore the churn.

**5b. Turn it on** — the one thing `fly storage create` does *not* set:

```powershell
fly secrets set STORAGE_PROVIDER=flyio
```

That's all the wiring: `config.py` accepts the `AWS_*`/`BUCKET_NAME` names as fallbacks, and `flyio` defaults the endpoint to `https://fly.storage.tigris.dev`. **Leave `STORAGE_*` out of your `.env`** — Fly's injected secrets cover it (and stray values there can conflict).

**5c. Confirm the wiring** (names/digests only — values stay hidden):

```powershell
fly storage list     # the bucket and which app it's attached to
fly secrets list     # expect the five above + STORAGE_PROVIDER
```

**5d. Add the CORS rule — browser uploads fail without it.** The browser PUTs straight to `fly.storage.tigris.dev` with a presigned URL (bytes never flow through the API), so the bucket must allow that cross-origin request. Easiest via the dashboard:

```powershell
fly storage dashboard      # -> your bucket -> Settings -> CORS
```

Allow methods **`PUT` + `GET`**, origin **`https://<your-app>.fly.dev`** (add your custom domain, and `http://localhost:8000` if local dev uses the same bucket), **all headers**, and expose **`ETag`**. Or via the `aws` CLI against the Tigris endpoint, using the `tid_`/`tsec_` keys:

```bash
AWS_ACCESS_KEY_ID=tid_... AWS_SECRET_ACCESS_KEY=tsec_... \
aws s3api put-bucket-cors --bucket momentsearch-<you>-media \
  --endpoint-url https://fly.storage.tigris.dev --region auto \
  --cors-configuration '{"CORSRules":[{
    "AllowedOrigins":["https://momentsearch-<you>.fly.dev","http://localhost:8000"],
    "AllowedMethods":["PUT","GET"],"AllowedHeaders":["*"],
    "ExposeHeaders":["ETag"],"MaxAgeSeconds":3600}]}'
```

> **Using GCS or S3 instead of Tigris?** Skip 5a–5c and set that bucket up first — its creation, service account / IAM keys, and secrets are defined in its own doc: **GCS → [gcp.md → Object storage](gcp.md#object-storage)**, **S3 → [aws.md → Object storage](aws.md#object-storage)**. Add its CORS rule from that same section, then come back to **step 6**. See also [Other storage providers](#other-storage-providers).

---

## Step 6 — Push your `.env` as secrets

One import moves your whole local config (DB, Qdrant, Prefect, OpenAI, …) to Fly. The filter drops the local-only lines: `^FLY_` keeps your Fly token out of the app's secrets, and `YT_COOKIES_FILE` points at a bind mount Fly doesn't have.

```powershell
Get-Content .env |
  Where-Object { $_ -match '^[A-Z_]+=.+' -and $_ -notmatch '^FLY_' -and $_ -notmatch '^YT_COOKIES_FILE=' } |
  fly secrets import
```

YouTube cookies go up as base64 — the worker decodes the secret to a writable temp path at runtime (`src/ingest/fetch.py::_cookiefile`):

```powershell
$b64 = [Convert]::ToBase64String([IO.File]::ReadAllBytes("secrets/cookies.txt"))
fly secrets set YT_COOKIES_B64="$b64"
```

Check what landed with `fly secrets list` (names + digests only — values are write-only once set).

---

## Step 7 — Deploy

```powershell
fly deploy --ha=false
```

`release_command` runs the seed step (`python -m src.seed`) first. Samples already in shared Qdrant/Neon exit in seconds; a fresh Qdrant re-indexes the sample talk (best-effort by default — set `SEED_STRICT=true` for a hard gate, `SEED_SAMPLE_VIDEOS=false` to skip).

```powershell
fly open           # -> https://<your-app>.fly.dev/
fly status         # machines per process group
fly logs           # tail all processes
```

**Verify storage end-to-end** (proves credentials + bucket + presigning through the real code path — failures otherwise surface late as a stuck upload or blank thumbnail):

```powershell
fly ssh console        # you land in /app on a running machine
# then, inside the machine:
python -c "from src import storage as s; k='_selftest/probe.txt'; print(s.put_bytes(k,b'ok','text/plain')); print(s.head(k)); print(s.presign_get(k)[:90]); s.delete_key(k); print('storage OK')"
```

`head` returning a size and a `presign_get` URL means the write path is live. CORS is browser-only and *not* covered here — confirm it by uploading a small video through the UI.

---

## Step 8 — Optional: same token flow in CI

[`.github/workflows/fly-deploy.yml`](../.github/workflows/fly-deploy.yml) redeploys on every push to `dev`: `flyctl deploy --remote-only` with `FLY_API_TOKEN` in the environment. Add a **narrow** app-scoped token as the `FLY_API_TOKEN` repo secret (Settings → Secrets and variables → Actions):

```powershell
fly tokens create deploy -x 999999h
```

The app comes from `fly.toml`, so a renamed app needs no workflow edit.

---

## Other storage providers

Tigris (above) is the default recommendation on Fly only because `fly storage create` does the whole job in one command — it's not more or less capable than the others. All three are private buckets; presigned URLs are the only way in or out. If your infra already lives elsewhere:

| Provider | `STORAGE_PROVIDER` | Where the full setup lives |
|---|---|---|
| **Tigris** — Fly-native | `flyio` | This doc, step 5. |
| **GCS** — Google Cloud Storage | `gcp_native` | [gcp.md → Object storage](gcp.md#object-storage) — bucket + service account + the `fly secrets set` block. |
| **S3** — Amazon | `aws` | [aws.md → Object storage](aws.md#object-storage) — bucket + IAM user + the `fly secrets set` block. |

For GCS/S3 you create the bucket and its keys yourself (following that doc), set them as Fly secrets, then continue at step 6. `STORAGE_REGION` must be the bucket's **real** region for S3 (Tigris uses `auto`).

> `STORAGE_PROVIDER=local` (the repo default) writes under `./data` with no presigning — fine for dev, **wrong for Fly**: machines have ephemeral disks, so uploads vanish on restart and `api`/`worker` can't see each other's files. The preflight check ([`src/preflight.py`](../src/preflight.py)) warns whenever `DEPLOY_ENV` is set.

---

## Scale

```powershell
fly scale count worker=3          # more ingest throughput
fly scale count worker=0 clip=0   # idle between sessions
fly secrets set WORKER_CONCURRENCY=3   # more videos per worker
```

`api` auto-stops when idle (`min_machines_running = 0`), so it costs ~nothing at rest.

## Fat vs slim / GPU

Default is **fat** — all three groups in one CPU image on Fly. For a GPU CLIP, run the embedder on an external GPU host (Fly is CPU-only since Jul 2026), keep `api` + `worker` slim on Fly via [`fly.slim.toml`](../fly.slim.toml), and point `EMBED_SERVICE_URL` at it. That slim config is a **second Fly app** — give it its own name and run steps 3–7 against it (`fly deploy -c fly.slim.toml`). It has no `clip` process, so `EMBED_SERVICE_URL` is mandatory there.

## Troubleshooting

- **`fly` won't run / "No application is associated"** (Windows) → broken or half-upgraded binary (often a 0-byte `~/.fly/bin/fly.exe`). Re-run the installer (step 0).
- **Every `fly` command says you're not logged in** → `FLY_API_TOKEN` isn't set in *this* shell. It's per-terminal; re-run step 2. `fly auth whoami` is the check.
- **`Name has already been taken`** on `fly apps create` → app names are global. Pick a unique one and set the same value in `fly.toml` (step 3).
- **`not authorized` / permission error on `fly apps create` or `fly storage create`** → you're using an app-scoped deploy token. Those need an org / personal token (step 1).
- **`Could not find App`, or secrets land somewhere unexpected** → the `app = '…'` line in `fly.toml` doesn't match the app you created. Fix the file, or pass `-a <name>`.
- **Build fails with 403 / "high risk"** → build locally: `fly deploy --ha=false --local-only` (needs Docker Desktop), or unlock at <https://fly.io/high-risk-unlock>.
- **Deploy aborts on release_command** → only with `SEED_STRICT=true`; check `fly logs`, fix the bad secret, or drop `SEED_STRICT`.
- **`/demo` empty after fresh deploy** → best-effort seed skipped indexing; set `YT_COOKIES_B64` on the empty Qdrant and redeploy.
- **YouTube ingest fails** → set/refresh `YT_COOKIES_B64` (cookies expire in ~2–3 weeks). Uploads unaffected.
- **Browser uploads fail with a CORS error** → the bucket has no CORS rule, or `AllowedOrigins` doesn't match your exact serving origin (scheme + host, no trailing slash). See step 5d.
- **Uploads/thumbnails fail with `SignatureDoesNotMatch` or 403 on Tigris** → real AWS credentials are in the environment: `STORAGE_*` falls back to `AWS_ACCESS_KEY_ID`/`AWS_SECRET_ACCESS_KEY`, so stray AWS keys get used to sign Tigris requests. Set `STORAGE_ACCESS_KEY_ID`/`STORAGE_SECRET_ACCESS_KEY` explicitly (they win) or unset the `AWS_*` pair.
- **`NoSuchBucket`** → `STORAGE_BUCKET` takes precedence over Fly's injected `BUCKET_NAME`; if you set both, they must agree. `fly storage list` shows the real name.
- **Uploads vanish after a restart** → `STORAGE_PROVIDER` is still `local`; Fly disks are ephemeral. Pick a real provider (step 5).
- **`clip` unreachable** → confirm the `app = '…'` line in `fly.toml` matches your app and `clip` is running (`fly status`); override with `EMBED_SERVICE_URL`.
