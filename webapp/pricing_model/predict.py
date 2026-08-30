# webapp/pricing_model/predict.py
"""Turns a fitted ModelRun + a card's CardFeatures into a decomposable
prediction: point estimate, confidence band, and a per-factor multiplier
breakdown (so the UI can show "$142 = $8 base x 5.2 rarity x ..." exactly
as designed in the spec).
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

from pricing_model.db import ModelRun
from pricing_model.features import CardFeatures
from pricing_model.model import lifecycle_multiplier

# Confidence-interval z-multiplier (~80% interval) and per-language widening.
Z = 1.28
LANGUAGE_BAND_WIDENING = {"english": 1.0, "japanese": 1.4, "chinese": 2.0}


@dataclass
class Prediction:
    point_estimate: float
    low: float
    high: float
    breakdown: dict[str, float] = field(default_factory=dict)
    lifecycle_multiplier: float = 1.0
    r_squared: float = 0.0


def _fundamentals_log_price(coefficients: dict[str, float], features: CardFeatures,
                             include_gem: bool) -> tuple[float, dict[str, float]]:
    breakdown: dict[str, float] = {}
    total = 0.0

    def add(key: str, value: float | None = None):
        nonlocal total
        coef = coefficients.get(key)
        if coef is None:
            return
        contribution = coef * (value if value is not None else 1.0)
        total += contribution
        breakdown[key] = math.exp(contribution)

    add("intercept")
    add(f"era:{features.era}")
    add(f"lang:{features.language}")
    add("log_pull_scarcity", math.log(max(features.pull_scarcity, 1e-6)))
    add(f"char:{features.character_tier}")
    if include_gem:
        add("log_inv_gem_rate", math.log(1.0 / max(features.gem_rate, 1e-6)))

    return total, breakdown


def _band(point: float, residual_std: float, language: str) -> tuple[float, float]:
    widen = LANGUAGE_BAND_WIDENING.get(language, 2.0)
    spread = math.exp(Z * residual_std * widen)
    return point / spread, point * spread


def predict_raw_price(features: CardFeatures, run: ModelRun) -> Prediction:
    log_price, breakdown = _fundamentals_log_price(
        run.coefficients_raw, features, include_gem=False,
    )
    fundamentals_price = math.exp(log_price)
    mult = lifecycle_multiplier(run.lifecycle_curve, features.months_since_release)
    point = fundamentals_price * mult
    low, high = _band(point, run.residual_std_raw, features.language)
    return Prediction(
        point_estimate=point, low=low, high=high, breakdown=breakdown,
        lifecycle_multiplier=mult, r_squared=run.r_squared_raw,
    )


def predict_psa10_price(features: CardFeatures, run: ModelRun) -> Prediction | None:
    if not run.coefficients_psa10:
        return None
    log_price, breakdown = _fundamentals_log_price(
        run.coefficients_psa10, features, include_gem=True,
    )
    fundamentals_price = math.exp(log_price)
    mult = lifecycle_multiplier(run.lifecycle_curve, features.months_since_release)
    point = fundamentals_price * mult
    low, high = _band(point, run.residual_std_psa10, features.language)
    return Prediction(
        point_estimate=point, low=low, high=high, breakdown=breakdown,
        lifecycle_multiplier=mult, r_squared=run.r_squared_psa10,
    )


DEFAULT_GRADING_FEE_USD = 25.0


@dataclass
class GradeEV:
    expected_value: float
    raw_price: float
    predicted_psa10: float
    predicted_psa9: float
    gem_rate: float
    grading_fee: float
    worth_grading: bool


def grade_worthiness(features: CardFeatures, run: ModelRun,
                      grading_fee: float = DEFAULT_GRADING_FEE_USD) -> GradeEV | None:
    psa10_pred = predict_psa10_price(features, run)
    if psa10_pred is None:
        return None
    raw_pred = predict_raw_price(features, run)

    predicted_psa10 = psa10_pred.point_estimate
    predicted_psa9 = predicted_psa10 * run.psa9_fraction
    gem = features.gem_rate

    ev = (gem * predicted_psa10 + (1 - gem) * predicted_psa9) - grading_fee - raw_pred.point_estimate

    # Worth grading needs a real margin, not just EV > 0 — grading has
    # non-priced friction (turnaround time, shipping risk).
    margin_threshold = grading_fee * 0.5
    return GradeEV(
        expected_value=ev, raw_price=raw_pred.point_estimate,
        predicted_psa10=predicted_psa10, predicted_psa9=predicted_psa9,
        gem_rate=gem, grading_fee=grading_fee,
        worth_grading=ev > margin_threshold,
    )
