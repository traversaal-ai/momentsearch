# Deploying MomentSearch to AWS

Same Docker image as Fly (four entrypoints: `api`, `worker`, `clip`, `seed`); only **compute**, **`EMBED_SERVICE_URL`** (no auto-derive off Fly — set it), and **`STORAGE_PROVIDER=aws`** (S3 instead of GCS/Tigris) change.

## What you need

- `DATABASE_URL` — Postgres (RDS or keep your Neon URL).
- `QDRANT_URL` + `QDRANT_API_KEY` — Qdrant Cloud (or self-host). Collections `moments_l14` + `moments_text_openai` auto-create.
- `PREFECT_API_URL` + `PREFECT_API_KEY` — Prefect Cloud (worker polls outbound, no inbound ports).
- `OPENAI_API_KEY` — answer model, transcript embeddings, Whisper. (Set `TEXT_EMBED_PROVIDER=fastembed` to drop it for the text branch.)
- `STORAGE_PROVIDER=aws` + `STORAGE_BUCKET` + `STORAGE_REGION` + `STORAGE_ACCESS_KEY_ID` + `STORAGE_SECRET_ACCESS_KEY` — S3.
- `EMBED_SERVICE_URL` — **must set off Fly** (`http://clip:8001` on one box; clip internal DNS on ECS).
- `DEPLOY_ENV=production` — arms the preflight local-settings check (`STRICT_DEPLOY_CHECK=true` to abort instead of warn).
- Optional: `GEMINI_API_KEY` (speaker recognition); `YT_COOKIES_B64` (YouTube — datacenter IPs are bot-checked).
- AWS account + `aws` CLI (`aws configure` done) + Docker.

## Path A — one EC2 box (docker compose)

Fat image runs api + worker + clip + seed on one box; slim not needed.

**1. Launch EC2** — Ubuntu 22.04, `t3.large`+. Security group: allow inbound TCP **8000** (clients) and **22** (SSH); leave 8001 closed.

**2. Install Docker + clone**

```bash
sudo apt-get update && sudo apt-get install -y docker.io docker-compose-plugin git
sudo usermod -aG docker $USER && newgrp docker
git clone <your-repo-url> momentsearch && cd momentsearch
```

**3. Write `.env`**

```bash
cat > .env <<'EOF'
DEPLOY_ENV=production
STORAGE_PROVIDER=aws
STORAGE_BUCKET=momentsearch-media
STORAGE_REGION=us-east-1
STORAGE_ACCESS_KEY_ID=AKIA...
STORAGE_SECRET_ACCESS_KEY=...
EMBED_SERVICE_URL=http://clip:8001
DATABASE_URL=postgresql://user:pass@host:5432/db?sslmode=require
QDRANT_URL=https://xyz.cloud.qdrant.io:6333
QDRANT_API_KEY=...
PREFECT_API_URL=https://api.prefect.cloud/api/accounts/<acct>/workspaces/<ws>
PREFECT_API_KEY=...
OPENAI_API_KEY=sk-...
GEMINI_API_KEY=...
EOF
```

For YouTube: drop `cookies.txt` at `./data/cookies.txt` and add `YT_COOKIES_FILE=/app/data/cookies.txt`.

**4. Bring it up**

```bash
docker compose up -d --build
docker compose logs -f seed
```

Seed indexes the sample talk and exits; api/worker wait for it. Best-effort by default — `SEED_STRICT=true` to hard-gate, `SEED_SAMPLE_VIDEOS=false` to skip.

**5. Open** `http://<ec2-public-ip>:8000/`. Front with an ALB or nginx/Caddy for a domain + HTTPS.

## Path B — ECS/Fargate

Three services (`api`, `worker`, `clip`) from the one image, each with a command override. ALB → `api:8000`; `clip` internal only via Service Connect/Cloud Map; secrets via Secrets Manager.

