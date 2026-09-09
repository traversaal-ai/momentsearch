"""The curated sample corpus — 3Blue1Brown's "Neural networks" playlist.

Ten videos, one creator, one continuous topic (a perceptron in chapter 1 through
attention and cross-entropy at the end), so questions can be answered ACROSS
videos rather than inside one — which is the thing this app is for.

These are not indexed at startup. They were indexed once, offline, by
src/build_demo_corpus.py, and the result — vectors, frames, transcripts and
manifest rows — is shipped in demo_corpus/ and restored at boot by
src/demo_restore.py (the startup gate). This list stays the
source of truth for WHICH videos the demo holds: the demo session links exactly
these ids (src/api/sessions.py), and the restore gate verifies exactly these ids
are present in all three stores.

Adding an entry here therefore means rebuilding the corpus bundle. On a stack
that is NOT in demo mode, SEED_SAMPLE_VIDEOS=true still makes src/seeding.py
ingest anything in this list that is missing — a 45-minute job for all ten.
"""
from __future__ import annotations

import re

SAMPLE_VIDEOS = [
    {
        "url": "https://youtu.be/aircAruvnKk",
        "title": "3Blue1Brown — But what is a neural network? (18m)",
        # WHO MADE IT. Ingest replaces `title` with YouTube's own and
        # yt-dlp's uploader field is not persisted anywhere, so without this
        # the creator's name is simply lost. The demo runs on someone else's
        # work; it owes them the credit and a link, in their own name.
        "author": "3Blue1Brown",
        "author_url": "https://www.youtube.com/@3blue1brown",
    },
    {
        "url": "https://youtu.be/IHZwWFHWa-w",
        "title": "3Blue1Brown — Gradient descent, how neural networks learn (21m)",
        "author": "3Blue1Brown",
        "author_url": "https://www.youtube.com/@3blue1brown",
    },
    {
        "url": "https://youtu.be/Ilg3gGewQ5U",
        "title": "3Blue1Brown — Backpropagation, intuitively (13m)",
        "author": "3Blue1Brown",
        "author_url": "https://www.youtube.com/@3blue1brown",
    },
    {
        "url": "https://youtu.be/tIeHLnjs5U8",
        "title": "3Blue1Brown — Backpropagation calculus (10m)",
        "author": "3Blue1Brown",
        "author_url": "https://www.youtube.com/@3blue1brown",
    },
    {
        "url": "https://youtu.be/LPZh9BOjkQs",
        "title": "3Blue1Brown — LLMs explained briefly (8m)",
        "author": "3Blue1Brown",
        "author_url": "https://www.youtube.com/@3blue1brown",
    },
    {
        "url": "https://youtu.be/wjZofJX0v4M",
        "title": "3Blue1Brown — Transformers, the tech behind LLMs (27m)",
        "author": "3Blue1Brown",
        "author_url": "https://www.youtube.com/@3blue1brown",
    },
    {
        "url": "https://youtu.be/eMlx5fFNoYc",
        "title": "3Blue1Brown — Attention in transformers, step-by-step (26m)",
        "author": "3Blue1Brown",
        "author_url": "https://www.youtube.com/@3blue1brown",
    },
    {
        "url": "https://youtu.be/9-Jl0dxWQs8",
        "title": "3Blue1Brown — How might LLMs store facts (23m)",
        "author": "3Blue1Brown",
        "author_url": "https://www.youtube.com/@3blue1brown",
    },
    {
        "url": "https://youtu.be/GlYgs6v2YfU",
        "title": "3Blue1Brown — But what is cross-entropy? (34m)",
        "author": "3Blue1Brown",
        "author_url": "https://www.youtube.com/@3blue1brown",
    },
    {
        "url": "https://youtu.be/iv-5mZ_9CPY",
        "title": "3Blue1Brown — How do AI images and videos work? (37m, guest: Welch Labs)",
        # The one /demo names in its headline. Without a pick the hero took
        # whichever row happened to sort first, which changes with ingest order —
        # the front door shouldn't be decided by a timestamp.
        "featured": True,
        "author": "3Blue1Brown",
        "author_url": "https://www.youtube.com/@3blue1brown",
    },
]


def sample_video_id(url: str) -> str:
    m = re.search(r"(?:youtu\.be/|v=)([\w-]{11})", url)
    return f"yt_{m.group(1)}" if m else url


# The sample ids — protected: they can be unselected from a query but never
# deleted (the seed gate would just re-add them anyway).
SAMPLE_IDS = frozenset(sample_video_id(v["url"]) for v in SAMPLE_VIDEOS)


def is_sample(video_id: str) -> bool:
    return video_id in SAMPLE_IDS


# The sample /demo leads with — the corpus is ten videos, but the hero can only
# name one. Falls back to the first entry if nothing is flagged.
FEATURED_ID = next((sample_video_id(v["url"]) for v in SAMPLE_VIDEOS if v.get("featured")),
                   sample_video_id(SAMPLE_VIDEOS[0]["url"]) if SAMPLE_VIDEOS else None)


def is_featured(video_id: str) -> bool:
    return video_id == FEATURED_ID


def sample_attribution(video_id: str) -> dict:
    """Creator credit for a sample: {"author", "author_url"}, blank for anything
    else. Videos a user adds have no author because nothing captures the uploader
    at ingest — a curated corpus is the one place we know it by hand."""
    for v in SAMPLE_VIDEOS:
        if sample_video_id(v["url"]) == video_id:
            return {"author": v.get("author"), "author_url": v.get("author_url")}
    return {"author": None, "author_url": None}
