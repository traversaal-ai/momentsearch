"""Prefect Cloud trigger layer — the API schedules runs, workers execute them.

One flow ("ms-ingest-video" — the "ms-" prefix namespaces it inside a shared
Prefect workspace), one deployment ("ingest", registered by worker.py's
flow.serve()). The API never imports the pipeline or its heavy deps (torch,
ffmpeg) — it just asks Prefect Cloud to schedule a run; any live worker picks
it up. Retries/backoff live on the flow's tasks (src/ingest/pipeline.py);
failed runs are visible + retryable in the Prefect Cloud UI.
"""
from __future__ import annotations

from prefect.deployments import run_deployment

INGEST_DEPLOYMENT = "ms-ingest-video/ingest"


def enqueue_video(video_id: str, user_id: str) -> str:
    """Schedule the ingest flow for one video. Returns the Prefect flow-run id."""
    flow_run = run_deployment(
        name=INGEST_DEPLOYMENT,
        parameters={"video_id": video_id, "user_id": user_id},
        timeout=0,  # fire-and-forget: don't block the API waiting for the run
        flow_run_name=f"ingest-{video_id}",
    )
    return str(flow_run.id)
