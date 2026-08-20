"""Speaker diarization — captions/ASR give the WHEN + WHAT, Gemini gives the WHO.

Fitted to MomentSearch's cue shape. Instead of asking an LLM to (re)write the
transcript — slow, and long generations loop — Gemini WATCHES the video and
returns only a compact SPEAKER-CHANGE INDEX:

    HH:MM:SS | Speaker Full Name | first words of that turn, verbatim

Those opening words are string-matched into our verbatim cue stream to transfer
each name onto a real timestamp. The transcript TEXT is never altered — Gemini's
output is used only as *pointers* (who / roughly-when). Worst case for a model
error is a boundary sitting a cue or two off.

The video is processed in DIARIZE_WINDOW_S slices (Gemini's start/end offsets;
nothing is downloaded on the YouTube path): short windows can't loop and run in
parallel. Window 0 runs alone to learn the speakers' names, which are pinned for
the rest so spellings don't drift.

Source:
  * YouTube -> the URL is handed to Gemini directly (file_uri).
  * upload  -> the local file is pushed through the Gemini Files API first.

Gemini-only (video understanding), so it ALWAYS uses GEMINI_API_KEY regardless of
LLM_PROVIDER. Disabled, keyless, or ANY failure -> the cues are returned unchanged
(no speaker labels); this never raises.
"""
from __future__ import annotations

import re
import time
from concurrent.futures import ThreadPoolExecutor

from ..config import (DIARIZE_ENABLED, DIARIZE_MAX_WORKERS, DIARIZE_MODEL,
                      DIARIZE_WINDOW_S, GEMINI_API_KEY)

_OVERLAP_S = 15         # window overlap; seam duplicates are deduped on merge
_ALIGN_WINDOW_S = 150   # how far a matched phrase may sit from its claimed time

_PROMPT = """\
You are given a segment of a video, from {start} to {end}. Your ONLY job is to produce a compact SPEAKER-CHANGE INDEX for that segment — absolutely NOT a transcript.
{names_line}
Output one line for EVERY point in the segment where the person speaking changes:

HH:MM:SS | Speaker Full Name | first words of that turn, verbatim

Rules:
- HARD LIMIT: at most 10 words after the second |. NEVER continue a turn's text beyond 10 words — stop mid-sentence.
- Timestamps are ABSOLUTE positions in the full video (so between {start} and {end}), strictly increasing.
- Output ONLY these lines, nothing else. No transcript, no summary, no markdown, no numbering.
- Spell each speaker's name IDENTICALLY on every line.
- The words after the second | must be exactly what is said at that moment, not a paraphrase.
- Skip brief backchannel interjections (under ~3 words, like "Yeah." "Right.") that don't take over the conversation.
- Also include the speaker who is ALREADY talking when the segment begins, as its first line.
"""

_NAMES_KNOWN = ("The speakers in this video are: {names}. "
                "Use EXACTLY these names, spelled exactly like this.\n")
_NAMES_UNKNOWN = ("Identify each speaker's REAL FULL NAME (people often introduce "
                  "themselves near the start; on-screen text and the video title also "
                  "help).{hints} Spell every name carefully and IDENTICALLY on every "
                  "line. If a name truly never appears, use \"Speaker 1\", \"Speaker 2\", "
                  "etc., consistently.\n")

_MAP_LINE = re.compile(
    r"^\s*(\d{1,2}):(\d{2})(?::(\d{2}))?\s*\|\s*([^|]{1,60}?)\s*\|\s*(.+?)\s*$")


def _norm(tok: str) -> str:
    return re.sub(r"[^a-z0-9']", "", tok.lower())


def _fmt_ts(s: float) -> str:
    s = int(s)
    return f"{s // 3600:02d}:{s % 3600 // 60:02d}:{s % 60:02d}"


# --- Gemini access ------------------------------------------------------------

def _client():
    from google import genai
    return genai.Client(api_key=GEMINI_API_KEY)


def _upload(client, path: str):
    """Push a local upload to the Gemini Files API and block until it's ACTIVE
    (Gemini transcodes video server-side before it can be read)."""
    f = client.files.upload(file=path)
    deadline = time.time() + 600
    while time.time() < deadline:
        state = getattr(f.state, "name", str(f.state))
        if state == "ACTIVE":
            return f
        if state == "FAILED":
            raise RuntimeError("Gemini file processing FAILED")
        time.sleep(3)
        f = client.files.get(name=f.name)
    raise RuntimeError("Gemini file not ACTIVE in time")


