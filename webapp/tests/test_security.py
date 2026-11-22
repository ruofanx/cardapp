"""Ownership, scan quota, upload path, and admin gates."""
from __future__ import annotations

import os
import sys
from contextlib import contextmanager

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import app as app_module
from auth import get_current_account

ACCOUNT = {"id": "acct-1", "email": "owner@example.com", "plan": "free", "trial_ends_at": None}
PRO = {**ACCOUNT, "plan": "pro"}


@pytest.fixture
def client():
    yield TestClient(app_module.app)
    app_module.app.dependency_overrides.clear()


def _as(account):
    app_module.app.dependency_overrides[get_current_account] = lambda: account


def _card(user_id, card_id=5):
    return app_module.db.Card(id=card_id, user_id=user_id, name="Pikachu")


def test_collection_routes_require_a_token(client):
    assert client.get("/api/users").status_code == 401
    assert client.get("/api/users/1/cards").status_code == 401
    assert client.get("/api/users/1/tags").status_code == 401
    assert client.delete("/api/tags/1").status_code == 401
    assert client.post("/api/cards/5/tags", json={"tag_id": 1}).status_code == 401
    assert client.delete("/api/cards/5").status_code == 401
    assert client.delete("/api/cards/5/photo").status_code == 401
    assert client.post("/api/identify", json={"query": "pikachu"}).status_code == 401
    assert client.post("/api/trade/propose", json={"user_id": 1, "target_value": 10}).status_code == 401
    assert client.post("/api/refresh-price", json={"name": "Pikachu"}).status_code == 401
    assert client.post("/api/refresh-prices/run-now").status_code == 401
    assert client.delete("/api/identify-cache").status_code == 401


def test_cannot_delete_another_profiles_card(client, monkeypatch):
    _as(ACCOUNT)
    monkeypatch.setattr(app_module.db, "profile_ids_for_account", lambda account_id: [1])
    monkeypatch.setattr(app_module.db, "get_card", lambda cid: _card(user_id=2, card_id=cid))
    called = {}
    monkeypatch.setattr(app_module.db, "delete_card", lambda cid: called.setdefault("id", cid) or True)
    res = client.delete("/api/cards/5")
    assert res.status_code == 403
    assert "id" not in called


def test_can_delete_own_card(client, monkeypatch):
    _as(ACCOUNT)
    monkeypatch.setattr(app_module.db, "profile_ids_for_account", lambda account_id: [1])
    monkeypatch.setattr(app_module.db, "get_card", lambda cid: _card(user_id=1, card_id=cid))
    monkeypatch.setattr(app_module.db, "delete_card", lambda cid: True)
    res = client.delete("/api/cards/5")
    assert res.status_code == 200
    assert res.json()["card_id"] == 5


def test_tag_attach_does_not_return_someone_elses_card(client, monkeypatch):
    _as(ACCOUNT)
    monkeypatch.setattr(app_module.db, "profile_ids_for_account", lambda account_id: [1])
    monkeypatch.setattr(app_module.db, "get_card", lambda cid: _card(user_id=9))
    res = client.post("/api/cards/5/tags", json={"tag_id": 1})
    assert res.status_code == 403


def test_free_scan_limit_uses_the_stored_count(client, monkeypatch):
    _as(ACCOUNT)
    monkeypatch.setattr(app_module.db, "get_scan_count", lambda account_id, month: 20)
    bumped = {}
    monkeypatch.setattr(app_module.db, "increment_scan_count", lambda *a, **k: bumped.setdefault("n", 1))
    res = client.post("/api/identify", json={"query": "pikachu"})
    assert res.status_code == 429
    assert res.json()["detail"]["error"] == "scan_limit_reached"
    assert "n" not in bumped


def test_identify_rejects_non_image_suffix(client, monkeypatch):
    _as(PRO)
    monkeypatch.setenv("GOOGLE_API_KEY", "test-key")
    monkeypatch.setattr(app_module.db, "increment_scan_count", lambda *a, **k: 1)
    res = client.post("/api/identify", files={"photo": ("xss.html", b"<script>", "text/html")})
    assert res.status_code == 400


