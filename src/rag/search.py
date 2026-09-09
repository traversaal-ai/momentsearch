"""Read path: question -> retrieve -> gate -> cited answer (or honest abstain).

Retrieval is milliseconds; the multimodal LLM call is seconds and dominates
cost. So the shape is a confidence funnel: fetch KNN_K candidates, collapse
temporal near-duplicates, trim to TOP_K, and — Gate 1 — if even the best
score is below CONFIDENCE_THRESHOLD, abstain WITHOUT calling the LLM. That
one free check kills most hallucination risk. Generated answers get their
[n] citations validated; invented references are stripped.

Three refinements sit on that spine (all env-tunable, see config.py):
  - per-moment pruning after the rerank (FRAME_SCORE_RATIO / TEXT_RERANK_FLOOR),
    off by default — measured not to help with the default CLIP + MiniLM stack;
  - the model also reads the speech AROUND each moment (CONTEXT_PAD_S);
  - a multi-part question is split (query_split.py) and each part retrieved in
    parallel — the split check runs alongside the plain retrieval, so a
    single-part question pays no extra time.
"""
from __future__ import annotations

import json
import re
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from .. import config, db, llm, storage
from ..config import (BRANCH_TOP_K, CONFIDENCE_THRESHOLD, CROSS_MODAL_BOOST,
                      FUSION_WINDOW_S, RRF_K, TEXT_CONFIDENCE_THRESHOLD, TOP_K)
from . import query_split, vector_store
from .embeddings import embed_query, embed_text

ABSTAIN = ("I couldn't find that in your videos — nothing indexed looks "
           "related to the question (neither what's on screen nor what's said).")


def _seconds(ms: int) -> str:
    s = ms // 1000
    return f"{s // 60:02d}:{s % 60:02d}"


def _fuse(visual_hits: list[dict], text_hits: list[dict]) -> list[dict]:
    """Reciprocal-Rank-Fusion of the two branches into time windows.

    Raw scores are incomparable (CLIP ~0.3 vs bge ~0.7), so we rank each branch
    on its own and score by rank: rrf = 1/(RRF_K + rank). Then we bucket hits
    within FUSION_WINDOW_S seconds of each other (same video) into one 'moment',
    sum their rrf, and boost windows where BOTH modalities agree — two
    independent signals pointing at the same instant is the strongest evidence.
    """
    def ranked(hits, modality):
        out = []
        for rank, h in enumerate(hits):
            t = float(h.get("t_start", h.get("ms", 0) / 1000.0))
            out.append({**h, "modality": modality, "rrf": 1.0 / (RRF_K + rank), "t": t})
        return out

    windows: list[dict] = []
    # Hits arrive best-first (rrf desc), so the first hit landing in a window for
    # a given modality is that modality's best hit there.
    for h in sorted(ranked(visual_hits, "frame") + ranked(text_hits, "text"),
                    key=lambda x: x["rrf"], reverse=True):
        w = next((w for w in windows if w["video_id"] == h["video_id"]
                  and abs(w["t"] - h["t"]) <= FUSION_WINDOW_S), None)
        if w is None:
            w = {"video_id": h["video_id"], "t": h["t"], "rrf": 0.0,
                 "modalities": set(), "frame": None, "text": None}
            windows.append(w)
        w["modalities"].add(h["modality"])
        slot = "frame" if h["modality"] == "frame" else "text"
        # Keep only the BEST hit per modality. Summing every hit would let a
        # burst of near-identical frames clustered in one 15s window inflate its
        # score past a genuine frame+transcript match — the bug that ranked a
        # silent frame-burst above the moment that actually answered.
        if w[slot] is None:
            w[slot] = h
    for w in windows:
        # Score = best frame + best transcript hit; ×boost when BOTH modalities
        # agree at this instant (two independent signals = strongest evidence).
        w["rrf"] = (w["frame"]["rrf"] if w["frame"] else 0.0) + \
                   (w["text"]["rrf"] if w["text"] else 0.0)
        if {"frame", "text"} <= w["modalities"]:
            w["rrf"] *= CROSS_MODAL_BOOST
    windows.sort(key=lambda w: w["rrf"], reverse=True)
    return windows


