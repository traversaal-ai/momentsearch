# Deploying MomentSearch to AWS

Same Docker image as Fly (four entrypoints: `api`, `worker`, `clip`, `seed`); only **compute**, **`EMBED_SERVICE_URL`** (no auto-derive off Fly — set it), and the **storage provider** change — S3 is the natural pick here, though GCS and Tigris work unchanged ([Object storage](#object-storage)).

## What you need

- `DATABASE_URL` — Postgres (RDS or keep your Neon URL).
- `QDRANT_URL` + `QDRANT_API_KEY` — Qdrant Cloud (or self-host). Collections `moments_l14` + `moments_text_openai` auto-create.
- `PREFECT_API_URL` + `PREFECT_API_KEY` — Prefect Cloud (worker polls outbound, no inbound ports).
- `OPENAI_API_KEY` — answer model, transcript embeddings, Whisper. (Set `TEXT_EMBED_PROVIDER=fastembed` to drop it for the text branch.)
- **Object storage** — one private bucket for videos, thumbnails and transcripts. **S3** (`STORAGE_PROVIDER=aws`) is the natural fit here, but **GCS** and **Tigris** are equally supported. See [Object storage](#object-storage).
- `EMBED_SERVICE_URL` — **must set off Fly** (`http://clip:8001` on one box; clip internal DNS on ECS).
- `DEPLOY_ENV=production` — arms the preflight local-settings check (`STRICT_DEPLOY_CHECK=true` to abort instead of warn).
- Optional: `GEMINI_API_KEY` (speaker recognition); `YT_COOKIES_B64` (YouTube — datacenter IPs are bot-checked).
- An **AWS account**, the **`aws` CLI** (authenticated), and **Docker**. See [Set up the AWS CLI](#set-up-the-aws-cli).

## The flow

Do these in order. The bucket has to exist **before** the first deploy, because the seed
step writes frames to it as soon as the app starts.

| Step | What | Where |
|---|---|---|
| **1** | Install the `aws` CLI and authenticate it | [Set up the AWS CLI](#set-up-the-aws-cli) |
| **2** | Pick your bucket name + region (the bucket name is **globally unique**) | [Choose your names](#choose-your-names) |
| **3** | Create the private bucket, grant access, add the **CORS rule** | [Object storage](#object-storage) |
| **4** | Put your config where the containers can read it (`.env`, or Secrets Manager) | [Path A](#path-a--one-ec2-box-docker-compose) step 3 / [Path B](#path-b--ecsfargate) step 2 |
| **5** | Deploy — one EC2 box, or ECS/Fargate | [Path A](#path-a--one-ec2-box-docker-compose) / [Path B](#path-b--ecsfargate) |
| **6** | Verify storage, seed and search actually work | end of each path |

## Set up the AWS CLI

**Step 1.** Two different sets of credentials are involved, and mixing them up is the
usual first stumble:

- **Yours**, used by the `aws` CLI to *create* infrastructure (bucket, IAM, ECR, ECS).
- **The app's**, used at runtime to read/write objects — either
  `STORAGE_ACCESS_KEY_ID`/`STORAGE_SECRET_ACCESS_KEY`, or an ECS task role / EC2 instance
  profile with no keys at all.

**Install:**

```powershell
winget install -e --id Amazon.AWSCLI      # Windows
```

```bash
brew install awscli                       # macOS
# Linux:
curl "https://awscli.amazonaws.com/awscli-exe-linux-x86_64.zip" -o awscliv2.zip
unzip awscliv2.zip && sudo ./aws/install
```

**Authenticate** — an IAM user access key, or SSO if your org uses it:

```bash
aws configure          # access key, secret, default region, output format
# or: aws configure sso

aws sts get-caller-identity     # prints your account + ARN = you're authenticated
```

Docker is also required (it builds the image on both paths): `docker version`.

## Choose your names

**Step 2.** One of these is globally unique and will bite you exactly like a taken app
name does on Fly:

| Name | Example | Unique across | Notes |
|---|---|---|---|
| **S3 bucket** | `momentsearch-media` | **all of AWS, every account** | `momentsearch-media` is very likely taken → `BucketAlreadyExists`. Use `momentsearch-media-<you>`. Lowercase, digits, hyphens only. |
| **Region** | `us-east-1` | — | Must be the **same** value in the bucket, `STORAGE_REGION`, ECR and ECS. A mismatch surfaces as `PermanentRedirect`/301 on upload. |
| **ECR repos** | `momentsearch`, `-slim`, `-clip` | your account + region | Free choice. |
| **ECS cluster / services** | `momentsearch`, `api`/`worker`/`clip` | your account + region | Free choice, but the Service Connect name feeds `EMBED_SERVICE_URL`. |
| **Secrets Manager secret** | `momentsearch/env` | your account + region | Free choice; the ARN is referenced per-variable in the task definition. |

Pick the bucket name **now** and use it consistently — it appears in five places:

1. `aws s3api create-bucket --bucket <name>`
2. the IAM policy ARNs (`arn:aws:s3:::<name>` and `<name>/*`)
3. the CORS command
4. `STORAGE_BUCKET` in `.env` or the task definition's `environment`
5. `aws s3api put-public-access-block --bucket <name>`

Everything below writes `momentsearch-media` — substitute yours throughout. Unlike a Fly
app name, an S3 bucket can't be renamed either: you create a new one and copy objects
across (`aws s3 sync s3://old s3://new`).

## Object storage

**Step 3 — do steps 1–2 first.** These `aws s3api` / `aws iam` commands need the CLI
authenticated (step 1) and your bucket name chosen (step 2), or they fail with
"Unable to locate credentials" / `BucketAlreadyExists` errors. Uploaded videos, frame
thumbnails and transcript JSON live in **one private bucket**, keyed
`{user_id}/{video_id}/` ([`src/storage.py`](../src/storage.py)). The browser
PUTs straight to the bucket with a presigned URL and reads thumbnails/playback with
presigned GETs — bytes never pass through the API.

### Pick a provider

All three are fully supported and identical at runtime; choose on where your
infrastructure already lives. S3 is the obvious pick on AWS (task-role auth, no extra
vendor), not a technically better one.

| Provider | `STORAGE_PROVIDER` | Setup | Notes on AWS |
|---|---|---|---|
| **S3** | `aws` | bucket + IAM user/role | Native. ECS task role means **no static keys** at all. |
| **GCS** | `gcp_native` | GCP project + service account | Works fine; cross-cloud egress applies. Setup: [gcp.md → Object storage](gcp.md#object-storage). |
| **Tigris** | `flyio` | one command on Fly | Easiest if you already run a Fly app; usable from AWS with the `tid_`/`tsec_` keys. Setup: [fly.md → step 5](fly.md#step-5--create-the-storage-bucket-tigris). |

For GCS or Tigris on AWS, set that provider's vars from the linked section instead of
the `STORAGE_*` S3 ones below — nothing else about this deploy changes.
`STORAGE_PROVIDER=local` is dev-only (no presigning, EC2/Fargate disks aren't shared or
durable) and the preflight check flags it when `DEPLOY_ENV` is set.

### S3 (`STORAGE_PROVIDER=aws`)

**1. Create a private bucket:**

```bash
aws s3api create-bucket --bucket momentsearch-media --region us-east-1
# non-us-east-1: add --create-bucket-configuration LocationConstraint=<region>
aws s3api put-public-access-block --bucket momentsearch-media \
  --public-access-block-configuration BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true
```

Keep it private — presigned URLs are the only way in or out.

**2. Grant access.** `s3:PutObject`, `s3:GetObject` and `s3:DeleteObject` on
`arn:aws:s3:::momentsearch-media/*`, plus `s3:ListBucket` on the **bucket** ARN — prefix
listing is what makes deleting a video one call. (`s3:GetObject` also authorizes the HEAD
the app does after each upload; there is no separate `s3:HeadObject` action.)

```bash
cat > s3-policy.json <<'EOF'
{ "Version": "2012-10-17", "Statement": [
  { "Effect": "Allow",
    "Action": ["s3:PutObject","s3:GetObject","s3:DeleteObject"],
    "Resource": "arn:aws:s3:::momentsearch-media/*" },
  { "Effect": "Allow", "Action": ["s3:ListBucket"],
    "Resource": "arn:aws:s3:::momentsearch-media" }
]}
EOF
aws iam create-policy --policy-name momentsearch-s3 --policy-document file://s3-policy.json
```

Attach the policy to the
**ECS task role** (Path B — no static keys needed, boto3 picks up the role) or to an IAM
user whose access key you set as `STORAGE_ACCESS_KEY_ID`/`STORAGE_SECRET_ACCESS_KEY`
(Path A — EC2 can also use an instance profile and skip the keys).

**3. Set the env vars:**

```dotenv
STORAGE_PROVIDER=aws
STORAGE_BUCKET=momentsearch-media
STORAGE_REGION=us-east-1          # the bucket's REAL region, not `auto`
STORAGE_ACCESS_KEY_ID=AKIA...     # omit both if using a task role / instance profile
STORAGE_SECRET_ACCESS_KEY=...
```

**4. CORS — required, or every browser upload fails.** The presigned `PUT` is
cross-origin from your page to the bucket's own endpoint
(`momentsearch-media.s3.<region>.amazonaws.com`):

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

`AllowedOrigins` must match the serving origin exactly — scheme + host + port, no
trailing slash. Add every origin you serve from (ALB domain, custom domain,
`http://localhost:8000` if local dev shares the bucket). On Path A the origin is
`http://<ec2-public-ip>:8000` until you front it with a domain — so revisit this rule
once the ALB is in place.

**5. Verify the round trip** (after the app is up — step 6 of either path) before blaming
the worker for a stuck upload. This exercises the real code path: credentials, bucket,
presigning.

```bash
docker compose exec api python -c "from src import storage as s; k='_selftest/probe.txt'; print(s.put_bytes(k,b'ok','text/plain')); print(s.head(k)); print(s.presign_get(k)[:90]); s.delete_key(k); print('storage OK')"
# ECS: aws ecs run-task ... --overrides '{"containerOverrides":[{"name":"api","command":["python","-c","..."]}]}'
```

CORS isn't covered by that check (it's a browser-only rule) — confirm it by uploading a
small video through the UI.

## Path A — one EC2 box (docker compose)

**Steps 4-6, single box.** The fat image runs api + worker + clip + seed together; slim
isn't needed. Cheapest way to get a working deploy.

**1. Launch EC2** — Ubuntu 22.04, `t3.large`+. Security group: allow inbound TCP **8000** (clients) and **22** (SSH); leave 8001 closed.

**2. Install Docker + clone**

```bash
sudo apt-get update && sudo apt-get install -y docker.io docker-compose-plugin git
sudo usermod -aG docker $USER && newgrp docker
git clone <your-repo-url> momentsearch && cd momentsearch
```

**3. Write `.env`** — this is the whole configuration step. Every value here comes either
from [What you need](#what-you-need) (managed-service URLs and keys) or from
[Object storage](#object-storage) (the `STORAGE_*` block):

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

`EMBED_SERVICE_URL=http://clip:8001` is the compose service name — it must be set here,
since only Fly derives it automatically. Drop the two `STORAGE_*` keys if you attached an
instance profile instead.

For YouTube: drop `cookies.txt` at `./secrets/cookies.txt` and add `YT_COOKIES_FILE=/app/secrets/cookies.txt` (compose mounts `./secrets` read-only into the worker and seed — it's deliberately outside `./data`, which is the storage tree).

**4. Bring it up**

```bash
docker compose up -d --build
docker compose logs -f seed
```

Seed indexes the sample talk and exits; api/worker wait for it. Best-effort by default — `SEED_STRICT=true` to hard-gate, `SEED_SAMPLE_VIDEOS=false` to skip.

**5. Open** `http://<ec2-public-ip>:8000/`. Front with an ALB or nginx/Caddy for a domain + HTTPS.

**6. Verify** — three checks that between them cover the whole stack:

```bash
docker compose logs seed | tail -5      # expect "sample corpus complete"
docker compose exec api python -c "from src import storage as s; k='_selftest/probe.txt'; print(s.put_bytes(k,b'ok','text/plain')); print(s.head(k)); s.delete_key(k); print('storage OK')"
docker compose logs api | grep -i setup  # startup readiness summary (src/setup_check.py)
```

Then upload a short video through the UI — that's the only thing that exercises the
browser-side CORS rule.

## Path B — ECS/Fargate

**Steps 4-6, scaled.** Three services (`api`, `worker`, `clip`) from the one image, each with a command override. ALB → `api:8000`; `clip` internal only via Service Connect/Cloud Map; secrets via Secrets Manager.

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

**2. Secrets → Secrets Manager** — this is the configuration step on this path. Secret
values go here; non-secret values go in the task definition's plain `environment`.

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

**4. Seed once** — run it after `clip` is healthy, and only once per fresh Qdrant:

```bash
aws ecs run-task \
  --cluster momentsearch \
  --task-definition momentsearch-api \
  --launch-type FARGATE \
  --network-configuration '{"awsvpcConfiguration":{"subnets":["subnet-..."],"securityGroups":["sg-..."],"assignPublicIp":"ENABLED"}}' \
  --overrides '{"containerOverrides":[{"name":"api","command":["python","-m","src.seed"]}]}'
```

**5. Open** the ALB DNS name, then add that exact origin to the bucket CORS rule
([Object storage](#object-storage) step 4) or uploads will fail in the browser.

**6. Verify** — CloudWatch logs for the seed task (`sample corpus complete`), the api
service's startup readiness summary, and one real upload through the UI. Scale ingest with
`aws ecs update-service --cluster momentsearch --service worker --desired-count 3` (0 when idle).

## GPU CLIP (optional)

Run the clip service on a GPU EC2 (`g4dn`/`g5`) with the `Dockerfile.clip` GPU build and point `EMBED_SERVICE_URL` at it — nothing else changes.

```bash
docker build -f Dockerfile.clip --build-arg TORCH_INDEX_URL=https://download.pytorch.org/whl/cu121 -t $REG/momentsearch-clip:gpu .
docker run --gpus all -p 8001:8001 $REG/momentsearch-clip:gpu   # host needs NVIDIA driver + nvidia-container-toolkit
```

If public/cross-network, secure with `EMBED_SERVICE_TOKEN` (same value on clip + api/worker) behind HTTPS.

## Troubleshooting

- **`aws` says "Unable to locate credentials"** → the CLI isn't authenticated; run `aws configure` and confirm with `aws sts get-caller-identity` ([step 1](#set-up-the-aws-cli)).
- **`BucketAlreadyExists`** → S3 bucket names are global across every AWS account. Pick a unique one ([step 2](#choose-your-names)).
- **clip unreachable / can't embed** → set `EMBED_SERVICE_URL` (`http://clip:8001` one box, Cloud Map DNS on ECS); mandatory on slim.
- **Browser uploads fail (CORS)** → add the bucket CORS rule ([Object storage](#object-storage)); `AllowedOrigins` must match the exact serving origin, including the port on a bare EC2 IP.
- **`SignatureDoesNotMatch` / `PermanentRedirect` / 301 on upload** → `STORAGE_REGION` isn't the bucket's real region. `aws s3api get-bucket-location --bucket <name>` tells you it (`null` means `us-east-1`).
- **`AccessDenied` on delete or thumbnail load** → the policy is missing `s3:ListBucket` on the bucket ARN (needed for prefix listing) or `s3:DeleteObject` on `/*`.
- **Uploads land nowhere / vanish on restart** → `STORAGE_PROVIDER` is still `local`; container disks aren't shared between `api` and `worker`, or durable.
- **YouTube ingest fails** → provide `YT_COOKIES_B64` (ECS) or mounted `YT_COOKIES_FILE` (EC2); cookies expire in ~2–3 weeks.
- **Preflight warns about LOCAL settings** → a local `.env` leaked in; fix the flagged settings. Check fires only when `DEPLOY_ENV` is set.
- **`/demo` empty** → best-effort seeding skipped indexing (usually missing cookies); set cookies + re-run seed, or `SEED_SAMPLE_VIDEOS=false`.
