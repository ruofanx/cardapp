# webapp/tests/test_pricing_prediction_api.py
from __future__ import annotations

import math
import os
from types import SimpleNamespace
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

    # set_name must match card.set_name ("Surging Sparks", from _fake_card's
    # defaults) -- app.py's post-Fix-2 set-matching check requires it before
    # accepting this candidate's rarity at all.
    fake_card_result = type("R", (), {"rarity": "Special Illustration Rare", "set_name": "Surging Sparks"})()

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


# ---------------------------------------------------------------------------
# Fix 2 regression: _resolve_rarity_and_release_date's EN branch must reject
# a candidate from the WRONG set rather than silently attaching its rarity.
# Mocking httpx directly (not card_lookup.search_cards) so search_cards's
# real free-text query-building runs, exercising the exact code path that
# let a mismatched-set candidate through under the old `set:"X"` Lucene-
# syntax-as-free-text bug.
# ---------------------------------------------------------------------------

_TCG_SETS_RESPONSE = {"data": [{"releaseDate": "2024/11/08"}]}


def _tcg_cards_response(cards: list[dict]) -> dict:
    return {"data": cards}


def _fake_httpx_get(cards_payload: dict):
    """Builds a fake `httpx.AsyncClient.get` side_effect that routes to
    either the /cards search response or the /sets release-date response
    based on the requested URL, mirroring test_pricing_corpus.py's pattern
    of mocking httpx.AsyncClient.get directly."""
    async def fake_get(*args, **kwargs):
        url = args[0] if args else kwargs.get("url", "")
        if "/sets" in url:
            return SimpleNamespace(
                status_code=200, json=lambda: _TCG_SETS_RESPONSE, raise_for_status=lambda: None,
            )
        return SimpleNamespace(
            status_code=200, json=lambda: cards_payload, raise_for_status=lambda: None,
        )
    return fake_get


def test_prediction_endpoint_only_accepts_candidate_matching_cards_set(monkeypatch):
    """Two candidates come back for the free-text query: one from the
    card's actual set ("Surging Sparks"), one from a different set. Only
    the matching one's rarity may be cached/used."""
    client = TestClient(app_module.app)
    card = _fake_card(3)  # set_name="Surging Sparks" per _fake_card defaults
    monkeypatch.setattr(app_module.db, "get_card", lambda cid: card if cid == 3 else None)
    _seed_model_run()

    cards_payload = _tcg_cards_response([
        {
            "name": "Charizard ex", "number": "1",
            "rarity": "Common",
            "set": {"id": "sv1", "name": "Some Other Set", "releaseDate": "2020/01/01"},
        },
        {
            "name": "Charizard ex", "number": "199",
            "rarity": "Special Illustration Rare",
            "set": {"id": "sv8", "name": "Surging Sparks", "releaseDate": "2024/11/08"},
        },
    ])

    with patch("httpx.AsyncClient.get", new=AsyncMock(side_effect=_fake_httpx_get(cards_payload))):
        resp = client.get("/api/cards/3/prediction")

    assert resp.status_code == 200
    features = pmdb.get_card_features(3)
    assert features is not None
    assert features.canonical_rarity == "special_illustration_rare"  # the MATCHING candidate's rarity
    assert features.canonical_rarity != "common"  # never the wrong-set candidate's rarity


def test_prediction_endpoint_422s_when_no_candidate_matches_cards_set(monkeypatch):
    """All candidates come from a DIFFERENT set than the card's own —
    endpoint must degrade to 422, not silently accept a wrong-set rarity."""
    client = TestClient(app_module.app)
    card = _fake_card(4)
    monkeypatch.setattr(app_module.db, "get_card", lambda cid: card if cid == 4 else None)
    _seed_model_run()

    cards_payload = _tcg_cards_response([
        {
            "name": "Charizard ex", "number": "1",
            "rarity": "Common",
            "set": {"id": "sv1", "name": "Some Other Set", "releaseDate": "2020/01/01"},
        },
    ])

    with patch("httpx.AsyncClient.get", new=AsyncMock(side_effect=_fake_httpx_get(cards_payload))):
        resp = client.get("/api/cards/4/prediction")

    assert resp.status_code == 422
    assert pmdb.get_card_features(4) is None
