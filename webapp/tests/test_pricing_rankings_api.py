# webapp/tests/test_pricing_rankings_api.py
from __future__ import annotations

import math
import os

os.environ["PRICING_MODEL_DB"] = "/tmp/test_rankings_api_pm.sqlite"

import pytest
from fastapi.testclient import TestClient

import app as app_module
import pricing_model.db as pmdb
from pricing_model.features import CardFeatures


@pytest.fixture(autouse=True)
def fresh_pricing_db():
    if pmdb.DB_PATH.exists():
        pmdb.DB_PATH.unlink()
    pmdb.init_db()
    yield


def _fake_card(card_id: int, **overrides):
    fields = dict(
        id=card_id, user_id=1, name="Card", set_name="Set", card_number="1",
        language="english", condition="NM", is_graded=False,
        grade_company=None, grade=None, purchase_price=None, purchase_date=None,
        current_market_price=None, last_priced_at=None, image_url=None,
        photo_path=None, notes=None, created_at=None, product_type="card",
    )
    fields.update(overrides)
    return app_module.db.Card(**fields)


def test_rankings_sorts_by_undervalued_by_default(monkeypatch):
    client = TestClient(app_module.app)

    cheap = _fake_card(1, name="Card Cheap", current_market_price=5.0)   # << fair value -> undervalued
    pricey = _fake_card(2, name="Card Pricey", card_number="2", current_market_price=500.0)  # >> fair value -> overvalued
    monkeypatch.setattr(app_module.db, "list_cards", lambda uid: [cheap, pricey])

    for c in (cheap, pricey):
        pmdb.upsert_card_features(c.id, CardFeatures(
            canonical_rarity="rare_holo", pull_scarcity=1.0, gem_rate=0.5,
            character_tier="C", is_trainer_art=False, language="english",
            era="sv", release_date="2024-01-01", months_since_release=12.0,
        ))
    run = pmdb.ModelRun(
        id=None, fitted_at="", coefficients_raw={"intercept": math.log(50.0)},
        coefficients_psa10={}, lifecycle_curve={}, market_index={},
        psa9_fraction=0.4, residual_std_raw=0.2, residual_std_psa10=0.0,
        r_squared_raw=0.7, r_squared_psa10=0.0, n_cards=500,
    )
    pmdb.save_model_run(run)

    resp = client.get("/api/users/1/rankings?sort=undervalued")
    assert resp.status_code == 200
    body = resp.json()
    ids_in_order = [row["card_id"] for row in body["rankings"]]
    assert ids_in_order[0] == cheap.id  # most undervalued first


def test_rankings_skips_cards_without_features_silently(monkeypatch):
    client = TestClient(app_module.app)
    no_features_card = _fake_card(1, name="No Features Card")
    monkeypatch.setattr(app_module.db, "list_cards", lambda uid: [no_features_card])

    run = pmdb.ModelRun(
        id=None, fitted_at="", coefficients_raw={"intercept": 1.0}, coefficients_psa10={},
        lifecycle_curve={}, market_index={}, psa9_fraction=0.4,
        residual_std_raw=0.2, residual_std_psa10=0.0, r_squared_raw=0.7,
        r_squared_psa10=0.0, n_cards=500,
    )
    pmdb.save_model_run(run)

    resp = client.get("/api/users/1/rankings")
    assert resp.status_code == 200
    assert resp.json()["rankings"] == []
