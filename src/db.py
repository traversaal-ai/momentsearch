"""Postgres (Neon) access layer — the videos manifest, source of truth.

One row per (user's) video; `status` tracks the ingest lifecycle:
pending -> fetching -> sampling -> embedding -> indexed | skipped | failed
(skipped = duplicate (user_id, source_hash); indexed = searchable in Qdrant).
"""
from __future__ import annotations

import os
from typing import Any

from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from psycopg_pool import ConnectionPool

from .config import DATABASE_URL, DEFAULT_USER_ID, INFLIGHT_STATUSES
from .samples import SAMPLE_IDS

_pool: ConnectionPool | None = None
_pool_pid: int | None = None


def pool() -> ConnectionPool:
    """Process-local pool. Prefect runs flows in subprocesses; a child must
    never reuse the parent's SSL connections (corrupts the TLS stream), so a
    fork gets a fresh pool."""
    global _pool, _pool_pid
    if _pool is None or _pool_pid != os.getpid():
        # check= pings each connection before lending it out — Neon silently
        # drops idle SSL connections, which otherwise 500s the first request
        # after a quiet period.
        _pool = ConnectionPool(DATABASE_URL, min_size=1, max_size=5,
                               check=ConnectionPool.check_connection,
                               kwargs={"row_factory": dict_row})
        _pool_pid = os.getpid()
    return _pool


SCHEMA = """
CREATE TABLE IF NOT EXISTS ms_videos (
    id           TEXT PRIMARY KEY,           -- yt_<id> | up_<uuid>
    user_id      TEXT NOT NULL,
    source       TEXT NOT NULL,              -- youtube | upload
    url          TEXT,                       -- YouTube URL (source=youtube)
    storage_key  TEXT,                       -- {user}/{id}/source.{ext} (source=upload)
    source_hash  TEXT,                       -- sha256 of the file / yt video id
    title        TEXT,
    status       TEXT NOT NULL DEFAULT 'pending',
    error        TEXT,
    frame_count  INT,
    progress     REAL,                       -- 0..1 within the current stage
    attempts     INT NOT NULL DEFAULT 0,
    embed_version TEXT,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ms_videos_user_idx   ON ms_videos (user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS ms_videos_status_idx ON ms_videos (status);
CREATE INDEX IF NOT EXISTS ms_videos_hash_idx   ON ms_videos (user_id, source_hash);

-- Bring-your-own-model: a tenant's own answer model — any provider name from
-- src/providers/registry.py, or their own OpenAI-compatible server via base_url.
-- When a row exists the read path answers with THIS model instead of the
-- server's LLM_* env config. Only the LLM is per-tenant: embeddings are shared
-- Qdrant collections, so their dimension can't vary by user.
CREATE TABLE IF NOT EXISTS ms_user_llms (
    user_id    TEXT PRIMARY KEY,
    -- a registry key: openai | gemini | anthropic | openrouter | xai | groq |
    -- together | fireworks | mistral | nvidia | azure_openai | ollama |
    -- lmstudio | vllm | custom  (`python -m src.providers` lists them)
    provider   TEXT NOT NULL DEFAULT 'openai',
    model      TEXT NOT NULL,
    base_url   TEXT,                            -- e.g. http://my-vllm:8000/v1
    api_key    TEXT,                            -- optional (vLLM often has none)
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Demo sign-in: an email IS the account, and it maps to exactly one workspace
-- id forever. That id is the `user_id` every video row, bucket key and Qdrant
-- point is already tagged with, so signing in doesn't introduce a new tenancy
-- concept — it just stops everyone sharing the "default" tenant.
CREATE TABLE IF NOT EXISTS ms_users (
    email      TEXT PRIMARY KEY,
    user_id    TEXT NOT NULL UNIQUE,   -- u_<uuid4 hex>, matches ^[A-Za-z0-9_-]{1,64}$
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_seen  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- A session is one folder: a set of videos + the chat about them. Uploads land
-- in the session you're in, so sessions are separate topics, not views of one
-- pile. kind='demo' is the seeded "Demo videos" session (the shared samples);
-- every session the user creates starts EMPTY — no samples ride along.
CREATE TABLE IF NOT EXISTS ms_sessions (
    id         TEXT PRIMARY KEY,           -- s_<uuid4 hex>
    user_id    TEXT NOT NULL,
    title      TEXT NOT NULL,
    kind       TEXT NOT NULL DEFAULT 'own',  -- demo | own
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ms_sessions_user_idx ON ms_sessions (user_id, created_at DESC);

-- Which videos a session searches. ON DELETE CASCADE both ways: deleting a
-- session drops its membership rows, and deleting a video removes it from every
-- session that held it (no dangling ids in a query scope).
CREATE TABLE IF NOT EXISTS ms_session_videos (
    session_id TEXT NOT NULL REFERENCES ms_sessions(id) ON DELETE CASCADE,
    video_id   TEXT NOT NULL REFERENCES ms_videos(id)   ON DELETE CASCADE,
    added_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (session_id, video_id)
);

-- The chat. Each question stores its own answer + the citations it was given,
-- so reopening a session shows exactly the moments that answer cited — no
-- re-running retrieval (whose results would drift as videos are added).
CREATE TABLE IF NOT EXISTS ms_messages (
    id         BIGSERIAL PRIMARY KEY,
    session_id TEXT NOT NULL REFERENCES ms_sessions(id) ON DELETE CASCADE,
    role       TEXT NOT NULL,              -- user | assistant
    content    TEXT NOT NULL DEFAULT '',
    citations  JSONB,
    meta       JSONB,                      -- llm_used, abstained, model, note
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ms_messages_session_idx ON ms_messages (session_id, id);
"""


