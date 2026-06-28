import pytest
import os

pytest.importorskip("jose", reason="python-jose required")


def test_missing_token_returns_401():
    os.environ.setdefault("SUPABASE_URL", "https://example.supabase.co")
    os.environ.setdefault("SUPABASE_JWT_SECRET", "test-secret-at-least-32-chars-long!!")
    import asyncio, importlib, sys
    sys.modules.pop("auth", None)
    from auth import get_current_account
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as exc:
        asyncio.run(get_current_account(authorization=None))
    assert exc.value.status_code == 401


def test_invalid_token_returns_401():
    os.environ.setdefault("SUPABASE_URL", "https://example.supabase.co")
    os.environ.setdefault("SUPABASE_JWT_SECRET", "test-secret-at-least-32-chars-long!!")
    import asyncio, sys
    sys.modules.pop("auth", None)
    from auth import get_current_account
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as exc:
        asyncio.run(get_current_account(authorization="Bearer not-a-real-token"))
    assert exc.value.status_code == 401