def _deeplink(video: dict | None, video_id: str, ms: int) -> str:
    secs = ms // 1000
    if video and video.get("source") == "youtube" and video.get("url"):
        sep = "&" if "?" in video["url"] else "?"
        return f"{video['url']}{sep}t={secs}"
    return f"/api/video/{video_id}#t={secs}"


def _thumb_url(user_id: str, video_id: str, idx: int) -> str:
    """Browser-facing thumbnail URL. Presigned GET straight to the bucket when
    the provider supports it (an <img> tag can't send auth headers); the API
    serves the bytes itself only in local-dev mode."""
    key = storage.frame_key(user_id, video_id, idx)
    # presign_capable(key) is False for a sample: its frames live in
    # demo_corpus/, on this disk, so there is no bucket object to sign.
    if storage.presign_capable(key):
        return storage.presign_get(key)
    return f"/api/frame/{video_id}/{idx:06d}.jpg?u={user_id}"


def _media_url(video: dict | None, user_id: str, video_id: str) -> str | None:
    """Playback URL for uploaded videos (YouTube plays via its own URL)."""
    if not video or video.get("source") != "upload" or not video.get("storage_key"):
        return None
    if storage.presign_capable():
        return storage.presign_get(video["storage_key"])
    return f"/api/video/{video_id}?u={user_id}"


def _match_pct(w: dict) -> int:
    """A 0-100 'match %' for the card — the moment's REAL strength, not its rank.

    RRF/blend are rank-based and squash together (rank 1/2/3 = 0.0164/0.0161/0.0159),
    so a naive score-vs-top would show every result at ~99%. Instead we read the raw
    branch signal: the reranker's 0-1 relevance when the moment was judged, else the
    raw cosine mapped between its gate threshold (weak) and its STRONG threshold.
    Take the strongest branch. Shown moments already cleared the gate, so the display
    is floored at BASE — nothing legitimate ever reads as ~0%."""
    def _norm(v: float, lo: float, hi: float) -> float:
        if hi <= lo:
            return 1.0 if v >= hi else 0.0
        return max(0.0, min(1.0, (v - lo) / (hi - lo)))

    strengths = []
    fr = w.get("frame")
    if fr:
        strengths.append(_norm(fr.get("score", 0.0),
                               config.CONFIDENCE_THRESHOLD, config.VISUAL_STRONG))
    tx = w.get("text")
    if tx:
        # The reranker already outputs a 0-1 relevance; prefer it when present.
        strengths.append(w["rerank"] if "rerank" in w else
                         _norm(tx.get("score", 0.0),
                               config.TEXT_CONFIDENCE_THRESHOLD, config.TEXT_STRONG))
    s = max(strengths) if strengths else 0.0
    BASE = 50   # a just-cleared moment reads ~50%, a clearly-strong one ~100%
    return int(round(BASE + s * (100 - BASE)))


def _rerank(question: str, windows: list[dict]) -> list[dict]:
    """Cross-encoder rerank of the text-bearing moments — RRF is rank-blind, this
    restores real relevance (src/rag/rerank.py).

    Judges each (question, transcript) pair, squashes the score to 0-1, and blends
    it with the moment's NORMALIZED RRF standing (RERANK_WEIGHT): truly relevant
    text moments rise, spurious ones sink. Frame-only moments carry no transcript,
    so they keep their RRF standing untouched — visual results aren't disturbed.
    Each judged/kept window gets a `blend` score (0-1, what the list is sorted by);
    unreranked runs leave it unset and the citation falls back to the raw rrf.
    Best-effort: any reranker failure logs and returns the order unchanged."""
    if not config.ENABLE_RERANK or len(windows) < 2:
        return windows
    import math

    from . import rerank as reranker
    cand = [i for i, w in enumerate(windows) if (w.get("text") or {}).get("text")]
    cand = cand[:config.RERANK_TOP_K]
    if len(cand) < 2:
        return windows
    try:
        raw = reranker.score(question, [windows[i]["text"]["text"] for i in cand])
    except Exception as exc:
        print(f"[rerank] skipped ({type(exc).__name__}: {exc})")
        return windows

    max_rrf = max((w["rrf"] for w in windows), default=0.0) or 1.0
    wt = config.RERANK_WEIGHT
    lo, hi = config.CONFIDENCE_THRESHOLD, config.VISUAL_STRONG
    rel = {i: 1.0 / (1.0 + math.exp(-s)) for i, s in zip(cand, raw)}
    for i, w in enumerate(windows):
        rr = w["rrf"] / max_rrf
        if i in rel:
            w["rerank"] = round(rel[i], 4)
            w["blend"] = wt * rel[i] + (1.0 - wt) * rr
        else:
            # Frame-only / unjudged: weight its RRF standing by HOW STRONG the raw
            # visual match is. A talking-head frame that barely cleared the gate
            # must not outrank a clearly-relevant transcript moment; a real visual
            # answer (a slide/diagram) still can. conf: 0 at the gate → 1 at STRONG.
            vis = (w.get("frame") or {}).get("score", 0.0)
            conf = 1.0 if hi <= lo else max(0.0, min(1.0, (vis - lo) / (hi - lo)))
            w["blend"] = rr * conf
    windows.sort(key=lambda w: w["blend"], reverse=True)
    return windows


