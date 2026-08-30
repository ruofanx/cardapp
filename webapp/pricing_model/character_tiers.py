"""Character-attraction tier lookup.

A curated, hand-editable tier list (S/A/B/C/D) seeded from well-known
Pokemon popularity polls and market behavior. Plain data — extend the dict
directly as new chase characters emerge. `is_trainer_art` is handled
separately since full-art Trainer cards price independent of any species.
"""
from __future__ import annotations

import re

DEFAULT_TIER = "C"
TRAINER_DEFAULT_TIER = "B"  # full-art trainers trade above the "unknown species" default

TIER_MULTIPLIER: dict[str, float] = {
    "S": 8.0,
    "A": 3.0,
    "B": 1.5,
    "C": 1.0,
    "D": 0.6,
}

# Species/character name (lowercase) -> tier. Match is on the base name with
# trailing card-type suffixes (ex, GX, V, VMAX, VSTAR, ...) stripped first.
CHARACTER_TIERS: dict[str, str] = {
    "charizard": "S",
    "umbreon": "S",
    "pikachu": "S",
    "rayquaza": "S",
    "mewtwo": "S",
    "mew": "S",
    "eevee": "A",
    "sylveon": "A",
    "gengar": "A",
    "lucario": "A",
    "greninja": "A",
    "gardevoir": "A",
    "dragonite": "A",
    "lugia": "A",
    "blastoise": "B",
    "venusaur": "B",
    "snorlax": "B",
    "gyarados": "B",
    "tyranitar": "B",
    "garchomp": "B",
}

_SUFFIX_RE = re.compile(
    r"\s+(ex|gx|v|vmax|vstar|break|prime|star|delta species|★)$",
    re.IGNORECASE,
)


def _base_name(card_name: str) -> str:
    name = card_name.strip().lower()
    # Strip one trailing suffix token at a time (e.g. "Charizard VMAX" -> "Charizard").
    prev = None
    while prev != name:
        prev = name
        name = _SUFFIX_RE.sub("", name).strip()
    return name


def get_character_tier(card_name: str, is_trainer_art: bool = False) -> str:
    if is_trainer_art:
        return TRAINER_DEFAULT_TIER
    if not card_name:
        return DEFAULT_TIER
    base = _base_name(card_name)
    return CHARACTER_TIERS.get(base, DEFAULT_TIER)
