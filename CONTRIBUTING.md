# Contributing to MomentSearch

Thanks for your interest! MomentSearch is meant to be a small, readable starter —
contributions that keep it minimal and easy to get started with are especially welcome.

## Ways to help

- 🐛 Report bugs (use the bug template)
- 💡 Propose features (use the feature template) — especially new embedding backbones,
  retrieval improvements, or LLM providers
- 📝 Improve docs / examples
- 🔌 Add support for another OpenAI-compatible LLM provider or vector store

## Development setup

You need **Python 3.11+** and **FFmpeg**.

```bash
git clone https://github.com/traversaal-ai/momentsearch.git
cd momentsearch

# Easiest: the full stack (qdrant + clip service + api + worker) in one command
cp .env.example .env            # fill in DATABASE_URL + PREFECT_API_URL/KEY
docker compose up --build

# Or bare processes (API + worker; add the clip service if you want warm embeds)
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
docker run -p 6333:6333 qdrant/qdrant
uvicorn src.app:app --reload --port 8000   # terminal 1 — API + UI
python -m src.worker                       # terminal 2 — ingest worker
uvicorn src.clip_service:app --port 8001   # terminal 3 — embedding service (or unset EMBED_SERVICE_URL)
```

Open http://localhost:8000.

Not sure which models your `.env` will actually use? Ask:

```bash
python -m src.providers          # resolved providers, missing keys, installed SDKs
python -m src.providers --live   # actually call each configured model
```

## Project conventions

- **Keep it minimal.** The frontend is a few static HTML pages + vanilla-JS modules in
  `ui/` (no build step) — please keep it that way unless there's a strong reason.
- **Retrieval stays local *by default*.** The default embedders (CLIP + bge) run without any
  API key. Hosted embedders are opt-in; new features shouldn't make a key mandatory for search.
- **Visual-first, multimodal for YouTube.** The core is visual (frames); for
  YouTube it also indexes the **transcript** (captions) and fuses the two branches by rank.
  Uploaded files are transcribed with Whisper (ASR) when they have no captions, so they're
  multimodal too.
- **New model provider = a row in the table, not a new code path.** Add it to
  `src/providers/registry.py`; only a genuinely different wire format needs an adapter module
  in `src/providers/llm/` or `src/providers/embed/`. Keep provider SDKs **lazily imported**
  and optional — importing the app must never require a provider you don't use. Visual-branch
  embedders must embed *text into the same space* (search is text→image).
- Each backend module has one job — see the layout in the README. Match the existing style
  (type hints, short docstrings explaining *why*).

## Pull requests

1. Fork and branch from `main`.
2. Keep PRs focused; describe what and why.
3. Make sure the app still boots and `python -m py_compile src/**/*.py` passes.
4. By contributing, you agree your work is licensed under Apache 2.0.

## Code of conduct

Be kind and constructive. We follow the spirit of the
[Contributor Covenant](https://www.contributor-covenant.org/).
