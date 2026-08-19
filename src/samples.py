"""The curated sample corpus — "A Deep Dive into LLMs" (the read-only / page).

One short, visually-rich LLM explainer (kept small so a fresh clone seeds in
under a minute). The worker auto-ingests it if it isn't indexed when it boots
(SEED_SAMPLE_VIDEOS=true, the default), so the demo is queryable the moment the
stack comes up; examples/quickstart.py uses the same list for the in-process
route. Add more entries here to grow the demo corpus.
"""
from __future__ import annotations

import re

SAMPLE_VIDEOS = [
    {
        "url": "https://youtu.be/LPZh9BOjkQs",
        "title": "3Blue1Brown — LLMs explained briefly (8m)",
        # WHO MADE IT. Ingest replaces `title` with YouTube's own ("Large
        # Language Models explained briefly") and yt-dlp's uploader field is not
        # persisted anywhere, so without this the creator's name is simply lost.
        # The demo runs on someone else's work; it owes them the credit and a
        # link, in their own name, not just a video id.
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


def sample_attribution(video_id: str) -> dict:
    """Creator credit for a sample: {"author", "author_url"}, blank for anything
    else. Videos a user adds have no author because nothing captures the uploader
    at ingest — a curated corpus is the one place we know it by hand."""
    for v in SAMPLE_VIDEOS:
        if sample_video_id(v["url"]) == video_id:
            return {"author": v.get("author"), "author_url": v.get("author_url")}
    return {"author": None, "author_url": None}
