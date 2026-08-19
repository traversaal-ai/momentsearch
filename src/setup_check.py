"""Runtime config readiness — what a running instance is still missing.

ONE source of truth for three surfaces, so the rules live in exactly one place:
  * the startup log (app.py lifespan) — visible on boot next to "MomentSearch is UP"
  * /api/config's `setup` block — the workspace reads it to draw the setup pill/bar
  * the register guard (api/videos.py) — a video isn't accepted into a stack that
    can't ingest it, so nothing sits `pending` forever with a cryptic failure.

Unlike preflight.py (which only fires on a DEPLOY and catches LOCAL settings leaking
to prod), this runs EVERYWHERE and answers the opposite question: "did the operator
forget a key this instance needs?" Each gap is classified:

  blocking  — nothing can ingest or search without it (Prefect, Qdrant, Postgres)
  degraded  — the app runs, one feature is off (no LLM = retrieval-only; no Gemini
              = no speaker recognition). Never blocks; just tints the pill amber.

Every item carries the exact env var(s) to set, so every surface can tell the user
precisely what to paste — not just "misconfigured".
"""
from __future__ import annotations

import os

from . import config


def _prefect_ok() -> bool:
    # The Prefect SDK reads these straight from the environment; config.py doesn't
    # mirror them, so check the env directly. Both are needed to schedule a run.
    return bool(os.getenv("PREFECT_API_URL", "").strip()
                and os.getenv("PREFECT_API_KEY", "").strip())


def _issue(id: str, level: str, feature: str, fix: str, env: list[str]) -> dict:
    return {"id": id, "level": level, "feature": feature, "fix": fix, "env": env}


def issues() -> list[dict]:
    """Every config gap on this instance, most severe first (empty = fully set up)."""
    out: list[dict] = []

    # ── blocking: the ingest + search stack ──────────────────────────────────
    if not _prefect_ok():
        out.append(_issue(
            "prefect", "blocking", "Video ingest is off",
            "Set your Prefect Cloud keys so added videos can be processed.",
            ["PREFECT_API_URL", "PREFECT_API_KEY"]))

    if not config.QDRANT_URL:
        out.append(_issue(
            "qdrant", "blocking", "Search index not configured",
            "Point QDRANT_URL at your Qdrant (Cloud, or the bundled local one).",
            ["QDRANT_URL"]))

    if not config.DATABASE_URL:
        out.append(_issue(
            "database", "blocking", "Database not configured",
            "Set DATABASE_URL — the app can't track videos or sessions without it.",
            ["DATABASE_URL"]))

    # ── degraded: optional features ──────────────────────────────────────────
    if not config.llm_configured():
        out.append(_issue(
            "llm", "degraded", "Answers are retrieval-only",
            "Add an LLM key for written, cited answers (moments still work without it).",
            ["OPENAI_API_KEY"]))

    if not config.GEMINI_API_KEY:
        out.append(_issue(
            "gemini", "degraded", "Speaker recognition off",
            "Add GEMINI_API_KEY to enable “who said what” on videos.",
            ["GEMINI_API_KEY"]))

    order = {"blocking": 0, "degraded": 1}
    out.sort(key=lambda i: order.get(i["level"], 9))
    return out


def report() -> dict:
    """The shape /api/config exposes and the UI consumes."""
    items = issues()
    return {
        "ready": not any(i["level"] == "blocking" for i in items),
        "issues": items,
    }


def ingest_blockers() -> list[dict]:
    """Blocking gaps that stop a NEW video from ever ingesting — used by the
    register endpoint to reject an add with the exact key to set, instead of
    letting it die deep in the worker. (Postgres isn't here: without it the API
    wouldn't be answering the request at all.)"""
    return [i for i in issues()
            if i["level"] == "blocking" and i["id"] in ("prefect", "qdrant")]


def log_startup() -> None:
    """Print the readiness summary on boot. Never raises — a missing key degrades
    the app, it doesn't stop it from starting."""
    try:
        items = issues()
    except Exception:
        return
    if not items:
        print("[setup] all keys present — every feature is on", flush=True)
        return
    bar = "-" * 64
    lines = []
    for i in items:
        tag = "OFF " if i["level"] == "blocking" else "note"
        lines.append(f"  [{tag}] {i['feature']} -> set {' + '.join(i['env'])}")
    body = "\n".join(lines)
    print(f"\n{bar}\n[setup] Some features need keys (see the workspace banner):\n"
          f"{body}\n{bar}\n", flush=True)
