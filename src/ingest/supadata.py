"""Supadata transcript source — an alternative to yt-dlp captions.

Supadata (https://supadata.ai) returns a YouTube video's captions from ITS OWN
infrastructure, so it sidesteps the by-IP bot-check that blocks yt-dlp on a
datacenter (or rate-limited) IP. Same output contract as
transcript.fetch_transcript(): returns [{text, t_start, t_end}] cues in seconds,
or [] on any failure — so the caller falls back or just indexes visually, never
fatal.

Only the caption FETCH is replaced; chunking, diarization and embedding are
shared with the yt-dlp path. Supadata does NOT download video, so the frame
branch still uses yt-dlp or an upload. Config: SUPADATA_API_KEY, SUPADATA_BASE_URL
(see src/config.py). Plain-stdlib HTTP, matching the Jina/Cohere/Voyage adapters.
"""
from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request

from .. import config


def fetch_transcript_supadata(url: str, video_id: str) -> list[dict]:
    """GET /v1/transcript for `url`, mapped to [{text,t_start,t_end}] (seconds).

    Returns [] on a missing key, HTTP/network error, quota exhaustion or a
    caption-less video — the same non-fatal contract as the yt-dlp path."""
    if not config.SUPADATA_API_KEY:
        return []

    # Ask for timestamped chunks (text omitted -> the API returns a list of
    # {text, offset, duration} segments, offsets in MILLISECONDS).
    params = {"url": url}
    if config.TRANSCRIPT_LANGS:
        params["lang"] = config.TRANSCRIPT_LANGS[0]
    endpoint = (f"{config.SUPADATA_BASE_URL.rstrip('/')}/transcript?"
                + urllib.parse.urlencode(params))
    req = urllib.request.Request(endpoint,
                                 headers={"x-api-key": config.SUPADATA_API_KEY})
    try:
        with urllib.request.urlopen(req, timeout=config.SUPADATA_TIMEOUT) as resp:
            data = json.loads(resp.read().decode("utf-8", errors="replace"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")[:200]
        print(f"[supadata] {video_id}: HTTP {exc.code} — {body}")
        return []
    except Exception as exc:
        print(f"[supadata] {video_id}: fetch failed ({type(exc).__name__}: {exc})")
        return []

    content = data.get("content")
    if not isinstance(content, list):   # plain-text mode or a caption-less video
        print(f"[supadata] {video_id}: no timed segments")
        return []

    cues: list[dict] = []
    for seg in content:
        text = (seg.get("text") or "").strip()
        if not text:
            continue
        t0 = seg.get("offset", 0) / 1000.0      # ms -> seconds
        dur = seg.get("duration", 0) / 1000.0
        cues.append({"text": text, "t_start": t0, "t_end": t0 + dur})
    return cues
