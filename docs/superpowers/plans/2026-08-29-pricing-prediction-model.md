# Pricing Prediction Model Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a fundamentals-based fair-value model for Pokemon cards (rarity, derived pull-rate scarcity, derived gem-rate scarcity, character attraction, and a fitted price lifecycle curve), expose it via two API endpoints, and surface it in the UI as a Fair Value panel, a grade-worthiness verdict, and a collection rankings screen.

**Architecture:** A new `webapp/pricing_model/` package holds pure feature/config logic (rarity, pull-rate, gem-rate, character tiers — no I/O) plus a dedicated SQLite data layer (`pricing_model.sqlite`, separate from the app's main DB) for fitted-model artifacts and the training corpus. A one-time + monthly job harvests training data from the Pokemon TCG API and the existing `pricecharting_lookup` module, fits a log-linear ridge regression with a market-index-detrended, binned lifecycle curve, and stores the result. `app.py` reads the latest fit on demand to answer two new endpoints; the frontend adds a Fair Value panel to Detail and a new Rankings screen.

**Tech Stack:** Python (FastAPI, httpx, sqlite3), **numpy** (new dependency — closed-form ridge regression; no numpy/sklearn currently in `requirements.txt`), React/Babel-standalone (no build step, no JS test runner — frontend tasks are verified manually in the browser, matching this repo's existing convention).

**Spec:** `docs/superpowers/specs/2026-08-29-pricing-prediction-model-design.md`

## Global Constraints

- Data policy: free/derivable inputs only. PSA and GemRate population scraping were evaluated during planning and are both Cloudflare-blocked (403/managed-challenge) — confirmed empirically, not assumed. `gem_rate` is therefore a config-table heuristic estimate, never live-scraped, mirroring pull-rate.
- Language scope: EN, JP, and Chinese-exclusive cards all get predictions. The training corpus itself is EN-only in this plan (Pokemon TCG API set-card lists are reliable and verified reachable; TCGdex connectivity could not be verified from the planning environment) — JP/CN cards are predicted using the fitted language-factor coefficient and always carry the widest confidence band. This matches the spec's explicit risk note on thin CN/JP training data.
- New tables live in a dedicated `webapp/pricing_model.sqlite` file (own connection, own schema), **not** in `db.py`/`db_postgres.py`/`schema.sql`. Reason: `app.py` reads/writes cards via `db_postgres` (Postgres) in production but falls back to `db` (SQLite) locally, while the existing background-job modules (`refresh_job.py`, `price_history_refresh.py`) both do a plain `import db` (SQLite) regardless of which backend `app.py` is using — a pre-existing split in this codebase. Following the `pricecharting_cache.sqlite` / `ebay_cache.sqlite` precedent (each subsystem owns its own cache/data file) sidesteps that inconsistency entirely instead of compounding it.
- No new frontend build tooling. `static/*.jsx` files are transpiled in-browser by Babel-standalone — follow that pattern exactly, no imports/exports beyond what `index.html`'s script tags already wire up.
- Every task ends with a runnable verification (`pytest` for backend, manual browser check for frontend) before its commit.

---

## Task 1: Rarity normalization

**Files:**
- Create: `webapp/pricing_model/__init__.py`
- Create: `webapp/pricing_model/rarity_map.py`
- Test: `webapp/tests/test_pricing_rarity_map.py`

**Interfaces:**
- Produces: `RARITY_RANK: dict[str, int]`, `normalize_rarity(raw: str | None) -> str | None`

- [ ] **Step 1: Write the failing test**

```python
# webapp/tests/test_pricing_rarity_map.py
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
```

- [ ] **Step 2: Run test to verify it fails**

Run (from `webapp/`): `python3 -m pytest tests/test_pricing_rarity_map.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'pricing_model'`

- [ ] **Step 3: Write minimal implementation**

```python
# webapp/pricing_model/__init__.py
```
(empty — marks the package)

```python
# webapp/pricing_model/rarity_map.py
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
    "rare shiny": "ultra_rare",
    "rare shiny gx": "ultra_rare",
    "double rare": "ultra_rare",
    "ace spec rare": "ultra_rare",
    "trainer gallery rare holo": "ultra_rare",
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/test_pricing_rarity_map.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add webapp/pricing_model/__init__.py webapp/pricing_model/rarity_map.py webapp/tests/test_pricing_rarity_map.py
git commit -m "feat: canonical rarity normalization for pricing model"
```

---

## Task 2: Pull-rate scarcity config

**Files:**
- Create: `webapp/pricing_model/pull_rates.py`
- Test: `webapp/tests/test_pricing_pull_rates.py`

**Interfaces:**
- Consumes: canonical tier strings from `rarity_map.CANONICAL_RARITY_LADDER` (Task 1)
- Produces: `pull_rate_scarcity(era: str, tier: str, set_name: str, cards_in_tier: int) -> float`

- [ ] **Step 1: Write the failing test**

```python
# webapp/tests/test_pricing_pull_rates.py
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_pricing_pull_rates.py -v`
Expected: FAIL — `pull_rates` has no attribute `pull_rate_scarcity` (module doesn't exist yet)

- [ ] **Step 3: Write minimal implementation**

```python
# webapp/pricing_model/pull_rates.py
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/test_pricing_pull_rates.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add webapp/pricing_model/pull_rates.py webapp/tests/test_pricing_pull_rates.py
git commit -m "feat: derived pull-rate scarcity config for pricing model"
```

---

## Task 3: Gem-rate heuristic config

**Files:**
- Create: `webapp/pricing_model/gem_rate.py`
- Test: `webapp/tests/test_pricing_gem_rate.py`

**Interfaces:**
- Produces: `estimate_gem_rate(era: str, tier: str, surface: str = "standard") -> float` (returns a value in `(0, 1]`)

- [ ] **Step 1: Write the failing test**

```python
# webapp/tests/test_pricing_gem_rate.py
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_pricing_gem_rate.py -v`
Expected: FAIL — module `pricing_model.gem_rate` not found

- [ ] **Step 3: Write minimal implementation**

```python
# webapp/pricing_model/gem_rate.py
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/test_pricing_gem_rate.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add webapp/pricing_model/gem_rate.py webapp/tests/test_pricing_gem_rate.py
git commit -m "feat: derived gem-rate heuristic config for pricing model"
```

---

## Task 4: Character tier lookup

**Files:**
- Create: `webapp/pricing_model/character_tiers.py`
- Test: `webapp/tests/test_pricing_character_tiers.py`

**Interfaces:**
- Produces: `get_character_tier(card_name: str, is_trainer_art: bool = False) -> str` (one of `"S","A","B","C","D"`)

- [ ] **Step 1: Write the failing test**

```python
# webapp/tests/test_pricing_character_tiers.py
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_pricing_character_tiers.py -v`
Expected: FAIL — module not found

- [ ] **Step 3: Write minimal implementation**

```python
# webapp/pricing_model/character_tiers.py
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/test_pricing_character_tiers.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add webapp/pricing_model/character_tiers.py webapp/tests/test_pricing_character_tiers.py
git commit -m "feat: curated character-tier lookup for pricing model"
```

---

## Task 5: Feature builder (era, release date, months-since-release, CardFeatures)

**Files:**
- Create: `webapp/pricing_model/features.py`
- Test: `webapp/tests/test_pricing_features.py`

**Interfaces:**
- Consumes: `rarity_map.normalize_rarity` (Task 1), `pull_rates.pull_rate_scarcity` (Task 2), `gem_rate.estimate_gem_rate` (Task 3), `character_tiers.get_character_tier` (Task 4)
- Produces: `CardFeatures` dataclass, `era_bucket(release_date: str) -> str`, `months_since_release(release_date: str, as_of: date | None = None) -> float`, `build_card_features(...) -> CardFeatures | None`

- [ ] **Step 1: Write the failing test**

```python
# webapp/tests/test_pricing_features.py
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_pricing_features.py -v`
Expected: FAIL — module not found

- [ ] **Step 3: Write minimal implementation**

```python
# webapp/pricing_model/features.py
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/test_pricing_features.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add webapp/pricing_model/features.py webapp/tests/test_pricing_features.py
git commit -m "feat: card feature builder combining rarity/pull-rate/gem-rate/character"
```

---

## Task 6: Pricing-model SQLite data layer

**Files:**
- Create: `webapp/pricing_model/db.py`
- Test: `webapp/tests/test_pricing_db.py`

**Interfaces:**
- Consumes: `features.CardFeatures` (Task 5)
- Produces: `init_db()`, `connect()` (contextmanager), `CorpusCardRow` dataclass, `ModelRun` dataclass, `upsert_card_features(card_id: int, f: CardFeatures) -> None`, `get_card_features(card_id: int) -> CardFeatures | None`, `upsert_corpus_card(row: CorpusCardRow) -> None`, `insert_corpus_history(card_key: str, points: list[tuple[str, float]]) -> None`, `get_all_corpus_cards() -> list[CorpusCardRow]`, `get_corpus_history(card_key: str) -> list[tuple[str, float]]`, `get_all_corpus_history() -> dict[str, list[tuple[str, float]]]`, `save_model_run(run: ModelRun) -> int`, `get_latest_model_run() -> ModelRun | None`

- [ ] **Step 1: Write the failing test**

```python
# webapp/tests/test_pricing_db.py
from __future__ import annotations

import os

os.environ["PRICING_MODEL_DB"] = "/tmp/test_pricing_model.sqlite"

import pytest

from pricing_model import db as pmdb
from pricing_model.features import CardFeatures


@pytest.fixture(autouse=True)
def fresh_db():
    if pmdb.DB_PATH.exists():
        pmdb.DB_PATH.unlink()
    pmdb.init_db()
    yield
    if pmdb.DB_PATH.exists():
        pmdb.DB_PATH.unlink()


def _sample_features() -> CardFeatures:
    return CardFeatures(
        canonical_rarity="special_illustration_rare",
        pull_scarcity=0.05,
        gem_rate=0.4,
        character_tier="S",
        is_trainer_art=False,
        language="english",
        era="sv",
        release_date="2024-11-08",
        months_since_release=6.0,
    )


def test_upsert_and_get_card_features_roundtrip():
    pmdb.upsert_card_features(42, _sample_features())
    got = pmdb.get_card_features(42)
    assert got is not None
    assert got.canonical_rarity == "special_illustration_rare"
    assert got.character_tier == "S"


def test_upsert_card_features_overwrites_existing_row():
    pmdb.upsert_card_features(42, _sample_features())
    updated = _sample_features()
    updated.gem_rate = 0.9
    pmdb.upsert_card_features(42, updated)
    got = pmdb.get_card_features(42)
    assert got.gem_rate == 0.9


def test_corpus_card_and_history_roundtrip():
    row = pmdb.CorpusCardRow(
        card_key="sv8-199", name="Charizard ex", set_name="Surging Sparks",
        card_number="199/191", rarity_raw="Special Illustration Rare",
        era="sv", language="english", release_date="2024-11-08",
        psa10_price_usd=450.0, grade9_price_usd=180.0,
    )
    pmdb.upsert_corpus_card(row)
    pmdb.insert_corpus_history("sv8-199", [("2024-11", 100.0), ("2024-12", 90.0)])

    cards = pmdb.get_all_corpus_cards()
    assert len(cards) == 1
    assert cards[0].name == "Charizard ex"

    history = pmdb.get_corpus_history("sv8-199")
    assert history == [("2024-11", 100.0), ("2024-12", 90.0)]


def test_save_and_get_latest_model_run():
    run = pmdb.ModelRun(
        id=None, fitted_at="2026-08-01T00:00:00",
        coefficients_raw={"intercept": 1.0}, coefficients_psa10={"intercept": 2.0},
        lifecycle_curve={"0-3": 1.2, "3-6": 0.9}, market_index={"2024-11": 1.0},
        psa9_fraction=0.4, residual_std_raw=0.3, residual_std_psa10=0.35,
        r_squared_raw=0.6, r_squared_psa10=0.55, n_cards=500,
    )
    run_id = pmdb.save_model_run(run)
    assert run_id > 0
    latest = pmdb.get_latest_model_run()
    assert latest is not None
    assert latest.coefficients_raw == {"intercept": 1.0}
    assert latest.n_cards == 500


def test_get_latest_model_run_returns_none_when_empty():
    assert pmdb.get_latest_model_run() is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_pricing_db.py -v`
Expected: FAIL — module not found

- [ ] **Step 3: Write minimal implementation**

```python
# webapp/pricing_model/db.py
"""Dedicated SQLite data layer for the pricing prediction model.

Deliberately separate from db.py / db_postgres.py (see plan Global
Constraints): app.py reads cards via db_postgres in production but the
background-job modules (refresh_job.py, price_history_refresh.py) both
import plain `db` (SQLite) regardless — this file sidesteps that split by
following the pricecharting_cache.sqlite / ebay_cache.sqlite precedent of
one dedicated file per subsystem.
"""
from __future__ import annotations

import json
import os
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from pricing_model.features import CardFeatures

DB_PATH = Path(os.environ.get("PRICING_MODEL_DB", str(Path(__file__).parent.parent / "pricing_model.sqlite")))

SCHEMA = """
CREATE TABLE IF NOT EXISTS card_features (
    card_id               INTEGER PRIMARY KEY,
    canonical_rarity      TEXT NOT NULL,
    pull_scarcity         REAL NOT NULL,
    gem_rate              REAL NOT NULL,
    character_tier        TEXT NOT NULL,
    is_trainer_art        INTEGER NOT NULL DEFAULT 0,
    language              TEXT NOT NULL,
    era                   TEXT NOT NULL,
    release_date          TEXT NOT NULL,
    months_since_release  REAL NOT NULL,
    features_updated_at   TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS corpus_cards (
    card_key        TEXT PRIMARY KEY,
    name            TEXT NOT NULL,
    set_name        TEXT NOT NULL,
    card_number     TEXT NOT NULL,
    rarity_raw      TEXT,
    era             TEXT NOT NULL,
    language        TEXT NOT NULL DEFAULT 'english',
    release_date    TEXT NOT NULL,
    psa10_price_usd REAL,
    grade9_price_usd REAL,
    harvested_at    TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS corpus_history (
    card_key      TEXT NOT NULL REFERENCES corpus_cards(card_key) ON DELETE CASCADE,
    month         TEXT NOT NULL,
    raw_price_usd REAL NOT NULL,
    PRIMARY KEY (card_key, month)
);

CREATE TABLE IF NOT EXISTS model_runs (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    fitted_at           TEXT NOT NULL DEFAULT (datetime('now')),
    coefficients_raw    TEXT NOT NULL,
    coefficients_psa10  TEXT NOT NULL,
    lifecycle_curve     TEXT NOT NULL,
    market_index        TEXT NOT NULL,
    psa9_fraction       REAL NOT NULL,
    residual_std_raw    REAL NOT NULL,
    residual_std_psa10  REAL NOT NULL,
    r_squared_raw       REAL NOT NULL,
    r_squared_psa10     REAL NOT NULL,
    n_cards             INTEGER NOT NULL
);
"""


def init_db() -> None:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with connect() as conn:
        conn.executescript(SCHEMA)
        conn.commit()


@contextmanager
def connect():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# card_features
# ---------------------------------------------------------------------------

def upsert_card_features(card_id: int, f: CardFeatures) -> None:
    with connect() as conn:
        conn.execute(
            """INSERT INTO card_features
               (card_id, canonical_rarity, pull_scarcity, gem_rate, character_tier,
                is_trainer_art, language, era, release_date, months_since_release,
                features_updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
               ON CONFLICT(card_id) DO UPDATE SET
                 canonical_rarity=excluded.canonical_rarity,
                 pull_scarcity=excluded.pull_scarcity,
                 gem_rate=excluded.gem_rate,
                 character_tier=excluded.character_tier,
                 is_trainer_art=excluded.is_trainer_art,
                 language=excluded.language,
                 era=excluded.era,
                 release_date=excluded.release_date,
                 months_since_release=excluded.months_since_release,
                 features_updated_at=datetime('now')""",
            (card_id, f.canonical_rarity, f.pull_scarcity, f.gem_rate, f.character_tier,
             int(f.is_trainer_art), f.language, f.era, f.release_date, f.months_since_release),
        )
        conn.commit()


def get_card_features(card_id: int) -> Optional[CardFeatures]:
    with connect() as conn:
        row = conn.execute(
            "SELECT * FROM card_features WHERE card_id = ?", (card_id,)
        ).fetchone()
    if not row:
        return None
    return CardFeatures(
        canonical_rarity=row["canonical_rarity"], pull_scarcity=row["pull_scarcity"],
        gem_rate=row["gem_rate"], character_tier=row["character_tier"],
        is_trainer_art=bool(row["is_trainer_art"]), language=row["language"],
        era=row["era"], release_date=row["release_date"],
        months_since_release=row["months_since_release"],
    )


# ---------------------------------------------------------------------------
# training corpus
# ---------------------------------------------------------------------------

@dataclass
class CorpusCardRow:
    card_key: str
    name: str
    set_name: str
    card_number: str
    rarity_raw: Optional[str]
    era: str
    language: str
    release_date: str
    psa10_price_usd: Optional[float] = None
    grade9_price_usd: Optional[float] = None


def upsert_corpus_card(row: CorpusCardRow) -> None:
    with connect() as conn:
        conn.execute(
            """INSERT INTO corpus_cards
               (card_key, name, set_name, card_number, rarity_raw, era, language,
                release_date, psa10_price_usd, grade9_price_usd, harvested_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
               ON CONFLICT(card_key) DO UPDATE SET
                 name=excluded.name, set_name=excluded.set_name,
                 card_number=excluded.card_number, rarity_raw=excluded.rarity_raw,
                 era=excluded.era, language=excluded.language,
                 release_date=excluded.release_date,
                 psa10_price_usd=excluded.psa10_price_usd,
                 grade9_price_usd=excluded.grade9_price_usd,
                 harvested_at=datetime('now')""",
            (row.card_key, row.name, row.set_name, row.card_number, row.rarity_raw,
             row.era, row.language, row.release_date, row.psa10_price_usd, row.grade9_price_usd),
        )
        conn.commit()


def insert_corpus_history(card_key: str, points: list[tuple[str, float]]) -> None:
    with connect() as conn:
        conn.executemany(
            """INSERT INTO corpus_history (card_key, month, raw_price_usd)
               VALUES (?, ?, ?)
               ON CONFLICT(card_key, month) DO UPDATE SET raw_price_usd=excluded.raw_price_usd""",
            [(card_key, month, price) for month, price in points],
        )
        conn.commit()


def get_all_corpus_cards() -> list[CorpusCardRow]:
    with connect() as conn:
        rows = conn.execute("SELECT * FROM corpus_cards").fetchall()
    return [
        CorpusCardRow(
            card_key=r["card_key"], name=r["name"], set_name=r["set_name"],
            card_number=r["card_number"], rarity_raw=r["rarity_raw"], era=r["era"],
            language=r["language"], release_date=r["release_date"],
            psa10_price_usd=r["psa10_price_usd"], grade9_price_usd=r["grade9_price_usd"],
        )
        for r in rows
    ]


def get_corpus_history(card_key: str) -> list[tuple[str, float]]:
    with connect() as conn:
        rows = conn.execute(
            "SELECT month, raw_price_usd FROM corpus_history WHERE card_key = ? ORDER BY month",
            (card_key,),
        ).fetchall()
    return [(r["month"], r["raw_price_usd"]) for r in rows]


def get_all_corpus_history() -> dict[str, list[tuple[str, float]]]:
    with connect() as conn:
        rows = conn.execute(
            "SELECT card_key, month, raw_price_usd FROM corpus_history ORDER BY card_key, month"
        ).fetchall()
    out: dict[str, list[tuple[str, float]]] = {}
    for r in rows:
        out.setdefault(r["card_key"], []).append((r["month"], r["raw_price_usd"]))
    return out


# ---------------------------------------------------------------------------
# model runs
# ---------------------------------------------------------------------------

@dataclass
class ModelRun:
    id: Optional[int]
    fitted_at: str
    coefficients_raw: dict
    coefficients_psa10: dict
    lifecycle_curve: dict
    market_index: dict
    psa9_fraction: float
    residual_std_raw: float
    residual_std_psa10: float
    r_squared_raw: float
    r_squared_psa10: float
    n_cards: int


def save_model_run(run: ModelRun) -> int:
    with connect() as conn:
        cur = conn.execute(
            """INSERT INTO model_runs
               (coefficients_raw, coefficients_psa10, lifecycle_curve, market_index,
                psa9_fraction, residual_std_raw, residual_std_psa10,
                r_squared_raw, r_squared_psa10, n_cards)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (json.dumps(run.coefficients_raw), json.dumps(run.coefficients_psa10),
             json.dumps(run.lifecycle_curve), json.dumps(run.market_index),
             run.psa9_fraction, run.residual_std_raw, run.residual_std_psa10,
             run.r_squared_raw, run.r_squared_psa10, run.n_cards),
        )
        conn.commit()
        return cur.lastrowid


def get_latest_model_run() -> Optional[ModelRun]:
    with connect() as conn:
        row = conn.execute(
            "SELECT * FROM model_runs ORDER BY id DESC LIMIT 1"
        ).fetchone()
    if not row:
        return None
    return ModelRun(
        id=row["id"], fitted_at=row["fitted_at"],
        coefficients_raw=json.loads(row["coefficients_raw"]),
        coefficients_psa10=json.loads(row["coefficients_psa10"]),
        lifecycle_curve=json.loads(row["lifecycle_curve"]),
        market_index=json.loads(row["market_index"]),
        psa9_fraction=row["psa9_fraction"],
        residual_std_raw=row["residual_std_raw"], residual_std_psa10=row["residual_std_psa10"],
        r_squared_raw=row["r_squared_raw"], r_squared_psa10=row["r_squared_psa10"],
        n_cards=row["n_cards"],
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/test_pricing_db.py -v`
Expected: PASS (6 tests)

- [ ] **Step 5: Commit**

```bash
git add webapp/pricing_model/db.py webapp/tests/test_pricing_db.py
git commit -m "feat: dedicated SQLite data layer for pricing model artifacts"
```

---

## Task 7: Training corpus harvester + one-time backfill CLI

**Files:**
- Create: `webapp/pricing_model/corpus.py`
- Create: `webapp/backfill_pricing_corpus.py`
- Modify: `webapp/requirements.txt` (no change needed here — httpx already present)
- Test: `webapp/tests/test_pricing_corpus.py`

**Interfaces:**
- Consumes: `pricing_model.db.CorpusCardRow`, `upsert_corpus_card`, `insert_corpus_history` (Task 6); `pricecharting_lookup.lookup_raw_price`, `pricecharting_lookup.fetch_chart_history` (existing); `pricing_model.features.era_bucket` (Task 5)
- Produces: `TRAINING_SETS: list[str]` (Pokemon TCG API set ids), `async def harvest_set(set_id: str) -> int` (returns cards harvested), `async def harvest_all(set_ids: list[str] | None = None) -> dict`

- [ ] **Step 1: Write the failing test**

```python
# webapp/tests/test_pricing_corpus.py
from __future__ import annotations

import asyncio
import os
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

os.environ["PRICING_MODEL_DB"] = "/tmp/test_pricing_corpus.sqlite"

from pricing_model import corpus, db as pmdb


def _fresh_db():
    if pmdb.DB_PATH.exists():
        pmdb.DB_PATH.unlink()
    pmdb.init_db()


_FAKE_TCG_API_RESPONSE = {
    "data": [
        {
            "name": "Charizard ex", "number": "199",
            "rarity": "Special Illustration Rare",
            "set": {"id": "sv8", "name": "Surging Sparks", "releaseDate": "2024/11/08"},
        },
        {
            "name": "Pikachu", "number": "5",
            "rarity": "Common",
            "set": {"id": "sv8", "name": "Surging Sparks", "releaseDate": "2024/11/08"},
        },
    ]
}


def test_harvest_set_writes_corpus_cards_and_history():
    _fresh_db()

    fake_response = SimpleNamespace(
        status_code=200,
        json=lambda: _FAKE_TCG_API_RESPONSE,
        raise_for_status=lambda: None,
    )

    fake_pc_result = SimpleNamespace(
        price_usd=100.0,
        all_prices={"Ungraded": 100.0, "PSA 10": 450.0, "Grade 9": 180.0},
    )

    async def fake_get(*args, **kwargs):
        return fake_response

    with patch("httpx.AsyncClient.get", new=AsyncMock(side_effect=fake_get)), \
         patch("pricecharting_lookup.lookup_raw_price", new=AsyncMock(return_value=fake_pc_result)), \
         patch("pricecharting_lookup.fetch_chart_history",
               new=AsyncMock(return_value=([(1700000000000, 90.0), (1702592000000, 100.0)], "https://example.com"))):
        n = asyncio.run(corpus.harvest_set("sv8"))

    assert n == 2
    cards = pmdb.get_all_corpus_cards()
    assert len(cards) == 2
    names = {c.name for c in cards}
    assert names == {"Charizard ex", "Pikachu"}
    charizard = next(c for c in cards if c.name == "Charizard ex")
    assert charizard.psa10_price_usd == 450.0
    assert charizard.grade9_price_usd == 180.0
    assert charizard.era == "sv"

    history = pmdb.get_corpus_history(charizard.card_key)
    assert len(history) == 2
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_pricing_corpus.py -v`
Expected: FAIL — module `pricing_model.corpus` not found

- [ ] **Step 3: Write minimal implementation**

```python
# webapp/pricing_model/corpus.py
"""Bulk training-corpus harvest for the pricing model.

EN-only in this version: Pokemon TCG API's per-set card list is reliable
and gives rarity + release date directly; TCGdex connectivity could not be
verified during planning, so JP/CN corpus harvesting is deferred (see plan
Global Constraints). JP/CN cards still get predictions via the fitted
language-factor coefficient and a wider confidence band.

Reuses pricecharting_lookup's existing per-card page fetch/cache machinery
(no new scraping code) for both the raw+PSA10+Grade9 snapshot and the
~33-month raw price history.
"""
from __future__ import annotations

import asyncio
import logging

import httpx

import pricecharting_lookup as pc
from pricing_model import db as pmdb
from pricing_model.features import era_bucket

log = logging.getLogger(__name__)

POKEMONTCG_BASE = "https://api.pokemontcg.io/v2"
PER_CARD_SLEEP_SEC = 1.5

# EN sets spanning eras, used to build the training corpus. Extend this list
# to widen/refresh corpus coverage.
TRAINING_SETS: list[str] = [
    "sv8", "sv7", "sv6", "sv4", "sv3pt5", "sv3", "sv1",
    "swsh12pt5", "swsh12", "swsh9", "swsh7", "swsh4",
    "sm12", "sm9", "sm5",
    "xy12", "xy7",
]


async def _fetch_set_cards(set_id: str) -> list[dict]:
    async with httpx.AsyncClient(timeout=20.0) as client:
        resp = await client.get(
            f"{POKEMONTCG_BASE}/cards",
            params={"q": f"set.id:{set_id}", "pageSize": 250},
        )
        resp.raise_for_status()
        return resp.json().get("data", [])


def _iso_month(ts_ms: int) -> str:
    from datetime import datetime, timezone
    dt = datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc)
    return f"{dt.year:04d}-{dt.month:02d}"


async def harvest_set(set_id: str) -> int:
    cards = await _fetch_set_cards(set_id)
    harvested = 0
    for card in cards:
        name = card.get("name")
        number = card.get("number")
        set_info = card.get("set") or {}
        set_name = set_info.get("name", "")
        release_raw = set_info.get("releaseDate", "")  # "YYYY/MM/DD"
        if not (name and number and release_raw):
            continue
        release_date = release_raw.replace("/", "-")
        card_key = f"{set_id}-{number}"

        pc_result = await pc.lookup_raw_price(name, set_name, number, "english")
        history = await pc.fetch_chart_history(name, set_name, number, "english")
        await asyncio.sleep(PER_CARD_SLEEP_SEC)

        if pc_result is None or history is None:
            continue
        points, _url = history

        pmdb.upsert_corpus_card(pmdb.CorpusCardRow(
            card_key=card_key, name=name, set_name=set_name, card_number=number,
            rarity_raw=card.get("rarity"), era=era_bucket(release_date),
            language="english", release_date=release_date,
            psa10_price_usd=pc_result.all_prices.get("PSA 10"),
            grade9_price_usd=pc_result.all_prices.get("Grade 9"),
        ))
        pmdb.insert_corpus_history(card_key, [(_iso_month(ts), price) for ts, price in points])
        harvested += 1
    return harvested


async def harvest_all(set_ids: list[str] | None = None) -> dict:
    set_ids = set_ids or TRAINING_SETS
    total = 0
    per_set: dict[str, int] = {}
    for set_id in set_ids:
        try:
            n = await harvest_set(set_id)
        except Exception as e:
            log.warning("corpus harvest failed for set %s: %s", set_id, e)
            n = 0
        per_set[set_id] = n
        total += n
    return {"total_cards": total, "per_set": per_set}
```

```python
# webapp/backfill_pricing_corpus.py
"""One-off / manual CLI for pricing_model.corpus.harvest_all().

Run from webapp/: `python3 backfill_pricing_corpus.py [--sets sv8,sv7,...]`
Populates pricing_model.sqlite's corpus_cards/corpus_history tables from
Pokemon TCG API + PriceCharting. Safe to re-run (upserts).
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import sys

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s — %(message)s")

from pricing_model import corpus, db as pmdb

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--sets", type=str, default=None,
                        help="comma-separated Pokemon TCG API set ids (default: corpus.TRAINING_SETS)")
    args = parser.parse_args()
    pmdb.init_db()
    set_ids = args.sets.split(",") if args.sets else None
    print("Harvesting training corpus…")
    summary = asyncio.run(corpus.harvest_all(set_ids))
    print(f"\nSummary: {summary}")
    sys.exit(0)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/test_pricing_corpus.py -v`
Expected: PASS (1 test)

- [ ] **Step 5: Commit**

```bash
git add webapp/pricing_model/corpus.py webapp/backfill_pricing_corpus.py webapp/tests/test_pricing_corpus.py
git commit -m "feat: EN training-corpus harvester (Pokemon TCG API + PriceCharting)"
```

---

## Task 8: Model fitting (market index, lifecycle curve, ridge regression)

**Files:**
- Create: `webapp/pricing_model/model.py`
- Modify: `webapp/requirements.txt` (add `numpy>=1.26`)
- Test: `webapp/tests/test_pricing_model.py`

**Interfaces:**
- Consumes: `db.CorpusCardRow`, `db.get_all_corpus_cards`, `db.get_all_corpus_history`, `db.ModelRun`, `db.save_model_run` (Task 6); `rarity_map.normalize_rarity` (Task 1); `pull_rates.pull_rate_scarcity` (Task 2); `gem_rate.estimate_gem_rate` (Task 3); `character_tiers.get_character_tier` (Task 4)
- Produces: `FEATURE_ORDER_RAW: list[str]`, `FEATURE_ORDER_PSA10: list[str]`, `lifecycle_multiplier(curve: dict, months: float) -> float`, `fit_model(cards: list[CorpusCardRow], history: dict[str, list[tuple[str,float]]]) -> ModelRun`

- [ ] **Step 1: Write the failing test**

```python
# webapp/tests/test_pricing_model.py
from __future__ import annotations

import math
import random

from pricing_model import model as pm
from pricing_model.db import CorpusCardRow


def _synthetic_corpus(n: int = 80, seed: int = 7):
    """Cards with a KNOWN price-generating rule so the fit can be checked
    against ground truth: rare_holo cards trade at 2x common's base price,
    with a flat monthly history (no lifecycle/market effects) so the
    regression should recover that ratio cleanly."""
    random.seed(seed)
    cards = []
    history = {}
    for i in range(n):
        tier_is_rare = i % 2 == 0
        rarity_raw = "Rare Holo" if tier_is_rare else "Common"
        base_price = 20.0 if tier_is_rare else 10.0
        card_key = f"synthetic-{i}"
        cards.append(CorpusCardRow(
            card_key=card_key, name=f"Card {i}", set_name="Synthetic Set",
            card_number=str(i), rarity_raw=rarity_raw, era="sv", language="english",
            release_date="2023-06-01", psa10_price_usd=None, grade9_price_usd=None,
        ))
        # Flat 6-month history at base_price with tiny noise — no lifecycle
        # shape, no market drift, isolates the rarity coefficient.
        history[card_key] = [
            (f"2024-{m:02d}", base_price * (1 + random.uniform(-0.02, 0.02)))
            for m in range(1, 7)
        ]
    return cards, history


def test_fit_model_recovers_known_rarity_ratio():
    cards, history = _synthetic_corpus()
    run = pm.fit_model(cards, history)

    assert run.n_cards == len(cards)
    assert run.r_squared_raw > 0.9  # near-noiseless synthetic data
    assert run.residual_std_raw < 0.2  # tight fit on near-noiseless data

    # rare_holo cards have a SMALLER pull_scarcity value (scarcer) than
    # common cards but a HIGHER price in this synthetic corpus, so the
    # fitted log_pull_scarcity coefficient must be negative (smaller
    # scarcity value -> higher predicted price) for the model to have
    # actually learned the rarity signal rather than fitting noise.
    assert run.coefficients_raw["log_pull_scarcity"] < 0


def test_lifecycle_multiplier_falls_back_to_median_when_bin_missing():
    curve = {"0-3": 1.3, "3-6": 0.9, "6-9": 0.8}
    # A months value with no exact bin match still returns a sane multiplier.
    assert pm.lifecycle_multiplier(curve, 100.0) == sorted(curve.values())[len(curve) // 2]


def test_lifecycle_multiplier_empty_curve_returns_one():
    assert pm.lifecycle_multiplier({}, 5.0) == 1.0


def _shift_month(year: int, month: int, delta_months: int) -> tuple[int, int]:
    total = (year * 12 + (month - 1)) - delta_months
    return total // 12, total % 12 + 1


def _synthetic_corpus_with_lifecycle(n_cards: int = 200, seed: int = 11):
    """Cards with a KNOWN non-flat lifecycle curve and a KNOWN +1%/month
    market trend, with STAGGERED release ages so different cards cover
    different age ranges within the same 12 calendar months -- this mirrors
    PriceCharting's calendar-anchored (not release-anchored) chart history
    and is exactly the shape that breaks a naive per-card-first-point
    anchor. A correct estimator must recover the true curve's shape even
    though no single card's own series spans more than 12 months of age."""
    import random
    random.seed(seed)

    true_curve = {
        "0-3": 1.3, "3-6": 1.05, "6-9": 0.9, "9-12": 0.85,
        "12-18": 0.82, "18-24": 0.85, "24-36": 0.95, "36+": 1.1,
    }

    def true_multiplier(months: float) -> float:
        for lo, hi in [(0, 3), (3, 6), (6, 9), (9, 12), (12, 18), (18, 24), (24, 36), (36, 9999)]:
            if lo <= months < hi:
                label = f"{lo:g}-{hi:g}" if hi < 9999 else f"{lo:g}+"
                return true_curve[label]
        return 1.0

    calendar_months = [f"2024-{m:02d}" for m in range(1, 13)]
    cards = []
    history = {}
    for i in range(n_cards):
        card_key = f"lc-{i}"
        age_at_window_start = random.randint(0, 40)
        ry, rm = _shift_month(2024, 1, age_at_window_start)
        cards.append(CorpusCardRow(
            card_key=card_key, name=f"Card {i}", set_name="Lifecycle Set",
            card_number=str(i), rarity_raw="Rare Holo", era="sv", language="english",
            release_date=f"{ry:04d}-{rm:02d}-01", psa10_price_usd=None, grade9_price_usd=None,
        ))
        points = []
        for month_idx, month in enumerate(calendar_months):
            months_since = age_at_window_start + month_idx
            true_price = 10.0 * (1.01 ** month_idx) * true_multiplier(months_since)
            noise = random.uniform(0.97, 1.03)
            points.append((month, true_price * noise))
        history[card_key] = points
    return cards, history, true_curve


def test_fit_model_recovers_non_flat_lifecycle_shape_under_staggered_releases():
    cards, history, true_curve = _synthetic_corpus_with_lifecycle()
    run = pm.fit_model(cards, history)

    curve = run.lifecycle_curve
    # Exact-value recovery isn't robust to noise/bin-coverage variance, but
    # the SHAPE must survive: the hype peak (0-3, true 1.3) must read
    # meaningfully higher than the trough (9-12, true 0.85), not flat or
    # inverted -- this is exactly the failure mode a first-point-anchored
    # estimator produces (it recovers ~1.0 for both, or worse).
    #
    # Threshold calibration matters here: an earlier draft of this test used
    # a 1.2x margin, which the FIRST-POINT-ANCHORED (buggy) estimator also
    # clears on ~70% of random seeds (its degenerate ~1.0-vs-~0.82 output is
    # itself a ~1.2x ratio) -- confirmed during review by running this exact
    # assertion against the pre-fix module across 30 seeds. 1.4x cleanly
    # separates the two (0/30 pre-fix, 30/30 post-fix), and the monotone
    # hype-to-trough chain below separates them even harder (2/30 vs 30/30)
    # -- both are included so this test actually fails against a
    # reintroduced first-point-anchoring bug, not just against a flat curve.
    for b in ("0-3", "3-6", "6-9", "9-12", "36+"):
        assert b in curve, f"bin {b} missing from recovered curve"
    assert curve["0-3"] > curve["9-12"] * 1.4
    assert curve["0-3"] > curve["3-6"] > curve["6-9"] > curve["9-12"]
    # The recovery bin (36+, true 1.1) must likewise read higher than the
    # trough, not collapse to the same level.
    assert curve["36+"] > curve["9-12"] * 1.1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_pricing_model.py -v`
Expected: FAIL — module `pricing_model.model` not found

- [ ] **Step 3: Write minimal implementation**

```python
# webapp/pricing_model/model.py
"""Model fitting: market index, binned lifecycle curve, cross-sectional
log-linear ridge regression. See spec "Model core" for the full design.

Two-stage estimation, deliberately not a single black-box fit:
  1. Market index — per-month median (price / that card's first-observed
     price) across the whole corpus. Captures "everything moved together."
  2. Lifecycle curve — per-card detrended relative price (index-adjusted),
     binned by months-since-release, median per bin. Captures the
     hype -> supply-slide -> trough -> recovery shape.
  3. Cross-sectional regression — each card's LATEST observation, with the
     market-index and lifecycle effects divided out, regressed on
     {era, language, log(pull_scarcity), character tier} (+ log(1/gem_rate)
     for the PSA10 head). Isolates fundamentals coefficients cleanly so
     every prediction decomposes into named multipliers.
"""
from __future__ import annotations

import math
import statistics
from collections import defaultdict
from datetime import date

import numpy as np

from pricing_model import character_tiers, gem_rate, pull_rates, rarity_map
from pricing_model.db import CorpusCardRow, ModelRun

RIDGE_ALPHA = 1.0
MEDIAN_POLISH_ITERATIONS = 20

# cards_in_tier is fixed at 1 everywhere in this model -- both here
# (training) and wherever CardFeatures is built for prediction (see Task
# 12's build_card_features call, which must also pass 1). Per-set
# slot-competition effects are folded into pull_rates.py's per-tier config
# instead (via SET_OVERRIDES) rather than computed dynamically at both
# training and serving time, so the two can never drift apart on this
# parameter. Do not change one side without the other.
FIXED_CARDS_IN_TIER = 1

LIFECYCLE_BINS: list[tuple[float, float]] = [
    (0, 3), (3, 6), (6, 9), (9, 12), (12, 18), (18, 24), (24, 36), (36, 10_000),
]

FEATURE_ORDER_RAW: list[str] = [
    "intercept",
    "era:ecard_ex", "era:dp_pt", "era:bw_xy", "era:sm", "era:swsh", "era:sv",
    "lang:japanese", "lang:chinese",
    "log_pull_scarcity",
    "char:S", "char:A", "char:B", "char:D",
]
FEATURE_ORDER_PSA10: list[str] = FEATURE_ORDER_RAW + ["log_inv_gem_rate"]


def _bin_label(months: float) -> str:
    if months < 0:
        months = 0.0  # future/clock-skew release date: treat as newly released
    for lo, hi in LIFECYCLE_BINS:
        if lo <= months < hi:
            return f"{lo:g}-{hi:g}" if hi < 10_000 else f"{lo:g}+"
    return f"{LIFECYCLE_BINS[-1][0]:g}+"


def _month_to_date(month_str: str) -> date:
    y, m = month_str.split("-")
    return date(int(y), int(m), 1)


def _months_between(a: date, b: date) -> float:
    return (b.year - a.year) * 12 + (b.month - a.month)


def _market_index(history: dict[str, list[tuple[str, float]]]) -> dict[str, float]:
    """Chain-linked index: for each pair of adjacent months present in the
    corpus, the link ratio is the median (this-month price / prior-month
    price) across cards observed in BOTH months, and the index cumulates
    these links from 1.0 at the earliest month.

    NOT a per-card ratio against each card's own first observation: cards
    enter this corpus at different ages within the same calendar window
    (PriceCharting's ~33-month chart_data is calendar-anchored, ending
    "now", not anchored to each card's release date), so anchoring each
    card's ratio at its own first point would conflate "when a card
    happened to join the panel" with "the market moving" -- verified during
    review to bias the index by double-digit percentages under realistic
    staggered-entry corpora.
    """
    by_month: dict[str, dict[str, float]] = defaultdict(dict)
    for card_key, points in history.items():
        for month, price in points:
            if price > 0:
                by_month[month][card_key] = price

    months = sorted(by_month.keys())
    index: dict[str, float] = {}
    if not months:
        return index
    index[months[0]] = 1.0
    for prev_month, month in zip(months, months[1:]):
        prev_prices = by_month[prev_month]
        curr_prices = by_month[month]
        common_keys = set(prev_prices) & set(curr_prices)
        ratios = [curr_prices[k] / prev_prices[k] for k in common_keys if prev_prices[k] > 0]
        link = statistics.median(ratios) if ratios else 1.0
        index[month] = index[prev_month] * link
    return index


def _lifecycle_curve(
    cards_by_key: dict[str, CorpusCardRow],
    history: dict[str, list[tuple[str, float]]],
    market_index: dict[str, float],
    iterations: int = MEDIAN_POLISH_ITERATIONS,
) -> dict[str, float]:
    """Median-polish estimate of the age-dependent lifecycle multiplier from
    the market-detrended (card x age-bin) table.

    Anchoring each card's detrended series at its own first observation (an
    earlier draft's approach) has the same staggered-entry problem as a
    naive market index (see `_market_index`): the anchor point's age differs
    per card, so each card gets normalized to 1.0 at a DIFFERENT point on
    the true curve, and averaging those per-bin flattens or inverts the
    recovered shape -- confirmed during review with a constructed
    counter-example (known hype->trough->recovery truth recovered as a
    flat, partly-inverted curve under first-point anchoring).

    Median polish instead treats this as a two-way table (card x age bin)
    and iteratively removes row (card) and column (bin) medians in log
    space. This correctly separates each card's own price level from the
    age-dependent shape even though the table is unbalanced (different
    cards cover different bin ranges within their own ~33-month window) --
    confirmed during review to recover a known non-flat truth almost
    exactly on a corpus with staggered release ages.
    """
    cells: dict[str, dict[str, float]] = defaultdict(dict)
    for card_key, points in history.items():
        card = cards_by_key.get(card_key)
        if not card:
            continue
        release = _month_to_date(card.release_date[:7])
        for month, price in points:
            idx = market_index.get(month)
            if not idx or idx <= 0 or price <= 0:
                continue
            months_since = _months_between(release, _month_to_date(month))
            if months_since < 0:
                continue
            label = _bin_label(months_since)
            log_val = math.log(price / idx)
            prev = cells[card_key].get(label)
            cells[card_key][label] = log_val if prev is None else (prev + log_val) / 2

    if not cells:
        return {}

    # A card whose window covers only one age bin (common for older cards --
    # any card past ~36 months of age has its entire ~33-month window inside
    # the "36+" bin) is exactly 0 after row-centering, injecting a hard 0
    # into that bin's column median. On a production-shaped corpus this can
    # be roughly half the cards and measurably pulls the affected bin's
    # multiplier down (confirmed during review: ~9% low on "36+" vs its
    # true value). Only rows spanning >=2 bins carry information about the
    # bin-to-bin SHAPE, so single-bin rows are dropped before polishing --
    # they contribute nothing but noise to column medians.
    residual = {k: dict(v) for k, v in cells.items() if len(v) >= 2}
    col_effect: dict[str, float] = {}
    all_bins = {b for row in residual.values() for b in row}
    for b in all_bins:
        col_effect[b] = 0.0

    for _ in range(iterations):
        for row in residual.values():
            vals = list(row.values())
            if not vals:
                continue
            m = statistics.median(vals)
            for b in row:
                row[b] -= m
        for b in all_bins:
            vals = [row[b] for row in residual.values() if b in row]
            if not vals:
                continue
            m = statistics.median(vals)
            col_effect[b] += m
            for row in residual.values():
                if b in row:
                    row[b] -= m

    return {b: math.exp(v) for b, v in col_effect.items()}


def lifecycle_multiplier(curve: dict[str, float], months_since_release: float) -> float:
    if not curve:
        return 1.0
    label = _bin_label(months_since_release)
    if label in curve:
        return curve[label]
    values = sorted(curve.values())
    return values[len(values) // 2]


def _feature_row(order: list[str], card: CorpusCardRow, pull_scarcity: float,
                  char_tier: str, log_inv_gem_rate: float | None = None) -> list[float]:
    row = [0.0] * len(order)

    def set_if_present(key: str, value: float = 1.0):
        if key in order:
            row[order.index(key)] = value

    set_if_present("intercept")
    set_if_present(f"era:{card.era}")
    set_if_present(f"lang:{card.language}")
    set_if_present("log_pull_scarcity", math.log(max(pull_scarcity, 1e-6)))
    set_if_present(f"char:{char_tier}")
    if log_inv_gem_rate is not None:
        set_if_present("log_inv_gem_rate", log_inv_gem_rate)
    return row


def _ridge_fit(X: list[list[float]], y: list[float]) -> tuple[np.ndarray, float, float]:
    """Closed-form ridge regression: beta = (X^T X + alpha*I)^-1 X^T y,
    intercept left unregularized. Returns (beta, residual_std, r_squared).
    Assumes column 0 of X is the intercept -- true for both
    FEATURE_ORDER_RAW and FEATURE_ORDER_PSA10 (asserted in fit_model)."""
    Xm = np.array(X)
    ym = np.array(y)
    n, k = Xm.shape
    reg = np.eye(k) * RIDGE_ALPHA
    reg[0, 0] = 0.0  # never regularize the intercept
    beta = np.linalg.solve(Xm.T @ Xm + reg, Xm.T @ ym)
    residuals = ym - Xm @ beta
    dof = max(n - k, 1)
    residual_std = float(math.sqrt(float(np.sum(residuals ** 2)) / dof))
    ss_res = float(np.sum(residuals ** 2))
    ss_tot = float(np.sum((ym - float(np.mean(ym))) ** 2)) or 1.0
    r_squared = 1 - ss_res / ss_tot
    return beta, residual_std, r_squared


def fit_model(cards: list[CorpusCardRow], history: dict[str, list[tuple[str, float]]]) -> ModelRun:
    assert FEATURE_ORDER_RAW[0] == "intercept" and FEATURE_ORDER_PSA10[0] == "intercept"

    cards_by_key = {c.card_key: c for c in cards}
    market_index = _market_index(history)
    curve = _lifecycle_curve(cards_by_key, history, market_index)

    X_raw, y_raw = [], []
    X_psa, y_psa = [], []
    g9_ratios: list[float] = []

    for card in cards:
        points = history.get(card.card_key) or []
        if len(points) < 2:
            continue
        tier = rarity_map.normalize_rarity(card.rarity_raw)
        if tier is None:
            continue
        latest_month, latest_price = points[-1]
        if latest_price <= 0:
            continue
        idx = market_index.get(latest_month) or 1.0
        release = _month_to_date(card.release_date[:7])
        months_since = _months_between(release, _month_to_date(latest_month))
        mult = lifecycle_multiplier(curve, months_since) or 1.0
        char_tier = character_tiers.get_character_tier(card.name)
        scarcity = pull_rates.pull_rate_scarcity(card.era, tier, card.set_name, cards_in_tier=FIXED_CARDS_IN_TIER)

        target = math.log(latest_price / (idx * mult))
        X_raw.append(_feature_row(FEATURE_ORDER_RAW, card, scarcity, char_tier))
        y_raw.append(target)

        if card.psa10_price_usd and card.psa10_price_usd > 0:
            gem = gem_rate.estimate_gem_rate(card.era, tier, "standard")
            X_psa.append(_feature_row(
                FEATURE_ORDER_PSA10, card, scarcity, char_tier,
                log_inv_gem_rate=math.log(1.0 / gem),
            ))
            y_psa.append(math.log(card.psa10_price_usd / (idx * mult)))
            if card.grade9_price_usd and card.grade9_price_usd > 0:
                g9_ratios.append(card.grade9_price_usd / card.psa10_price_usd)

    if not X_raw:
        raise ValueError("fit_model: no valid training rows in corpus (check rarity mapping / history length)")

    beta_raw, residual_std_raw, r_squared_raw = _ridge_fit(X_raw, y_raw)
    coefficients_raw = {name: float(b) for name, b in zip(FEATURE_ORDER_RAW, beta_raw)}

    if X_psa:
        beta_psa, residual_std_psa10, r_squared_psa10 = _ridge_fit(X_psa, y_psa)
        coefficients_psa10 = {name: float(b) for name, b in zip(FEATURE_ORDER_PSA10, beta_psa)}
    else:
        residual_std_psa10, r_squared_psa10, coefficients_psa10 = 0.0, 0.0, {}

    psa9_fraction = statistics.median(g9_ratios) if g9_ratios else 0.4

    return ModelRun(
        id=None, fitted_at="",
        coefficients_raw=coefficients_raw, coefficients_psa10=coefficients_psa10,
        lifecycle_curve=curve, market_index=market_index,
        psa9_fraction=psa9_fraction,
        residual_std_raw=residual_std_raw, residual_std_psa10=residual_std_psa10,
        r_squared_raw=r_squared_raw, r_squared_psa10=r_squared_psa10,
        n_cards=len(cards),
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pip install numpy && python3 -m pytest tests/test_pricing_model.py -v`
Expected: PASS (4 tests). If `r_squared_raw` comes in below 0.9 on the synthetic fixture, check that the two rarity tiers' flat-history noise (`uniform(-0.02, 0.02)`) isn't swamping the 2x base-price gap — the test is designed to have effect size >> noise. If the non-flat-lifecycle test fails, check that `_market_index` and `_lifecycle_curve` are the chain-linked/median-polish versions above, not a per-card-first-point-anchored version — that specific mistake is exactly what this test exists to catch.

- [ ] **Step 5: Commit**

```bash
git add webapp/pricing_model/model.py webapp/tests/test_pricing_model.py webapp/requirements.txt
git commit -m "feat: log-linear ridge model with market-index-detrended lifecycle curve"
```

---

## Task 9: Prediction computation (fair value, breakdown, confidence)

**Files:**
- Create: `webapp/pricing_model/predict.py`
- Test: `webapp/tests/test_pricing_predict.py`

**Interfaces:**
- Consumes: `features.CardFeatures` (Task 5), `db.ModelRun` (Task 6), `model.lifecycle_multiplier` (Task 8)
- Produces: `Prediction` dataclass, `predict_raw_price(features: CardFeatures, run: ModelRun) -> Prediction`, `predict_psa10_price(features: CardFeatures, run: ModelRun) -> Prediction | None`

- [ ] **Step 1: Write the failing test**

```python
# webapp/tests/test_pricing_predict.py
from __future__ import annotations

import math

from pricing_model.db import ModelRun
from pricing_model.features import CardFeatures
from pricing_model.predict import predict_psa10_price, predict_raw_price


def _sample_run() -> ModelRun:
    return ModelRun(
        id=1, fitted_at="2026-08-01T00:00:00",
        coefficients_raw={
            "intercept": math.log(10.0), "era:sv": 0.1, "lang:japanese": -0.2,
            "lang:chinese": -0.5, "log_pull_scarcity": 0.3,
            "char:S": math.log(8.0), "char:A": math.log(3.0),
            "char:B": math.log(1.5), "char:D": math.log(0.6),
        },
        coefficients_psa10={
            "intercept": math.log(40.0), "era:sv": 0.1, "lang:japanese": -0.2,
            "lang:chinese": -0.5, "log_pull_scarcity": 0.3,
            "char:S": math.log(8.0), "char:A": math.log(3.0),
            "char:B": math.log(1.5), "char:D": math.log(0.6),
            "log_inv_gem_rate": 0.4,
        },
        lifecycle_curve={"0-3": 1.4, "3-6": 1.0, "6-9": 0.85},
        market_index={"2026-07": 0.9, "2026-08": 0.95},
        psa9_fraction=0.4, residual_std_raw=0.2, residual_std_psa10=0.25,
        r_squared_raw=0.7, r_squared_psa10=0.65, n_cards=1000,
    )


def _sample_features(**overrides) -> CardFeatures:
    base = dict(
        canonical_rarity="special_illustration_rare", pull_scarcity=0.1,
        gem_rate=0.4, character_tier="S", is_trainer_art=False,
        language="english", era="sv", release_date="2026-02-08",
        months_since_release=6.0,
    )
    base.update(overrides)
    return CardFeatures(**base)


def test_predict_raw_price_is_positive_and_has_breakdown():
    pred = predict_raw_price(_sample_features(), _sample_run())
    assert pred.point_estimate > 0
    assert pred.low < pred.point_estimate < pred.high
    assert "char:S" in pred.breakdown
    assert pred.breakdown["char:S"] == math.exp(math.log(8.0))


def test_predict_raw_price_widens_band_for_chinese_language():
    en_pred = predict_raw_price(_sample_features(language="english"), _sample_run())
    cn_pred = predict_raw_price(_sample_features(language="chinese"), _sample_run())
    en_width = en_pred.high - en_pred.low
    cn_width = cn_pred.high - cn_pred.low
    assert cn_width > en_width


def test_predict_psa10_price_includes_gem_rate_factor():
    pred = predict_psa10_price(_sample_features(), _sample_run())
    assert pred is not None
    assert "log_inv_gem_rate" in pred.breakdown


def test_predict_psa10_price_returns_none_without_psa10_coefficients():
    run = _sample_run()
    run.coefficients_psa10 = {}
    assert predict_psa10_price(_sample_features(), run) is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_pricing_predict.py -v`
Expected: FAIL — module `pricing_model.predict` not found

- [ ] **Step 3: Write minimal implementation**

```python
# webapp/pricing_model/predict.py
"""Turns a fitted ModelRun + a card's CardFeatures into a decomposable
prediction: point estimate, confidence band, and a per-factor multiplier
breakdown (so the UI can show "$142 = $8 base x 5.2 rarity x ..." exactly
as designed in the spec).
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

from pricing_model.db import ModelRun
from pricing_model.features import CardFeatures
from pricing_model.model import lifecycle_multiplier

# Confidence-interval z-multiplier (~80% interval) and per-language widening.
Z = 1.28
LANGUAGE_BAND_WIDENING = {"english": 1.0, "japanese": 1.4, "chinese": 2.0}


@dataclass
class Prediction:
    point_estimate: float
    low: float
    high: float
    breakdown: dict[str, float] = field(default_factory=dict)
    lifecycle_multiplier: float = 1.0
    r_squared: float = 0.0


def _fundamentals_log_price(coefficients: dict[str, float], features: CardFeatures,
                             include_gem: bool) -> tuple[float, dict[str, float]]:
    breakdown: dict[str, float] = {}
    total = 0.0

    def add(key: str, value: float | None = None):
        nonlocal total
        coef = coefficients.get(key)
        if coef is None:
            return
        contribution = coef * (value if value is not None else 1.0)
        total += contribution
        breakdown[key] = math.exp(contribution)

    add("intercept")
    add(f"era:{features.era}")
    add(f"lang:{features.language}")
    add("log_pull_scarcity", math.log(max(features.pull_scarcity, 1e-6)))
    add(f"char:{features.character_tier}")
    if include_gem:
        add("log_inv_gem_rate", math.log(1.0 / max(features.gem_rate, 1e-6)))

    return total, breakdown


def _band(point: float, residual_std: float, language: str) -> tuple[float, float]:
    widen = LANGUAGE_BAND_WIDENING.get(language, 2.0)
    spread = math.exp(Z * residual_std * widen)
    return point / spread, point * spread


def predict_raw_price(features: CardFeatures, run: ModelRun) -> Prediction:
    log_price, breakdown = _fundamentals_log_price(
        run.coefficients_raw, features, include_gem=False,
    )
    fundamentals_price = math.exp(log_price)
    mult = lifecycle_multiplier(run.lifecycle_curve, features.months_since_release)
    point = fundamentals_price * mult
    low, high = _band(point, run.residual_std_raw, features.language)
    return Prediction(
        point_estimate=point, low=low, high=high, breakdown=breakdown,
        lifecycle_multiplier=mult, r_squared=run.r_squared_raw,
    )


def predict_psa10_price(features: CardFeatures, run: ModelRun) -> Prediction | None:
    if not run.coefficients_psa10:
        return None
    log_price, breakdown = _fundamentals_log_price(
        run.coefficients_psa10, features, include_gem=True,
    )
    fundamentals_price = math.exp(log_price)
    mult = lifecycle_multiplier(run.lifecycle_curve, features.months_since_release)
    point = fundamentals_price * mult
    low, high = _band(point, run.residual_std_psa10, features.language)
    return Prediction(
        point_estimate=point, low=low, high=high, breakdown=breakdown,
        lifecycle_multiplier=mult, r_squared=run.r_squared_psa10,
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/test_pricing_predict.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add webapp/pricing_model/predict.py webapp/tests/test_pricing_predict.py
git commit -m "feat: fair-value prediction with decomposable factor breakdown"
```

---

## Task 10: Grade-worthiness EV

**Files:**
- Modify: `webapp/pricing_model/predict.py`
- Test: `webapp/tests/test_pricing_grade_ev.py`

**Interfaces:**
- Consumes: `predict_raw_price`, `predict_psa10_price`, `ModelRun`, `CardFeatures` (all from this task's own file / Tasks 5, 6, 9)
- Produces: `GradeEV` dataclass, `grade_worthiness(features: CardFeatures, run: ModelRun, grading_fee: float = 25.0) -> GradeEV | None`

- [ ] **Step 1: Write the failing test**

```python
# webapp/tests/test_pricing_grade_ev.py
from __future__ import annotations

import math

from pricing_model.db import ModelRun
from pricing_model.features import CardFeatures
from pricing_model.predict import grade_worthiness


def _run_with_high_upside() -> ModelRun:
    return ModelRun(
        id=1, fitted_at="", coefficients_raw={"intercept": math.log(10.0)},
        coefficients_psa10={"intercept": math.log(200.0)},
        lifecycle_curve={}, market_index={}, psa9_fraction=0.5,
        residual_std_raw=0.1, residual_std_psa10=0.1,
        r_squared_raw=0.8, r_squared_psa10=0.8, n_cards=500,
    )


def _run_with_no_upside() -> ModelRun:
    return ModelRun(
        id=1, fitted_at="", coefficients_raw={"intercept": math.log(50.0)},
        coefficients_psa10={"intercept": math.log(55.0)},
        lifecycle_curve={}, market_index={}, psa9_fraction=0.5,
        residual_std_raw=0.1, residual_std_psa10=0.1,
        r_squared_raw=0.8, r_squared_psa10=0.8, n_cards=500,
    )


def _features() -> CardFeatures:
    return CardFeatures(
        canonical_rarity="ultra_rare", pull_scarcity=0.5, gem_rate=0.5,
        character_tier="C", is_trainer_art=False, language="english",
        era="sv", release_date="2026-01-01", months_since_release=1.0,
    )


def test_high_upside_card_is_worth_grading():
    ev = grade_worthiness(_features(), _run_with_high_upside(), grading_fee=25.0)
    assert ev is not None
    assert ev.expected_value > 0
    assert ev.worth_grading is True


def test_flat_upside_card_is_not_worth_grading():
    ev = grade_worthiness(_features(), _run_with_no_upside(), grading_fee=25.0)
    assert ev is not None
    assert ev.worth_grading is False


def test_returns_none_when_no_psa10_model():
    run = _run_with_high_upside()
    run.coefficients_psa10 = {}
    assert grade_worthiness(_features(), run) is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_pricing_grade_ev.py -v`
Expected: FAIL — `grade_worthiness` not defined

- [ ] **Step 3: Write minimal implementation**

Append to `webapp/pricing_model/predict.py`:

```python
DEFAULT_GRADING_FEE_USD = 25.0


@dataclass
class GradeEV:
    expected_value: float
    raw_price: float
    predicted_psa10: float
    predicted_psa9: float
    gem_rate: float
    grading_fee: float
    worth_grading: bool


def grade_worthiness(features: CardFeatures, run: ModelRun,
                      grading_fee: float = DEFAULT_GRADING_FEE_USD) -> GradeEV | None:
    psa10_pred = predict_psa10_price(features, run)
    if psa10_pred is None:
        return None
    raw_pred = predict_raw_price(features, run)

    predicted_psa10 = psa10_pred.point_estimate
    predicted_psa9 = predicted_psa10 * run.psa9_fraction
    gem = features.gem_rate

    ev = (gem * predicted_psa10 + (1 - gem) * predicted_psa9) - grading_fee - raw_pred.point_estimate

    # Worth grading needs a real margin, not just EV > 0 — grading has
    # non-priced friction (turnaround time, shipping risk).
    margin_threshold = grading_fee * 0.5
    return GradeEV(
        expected_value=ev, raw_price=raw_pred.point_estimate,
        predicted_psa10=predicted_psa10, predicted_psa9=predicted_psa9,
        gem_rate=gem, grading_fee=grading_fee,
        worth_grading=ev > margin_threshold,
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/test_pricing_grade_ev.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add webapp/pricing_model/predict.py webapp/tests/test_pricing_grade_ev.py
git commit -m "feat: grade-worthiness expected-value calculation"
```

---

## Task 11: Monthly refit job

**Files:**
- Create: `webapp/pricing_model/jobs.py`
- Modify: `webapp/refresh_job.py`
- Test: `webapp/tests/test_pricing_jobs.py`

**Interfaces:**
- Consumes: `corpus.harvest_all` (Task 7), `db.get_all_corpus_cards`, `db.get_all_corpus_history`, `db.get_latest_model_run`, `db.save_model_run` (Task 6), `model.fit_model` (Task 8)
- Produces: `async def monthly_refit() -> dict` (registered into `refresh_job.py`'s scheduler)

- [ ] **Step 1: Write the failing test**

```python
# webapp/tests/test_pricing_jobs.py
from __future__ import annotations

import asyncio
import os
from unittest.mock import AsyncMock, patch

os.environ["PRICING_MODEL_DB"] = "/tmp/test_pricing_jobs.sqlite"

from pricing_model import db as pmdb, jobs


def _fresh_db():
    if pmdb.DB_PATH.exists():
        pmdb.DB_PATH.unlink()
    pmdb.init_db()


def _seed_minimal_corpus():
    for i in range(20):
        rarity = "Rare Holo" if i % 2 == 0 else "Common"
        card_key = f"seed-{i}"
        pmdb.upsert_corpus_card(pmdb.CorpusCardRow(
            card_key=card_key, name=f"Card {i}", set_name="Seed Set",
            card_number=str(i), rarity_raw=rarity, era="sv", language="english",
            release_date="2023-01-01",
        ))
        pmdb.insert_corpus_history(card_key, [("2024-01", 10.0 + i), ("2024-02", 10.5 + i)])


def test_monthly_refit_keeps_previous_run_when_new_fit_is_worse():
    _fresh_db()
    _seed_minimal_corpus()

    good_run = pmdb.ModelRun(
        id=None, fitted_at="", coefficients_raw={"intercept": 1.0}, coefficients_psa10={},
        lifecycle_curve={}, market_index={}, psa9_fraction=0.4,
        residual_std_raw=0.05, residual_std_psa10=0.0,
        r_squared_raw=0.95, r_squared_psa10=0.0, n_cards=1000,
    )
    pmdb.save_model_run(good_run)

    worse_fit_run = pmdb.ModelRun(
        id=None, fitted_at="", coefficients_raw={"intercept": 0.5}, coefficients_psa10={},
        lifecycle_curve={}, market_index={}, psa9_fraction=0.4,
        residual_std_raw=0.9, residual_std_psa10=0.0,
        r_squared_raw=0.1, r_squared_psa10=0.0, n_cards=20,
    )

    with patch("pricing_model.corpus.harvest_all", new=AsyncMock(return_value={"total_cards": 20})), \
         patch("pricing_model.model.fit_model", return_value=worse_fit_run):
        result = asyncio.run(jobs.monthly_refit())

    assert result["kept_previous"] is True
    latest = pmdb.get_latest_model_run()
    assert latest.r_squared_raw == 0.95  # previous, better run stayed active


def test_monthly_refit_accepts_new_fit_when_it_is_better():
    _fresh_db()
    _seed_minimal_corpus()

    better_run = pmdb.ModelRun(
        id=None, fitted_at="", coefficients_raw={"intercept": 1.0}, coefficients_psa10={},
        lifecycle_curve={}, market_index={}, psa9_fraction=0.4,
        residual_std_raw=0.05, residual_std_psa10=0.0,
        r_squared_raw=0.9, r_squared_psa10=0.0, n_cards=1000,
    )

    with patch("pricing_model.corpus.harvest_all", new=AsyncMock(return_value={"total_cards": 20})), \
         patch("pricing_model.model.fit_model", return_value=better_run):
        result = asyncio.run(jobs.monthly_refit())

    assert result["kept_previous"] is False
    latest = pmdb.get_latest_model_run()
    assert latest.r_squared_raw == 0.9
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_pricing_jobs.py -v`
Expected: FAIL — module `pricing_model.jobs` not found

- [ ] **Step 3: Write minimal implementation**

```python
# webapp/pricing_model/jobs.py
"""Monthly corpus refresh + model refit, wired into refresh_job.py's
scheduler. A refit that degrades fit quality vs. the previous run keeps the
previous model_runs row active instead of shipping worse predictions.
"""
from __future__ import annotations

import logging

from pricing_model import corpus, db as pmdb, model as pm

log = logging.getLogger(__name__)

# A refit must beat the previous run's R^2 by at least this much to replace
# it — guards against a bad/partial harvest silently degrading predictions.
MIN_R_SQUARED_IMPROVEMENT = -0.05  # allow small noise-driven dips, reject real regressions


async def monthly_refit() -> dict:
    harvest_summary = await corpus.harvest_all()

    cards = pmdb.get_all_corpus_cards()
    history = pmdb.get_all_corpus_history()
    new_run = pm.fit_model(cards, history)

    previous = pmdb.get_latest_model_run()
    if previous is not None and new_run.r_squared_raw < previous.r_squared_raw + MIN_R_SQUARED_IMPROVEMENT:
        log.warning(
            "pricing_model monthly refit: new fit R^2=%.3f worse than previous %.3f, keeping previous run",
            new_run.r_squared_raw, previous.r_squared_raw,
        )
        return {"kept_previous": True, "harvest": harvest_summary,
                "new_r_squared": new_run.r_squared_raw, "previous_r_squared": previous.r_squared_raw}

    run_id = pmdb.save_model_run(new_run)
    log.info("pricing_model monthly refit: saved new model run %s (R^2=%.3f, n=%d)",
              run_id, new_run.r_squared_raw, new_run.n_cards)
    return {"kept_previous": False, "harvest": harvest_summary,
            "new_r_squared": new_run.r_squared_raw, "run_id": run_id}
```

Modify `webapp/refresh_job.py` — add the import and scheduler registration:

```python
# Add near the top with the other imports:
import pricing_model.jobs as pricing_model_jobs
```

```python
# Inside start_scheduler(), after the existing weekly_price_history_refresh job registration:
    _scheduler.add_job(
        pricing_model_jobs.monthly_refit,
        CronTrigger(day="1", hour=5, minute=0, timezone=DAILY_TIMEZONE),
        id="monthly_pricing_model_refit",
        name="Monthly pricing-model corpus refresh + refit (1st @ 5am CT)",
        replace_existing=True,
        misfire_grace_time=24 * 60 * 60,   # 1 day grace
    )
```

```python
# Update the log.info call at the end of start_scheduler() to mention the new job:
    log.info("Scheduler started: daily price refresh @ %02d:00 %s, "
             "weekly price-history refresh Sun 06:00 %s, "
             "monthly pricing-model refit 1st @ 05:00 %s",
             DAILY_HOUR_LOCAL, DAILY_TIMEZONE, DAILY_TIMEZONE, DAILY_TIMEZONE)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/test_pricing_jobs.py -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Run the full backend test suite to check nothing broke**

Run: `python3 -m pytest tests/ -v`
Expected: PASS (all existing tests plus the new pricing_model ones)

- [ ] **Step 6: Commit**

```bash
git add webapp/pricing_model/jobs.py webapp/refresh_job.py webapp/tests/test_pricing_jobs.py
git commit -m "feat: monthly pricing-model refit job with fit-quality guard"
```

---

## Task 12: API — `GET /api/cards/{card_id}/prediction`

**Files:**
- Modify: `webapp/app.py`
- Test: `webapp/tests/test_pricing_prediction_api.py`

**Interfaces:**
- Consumes: `db.get_card` (existing), `pricing_model.db.get_card_features`, `upsert_card_features`, `get_latest_model_run` (Task 6), `pricing_model.features.build_card_features` (Task 5), `pricing_model.predict.predict_raw_price`, `predict_psa10_price`, `grade_worthiness` (Tasks 9–10), `card_lookup.search_cards` / `tcgdex_lookup` (existing, for rarity + release date resolution)

- [ ] **Step 1: Write the failing test**

**Test design note:** `app.py` does `try: import db_postgres as db except: import db` at
module load — in a dev venv with `psycopg2-binary` installed (per
`requirements.txt`), `app.db` is `db_postgres` regardless of whether
`DATABASE_URL` is set, so a test that seeds data through a separately
imported `db` (SQLite) module and then hits the endpoint via `TestClient`
would be seeding a different database than the one the endpoint reads.
The established pattern in this repo (`tests/test_billing_webhook.py`)
sidesteps this by monkeypatching functions directly on `app_module.db`
rather than relying on a real round trip through whichever backend loaded.
Follow that pattern here, using `app_module.db.Card(...)` (not a
separately-imported `db.Card`) so the fake card's shape matches whichever
`Card` dataclass is actually active. `pricing_model.db` (this plan's own
dedicated SQLite file) has no such ambiguity — it's exercised for real, as
in Task 6.

```python
# webapp/tests/test_pricing_prediction_api.py
from __future__ import annotations

import math
import os
from unittest.mock import AsyncMock, patch

os.environ["PRICING_MODEL_DB"] = "/tmp/test_prediction_api_pm.sqlite"

import pytest
from fastapi.testclient import TestClient

import app as app_module
import pricing_model.db as pmdb


@pytest.fixture(autouse=True)
def fresh_pricing_db():
    if pmdb.DB_PATH.exists():
        pmdb.DB_PATH.unlink()
    pmdb.init_db()
    yield


def _fake_card(card_id: int, **overrides):
    fields = dict(
        id=card_id, user_id=1, name="Charizard ex", set_name="Surging Sparks",
        card_number="199/191", language="english", condition="NM",
        is_graded=False, grade_company=None, grade=None,
        purchase_price=None, purchase_date=None, current_market_price=120.0,
        last_priced_at=None, image_url=None, photo_path=None, notes=None,
        created_at=None, product_type="card",
    )
    fields.update(overrides)
    return app_module.db.Card(**fields)


def _seed_model_run():
    run = pmdb.ModelRun(
        id=None, fitted_at="", coefficients_raw={"intercept": math.log(20.0)},
        coefficients_psa10={"intercept": math.log(80.0)},
        lifecycle_curve={}, market_index={}, psa9_fraction=0.4,
        residual_std_raw=0.2, residual_std_psa10=0.2,
        r_squared_raw=0.7, r_squared_psa10=0.6, n_cards=500,
    )
    pmdb.save_model_run(run)


def test_prediction_endpoint_returns_fair_value_for_known_card(monkeypatch):
    client = TestClient(app_module.app)
    card = _fake_card(1)
    monkeypatch.setattr(app_module.db, "get_card", lambda cid: card if cid == 1 else None)
    _seed_model_run()

    fake_card_result = type("R", (), {"rarity": "Special Illustration Rare"})()

    with patch("card_lookup.search_cards", new=AsyncMock(return_value=[fake_card_result])), \
         patch.object(app_module, "_resolve_set_release_date", new=AsyncMock(return_value="2024-11-08")):
        resp = client.get("/api/cards/1/prediction")

    assert resp.status_code == 200
    body = resp.json()
    assert body["fair_value"]["point_estimate"] > 0
    assert "breakdown" in body["fair_value"]
    assert "confidence" in body["fair_value"]


def test_prediction_endpoint_404s_for_unknown_card(monkeypatch):
    client = TestClient(app_module.app)
    monkeypatch.setattr(app_module.db, "get_card", lambda cid: None)
    resp = client.get("/api/cards/999999/prediction")
    assert resp.status_code == 404


def test_prediction_endpoint_503s_when_no_model_run_exists_yet(monkeypatch):
    client = TestClient(app_module.app)
    card = _fake_card(2)
    monkeypatch.setattr(app_module.db, "get_card", lambda cid: card if cid == 2 else None)
    resp = client.get("/api/cards/2/prediction")
    assert resp.status_code == 503
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_pricing_prediction_api.py -v`
Expected: FAIL — 404 on the route (doesn't exist yet)

- [ ] **Step 3: Write minimal implementation**

Add to `webapp/app.py` (near the other `card_id`-scoped routes, e.g. after `card_price_history`):

```python
import pricing_model.db as pricing_db
from pricing_model.features import build_card_features
from pricing_model.predict import grade_worthiness, predict_psa10_price, predict_raw_price


async def _resolve_rarity_and_release_date(card: "db.Card") -> tuple[str | None, str | None]:
    """Resolve (rarity_raw, release_date) for a collection card via the
    existing lookup modules. Returns (None, None) if either can't be found —
    callers must degrade gracefully (see spec Error handling)."""
    if card.language == "japanese":
        import tcgdex_lookup
        result = await tcgdex_lookup.lookup_jp_card(card.name, card.set_name)
        if result is None:
            return None, None
        release_date = await _resolve_jp_set_release_date(card.set_name)
        return result.rarity, release_date
    if card.language == "chinese":
        # No rarity source exists for Chinese-exclusive cards anywhere in
        # this codebase yet (tcgdex_lookup.py is hardcoded to the /v2/ja
        # endpoint; card_lookup.py is EN-only via pokemontcg.io). Returning
        # (None, None) here is the honest degraded state — the endpoint
        # below turns this into a 422 rather than guessing from an EN
        # lookup that wouldn't actually match a CN-exclusive card.
        return None, None
    import card_lookup
    candidates = await card_lookup.search_cards(f'{card.name} set:"{card.set_name}"', limit=1)
    if not candidates:
        return None, None
    rarity = candidates[0].rarity
    release_date = await _resolve_set_release_date(card.set_name)
    return rarity, release_date


async def _resolve_set_release_date(set_name: str) -> Optional[str]:
    """EN sets: query Pokemon TCG API's /v2/sets by name. Returns
    'YYYY-MM-DD' or None if the set can't be resolved (an unrecognized EN
    set name — the caller returns 422 rather than guessing)."""
    import httpx
    async with httpx.AsyncClient(timeout=10.0) as client:
        try:
            resp = await client.get(
                "https://api.pokemontcg.io/v2/sets",
                params={"q": f'name:"{set_name}"'},
            )
            resp.raise_for_status()
        except httpx.HTTPError:
            return None
    data = resp.json().get("data") or []
    if not data:
        return None
    return data[0].get("releaseDate", "").replace("/", "-") or None


async def _resolve_jp_set_release_date(set_name: str) -> Optional[str]:
    """JP sets: resolve via tcgdex_lookup's existing set-id mapping, then
    query TCGdex's set endpoint for releaseDate.

    NOTE for the implementer: this assumes TCGdex's `/v2/ja/sets/{id}`
    response includes a `releaseDate` field (its documented set-level shape).
    This could not be verified against a live call from the planning
    environment (TCGdex was unreachable from that sandbox, unlike
    tcgdex_lookup.py's other endpoints which are confirmed working in this
    app already). Before marking this task done, run one real lookup for a
    known JP set (e.g. via `tcgdex_lookup._resolve_set_id("Terastal Festival")`
    to get the set id, then fetch `/v2/ja/sets/{id}`) and confirm the field
    name — adjust the `.get(...)` key below if it differs.
    """
    import tcgdex_lookup
    set_id = tcgdex_lookup._resolve_set_id(set_name)
    if not set_id:
        return None
    import httpx
    async with httpx.AsyncClient(timeout=10.0) as client:
        try:
            resp = await client.get(f"{tcgdex_lookup.TCGDEX_BASE}/sets/{set_id}")
            resp.raise_for_status()
        except httpx.HTTPError:
            return None
    release = resp.json().get("releaseDate")
    return release or None


@app.get("/api/cards/{card_id}/prediction")
async def card_prediction(card_id: int):
    """Fair-value prediction for one card: point estimate + band, factor
    breakdown, grade-worthiness (raw cards only). 404 if the card doesn't
    exist; 503 if no model has been fit yet (corpus backfill not run)."""
    card = db.get_card(card_id)
    if not card:
        raise HTTPException(404, "card not found")

    run = pricing_db.get_latest_model_run()
    if run is None:
        raise HTTPException(503, "pricing model not yet fitted — run backfill_pricing_corpus.py")

    features = pricing_db.get_card_features(card_id)
    if features is None:
        rarity_raw, release_date = await _resolve_rarity_and_release_date(card)
        if rarity_raw is None or release_date is None:
            raise HTTPException(422, "insufficient card data to build a prediction (rarity or release date unresolved)")
        features = build_card_features(
            name=card.name, set_name=card.set_name or "", card_number=card.card_number or "",
            rarity_raw=rarity_raw, language=card.language, release_date=release_date,
            # Must match pricing_model.model's FIXED_CARDS_IN_TIER (always 1)
            # -- training and prediction have to agree on this or predicted
            # prices skew arbitrarily against what the model learned.
            cards_in_tier=1,
        )
        if features is None:
            raise HTTPException(422, f"unmapped rarity {rarity_raw!r} — no prediction available")
        pricing_db.upsert_card_features(card_id, features)

    raw_pred = predict_raw_price(features, run)
    psa10_pred = predict_psa10_price(features, run)
    ev = None if card.is_graded else grade_worthiness(features, run)

    return {
        "fair_value": {
            "point_estimate": round(raw_pred.point_estimate, 2),
            "low": round(raw_pred.low, 2),
            "high": round(raw_pred.high, 2),
            "breakdown": {k: round(v, 3) for k, v in raw_pred.breakdown.items()},
            "confidence": "high" if features.language == "english" else
                          ("medium" if features.language == "japanese" else "low"),
        },
        "psa10_fair_value": None if psa10_pred is None else {
            "point_estimate": round(psa10_pred.point_estimate, 2),
            "low": round(psa10_pred.low, 2),
            "high": round(psa10_pred.high, 2),
        },
        "lifecycle": {
            "months_since_release": features.months_since_release,
            "multiplier": raw_pred.lifecycle_multiplier,
        },
        "grade_worthiness": None if ev is None else {
            "expected_value": round(ev.expected_value, 2),
            "worth_grading": ev.worth_grading,
            "predicted_psa10": round(ev.predicted_psa10, 2),
            "gem_rate_estimate": round(ev.gem_rate, 3),
        },
        "current_market_price": card.current_market_price,
        "model_fitted_at": run.fitted_at,
    }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/test_pricing_prediction_api.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Verify the TCGdex release-date field assumption against live data**

`_resolve_jp_set_release_date`'s `.get("releaseDate")` call is the one piece of this task not verified against live TCGdex data during planning (see its docstring). Run:

```bash
python3 -c "
import asyncio, httpx, tcgdex_lookup
async def main():
    set_id = tcgdex_lookup._resolve_set_id('Terastal Festival')
    print('set_id:', set_id)
    async with httpx.AsyncClient(timeout=10.0) as c:
        r = await c.get(f'{tcgdex_lookup.TCGDEX_BASE}/sets/{set_id}')
        print(r.status_code, r.json().get('releaseDate'))
asyncio.run(main())
"
```

Expected: prints a non-None date string. If the field is named differently (or nested), update `_resolve_jp_set_release_date` accordingly before committing.

- [ ] **Step 6: Commit**

```bash
git add webapp/app.py webapp/tests/test_pricing_prediction_api.py
git commit -m "feat: GET /api/cards/{card_id}/prediction endpoint"
```

---

## Task 13: API — `GET /api/users/{user_id}/rankings`

**Files:**
- Modify: `webapp/app.py`
- Test: `webapp/tests/test_pricing_rankings_api.py`

**Interfaces:**
- Consumes: `db.list_cards` (existing), `pricing_db.get_card_features`, `get_latest_model_run` (Task 6), `predict_raw_price`, `grade_worthiness` (Tasks 9–10)

- [ ] **Step 1: Write the failing test**

Uses the same `app_module.db` monkeypatching approach as Task 12 (see its
test design note) rather than a real round trip through whichever backend
`app.py` loaded.

```python
# webapp/tests/test_pricing_rankings_api.py
from __future__ import annotations

import math
import os

os.environ["PRICING_MODEL_DB"] = "/tmp/test_rankings_api_pm.sqlite"

import pytest
from fastapi.testclient import TestClient

import app as app_module
import pricing_model.db as pmdb
from pricing_model.features import CardFeatures


@pytest.fixture(autouse=True)
def fresh_pricing_db():
    if pmdb.DB_PATH.exists():
        pmdb.DB_PATH.unlink()
    pmdb.init_db()
    yield


def _fake_card(card_id: int, **overrides):
    fields = dict(
        id=card_id, user_id=1, name="Card", set_name="Set", card_number="1",
        language="english", condition="NM", is_graded=False,
        grade_company=None, grade=None, purchase_price=None, purchase_date=None,
        current_market_price=None, last_priced_at=None, image_url=None,
        photo_path=None, notes=None, created_at=None, product_type="card",
    )
    fields.update(overrides)
    return app_module.db.Card(**fields)


def test_rankings_sorts_by_undervalued_by_default(monkeypatch):
    client = TestClient(app_module.app)

    cheap = _fake_card(1, name="Card Cheap", current_market_price=5.0)   # << fair value -> undervalued
    pricey = _fake_card(2, name="Card Pricey", card_number="2", current_market_price=500.0)  # >> fair value -> overvalued
    monkeypatch.setattr(app_module.db, "list_cards", lambda uid: [cheap, pricey])

    for c in (cheap, pricey):
        pmdb.upsert_card_features(c.id, CardFeatures(
            canonical_rarity="rare_holo", pull_scarcity=1.0, gem_rate=0.5,
            character_tier="C", is_trainer_art=False, language="english",
            era="sv", release_date="2024-01-01", months_since_release=12.0,
        ))
    run = pmdb.ModelRun(
        id=None, fitted_at="", coefficients_raw={"intercept": math.log(50.0)},
        coefficients_psa10={}, lifecycle_curve={}, market_index={},
        psa9_fraction=0.4, residual_std_raw=0.2, residual_std_psa10=0.0,
        r_squared_raw=0.7, r_squared_psa10=0.0, n_cards=500,
    )
    pmdb.save_model_run(run)

    resp = client.get("/api/users/1/rankings?sort=undervalued")
    assert resp.status_code == 200
    body = resp.json()
    ids_in_order = [row["card_id"] for row in body["rankings"]]
    assert ids_in_order[0] == cheap.id  # most undervalued first


def test_rankings_skips_cards_without_features_silently(monkeypatch):
    client = TestClient(app_module.app)
    no_features_card = _fake_card(1, name="No Features Card")
    monkeypatch.setattr(app_module.db, "list_cards", lambda uid: [no_features_card])

    run = pmdb.ModelRun(
        id=None, fitted_at="", coefficients_raw={"intercept": 1.0}, coefficients_psa10={},
        lifecycle_curve={}, market_index={}, psa9_fraction=0.4,
        residual_std_raw=0.2, residual_std_psa10=0.0, r_squared_raw=0.7,
        r_squared_psa10=0.0, n_cards=500,
    )
    pmdb.save_model_run(run)

    resp = client.get("/api/users/1/rankings")
    assert resp.status_code == 200
    assert resp.json()["rankings"] == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_pricing_rankings_api.py -v`
Expected: FAIL — route doesn't exist (404)

- [ ] **Step 3: Write minimal implementation**

Add to `webapp/app.py`, after the prediction endpoint:

```python
@app.get("/api/users/{user_id}/rankings")
def user_rankings(user_id: int, sort: str = "undervalued"):
    """Rank a user's collection by valuation gap, combined upside, or grade
    EV. Cards with no stored features (never viewed via the prediction
    endpoint yet) are silently skipped rather than erroring — this is a
    best-effort view over whatever's already been computed."""
    run = pricing_db.get_latest_model_run()
    if run is None:
        return {"rankings": []}

    cards = db.list_cards(user_id)
    rows = []
    for card in cards:
        features = pricing_db.get_card_features(card.id)
        if features is None or card.current_market_price is None:
            continue
        raw_pred = predict_raw_price(features, run)
        gap_pct = (card.current_market_price - raw_pred.point_estimate) / raw_pred.point_estimate * 100
        ev = None if card.is_graded else grade_worthiness(features, run)
        rows.append({
            "card_id": card.id,
            "name": card.name,
            "current_market_price": card.current_market_price,
            "fair_value": round(raw_pred.point_estimate, 2),
            "valuation_gap_pct": round(gap_pct, 1),
            "grade_ev": None if ev is None else round(ev.expected_value, 2),
        })

    if sort == "grade_ev":
        rows = [r for r in rows if r["grade_ev"] is not None]
        rows.sort(key=lambda r: r["grade_ev"], reverse=True)
    elif sort == "upside":
        rows.sort(key=lambda r: -r["valuation_gap_pct"])
    else:  # "undervalued" (default) — most negative gap (cheapest vs. fair value) first
        rows.sort(key=lambda r: r["valuation_gap_pct"])

    return {"rankings": rows}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/test_pricing_rankings_api.py -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Run the full backend test suite**

Run: `python3 -m pytest tests/ -v`
Expected: PASS (all tests)

- [ ] **Step 6: Commit**

```bash
git add webapp/app.py webapp/tests/test_pricing_rankings_api.py
git commit -m "feat: GET /api/users/{user_id}/rankings endpoint"
```

---

## Task 14: Frontend — Fair Value panel on Detail screen

**Files:**
- Modify: `webapp/static/api.jsx`
- Modify: `webapp/static/screens/Detail.jsx`

**Interfaces:**
- Consumes: `GET /api/cards/{card_id}/prediction` (Task 12)
- Produces: `window.api.getPrediction(cardId)`, a `FairValuePanel` component rendered inside `OverviewTab`

- [ ] **Step 1: Add the API client method**

In `webapp/static/api.jsx`, add to the `P` routes object (near `cardPriceHistory`):

```js
    cardPrediction:  (cid) => `/api/cards/${cid}/prediction`,
```

Add a new method alongside `getPriceHistory` (same object literal):

```js
    // Fair-value prediction for a card: { fair_value, psa10_fair_value,
    // lifecycle, grade_worthiness, current_market_price, model_fitted_at }.
    // Returns null on any failure (503 = model not fitted yet, 422 =
    // unmapped rarity, 404 = card not found) so the UI can hide the panel
    // instead of showing an error for what's an optional enrichment.
    async getPrediction(cardId) {
      if (!cardId) return null;
      try {
        return await request(P.cardPrediction(cardId));
      } catch (e) {
        return null;
      }
    },
```

- [ ] **Step 2: Add the FairValuePanel component and wire it into OverviewTab**

In `webapp/static/screens/Detail.jsx`, add a new component near `PricePointChart` (both are Overview-tab-only pieces):

```jsx
function FairValuePanel({ card }) {
  const [pred, setPred] = useStateDetail(null);

  React.useEffect(() => {
    if (!card?.id || !window.api?.getPrediction) { setPred(null); return; }
    let cancelled = false;
    (async () => {
      const res = await window.api.getPrediction(card.id);
      if (!cancelled) setPred(res);
    })();
    return () => { cancelled = true; };
  }, [card?.id]);

  if (!pred) return null;

  const fv = pred.fair_value;
  const market = pred.current_market_price;
  const gapPct = market != null ? ((market - fv.point_estimate) / fv.point_estimate) * 100 : null;
  const badge = gapPct == null ? null : (gapPct > 10 ? 'Overvalued' : gapPct < -10 ? 'Undervalued' : 'Fair');

  return (
    <div className="card" style={{ padding: 16, marginTop: 12 }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline' }}>
        <div style={{ fontWeight: 600 }}>Fair Value</div>
        {badge && (
          <span style={{
            fontSize: 12, padding: '2px 8px', borderRadius: 999,
            background: badge === 'Undervalued' ? '#dcfce7' : badge === 'Overvalued' ? '#fee2e2' : '#f1f5f9',
            color: badge === 'Undervalued' ? '#166534' : badge === 'Overvalued' ? '#991b1b' : '#475569',
          }}>{badge}</span>
        )}
      </div>
      <div style={{ fontSize: 22, fontWeight: 700, marginTop: 4 }}>
        <Price usd={fv.point_estimate} />
        <span style={{ fontSize: 13, fontWeight: 400, color: '#64748b', marginLeft: 8 }}>
          (<Price usd={fv.low} /> – <Price usd={fv.high} />)
        </span>
      </div>
      {pred.grade_worthiness && (
        <div style={{ fontSize: 13, marginTop: 8, color: pred.grade_worthiness.worth_grading ? '#166534' : '#64748b' }}>
          {pred.grade_worthiness.worth_grading ? 'Worth grading — ' : 'Not worth grading — '}
          estimated EV <Price usd={pred.grade_worthiness.expected_value} sign />
          {' '}(est. gem rate {(pred.grade_worthiness.gem_rate_estimate * 100).toFixed(0)}%)
        </div>
      )}
    </div>
  );
}
```

Wire it into `OverviewTab`'s render, immediately after the `PricePointChart` call found earlier at `screens/Detail.jsx:1446`:

```jsx
            <PricePointChart points={activePts} w={358} h={160} windowStart={chartWindowStart}/>
            <FairValuePanel card={card} />
```

- [ ] **Step 3: Manual verification in the browser**

Run: `cd webapp && ./run.sh`, open `http://localhost:8000/`, navigate to any card's Detail screen, Overview tab.
Expected: if a model has been fitted (`backfill_pricing_corpus.py` has been run at least once), a "Fair Value" panel appears below the price chart with a point estimate, range, and (for raw cards) a grade-worthiness line. If no model is fitted yet, the panel is silently absent — confirm no console errors from the failed `getPrediction` call.

- [ ] **Step 4: Commit**

```bash
git add webapp/static/api.jsx webapp/static/screens/Detail.jsx
git commit -m "feat: Fair Value panel on card Detail screen"
```

---

## Task 15: Frontend — Rankings screen

**Files:**
- Modify: `webapp/static/api.jsx`
- Create: `webapp/static/screens/Rankings.jsx`
- Modify: `webapp/static/app.jsx`
- Modify: `webapp/static/index.html`

**Interfaces:**
- Consumes: `GET /api/users/{user_id}/rankings` (Task 13)
- Produces: `window.api.getRankings(userId, sort)`, a `RankingsScreen` registered in the nav stack as `'rankings'`

- [ ] **Step 1: Add the API client method**

In `webapp/static/api.jsx`, add to `P`:

```js
    userRankings: (uid, sort) => `/api/users/${uid}/rankings?sort=${sort}`,
```

Add the method:

```js
    // Collection ranked by valuation gap / upside / grade EV.
    // Returns { rankings: [] } on failure so the screen can show an empty state.
    async getRankings(userId, sort = 'undervalued') {
      if (!userId) return { rankings: [] };
      try {
        return await request(P.userRankings(userId, sort));
      } catch (e) {
        return { rankings: [] };
      }
    },
```

- [ ] **Step 2: Create the Rankings screen**

```jsx
// webapp/static/screens/Rankings.jsx
function RankingsScreen({ navigate, currentUserId }) {
  const [sort, setSort] = React.useState('undervalued');
  const [rows, setRows] = React.useState(null);

  React.useEffect(() => {
    let cancelled = false;
    setRows(null);
    (async () => {
      const res = await window.api.getRankings(currentUserId, sort);
      if (!cancelled) setRows(res.rankings || []);
    })();
    return () => { cancelled = true; };
  }, [currentUserId, sort]);

  return (
    <div className="screen">
      <NavBar title="Rankings" left={<NavBackButton onClick={() => navigate('__back')} />} />
      <div style={{ display: 'flex', gap: 8, padding: '8px 16px' }}>
        {['undervalued', 'upside', 'grade_ev'].map(s => (
          <button key={s} className={`chip ${sort === s ? 'chip-active' : ''}`} onClick={() => setSort(s)}>
            {s === 'undervalued' ? 'Undervalued' : s === 'upside' ? 'Upside' : 'Grade EV'}
          </button>
        ))}
      </div>
      {rows === null ? (
        <div style={{ padding: 16, color: '#64748b' }}>Loading…</div>
      ) : rows.length === 0 ? (
        <div style={{ padding: 16, color: '#64748b' }}>
          No ranked cards yet — open a few cards' Detail screens first so their fair-value features get computed.
        </div>
      ) : (
        <div style={{ padding: '0 16px' }}>
          {rows.map(r => (
            <div key={r.card_id} className="card" style={{ padding: 12, marginTop: 8, cursor: 'pointer' }}
                 onClick={() => navigate('detail', { id: r.card_id })}>
              <div style={{ display: 'flex', justifyContent: 'space-between' }}>
                <div style={{ fontWeight: 600 }}>{r.name}</div>
                <Price usd={r.current_market_price} />
              </div>
              <div style={{ fontSize: 13, color: '#64748b', marginTop: 2 }}>
                Fair value <Price usd={r.fair_value} /> ({r.valuation_gap_pct > 0 ? '+' : ''}{r.valuation_gap_pct}%)
                {r.grade_ev != null && <> · Grade EV <Price usd={r.grade_ev} sign /></>}
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
```

- [ ] **Step 3: Register the script tag**

In `webapp/static/index.html`, add a `<script type="text/babel" src="screens/Rankings.jsx">` line next to the existing screen script tags (same pattern as `Trade.jsx`/`Bulk.jsx`).

- [ ] **Step 4: Wire the route into the nav stack**

In `webapp/static/app.jsx`, add a case to the screen switch found earlier at `app.jsx:278-285`:

```jsx
    case 'rankings':  Screen = RankingsScreen; break;
```

Add an entry point — a button on `BrowseScreen` (or `HomeScreen`) that calls `navigate('rankings')`. Since the exact placement depends on that screen's current layout, add it as a `NavBar` right-side action on Browse: find `BrowseScreen`'s `<NavBar .../>` call and add a right-side button:

```jsx
right={<button className="tap" onClick={() => navigate('rankings')}>Rankings</button>}
```

(If `BrowseScreen`'s `NavBar` already has a `right` action for something else, add this as a second element in that slot rather than replacing it.)

- [ ] **Step 5: Manual verification in the browser**

Run: `cd webapp && ./run.sh`, open `http://localhost:8000/`, go to Browse, tap "Rankings".
Expected: screen loads, sort chips switch between Undervalued/Upside/Grade EV, tapping a row navigates to that card's Detail screen. With no fitted model or no cards with computed features yet, the empty state message renders instead of a blank/broken screen.

- [ ] **Step 6: Commit**

```bash
git add webapp/static/api.jsx webapp/static/screens/Rankings.jsx webapp/static/app.jsx webapp/static/index.html
git commit -m "feat: Rankings screen for collection valuation/upside/grade-EV sorting"
```

---

## Post-plan: one-time corpus backfill (not a code task — an operational step)

After Task 11 lands, run once (from `webapp/`, with the venv active):

```bash
python3 backfill_pricing_corpus.py
```

This populates `pricing_model.sqlite`'s corpus tables from `corpus.TRAINING_SETS` (~17 EN sets) and fits the first `model_runs` row. Until this has run at least once, `/api/cards/{id}/prediction` returns 503 and the Fair Value panel / Rankings screen show their empty states — this is the designed degraded state, not a bug.
