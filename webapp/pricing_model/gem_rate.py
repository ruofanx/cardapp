"""Derived grade-scarcity (gem rate) estimate.

PSA and GemRate population pages are both Cloudflare-protected (verified
403/managed-challenge on both during planning — see spec "Data policy").
No free source of real population data exists, so gem_rate is a config
table estimate, same treatment as pull_rates: keyed by (era, canonical
rarity tier, surface type), seeded from well-known community gem-rate
ranges. `card_features.gem_rate` stays generically named so a future paid
source can populate it with real data without a schema change.
"""
from __future__ import annotations

# Fallback when (era, tier) isn't in the table at all.
DEFAULT_GEM_RATE = 0.55

# Baseline gem rate by canonical tier, "standard" surface (non-textured).
_TIER_BASE: dict[str, float] = {
    "common": 0.85,
    "uncommon": 0.80,
    "rare": 0.75,
    "rare_holo": 0.60,
    "ultra_rare": 0.50,
    "illustration_rare": 0.45,
    "special_illustration_rare": 0.40,
    "gold_secret": 0.45,
}

# Era-level modifier: older cards handle worse (centering, edge whitening),
# newer high-gloss finishes also gem worse due to surface sensitivity.
_ERA_MODIFIER: dict[str, float] = {
    "vintage": 0.55,
    "ecard_ex": 0.70,
    "dp_pt": 0.80,
    "bw_xy": 0.90,
    "sm": 0.95,
    "swsh": 0.90,
    "sv": 0.85,
}

# Surface-type modifier: textured/full-art foils are known to gem
# noticeably worse than standard holo foil due to surface print defects.
_SURFACE_MODIFIER: dict[str, float] = {
    "standard": 1.0,
    "textured": 0.70,
    "full_art": 0.85,
}


def estimate_gem_rate(era: str, tier: str, surface: str = "standard") -> float:
    base = _TIER_BASE.get(tier)
    if base is None:
        return DEFAULT_GEM_RATE
    era_mod = _ERA_MODIFIER.get(era, 0.85)
    surface_mod = _SURFACE_MODIFIER.get(surface, 1.0)
    rate = base * era_mod * surface_mod
    return max(0.01, min(rate, 1.0))
