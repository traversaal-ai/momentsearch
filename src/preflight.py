"""Deploy-time sanity check - catch LOCAL settings on a real deploy.

STORAGE_PROVIDER=local, a compose-only Qdrant/Postgres host, an empty (embedded)
QDRANT_URL, or COMPOSE_PROFILES in the environment all work on your laptop but
BREAK a deploy: Fly machines have ephemeral disks and none of the compose service
names (`qdrant`, `postgres`) exist there. This warns loudly in the logs when it
sees them on a deployed machine, and - with STRICT_DEPLOY_CHECK=true - refuses to
start / aborts the deploy instead.

"Deployed" = Fly injects FLY_APP_NAME (or you set DEPLOY_ENV). Off a deploy this
is a no-op, so local dev never triggers it.
"""
from __future__ import annotations

import os
from urllib.parse import urlparse

from . import config

# Hosts that only resolve on a laptop / inside the compose network.
_LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1", "qdrant", "postgres", "db"}


def _is_deployed() -> bool:
    return bool(os.getenv("FLY_APP_NAME")
                or os.getenv("DEPLOY_ENV", "").strip().lower()
                in ("prod", "production", "deploy", "staging"))


def _host(url: str) -> str:
    try:
        return (urlparse(url).hostname or "").lower()
    except Exception:
        return ""


def issues() -> list[str]:
    """Local-only settings that will break on a deploy (empty list when fine)."""
    out: list[str] = []

    if config.STORAGE_PROVIDER == "local":
        out.append("STORAGE_PROVIDER=local - uploads and frames land on the "
                   "machine's EPHEMERAL disk: lost on restart and not shared "
                   "across api/worker. Use a bucket (flyio / gcp_native / aws).")

    if not config.QDRANT_URL:
        out.append("QDRANT_URL is empty - that's the embedded, single-process "
                   "Qdrant, which api and worker cannot share. Point it at "
                   "Qdrant Cloud.")
    elif _host(config.QDRANT_URL) in _LOCAL_HOSTS:
        out.append(f"QDRANT_URL points at '{_host(config.QDRANT_URL)}' - a "
                   "local/compose host that does not exist on the deploy. Point "
                   "it at Qdrant Cloud.")

    if config.DATABASE_URL and _host(config.DATABASE_URL) in _LOCAL_HOSTS:
        out.append(f"DATABASE_URL points at '{_host(config.DATABASE_URL)}' - a "
                   "local/compose host that does not exist on the deploy. Point "
                   "it at Neon (or a reachable managed Postgres).")

    if os.getenv("COMPOSE_PROFILES", "").strip():
        out.append("COMPOSE_PROFILES is set - a docker-compose-only setting. Its "
                   "presence means a LOCAL .env was copied to the deploy; remove "
                   "it and set cloud DATABASE_URL / QDRANT_URL.")
    return out


def check(context: str = "startup") -> list[str]:
    """Warn (or, with STRICT_DEPLOY_CHECK, exit) when a deploy carries local
    settings. No-op off a deploy. Returns the issues found (empty = clean)."""
    if not _is_deployed():
        return []
    found = issues()
    if not found:
        return []
    bar = "=" * 72
    body = "\n".join(f"  [!]  {m}" for m in found)
    print(f"\n{bar}\n[preflight] DEPLOYING WITH LOCAL SETTINGS ({context}) - these "
          f"will break in production:\n{body}\n{bar}\n", flush=True)
    if config.STRICT_DEPLOY_CHECK:
        raise SystemExit(
            "[preflight] STRICT_DEPLOY_CHECK=true and local settings are present "
            "- refusing to start. Fix the settings above, or unset "
            "STRICT_DEPLOY_CHECK to downgrade this to a warning.")
    return found