def init_schema() -> None:
    with pool().connection() as conn:
        conn.execute(SCHEMA)


def upsert_pending(video: dict[str, Any]) -> dict:
    """Insert a video as pending; re-submitting an existing id resets it."""
    with pool().connection() as conn:
        row = conn.execute(
            """
            INSERT INTO ms_videos (id, user_id, source, url, storage_key, source_hash, title, status)
            VALUES (%(id)s, %(user_id)s, %(source)s, %(url)s, %(storage_key)s,
                    %(source_hash)s, %(title)s, 'pending')
            ON CONFLICT (id) DO UPDATE SET
                url = COALESCE(EXCLUDED.url, ms_videos.url),
                storage_key = COALESCE(EXCLUDED.storage_key, ms_videos.storage_key),
                source_hash = COALESCE(EXCLUDED.source_hash, ms_videos.source_hash),
                title = COALESCE(EXCLUDED.title, ms_videos.title),
                status = 'pending', error = NULL, progress = NULL, updated_at = now()
            RETURNING *
            """,
            video,
        ).fetchone()
    return row


def set_status(video_id: str, status: str, *, error: str | None = None,
               title: str | None = None, frame_count: int | None = None,
               source_hash: str | None = None, embed_version: str | None = None,
               progress: float | None = None) -> None:
    with pool().connection() as conn:
        conn.execute(
            """
            UPDATE ms_videos SET status = %s, error = %s,
                title = COALESCE(%s, title),
                frame_count = COALESCE(%s, frame_count),
                source_hash = COALESCE(%s, source_hash),
                embed_version = COALESCE(%s, embed_version),
                progress = %s,
                updated_at = now()
            WHERE id = %s
            """,
            (status, error, title, frame_count, source_hash, embed_version,
             progress, video_id),
        )


def set_progress(video_id: str, progress: float) -> None:
    with pool().connection() as conn:
        conn.execute("UPDATE ms_videos SET progress = %s, updated_at = now() WHERE id = %s",
                     (round(progress, 3), video_id))