def _prune(windows: list[dict], best_visual: float) -> list[dict]:
    """Per-moment quality floor — the gate in ask() is per QUESTION (on the two
    branch bests) and lets a weak tail ride in behind one strong hit; this judges
    each moment on its own, after the rerank, before the trim to TOP_K.

    Frames: CLIP cosine isn't calibrated, so the rule is relative — a frame is
    "good" at >= FRAME_SCORE_RATIO of the best frame's score. Text: the
    cross-encoder's 0-1 relevance is calibrated, so an absolute TEXT_RERANK_FLOOR
    (only for moments the reranker actually judged; with the reranker off, text
    is never pruned). A moment stays if EITHER branch is good — a weak transcript
    under a strong frame, or the reverse, is still evidence. Dropped moments free
    their slots for the next-best, so this usually changes WHICH six show, not
    how many; fewer than TOP_K only when the library has nothing better.

    Both knobs default to 0 (off) — see config.py for the measurement: with the
    default CLIP + MiniLM stack the ratio never fires and the floor removes
    correct transcript moments. The machinery stays for other embedders/rerankers."""
    ratio, floor = config.FRAME_SCORE_RATIO, config.TEXT_RERANK_FLOOR
    if not ratio and not floor:
        return windows
    vis_floor = ratio * best_visual if ratio else 0.0

    def frame_ok(w: dict) -> bool:
        fr = w.get("frame")
        return bool(fr) and fr.get("score", 0.0) >= vis_floor

    def text_ok(w: dict) -> bool:
        if not w.get("text"):
            return False
        return not floor or "rerank" not in w or w["rerank"] >= floor

    return [w for w in windows if frame_ok(w) or text_ok(w)]


def _noop_stage(stage: str, detail: str = "") -> None:
    """Default progress sink. Callers that want to show what the pipeline is
    doing (the UI's streaming ask) pass their own on_stage; everyone else pays
    nothing. Stages are emitted at REAL boundaries — never on a timer — so the
    label on screen is always what the server is actually busy with."""


