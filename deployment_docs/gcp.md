# Deploy MomentSearch to Google Cloud

Two parts: **Part 1** sets up a storage bucket, **Part 2** runs the app. Do them in order. Commands use `bash` (or Cloud Shell).

---

## Before you start

1. **Get the shared keys first** — Neon, Qdrant, Prefect, OpenAI (and optional Gemini). See [DEPLOYMENT.md → the keys you need](../DEPLOYMENT.md#2-the-keys-every-deploy-needs). Put them in your `.env`.
2. A **Google Cloud account** with a **project** (note your project ID).
3. **Docker** installed, and the **`gcloud`** tool (installed in Step 1 below).

> One GCP-only setting: you must set **`EMBED_SERVICE_URL`** yourself (Fly sets it automatically, GCP doesn't). It's `http://clip:8001` on a single box. The guide shows where.

---

## Object storage

Your bucket holds uploaded videos, thumbnails and transcripts. The browser uploads straight to it, so it must be set up right. **GCS** is the native pick on Google Cloud, and what this guide sets up. But storage is a **free choice** — you can point GCP at **S3** ([aws.md](aws.md#object-storage)) or **Tigris** ([fly.md](fly.md#step-5--make-the-storage-bucket)) instead; only the keys change, nothing else in the deploy.

### First: which Google storage option?

There are **two ways** to use a Google bucket. Same bucket either way — only the login keys differ:

| Option | `STORAGE_PROVIDER` | What it uses | How many values in `.env` |
|---|---|---|---|
| **gcp_native** (recommended) | `gcp_native` | Google's own login (a **service-account key**) | **6** (`GOOGLE_CLOUD_*`) |
| **gcp** (S3 style) | `gcp` | Google's "pretend it's Amazon S3" mode (an **HMAC key pair**) | **2** (`STORAGE_ACCESS_KEY_ID` + `_SECRET`) |

**In plain words:** `gcp_native` is the normal Google way — you download one key file and copy 6 values out of it. `gcp` is a shortcut that treats the bucket like Amazon S3 — just 2 keys, but a second key type to manage. **Use `gcp_native` unless you have a reason not to.**

### Step 1 — Set up the gcloud tool (needed for both)

Install it:

```bash
curl https://sdk.cloud.google.com | bash && exec -l $SHELL   # macOS / Linux
```
On Windows, use the installer at <https://cloud.google.com/sdk/docs/install>, then open a new terminal.

Log in and pick your project:

```bash
gcloud auth login
gcloud config set project YOUR_PROJECT_ID
```

Turn on the services you'll use (run once):

```bash
gcloud services enable storage.googleapis.com compute.googleapis.com
```

Check it works: `gcloud auth list` (shows your account) and `docker version`.

### Step 2 — Pick a bucket name

Bucket names are **globally unique** in Google Cloud — the name lives in one namespace shared by everyone, so no two accounts can use the same name. **Try `momentsearch-media`** — if nobody's taken it, it's yours. If `create` fails with **`409 bucket already exists`**, just add something unique like `momentsearch-media-yourname` (lowercase, digits, hyphens). **The commands below call your bucket `YOUR_BUCKET`** — replace it with whatever name you picked here.

### Option A — gcp_native (recommended)

> **Two different names in these commands — don't mix them up:**
> - **`YOUR_BUCKET`** — the bucket name you picked in Step 2 (e.g. `momentsearch-media`). Replace `YOUR_BUCKET` everywhere below with it.
> - **`momentsearch-storage`** — a **service account** (the app's login that's allowed to use the bucket). It's a *separate* thing from the bucket; leave this name as-is — it lives inside your own project, so everyone can use the same name.
> - **`YOUR_PROJECT_ID`** — your Google Cloud project id.

**1. Create the private bucket:**

```bash
gcloud storage buckets create gs://YOUR_BUCKET \
  --location=us-central1 --uniform-bucket-level-access --public-access-prevention
```

**2. Make the service account** (the app's login) **and give it access to just this bucket:**

```bash
gcloud iam service-accounts create momentsearch-storage

gcloud storage buckets add-iam-policy-binding gs://YOUR_BUCKET \
  --member=serviceAccount:momentsearch-storage@YOUR_PROJECT_ID.iam.gserviceaccount.com \
  --role=roles/storage.objectAdmin
```

**3. Download its key file:**

```bash
gcloud iam service-accounts keys create key.json \
  --iam-account=momentsearch-storage@YOUR_PROJECT_ID.iam.gserviceaccount.com
```

This `key.json` is the app's **service-account key** — its private login to the bucket. Keep it safe (Step 4 copies its values into `.env` and deletes the file). *What a service-account key is →* [GCP: create & manage keys](https://cloud.google.com/iam/docs/keys-create-delete) · [video tutorial](https://www.youtube.com/results?search_query=gcp+service+account+key+json+tutorial).

**4. Put the 6 keys into `.env`, then delete `key.json`.** Two ways — pick one:

**Either — run this** (reads `key.json`, writes all 6 lines, deletes the file):

```bash
python3 - <<'PY' >> .env && rm key.json
import json
d = json.load(open("key.json"))
print("GOOGLE_CLOUD_PROJECT_ID=" + d["project_id"])
print("GOOGLE_CLOUD_PRIVATE_KEY_ID=" + d["private_key_id"])
print("GOOGLE_CLOUD_PRIVATE_KEY=" + json.dumps(d["private_key"]))
print("GOOGLE_CLOUD_CLIENT_EMAIL=" + d["client_email"])
print("GOOGLE_CLOUD_CLIENT_ID=" + d["client_id"])
print("GOOGLE_CLOUD_CLIENT_X509_CERT_URL=" + d["client_x509_cert_url"])
PY
```

**Or — by hand:** open `key.json` and copy each field into the matching line in `.env`:

```dotenv
GOOGLE_CLOUD_PROJECT_ID=<project_id>
GOOGLE_CLOUD_PRIVATE_KEY_ID=<private_key_id>
GOOGLE_CLOUD_PRIVATE_KEY="<private_key>"
GOOGLE_CLOUD_CLIENT_EMAIL=<client_email>
GOOGLE_CLOUD_CLIENT_ID=<client_id>
GOOGLE_CLOUD_CLIENT_X509_CERT_URL=<client_x509_cert_url>
```

Keep `GOOGLE_CLOUD_PRIVATE_KEY` on **one line, in quotes, with the `\n` left as literal `\n`** (exactly as it appears in the JSON) — a real multi-line key is the #1 cause of "could not deserialize key data" on boot. Then delete the file so the secret isn't left on disk:

```bash
rm key.json
```

**Either way**, set the storage provider + bucket. Your `.env` (copied from `.env.example`) has `STORAGE_PROVIDER=local` — **change that line to `gcp_native`** and set the bucket:

```dotenv
STORAGE_PROVIDER=gcp_native      # change this from the default `local`
STORAGE_BUCKET=YOUR_BUCKET
```

**5. Allow browser uploads (CORS).** Browser uploads go **straight to the bucket**, so it must allow requests from your app's web page — without this, uploading a video fails in the browser (search + playback still work).

> **You don't know your app's address yet** — it comes from Part 2, once you deploy. On the one-VM path it's `http://YOUR_VM_IP:8000` (the VM's external IP). So do this in **two passes**: run the command now with just `localhost` (below), then **run it again after Part 2** with your real address added to `origin`.

```bash
cat > cors.json <<'EOF'
[{ "origin": ["http://localhost:8000"],
   "method": ["PUT", "GET"], "responseHeader": ["Content-Type"], "maxAgeSeconds": 3600 }]
EOF
gcloud storage buckets update gs://YOUR_BUCKET --cors-file=cors.json
```

After Part 2, add your real address — edit `origin` to include it and re-run the two commands:

```
"origin": ["https://your-app.example.com", "http://YOUR_VM_IP:8000", "http://localhost:8000"]
```

The address must match **exactly** — scheme (`http`/`https`) + host + port, no trailing slash.

### Option B — gcp (S3 style, 2 keys)

Same bucket as Option A. In the Cloud console go to **Cloud Storage → Settings → Interoperability → Create a key for a service account** (use `momentsearch-storage`). Then in `.env`:

```dotenv
STORAGE_PROVIDER=gcp             # change this from the default `local`
STORAGE_BUCKET=YOUR_BUCKET
STORAGE_ACCESS_KEY_ID=GOOG1E...
STORAGE_SECRET_ACCESS_KEY=...
```

The bucket, access grant and CORS rule are the same as Option A — only the keys differ.

---

## Part 2 — Deploy

### Easiest: one VM with Docker

This uses **two machines** — and knowing which is which is the whole trick:

- 💻 **your computer** — where you run the `gcloud` CLI (PowerShell / terminal)
- ☁️ **the VM** — a Linux box you connect into; it runs Docker + the app

**Every step below is tagged 💻 or ☁️.** Anything tagged ☁️ runs in the VM's **Linux** shell — do **not** paste it into PowerShell.

**1. 💻 On your computer — create the VM and open port 8000:**

```bash
gcloud compute instances create momentsearch \
  --zone=us-central1-a --machine-type=e2-standard-4 \
  --image-family=ubuntu-2204-lts --image-project=ubuntu-os-cloud \
  --boot-disk-size=50GB --tags=momentsearch
gcloud compute firewall-rules create momentsearch-api \
  --allow=tcp:8000 --target-tags=momentsearch --direction=INGRESS
```

`--machine-type=e2-standard-4` = **4 vCPU / 16 GB RAM** and `--boot-disk-size=50GB` = 50 GB disk. You need roughly **8 GB RAM + 50 GB disk** (the CLIP model + ffmpeg are the heavy parts; smaller runs out of memory) — this size covers it. *Pick a different size →* [GCE machine types](https://cloud.google.com/compute/docs/machine-resource) · [video tutorial](https://www.youtube.com/results?search_query=gcp+compute+engine+machine+type+tutorial).

**2. 💻 On your computer — connect into the VM:**

```bash
gcloud compute ssh momentsearch --zone=us-central1-a
```

Your prompt now changes to something like `you@momentsearch:~$`. **From here on, every ☁️ command runs INSIDE the VM** (a Linux shell) — not in PowerShell. (To leave the VM later, type `exit`.)

**3. ☁️ On the VM — install Docker and clone the project:**

```bash
sudo apt-get update && sudo apt-get install -y docker.io docker-compose-plugin git
sudo usermod -aG docker $USER && exec sudo su - $USER
git clone <your-repo-url> momentsearch && cd momentsearch
```

**4. Get your `.env` onto the VM.** Two ways — pick one:

**A — you already filled in `.env` on your computer?** Just copy it up. 💻 Run this **on your computer**, from your local repo folder (Step 3's clone must be done first):

```bash
gcloud compute scp .env momentsearch:~/momentsearch/.env --zone=us-central1-a
```

**B — starting fresh on the VM?** ☁️ Make it from the template and edit it there:

```bash
cp .env.example .env     # creates .env from the template
nano .env                # fill it in  (or vim; save in nano: Ctrl+O, Enter, Ctrl+X)
```

Fill the shared keys (Neon, Qdrant, Prefect, OpenAI — [DEPLOYMENT.md → the keys you need](../DEPLOYMENT.md#2-the-keys-every-deploy-needs)) and your storage block from Part 1.

**Either way, that `.env` needs DEPLOY values** (not the local preset): `STORAGE_PROVIDER` = your bucket (not `local`), real `DATABASE_URL`/`QDRANT_URL` (not `qdrant`/`postgres` compose hosts), no `COMPOSE_PROFILES`, and `EMBED_SERVICE_URL=http://clip:8001`. `DEPLOY_ENV=production` warns if a local setting slips through.

Check it landed — ☁️ on the VM:

```bash
ls -la ~/momentsearch/.env      # should show the file with a real size (not missing / 0 bytes)
```

> **YouTube links** need a **SocialKit key** (easiest — one key covers transcript + video, set `SOCIALKIT_API_KEY` in your `.env`), cookies, or a proxy (the VM's IP is bot-checked; uploads work without any). See **[README → YouTube ingest](../README.md#youtube-ingest)** for all three. For the **cookies** option: export a `cookies.txt`, then copy it up like your `.env`:
> ```bash
> # ☁️ on the VM — make the folder and make sure you own it:
> mkdir -p ~/momentsearch/secrets
> sudo chown -R $USER:$USER ~/momentsearch/secrets
> # 💻 on your computer — copy the file up:
> gcloud compute scp cookies.txt momentsearch:~/momentsearch/secrets/cookies.txt --zone=us-central1-a
> ```
> …and set `YT_COOKIES_FILE=/app/secrets/cookies.txt` in `.env`. (Prefer a proxy instead? Set `YT_PROXY_URL=...` — see the README.)

**5. ☁️ On the VM — start it:**

```bash
docker compose up -d --build
docker compose logs -f seed        # wait for "sample corpus complete"
```

The first run takes a few minutes (it downloads the model and indexes a sample video).

**6. 💻 On your computer — open** `http://YOUR_VM_IP:8000/` in a browser. `YOUR_VM_IP` is the VM's external IP — get it with `gcloud compute instances list`.

> **Now finish CORS.** Back in Option A Step 5 you allowed only `localhost`. To let browser **uploads** work from the live app, re-run the CORS command with this address added:
> ```bash
> cat > cors.json <<'EOF'
> [{ "origin": ["http://YOUR_VM_IP:8000", "http://localhost:8000"],
>    "method": ["PUT", "GET"], "responseHeader": ["Content-Type"], "maxAgeSeconds": 3600 }]
> EOF
> gcloud storage buckets update gs://YOUR_BUCKET --cors-file=cors.json
> ```

For a domain + HTTPS, put nginx or Caddy in front (and add that origin to CORS too).

**7. ☁️ On the VM — check storage works:**

```bash
docker compose exec api python -c "from src import storage as s; k='_selftest/probe.txt'; print(s.put_bytes(k,b'ok','text/plain')); print(s.head(k)); s.delete_key(k); print('storage OK')"
```

Then upload a short video in the browser — that's the real test of the CORS rule.

### Optional — put it on a domain with HTTPS (Caddy)

The steps above serve plain `http://YOUR_VM_IP:8000`. For real users you want `https://yourdomain.com` (encrypted, a real name). The easiest way is **Caddy** — it fetches a free certificate automatically.

**1. 💻 At your domain registrar — point the domain at the VM:** add a DNS **A record** → `yourdomain.com` → `YOUR_VM_IP`.

**2. 💻 On your computer — open ports 80 + 443** (Caddy needs them for the cert + HTTPS):

```bash
gcloud compute firewall-rules create momentsearch-web \
  --allow=tcp:80,tcp:443 --target-tags=momentsearch --direction=INGRESS
```

**3. ☁️ On the VM — install Caddy:**

```bash
sudo apt-get install -y debian-keyring debian-archive-keyring apt-transport-https curl
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' | sudo gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' | sudo tee /etc/apt/sources.list.d/caddy-stable.list
sudo apt-get update && sudo apt-get install -y caddy
```

**4. ☁️ On the VM — point Caddy at your app.** Put this in `/etc/caddy/Caddyfile` (replace the domain), then reload:

```
yourdomain.com {
    reverse_proxy localhost:8000
}
```

```bash
sudo systemctl reload caddy
```

Caddy gets the HTTPS certificate on its own (give DNS a few minutes to propagate first). Open `https://yourdomain.com` — done.

**5. Update CORS** — add `https://yourdomain.com` to the bucket's allowed origins (re-run the CORS command from **Step 6** with it), or browser uploads fail from the new address.

### Scaling — GKE

> **New to this?** **GKE** (Google Kubernetes Engine) is Google's managed **Kubernetes** — a system that runs your containers across **many machines** and scales them automatically, instead of one VM. It's more setup than the one-VM path above, and **only needed for heavy traffic** — most people can skip it. *Learn it →* [What is Kubernetes](https://kubernetes.io/docs/concepts/overview/) · [GKE docs](https://cloud.google.com/kubernetes-engine/docs) · [video tutorial](https://www.youtube.com/results?search_query=kubernetes+gke+beginner+tutorial).

For a scaled setup, run each part as its own Kubernetes Deployment. In short:

1. Push the image to Artifact Registry.
2. `kubectl create secret generic momentsearch-env --from-env-file=.env`.
3. Deploy `clip` (internal Service on 8001), `api` (LoadBalancer on 8000), `worker`, and a one-shot `seed` Job. Set `EMBED_SERVICE_URL=http://clip:8001` on `api` and `worker`.
4. Add the LoadBalancer's IP to the bucket CORS rule.

The full manifest is below (optional — skip it if the one-VM path is enough).

<details>
<summary>Full GKE manifest and commands</summary>

```bash
gcloud artifacts repositories create momentsearch --repository-format=docker --location=us-central1
gcloud auth configure-docker us-central1-docker.pkg.dev
REPO=us-central1-docker.pkg.dev/YOUR_PROJECT_ID/momentsearch
docker build -t $REPO/momentsearch:latest . && docker push $REPO/momentsearch:latest

gcloud container clusters create-auto momentsearch --region=us-central1
gcloud container clusters get-credentials momentsearch --region=us-central1
kubectl create secret generic momentsearch-env --from-env-file=.env
```

> **YouTube on GKE:** pods have no `./secrets` mount, so use **`YT_COOKIES_B64`** (base64 of `cookies.txt`) in your `.env` *before* creating the secret — not `YT_COOKIES_FILE`. Make it with `base64 -w0 cookies.txt`.

```yaml
# k8s.yaml — clip (internal), api (LoadBalancer), worker
apiVersion: apps/v1
kind: Deployment
metadata: { name: clip }
spec:
  replicas: 1
  selector: { matchLabels: { app: clip } }
  template:
    metadata: { labels: { app: clip } }
    spec:
      containers:
        - name: clip
          image: REPLACE_REPO/momentsearch:latest
          command: ["uvicorn","src.clip_service:app","--host","0.0.0.0","--port","8001"]
          ports: [{ containerPort: 8001 }]
          envFrom: [{ secretRef: { name: momentsearch-env } }]
---
apiVersion: v1
kind: Service
metadata: { name: clip }
spec:
  selector: { app: clip }
  ports: [{ port: 8001, targetPort: 8001 }]
---
apiVersion: apps/v1
kind: Deployment
metadata: { name: api }
spec:
  replicas: 2
  selector: { matchLabels: { app: api } }
  template:
    metadata: { labels: { app: api } }
    spec:
      containers:
        - name: api
          image: REPLACE_REPO/momentsearch:latest
          command: ["uvicorn","src.app:app","--host","0.0.0.0","--port","8000"]
          ports: [{ containerPort: 8000 }]
          envFrom: [{ secretRef: { name: momentsearch-env } }]
          env: [{ name: EMBED_SERVICE_URL, value: "http://clip:8001" }]
---
apiVersion: v1
kind: Service
metadata: { name: api }
spec:
  type: LoadBalancer
  selector: { app: api }
  ports: [{ port: 80, targetPort: 8000 }]
---
apiVersion: apps/v1
kind: Deployment
metadata: { name: worker }
spec:
  replicas: 2
  selector: { matchLabels: { app: worker } }
  template:
    metadata: { labels: { app: worker } }
    spec:
      containers:
        - name: worker
          image: REPLACE_REPO/momentsearch:latest
          command: ["python","-m","src.worker"]
          envFrom: [{ secretRef: { name: momentsearch-env } }]
          env: [{ name: EMBED_SERVICE_URL, value: "http://clip:8001" }]
```

```bash
kubectl apply -f k8s.yaml
kubectl get service api -w      # wait for EXTERNAL-IP, then add it to the bucket CORS

# seed once, after clip is up:
kubectl run seed --image=$REPO/momentsearch:latest --restart=Never \
  --overrides='{"spec":{"containers":[{"name":"seed","image":"'$REPO'/momentsearch:latest","command":["python","-m","src.seed"],"envFrom":[{"secretRef":{"name":"momentsearch-env"}}],"env":[{"name":"EMBED_SERVICE_URL","value":"http://clip:8001"}]}]}}'
```

**Cloud Run** can host just the `api` (scales to zero); keep `worker` + `clip` on GKE/GCE and point the api's `EMBED_SERVICE_URL` at the clip service.

</details>

### GPU (optional)

For faster embedding, run only the `clip` part on a GPU machine (build `Dockerfile.clip` with `--build-arg TORCH_INDEX_URL=https://download.pytorch.org/whl/cu121`) and point `EMBED_SERVICE_URL` at it.

---

## If something goes wrong

- **"API has not been used" / `SERVICE_DISABLED`** → run the `gcloud services enable` command (Step 1).
- **`409 bucket already exists`** → the bucket name is taken (they're global). Pick another (Step 2).
- **"could not deserialize key data" on startup** → your `GOOGLE_CLOUD_PRIVATE_KEY` is a real multi-line key. Put it on one line, quoted, with literal `\n`.
- **Browser upload fails (CORS error)** → your app's address isn't in the bucket CORS rule, or doesn't match exactly (scheme + host + port, no trailing slash). Fix Step 5.
- **`403 does not have storage.objects.create`** → the service account isn't bound to the bucket with `objectAdmin`. Re-run the access command (Option A step 2).
- **Uploads vanish / worker can't find the file** → `STORAGE_PROVIDER` is still `local`. Set it to `gcp_native`.
- **Can't embed / clip unreachable** → set `EMBED_SERVICE_URL` (`http://clip:8001` on one box).
