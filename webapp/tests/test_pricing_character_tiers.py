from __future__ import annotations

from pricing_model import character_tiers


def test_known_top_tier_character():
    assert character_tiers.get_character_tier("Charizard") == "S"
    assert character_tiers.get_character_tier("Umbreon") == "S"


def test_unknown_character_defaults_to_c():
    assert character_tiers.get_character_tier("Some Obscure Mon Nobody Chases") == "C"


def test_matches_on_base_name_ignoring_suffixes():
    # "Charizard ex" / "Charizard VMAX" should still hit the Charizard entry.
    assert character_tiers.get_character_tier("Charizard ex") == "S"
    assert character_tiers.get_character_tier("Charizard VMAX") == "S"


def test_trainer_art_uses_trainer_tier_not_species_lookup():
    # A trainer full-art's "character" isn't a Pokemon species at all.
    tier = character_tiers.get_character_tier("Boss's Orders", is_trainer_art=True)
    assert tier in ("S", "A", "B", "C", "D")
