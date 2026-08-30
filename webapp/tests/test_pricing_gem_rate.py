from __future__ import annotations

from pricing_model import gem_rate


def test_textured_foil_gems_worse_than_standard():
    standard = gem_rate.estimate_gem_rate("sv", "special_illustration_rare", "standard")
    textured = gem_rate.estimate_gem_rate("sv", "special_illustration_rare", "textured")
    assert textured < standard


def test_returns_value_in_valid_probability_range():
    for era in ("sv", "swsh", "vintage", "totally_unknown_era"):
        for tier in ("common", "gold_secret", "not_a_real_tier"):
            v = gem_rate.estimate_gem_rate(era, tier, "standard")
            assert 0.0 < v <= 1.0


def test_unmapped_tier_falls_back_to_tier_only_default():
    # No crash, no KeyError, returns the coarse fallback.
    v = gem_rate.estimate_gem_rate("sv", "not_a_real_tier", "standard")
    assert v == gem_rate.DEFAULT_GEM_RATE
