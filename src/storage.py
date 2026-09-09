"""Object storage — env-switched provider, one interface (videos + thumbnails).

  aws        real S3 (boto3, default endpoint, IAM keys)
  gcp        GCS via its S3-interoperability endpoint (HMAC key pair)
  gcp_native GCS via Google's own SDK + service-account JSON exploded into
             GOOGLE_CLOUD_* env vars (no HMAC keys needed)
  flyio      Tigris (fly storage create injects AWS_* env vars automatically)
  local      ./data on disk, credential-free dev fallback (no presigning —
             the API serves/receives bytes itself in this mode)

aws / gcp / flyio share one boto3 S3 client (same protocol, different
endpoint); gcp_native uses google-cloud-storage with the service-account flow.

Beyond put/get this layer covers the scaling primitives the plan needs:
presigned PUT (browser uploads bypass the API), presigned GET (thumbnails and
playback stream from the bucket, not through us), HEAD verification after
upload, and prefix listing + batch delete (a video's frames go in one call).
"""
from __future__ import annotations

import shutil
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from pathlib import Path

from .config import (
    AWS_REGION,
    DATA,
    PRESIGN_EXPIRY_S,
    PRESIGN_GET_EXPIRY_S,
    STORAGE_ACCESS_KEY_ID,
    STORAGE_BUCKET,
    STORAGE_ENDPOINT,
    STORAGE_PROVIDER,
    STORAGE_SECRET_ACCESS_KEY,
    gcs_service_account_info,
    DEMO_DATA,
    DEMO_LOCAL,
)

_client = None


# ── Key layout (every key user-scoped — tenant isolation at the path level) ──

def video_prefix(user_id: str, video_id: str) -> str:
    """Everything for ONE video lives under this single prefix — its frames, its
    source upload and its transcript. So a video's whole footprint is one prefix
    delete, and a user's whole footprint is `<user_id>/`. Content-addressed by
    (owner, video): sessions are pointers in Postgres, never in the key."""
    return f"{user_id}/{video_id}/"


def upload_key(user_id: str, video_id: str, ext: str) -> str:
    return f"{video_prefix(user_id, video_id)}source{ext}"


def frame_key(user_id: str, video_id: str, index: int) -> str:
    return f"{video_prefix(user_id, video_id)}frames/{index:06d}.jpg"


def frame_prefix(user_id: str, video_id: str) -> str:
    return f"{video_prefix(user_id, video_id)}frames/"


def transcript_key(user_id: str, video_id: str) -> str:
    """Durable copy of a video's timed transcript (JSON: [{text,t_start,t_end}]).
    Lets us re-embed transcripts (e.g. on a text-model swap) without re-fetching
    captions from YouTube."""
    return f"{video_prefix(user_id, video_id)}transcript.json"


def _s3():
    global _client
    if _client is None:
        import boto3
        from botocore.config import Config

        _client = boto3.client(
            "s3",
            endpoint_url=STORAGE_ENDPOINT,
            region_name=AWS_REGION,
            aws_access_key_id=STORAGE_ACCESS_KEY_ID or None,
            aws_secret_access_key=STORAGE_SECRET_ACCESS_KEY or None,
            config=Config(signature_version="s3v4"),  # required for presigning on GCS/Tigris
        )
    return _client


def _gcs_bucket():
    """google-cloud-storage bucket handle (gcp_native provider)."""
    global _client
    if _client is None:
        from google.cloud import storage as gcs
        from google.oauth2 import service_account

        creds = service_account.Credentials.from_service_account_info(
            gcs_service_account_info())
        _client = gcs.Client(credentials=creds, project=creds.project_id)
    return _client.bucket(STORAGE_BUCKET)



# ── Where a key actually lives ───────────────────────────────────────────────

def _demo_key(key: str) -> bool:
    """True for a key belonging to a shipped sample video.

    Keys are `<user>/<video>/...`, so the video id is the second segment. The
    samples are read from the repo folder no matter what STORAGE_PROVIDER says
    (config.DEMO_LOCAL) — that is what lets someone run their own uploads on a
    bucket while the demo stays a local, zero-cost, zero-setup thing."""
    if not DEMO_LOCAL:
        return False
    from .samples import is_sample
    parts = key.split("/")
    return len(parts) > 1 and is_sample(parts[1])