def retrieve(question: str, user_id: str, *, top_k: int | None = None,
             video_id: str | None = None,
             video_ids: list[str] | None = None,
             include_samples: bool = True,
             on_stage=_noop_stage) -> dict[str, Any]:
    """Multimodal retrieve: query BOTH branches (CLIP frames + transcript text),
    fuse by RRF into time windows, and return numbered moment-citations.

    Returns {citations, best_visual, best_text, candidates} — the two raw bests
    feed the confidence gate (RRF scores are too small to threshold on);
    `candidates` is how many fused moments existed before pruning/trimming, so
    an empty result can tell "nothing indexed" from "nothing good enough".
    video_ids scopes the search to chosen videos (UI select/unselect);
    include_samples keeps the shared sample corpus searchable from any workspace."""
    k = top_k or TOP_K

    # Visual branch — CLIP text→image.
    on_stage("embedding")
    qvec = embed_text(question)
    on_stage("searching")
    vhits = vector_store.search(qvec, user_id, top_k=BRANCH_TOP_K,
                                video_id=video_id, video_ids=video_ids,
                                include_samples=include_samples)
    best_visual = vhits[0]["score"] if vhits else 0.0

    # Text branch — bge query→transcript-chunk (only if transcript is enabled).
    thits: list[dict] = []
    best_text = 0.0
    if config.ENABLE_TRANSCRIPT:
        thits = vector_store.search_text(embed_query(question), user_id,
                                         top_k=BRANCH_TOP_K, video_id=video_id,
                                         video_ids=video_ids,
                                         include_samples=include_samples)
        best_text = thits[0]["score"] if thits else 0.0

    fused = _fuse(vhits, thits)
    on_stage("ranking", f"{len(fused)} candidate moments")
    windows = _prune(_rerank(question, fused), best_visual)[:k]
    videos = db.videos_by_ids(sorted({w["video_id"] for w in windows}))

    # A text-only moment has no matched frame; borrow the video's picture nearest
    # its timestamp so the card isn't an empty "(no frame)" box (uploads have no
    # YouTube thumbnail to fall back on). One frame-list scroll per video, cached.
    _frames: dict[tuple[str, str], list[tuple[int, int]]] = {}

    def _preview(owner: str, vid: str, ms: int) -> str | None:
        key = (owner, vid)
        if key not in _frames:
            _frames[key] = vector_store.frame_times(owner, vid)
        fts = _frames[key]
        if not fts:
            return None
        idx = min(fts, key=lambda t: abs(t[1] - ms))[0]
        return _thumb_url(owner, vid, idx)

    citations = []
    for i, w in enumerate(windows, 1):
        vid = w["video_id"]
        meta = videos.get(vid)
        fr, tx = w["frame"], w["text"]
        # Anchor on the frame's exact timestamp when there is one (precise visual
        # seek); otherwise the transcript chunk's start.
        ms = int(fr["ms"]) if fr else int(w["t"] * 1000)
        idx = int(fr["idx"]) if fr else None
        # Frames and uploads live under the OWNING tenant's key prefix, which
        # isn't the asker for a shared sample — take it from the hit's payload.
        owner = (fr or tx or {}).get("user_id") or user_id
        citations.append({
            "n": i,
            "video_id": vid,
            "owner": owner,
            "title": (meta or {}).get("title") or vid,
            "url": (meta or {}).get("url"),
            "source": (meta or {}).get("source"),
            "ms": ms,
            "timestamp": _seconds(ms),
            # The matched span in seconds (a transcript chunk's extent; a frame
            # is a point) — what CONTEXT_PAD_S pads around when the model reads.
            "t_start": round(float(tx.get("t_start", w["t"])) if tx else ms / 1000.0, 2),
            "t_end": round(float(tx.get("t_end", tx.get("t_start", w["t"])))
                           if tx else ms / 1000.0, 2),
            "idx": idx,
            "thumbnail": _thumb_url(owner, vid, idx) if idx is not None else None,
            # Non-matched still shown for a text-only moment (kept separate from
            # `thumbnail` so the UI still labels it "Matched on transcript").
            "preview": None if idx is not None else _preview(owner, vid, ms),
            "media_url": _media_url(meta, owner, vid),
            "deeplink": _deeplink(meta, vid, ms),
            "score": round(w.get("blend", w["rrf"]), 4),
            "match": _match_pct(w),   # 0-100 real match strength (what the UI shows)
            "transcript": (tx or {}).get("text"),
            "speaker": (tx or {}).get("speaker"),   # who said it (diarized), if any
            "modalities": sorted(w["modalities"]),
        })
    return {"citations": citations, "best_visual": best_visual,
            "best_text": best_text, "candidates": len(fused)}


def _fallback_answer(citations: list[dict[str, Any]]) -> str:
    """No-LLM summary: rank the visually-closest moments. Honest about being
    similarity, not synthesis."""
    top = citations[0]
    where = f"{top['title']} at {top['timestamp']}" if top.get("title") else top["timestamp"]
    others = ", ".join(f"{c['timestamp']} [{c['n']}]" for c in citations[1:4])
    msg = f"Closest visual match: {where} [{top['n']}] (similarity {top['score']})."
    if others:
        msg += f" Other relevant moments: {others}."
    return msg


