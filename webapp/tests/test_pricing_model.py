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


def _shift_month(year: int, month: int, delta_months: int) -> tuple[int, int]:
    total = (year * 12 + (month - 1)) - delta_months
    return total // 12, total % 12 + 1


def _synthetic_corpus_with_lifecycle(n_cards: int = 200, seed: int = 11):
    """Cards with a KNOWN non-flat lifecycle curve and a KNOWN +1%/month
    market trend, with STAGGERED release ages so different cards cover
    different age ranges within the same 12 calendar months -- this mirrors
    PriceCharting's calendar-anchored (not release-anchored) chart history
    and is exactly the shape that breaks a naive per-card-first-point
    anchor. A correct estimator must recover the true curve's shape even
    though no single card's own series spans more than 12 months of age."""
    import random
    random.seed(seed)

    true_curve = {
        "0-3": 1.3, "3-6": 1.05, "6-9": 0.9, "9-12": 0.85,
        "12-18": 0.82, "18-24": 0.85, "24-36": 0.95, "36+": 1.1,
    }

    def true_multiplier(months: float) -> float:
        for lo, hi in [(0, 3), (3, 6), (6, 9), (9, 12), (12, 18), (18, 24), (24, 36), (36, 9999)]:
            if lo <= months < hi:
                label = f"{lo:g}-{hi:g}" if hi < 9999 else f"{lo:g}+"
                return true_curve[label]
        return 1.0

    calendar_months = [f"2024-{m:02d}" for m in range(1, 13)]
    cards = []
    history = {}
    for i in range(n_cards):
        card_key = f"lc-{i}"
        age_at_window_start = random.randint(0, 40)
        ry, rm = _shift_month(2024, 1, age_at_window_start)
        cards.append(CorpusCardRow(
            card_key=card_key, name=f"Card {i}", set_name="Lifecycle Set",
            card_number=str(i), rarity_raw="Rare Holo", era="sv", language="english",
            release_date=f"{ry:04d}-{rm:02d}-01", psa10_price_usd=None, grade9_price_usd=None,
        ))
        points = []
        for month_idx, month in enumerate(calendar_months):
            months_since = age_at_window_start + month_idx
            true_price = 10.0 * (1.01 ** month_idx) * true_multiplier(months_since)
            noise = random.uniform(0.97, 1.03)
            points.append((month, true_price * noise))
        history[card_key] = points
    return cards, history, true_curve


def test_fit_model_recovers_non_flat_lifecycle_shape_under_staggered_releases():
    cards, history, true_curve = _synthetic_corpus_with_lifecycle()
    run = pm.fit_model(cards, history)

    curve = run.lifecycle_curve
    # Exact-value recovery isn't robust to noise/bin-coverage variance, but
    # the SHAPE must survive: the hype peak (0-3, true 1.3) must read
    # meaningfully higher than the trough (9-12, true 0.85), not flat or
    # inverted -- this is exactly the failure mode a first-point-anchored
    # estimator produces (it recovers ~1.0 for both).
    assert "0-3" in curve and "9-12" in curve
    assert curve["0-3"] > curve["9-12"] * 1.2
    # The recovery bin (36+, true 1.1) must likewise read higher than the
    # trough, not collapse to the same level.
    if "36+" in curve:
        assert curve["36+"] > curve["9-12"] * 1.1