def _root(key: str) -> Path | None:
    """The on-disk root for a key, or None when it belongs in the bucket.
    Samples -> demo_corpus/objects; everything else -> ./data, but only when the
    provider IS local."""
    if _demo_key(key):
        return DEMO_DATA
    return DATA if STORAGE_PROVIDER == "local" else None

def presign_capable(key: str | None = None) -> bool:
    """Local disk can't mint URLs — the API falls back to direct upload/serving.
    A sample key is always local (see _root), so it can never be presigned even
    on a bucket deployment."""
    if key is not None and _demo_key(key):
        return False
    return STORAGE_PROVIDER != "local"


# ── Presigned URLs (the write-path front door) ───────────────────────────────

def presign_put(key: str, content_type: str, expires: int = PRESIGN_EXPIRY_S) -> dict:
    """A time-limited URL the browser PUTs the video to directly.

    Returns {"url": ..., "headers": {...}} — the client must send exactly these
    headers (they are part of the signature, so the content type is enforced).
    """
    if STORAGE_PROVIDER == "gcp_native":
        url = _gcs_bucket().blob(key).generate_signed_url(
            version="v4", method="PUT", expiration=timedelta(seconds=expires),
            content_type=content_type)
        return {"url": url, "headers": {"Content-Type": content_type}}
    url = _s3().generate_presigned_url(
        "put_object",
        Params={"Bucket": STORAGE_BUCKET, "Key": key, "ContentType": content_type},
        ExpiresIn=expires,
    )
    return {"url": url, "headers": {"Content-Type": content_type}}


def presign_get(key: str, expires: int = PRESIGN_GET_EXPIRY_S) -> str:
    """A time-limited read URL (thumbnail display, video playback)."""
    if STORAGE_PROVIDER == "gcp_native":
        return _gcs_bucket().blob(key).generate_signed_url(
            version="v4", method="GET", expiration=timedelta(seconds=expires))
    return _s3().generate_presigned_url(
        "get_object", Params={"Bucket": STORAGE_BUCKET, "Key": key}, ExpiresIn=expires)


def head(key: str) -> dict | None:
    """Object metadata ({size, content_type}) or None — the post-upload check."""
    root = _root(key)
    if root is not None:
        p = root / key
        return {"size": p.stat().st_size, "content_type": ""} if p.exists() else None
    if STORAGE_PROVIDER == "gcp_native":
        blob = _gcs_bucket().get_blob(key)
        if blob is None:
            return None
        return {"size": blob.size or 0, "content_type": blob.content_type or ""}
    try:
        resp = _s3().head_object(Bucket=STORAGE_BUCKET, Key=key)
        return {"size": resp.get("ContentLength", 0),
                "content_type": resp.get("ContentType", "")}
    except Exception:
        return None


def exists(key: str) -> bool:
    return head(key) is not None


# ── Bytes in / bytes out ─────────────────────────────────────────────────────

def put_bytes(key: str, body: bytes, content_type: str = "application/octet-stream") -> str:
    root = _root(key)
    if root is not None:
        path = root / key
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(body)
        return str(path)
    if STORAGE_PROVIDER == "gcp_native":
        _gcs_bucket().blob(key).upload_from_string(body, content_type=content_type)
        return f"gs://{STORAGE_BUCKET}/{key}"
    _s3().put_object(Bucket=STORAGE_BUCKET, Key=key, Body=body, ContentType=content_type)
    return f"s3://{STORAGE_BUCKET}/{key}"


def get_bytes(key: str) -> bytes:
    root = _root(key)
    if root is not None:
        return (root / key).read_bytes()
    if STORAGE_PROVIDER == "gcp_native":
        return _gcs_bucket().blob(key).download_as_bytes()
    resp = _s3().get_object(Bucket=STORAGE_BUCKET, Key=key)
    return resp["Body"].read()


