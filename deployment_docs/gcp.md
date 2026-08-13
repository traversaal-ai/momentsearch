# Deploy MomentSearch to Google Cloud

Two parts: **Part 1** sets up a storage bucket, **Part 2** runs the app. Do them in order. Commands use `bash` (or Cloud Shell).

---

## Before you start

1. **Get the shared keys first** — Neon, Qdrant, Prefect, OpenAI (and optional Gemini). See [DEPLOYMENT.md → Before you deploy](../DEPLOYMENT.md#before-you-deploy--get-these-keys-first). Put them in your `.env`.
2. A **Google Cloud account** with a **project** (note your project ID).
3. **Docker** installed, and the **`gcloud`** tool (installed in Step 1 below).

> One GCP-only setting: you must set **`EMBED_SERVICE_URL`** yourself (Fly sets it automatically, GCP doesn't). It's `http://clip:8001` on a single box. The guide shows where.

---

## Object storage

Your bucket holds uploaded videos, thumbnails and transcripts. The browser uploads straight to it, so it must be set up right.

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

Bucket names are **unique across all of Google Cloud**, so `momentsearch-media` is likely taken. Pick your own, like `momentsearch-media-yourname` (lowercase, digits, hyphens). Use the same name everywhere below.

### Option A — gcp_native (recommended)

**1. Create the private bucket:**

```bash
gcloud storage buckets create gs://momentsearch-media-yourname \
  --location=us-central1 --uniform-bucket-level-access --public-access-prevention
```

**2. Make a service account** (the app's login) **and give it access to just this bucket:**

```bash
gcloud iam service-accounts create momentsearch-storage

gcloud storage buckets add-iam-policy-binding gs://momentsearch-media-yourname \
  --member=serviceAccount:momentsearch-storage@YOUR_PROJECT_ID.iam.gserviceaccount.com \
  --role=roles/storage.objectAdmin
```

**3. Download its key file:**

```bash
gcloud iam service-accounts keys create key.json \
  --iam-account=momentsearch-storage@YOUR_PROJECT_ID.iam.gserviceaccount.com
```

**4. Copy 6 values from `key.json` into your `.env`:**

| From `key.json` | Into `.env` |
|---|---|
| `project_id` | `GOOGLE_CLOUD_PROJECT_ID` |
| `private_key_id` | `GOOGLE_CLOUD_PRIVATE_KEY_ID` |
| `private_key` | `GOOGLE_CLOUD_PRIVATE_KEY` |
| `client_email` | `GOOGLE_CLOUD_CLIENT_EMAIL` |
| `client_id` | `GOOGLE_CLOUD_CLIENT_ID` |
| `client_x509_cert_url` | `GOOGLE_CLOUD_CLIENT_X509_CERT_URL` |

Plus these two:

```dotenv
STORAGE_PROVIDER=gcp_native
STORAGE_BUCKET=momentsearch-media-yourname
```

> **Important about the private key:** keep it on **one line**, in quotes, with the `\n` as literal `\n` characters (exactly as it looks in the JSON) — **not** a real multi-line key. A real multi-line key is the #1 cause of "could not deserialize key data" on startup.

**5. Allow browser uploads (CORS):**

```bash
cat > cors.json <<'EOF'
[{ "origin": ["https://your-app-address", "http://localhost:8000"],
   "method": ["PUT", "GET"], "responseHeader": ["Content-Type"], "maxAgeSeconds": 3600 }]
EOF
gcloud storage buckets update gs://momentsearch-media-yourname --cors-file=cors.json
```

Replace `https://your-app-address` with wherever you'll serve the app (you can update this later once you know it — on a VM it's `http://YOUR_VM_IP:8000` at first).

### Option B — gcp (S3 style, 2 keys)

Same bucket as Option A. In the Cloud console go to **Cloud Storage → Settings → Interoperability → Create a key for a service account** (use `momentsearch-storage`). Then in `.env`:

```dotenv
STORAGE_PROVIDER=gcp
STORAGE_BUCKET=momentsearch-media-yourname
STORAGE_ACCESS_KEY_ID=GOOG1E...
STORAGE_SECRET_ACCESS_KEY=...
```

The bucket, access grant and CORS rule are the same as Option A — only the keys differ.

---

## Part 2 — Deploy

### Easiest: one VM with Docker

**1. Create the machine and open port 8000:**

```bash
gcloud compute instances create momentsearch \
  --zone=us-central1-a --machine-type=e2-standard-4 \
  --image-family=ubuntu-2204-lts --image-project=ubuntu-os-cloud \
  --boot-disk-size=50GB --tags=momentsearch
gcloud compute firewall-rules create momentsearch-api \
  --allow=tcp:8000 --target-tags=momentsearch --direction=INGRESS
```

**2. Connect and install Docker:**

```bash
gcloud compute ssh momentsearch --zone=us-central1-a
sudo apt-get update && sudo apt-get install -y docker.io docker-compose-plugin git
sudo usermod -aG docker $USER && exec sudo su - $USER
```

**3. Clone the project and create `.env`:**

```bash
git clone <your-repo-url> momentsearch && cd momentsearch
```

Put your values in `.env` — the shared keys (Neon, Qdrant, Prefect, OpenAI) plus your storage block from Part 1, plus these two lines:

```dotenv
EMBED_SERVICE_URL=http://clip:8001
DEPLOY_ENV=production
```

`EMBED_SERVICE_URL` must be set here (GCP doesn't auto-set it). `DEPLOY_ENV=production` turns on the safety check that warns if any setting is still local.

**4. Start it:**

```bash
docker compose up -d --build
docker compose logs -f seed        # wait for "sample corpus complete"
```

The first run takes a few minutes (it downloads the model and indexes a sample video).

**5. Open** `http://YOUR_VM_IP:8000/`. For a real domain + HTTPS, put nginx or Caddy in front.

**6. Check storage works:**

```bash
docker compose exec api python -c "from src import storage as s; k='_selftest/probe.txt'; print(s.put_bytes(k,b'ok','text/plain')); print(s.head(k)); s.delete_key(k); print('storage OK')"
```

Then upload a short video in the browser — that's the real test of the CORS rule.

### Bigger: GKE (for scaling)

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
