"""YouTube transcript extraction — the text branch's source.

Reuses the SAME authenticated yt-dlp path as the video download (cookies + JS
runtime + fallback clients, see fetch.py), so captions come through wherever
the video download already works — no second tool, no second blocking fight.

fetch_transcript() pulls the caption track (manual if present, else
auto-generated) as YouTube's json3 format, parses it into timed cues, and
chunk_cues() groups them into ~TRANSCRIPT_CHUNK_SECONDS passages so each chunk
is a coherent spoken segment with a real t_start/t_end. Returns [] when a video
has no captions — the caller then just indexes it visually (never fatal).
"""
from __future__ import annotations

import json
from pathlib import Path

from .. import config
from .fetch import _yt_opts, scratch_dir


def _sub_opts(video_id: str) -> dict:
    """yt-dlp options for subtitles only (no video download)."""
    opts = _yt_opts(video_id, config.YT_PLAYER_CLIENTS)  # inherit cookies/js/proxy
    opts.update({
        "skip_download": True,
        "writesubtitles": True,        # manual captions if the uploader added them
        "writeautomaticsub": True,     # else YouTube's auto-generated ones
        "subtitleslangs": config.TRANSCRIPT_LANGS,
        "subtitlesformat": "json3",    # clean per-event timing (unlike rolling vtt)
        "outtmpl": str(scratch_dir() / f"{video_id}.%(ext)s"),
    })
    return opts


def _parse_json3(path: Path) -> list[dict]:
    """YouTube json3 -> [{text, t_start, t_end}] (seconds)."""
    data = json.loads(path.read_text(encoding="utf-8", errors="replace"))
    cues: list[dict] = []
    for ev in data.get("events", []):
        segs = ev.get("segs")
        if not segs:
            continue
        text = "".join(s.get("utf8", "") for s in segs).strip()
        if not text:
            continue
        t0 = ev.get("tStartMs", 0) / 1000.0
        dur = ev.get("dDurationMs", 0) / 1000.0
        cues.append({"text": text, "t_start": t0, "t_end": t0 + dur})
    return cues


def fetch_transcript(url: str, video_id: str) -> list[dict]:
    """Download + parse captions. Returns timed cues, or [] if none exist."""
    import yt_dlp

    scratch = scratch_dir()
    for f in scratch.glob(f"{video_id}.*.json3"):  # clear stale subs from a prior run
        f.unlink(missing_ok=True)
    try:
        with yt_dlp.YoutubeDL(_sub_opts(video_id)) as ydl:
            ydl.extract_info(url, download=True)  # writes the .json3 file(s)
    except Exception as exc:
        print(f"[transcript] {video_id}: fetch failed ({type(exc).__name__}: {exc})")
        return []
    # Pick the first language we asked for that actually got written.
    for lang in config.TRANSCRIPT_LANGS:
        hit = scratch / f"{video_id}.{lang}.json3"
        if hit.exists():
            cues = _parse_json3(hit)
            hit.unlink(missing_ok=True)
            return cues
    # Any json3 at all (language tag might differ, e.g. "en-orig").
    for f in sorted(scratch.glob(f"{video_id}.*.json3")):
        cues = _parse_json3(f)
        f.unlink(missing_ok=True)
        if cues:
            return cues
    return []


def get_youtube_cues(url: str, video_id: str) -> list[dict]:
    """Pick the transcript SOURCE for a YouTube video per TRANSCRIPT_PROVIDER.

      supadata -> Supadata only.
      youtube  -> yt-dlp captions only (cookies/proxy, the original path).
      auto     -> Supadata if SUPADATA_API_KEY is set (falling back to yt-dlp on
                  an empty result or error), else yt-dlp captions.

    Both sources return the same [{text,t_start,t_end}] cues, so everything
    downstream is identical. See src/config.py TRANSCRIPT_PROVIDER."""
    provider = config.TRANSCRIPT_PROVIDER

    if provider == "supadata":
        from .supadata import fetch_transcript_supadata
        return fetch_transcript_supadata(url, video_id)

    if provider == "auto" and config.SUPADATA_API_KEY:
        from .supadata import fetch_transcript_supadata
        cues = fetch_transcript_supadata(url, video_id)
        if cues:
            return cues
        print(f"[transcript] {video_id}: Supadata returned nothing — "
              "falling back to yt-dlp captions")
        return fetch_transcript(url, video_id)

    # provider == "youtube", or auto with no Supadata key configured.
    return fetch_transcript(url, video_id)


def chunk_cues(cues: list[dict], chunk_seconds: float | None = None) -> list[dict]:
    """Group cues into ~chunk_seconds passages, each with t_start/t_end.

    Speaker-aware: when cues carry a `speaker` (diarization ran, src/ingest/
    diarize.py), a chunk also breaks on a speaker change and is stamped with that
    speaker, so every chunk is single-speaker. With no speakers it behaves exactly
    as before (no `speaker` key)."""
    if not cues:
        return []
    span = chunk_seconds or config.TRANSCRIPT_CHUNK_SECONDS
    chunks: list[dict] = []
    buf: list[str] = []
    start: float | None = None
    speaker: str | None = None
    last_end = cues[0]["t_end"]

    def flush() -> None:
        nonlocal buf, start, speaker
        if buf and start is not None:
            ch = {"text": " ".join(buf), "t_start": start, "t_end": last_end}
            if speaker is not None:
                ch["speaker"] = speaker
            chunks.append(ch)
        buf, start, speaker = [], None, None

    for c in cues:
        cs = c.get("speaker")
        if start is not None and speaker is not None and cs is not None and cs != speaker:
            flush()                                    # speaker changed -> new chunk
        if start is None:
            start, speaker = c["t_start"], cs
        buf.append(c["text"])
        last_end = c["t_end"]
        if last_end - start >= span:
            flush()
    flush()
    return chunks
