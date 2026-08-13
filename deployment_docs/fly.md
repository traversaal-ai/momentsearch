# Deploy MomentSearch to Fly.io

This puts MomentSearch online at `https://<your-app>.fly.dev`. Follow the steps in order. Copy each command, run it, then go to the next one. Use **PowerShell** on Windows, and run everything from inside the project folder.

---

## Before you start

You need a **Fly.io account** and these values saved in your `.env` file (open `.env.example` to see where each one goes):

- `DATABASE_URL` — your Neon Postgres link
- `QDRANT_URL` and `QDRANT_API_KEY` — your Qdrant Cloud
- `PREFECT_API_URL` and `PREFECT_API_KEY` — your Prefect Cloud
- `OPENAI_API_KEY` — for answers and transcripts
- `FLY_IO_TOKEN` — your Fly login token (Step 2 shows how to get one)

Optional: `GEMINI_API_KEY` (speaker names), `YT_COOKIES_B64` (YouTube).

You do **not** need storage keys — Step 5 makes those for you.

---

## Step 1 — Install the Fly tool

Run this. It downloads and installs `fly`:

```powershell
iwr https://fly.io/install.ps1 -useb | iex
```

Now **close this window and open a new PowerShell.** Then check it works:

```powershell
fly version
```

If you see a version number (like `v0.4.82`), you're good — go to Step 2.

**If it shows an error** (like "no application is associated" or "failed to update… wintun.dll"), your `fly` is broken. Just run the install command above again, open a new window, and check `fly version` once more.

---

## Step 2 — Log in

First get a token: go to <https://fly.io/user/personal_access_tokens>, create one, and paste it into your `.env` file like this:

```dotenv
FLY_IO_TOKEN=paste-it-here
```

> Pick the normal token, not a "deploy" token. A deploy token can't create apps or buckets.

Now run these two lines. The first loads your token; the second checks it:

```powershell
$env:FLY_API_TOKEN = ((Select-String '^FLY_IO_TOKEN=' .env).Line -replace '^FLY_IO_TOKEN=','').Trim().Trim('"')
fly auth whoami
```

If it prints your email, you're logged in. That's the whole login.

> Note: this login only lasts for **this window**. If you open a new PowerShell later, run those two lines again.

---

## Step 3 — Pick a name for your app

App names have to be unique on Fly, so `momentsearch` is taken. Pick your own, like `momentsearch-yourname`.

Open the file `fly.toml`, find the top line that says:

```toml
app = 'momentsearch'
```

Change it to your name:

```toml
app = 'momentsearch-yourname'
```

Save the file. (Pick the name once — you can't rename it later.)

---

## Step 4 — Create the app

```powershell
fly apps create momentsearch-yourname --org personal
```

Use the **same name** you put in `fly.toml`.

---

## Step 5 — Make the storage bucket

This is where uploaded videos and thumbnails are stored. One command makes the bucket **and** sets up its keys for you — you don't create any keys by hand:

```powershell
fly storage create --name momentsearch-yourname-media
```

Then turn it on:

```powershell
fly secrets set STORAGE_PROVIDER=flyio
```

That's storage done.

> Want to use Google Cloud or Amazon storage instead? Skip this step and follow [gcp.md](gcp.md#object-storage) or [aws.md](aws.md#object-storage) to make that bucket, then come back to Step 7.

---

## Step 6 — Allow uploads from your website

Browser uploads need one setting turned on, or uploading a video will fail. Open the storage settings:

```powershell
fly storage dashboard
```

In the page that opens: click your bucket → **Settings** → **CORS**. Add a rule that allows:

- Methods: **PUT** and **GET**
- Origin: **`https://momentsearch-yourname.fly.dev`** (your app's web address)
- Headers: **all** (`*`)
- Expose header: **ETag**

Save it. (Everything else works without this — only *uploading* needs it.)

---

## Step 7 — Send your settings to Fly

This copies everything in your `.env` file up to Fly as secrets:

```powershell
Get-Content .env |
  Where-Object { $_ -match '^[A-Z_]+=.+' -and $_ -notmatch '^FLY_' -and $_ -notmatch '^YT_COOKIES_FILE=' } |
  fly secrets import
```

Using YouTube links? Also run this to send your cookies file:

```powershell
$b64 = [Convert]::ToBase64String([IO.File]::ReadAllBytes("secrets/cookies.txt"))
fly secrets set YT_COOKIES_B64="$b64"
```

---

## Step 8 — Deploy

```powershell
fly deploy --ha=false
```

This builds the app and starts it. The first time takes a few minutes (it also loads a sample video). When it finishes, open your site:

```powershell
fly open
```

Done — your app is live.

---

## Step 9 — Check it works

```powershell
fly status      # shows your machines running
fly logs        # shows what the app is doing
```

Then upload a short video on the site to make sure storage and uploads work.

---

## If something goes wrong

- **"You are not logged in"** → your token isn't loaded in this window. Run the two lines from Step 2 again.
- **`fly` won't run / update error** → reinstall it (Step 1), open a new window.
- **"Name has already been taken"** → someone else has that app name. Pick another and update `fly.toml` (Step 3).
- **"not authorized" when creating the app or bucket** → you used a deploy token. Make a normal token (Step 2).
- **Upload fails in the browser (CORS error)** → the origin in Step 6 must match your address exactly (`https://` + name, no slash at the end).
- **Uploads disappear after a restart** → `STORAGE_PROVIDER` is still `local`. Do Step 5.
- **`/demo` page is empty** → the sample video didn't load (often missing YouTube cookies). Add `YT_COOKIES_B64` (Step 7) and deploy again.
- **YouTube links won't load** → add or refresh `YT_COOKIES_B64` (cookies expire in about 2–3 weeks). Uploaded files still work.

---

## Later: handy commands

```powershell
fly scale count worker=3      # handle more videos at once
fly scale count worker=0 clip=0   # turn off between sessions to save money
fly logs                      # watch what's happening
```

To redeploy after a code change, just run `fly deploy --ha=false` again.

---

## More detail (optional)

- **Storage options** — Tigris (above) is the easy one on Fly. Google Cloud and Amazon work too: [gcp.md](gcp.md#object-storage), [aws.md](aws.md#object-storage). Each bucket stays private; the app reaches it with keys, not public access.
- **GPU / big jobs** — Fly is CPU-only. For a GPU embedder, run the `clip` part on a GPU machine elsewhere and point `EMBED_SERVICE_URL` at it, using [`fly.slim.toml`](../fly.slim.toml) for the Fly side.
- **Auto-deploy from GitHub** — `.github/workflows/fly-deploy.yml` redeploys on each push, using the same token. Add a `FLY_API_TOKEN` repo secret (make a deploy-only token with `fly tokens create deploy -x 999999h`).