def bump_attempts(video_id: str) -> int:
    with pool().connection() as conn:
        row = conn.execute(
            "UPDATE ms_videos SET attempts = attempts + 1, updated_at = now() WHERE id = %s RETURNING attempts",
            (video_id,),
        ).fetchone()
    return row["attempts"] if row else 0


def get_video(video_id: str) -> dict | None:
    with pool().connection() as conn:
        return conn.execute("SELECT * FROM ms_videos WHERE id = %s", (video_id,)).fetchone()


def find_duplicate(user_id: str, source_hash: str, exclude_id: str) -> dict | None:
    """An already-indexed video with the same content for the same user."""
    with pool().connection() as conn:
        return conn.execute(
            """
            SELECT * FROM ms_videos
            WHERE user_id = %s AND source_hash = %s AND id <> %s AND status = 'indexed'
            LIMIT 1
            """,
            (user_id, source_hash, exclude_id),
        ).fetchone()


def count_video_sessions(video_id: str) -> int:
    """How many sessions still point at this video. Reference count for the
    delete-session cleanup: a video is purged only when this hits zero."""
    with pool().connection() as conn:
        row = conn.execute(
            "SELECT count(*) AS n FROM ms_session_videos WHERE video_id = %s",
            (video_id,)).fetchone()
    return int(row["n"]) if row else 0


def resolve_duplicate(stub_id: str, original_id: str) -> None:
    """A re-added video turned out to duplicate content the user already has
    indexed (`original_id`). Move the redundant stub's session memberships onto
    the original, then delete the stub — so the user ends up with the WORKING
    video in their session(s) instead of a dead 'duplicate' card. The stub's
    ms_session_videos rows go with it via ON DELETE CASCADE."""
    with pool().connection() as conn:
        conn.execute(
            """
            INSERT INTO ms_session_videos (session_id, video_id)
            SELECT session_id, %s FROM ms_session_videos WHERE video_id = %s
            ON CONFLICT DO NOTHING
            """,
            (original_id, stub_id),
        )
        conn.execute("DELETE FROM ms_videos WHERE id = %s AND status = 'skipped'",
                     (stub_id,))


def list_videos(user_id: str, status: str | None = None,
                include_samples: bool = False) -> list[dict]:
    """A tenant's videos, newest first.

    include_samples also returns the curated sample corpus (owned by the default
    tenant), so a freshly signed-in workspace has something to search on arrival
    instead of an empty library. Samples come last — your own videos first.
    """
    q = "SELECT * FROM ms_videos WHERE (user_id = %s"
    params: list = [user_id]
    if include_samples and user_id != DEFAULT_USER_ID:
        q += " OR (user_id = %s AND id = ANY(%s))"
        params += [DEFAULT_USER_ID, list(SAMPLE_IDS)]
    q += ")"
    if status:
        q += " AND status = %s"
        params.append(status)
    q += " ORDER BY (user_id <> %s), created_at DESC"
    params.append(user_id)
    with pool().connection() as conn:
        return conn.execute(q, tuple(params)).fetchall()


# ── Demo sign-in (email -> workspace) ────────────────────────────────────────

def get_or_create_user(email: str, user_id: str) -> dict:
    """Resolve an email to its workspace, minting `user_id` only if this email
    has never been seen. The INSERT ... ON CONFLICT makes concurrent first
    sign-ins safe: the loser's generated id is discarded, not stored."""
    with pool().connection() as conn:
        return conn.execute(
            """
            INSERT INTO ms_users (email, user_id) VALUES (%s, %s)
            ON CONFLICT (email) DO UPDATE SET last_seen = now()
            RETURNING *
            """,
            (email, user_id),
        ).fetchone()


def get_user_by_workspace(user_id: str) -> dict | None:
    with pool().connection() as conn:
        return conn.execute("SELECT * FROM ms_users WHERE user_id = %s",
                            (user_id,)).fetchone()


# ── Sessions (a folder of videos + the chat about them) ───────────────────────