def _video_part(ref, start_s: int, end_s: int):
    """A video Part for one time slice — `ref` is the YouTube URL (str) or an
    uploaded Gemini File."""
    from google.genai import types
    if isinstance(ref, str):
        fd = types.FileData(file_uri=ref)
    else:
        fd = types.FileData(file_uri=ref.uri, mime_type=ref.mime_type)
    return types.Part(
        file_data=fd,
        video_metadata=types.VideoMetadata(
            start_offset=f"{start_s}s", end_offset=f"{end_s}s"),
    )


def _map_window_once(client, ref, start_s: int, end_s: int,
                     names: list[str] | None, hints: str = "") -> str:
    from google.genai import types
    names_line = (_NAMES_KNOWN.format(names=", ".join(names)) if names
                  else _NAMES_UNKNOWN.format(hints=hints))
    prompt = _PROMPT.format(start=_fmt_ts(start_s), end=_fmt_ts(end_s),
                            names_line=names_line)
    resp = client.models.generate_content(
        model=DIARIZE_MODEL,
        contents=types.Content(parts=[_video_part(ref, start_s, end_s),
                                      types.Part(text=prompt)]),
        config=types.GenerateContentConfig(
            temperature=0.0, max_output_tokens=8192,
            # thinking is pointless for a mechanical index and eats the budget
            thinking_config=types.ThinkingConfig(thinking_budget=0),
        ),
    )
    return resp.text or ""


def _map_window(client, ref, start_s: int, end_s: int,
                names: list[str] | None, hints: str = "", attempts: int = 3) -> str:
    """Retry with backoff so a burst of parallel videos riding a 429/5xx doesn't
    silently lose its speaker names."""
    for i in range(attempts - 1):
        try:
            return _map_window_once(client, ref, start_s, end_s, names, hints)
        except Exception as exc:
            wait = 20 * (2 ** i)
            print(f"[diarize] window {start_s}-{end_s}s failed "
                  f"({type(exc).__name__}), retrying in {wait}s")
            time.sleep(wait)
    return _map_window_once(client, ref, start_s, end_s, names, hints)


def _parse_map(raw: str, lo_s: int, hi_s: int) -> list[dict]:
    """Parse one window's lines into {claim_s, speaker, opening}. Models are
    sloppy with timestamp formats; each reading is voted against the window's
    known bounds and the winning format is applied, out-of-bounds lines dropped."""
    rows = []
    for line in raw.splitlines():
        m = _MAP_LINE.match(line)
        if not m:
            continue
        a, b, c, name, opening = m.groups()
        if c is None:
            cands = {"ms": int(a) * 60 + int(b)}                       # MM:SS
        else:
            cands = {"hms": (int(a) * 60 + int(b)) * 60 + int(c),      # H:MM:SS
                     "shifted": int(a) * 60 + int(b)}                  # MM:SS:00
        rows.append((cands, name.strip(), opening))
    if not rows:
        return []
    votes: dict[str, int] = {}
    for cands, _, _ in rows:
        for fmt, v in cands.items():
            if lo_s - 30 <= v <= hi_s + 30:
                votes[fmt] = votes.get(fmt, 0) + 1
    turns = []
    for cands, name, opening in rows:
        fmt = max(cands, key=lambda f: votes.get(f, 0))
        ts_s = cands[fmt]
        if not (lo_s - 30 <= ts_s <= hi_s + 30):
            continue
        turns.append({"claim_s": float(ts_s), "speaker": name, "opening": opening})
    return turns


def _speaker_map(client, ref, dur_s: int, title: str | None) -> list[dict]:
    """Windowed speaker map for the whole video: window 0 runs alone to learn the
    names (title helps — guests are usually named there), the rest run in
    parallel with the names pinned."""
    dur_s = dur_s + 1
    windows = [(s, min(s + DIARIZE_WINDOW_S + _OVERLAP_S, dur_s))
               for s in range(0, dur_s, DIARIZE_WINDOW_S)]
    hints = f' The video title is: "{title}" (names often appear in it).' if title else ""

    per_window = [_parse_map(_map_window(client, ref, *windows[0], names=None,
                                         hints=hints), *windows[0])]
    names = sorted({t["speaker"] for t in per_window[0]})
    print(f"[diarize] {len(windows)} windows, speakers: {names}")

    if len(windows) > 1:
        with ThreadPoolExecutor(max_workers=DIARIZE_MAX_WORKERS) as pool:
            futs = [pool.submit(_map_window, client, ref, s, e, names)
                    for s, e in windows[1:]]
            for (s, e), f in zip(windows[1:], futs):
                per_window.append(_parse_map(f.result(), s, e))

    seen: set[str] = set()
    turns: list[dict] = []
    for window in per_window:
        for t in window:
            key = " ".join(_norm(x) for x in t["opening"].split()[:8])
            if key and key in seen:
                continue
            seen.add(key)
            turns.append(t)
    turns.sort(key=lambda t: t["claim_s"])
    return _collapse_names(turns)


