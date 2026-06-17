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

# Qdrant
docker run -p 6333:6333 qdrant/qdrant

# Backend
python -m venv .venv && source .venv/bin/activate
pip install -r backend/requirements.txt
cp .env.example .env            # add an LLM key if you want synthesized answers
uvicorn backend.app.main:app --reload --port 8000
```

Open http://localhost:8000.

## Project conventions

- **Keep it minimal.** The frontend is a single `index.html` with no build step — please
  keep it that way unless there's a strong reason.
- **Retrieval stays local.** CLIP runs without any API key. The LLM is only for the final
  answer; new features shouldn't make a key mandatory for search.
- **Visual, not audio.** MomentSearch deliberately ignores audio/transcripts. Audio features
  are out of scope for this repo.
- Each backend module has one job — see the layout in the README. Match the existing style
  (type hints, short docstrings explaining *why*).

## Pull requests

1. Fork and branch from `main`.
2. Keep PRs focused; describe what and why.
3. Make sure the app still boots and `python -m py_compile backend/app/*.py` passes.
4. By contributing, you agree your work is licensed under Apache 2.0.

## Code of conduct

Be kind and constructive. We follow the spirit of the
[Contributor Covenant](https://www.contributor-covenant.org/).