_CITE_RE = re.compile(r"\[(\d+(?:\s*,\s*\d+)*)\]")


def _validate_citations(answer: str, n_frames: int) -> str:
    """Strip invented [n] references the model has no frame for."""
    def fix(m: re.Match) -> str:
        nums = [int(x) for x in re.split(r"\s*,\s*", m.group(1))]
        valid = [str(x) for x in nums if 1 <= x <= n_frames]
        return f"[{', '.join(valid)}]" if valid else ""
    return _CITE_RE.sub(fix, answer)


def _transcript_chunks(owner: str, video_id: str) -> list[dict]:
    """The video's stored timed transcript (`[{text,t_start,t_end,speaker?}]`,
    written at ingest) — one GET, best-effort: a video with no transcript, or a
    copy that predates transcript-to-storage, simply yields no context."""
    try:
        chunks = json.loads(storage.get_bytes(storage.transcript_key(owner, video_id)))
        return chunks if isinstance(chunks, list) else []
    except Exception:
        return []


def _context(chunks: list[dict], c: dict, pad: float) -> list[tuple[str, str]]:
    """What was said around a moment, as (where, text) pairs for the prompt.

    A matched transcript chunk already spans ~TRANSCRIPT_CHUNK_SECONDS, so the
    pad reaches from the chunk's EDGES (t_start - pad, t_end + pad): padding only
    around the anchor time would fetch the chunk before and miss the one after,
    where an answer usually continues. A frame-only moment has no span, so it is
    simply the speech within `pad` seconds either side of the frame — including
    the chunk playing while it was on screen, which today's frame-only moment
    never showed the model at all. The matched chunk itself is left out (it is
    already the moment's transcript)."""
    t0 = float(c.get("t_start", c["ms"] / 1000.0))
    t1 = float(c.get("t_end", t0))
    lo, hi = t0 - pad, t1 + pad
    matched = c.get("transcript")
    before: list[str] = []
    during: list[str] = []
    after: list[str] = []
    for ch in chunks:
        cs = float(ch.get("t_start", 0.0))
        ce = float(ch.get("t_end", cs))
        if ce <= lo or cs >= hi:
            continue
        text = (ch.get("text") or "").strip()
        if not text or text == matched:
            continue
        if ch.get("speaker"):
            text = f"{ch['speaker']}: {text}"
        (before if ce <= t0 else after if cs >= t1 else during).append(text)
    out: list[tuple[str, str]] = []
    if before:
        out.append(("said just before", " ".join(before)))
    if during:
        out.append(("said while this frame was on screen", " ".join(during)))
    if after:
        out.append(("said just after", " ".join(after)))
    return out


def _build_moments(user_id: str, citations: list[dict[str, Any]]) -> list[dict]:
    """Turn citations into what the LLM sees: each moment carries its frame
    image (if any), its transcript excerpt (if any) and — CONTEXT_PAD_S — the
    speech around it, numbered to match.

    Everything remote goes through ONE thread pool: a frame GET per moment plus
    a transcript GET per distinct video, all at once, so the context adds no
    round-trip to the clock."""
    pad = config.CONTEXT_PAD_S

    def owner_of(c: dict) -> str:
        # c["owner"], not the asker: a shared sample's frames and transcript sit
        # under the tenant that ingested it.
        return c.get("owner") or user_id

    def frame_bytes(c: dict):
        if c.get("idx") is None:
            return None
        try:
            return storage.get_bytes(storage.frame_key(owner_of(c), c["video_id"], c["idx"]))
        except Exception:
            return None

    videos = sorted({(owner_of(c), c["video_id"]) for c in citations}) if pad > 0 else []
    with ThreadPoolExecutor(max_workers=max(1, min(16, len(citations) + len(videos)))) as ex:
        f_images = [ex.submit(frame_bytes, c) for c in citations]
        f_chunks = {key: ex.submit(_transcript_chunks, *key) for key in videos}
        images = [f.result() for f in f_images]
        chunks = {key: f.result() for key, f in f_chunks.items()}

    moments = []
    for img, c in zip(images, citations):
        m: dict[str, Any] = {"image": img, "transcript": c.get("transcript"),
                             "speaker": c.get("speaker"), "timestamp": c["timestamp"]}
        if c.get("parts"):                  # multi-part: which sub-question found it
            m["parts"] = c["parts"]
        if pad > 0:
            ctx = _context(chunks.get((owner_of(c), c["video_id"]), []), c, pad)
            if ctx:
                m["context"] = ctx
        moments.append(m)
    return moments


