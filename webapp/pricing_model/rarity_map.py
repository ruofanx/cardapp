"""Canonical rarity ladder + normalization.

Card sources return ~40 era/language-specific rarity strings (Pokemon TCG
API for EN, TCGdex for JP/CN). This collapses them onto one scarcity ladder
so the model has a single ordinal rarity signal regardless of source.
Unmapped strings are logged and return None — callers must not guess a
rarity for a card the table doesn't recognize (see spec: "no prediction
rather than a wrong one").
"""
from __future__ import annotations

import logging

log = logging.getLogger(__name__)

# Ordered scarcity ladder, low -> high.
CANONICAL_RARITY_LADDER: list[str] = [
    "common",
    "uncommon",
    "rare",
    "rare_holo",
    "ultra_rare",
    "illustration_rare",
    "special_illustration_rare",
    "gold_secret",
]

RARITY_RANK: dict[str, int] = {tier: i for i, tier in enumerate(CANONICAL_RARITY_LADDER)}

# Raw rarity string (lowercased) -> canonical tier. Extend this table when
# normalize_rarity() logs an unmapped string against real card data.
RARITY_ALIASES: dict[str, str] = {
    # Pokemon TCG API (EN)
    "common": "common",
    "uncommon": "uncommon",
    "rare": "rare",
    "rare holo": "rare_holo",
    "rare holo ex": "rare_holo",
    "rare holo gx": "rare_holo",
    "rare holo v": "rare_holo",
    "rare holo vmax": "rare_holo",
    "rare holo vstar": "rare_holo",
    "rare break": "rare_holo",
    "rare prime": "rare_holo",
    "radiant rare": "ultra_rare",
    "amazing rare": "ultra_rare",
    "rare ultra": "ultra_rare",
    "ultra rare": "ultra_rare",
    "rare shiny": "ultra_rare",
    "rare shiny gx": "ultra_rare",
    "double rare": "ultra_rare",
    "ace spec rare": "ultra_rare",
    "trainer gallery rare holo": "ultra_rare",
    "rare prism star": "ultra_rare",
    "rare rainbow": "special_illustration_rare",
    "illustration rare": "illustration_rare",
    "special illustration rare": "special_illustration_rare",
    "hyper rare": "gold_secret",
    "rare secret": "gold_secret",
    "classic collection": "ultra_rare",
    "promo": "rare_holo",
    # TCGdex (JP) — common hobby shorthand for JP-native rarity tiers.
    "ar": "illustration_rare",
    "sar": "special_illustration_rare",
    "sr": "ultra_rare",
    "hr": "gold_secret",
    "ur": "gold_secret",
    "ssr": "gold_secret",
}


def normalize_rarity(raw: str | None) -> str | None:
    if not raw:
        return None
    key = raw.strip().lower()
    tier = RARITY_ALIASES.get(key)
    if tier is None:
        log.warning("pricing_model.rarity_map: unmapped rarity string %r", raw)
    return tier
