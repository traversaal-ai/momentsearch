"""Demo sign-in — an email becomes a workspace.

    POST /api/auth/demo  {email}  -> {email, user_id, token}
    GET  /api/auth/me              -> who the caller's token says they are

The whole point is the second line of that exchange: `user_id` is the SAME
tenant key every video row, bucket key and Qdrant point is already tagged with
(src/db.py, src/config.py's key layout), so signing in doesn't add a tenancy
concept — it stops everyone sharing the "default" tenant.

Tokens are `user_id.expiry.hmac`, signed with AUTH_SECRET. The API derives the
tenant from the SIGNATURE, so a caller can't reach another workspace by editing
the X-User-Id header. That's the only security property here:

    THIS IS NOT AUTHENTICATION. No password, no verification — anyone can type
    anyone else's address and get their workspace. It gives a demo real,
    separate, persistent workspaces; it must not be the front door for users
    whose data matters. Put an IdP in front of this router and the tenant model
    underneath survives unchanged.
"""
from __future__ import annotations

import hashlib
import hmac
import re
import secrets
import time
import uuid

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel

from .. import config, db

router = APIRouter(prefix="/api/auth", tags=["auth"])

# Deliberately loose: a demo takes ada@demo.dev, not RFC 5322.
_EMAIL_RE = re.compile(r"^[^@\s]{1,64}@[^@\s.]+(?:\.[^@\s.]+)+$")
_WORKSPACE_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")

# No AUTH_SECRET = a per-process random one: tokens still can't be forged, they
# just don't survive a restart and don't validate across replicas. Loud about it
# rather than silently signing with a guessable constant.
_SECRET = config.AUTH_SECRET or secrets.token_hex(32)
if not config.AUTH_SECRET:
    # ASCII only: this runs at import, and a cp1252 console can raise on the
    # fancy dash mid-boot.
    print("[auth] AUTH_SECRET unset - signing sessions with a per-process key. "
          "Sign-ins won't survive a restart or work across replicas. "
          "Set AUTH_SECRET (openssl rand -hex 32) in any real deploy.", flush=True)


def _sign(body: str) -> str:
    return hmac.new(_SECRET.encode(), body.encode(), hashlib.sha256).hexdigest()[:32]


def mint_token(user_id: str) -> tuple[str, int]:
    """Returns (token, unix_expiry)."""
    exp = int(time.time()) + config.SESSION_TTL_DAYS * 86400
    body = f"{user_id}.{exp}"
    return f"{body}.{_sign(body)}", exp


def workspace_from_token(authorization: str | None) -> str | None:
    """The workspace id a Bearer token proves, or None.

    None covers every failure the same way — malformed, bad signature, expired,
    or simply not a session token (e.g. the ADMIN_TOKEN) — because callers all
    treat "no proven workspace" identically.
    """
    if not authorization or not authorization.startswith("Bearer "):
        return None
    parts = authorization[7:].strip().split(".")
    if len(parts) != 3:
        return None
    uid, exp, sig = parts
    if not hmac.compare_digest(sig, _sign(f"{uid}.{exp}")):
        return None
    try:
        if int(exp) < time.time():
            return None
    except ValueError:
        return None
    return uid if _WORKSPACE_RE.match(uid) else None


# ── Endpoints ────────────────────────────────────────────────────────────────

class DemoSignIn(BaseModel):
    email: str


@router.post("/demo")
def demo_sign_in(req: DemoSignIn):
    email = req.email.strip().lower()
    if not _EMAIL_RE.match(email):
        raise HTTPException(400, "That doesn't look like an email address.")
    # The generated id is only used if this email is new (ON CONFLICT keeps the
    # existing one), so the same address always returns to the same workspace.
    row = db.get_or_create_user(email, f"u_{uuid.uuid4().hex}")
    token, exp = mint_token(row["user_id"])
    return {"email": row["email"], "user_id": row["user_id"],
            "token": token, "expires_at": exp,
            "created": row["created_at"] == row["last_seen"]}


@router.get("/me")
def me(authorization: str | None = Header(default=None)):
    uid = workspace_from_token(authorization)
    if uid is None:
        raise HTTPException(401, "No valid session.")
    row = db.get_user_by_workspace(uid)
    if row is None:                     # workspace deleted under a live token
        raise HTTPException(401, "Session no longer valid.")
    return {"email": row["email"], "user_id": row["user_id"]}
