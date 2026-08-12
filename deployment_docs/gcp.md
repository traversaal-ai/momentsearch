# Deploying MomentSearch to Google Cloud (GCP)

Same one Docker image as on Fly (`api`/`worker`/`clip`/`seed` entrypoints) — only the **compute**, `EMBED_SERVICE_URL` (no Fly auto-derive), and `STORAGE_PROVIDER=gcp_native` differ.

## What you need

- `DATABASE_URL` — Postgres (Cloud SQL or Neon).
- `QDRANT_URL` + `QDRANT_API_KEY` — Qdrant Cloud (collections `moments_l14`, `moments_text_openai` auto-created).
- `PREFECT_API_URL` + `PREFECT_API_KEY` — Prefect Cloud (worker polls outbound, no inbound ports).
- `OPENAI_API_KEY` — answer model, transcript embeddings, Whisper ASR (`TEXT_EMBED_PROVIDER=fastembed` for keyless transcripts).
- `STORAGE_PROVIDER=gcp_native` + `GOOGLE_CLOUD_*` (service-account JSON) + `STORAGE_BUCKET` — see [GCS bucket](#gcs-bucket).
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

## GCS bucket

`gcp_native` uses the Google SDK with a service-account JSON exploded into `GOOGLE_CLOUD_*`.

```bash
gcloud storage buckets create gs://momentsearch-media --location=us-central1
gcloud iam service-accounts create momentsearch-storage
gcloud storage buckets add-iam-policy-binding gs://momentsearch-media \
  --member=serviceAccount:momentsearch-storage@your-project.iam.gserviceaccount.com \
  --role=roles/storage.objectAdmin
gcloud iam service-accounts keys create key.json \
  --iam-account=momentsearch-storage@your-project.iam.gserviceaccount.com
```

Map `key.json` fields → env: `project_id`→`GOOGLE_CLOUD_PROJECT_ID`, `private_key_id`→`GOOGLE_CLOUD_PRIVATE_KEY_ID`, `private_key`→`GOOGLE_CLOUD_PRIVATE_KEY`, `client_email`→`GOOGLE_CLOUD_CLIENT_EMAIL`, `client_id`→`GOOGLE_CLOUD_CLIENT_ID`, `client_x509_cert_url`→`GOOGLE_CLOUD_CLIENT_X509_CERT_URL`; plus `STORAGE_BUCKET=momentsearch-media`.

CORS for browser uploads (presigned `PUT`s from the browser). Keep the bucket private — reads use presigned GET:

```bash
cat > cors.json <<'EOF'
[{ "origin": ["https://your-momentsearch-domain"], "method": ["PUT", "GET"],
   "responseHeader": ["Content-Type"], "maxAgeSeconds": 3600 }]
EOF
gcloud storage buckets update gs://momentsearch-media --cors-file=cors.json
```

## GPU CLIP (optional)

For large backfills, run clip on GPU: build the `Dockerfile.clip` GPU image (`--build-arg TORCH_INDEX_URL=https://download.pytorch.org/whl/cu121`) and run it on a GKE GPU node pool (`nvidia.com/gpu: 1` + node selector) or a GPU GCE VM (`--gpus all` + NVIDIA driver + `nvidia-container-toolkit`). Point `EMBED_SERVICE_URL` at it. If public/non-private, secure with `EMBED_SERVICE_TOKEN` (same value both sides) + HTTPS.

## Troubleshooting

- **clip unreachable / can't embed** → set `EMBED_SERVICE_URL` (no Fly auto-derive): `http://clip:8001` on compose, the clip Service name on GKE, or the clip URL on Cloud Run.
- **Browser uploads fail** (CORS error) → add the GCS bucket CORS rule for `PUT`/`GET` from your origin.
- **YouTube ingest fails** → datacenter IPs are bot-checked; supply `YT_COOKIES_B64` (expires in ~2–3 weeks).
- **Preflight not warning** → it only runs when `DEPLOY_ENV` is production/staging; set `DEPLOY_ENV=production` (add `STRICT_DEPLOY_CHECK=true` to abort).
