from __future__ import annotations
import os
from fastapi import Header, HTTPException, status

SUPABASE_JWT_SECRET = os.getenv("SUPABASE_JWT_SECRET", "")


async def get_current_account(authorization: str | None = Header(default=None)) -> dict:
    """FastAPI dependency — validates Supabase JWT, returns account row."""
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing token")
    token = authorization.removeprefix("Bearer ")
    try:
        from jose import jwt as jose_jwt
        payload = jose_jwt.decode(
            token,
            SUPABASE_JWT_SECRET,
            algorithms=["HS256"],
            options={"verify_aud": False},
        )
        uid = payload.get("sub")
        if not uid:
            raise ValueError("no sub")
    except Exception:
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
