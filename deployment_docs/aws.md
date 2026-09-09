# Deploy MomentSearch to AWS

Two parts: **Part 1** sets up an S3 bucket, **Part 2** runs the app. Do them in order.

---

## Before you start

1. **Get the shared keys first** — Neon, Qdrant, Prefect, OpenAI (and optional Gemini). See [DEPLOYMENT.md → the keys you need](../DEPLOYMENT.md#2-the-keys-every-deploy-needs). Put them in your `.env`.
2. An **AWS account**.
3. **Docker** installed, and the **`aws`** CLI (installed in Step 1 below).

> One AWS-only setting: you must set **`EMBED_SERVICE_URL`** yourself (Fly sets it automatically, AWS doesn't). It's `http://clip:8001` on a single instance.

---

## Object storage

Your bucket holds uploaded videos, thumbnails and transcripts. The browser uploads straight to it, so it must be set up right. **S3** (`STORAGE_PROVIDER=aws`) is the native pick on AWS, and what this guide sets up. But storage is a **free choice** — you can point AWS at **GCS** ([gcp.md](gcp.md#object-storage)) or **Tigris** ([fly.md](fly.md#step-5--make-the-storage-bucket)) instead; only the keys change, nothing else in the deploy.

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

Log in with an IAM user's **access key** — an ID + secret from your AWS account (*new to this? how to get one →* [AWS: access keys](https://docs.aws.amazon.com/IAM/latest/UserGuide/id_credentials_access-keys.html) · [video tutorial](https://www.youtube.com/results?search_query=aws+create+access+key+aws+configure+tutorial)):

```bash
aws configure          # paste access key, secret, region (e.g. us-east-1), output = json
```

Check it works: `aws sts get-caller-identity` (shows your account) and `docker version`.

### Step 2 — Pick a bucket name and region

- **Bucket name** is **globally unique** across all AWS accounts by default. **Try `momentsearch-media`** — if nobody's taken it, it's yours. If `create` fails with **`BucketAlreadyExists`**, add something unique like `momentsearch-media-yourname` (lowercase, digits, hyphens).
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

> *New to IAM (users, policies, access keys)? →* [AWS: create an IAM user](https://docs.aws.amazon.com/IAM/latest/UserGuide/id_users_create.html) · [video tutorial](https://www.youtube.com/results?search_query=aws+iam+user+policy+access+key+tutorial).

### Step 5 — Put it in `.env`

Your `.env` (copied from `.env.example`) has `STORAGE_PROVIDER=local` — **change that line to `aws`** and fill in the rest:

```dotenv
STORAGE_PROVIDER=aws              # change this from the default `local`
STORAGE_BUCKET=momentsearch-media-yourname
STORAGE_REGION=us-east-1          # your bucket's real region
STORAGE_ACCESS_KEY_ID=AKIA...
STORAGE_SECRET_ACCESS_KEY=...
```

(On ECS with a role, leave out the two key lines — the role provides access.)

### Step 6 — Allow browser uploads (CORS)

Browser uploads go **straight to the bucket**, so it must allow requests from your app's page — without this, uploading fails in the browser (search + playback still work).

> **You don't know your app's address yet** — it comes from Part 2 (the EC2 public IP, `http://YOUR_EC2_IP:8000`). So do this in **two passes**: run it now with just `localhost` (below), then **run it again after Part 2** with your real address added.

```bash
aws s3api put-bucket-cors --bucket momentsearch-media-yourname --cors-configuration '{
  "CORSRules": [{
    "AllowedOrigins": ["http://localhost:8000"],
    "AllowedMethods": ["PUT","GET"], "AllowedHeaders": ["*"],
    "ExposeHeaders": ["ETag"], "MaxAgeSeconds": 3000
  }]
}'
```

The address must match **exactly** — scheme (`http`/`https`) + host + port, no trailing slash.

---

## Part 2 — Deploy

### Easiest: one EC2 instance with Docker

This uses **two machines** — and knowing which is which is the whole trick:

- 💻 **your computer** — where you use the AWS console / `aws` CLI
- ☁️ **the EC2 instance** — a Linux instance you SSH into; it runs Docker + the app

**Every step below is tagged 💻 or ☁️.** Anything tagged ☁️ runs in the EC2 instance's **Linux** shell — do **not** paste it into PowerShell.

**1. 💻 On your computer — launch an EC2 instance** (AWS console → EC2 → *Launch instance*):

- **OS:** Ubuntu 22.04.
- **Size (instance type):** `t3.large` or bigger — you need about **8 GB RAM + 50 GB disk** (the CLIP model + ffmpeg are the heavy parts; smaller types run out of memory). *Which type to pick →* [AWS EC2 instance types](https://aws.amazon.com/ec2/instance-types/) · [video tutorial](https://www.youtube.com/results?search_query=aws+ec2+choose+instance+type+tutorial).
- **Security group:** allow inbound **8000** (the app) and **22** (SSH).
- **Key pair:** download the **`.pem`** file it offers — that's your private key for logging in over SSH. Keep it safe (you **can't** re-download it). *What a key pair is →* [AWS: EC2 key pairs](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/ec2-key-pairs.html) · [video tutorial](https://www.youtube.com/results?search_query=aws+ec2+key+pair+pem+ssh+tutorial).

Note the instance's **public IP** once it's running.

**2. 💻 On your computer — connect into it** (from the EC2 console's "Connect" button, or):

```bash
ssh -i your-key.pem ubuntu@YOUR_EC2_IP
```

(`your-key.pem` is the key file you downloaded in Step 1; `YOUR_EC2_IP` is the instance's public IP.) Your prompt now changes to something like `ubuntu@ip-...:~$`. **From here on, every ☁️ command runs INSIDE the EC2 instance** (Linux) — not in PowerShell. (Type `exit` to leave.)

**3. ☁️ On the EC2 instance — install Docker and clone the project:**

```bash
sudo apt-get update && sudo apt-get install -y docker.io docker-compose-plugin git
sudo usermod -aG docker $USER && newgrp docker
git clone <your-repo-url> momentsearch && cd momentsearch
```

**4. Get your `.env` onto the instance.** Two ways — pick one:

**A — you already filled in `.env` on your computer?** Just copy it up. 💻 Run this **on your computer**, from your local repo folder (Step 3's clone must be done first):

```bash
scp -i your-key.pem .env ubuntu@YOUR_EC2_IP:~/momentsearch/.env
```

**B — starting fresh on the instance?** ☁️ Make it from the template and edit it there:

```bash
cp .env.example .env     # creates .env from the template
nano .env                # fill it in  (or vim; save in nano: Ctrl+O, Enter, Ctrl+X)
```

Fill the shared keys (Neon, Qdrant, Prefect, OpenAI — [DEPLOYMENT.md → the keys you need](../DEPLOYMENT.md#2-the-keys-every-deploy-needs)) and your storage block from Part 1.

**Either way, that `.env` needs DEPLOY values** (not the local preset): `STORAGE_PROVIDER` = your bucket (not `local`), real `DATABASE_URL`/`QDRANT_URL` (not `qdrant`/`postgres` compose hosts), no `COMPOSE_PROFILES`, and `EMBED_SERVICE_URL=http://clip:8001`.

Check it landed — ☁️ on the instance:

```bash
ls -la ~/momentsearch/.env      # should show the file with a real size (not missing / 0 bytes)
```

> **YouTube links** need a **SocialKit key** (easiest — one key covers transcript + video, set `SOCIALKIT_API_KEY` in your `.env`), cookies, or a proxy (the EC2 IP is bot-checked; uploads work without any). See **[README → YouTube ingest](../README.md#youtube-ingest)** for all three. For the **cookies** option: export a `cookies.txt`, then copy it up like your `.env`:
> ```bash
> # ☁️ on the instance — make the folder and make sure you own it:
> mkdir -p ~/momentsearch/secrets
> sudo chown -R $USER:$USER ~/momentsearch/secrets
> # 💻 on your computer — copy the file up:
> scp -i your-key.pem cookies.txt ubuntu@YOUR_EC2_IP:~/momentsearch/secrets/cookies.txt
> ```
> …and set `YT_COOKIES_FILE=/app/secrets/cookies.txt` in `.env`. (Prefer a proxy instead? Set `YT_PROXY_URL=...` — see the README.)

**5. ☁️ On the EC2 instance — start it:**

```bash
docker compose up -d --build
docker compose logs -f seed        # wait for "10 videos ready"
```

First run takes a few minutes (downloads the CLIP model). The ten demo videos
are not indexed — they load from `demo_corpus/`, which lives in the repo, not in
the image. See **The demo on a cloud deploy** in DEPLOYMENT.md.

**6. 💻 On your computer — open** `http://YOUR_EC2_IP:8000/` in a browser (the EC2 public IP).

> **Now finish CORS.** In Part 1's CORS step you allowed only `localhost`. To let browser **uploads** work from the live app, re-run it with this address added:
> ```bash
> aws s3api put-bucket-cors --bucket momentsearch-media-yourname --cors-configuration '{
>   "CORSRules": [{
>     "AllowedOrigins": ["http://YOUR_EC2_IP:8000", "http://localhost:8000"],
>     "AllowedMethods": ["PUT","GET"], "AllowedHeaders": ["*"],
>     "ExposeHeaders": ["ETag"], "MaxAgeSeconds": 3000
>   }]
> }'
> ```

For a domain + HTTPS, put an ALB or nginx/Caddy in front (add that origin to CORS too).

**7. ☁️ On the EC2 instance — check storage works:**

```bash
docker compose exec api python -c "from src import storage as s; k='_selftest/probe.txt'; print(s.put_bytes(k,b'ok','text/plain')); print(s.head(k)); s.delete_key(k); print('storage OK')"
```

Then upload a short video in the browser — the real test of the CORS rule.

### Optional — put it on a domain with HTTPS (Caddy)

The steps above serve plain `http://YOUR_EC2_IP:8000`. For real users you want `https://yourdomain.com` (encrypted, a real name). The easiest way is **Caddy** — it fetches a free certificate automatically.

**1. 💻 At your domain registrar — point the domain at the instance:** add a DNS **A record** → `yourdomain.com` → `YOUR_EC2_IP`.

**2. 💻 Open ports 80 + 443** in the EC2 **security group** (inbound, TCP 80 and 443) — Caddy needs them for the cert + HTTPS.

**3. ☁️ On the instance — install Caddy:**

```bash
sudo apt-get install -y debian-keyring debian-archive-keyring apt-transport-https curl
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' | sudo gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' | sudo tee /etc/apt/sources.list.d/caddy-stable.list
sudo apt-get update && sudo apt-get install -y caddy
```

**4. ☁️ On the instance — point Caddy at your app.** Put this in `/etc/caddy/Caddyfile` (replace the domain), then reload:

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

### Scaling — ECS / Fargate

> **New to this?** **ECS** (Elastic Container Service) with **Fargate** is AWS's way to run your containers across **many machines without managing servers** — it scales them for you, instead of one EC2 instance. It's more setup than the one-instance path above, and **only needed for heavy traffic** — most people can skip it. *Learn it →* [What is ECS](https://docs.aws.amazon.com/AmazonECS/latest/developerguide/Welcome.html) · [What is Fargate](https://docs.aws.amazon.com/AmazonECS/latest/userguide/what-is-fargate.html) · [video tutorial](https://www.youtube.com/results?search_query=aws+ecs+fargate+beginner+tutorial).

Run each part as its own ECS service from the same image. In short:

1. Push the image to ECR.
2. Put secret values in **Secrets Manager**; put non-secret ones (`STORAGE_*`, `DEPLOY_ENV`, `EMBED_SERVICE_URL`) in the task's plain `environment`. For YouTube, add **`YT_COOKIES_B64`** (base64 of `cookies.txt` — `base64 -w0 cookies.txt`) as a secret; Fargate has no `./secrets` mount.
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
- **Can't embed / clip unreachable** → set `EMBED_SERVICE_URL` (`http://clip:8001` on one instance).
