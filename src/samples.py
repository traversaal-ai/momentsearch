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
