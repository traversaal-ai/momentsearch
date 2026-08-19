"""Identity — there is exactly one account, and you are always it.

    GET /api/auth/me  -> {"name": "admin", "user_id": "default"}

This deployment is single-user by design: no sign-up, no sign-in, no session
tokens, no email. Opening the app IS being the admin. `/api/auth/me` survives
only so the UI has one place to ask what to print in the header — it takes no
credentials and can never fail.

WHAT THIS REPLACED, AND WHY THE DATA MODEL DIDN'T CHANGE
--------------------------------------------------------
There used to be an email-for-workspace exchange here that minted `u_<uuid>`
tenants and HMAC-signed tokens. It's gone. What has NOT changed is that every
bucket key, Postgres row and Qdrant point is still `user_id`-tagged and still
filtered by tenant on read — the tenant is simply a constant now
(config.SINGLE_USER_ID). So restoring real multi-user auth means putting an IdP
in front and resolving a per-request user_id again; the storage layout, the
queries and the vector filters all already do the right thing.

    NO AUTHENTICATION IS PERFORMED ANYWHERE. Whoever can reach this port owns
    the account and can upload, ask and delete. Bind it to localhost, or put a
    reverse proxy that authenticates in front of it.
"""
from __future__ import annotations

from fastapi import APIRouter

from .. import config

router = APIRouter(prefix="/api/auth", tags=["auth"])


@router.get("/me")
def me():
    """The one account. No headers read, no failure path — see the module note."""
    return {"name": config.SINGLE_USER_NAME, "user_id": config.SINGLE_USER_ID}