def resolve_llm(user_id: str) -> tuple[llm.LLMConfig | None, str]:
    """Which model answers for this tenant: their own hosted endpoint
    (ms_user_llms — e.g. a vLLM server) first, the server-wide LLM_* env
    config as fallback. Returns (config, source) with source in
    {"user", "server", "none"}."""
    row = db.get_user_llm(user_id)
    if row and row.get("model"):
        return llm.from_row(row), "user"
    cfg = llm.env_config()
    return (cfg, "server") if cfg else (None, "none")


def _gate_ok(r: dict[str, Any]) -> bool:
    """Gate 1 — confidence on the RAW per-branch bests (not the RRF score).
    Passes when EITHER what's on screen or what's said looks relevant; abstain
    only when neither does. Off when CONFIDENCE_THRESHOLD is 0."""
    if not CONFIDENCE_THRESHOLD:
        return True
    return (r["best_visual"] >= CONFIDENCE_THRESHOLD
            or r["best_text"] >= TEXT_CONFIDENCE_THRESHOLD)


def _merge_parts(results: list[dict[str, Any]], found: list[int]) -> list[dict]:
    """One citation list out of per-part retrievals: grouped by part, deduped by
    (video, FUSION_WINDOW_S) so a moment two parts both found appears once and
    is tagged with both, then renumbered 1..N so the [n] the model cites is the
    card the user sees."""
    win_ms = FUSION_WINDOW_S * 1000
    out: list[dict] = []
    for pi in found:
        for c in results[pi]["citations"]:
            dup = next((o for o in out if o["video_id"] == c["video_id"]
                        and abs(o["ms"] - c["ms"]) <= win_ms), None)
            if dup is not None:
                if pi + 1 not in dup["parts"]:
                    dup["parts"].append(pi + 1)
                continue
            out.append({**c, "parts": [pi + 1]})
    for i, c in enumerate(out, 1):
        c["n"] = i
    return out


def _answer(result: dict[str, Any], user_id: str, cfg: llm.LLMConfig | None,
            source: str, on_stage, opts: dict | None = None) -> dict[str, Any]:
    """The shared tail of ask(): the retrieved moments passed the gate — read
    them, call the model, validate what it cited."""
    citations = result["citations"]
    if cfg is None:
        # No generative model — summarize the best matches instead of inventing.
        result.update(answer=_fallback_answer(citations), llm_used=False,
                      note=("Retrieval-only results. Connect your own model "
                            "(vLLM/Ollama/API) in settings, or set LLM_API_KEY "
                            "on the server, for a synthesized, grounded answer."))
        return result

    # Pulling the matched frames (and the transcript around each moment) out of
    # object storage is its own wait, so it gets its own stage rather than hiding
    # inside "answering".
    on_stage("reading", f"{len(citations)} moment{'' if len(citations) == 1 else 's'}")
    moments = _build_moments(user_id, citations)
    # A local runtime (Ollama & co) serves a 4096-token window by default, which a
    # full six-frame request overruns — trim to fit rather than 500. No-op for
    # hosted providers. The note tells the user what was left out and how to lift
    # the cap; citations still list everything retrieved, so nothing is hidden.
    moments, trim_note = llm.fit_local_context(cfg, moments)
    if trim_note:
        result["note"] = trim_note
    frames = sum(1 for m in moments if m.get("image"))
    on_stage("answering", f"{cfg.model} reading {frames} frame{'' if frames == 1 else 's'}")
    # Bound the citation validator by what the model was actually SHOWN: after a
    # trim it only knows moments 1..len(moments), so a [5] from a 3-moment prompt
    # is an invention and gets stripped.
    answer = _validate_citations(
        llm.answer(result["question"], moments, cfg, opts=opts), len(moments))
    result["answer"] = answer
    result["llm_used"] = True
    result["llm_source"] = source          # "user" = their own hosted model
    result["llm_model"] = cfg.model
    # A refusal ("couldn't find it in your videos") cites no moment — a real
    # answer always cites [n]. When nothing is cited, hide the cards so the reply
    # doesn't sit above a grid of moments that look like results. Use _CITE_RE
    # (not a bare \[\d+\]) so a COMBINED cite like [2, 6] still counts.
    if not _CITE_RE.search(answer):
        result["citations"] = []
        result["abstained"] = True
    return result


