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