def create_session(session_id: str, user_id: str, title: str,
                   kind: str = "own") -> dict:
    with pool().connection() as conn:
        return conn.execute(
            """
            INSERT INTO ms_sessions (id, user_id, title, kind)
            VALUES (%s, %s, %s, %s) RETURNING *
            """,
            (session_id, user_id, title, kind),
        ).fetchone()


def list_sessions(user_id: str) -> list[dict]:
    """Sessions newest-first, each with how many videos and messages it holds —
    one query, so the sidebar doesn't need a request per session."""
    with pool().connection() as conn:
        return conn.execute(
            """
            SELECT s.*,
                   (SELECT count(*) FROM ms_session_videos v WHERE v.session_id = s.id) AS video_count,
                   (SELECT count(*) FROM ms_messages m WHERE m.session_id = s.id) AS message_count
            FROM ms_sessions s
            WHERE s.user_id = %s
            ORDER BY s.updated_at DESC, s.created_at DESC
            """,
            (user_id,),
        ).fetchall()


def get_session(session_id: str, user_id: str) -> dict | None:
    """Scoped by user_id on purpose: another workspace's session id reads as
    'not found', never as someone else's data."""
    with pool().connection() as conn:
        return conn.execute(
            "SELECT * FROM ms_sessions WHERE id = %s AND user_id = %s",
            (session_id, user_id),
        ).fetchone()


def rename_session(session_id: str, user_id: str, title: str) -> dict | None:
    with pool().connection() as conn:
        return conn.execute(
            """
            UPDATE ms_sessions SET title = %s, updated_at = now()
            WHERE id = %s AND user_id = %s RETURNING *
            """,
            (title, session_id, user_id),
        ).fetchone()


def touch_session(session_id: str) -> None:
    """Bump updated_at so the sidebar sorts by recent activity."""
    with pool().connection() as conn:
        conn.execute("UPDATE ms_sessions SET updated_at = now() WHERE id = %s",
                     (session_id,))


def delete_session(session_id: str, user_id: str) -> bool:
    """Drops the session, its video membership and its chat (FK cascade). The
    videos themselves survive — they belong to the workspace, not the session."""
    with pool().connection() as conn:
        row = conn.execute(
            "DELETE FROM ms_sessions WHERE id = %s AND user_id = %s RETURNING id",
            (session_id, user_id),
        ).fetchone()
    return row is not None


def add_session_video(session_id: str, video_id: str) -> None:
    with pool().connection() as conn:
        conn.execute(
            """
            INSERT INTO ms_session_videos (session_id, video_id) VALUES (%s, %s)
            ON CONFLICT DO NOTHING
            """,
            (session_id, video_id),
        )


def remove_session_video(session_id: str, video_id: str) -> None:
    with pool().connection() as conn:
        conn.execute(
            "DELETE FROM ms_session_videos WHERE session_id = %s AND video_id = %s",
            (session_id, video_id),
        )


def session_videos(session_id: str) -> list[dict]:
    """Full video rows in a session, own videos before shared samples."""
    with pool().connection() as conn:
        return conn.execute(
            """
            SELECT v.* FROM ms_session_videos sv
            JOIN ms_videos v ON v.id = sv.video_id
            WHERE sv.session_id = %s
            ORDER BY sv.added_at
            """,
            (session_id,),
        ).fetchall()


# ── Chat ─────────────────────────────────────────────────────────────────────

def add_message(session_id: str, role: str, content: str,
                citations: list | None = None, meta: dict | None = None) -> dict:
    with pool().connection() as conn:
        row = conn.execute(
            """
            INSERT INTO ms_messages (session_id, role, content, citations, meta)
            VALUES (%s, %s, %s, %s, %s) RETURNING *
            """,
            (session_id, role, content, Jsonb(citations) if citations is not None else None,
             Jsonb(meta) if meta is not None else None),
        ).fetchone()
    return row


