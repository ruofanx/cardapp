from __future__ import annotations

import math

from pricing_model.db import ModelRun
from pricing_model.features import CardFeatures
from pricing_model.predict import grade_worthiness


def _run_with_high_upside() -> ModelRun:
    return ModelRun(
        id=1, fitted_at="", coefficients_raw={"intercept": math.log(10.0)},
        coefficients_psa10={"intercept": math.log(200.0)},
        lifecycle_curve={}, market_index={}, psa9_fraction=0.5,
        residual_std_raw=0.1, residual_std_psa10=0.1,
        r_squared_raw=0.8, r_squared_psa10=0.8, n_cards=500,
    )


def _run_with_no_upside() -> ModelRun:
    return ModelRun(
        id=1, fitted_at="", coefficients_raw={"intercept": math.log(50.0)},
        coefficients_psa10={"intercept": math.log(55.0)},
        lifecycle_curve={}, market_index={}, psa9_fraction=0.5,
        residual_std_raw=0.1, residual_std_psa10=0.1,
        r_squared_raw=0.8, r_squared_psa10=0.8, n_cards=500,
    )


def _features() -> CardFeatures:
    return CardFeatures(
        canonical_rarity="ultra_rare", pull_scarcity=0.5, gem_rate=0.5,
        character_tier="C", is_trainer_art=False, language="english",
        era="sv", release_date="2026-01-01", months_since_release=1.0,
    )


def test_high_upside_card_is_worth_grading():
    ev = grade_worthiness(_features(), _run_with_high_upside(), grading_fee=25.0)
    assert ev is not None
    assert ev.expected_value > 0
    assert ev.worth_grading is True


def test_flat_upside_card_is_not_worth_grading():
    ev = grade_worthiness(_features(), _run_with_no_upside(), grading_fee=25.0)
    assert ev is not None
    assert ev.worth_grading is False


def test_returns_none_when_no_psa10_model():
    run = _run_with_high_upside()
    run.coefficients_psa10 = {}
    assert grade_worthiness(_features(), run) is None
