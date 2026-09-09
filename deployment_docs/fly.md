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

Run these two lines. The first saves your name; the second writes it into `fly.toml` for you (no manual editing):

```powershell
$APP = "momentsearch-yourname"     # <- change to your own name
(Get-Content fly.toml) -replace "^app = .*", "app = '$APP'" | Set-Content fly.toml
```

Check it worked:

```powershell
Select-String '^app =' fly.toml    # should show: app = 'momentsearch-yourname'
```

Keep this window open — the next steps reuse `$APP`. (Pick the name once; you can't rename it later.)

---

## Step 4 — Create the app

```powershell
fly apps create $APP --org personal
```

(This uses the `$APP` name from Step 3.)

---

## Step 5 — Make the storage bucket

This is where uploaded videos and thumbnails are stored. One command makes the bucket **and** sets up its keys for you — you don't create any keys by hand:

```powershell
fly storage create --name "$APP-media"
```

> **This needs a full personal Fly token on YOUR OWN org, with billing set up.** A deploy token, or someone else's org (like a shared/personal org you were added to), fails with `Not authorized … createextensiontosagreement`. If that happens: ask the org **owner** to run this one command for you, or use Google/Amazon storage instead (see the note below).

The bucket and its keys are now ready. You'll **switch storage on to Tigris in Step 7** — *after* your `.env` uploads — so the `STORAGE_PROVIDER=local` in your `.env` doesn't overwrite it.

> **Using Google Cloud (`gcp_native`) or Amazon (`aws`) instead of Tigris?** Then **skip Steps 5 AND 6** — they're Tigris-only. Make your bucket **and set its CORS** by following [gcp.md](gcp.md#object-storage) or [aws.md](aws.md#object-storage), using your Fly URL **`https://<your-app>.fly.dev`** as the CORS origin. Then do Step 7 — but **skip its last line** (`fly secrets set STORAGE_PROVIDER=flyio`), since your `.env` already sets `gcp_native`/`aws`.
>
> ⚠️ **Take only the bucket + keys + CORS from that guide — do NOT set `EMBED_SERVICE_URL`.** gcp.md/aws.md tell you to set `EMBED_SERVICE_URL=http://clip:8001`, but that's their **VM** address and **does not exist on Fly**. On Fly, leave `EMBED_SERVICE_URL` **blank** — Fly finds `clip` automatically. (If it's already set, `fly secrets unset EMBED_SERVICE_URL`.)

---

## Step 6 — Allow uploads from your website (Tigris only)

> **Used GCS or S3, not Tigris? Skip this Tigris step** and set CORS on your **own** bucket instead — run this in **PowerShell, in your repo folder**. It reads your Fly app name from `fly.toml` **and** your bucket from `.env`, so there's **nothing to type**. Then go to Step 7.
>
> **GCS (`gcp_native`):**
> ```powershell
> $APP    = (Select-String -Path fly.toml -Pattern '^app' | Select-Object -First 1).Line.Split("'")[1]
> $BUCKET = ((Select-String -Path .env -Pattern '^STORAGE_BUCKET=' | Select-Object -First 1).Line -replace '^STORAGE_BUCKET=','' -replace '#.*','').Trim().Trim('"')
> Write-Output "origin: https://$APP.fly.dev   bucket: $BUCKET"    # check it looks right
> @"
> [{ "origin": ["https://$APP.fly.dev"],
>    "method": ["PUT", "GET"], "responseHeader": ["Content-Type"], "maxAgeSeconds": 3600 }]
> "@ | Out-File -Encoding ascii cors.json
> gcloud storage buckets update gs://$BUCKET --cors-file=cors.json
> ```
> **S3 (`aws`):**
> ```powershell
> $APP    = (Select-String -Path fly.toml -Pattern '^app' | Select-Object -First 1).Line.Split("'")[1]
> $BUCKET = ((Select-String -Path .env -Pattern '^STORAGE_BUCKET=' | Select-Object -First 1).Line -replace '^STORAGE_BUCKET=','' -replace '#.*','').Trim().Trim('"')
> @"
> { "CORSRules": [{
>     "AllowedOrigins": ["https://$APP.fly.dev"],
>     "AllowedMethods": ["PUT","GET"], "AllowedHeaders": ["*"],
>     "ExposeHeaders": ["ETag"], "MaxAgeSeconds": 3000 }] }
> "@ | Out-File -Encoding ascii cors.json
> aws s3api put-bucket-cors --bucket $BUCKET --cors-configuration file://cors.json
> ```

Browser uploads need one setting turned on, or uploading a video will fail. Open the storage settings:

```powershell
fly storage dashboard
```

First print your app's web address (you'll paste it in a second):

```powershell
echo "https://$APP.fly.dev"
```

In the page that opens: click your bucket → **Settings** → **CORS**. Add a rule that allows:

- Methods: **PUT** and **GET**
- Origin: the address you just printed (like `https://momentsearch-yourname.fly.dev`)
- Headers: **all** (`*`)
- Expose header: **ETag**

Save it. (Everything else works without this — only *uploading* needs it.)

---

## Step 7 — Send your settings to Fly

> ⚠️ **On Fly, leave `EMBED_SERVICE_URL` BLANK in your `.env`.** Fly runs `clip` as its own machine and finds it **automatically** — you don't set an address. If your `.env` has `EMBED_SERVICE_URL=http://clip:8001` (that value comes from the *VM* guides, gcp.md/aws.md, and does **not** exist on Fly), your app will crash with *"clip unreachable / Name or service not known."* So **delete that line** before importing (or fix it later with `fly secrets unset EMBED_SERVICE_URL`).

This copies everything in your `.env` file up to Fly as secrets:

```powershell
Get-Content .env |
  ForEach-Object { $_ -replace '\s+#.*$','' } |                                   # strip inline "# comments" so they don't leak into a value
  Where-Object { $_ -match '^[A-Z_]+=.+' -and $_ -notmatch '^FLY_' -and $_ -notmatch '^YT_COOKIES_FILE=' } |
  fly secrets import
```

**YouTube links** need a **SocialKit key** (easiest — one key covers transcript + video, set `SOCIALKIT_API_KEY` in your `.env`/secrets), **cookies**, or a **proxy** to get past Fly's bot-checked IP (uploads work without any) — see **[README → YouTube ingest](../README.md#youtube-ingest)** for all three. For the cookies option, send them as a secret:

```powershell
$b64 = [Convert]::ToBase64String([IO.File]::ReadAllBytes("secrets/cookies.txt"))
fly secrets set YT_COOKIES_B64="$b64"
```

(Proxy instead? `fly secrets set YT_PROXY_URL=http://user:pass@host:port`.)

**Now switch storage on to Tigris** — **Tigris users only. Skip this if you used GCS/S3** (your `.env` already set `gcp_native`/`aws`, which the import above sent up). For Tigris, run this **after** the import so it overrides the `STORAGE_PROVIDER=local` that was in `.env`:

```powershell
fly secrets set STORAGE_PROVIDER=flyio
```

---

## Step 8 — Deploy

```powershell
fly deploy --ha=false
```

This builds the app and starts it. The first time takes a few minutes. When it finishes, open your site:

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
- **`/demo` page is empty** → the shipped corpus isn't in the image (see **The demo on a cloud deploy** in DEPLOYMENT.md). Either set `DEMO_LOCAL=false` + `SEED_MODE=ingest` to index the ten videos into your own stores — that needs `YT_COOKIES_B64` (Step 7) — or ship `demo_corpus/` yourself.
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