def download_to(key: str, dest: Path) -> Path:
    """Stream an object to a local file (worker scratch) without buffering it all."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    root = _root(key)
    if root is not None:
        shutil.copyfile(root / key, dest)
    elif STORAGE_PROVIDER == "gcp_native":
        _gcs_bucket().blob(key).download_to_filename(str(dest))
    else:
        _s3().download_file(STORAGE_BUCKET, key, str(dest))
    return dest


def upload_file(path: Path, key: str, content_type: str = "application/octet-stream") -> str:
    root = _root(key)
    if root is not None:
        target = root / key
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, target)
        return str(target)
    if STORAGE_PROVIDER == "gcp_native":
        _gcs_bucket().blob(key).upload_from_filename(str(path), content_type=content_type)
        return f"gs://{STORAGE_BUCKET}/{key}"
    _s3().upload_file(str(path), STORAGE_BUCKET, key,
                      ExtraArgs={"ContentType": content_type})
    return f"s3://{STORAGE_BUCKET}/{key}"


# ── Listing + batch delete (video lifecycle) ─────────────────────────────────

def list_keys(prefix: str) -> list[str]:
    root = _root(prefix if prefix.count("/") >= 2 else prefix + "/x")
    if root is not None:
        base = root / prefix
        if not base.exists():
            return []
        # relative to the root the paths were BUILT from, not to DATA: for a
        # sample those are sibling trees (demo_corpus/objects vs ./data) and
        # relative_to() raises instead of returning a key.
        return [str(p.relative_to(root)).replace("\\", "/")
                for p in base.rglob("*") if p.is_file()]
    if STORAGE_PROVIDER == "gcp_native":
        return [b.name for b in _gcs_bucket().list_blobs(prefix=prefix)]
    keys: list[str] = []
    paginator = _s3().get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=STORAGE_BUCKET, Prefix=prefix):
        keys.extend(o["Key"] for o in page.get("Contents", []))
    return keys


def delete_prefix(prefix: str) -> int:
    """Delete every object under a prefix (a video's frames in one batch call)."""
    keys = list_keys(prefix)
    if not keys:
        return 0
    if _root(keys[0]) is not None:
        for k in keys:
            ((_root(k) or DATA) / k).unlink(missing_ok=True)
    elif STORAGE_PROVIDER == "gcp_native":
        from google.api_core.exceptions import NotFound
        bucket = _gcs_bucket()

        def _rm(k: str) -> None:
            try:  # a stale listing may name an object already gone — ignore it
                bucket.blob(k).delete()
            except NotFound:
                pass

        # One HTTP round trip per object, and GCS has no batch-delete verb. Serial
        # that was ~250ms x N — a 200-frame video took the better part of a minute
        # and a delete looked hung. These calls are pure network wait, so threads
        # collapse it to roughly N/16 round trips.
        if len(keys) > 1:
            with ThreadPoolExecutor(max_workers=min(16, len(keys))) as ex:
                list(ex.map(_rm, keys))
        else:
            _rm(keys[0])
    else:
        for i in range(0, len(keys), 1000):  # S3 DeleteObjects caps at 1000/call
            _s3().delete_objects(
                Bucket=STORAGE_BUCKET,
                Delete={"Objects": [{"Key": k} for k in keys[i:i + 1000]],
                        "Quiet": True})
    return len(keys)


def delete_key(key: str) -> None:
    root = _root(key)
    if root is not None:
        (root / key).unlink(missing_ok=True)
    elif STORAGE_PROVIDER == "gcp_native":
        from google.api_core.exceptions import NotFound
        try:
            _gcs_bucket().blob(key).delete()
        except NotFound:
            pass  # already gone — deleting is idempotent, like the other providers
    else:
        _s3().delete_object(Bucket=STORAGE_BUCKET, Key=key)


def local_path(key: str) -> Path:
    """Absolute path for a key that lives on disk — the local provider, or a
    shipped sample under demo_corpus/ regardless of provider."""
    return (_root(key) or DATA) / key