def list_messages(session_id: str, limit: int = 200) -> list[dict]:
    with pool().connection() as conn:
        return conn.execute(
            "SELECT * FROM ms_messages WHERE session_id = %s ORDER BY id LIMIT %s",
            (session_id, limit),
        ).fetchall()


def videos_by_ids(ids: list[str]) -> dict[str, dict]:
    """Metadata join for search citations (title/url/source live here, not in Qdrant)."""
    if not ids:
        return {}
    with pool().connection() as conn:
        rows = conn.execute("SELECT * FROM ms_videos WHERE id = ANY(%s)", (ids,)).fetchall()
    return {r["id"]: r for r in rows}


def delete_video(video_id: str) -> None:
    with pool().connection() as conn:
        conn.execute("DELETE FROM ms_videos WHERE id = %s", (video_id,))


# ── Fair scheduling (WFQ) ────────────────────────────────────────────────────

def count_inflight() -> int:
    """How many videos currently occupy execution capacity (scheduled/running)."""
    with pool().connection() as conn:
        row = conn.execute(
            "SELECT count(*) AS n FROM ms_videos WHERE status = ANY(%s)",
            (list(INFLIGHT_STATUSES),),
        ).fetchone()
    return row["n"] if row else 0


def wfq_claim(limit: int) -> list[dict]:
    """Atomically claim up to `limit` pending videos in FAIR (round-robin across
    users) order, flipping them pending -> queued. Returns the claimed rows.

    Fairness: rank each user's pending videos by age (row_number partitioned by
    user_id), then order by that rank first — so we take everyone's oldest, then
    everyone's 2nd, ... A user who dumped 50 videos only gets one slot per round,
    exactly like the others. The UPDATE ... WHERE status='pending' RETURNING is
    the atomic claim: if two dispatchers race, each row is handed out once.
    """
    if limit <= 0:
        return []
    with pool().connection() as conn:
        picked = conn.execute(
            """
            SELECT id FROM (
                SELECT id, row_number() OVER (
                    PARTITION BY user_id ORDER BY created_at, id) AS rn
                FROM ms_videos WHERE status = 'pending'
            ) t
            ORDER BY rn, id
            LIMIT %s
            """,
            (limit,),
        ).fetchall()
        ids = [r["id"] for r in picked]
        if not ids:
            return []
        return conn.execute(
            """
            UPDATE ms_videos SET status = 'queued', updated_at = now()
            WHERE id = ANY(%s) AND status = 'pending'
            RETURNING id, user_id
            """,
            (ids,),
        ).fetchall()


# ── Bring-your-own-model (per-tenant LLM endpoint) ───────────────────────────

def get_user_llm(user_id: str) -> dict | None:
    with pool().connection() as conn:
        return conn.execute("SELECT * FROM ms_user_llms WHERE user_id = %s",
                            (user_id,)).fetchone()


def set_user_llm(user_id: str, *, provider: str, model: str,
                 base_url: str | None, api_key: str | None) -> dict:
    """Upsert a tenant's model endpoint. An empty api_key keeps the stored one
    (so users can change model/URL without re-pasting their secret)."""
    with pool().connection() as conn:
        return conn.execute(
            """
            INSERT INTO ms_user_llms (user_id, provider, model, base_url, api_key)
            VALUES (%s, %s, %s, %s, %s)
            ON CONFLICT (user_id) DO UPDATE SET
                provider = EXCLUDED.provider,
                model = EXCLUDED.model,
                base_url = EXCLUDED.base_url,
                api_key = COALESCE(NULLIF(EXCLUDED.api_key, ''), ms_user_llms.api_key),
                updated_at = now()
            RETURNING *
            """,
            (user_id, provider, model, base_url, api_key),
        ).fetchone()


def delete_user_llm(user_id: str) -> None:
    with pool().connection() as conn:
        conn.execute("DELETE FROM ms_user_llms WHERE user_id = %s", (user_id,))
