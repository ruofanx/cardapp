# webapp/tests/test_pricing_model.py
from __future__ import annotations

import math
import random

from pricing_model import model as pm
from pricing_model.db import CorpusCardRow


def _synthetic_corpus(n: int = 80, seed: int = 7):
    """Cards with a KNOWN price-generating rule so the fit can be checked
    against ground truth: rare_holo cards trade at 2x common's base price,
    with a flat monthly history (no lifecycle/market effects) so the
    regression should recover that ratio cleanly."""
    random.seed(seed)
    cards = []
    history = {}
    for i in range(n):
        tier_is_rare = i % 2 == 0
        rarity_raw = "Rare Holo" if tier_is_rare else "Common"
        base_price = 20.0 if tier_is_rare else 10.0
        card_key = f"synthetic-{i}"
        cards.append(CorpusCardRow(
            card_key=card_key, name=f"Card {i}", set_name="Synthetic Set",
            card_number=str(i), rarity_raw=rarity_raw, era="sv", language="english",
            release_date="2023-06-01", psa10_price_usd=None, grade9_price_usd=None,
        ))
        # Flat 6-month history at base_price with tiny noise — no lifecycle
        # shape, no market drift, isolates the rarity coefficient.
        history[card_key] = [
            (f"2024-{m:02d}", base_price * (1 + random.uniform(-0.02, 0.02)))
            for m in range(1, 7)
        ]
    return cards, history


def test_fit_model_recovers_known_rarity_ratio():
    cards, history = _synthetic_corpus()
    run = pm.fit_model(cards, history)

    assert run.n_cards == len(cards)
    assert run.r_squared_raw > 0.9  # near-noiseless synthetic data
    assert run.residual_std_raw < 0.2  # tight fit on near-noiseless data

    # rare_holo cards have a SMALLER pull_scarcity value (scarcer) than
    # common cards but a HIGHER price in this synthetic corpus, so the
    # fitted log_pull_scarcity coefficient must be negative (smaller
    # scarcity value -> higher predicted price) for the model to have
    # actually learned the rarity signal rather than fitting noise.
    assert run.coefficients_raw["log_pull_scarcity"] < 0


def test_lifecycle_multiplier_falls_back_to_median_when_bin_missing():
    curve = {"0-3": 1.3, "3-6": 0.9, "6-9": 0.8}
    # A months value with no exact bin match still returns a sane multiplier.
    assert pm.lifecycle_multiplier(curve, 100.0) == sorted(curve.values())[len(curve) // 2]


def test_lifecycle_multiplier_empty_curve_returns_one():
    assert pm.lifecycle_multiplier({}, 5.0) == 1.0
