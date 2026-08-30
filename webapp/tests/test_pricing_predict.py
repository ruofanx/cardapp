# webapp/tests/test_pricing_predict.py
from __future__ import annotations

import math

from pricing_model.db import ModelRun
from pricing_model.features import CardFeatures
from pricing_model.predict import predict_psa10_price, predict_raw_price


def _sample_run() -> ModelRun:
    return ModelRun(
        id=1, fitted_at="2026-08-01T00:00:00",
        coefficients_raw={
            "intercept": math.log(10.0), "era:sv": 0.1, "lang:japanese": -0.2,
            "lang:chinese": -0.5, "log_pull_scarcity": 0.3,
            "char:S": math.log(8.0), "char:A": math.log(3.0),
            "char:B": math.log(1.5), "char:D": math.log(0.6),
        },
        coefficients_psa10={
            "intercept": math.log(40.0), "era:sv": 0.1, "lang:japanese": -0.2,
            "lang:chinese": -0.5, "log_pull_scarcity": 0.3,
            "char:S": math.log(8.0), "char:A": math.log(3.0),
            "char:B": math.log(1.5), "char:D": math.log(0.6),
            "log_inv_gem_rate": 0.4,
        },
        lifecycle_curve={"0-3": 1.4, "3-6": 1.0, "6-9": 0.85},
        market_index={"2026-07": 0.9, "2026-08": 0.95},
        psa9_fraction=0.4, residual_std_raw=0.2, residual_std_psa10=0.25,
        r_squared_raw=0.7, r_squared_psa10=0.65, n_cards=1000,
    )


def _sample_features(**overrides) -> CardFeatures:
    base = dict(
        canonical_rarity="special_illustration_rare", pull_scarcity=0.1,
        gem_rate=0.4, character_tier="S", is_trainer_art=False,
        language="english", era="sv", release_date="2026-02-08",
        months_since_release=6.0,
    )
    base.update(overrides)
    return CardFeatures(**base)


def test_predict_raw_price_is_positive_and_has_breakdown():
    pred = predict_raw_price(_sample_features(), _sample_run())
    assert pred.point_estimate > 0
    assert pred.low < pred.point_estimate < pred.high
    assert "char:S" in pred.breakdown
    assert pred.breakdown["char:S"] == math.exp(math.log(8.0))


def test_predict_raw_price_widens_band_for_chinese_language():
    en_pred = predict_raw_price(_sample_features(language="english"), _sample_run())
    cn_pred = predict_raw_price(_sample_features(language="chinese"), _sample_run())
    en_width = en_pred.high - en_pred.low
    cn_width = cn_pred.high - cn_pred.low
    assert cn_width > en_width


def test_predict_psa10_price_includes_gem_rate_factor():
    pred = predict_psa10_price(_sample_features(), _sample_run())
    assert pred is not None
    assert "log_inv_gem_rate" in pred.breakdown


def test_predict_psa10_price_returns_none_without_psa10_coefficients():
    run = _sample_run()
    run.coefficients_psa10 = {}
    assert predict_psa10_price(_sample_features(), run) is None
