# Deploying MomentSearch to Google Cloud (GCP)

Same one Docker image as on Fly (`api`/`worker`/`clip`/`seed` entrypoints) — only the **compute**, `EMBED_SERVICE_URL` (no Fly auto-derive), and the **storage provider** differ — GCS is the natural pick here, though S3 and Tigris work unchanged ([Object storage](#object-storage)).

## What you need

- `DATABASE_URL` — Postgres (Cloud SQL or Neon).
- `QDRANT_URL` + `QDRANT_API_KEY` — Qdrant Cloud (collections `moments_l14`, `moments_text_openai` auto-created).
- `PREFECT_API_URL` + `PREFECT_API_KEY` — Prefect Cloud (worker polls outbound, no inbound ports).
- `OPENAI_API_KEY` — answer model, transcript embeddings, Whisper ASR (`TEXT_EMBED_PROVIDER=fastembed` for keyless transcripts).
- **Object storage** — one private bucket for videos, thumbnails and transcripts. **GCS** (`STORAGE_PROVIDER=gcp_native` + `GOOGLE_CLOUD_*` + `STORAGE_BUCKET`) is the natural fit here; **S3** and **Tigris** are equally supported. See [Object storage](#object-storage).
- `EMBED_SERVICE_URL` — **must set explicitly off Fly**, or api/worker can't embed.
- `DEPLOY_ENV=production` — arms the preflight (`src/preflight.py`).
- Optional: `GEMINI_API_KEY` (speaker recognition), `YT_COOKIES_B64` (YouTube ingest; datacenter IPs are bot-checked).
- A GCP project, `gcloud` (`gcloud auth login`, `gcloud config set project <ID>`), and Docker.

## Path A — one GCE VM (docker compose)

One box runs the whole **fat** image (embeds in-process and serves clip).

1. **Create the VM + open port 8000:**

   ```bash
   gcloud compute instances create momentsearch \
     --zone=us-central1-a --machine-type=e2-standard-4 \
     --image-family=ubuntu-2204-lts --image-project=ubuntu-os-cloud \
     --boot-disk-size=50GB --tags=momentsearch
   gcloud compute firewall-rules create momentsearch-api \
     --allow=tcp:8000 --target-tags=momentsearch --direction=INGRESS
   ```

2. **SSH + install Docker:**

   ```bash
   gcloud compute ssh momentsearch --zone=us-central1-a
   sudo apt-get update && sudo apt-get install -y docker.io docker-compose-plugin git
   sudo usermod -aG docker $USER && exec sudo su - $USER
   ```

3. **Clone + write `.env`:**

   ```bash
   git clone <your-repo-url> momentsearch && cd momentsearch
   ```

   ```dotenv
   STORAGE_PROVIDER=gcp_native
   STORAGE_BUCKET=momentsearch-media
   GOOGLE_CLOUD_PROJECT_ID=your-project
   GOOGLE_CLOUD_PRIVATE_KEY_ID=...
   GOOGLE_CLOUD_PRIVATE_KEY="-----BEGIN PRIVATE KEY-----\n...\n-----END PRIVATE KEY-----\n"
   GOOGLE_CLOUD_CLIENT_EMAIL=momentsearch-storage@your-project.iam.gserviceaccount.com
   GOOGLE_CLOUD_CLIENT_ID=...
   GOOGLE_CLOUD_CLIENT_X509_CERT_URL=https://www.googleapis.com/robot/v1/metadata/x509/...
   EMBED_SERVICE_URL=http://clip:8001        # compose service name
   DATABASE_URL=postgresql://...             # Cloud SQL or Neon
   QDRANT_URL=https://...                    # Qdrant Cloud
   QDRANT_API_KEY=...
   PREFECT_API_URL=https://api.prefect.cloud/api/accounts/.../workspaces/...
   PREFECT_API_KEY=...
   OPENAI_API_KEY=sk-...
   DEPLOY_ENV=production
   ```

   Keep `GOOGLE_CLOUD_PRIVATE_KEY` quoted with literal `\n` (`config.py` un-escapes at load). YouTube: drop `cookies.txt` at `./secrets/cookies.txt` + set `YT_COOKIES_FILE=/app/secrets/cookies.txt` (compose mounts `./secrets` read-only; it's outside `./data` on purpose, that being the storage tree).

4. **Bring it up:**

   ```bash
   docker compose up -d --build
   docker compose logs -f seed
   ```

   `seed` runs once; api/worker wait via `service_completed_successfully`. Best-effort by default (`SEED_STRICT=true` to hard-gate, `SEED_SAMPLE_VIDEOS=false` to skip).

5. Open `http://<VM_EXTERNAL_IP>:8000/`. Front with a reverse proxy (Caddy/nginx) for HTTPS.

## Path B — GKE

Each process group is its own Deployment: `worker` scales on ingest, `api` on requests, `clip` a single warm model behind an internal Service.

1. **Push image(s) to Artifact Registry:**

   ```bash
   gcloud artifacts repositories create momentsearch \
     --repository-format=docker --location=us-central1
   gcloud auth configure-docker us-central1-docker.pkg.dev
   REPO=us-central1-docker.pkg.dev/your-project/momentsearch

   docker build -t $REPO/momentsearch:latest . && docker push $REPO/momentsearch:latest
   ```

   **FAT** = all three from one image. **SLIM** = api+worker slim (`--build-arg WITH_TORCH=false`, embed only over `EMBED_SERVICE_URL`) + clip from `Dockerfile.clip`:

   ```bash
   docker build --build-arg WITH_TORCH=false -t $REPO/momentsearch-slim:latest . && docker push $REPO/momentsearch-slim:latest
   docker build -f Dockerfile.clip -t $REPO/momentsearch-clip:latest . && docker push $REPO/momentsearch-clip:latest
   ```

2. **Cluster + secrets** (don't put `EMBED_SERVICE_URL` in the shared secret — set it per-Deployment):

   ```bash
   gcloud container clusters create-auto momentsearch --region=us-central1
   gcloud container clusters get-credentials momentsearch --region=us-central1
   kubectl create secret generic momentsearch-env --from-env-file=.env   # incl. STORAGE_PROVIDER=gcp_native + DEPLOY_ENV=production
   ```

3. **Deployments** — clip (ClusterIP `http://clip:8001`), api (LoadBalancer :8000), worker. `command` overrides the entrypoint:

   ```yaml
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
             image: $REPO/momentsearch:latest
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
             image: $REPO/momentsearch:latest
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
             image: $REPO/momentsearch:latest
             command: ["python","-m","src.worker"]
             envFrom: [{ secretRef: { name: momentsearch-env } }]
             env: [{ name: EMBED_SERVICE_URL, value: "http://clip:8001" }]
   ```

   ```bash
   kubectl apply -f k8s.yaml
   kubectl get service api -w        # wait for EXTERNAL-IP
   ```

   For SLIM: swap api/worker to `momentsearch-slim:latest` and clip to `momentsearch-clip:latest`.

4. **Seed as a GKE Job** (after clip is up):

   ```yaml
   apiVersion: batch/v1
   kind: Job
   metadata: { name: seed }
   spec:
     template:
       spec:
         restartPolicy: Never
         containers:
           - name: seed
             image: $REPO/momentsearch:latest
             command: ["python","-m","src.seed"]
             envFrom: [{ secretRef: { name: momentsearch-env } }]
             env: [{ name: EMBED_SERVICE_URL, value: "http://clip:8001" }]
     backoffLimit: 1
   ```

**Cloud Run** — api only (stateless, scales to zero); worker+clip belong on GKE/GCE. Point its `EMBED_SERVICE_URL` at the GKE/GCE clip:

```bash
gcloud run deploy momentsearch-api --image=$REPO/momentsearch:latest \
  --command=uvicorn --args=src.app:app,--host,0.0.0.0,--port,8000 \
  --port=8000 --allow-unauthenticated \
  --set-env-vars=STORAGE_PROVIDER=gcp_native,DEPLOY_ENV=production,EMBED_SERVICE_URL=https://clip.your-domain:8001
```

## Object storage

Uploaded videos, frame thumbnails and transcript JSON live in **one private bucket**,
keyed `{user_id}/{video_id}/` ([`src/storage.py`](../src/storage.py)). The browser PUTs
straight to the bucket with a presigned URL and reads thumbnails/playback with presigned
GETs — bytes never pass through the API.

### Pick a provider

All three are fully supported and identical at runtime; choose on where your
infrastructure already lives. GCS is the obvious pick on GCP (same project, same IAM, no
egress), not a technically better one.

| Provider | `STORAGE_PROVIDER` | Setup | Notes on GCP |
|---|---|---|---|
| **GCS** | `gcp_native` | bucket + service account | Native. Google SDK + SA key; no HMAC keys. |
| **GCS over S3** | `gcp` | bucket + HMAC key pair | Same bucket, S3 protocol — two credentials instead of seven env vars. |
| **S3** | `aws` | bucket + IAM user | Works fine; cross-cloud egress applies. Setup: [aws.md → Object storage](aws.md#object-storage). |
| **Tigris** | `flyio` | one command on Fly | Easiest if you already run a Fly app; usable from GCP with the `tid_`/`tsec_` keys. Setup: [fly.md → Object storage](fly.md#object-storage). |

For S3 or Tigris on GCP, set that provider's vars from the linked section instead of the
`GOOGLE_CLOUD_*` ones below — nothing else about this deploy changes.
`STORAGE_PROVIDER=local` is dev-only (no presigning, and GKE pods don't share a disk); the
preflight check flags it when `DEPLOY_ENV` is set.

### GCS (`STORAGE_PROVIDER=gcp_native`)

Google's SDK with a service-account JSON exploded into `GOOGLE_CLOUD_*` env vars.

**1. Bucket + service account + key:**

```bash
gcloud storage buckets create gs://momentsearch-media \
  --location=us-central1 \
  --uniform-bucket-level-access \
  --public-access-prevention

gcloud iam service-accounts create momentsearch-storage

# Object admin on THIS bucket only — read, write, delete, list. Not project-wide.
gcloud storage buckets add-iam-policy-binding gs://momentsearch-media \
  --member=serviceAccount:momentsearch-storage@your-project.iam.gserviceaccount.com \
  --role=roles/storage.objectAdmin

gcloud iam service-accounts keys create key.json \
  --iam-account=momentsearch-storage@your-project.iam.gserviceaccount.com
```

`--public-access-prevention` is what keeps the bucket private for good; presigned URLs
are the only way in or out. `objectAdmin` is the minimum that covers all six operations
the app performs (put, get, head, list-by-prefix, delete, delete-prefix) — `objectViewer`
breaks uploads, `admin` is more than needed.

**2. Map `key.json` → env vars:**

| `key.json` field | Env var |
|---|---|
| `project_id` | `GOOGLE_CLOUD_PROJECT_ID` |
| `private_key_id` | `GOOGLE_CLOUD_PRIVATE_KEY_ID` |
| `private_key` | `GOOGLE_CLOUD_PRIVATE_KEY` |
| `client_email` | `GOOGLE_CLOUD_CLIENT_EMAIL` |
| `client_id` | `GOOGLE_CLOUD_CLIENT_ID` |
| `client_x509_cert_url` | `GOOGLE_CLOUD_CLIENT_X509_CERT_URL` |

Plus `STORAGE_PROVIDER=gcp_native` and `STORAGE_BUCKET=momentsearch-media`. Keep
`GOOGLE_CLOUD_PRIVATE_KEY` **quoted with literal `\n`** exactly as it appears in the JSON
— `config.py` un-escapes them and strips stray surrounding quotes, so the same value works
in `.env`, a `kubectl` secret, and `fly secrets`
([`config.py:113-122`](../src/config.py#L113-L122)). A real multi-line PEM in a dotenv file
is the single most common cause of "could not deserialize key data" on boot.

Signing happens locally with that private key, so no extra IAM role is needed to mint
presigned URLs — but the SA still needs write access for the PUT the URL authorizes.

**3. CORS — required, or every browser upload fails.** The presigned `PUT` is
cross-origin from your page to `storage.googleapis.com`:

```bash
cat > cors.json <<'EOF'
[{ "origin": ["https://your-momentsearch-domain", "http://localhost:8000"],
   "method": ["PUT", "GET"],
   "responseHeader": ["Content-Type"],
   "maxAgeSeconds": 3600 }]
EOF
gcloud storage buckets update gs://momentsearch-media --cors-file=cors.json
gcloud storage buckets describe gs://momentsearch-media --format="default(cors_config)"
```

`origin` must match the serving origin exactly — scheme + host + port, no trailing slash.
List every origin you serve from.

**4. Verify the round trip** before blaming the worker for a stuck upload — this exercises
the real code path (credentials, bucket, presigning):

```bash
docker compose exec api python -c "from src import storage as s; k='_selftest/probe.txt'; print(s.put_bytes(k,b'ok','text/plain')); print(s.head(k)); print(s.presign_get(k)[:90]); s.delete_key(k); print('storage OK')"
# GKE: kubectl exec deploy/api -- python -c "...same..."
```

CORS isn't covered by that check (browser-only rule) — confirm it by uploading a small
video through the UI.

### GCS over the S3 API (`STORAGE_PROVIDER=gcp`)

Same bucket, reached through Google's S3-interoperability endpoint
(`https://storage.googleapis.com`) with an HMAC key pair — two secrets instead of the
seven `GOOGLE_CLOUD_*` vars, and it drops the `google-cloud-storage` dependency from the
path. Create the key under **Cloud Storage → Settings → Interoperability → Create a key
for a service account** (the same `momentsearch-storage` SA), then:

```dotenv
STORAGE_PROVIDER=gcp
STORAGE_BUCKET=momentsearch-media
STORAGE_ACCESS_KEY_ID=GOOG1E...
STORAGE_SECRET_ACCESS_KEY=...
```

Bucket creation, IAM and the CORS rule above are identical — only the credential type
changes.

## GPU CLIP (optional)

For large backfills, run clip on GPU: build the `Dockerfile.clip` GPU image (`--build-arg TORCH_INDEX_URL=https://download.pytorch.org/whl/cu121`) and run it on a GKE GPU node pool (`nvidia.com/gpu: 1` + node selector) or a GPU GCE VM (`--gpus all` + NVIDIA driver + `nvidia-container-toolkit`). Point `EMBED_SERVICE_URL` at it. If public/non-private, secure with `EMBED_SERVICE_TOKEN` (same value both sides) + HTTPS.

## Troubleshooting

- **clip unreachable / can't embed** → set `EMBED_SERVICE_URL` (no Fly auto-derive): `http://clip:8001` on compose, the clip Service name on GKE, or the clip URL on Cloud Run.
- **Browser uploads fail** (CORS error) → add the bucket CORS rule for `PUT`/`GET` from your origin ([Object storage](#object-storage)); `origin` must match scheme + host + port exactly.
- **Boot fails on "could not deserialize key data" / invalid PEM** → `GOOGLE_CLOUD_PRIVATE_KEY` was pasted as a real multi-line value. Keep it one line, quoted, with literal `\n`.
- **`403 does not have storage.objects.create`** → the SA is bound to the project but not the bucket, or has `objectViewer`. Re-run the `add-iam-policy-binding` with `roles/storage.objectAdmin` on the bucket.
- **Thumbnails 404 / delete leaves objects** → prefix listing needs list permission on the bucket; `objectAdmin` on the *bucket* (not just objects inherited from elsewhere) covers it.
- **Uploads vanish or `worker` can't find the file** → `STORAGE_PROVIDER` is still `local`; GKE pods and the API don't share a disk.
- **YouTube ingest fails** → datacenter IPs are bot-checked; supply `YT_COOKIES_B64` (expires in ~2–3 weeks).
- **Preflight not warning** → it only runs when `DEPLOY_ENV` is production/staging; set `DEPLOY_ENV=production` (add `STRICT_DEPLOY_CHECK=true` to abort).
