"""Derived pull-rate scarcity — no official pull-rate data exists, so this
is a config table of community-known "expected copies per booster box" by
(era, canonical rarity tier), overridable per set for known outliers
(god-pack sets, special structures). See spec section 2.
"""
from __future__ import annotations

# copies expected per booster box, by era then canonical tier. Coarser tiers
# (common/uncommon/rare) are deliberately omitted from an era's table when
# they don't meaningfully affect scarcity-driven price — DEFAULT_TIER_RATE
# covers them.
DEFAULT_TIER_RATE = 40.0  # generic non-chase card: assume plentiful

_ERA_TABLE: dict[str, dict[str, float]] = {
    "sv": {
        "rare_holo": 3.0,
        "ultra_rare": 1.2,
        "illustration_rare": 0.6,
        "special_illustration_rare": 0.35,
        "gold_secret": 0.12,
    },
    "swsh": {
        "rare_holo": 3.0,
        "ultra_rare": 1.0,
        "illustration_rare": 0.5,
        "special_illustration_rare": 0.3,
        "gold_secret": 0.1,
    },
    "sm": {
        "rare_holo": 3.0,
        "ultra_rare": 1.0,
        "gold_secret": 0.15,
    },
    "bw_xy": {
        "rare_holo": 3.5,
        "ultra_rare": 1.2,
        "gold_secret": 0.2,
    },
    "dp_pt": {
        "rare_holo": 4.0,
        "ultra_rare": 1.5,
    },
    "ecard_ex": {
        "rare_holo": 4.0,
        "ultra_rare": 1.5,
    },
    "vintage": {
        "rare_holo": 4.0,
        "ultra_rare": 1.5,
    },
}

# Per-set overrides for known outlier pull structures (god packs, special
# box-only inserts). Keyed by lowercased set_name substring.
SET_OVERRIDES: dict[str, dict[str, float]] = {
    "terastal festival": {
        "special_illustration_rare": 0.15,
        "gold_secret": 0.04,
    },
}


def _era_table(era: str) -> dict[str, float]:
    return _ERA_TABLE.get(era, _ERA_TABLE["sv"])


def pull_rate_scarcity(era: str, tier: str, set_name: str, cards_in_tier: int) -> float:
    """Effective copies-per-card = (copies expected per box for this tier)
    / (number of cards sharing that tier in the set). Smaller = scarcer.
    """
    table = _era_table(era)
    set_key = (set_name or "").strip().lower()
    override = next(
        (ov for key, ov in SET_OVERRIDES.items() if key in set_key),
        None,
    )
    copies_per_box = (override or {}).get(tier, table.get(tier, DEFAULT_TIER_RATE))
    n = max(cards_in_tier, 1)
    return copies_per_box / n
