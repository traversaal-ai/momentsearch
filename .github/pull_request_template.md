## What & why

Briefly describe the change and the motivation.

## Checklist

- [ ] App still boots (`uvicorn backend.app.main:app`)
- [ ] `python -m py_compile backend/app/*.py` passes
- [ ] Kept it minimal (no build step added to the frontend without discussion)
- [ ] Retrieval still works without an LLM key
- [ ] Updated README / `.env.example` if config changed

## Notes for reviewers

Anything to call out — trade-offs, follow-ups, screenshots.
