from __future__ import annotations

from pricing_model import rarity_map


def test_normalizes_known_en_rarities():
    assert rarity_map.normalize_rarity("Common") == "common"
    assert rarity_map.normalize_rarity("Rare Holo VMAX") == "rare_holo"
    assert rarity_map.normalize_rarity("Special Illustration Rare") == "special_illustration_rare"
    assert rarity_map.normalize_rarity("Hyper Rare") == "gold_secret"


def test_normalization_is_case_and_whitespace_insensitive():
    assert rarity_map.normalize_rarity("  rare ultra  ") == "ultra_rare"


def test_unmapped_rarity_returns_none():
    assert rarity_map.normalize_rarity("Some Future Rarity Nobody Has Seen") is None
    assert rarity_map.normalize_rarity(None) is None


def test_rarity_rank_is_monotonic_with_ladder_order():
    assert rarity_map.RARITY_RANK["common"] < rarity_map.RARITY_RANK["rare_holo"]
    assert rarity_map.RARITY_RANK["rare_holo"] < rarity_map.RARITY_RANK["ultra_rare"]
    assert rarity_map.RARITY_RANK["ultra_rare"] < rarity_map.RARITY_RANK["illustration_rare"]
    assert rarity_map.RARITY_RANK["special_illustration_rare"] < rarity_map.RARITY_RANK["gold_secret"]