def test_identify_rejects_oversized_photo(client, monkeypatch):
    _as(PRO)
    monkeypatch.setenv("GOOGLE_API_KEY", "test-key")
    monkeypatch.setattr(app_module, "MAX_UPLOAD_BYTES", 4)
    monkeypatch.setattr(app_module.db, "increment_scan_count", lambda *a, **k: 1)
    res = client.post("/api/identify", files={"photo": ("a.jpg", b"0123456789", "image/jpeg")})
    assert res.status_code == 413


def test_cardpatch_cannot_set_photo_path():
    assert "photo_path" not in app_module.CardPatch.model_fields


def test_safe_photo_file_rejects_traversal(tmp_path):
    uploads = tmp_path / "uploads"
    uploads.mkdir()
    secret = tmp_path / "secret.txt"
    secret.write_text("nope")
    assert app_module.safe_photo_file("/uploads/../../secret.txt", uploads) is None
    assert app_module.safe_photo_file("../secret.txt", uploads) is None
    assert app_module.safe_photo_file("/uploads/nested/a.jpg", uploads) is None
    assert app_module.safe_photo_file("/uploads/note.html", uploads) is None
    good = uploads / "card_1.jpg"
    good.write_bytes(b"img")
    assert app_module.safe_photo_file("/uploads/card_1.jpg", uploads) == good.resolve()


def test_trade_combo_size_is_capped(client, monkeypatch):
    _as(ACCOUNT)
    monkeypatch.setattr(app_module.db, "profile_ids_for_account", lambda account_id: [1])
    res = client.post("/api/trade/propose", json={
        "user_id": 1, "target_value": 10, "max_combo_size": 40, "tolerance": 1e12,
    })
    assert res.status_code == 422


def test_run_now_requires_admin_email(client, monkeypatch):
    _as(ACCOUNT)
    monkeypatch.delenv("ADMIN_EMAILS", raising=False)
    assert client.post("/api/refresh-prices/run-now").status_code == 403

    monkeypatch.setenv("ADMIN_EMAILS", "other@example.com")
    assert client.post("/api/refresh-prices/run-now").status_code == 403

    monkeypatch.setenv("ADMIN_EMAILS", "owner@example.com")

    async def fake_refresh():
        return {"updated": 0, "skipped": 0, "failures": 0}

    monkeypatch.setattr("refresh_job.refresh_all_cards", fake_refresh)
    res = client.post("/api/refresh-prices/run-now")
    assert res.status_code == 200
    assert res.json()["updated"] == 0


def test_get_scan_count_reads_named_column(monkeypatch):
    import db_postgres

    class Cur:
        def execute(self, *a, **k):
            pass

        def fetchone(self):
            return {"scan_count": 7}

    class Conn:
        def cursor(self):
            return Cur()

    @contextmanager
    def fake_connect():
        yield Conn()

    monkeypatch.setattr(db_postgres, "connect", fake_connect)
    assert db_postgres.get_scan_count("acct", "2026-11") == 7


def test_list_profiles_does_not_claim_unowned_rows(monkeypatch):
    import db_postgres

    sqls = []

    class Cur:
        def execute(self, sql, params=None):
            sqls.append(sql)

        def fetchall(self):
            return []

    class Conn:
        def cursor(self):
            return Cur()

        def commit(self):
            pass

    @contextmanager
    def fake_connect():
        yield Conn()

    monkeypatch.setattr(db_postgres, "connect", fake_connect)
    assert db_postgres.list_profiles("acct-1") == []
    assert sqls
    assert not any("IS NULL" in sql for sql in sqls)


def test_get_account_does_not_swallow_db_errors(monkeypatch):
    import db_postgres

    @contextmanager
    def boom():
        raise RuntimeError("db down")
        yield  # pragma: no cover

    monkeypatch.setattr(db_postgres, "connect", boom)
    with pytest.raises(RuntimeError):
        db_postgres.get_account("acct-1")