**FAT vs SLIM:** FAT = all 3 from `momentsearch`. SLIM = api+worker from `momentsearch-slim` (no torch), clip from `momentsearch-clip` (`Dockerfile.clip`) — `EMBED_SERVICE_URL` → clip service is then mandatory.

**1. Push to ECR**

```bash
aws ecr create-repository --repository-name momentsearch
aws ecr create-repository --repository-name momentsearch-slim   # slim path only
aws ecr create-repository --repository-name momentsearch-clip   # slim path only

ACCT=$(aws sts get-caller-identity --query Account --output text)
REGION=us-east-1
REG=$ACCT.dkr.ecr.$REGION.amazonaws.com
aws ecr get-login-password --region $REGION | docker login --username AWS --password-stdin $REG

# FAT
docker build -t $REG/momentsearch:latest . && docker push $REG/momentsearch:latest

# SLIM
docker build --build-arg WITH_TORCH=false -t $REG/momentsearch-slim:latest . && docker push $REG/momentsearch-slim:latest
docker build -f Dockerfile.clip -t $REG/momentsearch-clip:latest . && docker push $REG/momentsearch-clip:latest
```

**2. Secrets → Secrets Manager**

```bash
aws secretsmanager create-secret --name momentsearch/env --secret-string '{
  "DATABASE_URL": "postgresql://user:pass@your-rds:5432/db?sslmode=require",
  "QDRANT_URL": "https://xyz.cloud.qdrant.io:6333",
  "QDRANT_API_KEY": "...",
  "PREFECT_API_URL": "https://api.prefect.cloud/api/accounts/<acct>/workspaces/<ws>",
  "PREFECT_API_KEY": "...",
  "OPENAI_API_KEY": "sk-...",
  "STORAGE_SECRET_ACCESS_KEY": "..."
}'
```

Non-secret config (`STORAGE_PROVIDER`, `STORAGE_BUCKET`, `STORAGE_REGION`, `DEPLOY_ENV`, `EMBED_SERVICE_URL`) goes in plain `environment`. Give the **execution role** `secretsmanager:GetValue`, the **task role** S3 access (or set `STORAGE_ACCESS_KEY_ID`/`STORAGE_SECRET_ACCESS_KEY`).

**3. Three services** — same image, different `command`:

| Service | FAT image | SLIM image | Command | Networking |
|---|---|---|---|---|
| **api** | `momentsearch` | `momentsearch-slim` | `uvicorn src.app:app --host 0.0.0.0 --port 8000` | behind ALB, port **8000** |
| **worker** | `momentsearch` | `momentsearch-slim` | `python -m src.worker` | no ports (outbound) |
| **clip** | `momentsearch` | `momentsearch-clip` | `uvicorn src.clip_service:app --host 0.0.0.0 --port 8001` | Service Connect, internal |

Enable Service Connect, give clip a discovery name on 8001, then on **api and worker**:

```
EMBED_SERVICE_URL=http://clip.momentsearch.local:8001
```

Minimal api container fragment (fat):

```json
{
  "name": "api",
  "image": "<ACCT>.dkr.ecr.us-east-1.amazonaws.com/momentsearch:latest",
  "command": ["uvicorn","src.app:app","--host","0.0.0.0","--port","8000"],
  "portMappings": [{ "containerPort": 8000, "name": "api" }],
  "environment": [
    { "name": "STORAGE_PROVIDER", "value": "aws" },
    { "name": "STORAGE_BUCKET",   "value": "momentsearch-media" },
    { "name": "STORAGE_REGION",   "value": "us-east-1" },
    { "name": "EMBED_SERVICE_URL","value": "http://clip.momentsearch.local:8001" },
    { "name": "DEPLOY_ENV",       "value": "production" }
  ],
  "secrets": [
    { "name": "DATABASE_URL",    "valueFrom": "arn:aws:secretsmanager:...:momentsearch/env:DATABASE_URL::" },
    { "name": "QDRANT_URL",      "valueFrom": "arn:aws:secretsmanager:...:momentsearch/env:QDRANT_URL::" },
    { "name": "QDRANT_API_KEY",  "valueFrom": "arn:aws:secretsmanager:...:momentsearch/env:QDRANT_API_KEY::" },
    { "name": "PREFECT_API_URL", "valueFrom": "arn:aws:secretsmanager:...:momentsearch/env:PREFECT_API_URL::" },
    { "name": "PREFECT_API_KEY", "valueFrom": "arn:aws:secretsmanager:...:momentsearch/env:PREFECT_API_KEY::" },
    { "name": "OPENAI_API_KEY",  "valueFrom": "arn:aws:secretsmanager:...:momentsearch/env:OPENAI_API_KEY::" }
  ]
}
```

