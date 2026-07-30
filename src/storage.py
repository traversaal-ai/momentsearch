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
from datetime import timedelta
from pathlib import Path

from .config import (
    AWS_REGION,
    DATA,
    FRAME_KEY_PREFIX,
    PRESIGN_EXPIRY_S,
    PRESIGN_GET_EXPIRY_S,
    STORAGE_ACCESS_KEY_ID,
    STORAGE_BUCKET,
    STORAGE_ENDPOINT,
    STORAGE_PROVIDER,
    STORAGE_SECRET_ACCESS_KEY,
    UPLOAD_KEY_PREFIX,
    gcs_service_account_info,
)

_client = None


# ── Key layout (every key user-scoped — tenant isolation at the path level) ──

def upload_key(user_id: str, video_id: str, ext: str) -> str:
    return f"{UPLOAD_KEY_PREFIX}{user_id}/{video_id}{ext}"


def frame_key(user_id: str, video_id: str, index: int) -> str:
    return f"{FRAME_KEY_PREFIX}{user_id}/{video_id}/{index:06d}.jpg"


def frame_prefix(user_id: str, video_id: str) -> str:
    return f"{FRAME_KEY_PREFIX}{user_id}/{video_id}/"


def transcript_key(user_id: str, video_id: str) -> str:
    """Durable copy of a video's timed transcript (JSON: [{text,t_start,t_end}]).
    Lets us re-embed transcripts (e.g. on a text-model swap) without re-fetching
    captions from YouTube — the same reason akash persists its transcripts."""
    return f"transcripts/{user_id}/{video_id}.json"


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


def presign_capable() -> bool:
    """Local disk can't mint URLs — the API falls back to direct upload/serving."""
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
    if STORAGE_PROVIDER == "local":
        p = DATA / key
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
    if STORAGE_PROVIDER == "local":
        path = DATA / key
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(body)
        return str(path)
    if STORAGE_PROVIDER == "gcp_native":
        _gcs_bucket().blob(key).upload_from_string(body, content_type=content_type)
        return f"gs://{STORAGE_BUCKET}/{key}"
    _s3().put_object(Bucket=STORAGE_BUCKET, Key=key, Body=body, ContentType=content_type)
    return f"s3://{STORAGE_BUCKET}/{key}"


def get_bytes(key: str) -> bytes:
    if STORAGE_PROVIDER == "local":
        return (DATA / key).read_bytes()
    if STORAGE_PROVIDER == "gcp_native":
        return _gcs_bucket().blob(key).download_as_bytes()
    resp = _s3().get_object(Bucket=STORAGE_BUCKET, Key=key)
    return resp["Body"].read()


def download_to(key: str, dest: Path) -> Path:
    """Stream an object to a local file (worker scratch) without buffering it all."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    if STORAGE_PROVIDER == "local":
        shutil.copyfile(DATA / key, dest)
    elif STORAGE_PROVIDER == "gcp_native":
        _gcs_bucket().blob(key).download_to_filename(str(dest))
    else:
        _s3().download_file(STORAGE_BUCKET, key, str(dest))
    return dest


def upload_file(path: Path, key: str, content_type: str = "application/octet-stream") -> str:
    if STORAGE_PROVIDER == "local":
        target = DATA / key
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
    if STORAGE_PROVIDER == "local":
        base = DATA / prefix
        if not base.exists():
            return []
        return [str(p.relative_to(DATA)).replace("\\", "/")
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
    if STORAGE_PROVIDER == "local":
        for k in keys:
            (DATA / k).unlink(missing_ok=True)
    elif STORAGE_PROVIDER == "gcp_native":
        from google.api_core.exceptions import NotFound
        bucket = _gcs_bucket()
        for k in keys:
            try:  # a stale listing may name an object already gone — ignore it
                bucket.blob(k).delete()
            except NotFound:
                pass
    else:
        for i in range(0, len(keys), 1000):  # S3 DeleteObjects caps at 1000/call
            _s3().delete_objects(
                Bucket=STORAGE_BUCKET,
                Delete={"Objects": [{"Key": k} for k in keys[i:i + 1000]],
                        "Quiet": True})
    return len(keys)


def delete_key(key: str) -> None:
    if STORAGE_PROVIDER == "local":
        (DATA / key).unlink(missing_ok=True)
    elif STORAGE_PROVIDER == "gcp_native":
        from google.api_core.exceptions import NotFound
        try:
            _gcs_bucket().blob(key).delete()
        except NotFound:
            pass  # already gone — deleting is idempotent, like the other providers
    else:
        _s3().delete_object(Bucket=STORAGE_BUCKET, Key=key)


def local_path(key: str) -> Path:
    """Absolute path for the local provider (dev-only direct serving)."""
    return DATA / key
