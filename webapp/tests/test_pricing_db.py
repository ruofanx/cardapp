# webapp/tests/test_pricing_db.py
from __future__ import annotations

import os

os.environ["PRICING_MODEL_DB"] = "/tmp/test_pricing_model.sqlite"

import pytest

from pricing_model import db as pmdb
from pricing_model.features import CardFeatures


@pytest.fixture(autouse=True)
def fresh_db():
    if pmdb.DB_PATH.exists():
        pmdb.DB_PATH.unlink()
    pmdb.init_db()
    yield
    if pmdb.DB_PATH.exists():
        pmdb.DB_PATH.unlink()


def _sample_features() -> CardFeatures:
    return CardFeatures(
        canonical_rarity="special_illustration_rare",
        pull_scarcity=0.05,
        gem_rate=0.4,
        character_tier="S",
        is_trainer_art=False,
        language="english",
        era="sv",
        release_date="2024-11-08",
        months_since_release=6.0,
    )


def test_upsert_and_get_card_features_roundtrip():
    pmdb.upsert_card_features(42, _sample_features())
    got = pmdb.get_card_features(42)
    assert got is not None
    assert got.canonical_rarity == "special_illustration_rare"
    assert got.character_tier == "S"


def test_upsert_card_features_overwrites_existing_row():
    pmdb.upsert_card_features(42, _sample_features())
    updated = _sample_features()
    updated.gem_rate = 0.9
    pmdb.upsert_card_features(42, updated)
    got = pmdb.get_card_features(42)
    assert got.gem_rate == 0.9


def test_corpus_card_and_history_roundtrip():
    row = pmdb.CorpusCardRow(
        card_key="sv8-199", name="Charizard ex", set_name="Surging Sparks",
        card_number="199/191", rarity_raw="Special Illustration Rare",
        era="sv", language="english", release_date="2024-11-08",
        psa10_price_usd=450.0, grade9_price_usd=180.0,
    )
    pmdb.upsert_corpus_card(row)
    pmdb.insert_corpus_history("sv8-199", [("2024-11", 100.0), ("2024-12", 90.0)])

    cards = pmdb.get_all_corpus_cards()
    assert len(cards) == 1
    assert cards[0].name == "Charizard ex"

    history = pmdb.get_corpus_history("sv8-199")
    assert history == [("2024-11", 100.0), ("2024-12", 90.0)]


def test_save_and_get_latest_model_run():
    run = pmdb.ModelRun(
        id=None, fitted_at="2026-08-01T00:00:00",
        coefficients_raw={"intercept": 1.0}, coefficients_psa10={"intercept": 2.0},
        lifecycle_curve={"0-3": 1.2, "3-6": 0.9}, market_index={"2024-11": 1.0},
        psa9_fraction=0.4, residual_std_raw=0.3, residual_std_psa10=0.35,
        r_squared_raw=0.6, r_squared_psa10=0.55, n_cards=500,
    )
    run_id = pmdb.save_model_run(run)
    assert run_id > 0
    latest = pmdb.get_latest_model_run()
    assert latest is not None
    assert latest.coefficients_raw == {"intercept": 1.0}
    assert latest.n_cards == 500


def test_get_latest_model_run_returns_none_when_empty():
    assert pmdb.get_latest_model_run() is None
