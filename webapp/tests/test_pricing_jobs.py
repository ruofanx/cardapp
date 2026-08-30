# webapp/tests/test_pricing_jobs.py
from __future__ import annotations

import asyncio
import os
from unittest.mock import AsyncMock, patch

os.environ["PRICING_MODEL_DB"] = "/tmp/test_pricing_jobs.sqlite"

from pricing_model import db as pmdb, jobs


def _fresh_db():
    if pmdb.DB_PATH.exists():
        pmdb.DB_PATH.unlink()
    pmdb.init_db()


def _seed_minimal_corpus():
    for i in range(20):
        rarity = "Rare Holo" if i % 2 == 0 else "Common"
        card_key = f"seed-{i}"
        pmdb.upsert_corpus_card(pmdb.CorpusCardRow(
            card_key=card_key, name=f"Card {i}", set_name="Seed Set",
            card_number=str(i), rarity_raw=rarity, era="sv", language="english",
            release_date="2023-01-01",
        ))
        pmdb.insert_corpus_history(card_key, [("2024-01", 10.0 + i), ("2024-02", 10.5 + i)])


def test_monthly_refit_keeps_previous_run_when_new_fit_is_worse():
    _fresh_db()
    _seed_minimal_corpus()

    good_run = pmdb.ModelRun(
        id=None, fitted_at="", coefficients_raw={"intercept": 1.0}, coefficients_psa10={},
        lifecycle_curve={}, market_index={}, psa9_fraction=0.4,
        residual_std_raw=0.05, residual_std_psa10=0.0,
        r_squared_raw=0.95, r_squared_psa10=0.0, n_cards=1000,
    )
    pmdb.save_model_run(good_run)

    worse_fit_run = pmdb.ModelRun(
        id=None, fitted_at="", coefficients_raw={"intercept": 0.5}, coefficients_psa10={},
        lifecycle_curve={}, market_index={}, psa9_fraction=0.4,
        residual_std_raw=0.9, residual_std_psa10=0.0,
        r_squared_raw=0.1, r_squared_psa10=0.0, n_cards=20,
    )

    with patch("pricing_model.corpus.harvest_all", new=AsyncMock(return_value={"total_cards": 20})), \
         patch("pricing_model.model.fit_model", return_value=worse_fit_run):
        result = asyncio.run(jobs.monthly_refit())

    assert result["kept_previous"] is True
    latest = pmdb.get_latest_model_run()
    assert latest.r_squared_raw == 0.95  # previous, better run stayed active


def test_monthly_refit_accepts_new_fit_when_it_is_better():
    _fresh_db()
    _seed_minimal_corpus()

    better_run = pmdb.ModelRun(
        id=None, fitted_at="", coefficients_raw={"intercept": 1.0}, coefficients_psa10={},
        lifecycle_curve={}, market_index={}, psa9_fraction=0.4,
        residual_std_raw=0.05, residual_std_psa10=0.0,
        r_squared_raw=0.9, r_squared_psa10=0.0, n_cards=1000,
    )

    with patch("pricing_model.corpus.harvest_all", new=AsyncMock(return_value={"total_cards": 20})), \
         patch("pricing_model.model.fit_model", return_value=better_run):
        result = asyncio.run(jobs.monthly_refit())

    assert result["kept_previous"] is False
    latest = pmdb.get_latest_model_run()
    assert latest.r_squared_raw == 0.9


def test_monthly_refit_survives_harvest_failure_and_keeps_previous_run():
    """Fix 7 (final-review pass): a harvest/fit exception must not crash the
    scheduled job -- it should log and report kept_previous, leaving whatever
    model run was already active untouched."""
    _fresh_db()
    _seed_minimal_corpus()

    existing_run = pmdb.ModelRun(
        id=None, fitted_at="", coefficients_raw={"intercept": 1.0}, coefficients_psa10={},
        lifecycle_curve={}, market_index={}, psa9_fraction=0.4,
        residual_std_raw=0.05, residual_std_psa10=0.0,
        r_squared_raw=0.8, r_squared_psa10=0.0, n_cards=500,
    )
    pmdb.save_model_run(existing_run)

    with patch("pricing_model.corpus.harvest_all", new=AsyncMock(side_effect=RuntimeError("harvest boom"))):
        result = asyncio.run(jobs.monthly_refit())

    assert result["kept_previous"] is True
    assert "harvest boom" in result["error"]
    latest = pmdb.get_latest_model_run()
    assert latest.r_squared_raw == 0.8  # untouched
