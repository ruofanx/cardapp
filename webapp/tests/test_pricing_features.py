from __future__ import annotations

from datetime import date

from pricing_model.features import build_card_features, era_bucket, months_since_release


def test_era_bucket_boundaries():
    assert era_bucket("1999-01-09") == "vintage"
    assert era_bucket("2004-06-01") == "ecard_ex"
    assert era_bucket("2008-05-01") == "dp_pt"
    assert era_bucket("2013-02-01") == "bw_xy"
    assert era_bucket("2018-02-01") == "sm"
    assert era_bucket("2021-02-01") == "swsh"
    assert era_bucket("2023-03-31") == "sv"


def test_months_since_release_uses_as_of_date():
    assert months_since_release("2024-01-01", as_of=date(2024, 7, 1)) == 6.0


def test_build_card_features_happy_path():
    f = build_card_features(
        name="Charizard ex",
        set_name="Surging Sparks",
        card_number="199/191",
        rarity_raw="Special Illustration Rare",
        language="english",
        release_date="2024-11-08",
        cards_in_tier=10,
        as_of=date(2025, 5, 8),
    )
    assert f is not None
    assert f.canonical_rarity == "special_illustration_rare"
    assert f.character_tier == "S"
    assert f.language == "english"
    assert f.era == "sv"
    assert f.months_since_release == 6.0
    assert 0.0 < f.gem_rate <= 1.0
    assert f.pull_scarcity > 0.0


def test_build_card_features_returns_none_for_unmapped_rarity():
    f = build_card_features(
        name="Charizard ex",
        set_name="Surging Sparks",
        card_number="199/191",
        rarity_raw="Not A Real Rarity",
        language="english",
        release_date="2024-11-08",
        cards_in_tier=10,
    )
    assert f is None
