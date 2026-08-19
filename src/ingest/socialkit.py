"""SocialKit source — YouTube transcript AND video from one hosted API.

SocialKit (https://socialkit.dev) returns a YouTube video's captions AND the
video file itself from ITS OWN infrastructure, so it sidesteps the by-IP
bot-check that blocks yt-dlp on a datacenter (or rate-limited) IP — one key
covers BOTH branches, no cookies, no proxy.

  transcript -> fetch_transcript_socialkit(): [{text,t_start,t_end}] cues, or []
  video      -> fetch_video_socialkit(): (scratch_path, title), or None

Both are non-fatal: on a missing key, HTTP/quota error, a caption-less video or
a failed download they return the empty value and the caller falls back to
yt-dlp (see transcript.get_youtube_cues / fetch.fetch_youtube). Video uses the
async v2 job API (submit -> poll -> download) so it isn't limited by the 10 MB
sync cap. Config: SOCIALKIT_API_KEY, SOCIALKIT_BASE_URL, SOCIALKIT_VIDEO_QUALITY,
SOCIALKIT_TIMEOUT (see src/config.py). Plain-stdlib HTTP, matching the other
adapters.
"""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from .. import config
from .fetch import scratch_dir

_POLL_INTERVAL = 4.0     # seconds between async-download status checks
_POLL_BUDGET = 420.0     # give up waiting for a download job after this long

# SocialKit's API sits behind Cloudflare, which 403s (error 1010) the default
# Python-urllib User-Agent as a bot — send a browser UA so requests get through.
_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/122.0 Safari/537.36")


def _read(req: urllib.request.Request) -> dict | None:
    """Send `req`; return parsed JSON, or None on any HTTP/network error."""
    try:
        with urllib.request.urlopen(req, timeout=config.SOCIALKIT_TIMEOUT) as resp:
            return json.loads(resp.read().decode("utf-8", errors="replace"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")[:200]
        print(f"[socialkit] HTTP {exc.code} — {body}")
    except Exception as exc:
        print(f"[socialkit] request failed ({type(exc).__name__}: {exc})")
    return None


def _get(path: str, params: dict) -> dict | None:
    q = urllib.parse.urlencode({**params, "access_key": config.SOCIALKIT_API_KEY})
    url = f"{config.SOCIALKIT_BASE_URL.rstrip('/')}{path}?{q}"
    return _read(urllib.request.Request(url, headers={"User-Agent": _UA}))


def _post(path: str, body: dict) -> dict | None:
    data = json.dumps({**body, "access_key": config.SOCIALKIT_API_KEY}).encode()
    req = urllib.request.Request(
        f"{config.SOCIALKIT_BASE_URL.rstrip('/')}{path}", data=data,
        headers={"Content-Type": "application/json", "User-Agent": _UA})
    return _read(req)


def fetch_transcript_socialkit(url: str, video_id: str) -> list[dict]:
    """GET /youtube/transcript -> [{text,t_start,t_end}] (seconds).

    Returns [] on a missing key, HTTP/network error, quota exhaustion or a
    caption-less video — the same non-fatal contract as the yt-dlp path."""
    if not config.SOCIALKIT_API_KEY:
        return []
    data = _get("/youtube/transcript", {"url": url})
    segs = ((data or {}).get("data") or {}).get("transcriptSegments")
    if not isinstance(segs, list):
        print(f"[socialkit] {video_id}: no transcript segments")
        return []
    cues: list[dict] = []
    for s in segs:
        text = (s.get("text") or "").strip()
        if not text:
            continue
        t0 = float(s.get("start", 0.0))
        dur = float(s.get("duration", 0.0))
        cues.append({"text": text, "t_start": t0, "t_end": t0 + dur})
    return cues


def fetch_video_socialkit(url: str, video_id: str) -> tuple[Path, str] | None:
    """Download the video via the async v2 job API (submit -> poll -> download).

    Returns (scratch_path, title), or None on a missing key, job failure,
    timeout or download error — the caller then falls back to yt-dlp."""
    if not config.SOCIALKIT_API_KEY:
        return None

    # 1. submit the job
    sub = _post("/v2/youtube/download",
                {"url": url, "quality": config.SOCIALKIT_VIDEO_QUALITY, "format": "mp4"})
    job = ((sub or {}).get("data") or {}).get("jobId")
    if not job:
        return None

    # 2. poll until ready / failed / budget exhausted
    info: dict = {}
    deadline = time.monotonic() + _POLL_BUDGET
    while time.monotonic() < deadline:
        time.sleep(_POLL_INTERVAL)
        info = ((_get(f"/v2/downloads/{job}", {}) or {}).get("data") or {})
        status = info.get("status")
        if status == "ready":
            break
        if status == "failed":
            print(f"[socialkit] {video_id}: download job failed")
            return None
    dl = info.get("downloadUrl")
    if not dl:
        print(f"[socialkit] {video_id}: download job did not finish in time")
        return None

    # 3. stream the file to worker scratch (deleted after the run; the durable
    #    copy lives in object storage, same as the yt-dlp path).
    dest = scratch_dir() / f"{video_id}.mp4"
    try:
        dreq = urllib.request.Request(dl, headers={"User-Agent": _UA})
        with urllib.request.urlopen(dreq, timeout=config.SOCIALKIT_TIMEOUT) as resp, \
                open(dest, "wb") as fh:
            while chunk := resp.read(1 << 20):
                fh.write(chunk)
    except Exception as exc:
        print(f"[socialkit] {video_id}: file download failed ({type(exc).__name__}: {exc})")
        dest.unlink(missing_ok=True)
        return None
    return dest, (info.get("title") or video_id)
