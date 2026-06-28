import pytest

pytest.importorskip("jose", reason="python-jose required")

_TEST_JWK = [{
    "kty": "EC", "crv": "P-256", "alg": "ES256",
    "x": "7_HIUr0hFGYa3qgU0oYR9yiVPaLcpjNVpXjskURiLpw",
    "y": "ByFuVE8LGA9IOd7CFrLognbQ39xiQEYzKg_LJ_QpIAs",
}]


def test_missing_token_returns_401():
    """No Authorization header → 401 without hitting network."""
    import asyncio, auth
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as exc:
        asyncio.run(auth.get_current_account(authorization=None))
    assert exc.value.status_code == 401


def test_invalid_token_returns_401(monkeypatch):
    """Garbage Bearer token → 401 (JWKS fetch mocked)."""
    import asyncio, auth
    from fastapi import HTTPException
    monkeypatch.setattr(auth, "_jwks", lambda: _TEST_JWK)
    with pytest.raises(HTTPException) as exc:
        asyncio.run(auth.get_current_account(authorization="Bearer not-a-real-token"))
    assert exc.value.status_code == 401
