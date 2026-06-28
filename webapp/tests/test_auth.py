import pytest
import os

pytest.importorskip("jose", reason="python-jose required")


def test_missing_token_returns_401(monkeypatch):
    """No Authorization header → 401."""
    import auth
    monkeypatch.setattr(auth, "SUPABASE_JWT_SECRET", "test-secret-at-least-32-chars-long!!")
    import asyncio
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as exc:
        asyncio.run(auth.get_current_account(authorization=None))
    assert exc.value.status_code == 401


def test_invalid_token_returns_401(monkeypatch):
    """Garbage Bearer token → 401."""
    import auth
    monkeypatch.setattr(auth, "SUPABASE_JWT_SECRET", "test-secret-at-least-32-chars-long!!")
    import asyncio
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as exc:
        asyncio.run(auth.get_current_account(authorization="Bearer not-a-real-token"))
    assert exc.value.status_code == 401
