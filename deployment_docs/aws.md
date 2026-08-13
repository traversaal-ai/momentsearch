# Deploy MomentSearch to AWS

Two parts: **Part 1** sets up an S3 bucket, **Part 2** runs the app. Do them in order.

---

## Before you start

1. **Get the shared keys first** — Neon, Qdrant, Prefect, OpenAI (and optional Gemini). See [DEPLOYMENT.md → Before you deploy](../DEPLOYMENT.md#before-you-deploy--get-these-keys-first). Put them in your `.env`.
2. An **AWS account**.
3. **Docker** installed, and the **`aws`** CLI (installed in Step 1 below).

> One AWS-only setting: you must set **`EMBED_SERVICE_URL`** yourself (Fly sets it automatically, AWS doesn't). It's `http://clip:8001` on a single box.

---

## Object storage

Your S3 bucket holds uploaded videos, thumbnails and transcripts. The browser uploads straight to it, so it must be set up right. On AWS there's **one** storage option: **S3** (`STORAGE_PROVIDER=aws`).

Two ways to give the app access to the bucket:

- **Access keys** — an IAM user's key + secret in `.env` (`STORAGE_ACCESS_KEY_ID` / `_SECRET`). Works anywhere. Simplest to start.
- **A role** — on ECS/EC2 you can attach a role so there are **no keys at all**. Better for production (shown in Part 2's ECS path).

We'll use **access keys** below since they work on any setup.

### Step 1 — Set up the aws CLI

Install it:

```powershell
winget install -e --id Amazon.AWSCLI      # Windows
```
```bash
brew install awscli                        # macOS
```

Log in with an IAM user's access key:

```bash
aws configure          # paste access key, secret, region (e.g. us-east-1), output = json
```

Check it works: `aws sts get-caller-identity` (shows your account) and `docker version`.

### Step 2 — Pick a bucket name and region

- **Bucket name** is **unique across all of AWS**, so `momentsearch-media` is likely taken. Use `momentsearch-media-yourname` (lowercase, digits, hyphens).
- **Region** (e.g. `us-east-1`) must be the **same** in the bucket, `STORAGE_REGION`, and everywhere below. A wrong region shows up as a `301`/redirect error on upload.

### Step 3 — Create the private bucket

```bash
aws s3api create-bucket --bucket momentsearch-media-yourname --region us-east-1
# outside us-east-1, add: --create-bucket-configuration LocationConstraint=YOUR_REGION

aws s3api put-public-access-block --bucket momentsearch-media-yourname \
  --public-access-block-configuration BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true
```

The bucket stays private — the app reaches it with keys, never public access.

### Step 4 — Give access + get keys

Create a permission policy for **just this bucket**, then attach it to an IAM user whose access key you'll use:

```bash
cat > s3-policy.json <<'EOF'
{ "Version": "2012-10-17", "Statement": [
  { "Effect": "Allow", "Action": ["s3:PutObject","s3:GetObject","s3:DeleteObject"],
    "Resource": "arn:aws:s3:::momentsearch-media-yourname/*" },
  { "Effect": "Allow", "Action": ["s3:ListBucket"],
    "Resource": "arn:aws:s3:::momentsearch-media-yourname" }
]}
EOF
aws iam create-policy --policy-name momentsearch-s3 --policy-document file://s3-policy.json
```

Attach that policy to an IAM user in the console (**IAM → Users → your user → Add permissions**), then **create an access key** for that user (**Security credentials → Create access key**). You'll get an **Access key ID** (`AKIA...`) and a **Secret**.

### Step 5 — Put it in `.env`

```dotenv
STORAGE_PROVIDER=aws
STORAGE_BUCKET=momentsearch-media-yourname
STORAGE_REGION=us-east-1          # your bucket's real region
STORAGE_ACCESS_KEY_ID=AKIA...
STORAGE_SECRET_ACCESS_KEY=...
```

(On ECS with a role, leave out the two key lines — the role provides access.)

### Step 6 — Allow browser uploads (CORS)

```bash
aws s3api put-bucket-cors --bucket momentsearch-media-yourname --cors-configuration '{
  "CORSRules": [{
    "AllowedOrigins": ["https://your-app-address", "http://localhost:8000"],
    "AllowedMethods": ["PUT","GET"], "AllowedHeaders": ["*"],
    "ExposeHeaders": ["ETag"], "MaxAgeSeconds": 3000
  }]
}'
```

Replace `https://your-app-address` with where you'll serve the app (on a bare EC2 box it's `http://YOUR_EC2_IP:8000` at first — update it once you have a domain).

---

## Part 2 — Deploy

### Easiest: one EC2 box with Docker

**1. Launch EC2** — Ubuntu 22.04, `t3.large` or bigger. In its security group, allow inbound **8000** (the app) and **22** (SSH).

**2. Connect, install Docker, clone:**

```bash
sudo apt-get update && sudo apt-get install -y docker.io docker-compose-plugin git
sudo usermod -aG docker $USER && newgrp docker
git clone <your-repo-url> momentsearch && cd momentsearch
```

**3. Create `.env`** — the shared keys (Neon, Qdrant, Prefect, OpenAI) plus your storage block from Part 1, plus:

```dotenv
EMBED_SERVICE_URL=http://clip:8001
DEPLOY_ENV=production
```

`EMBED_SERVICE_URL` must be set here (AWS doesn't auto-set it). `DEPLOY_ENV=production` turns on the safety check that warns if any setting is still local.

**4. Start it:**

```bash
docker compose up -d --build
docker compose logs -f seed        # wait for "sample corpus complete"
```

First run takes a few minutes (downloads the model, indexes a sample video).

**5. Open** `http://YOUR_EC2_IP:8000/`. For a domain + HTTPS, put an ALB or nginx/Caddy in front.

**6. Check storage works:**

```bash
docker compose exec api python -c "from src import storage as s; k='_selftest/probe.txt'; print(s.put_bytes(k,b'ok','text/plain')); print(s.head(k)); s.delete_key(k); print('storage OK')"
```

Then upload a short video in the browser — the real test of the CORS rule.

### Bigger: ECS / Fargate (for scaling)

Run each part as its own ECS service from the same image. In short:

1. Push the image to ECR.
2. Put secret values in **Secrets Manager**; put non-secret ones (`STORAGE_*`, `DEPLOY_ENV`, `EMBED_SERVICE_URL`) in the task's plain `environment`.
3. Three services: **api** (behind an ALB on 8000), **worker** (no ports), **clip** (internal via Service Connect on 8001). Set `EMBED_SERVICE_URL=http://clip.your-namespace.local:8001` on api + worker.
4. Give the **task role** the S3 policy from Part 1 (then you need no access keys at all).
5. Run `seed` once as a one-off task after clip is healthy, then add the ALB address to the bucket CORS rule.

<details>
<summary>ECS details (image push, task container example)</summary>

```bash
aws ecr create-repository --repository-name momentsearch
ACCT=$(aws sts get-caller-identity --query Account --output text); REGION=us-east-1
REG=$ACCT.dkr.ecr.$REGION.amazonaws.com
aws ecr get-login-password --region $REGION | docker login --username AWS --password-stdin $REG
docker build -t $REG/momentsearch:latest . && docker push $REG/momentsearch:latest
```

Each service is the same image with a different `command`:
- **api:** `uvicorn src.app:app --host 0.0.0.0 --port 8000`
- **worker:** `python -m src.worker`
- **clip:** `uvicorn src.clip_service:app --host 0.0.0.0 --port 8001`

Example api container (non-secrets in `environment`, secrets from Secrets Manager):

```json
{
  "name": "api",
  "image": "<ACCT>.dkr.ecr.us-east-1.amazonaws.com/momentsearch:latest",
  "command": ["uvicorn","src.app:app","--host","0.0.0.0","--port","8000"],
  "portMappings": [{ "containerPort": 8000, "name": "api" }],
  "environment": [
    { "name": "STORAGE_PROVIDER", "value": "aws" },
    { "name": "STORAGE_BUCKET",   "value": "momentsearch-media-yourname" },
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

Seed once (after clip is healthy):

```bash
aws ecs run-task --cluster momentsearch --task-definition momentsearch-api --launch-type FARGATE \
  --network-configuration '{"awsvpcConfiguration":{"subnets":["subnet-..."],"securityGroups":["sg-..."],"assignPublicIp":"ENABLED"}}' \
  --overrides '{"containerOverrides":[{"name":"api","command":["python","-m","src.seed"]}]}'
```

Scale ingest with `aws ecs update-service --cluster momentsearch --service worker --desired-count 3`.

</details>

### GPU (optional)

For faster embedding, run only the `clip` part on a GPU EC2 (`g4dn`/`g5`), built from `Dockerfile.clip` with `--build-arg TORCH_INDEX_URL=https://download.pytorch.org/whl/cu121`, and point `EMBED_SERVICE_URL` at it.

---

## If something goes wrong

- **"Unable to locate credentials"** → the aws CLI isn't logged in. Run `aws configure` (Step 1).
- **`BucketAlreadyExists`** → the name is taken (they're global). Pick another (Step 2).
- **`301` / `PermanentRedirect` / `SignatureDoesNotMatch` on upload** → `STORAGE_REGION` isn't the bucket's real region. Check with `aws s3api get-bucket-location --bucket <name>`.
- **Browser upload fails (CORS error)** → your app's address isn't in the CORS rule, or doesn't match exactly (scheme + host + port). Fix Step 6.
- **`AccessDenied` on delete or thumbnails** → the policy is missing `s3:ListBucket` on the bucket, or `s3:DeleteObject`. Re-check Step 4.
- **Uploads vanish / worker can't find the file** → `STORAGE_PROVIDER` is still `local`. Set it to `aws`.
- **Can't embed / clip unreachable** → set `EMBED_SERVICE_URL` (`http://clip:8001` on one box).
