# Examples

## `quickstart.py` — ingest four LLM talks and ask visual questions

A tiny end-to-end starter. It ingests four visually-rich LLM talks/explainers and
then queries them by **what's on screen** — diagrams, animations, slides.

The demo corpus:

| Video | Length | Why it's here |
|---|---|---|
| 3Blue1Brown — *LLMs explained briefly* | 8m | clean animated explainers |
| 3Blue1Brown — *Transformers, the tech behind LLMs* | 27m | network/embedding diagrams |
| 3Blue1Brown — *Attention in transformers, step-by-step* | 26m | attention-matrix visuals |
| Andrej Karpathy — *[1hr Talk] Intro to LLMs* | 60m | slide-based talk on stage |

> **Note:** if you run the app with `docker compose up`, the worker
> **auto-ingests these four talks on first boot** (`SEED_SAMPLE_VIDEOS=true`)
> — no manual step needed. This script is the manual, in-process route, plus
> a terminal demo of the sample queries.

### Run it

```bash
# 1. Qdrant + FFmpeg + deps + .env (needs DATABASE_URL; see .env.example)
docker run -p 6333:6333 qdrant/qdrant
pip install -r requirements.txt

# 2. Ingest the four videos and run the sample queries
python examples/quickstart.py
```

Then try your own:

```bash
python examples/quickstart.py --skip-ingest --ask "a diagram of the attention mechanism"
python examples/quickstart.py --skip-ingest --ask "a slide listing examples of large language models"
```

Each result is a moment — video title, timestamp, similarity score, and a deep
link that jumps straight to that point on YouTube.

> Retrieval is local by default and needs no key — CLIP for visuals, bge for YouTube
> transcripts. Set `LLM_API_KEY` in your `.env` to also get a synthesized, grounded
> answer from your own LLM.

### A note on content

MomentSearch reads the **picture** for every video, and for **YouTube** also the
**transcript** (captions), fusing the two. Talks with slides, diagrams, demos, and
code (like these) are an ideal fit for the visual side. For pure talking-head
podcasts the visual signal is thin — but on YouTube the transcript branch still
surfaces what was *said*. Uploaded (non-YouTube) files are transcribed via Whisper
(ASR) when they're captionless, so they get the same transcript branch.