def _collapse_names(turns: list[dict]) -> list[dict]:
    """Merge near-duplicate speaker spellings ("Pawel Hurel"/"Pawel Huryn") that
    slip through when a window mishears a name — >=0.8 similarity collapses to the
    most frequent variant."""
    import difflib
    from collections import Counter

    def sim(a: str, b: str) -> float:
        return difflib.SequenceMatcher(None, a.casefold(), b.casefold()).ratio()

    counts = Counter(t["speaker"] for t in turns)
    names = sorted(counts, key=counts.get, reverse=True)
    canon: dict[str, str] = {}
    for name in names:
        if name in canon:
            continue
        canon[name] = name
        for other in names:
            if other not in canon and sim(name, other) >= 0.8:
                canon[other] = name
    merged = {n: c for n, c in canon.items() if n != c}
    if merged:
        print(f"[diarize] collapsed name variants: {merged}")
        for t in turns:
            t["speaker"] = canon[t["speaker"]]
    return turns


# --- cue-level alignment ------------------------------------------------------

def _assign(turns: list[dict], cues: list[dict]) -> list[dict]:
    """Stamp each cue with a speaker. Snap every turn's boundary to the cue that
    best matches its opening words near the claimed time (falling back to the
    claimed time), then label each cue by the latest boundary at/ before it."""
    import bisect

    if not turns:
        return cues
    norm_cues = [[_norm(w) for w in c["text"].split()] for c in cues]

    boundaries: list[tuple[float, str]] = []
    for t in turns:
        toks = [_norm(x) for x in t["opening"].split() if _norm(x)][:8]
        claim = t["claim_s"]
        best_i, best_score = None, 0
        if len(toks) >= 3:
            for i, c in enumerate(cues):
                if not (claim - _ALIGN_WINDOW_S <= c["t_start"] <= claim + _ALIGN_WINDOW_S):
                    continue
                words = norm_cues[i]
                if not words:
                    continue
                # order-preserving overlap of the opening with the cue's lead
                score = sum(1 for a, b in zip(toks, words) if a == b)
                if score > best_score or (
                        score == best_score and best_i is not None and score > 0
                        and abs(c["t_start"] - claim) < abs(cues[best_i]["t_start"] - claim)):
                    best_i, best_score = i, score
        if best_i is not None and best_score >= max(2, len(toks) // 2):
            boundaries.append((cues[best_i]["t_start"], t["speaker"]))
        else:
            boundaries.append((claim, t["speaker"]))

    boundaries.sort(key=lambda b: b[0])
    dedup: list[tuple[float, str]] = []
    for tm, sp in boundaries:              # collapse consecutive same-speaker
        if not dedup or dedup[-1][1] != sp:
            dedup.append((tm, sp))
    times = [b[0] for b in dedup]
    for c in cues:
        j = bisect.bisect_right(times, c["t_start"]) - 1
        c["speaker"] = dedup[max(j, 0)][1]
    return cues


# --- public API ---------------------------------------------------------------

def diarize_cues(cues: list[dict], *, source: str, url: str | None = None,
                 path: str | None = None, video_id: str = "",
                 title: str | None = None, duration_s: float = 0.0
                 ) -> tuple[list[dict], list[str]]:
    """Return (cues, speakers): each cue gains a `speaker`, `speakers` is the
    sorted distinct set. Disabled / keyless / no source / any failure -> the cues
    are returned UNCHANGED with an empty speaker list (never raises)."""
    if not (DIARIZE_ENABLED and GEMINI_API_KEY and cues):
        return cues, []

    client = _client()
    uploaded = None
    try:
        if source == "youtube" and url:
            ref = url
        elif path:
            uploaded = ref = _upload(client, path)
        else:
            return cues, []

        dur_s = int(duration_s or cues[-1]["t_end"])
        turns = _speaker_map(client, ref, dur_s, title)
        if not turns:
            print(f"[diarize] {video_id}: empty speaker map — unlabeled")
            return cues, []
        labeled = _assign(turns, cues)
        speakers = sorted({c["speaker"] for c in labeled if c.get("speaker")})
        print(f"[diarize] {video_id}: {len(turns)} turns, speakers={speakers}")
        return labeled, speakers
    except Exception as exc:
        print(f"[diarize] {video_id}: failed ({type(exc).__name__}: {exc}) — unlabeled")
        return cues, []
    finally:
        if uploaded is not None:
            try:
                client.files.delete(name=uploaded.name)
            except Exception:
                pass
