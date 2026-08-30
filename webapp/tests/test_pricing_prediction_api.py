# webapp/tests/test_pricing_prediction_api.py
from __future__ import annotations

import math
import os
from unittest.mock import AsyncMock, patch

os.environ["PRICING_MODEL_DB"] = "/tmp/test_prediction_api_pm.sqlite"

import pytest
from fastapi.testclient import TestClient

import app as app_module
import pricing_model.db as pmdb


@pytest.fixture(autouse=True)
def fresh_pricing_db():
    if pmdb.DB_PATH.exists():
        pmdb.DB_PATH.unlink()
    pmdb.init_db()
    yield


def _fake_card(card_id: int, **overrides):
    fields = dict(
        id=card_id, user_id=1, name="Charizard ex", set_name="Surging Sparks",
        card_number="199/191", language="english", condition="NM",
        is_graded=False, grade_company=None, grade=None,
        purchase_price=None, purchase_date=None, current_market_price=120.0,
        last_priced_at=None, image_url=None, photo_path=None, notes=None,
        created_at=None, product_type="card",
    )
    fields.update(overrides)
    return app_module.db.Card(**fields)


def _seed_model_run():
    run = pmdb.ModelRun(
        id=None, fitted_at="", coefficients_raw={"intercept": math.log(20.0)},
        coefficients_psa10={"intercept": math.log(80.0)},
        lifecycle_curve={}, market_index={}, psa9_fraction=0.4,
        residual_std_raw=0.2, residual_std_psa10=0.2,
        r_squared_raw=0.7, r_squared_psa10=0.6, n_cards=500,
    )
    pmdb.save_model_run(run)


def test_prediction_endpoint_returns_fair_value_for_known_card(monkeypatch):
    client = TestClient(app_module.app)
    card = _fake_card(1)
    monkeypatch.setattr(app_module.db, "get_card", lambda cid: card if cid == 1 else None)
    _seed_model_run()

    fake_card_result = type("R", (), {"rarity": "Special Illustration Rare"})()

    with patch("card_lookup.search_cards", new=AsyncMock(return_value=[fake_card_result])), \
         patch.object(app_module, "_resolve_set_release_date", new=AsyncMock(return_value="2024-11-08")):
        resp = client.get("/api/cards/1/prediction")

    assert resp.status_code == 200
    body = resp.json()
    assert body["fair_value"]["point_estimate"] > 0
    assert "breakdown" in body["fair_value"]
    assert "confidence" in body["fair_value"]


def test_prediction_endpoint_404s_for_unknown_card(monkeypatch):
    client = TestClient(app_module.app)
    monkeypatch.setattr(app_module.db, "get_card", lambda cid: None)
    resp = client.get("/api/cards/999999/prediction")
    assert resp.status_code == 404


def test_prediction_endpoint_503s_when_no_model_run_exists_yet(monkeypatch):
    client = TestClient(app_module.app)
    card = _fake_card(2)
    monkeypatch.setattr(app_module.db, "get_card", lambda cid: card if cid == 2 else None)
    resp = client.get("/api/cards/2/prediction")
    assert resp.status_code == 503
