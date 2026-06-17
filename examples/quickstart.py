#!/usr/bin/env python3
"""MomentSearch quickstart — a tiny, end-to-end starter.

Ingests four LLM talks/explainers (slides, diagrams, animations) and then asks
*visual* questions against them — e.g. "a diagram of the attention mechanism".
MomentSearch matches your question against what is **seen** on screen, so the
answer points you at the exact frame/timestamp.

Prerequisites
-------------
1. Qdrant running:   docker run -p 6333:6333 qdrant/qdrant
2. FFmpeg installed:  ffmpeg -version
3. Deps installed:    pip install -r backend/requirements.txt

Usage
-----
    python examples/quickstart.py                     # ingest the 4 videos, run sample queries
    python examples/quickstart.py --skip-ingest       # just run the sample queries
    python examples/quickstart.py --ask "a tokenizer splitting text into tokens"

Tip: set LLM_API_KEY in your .env to get a synthesized, frame-grounded answer.
Without it you still get the ranked, timestamped moments (retrieval is local).
"""
from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path

# Make the repo importable when run as `python examples/quickstart.py`.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# Demo-friendly defaults (only applied if you haven't set them in .env).
# Wider frame spacing + a frame cap keeps a multi-video ingest fast.
os.environ.setdefault("FRAME_INTERVAL_SEC", "4")
os.environ.setdefault("MAX_FRAMES", "40")

from backend.app import ingest, search, vector_store  # noqa: E402

# Four visually-rich LLM talks. Each ships a couple of questions that the
# *picture* can actually answer.
VIDEOS = [
    {
        "url": "https://youtu.be/LPZh9BOjkQs",
        "label": "3Blue1Brown — LLMs explained briefly (8m)",
    },
    {
        "url": "https://youtu.be/wjZofJX0v4M",
        "label": "3Blue1Brown — Transformers, the tech behind LLMs (27m)",
    },
    {
        "url": "https://youtu.be/eMlx5fFNoYc",
        "label": "3Blue1Brown — Attention in transformers, step-by-step (26m)",
    },
    {
        "url": "https://youtu.be/zjkBMFhNj_g",
        "label": "Andrej Karpathy — [1hr Talk] Intro to Large Language Models (60m)",
    },
]

SAMPLE_QUERIES = [
    "a diagram of the attention mechanism",
    "an animation of a neural network with weighted connections",
    "a slide listing examples of large language models",
    "text being split into tokens",
    "a person speaking on stage next to slides",
]


def _video_id(url: str) -> str:
    m = re.search(r"(?:youtu\.be/|v=)([\w-]{11})", url)
    return f"yt_{m.group(1)}" if m else url


def ingest_all() -> None:
    indexed = {v["video_id"] for v in vector_store.list_videos()}
    for v in VIDEOS:
        vid = _video_id(v["url"])
        if vid in indexed:
            print(f"  ✓ already indexed: {v['label']}")
            continue
        print(f"\n→ Ingesting {v['label']}")
        ingest.ingest_youtube(
            v["url"],
            progress=lambda stage, d: print(f"    [{stage}] {d.get('message', '')}"),
        )


def ask(question: str) -> None:
    print(f"\n❓ {question}")
    result = search.ask(question, top_k=4)
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
