# MomentSearch

**Ask questions about your videos and get answers grounded in the exact moments — by what's _seen_ on screen.**

🌐 **Landing page:** [momentsearch.vercel.app](https://momentsearch.vercel.app/)

MomentSearch is a small, open-source, full-stack starter for **visual** video search and RAG.
Drop in a YouTube URL or upload a file; it samples frames, embeds them with CLIP, and stores
them in [Qdrant](https://qdrant.tech). Ask a question and it retrieves the most relevant
moments and (optionally) has **your own LLM** read those frames to write a cited answer.

> **Visual, not audio.** MomentSearch understands the *picture* — it never transcribes speech.
> That means it works on silent footage, screen recordings, sports, surveillance, b-roll, slides,
> demos, and anything where what you're looking for is something you can *see*.

- 🎥 **Add videos** — paste a YouTube URL or upload a file
- 🔍 **Visual retrieval** — CLIP embeddings, runs locally, no API key needed to search
- 💬 **Cited answers** — bring your own vision LLM (OpenAI-compatible or Anthropic)
- 🧱 **Qdrant** vector backend
- 🪶 **Minimal** — one FastAPI service + a single-file UI, no build step
- 🔓 **Apache 2.0**

---

## Quickstart (Docker)

```bash
git clone https://github.com/traversaal-ai/momentsearch.git
cd momentsearch
cp .env.example .env          # add your LLM key (optional — search works without it)
docker compose up --build
```

Open **http://localhost:8000**. Paste a YouTube URL, wait for ingestion, then ask away.

## Quickstart (local, no Docker)

You need **Python 3.11+** and **FFmpeg** installed.

```bash
# 1. Start Qdrant
docker run -p 6333:6333 qdrant/qdrant

# 2. Backend
python -m venv .venv && source .venv/bin/activate
pip install -r backend/requirements.txt
cp .env.example .env          # edit as needed
uvicorn backend.app.main:app --reload --port 8000
```

Open **http://localhost:8000**.

---

## How it works

```
                 ┌──────────────┐   frames    ┌──────────┐   vectors   ┌──────────┐
  YouTube / file │  yt-dlp /    │ ──────────► │  FFmpeg  │ ──────────► │   CLIP   │
                 │  upload      │  (sampled)  │ sampling │  (PIL imgs) │  encoder │
                 └──────────────┘             └──────────┘             └────┬─────┘
                                                                            │ upsert
                                                                            ▼
   question ──► CLIP text encoder ──► Qdrant kNN ──► top-k frames ──►  ┌──────────┐
                                                          │           │  Qdrant  │
                                                          ▼           └──────────┘
                                            your vision LLM reads the frames
                                                          │
                                                          ▼
                                          cited answer + clickable moments
```

1. **Ingest** — the video is fetched (yt-dlp) or uploaded, then FFmpeg samples frames
   (every *N* seconds, or on scene cuts).
2. **Embed** — each frame is encoded with CLIP. The *same* model encodes text queries into
   the *same* vector space, so a natural-language question matches what's visible.
3. **Index** — frame vectors + `{video_id, timestamp, thumbnail}` go into Qdrant.
4. **Ask** — the question is embedded, Qdrant returns the closest frames, and your LLM is shown
   those frames to write an answer that cites each one as `[n]`. Citations link back to the
   exact moment (YouTube `?t=` deep link, or the seeked local clip).

Retrieval is fully local. The LLM is only used for the final written answer — skip it and you
still get ranked, thumbnailed, timestamped moments.

## Bring your own LLM

MomentSearch never hardcodes a model. Set these in `.env`:

| Provider | `LLM_PROVIDER` | `LLM_BASE_URL` | `LLM_MODEL` (must be vision-capable) |
|---|---|---|---|
| OpenAI | `openai` | *(blank)* | `gpt-4o-mini`, `gpt-4o` |
| Ollama (local) | `openai` | `http://localhost:11434/v1` | `llava`, `llama3.2-vision`, `qwen2.5-vl` |
| vLLM / LM Studio | `openai` | `http://localhost:8000/v1` | *(your served model)* |
| Together / Groq / OpenRouter | `openai` | their `/v1` base URL | *(a vision model)* |
| Anthropic | `anthropic` | *(blank)* | `claude-sonnet-4-6`, `claude-opus-4-8` |

Anything that speaks the OpenAI Chat Completions API works via `LLM_BASE_URL`.

## Configuration

All via `.env` (see [`.env.example`](.env.example)):

| Variable | Default | Notes |
|---|---|---|
| `QDRANT_URL` | `http://localhost:6333` | Qdrant endpoint |
| `QDRANT_COLLECTION` | `moments` | collection name |
| `LLM_PROVIDER` / `LLM_API_KEY` / `LLM_BASE_URL` / `LLM_MODEL` | — | answer synthesis (optional) |
| `FRAME_STRATEGY` | `interval` | `interval` or `scene` |
| `FRAME_INTERVAL_SEC` | `2` | seconds between sampled frames |
| `SCENE_THRESHOLD` | `0.4` | scene-cut sensitivity (`scene` mode) |
| `MAX_FRAMES` | `400` | per-video cap (0 = unlimited) |
| `CLIP_MODEL` | `clip-ViT-B-32` | any sentence-transformers CLIP model |
| `TOP_K` | `6` | frames retrieved per question |

## API

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/api/config` | feature flags for the UI |
| `GET` | `/api/videos` | list indexed videos |
| `DELETE` | `/api/videos/{id}` | remove a video |
| `GET` | `/api/ingest/youtube?url=` | ingest a URL (SSE progress) |
| `POST` | `/api/upload` | upload a file |
| `GET` | `/api/ingest/upload?video_id=&title=` | ingest an upload (SSE progress) |
| `POST` | `/api/ask` | `{question, video_id?}` → `{answer, citations}` |
| `GET` | `/api/frame/{path}` | frame thumbnail |
| `GET` | `/api/video/{id}` | source video (range requests) |

## Project layout

```
momentsearch/
├── backend/
│   ├── app/
│   │   ├── main.py          # FastAPI routes + static UI
│   │   ├── config.py        # env-driven settings
│   │   ├── embeddings.py    # CLIP (frames + text → shared space)
│   │   ├── vector_store.py  # Qdrant
│   │   ├── ingest.py        # yt-dlp / upload → FFmpeg frames → embed → upsert
│   │   ├── llm.py           # bring-your-own vision LLM
│   │   └── search.py        # retrieve + synthesize cited answer
│   ├── requirements.txt
│   └── Dockerfile
├── frontend/
│   └── index.html           # single-file UI (no build step)
├── docker-compose.yml
└── .env.example
```

## Roadmap ideas

- Multiple CLIP backbones / open multimodal embedding models
- Hybrid scoring across nearby frames (temporal smoothing)
- Streaming answers
- Auth + multi-user libraries

Contributions welcome.

## License

[Apache 2.0](LICENSE) © Traversaal.ai
