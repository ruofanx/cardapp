"""Combines rarity/pull-rate/gem-rate/character-tier config into one
per-card feature snapshot. Pure functions — no I/O, no DB. Callers resolve
identity fields (name, set_name, card_number, rarity_raw, release_date)
from the existing lookup modules (card_lookup / tcgdex_lookup) or the
training corpus before calling build_card_features().
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from pricing_model import character_tiers, gem_rate, pull_rates, rarity_map

# (year, month) the era starts, ascending. era_bucket finds the last bucket
# whose start is <= the card's release date.
_ERA_STARTS: list[tuple[str, tuple[int, int]]] = [
    ("vintage", (1996, 1)),
    ("ecard_ex", (2002, 1)),
    ("dp_pt", (2007, 1)),
    ("bw_xy", (2011, 1)),
    ("sm", (2017, 1)),
    ("swsh", (2020, 1)),
    ("sv", (2023, 1)),
]


def era_bucket(release_date: str) -> str:
    y, m = int(release_date[0:4]), int(release_date[5:7])
    era = _ERA_STARTS[0][0]
    for name, (sy, sm) in _ERA_STARTS:
        if (y, m) >= (sy, sm):
            era = name
        else:
            break
    return era


def months_since_release(release_date: str, as_of: date | None = None) -> float:
    as_of = as_of or date.today()
    y, m, d = int(release_date[0:4]), int(release_date[5:7]), int(release_date[8:10])
    released = date(y, m, d)
    return (as_of.year - released.year) * 12 + (as_of.month - released.month) \
        + (as_of.day - released.day) / 30.0


@dataclass
class CardFeatures:
    canonical_rarity: str
    pull_scarcity: float
    gem_rate: float
    character_tier: str
    is_trainer_art: bool
    language: str
    era: str
    release_date: str
    months_since_release: float


def build_card_features(
    *,
    name: str,
    set_name: str,
    card_number: str,
    rarity_raw: str | None,
    language: str,
    release_date: str,
    cards_in_tier: int,
    is_trainer_art: bool = False,
    surface: str = "standard",
    as_of: date | None = None,
) -> CardFeatures | None:
    tier = rarity_map.normalize_rarity(rarity_raw)
    if tier is None:
        return None
    era = era_bucket(release_date)
    scarcity = pull_rates.pull_rate_scarcity(era, tier, set_name, cards_in_tier)
    gem = gem_rate.estimate_gem_rate(era, tier, surface)
    char_tier = character_tiers.get_character_tier(name, is_trainer_art=is_trainer_art)
    return CardFeatures(
        canonical_rarity=tier,
        pull_scarcity=scarcity,
        gem_rate=gem,
        character_tier=char_tier,
        is_trainer_art=is_trainer_art,
        language=language,
        era=era,
        release_date=release_date,
        months_since_release=months_since_release(release_date, as_of=as_of),
    )
