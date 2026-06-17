"""Ingestion: video in -> sampled frames -> CLIP vectors -> Qdrant.

Two entry points to get a video file:
  * `fetch_youtube(url)`  — download with yt-dlp
  * a file the user uploaded (handled in main.py, saved under data/videos/)

Then `ingest_video_file(...)` samples frames with FFmpeg, embeds each frame with
CLIP, and upserts them. We look only at the *picture* — audio is never touched.
"""
from __future__ import annotations

import json
import re
import subprocess
import uuid
from pathlib import Path
from typing import Any, Callable, Iterator

from PIL import Image

from .config import get_settings
from .embeddings import embed_images
from . import vector_store

Progress = Callable[[str, dict], None]


def _noop(stage: str, detail: dict) -> None:  # default progress sink
    pass


# ── Acquiring a video ────────────────────────────────────────────────────────

def fetch_youtube(url: str, progress: Progress = _noop) -> tuple[str, str, Path]:
    """Download a YouTube (or yt-dlp-supported) URL. Returns (video_id, title, path)."""
    import yt_dlp

    s = get_settings()
    s.video_dir.mkdir(parents=True, exist_ok=True)
    progress("download", {"message": "Resolving video…", "url": url})

    opts = {
        # We only ever look at the picture, so grab a small video-only stream
        # (<=480p). CLIP downsizes to 224px anyway — 4K would just waste bandwidth.
        "format": ("bestvideo[height<=480][ext=mp4]/bestvideo[height<=480]/"
                   "best[height<=480][ext=mp4]/best[height<=480]/best"),
        "outtmpl": str(s.video_dir / "%(id)s.%(ext)s"),
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
    }
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=True)
        path = Path(ydl.prepare_filename(info))
    video_id = f"yt_{info['id']}"
    title = info.get("title") or video_id
    progress("download", {"message": "Downloaded", "title": title})
    return video_id, title, path


# ── Frame sampling (FFmpeg) ──────────────────────────────────────────────────

def _probe_duration(path: Path) -> float:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
        capture_output=True, text=True,
    )
    try:
        return float(out.stdout.strip())
    except ValueError:
        return 0.0


def _extract_interval(path: Path, out_dir: Path) -> list[tuple[int, Path]]:
    """One frame every FRAME_INTERVAL_SEC seconds (single FFmpeg pass)."""
    s = get_settings()
    interval = max(0.2, s.FRAME_INTERVAL_SEC)
    duration = _probe_duration(path)
    if s.MAX_FRAMES and duration > 0:
        est = duration / interval
        if est > s.MAX_FRAMES:
            interval = duration / s.MAX_FRAMES  # widen spacing to respect the cap
    fps = 1.0 / interval
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", "-i", str(path),
         "-vf", f"fps={fps:.6f}", "-q:v", "3", str(out_dir / "%06d.jpg")],
        check=True, capture_output=True,
    )
    frames = sorted(out_dir.glob("*.jpg"))
    # Frame i (1-indexed) sits at ~ (i-1) * interval seconds in.
    return [(int(round((i) * interval * 1000)), p) for i, p in enumerate(frames)]


_PTS_RE = re.compile(r"pts_time:([0-9.]+)")


def _extract_scene(path: Path, out_dir: Path) -> list[tuple[int, Path]]:
    """One frame per detected scene cut. Timestamps parsed from showinfo."""
    s = get_settings()
    proc = subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "info", "-i", str(path),
         "-vf", f"select='gt(scene,{s.SCENE_THRESHOLD})',showinfo",
         "-vsync", "vfr", "-q:v", "3", str(out_dir / "%06d.jpg")],
        capture_output=True, text=True,
    )
    times = [int(round(float(m) * 1000)) for m in _PTS_RE.findall(proc.stderr)]
    frames = sorted(out_dir.glob("*.jpg"))
    pairs = list(zip(times, frames)) if len(times) >= len(frames) else \
        [(int(round(i * 1000)), p) for i, p in enumerate(frames)]
    if s.MAX_FRAMES and len(pairs) > s.MAX_FRAMES:
        step = len(pairs) / s.MAX_FRAMES
        pairs = [pairs[int(i * step)] for i in range(s.MAX_FRAMES)]
    return pairs


def extract_frames(path: Path, video_id: str) -> list[tuple[int, Path]]:
    s = get_settings()
    out_dir = s.frame_dir / video_id
    out_dir.mkdir(parents=True, exist_ok=True)
    if s.FRAME_STRATEGY == "scene":
        return _extract_scene(path, out_dir)
    return _extract_interval(path, out_dir)


# ── Full pipeline ────────────────────────────────────────────────────────────

def _batched(items: list, n: int) -> Iterator[list]:
    for i in range(0, len(items), n):
        yield items[i:i + n]


def ingest_video_file(
    path: Path,
    video_id: str,
    title: str,
    *,
    source: str,
    url: str | None = None,
    progress: Progress = _noop,
) -> dict[str, Any]:
    """Sample, embed and index every frame of a local video file."""
    vector_store.ensure_collection()
    vector_store.delete_video(video_id)  # idempotent re-ingest

    progress("frames", {"message": "Sampling frames…"})
    frames = extract_frames(path, video_id)
    if not frames:
        raise RuntimeError("No frames could be extracted from the video.")
    progress("frames", {"message": f"Extracted {len(frames)} frames", "count": len(frames)})

    total = 0
    for batch in _batched(frames, 32):
        images = [Image.open(p).convert("RGB") for _, p in batch]
        vectors = embed_images(images)
        for img in images:
            img.close()
        payloads = [{
            "video_id": video_id,
            "title": title,
            "source": source,
            "url": url,
            "ms": ms,
            "frame": f"{video_id}/{p.name}",
        } for ms, p in batch]
        vector_store.upsert_frames(vectors, payloads)
        total += len(batch)
        progress("embed", {"message": f"Indexed {total}/{len(frames)} frames",
                           "done": total, "total": len(frames)})

    meta = {"video_id": video_id, "title": title, "source": source,
            "url": url, "frames": len(frames)}
    (get_settings().frame_dir / video_id / "meta.json").write_text(json.dumps(meta))
    progress("done", meta)
    return meta


def ingest_youtube(url: str, progress: Progress = _noop) -> dict[str, Any]:
    video_id, title, path = fetch_youtube(url, progress)
    return ingest_video_file(path, video_id, title, source="youtube", url=url,
                             progress=progress)


def new_upload_id() -> str:
    return f"up_{uuid.uuid4().hex[:10]}"
