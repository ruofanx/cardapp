from __future__ import annotations

from pricing_model import pull_rates


def test_higher_tier_is_scarcer_within_same_era():
    common = pull_rates.pull_rate_scarcity("sv", "common", "Surging Sparks", cards_in_tier=20)
    sir = pull_rates.pull_rate_scarcity("sv", "special_illustration_rare", "Surging Sparks", cards_in_tier=10)
    assert sir < common  # scarcer = smaller effective copies-per-card


def test_set_override_changes_scarcity():
    default = pull_rates.pull_rate_scarcity("sv", "gold_secret", "Some Ordinary Set", cards_in_tier=5)
    override = pull_rates.pull_rate_scarcity("sv", "gold_secret", "Terastal Festival", cards_in_tier=5)
    assert override != default


def test_unknown_era_falls_back_to_default_table():
    # Doesn't raise; returns a positive scarcity value using the fallback era.
    value = pull_rates.pull_rate_scarcity("unknown_era", "rare_holo", "Some Set", cards_in_tier=8)
    assert value > 0


def test_more_cards_competing_in_slot_reduces_effective_pulls_per_card():
    fewer = pull_rates.pull_rate_scarcity("sv", "ultra_rare", "Set A", cards_in_tier=5)
    more = pull_rates.pull_rate_scarcity("sv", "ultra_rare", "Set A", cards_in_tier=20)
    assert more < fewer
