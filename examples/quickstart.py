#!/usr/bin/env python3
"""MomentSearch quickstart — a tiny, end-to-end starter.

Ingests one LLM talk/explainer (slides, diagrams, animations) and then asks
*visual* questions against them — e.g. "a diagram of the attention mechanism".
MomentSearch matches your question against what is **seen** on screen, so the
answer points you at the exact frame/timestamp.

This script runs the ingest flow IN-PROCESS (no worker needed): with
PREFECT_API_URL unset, Prefect executes the flow locally; with it set, the
runs also show up in your Prefect Cloud dashboard.

Prerequisites
-------------
1. DATABASE_URL set in .env (Neon Postgres — free tier)
2. Qdrant running:    docker run -p 6333:6333 qdrant/qdrant
                      (and QDRANT_URL=http://localhost:6333 in .env)
3. FFmpeg installed:  ffmpeg -version
4. Deps installed:    pip install -r requirements.txt

Usage
-----
    python examples/quickstart.py                     # ingest the video, run sample queries
    python examples/quickstart.py --skip-ingest       # just run the sample queries
    python examples/quickstart.py --ask "a tokenizer splitting text into tokens"

Tip: set LLM_API_KEY in your .env to get a synthesized, frame-grounded answer.
Without it you still get the ranked, timestamped moments (retrieval is local).
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

# Make the repo importable when run as `python examples/quickstart.py`.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# Demo-friendly defaults (only applied if you haven't set them in .env).
# Wider frame spacing + a frame cap keeps a multi-video ingest fast.
os.environ.setdefault("FRAME_INTERVAL_SEC", "4")
os.environ.setdefault("MAX_FRAMES", "40")

from src import db  # noqa: E402
from src.config import DEFAULT_USER_ID  # noqa: E402
from src.ingest.pipeline import ingest_video  # noqa: E402
from src.rag import search as rag_search  # noqa: E402
from src.samples import SAMPLE_VIDEOS, sample_video_id  # noqa: E402

USER = DEFAULT_USER_ID

# The same talk the worker auto-seeds on boot (src/samples.py).
VIDEOS = [{"url": v["url"], "label": v["title"]} for v in SAMPLE_VIDEOS]

SAMPLE_QUERIES = [
    "a diagram of the attention mechanism",
    "an animation of a neural network with weighted connections",
    "a slide listing examples of large language models",
    "text being split into tokens",
    "a person speaking on stage next to slides",
]


def ingest_all() -> None:
    indexed = {v["id"] for v in db.list_videos(USER, status="indexed")}
    for v in VIDEOS:
        vid = sample_video_id(v["url"])
        if vid in indexed:
            print(f"  ✓ already indexed: {v['label']}")
            continue
        print(f"\n→ Ingesting {v['label']}")
        db.upsert_pending({"id": vid, "user_id": USER, "source": "youtube",
                           "url": v["url"], "storage_key": None,
                           "source_hash": vid, "title": v["label"]})
        ingest_video(video_id=vid, user_id=USER)


def ask(question: str) -> None:
    print(f"\n❓ {question}")
    result = rag_search.ask(question, USER, top_k=4)
    if result.get("answer"):
        print(f"   💬 {result['answer']}")
    for c in result["citations"]:
        title = (c.get("title") or c["video_id"])[:48]
        print(f"   [{c['n']}] {c['timestamp']}  score={c['score']}  {title}")
        print(f"        ↳ {c['deeplink']}")


def main() -> None:
    ap = argparse.ArgumentParser(description="MomentSearch quickstart")
    ap.add_argument("--skip-ingest", action="store_true", help="don't (re)ingest the demo videos")
    ap.add_argument("--ask", metavar="QUESTION", help="ask a single question and exit")
    args = ap.parse_args()

    db.init_schema()

    if args.ask:
        ask(args.ask)
        return

    if not args.skip_ingest:
        print("Ingesting demo videos (downloads ≤480p video, samples frames, embeds with CLIP)…")
        ingest_all()

    print("\n" + "=" * 60)
    print("Sample queries (matched against what's visible on screen)")
    print("=" * 60)
    for q in SAMPLE_QUERIES:
        ask(q)


if __name__ == "__main__":
    main()
