"""Speech-to-text for UPLOADED videos — the transcript branch's source when
there are no captions (YouTube has captions; uploads don't).

transcribe(path) pulls mono 16 kHz audio out of the file with ffmpeg, sends it
to the ASR provider, and returns timed cues [{text, t_start, t_end}] in the SAME
shape YouTube captions produce — so chunk_cues() and everything downstream (the
durable GCP copy, text embedding, retrieval, the synced transcript panel) treat
an upload and a YouTube video identically.

Default provider: OpenAI whisper-1. `verbose_json` yields per-segment
timestamps, and the request reuses LLM_API_KEY (one OpenAI key for the answer,
the embeddings and transcription). The API caps a request at 25 MB, so audio
above that is transcribed in time windows and each window's timestamps are
offset back to absolute time. Every failure path returns [] — the caller then
indexes the video visually, exactly as for a caption-less YouTube video.
"""
from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path

from .. import config

# mono 16 kHz mp3 @ 64 kbps ≈ 0.48 MB/min, so a 20-min window ≈ 9.6 MB — safely
# under the API's 25 MB ceiling even with container overhead.
_MAX_BYTES = 24 * 1024 * 1024
_WINDOW_S = 20 * 60


def _seg_get(seg, key, default=None):
    """Segments are pydantic objects from the OpenAI SDK, or dicts from an
    OpenAI-compatible server — read either the same way."""
    return seg.get(key, default) if isinstance(seg, dict) else getattr(seg, key, default)


def _extract_audio(src: Path, dst: Path,
                   start: float | None = None, dur: float | None = None) -> Path:
    """ffmpeg: (optionally a [start, start+dur] slice of) the source's audio ->
    mono 16 kHz mp3. Fast input-seeking (-ss before -i) is accurate enough for a
    transcript panel and keeps window extraction cheap."""
    cmd = ["ffmpeg", "-nostdin", "-y", "-loglevel", "error"]
    if start:
        cmd += ["-ss", f"{start:.3f}"]
    if dur is not None:
        cmd += ["-t", f"{dur:.3f}"]
    cmd += ["-i", str(src), "-vn", "-ac", "1", "-ar", "16000", "-b:a", "64k", str(dst)]
    subprocess.run(cmd, check=True, capture_output=True)
    return dst


def _duration(path: Path) -> float:
    """Seconds via ffprobe (ships with ffmpeg); 0.0 if it can't be read."""
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "quiet", "-show_entries", "format=duration",
             "-of", "csv=p=0", str(path)], capture_output=True, text=True)
        return float(out.stdout.strip())
    except Exception:
        return 0.0


def _openai_segments(audio: Path, offset: float) -> list[dict]:
    """One transcription request -> timed cues, timestamps shifted by `offset`
    (the window's absolute start)."""
    from openai import OpenAI

    client = OpenAI(api_key=config.ASR_API_KEY, base_url=config.ASR_BASE_URL or None)
    kwargs = {"model": config.ASR_MODEL, "response_format": "verbose_json",
              "timestamp_granularities": ["segment"]}
    if config.ASR_LANGUAGE:
        kwargs["language"] = config.ASR_LANGUAGE
    with audio.open("rb") as fh:
        resp = client.audio.transcriptions.create(file=fh, **kwargs)

    segs = _seg_get(resp, "segments") or []
    cues: list[dict] = []
    for s in segs:
        text = (_seg_get(s, "text", "") or "").strip()
        if not text:
            continue
        t0 = float(_seg_get(s, "start", 0.0) or 0.0) + offset
        t1 = float(_seg_get(s, "end", t0) or t0) + offset
        cues.append({"text": text, "t_start": t0, "t_end": t1})
    return cues


def transcribe(path: str | Path) -> list[dict]:
    """Timed cues [{text, t_start, t_end}] for an uploaded video, or [] when ASR
    is off/misconfigured, the file has no audio, or the call fails."""
    if not (config.ENABLE_ASR and config.ASR_PROVIDER):
        return []
    if config.ASR_PROVIDER != "openai":
        print(f"[asr] unknown ASR_PROVIDER {config.ASR_PROVIDER!r} — uploads stay visual-only")
        return []
    if not config.ASR_API_KEY:
        print("[asr] no ASR/OpenAI/LLM API key set — uploads stay visual-only")
        return []

    src = Path(path)
    if not src.exists():
        return []

    with tempfile.TemporaryDirectory() as td:
        full = Path(td) / "audio.mp3"
        try:
            _extract_audio(src, full)              # whole audio track, one pass
        except subprocess.CalledProcessError:
            print(f"[asr] {src.name}: no audio track (or ffmpeg failed) — visual-only")
            return []

        # Common case: short enough for a single request, no window drift.
        if full.stat().st_size <= _MAX_BYTES:
            return _openai_segments(full, offset=0.0)

        # Long audio: transcribe in windows cut from the original source.
        dur = _duration(src)
        if dur <= 0:                               # can't window — try it whole
            return _openai_segments(full, offset=0.0)
        cues: list[dict] = []
        start = 0.0
        while start < dur:
            win = Path(td) / f"w_{int(start)}.mp3"
            _extract_audio(src, win, start=start, dur=min(_WINDOW_S, dur - start))
            cues += _openai_segments(win, offset=start)
            win.unlink(missing_ok=True)
            start += _WINDOW_S
        return cues
