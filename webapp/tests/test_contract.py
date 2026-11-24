"""Portfolio history, price-log window, CSV export, and health on SQLite."""
from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import db as sqlite_db
import app as app_module
from auth import get_current_account, get_current_profile


@pytest.fixture
def mem_db(tmp_path, monkeypatch):
    monkeypatch.setattr(sqlite_db, "DB_PATH", tmp_path / "cards.sqlite")
    sqlite_db.init_db()
    return sqlite_db


def test_log_price_keeps_a_later_unchanged_snapshot(mem_db):
    user = mem_db.list_users()[0]
    card = mem_db.create_card(mem_db.Card(id=None, user_id=user.id, name="Pikachu"))
    older = (datetime.now(timezone.utc) - timedelta(days=2)).strftime("%Y-%m-%d %H:%M:%S")
    mem_db.log_price(card.id, 12.5, source="test", at=older)
    mem_db.log_price(card.id, 12.5, source="test")
    points = mem_db.get_price_history(card.id)
    assert len(points) == 2
    assert points[0]["price"] == points[1]["price"] == 12.5


def test_log_price_skips_a_duplicate_inside_the_window(mem_db):
    user = mem_db.list_users()[0]
    card = mem_db.create_card(mem_db.Card(id=None, user_id=user.id, name="Bulbasaur"))
    mem_db.log_price(card.id, 4.0, source="test")
    mem_db.log_price(card.id, 4.0, source="test")
    assert len(mem_db.get_price_history(card.id)) == 1


def test_cardpatch_accepts_alert_price():
    patch = app_module.CardPatch.model_validate({"alert_price": 19.5})
    assert patch.dict(exclude_unset=True) == {"alert_price": 19.5}
    cleared = app_module.CardPatch.model_validate({"alert_price": None})
    assert cleared.dict(exclude_unset=True) == {"alert_price": None}


def test_csv_export_uses_card_number_and_tag_names(monkeypatch):
    tag = sqlite_db.Tag(id=1, user_id=1, name="binder", color="#fff")
    card = sqlite_db.Card(
        id=1, user_id=1, name="Pikachu", set_name="Base", card_number="58/102",
        language="chinese", current_market_price=10.0, purchase_price=4.0,
        created_at="2026-01-02T15:00:00", tags=[tag],
    )
    monkeypatch.setattr(app_module.db, "list_cards", lambda user_id: [card])
    app_module.app.dependency_overrides[get_current_profile] = lambda: {
        "id": 1, "name": "Ro", "account": {"id": "a", "email": "a@b.c", "plan": "pro"},
    }
    try:
        res = TestClient(app_module.app).get("/api/profile/export")
    finally:
        app_module.app.dependency_overrides.pop(get_current_profile, None)
    assert res.status_code == 200
    body = res.text
    assert "58/102" in body
    assert "CH" in body
    assert "binder" in body
    assert "2026-01-02" in body


def test_health_counts_cards_without_conn_execute():
    try:
        app_module.db.init_db()
    except Exception as e:
        raise AssertionError(f"init failed: {e}") from e
    res = TestClient(app_module.app).get("/api/health")
    assert res.status_code == 200
    body = res.json()
    assert body["ok"] is True
    assert "db_error" not in body
    assert isinstance(body["cards"], int)


def test_postgres_module_has_portfolio_history():
    import db_postgres
    assert callable(db_postgres.portfolio_history)