def _ask_multi(question: str, parts: list[str], user_id: str,
               cfg: llm.LLMConfig, source: str, *, top_k: int | None,
               on_stage, **scope) -> dict[str, Any]:
    """A multi-part question: retrieve every part IN PARALLEL, gate each on its
    own, merge, and answer the ORIGINAL question once from all the moments.

    Per-part k is MULTI_QUERY_TOTAL_K shared out (6+6, or 4+4+4), so the model
    reads about as many moments as one deep single retrieval. The sub-retrievals
    report no stages (they would interleave); one stage names the parts, which
    is also what the UI shows the user as "searched as"."""
    n = len(parts)
    per_k = top_k or max(3, config.MULTI_QUERY_TOTAL_K // n)
    on_stage("parts", f"{n} parts · " + " · ".join(parts))
    with ThreadPoolExecutor(max_workers=n) as ex:
        results = list(ex.map(
            lambda p: retrieve(p, user_id, top_k=per_k, on_stage=_noop_stage, **scope),
            parts))
    found = [i for i, r in enumerate(results) if r["citations"] and _gate_ok(r)]
    missing = [i for i in range(n) if i not in found]
    result: dict[str, Any] = {"question": question, "parts": parts}
    if not found:
        result.update(answer=ABSTAIN, citations=[], llm_used=False, abstained=True)
        return result
    result["citations"] = _merge_parts(results, found)
    return _answer(result, user_id, cfg, source, on_stage,
                   opts={"parts": parts, "missing": missing})


def ask(question: str, user_id: str, *, top_k: int | None = None,
        video_id: str | None = None,
        video_ids: list[str] | None = None,
        on_stage=_noop_stage) -> dict[str, Any]:
    scope = {"video_id": video_id, "video_ids": video_ids}

    # Retrieval starts FIRST, on a worker; everything else this path needs up
    # front overlaps it instead of preceding it. The model lookup is a DB
    # round-trip (~0.5s to a hosted Postgres) and the multi-part check is an LLM
    # round-trip (~1-2s) — both used to sit on the clock, now both are hidden
    # behind the ~2s retrieval. A single-part question (most of them) pays
    # nothing extra; a multi-part one only wastes the plain retrieval it would
    # have thrown away anyway.
    parts = [question]
    with ThreadPoolExecutor(max_workers=1) as ex:
        f_ret = ex.submit(retrieve, question, user_id, top_k=top_k,
                          on_stage=on_stage, **scope)
        cfg, source = resolve_llm(user_id)
        f_split = (ex.submit(query_split.split, question, cfg)
                   if config.MULTI_QUERY and cfg is not None else None)
        r = f_ret.result()
        if f_split is not None:
            if not f_split.done():
                on_stage("splitting", "checking whether the question has several parts")
            parts = f_split.result()
    if len(parts) > 1:
        return _ask_multi(question, parts, user_id, cfg, source,
                          top_k=top_k, on_stage=on_stage, **scope)

    citations = r["citations"]
    result: dict[str, Any] = {"question": question, "citations": citations}
    if not citations:
        # An empty library and "every candidate was too weak to show" are
        # different situations and get different replies.
        result.update(answer=(ABSTAIN if r.get("candidates") else
                              "I couldn't find anything relevant in your videos. "
                              "Try adding a video first."),
                      llm_used=False, abstained=True)
        return result
    if not _gate_ok(r):
        # Hide the moment cards too — a "couldn't find it" reply sitting above a
        # grid of moments reads as a contradiction (they look like results).
        result.update(answer=ABSTAIN, citations=[], llm_used=False, abstained=True)
        return result
    return _answer(result, user_id, cfg, source, on_stage)
