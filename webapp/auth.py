from __future__ import annotations
import functools
import logging
import os
from fastapi import Header, HTTPException, status
import requests as _requests

log = logging.getLogger(__name__)

SUPABASE_URL = os.getenv("SUPABASE_URL", "")


@functools.lru_cache(maxsize=1)
def _jwks() -> list:
    """Fetch and cache Supabase's public JWKS. Called once per process."""
    url = f"{SUPABASE_URL}/auth/v1/.well-known/jwks.json"
    resp = _requests.get(url, timeout=10)
    resp.raise_for_status()
    return resp.json().get("keys", [])


async def get_current_account(authorization: str | None = Header(default=None)) -> dict:
    """FastAPI dependency — validates Supabase JWT, returns account row."""
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing token")
    token = authorization.removeprefix("Bearer ")
    try:
        from jose import jwk as jose_jwk, jwt as jose_jwt
        keys = _jwks()
        payload = None
        for key_dict in keys:
            try:
                alg = key_dict.get("alg", "ES256")
                constructed = jose_jwk.construct(key_dict, algorithm=alg)
                payload = jose_jwt.decode(
                    token, constructed,
                    algorithms=[alg],
                    options={"verify_aud": False},
                )
                break
            except Exception:
                continue
        if payload is None:
            raise ValueError("no key matched")
        uid = payload.get("sub")
        if not uid:
            raise ValueError("no sub claim")
    except Exception as exc:
        log.debug("JWT decode failed: %s", type(exc).__name__)
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")

    import db_postgres as db
    account = db.get_account(uid)
    if not account:
        email = payload.get("email", "")
        account = db.create_account(uid, email)
    return account


async def get_current_account_optional(authorization: str | None = Header(default=None)) -> dict | None:
    """Same as get_current_account but returns None instead of 401."""
    try:
        return await get_current_account(authorization)
    except HTTPException:
        return None


async def get_current_profile(
    authorization: str | None = Header(default=None),
    x_profile_id: str | None = Header(default=None),
) -> dict:
    """FastAPI dependency — validates JWT, resolves active profile (users row).

    The frontend sends ``X-Profile-Id: <user_id>`` alongside the Bearer token.
    We verify that the profile belongs to the authenticated account before
    returning it.  Falls back to the account's first profile when the header
    is absent (e.g. first launch before any profile is selected).

    Returns a dict with keys: id, name, account_id, account (nested account dict).
    """
    account = await get_current_account(authorization)
    import db_postgres as db

    if x_profile_id:
        try:
            pid = int(x_profile_id)
        except (TypeError, ValueError):
            raise HTTPException(status_code=400, detail="Invalid X-Profile-Id")
        profile = db.get_profile(account["id"], pid)
        if not profile:
            raise HTTPException(status_code=403, detail="Profile not found or not owned by account")
    else:
        # Auto-select: return the first profile for this account.
        profiles = db.list_profiles(account["id"])
        if not profiles:
            # First sign-in — create a default profile named after the email prefix.
            name = account["email"].split("@")[0].capitalize() or "Me"
            profile = db.create_profile(account["id"], name)
        else:
            profile = profiles[0]

    profile["account"] = account
    return profile


async def get_current_profile_optional(
    authorization: str | None = Header(default=None),
    x_profile_id: str | None = Header(default=None),
) -> dict | None:
    """Same as get_current_profile but returns None instead of 401/403."""
    try:
        return await get_current_profile(authorization, x_profile_id)
    except HTTPException:
        return None