worker/clip are the same shape with a different `command` (and slim `image`); clip needs no ALB.

**4. Seed once**

```bash
aws ecs run-task \
  --cluster momentsearch \
  --task-definition momentsearch-api \
  --launch-type FARGATE \
  --network-configuration '{"awsvpcConfiguration":{"subnets":["subnet-..."],"securityGroups":["sg-..."],"assignPublicIp":"ENABLED"}}' \
  --overrides '{"containerOverrides":[{"name":"api","command":["python","-m","src.seed"]}]}'
```

Scale ingest: `aws ecs update-service --cluster momentsearch --service worker --desired-count 3` (0 when idle).

## S3 bucket

```bash
aws s3api create-bucket --bucket momentsearch-media --region us-east-1
# non-us-east-1: add --create-bucket-configuration LocationConstraint=<region>
aws s3api put-public-access-block --bucket momentsearch-media \
  --public-access-block-configuration BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true
```

Keep it private (access via presigned URLs). IAM policy actions: `s3:PutObject`, `s3:GetObject`, `s3:HeadObject`, `s3:DeleteObject` on `arn:aws:s3:::momentsearch-media/*`, plus `s3:ListBucket` on the bucket. Attach to an IAM user (keys → `STORAGE_ACCESS_KEY_ID`/`STORAGE_SECRET_ACCESS_KEY`) or the ECS task role.

CORS (required for browser presigned-PUT uploads):

```bash
aws s3api put-bucket-cors --bucket momentsearch-media --cors-configuration '{
  "CORSRules": [
    {
      "AllowedOrigins": ["https://your-site.example.com"],
      "AllowedMethods": ["PUT","GET"],
      "AllowedHeaders": ["*"],
      "ExposeHeaders": ["ETag"],
      "MaxAgeSeconds": 3000
    }
  ]
}'
```

## GPU CLIP (optional)

Run the clip service on a GPU EC2 (`g4dn`/`g5`) with the `Dockerfile.clip` GPU build and point `EMBED_SERVICE_URL` at it — nothing else changes.

```bash
docker build -f Dockerfile.clip --build-arg TORCH_INDEX_URL=https://download.pytorch.org/whl/cu121 -t $REG/momentsearch-clip:gpu .
docker run --gpus all -p 8001:8001 $REG/momentsearch-clip:gpu   # host needs NVIDIA driver + nvidia-container-toolkit
```

If public/cross-network, secure with `EMBED_SERVICE_TOKEN` (same value on clip + api/worker) behind HTTPS.

## Troubleshooting

- **clip unreachable / can't embed** → set `EMBED_SERVICE_URL` (`http://clip:8001` one box, Cloud Map DNS on ECS); mandatory on slim.
- **Browser uploads fail (CORS)** → add S3 CORS rule; `AllowedOrigins` must match the exact serving origin.
- **YouTube ingest fails** → provide `YT_COOKIES_B64` (ECS) or mounted `YT_COOKIES_FILE` (EC2); cookies expire in ~2–3 weeks.
- **Preflight warns about LOCAL settings** → a local `.env` leaked in; fix the flagged settings. Check fires only when `DEPLOY_ENV` is set.
- **`/demo` empty** → best-effort seeding skipped indexing (usually missing cookies); set cookies + re-run seed, or `SEED_SAMPLE_VIDEOS=false`.
